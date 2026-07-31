# $ARTIFACT_CONTRACT
## @PURPOSE Консолидация lint.sh (238 LOC) + check-doc-headers.sh (236 LOC) → два тонких Python-фасада (~100 LOC суммарно)
## @DESCRIPTION
Два entrypoint-скрипта с дублирующейся логикой валидации, оба без Python-модулей:

**`lint.sh` (238 LOC):**
- `check_grepsummary()` — валидация GREP_SUMMARY keywords в файлах
- `check_sh_refs_in_md()` — проверка .sh ссылок в .md файлах
- `check_region_balance()` — парные #region/#endregion
- `namelint()` — проверка naming conventions
- Color helpers (red/green/yellow)

**`check-doc-headers.sh` (236 LOC):**
- `check_grep_summary()` — ДУБЛИКАТ check_grepsummary из lint.sh
- `check_md_sh_refs()` — ДУБЛИКАТ check_sh_refs_in_md из lint.sh
- `check_module_contract()` — валидация MODULE_CONTRACT заголовков
- `check_structure_line()` — STRUCTURE-строка
- `check_file_lines()` — длина файлов
- `check_shellcheck_directives()` — shellcheck аннотации

**Проблема:**
- `entrypoint-manifest.yaml` заявляет что check-doc-headers «replaces former grepsummary from lint.sh», но lint.sh сохранил копии
- 474 строки суммарно на валидацию, которую можно сделать в 2 Python-модулях
- Оба вызываются из `.pre-commit-config.yaml` как separate hooks

**План:**
- Вынести grepsummary + md-sh-refs в общий `core/internal/lint/grepsummary_validator.py`
- Вынести region-balance + namelint + module-contract + structure-line + shellcheck в `core/internal/lint/doc_header_validator.py`
- `lint.sh` → thin facade (~30 LOC)
- `check-doc-headers.sh` → thin facade (~30 LOC)
- Убрать дублирование: lint.sh больше не содержит grepsummary (делегирует в Python)
## @RATIONALE
- 474 LOC дублирования — крупнейший случай копипасты в entrypoints
- Манифест уже заявляет о замене, но код не соответствует — drift
- После миграции все entrypoints >100 LOC будут либо мигрированы, либо исключены политикой
## @ACCEPTANCE_CRITERIA
- AC1: `core/internal/lint/grepsummary_validator.py` — единый grepsummary + md-sh-refs валидатор
- AC2: `core/internal/lint/doc_header_validator.py` — module-contract + structure + shellcheck валидатор
- AC3: `lint.sh` ≤ 40 LOC (color helpers + вызов Python)
- AC4: `check-doc-headers.sh` ≤ 40 LOC (вызов Python)
- AC5: grepsummary больше не дублируется — lint.sh делегирует в grepsummary_validator.py
- AC6: `make lint` проходит идентично (все проверки сохранены)
- AC7: Pre-commit hooks работают идентично
- AC8: `entrypoint-manifest.yaml` обновлён (lint.sh delegates_to содержит Python-модули)
- AC9: `make gate MODE=fast` зелёный
- AC10: Все существующие false-positive исключения сохранены
## @IMPLEMENTS Brief 106
## @IMPACTS core/entrypoints/lint.sh, core/entrypoints/check-doc-headers.sh, core/internal/lint/grepsummary_validator.py (NEW), core/internal/lint/doc_header_validator.py (NEW), tests/unit/test_grepsummary_validator.py (NEW), tests/unit/test_doc_header_validator.py (NEW), core/entrypoint-manifest.yaml
## @REQUIRES Ничего
