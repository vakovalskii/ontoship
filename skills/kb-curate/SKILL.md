---
name: kb-curate
description: >-
   Rules for maintaining a markdown knowledge base (GitMark) — apply when adding, editing, moving, or deleting documentation (.md). A lightweight code-ontology: every document has a type, properties (frontmatter), and typed links. Keeps the KB structured instead of a pile of files. Use on "add a doc", "record a decision", "update the docs", "reorganize docs".
---

# kb-curate — how to maintain the knowledge base (GitMark ontology)

Full model: `docs/ontology.md`. This skill is the operational checklist. Principle:
**md+git is the source of truth, with an ontology on top** (object types / properties /
links — inspired by Palantir Foundry/Gotham, but for documentation over code).

## Before writing — search, don't duplicate

```bash
python3 <plugin>/skills/kb-search/gitmark.py search "<topic>"
```
If the topic already exists — **edit the existing doc**, don't create a second one.

## When ADDING knowledge (CREATE)

1. **Pick a `node_type`**: `service` · `reference` · `runbook` · `gotcha` · `decision`
   · `plan` · `guide` · `report` · `index`. Unsure → spec = `reference`, how-to = `guide`.
2. **Put it in the right folder** (type → folder): service-specific →
   `docs/services/<svc>/`; cross-cutting → `docs/reference/`; ops procedure →
   `docs/ops/`; plan → `docs/plans/`; decision → `docs/decisions/`.
3. **Add frontmatter** (min `node_type`; for load-bearing docs also `title`, `service`,
   `status: active`, `updated: YYYY-MM-DD`):
   ```yaml
   ---
   node_type: runbook
   title: Deploy the gateway
   service: api
   status: active
   updated: 2026-06-06
   links:
     documents: [../../scripts/deploy.sh]
     depends_on: [../reference/architecture.md]
   ---
   ```
4. **Add ≥1 link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans.
5. **Add a line to the folder's `README.md`** (its index): `- [Title](file.md) — hook`.

## When EDITING (UPDATE)

- Meaning changed → bump `updated:`. Doc is stale → `status: deprecated` and set
  `supersedes: [old.md]` on the replacement. Junk → delete (git keeps history).

## When MOVING (reorganizing)

- `git mv` (preserves history), then **rewrite every link** to it and update the
  README indexes of both folders.

## Always at the end

```bash
python3 <plugin>/skills/kb-search/gitmark.py lint     # invariants I1–I6
python3 <plugin>/skills/kb-search/gitmark.py index    # rebuild search
```
`lint` flags: missing/broken frontmatter, type outside vocabulary, orphans (0 links),
broken links, folder without README. Fix until clean.

## Vocabularies (don't invent values)

- `node_type`: service|reference|runbook|gotcha|decision|plan|guide|report|index
- `status`: active|draft|deprecated|archived
- `service`: your project's controlled vocabulary (define it in `docs/ontology.md`)
