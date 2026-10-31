---
node_type: service
title: gitmark CLI — KB search/index engine
service: gitmark-cli
status: active
updated: 2026-06-16
tags: [fts5, search, sqlite, ontology, cli]
links:
  documents: [../../../skills/kb-search/gitmark.py, ../../../skills/kb-search/SKILL.md]
  relates_to: [../../ontology.md]
---

# gitmark CLI — KB search/index engine

`gitmark.py` is the search/index engine behind OntoShip. It treats the repo's markdown
(`docs/`, README files, any `*.md`) as a **md + git** knowledge base: markdown is the
source of truth, and everything derived — the search index, HTML map, graph — is
regenerated from it. The whole thing is pure Python stdlib (no third-party deps required;
`markdown` is an optional enhancement for the `map` renderer) and runs fully offline.

It enforces and operates over the OntoShip ontology (object types, properties, typed
links). For the model itself — `node_type`, frontmatter, link types, invariants I1–I6 —
see [ontology.md](../../ontology.md); this doc describes the engine, not the model.

Script: `skills/kb-search/gitmark.py`. The usage skill is `skills/kb-search/SKILL.md`.

## Subcommands

Invoked as `python3 gitmark.py <cmd>`. A global `--root <path>` overrides repo
auto-detection (see below); it precedes the subcommand. Defined in `main()`
(gitmark.py:556).

| cmd | what it does |
|---|---|
| `index [--force]` | (Re)build `.gitmark/index.db` — scans all `*.md`, chunks them, fills FTS tables + the `files`/`links`/`meta` tables. |
| `search "<q>" [-k N] [--json]` | Search the index: bm25 ∪ trigram ∪ fuzzy. Prints `path:line › heading [via]` + snippet; `-k` caps results (default 8); `--json` for machine output. |
| `map [-o OUT]` | Emit a single self-contained HTML file: collapsible tree + rendered markdown + radial link graph. Default out is `docs/docs-map.html`. |
| `serve [-p PORT]` | Local `http.server` over `docs/` (falls back to repo root) to view the map. Default port 8799. |
| `stat` | Index statistics: files, folders/areas, chunks, links, bytes, trigram on/off. |
| `lint [paths…] [--strict]` | Check ontology invariants I1–I6 (frontmatter/types/vocab/links/README/orphans). `--strict` exits 1 on any ERR. |
| `version` | Print `gitmark <VERSION>` (currently `0.1.0`, gitmark.py:33). |

Note: `index` accepts `--force` but the flag is currently inert — each `index` run already
deletes and rebuilds the tables unconditionally (gitmark.py:158-161).

## Root auto-detection

When `--root` is not given, the CLI walks up from the current working directory looking for
a `.git` directory and uses the first ancestor that has one; if none is found it falls back
to cwd. See `repo_root()` (gitmark.py:48-53). So `gitmark` can be run from anywhere inside
the repo and will resolve to the repo root.

A fixed set of directories is excluded from scanning regardless of root: `.git`,
`node_modules`, `.next`, `dist`, `build`, `__pycache__`, `.pytest_cache`, `_vendor`,
`.venv`, `venv`, `vendor`, and `.gitmark` itself (`EXCLUDE_DIRS`, gitmark.py:35-39). File
discovery is `iter_md()` (gitmark.py:56-60).

## Where the index lives

`.gitmark/index.db` under the repo root (`DB_REL`, gitmark.py:40). It is a derived cache and
should be gitignored — never edit it directly; edit the markdown and reindex. `search`/`stat`
require it to exist and tell you to run `gitmark index` if it's missing.

## How indexing works

`cmd_index()` (gitmark.py:139-198):

1. Read every non-excluded `*.md` under root into memory (used both for chunking and for
   link resolution against the set of known files).
2. For each file: derive a `title` (first `# H1`, else filename — `title_of`,
   gitmark.py:77-81) and an `area` (`area_of`, gitmark.py:63-74 — `docs/services/<svc>`
   gets its own group, otherwise `docs/<sub>`, `services/<svc>`, etc.).
3. **Chunking** — `chunk_md()` (gitmark.py:84-98) splits each file at markdown headings
   (`^#{1,6} …`). Each chunk is `(line_start, heading, body)`; this is the unit that gets
   indexed and the granularity at which search results point (`path:line`).
4. Each chunk is inserted into the `fts` table and (if available) the `tri` table.
5. **Links** — `LINK_RE` finds markdown links `[text](href)` (ignoring image links), and
   `resolve_link()` (gitmark.py:105-126) resolves each `href` to a known `.md` file. It
   strips `#anchors`, skips `http`/`mailto`, NFC-normalizes (so non-ASCII paths match),
   resolves relative/`../` paths via `posixpath.normpath`, and falls back to a unique
   basename match. Resolved `(src, dst)` pairs (deduped, excluding self-links) go into the
   `links` table. This populates the doc→doc edges the graph and the linter's orphan check
   use.
6. `meta` records whether trigram is available and the engine version.

**Trigram availability** is probed at runtime by trying to create an fts5 table with
`tokenize='trigram'` (`_has_trigram`, gitmark.py:130-136). If the SQLite build lacks the
trigram tokenizer, the `tri` table is skipped and search degrades to bm25-only. FTS5 itself
is mandatory — if SQLite has no FTS5 at all, `index` errors out with exit code 2
(gitmark.py:147-149).

## SQLite schema

Created in `cmd_index()`:

- `fts` — FTS5 virtual table, `tokenize='unicode61 remove_diacritics 2'`. Columns:
  `path UNINDEXED, heading, lineno UNINDEXED, body`. One row per chunk; the bm25 ranking
  table.
- `tri` — FTS5 virtual table, `tokenize='trigram'` (only if the trigram tokenizer exists).
  Same columns. Powers substring/fuzzy matching.
- `files(path TEXT PRIMARY KEY, title TEXT, area TEXT, size INT, chunks INT)` — one row per
  file (size is UTF-8 byte length).
- `links(src TEXT, dst TEXT)` — resolved doc→doc edges.
- `meta(k TEXT PRIMARY KEY, v TEXT)` — key/value: `trigram` (`"1"`/`"0"`) and `version`.

## How search ranks

`cmd_search()` (gitmark.py:227-292) merges up to three passes into one result set keyed by
`(path, lineno)`; earlier (stronger) passes win on collision, and results are sorted by the
combined score descending, then truncated to `k`.

1. **bm25 (exact terms)** — query words ≥2 chars become a prefix `OR` match
   (`"term"* OR …`, `_fts_match_query`, gitmark.py:202-204) against `fts`. Score is
   `-bm25(fts)` (full weight). `via: "bm25"`.
2. **trigram phrase (substring)** — the raw query as one quoted phrase against `tri`;
   matches exact substrings. Score weighted ×0.6. `via: "trigram"`. Only runs when the query
   is ≥3 chars and trigram is available.
3. **fuzzy (n-gram)** — handles typos, morphology, and Cyrillic. `_fuzzy_phrases()`
   (gitmark.py:207-224) breaks query words (≥4 chars) into overlapping 4-char windows;
   these are OR-matched against `tri`. A chunk is accepted only if its body contains at
   least `need` distinct windows (≈ceil(20% of windows), min 1 — gitmark.py:271), which
   filters chunks that merely share one common gram. Score weighted ×0.3. `via: "fuzzy"`.

Each pass returns a snippet via FTS5 `snippet(...,'»','«','…',14)` so the matched terms are
delimited in the output.

## Linting (ontology invariants)

`cmd_lint()` (gitmark.py:360-437) checks I1–I6 over `docs/**` using a stdlib mini
frontmatter parser (`parse_frontmatter`, gitmark.py:330-357 — no PyYAML). The controlled
vocabularies (`NODE_TYPES`, `SERVICES`, `STATUSES`, `LOAD_BEARING`, `LINK_KEYS`,
gitmark.py:312-319) are the lint source of truth and must stay in sync with
[ontology.md](../../ontology.md). Broken-link detection (I4) strips fenced/inline code first
(`strip_code`, gitmark.py:325-327) so example links in code blocks aren't flagged. ERR-level
issues: I1 (missing frontmatter/`node_type` on a load-bearing path), I2 (`node_type` out of
vocab), I4 (broken link). WARN-level: I2 service/status out of vocab, I3 (orphan — a
load-bearing doc with no in/out links and no `links:` block), I5 (a `docs/` folder with no
`README.md`), I6 (a `supersedes` target that isn't `deprecated`/`archived`).

## The map (HTML overview + graph)

`cmd_map()` (gitmark.py:441-537) produces one standalone HTML file (`_MAP_HTML` template,
gitmark.py:619+) with the data inlined as JSON: a collapsible per-area file tree, the
rendered markdown of each doc, and a radial link graph. The graph is laid out by BFS from an
entry node (`AGENTS.md` → `CLAUDE.md` → `README.md` → first file), with ring = distance-from-entry; it
supports drag, wheel-zoom, and clicking a node to open the doc. If the optional `markdown`
package is installed, docs render to HTML; otherwise they show as raw `<pre>` text (the CLI
prints a hint to `pip install markdown`).

## Usage examples

```bash
G="python3 skills/kb-search/gitmark.py"

$G index                       # build .gitmark/index.db
$G search "trigram ranking"    # bm25 + trigram + fuzzy; prints path:line + snippet
$G search "frontmater" -k 5    # typo-tolerant via fuzzy n-grams
$G search "ontology" --json    # machine-readable results
$G stat                        # files/folders/chunks/links/bytes · trigram on|off
$G lint                        # check ontology invariants over docs/
$G lint --strict               # exit 1 if any ERR
$G map -o out.html             # self-contained HTML overview + graph
$G serve -p 8799               # serve the map at http://127.0.0.1:8799/docs-map.html
```
