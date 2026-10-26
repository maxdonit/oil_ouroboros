# Tasks

## 1. Мёртвый код

- [x] 1.1 Python: `baseline_as_of` в `graphs/calc.py`, `Node.llm` и `ToolRun.events` в `graphs/kit.py`, запасные значения
  в `graphs/runs.py`, `check(download=)` и статус `missing`, `trigger="manual"` в `reports/watch.py`,
  `Edition.optional` (`reports/editions.py`, `rag/ingest.py`), `supply_shock(world_supply_mbd=)`, `tier_title` в
  `web/search.py`, запасные значения в `web/credibility.py`, параметр `source` у `_run_turn`. Проверка: `ruff`, `pytest`
  затронутых модулей.
  Итог: всё перечисленное удалено; статусы журнала `started`/`done` получили названия. Интерфейсы (`llm/fake.py`,
  `rag/vectors.py`, параметры провайдеров веба) оставлены. Спека `research-tool` «Журнал прогонов» — без
  запасного статуса (нашёл spec-guard). Проверено: ruff, тесты watch, websearch, sources, forecast, tools, console.
- [x] 1.2 Статусы `indexed`, `uploaded`, `queued` — из фильтров последней проверки (`mcp/server.py`, `ui/sources.js`
  `OK`), комментарий там же. Проверка: тесты `list_reports` и `/api/sources`, сценарии дельт `mcp-tools` и
  `data-sources`.
  Итог: фильтры — `present`, `scheduled`, `downloaded`; тест `list_reports` — на `downloaded`, как в дельте.
- [x] 1.3 Тесты: неиспользуемые фикстуры `tests/test_eval_retrieval.py`; `PRICES_REFRESH_HOURS=0` в `tests/conftest.py`.
  Проверка: `pytest`.
  Итог: сделано; тесты затронутых модулей зелёные.

## 2. Одна точка правды

- [x] 2.1 Один список расчётных инструментов для `evals/checks.py` и `console/turns.py`; маркеры подвала и префиксы меток — константы в
  `evidence/text.py`, их используют `agent.py`, `console/turns.py`, `evals/checks.py`; тест «`smoke.MCP` = `TOOLS`»;
  запасные строки identity в `console/smoke.py` — одна. Проверка: `pytest`.
  Итог: `evidence/text.LABEL`, `LABEL_PREFIXES`, `FOOTER`; `turns.RAW_ID` заменён на `CITE`; список расчётных
  инструментов — `evals/checks.CALC_TOOLS` (консоль берёт его оттуда: `evals.retrieval` работает в tools, и
  обратный импорт потянул бы код консоли в tools); тест `test_smoke_expects_exactly_the_mcp_tools`.
- [x] 2.2 Тест согласованности: число инструментов MCP в документах, compose, схеме и docstring'ах равно `len(TOOLS)`.
  Проверка: тест падает на прежних «8 инструментов» и проходит после задач группы 4.
  Итог: `test_mcp_tool_count_in_texts` нашёл 4 места до правки и проходит после (снимки прогонов исключены).

## 3. Цена GigaChat без сети

- [x] 3.1 `llm/pricing.py`: `GigaChat-*` — 0 без чтения каталога; предупреждение в журнал, если схема `price_quote` при
  импорте не получила каталог (`mcp/server.py`). Проверка: тест «вызов GigaChat без цены — 0, сети нет».
  Итог: `pricing.GIGACHAT_PREFIX` и ранний 0; у шлюза своя константа с перекрёстным комментарием (шлюз
  импортирует прайс лениво); предупреждение в журнал в `_quote_instruments`. Проверено: `test_llm_core`,
  `test_gateway`, `test_tools`.

## 4. UI и тексты

- [x] 4.1 UI: ветки `canManage` и поле (`ui/evals.js`, `ui/main.js`, `ui/util.js`), запасной путь графов без доступа к
  ходам (`ui/graphview.js`); у роли `user` — без ссылки и подсказки на «Ходы» (`ui/chat.js`); названия категорий и
  проверок eval (`ui/evals.js`, `evals/runner.py`); статусы журнала `started`/`done` (`reports/watch.py`,
  `ui/sources.js`); фильтры `cli`/`smoke` (`ui/turns.js`, `ui/monitor.js`); названия рядов EIA (`ui/sources.js`);
  подсказки `research_first`; котировки в подсказках и вступлениях; цвет `completed_by_console`; «роль неизвестна»;
  версия ассетов. Проверка: `node --check` копий `.mjs`, вид во временной консоли.
  Итог: роль по умолчанию пусто и «роль неизвестна»; `canManage` удалён; запасной путь графов оставлен — он
  нужен при сбое загрузки журнала (комментарий исправлен); названия категорий и проверок выровнены с
  `evals/runner.CATEGORY_RU` (добавлен `quote`); пометка trigger `manual` убрана из `ui/sources.js` (код его не
  выдаёт, в журнале стенда его нет); ассеты 20261025.2. Проверено: `node --check` 16 модулей; вид —
  в задаче 5.2.
- [x] 4.2 `ui/architecture.svg` — 9 инструментов, котировки и Мосбиржа, GigaChat по умолчанию. Проверка: вид на вкладке
  «Архитектура» и в README.
  Итог: 9 инструментов, `price_quote` и Мосбиржа, маршрут шлюза GigaChat → gpt2giga, профили по умолчанию в
  легенде; XML валиден. Вид — в задаче 5.2.
- [x] 4.3 Документы и комментарии под код (proposal.md, «Документы и комментарии»), `docker-compose.yml` (`x-links`,
  `x-externals`, `x-role`, `x-why`), `deploy/compose/proxy.yml`, описания `config.py`, комментарии `llm.yaml`,
  docstring'и `gateway/app.py`, `console/demo.py`, `net.py`, `forecast/data.py`, `llm/pricing.py`. Проверка:
  `tests/test_consistency.py`.
  Итог: 17 пунктов аудита документов закрыты (9 инструментов, 42 кейса, Мосбиржа, три ряда EIA, профили по
  умолчанию с оговоркой про gigachat, 16,4 ГБ, цифры OpenRouter с профилем, правила хода, роли во вкладках);
  шапка `proxy.yml` больше не обещает прокси для gpt2giga. Память агента не трогали (версия агента). Проверено:
  ruff, `tests/test_consistency.py`, полный pytest.

## 5. Проверка целиком

- [x] 5.1 Гейты: `ruff check` и `ruff format --check`, `pytest`, `openspec validate --all --strict`, `node --check` всех
  модулей UI. Проверка: всё зелёное.
  Итог: ruff чисто, pytest — 232, `openspec validate --all --strict` — 20 из 20, каталог `current@2026.10.24-3#b21faf41c6c0`
  (отпечаток прежний — версия агента не меняется), `node --check` — 16 из 16.
- [x] 5.2 Стенд: выкладка, живой ход, вкладки «Источники», «Качество», «Архитектура» во временной консоли без входа.
  Проверка: ход с проверкой цифр, ошибок в консоли браузера нет.
  Итог: выкладка прошла; живой ход «Сколько сейчас стоит нефть WTI?» — проверка 4 из 4; «Источники» — названия рядов
  EIA и статусы журнала; «Качество» — категория «текущая цена» и подпись стоимости по профилю; «Архитектура» — 9
  инструментов MCP, две подписи схемы укорочены, чтобы не вылезать из блоков. Временная консоль и туннель убраны.

## Workflow follow-up

- `/opsx:verify cleanup-legacy-drift` и субагент `spec-guard`; `/opsx:archive`; один коммит.
