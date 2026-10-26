# web-search Specification

## Purpose
Веб-поиск свежих данных с фильтром источников: инструмент `search_web` (граф `src/oil_ouroboros/graphs/search_web.py`),
общий поиск `src/oil_ouroboros/web/search.py` (цепочка Tavily → Brave → DuckDuckGo, проходы по уровням доверия, кэш,
бюджет времени), оценка доверия к доменам `src/oil_ouroboros/web/credibility.py` и политики каталога
`config/current/source_tiers.yaml` и `config/current/web.yaml`. Веб — дополнение к отчётам: жёлтая пресса отсекается
до контекста, а уровень доверия решает, может ли источник подтверждать цифры.

## Requirements

### Requirement: Правило «сначала отчёты» в search_web
Инструмент `search_web` SHALL исполняться графом `check_pack → search → pack`. Узел `check_pack` MUST пропускать
дальше только пакет `research` или `search_reports` не старше `PACK_MAX_AGE_S` = 1200 с (20 мин), иначе прогон
завершается `ToolError` с подсказкой агенту, что сделать. Свежесть `any` означает поиск без ограничения по дате.

#### Scenario: Нет пакета отчётов
- **WHEN** `pack_id` не найден в журнале или это пакет другого инструмента (например, `forecast_price`)
- **THEN** ошибка «search_web: сначала research (поиск по отчётам), затем веб — передай pack_id из его результата»

#### Scenario: Пакет устарел
- **WHEN** пакет research создан больше 20 минут назад
- **THEN** ошибка «search_web: пакет P<n> устарел (старше 20 мин) — сначала research по текущему вопросу»

### Requirement: Результат search_web
Узел `pack` SHALL записать каждый оставленный документ в журнал как `W#` (`put_web`) и создать пакет с `tool` =
`search_web`, вопросом — запросами через «; », `after` = пакет research и решением `search_web` типа
`web_source_filter` (коды `provider=…`, `freshness=…`, `after=P<n>`, причина агента в `rationale`, отсеянные адреса с
уровнем и причиной). Текст результата MUST заканчиваться строкой «Веб — дополнение к отчётам: в ответе явно отделяй
свежие веб-данные от данных отчётов.».

#### Scenario: Текст для агента
- **WHEN** поиск оставил два источника и отсеял три адреса
- **THEN** первая строка — `Пакет P<n> · trace <id> · после P<m> · провайдер <имя>, свежесть <значение>` (при сбое —
  ещё ` · сбой: <ошибка>`), затем записи `[W#] <метка> — <название уровня>` со строкой «<заголовок>. <содержимое>»
- **AND** есть строка «Отсеяно фильтром источников: 3 (жёлтая пресса, deny-список, лимит на домен).»; без источников —
  «— подходящих источников нет —»

### Requirement: Провайдеры и цепочка резервов
Основной провайдер SHALL выбираться по `WEB_PROVIDER` (по умолчанию `auto`: Tavily при `TAVILY_API_KEY`, иначе Brave
при `BRAVE_API_KEY`, иначе DuckDuckGo `ddgs`; явно — `tavily`, `brave`, `ddgs`, `none`). За ним в цепочке идут Brave
(есть ключ и он не основной) и `ddgs` (при `WEB_FALLBACK` = `ddgs`, по умолчанию). Следующий провайдер MUST вызываться,
только если текущий ответил HTTP 401, 402, 403, 429 или 432; такой провайдер пропускается 1800 с (в памяти процесса).

#### Scenario: Лимит основного провайдера исчерпан
- **WHEN** Tavily отвечает HTTP 432, а `WEB_FALLBACK` = `ddgs`
- **THEN** результат приходит из DuckDuckGo, `provider` = `ddgs`, у прохода `fallback` = «tavily: лимит исчерпан»
- **AND** следующий поиск в течение 30 минут Tavily не вызывает; для 401 и 403 причина — «ключ отклонён (HTTP <код>)»

#### Scenario: Резерва нет или веб выключен
- **WHEN** у Tavily исчерпан лимит, а `WEB_FALLBACK` = `none`
- **THEN** документов нет, а `error` содержит «все провайдеры недоступны»
- **AND** при `WEB_PROVIDER=none` поиск не выполняется: `error` = «веб-поиск выключен (WEB_PROVIDER=none)»

### Requirement: Запросы к провайдерам
Каждый провайдер SHALL получать запрос, фильтр доменов, свежесть (`day`, `week`, `month`, `year` или без неё) и число
результатов `web.max_results` = 6 и возвращать документы с содержимым не длиннее 1500 символов. Tavily и Brave ходят
наружу через `net.outbound` (таймаут 30 с), DuckDuckGo — через `OUTBOUND_PROXY` с таймаутом 20 с. Пустая выдача
DuckDuckGo MUST считаться пустым ответом, а не ошибкой.

#### Scenario: Tavily
- **WHEN** запрос идёт в Tavily
- **THEN** это `POST https://api.tavily.com/search` с `search_depth` = `basic`, `topic` = `news` при заданной свежести
  (иначе `general`), `time_range` = свежесть, `include_domains` (до 300) или `exclude_domains` (до 150)

#### Scenario: Brave и DuckDuckGo
- **WHEN** запрос идёт в Brave или DuckDuckGo с доверенным фильтром доменов
- **THEN** к запросу дописываются операторы `site:` по `source_tiers.site_operators` (по важности, до 12; пусто —
  первые 12 доменов фильтра), свежесть Brave — `pd|pw|pm|py`, DuckDuckGo — поиск новостей с `timelimit` `d|w|m|y`
- **AND** без свежести DuckDuckGo ищет обычный текст, а ответ «No results» даёт пустой список

### Requirement: Проходы поиска и бюджет времени
Каждый запрос SHALL сначала искать только по доменам уровней 0–1 из политики (проход `trusted_tier0_1`), а если среди
найденного меньше 3 источников уровня 0–1 и бюджет `WEB_TIME_BUDGET_S` = 40 с не исчерпан — открытым поиском без
доменов `deny` (проход `open_exclude_deny`). Запросов MUST быть не больше `research.max_web_queries` = 2 (пустые
отбрасываются); запросы идут параллельно.

#### Scenario: Бюджет исчерпан
- **WHEN** к началу открытого прохода поиск идёт дольше 40 с
- **THEN** проход не выполняется, в проходы пишется `mode` = `skipped` с заметкой «бюджет времени веб-поиска 40 с
  исчерпан»

#### Scenario: За неделю пусто
- **WHEN** свежесть `week`, а доверенный и открытый проходы ничего не нашли
- **THEN** выполняется третий проход открытым поиском за `month`: порядок вызовов — (доверенные, week), (открытый,
  week), (открытый, month)
- **AND** последний проход имеет `mode` = `open_month_fallback`

### Requirement: Отбор и ранжирование выдачи
Выдача всех проходов SHALL проходить один фильтр: повторы адреса (без якоря и хвостового «/») убираются; адрес выпуска,
который уже есть в базе отчётов, отсеивается с причиной `…|report_in_base`; адрес, отклонённый оценкой доверия,
отсеивается; остальное сортируется по уровню, затем по убыванию оценки провайдера. С одного домена MUST оставаться не
больше 2 источников (`…|domain_cap`), всего — не больше `web.max_results` = 6 (`…|limit`).

#### Scenario: Выпуск из базы и страница серии
- **WHEN** выдача содержит точный адрес выпуска EIA STEO, файл которого есть в базе, и страницу серии STEO
- **THEN** адрес выпуска отсеян с `report_in_base` (его цитируют как отчёт), а страница серии проходит в выдачу

#### Scenario: Лимит на домен
- **WHEN** после сортировки три источника с `reuters.com`
- **THEN** остаются два, третий отсеян с причиной, оканчивающейся на `|domain_cap`

### Requirement: Кэш выдачи
Каждый вызов провайдера SHALL идти через кэш: ключ — первые 24 символа sha256 от (провайдер, запрос, include, exclude,
свежесть, число результатов); каталог — `WEB_CACHE_DIR`, если задан, иначе `var/webcache/w<H>h-<номер окна>` по
`WEB_CACHE_HOURS` = 6 (окна выровнены по времени); `WEB_CACHE_HOURS=0` без `WEB_CACHE_DIR` — без кэша. Ошибка чтения
кэша MUST вести к живому запросу, ошибка записи — не ронять поиск.

#### Scenario: Повтор запроса
- **WHEN** задан `WEB_CACHE_DIR`, и один и тот же поиск выполнен дважды
- **THEN** провайдер вызван один раз, выдачи совпадают, у прохода второго поиска `source` = `cache` (у первого —
  `live`)

### Requirement: Сбой веба не роняет инструмент
Сбой поиска по запросу SHALL записываться в проходы (`mode` = `error`) и в поле `error` результата (до 300 символов,
вида `<провайдер>: <Класс>: <текст>`), а инструмент MUST вернуть результат с тем, что найдено. Итог поиска для трейса и
решения (`WebResult.summary`) содержит `provider` (цепочка вида `tavily→ddgs`, если ответ дал резерв), `passes`,
`rejected` с причинами, `policy_version`, `error`, `ms` и `freshness`.

#### Scenario: Провайдер ответил ошибкой
- **WHEN** провайдер вернул HTTP 500
- **THEN** резерв не вызывается, документов по запросу нет, `error` начинается с имени основного провайдера
- **AND** `search_web` отвечает без `isError`, с « · сбой: <ошибка>» в первой строке

### Requirement: Оценка доверия к адресу
`web/credibility.assess(url)` SHALL давать уровень 0–3 или отказ (`tier` = None) по первому сработавшему правилу: `curated_deny` → лента пресс-релизов `press_release` (3) → `curated_tier0` (0) →
`state_affiliated` (3, с меткой) → `government_domain` по `government_suffixes` (0) → адрес перепечатки релиза
`press_release` (3) → `curated_tier1`/`curated_tier2` → рейтинг `pc1_*` → `unknown_domain` (3). Совпадение MUST
проверяться по домену без `www.` и всем его родительским доменам.

#### Scenario: Таблица адресов
- **WHEN** оцениваются адреса
- **THEN** `https://www.eia.gov/outlooks/steo/` → 0 `curated_tier0`; `https://home.treasury.gov/news/press-releases/x`
  → 0 `curated_tier0`; `https://www.meti.go.jp/english/press/x.html` → 0 `government_domain`;
  `https://www.spa.gov.sa/w2k` → 3 `state_affiliated`; `https://news.bloomberg.com/a` → 1
- **AND** `https://markets.ft.com/data/announce/detail?dockey=…PR_NEWS…` → 3 `press_release`, но
  `https://www.ft.com/content/…` → 1 `curated_tier1`; `https://www.prnewswire.com/news-releases/x.html` → 3;
  `https://www.dailymail.co.uk/x` → отказ `curated_deny`; `https://totally-unknown-domain-xyz.com/` → 3
  `unknown_domain`

#### Scenario: Государственное СМИ
- **WHEN** оценивается `https://tass.ru/ekonomika/1`
- **THEN** уровень 3, причина `state_affiliated`, `state_affiliated` = true: источник маркируется, а не отбрасывается

### Requirement: Политика доверия source_tiers.yaml
Политика SHALL читаться из `config/current/source_tiers.yaml` (поле `version`, сейчас `2026-10-23.2`, пишется в решения
веб-фильтра) и рейтинга `data/source_quality/domain_pc1.csv` (Lin et al. 2023); при совпадении домена в нескольких
курируемых списках действует более высокий уровень. Пороги MUST быть упорядочены `deny_below ≤ tier2_min ≤ tier1_min`
(по умолчанию 0.40, 0.60, 0.85), `site_operators` — не длиннее 12. Политика кэшируется в процессе до перезапуска.

#### Scenario: Уровень по рейтингу pc1
- **WHEN** домена нет в курируемых списках, а его pc1 в рейтинге — 0.9, 0.7, 0.5 или 0.3
- **THEN** уровень соответственно 1 (`pc1_high`), 2 (`pc1_medium`), 3 (`pc1_low`) или отказ (`pc1_deny`)

#### Scenario: Неупорядоченные пороги
- **WHEN** в `source_tiers.yaml` `tier2_min` больше `tier1_min`
- **THEN** каталог невалиден, проблема `source_tiers.yaml: thresholds` содержит «нужно deny_below ≤ tier2_min ≤
  tier1_min»

### Requirement: Веб-политика web.yaml
`config/current/web.yaml` SHALL задавать `max_results` = 6 (1–30, источников в контексте), `min_tier_for_numbers` = 2
(0–3, худший уровень веб-источника, подтверждающего цифры; спека `answer-verification`) и условия похода в веб
`policy`: `needs_freshness` = true, `sufficiency` = `[partial, insufficient]`, `freshness_gap` = true. Флаги MUST быть
строго `true`/`false`; веб нужен, если выполнено хотя бы одно условие (решает `research`, спека `research-tool`).

#### Scenario: Причины похода в веб
- **WHEN** план требует свежести, вердикт достаточности — `partial`, оценщик увидел разрыв по датам
- **THEN** причины — `needs_freshness`, `sufficiency=partial`, `freshness_gap`
- **AND** при вердикте `sufficient`, без свежести и разрыва причин нет, и веб не нужен

#### Scenario: Нестрогий флаг
- **WHEN** в `web.yaml` записано `needs_freshness: yes`
- **THEN** каталог невалиден: `yes` — строка, а не логическое значение

### Requirement: Метка веб-источника
Каждый документ SHALL получать метку `Источник: <домен>, web[, <ГГГГ-ММ-ДД>], уровень <N>[, гос. СМИ]` и название
уровня по `TIER_TITLES`: 0 — «первичный источник», 1 — «агентство уровня 1», 2 — «деловая пресса», 3 — «не
подтверждено» (отклонённый — «отклонён»). Дата публикации MUST приводиться к `ГГГГ-ММ-ДД` из ISO или RFC 2822;
нераспознанная дата в метку не попадает.

#### Scenario: Дата в формате RFC 2822
- **WHEN** провайдер вернул дату «Fri, 16 Oct 2026 10:00:00 GMT» для `https://www.reuters.com/x`
- **THEN** метка — «Источник: reuters.com, web, 2026-10-16, уровень 1»
