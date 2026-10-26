# Spec Delta

## ADDED Requirements

### Requirement: Разговор по его id
Консоль SHALL пускать к разговору (`POST /api/chat` с `conversation_id`, `GET /api/conversations/{conversation_id}`,
`GET /api/chat/{turn_id}`) любого, кто знает его id: логинов нет. `GET /api/conversations/{conversation_id}` MUST
отдавать до 50 последних ходов разговора (всех источников) от старых к новым.

#### Scenario: Продолжение разговора
- **WHEN** приходит `POST /api/chat` с `conversation_id` прежнего разговора, в том числе вида `c-<12 hex>-<10 hex>`
- **THEN** ход идёт в этом разговоре, его история уходит агенту в текст задачи

#### Scenario: Чтение разговора
- **WHEN** запрашивается `GET /api/conversations/{conversation_id}`
- **THEN** ответ — до 50 последних ходов разговора (всех источников) от старых к новым

## MODIFIED Requirements

### Requirement: Запуск и прогресс хода в чате
Консоль SHALL принимать вопрос `POST /api/chat` `{question, conversation_id?}` и вести ход в фоновом потоке: ответ
`{turn_id, conversation_id}` приходит сразу, пустой вопрос — `400` «пустой вопрос». `GET /api/chat/{turn_id}` отдаёт
прогресс идущего хода из памяти процесса (`done: false`), затем сохранённый ход (`done: true`); неизвестный ход —
`404` «нет хода». Эндпоинта отмены хода у консоли нет: задачу отменяет только сам ход.

#### Scenario: Новый разговор
- **WHEN** приходит `POST /api/chat` с `{"question": "Прогноз Brent?"}` без `conversation_id`
- **THEN** ответ — `turn_id` из 12 hex-символов и `conversation_id` вида `c-<10 hex>`
- **AND** прогресс хода заведён со стадией `queued`, ход идёт в фоновом потоке

#### Scenario: Пустой вопрос
- **WHEN** `question` состоит из пробелов
- **THEN** консоль отвечает `400` «пустой вопрос» и задачу агенту не ставит

#### Scenario: Ход в работе
- **WHEN** Ouroboros принял задачу, а агент вызвал `research`
- **THEN** `GET /api/chat/{turn_id}` — `done: false`, `stage: "tools"` (до этого — `accepted` с `task_id`),
  `tool_calls` с вызовом `research`, `elapsed_s` с точностью 0,1 с
- **AND** после записи хода в журнал — `{"done": true, "turn": {...}}`, прогресс из памяти удалён

## REMOVED Requirements

### Requirement: Разговор принадлежит пользователю
**Reason**: Логинов нет, разговор не к кому привязывать.
**Migration**: Доступ к разговору — по его id (требование «Разговор по его id»); прежние id `c-<12 hex>-<10 hex>`
работают.
