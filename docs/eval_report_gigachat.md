# Прогон eval 20261026-144557-afae (gigachat-full-20261026)

Набор `dev.jsonl`, повторов 1, длительность 11 мин. Каждый кейс — настоящий ход агента: Ouroboros 7.7.0@e41596c (`openai-compatible::GigaChat-3-Ultra`) → инструменты LangGraph по MCP. Версия агента `aa4564a49bd8`, каталог инструментов `current@2026.10.24-3#b21faf41c6c0`.
_Раунды агента в этом отчёте — по событиям Ouroboros и неполные (на GigaChat цена всё равно $0). Весь прогон по журналу LLM-шлюза — 190 вызовов, ~3,8 млн токенов._


| Метрика | Значение |
|---|---|
| Кейсы пройдены | 35/42 = 83% (95% ДИ Уилсона 69%–92%) |
| Цифры показанного ответа прошли проверку кодом по источникам хода | 100.0% |
| Цена ответа, среднее | $0.0000 (всего $0.00) |
| Время ответа p50 / p95 | 30 с / 49 с |
| Раундов агента на ответ | 0.86 |
| Ошибок прогона | 0 |

**По категориям:** запрос инвестсовета 0%, комбинированные 100%, расхождение агентств 0%, уточнение в диалоге 100%, прогноз 100%, история цен 100%, честность (нет в базе) 100%, вне компетенции 100%, текущая цена 100%, по отчётам 78%, сценарий 100%, по вебу 67%.

**По проверкам** (доля кейсов, где проверка пройдена; _курсив_ — дисциплина протокола, исход кейса не решает): admits_missing 100%, _answer_is_checked_text_ 87%, cites_orgs 96%, cites_report 96%, cites_tool 100%, cites_web 80%, facts 79%, labels 100%, language 100%, must_include 100%, must_not_include 100%, no_error 100%, no_tools 100%, persona_clean 100%, research_first 95%, route_web 91%, scope 95%, source_quality 100%, tool_args 100%, tools 100%, verified 95%.

| Кейс | Категория | Итог | Ход агента | с | $ | Не пройдено |
|---|---|---|---|---|---|---|
| A01 | advice | ❌ | без инструментов | 7 | 0.0000 | scope: ожидался отказ=False, получено True; инструменты []; research_first: первый вызов: []; verified: финальная проверка: 0/0 подтверждено, неизвестных или чужих ссылок 0 |
| B01 | report | ✅ | research → verify_answer | 42 | 0.0000 | — |
| C01 | combined | ✅ | research → verify_answer | 37 | 0.0000 | — |
| C02 | combined | ✅ | research → verify_answer → verify_answer | 55 | 0.0000 | — |
| F01 | forecast | ✅ | forecast_price → verify_answer | 32 | 0.0000 | — |
| F02 | forecast | ✅ | forecast_price → verify_answer → verify_answer | 29 | 0.0000 | _answer_is_checked_text_: источник ответа: reverified_final |
| F03 | forecast | ✅ | research → forecast_price → verify_answer → verify_answer | 45 | 0.0000 | _answer_is_checked_text_: источник ответа: reverified_final |
| FU1 | followup | ✅ | forecast_price → verify_answer | 21 | 0.0000 | — |
| FU2 | followup | ✅ | research → verify_answer → verify_answer | 46 | 0.0000 | — |
| H01 | history | ✅ | price_history → verify_answer | 29 | 0.0000 | — |
| O01 | out_of_scope | ✅ | без инструментов | 6 | 0.0000 | — |
| O02 | out_of_scope | ✅ | без инструментов | 8 | 0.0000 | — |
| O03 | out_of_scope | ✅ | без инструментов | 10 | 0.0000 | — |
| O04 | out_of_scope | ✅ | без инструментов | 6 | 0.0000 | — |
| Q01 | quote | ✅ | price_quote → verify_answer | 19 | 0.0000 | — |
| Q02 | quote | ✅ | price_quote → verify_answer | 18 | 0.0000 | — |
| Q03 | quote | ✅ | price_quote → verify_answer | 17 | 0.0000 | — |
| Q04 | quote | ✅ | price_quote → verify_answer | 17 | 0.0000 | — |
| Q05 | quote | ✅ | price_quote → verify_answer | 17 | 0.0000 | — |
| R01 | report | ✅ | research → verify_answer | 24 | 0.0000 | — |
| R02 | report | ✅ | list_reports → research → verify_answer | 40 | 0.0000 | — |
| R03 | report | ✅ | research → verify_answer | 29 | 0.0000 | — |
| R04 | report | ✅ | research → verify_answer | 29 | 0.0000 | — |
| R05 | report | ❌ | research → verify_answer → verify_answer | 34 | 0.0000 | facts: нет фактов: ['−10 млн барр. в сентябре'] |
| R06 | report | ❌ | research → verify_answer → verify_answer | 36 | 0.0000 | facts: нет фактов: ['−1.9 млн б/с в 3 кв. (текст с. 7; таблица 3a: 1.87)', '−0.7 млн б/с в 4 кв.'; cites_report: нет ссылок типа report; есть []; cites_orgs: нет ссылок на ['EIA']; есть []; _answer_is_checked_text_: источник ответа: reverified_final |
| R07 | report | ✅ | list_reports → research → verify_answer → verify_answer | 43 | 0.0000 | _answer_is_checked_text_: источник ответа: reverified_final |
| R08 | report | ✅ | research → verify_answer | 32 | 0.0000 | — |
| R09 | conflict | ❌ | research → verify_answer → verify_answer → verify_answer | 58 | 0.0000 | facts: нет фактов: ['оценка EIA по кварталу (текст: 1.9; таблица 3a: 1.87)'] |
| R10 | report | ✅ | research → verify_answer | 39 | 0.0000 | — |
| R11 | report | ✅ | price_history → research → verify_answer | 29 | 0.0000 | — |
| R12 | report | ✅ | research → verify_answer | 30 | 0.0000 | — |
| R13 | report | ✅ | research → verify_answer | 29 | 0.0000 | — |
| R14 | report | ✅ | research → verify_answer → verify_answer | 39 | 0.0000 | — |
| R15 | report | ✅ | research → verify_answer | 23 | 0.0000 | — |
| R16 | report | ❌ | research → verify_answer → verify_answer | 36 | 0.0000 | facts: нет фактов: ['сентябрь: +0.4 млн б/с', 'октябрь: +0.2 млн б/с']; _answer_is_checked_text_: источник ответа: reverified_final |
| R17 | report | ❌ | list_reports → research → verify_answer | 37 | 0.0000 | route_web: ожидался веб=False, был=True |
| R18 | report | ✅ | research → verify_answer | 34 | 0.0000 | — |
| S01 | scenario | ✅ | list_reports → research → supply_shock_scenario → verify_answer | 49 | 0.0000 | — |
| U01 | honesty | ✅ | research → search_reports → verify_answer | 43 | 0.0000 | — |
| W01 | web | ✅ | research → search_web → verify_answer | 45 | 0.0000 | — |
| W02 | web | ❌ | без инструментов | 7 | 0.0000 | scope: ожидался отказ=False, получено True; инструменты []; research_first: первый вызов: []; route_web: ожидался веб=True, был=False; cites_web: нет ссылок типа web; есть []; verified: финальная проверка: 0/0 подтверждено, неизвестных или чужих ссылок 0 |
| W03 | web | ✅ | research → verify_answer | 35 | 0.0000 | — |
