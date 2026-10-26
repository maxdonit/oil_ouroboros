# owner-channel Specification

## Purpose
Канал владельца Ouroboros — профиль `owner` в `docker-compose.yml` (включён по умолчанию: `COMPOSE_PROFILES` =
`owner,gigachat`): собственный UI Ouroboros без ограничений консоли, выход агента наружу только через прокси `egress`
с белым списком хостов, Telegram-бот и утренняя сводка рынка по расписанию. Канал нужен владельцу агента и вне
требований к консоли; без профиля у агента один вход — консоль. Код — `src/oil_ouroboros/egress.py`,
`src/oil_ouroboros/schedules.py`, описания расписаний — `ouroboros/schedules/*.json`, руководство —
`docs/guide/owner.md`.

## Requirements

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
- **THEN** UI Ouroboros на `0.0.0.0:38630` (любой адрес хоста) пускает по паролю `ouroboros`, без пароля — страница
  входа (401)

### Requirement: Выход наружу только HTTPS-туннелем к разрешённым хостам
Сервис `egress` (`python -m oil_ouroboros.egress`, порт 3128, сети `oilo` и `agent`, без `.env`) SHALL пропускать
только `CONNECT <host>:443` к хостам из `EGRESS_ALLOW` (через запятую, без учёта регистра, шаблоны `fnmatch` вида
`*.example.com`; по умолчанию `api.telegram.org`). Содержимое туннеля MUST NOT читаться: внутри — TLS до самого хоста.
Ouroboros ходит наружу через него (`HTTPS_PROXY=http://egress:3128`; мимо прокси — `gateway`, `tools`, `ouroboros`,
`localhost`, `127.0.0.1`).

#### Scenario: Разрешённый и чужой хосты
- **WHEN** `EGRESS_ALLOW=api.telegram.org,*.githubusercontent.com`
- **THEN** `CONNECT api.telegram.org:443` и `CONNECT RAW.githubusercontent.com:443` разрешены
- **AND** `api.telegram.org:80`, `api.telegram.org:22`, `evil.org:443`, `api.telegram.org.evil.org:443` получают
  `403 Forbidden` и предупреждение в лог «отказано: <host:port> (разрешены: …, порт 443)»

#### Scenario: Не CONNECT
- **WHEN** клиент шлёт `GET http://example.com/ HTTP/1.1`
- **THEN** egress отвечает `405 Method Not Allowed`: открытого HTTP-прокси нет

#### Scenario: Туннель открыт
- **WHEN** `CONNECT api.telegram.org:443` разрешён и хост ответил за `TIMEOUT_S` = 15 с
- **THEN** клиент получает `HTTP/1.1 200 Connection established`, дальше байты передаются в обе стороны как есть
- **AND** недоступная за 15 с цель — `502 Bad Gateway`; заголовок запроса не пришёл за 15 с — соединение закрывается
  без ответа

### Requirement: Туннель через прокси организации
Если задан `EGRESS_UPSTREAM=host:port` (с `deploy/compose/proxy.yml` — `proxy:3128`), egress SHALL открывать туннель
к цели запросом `CONNECT <host>:443` к этому прокси и MUST применять белый список до обращения к нему. Ответ прокси без
` 200` в строке статуса — `502 Bad Gateway`. Пустой `EGRESS_UPSTREAM` — прямое соединение. При старте в лог пишется
«egress: :3128, разрешены …, выход прямой» или «… выход через host:port».

#### Scenario: Через прокси организации
- **WHEN** `EGRESS_UPSTREAM` указывает на прокси, клиент шлёт `CONNECT api.telegram.org:443`, затем `ping`
- **THEN** прокси получает ровно `CONNECT api.telegram.org:443 HTTP/1.1`, клиент — `200` и эхо `ping`

#### Scenario: Чужой хост до прокси не доходит
- **WHEN** `EGRESS_UPSTREAM` задан, а клиент шлёт `CONNECT example.com:443`
- **THEN** клиент получает `403`, прокси организации — ни одного запроса

### Requirement: Токен Telegram
Засев (спека `agent-bootstrap`) SHALL класть `TELEGRAM_BOT_TOKEN` из `.env` в `settings.json` Ouroboros, только если
там токена ещё нет или задан `--reset`; дальше токен ведёт владелец в UI Ouroboros, и засев его правку MUST NOT
откатывать. Токен — секрет: его нет в `settings_public` файла `state/oilo_seed.json`. Бот ходит к `api.telegram.org`
через `egress`; пустой `TELEGRAM_BOT_TOKEN` — канал владельца без Telegram, только UI.

#### Scenario: Первый засев с токеном
- **WHEN** в `.env` `TELEGRAM_BOT_TOKEN=123:abc`, а в `settings.json` токена нет
- **THEN** после засева `settings.json` содержит `TELEGRAM_BOT_TOKEN = "123:abc"`, в выводе — «TELEGRAM_BOT_TOKEN
  (секрет)»

#### Scenario: Владелец сменил токен
- **WHEN** владелец сменил токен в UI на `456:rotated`, а `.env` по-прежнему содержит `123:abc`
- **THEN** после повторного засева в `settings.json` остаётся `456:rotated`

### Requirement: Засев расписаний после старта агента
Разовый сервис `schedules` (профиль `owner`, `python -m oil_ouroboros.schedules`, пользователь `1000:1000`, после
`ouroboros` в состоянии `healthy`) SHALL засевать расписания из `ouroboros/schedules/*.json` (по имени файла, без
`_comment`) запросом `POST /api/schedules` Ouroboros с `X-Ouroboros-Password` и `reason` «засев oil-ouroboros
(ouroboros/schedules)». Поле описания `builtin_tools` MUST NOT уходить в Ouroboros. Итог — строки «расписание: <id>
<действие>», код 0.

#### Scenario: Ожидание готовности
- **WHEN** `GET /api/state` не отвечает `supervisor_ready: true`
- **THEN** засев опрашивает его раз в 5 с до `--wait` = 600 с, затем печатает «schedules: Ouroboros не готов за
  600 с» в stderr и завершается с кодом 2

#### Scenario: Ошибка API
- **WHEN** `POST /api/schedules` отвечает `HTTP 400`
- **THEN** засев печатает «schedules: /api/schedules: HTTP 400 …», завершается с кодом 2 и файл состояния не пишет

#### Scenario: Набор инструментов задачи по расписанию
- **WHEN** описание содержит `"builtin_tools": ["schedule_followup"]`
- **THEN** в теле нет `builtin_tools`, а `task.metadata.disabled_tools` — все имена `ouroboros/disabled_tools.txt`,
  кроме `schedule_followup` (`browse_page` там есть, `finish_task` — нет): контекст задачи — как у хода консоли

### Requirement: Расписание ведёт владелец
Засев SHALL ставить расписание и обновлять его до новой версии из репозитория, но MUST NOT трогать расписание, которое
владелец изменил, выключил или удалил в UI Ouroboros (Schedules); `--force` пишет версии из репозитория поверх.
Учёт — `state/oilo_seed.json` → `schedules_seeded[id] = {spec, row}`: sha версии из репозитория и sha полей автора
после засева (`name`, `description`, `enabled`, `timezone`, `trigger`, `task.text`, `task.chat_id`).

#### Scenario: Первый засев и повтор
- **WHEN** расписания в Ouroboros нет и засев его не ставил
- **THEN** засев создаёт его — «создано»
- **AND** после того как Ouroboros записал историю запуска, следующий засев — «без изменений», без `POST`

#### Scenario: Новая версия из репозитория
- **WHEN** владелец расписание не менял, а текст задачи в репозитории изменился
- **THEN** засев — «обновлено до версии из репозитория», в Ouroboros — новый текст

#### Scenario: Владелец выключил или удалил расписание
- **WHEN** владелец выключил расписание, а в репозитории вышла новая версия текста
- **THEN** засев — «оставлено: изменено владельцем», текст в Ouroboros прежний
- **AND** после удаления владельцем — «оставлено: удалено владельцем», а с `--force` — «создано» с `enabled: true`

### Requirement: Утренняя сводка рынка нефти
Репозиторий SHALL содержать одно расписание — `ouroboros/schedules/oil-morning-brief.json`: `id` `oil-morning-brief`,
`enabled: true`, cron `0 9 * * *` в `Europe/Moscow`, `builtin_tools: ["schedule_followup"]`, задача `type: "task"` с
`chat_id: 0` (ход скрыт). Её текст MUST вести агента к проверенной сводке с доставкой заметкой владельцу; метки
источников и строку проверки ставит сам `verify_answer` (`render=true`), финала хода консоли в этом пути нет.

#### Scenario: Шаги сводки
- **WHEN** срабатывает расписание `oil-morning-brief`
- **THEN** текст задачи требует: в одном шаге `list_reports()`, `price_quote()` и `price_quote(benchmark="wti")` (котировки Brent и WTI),
  `price_history` Brent и WTI (`freq="D"`, 10 дней) и
  `research` о новостях рынка за сутки (`search_web` — только без новостей за сутки); черновик по-русски до 1000
  знаков из блоков `**Цены.**` (котировки Brent и WTI с временем, динамика EIA), `**Новости.**` (3–5 пунктов со ссылками), `**Отчёты.**` (OPEC MOMR, IEA OMR,
  EIA STEO за 7 дней)
- **AND** затем `verify_answer(..., render=true)`, доставку `schedule_followup(notify=true, relation="independent",
  …)` с «Текстом для показа» символ в символ (заметка в чат Ouroboros и в Telegram) и `finish_task(action="finish")`

#### Scenario: Описание допустимо для Ouroboros
- **WHEN** тест проверяет `schedules.payload` всех описаний
- **THEN** есть ровно `oil-morning-brief`; `id` соответствует `[A-Za-z0-9][A-Za-z0-9_.-]{0,80}`, cron — 5 полей,
  часовой пояс существует
- **AND** в задаче и её `metadata` нет зарезервированных полей `memory_mode`, `workspace_root`, `parent_task_id`,
  `client_surface`, `task_id`, `session_id`
