---
description: Search the project knowledge base via GitMark (FTS5 bm25 + trigram/fuzzy). Argument = query; no argument shows stat.
allowed-tools: Bash(python3:*)
---

Search the project's markdown knowledge base (all `.md`: docs/, README files, etc.).

User query: `$ARGUMENTS`

Do:
1. If `$ARGUMENTS` is empty — run `python3 .qwen/skills/kb-search/gitmark.py stat` and show the `/kb <query>` syntax.
2. Otherwise:
   - Refresh the index if needed: `python3 .qwen/skills/kb-search/gitmark.py index`.
   - `python3 .qwen/skills/kb-search/gitmark.py search "$ARGUMENTS" -k 8`
   - Summarize the top hits (which files, `file:line`), don't reprint whole snippets. Open the 1–2 most relevant files and answer the question.
