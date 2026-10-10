# Qwen Code integration

This document explains how to use OntoShip with **Qwen Code** instead of Claude Code.

## Overview

OntoShip is designed to be AI-agent-agnostic at its core. The only agent-specific layer
is the **skill/command system** — the knowledge base (md+git), the search engine (gitmark),
and the ontology model work identically regardless of agent.

This adaptation provides:
- **`AGENTS.md`** — entry point for Qwen Code (replaces `CLAUDE.md`)
- **`.qwen/skills/`** — Qwen Code compatible skills
- **Updated `commands/`** — slash commands with correct paths

## File structure for Qwen Code

```
repo-root/
├── AGENTS.md                    ← entry point for Qwen Code
├── .qwen/
│   └── skills/                  ← Qwen Code skills directory
│       ├── kb-search/
│       │   ├── gitmark.py       ← CLI engine (copied from skills/)
│       │   └── README.md
│       ├── kb-curate/
│       │   └── SKILL.md         ← ontology rules for Qwen Code
│       └── dev-flow/
│           └── SKILL.md         ← ship pipeline for Qwen Code
├── commands/                    ← slash commands (agent-agnostic, updated paths)
├── docs/                        ← knowledge base
└── .gitmark/                    ← generated artifacts (gitignored)
```

## Setup in a project

### 1. Clone the fork

```bash
git clone https://github.com/YOUR_USERNAME/ontoship.git
cd ontoship
```

### 2. Copy files into your project

```bash
# Copy entry point
cp ontoship/AGENTS.md /path/to/your/project/

# Copy skills
mkdir -p /path/to/your/project/.qwen/skills/
cp -r ontoship/.qwen/skills/* /path/to/your/project/.qwen/skills/

# Copy commands
cp -r ontoship/commands/* /path/to/your/project/commands/
```

### 3. (Optional) Add as a git submodule

```bash
cd /path/to/your/project
git submodule add https://github.com/YOUR_USERNAME/ontoship.git ontoship
```

Then reference `ontoship/` paths in commands/skills.

## How Qwen Code uses the skills

### Skills (`SKILL.md` files)

Qwen Code discovers skills in `.qwen/skills/` directory. Each skill has:
- A `SKILL.md` with frontmatter (`name`, `description`, `skill-level`)
- Implementation files (gitmark.py, etc.)

The skills are invoked by Qwen Code when you type slash commands or ask it to perform
related actions.

### Slash commands

Qwen Code supports slash commands defined in `commands/*.md`. The adapted commands use
the correct paths (`.qwen/skills/kb-search/gitmark.py`).

Available commands:
- `/kb <query>` — Search KB
- `/kb-map` — Generate HTML graph
- `/doc <topic>` — Create/update document
- `/onto-doc [scope]` — Build full KB
- `/ship <what>` — Run dev-flow pipeline

### Direct CLI usage

You can also call GitMark directly from the terminal or from Qwen Code's shell commands:

```bash
python3 .qwen/skills/kb-search/gitmark.py index
python3 .qwen/skills/kb-search/gitmark.py search "authentication"
python3 .qwen/skills/kb-search/gitmark.py lint
python3 .qwen/skills/kb-search/gitmark.py map -o docs-map.html
```

## Qwen Code-specific adaptations

### Path differences

| Original (Claude Code) | Qwen Code |
|------------------------|-----------|
| `CLAUDE.md` | `AGENTS.md` |
| `${CLAUDE_PLUGIN_ROOT}/skills/` | `.qwen/skills/` |
| `.claude/skills/` | `.qwen/skills/` |
| `.claude-plugin/` | N/A (Qwen Code uses `.qwen/`) |

### Skill format differences

Qwen Code's SKILL.md format uses YAML frontmatter:
```yaml
---
name: skill-name
description: >-
   What the skill does and when to use it.
---

# Skill content...
```

The adapted skills maintain all original functionality while using Qwen Code's format.

## Using OpenCodeReview together with OntoShip

Both tools complement each other:
- **OntoShip** — structured knowledge management, search, dev-flow
- **OpenCodeReview** — AI-powered code review with line-level comments

To use both:
1. Set up OntoShip as above
2. Install OpenCodeReview: `npm install -g @alibaba-group/open-code-review`
3. Use OntoShip's `/ship` command which includes "Independent review" step — this is
   where you can invoke OpenCodeReview's delegation mode

Example workflow:
```bash
# 1. Research
/ship "Add user authentication with JWT"

# 2. When asked for independent review, run OpenCodeReview:
ocr review --from main --to feature/auth

# 3. After review fixes, ship:
git push && gh pr create ...
```

## Troubleshooting

### "FTS5 not supported"

Your Python build doesn't have FTS5. Check:
```bash
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

On Ubuntu/Debian:
```bash
sudo apt-get install libsqlite3-dev
# Then reinstall Python
```

### "No index found — run gitmark index"

```bash
python3 .qwen/skills/kb-search/gitmark.py index
```

### Commands don't work in Qwen Code

Make sure your Qwen Code has access to the `.qwen/skills/` directory. If using a
project-level `.qwen/`, ensure the project root is correctly set in Qwen Code settings.

## Contributing

This is a fork of [vakovalskii/ontoship](https://github.com/vakovalskii/ontoship).
PRs with Qwen Code-specific improvements are welcome!
