# Project Context

## Project
- **Name:** AI-platform

## Environment
- **Shell:** /bin/zsh
- **Python:** >= 3.10

## CI Pre-flight Rules

Перед любым push в CI:
1. **Локальный gate**: `make gate MODE=fast` — ДОЛЖЕН быть зелёным перед push
2. **Форматирование**: после `make gate MODE=fast` green — `ruff format . && ruff check --fix .`
3. **Ветки от origin/main**: диагностические ветки создавать через `git checkout -b <branch> origin/main`, не от локального main
4. **SKIP_PRECOMMIT**: при наличии `SKIP_PRECOMMIT=1` в окружении pre-commit не запускается повторно — единственный запуск на CI-шаге

