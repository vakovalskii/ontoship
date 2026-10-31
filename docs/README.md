---
node_type: index
title: OntoShip knowledge base
service: _platform
status: active
links:
  part_of: [../AGENTS.md]
---

# OntoShip — knowledge base

Master index of the docs. Markdown is the source of truth; the search index
(`.gitmark/index.db`) and the HTML graph are **derived** — regenerate them, never commit
them as truth. See [ontology.md](ontology.md) for how documents are typed and linked.

## Reference

- [architecture.md](reference/architecture.md) — how OntoShip fits together (marketplace → two plugins → skills → KB)
- [commands.md](reference/commands.md) — slash commands: `/kb`, `/kb-map`, `/doc`, `/onto-doc`, `/ship`
- [metrics.md](reference/metrics.md) — what OntoShip is measured by (experience-transfer metrics)
- [ontology.md](ontology.md) — the knowledge model (node_type, properties, typed links, linter)

## Decisions

- [ontoship-positioning.md](decisions/ontoship-positioning.md) — what OntoShip is for (team experience-transfer for AI-agent dev)

## Services

- [gitmark-cli](services/gitmark-cli/README.md) — KB search/index engine
- [kb-curate](services/kb-curate/README.md) — KB maintenance skill
- [dev-flow](services/dev-flow/README.md) — spec-driven ship pipeline
- [destructive-guard](services/destructive-guard/README.md) — destructive-command guard hook

## Derive

```bash
# from the repo root
python3 skills/kb-search/gitmark.py index            # build .gitmark/index.db
python3 skills/kb-search/gitmark.py search "query"   # FTS5 + trigram search
python3 skills/kb-search/gitmark.py lint             # check the ontology (I1–I6)
python3 skills/kb-search/gitmark.py map -o docs-map.html   # HTML overview + graph
```
