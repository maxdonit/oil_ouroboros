# deployment Specification

## Purpose
Развёртывание системы одной командой `docker compose up -d` по `docker-compose.yml`: состав сервисов (ядро и
профили), сети с изоляцией агента от интернета, порты, тома, порядок запуска, ресурсы и секреты по сервисам, хост за
прокси организации (`deploy/compose/proxy.yml`), общие HTTP-клиенты (`src/oil_ouroboros/net.py`), образы
(`Dockerfile`), создание `.env` (`scripts/init_env.py`) и выкладка на сервер (`scripts/deploy.py`).

## Requirements

### Requirement: Ядро и профили
`docker-compose.yml` SHALL всегда запускать ядро — `qdrant`, `encoder`, `gateway`, `tools`, `bootstrap`, `ouroboros`,
`console`, — а сервисы профилей только при профиле в `COMPOSE_PROFILES`: `owner` — `ouroboros-ui`, `egress`,
`schedules` (спека owner-channel), `gigachat` — `gpt2giga` (спека llm-profiles). Шаблон `.env.example` включает оба,
пусто — только ядро. У каждого сервиса, сети и тома MUST быть поля `x-role`/`x-why` (их показывает консоль).

#### Scenario: Профили по умолчанию
- **WHEN** `.env` создан из шаблона
- **THEN** `COMPOSE_PROFILES=owner,gigachat`, имя проекта — `${COMPOSE_PROJECT_NAME:-oilo}`

#### Scenario: Только ядро
- **WHEN** `COMPOSE_PROFILES=` и выполняется `docker compose up -d`
- **THEN** запускаются семь сервисов ядра; `bootstrap` завершается с кодом 0, остальные работают

#### Scenario: Описание сервисов
- **WHEN** тест читает `docker-compose.yml`
- **THEN** у каждого сервиса есть `x-role` и `x-why`, у каждой сети и тома — `x-why`; у `proxy` из
  `deploy/compose/proxy.yml` — тоже

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

### Requirement: У сервиса только свои секреты
`.env` общий для сервисов образа `oilo/app` (`env_file: .env`), поэтому каждый такой сервис SHALL получать блок
`x-no-secrets` с пустыми значениями всех секретов, а свои секреты — явно в `environment`. `encoder`, `gpt2giga`,
`egress` и `ouroboros` `.env` не получают вовсе.

#### Scenario: Обнуляемые секреты
- **WHEN** читается `x-no-secrets`
- **THEN** в нём 12 ключей: `OPENROUTER_API_KEY`, `OUROBOROS_PASSWORD`, `MCP_TOKEN`, `TOOLS_API_TOKEN`,
  `TAVILY_API_KEY`, `BRAVE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GIGACHAT_CREDENTIALS`, `GPT2GIGA_API_KEY`,
  `GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`

#### Scenario: Свои секреты сервисов
- **WHEN** читаются переменные сервисов в compose
- **THEN** `gateway` — `OPENROUTER_API_KEY`, `GPT2GIGA_API_KEY`, `GATEWAY_TOKENS`; `tools` — `TOOLS_API_TOKEN`,
  `MCP_TOKEN`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `OPENAI_COMPAT_API_KEY=${GATEWAY_TOKEN_TOOLS}`; `console` —
  `TOOLS_API_TOKEN`, `OUROBOROS_PASSWORD`, `GATEWAY_TOKEN_CONSOLE`; `bootstrap` —
  `GATEWAY_TOKEN_OUROBOROS`, `MCP_TOKEN`, `OUROBOROS_PASSWORD`, `TELEGRAM_BOT_TOKEN`; `schedules` — `OUROBOROS_PASSWORD`
- **AND** у `tools`, который читает недоверенный веб, пусты `OPENROUTER_API_KEY`, `OUROBOROS_PASSWORD` и
  `GATEWAY_TOKEN_CONSOLE`

#### Scenario: Обязательные токены
- **WHEN** в `.env` пуст `GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE` или `TOOLS_API_TOKEN`
- **THEN** compose не стартует с ошибкой `задайте <имя>…`

### Requirement: Порядок запуска и проверки здоровья
Compose SHALL запускать сервисы по `depends_on`: `encoder` после `qdrant`; `tools` после `qdrant`, `encoder` и
здорового `gateway`; `ouroboros` после успешно завершённого `bootstrap`, здоровых `tools` и `gateway`; `console` после
запуска `tools` и `ouroboros`. Здоровье `ouroboros` MUST означать `supervisor_ready` в `/api/state`: его рабочий
процесс читает список инструментов MCP один раз при старте.

#### Scenario: Проверки здоровья
- **WHEN** Docker проверяет сервисы (интервал 30 с)
- **THEN** `gateway` — `curl -fsS http://127.0.0.1:8030/health` (старт 20 с), `tools` — `:8000/api/health` (60 с),
  `encoder` — `:8020/health` (180 с), `console` — `:8000/api/health` (30 с), `ouroboros` — `supervisor_ready` из
  `http://127.0.0.1:8765/api/state` (таймаут 10 с, старт 90 с)

#### Scenario: Невалидный каталог
- **WHEN** `tools` отвечает на `/api/health` 503 (`invalid_catalog`)
- **THEN** `tools` нездоров, и `ouroboros` не стартует

#### Scenario: Засев упал
- **WHEN** `bootstrap` завершился с ненулевым кодом
- **THEN** `ouroboros` не стартует

### Requirement: Ресурсы и перезапуск
Compose SHALL ограничивать память сервисов: `qdrant` 2g, `encoder` 5g, `gateway` 512m, `tools` 3g, `ouroboros` 4g,
`console` 1g, `gpt2giga` 512m, `ouroboros-ui` 64m, `egress` 128m, `schedules` 256m (ядро — 15,5 ГБ), и ядра: `encoder`
— `${ENCODER_THREADS:-4}`, `tools` — `${TOOLS_CPUS:-4}`, `ouroboros` — 2. Долгоживущие сервисы перезапускаются
(`unless-stopped`), разовые `bootstrap` и `schedules` — нет (`"no"`), `ouroboros` — `on-failure`: после `/panic` он
остаётся остановленным.

#### Scenario: Потоки кодировщика
- **WHEN** `ENCODER_THREADS=8`
- **THEN** у `encoder` `cpus: 8`, `OMP_NUM_THREADS=8`, `MKL_NUM_THREADS=8`

#### Scenario: Пользователи контейнеров
- **WHEN** запускаются `ouroboros` и `bootstrap`
- **THEN** `ouroboros` работает под `1000:1000` (`stop_grace_period: 30s`), `bootstrap` — под `0:0`, чтобы засеять
  каталог данных пользователя 1000

### Requirement: Тома данных
Compose SHALL хранить состояние в именованных томах: `qdrant_data` (индекс), `models` (модели HuggingFace у
`encoder`), `tools_var` (общий у `tools` и `encoder`, `/app/var`), `gateway_var`, `console_var`, `ouroboros_data`,
`ouroboros_work`, `ouroboros_home`. Консоль MUST монтировать `ouroboros_data` только на чтение.

#### Scenario: Данные агента
- **WHEN** запущены `ouroboros`, `bootstrap`, `console`, `schedules`
- **THEN** `ouroboros_data` смонтирован в `ouroboros:/data`, `bootstrap:/ouroboros-data`, `schedules:/ouroboros-data`
  и `console:/ouroboros-data:ro`; `ouroboros_work` и `ouroboros_home` — у `ouroboros` (`/work`, `/home/ouroboros`) и
  `bootstrap`

#### Scenario: Остановка без потери данных
- **WHEN** выполняется `docker compose down` без `--volumes`
- **THEN** тома и их данные сохраняются

### Requirement: Хост за прокси организации
Дополнение `deploy/compose/proxy.yml` (`COMPOSE_FILE=docker-compose.yml:deploy/compose/proxy.yml`) SHALL добавлять
сервис `proxy` (`alpine/socat:1.8.0.3`, `TCP-LISTEN:3128` → `${UPSTREAM_PROXY}`, сети `oilo` и внешняя `upstream` —
`${UPSTREAM_PROXY_NETWORK:-bridge}`, 64m) и пускать `egress` через него (`EGRESS_UPSTREAM=proxy:3128`). Без
`UPSTREAM_PROXY` compose MUST NOT стартовать (ошибка `задайте UPSTREAM_PROXY=host:port`).

#### Scenario: Внешние запросы через прокси
- **WHEN** `OUTBOUND_PROXY=http://proxy:3128`
- **THEN** через прокси идут веб и сайты агентств у `tools`, внешний маршрут LLM у `gateway` и загрузка моделей у
  `encoder` (`HTTPS_PROXY=${OUTBOUND_PROXY:-}`)
- **AND** `gpt2giga` прокси не получает и ходит к GigaChat напрямую; `ouroboros` до `proxy` не достаёт — тот только
  в сетях `oilo` и `upstream`

### Requirement: Исходящие HTTP-клиенты
Модуль `net.py` SHALL давать общие HTTP-клиенты сервисов: `outbound(timeout=30)` — внешний интернет через
`OUTBOUND_PROXY` (если задан) с корнями `CA_BUNDLE` (если задан, иначе системными); `internal(timeout=15)` — сервисы
проекта без прокси из окружения (`trust_env=False`); `tools_api(path, params, body, timeout=60)` и
`gateway_api(path, params, timeout=15)` — внутренние API с токенами консоли.

#### Scenario: Служебное API инструментов
- **WHEN** консоль вызывает `tools_api("/api/finalize", body={…})`
- **THEN** уходит `POST <TOOLS_URL>/api/finalize` с `Authorization: Bearer <TOOLS_API_TOKEN>` (если токен задан), ошибка
  HTTP поднимается исключением

#### Scenario: Журнал шлюза
- **WHEN** консоль вызывает `gateway_api("/ledger", params={…})`
- **THEN** уходит `GET <GATEWAY_URL>/ledger` с `Authorization: Bearer <GATEWAY_TOKEN_CONSOLE>` в обход прокси

### Requirement: Образы и точка входа
`Dockerfile` SHALL собирать из `python:3.12-slim` две цели: `app` (без torch; `gateway`, `tools`, `console`,
`bootstrap`, `schedules`, `egress`) и `ml` (`encoder`: torch 2.14.1 CPU, docling; `HF_HOME=/app/models`). Обе работают
под пользователем `oilo` (uid 10001) с `OILO_ROOT=/app`, `VAR_DIR=/app/var`, `DATA_DIR=/app/data` и содержат копию
`config/` и `data/`. Агент — образ `oilo/ouroboros:7.7.0` из официального Dockerfile на закреплённом коммите без правок.

#### Scenario: Команда контейнера
- **WHEN** контейнер стартует с командой `tools`, `console`, `gateway`, `encoder`, `bootstrap`, `schedules` или `egress`
- **THEN** `docker/entrypoint.sh` запускает соответствующий модуль `oil_ouroboros`; любая другая команда — `oilo …`

#### Scenario: Файлы в образе
- **WHEN** меняется `config/`, `ui/`, `ouroboros/` или код
- **THEN** изменение действует после `docker compose up -d --build`: файлы входят в образ `oilo/app`

#### Scenario: Теги образов
- **WHEN** собираются образы
- **THEN** свои — `oilo/app:${OILO_TAG:-local}` и `oilo/ml:${OILO_TAG:-local}`; агент — из
  `https://github.com/razzant/ouroboros.git#e41596c389c3c33c3155637aaf67f8bebbf4934d`; Qdrant — `qdrant/qdrant:v1.19.2`

### Requirement: Создание .env
`scripts/init_env.py` SHALL создавать `.env` из `.env.example` с правами 600, генерируя пустые внутренние секреты
`GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`, `TOOLS_API_TOKEN`, `MCP_TOKEN`,
`GPT2GIGA_API_KEY` (48 hex); пароль UI Ouroboros `OUROBOROS_PASSWORD` берётся из шаблона (`ouroboros`), логинов
консоли он не создаёт. Существующий
`.env` без `--fill` MUST оставаться нетронутым; `--fill` дописывает только пустые. Внешние ключи скрипт не создаёт.

#### Scenario: Первый запуск
- **WHEN** `.env` нет и выполняется `python scripts/init_env.py`
- **THEN** создан `.env` с шестью токенами и `OUROBOROS_PASSWORD=ouroboros`; напечатаны адрес консоли и адрес и
  пароль UI Ouroboros
- **AND** напечатано `впишите GIGACHAT_CREDENTIALS в .env (LLM-профиль gigachat; …)`

#### Scenario: Повторный запуск
- **WHEN** `.env` уже есть
- **THEN** напечатано `.env уже есть — ничего не меняю (--fill дописывает пустые токены)`, файл не изменён

#### Scenario: Пароль UI стёрт
- **WHEN** в `.env` строка `OUROBOROS_PASSWORD=` пуста и выполняется `python scripts/init_env.py --fill`
- **THEN** пароль не генерируется; напечатано `впишите OUROBOROS_PASSWORD в .env — без него агент не стартует`

### Requirement: Выкладка кода и окружения на сервер
`scripts/deploy.py [--env ИМЯ]` SHALL выкладывать код из рабочего дерева и настройки из `.env` (или `.env.<ИМЯ>`) на
цель `DEPLOY_SSH`/`DEPLOY_DIR` (`DEPLOY_KEY` — по желанию) по ssh: `push` — код, `env` — окружение, `up [сервисы]` —
`push`, `env`, `docker compose build` и `up -d --remove-orphans`, `restart <сервисы>` —
`up -d --no-deps --force-recreate`. Строки `DEPLOY_*` MUST оставаться локально; значения ключей не печатаются.

#### Scenario: Копия кода
- **WHEN** выполняется `push`
- **THEN** уходит tar.gz файлов git (отслеживаемых и новых неигнорируемых) с манифестом `.deploy-manifest`: CRLF → LF
  у небинарных файлов, `.sh` — 755, время правки сохраняется
- **AND** на сервере удаляются файлы прошлого манифеста, которых нет в новом; `.env` создаётся из `.env.example`
  (umask 077), если его нет

#### Scenario: Синхронизация окружения
- **WHEN** выполняется `env`
- **THEN** на сервер уходят непустые ключи локального файла, а ключи LLM-профилей (`deploy/llm/*.env` без
  `REQUIRES_COMPOSE_PROFILE`, плюс `LLM_PROFILE`, `COMPOSE_PROFILES`), если они есть в файле, — и пустыми; строки
  заменяются на месте, новые — в конец, файл пишется только при изменениях (umask 077)
- **AND** печатается `изменены: …; добавлены: …; удалены: …; без изменений: N`

#### Scenario: Pull и prune
- **WHEN** выполняется `env --pull` или `env --prune`
- **THEN** `--pull` переносит серверный `.env` в локальный, локальные значения главнее; `--prune` удаляет с сервера
  ключи, которых нет в локальном наборе

#### Scenario: Нет цели
- **WHEN** нет файла окружения или в нём не заданы `DEPLOY_SSH` / `DEPLOY_DIR`
- **THEN** скрипт завершается с сообщением, ничего не отправив

### Requirement: Резервная копия и остановка
`deploy.py backup [каталог]` SHALL сохранять невосстановимое состояние сервера (белый список `BACKUP_PATHS`) в архив у
оператора `backups/<имя файла окружения без точки>-<ГГГГММДД-ЧЧММ>.tgz`; индекс Qdrant, модели и кэши не копируются —
они пересоздаются. `deploy.py down` MUST останавливать сервисы всех профилей (`--profile '*' down --remove-orphans`).

#### Scenario: Что в архиве
- **WHEN** выполняется `backup` с `.env`
- **THEN** архив `backups/env-<дата>.tgz` содержит `tools_var/reports`, `tools_var/runs.sqlite`,
  `tools_var/evidence.sqlite`, `tools_var/parsed`, `tools_var/mcp_audit.jsonl`, `console_var/turns.sqlite`,
  `console_var/evals`, `gateway_var/gateway/ledger.sqlite`, `ouroboros_data`

#### Scenario: Согласованные SQLite
- **WHEN** выполняется `backup`
- **THEN** базы SQLite сначала копируются онлайн-бэкапом в контейнерах `tools`, `console`, `gateway` в `*.sqlite.bak`,
  и в архив попадают эти копии; тома монтируются в `alpine:3.20.3` только на чтение

#### Scenario: Сбой копии
- **WHEN** удалённый `tar` завершился с ненулевым кодом
- **THEN** неполный архив удаляется, скрипт завершается с `резервная копия не снята (код N)`

#### Scenario: Чистый деплой
- **WHEN** выполняется `deploy.py down --volumes`
- **THEN** удаляются и тома проекта: индекс, журналы, память агента
