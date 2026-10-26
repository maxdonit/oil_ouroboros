# Установка за 5 шагов

Нужно: Docker с Compose v2, Python 3.10+, 16 ГБ памяти, ~25 ГБ диска, выход в интернет (на время сборки — и GitHub:
образ Ouroboros собирается из его репозитория). GPU не нужен. Docker Desktop (Windows, macOS): в Settings → Resources
дайте ему не меньше 16 ГБ памяти — кодировщику нужно 5 ГБ.
Ключ LLM — один из двух: GigaChat (по умолчанию; Freemium бесплатен, но это квота токенов из личного кабинета — когда
она кончится, GigaChat начнёт отвечать ошибкой) или OpenRouter (платно, потолок трат — `GATEWAY_BUDGET_USD`).

## 1. Скачать

```bash
git clone https://github.com/maxdonit/oil_ouroboros.git
cd oil_ouroboros
```

## 2. Создать `.env`

```bash
python scripts/init_env.py
```

Откройте `.env` и впишите ключ в раздел «1. Впишите сами»:
- **GigaChat** (по умолчанию) — `GIGACHAT_CREDENTIALS` (ключ авторизации из личного кабинета developers.sber.ru);
- **или OpenRouter** — `OPENROUTER_API_KEY`, затем `python scripts/llm_profile.py openrouter`.

По желанию: `TAVILY_API_KEY` (веб-поиск лучше, без него — DuckDuckGo), `TELEGRAM_BOT_TOKEN` (бот владельца).

## 3. Запустить

```bash
docker compose up -d
```

Первый запуск: сборка образов ~15 мин, модели ~6 мин, база отчётов ~1,5 ч в фоне. Агент отвечает и во время сборки
базы — по уже готовым выпускам.

## 4. Проверить

```bash
docker compose ps                          # сервисы ядра — healthy; bootstrap и schedules — exited (0)
docker compose exec tools oilo reports     # выпуски отчётов и сколько фрагментов уже в индексе
docker compose exec console oilo smoke     # один живой ход через агента
```

Консоль — http://127.0.0.1:38620 (с другой машины — http://адрес-сервера:38620), без пароля: чат и прогноз сразу,
все вкладки — кнопкой «Режим администратора». UI Ouroboros — порт 38630, пароль `ouroboros`.

Консоль и UI видны всем в сети сервера (`OILO_BIND=0.0.0.0` в `.env`); только эта машина — `OILO_BIND=127.0.0.1`.
Консоль — без входа: кто её открыл, тот может и запустить eval (квота GigaChat или деньги OpenRouter в пределах
`GATEWAY_BUDGET_USD`). Открывая её в интернет как демо, пробрасывайте только порт 38620; порт 38630 — никогда: у
агента в UI Ouroboros есть shell.

## 5. Спросить и прогнать

```bash
docker compose exec console oilo ask "Спрогнозируй цену Brent на 3 месяца"
docker compose exec console oilo eval --only R01 F01 O01               # выборочный eval
docker compose exec console oilo eval --label full --report var/eval_report.md   # весь набор (42 кейса)
docker compose exec console oilo demo                                  # 7 демо-диалогов → var/demo_dialogs.md
docker compose exec tools oilo eval-retrieval --label check            # поиск по отчётам, без агента
docker compose cp console:/app/var/eval_report.md .                    # отчёт и демо — из контейнера к себе
docker compose cp console:/app/var/demo_dialogs.md .
```

Весь набор eval: на OpenRouter ~$0.35 и ~22 мин, на GigaChat — ~4 млн токенов квоты Freemium (~11 мин). Итоги — вкладка
консоли «Качество».

## Остановить и начать заново

```bash
docker compose down              # остановить, данные сохранятся
docker compose down --volumes    # удалить всё (индекс, ходы, память агента) — следующий запуск с нуля
```

## Удалённый сервер

С вашей машины по ssh — тот же код и тот же `.env`. Впишите в локальный `.env` `DEPLOY_SSH=user@host`, `DEPLOY_DIR`
(каталог с правом записи, например `~/oil_ouroboros`; в шаблоне — `/opt/oil-ouroboros`) и при необходимости
`DEPLOY_KEY=~/.ssh/ключ`, затем:

```bash
python scripts/deploy.py up                # копия кода + .env на сервер, сборка, запуск
python scripts/deploy.py ps                # состояние сервисов
python scripts/deploy.py sh "docker compose exec -T console oilo smoke"
python scripts/deploy.py backup            # данные сервера → backups/ у вас
python scripts/deploy.py down --volumes    # удалить всё на сервере
```

Консоль на сервере — http://адрес-сервера:38620. Если у сервера несколько адресов, а проброс роутера ведёт на один из
них, оставьте `OILO_BIND=0.0.0.0`: адрес одного интерфейса принимает запросы только на нём.

## Если что-то не так

| Симптом | Что сделать |
|---|---|
| `ouroboros` не стартует | `docker compose logs bootstrap tools gateway` — он ждёт здоровых `tools` и шлюз |
| ответы без источников, агент не зовёт инструменты | `docker compose restart ouroboros` |
| `encoder` в `error`, модели не грузятся | нет выхода к HuggingFace: прокси — `deploy/compose/proxy.yml` |
| GigaChat: ошибка авторизации | проверьте `GIGACHAT_CREDENTIALS` и `GIGACHAT_SCOPE` в `.env`, затем `docker compose up -d` |

Подробно: эксплуатация — [docs/guide/operations.md](docs/guide/operations.md), параметры —
[docs/guide/parameters.md](docs/guide/parameters.md), как устроено — [README.md](README.md).
