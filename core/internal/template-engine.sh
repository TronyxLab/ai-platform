#!/usr/bin/env bash
# GREP_SUMMARY: template-engine bash-CLI wrapper template_engine.py render check render-all
# STRUCTURE: ┌arg parsing┐ → ◇ dispatch: render/check/render-all → ⊕ exit codes
# region MODULE_CONTRACT
## @purpose  Thin bash CLI wrapper for template_engine.py — called from scripts and Makefile
## @scope    Modes: render (single file), render-all (manifest), check (dry-run validation)
## @invariants
##   - python3 must be in PATH (exit 2 otherwise)
##   - template_engine.py is resolved relative to SCRIPT_DIR
##   - Default manifest: core/templates/template-manifest.yaml relative to platform root
##   - Exit codes: 0=OK, 1=render errors, 2=python3/environment errors
## @rationale Thin wrapper so bash scripts (install.sh, deploy-modules.sh, add-project.sh)
##            can call template-engine without inline python. Python core is the single
##            rendering implementation ($TESTING compliance).
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../.." 2>/dev/null && pwd || echo "/opt/platform")}"
ENGINE_PY="${SCRIPT_DIR}/template_engine.py"

# ── Pre-flight check ──────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "[IMP:10][template-engine] FATAL: python3 not found in PATH" >&2
    exit 2
fi

if [[ ! -f "$ENGINE_PY" ]]; then
    echo "[IMP:10][template-engine] FATAL: template_engine.py not found at ${ENGINE_PY}" >&2
    exit 2
fi

# ── Help ──────────────────────────────────────────────────────────────────

usage() {
    cat >&2 <<EOF
Usage: template-engine.sh <command> [options] [VAR=val ...]

Commands:
  render <template> [output] [VAR=val ...]
        Render a single template file. If output is omitted, print to stdout.
    render-all [--manifest PATH] [VAR=val ...]
        Render all templates from manifest (default: core/templates/template-manifest.yaml).
  render-dir <directory> [VAR=val ...]
        Render all text files in a directory in-place.
  check [--manifest PATH] [--verbose]
        Dry-run check all templates — exit 0 if all resolvable, 1 otherwise.

Options:
  --manifest PATH   Path to template-manifest.yaml (default: auto-detected)
  --verbose         Detailed diagnostics for check command

Environment:
  PLATFORM_ROOT     Override platform root (default: auto-detected)
EOF
    exit 2
}

# ── Default manifest resolution ───────────────────────────────────────────

resolve_manifest() {
    local manifest="${1:-}"
    if [[ -n "$manifest" ]]; then
        echo "$manifest"
        return 0
    fi
    # Try relative to platform root
    local default="${PLATFORM_ROOT}/core/templates/template-manifest.yaml"
    if [[ -f "$default" ]]; then
        echo "$default"
        return 0
    fi
    # Fallback: relative to SCRIPT_DIR
    echo "${SCRIPT_DIR}/../templates/template-manifest.yaml"
}

# ── Main dispatch ─────────────────────────────────────────────────────────

if [[ $# -lt 1 ]]; then
    usage
fi

COMMAND="$1"
shift

case "$COMMAND" in
    render)
        # template-engine.sh render <template> [output] [VAR=val ...]
        if [[ $# -lt 1 ]]; then
            echo "[IMP:9][template-engine] ERROR: render requires at least <template> argument" >&2
            usage
        fi
        TEMPLATE="$1"
        shift

        # Detect if second arg is output path (no "=") or first VAR
        OUTPUT=""
        ARGS=()
        if [[ $# -gt 0 ]] && [[ "$1" != *"="* ]]; then
            OUTPUT="$1"
            shift
        fi

        # Collect VAR=val pairs
        VAR_ARGS=()
        while [[ $# -gt 0 ]]; do
            if [[ "$1" == *"="* ]]; then
                VAR_ARGS+=("$1")
            else
                echo "[IMP:9][template-engine] ERROR: Unexpected argument '$1' (expected VAR=val)" >&2
                exit 2
            fi
            shift
        done

        echo "[IMP:7][template-engine][render] Rendering ${TEMPLATE}..." >&2
        if [[ -n "$OUTPUT" ]]; then
            python3 "$ENGINE_PY" render "$TEMPLATE" "$OUTPUT" "${VAR_ARGS[@]}"
        else
            python3 "$ENGINE_PY" render "$TEMPLATE" "${VAR_ARGS[@]}"
        fi
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 0 ]]; then
            echo "[IMP:9][template-engine][render] OK" >&2
        else
            echo "[IMP:9][template-engine][render] FAILED (exit=$EXIT_CODE)" >&2
        fi
        exit $EXIT_CODE
        ;;
    render-all)
        MANIFEST=""
        VAR_ARGS=()
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --manifest)
                    shift
                    MANIFEST="$1"
                    ;;
                --manifest=*)
                    MANIFEST="${1#*=}"
                    ;;
                *)
                    if [[ "$1" == *"="* ]]; then
                        VAR_ARGS+=("$1")
                    else
                        echo "[IMP:9][template-engine] ERROR: Unknown option '$1'" >&2
                        exit 2
                    fi
                    ;;
            esac
            shift
        done
        MANIFEST_PATH="$(resolve_manifest "$MANIFEST")"
        echo "[IMP:7][template-engine][render-all] Manifest: ${MANIFEST_PATH}" >&2
        python3 "$ENGINE_PY" render-all --manifest "$MANIFEST_PATH" "${VAR_ARGS[@]}"
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 0 ]]; then
            echo "[IMP:9][template-engine][render-all] All templates rendered OK" >&2
        else
            echo "[IMP:9][template-engine][render-all] ${EXIT_CODE} template(s) FAILED" >&2
        fi
        exit $EXIT_CODE
        ;;
    render-dir)
        # template-engine.sh render-dir <directory> [VAR=val ...]
        if [[ $# -lt 1 ]]; then
            echo "[IMP:9][template-engine] ERROR: render-dir requires <directory> argument" >&2
            usage
        fi
        DIR_PATH="$1"
        shift
        VAR_ARGS=()
        while [[ $# -gt 0 ]]; do
            if [[ "$1" == *"="* ]]; then
                VAR_ARGS+=("$1")
            else
                echo "[IMP:9][template-engine] ERROR: Unexpected argument '$1' (expected VAR=val)" >&2
                exit 2
            fi
            shift
        done
        echo "[IMP:7][template-engine][render-dir] Rendering directory: ${DIR_PATH}" >&2
        python3 "$ENGINE_PY" render-dir "$DIR_PATH" "${VAR_ARGS[@]}"
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 0 ]]; then
            echo "[IMP:9][template-engine][render-dir] OK" >&2
        else
            echo "[IMP:9][template-engine][render-dir] FAILED (exit=$EXIT_CODE)" >&2
        fi
        exit $EXIT_CODE
        ;;
    check)
        MANIFEST=""
        VERBOSE=false
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --manifest)
                    shift
                    MANIFEST="$1"
                    ;;
                --manifest=*)
                    MANIFEST="${1#*=}"
                    ;;
                --verbose)
                    VERBOSE=true
                    ;;
                *)
                    echo "[IMP:9][template-engine] ERROR: Unknown option '$1'" >&2
                    exit 2
                    ;;
            esac
            shift
        done
        MANIFEST_PATH="$(resolve_manifest "$MANIFEST")"
        echo "[IMP:7][template-engine][check] Checking manifest: ${MANIFEST_PATH}" >&2
        if $VERBOSE; then
            python3 "$ENGINE_PY" check --manifest "$MANIFEST_PATH" --verbose
        else
            python3 "$ENGINE_PY" check --manifest "$MANIFEST_PATH"
        fi
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 0 ]]; then
            echo "[IMP:9][template-engine][check] All templates resolvable" >&2
        else
            echo "[IMP:9][template-engine][check] UNRESOLVED placeholders detected" >&2
        fi
        exit $EXIT_CODE
        ;;
    --help|-h)
        usage
        ;;
    *)
        echo "[IMP:9][template-engine] ERROR: Unknown command '${COMMAND}'" >&2
        usage
        ;;
esac
