---
description: Compose or update a knowledge-base document for the given topic following the OntoShip ontology (node_type, frontmatter, typed links, folder README index). Wraps the kb-curate skill.
allowed-tools: Bash(python3:*)
---

Compose or update a KB document for: `$ARGUMENTS`

Follow the `kb-curate` skill (ontology over code):

1. **Search first** — `python3 .qwen/skills/kb-search/gitmark.py search "$ARGUMENTS"`.
   If the topic already exists → **edit that doc**, don't create a second one.
2. **Pick a `node_type`** — `service` · `reference` · `runbook` · `gotcha` · `decision` ·
   `plan` · `guide` · `report` · `index` (unsure → spec = `reference`, how-to = `guide`)
   and the **right folder** (service → `docs/services/<svc>/`, cross-cutting → `docs/reference/`,
   ops → `docs/ops/`, plan → `docs/plans/`, decision → `docs/decisions/`).
3. **Write frontmatter** — `node_type`, `title`, `service`, `status: active`, `updated: <today>`.
4. **Add ≥1 typed link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans.
5. **Add a line to the folder `README.md`** (its index): `- [Title](file.md) — hook`.
6. **Lint + reindex** — `python3 .qwen/skills/kb-search/gitmark.py lint`
   then `... gitmark.py index`.

Report which file you created/updated, its `node_type`, and the links you added.
