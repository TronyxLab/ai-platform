#!/usr/bin/env python3
"""
# GREP_SUMMARY: yaml, extract-field, node-yaml, typed, bootstrap
# STRUCTURE: ▶ extract_yaml_field → ⎋ CLI
"""

# region MODULE_CONTRACT
## @purpose  Extract typed fields from YAML files via dotted path — replaces inline python3 -c blocks in bootstrap.sh
## @scope    YAML field extraction for node.yaml and other bootstrap configuration files
## @invariants
##   - Never raises: returns "" on any parse error, missing key, or type error
##   - Field path is a dotted string splitting on '.' (e.g. "node.owner_key")
##   - Lists are handled by taking the first element's sub-key
##   - Depends on PyYAML (`import yaml`) — no fallback parser
## @rationale Inline `python3 -c "..."` in bootstrap.sh is fragile, untestable, and produces 0 LDD telemetry.
##            A dedicated module provides testable, logged, single-responsibility extraction.
## @changes
##   2026-07-25 · Initial — extracted from bootstrap.sh inline python3 blocks
## @modulemap
##   ▸ extract_yaml_field() — core extractor, called via CLI or Python import
## @usecases
##   ▸ bootstrap.sh: extract node.owner_key from node.yaml
##   ▸ bootstrap.sh: extract node.fqdn from node.yaml
##   ▸ bootstrap.sh: extract docker.mirror from node.yaml
# endregion MODULE_CONTRACT

import logging
import sys
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# region FUNC_extract_yaml_field
## @purpose  Extract a field from a YAML file using a dotted key path — returns "" on any error
## @io       [file_path: str, *field_path: str] → [str]
## @complexity O(depth) where depth = len(field_path)
def extract_yaml_field(file_path: str, *field_path: str) -> str:
    """
    Extract a field from YAML file using dotted path.

    Opens file, parses YAML, traverses field_path keys.
    If a field is a list, takes the first element's sub-key.
    Never raises — returns "" on any error.

    Args:
        file_path: Path to YAML file.
        *field_path: One or more key names forming the dotted path.

    Returns:
        Field value as string, or "" if not found / parse error.

    Example:
        extract_yaml_field('node.yaml', 'node', 'owner_key')
        → 'ssh-ed25519 AAAA...'
    """
    # ── Validate file existence ────────────────────────────────────────────────
    imp_level = 7
    func = "extract_yaml_field"
    logger.info(f"[IMP:{imp_level}][{func}][file_check] path={file_path}")

    if not file_path:
        logger.warning(f"[IMP:{imp_level}][{func}][empty_path] file_path is empty")
        return ""

    # ── Read and parse YAML ────────────────────────────────────────────────────
    imp_level = 8
    logger.info(f"[IMP:{imp_level}][{func}][parse] opening {file_path}")
    try:
        with open(file_path) as fh:
            data: Any = yaml.safe_load(fh)
    except FileNotFoundError:
        logger.warning(f"[IMP:{imp_level}][{func}][parse] file not found: {file_path}")
        return ""
    except yaml.YAMLError as exc:
        logger.warning(f"[IMP:{imp_level}][{func}][parse] YAML parse error: {exc}")
        return ""
    except PermissionError:
        logger.warning(f"[IMP:{imp_level}][{func}][parse] permission denied: {file_path}")
        return ""
    except OSError as exc:
        logger.warning(f"[IMP:{imp_level}][{func}][parse] OS error: {exc}")
        return ""

    if data is None:
        logger.info(f"[IMP:{imp_level}][{func}][parse] empty YAML file: {file_path}")
        return ""

    # ── Traverse field path ────────────────────────────────────────────────────
    imp_level = 9
    current: Any = data
    path_parts = list(field_path)

    if not path_parts:
        logger.info(f"[IMP:{imp_level}][{func}][traverse] empty field path, returning raw string")
        return str(current)

    logger.info(f"[IMP:{imp_level}][{func}][traverse] path={'.'.join(path_parts)}")

    for idx, key in enumerate(path_parts):
        # If we hit a list, take the first element
        if isinstance(current, list):
            if len(current) == 0:
                logger.info(f"[IMP:{imp_level}][{func}][traverse] empty list at path part {idx} ('{key}')")
                return ""
            current = current[0]

        # Navigate into dict
        if not isinstance(current, dict):
            logger.info(
                f"[IMP:{imp_level}][{func}][traverse] non-dict at path part {idx} ('{key}'): "
                f"type={type(current).__name__}"
            )
            return ""

        if key not in current:
            logger.info(
                f"[IMP:{imp_level}][{func}][traverse] key not found at path part {idx} ('{key}'): "
                f"available={list(current.keys())}"
            )
            return ""

        current = current[key]

    # ── Convert result to string ───────────────────────────────────────────────
    imp_level = 10
    result: str = str(current)
    logger.info(
        f"[IMP:{imp_level}][{func}][result] path={'.'.join(path_parts)} type={type(current).__name__} len={len(result)}"
    )
    return result


# endregion FUNC_extract_yaml_field


# region CLI
if __name__ == "__main__":
    imp_level = 7
    logger.info(f"[IMP:{imp_level}][CLI][start] argv={sys.argv}")

    if len(sys.argv) < 3:
        print("Usage: yaml_helpers.py <file> <field.path>", file=sys.stderr)
        logger.warning(f"[IMP:{imp_level}][CLI][usage] insufficient arguments ({len(sys.argv)}), need ≥3")
        sys.exit(1)

    file_path_arg: str = sys.argv[1]
    field_path_arg: str = sys.argv[2]
    field_parts = field_path_arg.split(".")

    logger.info(f"[IMP:{imp_level}][CLI][extract] file={file_path_arg} path={field_path_arg} parts={field_parts}")

    value = extract_yaml_field(file_path_arg, *field_parts)
    print(value)

    imp_level = 10
    logger.info(f"[IMP:{imp_level}][CLI][done] output length={len(value)}")
    sys.exit(0)
# endregion CLI
