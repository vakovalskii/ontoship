---
name: kb-curate
description: >-
   Rules for maintaining a markdown knowledge base (GitMark) — apply when adding, editing, moving, or deleting documentation (.md). A lightweight code-ontology: every document has a type, properties (frontmatter incl. a one-line description and OKF trust fields), and typed links. Keeps the KB structured instead of a pile of files. Use on "add a doc", "record a decision", "update the docs", "reorganize docs".
---

# kb-curate — how to maintain the knowledge base (GitMark ontology)

Full model: `docs/ontology.md`. This skill is the operational checklist. Principle:
**md+git is the source of truth, with an ontology on top** (object types / properties /
links — inspired by Palantir Foundry/Gotham, compatible with Google's Open Knowledge
Format (OKF) frontmatter).

## Before writing — search, don't duplicate

```bash
python3 <plugin>/skills/kb-search/gitmark.py search "<topic>"
python3 <plugin>/skills/kb-search/gitmark.py query type=<type> --fields path,title   # what exists
```
If the topic already exists — **edit the existing doc**, don't create a second one.

## When ADDING knowledge (CREATE)

1. **Pick a `node_type`**: `service` · `reference` · `runbook` · `gotcha` · `decision`
   · `plan` · `guide` · `report` · `index`. Unsure → spec = `reference`, how-to = `guide`.
2. **Put it in the right folder** (type → folder): service-specific →
   `docs/services/<svc>/`; cross-cutting → `docs/reference/`; ops procedure →
   `docs/ops/`; plan → `docs/plans/`; decision → `docs/decisions/`.
3. **Add frontmatter** (min `node_type`; for load-bearing docs also `title`,
   `description`, `service`, `status: active`, `updated: YYYY-MM-DD`):
   ```yaml
   ---
   node_type: runbook
   title: Deploy the gateway
   description: "How to roll out (deploy) the API gateway to prod and roll back if healthchecks fail."
   service: api
   status: active
   updated: 2026-06-06
   generated: { by: claude-code/<model>, at: 2026-06-06T10:00:00Z }
   links:
     documents: [../../scripts/deploy.sh]
     depends_on: [../reference/architecture.md]
   ---
   ```
   - **`description`** — one sentence, 12–30 words, in the words of someone who has *not*
     read the doc: the task/symptom it answers + key entities, a synonym where natural.
     Not the title again. It is indexed with extra weight and shown in folder indexes.
   - **`generated`** — when an agent wrote or substantially rewrote the content:
     `{ by: <tool>/<model>, at: <ISO datetime>Z }`. A human author: `by: human:<id>`.
   - **`verified`** — *only* the actor who actually checked the content against reality
     adds it: `verified: [{ by: human:<id>, at: … }]` or `process:<name>`. **An agent
     never writes `human:` into `verified` on its own** — that is the whole point of the
     trust tier. Content changed materially → drop stale `verified` entries.
   - **`stale_after`** — for perishable facts (hosts, versions, counts, "current state"):
     an ISO instant after which the doc must be re-checked. `lint` flags it (I8).
4. **Add ≥1 link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans. Paths are relative to the doc, or start with
   `/` for repo-root-relative (OKF style, stable when docs move).
5. **Add a line to the folder's `README.md`** (its index): `- [Title](file.md) — description`.

## Entry point — AGENTS.md, CLAUDE.md imports it

`AGENTS.md` is read by Codex, Qwen Code, Cursor and others; Claude Code reads `CLAUDE.md`.
Keep one source: rules in `AGENTS.md`, `CLAUDE.md` is only

```markdown
# CLAUDE.md
All rules live in `AGENTS.md`; Claude Code imports it below.

@AGENTS.md
```

`AGENTS.md` starts with a header: one sentence on what the project is, a link to
`docs/README.md`, 3–5 rules that hold everywhere, and a short tree of `docs/` and the top
code dirs (one line each). When the KB gains a folder or a top dir moves — update that tree.
Found a project with everything in `CLAUDE.md`? `git mv CLAUDE.md AGENTS.md`, add the header,
write the import. `lint` checks it (I9). Full model: `docs/ontology.md` → Entry point.

## When EDITING (UPDATE)

- Meaning changed → bump `updated:` (and `generated.at` if an agent rewrote it).
- Doc is stale → `status: deprecated` and set `supersedes: [old.md]` on the replacement.
  Junk → delete (git keeps history).

## When MOVING (reorganizing)

- `git mv` (preserves history), then **rewrite every link** to it and update the
  README indexes of both folders.

## Always at the end

```bash
python3 <plugin>/skills/kb-search/gitmark.py lint     # invariants I1–I9
python3 <plugin>/skills/kb-search/gitmark.py index    # rebuild search
python3 <plugin>/skills/kb-search/gitmark.py health   # optional: KB scorecard
```
`lint` flags: missing/broken frontmatter, values outside vocabularies, orphans, broken
links, folder without README, `supersedes` target still live, typed link to a missing
code path, past `stale_after`. Fix until clean.

## Vocabularies (don't invent values)

- `node_type`: service|reference|runbook|gotcha|decision|plan|guide|report|index
  (OKF's `type:` is accepted as a synonym)
- `status`: active|draft|deprecated|archived (OKF `stable` = active)
- link types: documents|depends_on|supersedes|relates_to|implemented_by|part_of
- `service`: your project's controlled vocabulary. Declare it (and any extra types,
  statuses, link types) in the frontmatter of `docs/ontology.md`:
  ```yaml
  vocab:
    service: [api, billing, frontend]
    node_type: [meeting]
  ```
  Without `vocab.service`, lint derives it from folder names under `docs/` and
  `services|apps|packages/`. Values starting with `_` (`_platform`) are sentinels.
