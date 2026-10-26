# Proposal

## Why

Текущую цену агент знает только для Brent (`price_quote`, спека `price-quote`). На «сколько сейчас стоит WTI» или «какая
цена газа Henry Hub / TTF» он ищет цифры в новостях через веб: без времени котировки, разные в разных статьях, ход
дольше. При этом на Мосбирже торгуются фьючерсы и на эти сорта — тот же ISS, тот же формат ответа, без ключа
(проверено со стенда 24.10.2026):

| Сорт | Актив ISS | Ближайший контракт | Цена 23.10, 23:50 МСК | Единица | Спот EIA |
|---|---|---|---|---|---|
| WTI | `WTI` | WTI-11.26 (до 20.11) | 103.43 | USD за баррель | `RWTC` — уже скачивается |
| Газ Henry Hub | `NG` | NG-10.26 (до 28.10) | 2.628 | USD за MMBtu | `RNGWHHD` — по 20.10, 3.10 |
| Газ TTF | `TTF` | TTF-10.26 (до 30.10) | 93.005 | EUR за МВт·ч | нет |

Контракты Мосбиржи рассчитываются по фьючерсам NYMEX и ICE и истекают вместе с их фронтом, поэтому правило перехода
на следующий контракт у Brent (`forecast.quote.roll_days`) подходит и им.

## What Changes

- **`price_quote(benchmark="brent")`** — аргумент `benchmark`: `brent`, `wti`, `henry_hub`, `ttf`. Без аргумента —
  Brent, как сейчас. Для каждого сорта — котировка ближайшего фьючерса Мосбиржи с единицей, контрактом, временем и
  задержкой 15 минут; рядом спот EIA, если он есть (Brent, WTI, Henry Hub). Метки — с названием сорта; у TTF без спота.
- **Сорта — в каталоге**, а не в коде: `forecast.quote.instruments` (актив ISS, название на двух языках, единица, ряд
  EIA для спота). Новый сорт Мосбиржи добавляется правкой каталога.
- **Спот газа Henry Hub** — третий ряд EIA (`RNGWHHD`, раздел `dnav/ng`) в плановом обновлении цен и снимке
  `data/prices`; используется только как спот рядом с котировкой (прогноз и история — по-прежнему Brent и WTI).
- Кэш, сохранённая котировка, отказ и заморозка eval — **по сорту** (`var/quotes/<сорт>.json`).
- **Регламент агента:** текущая цена Brent, WTI, Henry Hub, TTF → `price_quote(benchmark)`; Urals — по-прежнему оценка.
  Утренняя сводка берёт котировку WTI из `price_quote`, а не из веба.
- **Вкладка «Источники»** — котировки всех сортов; **eval** — три кейса о текущей цене WTI, Henry Hub и TTF.

Источник и условия — как у Brent: ISS Московской биржи, публичный доступ без ключа, данные с задержкой 15 минут;
распространение в коммерческом продукте — по договору с биржей (проверить до промышленного использования). EIA —
public domain, выкладывает дневные цены раз в неделю.

Не входит (Non-goals):
- прогноз и история цен газа (`forecast_price`, `price_history` остаются на Brent, WTI, Urals) — отдельная строка плана;
- котировка Urals: своего фьючерса нет, остаётся оценкой Brent − дисконт;
- пересчёт в рубли и другие валюты (курс ЦБ — отдельная строка плана), спот TTF (открытого дневного ряда нет).

## Capabilities

### New Capabilities
(нет)

### Modified Capabilities
- `price-quote`: котировки четырёх сортов из каталога, аргумент `benchmark`, кэш и файлы по сорту, спот EIA — где есть.
- `price-forecast`: третий ряд EIA — спот газа Henry Hub для котировки (не для прогноза и истории).
- `mcp-tools`: схема `price_quote(benchmark="brent")`.
- `agent-bootstrap`: регламент — текущая цена четырёх сортов через `price_quote`.
- `owner-channel`: котировка WTI в утренней сводке — из `price_quote`.
- `data-sources`: котировки всех сортов и третий ряд EIA в `/api/sources` и на вкладке.
- `agent-eval`: 42 кейса, категория `quote` — 5.
- `tool-catalog`: сценарии без номера версии каталога (раздел `forecast.quote.instruments` её повышает).

## Impact

- Код: `forecast/quotes.py` (сорт как параметр), `forecast/data.py` (адрес ряда по разделу EIA), `graphs/calc.py` и
  `graphs/render.py` (метки сортов), `mcp/server.py`, `tools_app.py`, `catalog.py` (схема `forecast.quote.instruments`).
- Каталог и данные: `config/current/forecast.yaml`, `config/current/meta.yaml`, снимок `data/prices/RNGWHHDd.xls`.
- Засев: `ouroboros/memory/knowledge/overview.md`, `ouroboros/memory/WORLD.md`, `ouroboros/schedules/oil-morning-brief.json`.
- UI: `ui/sources.js`, `ui/index.html`; eval: `evals/datasets/dev.jsonl`.
- Документы: README, DESIGN, `docs/guide/sources.md`, `docs/guide/parameters.md`.
- Новая версия агента (регламент, каталог). Сеть — тот же хост `iss.moex.com` и `www.eia.gov`; секретов нет.
