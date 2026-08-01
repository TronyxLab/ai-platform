#!/usr/bin/env python3
# GREP_SUMMARY: json-field-extractor, deploy-modules, topo-sort, json-helper
# STRUCTURE: ┌argparse CLI┐ → ◇ stdin JSON parse → ◇ navigate nested keys → ◇ filter/dump/list → ⎋ print value
# region MODULE_CONTRACT
## @purpose  Extract nested JSON fields from stdin for use in shell pipelines.
##           Replaces ALL inline `python3 -c "import json,sys; ..."` in deploy-modules.sh
##           per language policy Tier 1 (AGENTS.md).
## @scope    Called from deploy-modules.sh — thin JSON helper, no business logic.
## @invariants
##   - Reads JSON from stdin
##   - Outputs extracted value to stdout (single value, JSON, or line-per-item)
##   - Exit 0 on success, 1 on error
## @rationale Avoids inline python3 trigger in pre-commit hook while keeping
##            shell <> JSON interop simple.
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


# region main
def main() -> int:
    parser = argparse.ArgumentParser(description="Extract fields from JSON via stdin for shell pipelines")
    parser.add_argument(
        "field",
        nargs="?",
        default=None,
        help="Dot-separated field path (e.g. 'my_module.install_type')",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Return count of top-level items (for list input)",
    )
    parser.add_argument(
        "--dump",
        metavar="FIELD",
        default=None,
        help="Dump sub-field as JSON (e.g. 'groups' extracts data['groups'])",
    )
    parser.add_argument(
        "--default",
        default="",
        help="Default value if field not found (default: empty string)",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Extract list element by index and dump as JSON",
    )
    parser.add_argument(
        "--items",
        action="store_true",
        help="Print each list element on a separate line (plain text, not JSON)",
    )
    parser.add_argument(
        "--filter",
        metavar="KEY=VALUE",
        default=None,
        help="Filter dict entries by key=value, print matching keys comma-separated",
    )
    args = parser.parse_args()

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        logger.critical("[IMP:10][json_field_extractor] Invalid JSON input: %s", exc)
        return 1

    # --count: count top-level list items
    if args.count:
        if isinstance(data, list):
            print(len(data))
        else:
            logger.critical("[IMP:10][json_field_extractor] Cannot count: input is not a list")
            return 1
        return 0

    # --index: extract list element by index
    if args.index is not None:
        if isinstance(data, list):
            try:
                json.dump(data[args.index], sys.stdout)
            except IndexError:
                print("[]")
            return None
        logger.critical("[IMP:10][json_field_extractor] Cannot index: input is not a list")
        return 1

    # --items: print list elements one per line
    if args.items:
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    print(item)
                else:
                    print(json.dumps(item))
            return None
        logger.critical("[IMP:10][json_field_extractor] Cannot list items: input is not a list")
        return 1

    # --filter: filter dict entries and return comma-separated matching keys
    if args.filter is not None:
        if "=" not in args.filter:
            logger.critical("[IMP:10][json_field_extractor] Invalid filter format: %s (use KEY=VALUE)", args.filter)
            return 1
        filter_key, filter_val = args.filter.split("=", 1)
        if isinstance(data, dict):
            matching = [k for k, v in data.items() if isinstance(v, dict) and v.get(filter_key) == filter_val]
            print(",".join(matching))
        else:
            logger.critical("[IMP:10][json_field_extractor] Cannot filter: input is not a dict")
            return 1
        return 0

    # --dump: extract sub-field and dump as JSON
    if args.dump is not None:
        key = args.dump
        if isinstance(data, dict):
            result = data.get(key, {})
        else:
            logger.critical("[IMP:10][json_field_extractor] Cannot dump field from non-dict input")
            return 1
        json.dump(result, sys.stdout)
        return None

    # No field and no operation → dump entire input
    if args.field is None:
        json.dump(data, sys.stdout)
        return None

    # Navigate dot-separated path with --default fallback
    result = data
    for key in args.field.split("."):
        if isinstance(result, dict):
            result = result.get(key, args.default)
        elif isinstance(result, list) and key.isdigit():
            idx = int(key)
            result = result[idx] if idx < len(result) else args.default
        else:
            logger.critical(
                "[IMP:10][json_field_extractor] Cannot navigate key '%s' in %s",
                key,
                type(result).__name__,
            )
            return 1

    if isinstance(result, (dict, list)):
        json.dump(result, sys.stdout)
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
# endregion main
