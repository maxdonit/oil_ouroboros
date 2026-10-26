## 1. Шаблон окружения

- [x] 1.1 `.env.example`: `OILO_BIND=0.0.0.0`; комментарий — консоль без пароля и UI Ouroboros по паролю видны в сети
  хоста, `127.0.0.1` — только эта машина; у `OUROBOROS_PASSWORD` — напоминание сменить пароль и не пробрасывать 38630
  наружу. Проверка: `python scripts/init_env.py` во временный каталог даёт `.env` с `OILO_BIND=0.0.0.0`.
  Итог: `OILO_BIND=0.0.0.0` с комментарием про сеть хоста и `127.0.0.1`; у `OUROBOROS_PASSWORD` — сменить пароль, 38630
  в интернет не пробрасывать. `init_env.py` во временном каталоге — `OILO_BIND=0.0.0.0`.

## 2. Документы

- [x] 2.1 `INSTALL.md` (адреса консоли с этой машины и из сети), `docs/guide/parameters.md` (строка `OILO_BIND`),
  `docs/guide/operations.md` (раздел «Безопасность»), `docs/guide/owner.md` (пароль UI в сети). Проверка:
  `pytest tests/test_consistency.py`.
  Итог: `INSTALL.md` (адрес из сети, только 38620 наружу, несколько интерфейсов), строка `OILO_BIND` в
  `parameters.md`, «Безопасность» в `operations.md`, риски пароля в `owner.md`; `test_consistency.py` зелёный.

## 3. Проверка целиком

- [x] 3.1 Гейты: `ruff check` и `ruff format --check`, `pytest`, `openspec validate --all --strict`. Проверка: всё
  зелёное.
  Итог: ruff, pytest и `openspec validate --all --strict` — зелёные.
- [x] 3.2 Стенд: `OILO_BIND=0.0.0.0` в `.env`, `docker compose up -d console ouroboros-ui`; консоль отвечает 200 по
  обоим адресам хоста. Проверка: `curl` на адрес физической карты и адрес моста.
  Итог: на стенде `OILO_BIND=0.0.0.0`, `console` и `ouroboros-ui` пересозданы; консоль — 200 на `192.168.31.180:38620` и
  `192.168.41.1:38620`.
