# Spec Delta

## MODIFIED Requirements

### Requirement: API проигрывателя без агента и LLM
`GET /api/demo` SHALL отдавать `{recorded, turns: [{i, title, question, conversation_id, ms, cost_usd, rounds, tools,
agent_version}]}`, где `tools` — инструменты хода без `finish_task`, а `GET /api/demo/{i}` — запись хода целиком.
Эндпоинты MUST читать только файл записи (один раз на процесс консоли) и не обращаться к Ouroboros, tools и шлюзу; они
открыты без входа, а вкладка «Демо» есть в режиме администратора.

#### Scenario: Список и ход
- **WHEN** в файле один ход с вызовами research, verify_answer, finish_task
- **THEN** `GET /api/demo` даёт `turns[0].tools == ["research", "verify_answer"]`, `GET /api/demo/0` — ход с ответом
- **AND** `GET /api/demo/5` — 404 `нет такого демо-хода`

#### Scenario: Записи нет
- **WHEN** файла записи нет или он не читается как JSON
- **THEN** `GET /api/demo` отвечает `{"recorded": "", "turns": []}`, а вкладка показывает «Записей демо нет» с
  подсказкой `docker compose exec console oilo demo` и копированием `var/demo_turns.json → data/demo/turns.json`
