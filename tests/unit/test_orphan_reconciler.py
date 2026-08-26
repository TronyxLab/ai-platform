"""
# GREP_SUMMARY: test_orphan_reconciler, orphan, container, reconcile, batch, mock-docker, subprocess
# STRUCTURE: ▶ tmp_path + mock subprocess.run → ◇ batch_orphan_reconciliation 4× (empty/no-orphans/with-orphans/docker-unavailable) → ⎋ assert orphans list + LDD trajectory [IMP:7-10]
# region MODULE_CONTRACT
## @purpose  Unit tests for orphan_reconciler.batch_orphan_reconciliation — orphan container
##           detection logic extracted from deploy-modules.sh batch_orphan_reconciliation()
## @scope    Tests the full orphan detection pipeline using mocked subprocess.run for all docker
##           commands. Does NOT require a real docker daemon.
## @invariants
##   - All tests mock subprocess.run to avoid real docker calls
##   - Empty module_entries → empty list (edge case: no modules to check)
##   - All containers matching their expected project label → empty list (no orphans)
##   - Containers with mismatched or missing project label → reported as orphans
##   - docker binary not found → graceful degradation (empty list, WARN logged)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory decorator
## @rationale Direct function testing with mock subprocess.run is idiomatic for pure logic
##   extraction. Avoids requiring a real docker daemon in CI.
## @changes
##   2026-07-22 · Created (W4-E1 extraction from deploy-modules.sh)
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
from orphan_reconciler import batch_orphan_reconciliation


# region FUNC_mock_subprocess_run
## @purpose  Factory for mocked subprocess.run with configurable docker responses
## @io       ⇥ ps_containers: set[str], compose_responses: dict[str, dict], inspect_responses: dict[str, str]
##           → ⎋ callable side_effect function for unittest.mock.patch
## @complexity 2 — dispatches on command arguments to return appropriate CompletedProcess
## @invariants
##   - Dispatches on "docker ps -a", "docker compose -f ... config --format json", "docker inspect"
##   - Unknown commands return CompletedProcess(returncode=0, stdout="")
##   - Each docker call type returns configured data from the factory parameters
##   - Preserves subprocess.CompletedProcess interface (args, returncode, stdout, stderr)
def _make_mock_run(
    ps_containers: set | None = None,
    compose_responses: dict[str, dict] | None = None,
    inspect_responses: dict[str, str] | None = None,
) -> callable:
    """Create a side_effect function for subprocess.run.

    Args:
        ps_containers: Set of container names returned by 'docker ps -a --format {{.Names}}'.
            Default: empty set.
        compose_responses: Dict mapping compose file path → docker compose config JSON
            (as a parsed dict). Default: empty dict.
        inspect_responses: Dict mapping container name → project label string.
            Default: empty dict.

    Returns:
        A callable that accepts (cmd, *args, **kwargs) and returns subprocess.CompletedProcess
        based on the command arguments.
    """
    if ps_containers is None:
        ps_containers = set()
    if compose_responses is None:
        compose_responses = {}
    if inspect_responses is None:
        inspect_responses = {}

    def _mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

        # docker ps -a --format {{.Names}}
        if "ps" in cmd_str and "-a" in cmd_str:
            stdout = "\n".join(sorted(ps_containers))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

        # docker compose -f <path> ... config --format json
        if "compose" in cmd_str and "config" in cmd_str:
            # Extract compose file path from the command
            compose_path = ""
            for i, c in enumerate(cmd):
                if c in {"-f", "--file"} and i + 1 < len(cmd):
                    compose_path = cmd[i + 1]
                    break

            config_data = compose_responses.get(compose_path, {"services": {}})
            stdout = json.dumps(config_data)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

        # docker inspect --format '{{index .Config.Labels ...}}' <container_name>
        if "inspect" in cmd_str:
            # Last argument is the container name
            container_name = cmd[-1] if cmd else ""
            label = inspect_responses.get(container_name, "")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=label, stderr="")

        # Fallback: unknown command
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _mock_run


# endregion FUNC_mock_subprocess_run


# ══════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════


# region FUNC_modules_dir
## @purpose  Create a temporary modules directory with compose files for test modules
## @io       tmp_path → Path (modules directory with postgres/ and redis/ compose files)
## @complexity 2 — creates two module subdirectories with compose content, writes compose.yaml files
@pytest.fixture
def modules_dir(tmp_path) -> Path:
    """Create a tmp_path-based modules directory with compose files.

    Creates:
      modules/postgres/compose.yaml
      modules/redis/docker-compose.yaml
    """
    mod_dir = tmp_path / "modules"

    # postgres — uses compose.yaml
    pg_dir = mod_dir / "postgres"
    pg_dir.mkdir(parents=True)
    (pg_dir / "compose.yaml").write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")

    # redis — uses docker-compose.yaml
    redis_dir = mod_dir / "redis"
    redis_dir.mkdir(parents=True)
    (redis_dir / "docker-compose.yaml").write_text("services:\n  redis:\n    image: redis:7\n", encoding="utf-8")

    # nginx — no compose file (test module that should be silently skipped)
    nginx_dir = mod_dir / "nginx"
    nginx_dir.mkdir(parents=True)

    return mod_dir


# endregion FUNC_modules_dir


# ══════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════


# region FUNC_test_empty_module_entries
## @purpose  Empty list of module entries returns empty orphan list immediately
## @io       batch_orphan_reconciliation([]) → assert empty list + IMP:9
## @complexity 1 — edge case: early return before any subprocess calls
# 🧪 TRAP[TEST] · Regression · Scenario: empty module entries returns [] immediately · Last fail: N/A · Remove if: early-return guard removed
# GUARD-PRESERVE (168): единственное покрытие ветки early-return (пустой список модулей →
# 0 docker-вызовов, без подпроцессов); edge-case контракта batch_orphan_reconciliation
@ldd_trajectory
def test_empty_module_entries(caplog) -> None:
    """Empty module entries should return empty list without calling docker."""
    orphans = batch_orphan_reconciliation([], "/some/modules/dir")
    logger.info("[IMP:9][unit][orphan] Empty entries → orphans=%s", orphans)
    assert orphans == [], f"Expected empty list, got {orphans}"
    logger.info("[IMP:9][unit][orphan] Empty entries returns [] ✓")


# endregion FUNC_test_empty_module_entries


# region FUNC_test_no_orphans
## @purpose  All containers match their expected project labels — no orphans detected
## @io       tmp_path modules + mock docker → batch_orphan_reconciliation → assert empty list
## @complexity 3 — full pipeline: compose discovery → docker ps → service config → inspect
# 🧪 TRAP[TEST] · Regression · Scenario: all containers match project labels returns empty list · Last fail: N/A · Remove if: orphan detection algorithm changes
@ldd_trajectory
def test_no_orphans(modules_dir: Path, caplog) -> None:
    """When all container project labels match their module, returns empty list.

    Setup:
    - modules/postgres/ has compose.yaml with one service (container_name="postgres-ct")
    - modules/redis/ has docker-compose.yaml with one service (container_name="redis-ct")
    - docker ps -a returns both containers
    - docker inspect returns matching project labels ("postgres", "redis")
    """
    mock_run = _make_mock_run(
        ps_containers={"postgres-ct", "redis-ct"},
        compose_responses={
            str(modules_dir / "postgres" / "compose.yaml"): {
                "services": {
                    "postgres": {
                        "container_name": "postgres-ct",
                        "name": "postgres",
                    }
                }
            },
            str(modules_dir / "redis" / "docker-compose.yaml"): {
                "services": {
                    "redis": {
                        "container_name": "redis-ct",
                        "name": "redis",
                    }
                }
            },
        },
        inspect_responses={
            "postgres-ct": "postgres",
            "redis-ct": "redis",
        },
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["postgres", "redis"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Matching containers → orphans=%s", orphans)
    assert orphans == [], f"Expected no orphans, got {len(orphans)}: {orphans}"
    logger.info("[IMP:9][unit][orphan] No orphans detected ✓")


# endregion FUNC_test_no_orphans


# region FUNC_test_with_orphans
## @purpose  Container with mismatched project label is detected as orphan
## @io       tmp_path modules + mock docker → batch_orphan_reconciliation → assert 1 orphan
## @complexity 3 — full pipeline with mismatched label detection
# 🧪 TRAP[TEST] · Regression · Scenario: container with wrong project label is reported as orphan · Last fail: N/A · Remove if: orphan detection algorithm changes
@ldd_trajectory
def test_with_orphans(modules_dir: Path, caplog) -> None:
    """Container whose project label differs from its module name is reported as orphan.

    Setup:
    - modules/postgres/ has compose.yaml with one service (container_name="pg-orphan")
    - docker ps -a returns "pg-orphan"
    - docker inspect returns project="redis" (different from "postgres")
    - Expected: "pg-orphan" is detected as orphan with project="redis"
    """
    mock_run = _make_mock_run(
        ps_containers={"pg-orphan"},
        compose_responses={
            str(modules_dir / "postgres" / "compose.yaml"): {
                "services": {
                    "postgres": {
                        "container_name": "pg-orphan",
                        "name": "postgres",
                    }
                }
            },
        },
        inspect_responses={
            "pg-orphan": "redis",  # Mismatch: container belongs to "redis" project, not "postgres"
        },
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["postgres"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Mismatched labels → orphans=%s", orphans)
    assert len(orphans) == 1, f"Expected 1 orphan, got {len(orphans)}: {orphans}"
    assert orphans[0]["container_name"] == "pg-orphan", f"Expected container_name=pg-orphan, got {orphans[0]}"
    assert orphans[0]["project"] == "redis", f"Expected project=redis, got {orphans[0]}"
    logger.info("[IMP:9][unit][orphan] Orphan detected correctly ✓")


# endregion FUNC_test_with_orphans


# region FUNC_test_empty_project_label_is_orphan
## @purpose  Container with empty project label (label missing) is treated as orphan
## @io       tmp_path modules + mock docker → batch_orphan_reconciliation → assert orphan
## @complexity 3 — edge case: docker inspect returns empty label
# 🧪 TRAP[TEST] · Regression · Scenario: container with missing project label is orphan · Last fail: N/A · Remove if: orphan detection algorithm changes
@ldd_trajectory
def test_empty_project_label_is_orphan(modules_dir: Path, caplog) -> None:
    """Container with empty/missing project label is detected as orphan.

    docker inspect returns empty string for a container — the container
    has no com.docker.compose.project label. This should be treated as
    orphan (it doesn't belong to any known compose project).
    """
    mock_run = _make_mock_run(
        ps_containers={"pg-no-label"},
        compose_responses={
            str(modules_dir / "postgres" / "compose.yaml"): {
                "services": {
                    "postgres": {
                        "container_name": "pg-no-label",
                        "name": "postgres",
                    }
                }
            },
        },
        inspect_responses={
            "pg-no-label": "",  # No project label set on this container
        },
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["postgres"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Empty label → orphans=%s", orphans)
    assert len(orphans) == 1, f"Expected 1 orphan for empty label, got {len(orphans)}: {orphans}"
    assert orphans[0]["container_name"] == "pg-no-label"
    assert not orphans[0]["project"], f"Expected empty project, got '{orphans[0]['project']}'"
    logger.info("[IMP:9][unit][orphan] Empty project label treated as orphan ✓")


# endregion FUNC_test_empty_project_label_is_orphan


# region FUNC_test_docker_unavailable
## @purpose  When docker binary is not found, function returns empty list gracefully
## @io       mock FileNotFoundError → assert empty list + WARN log
## @complexity 2 — edge case: graceful degradation on missing docker binary
# 🧪 TRAP[TEST] · Regression · Scenario: docker binary not found returns empty list gracefully · Last fail: N/A · Remove if: error handling changes
@ldd_trajectory
def test_docker_unavailable(modules_dir: Path, caplog) -> None:
    """When docker binary is not found, returns empty list with WARN log.

    Simulates FileNotFoundError from subprocess.run (docker not installed
    or not in PATH). The function should degrade gracefully — return empty
    list instead of crashing.
    """

    def _mock_file_not_found(cmd, *args, **kwargs):
        msg = "docker: command not found"
        raise FileNotFoundError(msg)

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_file_not_found):
        orphans = batch_orphan_reconciliation(["postgres", "redis"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Docker unavailable → orphans=%s", orphans)
    assert orphans == [], f"Expected empty list when docker unavailable, got {orphans}"
    logger.info("[IMP:9][unit][orphan] Docker unavailable handled gracefully ✓")


# endregion FUNC_test_docker_unavailable


# region FUNC_test_docker_compose_config_fails
## @purpose  When docker compose config fails for one module, other modules are still checked
## @io       tmp_path modules + mock subprocess → assert orphan from redis found, postgres skipped
## @complexity 3 — partial failure: one module fails, other succeeds
# 🧪 TRAP[TEST] · Regression · Scenario: docker compose config fails for one module, others still checked · Last fail: N/A · Remove if: error-handling-per-module changes
@ldd_trajectory
def test_docker_compose_config_fails(modules_dir: Path, caplog) -> None:
    """When docker compose config fails for one module, the other modules are still checked.

    Setup:
    - modules/postgres/ compose config fails (returncode=1)
    - modules/redis/ compose config succeeds, has container "redis-ct"
    - "redis-ct" has mismatched project label "postgres" → should be detected as orphan
    """
    compose_postgres = str(modules_dir / "postgres" / "compose.yaml")
    compose_redis = str(modules_dir / "redis" / "docker-compose.yaml")

    def _mock_partial_fail(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

        # docker ps -a succeeds
        if "ps" in cmd_str and "-a" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="redis-ct\n", stderr="")

        # docker compose config for postgres fails
        if compose_postgres in cmd_str and "config" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ERROR: postgres compose config failed",
            )

        # docker compose config for redis succeeds
        if compose_redis in cmd_str and "config" in cmd_str:
            config_data = {
                "services": {
                    "redis": {
                        "container_name": "redis-ct",
                        "name": "redis",
                    }
                }
            }
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(config_data), stderr="")

        # docker inspect for redis-ct returns mismatched project
        if "inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres", stderr="")

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_partial_fail):
        orphans = batch_orphan_reconciliation(["postgres", "redis"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Partial config failure → orphans=%s", orphans)
    # postgres should be skipped (config failed)
    # redis has mismatched project label → should be detected
    assert len(orphans) == 1, f"Expected 1 orphan (redis), got {len(orphans)}: {orphans}"
    assert orphans[0]["container_name"] == "redis-ct"
    logger.info("[IMP:9][unit][orphan] One module fails, other module's orphan still detected ✓")


# endregion FUNC_test_docker_compose_config_fails


# region FUNC_test_module_without_compose_file
## @purpose  Module entry without any compose file is silently skipped (no error)
## @io       tmp_path modules (nginx has no compose file) + mock docker → assert empty list
## @complexity 2 — edge case: module directory exists but has no compose file
# 🧪 TRAP[TEST] · Regression · Scenario: module without compose file is skipped silently · Last fail: N/A · Remove if: compose-discovery logic changes
@ldd_trajectory
def test_module_without_compose_file(modules_dir: Path, caplog) -> None:
    """Module entry without any compose file is skipped without error.

    The modules_dir fixture includes an 'nginx' directory without any
    compose file. The function should handle this gracefully — skip
    the module and continue.
    """
    mock_run = _make_mock_run(
        ps_containers={"nginx-proxy"},
        compose_responses={},  # nginx has no compose file — no entry
        inspect_responses={},
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["nginx"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Module without compose → orphans=%s", orphans)
    assert orphans == [], f"Expected no orphans for module without compose, got {orphans}"
    logger.info("[IMP:9][unit][orphan] Module without compose file skipped ✓")


# endregion FUNC_test_module_without_compose_file


# region FUNC_test_root_compose_platform_project_not_orphan
## @purpose  B18 regression (141 r2): контейнеры root-compose проекта (config "name": "platform")
##           НЕ считаются орфанами — expected project = deploy project, не module_name
## @io       tmp_path modules + mock docker → batch_orphan_reconciliation → assert empty list
## @complexity 3 — root-compose scenario: config name=platform, containers labeled platform
# 🧪 TRAP[TEST] · NEGATIVE (R5) · batch_orphan_reconciliation — B18 (141 r2)
# · Last fail: node-update: контейнеры модулей (project=platform, root compose) удалялись
# ·   как orphans (expected=module_name) → все 13 модулей исчезали после деплоя.
# · Remove if: orphan-детекция вернётся к сравнению с module_name.
@ldd_trajectory
def test_root_compose_platform_project_not_orphan(modules_dir: Path, caplog) -> None:
    """Containers of the root compose project (config name="platform") are NOT orphans.

    Setup:
    - modules/postgres/ compose config returns name="platform" (root compose deployed)
    - docker ps -a returns "postgres-ct"
    - docker inspect returns project="platform" (root compose project label)
    - Expected: NO orphans — containers belong to the deploy project
    """
    mock_run = _make_mock_run(
        ps_containers={"postgres-ct"},
        compose_responses={
            str(modules_dir / "postgres" / "compose.yaml"): {
                "name": "platform",
                "services": {
                    "postgres": {
                        "container_name": "postgres-ct",
                        "name": "postgres",
                    }
                },
            },
        },
        inspect_responses={
            "postgres-ct": "platform",  # Root compose project label — must NOT be orphan
        },
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["postgres"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Root-compose platform containers → orphans=%s", orphans)
    assert orphans == [], f"B18 regression: root-compose containers must not be orphans, got {orphans}"
    logger.info("[IMP:9][unit][orphan] Root-compose project containers preserved ✓")


# endregion FUNC_test_root_compose_platform_project_not_orphan


# region FUNC_test_foreign_project_container_is_orphan
## @purpose  B18a-совместимость: контейнер-тёзка от ЧУЖОГО проекта (не deploy project)
##           удаляется как орфан — pre-up cleanup исключает name-conflict при повторном деплое
## @io       tmp_path modules + mock docker → batch_orphan_reconciliation → assert 1 orphan
## @complexity 3 — foreign project label vs deploy project name
# 🧪 TRAP[TEST] · NEGATIVE (R5) · batch_orphan_reconciliation — B18a (141 r2)
# · Last fail: "Container redis-exporter Creating" — конфликт имени с контейнером от
# ·   чужого проекта (миксовый деплой модульный/root) при повторном deploy service-exporters.
# · Remove if: name-conflict cleanup переедет из orphan_reconciler в другой механизм.
@ldd_trajectory
def test_foreign_project_container_is_orphan(modules_dir: Path, caplog) -> None:
    """Container with the same name from a FOREIGN project is detected as orphan.

    Setup:
    - modules/service-exporters/ compose config returns name="platform" (root compose deployed)
    - docker ps -a returns "redis-exporter" (existing container from module project)
    - docker inspect returns project="service-exporters" (module project — foreign now)
    - Expected: "redis-exporter" is an orphan → removed before up (no name conflict)
    """
    im_dir = modules_dir / "service-exporters"
    im_dir.mkdir(parents=True)
    (im_dir / "docker-compose.base.yml").write_text(
        "services:\n  redis-exporter:\n    image: redis_exporter:1\n", encoding="utf-8"
    )

    mock_run = _make_mock_run(
        ps_containers={"redis-exporter"},
        compose_responses={
            str(im_dir / "docker-compose.base.yml"): {
                "name": "platform",
                "services": {
                    "redis-exporter": {
                        "container_name": "redis-exporter",
                        "name": "redis-exporter",
                    }
                },
            },
        },
        inspect_responses={
            "redis-exporter": "service-exporters",  # module project — foreign vs "platform"
        },
    )

    with patch("orphan_reconciler.subprocess.run", side_effect=mock_run):
        orphans = batch_orphan_reconciliation(["service-exporters"], str(modules_dir))

    logger.info("[IMP:9][unit][orphan] Foreign project container → orphans=%s", orphans)
    assert len(orphans) == 1, f"Expected 1 orphan (foreign project), got {len(orphans)}: {orphans}"
    assert orphans[0]["container_name"] == "redis-exporter"
    assert orphans[0]["project"] == "service-exporters"
    logger.info("[IMP:9][unit][orphan] Foreign project container detected as orphan ✓")


# endregion FUNC_test_foreign_project_container_is_orphan


# ═══════════════════════════════════════════════════════════════════
# plan 012 T14 (F-027): disabled-module containers
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T14 F-027 · enabled:false → контейнер снят, volume цел
# · Scenario: модуль redis выключен (нет в enabled_names), его контейнер жив → detect_disabled
#   возвращает orphan; remove_orphans → docker rm (БЕЗ -v — volume сохранён)
# · Last fail: F-027 — контейнер отключённого модуля (правильный project-label) не детектился
#   batch_orphan_reconciliation (тот ищет только среди ENABLED) → висел вечно
# · Remove if: disabled-детекция перенесена в другой слой
@ldd_trajectory
def test_disabled_module_container_detected_and_removed_volume_kept(modules_dir: Path, caplog) -> None:
    """F-027: redis enabled:false → контейнер в orphans; remove без -v (volume цел)."""
    mock_run = _make_mock_run(
        ps_containers={"redis-ct", "postgres-ct"},
        compose_responses={
            str(modules_dir / "redis" / "docker-compose.yaml"): {
                "services": {
                    "redis": {
                        "container_name": "redis-ct",
                        "name": "redis",
                    }
                }
            },
            str(modules_dir / "postgres" / "compose.yaml"): {
                "services": {
                    "postgres": {
                        "container_name": "postgres-ct",
                        "name": "postgres",
                    }
                }
            },
        },
        inspect_responses={"redis-ct": "redis", "postgres-ct": "postgres"},
    )
    removed_calls: list[list[str]] = []

    def _rm_capture(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["docker", "rm"]:
            removed_calls.append(cmd)
        return mock_run(cmd, *args, **kwargs)

    from orphan_reconciler import detect_disabled_module_containers, remove_orphans

    with patch("orphan_reconciler.subprocess.run", side_effect=_rm_capture):
        disabled = detect_disabled_module_containers(["postgres"], str(modules_dir))
        assert any(o["container_name"] == "redis-ct" for o in disabled), f"redis-ct должен быть orphan: {disabled}"
        removed = remove_orphans(disabled)

    assert removed == 1
    rm_args = [c for c in removed_calls if c and "redis-ct" in c]
    assert rm_args, f"docker rm redis-ct не вызван: {removed_calls}"
    assert not any(flag in rm_args[0] for flag in ("-v", "--volumes")), (
        f"F-027 FAIL: volume удалён (docker rm -v): {rm_args[0]}"
    )
    logger.info("[IMP:9][test][F-027] disabled redis container removed, volume kept PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T14 F-027 · все модули enabled → 0 disabled-orphans
# · Remove if: disabled-детекция перенесена в другой слой
@ldd_trajectory
def test_no_disabled_modules_no_orphans(modules_dir: Path, caplog) -> None:
    """F-027: enabled включает ВСЕ module-dir → disabled-orphans пусты."""
    from orphan_reconciler import detect_disabled_module_containers

    with patch("orphan_reconciler.subprocess.run", side_effect=_make_mock_run(ps_containers={"postgres-ct"})):
        disabled = detect_disabled_module_containers(["postgres", "redis"], str(modules_dir))
    assert disabled == [], f"все enabled → пусто, got {disabled}"
    logger.info("[IMP:9][test][F-027] no disabled modules → no orphans PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T14 F-027 · dry-run: детекция без мутаций
# · Remove if: dry-run семантика CLI изменится
def test_dry_run_detects_without_mutation(modules_dir: Path, caplog) -> None:
    """F-027: dry-run — detect возвращает orphans, docker rm НЕ вызывается."""
    from orphan_reconciler import detect_disabled_module_containers

    mock_run = _make_mock_run(
        ps_containers={"redis-ct"},
        compose_responses={
            str(modules_dir / "redis" / "docker-compose.yaml"): {
                "services": {"redis": {"container_name": "redis-ct", "name": "redis"}}
            }
        },
    )
    rm_calls: list = []

    def _capture(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["docker", "rm"]:
            rm_calls.append(cmd)
        return mock_run(cmd, *args, **kwargs)

    with patch("orphan_reconciler.subprocess.run", side_effect=_capture):
        disabled = detect_disabled_module_containers(["postgres"], str(modules_dir))
    assert len(disabled) == 1, f"dry-run: redis-ct детектится, got {disabled}"
    assert rm_calls == [], "dry-run: мутаций быть не должно"
    logger.info("[IMP:9][test][F-027] dry-run detect-only PASS")
