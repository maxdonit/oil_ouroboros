## MODIFIED Requirements

### Requirement: API и вкладка «Качество»
Консоль SHALL отдавать прогоны eval через API: `GET /api/evals` (30 новейших: `eval_id`, `label`, `dataset`,
`repeats`, `config`, `summary`, `started`, `duration_s`), `GET /api/evals/{id}` (запись или 404 `нет прогона`),
`GET /api/evals/status` (`active`, `started`, `label`, `only`, `error`, `eval_id`) и `POST /api/evals/run`. Второй
запуск при идущем MUST отвечать 409 `eval уже идёт`: флаг ставится под блокировкой до старта потока.

#### Scenario: Запуск из консоли
- **WHEN** admin отправляет `POST /api/evals/run {"only": ["R01"], "label": "t"}`
- **THEN** в фоне идёт прогон `evals/datasets/dev.jsonl` с `repeats` из тела (по умолчанию 1), ответ `{"started": true}`
- **AND** вкладка опрашивает `/api/evals/status` каждые 10 с, пока `active`

#### Scenario: Вкладка для разных ролей
- **WHEN** вкладку «Качество» открывает admin
- **THEN** видны прогоны, KPI, доли по категориям и проверкам, таблица кейсов со ссылкой `#/turns/<run_id>`,
  сравнение и форма запуска
- **AND** у роли `user` вкладки «Качество» нет, а `GET /api/evals` и `POST /api/evals/run` от неё — 403

#### Scenario: Вердикт сравнения
- **WHEN** в сравнении `p_value < 0.05`
- **THEN** при `n_net > 0` вкладка пишет «улучшение статистически значимо», при `n_net < 0` — «ухудшение
  статистически значимо»; иначе — «различие не значимо (p ≥ 0.05)»
