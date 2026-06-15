---
name: dev-flow
description: The spec-driven development loop for shipping a feature/fix fast and safely on top of a GitMark KB — research → tasks → goal → spec (markdown via skills) → isolated git worktree → implement → tests → independent review → dev-tests → prod-tests → ship (MR → dev → main). Use when starting a feature or fix, or when asked "how do we build/ship a change here".
---

# dev-flow — from idea to production

A battle-tested loop for shipping with an AI agent: a feature reaches production in
roughly **40 minutes to 2 hours**. The spec is plain **markdown written via the KB
skills**, the work happens in an **isolated git worktree**, and **only green reaches
prod**. The KB (see `kb-curate`) is the carrier of knowledge — onboarding, hand-off and
scaling all start from it, not from the code.

## The loop

1. **Research** — understand from facts, not guesses: read logs, traces, and the code
   itself. Reproduce before fixing.
2. **Tasks** — turn the research into a list of tracked tasks with dependencies. Nothing
   gets lost.
3. **Goal** — crystallize one clear goal and the "done" criterion from those tasks.
4. **Spec** — write it as markdown in the KB via **`kb-curate`** (ontology: `node_type`,
   frontmatter, typed links `links.documents: [src/…]`). The spec is durable knowledge,
   not a throwaway ticket — searchable, linkable, graphable.
5. **Isolate** — work in a dedicated **`git worktree`**: `main` stays untouched, parallel
   agents don't collide, and rollback is just dropping the worktree.
6. **Implement** — code to the spec inside the worktree; keep doc↔code linked
   (`implemented_by`).
7. **Tests** — write/adjust unit + E2E for the feature. The test is part of the feature,
   not an afterthought.
8. **Independent review** — run an **independent model** (e.g. Codex CLI, read-only) over
   the diff for logic and security bugs before rollout. A second model catches what the
   author's model misses — on a real production codebase this pass caught **191 bugs**
   before they reached prod.
9. **Dev-tests** — open an **MR with the commits into the `dev` branch**; run the full
   suite there. Red → fix in the worktree, don't merge.
10. **Prod-tests** — E2E/smoke against the **real prod contour**, not only mocks or dev.
    Verify behaviour where users live.
11. **Ship** — merge **`dev → main`** and deploy (build the new image *before* stopping the
    old container, then poll the healthcheck to measure real downtime).

## Git-flow

```
git worktree  →  MR + commits  →  dev branch (dev-tests)  →  main (prod-tests + deploy)  →  prod
```

## Principles

- **Spec = markdown + skills.** No ceremony, but ontological — so it stays searchable,
  linkable and graphable (`kb-search` / `gitmark map`).
- **Worktree isolation by default** — clean parallelism and clean rollback.
- **Tests and independent review are gates, not afterthoughts.**
- **Verify on prod**, not just dev.
- **md + git = source of truth**; everything derived (search index, graph) is regenerated.

## When to apply

Starting any non-trivial feature or fix. For a one-line change you can collapse steps,
but keep the gates (review + tests) — they are where the 191 bugs were caught.
