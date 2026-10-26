# Spec Delta

## MODIFIED Requirements

### Requirement: API вкладки «Прогноз»
Сервис инструментов SHALL отдавать `GET /api/forecast` (`benchmark`=brent, `horizon`=3, `method`=auto; полный
результат со всей таблицей скилла), `GET /api/forecast/history` (`start`=2024-01-01, `freq`=M; без прореживания) и
`GET /api/forecast/scenario` (`delta_mbd` обязателен; `benchmark`, `horizon`, `compliance`=1, `offset`=0). Консоль
проксирует их с токеном `TOOLS_API_TOKEN`; они открыты без входа, вкладка есть в обоих режимах. При старте tools прогноз Brent и
WTI на 3 мес. считается в фоне.

#### Scenario: Запросы вкладки
- **WHEN** открыта вкладка «Прогноз» (`ui/forecast.js`)
- **THEN** она запрашивает прогноз с выбранными рядом, горизонтом 1–12 и методом и историю с `start=2018-01-01`, `freq=M`
- **AND** сценарий — `GET /api/forecast/scenario` через 450 мс после изменения ползунков

#### Scenario: Ошибка расчёта
- **WHEN** расчёт падает (например, нет файлов цен)
- **THEN** API отвечает HTTP 500 (своего обработчика ошибок нет), вкладка показывает блок ошибки
