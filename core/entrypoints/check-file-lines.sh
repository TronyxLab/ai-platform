#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint check-file-lines file-line-limit 500-line-warning scanning
# STRUCTURE: ▶ init → ○ parse --max-lines → ⊕ find files → ◇ wc -l each → ○ WARNING if >limit → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Scan repository files for line count exceeding configurable limit (default 500) — non-blocking warning
## @scope    Called from `make check-file-lines` and `make gate MODE=full`. Scans .py/.sh/.yml/.yaml/.json/.md
##           excluding .venv/, node_modules/, __pycache__/. Always exits 0 (informational only).
## @invariants
##   - Always exits 0 — non-blocking by design per DevPlan 030 AC5
##   - Default max-lines is 500; overridable via --max-lines argument
##   - Exclusion list: .venv/, node_modules/, __pycache__/
## @rationale Provides visibility into file size growth without blocking CI pipeline.
##            Existing files (e.g. conftest.py: 1671 lines) already exceed limit;
##            blocking would break gate. Warning mode enables incremental reduction.
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

MAX_LINES=500

# ── Parse arguments ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-lines)
            if [[ -z "${2:-}" || ! "$2" =~ ^[0-9]+$ ]]; then
                echo "[IMP:9][check-file-lines] ERROR: --max-lines requires a numeric argument" >&2
                exit 1
            fi
            MAX_LINES="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--max-lines N]"
            echo ""
            echo "Scan repository files for those exceeding N lines (default: 500)."
            echo "Scans: *.py *.sh *.yml *.yaml *.json *.md"
            echo "Excludes: .venv/ node_modules/ __pycache__/"
            echo "Always exits 0 (non-blocking warning)."
            exit 0
            ;;
        *)
            echo "[IMP:9][check-file-lines] ERROR: Unknown argument: $1" >&2
            echo "Usage: $0 [--max-lines N]" >&2
            exit 1
            ;;
    esac
done

echo "[IMP:7][check-file-lines] Scanning files exceeding ${MAX_LINES} lines..."

WARNING_COUNT=0

# 2026-08-04 (DevPlan 129 W2): TRAP[DEBT] 2026-08-03 снят — xdist-race устранён двойной защитой:
# 1) [ -f "$file" ] перед wc в ТОМ ЖЕ цикле (файл мог исчезнуть между find-итерациями);
# 2) || true в command substitution (pipefail + set -e иначе роняли скрипт ДО fallback-ветки —
#    bash сообщает redirection-ошибку на своём stderr, 2>/dev/null не перехватывает).
# Обе ветки перекрывают окно «probe-файл теста создан/удалён конкурентным xdist-воркером».
while IFS= read -r -d '' file; do
    # Файл может быть удалён конкуретным xdist-воркером между find и wc (probe-файлы тестов).
    # Атомарная проверка перед wc — не читаем несуществующий файл, не роняем non-blocking контракт.
    if [[ ! -f "$file" ]]; then
        echo "[IMP:7][check-file-lines] SKIP (deleted between find and wc): ${file}" >&2
        continue
    fi
    line_count=$(wc -l < "$file" 2>/dev/null | tr -d ' ' || true)
    if [[ -z "$line_count" || ! "$line_count" =~ ^[0-9]+$ ]]; then
        # Файл исчез между [ -f ] и wc (микро-окно race) — пропускаем, не роняем gate
        continue
    fi
    if [[ "$line_count" -gt "$MAX_LINES" ]]; then
        echo "[IMP:8][check-file-lines][WARNING] ${file}: ${line_count} lines (max ${MAX_LINES})"
        WARNING_COUNT=$((WARNING_COUNT + 1))
    fi
done < <(find "${PATHS_CORE_DIR}" \
    -type f \( -name '*.py' -o -name '*.sh' -o -name '*.yml' -o -name '*.yaml' -o -name '*.json' -o -name '*.md' \) \
    -not -path '*/.venv/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/__pycache__/*' \
    -print0 2>/dev/null)

if [[ "$WARNING_COUNT" -gt 0 ]]; then
    echo "[IMP:9][check-file-lines] ${WARNING_COUNT} file(s) exceed ${MAX_LINES}-line limit (non-blocking warning)"
else
    echo "[IMP:9][check-file-lines] All files within ${MAX_LINES}-line limit"
fi

echo "[IMP:9][check-file-lines] Check complete (exit 0 — non-blocking)"
exit 0
