#!/usr/bin/env python3
# GREP_SUMMARY: preflight, deploy-preflight, fqdn-uniqueness, port-conflict, validate-sh, ss-tlnp, E4, deploy-engine-decomposition
# STRUCTURE: ▶ run_preflight_checks ┌project_dir, service, validate_script┐ → ◇ validate.sh --check-fqdn → ◇ ai-platform.yaml host_port → ◇ ss -tlnp conflict → ⎋ None (raises ValidationError/DeployError)
# region MODULE_CONTRACT
## @purpose  Pre-deploy validation checks (DevPlan 119 E4) — extracted from DeployEngine._preflight_checks
##           (deploy_engine.py 874 LOC монолит): FQDN uniqueness + port conflict detection.
## @scope    core/internal/deploy/preflight.py — consumed by DeployEngine (тонкий фасад-делегат).
##           Чистые module-level функции с явными параметрами (validate_script передаётся caller'ом).
## @invariants
##   - FQDN check via validate.sh subprocess (canonical, not duplicated in Python)
##   - Port conflict via ss -tlnp (shows ALL listening ports)
##   - Both checks are non-blocking for first deploy (warnings logged)
##   - Raises ValidationError (FQDN) / DeployError (port) — caller решает abort vs continue
## @rationale E4 (DevPlan 119, AUDIT-2 M9): _preflight_checks (57 LOC) вынесен из монолита
##           deploy_engine.py в изолированный модуль — тестируемость + декомпозиция 874→<600 LOC.
## @changes  2026-08-02 · DevPlan 119 E4 — экстракция из DeployEngine._preflight_checks
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

# B1: единый shared-ридер ai-platform.yaml (yaml.safe_load вне shared удалён)
from core.internal.shared import project_yaml as shared_project_yaml

# W1-A1 (план 170): +CONVERGE_DOCKER_TIMEOUT (30) — validate_script --check-fqdn (системная команда)
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT, DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_run_preflight_checks
## @purpose  Validate pre-deploy conditions: FQDN uniqueness and port conflicts.
## @io       ⇥ project_dir: str, service: str, validate_script: str → ⎋ None (raises on fail)
## @complexity — O(1) — subprocess calls to validate.sh and ss
## @invariants
##   - FQDN check via validate.sh subprocess (canonical, not duplicated in Python)
##   - Port conflict via ss -tlnp (shows ALL listening ports)
##   - Both checks are non-blocking for first deploy (warnings logged)
##   - Raises ValidationError if FQDN conflict; DeployError if port conflict
def run_preflight_checks(project_dir: str, service: str, validate_script: str) -> None:  # ruff: ignore[ARG001]
    """Run pre-deploy validation checks (E4 — extracted from DeployEngine._preflight_checks).

    Args:
        project_dir: Project directory.
        service: Docker Compose service name.
        validate_script: Path to canonical validate.sh.

    Raises:
        ValidationError: If FQDN conflict detected.
        DeployError: If port conflict detected.
    """
    # 🧐 TRAP[DECISION] · 2026-07-26 · — · FQDN uniqueness via validate.sh subprocess
    # · Rejected: Python socket/FQDN parsing (duplicates validate.sh logic)
    # · Reason: validate.sh is the canonical FQDN check
    if Path(validate_script).is_file() and os.access(validate_script, os.X_OK):
        logger.info("[IMP:8][preflight] Checking FQDN uniqueness...")
        result = subprocess.run(
            [validate_script, "--check-fqdn", project_dir],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            msg = f"FQDN conflict detected: {result.stderr.strip()}"
            logger.error("[IMP:10][preflight] %s", msg)
            raise ValidationError(msg)
    else:
        logger.info("[IMP:6][preflight] validate.sh not found — skipping FQDN check")

    # 🧐 TRAP[DECISION] · 2026-07-26 · — · Port conflict via ss -tlnp
    # · Rejected: Docker network inspect (only shows mapped ports, not host conflicts)
    # · Reason: ss -tlnp shows ALL listening ports
    ai_yaml = Path(project_dir) / "ai-platform.yaml"
    if Path(ai_yaml).is_file():
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            config = shared_project_yaml.load_project_yaml(Path(project_dir))
            host_port = shared_project_yaml.get_monitoring(config).get("host_port")
            if host_port and isinstance(host_port, (int, str)) and int(host_port) > 0:
                port = int(host_port)
                logger.info("[IMP:8][preflight] Checking port %s for conflicts...", port)
                ss_result = subprocess.run(
                    ["ss", "-tlnp"], capture_output=True, text=True, timeout=DOCKER_CMD_TIMEOUT, check=False
                )
                if f":{port} " in ss_result.stdout:
                    msg = f"Port {port} already in use — deploy blocked"
                    logger.error("[IMP:10][preflight] %s", msg)
                    raise DeployError(msg)
                logger.info("[IMP:8][preflight] Port %s available", port)
        except (ImportError, ValueError, OSError) as e:
            logger.info("[IMP:6][preflight] Could not check port: %s", e)
    logger.info("[IMP:9][preflight] Pre-deploy checks complete — no conflicts (project=%s)", project_dir)


# endregion FUNC_run_preflight_checks


# region EXC_ValidationError
class ValidationError(Exception):
    """FQDN uniqueness validation failed."""


# endregion EXC_ValidationError


# region EXC_DeployError
class DeployError(Exception):
    """Port conflict or other deploy-blocking error."""


# endregion EXC_DeployError
