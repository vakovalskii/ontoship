---
name: kb-curate
description: >-
   Правила поддержки Markdown базы знаний (GitMark) — применяй при добавлении,
   редактировании, перемещении или удалении документации (.md).
   Лёгкая онтология: каждый документ имеет тип, свойства (frontmatter) и
   связанные ссылки (typed links). Поддерживай KB структурированной,
   а не свалкой файлов. Вызывай при: "добавить документ", "записать решение",
   "обновить docs", "реорганизовать docs".
---

# kb-curate — поддержка базы знаний (GitMark онтология)

Полная модель: `docs/ontology.md`. Этот скилл — чеклист операционных правил.
Принцип: **md+git = источник истины, поверх — онтология** (типы объектов, свойства,
связи — вдохновлено Palantir Foundry/Gotham, но для документации над кодом).

## Перед написанием — search, не дублируй

```bash
python3 .qwen/skills/kb-search/gitmark.py search "<тема>"
```
Если тема уже существует — **обнови существующий документ**, не создавай второй.

## При ДОБАВЛЕНИИ (CREATE)

1. **Выбери `node_type`**: `service` · `reference` · `runbook` · `gotcha` · `decision`
   · `plan` · `guide` · `report` · `index`. Не уверен → spec = `reference`, how-to = `guide`.
2. **Размести в правильной папке** (тип → папка): сервисный →
   `docs/services/<svc>/`; кросс-сервисный → `docs/reference/`; операционный →
   `docs/ops/`; план → `docs/plans/`; решение → `docs/decisions/`.
3. **Добавь frontmatter** (минимум `node_type`; для важных документов также
   `title`, `service`, `status: active`, `updated: YYYY-MM-DD`):
   ```yaml
   ---
   node_type: runbook
   title: Deploy the gateway
   service: api
   status: active
   updated: 2026-06-06
   links:
     documents: [../../scripts/deploy.sh]
     depends_on: [../reference/architecture.md]
   ---
   ```
4. **Добавь ≥1 связь** — на код (`documents`/`implemented_by`) или на другой документ
   (`depends_on`/`relates_to`). Не создавай сирот.
5. **Добавь строку в `README.md` папки** (её индекс): `- [Title](file.md) — hook`.

## При РЕДАКТИРОВАНИИ (UPDATE)

- Смысл изменился → обновить `updated:`. Документ устарел → `status: deprecated`
  и добавить `supersedes: [новый.md]` на замену. Хлам → удалить (git хранит историю).

## При ПЕРЕМЕЩЕНИИ (reorganize)

- `git mv` (сохраняет историю), затем **переписать все ссылки** на него и обновить
  README индексы обеих папок.

## Всегда в конце

```bash
python3 .qwen/skills/kb-search/gitmark.py lint     # инварианты I1–I6
python3 .qwen/skills/kb-search/gitmark.py index    # rebuild search
```
`lint` флаги: отсутствие/сломанный frontmatter, тип вне словаря, сироты (0 связей),
битые ссылки, папки без README. Исправляй до чистого результата.

## Словари (не придумывай значения)

- `node_type`: service|reference|runbook|gotcha|decision|plan|guide|report|index
- `status`: active|draft|deprecated|archived
- `service`: controlled vocabulary проекта (определи в `docs/ontology.md`)
