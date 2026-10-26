# Spec Delta

## MODIFIED Requirements

### Requirement: Аргументы инструментов
Схема аргументов SHALL выводиться из сигнатуры функции инструмента в `mcp/server.py`, а проверку типов, диапазонов и
перечислений MUST выполнять MCP SDK: нарушение возвращается агенту результатом с `isError=true` и именем поля в тексте.
`pack_ids` инструмента `verify_answer` приводится функцией `pack_list` к списку `P<n>` (латинская `P` или кириллическая
`Р`, голое число; без повторов, не больше 30).

#### Scenario: Сигнатуры и значения по умолчанию
- **WHEN** агент читает схемы инструментов
- **THEN** они такие: `research(question, focus="")`; `search_reports(queries, orgs=None, editions=None,
  question="")`; `search_web(queries, pack_id, freshness="month", reason="")` с `freshness` из
  `day|week|month|year|any`; `forecast_price(benchmark="brent", horizon_months=3, method="auto")` с `benchmark` из
  `brent|wti|urals`, `horizon_months` 1–12, `method` из `auto|ensemble|ets|arima|theta|drift|rw`
- **AND** `supply_shock_scenario(delta_mbd, benchmark="brent", horizon_months=3, compliance=1.0, offset=0.0)` с
  `delta_mbd` от −30 до 30, `compliance` и `offset` от 0 до 1; `price_history(benchmark="brent", start="2025-01-01",
  freq="M")` с `freq` из `M|D`; `price_quote(benchmark="brent")` с `benchmark` из
  `brent|wti|henry_hub|ttf`; `verify_answer(answer, pack_ids, question="",
  render=true)`, где обязательны `answer` и `pack_ids`; `list_reports()` без аргументов

#### Scenario: Значение вне диапазона
- **WHEN** агент вызывает `supply_shock_scenario` с `delta_mbd=500` или `forecast_price` с `horizon_months=24`
- **THEN** результат имеет `isError=true`, а текст называет поле `delta_mbd` или `horizon_months`

#### Scenario: Нет обязательного аргумента
- **WHEN** агент вызывает `research` с аргументом `q` вместо `question`
- **THEN** результат имеет `isError=true`, и текст содержит `question`

#### Scenario: Разные записи пакета
- **WHEN** передано `["P12", "[P13]", "пакет Р14", 15, "P12"]`
- **THEN** проверка получает `["P12", "P13", "P14", "P15"]`
- **AND** `pack_list(None)` даёт пустой список

### Requirement: Графы и источники для консоли
`GET /api/graphs` SHALL возвращать `graphs` — четыре графа из кода (`research`, `search_web`, `calc`, `verify_answer`)
с узлами и рёбрами (`graphs/kit.describe`) — и `mcp_tools` со схемой каждого инструмента; каждый инструмент MCP, кроме
`list_reports`, MUST входить ровно в один граф. `GET /api/sources` отдаёт разделы `reports`, `web`, `prices`, `quote`;
`GET /api/sources/domain?url=…` оценивает адрес правилами фильтра веб-поиска (спека `web-search`).

#### Scenario: Соответствие графов инструментам
- **WHEN** консоль запрашивает `/api/graphs`
- **THEN** граф `research` отвечает за `research` и `search_reports`, `search_web` — за `search_web`, `calc` — за
  `forecast_price`, `supply_shock_scenario`, `price_history`, `price_quote`, `verify_answer` — за `verify_answer`
- **AND** у графа `research` рёбра вида `branch` ведут в `web` и `pack`, у каждого есть подпись

#### Scenario: Проверка доменов
- **WHEN** запрошено `/api/sources/domain?url=https://www.eia.gov/outlooks/steo/`
- **THEN** ответ: `domain` = `eia.gov`, `tier` = 0, `allowed` = true
- **AND** для `url=kp.ru` (без схемы дополняется `https://`) — `allowed` = false и причина со словом `deny`

#### Scenario: Раздел веба в источниках
- **WHEN** консоль запрашивает `/api/sources`
- **THEN** `web` содержит `provider`, `max_results`, `min_tier_for_numbers` = 2, `policy`, `tiers`, `titles`
  (`titles["0"]` = «первичный источник»), `rated_domains` и `used` — домены из журнала доказательств с уровнем
- **AND** `prices.series` содержит ряды `brent`, `wti` и `henry_hub`, у каждого `as_of` или `error`
