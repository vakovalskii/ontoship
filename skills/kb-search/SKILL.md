---
name: kb-search
description: Search a project's markdown knowledge base (docs/, README files, *.md) via the GitMark CLI — ranked file-level results (FTS5 bm25 + title/description/tags + trigram/fuzzy, fused with RRF) instead of grepping across files — and answer structured questions over YAML frontmatter ("which decisions are active", "what did the agent write that no human verified", "how many plans per service"). Use when you need to find where something is documented, before reading files at random, or to generate an HTML overview/graph of the knowledge base. Handles substrings, typos, Russian morphology.
---

# GitMark — knowledge-base search

This plugin treats the repo's markdown as a **md + README(index) + git** knowledge base.
Markdown is the source of truth; the search index and HTML map are **derived** and
regenerated from md (`.gitmark/` is gitignored). The CLI is pure Python stdlib (PyYAML is
used if installed, otherwise a built-in YAML subset parser).

Script: `${CLAUDE_PLUGIN_ROOT}/skills/kb-search/gitmark.py` (when run as a project-local
copy, it's `.claude/skills/kb-search/gitmark.py`).

## Two kinds of questions → two commands

| Question | Command |
|---|---|
| **Where is X described / how does X work?** (text) | `search "<query>"` |
| **Which / how many docs have property P?** (structure) | `query key=val …` |

`search` is full-text; `query` reads frontmatter straight from the md files (no index).
Don't grep frontmatter by hand — `query` understands lists, nested keys and OKF trust fields.

## Commands

```bash
G="python3 ${CLAUDE_PLUGIN_ROOT:-.claude/plugins/gitmark}/skills/kb-search/gitmark.py"

$G index                          # (re)build .gitmark/index.db (fast, always full)
$G search "<query>" [-k 8]        # one result per file: path:line › heading [type status]
$G search "<q>" --type runbook,gotcha --status active    # filter by frontmatter
$G search "<q>" --scope live      # skip historical plan/report snapshots (or: history)
$G query type=decision status!=deprecated --fields path,title,updated
$G query type=plan --count-by service                     # group & count (lists unrolled)
$G query 'generated.by~claude' trust=unverified           # agent-written, nobody verified
$G query 'updated<2026-01-01' type=runbook                # possibly outdated runbooks
$G health                         # KB scorecard: typed %, description %, links, trust, stale
$G lint [paths…] [--json]         # ontology check (I1–I9)
$G lint --baseline origin/main    # ratchet: exit 1 only if findings grew vs that ref
$G toc [--write]                  # rebuild <!-- gitmark:toc --> blocks in folder READMEs
$G secrets [files…]               # tokens / keys / passwords in the KB (masked)
$G attrs [--write]                # .gitattributes: README indexes merge=union
$G map -o docs-map.html           # self-contained HTML: tree + rendered md + link graph
$G stat | version
```

`query` conditions (AND-ed): `key=val` · `key!=val` · `key~substr` · `key>val` /
`key<val` (ISO dates compare as strings) · `'key?'` exists · `'!key'` missing ·
alternatives `status=draft|active` · nested `verified.by`, `links.depends_on`.
Computed keys: `path`, `area`, `type` (= `node_type`), `trust` (`unverified|machine|human`),
`stale`. Quote conditions with `?`, `!`, `<`, `>`, `|` in the shell.

## Workflow

1. Docs changed since the last index → `$G index`.
2. `$G search "<terms>"`. Results are **per file**, best section first; typed KB docs
   rank above stray READMEs, `deprecated`/`archived`/stale docs are demoted (and labeled).
   `[via]` shows which channels matched: `chunk` (terms in a section), `doc`
   (title/description/tags/path), `phrase` (exact substring), `fuzzy` (typos/forms).
3. Open the returned `file:line` and read the exact place.
4. **Miss?** Reformulate once or twice before giving up: the exact identifier
   (`MAX_RETRIES`, `api-gw-02`), the other language (ru↔en), a synonym
   (деплой/выкатка, rate limit/лимит запросов). Search is lexical — it can't guess synonyms.
5. Trust signals in results: `stale` = past its `stale_after`; `✓human` = verified by a
   person. Prefer a verified, live doc over a stale one when they disagree.

## Principles

- **Markdown is the source of truth.** Edit knowledge in `.md`, never the index.
- **Don't commit `.gitmark/`** (it's in `.gitignore`).
- What gets indexed: md files git sees (tracked + untracked, minus `.gitignore`), minus
  `.gitmarkignore` prefixes (one path per line — e.g. a docs copy used as test data).
- How to *maintain* the KB (ontology, file placement, trust fields) — see `kb-curate`.
