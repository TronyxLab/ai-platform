#!/usr/bin/env python3
# GREP_SUMMARY: validation-helpers, verify-core-files, validate-node-yaml, validate-sudoers, schema, jsonschema, sudoers-d
# STRUCTURE: ▶ verify_core_files ┌node-lifecycle.sh marker + VERSION┐ → ⚡ validate_node_yaml ┌NodeYaml + jsonschema (fallback subprocess)┐ → ⚡ validate_sudoers ┌/etc/sudoers.d owner 0:0 mode≤0440┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Валидационные I/O-хелперы bootstrap-фаз (core delivery, node.yaml schema,
##           sudoers.d security) — извлечены из state_machine (B9 T1, U-08). Публичные.
## @scope    validation.py: verify_core_files, validate_node_yaml, validate_sudoers.
##           Используются phases.py (φ3 platform_setup, φ5 node_configuration, φ10 node_config_update).
## @invariants
##   - verify_core_files FATAL (ConfigNotFoundError) при отсутствии core marker (SCP/rsync)
##   - validate_node_yaml: schema-валидация best-effort (jsonschema недоступен → WARN + skip)
##   - validate_sudoers FATAL при нарушении owner=0:0 / mode≤0440 (security)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.internal.shared.exceptions import ConfigNotFoundError, PlatformFatalError
from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)


# region FUNC_verify_core_files
## @purpose  Verify core files are properly delivered (SCP/rsync marker check).
## @io       ⇥ core_dir → ⎋ None (raises ConfigNotFoundError if core missing)
## @complexity O(1)
def verify_core_files(core_dir: str) -> None:
    """Verify core files are properly delivered (SCP/rsync marker check)."""
    marker = os.path.join(core_dir, "internal", "bootstrap", "node-lifecycle.sh")
    if not os.path.isfile(marker):
        raise ConfigNotFoundError(
            f"Core bootstrap not found at {marker}. Deploy first:\n  rsync -avz core/ root@<server>:{core_dir}/"
        )
    ver_file = os.path.join(core_dir, "VERSION")
    if os.path.isfile(ver_file):
        with open(ver_file) as f:
            ver = f.readline().strip()
        logger.info("[IMP:9][verify_core] Core v%s at %s", ver, core_dir)
    else:
        logger.info("[IMP:9][verify_core] Core found at %s (no VERSION file)", core_dir)


# endregion FUNC_verify_core_files


# region FUNC_validate_node_yaml
## @purpose  Schema validation of node.yaml using jsonschema (best-effort).
## @io       ⇥ node_yaml, core_dir → ⎋ None (raises ConfigNotFoundError on missing file)
## @complexity O(1) for schema load + validation
def validate_node_yaml(node_yaml: str, core_dir: str) -> None:
    """Validate node.yaml against node.schema.json."""
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml}")

    schema_file = os.path.join(core_dir, "schemas", "node.schema.json")
    if not os.path.isfile(schema_file):
        logger.warning("[IMP:7][validate_node_yaml] Schema file not found at %s — skipping validation", schema_file)
        return

    try:
        import jsonschema

        from core.internal.shared.node_yaml import NodeYaml

        with open(schema_file) as f:
            schema = json.load(f)
        instance = NodeYaml(node_yaml).raw()
        jsonschema.validate(instance, schema)
        logger.info("[IMP:9][validate_node_yaml] node.yaml valid against schema")
    except ImportError:
        logger.warning("[IMP:7][validate_node_yaml] yaml/jsonschema not available — skipping Python validation")
        # Fall back to subprocess python3 (B4: non_fatal=True + fatal_rc=(127,) — единый канон)
        run_subprocess(
            [
                "python3",
                "-c",
                f"""
import json, sys
from core.internal.shared.node_yaml import NodeYaml
import jsonschema
instance = NodeYaml('{node_yaml}').raw()
with open('{schema_file}') as f:
    schema = json.load(f)
jsonschema.validate(instance, schema)
""",
            ],
            non_fatal=True,
            fatal_rc=(127,),
        )
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        logger.warning("[IMP:7][validate_node_yaml] node.yaml validation failed: %s", e)


# endregion FUNC_validate_node_yaml


# region FUNC_validate_sudoers
## @purpose  Validate /etc/sudoers.d files for correct ownership and permissions.
## @io       ⇥ None → ⎋ None (raises PlatformFatalError on violations)
## @complexity O(N) where N = files in sudoers.d
def validate_sudoers() -> None:
    """Validate /etc/sudoers.d files for correct ownership and permissions."""
    sudoers_d = "/etc/sudoers.d"
    if not os.path.isdir(sudoers_d):
        logger.info("[IMP:7][sudoers] %s not found — skipping validation", sudoers_d)
        return

    try:
        entries = list(Path(sudoers_d).iterdir())
    except PermissionError:
        logger.warning(
            "[IMP:7][sudoers] Permission denied reading %s — skipping validation (non-root or restricted permissions)",
            sudoers_d,
        )
        return

    errors = 0
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name == "README":
            continue

        stat_info = entry.stat()
        owner = f"{stat_info.st_uid}:{stat_info.st_gid}"
        mode = oct(stat_info.st_mode)[-3:]

        if owner != "0:0":
            logger.error("[IMP:10][sudoers] %s: owner %s instead of 0:0", entry.name, owner)
            errors += 1
        mode_int = int(mode, 8)
        if mode_int > 0o440:
            logger.error("[IMP:10][sudoers] %s: permissions %s instead of ≤0440", entry.name, mode)
            errors += 1

    if errors > 0:
        raise PlatformFatalError(
            f"{errors} sudoers file(s) with wrong owner/permissions. Fix:\n"
            f"  chown root:root {sudoers_d}/*\n"
            f"  chmod 0440 {sudoers_d}/*"
        )
    logger.info("[IMP:9][sudoers] All sudoers files validated: owner=root:root, mode≤0440")


# endregion FUNC_validate_sudoers
