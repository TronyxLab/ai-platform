#!/usr/bin/env bash
# GREP_SUMMARY: yaml library yaml-read yaml-get-field yaml-get-list dotted-key yaml-parsing
# STRUCTURE: ┌yaml_path + dotted_key┐ → ◇ yaml_get_field (single value) / yaml_get_list (multi-line) → ⎋ stdout value(s) | return 1/2
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — YAML Read Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Centralized YAML reading via python3+yaml — replaces inline
##           `python3 -c "import yaml; ..."` patterns across the codebase.
##           Provides two functions: yaml_get_field (single value or JSON
##           for complex types) and yaml_get_list (one item per line).
## @scope    — yaml_get_field(yaml_path, dotted_key): traverse YAML by dotted
##             key path, print leaf value (JSON for dicts/lists)
##           — yaml_get_list(yaml_path, dotted_key): same traversal, but
##             expects a list and prints each item on its own line (JSON
##             for complex items)
## @input    — $1: yaml_path — path to YAML file
##           — $2: dotted_key — e.g. "hooks.on_project_deploy" or
##             "networks" or "monitoring.host_port"
## @output   — stdout: field value (yaml_get_field) or one item per line
##             (yaml_get_list). Dict/list values serialized as JSON.
##           — stderr: error messages to /dev/null by default callers,
##             can be redirected for debugging
## @links    — USED_BY: core/internal/deploy/deploy-project.sh
##           — USED_BY: core/internal/provision-environment.sh
##           — USED_BY: any script needing YAML field extraction
##           — CONSOLIDATES: 13+ inline `python3 -c "import yaml"` calls
## @invariants
##   - Requires python3 with PyYAML installed (python3 -c "import yaml")
##   - yaml_get_field: field not found → empty stdout + return 1
##   - yaml_get_field: file not found → empty stdout + return 1
##   - yaml_get_field: parse error (TypeError) → stderr + return 2
##   - yaml_get_list: key not a list → stderr + return 2
##   - yaml_get_list: any item within list is printed
##   - Script is a library — no main() entry point, functions only
## @rationale Q: Why a separate lib instead of keeping inline python3?
##   A: 13 inline `python3 -c "import yaml"` calls across 5+ files create
##   maintenance burden, inconsistent error handling, and impossible grep
##   discoverability. One library with contract + GREP_SUMMARY eliminates
##   drift. Wave 3 (D10 from Brief) — low priority, protected by contract
##   tests from Waves 1-2.
## @changes
##   2026-07-17 · tronyx · T22 — initial creation
# endregion MODULE_CONTRACT

set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
# region yaml_get_field
## @purpose  Read a scalar or complex value from YAML by dotted key path.
##           Dict/list values are serialized as JSON for bash consumption.
## @io       Input: $1=yaml_path, $2=dotted_key (e.g. "monitoring.host_port")
##           Output: stdout — value (string/number) or JSON (dict/list)
##           Return: 0=found, 1=not found, 2=parse error
## @example
##   yaml_get_field "/opt/platform-env.yaml" "proxy.no_proxy_internal"
##   # → "localhost,127.0.0.1,::1"
## @rationale  Centralizes python3+yaml error handling — all callers get
##             consistent field-not-found → exit 1 semantics. JSON output
##             for complex types avoids bash YAML parsing workarounds.
## @complexity O(d) where d = depth of dotted key
##             Each key traversal is O(1) dict lookup in Python.
yaml_get_field() {
    local yaml_path="$1"
    local dotted_key="$2"

    python3 -c "
import yaml, sys, json
try:
    with open('${yaml_path}') as _f:
        _data = yaml.safe_load(_f)
    _keys = '${dotted_key}'.split('.')
    _val = _data
    for _k in _keys:
        _val = _val[_k]
    if isinstance(_val, (dict, list)):
        print(json.dumps(_val))
    else:
        print(_val)
except KeyError:
    sys.exit(1)  # field not found → empty stdout, return 1
except (TypeError, AttributeError) as _e:
    sys.stderr.write('yaml_get_field: parse error: {}\n'.format(_e))
    sys.exit(2)  # parse error → stderr, return 2
except FileNotFoundError as _e:
    sys.stderr.write('yaml_get_field: file not found: {}\n'.format(_e))
    sys.exit(1)  # file not found → empty stdout, return 1
" 2>/dev/null || return $?
}
# endregion yaml_get_field

# ═══════════════════════════════════════════════════════════════════════════════
# region yaml_get_list
## @purpose  Read a YAML list by dotted key path and output each item on
##           its own line. Complex items (dicts) are serialized as JSON.
## @io       Input: $1=yaml_path, $2=dotted_key (must resolve to a list)
##           Output: stdout — one line per list item (JSON for dict items)
##           Return: 0=success, 1=not found, 2=key not a list or parse error
## @example
##   yaml_get_list "/opt/platform-env.yaml" "networks"
##   # → {"name": "proxy", "driver": "bridge"}  (one JSON object per line)
##   yaml_get_list "/opt/platform-env.yaml" "profiles"
##   # → fullstack
##   # → backend
## @rationale  List iteration in bash is fragile. yaml_get_list provides
##             a stable line-per-item interface. Consumer iterates with
##             `while IFS= read -r line; do ... done`.
## @complexity O(n) where n = number of items in the list
yaml_get_list() {
    local yaml_path="$1"
    local dotted_key="$2"

    python3 -c "
import yaml, sys, json
try:
    with open('${yaml_path}') as _f:
        _data = yaml.safe_load(_f)
    _keys = '${dotted_key}'.split('.')
    _val = _data
    for _k in _keys:
        _val = _val[_k]
    if not isinstance(_val, list):
        sys.stderr.write('yaml_get_list: key is not a list, got {}'.format(type(_val).__name__))
        sys.exit(2)
    for _item in _val:
        if isinstance(_item, (dict, list)):
            print(json.dumps(_item))
        else:
            print(_item)
except KeyError:
    sys.exit(1)  # field not found → empty stdout, return 1
except TypeError as _e:
    sys.stderr.write('yaml_get_list: type error: {}\n'.format(_e))
    sys.exit(2)  # type error → stderr, return 2
except FileNotFoundError as _e:
    sys.stderr.write('yaml_get_list: file not found: {}\n'.format(_e))
    sys.exit(1)  # file not found → empty stdout, return 1
" 2>/dev/null || return $?
}
# endregion yaml_get_list
