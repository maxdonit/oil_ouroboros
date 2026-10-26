# Нефтегазовый аналитик на Ouroboros — правила работы в репозитории

## Доработка — только через OpenSpec
Любое изменение поведения — код, каталог `config/`, регламент и память агента, состав compose, UI, источники данных —
проходит как change OpenSpec по скиллу `sdd` (`.claude/skills/sdd/SKILL.md`):
`/opsx:explore` → `/opsx:propose` → согласие пользователя → `/opsx:apply` → гейты → `/opsx:verify` и субагент
`spec-guard` → `/opsx:archive` → коммит. Без change — только правки, не меняющие поведения (опечатка, формулировка,
комментарий).

Живые спеки `openspec/specs/` описывают, что система делает сейчас: на вопросы «как работает» отвечать по ним и по
коду, со ссылками на требование и `file:line`.

## Гейты перед коммитом
```
.venv/Scripts/ruff.exe check scripts src tests && .venv/Scripts/ruff.exe format --check scripts src tests
.venv/Scripts/python.exe -m pytest
OPENSPEC_TELEMETRY=0 openspec validate --all --strict
```
`tests/test_consistency.py` ловит дрейф: устаревшие имена, битые ссылки и пути в документах, параметры без описания,
спеки вне таблицы процесса. Красное — не коммитить.

## Где что
- `README.md` — запуск и обзор; `docs/architecture/DESIGN.md` — устройство и решения;
- `docs/guide/` — ход ответа, параметры, эксплуатация, источники, канал владельца; `docs/report.md` — замеры;
- `openspec/specs/` — поведение по возможностям; `openspec/changes/` — доработки в работе и архив.

## Ограничения
- Всё на русском: документы, комментарии, тексты UI, артефакты OpenSpec, сообщения коммитов; ruff line-length 119.
- Полный eval на GigaChat стоит ~4 млн токенов Ultra (замер на 42 кейсах) — только с согласия владельца; для
  проверки — `oilo eval --only …`.
- Секреты — только в `.env` (в git не попадает); у сервиса compose только свои ключи (`x-no-secrets`).
- UI — ES-модули без сборки: синтаксис проверять `node --check` копии файла с расширением `.mjs`, версию ассетов в
  `ui/index.html` поднимать.
- На Windows Python по умолчанию пишет CRLF: файлы записывать с `newline="\n"` (`.gitattributes`: `eol=lf`).
