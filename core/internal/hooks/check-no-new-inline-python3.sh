#!/usr/bin/env bash
# GREP_SUMMARY: pre-commit, inline-python3, block-new, language-policy, enforcement
# STRUCTURE: ┌git diff --cached┐ → ◇ whitelist check → ◇ scan added lines for python3 -c/heredoc → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Pre-commit hook: блокирует добавление НОВЫХ inline-Python / heredoc в shell-файлах под core/.
##           Не трогает существующие (консолидация через Strangler-триггер).
## @scope    Только staged changes (+line prefix в git diff) в .sh файлах под core/.
## @invariants
##   - Проверяет только staged additions (git diff --cached, строки с '+')
##   - Whitelist: core/lib/yaml_read.sh, core/internal/scripts/*.py, core/internal/hooks/*.sh
##   - Exit 0 = clean | Exit 1 = violation detected
## @rationale Enforcement языковой политики (AGENTS.md «Языковая политика» Tier 1).
##            CI gate отклонён оператором (TRAP[DECISION] в AGENTS.md).
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT
set -euo pipefail

WHITELIST_REGEX="^core/lib/yaml_read\.sh$|^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"

# Получаем staged .sh files under core/
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' || true)

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
