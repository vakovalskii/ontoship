---
node_type: reference
title: OntoShip slash commands
service: _platform
status: active
updated: 2026-10-15
tags: [commands, slash-commands, reference]
links:
  documents: [../../commands/kb.md, ../../commands/kb-map.md, ../../commands/doc.md, ../../commands/onto-doc.md, ../../commands/ship.md]
  relates_to: [../services/gitmark-cli/README.md, ../services/dev-flow/README.md]
---

# OntoShip slash commands

Reference for the slash commands shipped by the **OntoShip** Claude Code plugin. Each
command is a thin `commands/*.md` definition that drives a skill or engine. Two families:

- **KB curation & search** — `/kb`, `/kb-map`, `/doc`, `/onto-doc` — drive the GitMark
  CLI (`skills/kb-search/gitmark.py`) and the `kb-curate` ontology rules.
- **Dev-flow** — `/ship` — drives the gated `dev-flow` pipeline from idea to production.

`${CLAUDE_PLUGIN_ROOT}` below resolves to the installed plugin directory; the GitMark CLI
is `${CLAUDE_PLUGIN_ROOT}/skills/kb-search/gitmark.py`.

## Summary

| Command | What it does | Args | Drives |
|---|---|---|---|
| `/kb` | Search the project KB (all `.md`) and answer from the top hits; structural questions go to `query` | `<query>` (empty → stat + usage) | GitMark CLI `search` / `query` / `stat` / `index` |
| `/kb-map` | Build a self-contained HTML map of the KB (tree + link graph) | `[output-path]` (default `docs-map.html`) | GitMark CLI `map` / `index` |
| `/doc` | Compose or update **one** KB document per the ontology | `<topic>` | `kb-curate` skill + GitMark CLI |
| `/onto-doc` | Build (or rebuild) the **whole** KB by fanning out curator agents | `[scope]` (empty → whole repo) | `kb-curate` skill via `Task` fan-out + GitMark CLI |
| `/ship` | Take a feature/fix from idea to production through the gated pipeline | `<change description>` | `dev-flow` skill |

---

## `/kb` — search the knowledge base

- **Definition:** `commands/kb.md` · **Allowed tools:** `Bash(python3:*)`
- **What it does:** searches every `.md` git sees in the project (docs/, READMEs, etc.) via
  GitMark (per-file ranking: section bm25 + title/description/tags + trigram/fuzzy, RRF
  fusion; typed docs boosted, deprecated/stale demoted), then summarizes the top hits and
  answers from the 1–2 most relevant files. Structural questions ("which decisions are
  active", "plans per service") are answered with `gitmark.py query` over frontmatter.
- **Args:** `$ARGUMENTS` = the search query. **Empty** → runs `gitmark.py stat` and shows
  the `/kb <query>` syntax instead of searching.
- **Behavior:**
  1. Empty query → `gitmark.py stat` + usage hint.
  2. Otherwise: refresh index if needed (`gitmark.py index`), then
     `gitmark.py search "$ARGUMENTS" -k 8`, summarize hits as `file:line` (no full
     snippets), and open the most relevant files to answer.
- **Drives:** GitMark CLI (`kb-search` skill).

## `/kb-map` — render the KB graph

- **Definition:** `commands/kb-map.md` · **Allowed tools:** `Bash(python3:*)`
- **What it does:** generates a self-contained HTML map of the knowledge base — a
  collapsible tree, rendered markdown, and a force/radial link graph built from the typed
  links in frontmatter.
- **Args:** `$ARGUMENTS` = output path. **Default `docs-map.html`** when omitted.
- **Behavior:**
  1. Refresh the index: `gitmark.py index`.
  2. Build the map: `gitmark.py map -o "${ARGUMENTS:-docs-map.html}"`.
  3. Report the output path and offer to `open <path>` or `serve` it.
- **Drives:** GitMark CLI `map` (`kb-search` skill).

## `/doc` — compose/update one KB document

- **Definition:** `commands/doc.md` · **Allowed tools:** `Bash(python3:*)`
- **What it does:** composes or updates a **single** KB document for a topic, following the
  `kb-curate` ontology (node_type, frontmatter, typed links, folder README index).
- **Args:** `$ARGUMENTS` = the topic/document subject.
- **Behavior (per `kb-curate`):**
  1. **Search first** (`gitmark.py search`) — if the topic exists, edit that doc; never
     create a duplicate.
  2. **Pick a `node_type`** (`service` · `reference` · `runbook` · `gotcha` · `decision` ·
     `plan` · `guide` · `report` · `index`) and the right folder.
  3. **Write frontmatter** — `node_type`, `title`, `description`, `service`,
     `status: active`, `updated: <today>`, `generated` (agent-written).
  4. **Add ≥1 typed link** (to code or a sibling doc) — no orphans.
  5. **Add a line to the folder `README.md`** index.
  6. **Lint + reindex** — `gitmark.py lint` then `gitmark.py index`.
- **Drives:** `kb-curate` skill + GitMark CLI. Wraps `gitmark:doc`.

## `/onto-doc` — build the whole KB

- **Definition:** `commands/onto-doc.md` · **Allowed tools:** `Bash(python3:*)`, `Task`
- **What it does:** builds (or rebuilds) the **entire** OntoShip KB for the repo by
  surveying the codebase and fanning out `kb-curate` curator subagents per area, then
  linting, indexing, and mapping. Used to bootstrap or rebuild a project's whole KB.
- **Args:** `$ARGUMENTS` = scope hint. **Empty** → whole repo; or a subset
  (e.g. `services/api services/billing`, or "only reference docs").
- **Behavior:**
  1. **Survey** the repo (dirs, services, entry points, build/deploy, existing docs);
     check coverage with `gitmark.py stat`.
  2. **Decompose** into doc areas — service READMEs (`service`), cross-cutting specs
     (`reference`), ops procedures (`runbook`/`gotcha`), decisions (`decision`).
  3. **Dispatch curators (fan-out)** — one `Task` subagent per area, each following
     `kb-curate` on its slice only (search first, pick node_type + folder, frontmatter,
     ≥1 typed link, README index line). Independent areas run in parallel, scoped to avoid
     collisions.
  4. **Entry point + indexes** — `AGENTS.md` with a header and short tree (link to
     `docs/README.md`), `CLAUDE.md` = `@AGENTS.md` only (lint I9), `docs/README.md`
     is the master index, every folder has a README index.
  5. **Verify & derive** — `gitmark.py lint` (fix broken links/orphans/missing
     frontmatter), then `gitmark.py index`, then `gitmark.py map -o docs-map.html`.
  6. **Report** — docs created/updated, coverage before→after, lint result, map path,
     areas needing a human decision.
- **Drives:** `kb-curate` skill via `Task` fan-out + GitMark CLI. Wraps `gitmark:onto-doc`.

## GitMark CLI subcommands (0.2)

| Subcommand | What it does |
|---|---|
| `index` | rebuild `.gitmark/index.db` (chunks, frontmatter, typed links) |
| `search "<q>" [--type --status --service]` | ranked, one result per file, with type/status/trust labels |
| `query key=val … [--count-by key] [--fields …]` | structured questions over frontmatter, straight from md |
| `lint [--json]` | ontology invariants I1–I9 |
| `lint --baseline <ref>` | ratchet: exit 1 only if findings grew vs `<ref>`, exit 2 if it could not compare |
| `toc [--write]` | rebuild `<!-- gitmark:toc -->` blocks in folder READMEs from `title`/`description`; without `--write` — check |
| `secrets [files…]` | token / key / password shapes in the KB (masked); exit 1 when found |
| `attrs [--write]` | `.gitattributes`: `merge=union` for README indexes |
| `health [--json]` | KB scorecard: typed %, description %, typed links, orphans, trust, stale, duplicates |
| `map` / `serve` / `stat` / `version` | HTML graph, local server, index stats, version |

## `/ship` — run the dev-flow

- **Definition:** `commands/ship.md` · **Allowed tools:** none declared (uses default
  toolset)
- **What it does:** drives a feature/fix from idea to production through the gated
  OntoShip dev-flow pipeline.
- **Args:** `$ARGUMENTS` = the change to ship (feature/fix description).
- **Pipeline (per the `dev-flow` skill, end to end):**
  1. **Research** — understand from facts (logs, traces, code); reproduce before fixing.
  2. **Tasks** — decompose into tracked tasks.
  3. **Goal** — one clear goal + a "done" criterion.
  4. **Spec** — write it as markdown in the KB via `kb-curate` (node_type, frontmatter,
     typed links `documents:[src/…]`); search the KB first — don't duplicate.
  5. **Isolate** — work in a dedicated `git worktree`.
  6. **Implement** — code to the spec.
  7. **Tests** — write/adjust unit + E2E.
  8. **Independent review** — run an independent model (e.g. Codex CLI, read-only) over
     the diff.
  9. **Dev-tests** — MR + commits into `dev`; run the full suite. Red → fix, don't merge.
  10. **Prod-tests** — E2E/smoke against the real prod contour.
  11. **Ship** — merge `dev → main` and deploy (build-before-stop + healthcheck-poll).
- **Gates:** tests + independent review are not skippable; the spec is the carrier of
  knowledge, not a throwaway ticket.
- **Drives:** `dev-flow` skill.
