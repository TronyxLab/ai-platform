#!/usr/bin/env bash
# GREP_SUMMARY: pre-commit-hook, basedpyright, thin-facade, level-error, venv-resolution, error-grep, CI-determinism, changed-files, F-02
# STRUCTURE: ┌resolve basedpyright (.venv/bin → PATH)┐ → ◇ --changed? → git diff+untracked *.py → files | ┌run basedpyright --level=error "$@"┐ → ◇ grep ' - error: ' → ⊕ errors→exit 1 │ warnings-only→exit 0 (контракт --level error) → ⎋
# region MODULE_CONTRACT
## @purpose  Pre-commit hook + gate-шаг pyright: basedpyright с контрактом «0 errors» (DevPlan 163 M2).
##           Thin facade, ДЕТЕРМИНИЗМ ПО ГРЕПУ, не по exit-коду: v1.0.1 CI-fix — на GitHub
##           раннере basedpyright 1.39.9 исполнялся с дефолтным уровнем (warning): summary
##           «0 errors, 690+1015 warnings» + exit 1 при локально/контейнерно зелёном прогоне
##           (механизм потери --level на раннере не воспроизведён на macOS/python:slim/
##           ubuntu:24.04). Контракт платформы — БЛОК ТОЛЬКО НА ОШИБКАХ (reportAny и пр.
##           pyrightconfig-правила уровня error); warnings — advisory (как при --level error).
##           Grep ' - error: ' даёт тот же вердикт независимо от того, применился ли --level.
## @scope    pre-commit хук basedpyright (.pre-commit-config.yaml, language: system) +
##           gate-шаг pyright (core/check-suite.yaml cmd).
## @io       ⇥ файлы (pre-commit батчами), --changed (changed-files скоуп, F-02) или пусто
##           (project-scan по pyrightconfig) → rc 0/1
## @invariants
##   - Резолв: .venv/bin/basedpyright (dev-venv) → command -v basedpyright (PATH/CI) → FAIL с инструкцией
##   - Выход: ' - error: ' в выводе → exit 1 (до 60 строк ошибок в stderr); иначе → exit 0
##   - Fatal (rc≠0 + пустой вывод) → exit 1 с IMP:10
##   - --changed (DevPlan 015 F-02): скоуп = изменённые *.py (git diff HEAD + untracked);
##     ПУСТОЙ changed-набор → full-repo scan (CI-checkout без diff не должен дать false-green)
##   - 0 inline python3; shell — тонкий фасад (языковая политика; git-пайплайн — оркестрация)
## @rationale language: python для basedpyright требовал nodeenv-env и терял args на
##            раннере; rc-семантика basedpyright недетерминирована на CI-раннере —
##            единственный надёжный сигнал «есть ошибки» — сами строки ' - error: '.
##            F-02 (015): full-repo pyright на dev >120s (таймаут check-suite) — changed-files
##            скоуп (как agent-check/check-diff); full-repo — отдельный опциональный шаг
##            pyright-full (check-suite.yaml), только full-гейт.
## @changes 2026-08-15 | Created (v1.0.1 CI-green: determinism fix, rounds 1-5)
## @changes 2026-08-27 | DevPlan 015 F-02 — +--changed (changed-files скоуп; пусто → full-repo fallback)
# endregion MODULE_CONTRACT

set -euo pipefail

if [ -x ".venv/bin/basedpyright" ]; then
    PYRIGHT=".venv/bin/basedpyright"
elif command -v basedpyright >/dev/null 2>&1; then
    PYRIGHT="$(command -v basedpyright)"
else
    echo "[IMP:10][pyright-hook] basedpyright not found — install: make venv (dev-extra pyproject) или pip install basedpyright==1.39.9" >&2
    exit 1
fi

# F-02 (DevPlan 015): --changed — changed-files скоуп (git diff HEAD + untracked *.py).
# Пустой changed-набор (CI-checkout без diff) → full-repo scan: false-green недопустим.
if [ "${1:-}" = "--changed" ]; then
    shift
    _CHANGED_PY="$( { git diff --name-only HEAD -- '*.py' 2>/dev/null; git ls-files --others --exclude-standard -- '*.py' 2>/dev/null; } | sort -u )"
    if [ -n "${_CHANGED_PY}" ]; then
        # shellcheck disable=SC2086
        # намеренное word-splitting в список файлов (_CHANGED_PY)
        set -- $_CHANGED_PY
        echo "[IMP:8][pyright-hook] changed-files scope: $(printf '%s' "${_CHANGED_PY}" | wc -l | tr -d ' ') .py file(s)" >&2
    else
        echo "[IMP:8][pyright-hook] --changed: пустой changed-набор (clean checkout) — full-repo scan (false-green guard)" >&2
    fi
fi

_OUT="$(mktemp "${TMPDIR:-/tmp}/pyright-hook.XXXXXX")"
trap 'rm -f "${_OUT}"' EXIT

# --level=error: на машинах, где флаг доходит, вывод компактен; где теряется —
# warnings печатаются, но вердикт всё равно берётся из grep ' - error: '.
"$PYRIGHT" --level=error "$@" > "${_OUT}" 2>&1 || true

if grep -qE ' - error: ' "${_OUT}"; then
    grep -E ' - error: ' "${_OUT}" | head -60 >&2
    tail -1 "${_OUT}" >&2
    exit 1
fi

if [ ! -s "${_OUT}" ]; then
    echo "[IMP:10][pyright-hook] basedpyright fatal: пустой вывод (запуск не удался?)" >&2
    exit 1
fi

tail -1 "${_OUT}" >&2
exit 0
