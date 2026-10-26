# Spec Delta

## MODIFIED Requirements

### Requirement: Собственный UI Ouroboros
Профиль `owner` SHALL публиковать UI и API Ouroboros сервисом `ouroboros-ui` (`alpine/socat:1.8.0.3`,
`TCP-LISTEN:8765,fork,reuseaddr` → `TCP:ouroboros:8765`) на `${OILO_BIND:-127.0.0.1}:${OUROBOROS_UI_PORT:-38630}`.
Вход — по паролю `OUROBOROS_PASSWORD`, по умолчанию `ouroboros` (засев кладёт его в
`OUROBOROS_NETWORK_PASSWORD`). Ограничения хода консоли (`disabled_tools`,
`memory_mode=forked`) к чату владельца не применяются: он пишет в общую память агента.

#### Scenario: Адрес UI по умолчанию
- **WHEN** профиль `owner` включён, `OILO_BIND` и `OUROBOROS_UI_PORT` не заданы
- **THEN** UI Ouroboros доступен на `127.0.0.1:38630`

#### Scenario: Без профиля owner
- **WHEN** `COMPOSE_PROFILES` не содержит `owner`
- **THEN** сервисы `ouroboros-ui`, `egress` и `schedules` не запускаются, и снаружи к агенту ведёт только консоль

#### Scenario: Пароль по умолчанию
- **WHEN** `.env` создан `scripts/init_env.py` из `.env.example` без правок, профиль `owner` включён
- **THEN** UI Ouroboros на `127.0.0.1:38630` пускает по паролю `ouroboros`, без пароля — страница входа (401)
