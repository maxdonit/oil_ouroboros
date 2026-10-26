# agent-eval Specification

## Purpose
Оценка качества агента (`src/oil_ouroboros/evals/`): набор кейсов с эталонами, прогон каждого кейса как настоящего
хода агента через Ouroboros, детерминированные проверки без LLM-судьи, сводка с доверительным интервалом и сравнение
версий точным McNemar; отдельно — eval поиска и отбора без агента. На время замера данные сервиса инструментов
заморожены, чтобы эффект версии агента не смешивался с эффектом новых данных. Сборка хода — спека `agent-turn`,
права на запуск — `console-ui`.

## Requirements

### Requirement: Набор кейсов
Кейсы SHALL храниться в JSONL (по умолчанию `evals/datasets/dev.jsonl`): строка — `Case` с полями `id`, `split`
(по умолчанию `dev`), `category`, `question`, `history` (прошлые реплики `{q, a}`), `expect`, `notes`. Пустые строки и
строки, начинающиеся с `#`, MUST пропускаться. `Expect` задаёт отказ, маршрут в веб, инструменты и их аргументы,
факты, типы и агентства ссылок, обязательные и запрещённые фразы, `min_verified_rate` = 0.9, `max_web_tier` = 2.

#### Scenario: Текущий dev-набор
- **WHEN** загружается `evals/datasets/dev.jsonl`
- **THEN** в нём 42 кейса, все `split = dev`: категории report (18), quote (5), out_of_scope (4), web (3),
  forecast (3), combined (2), followup (2), conflict, scenario, history, honesty, advice (по 1)

#### Scenario: Факт с ключевыми словами
- **WHEN** у кейса категорий report, conflict, combined есть `expect.facts`
- **THEN** каждый факт — `{any_of: [числа], what, near: [регулярные выражения]}`, и у всех таких фактов `near` непуст

### Requirement: Прогон через агента
`oilo eval` SHALL прогонять каждый кейс × повтор как настоящий ход агента (`agent.ask`) в отдельном разговоре
`eval:<eval_id>:<case_id>:<повтор>` с историей только из `history` кейса, в 2 потока. Ход MUST сохраняться в журнал
ходов с источником `eval`. Сбой кейса не роняет прогон, а пишется в его `error`; статус задачи не `completed` и не
`completed_by_console` даёт ошибку `задача агента: <статус>`.

#### Scenario: Выборочный прогон
- **WHEN** выполняется `oilo eval --only R01 forecast --label t`
- **THEN** идут кейс `R01` и все кейсы категории `forecast` (по `id` или `category`), по одному повтору
- **AND** команда печатает `{eval_id, config, summary}`; `--report <файл>` ещё пишет отчёт в markdown

#### Scenario: Параметры по умолчанию
- **WHEN** выполняется `oilo eval` без аргументов
- **THEN** набор `evals/datasets/dev.jsonl`, `--repeats 1`, метка пустая, все кейсы

### Requirement: Запись прогона
Прогон SHALL записываться в `var/evals/<eval_id>/record.json` по схеме `oilo.eval_run.v1` с полями `eval_id`
(`ГГГГММДД-ЧЧММСС-<4 hex>`), `label`, `dataset`, `repeats`, `config`, `started`, `duration_s`, `results`, `summary`.
`config` MUST быть отпечатком версии агента на момент старта (`fingerprint.current(max_age_s=0)`): по нему сравнение
показывает, что изменилось.

#### Scenario: Результат кейса
- **WHEN** кейс прогнан
- **THEN** в `results` строка `case_id`, `category`, `split`, `run_id` (= `turn_id` хода), `passed`, `checks`
  (`[{name, ok, detail}]`), `ms`, `cost_usd`, `llm_calls` (раунды агента), `numbers_total`, `numbers_verified`,
  `path` (инструменты по порядку), `error`

#### Scenario: Последний прогон
- **WHEN** запрашивается прогон `latest`
- **THEN** берётся последний по имени каталог `var/evals/*` с файлом `record.json`

### Requirement: Детерминированные проверки без LLM-судьи
Исход кейса SHALL решаться только бинарными проверками `checks.evaluate(case, turn)` — по одному типу ошибки, каждая с
причиной провала в `detail`; LLM-судьи MUST NOT быть. Проверки читают показанный ответ без подвала
`**Источники**` / `**Sources**`, успешные вызовы инструментов, `went_web`, `verification`, `evidence` и онлайн-`checks`
хода.

#### Scenario: Общие проверки
- **WHEN** проверяется любой кейс
- **THEN** всегда есть `no_error` (нет ошибки и ответ непуст), `scope` (отказ ровно когда `expect.refusal`),
  `persona_clean`, `language`; при `must_include_any` — `must_include` (у категории honesty — `admits_missing`), при
  `must_not_include` — `must_not_include`

#### Scenario: Ожидался отказ
- **WHEN** у кейса `expect.refusal = true`
- **THEN** после `language` проверки заканчиваются, а вызванные MCP-инструменты дают проваленную `no_tools`

#### Scenario: Ответ по существу
- **WHEN** отказ не ожидался
- **THEN** проверяются `research_first` (первый MCP — research, list_reports или расчёт), `route_web`, `tools`,
  `tool_args` (значение или `"<0"`/`">0"`), `no_tools` (без forecast_price, supply_shock_scenario, price_history, price_quote),
  `facts`, `cites_<kind>`, `cites_orgs`, `verified`, `answer_is_checked_text`, `source_quality`, `labels`
- **AND** `verified` пройдена, если проверка хода есть, доля подтверждённых цифр ≥ `min_verified_rate` и нет
  неизвестных ссылок; `source_quality` — нет процитированного веба уровня выше `max_web_tier` или без уровня

#### Scenario: Факт ищется в показанном ответе
- **WHEN** эталон `any_of: [2.6]` и `[0.62]`, ответ «Спрос снизится на 2,6 млн б/с и будет на 620 тыс. б/с ниже
  [Отчёт IEA OMR, октябрь 2026, с. 3]»
- **THEN** `facts` пройдена (числа в метках источников фактами не считаются); с «3,1 млн б/с» — провалена

### Requirement: Исход кейса и сводка прогона
Кейс SHALL считаться пройденным, если пройдены все проверки, кроме информационных `INFO_CHECKS` =
{`answer_is_checked_text`}: они считаются и показываются, но исход MUST NOT решать. Сводка `summary` SHALL содержать
`cases`, `runs`, `pass_rate`, `pass_rate_ci95` (95% Уилсона), `pass^<repeats>`, `by_category`, `by_check`,
`failures_by_check`, `numbers_verified_rate`, `cost_usd_total`, `cost_usd_per_answer`, `latency_p50_s`,
`latency_p95_s`, `rounds_mean`, `errors`.

#### Scenario: Информационная проверка провалена
- **WHEN** у кейса провалена только `answer_is_checked_text`
- **THEN** `passed = true`, а в `by_check` и `failures_by_check` этот провал учтён

#### Scenario: Метрики сводки
- **WHEN** считается сводка
- **THEN** `pass^k` — доля кейсов, пройденных во всех k повторах; перцентили времени — методом ближайшего ранга;
  `numbers_verified_rate` = `null`, если в ответах нет чисел; `pass_rate_ci95` = `null` при нуле прогонов

### Requirement: Сравнение прогонов точным McNemar
`oilo compare <база> <кандидат>` и `GET /api/evals/compare?base=&cand=` SHALL сравнивать прогоны по общим кейсам:
исход кейса — «верен строго больше чем в половине повторов», `n_break` — база верна, кандидат нет, `n_fix` — наоборот.
p MUST считаться двусторонним точным биномиальным тестом по `min(n_break, n_fix)` из `n_break + n_fix` при p = 0.5.

#### Scenario: Состав сравнения
- **WHEN** сравниваются два прогона
- **THEN** ответ содержит `base`, `cand`, `common_cases`, `mcnemar` (`n_fix`, `n_break`, `n_net`, `both_ok`,
  `both_fail`, `p_value` до 6 знаков), `fixed_cases`, `broken_cases`, `pass_rate`, `by_category`, `by_check`,
  `cost_usd_per_answer`, `latency_p95_s` и `config_diff` — ключи отпечатка, значения которых различаются

#### Scenario: Нет разногласий
- **WHEN** `n_break = 0` и `n_fix = 0`
- **THEN** `p_value = 1.0`; при `n_break = 0`, `n_fix = 5` — `p_value = 0.0625`

#### Scenario: Ничья по повторам
- **WHEN** кейс прошёл в одном повторе из двух
- **THEN** его исход для сравнения — «не верен»

### Requirement: Заморозка данных на время eval
Пока идёт замер, данные SHALL быть заморожены файлом `var/eval.lock` (`{eval_id, kind, at}`): `freeze.reason()`
возвращает `идёт замер eval: данные обновятся после`, новые выпуски ждут в очереди, цены EIA не обновляются (спеки
`reports-base`, `price-forecast`). Блокировка старше `LOCK_MAX_S` = 3 ч (по mtime) MUST NOT действовать.

#### Scenario: Eval через агента ставит блокировку в tools
- **WHEN** стартует `oilo eval` при непустом `TOOLS_URL`
- **THEN** до первого кейса идёт `POST /api/eval_lock {"eval_id", "active": true}` сервиса tools (ответ
  `{"locked": true}`), после прогона — `active: false`
- **AND** если поставить не удалось — прогон не начинается (`eval: не удалось заблокировать индекс…`); если не
  удалось снять — предупреждение в журнале, блокировка истечёт сама

#### Scenario: Оборванный прогон
- **WHEN** файлу `var/eval.lock` больше 3 ч
- **THEN** `freeze.reason() == ""`, данные снова обновляются

#### Scenario: Чужая блокировка
- **WHEN** `freeze.frozen(run_id, "retrieval")` начинается при уже действующей блокировке
- **THEN** он её не ставит заново и по окончании не снимает

### Requirement: Eval поиска и отбора без агента
`oilo eval-retrieval` SHALL прогонять на кейсах категорий report, conflict, combined с `expect.facts` узлы `research`
напрямую: `plan` (без фонового веба) → `retrieve` → `select_grade`, без Ouroboros, без узла `pack`, без веб-поиска и
без записи в журнал доказательств, в 3 потока, под `freeze.frozen(run_id, "retrieval")`. Запись MUST сохраняться в
`var/evals/retrieval/<run_id>.json` по схеме `oilo.eval_retrieval.v1`.

#### Scenario: Факт во фрагменте
- **WHEN** факт `any_of: [2.6], near: ["demand"]` ищется в тексте фрагмента
- **THEN** «demand … contract by 2.6 mb/d» — найден; «falls by 3 mb/d» — нет (точность эталона); «Supply drops by
  2.6» — нет (ключевого слова нет в окне 160 символов); 1.87 подтверждает эталон 1.9

#### Scenario: Прогон по слоям
- **WHEN** выполняется прогон кейсов `R02 R13 R18`
- **THEN** у каждого кейса 2 вызова LLM (план и отбор), факты `{what, candidate_rank, selected}` по фрагментам
  ожидаемых агентств, `orgs_covered`, `verdict`, `would_web`, а журнал доказательств не меняется
- **AND** сводка: `facts`, `fact_in_candidates`, `fact_selected`, `candidate_rank_p50`, `orgs_covered`, `verdicts`,
  `extra_web`, `missed_web`, `cost_usd`, `ms_p50`

#### Scenario: Планы из прошлого прогона и только поиск
- **WHEN** задан `--plans <run_id или файл>`, а затем ещё и `--no-select`
- **THEN** с `--plans` план берётся из записи (1 вызов LLM на кейс); с `--no-select` вызовов LLM 0, `verdict = null`,
  `fact_selected = null`

### Requirement: API и вкладка «Качество»
Консоль SHALL отдавать прогоны eval через API: `GET /api/evals` (30 новейших: `eval_id`, `label`, `dataset`,
`repeats`, `config`, `summary`, `started`, `duration_s`), `GET /api/evals/{id}` (запись или 404 `нет прогона`),
`GET /api/evals/status` (`active`, `started`, `label`, `only`, `error`, `eval_id`) и `POST /api/evals/run`. Второй
запуск при идущем MUST отвечать 409 `eval уже идёт`: флаг ставится под блокировкой до старта потока.

#### Scenario: Запуск из консоли
- **WHEN** отправлен `POST /api/evals/run {"only": ["R01"], "label": "t"}`
- **THEN** в фоне идёт прогон `evals/datasets/dev.jsonl` с `repeats` из тела (по умолчанию 1), ответ `{"started": true}`
- **AND** вкладка опрашивает `/api/evals/status` каждые 10 с, пока `active`

#### Scenario: Кому видна вкладка
- **WHEN** вкладку «Качество» открывают в режиме администратора
- **THEN** видны прогоны, KPI, доли по категориям и проверкам, таблица кейсов со ссылкой `#/turns/<run_id>`,
  сравнение и форма запуска
- **AND** в простом режиме вкладки «Качество» нет; `GET /api/evals` и `POST /api/evals/run` открыты без входа

#### Scenario: Вердикт сравнения
- **WHEN** в сравнении `p_value < 0.05`
- **THEN** при `n_net > 0` вкладка пишет «улучшение статистически значимо», при `n_net < 0` — «ухудшение
  статистически значимо»; иначе — «различие не значимо (p ≥ 0.05)»

### Requirement: Отчёт прогона в markdown
`oilo eval-report [eval_id|latest] --out var/eval_report.md` и `oilo eval --report <файл>` SHALL писать отчёт
`runner.report_markdown`: набор, повторы, длительность, версия Ouroboros и модель, версия агента и каталог; таблица
метрик (пройдено k/n с 95% ДИ Уилсона, доля подтверждённых цифр с одним знаком, цена, p50/p95, раунды, ошибки); доли
по категориям и проверкам, где информационные проверки MUST быть курсивом; строка на кейс, по `case_id`.

#### Scenario: Строка кейса
- **WHEN** кейс `R01` категории report пройден, путь `research → verify_answer → finish_task`
- **THEN** строка начинается с `| R01 | report | ✅ | research → verify_answer |`, а проваленная
  `answer_is_checked_text` в ней — `_answer_is_checked_text_`

#### Scenario: Отчёты в репозитории
- **WHEN** обновляется итог полного прогона
- **THEN** `docs/eval_report.md` — такой отчёт, а раздел «Качество» в `docs/report.md` описывает методику (кейсы —
  ходы через Ouroboros, проверки без LLM-судьи), команды `oilo eval`, `oilo compare`, `oilo eval-retrieval` и итог
