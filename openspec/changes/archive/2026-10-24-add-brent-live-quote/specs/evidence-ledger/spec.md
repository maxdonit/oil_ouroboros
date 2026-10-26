# Spec Delta

## MODIFIED Requirements

### Requirement: Пакеты инструментов
`new_pack(tool, ids, run_id, question, decisions, **meta)` SHALL выдавать пакету следующий глобальный номер `P<n>` и
сохранять время создания, инструмент, прогон, вопрос, список id, решения и метаданные; связь «пакет — доказательство»
пишется в `pack_items`. `pack(id)` MUST отдавать `id`, `created`, `tool`, `run_id`, `question`, `ids`, `decisions` и
поля метаданных верхнего уровня, а для неизвестного id — `None`.

#### Scenario: Кто создаёт пакеты
- **WHEN** отвечают инструменты и консоль
- **THEN** пакеты создают `research`, `search_reports`, `search_web` (с `after` = пакет research), `forecast_price`,
  `supply_shock_scenario`, `price_history`, `price_quote` и консоль через `POST /api/packs` (пакет `dialog` — доказательства прошлых
  реплик разговора)
- **AND** `verify_answer` пакетов не создаёт
