#!/usr/bin/env python3
# GREP_SUMMARY: lint, grepsummary-validator, doc-header-validator, namelint, package-contract
# STRUCTURE: ┌core/internal/lint/┐ → ◇ grepsummary_validator.py (keywords + .sh refs) → ◇ doc_header_validator.py (doc headers + namelint) → ⎋ thin shell facades
# region MODULE_CONTRACT
## @purpose  package-contract for core/internal/lint/ — консолидация lint-валидации (DevPlan 106).
##           Strangler-Fig: lint.sh (238 LOC) + check-doc-headers.sh (236 LOC) → Python-модули.
## @scope    grepsummary-validator (GREP_SUMMARY keywords + .sh refs в .md, scan-all/staged),
##           doc-header-validator (doc-header проверки + namelint make-target валидация)
## @invariants
##   1. Единственная cross-import: doc_header_validator → grepsummary_validator (sequential dependency)
##   2. Оба модуля импортируемы standalone через python3 -m (repo root на sys.path)
##   3. Каждый модуль self-hosted: GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT (проходит check-doc-headers)
## @rationale Манифест заявлял «replaces former grepsummary from lint.sh» без фактического переноса
##            (drift P7); консолидация устраняет дублирование P1/P2 и покрывает логику unit-тестами.
## @changes 2026-07-31 | Created (DevPlan 106 Strangler-Fig)
# endregion MODULE_CONTRACT
