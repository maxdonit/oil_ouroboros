# Прогон eval 20261023-134859-4822 (release)

Набор `dev.jsonl`, повторов 1, длительность 19 мин. Каждый кейс — настоящий ход агента: Ouroboros 7.7.0@e41596c (`openai-compatible::anthropic/claude-haiku-5.5`) → инструменты LangGraph по MCP. Версия агента `5e8c18567745`, каталог инструментов `current@2026.10.23-1#9dce9983d243`.

| Метрика | Значение |
|---|---|
| Кейсы пройдены | 32/37 = 86% (95% ДИ Уилсона 72%–94%) |
| Цифры показанного ответа прошли проверку кодом по источникам хода | 100.0% |
| Цена ответа, среднее | $0.0095 (всего $0.35) |
| Время ответа p50 / p95 | 68 с / 88 с |
| Раундов агента на ответ | 3.35 |
| Ошибок прогона | 0 |

**По категориям:** запрос инвестсовета 100%, комбинированные 100%, расхождение агентств 0%, уточнение в диалоге 50%, прогноз 100%, история цен 100%, честность (нет в базе) 100%, вне компетенции 100%, по отчётам 83%, сценарий 100%, по вебу 100%.

**По проверкам** (доля кейсов, где проверка пройдена; _курсив_ — дисциплина протокола, исход кейса не решает): admits_missing 100%, _answer_is_checked_text_ 97%, cites_orgs 96%, cites_report 100%, cites_tool 100%, cites_web 100%, facts 84%, labels 100%, language 100%, must_include 100%, must_not_include 100%, no_error 100%, no_tools 100%, persona_clean 100%, research_first 100%, route_web 82%, scope 100%, source_quality 100%, tool_args 100%, tools 100%, verified 100%.

| Кейс | Категория | Итог | Ход агента | с | $ | Не пройдено |
|---|---|---|---|---|---|---|
| A01 | advice | ✅ | research → forecast_price → verify_answer → verify_answer | 78 | 0.0130 | — |
| B01 | report | ✅ | research → verify_answer | 69 | 0.0092 | — |
| C01 | combined | ✅ | research → verify_answer | 67 | 0.0098 | — |
| C02 | combined | ✅ | research → price_history → verify_answer | 68 | 0.0101 | _answer_is_checked_text_: источник ответа: reverified_final |
| F01 | forecast | ✅ | research → forecast_price → verify_answer → verify_answer → verify_answer | 74 | 0.0149 | — |
| F02 | forecast | ✅ | research → forecast_price → verify_answer → verify_answer | 79 | 0.0133 | — |
| F03 | forecast | ✅ | research → forecast_price → verify_answer → verify_answer | 74 | 0.0116 | — |
| FU1 | followup | ✅ | forecast_price → research → verify_answer → verify_answer → verify_answer | 88 | 0.0133 | — |
| FU2 | followup | ❌ | research → verify_answer → verify_answer | 77 | 0.0111 | route_web: ожидался веб=False, был=True |
| H01 | history | ✅ | price_history → research → verify_answer → verify_answer → verify_answer | 95 | 0.0142 | — |
| O01 | out_of_scope | ✅ | без инструментов | 14 | 0.0040 | — |
| O02 | out_of_scope | ✅ | без инструментов | 14 | 0.0040 | — |
| O03 | out_of_scope | ✅ | без инструментов | 14 | 0.0040 | — |
| O04 | out_of_scope | ✅ | без инструментов | 14 | 0.0040 | — |
| R01 | report | ✅ | research → verify_answer | 48 | 0.0081 | — |
| R02 | report | ✅ | research → verify_answer | 54 | 0.0060 | — |
| R03 | report | ✅ | research → verify_answer | 51 | 0.0079 | — |
| R04 | report | ✅ | research → verify_answer | 49 | 0.0075 | — |
| R05 | report | ❌ | research → verify_answer → verify_answer | 65 | 0.0102 | route_web: ожидался веб=False, был=True; facts: нет фактов: ['−10 млн барр. в сентябре'] |
| R06 | report | ❌ | research → search_reports → verify_answer | 82 | 0.0105 | route_web: ожидался веб=False, был=True; facts: нет фактов: ['−1.9 млн б/с в 3 кв. (текст с. 7; таблица 3a: 1.87)', '−0.7 млн б/с в 4 кв.' |
| R07 | report | ✅ | research → verify_answer → verify_answer → verify_answer | 74 | 0.0122 | — |
| R08 | report | ✅ | research → verify_answer | 53 | 0.0074 | — |
| R09 | conflict | ❌ | research → verify_answer → verify_answer → verify_answer | 87 | 0.0141 | facts: нет фактов: ['оценка EIA по кварталу (текст: 1.9; таблица 3a: 1.87)']; cites_orgs: нет ссылок на ['EIA']; есть ['IEA'] |
| R10 | report | ❌ | research → search_reports → verify_answer | 75 | 0.0101 | route_web: ожидался веб=False, был=True |
| R11 | report | ✅ | research → price_history → verify_answer | 50 | 0.0085 | — |
| R12 | report | ✅ | research → verify_answer → verify_answer | 68 | 0.0103 | — |
| R13 | report | ✅ | research → verify_answer | 50 | 0.0077 | — |
| R14 | report | ✅ | research → verify_answer | 53 | 0.0076 | — |
| R15 | report | ✅ | research → verify_answer | 48 | 0.0077 | — |
| R16 | report | ✅ | research → verify_answer | 53 | 0.0063 | — |
| R17 | report | ✅ | research → verify_answer | 56 | 0.0083 | — |
| R18 | report | ✅ | research → verify_answer → verify_answer | 70 | 0.0104 | — |
| S01 | scenario | ✅ | research → supply_shock_scenario → verify_answer → verify_answer | 70 | 0.0120 | — |
| U01 | honesty | ✅ | research → search_web → verify_answer | 76 | 0.0102 | — |
| W01 | web | ✅ | research → search_web → verify_answer | 70 | 0.0107 | — |
| W02 | web | ✅ | research → search_web → verify_answer | 79 | 0.0122 | — |
| W03 | web | ✅ | research → verify_answer | 56 | 0.0082 | — |
