# mcp-tools Specification

## Purpose
Контракт сервиса инструментов (контейнер `tools`, `src/oil_ouroboros/tools_app.py`): MCP-сервер с девятью инструментами
аналитика для агента Ouroboros (`src/oil_ouroboros/mcp/server.py`) и служебное HTTP API `/api/*` для консоли. Спека
фиксирует имена, аргументы, формат результатов, ошибки и авторизацию; алгоритмы инструментов описаны в спеках
`research-tool`, `web-search`, `answer-verification`, `price-forecast`, `price-quote`, журнал доказательств — в
`evidence-ledger`.

## Requirements

### Requirement: Набор инструментов MCP
MCP-сервер SHALL называться `oil-ouroboros-tools` (версия — `__version__` пакета, `instructions` — константа
`INSTRUCTIONS`) и отдавать ровно девять инструментов в порядке списка `TOOLS`. Все они помечены
`read_only_hint=true` и `destructive_hint=false`; `search_web` и `price_quote` — ещё `open_world_hint=true`,
`list_reports` — `idempotent_hint=true`. Описание каждого аргумента в схеме MUST быть не длиннее 512 символов: клиент
Ouroboros режет строки схемы.

#### Scenario: Список инструментов стабилен
- **WHEN** клиент запрашивает `tools/list`
- **THEN** приходят `research`, `search_reports`, `search_web`, `forecast_price`, `supply_shock_scenario`,
  `price_history`, `price_quote`, `verify_answer`, `list_reports` — в этом порядке
- **AND** у каждого есть описание и `inputSchema` типа `object`, а `outputSchema` нет

### Requirement: Инструменты в засеве агента
Список `allowed_tools` сервера `oil` в засеве `ouroboros/settings.template.json` SHALL совпадать с именами `TOOLS` по
составу и порядку. Тот же засев MUST задавать адрес `http://tools:8000/mcp`, транспорт `streamable_http`,
`auth_token` = `Bearer ${MCP_TOKEN}` и общий таймаут вызова `MCP_TOOL_TIMEOUT_SEC` = 180 с.

#### Scenario: Инструмент не добавлен в засев
- **WHEN** в `TOOLS` появился инструмент, которого нет в `allowed_tools` (например, `price_quote`)
- **THEN** тест `test_agent_is_allowed_exactly_the_mcp_tools` падает

### Requirement: Аргументы инструментов
Схема аргументов SHALL выводиться из сигнатуры функции инструмента в `mcp/server.py`, а проверку типов, диапазонов и
перечислений MUST выполнять MCP SDK: нарушение возвращается агенту результатом с `isError=true` и именем поля в тексте.
`pack_ids` инструмента `verify_answer` приводится функцией `pack_list` к списку `P<n>` (латинская `P` или кириллическая
`Р`, голое число; без повторов, не больше 30).

#### Scenario: Сигнатуры и значения по умолчанию
- **WHEN** агент читает схемы инструментов
- **THEN** они такие: `research(question, focus="")`; `search_reports(queries, orgs=None, editions=None,
  question="")`; `search_web(queries, pack_id, freshness="month", reason="")` с `freshness` из
  `day|week|month|year|any`; `forecast_price(benchmark="brent", horizon_months=3, method="auto")` с `benchmark` из
  `brent|wti|urals`, `horizon_months` 1–12, `method` из `auto|ensemble|ets|arima|theta|drift|rw`
- **AND** `supply_shock_scenario(delta_mbd, benchmark="brent", horizon_months=3, compliance=1.0, offset=0.0)` с
  `delta_mbd` от −30 до 30, `compliance` и `offset` от 0 до 1; `price_history(benchmark="brent", start="2025-01-01",
  freq="M")` с `freq` из `M|D`; `price_quote(benchmark="brent")` с `benchmark` из
  `brent|wti|henry_hub|ttf`; `verify_answer(answer, pack_ids, question="",
  render=true)`, где обязательны `answer` и `pack_ids`; `list_reports()` без аргументов

#### Scenario: Значение вне диапазона
- **WHEN** агент вызывает `supply_shock_scenario` с `delta_mbd=500` или `forecast_price` с `horizon_months=24`
- **THEN** результат имеет `isError=true`, а текст называет поле `delta_mbd` или `horizon_months`

#### Scenario: Нет обязательного аргумента
- **WHEN** агент вызывает `research` с аргументом `q` вместо `question`
- **THEN** результат имеет `isError=true`, и текст содержит `question`

#### Scenario: Разные записи пакета
- **WHEN** передано `["P12", "[P13]", "пакет Р14", 15, "P12"]`
- **THEN** проверка получает `["P12", "P13", "P14", "P15"]`
- **AND** `pack_list(None)` даёт пустой список

### Requirement: Формат результата для модели
Каждый инструмент SHALL возвращать один текстовый блок без `structuredContent` (`structured_output=False`): клиент
Ouroboros дописывает структуру к тексту, и модель платила бы за результат дважды. Текст графового инструмента — поле
`result` итогового состояния графа и содержит `trace <id>`; структура для консоли хранится в трейсе прогона. Ошибка
графа MUST уходить агенту с `isError=true` и текстом `<ошибка> (trace <id>)`.

#### Scenario: Успешный research
- **WHEN** агент вызывает `research` с вопросом об оценке EIA добычи нефти в США
- **THEN** `isError=false`, `structuredContent` отсутствует, текст начинается с `Пакет P<n> · trace <id>`

#### Scenario: Ошибка порядка вызовов
- **WHEN** агент вызывает `search_web` с `pack_id="P999999"`
- **THEN** результат имеет `isError=true`, текст содержит «сначала research» и `(trace <id>)`

### Requirement: Отклонение пустых аргументов
Пустые обязательные аргументы SHALL отклоняться до запуска графа (без прогона и трейса) ошибкой с `isError=true` и
подсказкой, что исправить.

#### Scenario: Пустые запросы и пустой черновик
- **WHEN** агент вызывает `search_reports` с `queries=[]`
- **THEN** текст ошибки — «queries: нужен хотя бы один запрос (лучше на английском: корпус отчётов англоязычный)»
- **AND** `search_web` с `queries=[]` получает «queries: нужен хотя бы один запрос», а `verify_answer` с черновиком из
  пробелов — «answer: пустой черновик»

### Requirement: Справка list_reports
Инструмент `list_reports` SHALL без графа и трейса вернуть текст о базе отчётов: текущее локальное время, выпуски базы,
последнюю проверку и частоту наблюдателя выпусков (`REPORTS_WATCH_HOURS`, по умолчанию 6 ч), ближайшие выпуски по
графику и правило «Отчёты — приоритетный источник; события после дат публикации — только из веба.». Выпуски последней
проверки со статусом не из `present`, `scheduled`, `downloaded` MUST перечисляться отдельной строкой.

#### Scenario: Строки справки
- **WHEN** агент вызывает `list_reports`
- **THEN** текст начинается с `Сейчас: <ГГГГ-ММ-ДДTЧЧ:ММ:СС±ЧЧ:ММ>.`, затем «База отчётов (RAG):» и строки
  `- <org> <report> «<title>», выпуск <ГГГГ-ММ>, <дата>`, отсортированные по агентству, серии и выпуску
- **AND** есть «Наблюдатель выпусков: последняя проверка сайтов агентств — <время или «не было»>; проверяет каждые
  6 ч.» (при `REPORTS_WATCH_HOURS=0` — «только вручную.») и «Ближайшие выпуски по графику: …» (или «графика нет»)

#### Scenario: Выпуск закрыт от ботов
- **WHEN** в последней проверке OPEC MOMR 2026-10 имеет статус `blocked`, а EIA STEO 2026-10 — `downloaded`
- **THEN** текст содержит «Последняя проверка не получила: OPEC MOMR 2026-10 — закрыто проверкой от ботов.»
- **AND** EIA STEO 2026-10 в этой строке нет

### Requirement: Журнал вызовов MCP
Каждый вызов инструмента SHALL дописывать строку JSON в `var/mcp_audit.jsonl`, в том числе при ошибке. Файл больше
20 МБ (`AUDIT_MAX_BYTES`) MUST переименовываться в `mcp_audit.jsonl.1` перед записью; хранится одна прошлая часть.

#### Scenario: Поля записи
- **WHEN** агент вызывает `verify_answer`
- **THEN** в журнал пишется `ts`, `tool`, `args` (значение длиннее 80 символов заменено на `<N симв.>`), `ok`,
  `error` (до 300 символов), `ms`, `trace`, `cost`, `check` вида `1/2` и `chars` — длина текста результата

#### Scenario: Ошибка тоже записывается
- **WHEN** вызов инструмента завершается исключением
- **THEN** в журнал пишется строка с `ok=false` и текстом ошибки, а исключение уходит агенту как `isError`

### Requirement: Транспорт и авторизация MCP
Сервис SHALL отдавать MCP по пути `/mcp` (streamable HTTP официального SDK) без сессий (`stateless_http=True`), с
JSON-ответами, лимитом тела запроса 1 000 000 байт и без защиты от DNS rebinding. Доступ к `/mcp` MUST требовать
`MCP_TOKEN` в заголовке `Authorization` (`Bearer <токен>` или токен как есть), сравнение — за постоянное время.

#### Scenario: Без токена и с токеном
- **WHEN** клиент шлёт `initialize` на `/mcp` без заголовка `Authorization`
- **THEN** ответ 401 `{"error": "unauthorized"}`
- **AND** с `Authorization: <MCP_TOKEN>` ответ 200 с `serverInfo.name` = `oil-ouroboros-tools`, и `tools/list`
  отвечает без сессии

#### Scenario: MCP выключен
- **WHEN** `MCP_TOKEN` пуст
- **THEN** любой запрос к `/mcp` получает 404 `{"error": "MCP выключен: задайте MCP_TOKEN"}`

### Requirement: Авторизация служебного API
Если задан `TOOLS_API_TOKEN`, каждый запрос к `/api/*`, кроме `/api/health`, SHALL требовать этот токен в
`Authorization` (`Bearer <токен>` или как есть); пустой `TOOLS_API_TOKEN` оставляет `/api/*` открытым. Токен MCP MUST
NOT открывать `/api/*`. В `docker-compose.yml` сервис `tools` без `TOOLS_API_TOKEN` не стартует.

#### Scenario: Консоль без токена
- **WHEN** `TOOLS_API_TOKEN` задан, а запрос `GET /api/runs/abc` пришёл без него
- **THEN** ответ 401 `{"error": "нужен токен консоли (TOOLS_API_TOKEN)"}`
- **AND** `GET /api/health` без токена отвечает

### Requirement: Готовность сервиса
`GET /api/health` SHALL возвращать `status`: `ok` — каталог валиден и в коллекции `QDRANT_COLLECTION` (по умолчанию
`reports`) больше 0 фрагментов; `degraded` — каталог валиден, но индекс пуст или недоступен; `invalid_catalog` — каталог
невалиден, и тогда код ответа MUST быть 503 (Ouroboros с такими инструментами не стартует). При старте сервис в фоне
прогревает кодировщик и прогнозы Brent и WTI на 3 мес. и запускает планировщики цен EIA и наблюдателя выпусков.

#### Scenario: Поля ответа
- **WHEN** консоль запрашивает `/api/health`
- **THEN** в ответе `status`, `version`, `catalog`, `index_chunks`, `warmup` (`pending`, затем `ready` с `seconds`
  или `error`), `web_provider`, `mcp` (задан ли `MCP_TOKEN`), `evidence`, `reranker`, `embedder` и `prices`
  (`as_of`, `last_refresh`, `next_refresh`, `result`, `skipped`)

#### Scenario: Невалидный каталог
- **WHEN** каталог `config/current` не проходит схему
- **THEN** `/api/health` отвечает 503 со `status` = `invalid_catalog` и списком проблем в `catalog.problems`

### Requirement: API для консоли
Сервис SHALL предоставлять консоли эндпоинты трейсов, журнала доказательств, финала хода, заморозки данных eval,
каталога, графов, источников, отчётов и прямого вызова расчётного модуля; приложение MCP SDK MUST монтироваться после
всех маршрутов `/api/*` и отвечать только на `/mcp`.

#### Scenario: Трейсы, доказательства, финал и заморозка
- **WHEN** консоль вызывает служебное API
- **THEN** `GET /api/runs/{run_id}` отдаёт прогон с событиями (нет прогона — 404 «нет прогона»), `GET
  /api/evidence?ids=R1,W2` — доказательства по списку id, `POST /api/finalize` с `{answer, question, traces,
  history_ids}` — финал хода (спека `answer-verification`)
- **AND** `POST /api/eval_lock` с `{active, eval_id}` ставит (`active` истинно) или снимает заморозку данных и
  отвечает `{"locked": <bool>}`; `GET /api/catalog?texts=false`, `/api/reports`, `/api/forecast`,
  `/api/forecast/history`, `/api/forecast/scenario`, `/api/docs`, `/api/openapi.json` отвечают по своим путям

#### Scenario: Пакет диалога только из известных доказательств
- **WHEN** консоль шлёт `POST /api/packs` с `ids` = [известный R#, `R999999`] и вопросом
- **THEN** создаётся пакет с `tool` = `dialog` и единственным известным id, ответ — `{pack_id, ids: 1}`
- **AND** запрос только с `["R999999"]` получает 400 «нет доказательств для пакета»

### Requirement: Графы и источники для консоли
`GET /api/graphs` SHALL возвращать `graphs` — четыре графа из кода (`research`, `search_web`, `calc`, `verify_answer`)
с узлами и рёбрами (`graphs/kit.describe`) — и `mcp_tools` со схемой каждого инструмента; каждый инструмент MCP, кроме
`list_reports`, MUST входить ровно в один граф. `GET /api/sources` отдаёт разделы `reports`, `web`, `prices`, `quote`;
`GET /api/sources/domain?url=…` оценивает адрес правилами фильтра веб-поиска (спека `web-search`).

#### Scenario: Соответствие графов инструментам
- **WHEN** консоль запрашивает `/api/graphs`
- **THEN** граф `research` отвечает за `research` и `search_reports`, `search_web` — за `search_web`, `calc` — за
  `forecast_price`, `supply_shock_scenario`, `price_history`, `price_quote`, `verify_answer` — за `verify_answer`
- **AND** у графа `research` рёбра вида `branch` ведут в `web` и `pack`, у каждого есть подпись

#### Scenario: Проверка доменов
- **WHEN** запрошено `/api/sources/domain?url=https://www.eia.gov/outlooks/steo/`
- **THEN** ответ: `domain` = `eia.gov`, `tier` = 0, `allowed` = true
- **AND** для `url=kp.ru` (без схемы дополняется `https://`) — `allowed` = false и причина со словом `deny`

#### Scenario: Раздел веба в источниках
- **WHEN** консоль запрашивает `/api/sources`
- **THEN** `web` содержит `provider`, `max_results`, `min_tier_for_numbers` = 2, `policy`, `tiers`, `titles`
  (`titles["0"]` = «первичный источник»), `rated_domains` и `used` — домены из журнала доказательств с уровнем
- **AND** `prices.series` содержит ряды `brent`, `wti` и `henry_hub`, у каждого `as_of` или `error`
