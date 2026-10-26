# Spec Delta

## MODIFIED Requirements

### Requirement: Набор кейсов
Кейсы SHALL храниться в JSONL (по умолчанию `evals/datasets/dev.jsonl`): строка — `Case` с полями `id`, `split`
(по умолчанию `dev`), `category`, `question`, `history` (прошлые реплики `{q, a}`), `expect`, `notes`. Пустые строки и
строки, начинающиеся с `#`, MUST пропускаться. `Expect` задаёт отказ, маршрут в веб, инструменты и их аргументы,
факты, типы и агентства ссылок, обязательные и запрещённые фразы, `min_verified_rate` = 0.9, `max_web_tier` = 2.

#### Scenario: Текущий dev-набор
- **WHEN** загружается `evals/datasets/dev.jsonl`
- **THEN** в нём 42 кейса, все `split = dev`: категории report (18), quote (5), out_of_scope (4), web (3),
  forecast (3), combined (2), followup (2), conflict, scenario, history, honesty, advice (по 1)

#### Scenario: Факт с ключевыми словами
- **WHEN** у кейса категорий report, conflict, combined есть `expect.facts`
- **THEN** каждый факт — `{any_of: [числа], what, near: [регулярные выражения]}`, и у всех таких фактов `near` непуст
