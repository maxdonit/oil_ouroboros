# Spec Delta

## MODIFIED Requirements

### Requirement: Отпечаток, lock и ref
Загрузчик SHALL вычислять `fp` — первые 12 hex sha256 от JSON итоговых значений всех разделов (после слоёв, ключи
отсортированы) и sha промптов; `lock` — для каждого файла `<версия>#<12 hex sha текста>`; `ref` —
`<слот>@<catalog_version>#<fp>`. Тексты перед хешированием нормализуются: без BOM, переводы строк LF, без хвостовых
пробелов. `ref` пишется в каждый трейс инструментов и прогон eval: разница двух прогонов объясняется разницей `lock`.

#### Scenario: Текущий каталог
- **WHEN** загружается слот `current` без слоёв
- **THEN** `fp` — 12 символов, `ref` — `current@<catalog_version>#<fp>`, в `lock` есть `retrieval.yaml` и
  `prompts/research_plan.md`

#### Scenario: Хвостовые пробелы
- **WHEN** в тексте `research_plan.md` к каждой строке добавлены пробелы в конце
- **THEN** `fp` не меняется

### Requirement: Команды oilo catalog
`oilo catalog [check|show|diff] [слот]` (по умолчанию `check`) SHALL проверять и показывать текущий каталог; при
невалидном текущем каталоге любая команда MUST печатать `каталог невалиден:` со списком проблем и завершаться с кодом 2.

#### Scenario: Проверка
- **WHEN** `oilo catalog check` на валидном `current`
- **THEN** печатается `каталог валиден: current@<catalog_version>#<fp> (файлов: 12)`, код 0

#### Scenario: Итоговые значения
- **WHEN** `oilo catalog show`
- **THEN** печатается `ref`, по строке `<ключ> <значение> [<слой>] <описание>` и строки
  `prompts/<имя> v<version> #<sha>  <purpose>`

#### Scenario: Сравнение слотов
- **WHEN** `oilo catalog diff exp-topk8`
- **THEN** печатается `<ref текущего>  vs  <ref exp-topk8>` и файлы с разным `lock`: `<файл> <было или —> → <стало или —>`
