# tool-catalog Specification

## Purpose
Каталог поведения инструментов `config/<слот>/` выносит из кода всё, что меняет качество работы графов LangGraph
сервиса tools: модель и параметры субагентов, поиск, веб-политику, проверку, прогноз, источники и промпты. Механизм —
`src/oil_ouroboros/catalog.py`: строгая схема, слои переопределений, отпечаток в каждом трейсе и прогоне eval; сюда же
входит версия агента (`src/oil_ouroboros/console/fingerprint.py`), частью которой является каталог. Значения
конкретных ключей описывают спеки инструментов.

## Requirements

### Requirement: Состав слота
Каталог SHALL загружаться из `<CONFIG_DIR>/<CATALOG_SLOT>/` (`CONFIG_DIR` по умолчанию `<корень проекта>/config`, в
образе `/app/config`; `CATALOG_SLOT` — `current` по умолчанию или экспериментальный `exp-*`). Слот MUST содержать
ровно десять YAML-разделов — `meta`, `llm`, `retrieval`, `indexing`, `web`, `research`, `verify`, `forecast`,
`reports`, `source_tiers` — и промпты `prompts/research_plan.md`, `prompts/select_grade.md`. Каталог входит в образ:
правка действует после пересборки.

#### Scenario: Нет слота
- **WHEN** `CATALOG_SLOT=exp-x`, а каталога этого слота (`config/<слот>/`) нет
- **THEN** загрузка падает с проблемой `нет слота каталога <путь>`

#### Scenario: Лишние и недостающие файлы
- **WHEN** в слоте есть `stray.yaml` и `prompts/extra.md`, но нет `verify.yaml`
- **THEN** проблемы: `stray.yaml: файл без схемы (переименуйте или добавьте раздел в catalog.py)`,
  `prompts/extra.md: промпт не используется кодом (известные: research_plan, select_grade)`, `verify.yaml: нет файла`

#### Scenario: Паспорт каталога
- **WHEN** читается `config/current/meta.yaml`
- **THEN** в нём `catalog_version` (строка вида `ГГГГ.ММ.ДД-N`, повышает человек при содержательной правке),
  `agent: oil-analyst-ouroboros` и журнал `changelog`

### Requirement: Строгая схема и все проблемы сразу
Каждый раздел SHALL проверяться схемой pydantic, запрещающей лишние ключи: неизвестный ключ, неверный тип, значение
вне диапазона или нарушенная согласованность — проблема `<раздел>.yaml: <путь.к.полю>: <сообщение>`. Загрузка MUST
собрать все проблемы (YAML, кодировка, frontmatter, слои, схема) и поднять одну `CatalogError` со списком
(`каталог невалиден:` и по строке на проблему), а не остановиться на первой.

#### Scenario: Четыре ошибки в одном отчёте
- **WHEN** в `retrieval.yaml` повторён `top_k`, в `web.yaml` лишний ключ `surprise`, в `select_grade.md` `{top_k}`
  заменён на `{k}`, в слоте лишний `stray.yaml`
- **THEN** одна `CatalogError` содержит `повтор ключа 'top_k'`, `surprise`, `select_grade.md: переменные` и
  `stray.yaml: файл без схемы`

#### Scenario: Битые файлы — проблемы, а не падение
- **WHEN** `research.yaml` в cp1251, а `retrieval.yaml` — YAML-список
- **THEN** проблемы `research.yaml: файл не в кодировке UTF-8` и `retrieval.yaml: ожидается словарь ключей, а не list`

#### Scenario: Согласованность значений
- **WHEN** у пресета `forecast.scenario.presets.cci` `eps_s=0` и `eps_d=0`, а `urals_discount.low=40`
- **THEN** проблемы содержат `оба равны 0` и `low ≤ base ≤ high`

### Requirement: Строгий YAML
Разбор YAML SHALL следовать YAML 1.2 core для логических значений: логические — только `true` и `false`, а `yes`,
`no`, `on`, `off` — строки; даты остаются строками; повтор ключа в словаре — ошибка `повтор ключа '<ключ>'`; ключ
слияния `<<` запрещён.

#### Scenario: yes вместо true
- **WHEN** в `web.yaml` `needs_freshness: yes`
- **THEN** загрузка падает: поле строго логическое, а `yes` — строка

### Requirement: Промпты с frontmatter
Промпт SHALL быть `.md` с frontmatter между строками `---` (`id`, `version`, `purpose`, `format`, `variables`): `id`
равен имени файла, `format` — `python` или `plain`; у `python` множество переменных `{…}` в тексте MUST совпадать с
`variables`. `Catalog.prompt(имя, **значения)` подставляет переменные для `python` и отдаёт текст как есть для `plain`.

#### Scenario: Переменные не совпали
- **WHEN** в тексте `select_grade.md` `{k}` вместо объявленного `top_k`
- **THEN** проблема `prompts/select_grade.md: переменные в тексте [...] ≠ объявленным [...]`

#### Scenario: Неверный frontmatter
- **WHEN** `format: jinja`, или frontmatter нет, или он строка, а не словарь
- **THEN** проблема `select_grade.md: format…`, `нет frontmatter (--- id, version, variables ---)` или
  `frontmatter — не словарь ключей (id, version, variables)`

### Requirement: Слои переопределений
Итоговые значения SHALL складываться из слоёв, поздний переопределяет ранний: `slot` (файлы слота) → `env` (непустой
`LLM_MODEL_SUBAGENT` заменяет `llm.subagent.model`) → `set` (`CATALOG_SET="раздел.ключ=значение;…"`, значения —
YAML-скаляры). Слой переопределённого ключа записывается в `layers`; значения после слоёв проверяются той же схемой.
`CATALOG_SET` MUST менять только разделы `llm`, `retrieval`, `web`, `research`, `forecast`, `reports`; `null` в слое
запрещён.

#### Scenario: Замер без правки файлов
- **WHEN** `CATALOG_SET=retrieval.top_k=8`
- **THEN** `retrieval.top_k=8`, `layers["retrieval.top_k"]="set"`, отпечаток `fp` отличается от каталога без слоя

#### Scenario: Модель из окружения
- **WHEN** `LLM_MODEL_SUBAGENT=test/override-model`
- **THEN** `llm.subagent.model=test/override-model`, `layers["llm.subagent.model"]="env"`

#### Scenario: Запрещённый раздел
- **WHEN** `CATALOG_SET=source_tiers.thresholds.deny_below=0;foo.bar=1`
- **THEN** проблема `CATALOG_SET: разделы ['source_tiers', 'foo'] нельзя менять слоем (можно: llm, retrieval, web, research, forecast, reports)`

#### Scenario: Противоречивый CATALOG_SET
- **WHEN** `CATALOG_SET=web.policy=1;web.policy.x=2`
- **THEN** проблема начинается с `CATALOG_SET: web.policy.x`

### Requirement: Отпечаток, lock и ref
Загрузчик SHALL вычислять `fp` — первые 12 hex sha256 от JSON итоговых значений всех разделов (после слоёв, ключи
отсортированы) и sha промптов; `lock` — для каждого файла `<версия>#<12 hex sha текста>`; `ref` —
`<слот>@<catalog_version>#<fp>`. Тексты перед хешированием нормализуются: без BOM, переводы строк LF, без хвостовых
пробелов. `ref` пишется в каждый трейс инструментов и прогон eval: разница двух прогонов объясняется разницей `lock`.

#### Scenario: Текущий каталог
- **WHEN** загружается слот `current` без слоёв
- **THEN** `fp` — 12 символов, `ref` — `current@<catalog_version>#<fp>`, в `lock` есть `retrieval.yaml` и
  `prompts/research_plan.md`

#### Scenario: Хвостовые пробелы
- **WHEN** в тексте `research_plan.md` к каждой строке добавлены пробелы в конце
- **THEN** `fp` не меняется

### Requirement: Загрузка один раз и готовность сервиса инструментов
`catalog.get()` SHALL загружать каталог один раз на процесс; успешная загрузка не перечитывается, неуспешная
повторяется при следующем обращении. `catalog.health()` отдаёт `{ok: true, ref}` или `{ok: false, problems}`. При
невалидном каталоге `GET /api/health` сервиса tools MUST отвечать 503 со `status: invalid_catalog` и проблемами в поле
`catalog`; Ouroboros ждёт здоровый tools (спека deployment) и с таким каталогом не стартует.

#### Scenario: Ошибка в config/
- **WHEN** в `config/current/retrieval.yaml` `top_k: 0`
- **THEN** `GET /api/health` → 503, `status=invalid_catalog`, `catalog.problems` содержит `retrieval.yaml: top_k: …`

#### Scenario: Валидный каталог
- **WHEN** каталог валиден
- **THEN** `GET /api/health` → 200 со `status` `ok` (или `degraded`, пока индекс отчётов пуст) и `catalog.ref`

### Requirement: Описание параметров
`catalog.describe(texts)` и `GET /api/catalog?texts=` сервиса tools SHALL отдавать итоговые значения каталога со
слоем и описанием каждого ключа; описание — комментарий `# …` в той же строке YAML. Значения показываются по разделам
`llm`, `retrieval`, `indexing`, `web`, `research`, `verify`, `forecast`, ключи `version` пропускаются.

#### Scenario: Состав описания
- **WHEN** вызывается `catalog.describe(texts=True)`
- **THEN** ответ: `ref`, `slot`, `catalog_version`, `fp`, `lock`, `values` (`key`, `value`, `layer`, `description`),
  `prompts` (`version`, `sha`, `purpose`, `variables`, `text`), `report_series`, `changelog`, `source_tiers_version`
- **AND** без `texts` промпты отдаются без `text`

#### Scenario: Ключ с описанием
- **WHEN** в `llm.yaml` строка `timeout_s: 45  # таймаут одного вызова, с (обычно 3–15 с)`
- **THEN** строка `values`: `key=llm.subagent.timeout_s`, `value=45.0`, `layer=slot`,
  `description=таймаут одного вызова, с (обычно 3–15 с)`

### Requirement: Команды oilo catalog
`oilo catalog [check|show|diff] [слот]` (по умолчанию `check`) SHALL проверять и показывать текущий каталог; при
невалидном текущем каталоге любая команда MUST печатать `каталог невалиден:` со списком проблем и завершаться с кодом 2.

#### Scenario: Проверка
- **WHEN** `oilo catalog check` на валидном `current`
- **THEN** печатается `каталог валиден: current@<catalog_version>#<fp> (файлов: 12)`, код 0

#### Scenario: Итоговые значения
- **WHEN** `oilo catalog show`
- **THEN** печатается `ref`, по строке `<ключ> <значение> [<слой>] <описание>` и строки
  `prompts/<имя> v<version> #<sha>  <purpose>`

#### Scenario: Сравнение слотов
- **WHEN** `oilo catalog diff exp-topk8`
- **THEN** печатается `<ref текущего>  vs  <ref exp-topk8>` и файлы с разным `lock`: `<файл> <было или —> → <стало или —>`

### Requirement: Версия агента
Консоль SHALL ставить на каждый ход (`turns.sqlite`: `fingerprint`, `agent_version`) и каждый прогон eval отпечаток
`fingerprint.current()`, где `agent_version` — первые 12 hex sha256 от JSON ядра: релиз Ouroboros `7.7.0@e41596c`,
sha файлов памяти, ключи поведения Ouroboros, шаблон хода, `ref` каталога, версия, схемы MCP и корпус сервиса tools,
`RERANKER`. Метрики мониторинга и eval группируются по `agent_version`, поэтому бюджеты в ядро не входят.

#### Scenario: Состав ядра
- **WHEN** считается `agent_version`
- **THEN** в ядро входят `files` (sha12 `memory/identity.md`, `memory/WORLD.md`, `memory/knowledge/overview.md`,
  `memory/knowledge/patterns.md`), `settings` (16 ключей: `OUROBOROS_MODEL`, `…_LIGHT`, `…_HEAVY`, `…_FALLBACKS`,
  `…_RUNTIME_MODE`, `…_CONTEXT_MODE`, `…_SAFETY_MODE`, `…_TASK_REVIEW_MODE`, `…_REVIEW_ENFORCEMENT`,
  `…_POST_TASK_EVOLUTION`, `…_CONSCIOUSNESS_AUTONOMY`, `…_MAIN_WEB_SEARCH`, `…_EFFORT_MIN`, `…_EFFORT_TASK`,
  `…_EFFORT_MAX`, `…_MAX_ROUNDS`), `task_template` (sha12 правил хода, шаблона задачи с блоком диалога и списка
  отключённых инструментов), `catalog`, `tools_version`, `mcp_tools`, `corpus` (sha12 списка `doc_id#sha256` выпусков),
  `reranker`
- **AND** рядом, но вне ядра пишутся `budgets` (`TOTAL_BUDGET`, `OUROBOROS_PER_TASK_COST_USD`), `ouroboros_reported`,
  `modified_by_agent`, `index_chunks`

#### Scenario: Что меняет версию
- **WHEN** меняется LLM-профиль (`OUROBOROS_MODEL`), регламент `knowledge/overview.md` или `CATALOG_SET`
- **THEN** `agent_version` новый; при смене только `OUROBOROS_PER_TASK_COST_USD` — прежний

### Requirement: Источники версии агента
Отпечаток SHALL брать настройки из живого Ouroboros (`GET <OUROBOROS_URL>/api/settings` с `X-Ouroboros-Password`,
только ключи версии), а если он недоступен — из записи засева `state/oilo_seed.json` → `settings_public`
(`settings.json` с секретами консоли недоступен); файлы памяти — из тома данных Ouroboros только на чтение
(`OUROBOROS_DATA_DIR`); часть tools — из его `/api/health`, `/api/graphs`, `/api/reports`. Отпечаток кэшируется на 30 с.

#### Scenario: Правка владельца в UI Ouroboros
- **WHEN** владелец меняет `OUROBOROS_EFFORT_TASK` в UI Ouroboros
- **THEN** ход после истечения кэша (30 с) получает новый `agent_version` без нового засева

#### Scenario: Агент сам изменил память
- **WHEN** sha засеянного файла памяти (`identity.md`, `WORLD.md`, `knowledge/overview.md`) не совпадает с записанным
  при последнем засеве
- **THEN** файл попадает в `modified_by_agent`

#### Scenario: Свежий отпечаток
- **WHEN** запускается eval или открывается вкладка «Параметры» (`/api/params`)
- **THEN** отпечаток считается заново (`max_age_s=0`); недоступный tools даёт в его части `{error}` и пустой `catalog`
