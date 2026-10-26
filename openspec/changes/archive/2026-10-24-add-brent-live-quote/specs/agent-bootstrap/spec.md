# Spec Delta

## MODIFIED Requirements

### Requirement: Инструменты MCP агента
Засев SHALL подключать агенту ровно один MCP-сервер — `MCP_SERVERS[0]`: `id` `oil`, `transport` `streamable_http`,
`url` `http://tools:8000/mcp`, `auth_token` `Bearer <MCP_TOKEN>`. Его `allowed_tools` MUST совпадать с именами
инструментов MCP-сервера (`mcp/server.py` `TOOLS`) в том же порядке: инструмент, не добавленный в `allowed_tools`,
агент молча не увидел бы.

#### Scenario: Состав allowed_tools
- **WHEN** засев записал `settings.json`
- **THEN** `allowed_tools` — девять инструментов: `research`, `search_reports`, `search_web`, `forecast_price`,
  `supply_shock_scenario`, `price_history`, `price_quote`, `verify_answer`, `list_reports`, а `auth_token` =
  `Bearer <MCP_TOKEN>`

#### Scenario: Новый инструмент MCP
- **WHEN** в `mcp/server.py` добавлен инструмент, а `allowed_tools` шаблона не обновлён
- **THEN** тест `test_agent_is_allowed_exactly_the_mcp_tools` падает

### Requirement: Содержание засеянной памяти
Засеянная память SHALL задавать роль и регламент аналитика: `identity.md` — Ouroboros как старший аналитик
нефтегазового рынка (цифры только из `mcp_oil__*`, проверка до ответа); `WORLD.md` — среда (сеть без интернета, шлюз,
9 инструментов MCP); `knowledge/overview.md` («Shared understanding») — экспертиза, «Вне компетенции», приоритет
источников, протокол инструментов, формат ответа. Число проверок регламент MUST NOT задавать: его сообщает
`verify_answer` (каталог `verify.max_checks`).

#### Scenario: Отказ вне компетенции
- **WHEN** агент читает раздел «Вне компетенции» регламента
- **THEN** отказ начинается со слов «Это вне моей компетенции» (на английском — «This is outside my area of
  expertise»), без инструментов; эти слова узнаёт `agent.REFUSAL` (спека `agent-turn`)

#### Scenario: Протокол завершения
- **WHEN** тест читает `ouroboros/memory/knowledge/overview.md`
- **THEN** в нём есть «Регламент», `acceptance_subject` (не использовать), «отдельным шагом» (`finish_task` после
  результата `verify_answer`) и `render=false` (исключение хода консоли)
- **AND** нет «всегда первым» и «не больше двух»

#### Scenario: Текущая цена
- **WHEN** агент читает протокол инструментов регламента
- **THEN** вопрос о текущей цене Brent («сейчас», «курс», «котировка») ведёт сразу в `price_quote()`, без `research`
- **AND** регламент требует назвать в ответе, что это фьючерс ближайшего месяца, время котировки, а спот EIA — с его
  датой; при `status` `stale` или `unavailable` — прямо сказать, что свежей котировки нет
- **AND** текущих котировок WTI и Urals у агента нет: последняя цена EIA с датой (`price_history`), Urals — оценка
