# llm-profiles Specification

## Purpose
LLM-профиль стенда — набор моделей агента Ouroboros и субагентов инструментов и путь, которым они идут к провайдеру:
GigaChat через прокси gpt2giga (по умолчанию) или OpenRouter через шлюз. Сюда же входит вызов LLM из графов
инструментов (`src/oil_ouroboros/llm/core.py`): провайдер, параметры из каталога, метки прогона, учёт и тестовая модель
`llm/fake.py`.

## Requirements

### Requirement: Файлы профилей
LLM-профиль MUST быть файлом `deploy/llm/<имя>.env` со строками `КЛЮЧ=значение`: модели `OUROBOROS_MODEL`,
`OUROBOROS_MODEL_LIGHT`, `LLM_MODEL_SUBAGENT` и, если профилю нужен отдельный сервис, `REQUIRES_COMPOSE_PROFILE` (в
`.env` не пишется). У профиля есть внешний ключ, который вписывает человек (`REQUIRED`), и, возможно, внутренний токен,
который создаёт скрипт (`GENERATED`). Профилей SHALL быть два: `gigachat` и `openrouter`.

#### Scenario: Профиль gigachat
- **WHEN** читается `deploy/llm/gigachat.env`
- **THEN** `OUROBOROS_MODEL=GigaChat-3-Ultra`, `OUROBOROS_MODEL_LIGHT=GigaChat-2`, `LLM_MODEL_SUBAGENT=GigaChat-3-Ultra`,
  `REQUIRES_COMPOSE_PROFILE=gigachat`
- **AND** внешний ключ — `GIGACHAT_CREDENTIALS`, внутренний — `GPT2GIGA_API_KEY`

#### Scenario: Профиль openrouter
- **WHEN** читается `deploy/llm/openrouter.env`
- **THEN** `OUROBOROS_MODEL=anthropic/claude-haiku-5.5`, `OUROBOROS_MODEL_LIGHT=deepseek/deepseek-v4.1-flash`,
  `LLM_MODEL_SUBAGENT=` (пусто — модель каталога `llm.subagent.model`, `openai/gpt-6-luna`), отдельного сервиса нет
- **AND** внешний ключ — `OPENROUTER_API_KEY`

### Requirement: Переключение профиля
`python scripts/llm_profile.py <имя> [--env <файл>]` SHALL менять только `.env` (по умолчанию в корне проекта):
ключи профиля и `LLM_PROFILE=<имя>` заменяются на месте, отсутствующие дописываются в конец под строкой
`# --- LLM-профиль (<имя>): scripts/llm_profile.py`; из `COMPOSE_PROFILES` убираются compose-профили всех LLM-профилей,
остальные сохраняются, нужный добавляется в конец; пустой внутренний токен получает 48 hex. Прочие строки не меняются.

#### Scenario: На gigachat и обратно
- **WHEN** в `.env` `OPENROUTER_API_KEY=sk-x`, `COMPOSE_PROFILES=owner`, пустые `GIGACHAT_CREDENTIALS` и
  `GPT2GIGA_API_KEY`, и выполняется переключение на `gigachat`
- **THEN** модели — GigaChat, `COMPOSE_PROFILES=owner,gigachat`, `GPT2GIGA_API_KEY` — 48 символов, `LLM_PROFILE=gigachat`,
  `OPENROUTER_API_KEY=sk-x`, скрипт печатает `впишите GIGACHAT_CREDENTIALS в .env — без него профиль не заработает`
- **AND** обратное переключение на `openrouter` даёт `COMPOSE_PROFILES=owner`, пустой `LLM_MODEL_SUBAGENT` и прежний
  `GPT2GIGA_API_KEY`

#### Scenario: Без аргумента
- **WHEN** выполняется `python scripts/llm_profile.py`
- **THEN** печатается `текущий профиль: <LLM_PROFILE или «не задан»>` и `профили: gigachat, openrouter`, `.env` не меняется

#### Scenario: Ошибки
- **WHEN** имя профиля неизвестно или `.env` нет
- **THEN** скрипт завершается с `нет профиля 'x'; есть: gigachat, openrouter` или `нет <путь>: сначала python scripts/init_env.py`

#### Scenario: Подсказка применения
- **WHEN** профиль переключён
- **THEN** скрипт печатает `применить: docker compose up -d && docker compose restart ouroboros`

### Requirement: Профиль по умолчанию — gigachat
Шаблон `.env.example` MUST соответствовать профилю `gigachat`: `LLM_PROFILE=gigachat`, `COMPOSE_PROFILES=owner,gigachat`,
модели как в `deploy/llm/gigachat.env`. Те же модели — значения по умолчанию засева агента (`bootstrap`:
`OUROBOROS_MODEL=GigaChat-3-Ultra`, `OUROBOROS_MODEL_LIGHT=GigaChat-2`), а `LLM_PROFILE` по умолчанию в
`src/oil_ouroboros/config.py` — `gigachat`. Причина: Freemium бесплатен и не нужен ключ с зарубежной оплатой.

#### Scenario: Шаблон уже на gigachat
- **WHEN** переключатель применяет `gigachat` к тексту `.env.example`
- **THEN** меняется только строка `GPT2GIGA_API_KEY`, недостающий ключ — `GIGACHAT_CREDENTIALS`

#### Scenario: Шаблон на openrouter
- **WHEN** переключатель применяет `openrouter` к тексту `.env.example`
- **THEN** `COMPOSE_PROFILES=owner`, недостающий ключ — `OPENROUTER_API_KEY`

### Requirement: Путь моделей агента
Модели агента SHALL доходить до Ouroboros через засев: в его `settings.json` `OUROBOROS_MODEL` и `OUROBOROS_MODEL_LIGHT`
записываются как `openai-compatible::<модель из .env>`, `OPENAI_COMPATIBLE_BASE_URL=http://gateway:8030/api/v1`,
`OPENAI_COMPATIBLE_API_KEY` — `GATEWAY_TOKEN_OUROBOROS`, `OPENROUTER_API_KEY` пуст. Поэтому все вызовы агента идут через
шлюз, а куда дальше — решает шлюз по имени модели (спека llm-gateway).

#### Scenario: Агент на GigaChat
- **WHEN** `OUROBOROS_MODEL=GigaChat-3-Ultra` и засев выполнен
- **THEN** в настройках Ouroboros `OUROBOROS_MODEL=openai-compatible::GigaChat-3-Ultra`, и его вызовы шлюз отправляет в
  gpt2giga

### Requirement: Прокси gpt2giga
Сервис `gpt2giga` (образ `ghcr.io/ai-forever/gpt2giga:0.3.0-py3.12`, только в профиле compose `gigachat`) SHALL
переводить вызовы шлюза из формата OpenAI в API GigaChat и сам обновлять токен доступа Сбера. Он не получает `.env`,
только свои переменные; принимает запросы только с ключом `GPT2GIGA_API_KEY` (общий со шлюзом); живёт только в сети
`oilo` на порту 8090; память — 512m.

#### Scenario: Переменные прокси
- **WHEN** сервис запущен
- **THEN** `GPT2GIGA_MODE=PROD`, `GPT2GIGA_PORT=8090`, `GPT2GIGA_ENABLE_API_KEY_AUTH=True`,
  `GPT2GIGA_LOG_REDACT_SENSITIVE=True`, `GIGACHAT_SCOPE` по умолчанию `GIGACHAT_API_PERS`, `GIGACHAT_TIMEOUT=180`
- **AND** `GIGACHAT_MAX_CONNECTIONS` по умолчанию 1: во Freemium один поток, запросы ждут в очереди прокси

#### Scenario: Рассуждения выключены
- **WHEN** Ouroboros просит `reasoning_effort`
- **THEN** прокси работает с `GPT2GIGA_DISABLE_REASONING=True`: в режиме рассуждений GigaChat 3 Ultra зацикливался и
  тратил весь `max_tokens` без ответа

#### Scenario: TLS к Сберу
- **WHEN** прокси соединяется с GigaChat
- **THEN** сертификат проверяется (`GIGACHAT_VERIFY_SSL_CERTS=True`) по корню Минцифры
  `deploy/gigachat/russian_trusted_root_ca.pem`, смонтированному только в этот контейнер как
  `/certs/russian_trusted_root_ca.pem:ro`

#### Scenario: Здоровье
- **WHEN** Docker проверяет сервис
- **THEN** каждые 30 с запрашивается `http://127.0.0.1:8090/health` (таймаут 10 с, старт 20 с)

### Requirement: Провайдер LLM инструментов
`chat_model(role)` SHALL строить модель по `LLM_PROVIDER` (по умолчанию `openai_compatible`): `openai_compatible` —
`OPENAI_COMPAT_BASE_URL` с ключом `OPENAI_COMPAT_API_KEY` без прокси; `openrouter` — напрямую `OPENROUTER_BASE_URL` (по
умолчанию `https://openrouter.ai/api/v1`) с `OPENROUTER_API_KEY` через `OUTBOUND_PROXY`; `fake` — `ScriptedChat`.
Иное значение MUST давать `ValueError` `неизвестный LLM_PROVIDER=<значение>`. `CA_BUNDLE`, если задан, действует для
обоих сетевых провайдеров.

#### Scenario: Инструменты в Docker
- **WHEN** сервис `tools` запущен из compose
- **THEN** `LLM_PROVIDER=openai_compatible`, `OPENAI_COMPAT_BASE_URL=http://gateway:8030/api/v1`,
  `OPENAI_COMPAT_API_KEY` — `GATEWAY_TOKEN_TOOLS`: все вызовы субагентов идут через шлюз

#### Scenario: OpenRouter напрямую
- **WHEN** `LLM_PROVIDER=openrouter` (разработка вне Docker)
- **THEN** запросы несут заголовки `HTTP-Referer: https://github.com/maxdonit/oil_ouroboros` и `X-Title: oil-ouroboros`

### Requirement: Параметры вызова из каталога
Модель и параметры вызова SHALL браться из раздела `llm.subagent` каталога после слоёв (спека tool-catalog): `model`,
`temperature`, `timeout_s` (таймаут клиента), `retries` (`max_retries`), непустой `reasoning_effort` — в теле запроса
`{"reasoning": {"effort": …}}`. Модель создаётся один раз на процесс для ключа
`провайдер|модель|temperature|timeout_s|retries|reasoning_effort`.

#### Scenario: Модель профиля
- **WHEN** `LLM_MODEL_SUBAGENT=GigaChat-3-Ultra`
- **THEN** субагенты запрашивают модель `GigaChat-3-Ultra`, и шлюз отправляет их вызовы в gpt2giga

#### Scenario: Рассуждения субагента
- **WHEN** в каталоге `reasoning_effort: low`
- **THEN** тело запроса содержит `"reasoning": {"effort": "low"}`; пустое значение поля не добавляет

### Requirement: Метки прогона в запросах
Каждый HTTP-запрос модели внутри контекста прогона (`RunContext`) SHALL нести `X-Oil-Run: <run_id>` и, если у вызова
есть назначение, `X-Oil-Purpose: <назначение>` (до 60 символов); вне прогона этих заголовков нет. По ним журнал шлюза
связывает деньги с трейсом инструмента.

#### Scenario: Отбор фрагментов
- **WHEN** граф research вызывает `call_json("subagent", …, purpose="select_grade")` в прогоне `abc123`
- **THEN** запрос несёт `X-Oil-Run: abc123` и `X-Oil-Purpose: select_grade`, а после вызова назначение сбрасывается

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

### Requirement: Дублирующий запрос
При `hedge_after_s` > 0 (в каталоге `current` — 15) вызов SHALL, не получив ответа за `hedge_after_s` секунд, послать
второй такой же запрос и вернуть первый пришедший ответ; если первым завершился запрос с ошибкой — результат другого.
Каждый запрос получает свою копию контекста (метки прогона); проигравший дорабатывает в фоне. `0` — один запрос.

#### Scenario: Всплеск задержки
- **WHEN** первый запрос отвечает через 0.5 с, второй — сразу, `hedge_after_s=0.05`
- **THEN** возвращается ответ второго, `hedged=True`, модель вызвана дважды

#### Scenario: Дублирование выключено
- **WHEN** `hedge_after_s=0`
- **THEN** один запрос, `hedged=False`

### Requirement: JSON-ответы с одним ремонтом
`call_json(role, messages, schema, purpose)` SHALL разбирать ответ как JSON (снимая ограду ```` ```json ````; иначе —
текст от первой `{` до последней `}`), проверять его pydantic-схемой и при ошибке делать ровно одну повторную попытку с назначением
`<purpose>:repair`; вторая неудача MUST подниматься исключением.

#### Scenario: Ремонт
- **WHEN** первый ответ не проходит схему
- **THEN** второй вызов получает прежнюю переписку, ответ модели и сообщение
  `Ответ не прошёл проверку схемы: <ошибка>. Верни ТОЛЬКО корректный JSON по схеме.`

### Requirement: Тестовая модель
При `LLM_PROVIDER=fake` вызовы SHALL обслуживаться детерминированной `ScriptedChat` (`llm/fake.py`) без сети и денег:
по маркеру `[[research_plan]]` в системном промпте — JSON плана, по `[[select_grade]]` — JSON отбора, иначе `{}`;
usage каждого ответа — 100 входных и 20 выходных токенов.

#### Scenario: План вне темы
- **WHEN** вопрос «рецепт борща» с маркером `[[research_plan]]`
- **THEN** план: `in_scope=false`, `queries` — вопрос и «oil market <вопрос>», `orgs` — агентства из строки
  «Агентства в базе:», названные в вопросе, `editions=[]`

#### Scenario: Отбор
- **WHEN** маркер `[[select_grade]]` и 8 фрагментов `#1 `…`#8 `
- **THEN** `selected=[1,2,3,4,5,6]`, `verdict=sufficient`; вопрос со словом «сегодня» или без фрагментов — `partial`
