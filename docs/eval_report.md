# Прогон eval 20261026-150009-f571 (openrouter-full-20261026)

Набор `dev.jsonl`, повторов 1, длительность 22 мин. Каждый кейс — настоящий ход агента: Ouroboros 7.7.0@e41596c (`openai-compatible::anthropic/claude-haiku-5.5`) → инструменты LangGraph по MCP. Версия агента `a3357e025947`, каталог инструментов `current@2026.10.24-3#9c74c772e3ff`.
_Цена в этом отчёте — по событиям Ouroboros, и стоимость раундов агента в них неполная. Фактическая цена по журналу LLM-шлюза: $0.33 за прогон, ~$0.008 на ответ._


| Метрика | Значение |
|---|---|
| Кейсы пройдены | 38/42 = 90% (95% ДИ Уилсона 78%–96%) |
| Цифры показанного ответа прошли проверку кодом по источникам хода | 100.0% |
| Цена ответа, среднее | $0.0024 (всего $0.10) |
| Время ответа p50 / p95 | 61 с / 98 с |
| Раундов агента на ответ | 0.62 |
| Ошибок прогона | 0 |

**По категориям:** запрос инвестсовета 0%, комбинированные 100%, расхождение агентств 100%, уточнение в диалоге 100%, прогноз 100%, история цен 100%, честность (нет в базе) 100%, вне компетенции 100%, текущая цена 100%, по отчётам 83%, сценарий 100%, по вебу 100%.

**По проверкам** (доля кейсов, где проверка пройдена; _курсив_ — дисциплина протокола, исход кейса не решает): admits_missing 100%, _answer_is_checked_text_ 97%, cites_orgs 100%, cites_report 100%, cites_tool 100%, cites_web 100%, facts 95%, labels 100%, language 100%, must_include 100%, must_not_include 100%, no_error 100%, no_tools 100%, persona_clean 100%, research_first 97%, route_web 86%, scope 100%, source_quality 100%, tool_args 100%, tools 100%, verified 100%.

| Кейс | Категория | Итог | Ход агента | с | $ | Не пройдено |
|---|---|---|---|---|---|---|
| A01 | advice | ❌ | verify_answer | 24 | 0.0000 | research_first: первый вызов: ['verify_answer'] |
| B01 | report | ✅ | research → verify_answer | 90 | 0.0012 | — |
| C01 | combined | ✅ | research → verify_answer | 65 | 0.0028 | — |
| C02 | combined | ✅ | research → verify_answer → verify_answer | 74 | 0.0035 | — |
| F01 | forecast | ✅ | research → forecast_price → price_quote → verify_answer → verify_answer | 80 | 0.0037 | — |
| F02 | forecast | ✅ | forecast_price → research → price_quote → verify_answer → verify_answer | 90 | 0.0013 | _answer_is_checked_text_: источник ответа: reverified_final |
| F03 | forecast | ✅ | research → forecast_price → verify_answer → verify_answer | 83 | 0.0012 | — |
| FU1 | followup | ✅ | research → forecast_price → verify_answer → verify_answer | 103 | 0.0015 | — |
| FU2 | followup | ✅ | research → verify_answer → verify_answer | 75 | 0.0020 | — |
| H01 | history | ✅ | research → price_history → verify_answer | 63 | 0.0034 | — |
| O01 | out_of_scope | ✅ | без инструментов | 16 | 0.0027 | — |
| O02 | out_of_scope | ✅ | без инструментов | 17 | 0.0000 | — |
| O03 | out_of_scope | ✅ | без инструментов | 14 | 0.0000 | — |
| O04 | out_of_scope | ✅ | без инструментов | 21 | 0.0000 | — |
| Q01 | quote | ✅ | price_quote → verify_answer | 32 | 0.0000 | — |
| Q02 | quote | ✅ | price_quote → verify_answer | 33 | 0.0000 | — |
| Q03 | quote | ✅ | price_quote → verify_answer | 32 | 0.0000 | — |
| Q04 | quote | ✅ | price_quote → verify_answer | 32 | 0.0000 | — |
| Q05 | quote | ✅ | price_quote → research → verify_answer → verify_answer | 73 | 0.0023 | — |
| R01 | report | ✅ | research → verify_answer | 79 | 0.0035 | — |
| R02 | report | ✅ | research → verify_answer | 53 | 0.0014 | — |
| R03 | report | ✅ | research → verify_answer | 57 | 0.0010 | — |
| R04 | report | ✅ | research → verify_answer | 53 | 0.0037 | — |
| R05 | report | ✅ | research → verify_answer → verify_answer | 68 | 0.0026 | — |
| R06 | report | ❌ | research → search_reports → search_reports → search_reports → verify_answer | 147 | 0.0111 | route_web: ожидался веб=False, был=True |
| R07 | report | ✅ | research → verify_answer → verify_answer | 61 | 0.0027 | — |
| R08 | report | ✅ | research → verify_answer → verify_answer | 68 | 0.0024 | — |
| R09 | conflict | ✅ | research → verify_answer → verify_answer | 98 | 0.0048 | — |
| R10 | report | ❌ | research → verify_answer | 69 | 0.0035 | route_web: ожидался веб=False, был=True; facts: нет фактов: ['exports 700 kb/d lower y-o-y'] |
| R11 | report | ✅ | price_history → research → verify_answer → verify_answer | 70 | 0.0011 | — |
| R12 | report | ✅ | research → verify_answer | 56 | 0.0010 | — |
| R13 | report | ✅ | research → verify_answer | 57 | 0.0042 | — |
| R14 | report | ❌ | research → verify_answer | 56 | 0.0065 | route_web: ожидался веб=False, был=True |
| R15 | report | ✅ | research → verify_answer | 56 | 0.0010 | — |
| R16 | report | ✅ | research → verify_answer | 55 | 0.0038 | — |
| R17 | report | ✅ | research → verify_answer | 52 | 0.0022 | — |
| R18 | report | ✅ | research → verify_answer | 58 | 0.0011 | — |
| S01 | scenario | ✅ | research → supply_shock_scenario → verify_answer → verify_answer | 90 | 0.0013 | — |
| U01 | honesty | ✅ | research → search_reports → verify_answer | 76 | 0.0071 | — |
| W01 | web | ✅ | research → search_web → verify_answer → verify_answer | 88 | 0.0043 | — |
| W02 | web | ✅ | research → verify_answer | 60 | 0.0011 | — |
| W03 | web | ✅ | research → search_web → verify_answer | 75 | 0.0054 | — |
