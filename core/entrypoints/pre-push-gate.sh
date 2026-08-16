#!/usr/bin/env bash
# GREP_SUMMARY: pre-push-gate, quick-check, all-branches, CI-gate, make-gate, pre-commit, ruff, check-diff, pre-push, blocking, OOM, D4
# STRUCTURE: ┌pre-push stdin refs┐ → ⚡ pre_push_branch_detect.py (лог целевой ветки) → ⊕ quick check (pre-commit run --all-files + ruff check . + make check-diff) для ВСЕХ веток → ⎋ exit 0 allow | exit 1 block
# region MODULE_CONTRACT
## @purpose — Pre-push hook (DevPlan 160 W6 T6.2 D4 → v1.0.1 quick-check policy):
##            ЛЮБАЯ ветка (main/release/feature) → БЫСТРЫЙ чек: `pre-commit run --all-files`
##            + `.venv/bin/ruff check .` + `make check-diff` (~1-2 мин, O(1) память).
##            ПОЛНЫЙ fast-gate — ТОЛЬКО CI (push-gate.yml, все ветки, blocking).
##            Branch-детекция (stdin → env → git HEAD) — в core/internal/lint/pre_push_branch_detect.py
##            (DevPlan 170 W9-F2; 2 TRAP[BUG] закрыты: while read no-\n, paths.sh PLATFORM_ROOT).
## @io — (stdin from git pre-push: `<local ref> <local sha> <remote ref> <remote sha>` per line)
##       → exit 0 (allow push) / exit 1 (block push)
## @complexity — O(1) — один путь для всех веток
## @rationale — Полный `make gate MODE=fast` в hook'е вызывал OOM на dev-машине (16 GB,
##              2 зависания с потерей сессии — 2026-08-15, push v1.0.1 baseline; ~12
##              параллельных pytest-xdist прогонов × 12 CPU). Решение оператора: локальный
##              hook = быстрый детерминированный сигнал <2 мин; полный арбитр — CI
##              push-gate.yml (все ветки) + platform-gate-fast.yml (push в main).
## @invariants
##   - Целевая ветка: pre_push_branch_detect.py (stdin remote ref → PRE_COMMIT_REMOTE_BRANCH
##     → git rev-parse --abbrev-ref HEAD → "unknown") — ТОЛЬКО для IMP-лога
##   - Любой failure из pre-commit/ruff/check-diff блокирует push (exit 1)
##   - Полный fast-gate локально в hook'е НЕ запускается (OOM-политика v1.0.1)
##   - Runs `always_run: true` in pre-commit-config.yaml
##   - Exit-коды НЕ меняются: 0 allow / 1 block (контракт DevPlan 157 W1 сохранён)
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-2
##            2026-07-31 | DevPlan 104 — re-enabled: removed exit 0 + heredoc blocker, restored make gate MODE=fast
##            2026-08-11 | DevPlan 145 W3 D-142-B38 — removed non-blocking reinstall step
##            2026-08-12 | DevPlan 157 W1 T1 — hint-строки: длительность, поведение при FAIL, repair-подсказка
##            2026-08-13 | DevPlan 160 W6 T6.2 (D4) — ГИБРИД: main/release* → fast-gate; feature → quick check
##            2026-08-15 | DevPlan 170 W9-F2 — branch-detect извлечён в pre_push_branch_detect.py;
##                       закрыты TRAP[BUG] 2026-08-06 (paths.sh PLATFORM_ROOT export — чистый Python,
##                       0 shell-библиотек) и 2026-08-13 (while read no-\n — splitlines() корректен)
##            2026-08-15 | v1.0.1 (0.8) — TRAP[DECISION]: ГИБРИД ОТМЕНЁН, full-gate из hook'а УДАЛЁН.
##                       OOM-инциденты (2026-08-14/15): pre-push hook → make gate MODE=fast →
##                       ~12 pytest-xdist прогонов (-n auto = 12 CPU) + Docker Desktop на 16 GB →
##                       зависание macOS (2 ребута). Решение оператора: комп не должен умирать;
##                       локальный арбитр = ручной прогон 0.3 (check → gate → agent-check),
##                       CI push-gate.yml — финальный арбитр всех веток.
##                       Rev: если CI-гейт начнёт пропускать регрессии класса, ловившегося
##                       локальным gate (прецедент-метрика: >2 RED-пушей в CI подряд от одного
##                       агента) → вернуть gate в hook с WORKERS=2 + memory-guard.
##            2026-08-15 | v1.0.1 (0.8b) — TRAP[BUG]: ПЕРВОПРИЧИНА обоих зависаний — фан-аут
##                       pre-commit: БЕЗ pass_filenames: false хук вызывался ПО ПАЧКАМ файлов
##                       (orphan-baseline ~2000 staged-ADD → сотни параллельных инвокаций, каждая
##                       гоняла вложенный pre-commit/gate). Фикс: pass_filenames: false в
##                       .pre-commit-config.yaml + file-args guard (exit 0, проверка — в
##                       no-args-инвокации). Инвариант: конфиг обязан держать
##                       pass_filenames: false — убрать его = meltdown на крупном push.
# endregion MODULE_CONTRACT

set -euo pipefail
echo "[IMP:7][pre-push-gate][main] Starting pre-push gate (quick check)" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $(basename "$0")"
    echo ""
    echo "Pre-push hook (v1.0.1 quick-check policy):"
    echo "  ЛЮБАЯ ветка → быстрый чек: pre-commit run --all-files + ruff check . + make check-diff"
    echo "  ПОЛНЫЙ fast-gate → CI push-gate.yml (все ветки, blocking) + platform-gate-fast.yml (main)."
    echo ""
    echo "Целевая ветка определяется pre_push_branch_detect.py (stdin pre-push →"
    echo "PRE_COMMIT_REMOTE_BRANCH → git rev-parse --abbrev-ref HEAD → 'unknown') — для IMP-лога."
    echo ""
    echo "Wall-time: ~1-2 мин. Полный локальный арбитр (check → gate → agent-check) —"
    echo "ручной прогон при консолидации/релизе (OOM-политика 0.8, v1.0.1)."
    echo "Отклонение push БЕЗ remote-сообщения = exit hook'а: ищи FAIL/Gate-строки в stderr hook-лога (157 W1)."
    exit 0
fi

# ══ TRAP[BUG] 2026-08-15: защита от файл-аргументов ══
# Контракт: pre-commit вызывает хук ОДИН раз БЕЗ файловых аргументов (pass_filenames: false,
# always_run). Если кто-то уберёт pass_filenames: false — pre-commit начнёт фан-аутить хук по
# пачкам staged-файлов (на orphan-baseline: сотни параллельных инвокаций, каждая с вложенным
# pre-commit run --all-files → meltdown). Guard: инвокации С файловыми аргументами немедленно
# выходят 0 (проверку выполняет инвокация без аргументов) — push не виснет, защита остаётся.
if [[ $# -gt 0 ]]; then
    echo "[IMP:8][pre-push-gate][guard] Invoked with file args ($#) — skipped (pass_filenames: false регрессия?); проверку делает no-args-инвокация (always_run)." >&2
    exit 0
fi

# ── Целевая ветка (информационный лог, DevPlan 170 W9-F2) ──────────────────
# Формат git pre-push: `<local ref> <local sha> <remote ref> <remote sha>`. Логика (включая
# deleted-branch и финальную строку без \n) — в pre_push_branch_detect.py; "unknown" → не критично.
_TARGET_BRANCH="$(python3 "${_EP_DIR}/../internal/lint/pre_push_branch_detect.py" 2>/dev/null || echo "unknown")"
echo "[IMP:7][pre-push-gate][branch] Target branch: ${_TARGET_BRANCH}" >&2

# ══ ВСЕ ветки → БЫСТРЫЙ чек (pre-commit + ruff + check-diff) ══
# Полный fast-gate выполняет CI (push-gate.yml, все ветки, blocking) —
# OOM-политика v1.0.1: локальный hook не гоняет 12 параллельных xdist-прогонов.
echo "[IMP:9][pre-push-gate][quick] Executing quick check (pre-commit run --all-files + ruff check . + make check-diff)"
echo "[pre-push-gate][hint] Quick check: ~1-2 мин. ПОЛНЫЙ fast-gate выполнит CI (push-gate.yml, все ветки)." >&2

echo "[IMP:8][pre-push-gate][quick] Step 1/3: pre-commit run --all-files"
if ! pre-commit run --all-files; then
    echo "[pre-push-gate][hint] pre-commit FAIL. Типовые фиксы: make fix-gate && git add -u (whitespace/ruff); остальное — по выводу хука." >&2
    exit 1
fi

echo "[IMP:8][pre-push-gate][quick] Step 2/3: .venv/bin/ruff check ."
if ! .venv/bin/ruff check .; then
    echo "[pre-push-gate][hint] ruff check FAIL. Фикс: .venv/bin/ruff check --fix . && .venv/bin/ruff format ." >&2
    exit 1
fi

echo "[IMP:8][pre-push-gate][quick] Step 3/3: make check-diff"
if ! make check-diff; then
    echo "[pre-push-gate][hint] check-diff FAIL. Фикс: смотри FAIL-секции check-diff (pre-commit --files + ruff + pytest изменённых тестов)." >&2
    exit 1
fi

echo "[IMP:9][pre-push-gate][quick] Quick check PASS — push allowed. Полный fast-gate: CI push-gate.yml." >&2
exit 0
