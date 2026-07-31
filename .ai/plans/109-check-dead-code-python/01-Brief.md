# $ARTIFACT_CONTRACT
## @PURPOSE Миграция check-dead-code.sh (86 LOC) → Python-модуль + тонкий shell-фасад (~20 LOC)
## @DESCRIPTION
`core/entrypoints/check-dead-code.sh` (86 LOC) — детектор мёртвого кода через git blame.
Вызывается из `make check-dead-code`.

Функции:
- `check_file_age()` — git log --follow для определения последнего изменения
- `check_references()` — grep по кодовой базе для поиска использования
- `check_git_tracked()` — проверка что файл tracked в git
- Форматирование вывода с цветами

Вся логика — парсинг git blame/ log porcelain + grep. Идеальный кандидат для Python
(plumbing-парсинг через subprocess).

**План:** вынести всю логику в `core/internal/lint/dead_code_checker.py`.
Shell оставляет: arg parsing, вызов Python, exit code.
## @RATIONALE
- Git blame porcelain парсинг в awk/grep — хрупкий и трудночитаемый
- Python с subprocess + regex — более надёжный парсинг
- 86→20 LOC (−77%)
## @ACCEPTANCE_CRITERIA
- AC1: `core/internal/lint/dead_code_checker.py` с check_file_age(), check_references(), check_git_tracked()
- AC2: Shell-фасад ≤ 25 LOC
- AC3: `make check-dead-code` проходит идентично
- AC4: Те же false-positive исключения сохранены
- AC5: Цветной вывод сохранён (или через Python colorama/ ANSI)
- AC6: `make gate MODE=fast` зелёный
## @IMPLEMENTS Brief 109
## @IMPACTS core/entrypoints/check-dead-code.sh, core/internal/lint/dead_code_checker.py (NEW), tests/unit/test_dead_code_checker.py (NEW)
## @REQUIRES Ничего
