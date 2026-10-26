# Эксплуатация: запуск, проверка, логи, резервная копия, обновление, сбои

Руководство для того, кто разворачивает и сопровождает систему. Что делает каждый сервис, его сети, тома, порты,
порядок запуска, проверка здоровья, ресурсы и секреты — вкладка консоли «Архитектура» → «Карта системы» → режим
«Развёртывание» (те же данные — в [docker-compose.yml](../../docker-compose.yml)). Все параметры — в
[parameters.md](parameters.md). Команды ниже выполняются в каталоге проекта на сервере; на машине оператора то же
делает `python scripts/deploy.py` (цель — `DEPLOY_SSH` и `DEPLOY_DIR` в `.env`).

## 1. Требования к хосту

| Что | Сколько |
|---|---|
| CPU | от 8 ядер (кодировщику 4 — `ENCODER_THREADS`, инструментам 4 — `TOOLS_CPUS`, агенту 2); GPU не нужен |
| Память | от 16 ГБ: сумма лимитов по умолчанию (ядро и профили owner, gigachat) — 16,4 ГБ, из них ядро — 15,5 ГБ (encoder 5, ouroboros 4, tools 3, qdrant 2, console 1, gateway 0,5), gpt2giga 0,5, schedules, egress и ouroboros-ui — 0,45. Лимит — потолок контейнера, а не обычный расход |
| Диск | ~25 ГБ: образы, модели HuggingFace ~4,8 ГБ, индекс, журналы |
| Сеть | на время сборки — GitHub (образ Ouroboros), PyPI, HuggingFace; в работе — провайдер LLM, веб-поиск, сайты агентств, EIA, котировки Мосбиржи (iss.moex.com). Без прямого выхода — `deploy/compose/proxy.yml` |
| Порты на хосте | консоль `OILO_PORT` (38620), UI Ouroboros `OUROBOROS_UI_PORT` (38630, профиль owner) на адресе `OILO_BIND` |
| ПО | Docker с Compose v2; Python 3.10+ только для `scripts/` |

Внутренние порты наружу не публикуются: tools 8000, console 8000, gateway 8030, encoder 8020, ouroboros 8765,
qdrant 6333, gpt2giga 8090, egress 3128.

## 2. Первый запуск и готовность

```bash
python scripts/init_env.py          # .env: внутренние токены; консоль без пароля, UI Ouroboros — ouroboros
# впишите в .env GIGACHAT_CREDENTIALS (и TAVILY_API_KEY, TELEGRAM_BOT_TOKEN, если есть)
docker compose up -d                # ядро и профили из COMPOSE_PROFILES (по умолчанию owner,gigachat)
docker compose ps                   # все сервисы ядра — healthy, bootstrap — exited (0)
```

Сроки на CPU-сервере: сборка образов ~15 мин, модели ~6 мин, база отчётов ~1,5 ч (кодировщик строит её сам). Агент
отвечает и во время сборки базы — по готовым выпускам.

Готовность по слоям:

| Проверка | Как | Что значит |
|---|---|---|
| консоль | `curl -s http://127.0.0.1:38620/api/health` | `ok` — шлюз и Ouroboros отвечают, а tools сам `ok`; `degraded` — кто-то из них не отвечает или tools не `ok`: база отчётов ещё строится (на первом запуске — обычное дело) или каталог невалиден (подробности — в консоли после входа) |
| инструменты | `docker compose exec tools curl -s localhost:8000/api/health` | `ok`; `degraded` — база отчётов ещё строится; `invalid_catalog` (503) — ошибка в `config/`, Ouroboros не стартует |
| кодировщик | `docker compose exec encoder curl -s localhost:8020/health` | `loading` — модели грузятся; `error` (503) — загрузка не удалась, повтор раз в минуту |
| шлюз | `docker compose exec gateway curl -s localhost:8030/health` | потрачено и лимит денег, отказы |
| база отчётов | `docker compose exec tools oilo reports` | выпуски, файлы, фрагменты в индексе, очередь |
| живой ход | `docker compose exec console oilo smoke` | один ход через агента и проверки того, что видела модель (GigaChat Freemium — бесплатно, OpenRouter ~$0.01) |

## 3. Профили и дополнения

По умолчанию (`.env.example`) `COMPOSE_PROFILES=owner,gigachat`; пусто — только ядро. На LLM-профиле gigachat без
профиля compose `gigachat` у шлюза нет модели: только ядро — после `python scripts/llm_profile.py openrouter`.

- `gigachat` — модели GigaChat через `gpt2giga` (ключ `GIGACHAT_CREDENTIALS`), LLM-профиль по умолчанию. На OpenRouter:
  `python scripts/llm_profile.py openrouter` (ключ `OPENROUTER_API_KEY`; профиль `gigachat` уходит из
  `COMPOSE_PROFILES`), затем `docker compose up -d && docker compose restart ouroboros`; обратно —
  `python scripts/llm_profile.py gigachat`.
- `owner` — канал владельца: `ouroboros-ui`, `egress` (Telegram, навыки), `schedules` (утренняя сводка). Настройка —
  [owner.md](owner.md); без `TELEGRAM_BOT_TOKEN` остаётся собственный UI Ouroboros.
- Хост без прямого выхода в интернет — `COMPOSE_FILE=docker-compose.yml:deploy/compose/proxy.yml`,
  `OUTBOUND_PROXY=http://proxy:3128`, `UPSTREAM_PROXY=host:port` и, если прокси доступен только из другой
  docker-сети, `UPSTREAM_PROXY_NETWORK`. gpt2giga ходит к GigaChat напрямую.

## 4. Логи и журналы

```bash
docker compose logs -f --tail=200 tools          # любой сервис: tools, console, gateway, encoder, ouroboros, …
```

| Что | Где (том) | Как смотреть |
|---|---|---|
| ходы агента: вопрос, раунды, инструменты, проверка, деньги | `console_var/turns.sqlite` | консоль → «Ходы», «Мониторинг» |
| прогоны инструментов (узлы графов, LLM субагентов) | `tools_var/runs.sqlite` | консоль → «Ходы» → прогон |
| журнал доказательств R#/W#/F# и пакеты P# | `tools_var/evidence.sqlite` | ссылки в ответах и ходах |
| вызовы MCP | `tools_var/mcp_audit.jsonl` | `docker compose exec tools tail var/mcp_audit.jsonl` |
| вызовы LLM, деньги, тела запросов | `gateway_var/gateway/ledger.sqlite`, `gateway_var/gateway/bodies` | консоль → «Мониторинг», ход → вызовы LLM |
| наблюдатель выпусков | `tools_var/reports/journal.jsonl`, `state.json` | консоль → «Источники» → журнал |
| сам агент | `ouroboros_data` (журналы Ouroboros) | UI Ouroboros (профиль owner) или `docker compose logs ouroboros` |

## 5. Деньги

Лимитов два, оба копятся за всё время:
- **шлюз** — `GATEWAY_BUDGET_USD` (и по вызывающим — `GATEWAY_CALLER_BUDGETS`): при превышении любой вызов LLM
  получает 402. Поднять: значение в `.env`, затем `docker compose up -d gateway`;
- **Ouroboros** — `OUROBOROS_TOTAL_BUDGET` и потолок на задачу `OUROBOROS_PER_TASK_COST_USD`: на нуле задачи встают на
  паузу, консоль закрывает такой ход. Засев переписывает их из `.env` при каждом запуске: после правки
  `docker compose up -d --force-recreate bootstrap ouroboros`.

Сколько потрачено — консоль → «Мониторинг» или `/health` шлюза.

## 6. Тома, резервная копия, восстановление

Что в каких томах — вкладка «Архитектура» → режим «Развёртывание» → нижняя полоса. Невосстановимое: журналы и ходы,
выпуски от наблюдателя, кэш разбора PDF, данные агента (`ouroboros_data`). Индекс Qdrant и модели пересоздаются
сами.

```bash
python scripts/deploy.py backup                  # с машины оператора: архив в backups/<env>-<дата>.tgz
```

Восстановление на сервере (архив скопирован в `/tmp/oilo.tgz`; проект — `COMPOSE_PROJECT_NAME`, по умолчанию `oilo`):

```bash
docker compose stop
docker run --rm -v /tmp:/in \
  -v oilo_tools_var:/b/tools_var -v oilo_console_var:/b/console_var \
  -v oilo_gateway_var:/b/gateway_var -v oilo_ouroboros_data:/b/ouroboros_data \
  alpine:3.20.3 sh -c 'cd /b && tar xzf /in/oilo.tgz && find . -name "*.sqlite.bak" | while read f; do mv "$f" "${f%.bak}"; done'
docker compose up -d                             # кодировщик сам перестроит индекс по восстановленному кэшу разбора
```

Остановить всё, сохранив данные, — `docker compose down`; удалить и тома (чистый деплой) —
`python scripts/deploy.py down --volumes`.

## 7. Обновление

| Что меняется | Что сделать |
|---|---|
| код проекта, `config/`, `data/reports`, `ui/` (всё это в образах) | `docker compose up -d --build` (на стенде — `python scripts/deploy.py up`); проверка каталога — `docker compose exec tools oilo catalog check` |
| `.env` | `docker compose up -d` пересоздаст сервисы с изменённым окружением; ключи Ouroboros (`MCP_TOKEN`, `GATEWAY_TOKEN_OUROBOROS`, `OUROBOROS_PASSWORD`, бюджет) — ещё `--force-recreate bootstrap ouroboros` |
| состав инструментов MCP | `docker compose restart ouroboros`: его рабочий процесс читает список инструментов один раз при старте |
| релиз Ouroboros | новый коммит в `build.context` сервиса `ouroboros`, сверить `ouroboros/disabled_tools.txt` с новыми встроенными инструментами, `docker compose build ouroboros && docker compose up -d ouroboros`, затем `oilo smoke` и eval |
| модель агента или инструментов | `python scripts/llm_profile.py <профиль>` или `OUROBOROS_MODEL` / `LLM_MODEL_SUBAGENT` в `.env`, затем `docker compose up -d && docker compose restart ouroboros` |

Каждое изменение меняет версию агента (отпечаток в консоли): метрики «Мониторинга» и eval сравниваются по версиям.

## 8. База отчётов

| Задача | Команда |
|---|---|
| проверить сайты агентств и скачать новые выпуски | `docker compose exec tools oilo reports --check` |
| загрузить выпуск вручную (сайт OPEC закрыт Cloudflare) | `docker compose cp файл.pdf tools:/tmp/` и `docker compose exec tools oilo reports --upload /tmp/файл.pdf` |
| добавить в индекс то, чего там нет | `docker compose exec tools oilo ingest` |
| перестроить индекс целиком (смена модели эмбеддингов, размера фрагмента) | `docker compose exec tools oilo ingest --recreate` (~50 мин с кэшем разбора); заново разобрать PDF — удалить `tools_var/parsed` |
| обновить цены EIA | `docker compose exec tools oilo prices refresh` |

Пока идёт eval, данные заморожены (`var/eval.lock`, не дольше 3 ч): новые выпуски ждут в очереди, цены не
обновляются. Как добавить новый источник — [sources.md](sources.md).

## 9. Типовые сбои

| Симптом | Причина | Что сделать |
|---|---|---|
| `ouroboros` не стартует | ждёт здоровый `tools` (а тот — шлюз) или `bootstrap` упал | `docker compose ps`, `docker compose logs bootstrap tools gateway` |
| `tools`: `invalid_catalog` | ошибка в `config/current/*.yaml` | `docker compose exec tools oilo catalog check` — список проблем с файлом и полем |
| агент отвечает без инструментов | `tools` был недоступен при старте Ouroboros | `docker compose restart ouroboros` |
| ответы с ошибкой 402 | исчерпан лимит шлюза | раздел 5 |
| ход «на паузе» | потолок денег Ouroboros на задачу или общий бюджет | раздел 5 |
| новых выпусков OPEC нет, статус «закрыто проверкой от ботов» | Cloudflare на opec.org | ручная загрузка (раздел 8) |
| `encoder`: `error`, модели не грузятся | нет выхода к HuggingFace | `OUTBOUND_PROXY` или `deploy/compose/proxy.yml` |
| веб-поиск пуст или медленный | нет ключа Tavily или кончился лимит — работает DuckDuckGo | `TAVILY_API_KEY` / `BRAVE_API_KEY` в `.env` |
| Telegram молчит | хост не в `EGRESS_ALLOW` или не включён профиль owner | `docker compose logs egress` — отказы пишутся с именем хоста |
| GigaChat: ошибки авторизации или очередь | неверный `GIGACHAT_CREDENTIALS` или один поток во Freemium | ключ в `.env`, `GIGACHAT_MAX_CONNECTIONS` |

## 10. Безопасность

- У каждого сервиса только свои секреты: чужие ключи `.env` обнуляет compose (`x-no-secrets`), это проверяет тест;
  имена секретов сервиса — в паспорте режима «Развёртывание».
- Порты публикуются только на `OILO_BIND`. Консоль — без паролей и ролей (решение владельца: после установки в неё
  должно быть легко попасть): по шаблону `.env` (`OILO_BIND=0.0.0.0`) она на всех адресах хоста и видна всем в его
  сети, включая запуск eval; только эта машина — `OILO_BIND=127.0.0.1`. Потолок трат — лимит шлюза
  `GATEWAY_BUDGET_USD`. В интернет пробрасывайте только порт консоли (38620).
- UI Ouroboros (профиль owner) — по паролю `OUROBOROS_PASSWORD`, по умолчанию `ouroboros`: его знает каждый, кто
  читал README, а по шаблону `.env` UI тоже виден в сети хоста: впишите свой пароль и пересоздайте сервисы (раздел 7);
  порт 38630 в интернет не пробрасывайте.
- Агент живёт в сети без выхода в интернет; канал владельца выходит наружу только к хостам `EGRESS_ALLOW`.
  Риски и меры для продуктива — [owner.md](owner.md#риски-канала-владельца).
- Смена токенов: новые значения в `.env` (или `python scripts/init_env.py` в новый файл — пароль UI Ouroboros там
  снова `ouroboros`, перенесите свой), затем `docker compose up -d --force-recreate`.
