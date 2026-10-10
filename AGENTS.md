# OntoShip — entry point для Qwen Code

> **Это адаптация Ontoship (vakovalskii/ontoship) для работы с Qwen Code.**
> Оригинал: [github.com/vakovalskii/ontoship](https://github.com/vakovalskii/ontoship)
>
> OntoShip — система управления знаниями (Knowledge Base) на базе **Markdown + Git** +
> **FTS5-поиск** (SQLite BM25 + trigram/fuzzy) + **онтология** (node_type, frontmatter,
> typed links) + **dev-flow** (от research до ship).

## Где что лежит

```
.qwen/skills/              skills для Qwen Code
  kb-search/               ядро: gitmark.py (index/search/map/lint/stat/serve/version)
  kb-curate/               правила онтологии (node_type, frontmatter, links)
  dev-flow/                gated ship pipeline (research → ship)
commands/                   slash commands: /kb /kb-map /doc /onto-doc /ship
docs/                       база знаний проекта
.gitmark/index.db           поисковый индекс (gitignored, сгенерированный артефакт)
```

## Принцип

**Markdown + Git = источник истины.** Всё производное (поисковый индекс, HTML-граф)
регенерируется из Markdown, не коммитится как истина. Каждый `README.md` в папке —
это её индекс.

## Быстрый старт

```bash
# 1. Индексация
python3 .qwen/skills/kb-search/gitmark.py index

# 2. Поиск
python3 .qwen/skills/kb-search/gitmark.py search "your query"

# 3. Граф связей (HTML)
python3 .qwen/skills/kb-search/gitmark.py map -o docs-map.html

# 4. Линтинг онтологии
python3 .qwen/skills/kb-search/gitmark.py lint
```

## Slash commands

| Команда | Описание |
|---------|----------|
| `/kb <query>` | Поиск по KB (FTS5 + fuzzy) |
| `/kb-map` | Построить HTML-граф связей |
| `/doc <topic>` | Создать/обновить один документ |
| `/onto-doc [scope]` | Построить всю KB (fan-out curator agents) |
| `/ship <what>` | Dev-flow: research → spec → implement → tests → ship |

## Start here

- **База знаний** → [docs/README.md](docs/README.md) — master index
- **Онтология** → [docs/ontology.md](docs/ontology.md) — как документы типизируются и связываются
- **Архитектура** → [docs/reference/architecture.md](docs/reference/architecture.md)
- **Команды** → [docs/reference/commands.md](docs/reference/commands.md)
- **Qwen Code** → [docs/usage/qwen-code.md](docs/usage/qwen-code.md) — как настроить для Qwen Code

## Maintain

```bash
python3 .qwen/skills/kb-search/gitmark.py index    # rebuild index
python3 .qwen/skills/kb-search/gitmark.py lint     # check ontology (I1–I6)
python3 .qwen/skills/kb-search/gitmark.py map -o docs-map.html   # graph
```
