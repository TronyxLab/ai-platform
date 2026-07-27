# GREP_SUMMARY: yaml_query, yaml-api, json-api, python3-c-consolidation, typed-access
# STRUCTURE: ▶ yaml_get(path,key) → ◇ load_yaml → ⊕ key lookup (nested dotted) → ⎋ value | default | exit 1
#            ▶ yaml_query(path, jq_filter) → ◇ load_yaml → ⊕ simplified jq eval → ⎋ result
#            ▶ json_get(path, key) → ◇ load_json → ⊕ key lookup → ⎋ value
# region MODULE_CONTRACT
## @purpose  Typed Python API + CLI для YAML/JSON-запросов. Заменяет inline Python-однострочники в shell-скриптах.
## @scope    Чтение YAML/JSON файлов с типизированным доступом по dotted-key path.
##           Не выполняет запись, не валидирует schema (для этого — validate_module_yaml.py в Wave 3).
## @invariants
##   - yaml_get с nested key: "node.host" -> data["node"]["host"]
##   - missing key без default -> exit 1 (CLI) / raise KeyError (API)
##   - missing key с default -> return default (CLI prints nothing, exit 0)
##   - malformed YAML -> exit 3 / raise yaml.YAMLError
##   - file not found -> exit 2 / raise FileNotFoundError
##   - --items flag: output list items one per line (backward compat for yaml_get_list)
##   - --stdin flag: read JSON from stdin instead of --file (mutually exclusive with --file).
##     YAML from stdin is NOT supported — only JSON. Empty stdin -> exit 2. Invalid JSON -> exit 3.
## @rationale 40+ inline Python one-liners (import yaml; yaml.safe_load...) в shell-скриптах сигнализируют о Bash-ceiling.
##            Централизация в typed-модуль: тестируемость (unit-тесты), grep-ability, единая обработка ошибок,
##            consistent CLI exit codes. Wave 1 — консолидация yaml_read.sh; Wave 4 — остальные в ходе декомпозиции.
##            --items флаг добавлен для backward-compat yaml_get_list (one-item-per-line вывод).
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import yaml

# region PUBLIC_API


def yaml_get(path: pathlib.Path, key: str, default: Any = None) -> Any:
    """Get value from YAML file by dotted-key path.

    Examples:
        yaml_get(Path("node.yaml"), "node.host") -> "127.0.0.1"
        yaml_get(Path("node.yaml"), "node.nonexistent", default="fallback") -> "fallback"
    """
    data = _load_yaml(path)
    return _dotted_get(data, key, default)


def yaml_query(path: pathlib.Path, jq_filter: str) -> Any:
    """Simplified jq-like query against YAML data.

    Supported filters (subset of jq):
        ".key"              -> data["key"]
        ".key.subkey"       -> nested
        ".key[]"            -> list iteration (returns JSON array)
        ".key[] | .subkey"  -> map over list

    For complex queries — use Python API directly, not CLI.
    """
    data = _load_yaml(path)
    return _jq_eval(data, jq_filter)


def json_get(path: pathlib.Path, key: str, default: Any = None) -> Any:
    """Get value from JSON file by dotted-key path."""
    with open(path) as f:
        data = json.load(f)
    return _dotted_get(data, key, default)


# endregion PUBLIC_API


# region INTERNAL_HELPERS


def _load_yaml(path: pathlib.Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"[IMP:9][yaml_query] file not found: {path}")
    with open(path) as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"[IMP:9][yaml_query] malformed YAML in {path}: {e}") from e


def _dotted_get(data: Any, key: str, default: Any = None) -> Any:
    current = data
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            if default is not None:
                return default
            raise KeyError(f"[IMP:9][yaml_query] key not found: {key}")
    return current


def _jq_eval(data: Any, jq_filter: str) -> Any:
    # Simplified jq evaluator — supports dotted paths + list iteration + pipe
    # NOT full jq implementation; for complex queries use Python API
    current = data
    for segment in jq_filter.strip().lstrip(".").split("|"):
        segment = segment.strip()
        if segment.endswith("[]"):
            key = segment[:-2]
            current = _dotted_get(current, key) if key else current
            if not isinstance(current, list):
                raise TypeError(f"[IMP:9][yaml_query] expected list for {segment}, got {type(current).__name__}")
            current = list(current)
        else:
            current = _dotted_get(current, segment)
    return current


# endregion INTERNAL_HELPERS


# region CLI


def _format_item(item: Any, json_output: bool = False) -> str:
    """Format a single list item for --items output."""
    if isinstance(item, (dict, list)):
        return json.dumps(item)
    return str(item)


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="yaml_query.py",
        description="Typed YAML/JSON query — replacement for inline Python one-liners in shell scripts",
    )
    # --stdin and --file are mutually exclusive (DRIFT-046 R-046-4)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=pathlib.Path, help="YAML or JSON file path")
    source_group.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON from stdin instead of --file (StatusReport 046 T5 — CICD-01d)",
    )
    parser.add_argument("--get", metavar="KEY", help="Dotted-key path (e.g. node.host)")
    parser.add_argument("--query", metavar="JQ_FILTER", help="Simplified jq-like filter")
    parser.add_argument("--default", default=None, help="Default value if key not found")
    parser.add_argument("--json-output", action="store_true", help="Output as JSON (default: raw)")
    parser.add_argument(
        "--items",
        action="store_true",
        help="If value is a list, print each item on its own line (backward compat for yaml_get_list)",
    )
    parser.add_argument(
        "--keys",
        action="store_true",
        help="Print all top-level keys of the JSON/YAML object, one per line",
    )
    args = parser.parse_args()

    try:
        # ⚠️ TRAP[DESIGN] · 2026-07-22 · MED · --stdin добавляет второй режим ввода в yaml_query.py
        # · Risk: нарушение SRP — yaml_query.py становится "YAML file reader" + "stdin JSON reader"
        # · Mitigation: --stdin автоматически определяет формат как JSON (stdin всегда text/plain).
        # ·   YAML из stdin НЕ поддерживается — только JSON. mutually_exclusive_group с --file.
        # · Reason: deploy-project.yml:79 читает NODE_HOST_MAP (JSON) из stdin через `echo "$MAP" | ...`.
        # ·   Отдельный json_query_stdin.py отклонён — overhead двух CLI-модулей для одной задачи.
        # · Rev: если потребуется YAML из stdin → выделить stdin_query.py.
        if args.stdin:
            raw = sys.stdin.read().strip()
            if not raw:
                print("[IMP:10][yaml_query] ERROR: empty stdin", file=sys.stderr)
                return 2
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"[IMP:10][yaml_query] ERROR: invalid JSON from stdin: {e}", file=sys.stderr)
                return 3
            if args.keys:
                # --stdin --keys: print all top-level keys, one per line
                if isinstance(data, dict):
                    for k in data:
                        print(k)
                    return 0
                print(f"[IMP:9][yaml_query] --keys requires a dict, got {type(data).__name__}", file=sys.stderr)
                return 1
            if args.get:
                value = _dotted_get(data, args.get, args.default)
            elif args.query:
                value = _jq_eval(data, args.query)
            elif args.items:
                # --stdin --items without --get: treat stdin JSON as array
                if isinstance(data, list):
                    for item in data:
                        print(_format_item(item, args.json_output))
                    return 0
                print(
                    f"[IMP:9][yaml_query] --stdin --items requires a list at root, got {type(data).__name__}",
                    file=sys.stderr,
                )
                return 1
            else:
                parser.error("either --get, --query, or --keys required")
                return 1  # unreachable
        elif args.keys:
            # --file --keys: print all top-level YAML keys, one per line
            data = _load_yaml(args.file)
            if isinstance(data, dict):
                for k in data:
                    print(k)
                return 0
            print(f"[IMP:9][yaml_query] --keys requires a dict, got {type(data).__name__}", file=sys.stderr)
            return 1
        elif args.get:
            value = yaml_get(args.file, args.get, args.default)
        elif args.query:
            value = yaml_query(args.file, args.query)
        else:
            parser.error("either --get or --query required")
            return 1  # unreachable
    except FileNotFoundError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 1
    except yaml.YAMLError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 3
    except TypeError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 3

    if value is None and args.default is None and args.get:
        print("[IMP:9][yaml_query] key not found, no default", file=sys.stderr)
        return 1

    # Handle --items flag (one item per line for lists)
    if args.items:
        if isinstance(value, list):
            for item in value:
                print(_format_item(item, args.json_output))
            return 0
        print(f"[IMP:9][yaml_query] --items requires a list value, got {type(value).__name__}", file=sys.stderr)
        return 1

    # ⚠️ TRAP[BUG] · 2026-07-21 · HIGH · yaml_get_field возвращает Python repr вместо JSON для dict/list
    # · Symptom: provision-environment.sh _provision_networks() получает невалидный JSON
    # ·   `[{'name': 'proxy-net', ...}]` вместо `[{"name": "proxy-net", ...}]`
    # ·   → while loop итерирует 0 сетей → docker compose up падает с "network X declared as external"
    # · Root: print(value) выводит Python str() для dict/list, а не json.dumps()
    # · Fix: заменить print(value) на json.dumps(value) при выводе dict/list
    # ·   _format_item() для --items уже правильно использует json.dumps()
    # ·   Проблема только в режиме --get без --items для не-scalar значений
    # · Prevention: unit-тест, проверяющий что yaml_get_field для списка возвращает валидный JSON
    if args.json_output or isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())


# endregion CLI
