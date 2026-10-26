# Spec Delta

## MODIFIED Requirements

### Requirement: Создание .env
`scripts/init_env.py` SHALL создавать `.env` из `.env.example` с правами 600, генерируя пустые внутренние секреты
`GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`, `TOOLS_API_TOKEN`, `MCP_TOKEN`,
`GPT2GIGA_API_KEY` (48 hex); пароль UI Ouroboros `OUROBOROS_PASSWORD` берётся из шаблона (`ouroboros`), логинов
консоли он не создаёт. Существующий
`.env` без `--fill` MUST оставаться нетронутым; `--fill` дописывает только пустые. Внешние ключи скрипт не создаёт.

#### Scenario: Первый запуск
- **WHEN** `.env` нет и выполняется `python scripts/init_env.py`
- **THEN** создан `.env` с шестью токенами и `OUROBOROS_PASSWORD=ouroboros`; напечатаны адрес консоли и адрес и
  пароль UI Ouroboros
- **AND** напечатано `впишите GIGACHAT_CREDENTIALS в .env (LLM-профиль gigachat; …)`

#### Scenario: Повторный запуск
- **WHEN** `.env` уже есть
- **THEN** напечатано `.env уже есть — ничего не меняю (--fill дописывает пустые токены)`, файл не изменён

#### Scenario: Пароль UI стёрт
- **WHEN** в `.env` строка `OUROBOROS_PASSWORD=` пуста и выполняется `python scripts/init_env.py --fill`
- **THEN** пароль не генерируется; напечатано `впишите OUROBOROS_PASSWORD в .env — без него агент не стартует`
