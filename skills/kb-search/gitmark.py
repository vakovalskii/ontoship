#!/usr/bin/env python3
"""GitMark Memory Bank — CLI для базы знаний на чистом md + README + git.

Source of truth = markdown. Всё производное (поисковый индекс, HTML-обзор, граф)
регенерится из md → git остаётся чистым (.gitmark/ в .gitignore).

Поиск: SQLite FTS5 — bm25() (ранжировка по терминам) ∪ trigram-токенайзер
(n-gram: substring, опечатки, кириллица). Чистый python stdlib. Оффлайн.

Команды:
    gitmark index  [--root .] [--force]      построить/обновить индекс
    gitmark search "<q>" [-k 8] [--json]      искать (bm25 ∪ trigram)
    gitmark map   [-o docs/docs-map.html]     self-contained HTML: дерево+рендер+граф
    gitmark serve [-p 8799]                   локальный http для просмотра HTML
    gitmark stat                              статистика индекса/БЗ
    gitmark lint  [paths…] [--strict]         проверить онтологию (типы/связи/README/битые ссылки)
    gitmark version

Markdown-рендер в `map` использует lib `markdown` если установлена (опционально),
иначе показывает raw md. Всё остальное — stdlib.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

VERSION = "0.1.0"

EXCLUDE_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".pytest_cache", "_vendor", ".venv", "venv", "vendor",
    ".gitmark", ".worktrees",
}
DB_REL = ".gitmark/index.db"
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


# ─────────────────────────── discovery ───────────────────────────
def repo_root(start: Path) -> Path:
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return p


def iter_md(root: Path):
    for p in sorted(root.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def area_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "docs":
        # docs/services/<svc> разносим по сервисам (отдельная группа/цвет), прочее — docs/<sub>
        if len(parts) > 3 and parts[1] == "services":
            return "docs/services/" + parts[2]
        return "docs/" + parts[1] if len(parts) > 2 else "docs"
    if parts[0] == "services" and len(parts) > 1:
        return "services/" + parts[1]
    if len(parts) == 1:
        return "(root)"
    return parts[0]


def title_of(text: str, rel: str) -> str:
    m = H1_RE.search(text)
    if m:
        return re.sub(r"[`*]", "", m.group(1)).strip()[:90]
    return Path(rel).name


def chunk_md(text: str):
    """Чанкуем по заголовкам → (line_start, heading, body)."""
    lines = text.split("\n")
    chunks, cur = [], {"line": 1, "heading": "", "body": []}
    for i, ln in enumerate(lines, 1):
        m = HEAD_RE.match(ln)
        if m:
            if cur["body"] or cur["heading"]:
                chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
            cur = {"line": i, "heading": m.group(2).strip(), "body": [ln]}
        else:
            cur["body"].append(ln)
    if cur["body"] or cur["heading"]:
        chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
    return chunks


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def resolve_link(src_rel: str, href: str, known: set) -> str | None:
    href = _nfc(href.split("#")[0].strip())
    if not href or not href.endswith(".md") or href.startswith(("http", "mailto:")):
        return None
    known = {_nfc(k) for k in known}
    src_dir = Path(_nfc(src_rel)).parent
    cands = []
    try:
        cands.append((src_dir / href).as_posix())
    except Exception:
        pass
    cands.append(href.lstrip("./"))
    # нормализуем ../ через PurePosix
    import posixpath
    norm = posixpath.normpath((src_dir / href).as_posix())
    cands.append(norm)
    for c in cands:
        if c in known:
            return c
    base = Path(href).name
    hits = [r for r in known if r.endswith("/" + base) or r == base]
    return hits[0] if len(hits) == 1 else None


# ─────────────────────────── index ───────────────────────────
def _has_trigram(con) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE _tri_probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE _tri_probe")
        return True
    except sqlite3.OperationalError:
        return False


def cmd_index(root: Path, force: bool = False) -> dict:
    db = root / DB_REL
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, "
                    "tokenize='unicode61 remove_diacritics 2')")
    except sqlite3.OperationalError as e:
        print(f"ОШИБКА: SQLite без FTS5 ({e}). Нужен python с FTS5.", file=sys.stderr)
        sys.exit(2)
    has_tri = _has_trigram(con)
    if has_tri:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tri USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, tokenize='trigram')")
    con.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, title TEXT, "
                "area TEXT, size INT, chunks INT)")
    con.execute("CREATE TABLE IF NOT EXISTS links(src TEXT, dst TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    for t in ("fts", "files", "links"):
        con.execute(f"DELETE FROM {t}")
    if has_tri:
        con.execute("DELETE FROM tri")

    files = {}
    for p in iter_md(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        files[rel] = text
    known = set(files)

    nchunks = 0
    for rel, text in files.items():
        title, area = title_of(text, rel), area_of(rel)
        chs = chunk_md(text)
        for line, heading, body in chs:
            con.execute("INSERT INTO fts(path,heading,lineno,body) VALUES(?,?,?,?)",
                        (rel, heading, str(line), body))
            if has_tri:
                con.execute("INSERT INTO tri(path,heading,lineno,body) VALUES(?,?,?,?)",
                            (rel, heading, str(line), body))
        nchunks += len(chs)
        con.execute("INSERT INTO files VALUES(?,?,?,?,?)",
                    (rel, title, area, len(text.encode("utf-8")), len(chs)))
        seen = set()
        for m in LINK_RE.finditer(text):
            dst = resolve_link(rel, m.group(1).split()[0], known)
            if dst and dst != rel and (rel, dst) not in seen:
                seen.add((rel, dst))
                con.execute("INSERT INTO links VALUES(?,?)", (rel, dst))
    con.execute("INSERT OR REPLACE INTO meta VALUES('trigram', ?)", ("1" if has_tri else "0",))
    con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (VERSION,))
    con.commit()
    n_links = con.execute("SELECT count(*) FROM links").fetchone()[0]
    con.close()
    return {"files": len(files), "chunks": nchunks, "links": n_links,
            "trigram": has_tri, "db": str(db.relative_to(root))}


# ─────────────────────────── search ───────────────────────────
def _fts_match_query(query: str) -> str:
    terms = [t for t in WORD_RE.findall(query) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' for t in terms)


def _fuzzy_phrases(s: str) -> list:
    """Фразы из пар соседних 3-грамм = 4-символьные подстроки запроса.

    Матч по 4-символьным окнам (а не одиночным 3-граммам) отсекает мусор: опечатка
    сохраняет длинные общие подстроки (`firecraker`↔`firecracker`: `fire`,`ecra`),
    а случайная строка делит с доками лишь разрозненные частые 3-граммы (`ent`,`non`).
    """
    wins, seen = [], set()
    for w in WORD_RE.findall(s.lower()):
        if len(w) < 4:
            continue
        for i in range(len(w) - 3):
            win = w[i:i + 4]            # 4-символьное окно; FTS5-trigram сам разложит
            if '"' in win or win in seen:
                continue
            seen.add(win)
            wins.append(win)
    return wins


def cmd_search(root: Path, query: str, k: int = 8) -> list:
    db = root / DB_REL
    if not db.exists():
        raise SystemExit("Индекс не найден — запусти `gitmark index`")
    con = sqlite3.connect(db)
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    results: dict = {}
    bm_q = _fts_match_query(query)
    if bm_q:
        try:
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(fts,3,'»','«','…',14), bm25(fts) "
                "FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (bm_q, k * 3),
            ):
                key = (path, lineno)
                results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                "snippet": " ".join(snip.split()), "score": -float(score), "via": "bm25"}
        except sqlite3.OperationalError:
            pass
    if has_tri and len(query.strip()) >= 3:
        # (a) фразовый trigram — точная подстрока (вес 0.6)
        try:
            tq = '"' + query.replace('"', " ").strip() + '"'
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(tri,3,'»','«','…',14), bm25(tri) "
                "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                (tq, k * 2),
            ):
                key = (path, lineno)
                if key not in results:
                    results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                    "snippet": " ".join(snip.split()), "score": -float(score) * 0.6,
                                    "via": "trigram"}
        except sqlite3.OperationalError:
            pass
        # (b) fuzzy: OR по 4-символьным окнам запроса → опечатки/морфология/кириллица.
        # Порог покрытия: чанк принимается, только если содержит ≥50% (и ≥2) различных
        # 4-грамм запроса — отсекает мусор, цепляющий 1 случайное частое окно (вес 0.3).
        grams = _fuzzy_phrases(query)
        if grams:
            fq = " OR ".join(f'"{g}"' for g in grams)
            need = max(1, (len(grams) + 4) // 5)   # ≥ceil(20% окон): коротким хватает 1
                                                   # (серединная опечатка), длинным — больше
            try:
                for path, heading, lineno, snip, body, score in con.execute(
                    "SELECT path,heading,lineno,"
                    "snippet(tri,3,'»','«','…',14), body, bm25(tri) "
                    "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                    (fq, k * 3),
                ):
                    key = (path, lineno)
                    if key in results:
                        continue
                    bl = body.lower()
                    if sum(1 for g in grams if g in bl) < need:
                        continue
                    results[key] = {"path": path, "heading": heading, "line": int(lineno),
                                    "snippet": " ".join(snip.split()), "score": -float(score) * 0.3,
                                    "via": "fuzzy"}
            except sqlite3.OperationalError:
                pass
    con.close()
    return sorted(results.values(), key=lambda r: -r["score"])[:k]


# ─────────────────────────── stat ───────────────────────────
def cmd_stat(root: Path) -> dict:
    db = root / DB_REL
    if not db.exists():
        return {"indexed": False}
    con = sqlite3.connect(db)
    f = con.execute("SELECT count(*), coalesce(sum(size),0), coalesce(sum(chunks),0) FROM files").fetchone()
    links = con.execute("SELECT count(*) FROM links").fetchone()[0]
    areas = con.execute("SELECT count(DISTINCT area) FROM files").fetchone()[0]
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    con.close()
    return {"indexed": True, "files": f[0], "bytes": f[1], "chunks": f[2],
            "links": links, "areas": areas, "trigram": has_tri}


# ─────────────────────────── lint (онтология) ───────────────────────────
# Словари из docs/reference/gitmark-ontology.md (source of truth).
NODE_TYPES = {"service", "reference", "runbook", "gotcha", "decision",
              "plan", "guide", "report", "index", "memory"}
# Реальный словарь сервисов выводится per-repo из имён папок docs/services/*
# (см. cmd_lint). Здесь — только кросс-срезовый sentinel.
SERVICES = {"_platform"}
STATUSES = {"active", "draft", "deprecated", "archived"}
LOAD_BEARING = {"service", "reference", "runbook", "plan", "decision"}
LINK_KEYS = {"documents", "depends_on", "supersedes", "relates_to",
             "implemented_by", "part_of"}
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def strip_code(text: str) -> str:
    """Убрать fenced ``` и inline `code` — чтобы не ловить ссылки-примеры из кода."""
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


def parse_frontmatter(text: str) -> dict | None:
    """Мини-парсер YAML-frontmatter (stdlib, без pyyaml). Скаляры + плоские списки."""
    m = FM_RE.match(text)
    if not m:
        return None
    fm, cur_key = {}, None
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.lstrip().startswith("- ") and cur_key:
            fm.setdefault(cur_key, [])
            if isinstance(fm[cur_key], list):
                fm[cur_key].append(raw.lstrip()[2:].strip().strip("[]'\""))
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if not val:                                   # ключ вложенного блока/списка
            cur_key = key
            fm[key] = {} if key == "links" else []
            continue
        cur_key = None
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            fm[key] = val.strip("'\"")
    return fm


def cmd_lint(root: Path, paths: list | None = None) -> dict:
    """Проверка инвариантов онтологии I1–I6. Возвращает {errors, warnings, checked}."""
    docs = list(iter_md(root))
    known = {_nfc(p.relative_to(root).as_posix()) for p in docs}
    # граф связей: кто на кого ссылается (для I3 — сироты)
    out_links: dict = {}
    in_links: dict = {}
    issues = []  # (level, code, path, msg)
    fm_cache = {}
    sel = set(paths) if paths else None
    # словарь сервисов выводится per-repo: sentinel + имена всех папок под docs/
    # (сервис обычно = папка docs/services/<svc> или группирующая папка docs/<svc>)
    docs_root = root / "docs"
    services_vocab = set(SERVICES)
    if docs_root.exists():
        services_vocab |= {d.name for d in docs_root.rglob("*") if d.is_dir()}

    for p in docs:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        fm_cache[rel] = fm
        outs = set()
        for href in LINK_RE.findall(strip_code(text)):
            tgt = resolve_link(rel, href, known)
            if tgt:
                outs.add(tgt)
                in_links.setdefault(tgt, set()).add(rel)
            elif href.split("#")[0].endswith(".md") and not href.startswith(("http", "mailto:")):
                issues.append(("ERR", "I4", rel, f"битая ссылка → {href}"))
        out_links[rel] = outs

    # README на каждую docs/-папку (I5)
    docs_dirs = {p.parent for p in docs if p.relative_to(root).as_posix().startswith("docs/")}
    for d in sorted(docs_dirs):
        if not (d / "README.md").exists():
            issues.append(("WARN", "I5", d.relative_to(root).as_posix() + "/", "нет README.md (индекс папки)"))

    for p in docs:
        rel = p.relative_to(root).as_posix()
        if sel and rel not in sel:
            continue
        if not rel.startswith("docs/"):
            continue
        fm = fm_cache.get(rel)
        nt = (fm or {}).get("node_type")
        # I1 — несущий документ без frontmatter/типа
        looks_bearing = rel.startswith(("docs/reference/", "docs/ops/", "docs/plans/",
                                        "docs/decisions/", "docs/services/"))
        if not fm or not nt:
            if looks_bearing and Path(rel).name != "README.md":
                issues.append(("ERR", "I1", rel, "нет frontmatter с node_type"))
            continue
        # I2 — значения в словарях
        if nt not in NODE_TYPES:
            issues.append(("ERR", "I2", rel, f"node_type='{nt}' вне словаря"))
        svc = fm.get("service")
        if svc and svc not in services_vocab:
            issues.append(("WARN", "I2", rel, f"service='{svc}' вне словаря"))
        st = fm.get("status")
        if st and st not in STATUSES:
            issues.append(("WARN", "I2", rel, f"status='{st}' вне словаря"))
        # I3 — сироты (несущий тип без входящих/исходящих связей)
        if nt in LOAD_BEARING:
            has_link = bool(out_links.get(rel)) or bool(in_links.get(rel))
            links_fm = fm.get("links") if isinstance(fm.get("links"), dict) else {}
            if not has_link and not links_fm:
                issues.append(("WARN", "I3", rel, "сирота — нет связей (ни in, ни out)"))
        # I6 — supersedes-цель должна быть deprecated/archived
        links_fm = fm.get("links") if isinstance(fm.get("links"), dict) else {}
        for tgt in (links_fm.get("supersedes") or []):
            t = resolve_link(rel, tgt, known)
            if t:
                tfm = fm_cache.get(t) or {}
                if tfm.get("status") not in ("deprecated", "archived"):
                    issues.append(("WARN", "I6", rel, f"supersedes {tgt}, но он не deprecated/archived"))

    errs = [i for i in issues if i[0] == "ERR"]
    warns = [i for i in issues if i[0] == "WARN"]
    return {"issues": issues, "errors": errs, "warnings": warns,
            "checked": len(docs)}


# ─────────────────────────── map (HTML обзор + граф) ───────────────────────────
def cmd_map(root: Path, out: Path) -> dict:
    """Self-contained HTML: дерево + рендер md + радиальный граф от точки входа."""
    try:
        import markdown as _md
        render = lambda t: _md.markdown(t, extensions=["extra", "sane_lists", "tables", "toc"], output_format="html5")
        pretty = True
    except Exception:
        import html as _html
        render = lambda t: f"<pre>{_html.escape(t)}</pre>"
        pretty = False

    files, edges = {}, []
    raw = {}
    for p in iter_md(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        raw[rel] = text
        body = FM_RE.sub("", text, count=1)   # не рендерить YAML-frontmatter как текст
        files[rel] = {"rel": rel, "area": area_of(rel), "title": title_of(text, rel),
                      "size": len(text.encode("utf-8")), "html": render(body)}
    known = set(files)
    for rel, text in raw.items():
        seen = set()
        for m in LINK_RE.finditer(text):
            dst = resolve_link(rel, m.group(1).split()[0], known)
            if dst and dst != rel and dst not in seen:
                seen.add(dst)
                edges.append({"s": rel, "t": dst, "kind": "ref"})

    areas = sorted({f["area"] for f in files.values()})
    nodes = [{"id": "area::" + a, "label": a, "kind": "area", "area": a, "size": 0} for a in areas]
    nodes += [{"id": r, "label": f["title"], "kind": "doc", "area": f["area"],
               "size": f["size"], "rel": r} for r, f in files.items()]
    edges += [{"s": "area::" + f["area"], "t": r, "kind": "own"} for r, f in files.items()]

    # BFS-уровни/углы от точки входа (CLAUDE.md|README.md|первый)
    import math
    from collections import defaultdict, deque
    nid = {n["id"]: n for n in nodes}
    root_id = next((c for c in ("CLAUDE.md", "README.md") if c in nid), nodes[0]["id"])
    adj = defaultdict(set)
    for e in edges:
        adj[e["s"]].add(e["t"]); adj[e["t"]].add(e["s"])
    level, parent, dq = {root_id: 0}, {root_id: None}, deque([root_id])
    while dq:
        u = dq.popleft()
        for v in sorted(adj[u]):
            if v not in level:
                level[v] = level[u] + 1; parent[v] = u; dq.append(v)
    children = defaultdict(list)
    for v, p in parent.items():
        if p is not None:
            children[p].append(v)
    leaf = {}
    def _cnt(n):
        if not children[n]:
            leaf[n] = 1; return 1
        s = sum(_cnt(c) for c in children[n]); leaf[n] = s; return s
    _cnt(root_id)
    ang = {}
    def _asn(n, a0, a1):
        ang[n] = (a0 + a1) / 2
        ch = sorted(children[n], key=lambda c: c)
        if not ch:
            return
        tot = sum(leaf[c] for c in ch) or 1; cur = a0
        for c in ch:
            sp = (a1 - a0) * leaf[c] / tot; _asn(c, cur, cur + sp); cur += sp
    _asn(root_id, 0, 2 * math.pi)
    maxlvl = max(level.values()) if level else 0
    unreached = [n["id"] for n in nodes if n["id"] not in level]
    for i, u in enumerate(unreached):
        level[u] = maxlvl + 1; ang[u] = 2 * math.pi * i / max(1, len(unreached))
    treeset = set()
    for v, p in parent.items():
        if p is not None:
            treeset.add((p, v)); treeset.add((v, p))
    for n in nodes:
        n["lvl"] = level.get(n["id"], maxlvl + 1); n["ang"] = round(ang.get(n["id"], 0), 4)
        n["root"] = n["id"] == root_id
    for e in edges:
        e["tree"] = (e["s"], e["t"]) in treeset

    data = {"files": files, "nodes": nodes, "edges": edges, "root": root_id,
            "stats": {"files": len(files), "areas": len(areas),
                      "refs": sum(1 for e in edges if e["kind"] == "ref"),
                      "bytes": sum(f["size"] for f in files.values()),
                      "maxlvl": maxlvl, "unreached": len(unreached), "pretty": pretty}}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = _MAP_HTML.replace("__DATA__", payload).replace("__ROOTNAME__", root.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"out": str(out), "files": len(files), "refs": data["stats"]["refs"],
            "unreached": len(unreached), "pretty": pretty}


# ─────────────────────────── serve ───────────────────────────
def cmd_serve(root: Path, port: int):
    import http.server, socketserver, functools
    docroot = root / "docs"
    if not (docroot / "docs-map.html").exists():
        docroot = root
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(docroot))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"GitMark serve → http://127.0.0.1:{port}/docs-map.html  (Ctrl-C стоп)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nстоп")


# ─────────────────────────── CLI ───────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitmark", description="GitMark Memory Bank — md+git knowledge base CLI")
    ap.add_argument("--root", default=None, help="корень репо (по умолчанию — авто по .git)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="построить индекс").add_argument("--force", action="store_true")
    sp = sub.add_parser("search", help="искать"); sp.add_argument("query"); sp.add_argument("-k", type=int, default=8); sp.add_argument("--json", action="store_true")
    mp = sub.add_parser("map", help="HTML обзор+граф"); mp.add_argument("-o", "--out", default=None)
    sv = sub.add_parser("serve", help="локальный http"); sv.add_argument("-p", "--port", type=int, default=8799)
    sub.add_parser("stat", help="статистика")
    lp = sub.add_parser("lint", help="проверить онтологию (I1–I6)")
    lp.add_argument("paths", nargs="*", help="ограничить файлами (по умолчанию — все docs/)")
    lp.add_argument("--strict", action="store_true", help="exit 1 при любых ERR")
    sub.add_parser("version", help="версия")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else repo_root(Path.cwd())

    if a.cmd == "index":
        r = cmd_index(root, a.force)
        print(f"✓ index: {r['files']} файлов · {r['chunks']} чанков · {r['links']} ссылок · "
              f"trigram={'on' if r['trigram'] else 'OFF'} → {r['db']}")
    elif a.cmd == "search":
        res = cmd_search(root, a.query, a.k)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2)); return
        if not res:
            print("ничего не найдено"); return
        for r in res:
            head = f" › {r['heading']}" if r["heading"] else ""
            print(f"\033[32m{r['path']}:{r['line']}\033[0m{head}  \033[90m[{r['via']}]\033[0m")
            print(f"   {r['snippet'][:200]}")
    elif a.cmd == "map":
        out = Path(a.out) if a.out else (root / "docs" / "docs-map.html")
        r = cmd_map(root, out)
        warn = "" if r["pretty"] else "  (raw md — `pip install markdown` для рендера)"
        print(f"✓ map: {r['out']} · {r['files']} файлов · {r['refs']} ссылок · "
              f"{r['unreached']} вне индекса{warn}")
    elif a.cmd == "serve":
        cmd_serve(root, a.port)
    elif a.cmd == "stat":
        s = cmd_stat(root)
        if not s.get("indexed"):
            print("индекс не построен — `gitmark index`"); return
        print(f"GitMark · {s['files']} файлов · {s['areas']} папок · {s['chunks']} чанков · "
              f"{s['links']} ссылок · {s['bytes']//1024} KB · trigram={'on' if s['trigram'] else 'off'}")
    elif a.cmd == "lint":
        r = cmd_lint(root, a.paths or None)
        order = {"ERR": 0, "WARN": 1}
        codes = {}
        for lvl, code, path, msg in sorted(r["issues"], key=lambda x: (order[x[0]], x[1], x[2])):
            codes[code] = codes.get(code, 0) + 1
            col = "\033[31m" if lvl == "ERR" else "\033[33m"
            print(f"{col}{lvl}\033[0m \033[90m{code}\033[0m {path} — {msg}")
        ne, nw = len(r["errors"]), len(r["warnings"])
        summary = " · ".join(f"{c}×{n}" for c, n in sorted(codes.items())) or "—"
        mark = "\033[32m✓ чисто\033[0m" if ne == 0 and nw == 0 else f"\033[31m{ne} ERR\033[0m · \033[33m{nw} WARN\033[0m"
        print(f"\n{mark}  ({r['checked']} файлов · {summary})")
        if a.strict and ne:
            sys.exit(1)
    elif a.cmd == "version":
        print(f"gitmark {VERSION}")


# HTML-шаблон map (дерево + рендер + радиальный граф). __DATA__/__ROOTNAME__ инжектятся.
_MAP_HTML = r"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GitMark · __ROOTNAME__</title>
<style>
:root{--bg:#0a0a0a;--panel:#121212;--panel2:#171717;--border:#262626;--fg:#e8e8e8;--muted:#8a8a8a;--accent:#00ff88;
--mono:"SF Mono",ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Arial,sans-serif;
--docs:#00ff88;--services:#7c9cff;--root:#9aa0a6;}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
#top{display:flex;align-items:center;gap:14px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--panel)}
#top h1{font-family:var(--mono);font-size:15px;margin:0;font-weight:600}
#top .stat{color:var(--muted);font-size:12.5px;font-family:var(--mono)}
#search{flex:0 0 240px;margin-left:auto;background:var(--panel2);border:1px solid var(--border);color:var(--fg);padding:6px 10px;border-radius:7px;font-family:var(--mono);font-size:13px;outline:none}
.tabs{display:flex;gap:4px}.tab{cursor:pointer;padding:6px 14px;border-radius:7px;border:1px solid var(--border);background:var(--panel2);color:var(--muted);font-family:var(--mono);font-size:13px}
.tab.on{color:var(--bg);background:var(--accent);border-color:var(--accent);font-weight:600}
#main{display:flex;height:calc(100vh - 53px)}
#side{flex:0 0 320px;overflow:auto;border-right:1px solid var(--border);background:var(--panel);padding:8px 0}
.grp{padding:7px 14px 3px;font-family:var(--mono);font-size:11.5px;color:var(--muted);text-transform:uppercase;cursor:pointer;display:flex;justify-content:space-between;position:sticky;top:0;background:var(--panel)}
.grp:hover{color:var(--fg)}.grp .car{display:inline-block;width:10px;color:var(--muted);font-size:10px}
.it{padding:4px 14px 4px 26px;cursor:pointer;font-size:13.5px;border-left:2px solid transparent;color:#cfcfcf}
.it:hover{background:var(--panel2)}.it.on{border-left-color:var(--accent);background:var(--panel2);color:#fff}
.it .p{display:block;font-family:var(--mono);font-size:10.5px;color:var(--muted)}.hidden{display:none}
#doc{flex:1;overflow:auto;padding:34px 44px 80px}#docwrap{max-width:860px;margin:0 auto}
#crumb{font-family:var(--mono);font-size:12px;color:var(--muted);margin-bottom:18px}#crumb b{color:var(--accent)}
.md h1{font-family:var(--mono);font-size:1.9rem;border-bottom:1px solid var(--border);padding-bottom:.35em}
.md h2{font-family:var(--mono);font-size:1.4rem;border-bottom:1px solid var(--border);padding-bottom:.3em;margin-top:2em}
.md h3{font-family:var(--mono);color:var(--accent)}.md{line-height:1.7}
.md code{font-family:var(--mono);background:#1d1d1d;padding:.12em .4em;border-radius:4px;color:#ffd479}
.md pre{background:#0e0e0e;border:1px solid var(--border);border-radius:8px;padding:14px;overflow:auto}.md pre code{background:none;color:#dcdcdc}
.md table{border-collapse:collapse;font-size:13.5px;display:block;overflow:auto}.md th,.md td{border:1px solid var(--border);padding:6px 10px}
.md blockquote{border-left:3px solid var(--accent);padding:.3em 1em;color:var(--muted);background:#0e0e0e}
#gp{flex:1;position:relative;overflow:hidden}#cv{display:block;width:100%;height:100%;cursor:grab}
#legend{position:absolute;top:12px;left:12px;background:rgba(18,18,18,.9);border:1px solid var(--border);border-radius:8px;padding:10px;font-family:var(--mono);font-size:12px}
#legend .row{display:flex;align-items:center;gap:7px;margin:3px 0}#legend .dot{width:10px;height:10px;border-radius:50%}
#tip{position:absolute;pointer-events:none;background:#000;border:1px solid var(--accent);border-radius:6px;padding:6px 9px;font-family:var(--mono);font-size:12px;max-width:340px;display:none}
#tip .s{color:var(--muted);font-size:10.5px}
#gctl{position:absolute;top:12px;right:12px;background:rgba(18,18,18,.92);border:1px solid var(--border);border-radius:8px;padding:9px 11px;font-family:var(--mono);font-size:12px;display:flex;flex-direction:column;gap:6px}
#gctl label{display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted)}#gctl label:hover{color:var(--fg)}
#gctl input{accent-color:var(--accent);cursor:pointer}#gctl #reheat{color:var(--accent);border-top:1px solid var(--border);padding-top:6px}
#ghint{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);background:rgba(18,18,18,.85);border:1px solid var(--border);border-radius:7px;padding:5px 12px;font-family:var(--mono);font-size:11.5px;color:var(--muted);white-space:nowrap}
</style></head><body>
<div id="top"><h1>🧠 GitMark · __ROOTNAME__</h1><span class="stat" id="stat"></span>
<div class="tabs"><div class="tab on" data-v="browse">Дерево</div><div class="tab" data-v="graph">Граф</div></div>
<input id="search" placeholder="фильтр файлов…"></div>
<div id="main">
  <div id="side"></div>
  <div id="doc"><div id="docwrap"><div id="crumb"></div><div class="md" id="mdbody"></div></div></div>
  <div id="gp" class="hidden"><canvas id="cv"></canvas><div id="legend"></div>
    <div id="gctl">
      <label><input type="checkbox" id="showref" checked> ссылки между доками</label>
      <label><input type="checkbox" id="showown" checked> папка→док</label>
      <label><input type="checkbox" id="physics"> свободный режим (гравитация)</label>
      <label id="reheat">↻ пересобрать рост</label>
    </div>
    <div id="ghint">🕸 центр = точка входа · ЛКМ-таскать · колесо-зум · клик по точке → открыть док</div>
    <div id="tip"></div></div>
</div>
<script>
const DATA=__DATA__,F=DATA.files,$=s=>document.querySelector(s);
// группа узла = сервис (docs/services/<svc> или services/<svc>), иначе docs/root.
function svcOf(a){const m=a.match(/^docs\/services\/([^/]+)/)||a.match(/^services\/([^/]+)/);return m?m[1]:null;}
function grp(a){return svcOf(a)||(a.startsWith('docs')?'docs':'root');}
const PAL=['#7c9cff','#ff6ec7','#ffb454','#4ec9ff','#c586ff','#f7768e','#9ece6a','#e0af68','#bb9af7','#73daca','#ff9e64','#7dcfff'];
const _cc={};
function colorOf(g){if(g==='docs')return '#00ff88';if(g==='root')return '#9aa0a6';
 if(_cc[g])return _cc[g];let h=0;for(let i=0;i<g.length;i++)h=(h*31+g.charCodeAt(i))>>>0;return _cc[g]=PAL[h%PAL.length];}
const COL=new Proxy({},{get:(_,k)=>colorOf(k)});  // COL[grp(area)] совместимость
function esc(s){return(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
$('#stat').textContent=DATA.stats.files+' файлов · '+DATA.stats.areas+' папок · '+DATA.stats.refs+' ссылок · '+(DATA.stats.bytes/1024).toFixed(0)+' KB';
const byArea={};DATA.nodes.filter(n=>n.kind==='doc').forEach(n=>{(byArea[n.area]=byArea[n.area]||[]).push(n);});
const side=$('#side');
Object.keys(byArea).sort().forEach(a=>{const items=byArea[a].sort((x,y)=>x.rel.localeCompare(y.rel));
 const g=document.createElement('div');g.className='grp';
 g.innerHTML='<span><span class="car">▸</span> <span style="color:'+COL[grp(a)]+'">'+a+'</span></span><span>'+items.length+'</span>';side.appendChild(g);
 const box=document.createElement('div');box.className='gbox hidden';  // свёрнуто по умолчанию
 items.forEach(n=>{const d=document.createElement('div');d.className='it';d.dataset.rel=n.rel;
  d.innerHTML=esc(n.label)+'<span class="p">'+esc(n.rel)+'</span>';d.onclick=()=>open(n.rel);box.appendChild(d);});side.appendChild(box);
 g.onclick=()=>{const h=box.classList.toggle('hidden');g.querySelector('.car').textContent=h?'▸':'▾';};});
function open(rel){const f=F[rel];if(!f)return;$('#mdbody').innerHTML=f.html;
 $('#crumb').innerHTML='<b>'+esc(f.area)+'</b> / '+esc(rel.split('/').pop())+' · '+(f.size/1024).toFixed(1)+' KB';
 document.querySelectorAll('.it').forEach(x=>x.classList.toggle('on',x.dataset.rel===rel));
 const act=document.querySelector('.it.on');if(act){const bx=act.parentElement;if(bx.classList.contains('hidden')){bx.classList.remove('hidden');const gg=bx.previousElementSibling,c=gg&&gg.querySelector('.car');if(c)c.textContent='▾';}}
 $('#mdbody').querySelectorAll('a[href]').forEach(an=>{const h=an.getAttribute('href');const b=(h||'').split('#')[0].split('/').pop();
  const hit=Object.keys(F).filter(r=>r.endsWith('/'+b)||r===b);if(b&&b.endsWith('.md')&&hit.length===1){an.onclick=e=>{e.preventDefault();open(hit[0]);$('#doc').scrollTop=0;};}});
 $('#doc').scrollTop=0;}
$('#search').oninput=e=>{const q=e.target.value.toLowerCase();
 document.querySelectorAll('.it').forEach(it=>{it.style.display=it.textContent.toLowerCase().includes(q)?'':'none';});
 document.querySelectorAll('.gbox').forEach(b=>b.classList.toggle('hidden',!q));      // при поиске разворачиваем, пусто — сворачиваем
 document.querySelectorAll('.grp .car').forEach(c=>c.textContent=q?'▾':'▸');};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
 const g=t.dataset.v==='graph';$('#side').classList.toggle('hidden',g);$('#doc').classList.toggle('hidden',g);$('#gp').classList.toggle('hidden',!g);if(g){resize();if(!G.on)startG();}});
/* радиальный граф */
const cv=$('#cv'),ctx=cv.getContext('2d');const G={on:false,view:{x:0,y:0,k:1},drag:null,hover:null,grow:0,maxlvl:DATA.stats.maxlvl};
function resize(){const r=cv.getBoundingClientRect();cv.width=r.width*devicePixelRatio;cv.height=r.height*devicePixelRatio;ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);G.W=r.width;G.H=r.height;if(G.on)layout();}
addEventListener('resize',()=>{if(!$('#gp').classList.contains('hidden'))resize();});
function layout(){const cx=G.W/2,cy=G.H/2,gap=Math.min(G.W,G.H)*0.46/(G.maxlvl+2);G.cx=cx;G.cy=cy;G.gap=gap;
 G.nodes.forEach(n=>{n.tx=n.root?cx:cx+n.lvl*gap*Math.cos(n.ang);n.ty=n.root?cy:cy+n.lvl*gap*Math.sin(n.ang);});}
function startG(){G.on=true;const idx={};G.nodes=DATA.nodes.map((n,i)=>{idx[n.id]=i;return{...n,x:G.W/2,y:G.H/2,vx:0,vy:0,
 r:n.root?13:(n.kind==='area'?7:3+Math.min(6,Math.sqrt(n.size/1100))),col:n.root?'#fff':COL[grp(n.area)]};});
 G.edges=DATA.edges.map(e=>({s:idx[e.s],t:idx[e.t],kind:e.kind,tree:e.tree})).filter(e=>e.s!=null&&e.t!=null);
 layout();legend();G.grow=0;loop();}
function spider(){if(G.grow<1)G.grow=Math.min(1,G.grow+0.02);const g=1-Math.pow(1-G.grow,3);
 G.nodes.forEach(n=>{if(n===G.drag)return;n.x+=(G.cx+(n.tx-G.cx)*g-n.x)*0.14;n.y+=(G.cy+(n.ty-G.cy)*g-n.y)*0.14;});}
function physics(){const N=G.nodes,rep=2200,k=.045;
 for(let i=0;i<N.length;i++){const a=N[i];for(let j=i+1;j<N.length;j++){const b=N[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+.01,d=Math.sqrt(d2),f=rep/d2;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}}
 G.edges.forEach(e=>{const a=N[e.s],b=N[e.t];let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,L=e.kind==='own'?70:150,f=(d-L)*k*(e.kind==='own'?1.4:.5);a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;});
 N.forEach(n=>{n.vx+=(G.W/2-n.x)*.001;n.vy+=(G.H/2-n.y)*.001;if(n===G.drag)return;n.x+=n.vx*=.85;n.y+=n.vy*=.85;});}
function loop(){($('#physics').checked?physics:spider)();draw();requestAnimationFrame(loop);}
function draw(){ctx.clearRect(0,0,G.W,G.H);const v=G.view;ctx.save();ctx.translate(v.x,v.y);ctx.scale(v.k,v.k);
 const sr=$('#showref').checked,so=$('#showown').checked,phys=$('#physics').checked;
 if(!phys){ctx.strokeStyle='rgba(255,255,255,.04)';for(let l=1;l<=G.maxlvl;l++){ctx.beginPath();ctx.arc(G.cx,G.cy,l*G.gap*(1-Math.pow(1-G.grow,3)),0,7);ctx.stroke();}}
 G.edges.forEach(e=>{if(e.tree||(e.kind==='ref'&&!sr)||(e.kind==='own'&&!so))return;const a=G.nodes[e.s],b=G.nodes[e.t];ctx.strokeStyle=e.kind==='ref'?'rgba(0,255,136,.13)':'rgba(120,120,120,.07)';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
 G.edges.forEach(e=>{if(!e.tree||(e.kind==='ref'&&!sr)||(e.kind==='own'&&!so))return;const a=G.nodes[e.s],b=G.nodes[e.t],par=a.lvl<=b.lvl?a:b,hot=G.hover&&(a===G.hover||b===G.hover);
  ctx.strokeStyle=hot?'rgba(255,255,255,.7)':(COL[grp(par.area)]+'66');ctx.lineWidth=hot?1.6:1;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
 G.nodes.forEach(n=>{if(n.root){ctx.beginPath();ctx.arc(n.x,n.y,n.r+7,0,7);ctx.fillStyle='rgba(255,255,255,.1)';ctx.fill();}
  ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,7);ctx.fillStyle=n.col;ctx.fill();if(n.kind==='area'||n.root){ctx.strokeStyle=n.root?'#00ff88':'#000';ctx.lineWidth=n.root?2:1.4;ctx.stroke();}
  if(n.root||n.kind==='area'||n===G.hover||v.k>1.7){ctx.fillStyle=n.root?'#00ff88':(n===G.hover?'#fff':'#bdbdbd');ctx.font=((n.root||n.kind==='area')?'600 ':'')+(n.root?13:11)+'px '+getComputedStyle(document.body).getPropertyValue('--mono');
   ctx.fillText(n.root?(DATA.root+' ◀ вход'):(n.kind==='area'?n.label:n.label.slice(0,30)),n.x+n.r+4,n.y+3);}});ctx.restore();}
function legend(){const groups={};DATA.nodes.forEach(n=>{const g=grp(n.area);groups[g]=(groups[g]||0)+1;});
 const rank=x=>x==='docs'?1:x==='root'?2:0;  // сервисы первыми, docs/root в конце
 const order=Object.keys(groups).sort((a,b)=>rank(a)-rank(b)||a.localeCompare(b));
 const lbl={docs:'docs/* (сквозное)',root:'корень/прочее'};
 $('#legend').innerHTML='<div class="row" style="color:#00ff88">● '+DATA.root+' = вход</div>'+
  order.map(g=>'<div class="row"><span class="dot" style="background:'+colorOf(g)+'"></span>'+(lbl[g]||g)+' <span style="color:var(--muted)">'+groups[g]+'</span></div>').join('')+
  '<div class="row" style="color:var(--muted);margin-top:4px">кольцо = шагов от входа</div>';}
function tw(mx,my){return{x:(mx-G.view.x)/G.view.k,y:(my-G.view.y)/G.view.k};}
function pick(mx,my){const w=tw(mx,my);let b=null,bd=1e9;G.nodes.forEach(n=>{const d=(n.x-w.x)**2+(n.y-w.y)**2;if(d<bd&&d<(n.r+6)**2){bd=d;b=n;}});return b;}
cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect();const n=pick(e.clientX-r.left,e.clientY-r.top);G.moved=false;if(n){G.drag=n;G.ds={x:e.clientX,y:e.clientY};}else G.pan={x:e.clientX,y:e.clientY,vx:G.view.x,vy:G.view.y};});
cv.addEventListener('mousemove',e=>{const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
 if(G.drag){const w=tw(mx,my);G.drag.x=w.x;G.drag.y=w.y;if(G.ds&&Math.abs(e.clientX-G.ds.x)+Math.abs(e.clientY-G.ds.y)>4)G.moved=true;return;}
 if(G.pan){G.view.x=G.pan.vx+(e.clientX-G.pan.x);G.view.y=G.pan.vy+(e.clientY-G.pan.y);return;}
 const n=pick(mx,my);G.hover=n;const tip=$('#tip');if(n){tip.style.display='block';tip.style.left=(mx+16)+'px';tip.style.top=(my+12)+'px';
  tip.innerHTML='<div>'+esc(n.root?DATA.root:n.label)+'</div><div class="s">'+esc(n.kind==='area'?n.area:(n.rel||'')+' · '+n.lvl+' шаг(ов)')+'</div>';}else tip.style.display='none';});
addEventListener('mouseup',()=>{if(G.drag){const n=G.drag;G.drag=null;if(!G.moved&&n.kind==='doc'){document.querySelector('.tab[data-v=browse]').click();open(n.rel);}}G.pan=null;});
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,f=e.deltaY<0?1.12:.89,w=tw(mx,my);G.view.k=Math.max(.3,Math.min(5,G.view.k*f));G.view.x=mx-w.x*G.view.k;G.view.y=my-w.y*G.view.k;},{passive:false});
$('#reheat').onclick=()=>{if(!G.on)return;layout();G.nodes.forEach(n=>{n.x=G.cx;n.y=G.cy;n.vx=0;n.vy=0;});G.grow=0;};
$('#physics').onchange=()=>{if(!$('#physics').checked&&G.on){layout();G.grow=1;}};
open(DATA.files[DATA.root]?DATA.root:Object.keys(F)[0]);
</script></body></html>"""

if __name__ == "__main__":
    main()
