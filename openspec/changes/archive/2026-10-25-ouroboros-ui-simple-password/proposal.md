# Proposal

## Why

Пароль собственного UI Ouroboros (профиль `owner`, порт 38630) сейчас — случайные 48 hex-символов. Их генерирует
`scripts/init_env.py`, и владелец должен искать их в `.env`. Владелец решил пароль оставить, но сделать его простым и
известным сразу после установки. Механизм Ouroboros не трогаем: без обходов его проверки входа и без переключателей
«доверенной сети».

## What Changes

- Пароль UI Ouroboros по умолчанию — `ouroboros`: значение стоит в `.env.example`, его же называет README.
- `scripts/init_env.py` больше не генерирует `OUROBOROS_PASSWORD`, пароль берётся из шаблона. Скрипт в конце печатает
  адрес UI Ouroboros и пароль: «UI Ouroboros (профиль owner) — http://127.0.0.1:38630, пароль ouroboros
  (OUROBOROS_PASSWORD в .env)».
- Сменить пароль — вписать другой `OUROBOROS_PASSWORD` в `.env` и пересоздать сервисы, как сейчас
  (`docs/guide/operations.md`).
- Засев, Ouroboros, консоль и расписания работают как раньше: пароль непустой, засев кладёт его в
  `OUROBOROS_NETWORK_PASSWORD`, консоль и расписания шлют его заголовком `X-Ouroboros-Password`.
- Существующие `.env` не меняются: `init_env.py --fill` дописывает только пустые значения.
- На стенде пароль тоже становится `ouroboros` (перед этим — копия `.env`). Сервисы с паролем пересоздаются вместе:
  засев, Ouroboros, консоль, расписания.

Не входит (Non-goals):
- UI Ouroboros без пароля (отклонено владельцем).
- Межсервисные токены (`MCP_TOKEN`, `GATEWAY_TOKEN_*`, `TOOLS_API_TOKEN`, `GPT2GIGA_API_KEY`): их никто не вводит
  руками, они по-прежнему случайные.

## Capabilities

### New Capabilities
(нет)

### Modified Capabilities
- `owner-channel`: вход в UI Ouroboros — паролем `OUROBOROS_PASSWORD`, по умолчанию `ouroboros`.
- `deployment`: `init_env.py` генерирует шесть внутренних токенов, пароль UI Ouroboros берёт из шаблона и печатает
  вместе с адресом.

## Impact

- Код: `scripts/init_env.py` (`GENERATED` без `OUROBOROS_PASSWORD`, итоговая подсказка).
- Конфигурация: `.env.example` (`OUROBOROS_PASSWORD=ouroboros` и комментарий).
- Тесты: `tests/test_console.py` (`init_env`: шесть токенов, пароль из шаблона).
- Документы: README (быстрый старт), `docs/guide/owner.md`, `docs/guide/parameters.md`.
- Версия агента не меняется: пароль — секрет засева, в отпечаток он не входит.
- Стенд: копия `.env`, новый пароль, пересоздание `bootstrap`, `ouroboros`, `console`, `schedules`.
