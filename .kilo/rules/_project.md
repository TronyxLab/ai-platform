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
5. **SKIP_PRECOMMIT:** при наличии `SKIP_PRECOMMIT=1` в окружении pre-commit не запускается повторно — единственный запуск на CI-шаге
6. **После merge:** `make fix-gate && git add -u && make gate MODE=fast` — особенно после конфликтов

