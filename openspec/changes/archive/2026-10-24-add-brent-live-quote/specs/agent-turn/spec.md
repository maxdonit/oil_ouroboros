# Spec Delta

## MODIFIED Requirements

### Requirement: Деньги и онлайн-проверки хода
Запись хода SHALL содержать деньги и время по слоям — `cost_agent_usd` (раунды LLM агента), `cost_tools_usd`
(прогоны инструментов), `cost_usd`, `rounds`, `tokens_in`, `tokens_cached`, `ms`, отпечаток версии агента — и
`checks`: онлайн-проверки без эталона для мониторинга (ответ дан, проверка вызвана и пройдена, показан проверенный
текст, первый инструмент, метки, чужие ссылки, «кухня» агента, язык, ошибки инструментов).

#### Scenario: Стоимость хода
- **WHEN** раунд LLM стоил $0.002 (100 входных токенов, 50 из кэша), прогон `research` — $0.001
- **THEN** `cost_usd = 0.003`, `cost_agent_usd = 0.002`, `cost_tools_usd = 0.001`, `rounds = 1`, `tokens_cached = 50`

#### Scenario: Состав проверок
- **WHEN** ход собран
- **THEN** `checks` — `answered` (текст непуст, статус `completed`/`completed_by_console`), `finish_held`,
  `refused`, `research_first` (первый MCP — `research`, `list_reports`, `forecast_price`, `supply_shock_scenario`,
  `price_history` или `price_quote`), `verify_called`, `verify_ok`, `answer_is_checked_text` (`verified_draft` и проверка
  пройдена), `labels`, `citations_in_turn`, `persona_clean`, `language_ok`, `final_rewrite_rejected`,
  `tool_errors`, `builtin_tools`
- **AND** `persona_clean = false`, если в тексте ответа есть «Ouroboros», «Уробор…», `verify_answer`, `finish_task`,
  `mcp_oil` или «пакет P#»

#### Scenario: Правка агента отвергнута
- **WHEN** агент после проверки заменил цифру, и финал вернул `fallback_verified_draft` с непустым `rejected`
- **THEN** `checks.final_rewrite_rejected = true`, `checks.verify_ok = true`, `checks.answer_is_checked_text = false`
