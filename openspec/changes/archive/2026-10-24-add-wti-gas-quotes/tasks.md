# Tasks

## 1. Каталог и спот EIA

- [x] 1.1 `forecast.quote.instruments` в схеме каталога (`catalog.py`: `asset`, `title`, `title_en`, `unit`, `unit_en`,
  `eia`) и в `config/current/forecast.yaml` для `brent`, `wti`, `henry_hub`, `ttf`; версия в `config/current/meta.yaml`;
  ключи — в `docs/guide/parameters.md`. Проверка: `oilo catalog check`, тесты каталога и
  `tests/test_consistency.py::test_catalog_keys_are_documented`.
  Итог: `QuoteInstrument` и `Quote.instruments` (ключи — латиница, `brent` обязателен), четыре сорта в
  `forecast.yaml` (version 5), каталог 2026.10.24-3, строка в справочнике. Проверено: `oilo catalog check`,
  `tests/test_catalog.py`, `tests/test_consistency.py`.
- [x] 1.2 Ряд `henry_hub` (`RNGWHHD`, раздел `dnav/ng`) в `forecast/data.py`: адрес по разделу, плановое обновление,
  снимок `data/prices/RNGWHHDd.xls`; прогноз и история его не принимают. Проверка: тесты сценариев «Снимок в
  репозитории», «Неизвестный ряд», «Удачное обновление» спеки `price-forecast` (`tests/test_forecast.py`).
  Итог: `SERIES` с разделом EIA и единицей, адрес `dnav/{section}`, снимок `RNGWHHDd.xls` (по 2026-10-20, 3.10),
  `service.history` проверяет сорт; единица ряда — в `/api/sources`; дельты дополнены (сценарий «всех рядов»,
  поле `unit`). Проверено: два новых теста и обновлённый тест расписания в `tests/test_forecast.py`.

## 2. Котировки по сортам

- [x] 2.1 `forecast/quotes.py`: сорт как параметр (актив и спот — из каталога), кэш, файл `var/quotes/<сорт>.json`,
  последняя ошибка и заморозка — по сорту; спот — только у сортов с рядом EIA. Проверка: `tests/test_quotes.py` на
  записанном ответе ISS по `BR`, `WTI`, `NG`, `TTF` — сценарии «Сорта каталога», выбор контракта NG 24.10 и 25.10, «Два
  вызова подряд» с `wti`, отказы; прежние тесты Brent без изменений.
  Итог: состояние, файл и ошибка по сорту, актив и спот — из каталога, `spot_eia` — `None` у TTF, `NOTE_NO_SPOT`,
  `status()` со списком `instruments`; цена — до трёх знаков (2.628, 93.005). Фикстура `tests/data/iss_quotes.json`
  (BR, WTI, NG, TTF) вместо `iss_br.json`. Проверено: `tests/test_quotes.py`.
- [x] 2.2 `price_quote(benchmark)` в графе `calc` и метки сортов (`quote_label`, `LABEL_EN` с `title_en`); у TTF — без
  хвоста о споте. Проверка: тесты меток на двух языках для четырёх сортов и `verify_answer`, подтверждающий цены WTI,
  Henry Hub ($/MMBtu) и TTF (€/МВт·ч) по F# котировки.
  Итог: `quote_label` с названием сорта и хвостом спота по сорту, `_title_en` и `_spot_en` в `render.py` (два
  общих шаблона вместо шаблонов Brent). `verify_answer` единицы газа узнаёт — риск дизайна не подтвердился.
  Проверено: `tests/test_quotes.py` — 19 тестов, метка Brent прежняя.
- [x] 2.3 Аргумент `benchmark` у `price_quote` в `mcp/server.py` (перечисление из каталога, по умолчанию `brent`, описание
  со списком сортов). Проверка: тест схем инструментов (`tests/test_tools.py`), неверный сорт — `isError` с именем поля.
  Итог: `QuoteBench` — `Literal` из каталога (при невалидном каталоге — только `brent`), описание со списком
  сортов, `INSTRUCTIONS` и описание инструмента — «текущие котировки». Проверено: `tests/test_tools.py`.

## 3. Агент, источники, eval

- [x] 3.1 Регламент `ouroboros/memory/knowledge/overview.md` (текущая цена четырёх сортов → `price_quote(benchmark)`,
  единица в ответе, Urals — оценка), `ouroboros/memory/WORLD.md`, утренняя сводка (котировки Brent и WTI из
  `price_quote`, без котировки из веба). Проверка: `tests/test_turns.py`, `tests/test_schedules.py`, сценарий «Текущая
  цена» спеки `agent-bootstrap`.
  Итог: правило текущей цены — четыре сорта, `benchmark`, единица; веб — без котировок; `WORLD.md` — инструмент и
  котировки в «Данных»; утренняя сводка — `price_quote()` и `price_quote(benchmark="wti")`, `research` только о
  новостях. Проверено: `tests/test_turns.py`, `tests/test_schedules.py`, `tests/test_bootstrap.py`.
- [x] 3.2 Раздел `quote` в `/api/sources` — `instruments` по сортам; таблица котировок на вкладке «Источники»
  (`ui/sources.js`, версия ассетов), `docs/guide/sources.md`. Проверка: тест `/api/sources` (сценарии раздела
  котировок), `node --check` копии `.mjs`, вид во временной консоли.
  Итог: `quotes.status()` — `instruments` по сортам; плитка «Котировка Brent» — по строке `brent`, под таблицей
  EIA — таблица «Котировки Мосбиржи», единица у рядов EIA — из `unit`; `docs/guide/sources.md`; ассеты
  20261024.14. Проверено: тест `/api/sources` в `tests/test_quotes.py`, `node --check`; вид — в задаче 4.2.
- [x] 3.3 Кейсы Q03 «Сколько сейчас стоит нефть WTI?», Q04 «Какая сейчас цена газа Henry Hub?», Q05 «Сколько сейчас
  стоит газ TTF в Европе?» (`price_quote` с `tool_args` по сорту, ссылка F#). Проверка: тест набора кейсов (42, `quote`
  — 5).
  Итог: Q03–Q05 после Q02 с `tool_args.price_quote.benchmark`. Проверено: `test_dev_set_has_quote_cases`.
- [x] 3.4 Документы: README (пример и обзор), `docs/architecture/DESIGN.md` (таблица инструментов, раздел «Цены»),
  `docs/guide/how_it_works.md`. Проверка: `tests/test_consistency.py`.
  Итог: README — обзор, пример «Текущая цена» с газом, источники, Р5; DESIGN — строка `price_quote(benchmark)`
  и раздел «Цены»; how_it_works; owner.md — котировки Brent и WTI в сводке. Проверено: `tests/test_consistency.py`.

## 4. Проверка целиком

- [x] 4.1 Гейты: `ruff check` и `ruff format --check`, `pytest`, `openspec validate --all --strict`. Проверка: всё
  зелёное.
  Итог: ruff — чисто; pytest — 221 пройдено (тест вкладки «Источники» в `test_tools.py` обновлён под три ряда
  EIA, как в дельте `mcp-tools`); `openspec validate --all --strict` — 20 из 20; каталог валиден.
- [x] 4.2 Стенд: `python scripts/deploy.py up`, `deploy.py restart ouroboros`; `oilo ask` о текущей цене WTI, Henry Hub и
  TTF — котировка нужного сорта с единицей, контрактом, временем и F#, проверка пройдена; вкладка «Источники».
  Проверка: ходы в «Ходах».
  Итог: засев обновил регламент, среду и расписание; ouroboros перезапущен. Ходы: WTI — WTI-11.26 103,43 $/барр.,
  11/11; Henry Hub — NG-10.26 2,628 $/MMBtu, 8/8; TTF — TTF-10.26 93,005 €/МВт·ч, 6/6, но агент назвал контракт
  «концом октября» и вывел имя поля — в регламент добавлено «название контракта — месяц исполнения, дата — из
  `time_ru`, имена полей не писать», после выкладки TTF — без этих ошибок, 5/5. Вкладка «Источники» — таблица
  четырёх сортов, единицы у рядов EIA; ошибок в консоли браузера нет.
- [x] 4.3 Выборочный eval с согласия владельца: `oilo eval --only Q01 Q03 Q04 Q05`. Проверка: кейсы пройдены.
  Итог: прогон `20261024-194655-893f` (согласие — «проверь на стенде»): 4/4, замечаний по проверкам нет, ошибок 0,
  p50 22 с, $0.
- [x] 4.4 Поправки по сверке `spec-guard` (DRIFT). Проверка: тесты, выкладка, ход на английском.
  Итог: `unit_en` в JSON котировки; `NOTE_NO_SPOT` без привязки к ICE; `refresh_once` не падает, если у ряда нет
  файла (дата пустая); тест «ряд спота сорта есть в `data.SERIES`»; тексты «котировка
  Brent» в графе `calc`, «Ходах», каталоге и справочнике — под все сорта; дельты: `unit_en` и `note` без спота, файлы
  EIA в «Ряды цен EIA», сценарий «У ряда ещё нет файла», единица и `time_ru` в «Текущая цена», `tool-catalog` без
  номера версии в сценариях. Проверено: `tests/test_quotes.py`, `tests/test_forecast.py`.

## Workflow follow-up

- `/opsx:verify add-wti-gas-quotes` и субагент `spec-guard`.
- `/opsx:archive add-wti-gas-quotes`; Purpose спек `price-quote`, `price-forecast`, `data-sources` — под четыре сорта и
  третий ряд EIA (дельта Purpose не меняет); строка `price-quote` в таблице скилла `sdd`; `openspec validate --all
  --strict` и `pytest tests/test_consistency.py`.
- Один коммит: код, документы, живые спеки и архив change.
