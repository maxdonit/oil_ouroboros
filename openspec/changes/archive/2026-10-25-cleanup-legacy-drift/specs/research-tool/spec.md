# Spec Delta

## MODIFIED Requirements

### Requirement: Журнал прогонов
Каждый вызов графового инструмента SHALL становиться прогоном в `var/runs.sqlite` (`kit.run_graph`): `run_id` из 12
шестнадцатеричных символов, `thread_id` — имя инструмента, `source` = `mcp:<инструмент>`, статус `running`, затем
`error` или `done`. События MUST сохраняться по порядку: `run_start`, `node_start`/`node_end` (с `ms` и сводкой) или
`node_error`, `llm_call`, `run_error`, `run_end` (с `usage`). Код журнал не очищает.

#### Scenario: Ошибка внутри графа
- **WHEN** узел бросил `ToolError("…")`
- **THEN** прогон получает статус `error` и текст ошибки как есть; для другого исключения — `<Класс>: <текст>`
- **AND** итоговое состояние (без `run_id`) и ответ (до 20 000 символов) сохраняются, предел рекурсии графа — 40

#### Scenario: Чтение прогонов
- **WHEN** консоль запрашивает прогон по `run_id`
- **THEN** `get_run` отдаёт поля прогона с разобранными `args`, `usage`, `state` и события по порядку; неизвестный
  `run_id` даёт `None`
- **AND** `recent_args("mcp:verify_answer", since)` отдаёт аргументы прогонов этого источника с `since`, новые первыми
