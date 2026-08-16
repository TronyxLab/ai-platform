#!/usr/bin/env bash
# GREP_SUMMARY: pre-commit-hook, basedpyright, thin-facade, level-error, venv-resolution, error-grep, CI-determinism
# STRUCTURE: ┌resolve basedpyright (.venv/bin → PATH)┐ → ○ run basedpyright --level=error "$@" → ◇ grep ' - error: ' → ⊕ errors→exit 1 │ warnings-only→exit 0 (контракт --level error) → ⎋
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
## @io       ⇥ файлы (pre-commit батчами) или пусто (project-scan по pyrightconfig) → rc 0/1
## @invariants
##   - Резолв: .venv/bin/basedpyright (dev-venv) → command -v basedpyright (PATH/CI) → FAIL с инструкцией
##   - Выход: ' - error: ' в выводе → exit 1 (до 60 строк ошибок в stderr); иначе → exit 0
##   - Fatal (rc≠0 + пустой вывод) → exit 1 с IMP:10
##   - 0 inline python3; shell — тонкий фасад (языковая политика)
## @rationale language: python для basedpyright требовал nodeenv-env и терял args на
##            раннере; rc-семантика basedpyright недетерминирована на CI-раннере —
##            единственный надёжный сигнал «есть ошибки» — сами строки ' - error: '.
## @changes 2026-08-15 | Created (v1.0.1 CI-green: determinism fix, rounds 1-5)
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
