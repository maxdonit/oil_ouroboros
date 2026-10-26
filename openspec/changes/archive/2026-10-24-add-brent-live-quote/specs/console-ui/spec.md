# Spec Delta

## MODIFIED Requirements

### Requirement: Дымовой тест oilo smoke
`oilo smoke [--question …]` SHALL сделать один ход через Ouroboros (разговор `smoke`, ход сохраняется с источником
`smoke`) и проверить по телам запросов в журнале шлюза, что видела модель. Код выхода MUST быть 0 только при всех
пройденных проверках. Вопрос по умолчанию: «Кратко: с какими вопросами ты можешь помочь? Ответь в двух предложениях
без инструментов.»

#### Scenario: Состав проверок
- **WHEN** ход завершён
- **THEN** проверяются `ouroboros_health`, `answered`, `gateway_sees_turn` (есть вызовы LLM с меткой хода),
  `cost_known` (cost > 0, у моделей `GigaChat*` — токены > 0) и `language` (язык ответа = язык вопроса)
- **AND** по телу первого вызова с инструментами: `tools_mcp` — ровно research, search_reports, search_web,
  forecast_price, supply_shock_scenario, price_history, price_quote, verify_answer, list_reports; `tools_builtin`;
  `playbook_in_context` («Регламент: старший аналитик» в сообщениях); `identity_in_context`

#### Scenario: Лишний встроенный инструмент
- **WHEN** в запросе к модели есть встроенный инструмент вне finish_task, knowledge_read, knowledge_list,
  enable_tools, list_available_tools
- **THEN** `tools_builtin` печатается как `НЕТ`, и команда завершается с кодом 1
