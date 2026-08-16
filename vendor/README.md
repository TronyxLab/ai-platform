# GREP_SUMMARY: vendor ai-instructions runtime компылятор вендоринг приватный-репо pyproject-ci-fix
# STRUCTURE: ┌источник v0.7.0┐ → ◇ решение вендоринга → ◇ состав → ◇ обновление → ⎋ контракт

# vendor/ai_instructions — вендоренный runtime компилятора инструкций

- **Источник:** Tronyx161/AI-instructions @ v0.7.0 (приватный репо; git-пин в pyproject
  ломал CI-клонирование — 2026-08-16, «fatal: could not read Username»).
- **Решение оператора (2026-08-16):** вендорить пакет вместо git-зависимости
  (отход от R12 «pip из git-repo» — зафиксирован TRAP[DECISION] в pyproject).
- **Состав:** `ai_instructions/` (runtime, stdlib + pyyaml) — 0.7.0.
- **Обновление:** скопировать `ai_instructions/` из тегированного релиза ai-instructions
  + обновить tag/digest в `core/internal/ai-instructions/ai-instructions-pins.yaml`.
- **Контракт:** код вендоренный, платформенными гейтами НЕ линтуется (ruff/pyright
  exclude vendor/); изменения — только через апдейт из апстрима.
