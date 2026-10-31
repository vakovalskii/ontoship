#!/usr/bin/env python3
"""GitMark Memory Bank — CLI для базы знаний на чистом md + README + git.

Source of truth = markdown. Всё производное (поисковый индекс, HTML-обзор, граф)
регенерится из md → git остаётся чистым (.gitmark/ в .gitignore).

Поиск: SQLite FTS5 — несколько каналов (bm25 по чанкам, bm25 по title/description/
tags, trigram-подстрока, fuzzy 4-граммы), слитые RRF на уровне
файлов. Frontmatter (тип, статус, trust, stale) — в индексе и в фильтрах.
Чистый python stdlib (PyYAML — опционально, для полного YAML). Оффлайн.

Команды:
    gitmark [--root .] index                  построить индекс
    gitmark search "<q>" [-k 8] [--json] [--type T] [--status S] [--service S]
                   [--scope all|live|history]
    gitmark query  [key=val key!=val key~sub key>val key? !key …] [--fields …]
                   [--count-by key] [--json]  структурный запрос по frontmatter
    gitmark map   [-o docs/docs-map.html]     self-contained HTML: дерево+рендер+граф
    gitmark serve [-p 8799]                   локальный http для просмотра HTML
    gitmark stat                              статистика индекса/БЗ
    gitmark lint  [paths…] [--strict] [--json] [--baseline REF]  проверить онтологию (I1–I9);
                   с --baseline падает, только если замечаний стало больше, чем в REF (храповик)
    gitmark health [--json]                   скоркард БЗ: типизация, связи, trust, stale
    gitmark toc   [--write]                   собрать блоки <!-- gitmark:toc --> в README-индексах
    gitmark secrets [files…]                  найти в БЗ токены/ключи/пароли (exit 1 — нашёл)
    gitmark attrs [--write]                   .gitattributes: README-индексы docs/ — merge=union
    gitmark version

Совместимо с Open Knowledge Format (OKF v0.2): `type` = синоним `node_type`,
`description` (синоним `summary`), `generated`/`verified` (trust tier), `stale_after`,
`status: stable`, ссылки от корня (`/path.md`). `status: frozen` — снимок на дату, не правится.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

_yaml = False                           # PyYAML грузится лениво (поиску он не нужен)


def _load_yaml():
    """PyYAML, если установлен (полный YAML); иначе None → мини-парсер."""
    global _yaml
    if _yaml is False:
        try:
            import yaml as _y
            _yaml = _y
        except Exception:
            _yaml = None
    return _yaml

VERSION = "0.3.1"

EXCLUDE_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".pytest_cache", "_vendor", ".venv", "venv", "vendor",
    ".gitmark", ".worktrees",
}
IGNORE_FILE = ".gitmarkignore"
DB_REL = ".gitmark/index.db"
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)
FM_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


# ─────────────────────────── discovery ───────────────────────────
def repo_root(start: Path) -> Path:
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return p


def ignored_prefixes(root: Path) -> tuple:
    """.gitmarkignore в корне: путь на строку (папка или файл от корня), # — комментарий."""
    f = root / IGNORE_FILE
    if not f.exists():
        return ()
    return tuple(l.strip().strip("/") for l in f.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#"))


def _git_md(root: Path) -> list | None:
    """*.md, которые видит git (tracked + untracked, без .gitignore). None — не git-репо."""
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard",
                            "-z", "--", "*.md"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]


def iter_md(root: Path):
    """Markdown базы знаний. В git-репо — то, что видит git (вендорные/игнорируемые
    папки отпадают сами), иначе обход ФС. Плюс EXCLUDE_DIRS и .gitmarkignore."""
    skip = ignored_prefixes(root)
    rels = _git_md(root)
    if rels is None or os.environ.get("GITMARK_NO_GIT"):
        rels = [p.relative_to(root).as_posix() for p in root.rglob("*.md")]
    for rel in sorted(set(rels)):
        parts = rel.split("/")
        if any(part in EXCLUDE_DIRS for part in parts[:-1]):
            continue
        if any(rel == s or rel.startswith(s + "/") for s in skip):
            continue
        p = root / rel
        if p.is_file():
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


def chunk_md(text: str, line_offset: int = 0):
    """Чанкуем по заголовкам → (line_start, heading, body). line_offset — строки frontmatter."""
    lines = text.split("\n")
    chunks, cur = [], {"line": 1 + line_offset, "heading": "", "body": []}
    in_fence = False
    for i, ln in enumerate(lines, 1 + line_offset):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEAD_RE.match(ln)
        if m:
            if cur["body"] or cur["heading"]:
                chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
            cur = {"line": i, "heading": m.group(2).strip(), "body": [ln]}
        else:
            cur["body"].append(ln)
    if any(x.strip() for x in cur["body"]) or cur["heading"]:
        chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
    return chunks


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def resolve_link(src_rel: str, href: str, known: set) -> str | None:
    """md-ссылка → путь известного дока. `/x.md` — от корня (OKF), иначе от папки src."""
    import posixpath
    href = _nfc(href.split("#")[0].strip().strip("<>"))
    if not href or not href.endswith(".md") or href.startswith(("http", "mailto:")):
        return None
    src_dir = Path(_nfc(src_rel)).parent
    if href.startswith("/"):
        cands = [href.lstrip("/")]
    else:
        cands = [posixpath.normpath((src_dir / href).as_posix()), href.lstrip("./")]
    for c in cands:
        if c in known:
            return c
    base = Path(href).name
    hits = [r for r in known if r.endswith("/" + base) or r == base]
    return hits[0] if len(hits) == 1 else None


# ─────────────────────────── frontmatter ───────────────────────────
def split_frontmatter(text: str):
    """→ (yaml-текст | None, тело, число строк, занятых frontmatter)."""
    m = FM_RE.match(text)
    if not m:
        return None, text.lstrip("﻿"), 0
    return m.group(1), text[m.end():], m.group(0).count("\n")


def _plain(v):
    """PyYAML-значения → JSON-совместимые (даты → ISO-строки)."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat().replace("+00:00", "Z")
    return v


def _strip_comment(line: str) -> str:
    """Убрать YAML-комментарий ` # …` вне кавычек."""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _split_top(s: str) -> list:
    """Разбить по запятым верхнего уровня (не внутри [] {} и кавычек)."""
    out, depth, q, cur = [], 0, None, ""
    for ch in s:
        if q:
            q = None if ch == q else q
        elif ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


_KV_RE = re.compile(r"^([^\s'\"\[{#-][^:]*?|\"[^\"]*\"|'[^']*'):(?:\s+(.*)|\s*)$")


def _scalar(v: str):
    v = v.strip()
    if not v:
        return None
    if v[0] == "[" and v[-1] == "]":
        return [_scalar(x) for x in _split_top(v[1:-1])]
    if v[0] == "{" and v[-1] == "}":
        d = {}
        for part in _split_top(v[1:-1]):
            m = _KV_RE.match(part)
            if m:
                d[m.group(1).strip("'\"")] = _scalar(m.group(2) or "")
        return d
    if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _mini_yaml(raw: str) -> dict:
    """Мини-парсер YAML (stdlib): скаляры, flow [..]/{..}, вложенные map/list по отступам,
    блочные скаляры | и >, комментарии. Покрывает то, что реально пишут во frontmatter."""
    root: dict = {}
    stack = [(-1, root)]          # (мин. отступ элементов, контейнер)
    pending = None                # (отступ, dict, ключ) — ключ без значения ждёт блок
    block = None                  # (отступ ключа, dict, ключ, style, [строки]) — | или >
    for line in raw.split("\n"):
        if block:
            ind = len(line) - len(line.lstrip(" \t"))
            if not line.strip() or ind > block[0]:
                block[4].append(line.strip())
                continue
            sep = "\n" if block[3] == "|" else " "
            block[1][block[2]] = sep.join(block[4]).strip()
            block = None
        line = _strip_comment(line).rstrip()
        if not line.strip():
            continue
        ind = len(line) - len(line.lstrip(" \t"))
        s = line.strip()
        is_item = s == "-" or s.startswith("- ")
        if pending:
            pind, pd, pkey = pending
            pending = None
            if ind > pind or (is_item and ind == pind):
                cont = [] if is_item else {}
                pd[pkey] = cont
                stack.append((ind, cont))
        while len(stack) > 1:
            mind, cont = stack[-1]
            if ind < mind or (ind == mind and isinstance(cont, list) != is_item):
                stack.pop()
            else:
                break
        cont = stack[-1][1]
        if is_item and isinstance(cont, list):
            val = s[1:].strip()
            m = _KV_RE.match(val) if val and val[0] not in "[{'\"" else None
            if m:                                     # "- key: v" — map внутри списка
                d = {}
                cont.append(d)
                stack.append((ind + 2, d))
                k, v = m.group(1).strip("'\""), m.group(2) or ""
                d[k] = _scalar(v) if v else None
                if not v:
                    pending = (ind + 2, d, k)
            else:
                cont.append(_scalar(val))
            continue
        if not isinstance(cont, dict):
            continue
        m = _KV_RE.match(s)
        if not m:
            continue
        k, v = m.group(1).strip("'\""), (m.group(2) or "").strip()
        if v in ("|", ">", "|-", ">-", "|+", ">+"):
            block = (ind, cont, k, v[0], [])
            cont[k] = ""
        elif not v:
            cont[k] = None
            pending = (ind, cont, k)
        else:
            cont[k] = _scalar(v)
    if block:
        block[1][block[2]] = ("\n" if block[3] == "|" else " ").join(block[4]).strip()
    return root


def parse_frontmatter(text: str) -> dict | None:
    """YAML-frontmatter → dict (None — нет frontmatter). PyYAML если есть, иначе мини-парсер;
    мини-парсер же — фолбэк на «почти-YAML», который PyYAML не принимает (`title: a: b`)."""
    raw, _, _ = split_frontmatter(text)
    if raw is None:
        return None
    y = _load_yaml()
    if y is not None:
        try:
            data = y.safe_load(raw)
            if isinstance(data, dict):
                return _plain(data)
        except Exception:
            pass
    return _mini_yaml(raw)


def as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None and x != ""]
    return [v]


def trust_tier(fm: dict) -> str:
    """OKF §5.3: нет verified → unverified; только машины → machine; есть human: → human."""
    ver = (fm or {}).get("verified")
    if not ver:
        return "unverified"
    evs = ver if isinstance(ver, list) else [ver]
    bys = [str(e.get("by", "")) if isinstance(e, dict) else str(e) for e in evs]
    return "human" if any(b.startswith("human:") for b in bys) else "machine"


def _parse_when(v) -> _dt.datetime | None:
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = _dt.datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def is_stale(fm: dict, now: _dt.datetime | None = None) -> bool:
    """OKF §5.5: stale, когда now >= stale_after."""
    when = _parse_when((fm or {}).get("stale_after"))
    return bool(when and (now or _dt.datetime.now(_dt.timezone.utc)) >= when)


def node_type_of(fm: dict | None) -> str | None:
    """`node_type` (OntoShip) или `type` (OKF)."""
    fm = fm or {}
    nt = fm.get("node_type") or fm.get("type")
    return str(nt).strip() if nt else None


def fm_links(fm: dict | None) -> list:
    """Типизированные связи из `links:` → [(link_type, target), …]."""
    links = (fm or {}).get("links")
    if not isinstance(links, dict):
        return []
    return [(str(lt), str(t).strip()) for lt, ts in links.items()
            for t in as_list(ts) if str(t).strip()]


def load_doc(root: Path, p: Path) -> dict | None:
    """Прочитать md → {rel, text, body, offset, fm, meta}. None — не читается."""
    rel = p.relative_to(root).as_posix()
    try:
        text = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    except OSError:
        return None
    _, body, offset = split_frontmatter(text)
    fm = parse_frontmatter(text) or {}
    gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
    meta = {
        "node_type": node_type_of(fm) or "",
        "title": str(fm.get("title") or title_of(body, rel)),
        "description": str(fm.get("description") or fm.get("summary") or ""),   # summary — синоним
        "tags": [str(x) for x in as_list(fm.get("tags"))],
        "aliases": [str(x) for x in as_list(fm.get("aliases"))],
        "status": str(fm.get("status") or ""),
        "service": [x.strip() for v in as_list(fm.get("service")) for x in str(v).split(",") if x.strip()],
        "updated": str(fm.get("updated") or gen.get("at") or ""),   # OKF: generated.at
        "trust": trust_tier(fm),
        "stale": is_stale(fm),
    }
    return {"rel": rel, "text": text, "body": body, "offset": offset,
            "fm": fm if text.startswith(("---", "﻿---")) else None, "meta": meta}


def load_docs(root: Path) -> dict:
    out = {}
    for p in iter_md(root):
        d = load_doc(root, p)
        if d:
            out[_nfc(d["rel"])] = d
    return out


def doc_edges(d: dict, known: set) -> list:
    """Все связи дока: (dst, link_type, kind). kind: doc|code|url|missing."""
    rel, out, seen = d["rel"], [], set()
    for lt, t in fm_links(d["fm"]):
        dst = resolve_link(rel, t, known)
        if dst:
            kind = "doc"
        elif t.startswith(("http://", "https://")):
            dst, kind = t, "url"
        else:
            dst, kind = t, "code"
        if (dst, lt) not in seen:
            seen.add((dst, lt))
            out.append((dst, lt, kind))
    for href in LINK_RE.findall(strip_code(d["body"])):
        dst = resolve_link(rel, href.split()[0] if href.split() else href, known)
        if dst and dst != rel and not any(o[0] == dst for o in out):
            out.append((dst, "relates_to", "doc"))
    return out


# ─────────────────────────── index ───────────────────────────
def _has_trigram(con) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE _tri_probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE _tri_probe")
        return True
    except sqlite3.OperationalError:
        return False


def cmd_index(root: Path, force: bool = False) -> dict:
    """Индекс — кэш: всегда пересобирается целиком (быстро; схема меняется между версиями)."""
    db = root / DB_REL
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    tok = "tokenize='unicode61 remove_diacritics 2'"
    try:
        con.execute(f"CREATE VIRTUAL TABLE fts USING fts5(path UNINDEXED, lineno UNINDEXED, heading, body, {tok})")
        con.execute(f"CREATE VIRTUAL TABLE dfts USING fts5(path UNINDEXED, title, meta, {tok})")
    except sqlite3.OperationalError as e:
        print(f"ОШИБКА: SQLite без FTS5 ({e}). Нужен python с FTS5.", file=sys.stderr)
        sys.exit(2)
    has_tri = _has_trigram(con)
    if has_tri:
        con.execute("CREATE VIRTUAL TABLE tri USING fts5(path UNINDEXED, lineno UNINDEXED, body, tokenize='trigram')")
    con.execute("CREATE TABLE files(path TEXT PRIMARY KEY, title TEXT, area TEXT, size INT, chunks INT, "
                "node_type TEXT, status TEXT, service TEXT, description TEXT, updated TEXT, "
                "trust TEXT, stale INT, fm TEXT)")
    con.execute("CREATE TABLE links(src TEXT, dst TEXT, type TEXT, kind TEXT)")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")

    docs = load_docs(root)
    known = set(docs)
    nchunks = 0
    for rel, d in docs.items():
        m = d["meta"]
        chs = chunk_md(d["body"], d["offset"])
        for line, heading, body in chs:
            con.execute("INSERT INTO fts VALUES(?,?,?,?)", (rel, str(line), heading, body))
            if has_tri:
                con.execute("INSERT INTO tri VALUES(?,?,?)", (rel, str(line), heading + "\n" + body))
        nchunks += len(chs)
        path_words = " ".join(re.split(r"[/_.\-]+", rel.rsplit(".", 1)[0]))
        con.execute("INSERT INTO dfts VALUES(?,?,?)", (
            rel, m["title"],
            " ".join([m["description"], " ".join(m["tags"]), " ".join(m["aliases"]), path_words])))
        con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            rel, m["title"], area_of(rel), len(d["text"].encode("utf-8")), len(chs),
            m["node_type"], m["status"], ",".join(m["service"]), m["description"], m["updated"],
            m["trust"], int(m["stale"]), json.dumps(d["fm"], ensure_ascii=False, default=str)))
        for dst, lt, kind in doc_edges(d, known):
            con.execute("INSERT INTO links VALUES(?,?,?,?)", (rel, dst, lt, kind))
    con.execute("INSERT OR REPLACE INTO meta VALUES('trigram', ?)", ("1" if has_tri else "0",))
    con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (VERSION,))
    con.commit()
    n_links = con.execute("SELECT count(*) FROM links WHERE kind='doc'").fetchone()[0]
    con.close()
    return {"files": len(docs), "chunks": nchunks, "links": n_links,
            "trigram": has_tri, "db": str(db.relative_to(root))}


# ─────────────────────────── search ───────────────────────────
STOPWORDS = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее её мне
было вот от меня еще ещё нет о об из ему теперь когда ли если уже или ни быть был него до вас
опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем
была сам чтоб без чего раз тоже себе под будет ж тогда кто этот того потому этого какой какая
какие каким каких какую здесь этом один мой тем чтобы нее сейчас были куда зачем всех можно при
после над больше тот через эти нас про всего них много три эту эта это моя свою этой перед лучше
том нельзя такой им более всегда между почему нужно надо сколько чтобы ли как-то который которая
которые если именно сделать делать
a an the and or but if of to in on at by for with from as is are was were be been being do does
did how what where which who whom why when can could should would will shall may might must i we
you he she it they our your my me us this that these those there here into about than then so not
no have has had get got its it's any some all each just also
""".split())

_RU_END = sorted("""ями ами иями ией иях ях ах ов ев ей ий ый ой ая яя ое ее ые ие ых их ого его
ому ему ыми ими ом ем ам ям ую юю ть ться ется ются ет ют ит ят ил ила ило или ешь ишь ете ите ся
ость ости остью ения ение ений ением ениям ированный ировать ирование ующий ующая ующие а я о е ы
и у ю ь й""".split(), key=len, reverse=True)
_EN_END = sorted("ations ation ings ing ments ment ness ies ied ers er es ed ly s".split(),
                 key=len, reverse=True)
_CYR = re.compile(r"[а-яё]")


def stem(w: str) -> str:
    """Лёгкий стеммер ru/en: срезать одно окончание, оставив основу ≥4 символов.
    Запрос ищется префиксом основы — «деплоймента» найдёт «деплоймент», «деплойменты»."""
    if len(w) <= 4:
        return w
    for e in (_RU_END if _CYR.search(w) else _EN_END):
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[:-len(e)]
    return w


def query_terms(query: str) -> list:
    """Значимые термины запроса (lower, без стоп-слов; если всё стоп-слова — оставить всё)."""
    words = [w.lower().strip("-") for w in WORD_RE.findall(query)]
    words = [w for w in words if len(w) >= 2]
    sig = [w for w in words if w not in STOPWORDS]
    out, seen = [], set()
    for w in sig or words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _fts_match_query(query: str) -> str:
    return " OR ".join(f'"{stem(t)}"*' for t in query_terms(query))


def _fuzzy_phrases(s: str) -> list:
    """Фразы из пар соседних 3-грамм = 4-символьные подстроки запроса.

    Матч по 4-символьным окнам (а не одиночным 3-граммам) отсекает мусор: опечатка
    сохраняет длинные общие подстроки (`firecraker`↔`firecracker`: `fire`,`ecra`),
    а случайная строка делит с доками лишь разрозненные частые 3-граммы (`ent`,`non`).
    """
    wins, seen = [], set()
    for w in query_terms(s):
        if len(w) < 4:
            continue
        for i in range(len(w) - 3):
            win = w[i:i + 4]            # 4-символьное окно; FTS5-trigram сам разложит
            if '"' in win or win in seen:
                continue
            seen.add(win)
            wins.append(win)
    return wins


# Веса каналов в RRF и константа сглаживания. Подобраны на bench/ (см. bench/README.md).
CHANNEL_W = {"chunk": 1.0, "doc": 0.5, "phrase": 0.6, "fuzzy": 0.5}
RRF_K = 5
PRIOR = {"typed": 1.3, "index": 0.7, "retired": 0.6, "stale": 0.85}
HUB_NAMES = {"changelog.md", "log.md", "history.md"}
# Исторические типы — датированные снимки (замысел до реализации, разовый аудит), которые
# расходятся с фактом. `--scope live` их прячет, `--scope history` ищет только по ним.
HISTORICAL_TYPES = {"plan", "report"}     # хроники упоминают всё подряд
if os.environ.get("GITMARK_TUNE"):                        # для bench/: подбор весов без правки кода
    _t = json.loads(os.environ["GITMARK_TUNE"])
    CHANNEL_W.update(_t.get("CHANNEL_W", {}))
    PRIOR.update(_t.get("PRIOR", {}))
    RRF_K = _t.get("RRF_K", RRF_K)


def _allowed(con, types=None, statuses=None, services=None, scope="all") -> set | None:
    if not (types or statuses or services) and scope == "all":
        return None
    ok = set()
    for path, nt, st, svc in con.execute("SELECT path,node_type,status,service FROM files"):
        if types and nt not in types:
            continue
        if scope == "live" and nt in HISTORICAL_TYPES or scope == "history" and nt not in HISTORICAL_TYPES:
            continue
        if statuses and st not in statuses:
            continue
        if services and not set(svc.split(",")) & set(services):
            continue
        ok.add(path)
    return ok


def cmd_search(root: Path, query: str, k: int = 8, types=None, statuses=None,
               services=None, scope: str = "all") -> list:
    """Поиск: каналы → ранги файлов → RRF → приоры (типизирован / index / deprecated / stale).
    Один результат на файл — лучший чанк (path:line + сниппет)."""
    db = root / DB_REL
    if not db.exists():
        raise SystemExit("Индекс не найден — запусти `gitmark index`")
    con = sqlite3.connect(db)
    try:
        con.execute("SELECT trust FROM files LIMIT 1")
    except sqlite3.OperationalError:
        raise SystemExit("Индекс старой версии — запусти `gitmark index`")
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    allowed = _allowed(con, types, statuses, services, scope)
    ranks: dict = defaultdict(dict)        # channel → {path: rank}
    best: dict = {}                        # path → лучший чанк {line, heading, snippet}
    N = max(60, k * 8)

    def take(channel, rows, with_chunk=True):
        r = 0
        for row in rows:
            path = row[0]
            if allowed is not None and path not in allowed:
                continue
            if path in ranks[channel]:
                continue
            r += 1
            ranks[channel][path] = r
            if with_chunk and path not in best:
                best[path] = {"line": int(row[1]), "heading": row[2], "snippet": " ".join(row[3].split())}

    bm_q = _fts_match_query(query)
    if bm_q:
        try:   # (1) bm25 по чанкам; заголовок весит больше тела
            take("chunk", con.execute(
                "SELECT path,lineno,heading,snippet(fts,3,'»','«','…',14) FROM fts "
                "WHERE fts MATCH ? ORDER BY bm25(fts,0,0,2.0,1.0) LIMIT ?", (bm_q, N)))
        except sqlite3.OperationalError:
            pass
        try:   # (2) bm25 по документу: title, description/tags/aliases/путь
            take("doc", con.execute(
                "SELECT path FROM dfts WHERE dfts MATCH ? ORDER BY bm25(dfts,0,3.0,1.5) LIMIT ?",
                (bm_q, N)), with_chunk=False)
        except sqlite3.OperationalError:
            pass
    if has_tri and len(query.strip()) >= 3:
        try:   # (3) точная подстрока
            tq = '"' + query.replace('"', " ").strip() + '"'
            take("phrase", con.execute(
                "SELECT path,lineno,'',snippet(tri,2,'»','«','…',14) FROM tri "
                "WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?", (tq, N)))
        except sqlite3.OperationalError:
            pass
        # (4) fuzzy: OR по 4-символьным окнам → опечатки/морфология/кириллица.
        # Порог покрытия: чанк принимается, только если содержит ≥ ceil(20%) окон запроса.
        grams = _fuzzy_phrases(query)
        if grams:
            need = max(1, (len(grams) + 4) // 5)
            fq = " OR ".join(f'"{g}"' for g in grams)
            try:
                rows = []
                for path, lineno, snip, body in con.execute(
                        "SELECT path,lineno,snippet(tri,2,'»','«','…',14),body FROM tri "
                        "WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?", (fq, N * 2)):
                    bl = body.lower()
                    if sum(1 for g in grams if g in bl) >= need:
                        rows.append((path, lineno, "", snip))
                take("fuzzy", rows)
            except sqlite3.OperationalError:
                pass

    fused: dict = defaultdict(float)
    via: dict = defaultdict(list)
    for ch, rk in ranks.items():
        for path, r in rk.items():
            fused[path] += CHANNEL_W[ch] / (RRF_K + r)
            via[path].append(ch)
    if not fused:
        con.close()
        return []
    info = {r[0]: r[1:] for r in con.execute(
        "SELECT path,title,node_type,status,trust,stale FROM files WHERE path IN (%s)"
        % ",".join("?" * len(fused)), tuple(fused))}
    for path in fused:
        title, nt, st, trust, stale = info.get(path, ("", "", "", "", 0))
        f = PRIOR["typed"] if nt else 1.0
        if nt == "index" or Path(path).name.lower() in HUB_NAMES:
            f *= PRIOR["index"]
        if st in ("deprecated", "archived", "superseded"):
            f *= PRIOR["retired"]
        if stale:
            f *= PRIOR["stale"]
        fused[path] *= f
    top = sorted(fused, key=lambda p: -fused[p])[:k]
    out = []
    for path in top:
        title, nt, st, trust, stale = info.get(path, ("", "", "", "", 0))
        b = best.get(path)
        if b is None:                       # файл найден только по doc-каналу — берём первый чанк
            row = con.execute("SELECT lineno,heading,substr(body,1,240) FROM fts WHERE path=? "
                              "ORDER BY CAST(lineno AS INT) LIMIT 1", (path,)).fetchone() or (1, "", "")
            b = {"line": int(row[0]), "heading": row[1], "snippet": " ".join(row[2].split())}
        out.append({"path": path, "line": b["line"], "heading": b["heading"],
                    "snippet": b["snippet"], "score": round(fused[path], 5),
                    "via": "+".join(via[path]), "title": title, "node_type": nt,
                    "status": st, "trust": trust, "stale": bool(stale)})
    con.close()
    return out


# ─────────────────────────── query (структурные вопросы к frontmatter) ───────────────────────────
_COND_RE = re.compile(r"^(!)?([\w.\-]+)(?:(!=|>=|<=|=|~|>|<)(.*)|(\?))?$")


def _get_path(fm: dict, key: str) -> list:
    """`a.b` по frontmatter → плоский список значений (сквозь списки)."""
    vals = [fm]
    for part in key.split("."):
        nxt = []
        for v in vals:
            if isinstance(v, list):
                v_items = v
            else:
                v_items = [v]
            for it in v_items:
                if isinstance(it, dict) and part in it:
                    x = it[part]
                    nxt.extend(x if isinstance(x, list) else [x])
        vals = nxt
    return [v for v in vals if v is not None and v != ""]


def doc_record(d: dict) -> dict:
    """frontmatter + вычисляемые поля: path, area, type, trust, stale, links.<type>."""
    rec = dict(d["fm"] or {})
    m = d["meta"]
    rec.update({"path": d["rel"], "area": area_of(d["rel"]), "type": m["node_type"],
                "node_type": m["node_type"], "title": m["title"], "trust": m["trust"],
                "stale": m["stale"]})
    return rec


def match_cond(rec: dict, cond: str) -> bool:
    m = _COND_RE.match(cond.strip())
    if not m:
        raise SystemExit(f"не понял условие: {cond!r} (ожидаю key=val, key!=val, key~sub, "
                         f"key>val, key<val, key?, !key)")
    neg, key, op, val, exists = m.groups()
    vals = [str(v).lower() if not isinstance(v, bool) else str(v).lower()
            for v in _get_path(rec, key)]
    if neg and not op:
        return not vals
    if exists or not op:
        return bool(vals)
    val = (val or "").strip().strip("'\"").lower()
    alts = [x.strip() for x in val.split("|")]
    if op == "=":
        return any(v in alts for v in vals)
    if op == "!=":
        return not any(v in alts for v in vals)
    if op == "~":
        return any(a in v for v in vals for a in alts)
    cmp = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
           ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}[op]
    return any(cmp(v, val) for v in vals)


def cmd_query(root: Path, conds: list, count_by: str | None = None) -> dict:
    """Фильтр по frontmatter прямо из md (источник истины), без индекса."""
    rows = [doc_record(d) for d in load_docs(root).values() if d["fm"] is not None]
    hits = [r for r in rows if all(match_cond(r, c) for c in conds)]
    res = {"total": len(rows), "matched": len(hits), "rows": hits}
    if count_by:
        cnt = Counter()
        for r in hits:
            for v in _get_path(r, count_by) or ["(нет)"]:
                cnt[str(v)] += 1
        res["counts"] = cnt.most_common()
    return res


# ─────────────────────────── stat ───────────────────────────
def cmd_stat(root: Path) -> dict:
    db = root / DB_REL
    if not db.exists():
        return {"indexed": False}
    con = sqlite3.connect(db)
    try:
        f = con.execute("SELECT count(*), coalesce(sum(size),0), coalesce(sum(chunks),0) FROM files").fetchone()
        links = con.execute("SELECT count(*) FROM links WHERE kind='doc'").fetchone()[0]
        typed = con.execute("SELECT count(*) FROM links WHERE type!='relates_to'").fetchone()[0]
        areas = con.execute("SELECT count(DISTINCT area) FROM files").fetchone()[0]
        types = con.execute("SELECT node_type, count(*) FROM files WHERE node_type!='' "
                            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    except sqlite3.OperationalError:
        con.close()
        return {"indexed": False, "stale_schema": True}
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    con.close()
    return {"indexed": True, "files": f[0], "bytes": f[1], "chunks": f[2], "links": links,
            "typed_links": typed, "areas": areas, "trigram": has_tri, "types": types}


# ─────────────────────────── lint (онтология) ───────────────────────────
# Словари по умолчанию. Проект расширяет их в frontmatter своего ontology.md:
#   vocab: { node_type: [meeting], service: [api, billing], status: [done] }
NODE_TYPES = {"service", "reference", "runbook", "gotcha", "decision",
              "plan", "guide", "report", "index", "memory"}
# Реальный словарь сервисов выводится per-repo из имён папок docs/** + vocab.service.
SERVICES = {"_platform"}
STATUSES = {"active", "draft", "deprecated", "archived", "stable",   # stable — OKF
            "frozen"}                                               # снимок на дату: не правится, факт — в живом доке
LOAD_BEARING = {"service", "reference", "runbook", "plan", "decision"}
BEARING_DIRS = {"reference", "ops", "plans", "decisions", "services"}
CODE_DIRS = ("services", "apps", "packages", "libs", "modules")
LINK_KEYS = {"documents", "depends_on", "supersedes", "relates_to",
             "implemented_by", "part_of"}
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")

INVARIANTS = {
    "I1": "несущий документ без frontmatter с node_type/type",
    "I2": "значение вне словаря (node_type/service/status/тип связи)",
    "I3": "сирота — нет связей",
    "I4": "битая md-ссылка",
    "I5": "папка docs/ без README.md",
    "I6": "supersedes-цель не deprecated/archived",
    "I7": "links: documents/implemented_by → путь не существует",
    "I8": "просрочен stale_after",
    "I9": "точка входа: AGENTS.md с шапкой и ссылкой на docs/README.md, CLAUDE.md подключает его (@AGENTS.md)",
}


def strip_code(text: str) -> str:
    """Убрать fenced ``` и inline `code` — чтобы не ловить ссылки-примеры из кода."""
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


def in_kb(rel: str) -> bool:
    """Что линтуется: docs/** и md в корне (README/CLAUDE/AGENTS). Прочие md (код, фикстуры) —
    ищутся, но онтологией не считаются."""
    return rel.startswith("docs/") or "/" not in rel


def project_vocab(docs: dict) -> dict:
    """vocab: из frontmatter любого ontology.md."""
    voc = defaultdict(set)
    for rel, d in docs.items():
        if Path(rel).name != "ontology.md":
            continue
        v = (d["fm"] or {}).get("vocab")
        if isinstance(v, dict):
            for key, vals in v.items():
                voc[key] |= {str(x) for x in as_list(vals)}
    return voc


def _code_path_exists(root: Path, src_rel: str, target: str) -> bool:
    t = target.split("#")[0].strip()
    if not t or "://" in t:
        return True
    base = root if t.startswith("/") else (root / src_rel).parent
    return (base / t.lstrip("/")).exists() or (root / t.lstrip("/")).exists()


def cmd_lint(root: Path, paths: list | None = None) -> dict:
    """Проверка инвариантов онтологии I1–I9. Возвращает {issues, errors, warnings, checked}."""
    docs = load_docs(root)
    known = set(docs)
    voc = project_vocab(docs)
    node_types = NODE_TYPES | voc["node_type"]
    statuses = STATUSES | voc["status"]
    link_keys = LINK_KEYS | voc["link"]
    if voc["service"]:                       # словарь задан явно — он и есть закон
        services_vocab = set(SERVICES) | voc["service"]
    else:                                    # иначе — из папок docs/** и кода services|apps|packages/*
        services_vocab = set(SERVICES)
        docs_root = root / "docs"
        if docs_root.exists():
            services_vocab |= {d.name for d in docs_root.rglob("*") if d.is_dir()}
        for top in CODE_DIRS:
            if (root / top).is_dir():
                services_vocab |= {d.name for d in (root / top).iterdir() if d.is_dir()}
    out_links, in_links, issues = {}, defaultdict(set), []
    sel = set(paths) if paths else None
    edges = {}

    for rel, d in docs.items():
        edges[rel] = doc_edges(d, known)
        outs = set()
        for dst, lt, kind in edges[rel]:
            if kind == "doc":
                outs.add(dst)
                in_links[dst].add(rel)
        out_links[rel] = outs
        if not in_kb(rel):
            continue
        for href in LINK_RE.findall(strip_code(d["body"])):
            h = href.split()[0] if href.split() else href
            if (h.split("#")[0].endswith(".md") and not h.startswith(("http", "mailto:"))
                    and not resolve_link(rel, h, known) and not _code_path_exists(root, rel, h)):
                issues.append(("ERR", "I4", rel, f"битая ссылка → {h}"))

    # README на каждую docs/-папку (I5)
    docs_dirs = {(root / rel).parent for rel in docs if rel.startswith("docs/")}
    for dd in sorted(docs_dirs):
        if not (dd / "README.md").exists() and not (dd / "index.md").exists():
            issues.append(("WARN", "I5", dd.relative_to(root).as_posix() + "/", "нет README.md (индекс папки)"))

    for rel, d in docs.items():
        if sel and rel not in sel:
            continue
        if not rel.startswith("docs/"):
            continue
        fm, nt = d["fm"], d["meta"]["node_type"]
        # I1 — несущий документ без frontmatter/типа
        parts = set(rel.split("/")[1:-1])
        if not fm or not nt:
            if parts & BEARING_DIRS and Path(rel).name not in ("README.md", "index.md", "log.md"):
                issues.append(("ERR", "I1", rel, "нет frontmatter с node_type"))
            continue
        # I2 — значения в словарях
        if nt not in node_types:
            issues.append(("ERR", "I2", rel, f"node_type='{nt}' вне словаря"))
        for svc in d["meta"]["service"]:
            if svc not in services_vocab and not svc.startswith("_"):
                issues.append(("WARN", "I2", rel, f"service='{svc}' вне словаря"))
        st = d["meta"]["status"]
        if st and st not in statuses:
            issues.append(("WARN", "I2", rel, f"status='{st}' вне словаря"))
        for lt, t in fm_links(fm):
            if lt not in link_keys:
                issues.append(("WARN", "I2", rel, f"тип связи '{lt}' вне словаря"))
        # I3 — сироты (несущий тип без входящих/исходящих связей, в т.ч. на код)
        if nt in LOAD_BEARING and not out_links.get(rel) and not in_links.get(rel) and not fm_links(fm):
            issues.append(("WARN", "I3", rel, "сирота — нет связей (ни in, ни out)"))
        for dst, lt, kind in edges[rel]:
            # I6 — supersedes-цель должна быть deprecated/archived
            if lt == "supersedes" and kind == "doc":
                if docs[dst]["meta"]["status"] not in ("deprecated", "archived"):
                    issues.append(("WARN", "I6", rel, f"supersedes {dst}, но он не deprecated/archived"))
            # I4/I7 — цели типизированных связей
            if kind == "code" and dst.endswith(".md") and not _code_path_exists(root, rel, dst):
                issues.append(("ERR", "I4", rel, f"links.{lt} → {dst} — нет такого дока"))
            elif kind == "code" and not _code_path_exists(root, rel, dst):
                issues.append(("WARN", "I7", rel, f"links.{lt} → {dst} — путь не существует"))
        # I8 — просрочен
        if d["meta"]["stale"]:
            issues.append(("WARN", "I8", rel, f"stale_after={fm.get('stale_after')} прошёл — перепроверь"))

    if not sel:
        issues += lint_entry(root)

    errs = [i for i in issues if i[0] == "ERR"]
    warns = [i for i in issues if i[0] == "WARN"]
    return {"issues": issues, "errors": errs, "warnings": warns, "checked": len(docs)}


AGENTS_IMPORT_RE = re.compile(r"^@\.?/?AGENTS\.md\s*$", re.MULTILINE)


def lint_entry(root: Path) -> list:
    """I9 — точка входа. AGENTS.md читают Codex, Qwen Code, Cursor и др., CLAUDE.md — только Claude Code:
    правила живут в AGENTS.md (шапка + короткое дерево docs/), CLAUDE.md подключает его строкой @AGENTS.md,
    без копии — иначе два файла разъедутся."""
    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    has_docs = (root / "docs").is_dir()
    out = []
    if not agents.exists():
        if claude.exists():
            out.append(("WARN", "I9", "CLAUDE.md",
                        "вход только в CLAUDE.md — перенеси правила в AGENTS.md, в CLAUDE.md оставь @AGENTS.md"))
        elif has_docs:
            out.append(("WARN", "I9", "AGENTS.md", "нет точки входа AGENTS.md"))
        return out
    text = agents.read_text(encoding="utf-8", errors="replace")
    # индекс базы знаний: обычно docs/README.md, но БЗ может жить и в другой папке (b24/, kb/)
    if has_docs and not re.search(r"\]\(\.?/?[\w.-]+(?:/[\w.-]+)*/(?:README|index)\.md\)", text):
        out.append(("WARN", "I9", "AGENTS.md", "нет ссылки на индекс базы знаний (docs/README.md или <папка>/README.md)"))
    if claude.exists() and not AGENTS_IMPORT_RE.search(claude.read_text(encoding="utf-8", errors="replace")):
        out.append(("WARN", "I9", "CLAUDE.md", "не подключает AGENTS.md строкой @AGENTS.md"))
    return out


# ─────────────────────────── храповик lint ───────────────────────────
def _issue_key(i) -> tuple:
    return (i[1], i[2], i[3])


def cmd_lint_baseline(root: Path, ref: str) -> dict:
    """Сравнить lint с состоянием REF (ветка/коммит): то же дерево из `git archive` во временной папке.
    Отвечает на один вопрос — стало ли замечаний больше. Старый долг ветку не валит, новый — валит.
    Не смогли сравнить → error (exit 2): «не посмотрел» нельзя выдавать за «не выросло»."""
    import tarfile, tempfile, io
    head = cmd_lint(root)
    try:
        r = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", ref],
                           capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"git archive {ref}: {e}"}
    if r.returncode != 0:
        return {"error": f"git archive {ref}: {r.stderr.decode('utf-8', 'replace').strip()}"}
    with tempfile.TemporaryDirectory(prefix="gitmark-base-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
            tar.extractall(tmp, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(tmp)
        prev = os.environ.get("GITMARK_NO_GIT")
        os.environ["GITMARK_NO_GIT"] = "1"   # во временной папке нет .git — обход ФС
        try:
            base = cmd_lint(Path(tmp))
        finally:
            if prev is None:
                os.environ.pop("GITMARK_NO_GIT", None)
            else:
                os.environ["GITMARK_NO_GIT"] = prev
    from collections import Counter
    new = Counter(map(_issue_key, head["issues"])) - Counter(map(_issue_key, base["issues"]))
    fresh = []
    for i in head["issues"]:   # по одному на каждое превышение счётчика: дубли считаются
        if new[_issue_key(i)] > 0:
            new[_issue_key(i)] -= 1
            fresh.append(i)
    return {"head": len(head["issues"]), "base": len(base["issues"]), "new": fresh,
            "grew": len(head["issues"]) > len(base["issues"]), "head_issues": head["issues"]}


# ─────────────────────────── toc: собираемые индексы ───────────────────────────
TOC_OPEN, TOC_CLOSE = "<!-- gitmark:toc -->", "<!-- /gitmark:toc -->"
TOC_RE = re.compile(re.escape(TOC_OPEN) + r".*?" + re.escape(TOC_CLOSE), re.DOTALL)
INDEX_NAMES = ("README.md", "index.md")


def _toc_lines(root: Path, folder: Path, docs: dict) -> list:
    """Строка на документ папки и на подпапку с индексом: `- [title](file) — description`."""
    rows = []
    for rel, d in sorted(docs.items()):
        p = root / rel
        if p.parent == folder and p.name not in INDEX_NAMES:
            rows.append((p.name, d))
        elif p.parent.parent == folder and p.name in INDEX_NAMES:
            rows.append((f"{p.parent.name}/{p.name}", d))
    out = []
    for href, d in rows:
        desc = d["meta"]["description"].strip().replace("\n", " ")
        out.append(f"- [{d['meta']['title']}]({href})" + (f" — {desc}" if desc else ""))
    return out


def cmd_toc(root: Path, write: bool = False) -> dict:
    """Индексы, которые СОБИРАЮТСЯ, а не пишутся: в README/index.md папки блок между
    <!-- gitmark:toc --> и <!-- /gitmark:toc --> пересобирается из title/description документов.
    Папка без маркеров не трогается. Без --write — проверка: stale = индексы, разошедшиеся с папкой."""
    docs = load_docs(root)
    stale, checked = [], 0
    for rel in sorted(docs):
        p = root / rel
        if p.name not in INDEX_NAMES:
            continue
        text = p.read_text(encoding="utf-8")
        if TOC_OPEN not in text:
            continue
        checked += 1
        block = "\n".join([TOC_OPEN, *_toc_lines(root, p.parent, docs), TOC_CLOSE])
        new = TOC_RE.sub(lambda _m: block, text, count=1)
        if new != text:
            stale.append(rel)
            if write:
                p.write_text(new, encoding="utf-8")
    return {"checked": checked, "stale": stale, "written": write}


# ─────────────────────────── secrets ───────────────────────────
SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("assigned-secret", re.compile(
        r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?([^\s'\"<>`]{8,})")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{4,}@")),
]
# заглушки в примерах — не секрет
PLACEHOLDER_RE = re.compile(r"(?i)^(?:\$\{?[A-Z_]+\}?|<[^>]*>|x{4,}|\*{4,}|\.{3,}|your[_-].*|changeme|example.*|placeholder.*)$")


def _mask(s: str) -> str:
    return s[:4] + "…" + str(len(s)) if len(s) > 8 else "…"


def cmd_secrets(root: Path, files: list | None = None) -> dict:
    """Токены, ключи и пароли в markdown БЗ (docs/** и md в корне) или в заданных файлах.
    Значение маскируется: в выводе только вид и первые символы."""
    if files:
        targets = [(f, (root / f) if not Path(f).is_absolute() else Path(f)) for f in files]
    else:
        targets = [(p.relative_to(root).as_posix(), p) for p in iter_md(root)
                   if in_kb(p.relative_to(root).as_posix())]
    found = []
    for rel, p in targets:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for name, rx in SECRET_PATTERNS:
                for m in rx.finditer(line):
                    val = m.group(m.lastindex or 0)
                    if PLACEHOLDER_RE.match(val.strip()):
                        continue
                    found.append((rel, n, name, _mask(val)))
    return {"checked": len(targets), "found": found}


# ─────────────────────────── .gitattributes ───────────────────────────
UNION_LINES = ("docs/**/README.md merge=union", "docs/**/index.md merge=union",
               "docs/README.md merge=union")


def cmd_attrs(root: Path, write: bool = False) -> dict:
    """README-индексы — append-списки: параллельные ветки дописывают строки, `merge=union` склеивает их
    без конфликта. Дубли и порядок потом выравнивает `gitmark toc --write`."""
    f = root / ".gitattributes"
    have = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    missing = [l for l in UNION_LINES if l not in have]
    if write and missing:
        tail = "" if not have or have[-1] == "" else "\n"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(tail + "# gitmark: индексы базы знаний склеиваются при слиянии\n" + "\n".join(missing) + "\n")
    return {"missing": missing, "written": write and bool(missing)}


# ─────────────────────────── health (скоркард) ───────────────────────────
def cmd_health(root: Path) -> dict:
    """Насколько БЗ — онтология, а не куча файлов. Считается по docs/** (или по всем,
    если docs/ нет): доля с типом/title/description/updated, типизированные связи,
    сироты, битые ссылки, trust-tier, stale, дубли по содержимому."""
    docs = load_docs(root)
    known = set(docs)
    scope = {r: d for r, d in docs.items() if r.startswith("docs/")} or docs
    content = {r: d for r, d in scope.items()
               if Path(r).name not in ("README.md", "index.md", "log.md")}
    n = len(content) or 1
    typed = [d for d in content.values() if d["meta"]["node_type"]]
    fm_of = lambda d: d["fm"] or {}
    linked_in = defaultdict(int)
    edges = {}
    for r, d in docs.items():
        edges[r] = doc_edges(d, known)
        for dst, _, kind in edges[r]:
            if kind == "doc":
                linked_in[dst] += 1
    orphans = [r for r in content if not edges[r] and not linked_in[r]]
    broken = sum(1 for r, d in scope.items() for h in LINK_RE.findall(strip_code(d["body"]))
                 if h.split("#")[0].endswith(".md") and not h.startswith("http")
                 and not resolve_link(r, h.split()[0], known)
                 and not _code_path_exists(root, r, h.split()[0]))
    hashes = defaultdict(list)
    for r, d in docs.items():
        body = re.sub(r"\s+", " ", d["body"]).strip()
        if len(body) > 200:
            hashes[hashlib.sha1(body.encode()).hexdigest()].append(r)
    dups = [v for v in hashes.values() if len(v) > 1]
    trust = Counter(d["meta"]["trust"] for d in content.values())
    pct = lambda c: round(100 * c / n)
    res = {
        "docs": len(content), "indexes": len(scope) - len(content), "all_md": len(docs),
        "typed_pct": pct(len(typed)),
        "title_pct": pct(sum(1 for d in content.values() if fm_of(d).get("title"))),
        "description_pct": pct(sum(1 for d in content.values() if fm_of(d).get("description") or fm_of(d).get("summary"))),
        "updated_pct": pct(sum(1 for d in content.values()
                               if fm_of(d).get("updated") or isinstance(fm_of(d).get("generated"), dict))),
        "typed_links_pct": pct(sum(1 for d in content.values() if fm_links(d["fm"]))),
        "code_links_pct": pct(sum(1 for r in content
                                  if any(k == "code" for _, _, k in edges[r]))),
        "orphans": len(orphans), "broken_links": broken,
        "stale": sum(1 for d in content.values() if d["meta"]["stale"]),
        "trust": dict(trust), "duplicate_groups": len(dups),
        "duplicates": dups[:10],
        "types": Counter(d["meta"]["node_type"] for d in typed).most_common(),
    }
    # интегральный балл 0–100: структура (тип/title/updated), связность, гигиена
    score = (0.30 * res["typed_pct"] + 0.10 * res["title_pct"] + 0.10 * res["description_pct"]
             + 0.10 * res["updated_pct"] + 0.15 * res["typed_links_pct"]
             + 0.15 * (100 - pct(len(orphans))) + 0.10 * (100 if broken == 0 else max(0, 100 - 5 * broken)))
    res["score"] = round(score)
    return res


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

    docs = load_docs(root)
    known = set(docs)
    files, edges = {}, []
    for rel, d in docs.items():
        files[rel] = {"rel": rel, "area": area_of(rel), "title": d["meta"]["title"],
                      "size": len(d["text"].encode("utf-8")), "html": render(d["body"]),
                      "type": d["meta"]["node_type"], "status": d["meta"]["status"]}
    for rel, d in docs.items():
        for dst, lt, kind in doc_edges(d, known):
            if kind == "doc" and dst != rel:
                edges.append({"s": rel, "t": dst, "kind": "ref", "lt": lt})

    areas = sorted({f["area"] for f in files.values()})
    nodes = [{"id": "area::" + a, "label": a, "kind": "area", "area": a, "size": 0} for a in areas]
    nodes += [{"id": r, "label": f["title"], "kind": "doc", "area": f["area"],
               "size": f["size"], "rel": r} for r, f in files.items()]
    edges += [{"s": "area::" + f["area"], "t": r, "kind": "own"} for r, f in files.items()]

    # BFS-уровни/углы от точки входа (AGENTS.md|CLAUDE.md|README.md|первый)
    import math
    from collections import defaultdict, deque
    nid = {n["id"]: n for n in nodes}
    root_id = next((c for c in ("AGENTS.md", "CLAUDE.md", "README.md") if c in nid), nodes[0]["id"])
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
def _csv(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitmark", description="GitMark Memory Bank — md+git knowledge base CLI")
    ap.add_argument("--root", default=None, help="корень репо (по умолчанию — авто по .git)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="построить индекс").add_argument("--force", action="store_true",
                                                                  help="(совместимость; индекс всегда пересобирается)")
    sp = sub.add_parser("search", help="искать")
    sp.add_argument("query"); sp.add_argument("-k", type=int, default=8); sp.add_argument("--json", action="store_true")
    sp.add_argument("--type", help="только эти node_type/type, через запятую")
    sp.add_argument("--status", help="только эти status, через запятую")
    sp.add_argument("--service", help="только эти service, через запятую")
    sp.add_argument("--scope", choices=["all", "live", "history"], default="all",
                    help="live — без исторических plan/report; history — только они")
    qp = sub.add_parser("query", help="структурный запрос по frontmatter",
                        description="Условия (AND): key=val  key!=val  key~sub  key>val  key<val  key?  !key. "
                                    "Альтернативы через |: status=draft|active. Вложенные ключи: verified.by, "
                                    "links.depends_on. Вычисляемые: path, area, type, trust, stale.")
    qp.add_argument("cond", nargs="*")
    qp.add_argument("--fields", default="path,type,title,status", help="колонки вывода")
    qp.add_argument("--count-by", help="посчитать по значениям ключа (списки разворачиваются)")
    qp.add_argument("--json", action="store_true")
    mp = sub.add_parser("map", help="HTML обзор+граф"); mp.add_argument("-o", "--out", default=None)
    sv = sub.add_parser("serve", help="локальный http"); sv.add_argument("-p", "--port", type=int, default=8799)
    sub.add_parser("stat", help="статистика")
    lp = sub.add_parser("lint", help="проверить онтологию (I1–I9)")
    lp.add_argument("paths", nargs="*", help="ограничить файлами (по умолчанию — все docs/)")
    lp.add_argument("--strict", action="store_true", help="exit 1 при любых ERR")
    lp.add_argument("--json", action="store_true")
    lp.add_argument("--baseline", metavar="REF",
                    help="храповик: exit 1, только если замечаний больше, чем в REF (exit 2 — не смог сравнить)")
    hp = sub.add_parser("health", help="скоркард БЗ"); hp.add_argument("--json", action="store_true")
    tp = sub.add_parser("toc", help="собрать блоки gitmark:toc в индексах")
    tp.add_argument("--write", action="store_true", help="записать; без флага — проверка (exit 1, если разошлись)")
    scp = sub.add_parser("secrets", help="токены/ключи/пароли в БЗ")
    scp.add_argument("files", nargs="*", help="проверить эти файлы (по умолчанию — md базы знаний)")
    atp = sub.add_parser("attrs", help=".gitattributes: merge=union для README-индексов")
    atp.add_argument("--write", action="store_true")
    sub.add_parser("version", help="версия")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else repo_root(Path.cwd())

    if a.cmd == "index":
        r = cmd_index(root, a.force)
        print(f"✓ index: {r['files']} файлов · {r['chunks']} чанков · {r['links']} ссылок · "
              f"trigram={'on' if r['trigram'] else 'OFF'} → {r['db']}")
    elif a.cmd == "search":
        res = cmd_search(root, a.query, a.k, _csv(a.type), _csv(a.status), _csv(a.service), a.scope)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2)); return
        if not res:
            print("ничего не найдено"); return
        for r in res:
            head = f" › {r['heading']}" if r["heading"] else ""
            tags = " ".join(x for x in (r["node_type"], r["status"],
                                        "stale" if r["stale"] else "",
                                        "✓human" if r["trust"] == "human" else "") if x)
            tags = f" \033[36m{tags}\033[0m" if tags else ""
            print(f"\033[32m{r['path']}:{r['line']}\033[0m{head}{tags}  \033[90m[{r['via']}]\033[0m")
            print(f"   {r['snippet'][:200]}")
    elif a.cmd == "query":
        r = cmd_query(root, a.cond, a.count_by)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1, default=str)); return
        if a.count_by:
            for v, c in r["counts"]:
                print(f"{c:>5}  {v}")
        else:
            fields = _csv(a.fields)
            for row in r["rows"]:
                print("  ".join(", ".join(str(x) for x in _get_path(row, f)) or "—" for f in fields))
        print(f"\n{r['matched']} из {r['total']} документов с frontmatter")
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
        types = " ".join(f"{t}:{n}" for t, n in s["types"])
        print(f"GitMark · {s['files']} файлов · {s['areas']} папок · {s['chunks']} чанков · "
              f"{s['links']} ссылок ({s['typed_links']} типизир.) · {s['bytes']//1024} KB · "
              f"trigram={'on' if s['trigram'] else 'off'}\n  типы: {types or '—'}")
    elif a.cmd == "lint" and a.baseline:
        r = cmd_lint_baseline(root, a.baseline)
        if "error" in r:
            print(f"\033[31mне смог сравнить\033[0m — {r['error']}", file=sys.stderr); sys.exit(2)
        for lvl, code, path, msg in r["new"]:
            col = "\033[31m" if lvl == "ERR" else "\033[33m"
            print(f"{col}{lvl}\033[0m \033[90m{code}\033[0m {path} — {msg}")
        verdict = "\033[31mвыросло\033[0m" if r["grew"] else "\033[32mне выросло\033[0m"
        print(f"\n{verdict}: {a.baseline} {r['base']} → сейчас {r['head']} · новых {len(r['new'])}")
        if r["grew"]:
            sys.exit(1)
    elif a.cmd == "toc":
        r = cmd_toc(root, a.write)
        for rel in r["stale"]:
            print(f"{'✓ собран' if a.write else 'разошёлся'}: {rel}")
        print(f"индексов с блоком gitmark:toc: {r['checked']} · {'обновлено' if a.write else 'разошлось'}: {len(r['stale'])}")
        if r["stale"] and not a.write:
            sys.exit(1)
    elif a.cmd == "secrets":
        r = cmd_secrets(root, a.files or None)
        for rel, n, name, masked in r["found"]:
            print(f"\033[31m{rel}:{n}\033[0m {name} {masked}")
        print(f"{'✓ чисто' if not r['found'] else f'найдено: {len(r[chr(102)+chr(111)+chr(117)+chr(110)+chr(100)])}'} ({r['checked']} файлов)")
        if r["found"]:
            sys.exit(1)
    elif a.cmd == "attrs":
        r = cmd_attrs(root, a.write)
        if not r["missing"]:
            print("✓ .gitattributes: индексы склеиваются (merge=union)")
        else:
            print(("✓ дописано в .gitattributes:\n  " if a.write else "нет в .gitattributes (gitmark attrs --write):\n  ")
                  + "\n  ".join(r["missing"]))
            if not a.write:
                sys.exit(1)
    elif a.cmd == "lint":
        r = cmd_lint(root, a.paths or None)
        if a.json:
            print(json.dumps({"errors": len(r["errors"]), "warnings": len(r["warnings"]),
                              "checked": r["checked"],
                              "issues": [dict(zip(("level", "code", "path", "msg"), i)) for i in r["issues"]]},
                             ensure_ascii=False, indent=1))
        else:
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
        if a.strict and r["errors"]:
            sys.exit(1)
    elif a.cmd == "health":
        h = cmd_health(root)
        if a.json:
            print(json.dumps(h, ensure_ascii=False, indent=1)); return
        print(f"GitMark health · {h['score']}/100 · {h['docs']} доков (+{h['indexes']} индексов)")
        print(f"  типизировано {h['typed_pct']}% · title {h['title_pct']}% · description {h['description_pct']}%"
              f" · updated {h['updated_pct']}%")
        print(f"  типизир. связи {h['typed_links_pct']}% · связь с кодом {h['code_links_pct']}%"
              f" · сирот {h['orphans']} · битых ссылок {h['broken_links']}")
        print(f"  trust {h['trust']} · stale {h['stale']} · дублей {h['duplicate_groups']}")
        for g in h["duplicates"][:5]:
            print(f"    дубль: {' = '.join(g)}")
        print(f"  типы: {' '.join(f'{t}:{n}' for t, n in h['types']) or '—'}")
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
