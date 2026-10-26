# Каталог поведения инструментов (config/)

Поведение графов LangGraph сервиса инструментов, вынесенное из кода; схема и загрузчик — `src/oil_ouroboros/catalog.py`.
Поведение самого агента — не здесь, а в его памяти (засев `ouroboros/memory/`).

`current/` — рабочий слот: `llm.yaml`, `retrieval.yaml`, `research.yaml`, `web.yaml`, `source_tiers.yaml`,
`verify.yaml`, `forecast.yaml`, `indexing.yaml` (правка — с переиндексацией), `reports.yaml`, `meta.yaml`
(`catalog_version`, журнал изменений), `prompts/*.md`. Комментарий в строке YAML — описание параметра в консоли.

Слои (поздний переопределяет ранний): слот `CATALOG_SLOT` (`current` или экспериментальный `exp-*`) →
`LLM_MODEL_SUBAGENT` (слой env) → `CATALOG_SET="retrieval.top_k=8;…"` (слой set: разделы llm, retrieval, web,
research, forecast, reports). Схема строгая: неизвестный ключ, повтор ключа, значение вне диапазона — ошибка загрузки,
сервис инструментов не готов (503). Ссылка `слот@версия#отпечаток` пишется в каждый трейс и прогон eval. Каталог
входит в образ: правка файлов действует после `docker compose up -d --build tools`.

```bash
docker compose exec tools oilo catalog check        # валиден ли каталог
docker compose exec tools oilo catalog show         # значение, слой и описание каждого параметра
docker compose exec tools oilo catalog diff exp-x   # чем слот exp-x отличается от текущего
```

Что делает каждый параметр и что будет при его изменении — [docs/guide/parameters.md](../docs/guide/parameters.md).
