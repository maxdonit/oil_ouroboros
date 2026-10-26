## MODIFIED Requirements

### Requirement: Доступ через консоль
Консоль SHALL проксировать `GET /api/sources` и `GET /api/sources/domain` в сервис инструментов (`TOOLS_URL`) с
токеном `TOOLS_API_TOKEN` и пускать к ним роль `admin`; вкладка «Источники» (`sources`) есть только у неё (общие правила ролей —
спека `console-ui`).

#### Scenario: Роль user
- **WHEN** пользователь с ролью `user` запрашивает `GET /api/sources`
- **THEN** консоль отвечает 403 «роль «user»: нет доступа к GET /api/sources», вкладки «Источники» у него нет

#### Scenario: Роль с доступом
- **WHEN** администратор открывает вкладку «Источники»
- **THEN** консоль отдаёт ответ сервиса инструментов как есть
