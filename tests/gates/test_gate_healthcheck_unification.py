#!/usr/bin/env python3
# GREP_SUMMARY: gate-test healthcheck-unification no-docker-inspect check_docker_health start_period exec-check
# STRUCTURE: ▶ 5 gate tests → grep module/*/healthcheck.sh + modules-healthcheck.sh + docker-compose.base.yml → assert patterns
# region MODULE_CONTRACT
## @purpose  Gate tests verifying DevPlan 083 healthcheck unification:
##           AC1: No raw docker inspect State.Running in module healthcheck.sh
##           AC2: All Docker modules call check_docker_health for liveness
##           AC3: modules-healthcheck.sh calls check_docker_health, not raw docker inspect
##           AC4: start_period values standardized to {5s, 15s, 30s, 60s, 180s}
##           AC5: clickhouse/redis/nginx/backup-cron deep mode uses exec_check/check_http
## @scope    5 static audit tests (no Docker required):
##           1. test_no_raw_docker_inspect_in_modules
##           2. test_all_modules_use_check_docker_health
##           3. test_modules_healthcheck_uses_lib
##           4. test_start_period_standardized
##           5. test_exec_check_used_in_docker_exec_modules
## @invariants
##   - All tests read shell scripts as text — no Docker, no subprocess
##   - Files that run in Docker context (platform-secrets) are excluded from docker checks
##   - LDD trajectory with caplog for IMP:9 verification
## @rationale DevPlan 083 defines 7 AC criteria. These gates verify AC1-AC5 statically.
##            AC6 (make healthcheck passes) requires Docker — local integration test.
##            AC7 (no State.Running) is covered by test_no_raw_docker_inspect_in_modules.
## @changes 2026-07-26 · DevPlan 083 — Initial implementation
## @changes 2026-08-31 | cold-start race fix — start_period 180s (langfuse/litellm) добавлен в allowlist AC4/AC5
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

_MODULES_DIR: Path = repo_root() / "core" / "modules"
_HEALTHCHECK_ORCHESTRATOR: Path = repo_root() / "core" / "internal" / "healthcheck" / "modules-healthcheck.sh"
_HEALTHCHECK_LIB: Path = repo_root() / "core" / "lib" / "healthcheck.sh"

# Modules that should use check_docker_health for liveness (excluded: platform-secrets — systemd)
_DOCKER_MODULES: list[str] = [
    "postgres",
    "pgbouncer",
    "redis",
    "clickhouse",
    "nginx",
    "backup-cron",
    "hermes-agent",
    "minio",
    "monitoring",
    "logging",
    "log-collector",
    "langfuse",
    "litellm",
    "status-page",
    "node-metrics",
    "service-exporters",
]

# Modules with exec_check copy-paste pattern (DRIFT-H4): should use exec_check() or check_http()
_EXEC_CHECK_MODULES: dict[str, str] = {
    "clickhouse": "check_http",
    "redis": "exec_check",
    "nginx": "check_http",
    "backup-cron": "exec_check",
}

# Allowed start_period values
# 180s — cold-start race fix (2026-08-31): langfuse/litellm под amd64-эмуляцией / 7.8GB VPS
# стартуют дольше 60s-окна — compose-гейт depends_on: service_healthy выстреливал раньше готовности.
_ALLOWED_START_PERIODS: set[str] = {"5s", "15s", "30s", "60s", "180s"}


def _read_module_healthcheck(module: str) -> str:
    """Read a module's healthcheck.sh content."""
    path = _MODULES_DIR / module / "healthcheck.sh"
    if not path.is_file():
        return ""
    return path.read_text()


# ═══════════════════════════════════════════════════════════════════
# TEST 1: No raw docker inspect State.Running in module healthchecks
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_no_raw_docker_inspect_in_modules(caplog: pytest.LogCaptureFixture) -> None:
    """AC7: No module healthcheck.sh contains direct `docker inspect State.Running` call.

    DRIFT-H7 fix: modules-healthcheck.sh now uses invoke_module_interface → check_docker_health()
    instead of raw docker inspect. Module healthcheck.sh scripts should never call
    `docker inspect State.Running` directly — that's what check_docker_health() is for.
    Exceptions: comments (documentation), platform-secrets (systemd — no Docker).
    """
    # 🧪 TRAP[TEST] · Regression: no raw docker inspect State.Running in module healthcheck.sh
    # · Scenario: grep all Docker module healthcheck.sh files for State.Running in non-comment lines
    # · Last fail: Never (DevPlan 083 DRIFT-H6/H7 fix)
    # · Remove if: module healthcheck pattern changes fundamentally
    caplog.set_level(logging.DEBUG)

    found_raw_inspect = False
    for module in _DOCKER_MODULES:
        content = _read_module_healthcheck(module)
        if not content:
            continue

        # Check for State.Running in non-comment lines
        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # Skip comments
            if "State.Running" in stripped:
                found_raw_inspect = True
                logger.info(
                    "[IMP:9][gate:no-raw-inspect] State.Running found in %s/healthcheck.sh L%d",
                    module,
                    line_num,
                )

    logger.info(
        "[IMP:9][gate:no-raw-inspect] Raw docker inspect State.Running found: %s",
        found_raw_inspect,
    )

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

    assert not found_raw_inspect, (
        "[IMP:9][gate:no-raw-inspect] FAIL: Raw `docker inspect State.Running` found in module "
        "healthcheck.sh. Modules should use check_docker_health() from lib/healthcheck.sh."
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    logger.info("[IMP:9][gate:no-raw-inspect] PASS: No raw docker inspect State.Running in modules")


# ═══════════════════════════════════════════════════════════════════
# TEST 2: All modules use check_docker_health for liveness
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_all_modules_use_check_docker_health(caplog: pytest.LogCaptureFixture) -> None:
    """AC3: All Docker modules call check_docker_health for liveness mode.

    Every Docker module's healthcheck.sh must contain check_docker_health for its
    default (liveness) mode.     Excludes platform-secrets (systemd module).
    """
    # 🧪 TRAP[TEST] · Regression: all Docker modules must call check_docker_health for liveness
    # · Scenario: grep all Docker module healthcheck.sh for check_docker_health
    # · Last fail: Never (DevPlan 083 AC3)
    # · Remove if: module healthcheck contract changes
    caplog.set_level(logging.DEBUG)

    modules_missing: list[str] = []

    for module in _DOCKER_MODULES:
        content = _read_module_healthcheck(module)
        if not content:
            logger.info("[IMP:8][gate:uses-hc] %s: no healthcheck.sh (skipping)", module)
            continue

        if "check_docker_health" not in content:
            modules_missing.append(module)
            logger.info("[IMP:9][gate:uses-hc] %s: MISSING check_docker_health", module)
        else:
            logger.info("[IMP:8][gate:uses-hc] %s: OK", module)

    logger.info(
        "[IMP:9][gate:uses-hc] Modules missing check_docker_health: %s",
        modules_missing,
    )

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

    assert not modules_missing, (
        f"[IMP:9][gate:uses-hc] FAIL: {len(modules_missing)} modules missing check_docker_health: "
        f"{', '.join(modules_missing)}"
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    logger.info("[IMP:9][gate:uses-hc] PASS: All modules use check_docker_health")


# ═══════════════════════════════════════════════════════════════════
# TEST 3: modules-healthcheck.sh calls check_docker_health, not raw docker inspect
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_modules_healthcheck_uses_lib(caplog: pytest.LogCaptureFixture) -> None:
    """AC4: modules-healthcheck (Python, DevPlan 118 E4) uses shared/module_interface.invoke →
    check_docker_health(), not raw docker inspect for liveness checks.

    DRIFT-H7 fix: The orchestrator delegates to module healthcheck.sh via
    shared/module_interface.invoke instead of performing raw docker inspect calls.
    DevPlan 118 E4: логика перенесена из modules-healthcheck.sh в modules_healthcheck.py.
    DevPlan 173 W1.4: middle-hop modules-healthcheck.sh удалён — бизнес-логика ТОЛЬКО в .py.
    """
    # 🧪 TRAP[TEST] · Regression: modules-healthcheck uses invoke_module_interface, not raw docker inspect
    # · Scenario: check modules_healthcheck.py for invoke_module_interface; verify no Health.Status in code
    # · Last fail: Never (DevPlan 083 DRIFT-H7 fix; 118 E4 Python-перенос)
    # · Remove if: modules_healthcheck.py is replaced
    caplog.set_level(logging.DEBUG)

    # E4: Python-модуль — каноническое место бизнес-логики
    py_path = _HEALTHCHECK_ORCHESTRATOR.parent / "modules_healthcheck.py"
    assert py_path.is_file(), f"modules_healthcheck.py not found: {py_path}"
    content = py_path.read_text()

    # Check 1: calls invoke_module_interface for default docker mode (not raw docker inspect)
    has_invoke_module = "invoke_module_interface" in content
    logger.info("[IMP:9][gate:orchestrator] invoke_module_interface present: %s", has_invoke_module)

    # Check 2: NO raw docker inspect for Health.Status in non-comment code (was DRIFT-H7)
    code_lines = [line for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    code_text = "\n".join(code_lines)
    has_raw_docker_inspect = "Health.Status" in code_text
    logger.info("[IMP:9][gate:orchestrator] Raw docker inspect Health.Status in code: %s", has_raw_docker_inspect)

    # Check 3: Still has docker inspect for restart detection (that's OK — different concern)
    has_restart_inspect = "State.Restarting" in content or "RestartCount" in content
    logger.info("[IMP:9][gate:orchestrator] Restart loop inspect: %s", has_restart_inspect)

    # Check 4 (R5 negative): shell facade удалён (DevPlan 173 W1.4) — бизнес-логика
    # живёт ТОЛЬКО в modules_healthcheck.py; двух-хоповый фасад
    # healthcheck.sh → modules-healthcheck.sh схлопнут.
    sh_path = _HEALTHCHECK_ORCHESTRATOR
    sh_exists = sh_path.exists()
    logger.info("[IMP:9][gate:orchestrator] Shell facade exists (should be False after 173 W1.4): %s", sh_exists)

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

    assert has_invoke_module, (
        "[IMP:9][gate:orchestrator] FAIL: invoke_module_interface not found in modules_healthcheck.py"
    )
    assert not has_raw_docker_inspect, (
        "[IMP:9][gate:orchestrator] FAIL: Raw docker inspect Health.Status found — DRIFT-H7 not fixed. "
        "modules_healthcheck.py should use invoke_module_interface → check_docker_health()."
    )
    assert not sh_exists, (
        "[IMP:9][gate:orchestrator] FAIL: modules-healthcheck.sh must be deleted (173 W1.4) — "
        "business logic lives only in modules_healthcheck.py"
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    logger.info("[IMP:9][gate:orchestrator] PASS: modules_healthcheck.py uses invoke_module_interface")


# ═══════════════════════════════════════════════════════════════════
# TEST 4: start_period values standardized
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_start_period_standardized(caplog: pytest.LogCaptureFixture) -> None:
    """AC5: All compose HEALTHCHECK start_period values are in {5s, 15s, 30s, 60s, 180s}.

    DevPlan 083 §7 defines 3 standardized tiers (15s default, 30s DB, 60s litellm) + 5s (nginx).
    180s — задокументированное исключение (2026-08-31): langfuse/litellm на холодном старте
    (amd64-эмуляция / 7.8GB VPS) прогреваются дольше 60s — гейт зависимостей
    depends_on: condition: service_healthy выстреливал раньше готовности (см. TRAP[DECISION]
    в docker-compose.base.yml обоих модулей). Любое значение вне набора — дрейф.
    """
    # 🧪 TRAP[TEST] · Regression: all start_period values must be in {5s, 15s, 30s, 60s}
    # · Scenario: grep start_period in all docker-compose.base.yml, check values against allowed set
    # · Last fail: Never (DevPlan 083 AC5)
    # · Remove if: start_period standardization changes
    caplog.set_level(logging.DEBUG)

    violations: list[str] = []

    # Find all docker-compose.base.yml files
    for compose_file in sorted(_MODULES_DIR.glob("*/docker-compose.base.yml")):
        content = compose_file.read_text()
        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if "start_period:" not in stripped:
                continue

            # Extract the value
            value = stripped.split("start_period:")[-1].strip().rstrip("# ").split()[0]
            if value not in _ALLOWED_START_PERIODS:
                violations.append(f"{compose_file.relative_to(repo_root())}:{line_num} → {value}")

    logger.info(
        "[IMP:9][gate:start-period] start_period violations: %s",
        violations,
    )

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

    assert not violations, (
        f"[IMP:9][gate:start-period] FAIL: {len(violations)} start_period violations: "
        f"{'; '.join(violations)}. Allowed: {_ALLOWED_START_PERIODS}"
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    logger.info("[IMP:9][gate:start-period] PASS: All start_period values standardized")


# ═══════════════════════════════════════════════════════════════════
# TEST 5: exec_check/check_http used in formerly copy-paste modules
# ═══════════════════════════════════════════════════════════════════


def _has_raw_docker_exec(content: str) -> bool:
    """True если вне комментариев остался raw `docker exec` (C901-extraction)."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "# \u26a0\ufe0f", "# \U0001f9d0")):
            continue
        if "docker exec" in stripped:
            return True
    return False


@pytest.mark.gate
def test_exec_check_used_in_docker_exec_modules(caplog: pytest.LogCaptureFixture) -> None:
    """AC2: clickhouse/redis/nginx/backup-cron deep mode uses exec_check() or check_http().

    DRIFT-H4 fix: these modules had inline `docker exec` copy-paste patterns in their
    deep mode. They should now use exec_check() or check_http() from lib/healthcheck.sh.
    """
    # 🧪 TRAP[TEST] · Regression: clickhouse/redis/nginx/backup-cron use exec_check/check_http in deep
    # · Scenario: grep healthcheck.sh for expected function; check no raw docker exec in code
    # · Last fail: Never (DevPlan 083 DRIFT-H4 fix)
    # · Remove if: module healthcheck deep contract changes
    caplog.set_level(logging.DEBUG)

    violations: dict[str, str] = {}
    for module, expected_func in _EXEC_CHECK_MODULES.items():
        content = _read_module_healthcheck(module)
        if not content:
            violations[module] = "healthcheck.sh not found"
            continue

        # Check deep mode for the expected function
        has_expected_func = expected_func in content
        # Check NO raw docker exec in deep mode (except for context-specific docker exec in comments)
        has_raw_docker_exec = _has_raw_docker_exec(content)

        if not has_expected_func:
            violations[module] = f"missing {expected_func}()"
        elif has_raw_docker_exec:
            violations[module] = f"still has raw docker exec (should use {expected_func}())"

        logger.info(
            "[IMP:8][gate:exec-check] %s: expected=%s, present=%s, raw-docker-exec=%s",
            module,
            expected_func,
            has_expected_func,
            has_raw_docker_exec,
        )

    logger.info("[IMP:9][gate:exec-check] Deep check violations: %s", violations)

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

    assert not violations, (
        f"[IMP:9][gate:exec-check] FAIL: {len(violations)} modules with violations: "
        f"{'; '.join(f'{k}: {v}' for k, v in violations.items())}"
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    logger.info("[IMP:9][gate:exec-check] PASS: All modules use exec_check/check_http")
