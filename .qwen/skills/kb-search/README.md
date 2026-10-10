# kb-search — GitMark CLI engine

Single-file Python CLI engine for markdown knowledge base search, indexing,
visualization, and ontology linting. Zero dependencies — pure stdlib.

## Installation

No install needed — `gitmark.py` uses only Python stdlib.

Prerequisites:
- Python 3.7+ with FTS5 support (standard in most distributions)
- SQLite with FTS5 extension enabled

## Commands

| Command | Description |
|---------|-------------|
| `gitmark index` | Build/update search index |
| `gitmark search "<query>" [-k 8] [--json]` | Search (BM25 + trigram/fuzzy) |
| `gitmark map [-o output.html]` | Generate self-contained HTML with tree + link graph |
| `gitmark serve [-p 8799]` | Local HTTP server for HTML preview |
| `gitmark stat` | Index/Knowledge base statistics |
| `gitmark lint [paths...] [--strict]` | Check ontology (I1–I6) |
| `gitmark version` | Show version |

## Search Strategies

### BM25
Term-based ranking via SQLite FTS5. Fast, exact match.

### Trigram (Fuzzy)
Substring matching, typo-tolerant, supports Cyrillic. Uses n-gram tokenization.

### Fuzzy Phrases
4-character window matching — catches typos and morphology while filtering noise.

## Architecture

```
markdown files (source of truth)
    ↓ gitmark index
SQLite FTS5 index (.gitmark/index.db) — search
HTML graph (.gitmark/map.html) — visualization
```

## Usage with Qwen Code

The GitMark CLI is called from Qwen Code commands and skills:

```bash
python3 .qwen/skills/kb-search/gitmark.py index
python3 .qwen/skills/kb-search/gitmark.py search "your query"
python3 .qwen/skills/kb-search/gitmark.py lint
python3 .qwen/skills/kb-search/gitmark.py map -o docs-map.html
```

## Output artifacts

All derived artifacts live in `.gitmark/` (gitignored):
- `index.db` — SQLite FTS5 search index
- `map.html` — self-contained HTML visualization (tree + rendered MD + force graph)

## Lint flags (I1–I6)

| Flag | Level | Meaning |
|------|-------|---------|
| I1 | ERR | Load-bearing doc without frontmatter/node_type |
| I2 | ERR/WARN | node_type/service/status value outside vocabulary |
| I3 | WARN | Orphan — no links (in or out) |
| I4 | ERR | Broken link to missing .md file |
| I5 | WARN | Folder without README.md index |
| I6 | WARN | supersedes target is not deprecated/archived |
