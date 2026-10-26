## MODIFIED Requirements

### Requirement: Сети, порты и изоляция агента
Compose SHALL создавать сеть `oilo` с выходом наружу и внутреннюю сеть `agent` (`internal: true`) без выхода; агент
`ouroboros` MUST быть только в `agent`: у него код и shell, поэтому наружу — только LLM через шлюз, инструменты через
tools и (профиль owner) Telegram через `egress`. На хосте публикуются только консоль и (профиль owner) UI Ouroboros на
адресе `${OILO_BIND:-127.0.0.1}`; внутренние порты MUST NOT публиковаться.

#### Scenario: Кто в какой сети
- **WHEN** compose запущен со всеми профилями
- **THEN** `gateway`, `tools`, `console`, `ouroboros-ui`, `egress` — в `oilo` и `agent`; `qdrant`, `encoder`,
  `bootstrap`, `gpt2giga` — только в `oilo`; `ouroboros`, `schedules` — только в `agent`
- **AND** сети называются `${COMPOSE_PROJECT_NAME:-oilo}` и `<проект>_agent`, подсети —
  `${OILO_SUBNET:-10.213.8.0/24}` и `${OILO_AGENT_SUBNET:-10.213.9.0/24}`

#### Scenario: Порты по умолчанию
- **WHEN** `OILO_BIND`, `OILO_PORT`, `OUROBOROS_UI_PORT` не заданы
- **THEN** консоль — `127.0.0.1:38620` → `console:8000`, UI Ouroboros — `127.0.0.1:38630` → `ouroboros-ui:8765`
- **AND** tools 8000, gateway 8030, encoder 8020, ouroboros 8765, qdrant 6333, gpt2giga 8090, egress 3128 на хосте не
  видны

#### Scenario: Порты по шаблону .env
- **WHEN** `.env` создан `scripts/init_env.py` из `.env.example` без правок
- **THEN** в нём `OILO_BIND=0.0.0.0`: консоль — `0.0.0.0:38620`, UI Ouroboros (профиль owner) — `0.0.0.0:38630`, то
  есть на всех адресах хоста, и консоль открывается с других машин сети по адресу хоста
- **AND** `OILO_BIND=127.0.0.1` в `.env` возвращает публикацию только для этой машины

#### Scenario: Прокси агента
- **WHEN** агент обращается наружу
- **THEN** у него `HTTPS_PROXY=http://egress:3128`, а `NO_PROXY=gateway,tools,ouroboros,localhost,127.0.0.1`: шлюз и
  инструменты — напрямую по внутренней сети
