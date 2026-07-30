$START_BRIEF
# Brief 093 — Validate & Checkpoint Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Мигрировать 2 shell-модуля с бизнес-логикой: `validate.sh` (380 LOC, PYOF jsonschema heredoc) и `checkpoint.sh` (203 LOC, 3 inline python3 для state.json R/W). Ликвидировать дублирование: `checkpoint.sh` дублирует существующий Python `state_machine.py`.
- **DESCRIPTION:** (1) `validate.sh` → расширение существующего `core/internal/validate/validate_module_yaml.py` (jsonschema уже есть). (2) `checkpoint.sh` → удалить (заменить прямым вызовом `state_machine.py` из 087). Shell-фасад `validate.sh` остаётся <50 LOC.
- **RATIONALE:** `checkpoint.sh` — 3 inline python3 блока для state.json R/W, но `state_machine.py` (из DevPlan 087) уже управляет state.json. Это **дублирование функциональности** = потенциальный источник дрейфа. `validate.sh` — jsonschema-валидация через PYOF heredoc, нарушает языковую политику Tier 1.
- **ACCEPTANCE_CRITERIA:** `make validate` работает идентично; `checkpoint.sh` удалён или пустой фасад; `state_machine.py` единственный source of truth для state.json; 0 inline python3 в обоих; unit-тесты.
- **IMPLEMENTS:** Закрытие gap «checkpoint.sh дублирует state_machine.py» + извлечение PYOF heredoc из validate.sh.
- **IMPACTS:** `core/internal/validate/validate.sh`, `core/lib/checkpoint.sh`, `core/internal/validate/validate_module_yaml.py`, `core/internal/bootstrap/lifecycle/state_machine.py`.
- **REQUIRES:** **DevPlan 087 STABLE** (state_machine.py финализирован). Блокируется 091 Wave B.

## Current Status (Audit 2026-07-30)
- **validate.sh:** 380 LOC, 1 PYOF heredoc (15 строк) с `jsonschema.validate()`. `validate_module_yaml.py` уже существует — нужно расширить, не создавать новый.
- **checkpoint.sh:** 203 LOC, 3 inline python3 (строки 52-69, 81-103, 172-188). `state_machine.py` уже управляет state.json напрямую.
- **Дублирование:** `checkpoint.sh` функции `checkpoint_save()`, `checkpoint_load()`, `checkpoint_clear()` дублируют `state_machine.py` методы `save_state()`, `load_state()`, `clear_state()`.

## Key Findings (verificated)
- `validate.sh` вызывает inline jsonschema для валидации module.yaml. Уже есть `validate_module_yaml.py` — нужно вызвать его вместо PYOF.
- `checkpoint.sh` вызывается из `node-lifecycle.sh` и `steps.py`. После 087 (14 фаз) state.json пишется `state_machine.py` напрямую — `checkpoint.sh` становится **dead code**.
- `node-lifecycle.sh` после 087 может быть тоже упрощён (зависит от 091 Wave B).

## Required Actions

### Wave 1: checkpoint.sh elimination
1. Найти все вызовы `checkpoint.sh` функций (`checkpoint_save/load/clear`) — grep по core/.
2. Заменить вызовы на прямой импорт `state_machine.py`.
3. **Удалить** `core/lib/checkpoint.sh` (или пустой фасад если вызывается из shell).
4. Удалить 3 inline python3 блока.
5. Unit-тест: state round-trip через `state_machine.py` только.

### Wave 2: validate.sh migration
6. Расширить `validate_module_yaml.py`: добавить валидацию, которая была в PYOF heredoc.
7. `validate.sh` → фасад <50 LOC: dispatch на `python3 -m core.internal.validate.validate_module_yaml`.
8. Удалить PYOF heredoc.
9. Unit-тесты: valid-module, invalid-missing-field, invalid-type, extra-field-warning.

## Verification
- `make validate` — все module.yaml валидируются, ошибки показываются.
- `grep -rn "python3 -c\|python3 <<\|python3 - <<" core/internal/validate/ core/lib/checkpoint.sh` → 0.
- `grep -rn "checkpoint_" core/` → либо 0, либо только в `state_machine.py`.
- `make gate MODE=fast` зелёный.

## Anti-Loop Note
Не реорганизовать `validate_module_yaml.py` архитектурно — только добавить недостающую валидацию из PYOF. Архитектурный рефакторинг — отдельный план если потребуется.

$END_BRIEF
