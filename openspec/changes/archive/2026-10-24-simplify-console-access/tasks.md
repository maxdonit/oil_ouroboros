# Tasks

## 1. Две роли: user и admin

- [x] 1.1 `src/oil_ouroboros/console/app.py`: `ROLES = ("user", "admin")`; `ROLE_TABS`: `user` — `chat`,
  `forecast`, `admin` — все вкладки; `allowed()`: `user` сверх общих адресов — только `GET /api/forecast*`, `admin` —
  всё; все разговоры видит только `admin`; комментарий о ролях; `config.py` — описание `ui_users`; проверка —
  `pytest tests/test_console.py`.
  Итог: `ROLES`, `ROLE_TABS`, `allowed()` и `_own()` — две роли; проверено `pytest tests/test_console.py` (12 passed).
- [x] 1.2 `scripts/init_env.py`: `UI_USERS=admin:<пароль>:admin,user:<пароль>:user`; `ui/index.html` — подсказка о
  ролях, версия ассетов `?v=` +1; проверка — тест в `tests/test_console.py`: `init_env.fill("UI_USERS=")` даёт
  логины `admin` и `user` с ролями `admin` и `user`.
  Итог: `init_env.py` создаёт `admin` и `user`, подсказка в `ui/index.html` — две роли, версия `20261024.11`;
  проверено тестом `test_init_env_creates_admin_and_user_logins`.
- [x] 1.3 `tests/test_console.py`: роли и вкладки без `operator` (у `user` — 403 на `GET /api/params`,
  `POST /api/evals/run`, `/api/demo`, `/api/about`, `/docs/*`; у `admin` — 200; чужой разговор открыт `admin`);
  проверка — `pytest tests/test_console.py`.
  Итог: новый тест `test_user_gets_simple_mode_and_admin_everything` (403 и текст отказа на 8 адресах), демо —
  только admin, мониторинг разговоров — admin; `pytest tests/test_console.py` — 12 passed. Далее: `LEGACY` — после
  архива (до него живые спеки ещё описывают `operator`), см. «Workflow follow-up».

## 2. Открытый режим только при OILO_BIND=127.0.0.1

- [x] 2.1 `src/oil_ouroboros/config.py`: поле `oilo_bind` (по умолчанию `127.0.0.1`), описание `ui_open`;
  `console/app.py`: открытый режим только при `OILO_BIND == "127.0.0.1"`, иначе 503 с причиной из дельты, в `main` —
  предупреждение с адресом; `docker-compose.yml`: `OILO_BIND: ${OILO_BIND:-127.0.0.1}` у `console`; проверка — тесты 2.2.
  Итог: `oilo_bind` в `Settings`, `_local()` в `console/app.py` (middleware и `main`), строка в compose; проверено
  тестами 2.2.
- [x] 2.2 `tests/conftest.py`: `OILO_BIND=127.0.0.1`; `tests/test_console.py`: при `0.0.0.0` и адресе в сети — 503 с
  причиной, `/api/health` без входа — только `{status, version}`; при `127.0.0.1` — `role == "admin"`; выражение
  `OILO_BIND` в `environment` консоли совпадает с адресом в её `ports`; проверка — `pytest tests/test_console.py`.
  Итог: `test_open_mode_only_when_published_on_loopback` (503 и текст без адреса на `0.0.0.0` и адресе в сети,
  `/api/health` — `{status, version}`, журнал `main`, на `127.0.0.1` — admin) и
  `test_compose_gives_console_its_publish_address`; `pytest tests/test_console.py` — 14 passed.
  Далее: при verify добавлен `test_wrong_password_or_unknown_role_is_401` (сценарии «Неверный пароль или запись
  без роли», «UI_OPEN при заданных логинах»); `pytest tests/test_console.py` — 15 passed.

## 3. Документы

- [x] 3.1 `.env.example`, `README.md`, `docs/guide/parameters.md` (`UI_USERS`, `UI_OPEN`, `OILO_BIND`),
  `docs/guide/operations.md` (неполадка «503 из-за OILO_BIND», правило и рецепт временной консоли без входа с
  `-e OILO_BIND=127.0.0.1`), `.claude/agents/spec-guard.md` (роли); проверка — `pytest tests/test_consistency.py`.
  Итог: роли и `OILO_BIND` в `.env.example`, `README.md`, `parameters.md`, `operations.md` (строка неполадок и
  рецепт временной консоли), `spec-guard.md`; `pytest tests/test_consistency.py` — 7 passed.

## 4. Гейты

- [x] 4.1 `ruff check scripts src tests` и `ruff format --check scripts src tests` — без замечаний; `node --check`
  не нужен (`ui/*.js` не меняются).
  Итог: `ruff check` — All checks passed, `ruff format --check` — 88 files already formatted.
- [x] 4.2 `pytest` целиком (с `tests/test_consistency.py`) — зелёный.
  Итог: `pytest` — 204 passed за 86 с.
- [x] 4.3 `openspec validate --all --strict` — без ошибок.
  Итог: `openspec validate --all --strict` — 20 passed, 0 failed.

## Workflow follow-up

- `/opsx:verify simplify-console-access` и субагент `spec-guard`; DRIFT — правка кода или дельты.
- `/opsx:archive simplify-console-access`, затем `\boperator\b` в `LEGACY` `tests/test_consistency.py`,
  `openspec validate --all --strict` и `pytest tests/test_consistency.py`.
- Один коммит на change по-русски, без пуша.
