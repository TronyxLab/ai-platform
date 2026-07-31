# $ARTIFACT_CONTRACT
## @PURPOSE Миграция core/internal/validate/validate.sh (251 LOC) → Python-модуль + тонкий shell-фасад
## @DESCRIPTION
`core/internal/validate/validate.sh` (251 LOC) — валидатор файлов с авто-обнаружением схем.
Entrypoint `core/entrypoints/validate.sh` уже тонкий (18 LOC), но внутренний скрипт содержит:

- Авто-обнаружение YAML/JSON файлов для валидации
- Schema resolution (module.schema.json, platform-env.schema.json, etc.)
- jsonschema валидация (УЖЕ делегирована в `jsonschema_validate.py` — DevPlan 093 W1)
- FILES фильтрация
- Lint mode vs validate mode routing

Фактически после DevPlan 093, jsonschema-валидация уже в Python. Осталась оркестрация:
обход файлов, определение схемы, вызов валидатора, агрегация ошибок.

**План:** вынести оркестрацию в `core/internal/validate/validate_orchestrator.py`.
Shell оставляет: arg parsing, вызов Python, exit code.
## @RATIONALE
- 251 LOC — последний «толстый» internal-скрипт валидации
- jsonschema уже в Python (093) — естественное завершение миграции
- После миграции все validate-скрипты >200 LOC будут в Python
## @ACCEPTANCE_CRITERIA
- AC1: `core/internal/validate/validate_orchestrator.py` с file discovery + schema resolution + validation orchestration
- AC2: Shell-фасад `validate.sh` (internal) ≤ 50 LOC
- AC3: `make validate` проходит идентично
- AC4: `make validate FILES=...` работает идентично
- AC5: `make lint` (validate --lint) работает идентично
- AC6: Авто-обнаружение схем идентично (module.schema.json, platform-env.schema.json, etc.)
- AC7: Все ошибки валидации выводятся с теми же путями и сообщениями
- AC8: `make gate MODE=fast` зелёный
- AC9: DevPlan 093 AC3 (PYOF heredoc → CLI) не регрессирует
## @IMPLEMENTS Brief 107
## @IMPACTS core/internal/validate/validate.sh, core/internal/validate/validate_orchestrator.py (NEW), core/internal/validate/jsonschema_validate.py (возможно MODIFY), tests/unit/test_validate_orchestrator.py (NEW)
## @REQUIRES jsonschema_validate.py (уже существует, DevPlan 093)
