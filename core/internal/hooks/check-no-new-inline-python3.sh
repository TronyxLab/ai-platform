#!/usr/bin/env bash
# GREP_SUMMARY: pre-commit, inline-python3, block-new, language-policy, enforcement
# STRUCTURE: ┌git diff --cached┐ → ◇ whitelist check → ◇ scan added lines for python3 -c/heredoc → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Pre-commit hook: блокирует добавление НОВЫХ inline-Python / heredoc в shell-файлах под core/
##           и в CI workflow-файлах под .github/ (StatusReport 046 T6 — CICD-01e).
##           Не трогает существующие (консолидация через Strangler-триггер).
## @scope    Только staged changes (+line prefix в git diff) в .sh файлах под core/ и .yml/.yaml под .github/.
## @invariants
##   - Проверяет только staged additions (git diff --cached, строки с '+')
##   - Whitelist: core/internal/scripts/*.py, core/internal/hooks/*.sh
##   - yaml_read.sh removed from whitelist after 038c (zero inline python3)
##   - Whitelist для легитимных CI-однострочников: `python3 -c` без `import` разрешён (print/format only)
##   - Exit 0 = clean | Exit 1 = violation detected
## @rationale Enforcement языковой политики (AGENTS.md «Языковая политика» Tier 1).
##            CI gate отклонён оператором (TRAP[DECISION] в AGENTS.md).
## @changes
##   LAST_CHANGE: 2026-07-26 | DevPlan 038c — yaml_read.sh removed from whitelist
##   2026-07-21 | Created (DevPlan 028 W1-E7)
##   2026-07-22 | Extended scope to .github/**/*.yml (StatusReport 046 T6 — CICD-01e)
# endregion MODULE_CONTRACT
set -euo pipefail

WHITELIST_REGEX="^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"
# ✅ TRAP[DEBT] 2026-07-26 D7 — все 3 whitelist-записи ЗАКРЫТЫ (DevPlan 128 W4):
# · generate-catalog.sh heredoc → core/internal/catalog/generate_catalog.py (фасад 17 LOC, 119 B1)
# · adopt-project.sh JSON-анализ → core/internal/scaffold/project_adopter.py (detect_project_config, 118 E11)
# · add-vhost.sh duplicate-domain check → core/internal/scaffold/vhost_renderer.py (Strangler 5b/5c)
# · Whitelist по этим записям пуст; hook-логика не менялась.

# Получаем staged files: shell under core/ + CI yaml under .github/ (StatusReport 046 T6)
# DRIFT-046-2 prevention: hook script glob must match .pre-commit-config.yaml files filter
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' '.github/workflows/*.yml' '.github/actions/*/action.yml' || true)

violations=0
for file in $staged_files; do
    # Whitelist check
    if echo "$file" | grep -qE "$WHITELIST_REGEX"; then
        continue
    fi

    # Проверяем только added lines
    while IFS= read -r line; do
        # Strip leading + from diff
        content="${line#+}"
        # Detect inline python3 patterns
        if echo "$content" | grep -qE 'python3 -c|python3 - <<|python3 <<EOF|python3 <<PYEOF'; then
            # TRAP[DESIGN] 2026-07-22 MED: Whitelist для легитимного inline в CI
            # Разрешить `python3 -c` БЕЗ `import` (чистый print/format однострочник).
            # Блокировать `python3 -c` с `import` — основной signal бизнес-логики inline.
            # Rev: если легитимных inline без import станет >5 → пересмотреть whitelist.
            if echo "$content" | grep -qE 'python3 -c' && ! echo "$content" | grep -qE 'import '; then
                continue
            fi
            echo "[IMP:10][no-new-inline-python3] VIOLATION in $file:"
            echo "  $content"
            echo ""
            echo "Language policy violation: new inline python3 blocked."
            echo "  -> Extract logic to core/internal/scripts/<module>.py"
            echo "  -> Or use existing core/internal/scripts/yaml_query.py for YAML/JSON access"
            echo "  -> See AGENTS.md §Языковая политика (Tier 1 trigger)"
            violations=$((violations + 1))
        fi
    done < <(git diff --cached -- "$file" | grep '^+')
done

if [[ $violations -gt 0 ]]; then
    exit 1
fi

exit 0
