---
name: dev-flow
description: >-
   Gated ship pipeline — структурированный процесс разработки от research до ship.
   Вызывай при: "ship feature", "implement task", "build from idea to production",
   "guided dev loop".
---

# dev-flow — gated ship pipeline

Принцип: **spec → implement → test → review → ship**. Gates (тесты, независимое ревью)
нельзя пропустить. Spec — носитель знаний, а не чёртов тикет.

## Pipeline (11 шагов)

```
Research → Tasks → Goal → Spec → Isolate → Implement → Tests →
Independent Review → Dev-tests → Prod-tests → Ship
```

### 1. Research — исследуй факты

- Изучи логи, трейсы, код **до** попытки починить.
- Воспроизведи проблему.
- Не чини вслепую.

### 2. Tasks — декомпозируй

- Разбей работу на отслеживаемые задачи.
- Каждая задача — атомарное изменение.

### 3. Goal — цель

- Сформулируй **одну чёткую цель** + критерий "done".
- Goal должен быть верифицируемым.

### 4. Spec — спецификация

- Напиши spec как **Markdown документ в KB** через `kb-curate`.
- Укажи `node_type: plan` или `reference`.
- Добавь typed links (`documents:[src/…]`).
- Сначала сделай **search** (`kb-search`) — не дублируй существующее.

### 5. Isolate — изолируй

- Работай в отдельной `git worktree`.
- Не миксуй изменения с основным кодом.

### 6. Implement — реализуй

- Код **по spec**, не по памяти.
- Spec — источник истины.

### 7. Tests — тесты

- Напиши или обнови unit + E2E тесты.
- Покрытие — не цель, **верификация** — цель.

### 8. Independent review — независимое ревью

- Запусти независимую модель (Codex CLI, OpenCodeReview, другой агент) **в read-only**.
- Агенты не должны редактировать код — только ревью.

### 9. Dev-tests — dev-тесты

- MR + коммиты в `dev`.
- Запусти полный suite.
- Red → фикс, не мержь.

### 10. Prod-tests — prod-тесты

- E2E/smoke против реальной prod-окружности.
- Не мержь, пока prod-tests не зелёные.

### 11. Ship — shipship

- Merge `dev → main`.
- Deploy (build-before-stop + healthcheck-poll).
- Убедись, что всё зелёно.

## Gates (не пропустить!)

| Gate | Что | Кто |
|------|-----|-----|
| Tests | Unit + E2E | CI |
| Independent review | Read-only AI review | Codex / OCR / другой агент |
| Dev-tests | Полный suite | CI |
| Prod-tests | E2E/smoke на prod | Human + CI |

Spec нельзя пропускать — он **носит знания**, а не тикет.
