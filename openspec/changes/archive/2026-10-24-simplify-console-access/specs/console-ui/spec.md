## MODIFIED Requirements

### Requirement: Вход по UI_USERS и закрытый режим по умолчанию
Консоль SHALL пускать по HTTP Basic с логинами `UI_USERS` (`login:password:role` через запятую; роли `user` и
`admin`), сверяя пароль `hmac.compare_digest`. При пустом `UI_USERS` она MUST отвечать 503 на всё, кроме
`/api/health`, пока не задан `UI_OPEN=true` (по умолчанию `false`) при `OILO_BIND=127.0.0.1`. `OILO_BIND` compose
MUST передавать консоли тем же выражением `${OILO_BIND:-127.0.0.1}`, что в её `ports`.

#### Scenario: Неверный пароль или запись без роли
- **WHEN** `UI_USERS=ann:pw1:user,eve:pw2:guest`, приходит запрос с Basic `ann:wrong` или `eve:pw2`
- **THEN** ответ 401 с заголовком `WWW-Authenticate: Basic realm="oil-ouroboros"`
- **AND** запись `eve` отброшена с предупреждением в журнале «UI_USERS: у eve нет допустимой роли — вход запрещён»

#### Scenario: Логины не заданы
- **WHEN** `UI_USERS` пуст, `UI_OPEN=false`, приходит `GET /api/me` или `GET /`
- **THEN** ответ 503 `вход не настроен: задайте UI_USERS (scripts/init_env.py) или UI_OPEN=true`

#### Scenario: Открытый режим
- **WHEN** `UI_USERS` пуст, `UI_OPEN=true`, `OILO_BIND=127.0.0.1` (или не задан), приходит `GET /api/me` без
  авторизации
- **THEN** ответ `{"user": "local", "role": "admin", "tabs": [...]}`
- **AND** при старте в журнале предупреждение «UI_OPEN=true: консоль без входа, у каждого — права администратора»

#### Scenario: UI_OPEN при заданных логинах
- **WHEN** `UI_USERS` непуст и `UI_OPEN=true`, запрос без авторизации
- **THEN** ответ 401: открытый режим действует только при пустом `UI_USERS`

#### Scenario: Открытый режим не на 127.0.0.1
- **WHEN** `UI_USERS` пуст, `UI_OPEN=true`, а `OILO_BIND` другой (`0.0.0.0`, адрес хоста в сети), приходит `GET /api/me`
  или `GET /`
- **THEN** ответ 503 `открытый режим (UI_OPEN=true) — только при OILO_BIND=127.0.0.1: задайте UI_USERS
  (scripts/init_env.py)`; адрес в ответе не называется
- **AND** `GET /api/health` без авторизации — только `{status, version}`; при старте в журнале предупреждение
  «UI_OPEN=true не действует: OILO_BIND=<адрес>, а не 127.0.0.1»

#### Scenario: Адрес публикации из compose
- **WHEN** читается сервис `console` в `docker-compose.yml`
- **THEN** в его `environment` — `OILO_BIND: ${OILO_BIND:-127.0.0.1}`, то же выражение, что адрес в его `ports`
  `${OILO_BIND:-127.0.0.1}:${OILO_PORT:-38620}:8000`

### Requirement: Роли, вкладки и права по эндпоинтам
Права SHALL проверяться на сервере (`allowed(role, method, path)`), интерфейс лишь прячет недоступное. Запрет MUST
давать 403 с текстом `роль «<роль>»: нет доступа к <METHOD> <path>`. Ролей две: `user` — простой режим (Чат и
Прогноз), `admin` — всё. Всем ролям открыты `/api/health`, `/api/me`, `/`, `/ui/*`, `/api/chat*`,
`/api/conversations*`; `user` — ещё только `GET /api/forecast*`; `admin` — всё. Чужой разговор роли `user` закрыт
(спека `agent-turn`).

#### Scenario: Пользователь открывает журнал ходов
- **WHEN** роль `user` запрашивает `GET /api/turns`
- **THEN** ответ 403 `роль «user»: нет доступа к GET /api/turns`

#### Scenario: Запуск eval без роли admin
- **WHEN** роль `user` отправляет `POST /api/evals/run` или `GET /api/params`
- **THEN** ответ 403, а `GET /api/params` роли `admin` — 200

#### Scenario: Вкладки роли
- **WHEN** `GET /api/me` под ролью `user`
- **THEN** `tabs == ["chat", "forecast"]`
- **AND** у `admin` — `chat, demo, turns, monitor, quality, params, forecast, sources, about`; роли `user`
  `GET /api/demo/0`, `GET /api/about` и `GET /docs/guide/operations.md` дают 403

### Requirement: Вкладки «Ходы» и «Мониторинг»
Консоль SHALL отдавать журнал ходов, прогоны инструментов, журнал LLM-шлюза и метрики только на чтение:
`GET /api/turns?limit=100&source=`, `GET /api/turns/{id}`, `GET /api/tool_runs/{trace}` (tools `/api/runs/{trace}`),
`GET /api/llm?since=0&caller=&limit=300` и `GET /api/llm/{call_id}` (шлюз `/ledger`, `/calls/{id}`),
`GET /api/metrics?hours=168&source=chat`. Ни один из них MUST NOT менять ходы, память агента или каталог.

#### Scenario: Фильтр по источнику хода
- **WHEN** запрашивается `GET /api/turns?source=eval`
- **THEN** в ответе только ходы с источником `eval` (бывают `chat`, `demo`, `eval`, `smoke`, `cli`), новые первыми

#### Scenario: Метрики всех источников за всё время
- **WHEN** администратор запрашивает `GET /api/metrics?hours=0&source=all`
- **THEN** считаются ходы всех источников без ограничения по времени: `{all, by_version, budget}` по `agent_version`
- **AND** `budget` — `spent_usd`, `budget_usd`, `by_caller`, `refused` из `/health` шлюза или `{"error": …}`

#### Scenario: Нет хода
- **WHEN** запрашивается `GET /api/turns/nope`
- **THEN** ответ 404 `нет хода`

### Requirement: Вкладка «Архитектура»: карта системы и графы инструментов
`GET /api/about` SHALL отдавать `{deployment, graphs, turn_rules}` (`graphs` — tools `/api/graphs` или `{"error"}`),
а вкладка MUST строить из них карту системы (`ui/sysmap.js`) и графы инструментов (`ui/graphview.js`) без ручных
схем. Документы вкладки — `GET /docs/{path}`.

#### Scenario: Режимы карты системы
- **WHEN** выбран режим «Связи» или «Развёртывание»
- **THEN** «Связи» рисует `x-links` между сервисами и `x-externals` (связь с неизвестным концом не рисуется);
  «Развёртывание» — колонки по шагу запуска (`depends_on`), полосы по сетям `agent`, обе, `oilo`, тома внизу
- **AND** щелчок открывает паспорт: образ, команда, пользователь, порты, тома, память, CPU, ожидания, проверка
  здоровья, имена секретов; переключатели `owner` и `gigachat` сначала стоят по `profiles_on`

#### Scenario: Наложение реального хода на граф
- **WHEN** администратор выбирает ход в «Наложить ход» (из последних 60 ходов — до 25 с инструментами графа)
- **THEN** пройденный путь и время узлов берутся из `tool_calls[].run` этого хода, остальные узлы приглушены
- **AND** у роли `user` вкладки «Архитектура» нет: `GET /api/about` от неё — 403

#### Scenario: Документы
- **WHEN** обработчик `/docs/{path}` получает `_root/README.md`, `guide/operations.md` или `../pyproject.toml`
- **THEN** первый отдаётся из корня репозитория, второй — из `docs/` как `text/markdown`, третий — 404 `нет документа`

### Requirement: Вкладка «Источники»
Вкладка «Источники» SHALL быть доступна роли `admin` и брать данные у сервиса инструментов через
консоль: `GET /api/sources` и `GET /api/sources/domain?url=` MUST проксироваться в tools как есть. Содержание
(отчёты, наблюдатель, уровни доверия веба, цены EIA) — спека `data-sources`.

#### Scenario: Уровень доверия домена
- **WHEN** администратор вводит адрес на вкладке «Источники»
- **THEN** консоль вызывает `GET /api/sources/domain?url=<адрес>` и показывает ответ tools `/api/sources/domain`
