#!/usr/bin/env python3
# GREP_SUMMARY: gate-single-orchestrator, docker-compose-gate, scp-rsync-gate, forced-command-gate, deploy-unification
# STRUCTURE: ▶ LAYER1: grep python subprocess docker compose → ◇ whitelist check → ⊕ FAIL | PASS
#            ▶ LAYER2: grep shell scp/rsync → ◇ whitelist check → ⊕ FAIL | PASS
#            ▶ LAYER3: grep shell forced-command → ◇ permitted channels → ⊕ FAIL | PASS
# region MODULE_CONTRACT
## @purpose  Multi-layer gate test verifying all deploy operations go through DeployOrchestrator.
##           Layer 1: Python — fail if `docker compose` called via subprocess outside allowed modules.
##           Layer 2: Shell — fail if `scp`/`rsync` used outside channels.py or bootstrap.
##           Layer 3: Shell — fail if ssh forced-command invoked outside permitted channels.
## @scope    CI gate (make gate MODE=fast). Blocks merge if a new deploy mechanism bypasses DeployOrchestrator.
## @invariants
##   - ALLOWED_DOCKER_COMPOSE_MODULES — must be updated if a new module needs direct compose access
##   - ALLOWED_SCP_RSYNC_PATHS — bootstrap scripts and channels.py only
##   - Layer 3 uses regex patterns to detect forced-command definitions in SSH configs
## @rationale DevPlan 089 T17: prevents regression to 6+ parallel deploy paths.
##            Without this gate, a new deploy path can be added without DeployOrchestrator oversight.
## @changes 2026-07-30 | DevPlan 089 T17 — Created multi-layer gate test
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re

import pytest

logger = logging.getLogger(__name__)

# ── Path resolution ──────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE_DIR = os.path.join(_PROJECT_ROOT, "core")
_CORE_INTERNAL_DIR = os.path.join(_CORE_DIR, "internal")

# ── Whitelists ────────────────────────────────────────────────────────────────

# Layer 1: Python files that are ALLOWED to call docker compose via subprocess
# NOTE: relpath is relative to core/ directory (no "core/" prefix)
ALLOWED_DOCKER_COMPOSE_MODULES: list[str] = [
    "internal/deploy/orchestrator.py",
    "internal/deploy/deploy_engine.py",  # DeployEngine.deploy_compose()
    "internal/bootstrap/deploy/docker_orchestrator.py",  # Not yet migrated
    "internal/bootstrap/deploy/compose_preflight.py",  # Compose preflight
    "internal/shared/docker_compose.py",  # Shared docker compose wrapper
    "internal/practices/check_project.py",  # K1 project-check: docker compose config --quiet (read-only validation, DevPlan 137)
]

# Layer 2: Shell/Python files that are ALLOWED to use scp/rsync directly
# NOTE: relpath is relative to core/ directory (no "core/" prefix)
ALLOWED_SCP_RSYNC_PATHS: list[str] = [
    "internal/deploy/channels.py",  # SCPChannel
    "internal/bootstrap/",  # Bootstrap scripts (nature: scp/rsync for core delivery)
    "entrypoints/bootstrap.sh",  # Bootstrap entrypoint
    "entrypoints/converge.sh",  # Not yet migrated
    "internal/bootstrap/converge.sh",  # Not yet migrated
]

# Layer 2: Non-deploy patterns — files that use scp/rsync for their own legitimate purposes
# (backup, module setup, library functions) — NOT deploy path bypasses
NON_DEPLOY_IGNORE_PREFIXES: list[str] = [
    "modules/",  # Module scripts (backup, setup, etc.)
    "lib/",  # Library functions (ssh.sh exports rsync/scp)
    "internal/scaffold/",  # Scaffolding, not deploy
]

# Layer 3: ssh forced-command patterns that are ALLOWED
# These are SSH authorized_keys command="..." directives
# DevPlan 116 B1 T2/T7: forced-command = `orchestrator_cli dispatch` (диспетчер SSH_ORIGINAL_COMMAND);
# receive/deploy-many — legacy/локальные subcommands, остаются разрешёнными.
PERMITTED_FORCED_COMMANDS: list[str] = [
    r"python3\s+-m\s+core\.internal\.deploy\.orchestrator_cli\s+dispatch",
    r"python3\s+-m\s+core\.internal\.deploy\.orchestrator_cli\s+receive",
    r"python3\s+-m\s+core\.internal\.deploy\.orchestrator_cli\s+deploy-many",
]

# Layer 3: Files that contain `command=` only in documentation/comments,
# not in actual forced-command definitions (false positive suppression)
FORCED_COMMAND_DOC_ONLY: list[str] = [
    "entrypoints/deploy.sh",  # Describes forced-command architecture in docstring
]


# ── Layer Helpers ────────────────────────────────────────────────────────────


def _iter_py_files(root_dir: str) -> list[str]:
    """Recursively yield all .py files under root_dir (relative to core/)."""
    result: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, _CORE_DIR)
                # Skip tests/ directory
                if rel.startswith(("../tests/", "tests/")):
                    continue
                result.append(rel)
    return sorted(result)


def _iter_sh_files(root_dir: str) -> list[str]:
    """Recursively yield all .sh files under root_dir."""
    result: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.endswith(".sh"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, _CORE_DIR)
                result.append(rel)
    return sorted(result)


def _is_path_allowed(filepath: str, allowed_patterns: list[str]) -> bool:
    """Check if filepath matches any allowed pattern (prefix match for dirs)."""
    return any(filepath == pattern or filepath.startswith(pattern) for pattern in allowed_patterns)


def _read_file_content(root: str, rel_path: str) -> str:
    """Read file content, returning empty string on error."""
    full = os.path.join(root, rel_path)
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, FileNotFoundError):
        return ""


# ── Layer 1: Python docker compose gate ────────────────────────────────────


# region TEST_layer1_python_docker_compose
## @purpose — Fail if any Python file in core/internal/ calls `docker compose`
##            via subprocess outside the whitelisted modules.
# ⚠️ TRAP[TEST] · 2026-07-30 · Scenario: layer1_docker_compose_through_orchestrator
# · Last fail: never (new gate)
# · Remove-if: ALL DeployOrchestrator whitelist is removed or deploy paths are fully consolidated
@pytest.mark.gate
def test_layer1_python_docker_compose(caplog):
    """Python: all docker compose calls go through DeployOrchestrator or allowed modules."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    py_files = _iter_py_files(_CORE_INTERNAL_DIR)
    logger.info("[IMP:8][LAYER1] Scanning %d Python files in core/internal/...", len(py_files))

    # Pattern: subprocess.*docker.*compose or run\(.*docker.*compose
    patterns: list[re.Pattern] = [
        re.compile(r'subprocess\.\w+\(.*["\']docker["\'].*["\']compose["\']'),
        re.compile(r'run\(.*["\']docker["\'].*["\']compose["\']'),
        re.compile(r'Popen\(.*["\']docker["\'].*["\']compose["\']'),
    ]

    for py_file in py_files:
        if _is_path_allowed(py_file, ALLOWED_DOCKER_COMPOSE_MODULES):
            logger.info("[IMP:7][LAYER1] ALLOWED (whitelisted): %s", py_file)
            continue

        content = _read_file_content(_CORE_DIR, py_file)

        for pat in patterns:
            if pat.search(content):
                violations.append(py_file)
                logger.warning("[IMP:10][LAYER1] VIOLATION: %s — docker compose call outside whitelist", py_file)
                break

    if violations:
        msg = (
            f"[LAYER1] Found {len(violations)} Python file(s) calling `docker compose` "
            f"outside allowed modules:\n  "
            + "\n  ".join(violations)
            + "\nAllowed modules: "
            + ", ".join(ALLOWED_DOCKER_COMPOSE_MODULES)
            + "\nAll docker compose operations MUST go through DeployOrchestrator or DeployEngine."
        )
        logger.error("[IMP:10][LAYER1] FAIL: %s", msg)
        pytest.fail(msg)

    logger.info("[IMP:9][LAYER1] PASS: No docker compose violations found in %d Python files", len(py_files))


# endregion TEST_layer1_python_docker_compose


# ── Layer 2: Shell scp/rsync gate ──────────────────────────────────────────


# region TEST_layer2_shell_scp_rsync
## @purpose — Fail if any shell file in core/ uses `scp`/`rsync` directly
##            outside channels.py or bootstrap scripts.
# ⚠️ TRAP[TEST] · 2026-07-30 · Scenario: layer2_scp_rsync_through_channels
# · Last fail: never (new gate)
# · Remove-if: All SCP/rsync operations are migrated through SCPChannel
@pytest.mark.gate
def test_layer2_shell_scp_rsync(caplog):
    """Shell: all scp/rsync calls go through SCPChannel (channels.py) or bootstrap."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    sh_files = _iter_sh_files(_CORE_DIR)
    logger.info("[IMP:8][LAYER2] Scanning %d shell files in core/...", len(sh_files))

    # Pattern: scp or rsync command invocations
    scp_pattern = re.compile(r"\bscp\b")
    rsync_pattern = re.compile(r"\brsync\b")

    for sh_file in sh_files:
        # Skip non-deploy files (modules, lib, scaffold)
        if _is_path_allowed(sh_file, NON_DEPLOY_IGNORE_PREFIXES):
            logger.info("[IMP:7][LAYER2] SKIP (non-deploy area): %s", sh_file)
            continue

        if _is_path_allowed(sh_file, ALLOWED_SCP_RSYNC_PATHS):
            logger.info("[IMP:7][LAYER2] ALLOWED (whitelisted): %s", sh_file)
            continue

        content = _read_file_content(_CORE_DIR, sh_file)

        if scp_pattern.search(content) or rsync_pattern.search(content):
            violations.append(sh_file)
            logger.warning("[IMP:10][LAYER2] VIOLATION: %s — scp/rsync call outside whitelist", sh_file)

    if violations:
        msg = (
            f"[LAYER2] Found {len(violations)} shell file(s) using `scp`/`rsync` "
            f"outside allowed paths:\n  "
            + "\n  ".join(violations)
            + "\nAllowed paths: "
            + ", ".join(ALLOWED_SCP_RSYNC_PATHS)
            + "\nAll SCP/rsync operations MUST go through SCPChannel (channels.py) or bootstrap."
        )
        logger.error("[IMP:10][LAYER2] FAIL: %s", msg)
        pytest.fail(msg)

    logger.info("[IMP:9][LAYER2] PASS: No scp/rsync violations found in %d shell files", len(sh_files))


# endregion TEST_layer2_shell_scp_rsync


# ── Layer 3: Shell ssh forced-command gate ────────────────────────────────


# region TEST_layer3_ssh_forced_command
## @purpose — Fail if any shell file in core/ defines ssh forced-command
##            (`command="..."`) outside permitted channels.
# ⚠️ TRAP[TEST] · 2026-07-30 · Scenario: layer3_forced_command_through_orchestrator
# · Last fail: never (new gate)
# · Remove-if: All forced-command usage is consolidated through orchestrator_cli.py
@pytest.mark.gate
def test_layer3_ssh_forced_command(caplog):
    """Shell: all ssh forced-commands go through orchestrator_cli.py or permitted channels."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    sh_files = _iter_sh_files(_CORE_DIR)
    logger.info("[IMP:8][LAYER3] Scanning %d shell files in core/...", len(sh_files))

    # Pattern: command="..." in SSH context (force-command) — deploy-related only
    # We only flag forced-commands that contain deploy/deliver/receive keywords
    forced_cmd_pat = re.compile(r'command\s*=\s*["\']')
    deploy_keyword_pat = re.compile(r"\b(deploy|deliver|receive|platform-)\b", re.IGNORECASE)

    for sh_file in sh_files:
        # Skip non-deploy files — lib/ and modules/ have SSH for non-deploy purposes
        if _is_path_allowed(sh_file, NON_DEPLOY_IGNORE_PREFIXES):
            logger.info("[IMP:7][LAYER3] SKIP (non-deploy area): %s", sh_file)
            continue

        # Skip files that only mention `command=` in documentation/comments
        if _is_path_allowed(sh_file, FORCED_COMMAND_DOC_ONLY):
            logger.info("[IMP:7][LAYER3] SKIP (doc-only forced-command): %s", sh_file)
            continue

        content = _read_file_content(_CORE_DIR, sh_file)

        if not forced_cmd_pat.search(content):
            continue

        # Found a forced-command — check if it's deploy-related
        if not deploy_keyword_pat.search(content):
            logger.info("[IMP:7][LAYER3] SKIP (non-deploy forced-command): %s", sh_file)
            continue

        # Found a deploy-related forced-command — check if it's permitted
        is_permitted = False
        for permitted_re in PERMITTED_FORCED_COMMANDS:
            if re.search(permitted_re, content):
                is_permitted = True
                break

        if is_permitted:
            logger.info("[IMP:7][LAYER3] PERMITTED: %s — forced-command matches orchestrator_cli", sh_file)
        else:
            violations.append(sh_file)
            logger.warning("[IMP:10][LAYER3] VIOLATION: %s — deploy forced-command outside permitted channels", sh_file)

    if violations:
        msg = (
            f"[LAYER3] Found {len(violations)} shell file(s) with ssh forced-command "
            f"outside permitted channels:\n  "
            + "\n  ".join(violations)
            + "\nPermitted commands: "
            + ", ".join(PERMITTED_FORCED_COMMANDS)
            + "\nAll ssh forced-commands MUST use orchestrator_cli.py receive/deploy-many."
        )
        logger.error("[IMP:10][LAYER3] FAIL: %s", msg)
        pytest.fail(msg)

    logger.info("[IMP:9][LAYER3] PASS: No forced-command violations found in %d shell files", len(sh_files))


# endregion TEST_layer3_ssh_forced_command
