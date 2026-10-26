# Proposal

## Why

Доступ к консоли сложнее, чем нужно для отладки, и в одном месте не совпадает с документами:

- `UI_OPEN=true` при пустом `UI_USERS` пускает без пароля с правами admin на любом адресе (`console/app.py:90`), а
  `.env.example`, `docs/guide/parameters.md` и `docs/guide/operations.md` обещают «только при `OILO_BIND=127.0.0.1`»;
- ролей три (`user`, `operator`, `admin`), а нужны две: простой режим для пользователя и полный для администратора.

## What Changes

- **Две роли.** `user` — простой режим: только Чат и Прогноз. `admin` — всё, в том числе Демо и Архитектура. Роль
  `operator` (все вкладки на чтение) удаляется; `scripts/init_env.py` создаёт логины `admin` и `user`.
- **BREAKING:** запись `UI_USERS` с ролью `operator` отбрасывается с предупреждением в журнале, как любая неизвестная
  роль: в `.env` её надо заменить на `admin` или `user`.
- **Открытый режим только локально.** `UI_OPEN=true` действует, только если `OILO_BIND=127.0.0.1`. При другом адресе
  консоль с пустым `UI_USERS` отвечает 503 «открытый режим (UI_OPEN=true) — только при OILO_BIND=127.0.0.1: задайте
  UI_USERS (scripts/init_env.py)», а фактический адрес пишет в журнал при старте.
- Compose передаёт консоли `OILO_BIND: ${OILO_BIND:-127.0.0.1}` — то же, что в её `ports`: консоль видит адрес, на
  котором опубликован порт, даже если `OILO_BIND` не задан в `.env`.
- Временная консоль без входа для проверки UI запускается с `-e OILO_BIND=127.0.0.1` — рецепт в
  `docs/guide/operations.md`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `console-ui`: «Вход по UI_USERS и закрытый режим по умолчанию» (две роли, `OILO_BIND`), «Роли, вкладки и права по
  эндпоинтам» (без `operator`), «Вкладки «Ходы» и «Мониторинг»», «Вкладка «Архитектура»: карта системы и графы
  инструментов», «Вкладка «Источники»» (оператор → администратор).
- `data-sources`: «Доступ через консоль» — только `admin`.
- `agent-turn`: «Разговор принадлежит пользователю» — все разговоры видит `admin`.
- `agent-eval`: «API и вкладка «Качество»» — вкладка только у `admin`.
- `demo-replay`: «API проигрывателя без агента и LLM» — демо только у `admin`.
- `deployment`: «Создание .env» — `UI_USERS` с `admin` и `user`.

## Non-goals

- Проверка адреса клиента запроса и защита открытого режима от соседних контейнеров: открытый режим — для отладки на
  своей машине.
- Вход по паролю, права `admin` и `/api/health` не меняются; `ui/*.js` не меняются — вкладки строятся по `tabs`
  из `/api/me`.

## Impact

- Код: `src/oil_ouroboros/console/app.py` (роли, проверка `OILO_BIND`), `src/oil_ouroboros/config.py` (поле
  `oilo_bind`, описания), `scripts/init_env.py`, `ui/index.html` (подсказка о ролях, версия ассетов).
- Compose: `docker-compose.yml`, сервис `console` — `OILO_BIND` в `environment`.
- Тесты: `tests/test_console.py`, `tests/conftest.py`, `tests/test_consistency.py` (`operator` — в `LEGACY`).
- Документы: `.env.example`, `README.md`, `docs/guide/parameters.md`, `docs/guide/operations.md`,
  `.claude/agents/spec-guard.md`.
- Стенд: в `.env` стенда логин `operator` перестанет входить — заменить роль на `admin` или `user`. Версия агента не
  меняется.
