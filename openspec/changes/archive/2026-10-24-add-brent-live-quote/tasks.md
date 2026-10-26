# Tasks

## 1. Котировка с биржи

- [x] 1.1 Раздел `quote` в схеме каталога (`catalog.py`) и в `config/current/forecast.yaml`: `roll_days: 3`,
  `cache_s: 300`, `timeout_s: 10`, с комментариями; версия в `config/current/meta.yaml`; ключи — в
  `docs/guide/parameters.md`. Проверка: `oilo catalog check`, `tests/test_consistency.py::test_catalog_keys_are_documented`.
  Итог: класс `Quote` и поле `forecast.quote` в схеме, ключи с комментариями в `forecast.yaml` (version 4), каталог
  2026.10.24-2; три строки в справочнике. Проверено: `oilo catalog check` — валиден, тесты каталога и согласованности.
- [x] 1.2 `src/oil_ouroboros/forecast/quotes.py`: запрос ISS через `net.outbound`, выбор контракта по `roll_days`,
  цена `LAST` → `SETTLEPRICE` → `PREVSETTLEPRICE` с видом и временем МСК, кэш в памяти и `var/quotes/brent.json`,
  заморозка eval, отказ `stale` / `unavailable` со спотом EIA. Проверка: `tests/test_quotes.py` на записанном ответе
  ISS (`tests/data/iss_br.json`) — сценарии спеки `price-quote` «Котировка после торгов пятницы», «Обычный день»,
  «Переход на следующий месяц», «Сделок ещё не было», «Два вызова подряд», «Идёт замер eval», оба сценария отказа.
  Итог: модуль `forecast/quotes.py` (`parse`, `fetch`, `current`, `snapshot`, `status`), фикстура — настоящий ответ ISS
  24.10.2026. Проверено: `tests/test_quotes.py` — 9 тестов по сценариям спеки, включая запрос через `net.outbound`.

## 2. Инструмент агента

- [x] 2.1 `price_quote` в графе `graphs/calc.py` (метка, JSON с `status`, `quote`, `spot_eia`, `note`, F# с ключом по
  времени котировки) и английская метка в `graphs/render.py`. Проверка: тест «Метка котировки», «Тот же F# для той же
  котировки» и `verify_answer`, подтверждающий 116.67 по F# котировки.
  Итог: ветка `price_quote` в `calc_compute`, `quote_label`, ключ F# по времени сделки и дате спота, два шаблона
  `LABEL_EN`; в котировке поле `time_ru` (короткую дату «23.10» проверка не узнаёт — пункт в рисках дизайна).
  Проверено: три теста в `tests/test_quotes.py` — метки на двух языках, F#, `verify_answer` по цене.
- [x] 2.2 `price_quote()` в `mcp/server.py` (`TOOLS`, `open_world_hint`, узкое описание «текущая цена Brent»),
  `allowed_tools` в `ouroboros/settings.template.json`, `research_first` в `console/turns.py`, ожидаемый состав в
  `console/smoke.py`, `no_tools` в `evals/checks.py`. Проверка: `test_agent_is_allowed_exactly_the_mcp_tools`, тесты
  MCP на `tools/list` и схемы, `tests/test_consistency.py::test_mcp_tools_are_described_everywhere` (после 2.3).
  Итог: инструмент `price_quote` (без аргументов, `open_world_hint`) после `price_history`, `INSTRUCTIONS`, засев,
  `CALC` в `turns.py`, `MCP` в `smoke.py`, `CALC_TOOLS` в `checks.py`, граф `calc` в реестре `GRAPHS` — пропуск,
  найденный тестом карты, оформлен MODIFIED-блоком «Графы и источники для консоли» в дельте `mcp-tools`.
  Проверено: `tests/test_tools.py`, `test_bootstrap.py`, `test_console.py`, `test_turns.py`, `test_schedules.py`.
- [x] 2.3 Регламент и среда агента: `ouroboros/memory/knowledge/overview.md` (текущая цена → `price_quote()`, вид цены и
  время, честность при `stale` / `unavailable`), `ouroboros/memory/WORLD.md` (девять инструментов, котировка в
  «Данных»), утренняя сводка `ouroboros/schedules/oil-morning-brief.json` (котировка в блоке «Цены»). Проверка:
  тесты засева и расписаний, сценарий «Текущая цена» спеки `agent-bootstrap`.
  Итог: правило «текущая цена Brent → `price_quote()`» в протоколе инструментов (вид цены, контракт, время словами,
  `stale` / `unavailable`, нет котировок WTI и Urals), веб — без «текущих котировок» Brent; `WORLD.md` — инструмент
  и котировка в «Данных»; утренняя сводка — `price_quote()` в шаге 1 и котировка в блоке «Цены», WTI — из веба;
  комментарий расписания приведён к коду (обновляется, пока владелец не менял). Проверено: `test_schedules.py`,
  `test_bootstrap.py`, `test_turns.py` (новые проверки регламента).
- [x] 2.4 Документы инструмента: README (список инструментов и примеры запросов), `docs/architecture/DESIGN.md`
  (таблица инструментов, раздел «Цены»), `docs/guide/how_it_works.md`. Проверка: `tests/test_consistency.py`.
  Итог: README — инструмент в обзоре, пример «Текущая цена», источник цен, Р5, «9 инструментов»; DESIGN — строка
  таблицы, «9 инструментов», раздел «Цены»; how_it_works — котировка в разделе расчётов. Проверено:
  `tests/test_consistency.py` (инструменты в документах и спеке, пути, ссылки).

## 3. Источники и eval

- [x] 3.1 Раздел `quote` в `GET /api/sources` (`tools_app.py`) без обращения к бирже и плитка со строкой котировки на
  вкладке «Источники» (`ui/sources.js`; версия ассетов в `ui/index.html`); `docs/guide/sources.md`. Проверка: тест
  `/api/sources` (сценарии «Котировка свежая», «Котировки ещё нет»), `node --check` копии `.mjs`, вид во временной
  консоли.
  Итог: `quotes.status()` в `/api/sources`, плитка «Котировка Brent» (предупреждение — котировка старше
  предыдущего рабочего дня или устарела: уточнено в дельте `data-sources`, чтобы в понедельник утром не было
  ложной тревоги) и строка под таблицей EIA; `docs/guide/sources.md`; ассеты 20261024.11. Проверено: тест
  `/api/sources` в `tests/test_quotes.py`, `node --check`; вид — в задаче 4.2.
- [x] 3.2 Два кейса `quote` в `evals/datasets/dev.jsonl` («Сколько сейчас стоит нефть Brent?», «What is the Brent
  price right now?»: `price_quote` первым, ссылка F#, без отказа) и заголовок категории в `ui/evals.js`. Проверка:
  тест набора кейсов (39 кейсов, категории).
  Итог: кейсы Q01 и Q02 (`tools: ["price_quote"]`, `must_cite_kinds: ["tool"]`) после H01, заголовок «текущая
  цена» в `ui/evals.js`, ассеты 20261024.12. Проверено: `test_dev_set_has_quote_cases`, `node --check`.

## 4. Проверка целиком

- [x] 4.1 Гейты: `ruff check` и `ruff format --check`, `pytest`, `openspec validate --all --strict`. Проверка: всё
  зелёное.
  Итог: ruff — чисто, pytest — 214 пройдено, `openspec validate --all --strict` — 19 из 19, каталог валиден.
- [x] 4.2 Стенд: `python scripts/deploy.py up`; `oilo ask "Сколько сейчас стоит Brent?"` — ответ с котировкой BR,
  временем и F#, проверка пройдена; вкладка «Источники» — раздел котировки. Проверка: ход в «Ходах», ответ в итоге.
  Итог: выкладка, засев обновил `allowed_tools`, `WORLD.md`, `overview.md`, расписание — до версии репозитория;
  ouroboros перезапущен. Ход «Сколько сейчас стоит нефть Brent?» — 32 с, `price_quote → verify_answer →
  finish_task`, BR-11.26 116,67 $ (23.10 23:50 МСК) и спот EIA 115,22 $ на 20.10, проверка 8/8. Агент повторил
  указание из `note` пользователю — `note` переписан справкой, выложено заново. Вкладка «Источники» — плитка и
  строка котировки, ассеты 20261024.12.
- [x] 4.3 Выборочный eval с согласия владельца (квота GigaChat): `oilo eval --only` по двум кейсам `quote` и трём
  прежним (`forecast`, `out_of_scope`, `report`). Проверка: новые кейсы пройдены, прежние не ухудшились.
  Итог: прогон `20261024-185914-25e1` (Q01, Q02, F01, O01, R01; согласие — «проверь на стенде»): 5/5, все
  проверки 1.0, ошибок 0, p50 34 с, $0 (GigaChat Freemium).
- [x] 4.4 Поправки по сверке `spec-guard` (DRIFT). Проверка: тесты и живой ход на английском.
  Итог: время сделки — `SYSTIME` (у вечерней сессии FORTS `TRADEDATE` — следующий торговый день), расчётные цены —
  `TRADEDATE` (`PREVDATE` у фьючерсов ISS не отдаёт); метки «Котировка/Quote» распознают фишки консоли (`ui/md.js`),
  онлайн-проверки (`turns.LABEL_TEXT`) и eval (`checks.LABELS`); рабочий день плитки — по МСК; `price_quote` в
  `ui/turnview.js`, `graphs/__init__.py`, `docs/guide/owner.md`; дельты дополнены (метки `stale`/`unavailable`, F# по
  статусу, ошибка в плитке, WTI в утренней сводке и регламенте, «четыре вида источников», паспорт каталога без
  номера версии — дельта `tool-catalog`). Проверено: `tests/test_quotes.py` (17), выкладка, ход «What is the Brent
  crude price right now?» — 28 с, английские метки, проверка 9/9.

## Workflow follow-up

- `/opsx:verify add-brent-live-quote` и субагент `spec-guard`.
- `/opsx:archive add-brent-live-quote`; новая спека `price-quote` получает Purpose из дельты — проверить, что заглушки
  нет; `openspec validate --all --strict` и `pytest tests/test_consistency.py`; таблица спек в
  `.claude/skills/sdd/SKILL.md` — строка `price-quote`.
- Один коммит: код, документы, живые спеки и архив change.
