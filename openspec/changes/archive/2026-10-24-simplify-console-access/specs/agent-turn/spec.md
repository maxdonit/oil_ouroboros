## MODIFIED Requirements

### Requirement: Разговор принадлежит пользователю
Консоль MUST пускать к разговору (`POST /api/chat` с `conversation_id`, `GET /api/conversations/{conversation_id}`,
`GET /api/chat/{turn_id}`) только пользователя, чей префикс `c-<sha256(логин)[:12]>-` стоит в начале id разговора;
роль `admin` видит все разговоры. Чужой разговор — `403` «чужой разговор»: история разговора уходит агенту в
текст задачи.

#### Scenario: Чужой разговор
- **WHEN** пользователь `bob` (роль `user`) шлёт `POST /api/chat` с `conversation_id` разговора пользователя `ann`
- **THEN** консоль отвечает `403`
- **AND** `GET /api/conversations/<тот же id>` от `bob` — `403`, от `admin` — `200`

#### Scenario: Префиксы различают логины
- **WHEN** считаются префиксы для `иван`, `пётр`, `Bob`, `bob`, `bob.smith`, `bobsmith`
- **THEN** все шесть префиксов различны

#### Scenario: Чтение разговора
- **WHEN** владелец разговора запрашивает `GET /api/conversations/{conversation_id}`
- **THEN** ответ — до 50 последних ходов разговора (всех источников) от старых к новым
