# agent-turn Specification

## Purpose
Ход консоли — один вопрос пользователя, отправленный агенту Ouroboros отдельной задачей API, прочитанный из потока
событий этой задачи и собранный в запись хода: что увидел пользователь, вызовы инструментов с их прогонами, деньги,
время и онлайн-проверки. С Ouroboros говорит `src/oil_ouroboros/agent.py`, ход собирает и хранит
`src/oil_ouroboros/console/turns.py`, чат даёт `src/oil_ouroboros/console/app.py`. Какой текст показать и как
проверяются числа — спека `answer-verification`.

## Requirements

### Requirement: Запуск и прогресс хода в чате
Консоль SHALL принимать вопрос `POST /api/chat` `{question, conversation_id?}` и вести ход в фоновом потоке: ответ
`{turn_id, conversation_id}` приходит сразу, пустой вопрос — `400` «пустой вопрос». `GET /api/chat/{turn_id}` отдаёт
прогресс идущего хода из памяти процесса (`done: false`), затем сохранённый ход (`done: true`); неизвестный ход —
`404` «нет хода». Эндпоинта отмены хода у консоли нет: задачу отменяет только сам ход.

#### Scenario: Новый разговор
- **WHEN** приходит `POST /api/chat` с `{"question": "Прогноз Brent?"}` без `conversation_id`
- **THEN** ответ — `turn_id` из 12 hex-символов и `conversation_id` вида `c-<10 hex>`
- **AND** прогресс хода заведён со стадией `queued`, ход идёт в фоновом потоке

#### Scenario: Пустой вопрос
- **WHEN** `question` состоит из пробелов
- **THEN** консоль отвечает `400` «пустой вопрос» и задачу агенту не ставит

#### Scenario: Ход в работе
- **WHEN** Ouroboros принял задачу, а агент вызвал `research`
- **THEN** `GET /api/chat/{turn_id}` — `done: false`, `stage: "tools"` (до этого — `accepted` с `task_id`),
  `tool_calls` с вызовом `research`, `elapsed_s` с точностью 0,1 с
- **AND** после записи хода в журнал — `{"done": true, "turn": {...}}`, прогресс из памяти удалён

### Requirement: Состав текста задачи
Текст задачи SHALL собираться `agent.compose(question, history, turn_id, dialog_pack)` из частей через пустую строку:
`[Диалог]` — последние `HISTORY_TURNS` = 3 реплики с непустым вопросом; `[Вопрос пользователя]` и вопрос; строка
«Язык ответа: …» по письменности вопроса (`evidence.text.lang_of`) и `TURN_RULES`; служебная метка
`oilo-turn <turn_id>`, по которой журнал LLM-шлюза относит вызовы модели к ходу.

#### Scenario: Вопрос на английском
- **WHEN** вопрос — «What is the latest IEA view on oil demand?»
- **THEN** текст содержит «Язык ответа: английский (English).», `TURN_RULES` и «Answer in English: …»
- **AND** для вопроса кириллицей — «Язык ответа: русский.» без `ANSWER_IN_ENGLISH`

#### Scenario: История с пакетом диалога
- **WHEN** история — `{"q": "Прогноз Brent?", "a": "Медиана $116 [F33]."}`, `dialog_pack="P77"`
- **THEN** текст содержит «Пользователь: Прогноз Brent?», «Аналитик: Медиана $116 [F33].», «из пакета диалога P77» и
  «добавь P77 в pack_ids»
- **AND** без пакета диалога вместо этого — «для цифр нужны результаты инструментов этого хода»

#### Scenario: Длинный прошлый ответ
- **WHEN** текст прошлого ответа до раздела `**Источники**` длиннее 1200 символов
- **THEN** в реплику «Аналитик:» идут его первые 1200 символов и знак «…», раздел источников — нет

### Requirement: Очистка текста пользователя
Текст пользователя MUST NOT становиться командой Ouroboros или разметкой хода: `agent.sanitize` заменяет ведущий «/»
на «∕», убирает `[Вопрос пользователя]` и `[Диалог]`, обрезает вопрос до 4000 символов; `oilo-turn` в любом регистре —
в вопросах и в прошлых ответах — заменяется на `oilo_turn`, и в тексте задачи остаётся единственная метка хода.

#### Scenario: Команда в вопросе
- **WHEN** вопрос — «/panic What is the latest IEA view on oil demand?»
- **THEN** в тексте задачи нет `/panic`, есть `∕panic`

#### Scenario: Чужая метка хода
- **WHEN** вопрос содержит `oilo-turn 0123456789ab`, история — `OILO-TURN=ffffffffffff` и `oilo-turn eeeeeeeeeeee`,
  `turn_id` — `abc123def456`
- **THEN** `oilo-turn` встречается в тексте задачи ровно один раз — `oilo-turn abc123def456`

### Requirement: Правила хода консоли
`agent.TURN_RULES` SHALL содержать только отличия хода консоли от регламента агента («Shared understanding»,
спека `agent-bootstrap`): вопрос не о нефтегазовой отрасли — только отказ по разделу «Вне компетенции»;
`mcp_oil__verify_answer` с `render=false`, в `finish_task` — проверенный черновик со ссылками символ в символ; не
писать в память и не запускать фоновых задач; раздел «This room» — не источник, цифры только из инструментов
хода. Протокол инструментов MUST NOT повторяться.

#### Scenario: Одна точка правды
- **WHEN** тест сверяет `TURN_RULES` с регламентом `ouroboros/memory/knowledge/overview.md`
- **THEN** в `TURN_RULES` есть «Вне компетенции» и «This room», нет `research`, `acceptance_subject`, `pack_ids`,
  «ещё раз», «не больше»
- **AND** в регламенте есть `acceptance_subject`, «отдельным шагом» и `render=false`

#### Scenario: Комната владельца не источник
- **WHEN** в контексте задачи комната Main с готовыми ответами о нефти и метками, а вопрос — «Какая сейчас цена газа
  Henry Hub?»
- **THEN** агент вызывает `price_quote(henry_hub)`, и цифры ответа — из её результата (F#) в этом ходе
- **AND** кейсы eval `Q04` и `Q05` проходят на стенде с этой комнатой

### Requirement: Задача Ouroboros для хода
Консоль SHALL ставить ход задачей `POST <OUROBOROS_URL>/api/tasks` (по умолчанию `http://ouroboros:8765`) с
заголовком `X-Ouroboros-Password` (`OUROBOROS_PASSWORD`, если задан), без системного прокси, с `disabled_tools` из
`ouroboros/disabled_tools.txt`, `memory_mode: "forked"` (запись памяти — в копию задачи, чтение — из общей памяти,
в том числе разговор владельца) и `execution_timeout_sec` = `TURN_TIMEOUT_S` = 480. Отказ Ouroboros MUST завершать
ход ошибкой.

#### Scenario: Тело задачи
- **WHEN** консоль ставит ход `abc123def456` с вопросом «Прогноз Brent?»
- **THEN** тело — `description`, `title` (первые 80 символов вопроса), 137 имён в `disabled_tools` (из встроенных
  остаются `finish_task`, `knowledge_read`, `knowledge_list`), `memory_mode: "forked"`, `execution_timeout_sec: 480`,
  `source: "oilo-console"`
- **AND** `metadata` — `oilo_turn: "abc123def456"`, `oilo_conversation`, `oilo_question` (до 2000 символов)

#### Scenario: Ouroboros не принял задачу
- **WHEN** `POST /api/tasks` отвечает `HTTP 503`
- **THEN** ход сохраняется: `status: error`, `error: "RuntimeError: Ouroboros не принял задачу: HTTP 503 …"`
- **AND** ответ без `task_id` даёт ошибку «Ouroboros не вернул task_id: …»

### Requirement: Ход читается из потока событий задачи
`agent.ask` SHALL читать ход из SSE `GET /api/tasks/{id}/events` (`cursor` — последний `seq`, `wait` =
`STREAM_WAIT_S` = 15 с) и переподключаться, пока не придёт `task_result` с терминальным статусом или не пройдёт
`TURN_TIMEOUT_S + 30` с; между подключениями опрашивается `GET /api/tasks/{id}` (`503` — `pending`). Сетевая ошибка
потока MUST NOT прерывать ход. Вызовы инструментов сворачиваются в один на `invocation_id`.

#### Scenario: Итог в потоке
- **WHEN** поток отдаёт вызов `mcp_oil__research` с `trace 0123456789ab` (`source=tools`), `llm_usage` с
  `prompt_tokens: 100` (`source=events`) и `task_result` `{"status": "completed", "result": "Ответ [R1]."}`
- **THEN** ход — `status: completed`, `answer: "Ответ [R1]."`, один вызов `research` (`mcp: true`, префикс
  `mcp_oil__` снят) с trace `0123456789ab`, `llm_rounds[0].tokens_in = 100`
- **AND** `GET /api/tasks/{id}` не вызывается

#### Scenario: Дубли строк журнала
- **WHEN** у вызова `invocation_id=i1` три строки: начало, копия из журнала форка памяти и итог со статусом `ok`
- **THEN** `tool_calls` содержит один вызов `research` с `ok = true`; у вызова с `is_error` — `preview` из последних
  600 символов результата

#### Scenario: Терминальные статусы
- **WHEN** `task_result` приходит со статусом из `TERMINAL`: `completed`, `failed`, `cancelled`, `canceled`, `error`,
  `stopped`, `timeout`, `timed_out`, `rejected`, `aborted`, `paused`, `budget_exceeded`, `budget_paused`,
  `interrupted`
- **THEN** чтение потока заканчивается, статус хода — статус итога

#### Scenario: Сбой потока
- **WHEN** подключение к потоку событий обрывается ошибкой HTTP
- **THEN** в лог — предупреждение «поток событий задачи …», ход переподключается с прежним `cursor`

### Requirement: Завершение задачи и отмена
`agent.ask` SHALL закрывать ход статусом итога задачи либо своим — `paused_budget`, `completed_by_console`,
`timeout` — и в этих трёх случаях сам отменять задачу (`POST /api/tasks/{id}/cancel`, сетевые ошибки игнорируются),
чтобы она не продолжилась. Ответ хода — итоговый текст задачи только при `completed` и аргумент `answer` вызова
`finish_task` при `completed_by_console`; при других статусах ответ пуст.

#### Scenario: Пауза по потолку денег
- **WHEN** задача отвечает `{"status": "paused_exact_continuation", "reason_code": "budget_paused"}` (статус на
  `paused…` или `reason_code` `budget_paused`/`owner_paused`)
- **THEN** консоль сразу отменяет задачу, статус хода — `paused_budget`

#### Scenario: Ouroboros держит завершение
- **WHEN** агент успешно вызвал `finish_task({"action": "finish", "answer": "…"})`, а задача не закрылась за 30 с
  (`FINISH_HOLD_S`)
- **THEN** консоль отменяет задачу, статус — `completed_by_console`, ответ — `answer` из вызова, если он короче
  `TOOL_ARG_LIMIT` = 2990 символов (длиннее журнал Ouroboros обрезает — тогда ответ пуст)

#### Scenario: Таймаут хода
- **WHEN** итога нет 510 с (`TURN_TIMEOUT_S` 480 + 30)
- **THEN** консоль отменяет задачу, статус — `timeout`, ответ пуст

#### Scenario: Итог без текста
- **WHEN** `task_result` со статусом `completed` не содержит текста в `final_answer`, `result` или `answer` (строкой
  или полем `text`/`answer`/`final_answer`/`content`)
- **THEN** ответ берётся из повторного `GET /api/tasks/{id}`

### Requirement: Сборка хода и финал
`turns.assemble` SHALL дополнять каждый вызов с `trace` сводкой прогона `GET /api/runs/{trace}` сервиса инструментов
и решать, что увидит пользователь: отказ вне компетенции (нет MCP-вызовов и сырых [R#]/[W#]/[F#], текст совпадает с
`agent.REFUSAL`) показывается как есть с `answer_source: "refusal"`; иначе — по ответу `POST /api/finalize` (выбор
текста — спека `answer-verification`); финал недоступен — текст агента с `answer_source: "unverified"`.

#### Scenario: Отказ вне компетенции
- **WHEN** агент без инструментов завершил ход текстом «Это вне моей компетенции: я аналитик нефтегазового рынка.»
- **THEN** `answer_source = "refusal"`, `checks.refused = true`, `checks.verify_ok = true`
- **AND** запроса `/api/finalize` не было

#### Scenario: Проверенный черновик
- **WHEN** агент завершил ход черновиком «Спрос упадёт на 2,6 млн б/с [R1].», прошедшим `verify_answer`
- **THEN** финал получает `{answer, question, traces, history_ids}`; `answer_source = "verified_draft"`, `answer` —
  текст с меткой «[Отчёт IEA OMR …]» вместо `[R1]`, `answer_shown_ids` — тот же текст с `[R1]`
- **AND** `cited = ["R1"]`, `evidence` — записи из `GET /api/evidence?ids=R1`, `allowed_ids` — из ответа финала

#### Scenario: Чужая ссылка
- **WHEN** в тексте агента есть `[R9]`, которого нет среди доказательств хода и истории
- **THEN** `foreign_citations = ["R9"]`, `checks.citations_in_turn = false`

#### Scenario: Финал недоступен
- **WHEN** `POST /api/finalize` падает с ошибкой
- **THEN** пользователь видит текст агента, `answer_source = "unverified"`, `verification = {ok: false, error}`

### Requirement: Деньги и онлайн-проверки хода
Запись хода SHALL содержать деньги и время по слоям — `cost_agent_usd` (раунды LLM агента), `cost_tools_usd`
(прогоны инструментов), `cost_usd`, `rounds`, `tokens_in`, `tokens_cached`, `ms`, отпечаток версии агента — и
`checks`: онлайн-проверки без эталона для мониторинга (ответ дан, проверка вызвана и пройдена, показан проверенный
текст, первый инструмент, метки, чужие ссылки, «кухня» агента, язык, ошибки инструментов).

#### Scenario: Стоимость хода
- **WHEN** раунд LLM стоил $0.002 (100 входных токенов, 50 из кэша), прогон `research` — $0.001
- **THEN** `cost_usd = 0.003`, `cost_agent_usd = 0.002`, `cost_tools_usd = 0.001`, `rounds = 1`, `tokens_cached = 50`

#### Scenario: Состав проверок
- **WHEN** ход собран
- **THEN** `checks` — `answered` (текст непуст, статус `completed`/`completed_by_console`), `finish_held`,
  `refused`, `research_first` (первый MCP — `research`, `list_reports`, `forecast_price`, `supply_shock_scenario`,
  `price_history` или `price_quote`), `verify_called`, `verify_ok`, `answer_is_checked_text` (`verified_draft` и проверка
  пройдена), `labels`, `citations_in_turn`, `persona_clean`, `language_ok`, `final_rewrite_rejected`,
  `tool_errors`, `builtin_tools`
- **AND** `persona_clean = false`, если в тексте ответа есть «Ouroboros», «Уробор…», `verify_answer`, `finish_task`,
  `mcp_oil` или «пакет P#»

#### Scenario: Правка агента отвергнута
- **WHEN** агент после проверки заменил цифру, и финал вернул `fallback_verified_draft` с непустым `rejected`
- **THEN** `checks.final_rewrite_rejected = true`, `checks.verify_ok = true`, `checks.answer_is_checked_text = false`

### Requirement: Журнал ходов
Каждый ход SHALL записываться `turns.save` в SQLite `<VAR_DIR>/turns.sqlite` (таблица `turns`, `insert or replace`
по `turn_id`) с источником — `chat`, `demo`, `eval`, `smoke` или `cli` (`oilo ask`) — и всей записью хода в JSON;
ход, упавший с исключением, MUST сохраняться со статусом `error`. `turns.listing` отдаёт ходы от новых к старым (по
умолчанию 100) с фильтрами по источнику и разговору.

#### Scenario: Сбой хода виден в мониторинге
- **WHEN** во время хода из чата `agent.ask` бросает исключение
- **THEN** в `turns.sqlite` — ход со статусом `error`, источником `chat`, `error` вида «<тип>: <текст>» до 500
  символов, пустым ответом и `checks = {answered: false}`
- **AND** прогресс хода из памяти удалён

### Requirement: История разговора в следующем ходе
Перед ходом консоль SHALL брать историю `turns.history(conversation_id)` — до 6 последних ходов разговора со
статусом `completed` или `completed_by_console`, от старых к новым, с текстом, который видел пользователь
(`answer_shown_ids`, иначе `answer_raw`), — и id их доказательств (`allowed_ids`): из них сервис инструментов делает
пакет диалога (`POST /api/packs`), и они же идут в финал хода как `history_ids`.

#### Scenario: Отвергнутая правка не попадает в историю
- **WHEN** прошлый ход показал проверенный черновик «…2,6 млн б/с [R1]», отвергнув правку агента «…987 млн б/с [R1]»
- **THEN** ответ реплики истории содержит «2,6 млн б/с» и `[R1]` и не содержит «987»

#### Scenario: Незавершённый ход
- **WHEN** прошлый ход разговора закончился статусом `timeout`, `paused_budget` или `error`
- **THEN** в историю следующего хода он не попадает

#### Scenario: Пакета диалога нет
- **WHEN** у истории нет id доказательств или `POST /api/packs` падает
- **THEN** ход ставится без пакета диалога, текст задачи содержит «для цифр нужны результаты инструментов этого хода»

### Requirement: Разговор по его id
Консоль SHALL пускать к разговору (`POST /api/chat` с `conversation_id`, `GET /api/conversations/{conversation_id}`,
`GET /api/chat/{turn_id}`) любого, кто знает его id: логинов нет. `GET /api/conversations/{conversation_id}` MUST
отдавать до 50 последних ходов разговора (всех источников) от старых к новым.

#### Scenario: Продолжение разговора
- **WHEN** приходит `POST /api/chat` с `conversation_id` прежнего разговора, в том числе вида `c-<12 hex>-<10 hex>`
- **THEN** ход идёт в этом разговоре, его история уходит агенту в текст задачи

#### Scenario: Чтение разговора
- **WHEN** запрашивается `GET /api/conversations/{conversation_id}`
- **THEN** ответ — до 50 последних ходов разговора (всех источников) от старых к новым
