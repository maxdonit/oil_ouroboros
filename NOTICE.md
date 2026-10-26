# Источники данных в репозитории

Данные лежат в репозитории, чтобы база знаний и расчётный модуль работали сразу после клонирования, без сети.
Права на них принадлежат правообладателям. Агент указывает источник в каждом ответе: метка `[Отчёт …]` и список
источников со ссылками.

| Файлы | Что это | Правообладатель и условия |
|---|---|---|
| `data/reports/eia_steo_2026-09.pdf`, `eia_steo_2026-10.pdf` | Short-Term Energy Outlook, выпуски сентябрь и октябрь 2026 | U.S. Energy Information Administration — public domain. https://www.eia.gov/outlooks/steo/ |
| `data/reports/opec_momr_2026-09.pdf`, `opec_momr_2026-10.pdf` | Monthly Oil Market Report, выпуски сентябрь и октябрь 2026 | © Organization of the Petroleum Exporting Countries (OPEC); воспроизводится с указанием источника. https://www.opec.org/monthly-oil-market-report.html |
| `data/reports/iea_omr_2026-09.html`, `iea_omr_2026-10.html` | Oil Market Report, публичная часть, выпуски сентябрь и октябрь 2026 | © International Energy Agency (IEA); публичная страница отчёта, с указанием источника. https://www.iea.org/reports/oil-market-report |
| `data/prices/RBRTEd.xls`, `RWTCd.xls`, `RNGWHHDd.xls` | Дневные спотовые цены Brent (RBRTE), WTI (RWTC) и газа Henry Hub (RNGWHHD) — снимок для первого запуска | U.S. Energy Information Administration — public domain. https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm, https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm |
| `data/source_quality/domain_pc1.csv` | Агрегированная оценка качества 11 520 новостных доменов (pc1, 0–1) | Lin H., Lasser J., Lewandowsky S. et al. «High level of correspondence across different news domain quality rating sets», PNAS Nexus 2(9), 2023. https://doi.org/10.1093/pnasnexus/pgad286 |

Новые выпуски наблюдатель скачивает в `var/reports` (в git не попадают); их условия те же, что у выпусков выше.
