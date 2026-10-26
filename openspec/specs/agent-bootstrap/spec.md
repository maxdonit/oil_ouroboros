# agent-bootstrap Specification

## Purpose
Засев агента Ouroboros до его старта: настройки `settings.json` из шаблона `ouroboros/settings.template.json` и
`.env`, память (роль, среда, регламент аналитика) из `ouroboros/memory/`, домашний и рабочий каталоги. Засев делает
интеграцию с проектом (шлюз LLM, MCP-инструменты, сетевой пароль, бюджет) конфигурацией как кодом и при этом не
затирает выбор владельца в UI Ouroboros и регламент, улучшенный самим агентом. Код — `src/oil_ouroboros/bootstrap.py`,
сервис `bootstrap` в `docker-compose.yml`.

## Requirements

### Requirement: Разовый сервис перед стартом агента
Засев SHALL выполняться разовым сервисом `bootstrap` (`python -m oil_ouroboros.bootstrap`, образ `oilo/app`,
`restart: "no"`, пользователь `0:0`) при каждом `docker compose up`, а сервис `ouroboros` MUST стартовать только после
его успешного завершения (`service_completed_successfully`). Аргументы: `--data` (по умолчанию `OUROBOROS_DATA`,
иначе `/ouroboros-data`) и `--reset`. Код выхода — 0, при ошибке засева — 2.

#### Scenario: Тома и переменные сервиса
- **WHEN** compose запускает `bootstrap`
- **THEN** тома `ouroboros_data`, `ouroboros_home`, `ouroboros_work` смонтированы в `/ouroboros-data`,
  `/ouroboros-home`, `/ouroboros-work`, и те же пути заданы в `OUROBOROS_DATA`, `OUROBOROS_HOME`, `OUROBOROS_WORK`

#### Scenario: Отчёт засева
- **WHEN** засев прошёл успешно
- **THEN** он печатает «settings.json: ключи засева изменены: …», «settings.json: правка владельца сохранена: …»
  («—», если пусто) и по строке «память: <файл> <действие>» на каждый файл памяти, код выхода 0

#### Scenario: Засев упал
- **WHEN** засев завершился с кодом 2
- **THEN** контейнер `ouroboros` не стартует

### Requirement: Обязательные переменные и значения по умолчанию
Засев SHALL подставлять в шаблон значения `${VAR}` из окружения, `${NOW}` — время UTC `%Y-%m-%dT%H:%M:%SZ`. Если
переменной нет в окружении, действуют умолчания: `OUROBOROS_MODEL` = `GigaChat-3-Ultra`, `OUROBOROS_MODEL_LIGHT` =
`GigaChat-2`, `OUROBOROS_TOTAL_BUDGET` = 4, `OUROBOROS_PER_TASK_COST_USD` = 0.3. Незаданная или пустая переменная
шаблона MUST останавливать засев до записи `settings.json`. Служебные ключи `_comment` и `_owner_keys` в настройки не
попадают.

#### Scenario: Нет токенов
- **WHEN** в окружении только `OUROBOROS_MODEL=x/y`
- **THEN** засев печатает в stderr «bootstrap: не заданы переменные окружения GATEWAY_TOKEN_OUROBOROS, MCP_TOKEN,
  OUROBOROS_PASSWORD (.env)» и завершается с кодом 2

#### Scenario: Модель и бюджет из окружения
- **WHEN** `OUROBOROS_MODEL=openai/gpt-6-luna`, бюджеты не заданы
- **THEN** в `settings.json` `OUROBOROS_MODEL` = `openai-compatible::openai/gpt-6-luna`, `TOTAL_BUDGET` = 4 (число),
  `OUROBOROS_PER_TASK_COST_USD` = 0.3 (число)

### Requirement: Интеграция — конфигурация как код
Ключи шаблона вне `_owner_keys` (интеграция: шлюз LLM и модели, MCP, сетевой пароль, бюджет) засев SHALL записывать
при каждом запуске, откатывая ручную правку. `OUROBOROS_ONBOARDING_COMPLETED_AT` MUST сохранять значение первого
засева, а ключи, которых нет в шаблоне (их дописывает сам Ouroboros), MUST сохраняться. Все вызовы LLM агента идут
через шлюз (спека `llm-gateway`), инструменты аналитика — через MCP-сервер `oil` сервиса `tools`.

#### Scenario: Значения интеграции
- **WHEN** засев записал `settings.json`
- **THEN** `OPENAI_COMPATIBLE_BASE_URL` = `http://gateway:8030/api/v1`, `OPENAI_COMPATIBLE_API_KEY` =
  `GATEWAY_TOKEN_OUROBOROS`, `OPENROUTER_API_KEY` = пусто, `OUROBOROS_MODEL_LIGHT` = `openai-compatible::<модель>`,
  `TOTAL_BUDGET` = `OUROBOROS_TOTAL_BUDGET`
- **AND** `MCP_ENABLED` = true, `MCP_TOOL_TIMEOUT_SEC` = 180, `OUROBOROS_NETWORK_PASSWORD` = `OUROBOROS_PASSWORD`,
  `OUROBOROS_SERVER_HOST` = `0.0.0.0`

#### Scenario: Ручная правка интеграции откатывается
- **WHEN** в `settings.json` вручную записано `OPENAI_COMPATIBLE_BASE_URL = "https://elsewhere"` и добавлен ключ
  `OUROBOROS_SOME_RUNTIME_KEY`
- **THEN** после повторного засева `OPENAI_COMPATIBLE_BASE_URL` снова `http://gateway:8030/api/v1` и входит в
  «ключи засева изменены»
- **AND** `OUROBOROS_SOME_RUNTIME_KEY` и `OUROBOROS_ONBOARDING_COMPLETED_AT` не изменились

### Requirement: Инструменты MCP агента
Засев SHALL подключать агенту ровно один MCP-сервер — `MCP_SERVERS[0]`: `id` `oil`, `transport` `streamable_http`,
`url` `http://tools:8000/mcp`, `auth_token` `Bearer <MCP_TOKEN>`. Его `allowed_tools` MUST совпадать с именами
инструментов MCP-сервера (`mcp/server.py` `TOOLS`) в том же порядке: инструмент, не добавленный в `allowed_tools`,
агент молча не увидел бы.

#### Scenario: Состав allowed_tools
- **WHEN** засев записал `settings.json`
- **THEN** `allowed_tools` — девять инструментов: `research`, `search_reports`, `search_web`, `forecast_price`,
  `supply_shock_scenario`, `price_history`, `price_quote`, `verify_answer`, `list_reports`, а `auth_token` =
  `Bearer <MCP_TOKEN>`

#### Scenario: Новый инструмент MCP
- **WHEN** в `mcp/server.py` добавлен инструмент, а `allowed_tools` шаблона не обновлён
- **THEN** тест `test_agent_is_allowed_exactly_the_mcp_tools` падает

### Requirement: Ключи владельца
Ключи `_owner_keys` шаблона (27: доп. модели, режимы, ревью, эволюция, «сознание», субагенты, усилие, раунды,
воркеры, веб-поиск, хранение, язык UI) засев SHALL ставить, а правку владельца в UI
Ouroboros MUST NOT откатывать. Правка — текущее значение отличается от значения прошлого засева (`settings_seeded`
в `state/oilo_seed.json`; записи нет — текущее считается засеянным). Нетронутый ключ получает новое значение шаблона.
`--reset` пишет `settings.json` заново.

#### Scenario: Значения засева
- **WHEN** первый засев на пустом каталоге данных
- **THEN** `OUROBOROS_RUNTIME_MODE` = `advanced`, `OUROBOROS_CONTEXT_MODE` = `low`, `OUROBOROS_CONTEXT_MODE_AUTO_LOW`
  = `"false"`, `OUROBOROS_SAFETY_MODE` = `off`, `OUROBOROS_TASK_REVIEW_MODE` = `off`, `OUROBOROS_POST_TASK_EVOLUTION`
  = `"false"`, `OUROBOROS_CONSCIOUSNESS_DAILY_USD` = `"0"`, `OUROBOROS_EFFORT_TASK` = `low`, `OUROBOROS_EFFORT_MAX` =
  `medium`, `OUROBOROS_MAX_ROUNDS` = 10, `OUROBOROS_MAX_WORKERS` = 3, `OUROBOROS_MAIN_WEB_SEARCH` = `off`

#### Scenario: Владелец включил проверку безопасности
- **WHEN** владелец поменял в UI `OUROBOROS_SAFETY_MODE` с `off` на `full`
- **THEN** повторный засев оставляет `full` и печатает его в «правка владельца сохранена»

#### Scenario: Нетронутый ключ получает новую версию
- **WHEN** прошлый засев записал `OUROBOROS_RUNTIME_MODE = "light"`, владелец его не менял, а шаблон теперь даёт
  `advanced`; при этом владелец выбрал `OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT = 2`
- **THEN** после засева `OUROBOROS_RUNTIME_MODE = "advanced"`, а `OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT = 2`

### Requirement: Секреты засева
Ключи `OPENAI_COMPATIBLE_API_KEY`, `OUROBOROS_NETWORK_PASSWORD`, `MCP_SERVERS`, `TELEGRAM_BOT_TOKEN`
(`bootstrap.SECRETS`) засев SHALL считать секретами: их значения MUST NOT попадать в файл состояния и в вывод —
изменённый секрет печатается именем с пометкой «(секрет)». `settings.json` пишется с правами `0600`. Сервису
`bootstrap` из `.env` доходят только его секреты; засев токена Telegram — спека `owner-channel`.

#### Scenario: Состояние без секретов
- **WHEN** засев прошёл с `TELEGRAM_BOT_TOKEN=123:abc`
- **THEN** в `state/oilo_seed.json` → `settings_public` нет `TELEGRAM_BOT_TOKEN`, `MCP_SERVERS`,
  `OPENAI_COMPATIBLE_API_KEY`, `OUROBOROS_NETWORK_PASSWORD`

#### Scenario: Секреты сервиса
- **WHEN** compose передаёт окружение сервису `bootstrap`
- **THEN** у него заданы `GATEWAY_TOKEN_OUROBOROS`, `MCP_TOKEN`, `OUROBOROS_PASSWORD`, `TELEGRAM_BOT_TOKEN`, а
  `OPENROUTER_API_KEY`, `TOOLS_API_TOKEN` и другие чужие секреты обнулены (`x-no-secrets`)

### Requirement: Файл состояния засева
Засев SHALL вести `<data>/state/oilo_seed.json` с правами `0644` (его читает консоль, которой `settings.json`
недоступен): `seeded` — sha256 засеянных файлов памяти, `settings_seeded` — значения ключей владельца после засева,
`settings_public` — действующие значения ключей шаблона без секретов, `settings_sha` — sha256 их JSON с сортировкой
ключей, `updated` — время засева. Ключ `schedules_seeded` того же файла ведёт засев расписаний (спека `owner-channel`).

#### Scenario: Правка владельца видна в состоянии
- **WHEN** владелец сменил `OUROBOROS_SAFETY_MODE` на `full`, и прошёл повторный засев
- **THEN** `settings_public.OUROBOROS_SAFETY_MODE` = `full`, `settings_seeded.OUROBOROS_SAFETY_MODE` = `off`, а
  `settings_sha` изменился

### Requirement: Засев памяти агента
Засев SHALL сеять три файла: `ouroboros/memory/identity.md`, `ouroboros/memory/WORLD.md` и
`ouroboros/memory/knowledge/overview.md` — в `<data>/memory/` по тем же относительным путям. Файл, который изменил
агент (`update_identity`, `knowledge_write`) после прошлого засева, MUST NOT перезаписываться без `--reset`: улучшенный
агентом регламент сохраняется. Действия: «создан», «без изменений», «обновлён до новой версии засева», «оставлен:
изменён агентом».

#### Scenario: Первый засев
- **WHEN** засев на пустом каталоге данных
- **THEN** все три файла — «создан», sha256 каждого записан в `seeded`

#### Scenario: Новая версия засева
- **WHEN** файл в данных совпадает с прошлым засевом (sha в `seeded`), а в репозитории — новая версия
- **THEN** файл заменяется, действие — «обновлён до новой версии засева»

#### Scenario: Регламент, улучшенный агентом
- **WHEN** агент дописал в `memory/knowledge/overview.md` новое правило
- **THEN** повторный засев оставляет файл, действие — «оставлен: изменён агентом»
- **AND** `bootstrap --reset` возвращает версию из репозитория

### Requirement: Содержание засеянной памяти
Засеянная память SHALL задавать роль и регламент аналитика: `identity.md` — Ouroboros как старший аналитик
нефтегазового рынка (цифры только из `mcp_oil__*`, проверка до ответа); `WORLD.md` — среда (сеть без интернета, шлюз,
9 инструментов MCP); `knowledge/overview.md` («Shared understanding») — экспертиза, «Вне компетенции», приоритет
источников, протокол инструментов, формат ответа. Число проверок регламент MUST NOT задавать: его сообщает
`verify_answer` (каталог `verify.max_checks`).

#### Scenario: Отказ вне компетенции
- **WHEN** агент читает раздел «Вне компетенции» регламента
- **THEN** отказ начинается со слов «Это вне моей компетенции» (на английском — «This is outside my area of
  expertise»), без инструментов; эти слова узнаёт `agent.REFUSAL` (спека `agent-turn`)

#### Scenario: Протокол завершения
- **WHEN** тест читает `ouroboros/memory/knowledge/overview.md`
- **THEN** в нём есть «Регламент», `acceptance_subject` (не использовать), «отдельным шагом» (`finish_task` после
  результата `verify_answer`) и `render=false` (исключение хода консоли)
- **AND** нет «всегда первым» и «не больше двух»

#### Scenario: Текущая цена
- **WHEN** агент читает протокол инструментов регламента
- **THEN** вопрос о текущей цене Brent, WTI, газа Henry Hub или TTF («сейчас», «курс», «котировка») ведёт сразу в
  `price_quote(benchmark)`, без `research`
- **AND** регламент требует назвать в ответе, что это фьючерс ближайшего месяца, время котировки, а спот EIA — с его
  датой; при `status` `stale` или `unavailable` — прямо сказать, что свежей котировки нет
- **AND** котировки Urals у агента нет: Urals — оценка (Brent − дисконт)
- **AND** в ответе — единица сорта (`unit`), дата и время сделки из `time_ru`; название контракта — месяц исполнения, а
  не дата цены; названия полей результата в ответ не попадают

### Requirement: Каталоги и владелец файлов агента
Засев SHALL создавать каталоги `OUROBOROS_HOME` и `OUROBOROS_WORK` (если заданы) и `<data>/state`, класть в домашний
каталог `.gitconfig`, если его нет, и, запущенный от root, MUST отдавать каталог данных, домашний и рабочий
каталоги пользователю контейнера `ouroboros` — `1000:1000`.

#### Scenario: Первый запуск от root
- **WHEN** сервис `bootstrap` (пользователь `0:0`) засевает пустые тома
- **THEN** файлы в `/ouroboros-data`, `/ouroboros-home`, `/ouroboros-work` принадлежат `1000:1000`
- **AND** `/ouroboros-home/.gitconfig` содержит `name = Ouroboros (oil analyst)`,
  `email = ouroboros@oil-ouroboros.local` и `directory = *` в `[safe]`
