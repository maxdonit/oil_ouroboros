# Spec Delta

## MODIFIED Requirements

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
