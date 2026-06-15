---
name: dev-loop
description: How to build software with a coding agent — a simple, human-in-the-loop development loop. Recon a tasks/ folder, talk to the model, plan in a loop (with compaction), save the plan, implement in a loop, then ship-and-verify (git → docs → full tests → dev → e2e → watch logs). Use when starting a feature, "how do we build this", running a long agent session, or working from a tasks/ backlog. Deliberately NOT spec-driven (SDD/Spec-Kit) and NOT an agent swarm — one strong model, one human watching, the whole way through.
---

# dev-loop — building with a coding agent

The whole method in one line: **recon → talk → plan (save it) → loop → ship & verify —
with a human in the loop the entire time.** A `tasks/` folder of plain-markdown task files
is the backlog; a saved plan is the only ceremony; the loop does the work; nothing reaches
users without git + docs + the full test gauntlet + dev + logs.

This is the counter-position to the 2026 fashion (see *Why this, not…* below): no
spec-as-contract, no role-play agent cast, no unattended swarm. Simpler wins.

## The loop

### 0. Recon (разведка)
Read-only scout of the `tasks/` folder and the part of the codebase a task touches. Build
a map first — *where* things live, naming, the existing patterns — before proposing a
single change. Conclusions, not file dumps. (Delegate the fan-out; keep the summary.)

### 1. Talk (разговор)
**Converge on intent in plain conversation before writing code.** What are we actually
building, what's in scope, what's the simplest shape. The conversation *is* the spec —
cheaper to change than a document, and it's where the human steers.

### 2. Plan — in a loop, with compaction
Draft the main plan, iterate it across a long session; when context fills, **compact** and
keep going (the plan survives the compaction, the noise doesn't). Then **save the plan to a
file** (`docs/plans/<feature>.md`, `node_type: plan` — per the kb-curate ontology). The
saved plan is the contract between this session and the next — and the thing goal-mode reads
when you're away.

### 3. Implement — in a loop
Execute the saved plan step by step in a loop. The human watches each step land — this is
*not* fire-and-forget. Re-read the plan after compaction so the thread never drifts.

### 4. When you're away — worktree + goal mode
If you can't sit at the PC: run an isolated **git worktree** + **goal mode** that
implements the tasks from the folder **one after another, one at a time**. Bounded
autonomy: a single task per step, isolated branch, the saved plan as the goal — not a fleet
running loose. You read the result when you're back.

### 5. Ship & verify — every time, no shortcuts
1. **Push everything to git** (branch + PR).
2. **Update the docs** — the memory bank (GitMark: md+README+git). Code without updated
   docs is half-done.
3. **Run the full test cycle** locally.
4. **Push to `dev`**, run the heavier tests there: **e2e → the whole suite**.
5. **Watch the logs on dev**, run more tests against the live dev stack. Only then is it
   real.

### 6. Cross-model review — `codex exec`, fixed in the PR
The model that *wrote* the code is the wrong one to be its only reviewer. Run an
independent pass with a **different** CLI/model — `codex exec` in read-only mode — over the
diff, and **fix every real finding in the PR** before merge:

```bash
codex exec -s read-only --cd <repo> -o /tmp/review.md --color never \
  "Review this PR/diff: bugs, security, regressions, missed edge cases. \
   For each: severity, file:line, the exploit/scenario, the fix. Validate by code — \
   scout 'critical' findings are often false; separate real from by-design."
```

Read the report, **validate each finding against the code** (cross-model reviews also
hallucinate), fix the real ones as commits on the PR branch, and note what you rejected and
why. Two models, opposite jobs: Opus writes, codex/GPT-5.5 tries to break it.

## Stay in the loop (the one non-negotiable)

A human drives, behind the strongest models (e.g. **Opus 4.8** + **GPT-5.5**), and
**watches every stage — including dev.** Human-in-the-loop belongs in the *middle*, not
bolted on as a final review. The agent is leverage, not an autopilot. If you wouldn't ship
it unwatched, don't automate it unwatched.

## Why this, not the fashionable thing

- **vs Spec-Driven Development / GitHub Spec-Kit** (specify → plan → tasks → implement, a
  "constitution", spec-as-source-of-truth): that replaces a conversation with a document
  pipeline and front-loads ceremony. Here recon + talk *is* the spec, and the only artifact
  is a lightweight saved plan you revise or replace. Specs rot; a short plan + the code +
  the KB don't.
- **vs BMAD / agile-role agent casts** (PM/Architect/Dev/Tester bots): role-play theater.
  One capable model plus a human who understands the system beats a cast of personas.
- **vs autonomous agent swarms**: orchestration is already fragile past a handful of agents
  — more agents in one swarm scale into chaos, not throughput. Autonomy here is *bounded*
  (goal-mode, one task at a time, in a worktree, with the plan as the goal), and a person
  reviews the output. No unattended fleet.

**Principle: the simplest thing that ships correctly wins** — a `tasks/` folder, a
conversation, a saved plan, a loop, and a human who never looks away.
