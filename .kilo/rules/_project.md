# Project Context

## Project
- **Name:** AI-platform

## Environment
- **Shell:** /bin/zsh
- **Python:** >= 3.10

## CI Pre-flight Rules

Перед любым push в CI:
1. **Auto-fix:** `make fix-gate && git add -u` — исправляет executable bits, ruff format, manifest drift
2. **Локальный gate:** `make gate MODE=fast` — ДОЛЖЕН быть зелёным перед push
3. **Форматирование:** покрывается `make fix-gate` (ruff на changed files). Если всё ещё fail — `ruff format . && ruff check --fix .`
4. **Ветки от origin/main:** диагностические ветки создавать через `git checkout -b <branch> origin/main`, не от локального main
5. **После merge:** `make fix-gate && git add -u && make gate MODE=fast` — особенно после конфликтов

## Commit Policy (U-83, DevPlan 116 B11 T8)

**Лимит: ≤2 коммита на DevPlan.** Один DevPlan = максимум 2 коммита:
- `docs(116): <N> DevPlan — <slug> (<U-...>)` — только документация (DevPlan-файл)
- `feat(116): <N> implementation — ...` — реализация (код + тесты + манифесты)

Раздельные коммиты по волнам — норма (одна волна = свой feat-коммит). Big-bang (один коммит на N волн) — ЗАПРЕЩЁН (U-83: история 116 была wave-коммитами; консолидация в один коммит теряет per-wave аудит-трейл).

