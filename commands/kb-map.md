---
description: Build the OntoShip KB graph (gitmark map) — collapsible tree + rendered markdown + force/radial link graph as a self-contained HTML — and point the user to it.
allowed-tools: Bash(python3:*)
---

Generate and surface the knowledge-base map.

Optional `$ARGUMENTS` = output path (default `docs-map.html`).

Do:
1. Refresh the index: `python3 .qwen/skills/kb-search/gitmark.py index`
2. Build the map: `python3 .qwen/skills/kb-search/gitmark.py map -o "${ARGUMENTS:-docs-map.html}"`
3. Tell the user the output path and that it's a self-contained HTML (tree + link graph), and offer to open it (`open <path>`) or `serve` it.
