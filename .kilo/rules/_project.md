# Project Context

## CI Pre-flight Rules

Перед push в CI:
1. `make fix-gate && git add -u` (executable bits, ruff format, manifest drift); если ruff всё ещё fail — `ruff format . && ruff check --fix .`
2. `make gate MODE=fast` — должен быть зелёным
3. Диагностические ветки — от origin/main: `git checkout -b <branch> origin/main`, не от локального main
4. После merge/конфликтов: `make fix-gate && git add -u && make gate MODE=fast`

## Верификация реализации (Code-агент)

**Батчинг вместо серийных циклов:** `make gate MODE=fast` падает на первой ошибке — не гоняй его ради каждой найденной проблемы.

1. Per-task: после каждой задачи — только затронутые тесты: `make test-summary TEST_FILE=tests/unit/test_x.py` (или `pytest tests/unit/test_x.py -q`)
2. Фикс-цикл — только `make preflight` (все gate-проверки параллельно, WORKERS=6): фикси все найденные ошибки батчем → повторяй до чистоты
3. Быстрые статические проверки (`ruff check .`, `ruff format --check .`, `make doxygen-check`, LOC-гейты против лимитов `tests/gates/test_gate_loc_allowlist.py`) — напрямую, не через полный gate
4. Полный gate — один раз в конце, после чистого preflight: `make gate MODE=fast && make check-manifests && ruff check .`; упал → снова preflight-цикл (п.2)
5. Запрещён `git checkout`/`git restore` для отката одиночных файлов — откатывает все незакоммиченные изменения (инцидент Wave 6, потеря E11); откатывай точечным `edit`
6. В промтах Code-субагентам gate-последовательность писать шагами: «`make preflight` (до чистоты) → `make gate MODE=fast` → `make check-manifests` → `ruff check .`», не одной цепочкой через `&&`

## Commit Policy (U-83, DevPlan 116 B11 T8)

**Лимит: ≤2 коммита на DevPlan:**
- `docs(116): <N> DevPlan — <slug> (<U-...>)` — только документация (DevPlan-файл)
- `feat(116): <N> implementation — ...` — реализация (код + тесты + манифесты)

Раздельные коммиты по волнам — норма (волна = свой feat-коммит). Big-bang (один коммит на N волн) — запрещён: теряется per-wave аудит-трейл.

