# GREP_SUMMARY: test-postgres compose-config-valid readiness-rc liveness-rc module-yaml no-ports no-resource-limits pg-hba docker-bridge-range
# STRUCTURE: ▶ fixture(postgres_fixtures) → test_module_yaml_install_type_docker → test_compose_no_ports → test_compose_restart_unless_stopped → test_compose_stop_grace_period → test_compose_has_resource_limits → test_compose_shared_db_net_external → test_compose_liveness_healthcheck_uses_pg_isready → test_compose_postgres_command_loads_mounted_config → test_postgresql_conf_owner_tuning → test_pg_hba_covers_docker_bridge_range → test_healthcheck_sh_exists → test_ready_check_sh_* → test_sudo_whitelist_*
# region MODULE_CONTRACT
## @purpose  Verify postgres compose config validity, readiness script behaviour,
##           healthcheck scripts, and module contract compliance without starting a real container.
##           Pgbouncer static audit tests split to test_pgbouncer_static.py (TASK-4).
## @scope    Unit tests; uses tmp_path for isolation via fixtures; no docker daemon required for config tests.
##           docker compose config called as subprocess (validates YAML synthesis only).
## @invariants
##   - Compose file has no ports directive
##   - restart: unless-stopped is set (not always — prevents Docker restart after compose down)
##   - stop_grace_period: 60s is set
##   - shared-db-net declared as external: true
##   - module.yaml install_type: docker
##   - deploy.resources.limits.memory = 1G
##   - At least one IMP:9 log per §TESTING
## @rationale Q: Why test compose config without containers? A: Configuration errors are
##            caught early without docker daemon dependency.
##            restart: unless-stopped is the universal policy per DD2 — always restarts containers
##            even after administrative docker compose down.
## @changes — LAST_CHANGE: 2026-07-11 | Extracted pgbouncer tests → test_pgbouncer_static.py (TASK-4: 17→12)
##            LAST_CHANGE: 2026-07-01 | Added MODULE_CONTRACT region for pre-commit compliance
##            REFACTORED: 2026-07-08 | Hardcoded paths→fixtures (Wave 2.2)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import shutil

import pytest
import yaml

logger = logging.getLogger(__name__)

import pathlib
from pathlib import Path

from conftest import ldd_trajectory

# region FIXTURES


@pytest.fixture(scope="module")
def postgres_fixtures(tmp_path_factory):
    """Copy postgres module files to temp dir for isolated testing."""
    # 177 W2.3: файл переехал tests/ → tests/unit/ — путь к core/ из tests/unit/ = ../../core
    src = Path(__file__).parent.parent.parent / "core" / "modules" / "postgres"
    dst = tmp_path_factory.mktemp("postgres")
    shutil.copytree(src, str(dst), dirs_exist_ok=True)
    dst_str = str(dst)
    return {
        "MODULE_DIR": dst_str,
        "COMPOSE_FILE": Path(dst_str) / "docker-compose.base.yml",
        "MODULE_YAML": Path(dst_str) / "module.yaml",
        "HEALTHCHECK_SH": Path(dst_str) / "healthcheck.sh",
        "READY_CHECK_SH": Path(dst_str) / "ready-check.sh",
    }


# endregion FIXTURES


# region HELPERS


def _load_compose(compose_path: str) -> dict:
    """Load docker-compose.base.yml as parsed YAML dict."""
    with pathlib.Path(compose_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# 🧐 TRAP[DECISION] · 2026-07-11 · — · Pgbouncer static audit tests extracted to test_pgbouncer_static.py
# · Rejected: keeping pgbouncer tests in test_postgres.py (single file)
# · Reason: separation of concerns — postgres tests focus on DB liveness/readiness;
# ·   pgbouncer tests focus on proxy config. 17→12 tests in postgres, +5 in pgbouncer_static.
# · Rev: if test_pgbouncer_static.py grows beyond 10 tests, consider further splitting.

# endregion HELPERS


# region MODULE_YAML_TESTS


@ldd_trajectory
def test_module_yaml_install_type_docker(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][module_yaml] START: checking module.yaml")

        module_yaml = postgres_fixtures["MODULE_YAML"]
        assert pathlib.Path(module_yaml).is_file(), f"module.yaml not found: {module_yaml}"
        with pathlib.Path(module_yaml).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)

        logger.critical(
            "[IMP:9][test_postgres][module_yaml] ASSERT: install_type=%s name=%s",
            data.get("install_type"),
            data.get("name"),
        )
        assert data.get("install_type") == "docker", f"Expected install_type=docker, got: {data.get('install_type')}"
        assert data.get("name") == "postgres"
        assert "version" not in data, "D4 contract violation: module.yaml must NOT have version field"


@ldd_trajectory
def test_module_yaml_no_per_module_version_file(postgres_fixtures, caplog) -> None:
    """AR6: no per-module VERSION file — version only in module.yaml."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][no_version_file] START: checking AR6 compliance")

        version_file = Path(postgres_fixtures["MODULE_DIR"]) / "VERSION"
        exists = pathlib.Path(version_file).is_file()

        logger.critical(
            "[IMP:9][test_postgres][no_version_file] ASSERT: VERSION file exists=%s (expected False, AR6)",
            exists,
        )
        assert not exists, f"AR6 violation: per-module VERSION file must not exist: {version_file}"


# endregion MODULE_YAML_TESTS


# region COMPOSE_CONTRACT_TESTS


@ldd_trajectory
@pytest.mark.parametrize(
    ("extract", "expected", "violation"),
    [
        (
            lambda d: d.get("services", {}).get("postgres", {}).get("ports", None),
            None,
            "R1 violation: ports must be absent",
        ),
        (
            lambda d: d.get("services", {}).get("postgres", {}).get("restart"),
            "unless-stopped",
            "R1 violation: restart must be 'unless-stopped'",
        ),
        (
            lambda d: d.get("services", {}).get("postgres", {}).get("stop_grace_period"),
            "60s",
            "06§13 violation: stop_grace_period must be '60s'",
        ),
        (
            lambda d: d.get("networks", {}).get("shared-db-net", {}).get("external"),
            True,
            "00§9 violation: shared-db-net must be external: true",
        ),
    ],
)
def test_compose_contract_fields(postgres_fixtures, caplog, extract, expected, violation) -> None:
    """Parametrized: postgres compose-контракт (ports/restart/grace/net) (F5-reduction)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][compose_contract] START")

        data = _load_compose(postgres_fixtures["COMPOSE_FILE"])
        value = extract(data)

        logger.critical(
            "[IMP:9][test_postgres][compose_contract] ASSERT: value=%s (expected %s)",
            value,
            expected,
        )
        assert value == expected, f"{violation}, got: {value}"


@ldd_trajectory
def test_compose_has_resource_limits(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][compose_has_resources] START")

        data = _load_compose(postgres_fixtures["COMPOSE_FILE"])
        service = data.get("services", {}).get("postgres", {})

        deploy = service.get("deploy", {})
        resources = deploy.get("resources", {}) if deploy else {}
        limits = resources.get("limits", {}) if resources else {}
        memory = limits.get("memory", None)

        logger.critical(
            "[IMP:9][test_postgres][compose_has_resources] ASSERT: memory=%s (expected 1G, TASK-5.1)",
            memory,
        )
        assert memory is not None, "TASK-5.1 violation: deploy.resources.limits.memory must be present"
        assert memory == "1G", f"TASK-5.1 violation: expected 1G, got: {memory}"


@ldd_trajectory
def test_compose_liveness_healthcheck_uses_pg_isready(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][compose_healthcheck] START")

        data = _load_compose(postgres_fixtures["COMPOSE_FILE"])
        service = data.get("services", {}).get("postgres", {})
        hc = service.get("healthcheck", {})
        test_cmd = hc.get("test", [])

        if isinstance(test_cmd, list):
            test_str = " ".join(test_cmd)
        else:
            test_str = str(test_cmd)

        logger.critical(
            "[IMP:9][test_postgres][compose_healthcheck] ASSERT: healthcheck.test contains pg_isready: %s",
            "pg_isready" in test_str,
        )
        assert "pg_isready" in test_str, f"R2 violation: liveness healthcheck must use pg_isready, got: {test_str}"


# endregion COMPOSE_CONTRACT_TESTS


# region COMPOSE_COMMAND_TESTS


@ldd_trajectory
def test_compose_postgres_command_loads_mounted_config(postgres_fixtures, caplog) -> None:
    """Verify postgres service has command directive that loads mounted config files (B1)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][compose_command] START: checking postgres command loads mounted configs")

        data = _load_compose(postgres_fixtures["COMPOSE_FILE"])
        service = data.get("services", {}).get("postgres", {})
        command = service.get("command")

        logger.critical(
            "[IMP:9][test_postgres][compose_command] ASSERT: command=%s (must exist with config_file and hba_file)",
            command,
        )
        assert command is not None, (
            "B1 violation: postgres service missing 'command' directive — "
            "mounted configs are NOT applied; postgres uses default paths"
        )

        if isinstance(command, list):
            cmd_str = " ".join(command)
        else:
            cmd_str = str(command)

        assert "config_file=/etc/postgresql/postgresql.conf" in cmd_str, (
            f"B1 violation: command must reference config_file=/etc/postgresql/postgresql.conf, got: {cmd_str}"
        )
        assert "hba_file=/etc/postgresql/pg_hba.conf" in cmd_str, (
            f"B1 violation: command must reference hba_file=/etc/postgresql/pg_hba.conf, got: {cmd_str}"
        )

        logger.critical(
            "[IMP:9][test_postgres][compose_command] PASS: command correctly loads "
            "/etc/postgresql/postgresql.conf and /etc/postgresql/pg_hba.conf"
        )


# endregion COMPOSE_COMMAND_TESTS


# region POSTGRESQL_CONF_TUNING_TESTS


def _parse_postgresql_conf(conf_path: str) -> dict[str, str]:
    """Parse postgresql.conf into key-value dict, skipping comments and blank lines.

    ## @purpose — Extract postgres tuning params for contract validation.
    ## @io — ⇥ conf_path: str → ⎋ dict[str, str]
    ## @complexity — O(n) where n = number of lines
    ## @invariants
    ##   - Lines starting with # are skipped
    ##   - Blank lines skipped
    ##   - Values are unquoted (both ' and " stripped)
    ##   - First = sign separates key from value (value may contain =)
    """
    result = {}
    with pathlib.Path(conf_path).open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip().strip("'").strip('"')
    return result


@ldd_trajectory
def test_postgresql_conf_owner_tuning(postgres_fixtures, caplog) -> None:
    """Verify postgresql.conf matches owner reference tuning (B2).

    Checks all owner-prescribed parameters plus PITR block preservation.
    """
    conf_path = Path(postgres_fixtures["MODULE_DIR"]) / "config" / "postgresql.conf"

    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][conf_tuning] START: validating postgresql.conf against owner reference")

        assert pathlib.Path(conf_path).is_file(), f"postgresql.conf not found: {conf_path}"
        config = _parse_postgresql_conf(conf_path)

        # — Connections —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: max_connections=%s (expected 30)",
            config.get("max_connections"),
        )
        assert config.get("max_connections") == "30", (
            f"Expected max_connections=30, got: {config.get('max_connections')}"
        )

        # — Memory —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: shared_buffers=%s (expected 512MB)",
            config.get("shared_buffers"),
        )
        assert config.get("shared_buffers") == "512MB", (
            f"Expected shared_buffers=512MB, got: {config.get('shared_buffers')}"
        )

        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: work_mem=%s (expected 8MB)",
            config.get("work_mem"),
        )
        assert config.get("work_mem") == "8MB", f"Expected work_mem=8MB, got: {config.get('work_mem')}"

        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: maintenance_work_mem=%s (expected 256MB)",
            config.get("maintenance_work_mem"),
        )
        assert config.get("maintenance_work_mem") == "256MB", (
            f"Expected maintenance_work_mem=256MB, got: {config.get('maintenance_work_mem')}"
        )

        # — Query safety —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: statement_timeout=%s (expected 5min)",
            config.get("statement_timeout"),
        )
        assert config.get("statement_timeout") == "5min", (
            f"Expected statement_timeout=5min, got: {config.get('statement_timeout')}"
        )

        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: lock_timeout=%s (expected 30s)",
            config.get("lock_timeout"),
        )
        assert config.get("lock_timeout") == "30s", f"Expected lock_timeout=30s, got: {config.get('lock_timeout')}"

        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: idle_in_transaction_session_timeout=%s (expected 60s)",
            config.get("idle_in_transaction_session_timeout"),
        )
        assert config.get("idle_in_transaction_session_timeout") == "60s", (
            "Expected idle_in_transaction_session_timeout=60s, "
            f"got: {config.get('idle_in_transaction_session_timeout')}"
        )

        # — Planner (SSD) —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: random_page_cost=%s (expected 1.1)",
            config.get("random_page_cost"),
        )
        assert config.get("random_page_cost") == "1.1", (
            f"Expected random_page_cost=1.1, got: {config.get('random_page_cost')}"
        )

        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: effective_io_concurrency=%s (expected 200)",
            config.get("effective_io_concurrency"),
        )
        assert config.get("effective_io_concurrency") == "200", (
            f"Expected effective_io_concurrency=200, got: {config.get('effective_io_concurrency')}"
        )

        # — Performance —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: jit=%s (expected off)",
            config.get("jit"),
        )
        assert config.get("jit") == "off", f"Expected jit=off, got: {config.get('jit')}"

        preload = config.get("shared_preload_libraries", "")
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: shared_preload_libraries=%s (must contain pg_stat_statements)",
            preload,
        )
        assert "pg_stat_statements" in preload, (
            f"Expected shared_preload_libraries to contain pg_stat_statements, got: '{preload}'"
        )

        # — Logging —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: log_min_duration_statement=%s (expected 500)",
            config.get("log_min_duration_statement"),
        )
        assert config.get("log_min_duration_statement") == "500", (
            f"Expected log_min_duration_statement=500, got: {config.get('log_min_duration_statement')}"
        )

        # — Client defaults —
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: timezone=%s (expected UTC)",
            config.get("timezone"),
        )
        assert config.get("timezone") == "UTC", f"Expected timezone='UTC', got: {config.get('timezone')}"

        # — PITR block preservation (WAL archiving MUST survive tuning) —
        archive_mode = config.get("archive_mode")
        archive_command = config.get("archive_command")
        logger.critical(
            "[IMP:9][test_postgres][conf_tuning] ASSERT: PITR archive_mode=%s archive_command present=%s",
            archive_mode,
            archive_command is not None and len(archive_command) > 0,
        )
        assert archive_mode == "on", f"PITR preservation violation: archive_mode must be 'on', got: {archive_mode}"
        assert archive_command is not None and len(archive_command) > 0, (
            "PITR preservation violation: archive_command must be present and non-empty"
        )


# endregion POSTGRESQL_CONF_TUNING_TESTS


# region PG_HBA_TESTS


def _parse_pg_hba(hba_path: str) -> list[dict[str, str]]:
    """Parse pg_hba.conf into list of non-comment, non-blank entries.

    ## @purpose — Extract authentication rules for contract validation.
    ## @io — ⇥ hba_path: str → ⎋ list[dict{type, database, user, address, method}]
    ## @complexity — O(n) where n = number of lines
    ## @invariants
    ##   - Comment lines (#) and blank lines are skipped
    ##   - For 'local' entries: no address field (Unix sockets)
    ##   - For 'host' entries: field index 3 = address, field index 4 = method
    """
    entries: list[dict[str, str]] = []
    with pathlib.Path(hba_path).open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            entry: dict[str, str] = {
                "type": parts[0],
                "database": parts[1],
                "user": parts[2],
            }
            if entry["type"] == "local":
                entry["method"] = parts[3] if len(parts) > 3 else ""
            else:
                entry["address"] = parts[3] if len(parts) > 3 else ""
                entry["method"] = parts[4] if len(parts) > 4 else ""
            entries.append(entry)
    return entries


@ldd_trajectory
def test_pg_hba_covers_docker_bridge_range(postgres_fixtures, caplog) -> None:
    """Verify pg_hba.conf covers full Docker bridge range (B4).

    Docker allocates bridge networks dynamically from 172.17.0.0/16 — 172.31.0.0/16
    (entire 172.16.0.0/12 range). A /16 pin (e.g. 172.20.0.0/16) breaks when Docker
    allocates a different /16 subnet. Must use 172.16.0.0/12.

    Additionally asserts invariant: no public access (0.0.0.0/0 or 'all' with md5/trust).
    """
    hba_path = Path(postgres_fixtures["MODULE_DIR"]) / "config" / "pg_hba.conf"

    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][pg_hba] START: checking pg_hba.conf covers Docker bridge range")

        assert pathlib.Path(hba_path).is_file(), f"pg_hba.conf not found: {hba_path}"
        entries = _parse_pg_hba(hba_path)

        # Filter to host entries with an address field
        host_entries = [e for e in entries if "address" in e]

        # Assert 1: must have 172.16.0.0/12 with md5 (full Docker bridge range)
        has_full_range = any(e.get("address") == "172.16.0.0/12" and e.get("method") == "md5" for e in host_entries)
        logger.critical(
            "[IMP:9][test_postgres][pg_hba] ASSERT: has 172.16.0.0/12 md5=%s",
            has_full_range,
        )
        assert has_full_range, (
            "B4 violation: pg_hba.conf must have 'host all all 172.16.0.0/12 md5' — "
            "Docker bridge dynamically allocates from 172.17–172.31 (full 172.16.0.0/12), "
            "not just /16. "
            f"Found host entries: {[(e.get('address'), e.get('method')) for e in host_entries]}"
        )

        # Assert 2: no public access (invariant preservation — no 0.0.0.0/0 or 'all')
        public_entries = [
            e for e in host_entries if e.get("address") in {"0.0.0.0/0", "all"} and e.get("method") in {"md5", "trust"}
        ]
        logger.critical(
            "[IMP:9][test_postgres][pg_hba] ASSERT: public_entries_count=%d (must be 0 — no public access)",
            len(public_entries),
        )
        assert len(public_entries) == 0, (
            f"Invariant violation: no 0.0.0.0/0 or 'all' entry with md5/trust allowed. Found: {public_entries}"
        )


# endregion PG_HBA_TESTS


# region HEALTHCHECK_SCRIPT_TESTS


@ldd_trajectory
def test_healthcheck_sh_exists(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][healthcheck_exists] START")

        hc_path = postgres_fixtures["HEALTHCHECK_SH"]
        hc_exists = pathlib.Path(hc_path).is_file()
        hc_executable = os.access(hc_path, os.X_OK) if hc_exists else False

        logger.critical(
            "[IMP:9][test_postgres][healthcheck_exists] ASSERT: exists=%s executable=%s",
            hc_exists,
            hc_executable,
        )
        assert hc_exists, f"healthcheck.sh not found: {hc_path}"
        with pathlib.Path(hc_path).open(encoding="utf-8") as f:
            first_line = f.readline().strip()
        assert first_line.startswith("#!/"), f"healthcheck.sh missing shebang: {first_line}"


@ldd_trajectory
def test_ready_check_sh_removed(postgres_fixtures, caplog) -> None:
    """ready-check.sh УДАЛЁН (волна 118 B7) — 0 runtime-вызовов (только COPY в Dockerfile).

    ## @purpose  R5 negative: файл удалён из core/modules/postgres/; Dockerfile COPY строки убраны.
    ## @scenario postgres_fixtures копирует module dir → ready-check.sh отсутствует
    ## @rationale В 118 B7 ready-check.sh удалён (мёртвый — только COPY в образ, 0 вызовов
    ##            из compose/nginx). Резолв пути в фикстуре оставлен для совместимости.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][ready_check_removed] START: B7 R5 — ready-check.sh удалён")

        rc_path = postgres_fixtures["READY_CHECK_SH"]
        rc_exists = pathlib.Path(rc_path).is_file()

        logger.critical(
            "[IMP:9][test_postgres][ready_check_removed] ASSERT: ready-check exists=%s (B7: должен быть удалён)",
            rc_exists,
        )
        assert not rc_exists, f"B7 FAIL: ready-check.sh должен быть удалён (0 callers), найден: {rc_path}"


@ldd_trajectory
def test_ready_check_sh_absent_from_dockerfile(postgres_fixtures, caplog) -> None:
    """Dockerfile не должен COPY ready-check.sh (волна 118 B7)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][ready_check_dockerfile] START")
        dockerfile = Path(postgres_fixtures["MODULE_DIR"]) / "Dockerfile"
        assert pathlib.Path(dockerfile).is_file(), f"Dockerfile not found: {dockerfile}"
        with pathlib.Path(dockerfile).open(encoding="utf-8") as f:
            content = f.read()
        assert "ready-check.sh" not in content, "B7 FAIL: Dockerfile всё ещё COPY ready-check.sh"
        logger.critical("[IMP:9][test_postgres][ready_check_dockerfile] PASS: Dockerfile чист (B7)")


# endregion HEALTHCHECK_SCRIPT_TESTS


# region SUDO_WHITELIST_TESTS


@ldd_trajectory
def test_sudo_whitelist_agent_cannot_stop_or_restore(postgres_fixtures, caplog) -> None:
    """sudo-whitelist.conf must NOT grant agent make:stop or make:restore (07 §2.3, AC-P5)."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_postgres][sudo_whitelist_agent] START")

        whitelist_path = Path(postgres_fixtures["MODULE_DIR"]) / "sudo-whitelist.conf"
        assert pathlib.Path(whitelist_path).is_file(), f"sudo-whitelist.conf not found: {whitelist_path}"

        with pathlib.Path(whitelist_path).open(encoding="utf-8") as f:
            content = f.read()

        agent_lines = [line for line in content.splitlines() if line.startswith("agent ") and not line.startswith("#")]

        agent_targets = []
        for line in agent_lines:
            parts = line.split()
            if len(parts) >= 2:
                agent_targets.append(parts[1])

        logger.critical(
            "[IMP:9][test_postgres][sudo_whitelist_agent] ASSERT: agent_targets=%s "
            "(must NOT contain make:stop or make:restore)",
            agent_targets,
        )
        assert "make:stop" not in agent_targets, (
            f"07§2.3 violation: agent must NOT have make:stop, found in: {agent_targets}"
        )
        assert "make:restore" not in agent_targets, (
            f"07§2.3 violation: agent must NOT have make:restore, found in: {agent_targets}"
        )
        assert "make:status" in agent_targets, "agent must have make:status"
        assert "make:backup" in agent_targets, "agent must have make:backup"


# endregion SUDO_WHITELIST_TESTS
