# Spec Delta

## MODIFIED Requirements

### Requirement: Правила хода консоли
`agent.TURN_RULES` SHALL содержать только отличия хода консоли от регламента агента («Shared understanding»,
спека `agent-bootstrap`): вопрос не о нефтегазовой отрасли — только отказ по разделу «Вне компетенции»;
`mcp_oil__verify_answer` с `render=false`, в `finish_task` — проверенный черновик со ссылками символ в символ; не
писать в память и не запускать фоновых задач; раздел «This room» — не источник, цифры только из инструментов
хода. Протокол инструментов MUST NOT повторяться.

#### Scenario: Одна точка правды
- **WHEN** тест сверяет `TURN_RULES` с регламентом `ouroboros/memory/knowledge/overview.md`
- **THEN** в `TURN_RULES` есть «Вне компетенции» и «This room», нет `research`, `acceptance_subject`, `pack_ids`,
  «ещё раз», «не больше»
- **AND** в регламенте есть `acceptance_subject`, «отдельным шагом» и `render=false`

#### Scenario: Комната владельца не источник
- **WHEN** в контексте задачи комната Main с готовыми ответами о нефти и метками, а вопрос — «Какая сейчас цена газа
  Henry Hub?»
- **THEN** агент вызывает `price_quote(henry_hub)`, и цифры ответа — из её результата (F#) в этом ходе
- **AND** кейсы eval `Q04` и `Q05` проходят на стенде с этой комнатой
