# GREP_SUMMARY: test clickhouse config mount per-file 10-users-xml ro no-dir-mount
# STRUCTURE: ▶ test_users_d_per_file_mount → grep base.yml for mount patterns → assert per-file ro + no dir bind | ▶ test_no_default_user_artifact_in_repo → ls users.d/ → assert no default-user.xml
# region MODULE_CONTRACT
## @purpose  Validates ClickHouse users.d/ mount refactoring (T1):
##           (a) base.yml mounts only 10-users.xml per-file ro, no users.d directory mount;
##           (b) repo users.d/ contains no default-user.xml runtime artifact.
## @scope    Static audit — no Docker required, no env vars needed.
## @invariants
##   - Test parses docker-compose.base.yml as text (YAML grep) — no PyYAML dependency
##   - Test reads directory listing — no filesystem mutations
## @rationale Per-file ro mount prevents default-user.xml runtime artifact from leaking to host
##   filesystem (langfuse auth outage 2026-07-17 P1). Directory bind mount is replaced.
##   Acceptance criteria: A1 (password rotation converges), A2 (no stale artifact in repo).
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE_YML = _PROJECT_ROOT / "core" / "modules" / "clickhouse" / "docker-compose.base.yml"
_USERS_D_DIR = _PROJECT_ROOT / "core" / "modules" / "clickhouse" / "config" / "users.d"


@pytest.mark.static_audit
def test_users_d_per_file_mount(caplog) -> None:
    """Assert base.yml mounts only 10-users.xml per-file ro and has NO users.d directory mount.

    Acceptance criterion A1: per-file ro mount enables automatic password rotation convergence.
    """
    caplog.set_level(logging.DEBUG)

    assert _BASE_YML.is_file(), f"Base compose not found: {_BASE_YML}"
    content = _BASE_YML.read_text()

    # ── Check 1: per-file ro mount for 10-users.xml is present ────────────
    per_file_mount = "./config/users.d/10-users.xml:/etc/clickhouse-server/users.d/10-users.xml:ro"
    has_per_file = per_file_mount in content
    logger.critical(
        "[IMP:9][test_clickhouse][mount] Per-file ro mount '%s' present: %s",
        per_file_mount,
        has_per_file,
    )
    assert has_per_file, (
        f"Expected per-file ro mount '{per_file_mount}' in {_BASE_YML}"
    )
    logger.info("[IMP:8][test_clickhouse][mount] Per-file ro mount confirmed")

    # ── Check 2: no users.d directory mount in the volumes block ──────────
    # The old dir mount pattern: ./config/users.d:/etc/clickhouse-server/users.d
    # Check only active YAML lines (starting with "- "), not TRAP comments that mention it
    dir_mount_yaml = "- ./config/users.d:/etc/clickhouse-server/users.d"
    has_dir_mount = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "users.d" in stripped and "10-users.xml" not in stripped:
            if dir_mount_yaml in stripped:
                has_dir_mount = True
                break

    logger.critical(
        "[IMP:9][test_clickhouse][mount] Directory bind mount YAML present: %s",
        has_dir_mount,
    )
    assert not has_dir_mount, (
        f"Directory bind mount '{dir_mount_yaml}' must NOT be active YAML in {_BASE_YML}. "
        f"Use per-file ro mount instead."
    )
    logger.info("[IMP:8][test_clickhouse][mount] No users.d directory bind mount in YAML — correct")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


@pytest.mark.static_audit
def test_no_default_user_artifact_in_repo(caplog) -> None:
    """Assert repository users.d/ directory contains no default-user.xml.

    Acceptance criterion A2: git status --ignored shows no default-user.xml after local stack run.
    """
    caplog.set_level(logging.DEBUG)

    assert _USERS_D_DIR.is_dir(), f"users.d/ not found: {_USERS_D_DIR}"

    # List all files in users.d/
    files = sorted(p.name for p in _USERS_D_DIR.iterdir() if p.is_file())
    logger.critical(
        "[IMP:9][test_clickhouse][repo] Files in users.d/: %s",
        files,
    )

    has_default_user = "default-user.xml" in files
    logger.critical(
        "[IMP:9][test_clickhouse][repo] default-user.xml present in repo: %s",
        has_default_user,
    )
    assert not has_default_user, (
        "default-user.xml found in repo config/users.d/. "
        "This is a Docker entrypoint runtime artifact and must NOT be committed. "
        "It was deleted as part of T1; verify gitignore or delete it."
    )
    logger.info("[IMP:8][test_clickhouse][repo] No default-user.xml in repo — clean")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
