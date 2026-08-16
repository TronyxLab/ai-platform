# Project Context

## CI Pre-flight Rules

Перед push в CI:
1. В новом клоне: `make pre-commit-install` — hooks не версионируются, без них push не гейтится
2. Перед ЛЮБЫМ `git commit` и перед push: `make fix-gate && git add -u` (executable bits, ruff format, manifest drift) — иначе pre-commit прервёт коммит (whitespace/ruff). Если ruff всё ещё fail — `ruff format . && ruff check --fix .`
3. `make gate MODE=fast` в dev-цикле вручную НЕ запускается (OOM-политика 0.8 v1.0.1: полный gate на dev-машине = ~12 параллельных xdist-прогонов × 12 CPU → зависание macOS). Арбитры: локально — `make check` (батч) + `make agent-check`; push — quick check в hook'е (pre-commit + ruff + check-diff, ~1-2 мин, ВСЕ ветки); полный fast-gate — ТОЛЬКО CI (push-gate.yml для всех веток + platform-gate-fast.yml на push в main). Исключение `--no-verify` или отсутствие hooks (новый клон без `make pre-commit-install`) — защита остаётся на CI
4. Push-чеклист (DevPlan 157; аудит 156 — push-фаза теряла 25–35 мин/сессию; v1.0.1 0.8 — OOM-фикс):
   - **любая ветка** → быстрый локальный чек в hook'е: `pre-commit run --all-files` + `.venv/bin/ruff check .` + `make check-diff` (~1–2 мин); ПОЛНЫЙ fast-gate гарантирует CI `push-gate.yml` (все ветки) → таймаут bash-тула ≥300s, один push за раз
   - ручной полный арбитр `make gate MODE=fast` — ТОЛЬКО шаг консолидации/релиза (после merged-дерева, перед force-push baseline); в dev-цикле — запрещён
   - отказ БЕЗ remote-сообщения = exit hook'а: вывод hook'а в stderr, git его не показывает → смотри FAIL/Gate-строки stderr hook-лога, НЕ rulesets/auth
   - при FAIL hook'а: `make fix-gate && git add -u` → повтор push; флак probe-гейтов — см. tests/gates/AGENTS.md §R5 probe-конвенция (119 H / 129 W2)
5. Диагностические ветки — от origin/main: `git checkout -b <branch> origin/main`, не от локального main
6. После merge/конфликтов: `make fix-gate && git add -u && make check`

## Верификация реализации (Code-агент)

**Цикл (DevPlan 120/165):** per-task `make check TEST_FILE=...` / `make check-diff` → фикс-цикл `make check` → финальная верификация `make check` до чистоты. `make gate MODE=fast` в dev-цикле НЕ запускается (OOM-политика 0.8 v1.0.1: hook гоняет только quick check — pre-commit + ruff + check-diff — для ВСЕХ веток; полный fast-gate — CI push-gate.yml). Все проверки читаются из единого SoT `core/check-suite.yaml` — ручные check-manifests/ruff из инструкции удалены (дыры закрыты манифестом).

**Единственная тестовая команда агента — `make check` (DevPlan 165):**
- `make check` — полная диагностика (батч всех ошибок, fingerprint-кэш)
- `make check MARKER=<suite>` — один сьют по id из check-suite.yaml (`contract|static_audit|predeploy|smoke|component|integration|gates|...`; включая diagnostic:false как integration); без кэша
- `make check TEST_FILE=<path>` — один тест-файл (compact-вывод test_runner); без кэша
- `make check-diff` — diff-скоуп (частный режим)
- `make gate MODE=fast|full|ci-docker` — арбитр CI (ручной локальный прогон — только консолидация/релиз, OOM-политика 0.8)
- `make test`/`make test-summary` ЗАПРЕЩЕНЫ (forbidden_verbs) — удалены DevPlan 165

**Журнал прогонов (DevPlan 165):** каждая тестовая команда (check/check-diff/gate/test-node/e2e-verify/load-test/agent-check) добавляет JSONL-запись в `.ai/logs/runs.jsonl` (ts, goal, branch, commit, exit_code, pass/fail/skip/error, duration, raw_log) и обновляет симлинк `.ai/logs/latest.log`. Raw-логи — `logs/make/<ts>-<goal>[<вариант>][-N].log` (вариант = MARKER/MODE/TEST_FILE/PROJECT/NODE/SCENARIO — из имени видно, что именно гонялось; суффикс -N — коллизии в одну секунду, файлы не перезаписываются). Агент, пришедший на ветку/план, ПЕРВЫМ делом смотрит журнал через симлинк в папке плана: `.ai/plans/<NNN>-<slug>/logs/` (runs.jsonl + latest.log) или `python3 -m core.internal.shared.test_journal latest` — что прогонял и прошёл ли предыдущий агент.

**Батчинг вместо серийных циклов:** `make gate MODE=fast` падает на первой ошибке (fail-fast) и рискован по памяти на dev-машине (OOM-политика 0.8) — поэтому в dev-цикле он не гоняется вовсе. Работай батчами: `make check` собирает ВСЕ ошибки за один проход.

1. Per-task: после каждой задачи — только затронутые тесты: `make check TEST_FILE=...` — ОДИН файл на вызов; два файла = два вызова (мультифайл-синтаксиса нет) (или `pytest tests/unit/test_x.py -q`); мелкая правка без тестов — `make check-diff` (diff-скоуп: pre-commit + ruff + pytest изменённых файлов)
2. Фикс-цикл — `make check` (все проверки из core/check-suite.yaml, WORKERS=6; fingerprint-кэш: повторный прогон на неизменённом дереве — replay <10s, CHECK_CACHE=0 отключает): фикси все найденные ошибки батчем → повторяй до чистоты
3. Быстрые статические проверки (`ruff check .`, `ruff format --check .`, `make check MARKER=doxygen-check`, LOC-гейты против лимитов `tests/gates/test_gate_loc_allowlist.py`) — напрямую, не через полный gate
4. Финальная верификация — `make check` до чистоты; pre-push hook прогонит гейт автоматически (без кэша; quick check для ВСЕХ веток, полный fast-gate — CI push-gate.yml, OOM-политика 0.8 v1.0.1). Ручной `make gate MODE=fast` НЕ нужен — вручную только при `--no-verify` или отсутствии hooks
5. Запрещён `git checkout`/`git restore` для отката одиночных файлов — откатывает все незакоммиченные изменения (инцидент Wave 6, потеря E11); откатывай точечным `edit`
6. В промтах Code-субагентам последовательность писать шагами: «`make check` (до чистоты)», не одной цепочкой через `&&`; `make gate MODE=fast` в промптах НЕ упоминать — арбитр pre-push hook (quick check) + CI push-gate.yml
7. Первая зацепка — текст ошибки (правило 5, DevPlan 157): при фейле любой проверки: 1) прочитай файл/идентификатор из сообщения об ошибке; 2) grep канона (имя probe `_gate_probe_*`, DevPlan-номер в `.ai/plans/*/`); 3) только потом воспроизводи/фикси. Канон 119H/129W2 существовал — агент 156 нашёл его после ~33 вызовов

## Commit Policy (U-83, DevPlan 116 B11 T8)

**Лимит: ≤2 коммита на DevPlan:**
- `docs(116): <N> DevPlan — <slug> (<U-...>)` — только документация (DevPlan-файл)
- `feat(116): <N> implementation — ...` — реализация (код + тесты + манифесты)

Раздельные коммиты по волнам — норма (волна = свой feat-коммит). Big-bang (один коммит на N волн) — запрещён: теряется per-wave аудит-трейл.
