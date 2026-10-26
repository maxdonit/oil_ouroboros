# Spec Delta

## MODIFIED Requirements

### Requirement: Состав слота
Каталог SHALL загружаться из `<CONFIG_DIR>/<CATALOG_SLOT>/` (`CONFIG_DIR` по умолчанию `<корень проекта>/config`, в
образе `/app/config`; `CATALOG_SLOT` — `current` по умолчанию или экспериментальный `exp-*`). Слот MUST содержать
ровно десять YAML-разделов — `meta`, `llm`, `retrieval`, `indexing`, `web`, `research`, `verify`, `forecast`,
`reports`, `source_tiers` — и промпты `prompts/research_plan.md`, `prompts/select_grade.md`. Каталог входит в образ:
правка действует после пересборки.

#### Scenario: Нет слота
- **WHEN** `CATALOG_SLOT=exp-x`, а каталога этого слота (`config/<слот>/`) нет
- **THEN** загрузка падает с проблемой `нет слота каталога <путь>`

#### Scenario: Лишние и недостающие файлы
- **WHEN** в слоте есть `stray.yaml` и `prompts/extra.md`, но нет `verify.yaml`
- **THEN** проблемы: `stray.yaml: файл без схемы (переименуйте или добавьте раздел в catalog.py)`,
  `prompts/extra.md: промпт не используется кодом (известные: research_plan, select_grade)`, `verify.yaml: нет файла`

#### Scenario: Паспорт каталога
- **WHEN** читается `config/current/meta.yaml`
- **THEN** в нём `catalog_version` (строка вида `ГГГГ.ММ.ДД-N`, повышает человек при содержательной правке),
  `agent: oil-analyst-ouroboros` и журнал `changelog`
