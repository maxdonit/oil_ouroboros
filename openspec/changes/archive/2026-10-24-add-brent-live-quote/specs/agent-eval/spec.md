# Spec Delta

## MODIFIED Requirements

### Requirement: Набор кейсов
Кейсы SHALL храниться в JSONL (по умолчанию `evals/datasets/dev.jsonl`): строка — `Case` с полями `id`, `split`
(по умолчанию `dev`), `category`, `question`, `history` (прошлые реплики `{q, a}`), `expect`, `notes`. Пустые строки и
строки, начинающиеся с `#`, MUST пропускаться. `Expect` задаёт отказ, маршрут в веб, инструменты и их аргументы,
факты, типы и агентства ссылок, обязательные и запрещённые фразы, `min_verified_rate` = 0.9, `max_web_tier` = 2.

#### Scenario: Текущий dev-набор
- **WHEN** загружается `evals/datasets/dev.jsonl`
- **THEN** в нём 39 кейсов, все `split = dev`: категории report (18), out_of_scope (4), web (3), forecast (3),
  combined (2), followup (2), quote (2), conflict, scenario, history, honesty, advice (по 1)

#### Scenario: Факт с ключевыми словами
- **WHEN** у кейса категорий report, conflict, combined есть `expect.facts`
- **THEN** каждый факт — `{any_of: [числа], what, near: [регулярные выражения]}`, и у всех таких фактов `near` непуст

### Requirement: Детерминированные проверки без LLM-судьи
Исход кейса SHALL решаться только бинарными проверками `checks.evaluate(case, turn)` — по одному типу ошибки, каждая с
причиной провала в `detail`; LLM-судьи MUST NOT быть. Проверки читают показанный ответ без подвала
`**Источники**` / `**Sources**`, успешные вызовы инструментов, `went_web`, `verification`, `evidence` и онлайн-`checks`
хода.

#### Scenario: Общие проверки
- **WHEN** проверяется любой кейс
- **THEN** всегда есть `no_error` (нет ошибки и ответ непуст), `scope` (отказ ровно когда `expect.refusal`),
  `persona_clean`, `language`; при `must_include_any` — `must_include` (у категории honesty — `admits_missing`), при
  `must_not_include` — `must_not_include`

#### Scenario: Ожидался отказ
- **WHEN** у кейса `expect.refusal = true`
- **THEN** после `language` проверки заканчиваются, а вызванные MCP-инструменты дают проваленную `no_tools`

#### Scenario: Ответ по существу
- **WHEN** отказ не ожидался
- **THEN** проверяются `research_first` (первый MCP — research, list_reports или расчёт), `route_web`, `tools`,
  `tool_args` (значение или `"<0"`/`">0"`), `no_tools` (без forecast_price, supply_shock_scenario, price_history, price_quote),
  `facts`, `cites_<kind>`, `cites_orgs`, `verified`, `answer_is_checked_text`, `source_quality`, `labels`
- **AND** `verified` пройдена, если проверка хода есть, доля подтверждённых цифр ≥ `min_verified_rate` и нет
  неизвестных ссылок; `source_quality` — нет процитированного веба уровня выше `max_web_tier` или без уровня

#### Scenario: Факт ищется в показанном ответе
- **WHEN** эталон `any_of: [2.6]` и `[0.62]`, ответ «Спрос снизится на 2,6 млн б/с и будет на 620 тыс. б/с ниже
  [Отчёт IEA OMR, октябрь 2026, с. 3]»
- **THEN** `facts` пройдена (числа в метках источников фактами не считаются); с «3,1 млн б/с» — провалена
