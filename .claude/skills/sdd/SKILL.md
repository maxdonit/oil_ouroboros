---
name: sdd
description: Единая точка входа для любой доработки «Нефтегазового аналитика на Ouroboros» по spec-driven процессу поверх OpenSpec CLI. Используй на запросы «добавь / поправь / почему / разберись / оптимизируй / подключи источник» по коду, конфигурации, UI или документам проекта — скилл классифицирует запрос и проводит его через спеку, реализацию, гейты и архивирование.
---

# sdd — spec-driven разработка поверх OpenSpec

## Принципы
- **Спека — источник правды о поведении.** Живые спеки `openspec/specs/<возможность>/spec.md` описывают, что система
  делает сейчас. Любое новое или изменённое поведение сначала описывается дельтой в `openspec/changes/<id>/`, затем
  кодируется; после архива живая спека снова равна коду.
- **Только через CLI.** Изменения создаются и проводятся `openspec` (команды `/opsx:*` и скиллы `openspec-*` —
  обёртки над ним), а не ручными файлами «по памяти»: `openspec new change`, `openspec instructions`,
  `openspec validate`, `openspec archive`.
- **Fail-closed гейт.** Красный ruff, pytest (включая `tests/test_consistency.py`) или `openspec validate --all
  --strict` — стоп и возврат к реализации.
- **Найденное по ходу** (ошибка рядом, дрейф) — отдельной доработкой, а не правкой «заодно».
- **Минимальный объём.** Строго в рамках запроса; старые режимы и имена удаляются в том же change (и добавляются в
  `LEGACY` в `tests/test_consistency.py`, чтобы не вернулись).

## Шаг 0 — классификация
- **question** («почему…», «как работает…», «что будет, если…») → путь A.
- **wording** — правка без изменения поведения: опечатка, формулировка в документе или UI, комментарий → путь B.
- **change** — новое или изменённое поведение: инструмент, источник данных, эндпоинт, параметр, промпт, регламент
  агента, состав сервисов, UI-функция, исправление ошибки → путь C.

## Путь A — вопрос (без изменений)
1. Найди возможность в таблице ниже и прочитай её спеку; затем код по ссылкам из спеки.
2. Ответь со ссылками `file:line` и именем требования спеки. Расхождение спеки и кода — назови его и предложи change.

## Путь B — правка без изменения поведения
1. Внеси правку. 2. Гейты (шаг C3). 3. Если формулировка встречается в спеке — поправь и её (`openspec validate`).

## Путь C — полный конвейер
1. **Исследование и предложение.** Неясно, как делать, — `/opsx:explore` (только чтение и замеры, без кода).
   Затем `/opsx:propose <описание>` → `openspec/changes/<id>/`: `proposal.md`, `design.md`, `specs/<спека>/spec.md`
   (дельта ADDED / MODIFIED / REMOVED), `tasks.md`. Проверка: `openspec validate <id> --strict`.
   Покажи пользователю proposal и дельту и **дождись согласия** — до него код не меняется.
2. **Реализация.** `/opsx:apply <id>` по `tasks.md`; задача отмечается `[x]` со строкой «Итог: что сделано и чем
   проверено». План по ходу меняется — сначала правка артефактов change (`/opsx:update`), потом код.
3. **Гейты** (из корня, Git Bash):
   ```
   .venv/Scripts/ruff.exe check scripts src tests && .venv/Scripts/ruff.exe format --check scripts src tests
   .venv/Scripts/python.exe -m pytest                       # ~1,5 мин, включает tests/test_consistency.py
   OPENSPEC_TELEMETRY=0 openspec validate --all --strict
   ```
   UI (`ui/*.js`): `node --check` копии файла с расширением `.mjs` (на `.js` синтаксис ES-модуля не проверяется),
   версия ассетов в `ui/index.html` поднята; вид — в браузере в консоли (она без пароля). Поведение агента —
   выборочный eval `oilo eval --only …` с согласия владельца.
4. **Ревью.** `/code-review` по диффу; изменения API, MCP-инструментов, промптов, регламента агента, секретов или
   сети — с вниманием к безопасности (агент читает недоверенный веб). Critical → шаг 2.
5. **Соответствие.** `/opsx:verify <id>` и субагент `spec-guard` (`.claude/agents/spec-guard.md`). DRIFT → поправить
   код или дельту.
6. **Архив.** `/opsx:archive <id>` — дельта вливается в `openspec/specs/`. Затем ловушки ниже, снова
   `openspec validate --all --strict` и `pytest tests/test_consistency.py`.
7. **Коммит** — один на change: код, документы, живые спеки и `openspec/changes/archive/<дата>-<id>/`. Сообщение — по-
   русски, нейтральное: что изменилось и зачем. Пуш — только по просьбе.

## Возможности и спеки
| Спека | Что описывает | Главный код |
|---|---|---|
| `agent-turn` | ход консоли через Ouroboros, финал хода, запись хода | `agent.py`, `console/turns.py`, `console/app.py` |
| `agent-bootstrap` | засев настроек и памяти агента | `bootstrap.py`, `ouroboros/` |
| `owner-channel` | профиль owner: UI Ouroboros, egress, утренняя сводка | `egress.py`, `schedules.py` |
| `mcp-tools` | контракт MCP и HTTP API сервиса инструментов | `mcp/server.py`, `tools_app.py` |
| `research-tool` | инструмент research, журнал прогонов графов | `graphs/research.py`, `graphs/kit.py`, `graphs/runs.py` |
| `web-search` | веб-поиск, провайдеры, доверие источников | `graphs/search_web.py`, `web/` |
| `evidence-ledger` | журнал доказательств R#/W#/F#, пакеты P# | `evidence/ledger.py`, `evidence/text.py` |
| `answer-verification` | проверка чисел ответа, финал хода | `evidence/numbers.py`, `evidence/verify.py`, `graphs/verify_answer.py` |
| `reports-base` | база отчётов агентств: наблюдатель, разбор, индекс, поиск | `reports/`, `rag/` |
| `price-forecast` | ряды цен, прогноз, сценарий шока | `forecast/`, `graphs/calc.py` |
| `price-quote` | текущие котировки сортов каталога (Brent, WTI, газ): Мосбиржа ISS, кэш, отказы, `price_quote` | `forecast/quotes.py`, `graphs/calc.py` |
| `data-sources` | панель источников: подключение, свежесть, доверие | `tools_app.py` (`/api/sources`), `ui/sources.js` |
| `llm-gateway` | LLM-шлюз: маршруты, деньги, журнал | `gateway/app.py`, `llm/pricing.py` |
| `llm-profiles` | LLM-профили gigachat / openrouter, вызов LLM инструментами | `scripts/llm_profile.py`, `deploy/llm/`, `llm/core.py` |
| `tool-catalog` | каталог `config/`: слоты, слои, проверка, версия агента | `catalog.py`, `console/fingerprint.py` |
| `deployment` | compose, сети, секреты, деплой и резервная копия | `docker-compose.yml`, `scripts/deploy.py`, `net.py` |
| `console-ui` | консоль без входа: простой режим и режим администратора, вкладки, карта системы | `console/app.py`, `console/deployment.py`, `ui/` |
| `agent-eval` | eval через агента, сравнение версий, заморозка данных | `evals/`, `freeze.py` |
| `demo-replay` | офлайн-демо записанных ходов | `console/demo.py`, `ui/demo.js` |

Новая возможность — новая спека (ADDED в дельте change); строка в этой таблице и в `spec-guard` — в том же change.

## Ловушки OpenSpec
- CLI — `openspec` версии 1.14.1 (`@fission-ai/openspec`), `OPENSPEC_TELEMETRY=0`; та же версия — в CI
  (`.github/workflows/ci.yml`). Артефакты пишутся по `openspec instructions <artifact> --change <id>`: там же
  контекст и правила проекта из `openspec/config.yaml`.
- Change без дельт спек (только документация или инструменты разработки) — `skip_specs: true` в
  `openspec/changes/<id>/.openspec.yaml`; в нём обязательны `schema: spec-driven` и `created: <дата>`.
- Архив создаёт новую спеку с заглушкой Purpose («TBD…») — сразу замени осмысленным текстом и повтори
  `openspec validate --all --strict` после архива, до коммита.
- MODIFIED-блок — полный текст требования со **всеми** его сценариями (имена сохраняются; устаревший сценарий
  переписывается, а не удаляется), иначе `validate --strict`: «omits scenario(s)». Удалить требование — REMOVED с
  **Reason** и **Migration**.
- Имя сценария переименовать в MODIFIED нельзя — поэтому в именах нет чисел и перечней («Состав allowed_tools»,
  а не «Восемь инструментов»); неудачное имя меняется правкой живой спеки до change.
- Архив change с MODIFIED меняет живую спеку: MODIFIED-блоки того же требования в других активных change устаревают —
  перенеси в них новые сценарии, иначе их `validate` и архив падают.
- Активный change описывает целевое состояние: план поменялся — правятся proposal, design, дельты и tasks
  (выполненные задачи не переписываются, к ним добавляется «Далее: …»).

## Команды
```
openspec list                         # активные change
openspec list --specs                 # живые спеки
openspec show <id|спека>              # содержимое
openspec status --change <id>         # готовность артефактов
openspec validate --all --strict      # всё: спеки и change
```

## Итог для пользователя
Тип запроса, id change, изменённые файлы, результат гейтов и ревью, статус спеки (OK / DRIFT), следующий шаг.
