## MODIFIED Requirements

### Requirement: Создание .env
`scripts/init_env.py` SHALL создавать `.env` из `.env.example` с правами 600, генерируя пустые внутренние секреты
`GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`, `TOOLS_API_TOKEN`, `OUROBOROS_PASSWORD`,
`MCP_TOKEN`, `GPT2GIGA_API_KEY` (48 hex) и `UI_USERS=admin:<пароль>:admin,user:<пароль>:user`. Существующий
`.env` без `--fill` MUST оставаться нетронутым; `--fill` дописывает только пустые. Внешние ключи скрипт не создаёт.

#### Scenario: Первый запуск
- **WHEN** `.env` нет и выполняется `python scripts/init_env.py`
- **THEN** создан `.env` с семью токенами и `UI_USERS`
- **AND** напечатано `впишите GIGACHAT_CREDENTIALS в .env (LLM-профиль gigachat; …)`

#### Scenario: Повторный запуск
- **WHEN** `.env` уже есть
- **THEN** напечатано `.env уже есть — ничего не меняю (--fill дописывает пустые токены)`, файл не изменён
