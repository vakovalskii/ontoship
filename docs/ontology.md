---
node_type: reference
title: GitMark ontology — a knowledge model over code
service: _platform
status: active
description: "The OntoShip document model: node types, frontmatter properties, typed links, trust and lifecycle fields (OKF-compatible) and the lint invariants I1–I9."
updated: 2026-10-15
tags: [ontology, palantir, okf, node-type, links, frontmatter, trust]
links:
  relates_to: [services/kb-curate/README.md, services/gitmark-cli/README.md, reference/architecture.md, decisions/okf-alignment.md]
---

# GitMark ontology — a knowledge model over code

> Rules for **how to maintain** a knowledge base (not just how to search it). The idea
> is borrowed from **Palantir's Ontology** (Gotham/Foundry): an organization is modeled
> as a graph of typed **objects**, their **properties**, and the **links** between them —
> a "digital twin." GitMark applies the same model to project documentation: every `.md`
> document is an **object** with a **type** and **properties**, and markdown links are
> **typed links**. The result is an ontology not of data, but of **knowledge over code**.

Why: so the KB doesn't rot into a pile of files. Type + properties + links make it
navigable, checkable (via the linter), and graphable (`gitmark map`).

## Semantic layer — Objects, Properties, Links

### Object types (`node_type`)

Each document has exactly one `node_type` — its "table" in the ontology.

| node_type | what it is | lives in |
|---|---|---|
| `service` | overview/index of one service/component | `docs/services/<svc>/README.md` |
| `reference` | cross-cutting spec (not about one service) | `docs/reference/` |
| `runbook` | operational procedure ("how to X") | `docs/ops/` |
| `gotcha` | a pitfall + how to avoid it | `docs/ops/` |
| `decision` | an architectural/product decision (ADR) | `docs/decisions/` |
| `plan` | a plan/design before implementation | `docs/plans/` |
| `guide` | how to use something (clients, public API) | varies |
| `report` | a one-off dated analysis/audit | `docs/reviews/` |
| `index` | a folder's table of contents | any `README.md` |

Rule: if unsure, a spec is `reference`, a how-to is `guide`. Add a new type only if
none fit and there will be ≥3 such documents.

### Properties (frontmatter)

YAML frontmatter at the top of the file — the "columns" of the object row.

```yaml
---
node_type: service          # REQUIRED — one of the table above (OKF: `type`)
title: Billing              # human-readable object name
description: "Who pays what and when: tariffs, trial, renewals and refunds in the billing service."
service: billing            # which service; use a sentinel (e.g. _platform) for cross-cutting
status: active              # active | draft | deprecated | archived
updated: 2026-06-06          # last meaningful edit (YYYY-MM-DD)
tags: [payments, api]       # free-form labels for search/grouping
generated: { by: claude-code/opus-5-5, at: 2026-06-06T10:00:00Z }   # who wrote it (OKF)
verified:                   # who confirmed it against reality (OKF) — never self-asserted
  - { by: human:vk, at: 2026-06-07T09:00:00Z }
stale_after: 2026-12-01T00:00:00Z   # perishable facts: re-check after this instant (OKF)
links:                      # typed links (see below), optional
  documents: [../../src/billing]
  depends_on: [../reference/architecture.md]
  supersedes: [old-billing.md]
---
```

Required: `node_type`. Strongly recommended for load-bearing docs
(`service|reference|runbook|plan|decision`): `title`, `description`, `service`, `status`,
`updated`. `description` is not decoration: it is indexed with extra weight, and on our
benchmark adding it lifted hit@1 from 0.77 to 0.83 (see
[decisions/okf-alignment.md](decisions/okf-alignment.md)).

### Trust and lifecycle (from OKF)

- **`generated`** `{by, at}` — who/what produced the current content. Actors:
  `<tool>/<version>` for agents (`claude-code/opus-5-5`), `human:<id>`, `process:<id>`.
- **`verified`** — list of `{by, at}` confirmations. The **trust tier** is derived, not
  stored: no `verified` → *unverified*; only machines → *machine*; any `human:` →
  *human*. An agent never writes a `human:` verification for itself.
- **`stale_after`** — absolute instant; past it the doc is *stale* (lint I8, demoted and
  labeled in search).

`gitmark query` answers questions over all of these without a database, e.g.
`query 'generated.by~claude' trust=unverified type=runbook`.

`service` is a **controlled vocabulary** you define for your project.

### Link types (`links`)

Links are markdown links `[text](path.md)`. The link type is declared by a key under
`links:`; inline links default to `relates_to`. `gitmark map` draws the graph from them.

| link type | meaning | direction |
|---|---|---|
| `documents` | this doc describes that code/service | doc → code |
| `depends_on` | read that one first to understand this | doc → doc |
| `supersedes` | replaces a stale document | new → old |
| `relates_to` | adjacent topic (default for inline links) | doc ↔ doc |
| `implemented_by` | where it lives in code | doc → source file |
| `part_of` | belongs to a larger index | doc → index |

The doc→code link (`documents`/`implemented_by`) is what makes this an ontology **over
code**: a document is explicitly tied to the files/component it describes.

## Kinetic layer — Actions (curation rules)

In Palantir, **Actions** sit on top of the semantics — what you can do with objects.
Here, Actions = the **curation procedures** a human/agent runs (see `kb-curate` skill):
CREATE → classify, place, frontmatter, link, index. UPDATE → bump `updated`/`status`.
DEPRECATE → `status` + `supersedes`. LINK → no orphans. REINDEX → `gitmark index`.

## Entry point

One file for every agent: `AGENTS.md` is read by Codex, Qwen Code, Cursor and others;
Claude Code reads `CLAUDE.md`. So the rules live in `AGENTS.md` and `CLAUDE.md` imports
it — never a copy, or the two drift apart.

```markdown
# AGENTS.md
> **<Project>** — one sentence: what it is and for whom.
> Before working read [docs/README.md](docs/README.md).
> - 3–5 rules that hold everywhere (language, branches/MR, what never to commit)
>
> ```
> docs/            knowledge base (OntoShip): README.md — master index
>   services/      one folder per service
>   reference/     specs; ops/ runbooks; decisions/ ADRs
> src/ …           the code, one line per top dir
> ```
…the rest of the rules…
```

```markdown
# CLAUDE.md
All rules live in `AGENTS.md`; Claude Code imports it below.

@AGENTS.md
```

A project that keeps everything in `CLAUDE.md` moves it to `AGENTS.md` (`git mv`) and leaves
the import. Nested folders follow the same pair when they need their own rules.

## Invariants (checked by `gitmark lint`)

- **I1.** Every load-bearing doc has frontmatter with a valid `node_type`.
- **I2.** `node_type`/`service`/`status` values are within their vocabularies.
- **I3.** No orphans: a load-bearing doc has ≥1 incoming or outgoing link.
- **I4.** No broken links (a markdown link to a missing file).
- **I5.** Every `docs/**` folder has a `README.md` index.
- **I6.** A `supersedes` target has `status: deprecated|archived`.
- **I7.** A `documents`/`implemented_by` target (code path) exists.
- **I8.** `stale_after` has not passed.
- **I9.** The entry point is `AGENTS.md` with a header (what the project is, the key rules)
  and a short tree linking `docs/README.md`; `CLAUDE.md`, if present, only imports it with a
  line `@AGENTS.md`. See [Entry point](#entry-point).

## Indexes, snapshots, debt

- **Indexes are append-lists, not documents with state.** A folder `README.md` carries no
  `updated:` (git log knows), new lines go to the end of their section, and
  `gitmark attrs --write` marks indexes `merge=union` in `.gitattributes` — parallel branches
  (and parallel agents in worktrees) adding lines to one index merge without conflicts.
- **Built, not written.** Put `<!-- gitmark:toc -->` … `<!-- /gitmark:toc -->` in a folder
  README and `gitmark toc --write` fills it with `- [title](file) — description` for every doc
  and sub-index of the folder; `gitmark toc` (no flag) fails when an index drifted. Hand-written
  text around the block stays.
- **`status: frozen`** — a snapshot on a date (audit, measurement, fleet state). Never edited:
  when the fact changes, edit the living doc and leave the snapshot.
- **Debt ratchet.** On a KB with old findings, gate CI or `pre-push` with
  `gitmark lint --baseline origin/main`: it fails only when a change adds findings, so the gate
  is green on day one and debt can only shrink.
- **No secrets in the KB.** `gitmark secrets` scans the corpus for token/key/password shapes.

Vocabularies are extensible per project in this file's own frontmatter
(`vocab: {service: […], node_type: […], status: […], link: […]}`); without
`vocab.service`, the service vocabulary is derived from folder names.

## Relation to the Open Knowledge Format (OKF)

OKF (Google, v0.2) standardizes the same idea — a folder of markdown concepts with YAML
frontmatter. OntoShip reads OKF bundles as-is and adds what OKF deliberately leaves out:

| | OKF | OntoShip |
|---|---|---|
| type key | `type` (free string) | `node_type` (controlled vocab) — `type` accepted |
| summary | `description` | `description` (same; `summary:` accepted as a synonym) |
| trust / lifecycle | `generated`, `verified`, `status`, `stale_after` | same fields; `stable` = `active` |
| links | untyped, meaning in prose | **typed** (`documents`, `depends_on`, `supersedes`…) incl. doc → code |
| folder index | `index.md` | `README.md` (`index.md` also accepted) |
| validation | consumers must tolerate anything | `gitmark lint` invariants I1–I9 |
| search / query | out of scope | `gitmark search` (FTS5) + `gitmark query` (frontmatter) |

## Why this, not a wiki/Notion

- **md+git** is already the source of truth. The ontology adds *structure on top* without
  changing the medium — frontmatter and links are plain markdown, readable in any viewer.
- The object/link graph gives Foundry-like navigation with no platform: `gitmark map`
  renders it from the same files.
- Types + invariants keep the KB from degrading into a pile as it grows — the exact pain
  Palantir's ontology solves for data, applied here to knowledge over code.

Prototype model: [Palantir Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview)
· [Core concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts).
