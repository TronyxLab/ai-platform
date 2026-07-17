# GREP_SUMMARY: gate dockerignore symlink .dockerignore template symlink-resolution module-fs-structure
# STRUCTURE: ┌discover_docker_modules┐ → ◇ ○ module_name ∋ ┌.dockerignore path┐ → ◇ os.path.islink? T→ ⚡ check target == TEMPLATE_DOCKERIGNORE, F→ fail → ⊕ result
# region MODULE_CONTRACT
## @purpose — Gate test T8.3: validate all 11 docker modules have .dockerignore symlink to templates/.dockerignore
## @scope — Checks each docker module directory (via discover_docker_modules) for .dockerignore as symlink
##          (not regular file) pointing to ../../templates/.dockerignore via os.path.realpath comparison.
## @invariants
##   - Module list is discovered dynamically (discover_docker_modules) — no hardcoded list
##   - TEMPLATE_DOCKERIGNORE is resolved relative to core/templates/ (one level below core/)
##   - os.path.islink() must be True — regular files with same content are rejected
##   - os.path.realpath() resolves all symlinks in the chain before comparison
## @rationale — Post-refactoring audit: centralized .dockerignore management via symlink
##              prevents drift between per-module .dockerignore files and ensures template
##              changes propagate to all modules automatically.
## @changes — 2026-07-14 | Created per TASK-T8.3
## @changes — 2026-07-16 | Migrated from hardcoded DOCKER_MODULES to discover_docker_modules (T7)
# endregion MODULE_CONTRACT

import logging
import os

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

MODULES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "core", "modules"
)

TEMPLATE_DOCKERIGNORE = os.path.join(os.path.dirname(MODULES_DIR), "templates", ".dockerignore")

from tests._conftest.audit import discover_docker_modules


@pytest.mark.gate
@ldd_trajectory
def test_all_docker_modules_have_dockerignore_symlink(caplog):
    """All docker modules have .dockerignore → symlink to ../../templates/.dockerignore (via discover_docker_modules)."""
    docker_modules = discover_docker_modules(MODULES_DIR)
    failed = []
    for module_name in docker_modules:
        symlink_path = os.path.join(MODULES_DIR, module_name, ".dockerignore")

        if not os.path.islink(symlink_path):
            failed.append(f"{module_name}: .dockerignore is not a symlink")
            logger.info("[IMP:9][gate] FAIL: %s → MISSING .dockerignore symlink", module_name)
            continue

        target = os.path.realpath(symlink_path)
        if target != os.path.realpath(TEMPLATE_DOCKERIGNORE):
            failed.append(f"{module_name}: .dockerignore symlink points to {target}, expected {TEMPLATE_DOCKERIGNORE}")
            logger.info("[IMP:9][gate] FAIL: %s → wrong symlink target: %s", module_name, target)
        else:
            logger.info("[IMP:8][gate] PASS: %s → .dockerignore symlink OK", module_name)

    assert not failed, "[IMP:9][gate] dockerignore violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: All %d modules have correct .dockerignore symlink", len(docker_modules))
