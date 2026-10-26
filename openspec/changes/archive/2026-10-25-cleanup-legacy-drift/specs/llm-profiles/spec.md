# Spec Delta

## MODIFIED Requirements

### Requirement: Единая точка вызова и учёт
`call(role, messages, purpose)` SHALL быть единственной точкой вызова LLM в графах: снимает ведущий маркер `[[имя]]`
строковых сообщений (кроме `LLM_PROVIDER=fake`), берёт токены из `usage_metadata`, цену — из `usage.cost` ответа, иначе
по прайсу `llm/pricing.py` (с обновлением каталога OpenRouter из сети раз в сутки; у `GigaChat-*` цена 0 без
обращения к каталогу), копит токены, цену и число вызовов в
контексте прогона и шлёт событие трейса `llm_call`.

#### Scenario: Событие трейса
- **WHEN** узел `select_grade` делает второй вызов в прогоне
- **THEN** событие `llm_call` с `key=select_grade#1`, ролью, моделью, назначением, `ms`, `tokens_in`, `tokens_out`,
  `cost_usd` (6 знаков), `hedged`, запросом и ответом (каждое сообщение до 12 000 символов, дальше «…») и `tool_calls`

#### Scenario: Вызов GigaChat без цены в ответе
- **WHEN** ответ модели `GigaChat-3-Ultra` пришёл без `usage.cost`
- **THEN** цена вызова — 0, а к openrouter.ai за каталогом цен запроса нет

#### Scenario: Маркер не уходит в модель
- **WHEN** системный промпт начинается с `[[research_plan]] Ты — субагент…` и провайдер не `fake`
- **THEN** модель получает текст без `[[research_plan]]`
