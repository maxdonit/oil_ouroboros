# console-ui Specification

## Purpose
Консоль (`src/oil_ouroboros/console/app.py`, сервис `console`) — веб-интерфейс системы для пользователей и
эксплуатации: без входа и паролей, простой режим и режим администратора, вкладки наблюдения за агентом («Ходы»,
«Мониторинг», «Параметры», «Источники», «Архитектура»), карта развёртывания и дымовой тест `oilo smoke`. Консоль
только наблюдает: память агента и параметры инструментов она не меняет. Ход агента в чате описывает спека
`agent-turn`, eval — `agent-eval`, офлайн-демо — `demo-replay`; здесь — оболочка UI и режимы.

## Requirements

### Requirement: Проверка здоровья без входа
`GET /api/health` SHALL отвечать без входа (healthcheck Docker и индикатор шапки): `status`, `version`, `console`,
`tools` (tools `/api/health`), `gateway` (шлюз `/health`) и `ouroboros`. `status` — `ok` или `degraded`; недоступный
компонент даёт `{"status": "unavailable", "error": <до 200 символов>}`.

#### Scenario: Шлюз недоступен
- **WHEN** вызов шлюза `/health` падает, tools и Ouroboros отвечают `ok`
- **THEN** ответ — `status: "degraded"` и `gateway: {"status": "unavailable", "error": "<текст ошибки>"}`, остальные
  компоненты — как ответили

#### Scenario: Всё отвечает
- **WHEN** у tools и шлюза `status == "ok"`, у Ouroboros статус `ok`, `healthy`, `True` или его нет, и нет `error`
- **THEN** `status == "ok"`; шапка UI перерисовывает индикатор (агент, инструменты, веб, деньги LLM) каждые 30 с

### Requirement: Оболочка UI без сборки
Интерфейс SHALL быть ES-модулями `ui/*.js` без сборщика: `/ui/` раздаёт каталог `ui/`, `/` — `ui/index.html`. Ответы
на `/` и `/ui/*` MUST иметь `Cache-Control: no-cache`, а importmap в `index.html` MUST сопоставлять каждому модулю
`/ui/<имя>.js` адрес `/ui/<имя>.js?v=<версия>` с одной версией для всех модулей и `app.css`.

#### Scenario: Правка интерфейса
- **WHEN** меняется файл в `ui/`
- **THEN** версия `?v=` увеличивается во всех строках `index.html`, иначе браузер может держать старый модуль

#### Scenario: Маршрутизация вкладок
- **WHEN** открыт адрес `#/quality/<eval_id>` или `#/unknown`
- **THEN** первый открывает вкладку «Качество» с параметром `<eval_id>`, второй — «Чат»
- **AND** модуль вкладки грузится при первом открытии и живёт в фоне: ход агента не прерывается при переключении

#### Scenario: Навигация и тема
- **WHEN** выбран простой режим или режим администратора
- **THEN** навигация показывает вкладки режима: `chat, forecast` или все
- **AND** тема переключается `auto → light → dark` и хранится в `localStorage` под ключом `oil.theme`

#### Scenario: Нет index.html
- **WHEN** файла `ui/index.html` нет
- **THEN** `GET /` отвечает 404 `{"error": "консоль не найдена"}`

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

### Requirement: Вкладка «Параметры»
`GET /api/params` SHALL отдавать всё, что определяет поведение агента: `fingerprint` (свежий, без кэша),
`tools_catalog` (tools `/api/catalog` или `{"error"}`), `journals`, `console_settings`, `disabled_tools`,
`turn_rules`. Значения секретов MUST NOT попадать в ответ.

#### Scenario: Секреты скрыты
- **WHEN** в `console_settings` выводятся поля с суффиксами `_key`, `_credentials`, `_password`, `_token`
- **THEN** вместо значения — `<задан>` или `<пусто>`; логин и пароль в адресе прокси заменены на `//<скрыто>@`

#### Scenario: Файл вне списка памяти
- **WHEN** запрашивается `GET /api/params/memory?file=../.env`
- **THEN** ответ 400 `файл не из списка памяти агента`
- **AND** читаются только `memory/identity.md`, `memory/WORLD.md`, `memory/knowledge/overview.md`,
  `memory/knowledge/patterns.md` из `OUROBOROS_DATA_DIR`

### Requirement: Описание развёртывания из docker-compose.yml
`deployment.describe()` SHALL строить описание только из `docker-compose.yml` (без `deploy/compose/*.yml` и значений
`.env`): сервисы с `x-role` и `x-why`, сети, тома, порядок запуска, `x-externals`, `x-links`, `profiles_on`. Секреты
MUST отдаваться только именами. Результат кэшируется на время процесса.

#### Scenario: Подстановка значений по умолчанию
- **WHEN** порт описан как `${OILO_BIND:-127.0.0.1}:${OILO_PORT:-38620}:8000`
- **THEN** в описании `127.0.0.1:38620:8000`, а `${GATEWAY_TOKEN_OUROBOROS:?задайте в .env}` → `GATEWAY_TOKEN_OUROBOROS`

#### Scenario: Когда работает сервис и порядок запуска
- **WHEN** у сервиса есть `profiles`, `restart: "no"` или ни того ни другого
- **THEN** `when` = `по профилю <имена>`, `разово` или `постоянно`; у `egress` — `по профилю owner`
- **AND** `order` — ступени ядра по `depends_on` (gateway раньше tools, tools раньше ouroboros, тот раньше console),
  сервисов профилей (например, `schedules`) в нём нет

#### Scenario: Только свои секреты
- **WHEN** строится описание сервиса `tools`
- **THEN** в `secrets` — ключи его `environment` с непустым значением из `x-no-secrets` или на `TOKEN(S)`, `KEY`,
  `PASSWORD`, `CREDENTIALS`: есть `MCP_TOKEN` и `TOOLS_API_TOKEN`, нет `OUROBOROS_PASSWORD`; у `encoder` список пуст

#### Scenario: Профили этого развёртывания
- **WHEN** `COMPOSE_PROFILES="owner, gigachat,unknown"`
- **THEN** `profiles_on == ["gigachat", "owner"]`; при пустой переменной — `[]`

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
- **WHEN** в режиме администратора выбран ход в «Наложить ход» (из последних 60 ходов — до 25 с инструментами графа)
- **THEN** пройденный путь и время узлов берутся из `tool_calls[].run` этого хода, остальные узлы приглушены
- **AND** в простом режиме вкладки «Архитектура» нет, а `GET /api/about` открыт без входа

#### Scenario: Документы
- **WHEN** обработчик `/docs/{path}` получает `_root/README.md`, `guide/operations.md` или `../pyproject.toml`
- **THEN** первый отдаётся из корня репозитория, второй — из `docs/` как `text/markdown`, третий — 404 `нет документа`

### Requirement: Вкладка «Источники»
Вкладка «Источники» SHALL показываться в режиме администратора и брать данные у сервиса инструментов через
консоль: `GET /api/sources` и `GET /api/sources/domain?url=` MUST проксироваться в tools как есть. Содержание
(отчёты, наблюдатель, уровни доверия веба, цены EIA) — спека `data-sources`.

#### Scenario: Уровень доверия домена
- **WHEN** администратор вводит адрес на вкладке «Источники»
- **THEN** консоль вызывает `GET /api/sources/domain?url=<адрес>` и показывает ответ tools `/api/sources/domain`

### Requirement: Дымовой тест oilo smoke
`oilo smoke [--question …]` SHALL сделать один ход через Ouroboros (разговор `smoke`, ход сохраняется с источником
`smoke`) и проверить по телам запросов в журнале шлюза, что видела модель. Код выхода MUST быть 0 только при всех
пройденных проверках. Вопрос по умолчанию: «Кратко: с какими вопросами ты можешь помочь? Ответь в двух предложениях
без инструментов.»

#### Scenario: Состав проверок
- **WHEN** ход завершён
- **THEN** проверяются `ouroboros_health`, `answered`, `gateway_sees_turn` (есть вызовы LLM с меткой хода),
  `cost_known` (cost > 0, у моделей `GigaChat*` — токены > 0) и `language` (язык ответа = язык вопроса)
- **AND** по телу первого вызова с инструментами: `tools_mcp` — ровно research, search_reports, search_web,
  forecast_price, supply_shock_scenario, price_history, price_quote, verify_answer, list_reports; `tools_builtin`;
  `playbook_in_context` («Регламент: старший аналитик» в сообщениях); `identity_in_context`

#### Scenario: Лишний встроенный инструмент
- **WHEN** в запросе к модели есть встроенный инструмент вне finish_task, knowledge_read, knowledge_list,
  enable_tools, list_available_tools
- **THEN** `tools_builtin` печатается как `НЕТ`, и команда завершается с кодом 1

### Requirement: Простой режим и режим администратора
Консоль SHALL открываться без входа и паролей: сервер не проверяет логинов и ролей, все эндпоинты открыты,
эндпоинта `/api/me` нет. Интерфейс MUST открываться в простом режиме (вкладки «Чат» и «Прогноз») и кнопкой в шапке
переключаться в режим администратора со всеми вкладками и обратно; выбор хранится в `localStorage` под ключом
`oil.mode`.

#### Scenario: Первое открытие
- **WHEN** консоль впервые открыта в браузере
- **THEN** видны вкладки «Чат» и «Прогноз», в шапке — кнопка «Режим администратора»
- **AND** ни пароля, ни ответов 401, 403 или 503 нет

#### Scenario: Переключение режима
- **WHEN** нажата кнопка «Режим администратора»
- **THEN** навигация — `chat, demo, turns, monitor, quality, params, forecast, sources, about`, кнопка называется
  «Простой режим», после перезагрузки страницы режим тот же
- **AND** переход в простой режим с вкладки администратора (например, «Ходы») открывает «Чат»

#### Scenario: Ссылка на вкладку администратора
- **WHEN** в простом режиме открыт адрес `#/turns/<id>` или `#/quality/<eval_id>`
- **THEN** включается режим администратора и открывается эта вкладка

#### Scenario: Эндпоинты без входа
- **WHEN** без авторизации приходят `GET /api/turns`, `GET /api/params`, `POST /api/evals/run`
- **THEN** они обрабатываются как обычно, без 401 и 403; `GET /api/me` — 404
