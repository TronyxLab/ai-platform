#!/usr/bin/env bash
# shellcheck shell=bash
# GREP_SUMMARY: node-resolver thin-facade resolve_node_yaml extract_node_host node.yaml python3 -m node_resolver 3-path-search host
# STRUCTURE: ▶ ┌node_name┐ → ○ resolve_node_yaml (python3 -m shared.node_resolver resolve) → ⊕ echo path | ⎋ exit1
#            ▶ ┌yaml_path┐ → ○ extract_node_host (python3 -m shared.node_resolver host) → ⊕ echo host | ⎋ exit1
# ⚠️ Errexit guard: warn if sourced without `set -e` (fail-fast on errors).
case $- in *e*) ;; *) echo "[WARN] node-resolver.sh sourced without set -e" >&2 ;; esac
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад резолва node.yaml (DevPlan 127 W2, S8/P2-1): резолв и LDD-логи
##           перенесены в core/internal/shared/node_resolver.py (чистые функции + CLI). Здесь —
##           байт-совместимые функции resolve_node_yaml()/extract_node_host() (имена, stdout,
##           exit-коды 0/1, log_imp-сообщения) для 6+ потребителей (bootstrap.sh, node-update.sh,
##           node-lifecycle.sh, converge.sh, deploy-context.sh, makefiles/deploy.mk).
## @scope    resolve_node_yaml: node_name ($1), platform_root ($2), projects_dir ($3) — $2/$3
##           vestigial (поиск управляется env PLATFORM_ROOT/HOME в NodeYaml.resolve, как было).
##           extract_node_host: yaml_path ($1). Ноль side-effects при source.
## @invariants
##   - Функции пишут данные в stdout, LDD-логи в stderr (log_imp, logging.sh)
##   - resolve_node_yaml: exit 0 + одна строка-путь; not found → return 1 + IMP:10
##   - extract_node_host: exit 0 + host (пустая строка при отсутствии поля); missing file → return 1
##   - Бизнес-логика НЕ дублируется — вызов python3 -m core.internal.shared.node_resolver
## @rationale Q: Почему фасад, а не прямой вызов node_yaml --resolve?
##            A: Языковая политика: shell — тонкие фасады; единая точка резолва (LDD + контракт)
##            в shared/node_resolver.py; потребители не меняются (байт-совместимость).
## @changes  2026-08-04 | DevPlan 127 W2 — сокращён до фасада (было 215 LOC)
# endregion MODULE_CONTRACT

# region SETUP
## @purpose  Резолв core/lib/ + дефолт __LOG_PREFIX + source logging.sh (идемпотентно).
## @invariants — _NODE_RESOLVER_LIB_DIR всегда core/lib/; logging.sh — чистые определения
# ⚠️ TRAP[BUG] · 2026-07-07 · P1 · SCRIPT_DIR collision with readonly from caller scripts
# · Root: библиотеки source'ятся вызывающими скриптами с readonly SCRIPT_DIR (declare -r)
# · Fix: уникальное имя _NODE_RESOLVER_LIB_DIR (избегает readonly-коллизии)
_NODE_RESOLVER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${__LOG_PREFIX:=node-resolver}"
# shellcheck source=core/lib/logging.sh
source "${_NODE_RESOLVER_LIB_DIR}/logging.sh"
# endregion SETUP

# region FUNC_resolve_node_yaml
## @purpose  Резолв node.yaml (3-path search) через CLI shared/node_resolver.py.
## @param $1  node_name; $2 platform_root / $3 projects_dir — vestigial (byte-compat)
## @io       stdout: путь; stderr: log_imp; exit 0/1
resolve_node_yaml() {
    local node_name="${1:-}"

    if [[ -z "${node_name}" ]]; then
        log_imp 10 "-" "Missing required argument: node_name"
        return 1
    fi

    log_imp 8 "-" "Resolving node.yaml for node=${node_name} via NodeYaml Python CLI"

    local result
    result="$(python3 -m core.internal.shared.node_resolver resolve --node "$node_name" 2>/dev/null)" || {
        log_imp 10 "-" "node.yaml not found for node=${node_name}"
        log_imp 10 "-" "  Ensure node-configs/${node_name}/node.yaml exists"
        return 1
    }

    echo "$result"
    log_imp 9 "-" "Resolved node.yaml: ${result}"
}
# endregion FUNC_resolve_node_yaml

# region FUNC_extract_node_host
## @purpose  Извлечение node.host через CLI shared/node_resolver.py (NodeYaml.get).
## @param $1  yaml_path — абсолютный путь к существующему node.yaml
## @io       stdout: host (пустая строка при отсутствии поля); stderr: log_imp; exit 0/1
extract_node_host() {
    local yaml_path="${1:-}"

    if [[ -z "${yaml_path}" ]]; then
        log_imp 10 "-" "Missing required argument: yaml_path"
        return 1
    fi

    if [[ ! -f "${yaml_path}" ]]; then
        log_imp 10 "-" "File not found: ${yaml_path}"
        return 1
    fi

    log_imp 8 "-" "Extracting host from: ${yaml_path}"

    local host
    host="$(python3 -m core.internal.shared.node_resolver host --file "${yaml_path}" 2>/dev/null)" || {
        log_imp 10 "-" "Failed to parse YAML or extract host: ${yaml_path}"
        return 1
    }

    if [[ -n "${host}" ]]; then
        log_imp 9 "-" "Extracted host: ${host}"
    else
        log_imp 9 "-" "No host field in node.yaml: ${yaml_path} (empty output)"
    fi

    echo "${host}"
}
# endregion FUNC_extract_node_host
