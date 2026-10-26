# Spec Delta

## MODIFIED Requirements

### Requirement: У сервиса только свои секреты
`.env` общий для сервисов образа `oilo/app` (`env_file: .env`), поэтому каждый такой сервис SHALL получать блок
`x-no-secrets` с пустыми значениями всех секретов, а свои секреты — явно в `environment`. `encoder`, `gpt2giga`,
`egress` и `ouroboros` `.env` не получают вовсе.

#### Scenario: Обнуляемые секреты
- **WHEN** читается `x-no-secrets`
- **THEN** в нём 12 ключей: `OPENROUTER_API_KEY`, `OUROBOROS_PASSWORD`, `MCP_TOKEN`, `TOOLS_API_TOKEN`,
  `TAVILY_API_KEY`, `BRAVE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GIGACHAT_CREDENTIALS`, `GPT2GIGA_API_KEY`,
  `GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`

#### Scenario: Свои секреты сервисов
- **WHEN** читаются переменные сервисов в compose
- **THEN** `gateway` — `OPENROUTER_API_KEY`, `GPT2GIGA_API_KEY`, `GATEWAY_TOKENS`; `tools` — `TOOLS_API_TOKEN`,
  `MCP_TOKEN`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `OPENAI_COMPAT_API_KEY=${GATEWAY_TOKEN_TOOLS}`; `console` —
  `TOOLS_API_TOKEN`, `OUROBOROS_PASSWORD`, `GATEWAY_TOKEN_CONSOLE`; `bootstrap` —
  `GATEWAY_TOKEN_OUROBOROS`, `MCP_TOKEN`, `OUROBOROS_PASSWORD`, `TELEGRAM_BOT_TOKEN`; `schedules` — `OUROBOROS_PASSWORD`
- **AND** у `tools`, который читает недоверенный веб, пусты `OPENROUTER_API_KEY`, `OUROBOROS_PASSWORD` и
  `GATEWAY_TOKEN_CONSOLE`

#### Scenario: Обязательные токены
- **WHEN** в `.env` пуст `GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE` или `TOOLS_API_TOKEN`
- **THEN** compose не стартует с ошибкой `задайте <имя>…`

### Requirement: Создание .env
`scripts/init_env.py` SHALL создавать `.env` из `.env.example` с правами 600, генерируя пустые внутренние секреты
`GATEWAY_TOKEN_OUROBOROS`, `GATEWAY_TOKEN_TOOLS`, `GATEWAY_TOKEN_CONSOLE`, `TOOLS_API_TOKEN`, `OUROBOROS_PASSWORD`,
`MCP_TOKEN`, `GPT2GIGA_API_KEY` (48 hex); логинов консоли он не создаёт. Существующий
`.env` без `--fill` MUST оставаться нетронутым; `--fill` дописывает только пустые. Внешние ключи скрипт не создаёт.

#### Scenario: Первый запуск
- **WHEN** `.env` нет и выполняется `python scripts/init_env.py`
- **THEN** создан `.env` с семью токенами
- **AND** напечатано `впишите GIGACHAT_CREDENTIALS в .env (LLM-профиль gigachat; …)`

#### Scenario: Повторный запуск
- **WHEN** `.env` уже есть
- **THEN** напечатано `.env уже есть — ничего не меняю (--fill дописывает пустые токены)`, файл не изменён
