---
node_type: index
title: Services
service: _platform
status: active
links:
  part_of: [../README.md]
---

# Services

Component overviews — one folder per service/skill, each with a `README.md` index.

| Service | What it is |
|---|---|
| [gitmark-cli](gitmark-cli/README.md) | The KB search/index engine (`gitmark.py`) — FTS5 bm25 + trigram/fuzzy, HTML graph, ontology linter |
| [kb-curate](kb-curate/README.md) | The skill that keeps the markdown KB a typed ontology (CREATE/UPDATE/DEPRECATE/LINK/REINDEX) |
| [dev-flow](dev-flow/README.md) | The gated spec-driven ship pipeline (research → … → MR → dev → main) |
| [destructive-guard](destructive-guard/README.md) | PreToolUse hook that turns destructive commands into y/n confirmations |
