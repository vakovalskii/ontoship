# OntoShip — entry point

OntoShip is a Claude Code marketplace (`ontoship`) shipping **gitmark** — an md+git
knowledge base (FTS5 search, HTML graph, ontology linter) plus the spec-driven dev-flow
built on top of it.

> **destructive-guard** (the PreToolUse safety hook) now lives in its own repo:
> [github.com/vakovalskii/destructive-guard](https://github.com/vakovalskii/destructive-guard).

## Where things live

```
.claude-plugin/        marketplace.json + plugin.json (the gitmark plugin manifest)
commands/              slash commands: /kb /kb-map /doc /onto-doc /ship
skills/
  kb-search/           the gitmark CLI engine (gitmark.py) + SKILL.md
  kb-curate/           rules for maintaining the KB as a typed ontology
  dev-flow/            the gated ship pipeline
docs/                  the knowledge base itself (this is the KB)
```

## Start here

- **Knowledge base** → [docs/README.md](docs/README.md) — master index
- **The model** → [docs/ontology.md](docs/ontology.md) — how docs are typed & linked
- **How it fits together** → [docs/reference/architecture.md](docs/reference/architecture.md)
- **Commands** → [docs/reference/commands.md](docs/reference/commands.md)

## Principle

Markdown + git is the source of truth. Everything derived — the search index
(`.gitmark/index.db`), the HTML graph — is regenerated, never committed as truth.
Every folder's `README.md` is its index; never let a doc become an orphan.

## Maintain

```bash
python3 skills/kb-search/gitmark.py index    # rebuild the index after editing docs
python3 skills/kb-search/gitmark.py lint     # check the ontology (broken links, orphans, frontmatter)
python3 skills/kb-search/gitmark.py map -o docs-map.html   # regenerate the graph
```
