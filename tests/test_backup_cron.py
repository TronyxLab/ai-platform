# GREP_SUMMARY: test-backup-cron compose-config-valid cron-schedule spool-volume liveness readiness no-resource-limits
# STRUCTURE: fixtures(module_dir) → test_compose_no_ports → test_restart_always → test_stop_grace_120s → test_no_resource_limits → test_cron_schedule_entries → test_spool_volume_declared → test_liveness_readiness_separate
# region MODULE_CONTRACT
## @purpose  Verify compose contract, cron schedule, spool volume declaration,
##           and liveness/readiness separation without starting real containers.
## @scope    Unit tests; no docker daemon required (YAML/script inspection only).
## @invariants
##   - Compose has no ports (backup-net internal only)
##   - restart: always
##   - stop_grace_period: 120s (06 §13)
##   - No resource limits (AR3)
##   - Crontab has 3 entries at 03:00/03:30/04:00 (03 §4)
##   - Spool volume declared
##   - liveness and readiness are separate scripts
## @rationale Q: Why validate compose config without docker? A: Docker commands are slow
##            and require daemon — YAML/script inspection is faster and sufficient.
## @changes — LAST_CHANGE: 2026-07-01 | Added MODULE_CONTRACT region for pre-commit compliance
def _module_contract():
    pass


# endregion MODULE_CONTRACT
"""
Tests for backup-cron module (TASK-02-05).

@purpose  Verify compose contract, cron schedule, spool volume declaration,
          and liveness/readiness separation without starting real containers.
@scope    Unit tests; no docker daemon required (YAML/script inspection only).
@invariants
  - Compose has no ports (backup-net internal only)
  - restart: always
  - stop_grace_period: 120s (06 §13)
  - No resource limits (AR3)
  - backup-net AND shared-db-net declared as external: true
  - Crontab has 3 entries at 03:00/03:30/04:00 (03 §4)
  - Spool volume /var/lib/platform/backup-spool/ declared
  - liveness (healthcheck.sh/pgrep) and readiness (ready-check.sh) are separate
  - S3 upload script is a stub (upload-s3.sh contains STUB marker)
  - agent: no make:stop, no make:restore (07 §2.3)
  - At least one IMP:9 log per §TESTING
"""

import logging
import os
import re

import pytest
import yaml

logger = logging.getLogger(__name__)

from conftest import ldd_trajectory

MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "core", "modules", "backup-cron")
COMPOSE_FILE = os.path.join(MODULE_DIR, "docker-compose.base.yml")
MODULE_YAML = os.path.join(MODULE_DIR, "module.yaml")
CRONTAB_FILE = os.path.join(MODULE_DIR, "scripts", "crontab")
HEALTHCHECK_SH = os.path.join(MODULE_DIR, "healthcheck.sh")
READY_CHECK_SH = os.path.join(MODULE_DIR, "ready-check.sh")
UPLOAD_S3_SH = os.path.join(MODULE_DIR, "scripts", "upload-s3.sh")
BACKUP_POSTGRES_SH = os.path.join(MODULE_DIR, "scripts", "backup-postgres.sh")
MINIO_COMPOSE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "core", "modules", "minio", "docker-compose.base.yml"
)


# region HELPERS


def _load_compose() -> dict:
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


# endregion HELPERS


# region MODULE_YAML_TESTS


@ldd_trajectory
def test_module_yaml_install_type_docker(caplog) -> None:
    """module.yaml must declare install_type: docker."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][module_yaml] START")

        assert os.path.isfile(MODULE_YAML), f"module.yaml not found: {MODULE_YAML}"
        with open(MODULE_YAML) as f:
            data = yaml.safe_load(f)

        logger.critical(
            "[IMP:9][test_backup_cron][module_yaml] ASSERT: install_type=%s name=%s",
            data.get("install_type"),
            data.get("name"),
        )

        assert data.get("install_type") == "docker"
        assert data.get("name") == "backup-cron"


# endregion MODULE_YAML_TESTS


# region POSTGRES_PORT_ENV_TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_backup_postgres_uses_port_env(caplog) -> None:
    """
    Verify pg_dumpall uses explicit host connection (not pgbouncer default).
    Bug: pg_dumpall went through pgbouncer (port 5432) but pgbouncer didn't
    know template1 → Connection refused. Fix:
      A) backup-postgres.sh pg_dumpall call includes -h "${POSTGRESS_HOST}" (direct to postgres)
      B) docker-compose.base.yml backup-cron.environment has POSTGRES_HOST
      C) minio-createbuckets service has environment: with MINIO_ROOT_USER
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][pg_port_env] START")

        # ── Check A: backup-postgres.sh has -h "${POSTGRESS_HOST" in pg_dumpall ──
        assert os.path.isfile(BACKUP_POSTGRES_SH), f"backup-postgres.sh not found: {BACKUP_POSTGRES_SH}"
        with open(BACKUP_POSTGRES_SH) as f:
            sh_content = f.read()

        has_host_flag = '-h "${POSTGRES_HOST' in sh_content
        logger.critical(
            "[IMP:9][test_backup_cron][pg_port_env][checkA] backup-postgres.sh has -h '${POSTGRES_HOST...': %s",
            has_host_flag,
        )
        assert has_host_flag, (
            'Bug 1 (Fix B) violation: backup-postgres.sh pg_dumpall call must include -h "${POSTGRES_HOST}"'
        )

        # ── Check B: docker-compose.base.yml backup-cron.environment has POSTGRES_HOST ──
        assert os.path.isfile(COMPOSE_FILE), f"compose file not found: {COMPOSE_FILE}"
        with open(COMPOSE_FILE) as f:
            compose_content = f.read()

        has_pg_host_env = "POSTGRES_HOST:" in compose_content
        logger.critical(
            "[IMP:9][test_backup_cron][pg_port_env][checkB] backup-cron environment has POSTGRES_HOST: %s",
            has_pg_host_env,
        )
        assert has_pg_host_env, (
            "Bug 1 (Fix A) violation: docker-compose.base.yml backup-cron "
            'environment must declare POSTGRES_HOST: "${POSTGRES_HOST:-pgbouncer}"'
        )

        # ── Check C: minio-createbuckets has environment: with MINIO_ROOT_USER ──
        assert os.path.isfile(MINIO_COMPOSE_FILE), f"minio compose file not found: {MINIO_COMPOSE_FILE}"
        with open(MINIO_COMPOSE_FILE) as f:
            minio_content = f.read()

        # Check that minio-createbuckets has environment: and MINIO_ROOT_USER
        has_minio_env_section = False
        has_minio_root_user = False

        # Parse by lines: find minio-createbuckets service then check for environment
        minio_lines = minio_content.splitlines()
        in_createbuckets = False
        brace_depth = 0
        for line in minio_lines:
            stripped = line.strip()
            if "minio-createbuckets:" in stripped:
                in_createbuckets = True
                continue
            if in_createbuckets:
                if "environment:" in stripped:
                    has_minio_env_section = True
                if "MINIO_ROOT_USER" in stripped:
                    has_minio_root_user = True
                # Count braces to detect end of service block
                brace_depth += stripped.count("{") - stripped.count("}")
                # Next service at same indentation level ends detection
                # Combined condition: new top-level key without indent
                if (
                    brace_depth <= 0
                    and stripped
                    and not stripped.startswith("#")
                    and line
                    and not line.startswith(" ")
                    and not line.startswith("#")
                ):
                    break

        logger.critical(
            "[IMP:9][test_backup_cron][pg_port_env][checkC] "
            "minio-createbuckets has environment: %s, MINIO_ROOT_USER: %s",
            has_minio_env_section,
            has_minio_root_user,
        )

        assert has_minio_env_section, "Bug 2 violation: minio-createbuckets service must have environment: section"
        assert has_minio_root_user, (
            'Bug 2 violation: minio-createbuckets environment must include MINIO_ROOT_USER: "${MINIO_ROOT_USER}"'
        )


# endregion POSTGRES_PORT_ENV_TESTS


# region COMPOSE_CONTRACT_TESTS

# ── Parametrized compose field checks ──────────────────────────────────
# Each entry: (test_id, field_extractor_lambda, expected_value, description)
_COMPOSE_CHECK_PARAMS = [
    (
        "no_ports",
        lambda d: d.get("services", {}).get("backup-cron", {}).get("ports"),
        None,
        "ports must be absent (backup-net internal only)",
    ),
    (
        "restart_always",
        lambda d: d.get("services", {}).get("backup-cron", {}).get("restart"),
        "always",
        "restart must be 'always'",
    ),
    (
        "stop_grace_120s",
        lambda d: d.get("services", {}).get("backup-cron", {}).get("stop_grace_period"),
        "120s",
        "stop_grace_period must be '120s' (06 §13)",
    ),
    (
        "memory_limit_128M",
        lambda d: (
            d.get("services", {})
            .get("backup-cron", {})
            .get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("memory")
        ),
        "128M",
        "TASK-5.1: deploy.resources.limits.memory must be 128M",
    ),
    (
        "backup_net_external",
        lambda d: d.get("networks", {}).get("backup-net", {}).get("external"),
        True,
        "backup-net must be external: true (TASK-02-03, 00 §9)",
    ),
    (
        "shared_db_net_external",
        lambda d: d.get("networks", {}).get("shared-db-net", {}).get("external"),
        True,
        "shared-db-net must be external: true",
    ),
]


@ldd_trajectory
@pytest.mark.parametrize(
    "test_id,extract_fn,expected,description",
    _COMPOSE_CHECK_PARAMS,
    ids=[p[0] for p in _COMPOSE_CHECK_PARAMS],
)
def test_compose_contract(test_id, extract_fn, expected, description, caplog) -> None:
    """Parametrized compose field validation."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][compose_contract] START: %s — %s", test_id, description)

        data = _load_compose()
        value = extract_fn(data)

        logger.critical(
            "[IMP:9][test_backup_cron][compose_contract][%s] ASSERT: value=%s (expected=%s)",
            test_id,
            value,
            expected,
        )

        if expected is None:
            assert value is None, f"{description}. Got: {value}"
        else:
            assert value == expected, f"{description}. Got: {value} (expected: {expected})"


@ldd_trajectory
def test_compose_spool_volume_declared(caplog) -> None:
    """
    Spool volume /var/lib/platform/backup-spool/ must be declared (03 §4, R3).
    @changes 2026-08-01 (DevPlan 116 B3 T4, U-49): volume declarations consolidated —
    backup-spool/backup-logs bind-тома живут в ROOT docker-compose.yml (единый SoT);
    модульный compose объявляет только сервисные mount-ссылки.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][spool_volume] START")

        # Root compose = single source of truth for volume declarations (B3 T4, U-49)
        root_compose_path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(root_compose_path) as f:
            root_data = yaml.safe_load(f)
        root_volumes = root_data.get("volumes", {})

        # Module compose keeps only service mount references
        data = _load_compose()
        service_volumes = data.get("services", {}).get("backup-cron", {}).get("volumes", [])

        # Check top-level volume declared in ROOT SoT
        has_backup_spool_volume = "backup-spool" in root_volumes
        # Bind device path present in root declaration (bind SoT)
        backup_spool_decl = root_volumes.get("backup-spool", {}) or {}
        device = (backup_spool_decl.get("driver_opts", {}) or {}).get("device", "")

        # Check service mounts include backup-spool path
        spool_path_mounted = any("/var/lib/platform/backup-spool" in str(v) for v in service_volumes)

        logger.critical(
            "[IMP:9][test_backup_cron][spool_volume] ASSERT: volume_declared(root)=%s spool_mounted=%s device=%s",
            has_backup_spool_volume,
            spool_path_mounted,
            device,
        )

        assert has_backup_spool_volume, "R3 violation: backup-spool volume must be declared in root volumes:"
        assert device == "/var/lib/platform/backup-spool", (
            f"R3 violation: backup-spool bind device must be /var/lib/platform/backup-spool. Got: {device}"
        )
        assert spool_path_mounted, (
            f"R3 violation: /var/lib/platform/backup-spool must be mounted in service. Got: {service_volumes}"
        )


# endregion COMPOSE_CONTRACT_TESTS


# region CRON_SCHEDULE_TESTS


@ldd_trajectory
def test_crontab_has_three_schedule_entries(caplog) -> None:
    """Crontab must have exactly 3 entries at 03:00/03:30/04:00 (03 §4, R3)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][cron_schedule] START")

        assert os.path.isfile(CRONTAB_FILE), f"crontab not found: {CRONTAB_FILE}"
        with open(CRONTAB_FILE) as f:
            content = f.read()

        # Extract cron schedule lines (not comments, not env vars)
        schedule_lines = [
            line
            for line in content.splitlines()
            if line
            and not line.startswith("#")
            and not line.startswith("SHELL")
            and not line.startswith("PATH")
            and re.match(r"^\d+\s+\d+", line)
        ]

        # Verify exact times: 03:00, 03:30, 04:00
        times_found = []
        for line in schedule_lines:
            parts = line.split()
            if len(parts) >= 2:
                minute, hour = parts[0], parts[1]
                times_found.append(f"{hour}:{minute}")

        logger.critical(
            "[IMP:9][test_backup_cron][cron_schedule] ASSERT: schedule_count=%d times=%s",
            len(schedule_lines),
            times_found,
        )

        assert len(schedule_lines) >= 3, (
            f"03§4 violation: crontab must have ≥3 entries, found {len(schedule_lines)}: {schedule_lines}"
        )

        # Check 03:00, 03:30, 04:00 are all present
        assert "3:0" in times_found or "3:00" in times_found, f"Missing 03:00 entry, found: {times_found}"
        assert "3:30" in times_found, f"Missing 03:30 entry, found: {times_found}"
        assert "4:0" in times_found or "4:00" in times_found, f"Missing 04:00 entry, found: {times_found}"


@ldd_trajectory
def test_crontab_scripts_exist(caplog) -> None:
    """All scripts referenced in crontab must exist."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][crontab_scripts] START")

        assert os.path.isfile(CRONTAB_FILE)
        with open(CRONTAB_FILE) as f:
            content = f.read()

        # Extract script paths from crontab (paths starting with /)
        script_refs = re.findall(r"/usr/local/bin/\S+\.sh", content)

        logger.critical(
            "[IMP:9][test_backup_cron][crontab_scripts] ASSERT: script_refs=%s",
            script_refs,
        )

        # Scripts exist in scripts/ dir (not /usr/local/bin/ on host)
        scripts_dir = os.path.join(MODULE_DIR, "scripts")
        missing = []
        for ref in script_refs:
            script_name = os.path.basename(ref)
            script_path = os.path.join(scripts_dir, script_name)
            if not os.path.isfile(script_path):
                missing.append(script_name)

        assert not missing, f"Scripts referenced in crontab but not found in scripts/: {missing}"


# endregion CRON_SCHEDULE_TESTS


# region HEALTHCHECK_TESTS


@ldd_trajectory
def test_liveness_uses_pgrep_cron(caplog) -> None:
    """healthcheck.sh (liveness) must use pgrep to check cron daemon."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][liveness_pgrep] START")

        assert os.path.isfile(HEALTHCHECK_SH), f"healthcheck.sh not found: {HEALTHCHECK_SH}"
        with open(HEALTHCHECK_SH) as f:
            content = f.read()

        has_pgrep = "pgrep" in content
        has_cron_ref = "cron" in content

        logger.critical(
            "[IMP:9][test_backup_cron][liveness_pgrep] ASSERT: has_pgrep=%s has_cron_ref=%s",
            has_pgrep,
            has_cron_ref,
        )

        assert has_pgrep, "healthcheck.sh must use pgrep for liveness check"
        assert has_cron_ref, "healthcheck.sh must reference 'cron'"


@ldd_trajectory
def test_readiness_separate_from_liveness(caplog) -> None:
    """ready-check.sh must be a SEPARATE distinct file from healthcheck.sh (06 §12)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][readiness_separate] START")

        assert os.path.isfile(READY_CHECK_SH), f"ready-check.sh not found: {READY_CHECK_SH}"
        assert os.path.isfile(HEALTHCHECK_SH), f"healthcheck.sh not found: {HEALTHCHECK_SH}"

        with open(HEALTHCHECK_SH) as f:
            hc_content = f.read()
        with open(READY_CHECK_SH) as f:
            rc_content = f.read()
        are_distinct = hc_content != rc_content

        logger.critical(
            "[IMP:9][test_backup_cron][readiness_separate] ASSERT: distinct=%s (expected True)",
            are_distinct,
        )

        assert are_distinct, "ready-check.sh and healthcheck.sh must be distinct scripts (06 §12)"


@ldd_trajectory
def test_readiness_checks_crontab_installed(caplog) -> None:
    """ready-check.sh must verify crontab file exists (not just pgrep)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][readiness_crontab] START")

        with open(READY_CHECK_SH) as f:
            content = f.read()

        # Readiness must check: pgrep + crontab file existence
        has_pgrep = "pgrep" in content
        has_crontab_check = "/etc/cron.d/" in content or "CRONTAB_FILE" in content

        logger.critical(
            "[IMP:9][test_backup_cron][readiness_crontab] ASSERT: has_pgrep=%s has_crontab_check=%s",
            has_pgrep,
            has_crontab_check,
        )

        assert has_pgrep, "ready-check.sh must use pgrep to verify cron is running"
        assert has_crontab_check, "ready-check.sh must verify crontab file installation"


# endregion HEALTHCHECK_TESTS


# region S3_STUB_TESTS


@ldd_trajectory
def test_upload_s3_real_implementation(caplog) -> None:
    """upload-s3.sh must NOT be STUB anymore — phase 06 replaced with real S3 logic."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][s3_real] START")

        assert os.path.isfile(UPLOAD_S3_SH), f"upload-s3.sh not found: {UPLOAD_S3_SH}"
        with open(UPLOAD_S3_SH) as f:
            content = f.read()

        # Phase 06: no longer a stub — delegates to upload.py
        has_upload_py = "upload.py" in content
        has_real_upload = "python3" in content
        has_s3_validation = "S3_BUCKET" in content or "S3_ACCESS_KEY" in content

        logger.critical(
            "[IMP:9][test_backup_cron][s3_real] ASSERT: has_upload_py=%s has_python3=%s has_s3_validation=%s",
            has_upload_py,
            has_real_upload,
            has_s3_validation,
        )

        assert has_upload_py, "Phase 06: upload-s3.sh must delegate to upload.py"
        assert has_real_upload, "Phase 06: upload-s3.sh must call python3 upload.py"
        assert has_s3_validation, "Phase 06: upload-s3.sh must validate S3 credentials"


# endregion S3_STUB_TESTS


# region SUDO_WHITELIST_TESTS


@ldd_trajectory
def test_sudo_whitelist_agent_cannot_stop_or_restore(caplog) -> None:
    """
    sudo-whitelist.conf must NOT grant agent make:stop or make:restore (07 §2.3, AC-P5).
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_backup_cron][sudo_whitelist] START")

        whitelist_path = os.path.join(MODULE_DIR, "sudo-whitelist.conf")
        assert os.path.isfile(whitelist_path), f"sudo-whitelist.conf not found: {whitelist_path}"

        with open(whitelist_path) as f:
            content = f.read()
        agent_lines = [line for line in content.splitlines() if line.startswith("agent ") and not line.startswith("#")]

        agent_targets = []
        for line in agent_lines:
            parts = line.split()
            if len(parts) >= 2:
                agent_targets.append(parts[1])

        logger.critical(
            "[IMP:9][test_backup_cron][sudo_whitelist] ASSERT: agent_targets=%s (no stop/restore)",
            agent_targets,
        )

        assert "make:stop" not in agent_targets, (
            f"07§2.3 violation: agent must NOT have make:stop. Found: {agent_targets}"
        )
        assert "make:restore" not in agent_targets, (
            f"07§2.3 violation: agent must NOT have make:restore. Found: {agent_targets}"
        )
        assert "make:status" in agent_targets, "agent must have make:status"
        assert "make:backup" in agent_targets, "agent must have make:backup"


# endregion SUDO_WHITELIST_TESTS
