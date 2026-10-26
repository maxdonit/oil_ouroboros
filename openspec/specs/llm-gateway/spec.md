# llm-gateway Specification

## Purpose
LLM-шлюз — сервис `gateway` (`src/oil_ouroboros/gateway/app.py`, порт `GATEWAY_PORT`, по умолчанию 8030): через него
идут все вызовы моделей системы — агента Ouroboros и инструментов. Только шлюз знает ключи провайдеров; он держит
жёсткий лимит денег, ведёт журнал каждого вызова и возвращает маркеры кэша промпта для Claude, а промпты и ответы по
существу не меняет.

## Requirements

### Requirement: Токены вызывающих
Шлюз SHALL пропускать запросы на `/api/v1/*` только с токеном вызывающего из `GATEWAY_TOKENS` (`имя:токен,…`; compose
собирает его из `GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`), сравнивая токен в
постоянное время. Токен берётся из `Authorization` (`Bearer <токен>` или голое значение) и наружу не уходит: его
заменяет ключ провайдера маршрута. Токен `console` открывает только журнал.

#### Scenario: Неверный токен
- **WHEN** приходит `POST /api/v1/chat/completions` с `Authorization: Bearer wrong`
- **THEN** шлюз отвечает 401 `LLM-шлюз: нужен токен шлюза`, провайдер не вызывается

#### Scenario: Подмена токена ключом провайдера
- **WHEN** агент вызывает генерацию `openai/gpt-6-luna` со своим токеном при `OPENROUTER_API_KEY=sk-upstream`
- **THEN** провайдер получает `Authorization: Bearer sk-upstream`
- **AND** при пустом ключе маршрута запрос уходит без `Authorization`

#### Scenario: Токен консоли
- **WHEN** консоль шлёт генерацию со своим токеном
- **THEN** ответ 403 `LLM-шлюз: токен консоли — только для журнала`

### Requirement: Пересылаются только генерация и справочные чтения
Шлюз SHALL пересылать провайдеру только `POST chat/completions`, `POST completions` и `GET` путей `models`,
`generation`, `credits`, `key`, `auth/key` (для `GET` — и их подпутей); остальное MUST получать 404
`LLM-шлюз: <метод> /<путь> не пересылается` без обращения к провайдеру. Путь сначала раскодируется из `%xx`, `\`
заменяется на `/`; сегмент `.`, `..` или пустой — отказ, чтобы `models/%2e%2e/keys` не ушёл как `keys`.

#### Scenario: Управление ключами закрыто
- **WHEN** агент шлёт `POST /api/v1/keys`
- **THEN** ответ 404, провайдер не вызван

#### Scenario: Закодированные точки
- **WHEN** приходит `GET /api/v1/models/%2e%2e/keys` или `GET /api/v1/credits/%2E%2E/activity`
- **THEN** ответ 404, а `GET /api/v1/models/openai/gpt-6-luna` пересылается

### Requirement: Маршрут по модели
Шлюз SHALL направлять генерацию модели, имя которой начинается с `GigaChat`, в прокси gpt2giga `GATEWAY_GIGACHAT_URL`
(по умолчанию `http://gpt2giga:8090/v1`) с ключом `GPT2GIGA_API_KEY` напрямую по сети проекта, а остальные запросы —
в `GATEWAY_UPSTREAM` (по умолчанию `https://openrouter.ai/api/v1`) с ключом `OPENROUTER_API_KEY` через
`OUTBOUND_PROXY` и с `CA_BUNDLE`, если они заданы. Ключ OpenRouter MUST NOT уходить в gpt2giga.

#### Scenario: Модель GigaChat
- **WHEN** агент вызывает генерацию `GigaChat-3-Ultra`
- **THEN** запрос уходит на `http://gpt2giga:8090/v1/chat/completions` с `Authorization: Bearer <GPT2GIGA_API_KEY>`

#### Scenario: Модель OpenRouter
- **WHEN** агент вызывает генерацию `openai/gpt-6-luna`
- **THEN** запрос уходит на `GATEWAY_UPSTREAM` с ключом `OPENROUTER_API_KEY`

### Requirement: Запрос к провайдеру
Шлюз SHALL менять пересылаемый запрос только служебно: на внешнем маршруте добавлять в генерацию
`usage: {include: true}` (OpenRouter вернёт цену), а потоку — `stream_options.include_usage: true`; ставить
`Accept-Encoding: identity`; не пересылать `X-Oil-Run`, `X-Oil-Purpose` и hop-by-hop заголовки. Таймаут вызова —
900 с, соединения — 30 с; ответ отдаётся с кодом и `content-type` провайдера, поток — потоком.

#### Scenario: Поля OpenRouter не уходят в gpt2giga
- **WHEN** агент вызывает `GigaChat-3-Ultra`, затем `openai/gpt-6-luna`
- **THEN** в теле первого запроса нет поля `usage`, во втором — `usage: {include: true}`

#### Scenario: Служебные заголовки
- **WHEN** `tools` шлёт генерацию с `X-Oil-Run: abc123`
- **THEN** провайдер не получает заголовка `x-oil-run`, а получает `accept-encoding: identity`

### Requirement: Жёсткий лимит денег
Шлюз SHALL до пересылки отвечать 402, если `потрачено + (вызовов в полёте + 1) × GATEWAY_RESERVE_USD` (резерв по
умолчанию 0.05) больше общего лимита `GATEWAY_BUDGET_USD` (по умолчанию 5) или лимита вызывающего из
`GATEWAY_CALLER_BUDGETS` (`имя:USD,…`, по умолчанию пусто). Потрачено считается за всё время тома: при старте шлюз
суммирует `cost` журнала, общий и по вызывающим. Резерв вызова снимается один раз, у потока — когда он закончился или
оборвался.

#### Scenario: Лимит вызывающего и общий лимит
- **WHEN** `GATEWAY_BUDGET_USD=0.05`, `GATEWAY_RESERVE_USD=0.001`, `GATEWAY_CALLER_BUDGETS=tools:0.015`, каждый вызов
  стоит 0.01; `tools` делает три вызова, затем `ouroboros` — четыре
- **THEN** третий вызов `tools` получает 402 `LLM-шлюз: бюджет «tools» 0.015 USD исчерпан`
- **AND** четвёртый вызов `ouroboros` получает 402 `LLM-шлюз: общий бюджет 0.05 USD исчерпан (потрачено …)`, а
  `/health` показывает `refused: 2`

#### Scenario: Резерв потока снимается
- **WHEN** потоковый вызов завершился
- **THEN** `/health` показывает `in_flight: 0`

### Requirement: Потолок max_tokens
Шлюз SHALL уменьшать `max_tokens` и `max_completion_tokens` генерации, превышающие `GATEWAY_MAX_TOKENS` (по умолчанию
16384), до этого значения; `0` — без потолка. Причина: Ouroboros просит 65 536, а OpenRouter заранее сверяет остаток
кредита с запрошенным числом.

#### Scenario: Запрос Ouroboros
- **WHEN** приходит генерация с `max_tokens: 65536`
- **THEN** провайдер получает `max_tokens: 16384`

### Requirement: Маркеры кэша промпта для Claude
При `GATEWAY_PROMPT_CACHE=true` (по умолчанию) шлюз SHALL ставить `cache_control: {type: ephemeral}` в генерациях
моделей `anthropic/*`, где ещё нет ни одного маркера, — туда же, где их ставит Ouroboros (на маршруте
`openai-compatible::` он их снимает): первый и последний стабильные текстовые блоки системного сообщения (все, кроме
последнего), задача (первое `user`) и хвост переписки; не больше 4 маркеров и только на непустой текст.

#### Scenario: Раунд агента на Claude
- **WHEN** модель `anthropic/claude-haiku-5.5`, сообщения: система из трёх текстовых блоков, задача `user`, пустой
  `assistant`, `tool`, `user` «[Host clock …]»
- **THEN** маркеры стоят на блоках 0 и 1 системы, на задаче и на последнем `user`
- **AND** последний (динамический) блок системы и пустой `assistant` — без маркера

#### Scenario: Не Claude
- **WHEN** модель `openai/gpt-6-luna`
- **THEN** в теле запроса нет `cache_control`

### Requirement: Журнал вызовов
Шлюз SHALL записывать каждую генерацию (POST с JSON-телом на путь, оканчивающийся на `completions`), в том числе с
ошибкой провайдера, строкой таблицы `calls` в `<GATEWAY_DIR>/ledger.sqlite` (`GATEWAY_DIR` по умолчанию
`/app/var/gateway`): кто, модель, статус, токены, цена, время, прогон и назначение из `X-Oil-Run` и `X-Oil-Purpose`,
ход — 12 hex из метки `oilo-turn <id>` в теле, по ней консоль относит вызов к ходу. Прочие пересылки не пишутся.

#### Scenario: Состав строки
- **WHEN** записан любой вызов
- **THEN** строка содержит `id` (16 hex), `ts`, `caller`, `model`, `path`, `stream`, `status`, `tokens_in`,
  `tokens_out`, `tokens_cached`, `cost`, `ms`, `gen_id`, `request_hash` (16 hex sha256 тела), `first_user`, `tools`,
  `finish`, `error`, `run_id`, `purpose`, `turn`
- **AND** `first_user` — первые 300 символов последнего сообщения `user`, `error` — до 300 символов тела ошибки

#### Scenario: Вызов инструмента
- **WHEN** `tools` вызывает генерацию с `X-Oil-Run: abc123` и `X-Oil-Purpose: select_grade`
- **THEN** в строке `run_id=abc123`, `purpose=select_grade`

#### Scenario: Поток агента
- **WHEN** поток вернул текст и кадр SSE с usage `prompt_tokens=1000`, `cached_tokens=800`, `cost=0.01`, `id=gen-1`
- **THEN** в строке `stream=1`, `tokens_in=1000`, `tokens_cached=800`, `cost=0.01`, `gen_id=gen-1`

### Requirement: Цена вызова без usage.cost
Если провайдер не прислал `usage.cost`, шлюз SHALL оценить цену по прайсу `src/oil_ouroboros/llm/pricing.py` без сети
(`fetch=False`) и учесть её в лимите, а не считать вызов бесплатным. Прайс — кэш каталога OpenRouter
`<VAR_DIR>/openrouter_models.json`, если файл есть, поверх запасных цен `FALLBACK` (октябрь 2026), перечитывается раз в
сутки; модель не из прайса стоит 0. Поток, оборванный до кадра usage при статусе < 400, оценивается ~4 символа на
токен.

#### Scenario: Провайдер без цены
- **WHEN** ответ для `openai/gpt-6-luna` содержит `prompt_tokens=100000`, `completion_tokens=1000` и не содержит `cost`
- **THEN** потрачено растёт на 100000 × 0.10e-6 + 1000 × 0.50e-6 = 0.0105 USD

#### Scenario: Запасные цены
- **WHEN** каталога нет и модель из `FALLBACK`
- **THEN** цена за токен вход/выход: `anthropic/claude-haiku-5.5` и `openai/gpt-6-luna` — 0.10e-6/0.50e-6,
  `anthropic/claude-sonnet-5.5` — 2.0e-6/10.0e-6, `google/gemini-3.8-flash` — 0.825e-6/4.125e-6,
  `deepseek/deepseek-v4.1-flash` — 0.30e-6/1.20e-6, `qwen/qwen3.8-flash` — 0.15e-6/0.47e-6

#### Scenario: GigaChat Freemium
- **WHEN** `GigaChat-3-Ultra` вернул 1200 входных токенов без цены
- **THEN** строка журнала: `tokens_in=1200`, `cost=0.0`

### Requirement: Провайдер недоступен
Если соединение с провайдером не удалось (`httpx.HTTPError`), шлюз SHALL ответить 502
`LLM-шлюз: upstream недоступен: <ошибка>`, а генерацию записать в журнал со `status=599`, нулевыми токенами и ценой и
`error` вида `<тип>: <сообщение>`.

#### Scenario: gpt2giga не запущен
- **WHEN** профиль compose gigachat выключен, а агент вызывает `GigaChat-3-Ultra`
- **THEN** агент получает 502, в журнале — строка со `status=599`

### Requirement: Тела запросов и ответов
При `GATEWAY_LOG_BODIES=true` (по умолчанию) шлюз SHALL сохранять для каждой генерации с ответом провайдера файл
`<GATEWAY_DIR>/bodies/<ГГГГММДД>/<id>.json.gz` с `{request, response}`; запрос — в том виде, в каком ушёл провайдеру.
Каждые 50 вызовов шлюз MUST удалять самые старые файлы (по времени изменения), пока их сумма больше
`GATEWAY_BODIES_MAX_MB` (по умолчанию 400).

#### Scenario: Тело потока
- **WHEN** консоль запрашивает `GET /calls/<id>` потокового вызова с текстом «Привет»
- **THEN** `response` — `{stream_text: "Привет", raw_tail: <последние 20 000 символов потока>}`

### Requirement: Эндпоинты состояния и журнала
Шлюз SHALL отдавать состояние `GET /health` без токена, а журнал `GET /ledger` и тела `GET /calls/{id}` — только с
токеном `console`; с другим токеном или без него они MUST отвечать 401 `нужен токен console`.

#### Scenario: Состояние
- **WHEN** запрос `GET /health`
- **THEN** ответ: `status: ok`, `upstream`, `budget_usd`, `spent_usd`, `by_caller`, `caller_budgets`, `calls`,
  `refused`, `in_flight`, `key_set` (задан ли `OPENROUTER_API_KEY`), `gigachat: {url, key_set}`

#### Scenario: Журнал
- **WHEN** консоль запрашивает `GET /ledger?since=<ts>&turn=0123456789ab` (есть ещё фильтр `caller` и `limit`)
- **THEN** ответ: `spent`, `budget`, `by_model` (свод по вызывающему и модели с `since`) и `calls` — строки хода
  `0123456789ab` по убыванию `ts`; `limit` по умолчанию 500, не больше 5000

#### Scenario: Агент не читает журнал
- **WHEN** запрос `GET /ledger` с токеном `ouroboros`
- **THEN** ответ 401

#### Scenario: Тело удалено
- **WHEN** консоль запрашивает `GET /calls/<id>` вызова без сохранённого тела
- **THEN** ответ 404 `тело вызова не сохранено (GATEWAY_LOG_BODIES) или удалено по лимиту`
