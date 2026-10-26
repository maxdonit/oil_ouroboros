# Spec Delta

## MODIFIED Requirements

### Requirement: Секреты засева
Ключи `OPENAI_COMPATIBLE_API_KEY`, `OUROBOROS_NETWORK_PASSWORD`, `MCP_SERVERS`, `TELEGRAM_BOT_TOKEN`
(`bootstrap.SECRETS`) засев SHALL считать секретами: их значения MUST NOT попадать в файл состояния и в вывод —
изменённый секрет печатается именем с пометкой «(секрет)». `settings.json` пишется с правами `0600`. Сервису
`bootstrap` из `.env` доходят только его секреты; засев токена Telegram — спека `owner-channel`.

#### Scenario: Состояние без секретов
- **WHEN** засев прошёл с `TELEGRAM_BOT_TOKEN=123:abc`
- **THEN** в `state/oilo_seed.json` → `settings_public` нет `TELEGRAM_BOT_TOKEN`, `MCP_SERVERS`,
  `OPENAI_COMPATIBLE_API_KEY`, `OUROBOROS_NETWORK_PASSWORD`

#### Scenario: Секреты сервиса
- **WHEN** compose передаёт окружение сервису `bootstrap`
- **THEN** у него заданы `GATEWAY_TOKEN_OUROBOROS`, `MCP_TOKEN`, `OUROBOROS_PASSWORD`, `TELEGRAM_BOT_TOKEN`, а
  `OPENROUTER_API_KEY`, `TOOLS_API_TOKEN` и другие чужие секреты обнулены (`x-no-secrets`)
