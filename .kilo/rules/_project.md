# Project Context

## CI Pre-flight Rules

Перед push в CI:
1. В новом клоне: `make pre-commit-install` — hooks не версионируются, без них push не гейтится
2. `make fix-gate && git add -u` (executable bits, ruff format, manifest drift); если ruff всё ещё fail — `ruff format . && ruff check --fix .`
3. `make gate MODE=fast` гоняется автоматически pre-push hook'ом (blocking); вручную — только при `--no-verify` или отсутствии hooks
4. Диагностические ветки — от origin/main: `git checkout -b <branch> origin/main`, не от локального main
5. После merge/конфликтов: `make fix-gate && git add -u && make gate MODE=fast`

## Верификация реализации (Code-агент)

**Цикл (DevPlan 120):** per-task `make test-summary TEST_FILE=...` / `make check-diff` → фикс-цикл `make check` → финальная верификация `make gate MODE=fast`. Все проверки читаются из единого SoT `core/check-suite.yaml` — ручные check-manifests/ruff из инструкции удалены (дыры закрыты манифестом).

**Батчинг вместо серийных циклов:** `make gate MODE=fast` падает на первой ошибке — не гоняй его ради каждой найденной проблемы.

1. Per-task: после каждой задачи — только затронутые тесты: `make test-summary TEST_FILE=tests/unit/test_x.py` (или `pytest tests/unit/test_x.py -q`); мелкая правка без тестов — `make check-diff` (diff-скоуп: pre-commit + ruff + pytest изменённых файлов)
2. Фикс-цикл — `make check` (все проверки из core/check-suite.yaml, WORKERS=6; fingerprint-кэш: повторный прогон на неизменённом дереве — replay <10s, CHECK_CACHE=0 отключает): фикси все найденные ошибки батчем → повторяй до чистоты
3. Быстрые статические проверки (`ruff check .`, `ruff format --check .`, `make doxygen-check`, LOC-гейты против лимитов `tests/gates/test_gate_loc_allowlist.py`) — напрямую, не через полный gate
4. Полный gate в конце вручную НЕ обязателен — pre-push прогонит `make gate MODE=fast` автоматически (без кэша); вручную — только при `--no-verify` или отсутствии hooks
5. Запрещён `git checkout`/`git restore` для отката одиночных файлов — откатывает все незакоммиченные изменения (инцидент Wave 6, потеря E11); откатывай точечным `edit`
6. В промтах Code-субагентам gate-последовательность писать шагами: «`make check` (до чистоты) → `make gate MODE=fast`», не одной цепочкой через `&&`

## Commit Policy (U-83, DevPlan 116 B11 T8)

**Лимит: ≤2 коммита на DevPlan:**
- `docs(116): <N> DevPlan — <slug> (<U-...>)` — только документация (DevPlan-файл)
- `feat(116): <N> implementation — ...` — реализация (код + тесты + манифесты)

Раздельные коммиты по волнам — норма (волна = свой feat-коммит). Big-bang (один коммит на N волн) — запрещён: теряется per-wave аудит-трейл.
