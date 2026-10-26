# Tasks

## 1. Пароль по умолчанию

- [x] 1.1 `.env.example`: `OUROBOROS_PASSWORD=ouroboros`, комментарий «пароль UI Ouroboros (профиль owner) и его
  API; поменяйте при открытии в сеть». `scripts/init_env.py`: `OUROBOROS_PASSWORD` убран из `GENERATED`; в конце —
  адрес UI Ouroboros и пароль из `.env`. Проверка: тест `init_env` — из `.env.example` ровно шесть токенов, в тексте
  `OUROBOROS_PASSWORD=ouroboros`; вывод `main()` на временном каталоге содержит адрес `:38630` и пароль (сценарий
  «Первый запуск»).
  Итог: пароль перенесён в раздел «Впишите сами» `.env.example` со значением `ouroboros`; `init_env.py` генерирует
  шесть токенов и печатает «UI Ouroboros (профиль owner) — http://127.0.0.1:38630, пароль ouroboros», а при пустом
  пароле — «впишите OUROBOROS_PASSWORD в .env — без него агент не стартует»; тест
  `test_init_env_creates_tokens_and_names_the_ouroboros_password` (шаблон и `main()` на временном каталоге).
- [x] 1.2 Документы: README (быстрый старт — адрес и пароль UI Ouroboros, как сменить), `docs/guide/owner.md`
  (пароль по умолчанию), `docs/guide/parameters.md` (строка `OUROBOROS_PASSWORD`: значение по умолчанию, «генерирует
  `init_env.py`» — только для токенов). Проверка: `tests/test_consistency.py`.
  Итог: README (адрес, пароль, ссылка на порядок смены в `operations.md` §7), `owner.md`, строка параметра,
  статус плана; `tests/test_consistency.py` — зелёный.
  Далее (по отчёту spec-guard): «пароли генерируются сами» убрано из README и `operations.md` §2; в `operations.md`
  §10 и `owner.md` — пароль по умолчанию и смена при открытии UI в сеть, оговорка про `init_env.py` при смене
  токенов; сценарий дельты «Пароль UI стёрт» и его проверка в тесте вместе со строкой адреса консоли.

## 2. Проверка целиком

- [x] 2.1 Гейты: `ruff check` и `ruff format --check`, `pytest`, `openspec validate --all --strict`. Проверка: всё
  зелёное, версия каталога прежняя.
  Итог: ruff и формат чисто, `pytest` — 229 passed, `openspec validate --all --strict` — 20 из 20, каталог
  `current@2026.10.24-3#b21faf41c6c0` (прежний).
- [x] 2.2 Стенд:
  - копия `.env`;
  - `OUROBOROS_PASSWORD=ouroboros` в локальном и серверном `.env`;
  - `python scripts/deploy.py up`, затем `docker compose up -d --force-recreate bootstrap ouroboros`;
  - засев прошёл без ошибок;
  - консоль: здоровье Ouroboros `ok`, живой ход проходит;
  - `GET` UI Ouroboros стенда (порт 38630) без пароля — страница входа (401), сценарий «Пароль по умолчанию».

  Пароль в браузер не вводится. Проверка: журнал засева, ответы API, ход.
  Итог: копия `.env` стенда — `.env.bak-2026-10-25-ouroboros-password`; `OUROBOROS_PASSWORD=ouroboros` в локальном и
  серверном `.env` (`deploy.py up`: «изменены: OUROBOROS_PASSWORD»), засев и Ouroboros пересозданы, засев — код 0,
  Ouroboros `healthy`; без пароля `:38630` — 401 и для страницы, и для API; консоль: здоровье Ouroboros `ok`, ход
  «Сколько сейчас стоит нефть Brent?» — `price_quote` → `verify_answer`, 8 из 8 цифр, 21 с; расписания читают API.
  Попутно: два вопроса о Henry Hub в чате прошли без инструментов (модель в запросе видела все 12) — не связано с
  паролем, отдельная доработка.

## Workflow follow-up

- `/opsx:verify ouroboros-ui-simple-password` и субагент `spec-guard`; `/opsx:archive`; один коммит.
