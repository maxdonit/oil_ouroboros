# Proposal

## Why

Сверка 25.10.2026 (шесть независимых аудитов: мёртвый код, архитектура, веб-консоль, документы, две полные сверки
спек) показала: по поведению код и спеки совпадают, слои и границы сервисов в порядке, но за сутки трёх доработок
(GigaChat по умолчанию, две роли консоли, котировки четырёх сортов) остался мусор и дрейф:
- мёртвый код и запасные ветки, которые никто не задействует (параметры без вызовов, статусы, которых наблюдатель не
  выдаёт, поля трейса без читателей, недостижимые ветки роли `operator` в UI);
- тексты на прошлом составе: «8 инструментов» в compose, схеме и справочнике, «37 кейсов», OpenRouter как основной
  провайдер, Мосбиржи нет в описании выхода в интернет, цены EIA «Brent, WTI», устаревшая версия каталога;
- дублирование: список расчётных инструментов, маркеры подвала и префиксы меток источников описаны в нескольких местах;
- на профиле по умолчанию (GigaChat) сервис инструментов раз в сутки ходит на openrouter.ai за каталогом цен прямо
  внутри хода (до 20 с ожидания), хотя моделей GigaChat там нет.

## What Changes

- **Мёртвый код удаляется** (без изменения поведения): `baseline_as_of` в ключе F#, `Node.llm`, `ToolRun.events`,
  `check(download=)` и статус `missing`, `trigger="manual"`, `Edition.optional`, `supply_shock(world_supply_mbd=)`,
  `tier_title` в трейсе веба, запасные значения в `graphs/runs.py` и `web/credibility.py`, параметр `source` у
  `_run_turn`, неиспользуемые фикстуры тестов; статусы `indexed`, `uploaded`, `queued` из фильтров последней проверки
  (`mcp/server.py`, `ui/sources.js`).
- **Следы роли `operator` в UI**: недостижимые ветки `canManage` («Качество») и запасной путь графов «без доступа к
  ходам»; роли `user` больше не показываются ссылка и подсказка на вкладку «Ходы», которой у неё нет.
- **Одна точка правды**: список расчётных инструментов — `evals/checks.CALC_TOOLS` (консоль берёт его же); маркеры подвала
  и префиксы меток — константы `evidence/text.py`; набор `smoke.MCP` сверяется тестом с `mcp/server.TOOLS`; тест
  согласованности ловит устаревшее число инструментов MCP в документах, compose и схеме.
- **Цена GigaChat без сети**: `llm/pricing.py` для `GigaChat-*` сразу отдаёт 0 (Freemium), каталог OpenRouter не
  скачивается.
- **UI и тексты**: категории eval и проверки с русскими названиями (в том числе `quote`, `followup`, `honesty`,
  `advice`, `must_include`); статусы журнала наблюдателя `started`/`done`; фильтры источников хода `cli` и `smoke`;
  названия рядов EIA вместо кодов; подсказки `research_first` и упоминания котировок; цвет `completed_by_console`;
  подпись «роль неизвестна» при сбое `/api/me`; схема `ui/architecture.svg` под текущий состав.
- **Документы и комментарии** — под код: 9 инструментов, 42 кейса, Мосбиржа в выходе наружу и в прокси, три ряда EIA,
  профили по умолчанию и «пусто — только ядро» (с оговоркой про GigaChat), память 16,4 ГБ по умолчанию, цифры
  OpenRouter — с указанием профиля, правила хода, версия каталога без зашитого номера, ссылки плана на строки кода.
- **Тесты без сети**: `PRICES_REFRESH_HOURS=0` в `tests/conftest.py`.

Не входит (Non-goals) — отдельными доработками:
- ошибки поведения: относительные ссылки документов в консоли ведут на 404; ошибки tools и шлюза при проксировании
  консолью превращаются в 500; открытый режим консоли принимает запросы из контейнеров сетей `agent`/`oilo`; учёт
  токенов GigaChat в UI (денежные показатели — $0); `gpt2giga` за прокси организации; поле `reasoning` уходит в
  gpt2giga (проверить);
- архитектура крупнее уборки: два подсчёта «не в индексе», HTTP-клиенты в обход `net.py` и утечка клиента на
  `/api/health`, `edition_subst` и таблицы месяцев, общий lock котировок, синхронная запись журнала шлюза, цикл
  console↔evals, `chunk_tokens` кодировщика, метки как структура;
- ключ `meta.agent` и поле `LLM_PROFILE` остаются: их удаление меняет отпечаток каталога и вкладку «Параметры».

## Capabilities

### New Capabilities
(нет)

### Modified Capabilities
- `evidence-ledger`: ключ F# — дата данных `as_of` или сегодняшняя; у котировки — время котировки и дата спота.
- `research-tool`: статус прогона в журнале — `error` или `done` (запасного статуса из состояния графа нет).
- `mcp-tools`: в справке `list_reports` проблемой не считаются только статусы, которые наблюдатель выдаёт.
- `data-sources`: то же для плитки наблюдателя.
- `llm-profiles`: цена вызовов `GigaChat-*` — 0 без обращения к каталогу OpenRouter.

## Impact

- Код: `graphs/calc.py`, `graphs/kit.py`, `graphs/runs.py`, `reports/watch.py`, `reports/editions.py`,
  `rag/ingest.py`, `forecast/scenario.py`, `web/search.py`, `web/credibility.py`, `llm/pricing.py`,
  `evidence/text.py`, `agent.py`, `console/turns.py`, `console/app.py`, `console/smoke.py`, `console/demo.py`,
  `evals/checks.py`, `evals/runner.py`, `mcp/server.py`, `gateway/app.py` (docstring), `config.py` (описания),
  `forecast/data.py` (комментарии), `net.py`, `catalog.py`, `graphs/__init__.py`, `llm/core.py` (docstring'и),
  `cli.py` (подсказки).
- UI: `ui/evals.js`, `ui/graphview.js`, `ui/chat.js`, `ui/turnview.js`, `ui/monitor.js`, `ui/turns.js`,
  `ui/sources.js`, `ui/main.js`, `ui/util.js`, `ui/architecture.svg`, `ui/index.html`.
- Конфигурация: комментарии `config/current/llm.yaml`, `docker-compose.yml` (`x-links`, `x-externals`, `x-role`,
  `x-why`), `deploy/compose/proxy.yml`, `.env.example`.
- Документы: README, DESIGN, ADR, `docs/guide/*`, `docs/report.md`, `CLAUDE.md`.
- Тесты: `tests/conftest.py`, `tests/test_consistency.py`, тесты затронутых модулей. Версия агента не меняется
  (регламент, засев и каталог по смыслу те же; комментарии каталога в отпечаток не входят).
