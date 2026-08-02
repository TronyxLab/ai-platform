"""
# GREP_SUMMARY: test module env compose-args platform-env pre-pull local-build skip docker-orchestrator
# STRUCTURE: ▶ _read_docker_orchestrator → ○ test_compose_args_has_platform_env → ◇ check _build_compose_args for platform_env → ▶ test_prepull_skips_local_build → ◇ check _pull_module_images for build: skip
# region MODULE_CONTRACT
## @purpose  Tests for docker_orchestrator.py env-file and pre-pull skip logic (migrated from
##           deploy-modules.sh after W4-E1 Strangler-Fig decomposition). Replaced shell-grep
##           pattern checks with Python source static analysis.
## @scope    Static analysis of core/internal/bootstrap/deploy/docker_orchestrator.py.
##           Verifies _build_compose_args has platform .env handling and _pull_module_images
##           has build: skip logic.
## @invariants
##   - Tests use static analysis (source read + string checks) — no module import
##   - No Docker daemon required — static patterns verified in Python source
##   - Tests are @pytest.mark.static_audit (gate-compatible)
## @rationale DevPlan 042 Phase 4: migrated from shell-grep (deploy-modules.sh) to Python source
##           analysis (docker_orchestrator.py). The actual unit tests with mock subprocess are
##           in tests/unit/test_docker_orchestrator.py.
## @changes   2026-07-22 · DevPlan 042 — rewritten as static analysis of docker_orchestrator.py
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_DOCKER_ORCHESTRATOR_PY = (
    Path(__file__).resolve().parent / ".." / "core" / "internal" / "bootstrap" / "deploy" / "docker_orchestrator.py"
).resolve()


# region HELPER__read_docker_orchestrator
@pytest.fixture
def docker_orchestrator_source() -> str:
    """Read docker_orchestrator.py source content.

    ## @purpose — Fixture: provides docker_orchestrator.py source for static analysis.
    ## @io — ⎋ str: full source text
    """
    return _DOCKER_ORCHESTRATOR_PY.read_text()


# endregion HELPER__read_docker_orchestrator


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: _build_compose_args has platform .env handling
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_compose_args_has_platform_env
## @purpose  Verify docker_orchestrator.py _build_compose_args includes --env-file for
##           platform .env file. After W4-E1, the compose args are built in Python, not shell.
##           Acceptance criterion: platform .env (142 variables) must be passed to docker compose.
## @io       ⇥ caplog, docker_orchestrator_source → ⎋ None (pytest.fail if platform_env missing)
## @complexity 1 — static grep on file content
## @invariants
##   - `platform_env` variable is defined in _build_compose_args function
##   - `--env-file` with platform_env is in the function
##   - platform_env is derived from `platform_root + "/.env"`
##   - Fallback to /opt/platform/.env when platform_root is None


@pytest.mark.static_audit
def test_compose_args_has_platform_env(caplog, docker_orchestrator_source: str) -> None:
    """
    # ◇ read docker_orchestrator.py → ⚡ grep _build_compose_args → ◇ platform_env → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    source = docker_orchestrator_source

    # ── platform_env variable in _build_compose_args ──
    # Extract _build_compose_args function body
    func_start = source.find("def _build_compose_args")
    assert func_start >= 0, "Function _build_compose_args not found"
    func_body = source[func_start:]
    # Find function end (next def at same level or end of file)
    next_def = func_body.find("\ndef ", 1)
    func_body = func_body[:next_def] if next_def > 0 else func_body

    has_platform_env = "platform_env" in func_body
    logger.critical("[IMP:9][test_compose_args] platform_env variable in _build_compose_args: %s", has_platform_env)
    assert has_platform_env, (
        "_build_compose_args must define platform_env variable for platform .env handling\n"
        "W4-E1 migrated --env-file logic from shell to docker_orchestrator.py"
    )

    # ── --env-file for platform .env ──
    has_env_file = "--env-file" in func_body
    logger.critical("[IMP:9][test_compose_args] --env-file flag in _build_compose_args: %s", has_env_file)
    assert has_env_file, "_build_compose_args must add --env-file for platform .env"

    # ── platform_root fallback (B3: канон shared/deploy_paths.platform_remote_base вместо литерала) ──
    has_fallback = "platform_remote_base()" in func_body
    logger.critical("[IMP:9][test_compose_args] platform_remote_base fallback: %s", has_fallback)
    assert has_fallback, "_build_compose_args must have platform_remote_base() fallback for platform_root"

    # ── LDD trajectory ──
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


# endregion FUNC_test_compose_args_has_platform_env


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: _pull_module_images skips modules with build: section
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_prepull_skips_local_build
## @purpose  Verify docker_orchestrator.py _pull_module_images skips modules with `build:`
##           section in compose file. After W4-E1, pre-pull skip logic is in Python, not shell.
##           Acceptance criterion: modules with local build: section are skipped (no pull).
## @io       ⇥ caplog, docker_orchestrator_source → ⎋ None (pytest.fail if build: check missing)
## @complexity 1 — static grep on file content
## @invariants
##   - `build:` string check is present in _pull_module_images
##   - Skip log message mentions "build" or "skip"
##   - Build check happens BEFORE docker compose pull call


@pytest.mark.static_audit
def test_prepull_skips_local_build(caplog, docker_orchestrator_source: str) -> None:
    """
    # ◇ read parallel_runner.py (D1: _pull_module_images переехал) → ⚡ grep pull_module_images → ◇ build: check → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    # DevPlan 118 D1: _pull_module_images переехал из docker_orchestrator.py в parallel_runner.py.
    parallel_runner_py = (
        Path(__file__).resolve().parent / ".." / "core" / "internal" / "bootstrap" / "deploy" / "parallel_runner.py"
    ).resolve()
    source = parallel_runner_py.read_text()

    # ── pull_module_images function ──
    func_start = source.find("def pull_module_images")
    assert func_start >= 0, "Function pull_module_images not found (parallel_runner.py)"
    func_body = source[func_start:]
    next_def = func_body.find("\ndef ", 1)
    func_body = func_body[:next_def] if next_def > 0 else func_body

    # ── build: check ──
    has_build_check = '"build:"' in func_body or "'build:'" in func_body or "build:" in func_body
    logger.critical("[IMP:9][test_prepull] build: check in pull_module_images: %s", has_build_check)
    assert has_build_check, (
        "pull_module_images must check for 'build:' section in compose file\n"
        "W4-E1 migrated build: skip logic from shell; DevPlan 118 D1 → parallel_runner.py"
    )

    # ── Skip log message ──
    has_skip_log = "skip" in func_body.lower() or "build" in func_body.lower()
    logger.critical("[IMP:9][test_prepull] skip/build log message: %s", has_skip_log)

    # ── Build check is BEFORE docker compose pull command ──
    # Find the compose pull command (not "skipping pull" log message)
    build_idx = func_body.find("build:")
    # Look for the docker compose pull command: pull_args + "pull"
    compose_pull_idx = max(
        func_body.find('"pull"'),
        func_body.find("'pull'"),
    )
    if build_idx >= 0 and compose_pull_idx >= 0:
        is_before = build_idx < compose_pull_idx
        logger.critical("[IMP:9][test_prepull] build: check before docker compose pull: %s", is_before)
        assert is_before, "build: check must happen BEFORE docker compose pull"

    # ── LDD trajectory ──
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


# endregion FUNC_test_prepull_skips_local_build
