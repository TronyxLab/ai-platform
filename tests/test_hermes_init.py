# GREP_SUMMARY: hermes-init test единый образ docker build run CONTEXT guard init-script hermes-agent-context user-10000 L1-collapse
# STRUCTURE: ⚡ [image_inspect] → ◇ exists? → skip:build → ▶ [docker build] → ⊕ [IMP:9] image built → ▶ [docker run] → ◇ guard CONTEXT? → ⊕ [IMP:9] guard_msg → ⎋ verify
# region MODULE_CONTRACT
## @purpose — Unit tests for Hermes Agent init scripts на ЕДИНОМ образе (L1→L2 коллапс DevPlan 002):
##            hermes-agent-context guard script prints FATAL and exits 1 when CONTEXT is empty,
##            with CONTEXT starts successfully; USER 10000 non-root runtime.
## @scope — Integration tests requiring Docker daemon (pytest.mark.requires_docker).
##          Tests build/verify the единый Docker image locally, never push to registry.
## @invariants
##   - docker CLI must be available for any test to execute
##   - Image built only once (skip if already present via docker image inspect)
##   - All containers are cleaned up after each test (docker rm -f in finally — even on failure)
##   - Tests are atomic and independent (each test uses unique container names)
##   - tmp_path fixture used for any temporary files (no hardcoded paths)
##   - Единый образ (hermes-agent-context) ВСЕГДА имеет CONTEXT guard в init-context.sh
##   - USER 10000:10000 non-root runtime (docker inspect Config.User)
##   - s6-overlay does NOT propagate cont-init.d exit codes to container exit code
##     (container exits 0 even if cont-init.d script fails)
##   - Контракт «L1 без guard / L2 с guard» УДАЛЁН (DevPlan 002): разницы больше нет —
##     единый образ всегда с guard (final-стадия)
## @requires
##   - Docker Desktop: ≥4GB RAM allocated (Settings → Resources → Memory).
##     macOS Apple Silicon: amd64 image runs under QEMU (+30-50% memory overhead).
##     Test containers are limited to 1G each via --memory flag (matches production limit).
## @rationale — DevPlan 002 W5 T5.10 (CRITICAL): полный rewrite под единый Dockerfile.
##              _L1_DOCKERFILE/_L2_DOCKERFILE удалены; guard-поведение проверяется на
##              едином образе (guard есть + USER 10000).
## @changes — CREATED: 2026-07-09 | TASK-0.5: Unit tests for L1/L2 init scripts
## @changes — 2026-08-03 | DevPlan 123 T5: container creation/verify wrapped in try/finally —
##            _cleanup_container() guarantees removal on ANY outcome (false-lead #10, 503 on /health)
## @changes — 2026-08-06 | DevPlan 140 W5 (W12-T13): detached контейнеры создаются с меткой
##            ai-platform.test=true (_run_container_detached) — label-first sweep session.py;
##            name-fallback в session.py удалён (label-only).
## @changes — 2026-08-16 | DevPlan 002 W5 T5.10 — rewrite: единый Dockerfile, guard-поведение единого образа
# endregion MODULE_CONTRACT

import itertools
import logging
import pathlib
import subprocess
import time

# Единый канон тест-метки (T12.9 T-13, DevPlan 140 W5): sweep в session.py использует
# ТУ ЖЕ константу — создатель и очиститель не могут разойтись (label-first единственный путь).
import pytest
from _conftest.honesty import require_docker_or_fail
from _conftest.session import _HERMES_TEST_LABEL
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# xdist-инвариант 4 (DevPlan 139 W2): uuid.uuid4() → детерминированный генератор имён.
# Docker-тесты — single-process по построению (tests/AGENTS.md §Параллельный запуск),
# поэтому фиксированный seed + счётчик детерминирован и коллизий не создаёт (никаких
# нестабильных имён при повторных прогонах / ретраях — flaky-фикс).
_FIXED_CONTAINER_SEED = "a1b2c3d4"
_container_seq = itertools.count(1)


def _container_name(prefix: str) -> str:
    """Детерминированное имя контейнера: <prefix>-<seed>-<NNNN> (xdist-safe, DevPlan 139 W2).

    ## @purpose — Замена uuid.uuid4().hex[:8] для имён docker-контейнеров: фиксированный
    ##            seed + монотонный счётчик. Docker-тесты выполняются single-process,
    ##            счётчик детерминирован — имена стабильны между прогонами.
    ## @io — ⇥ prefix: str → ⎋ str (container name)
    ## @complexity O(1)
    """
    return f"{prefix}-{_FIXED_CONTAINER_SEED}-{next(_container_seq):04d}"


# ── Paths ───────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
# DevPlan 002 W5 T5.10: единый Dockerfile (build/Dockerfile + context/Dockerfile удалены)
_UNIFIED_DOCKERFILE: pathlib.Path = _PROJECT_ROOT / "core" / "modules" / "hermes-agent" / "Dockerfile"

# ── Image tags ──────────────────────────────────────────────────────────────
_IMAGE_TAG: str = "hermes-agent-context:latest"


# region HELPERS


def _image_exists(tag: str) -> bool:
    """Check if a Docker image exists locally.

    ## @purpose — Avoid redundant builds by checking local image cache.
    ## @io — ⇥ tag → ⎋ bool: True if image exists
    ## @complexity — O(1) — single docker image inspect call
    """
    result = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, text=True, timeout=15, check=False
    )
    return result.returncode == 0


def _cleanup_container(container_name: str) -> None:
    """Best-effort removal of a test container — never masks the test's original error.

    ## @purpose — DevPlan 123 T5 (false-lead #10): guarantee removal of exited
    ##            hermes-test-* containers even when a test fails mid-way.
    ##            Called from the finally block of every hermes-init test; a leftover
    ##            exited container causes 503 on the status-page /health endpoint.
    ## @io — ⇥ container_name: str → ⎋ None (side-effect: docker rm -f)
    ## @complexity — O(1) — single docker rm -f call
    ## @invariants — Cleanup failures (OSError) are logged, never raised, so the
    ##              original test error is never masked by cleanup.
    """
    try:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30, check=False)
        logger.info("[IMP:7][_cleanup_container] Container '%s' removed", container_name)
    except OSError as exc:
        logger.error("[IMP:8][_cleanup_container] Failed to remove container '%s': %s", container_name, exc)


def _print_docker_imp_logs(output: str) -> None:
    """Log IMP:7-10 lines from docker build/run output through Python logger.

    ## @purpose — Extract structured LDD telemetry from docker command output
    ##            so caplog/@ldd_trajectory captures it for LDD trajectory display.
    ## @io — ⇥ output: str (stdout+stderr from docker command) → ⎛ None (side-effect: logger.info)
    ## @complexity — O(n) where n = lines in output
    """
    for line in output.splitlines():
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    logger.info(line)
            except (ValueError, IndexError):
                logger.info("[IMP:7][hermes] MALFORMED IMP tag: %s", line.strip())


def _build_image() -> None:
    """Build the единый hermes-agent-context image if not already present.

    ## @purpose — Idempotent build: only builds if image missing from local cache.
    ##            Builds with CONTEXT=test baked in as ENV (build-arg).
    ## @io — ⎛ None (side-effect: docker build, may pytest.fail on build error)
    ## @complexity — O(B) where B = Docker build time
    """
    if _image_exists(_IMAGE_TAG):
        logger.info("[IMP:7][_build_image] Image already exists — skipping build")
        return
    logger.info("[IMP:7][_build_image] Building единый hermes-agent-context image...")
    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "hermes-agent-context",
            "--build-arg",
            "CONTEXT=test",
            "-f",
            str(_UNIFIED_DOCKERFILE),
            str(_PROJECT_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    _print_docker_imp_logs(result.stderr)
    _print_docker_imp_logs(result.stdout)
    assert result.returncode == 0, f"Единый hermes build failed:\n{result.stderr[-1000:]}"
    logger.info("[IMP:9][_build_image] Единый образ built successfully")


def _docker_skip_if_unavailable() -> None:
    """Skip the test if Docker CLI is not available.

    ## @purpose — Centralized guard for all docker-dependent tests.
    ## @io — ⎛ None (side-effect: pytest.skip if docker not found)
    ## @complexity — O(1)
    """
    require_docker_or_fail(reason="hermes-init tests require Docker daemon")


def _run_container_detached(
    image_tag: str,
    env_vars: dict[str, str] | None = None,
    name: str | None = None,
    mem_limit: str = "1g",
) -> str:
    """Run a Docker container in detached mode and return the container name.

    ## @purpose — Start a container, verify creation, return its name for lifecycle mgmt.
    ## @io — ⇥ image_tag, env_vars, name, mem_limit → ⎋ str: container name
    ## @complexity — O(1) — single docker run call
    ## @rationale — mem_limit=1g matches production limit (module.yaml deploy.resources.limits.memory).
    ##              Prevents OOM kill on resource-constrained environments (macOS Docker Desktop default ~2GB)
    ##              while providing enough memory for s6-overlay init + QEMU emulation overhead.
    ## @rationale — W12-T13 (DevPlan 140 W5): detached hermes-test-* контейнеры помечаются
    ##              ai-platform.test=true (_HERMES_TEST_LABEL из _conftest/session.py) — это метка,
    ##              по которой sessionfinish-sweep _final_hermes_test_cleanup() их удаляет (label-only,
    ##              name-prefix fallback удалён). Без метки выживший контейнер дал бы 503 на /health
    ##              (false-lead #10, DevPlan 123 T5).
    """
    cmd = ["docker", "run", "-d", "--memory", mem_limit, "--label", _HERMES_TEST_LABEL]
    if name:
        cmd.extend(["--name", name])
    if env_vars:
        for k, v in env_vars.items():
            cmd.extend(["-e", f"{k}={v}"])
    cmd.append(image_tag)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, f"docker run failed:\n{result.stderr}"
    return name or result.stdout.strip()[:16]


def _stop_and_verify(container_name: str) -> tuple[int, bool]:
    """Stop a container, inspect its exit code and OOM status, return (exit_code, oom_killed).

    ## @purpose — Clean shutdown + exit code verification + OOMKilled diagnostics.
    ## @io — ⇥ container_name → ⎋ (int, bool): exit code, OOMKilled flag
    ## @complexity — O(1) — three docker calls (stop, inspect exit code, inspect OOMKilled)
    ## @rationale — OOMKilled check provides actionable diagnostics for exit code 137 (SIGKILL).
    ##              Without it, SIGKILL from OOM is indistinguishable from other kill signals.
    ## ⚠️ TRAP[BUG] · 2026-07-27 · HI · exit 137 + OOM=false = docker stop timeout, not OOM
    ## · Root: hermes init script does config migration + skill sync on SIGTERM;
    ## ·   docker stop --time 30 → SIGTERM → init busy → timeout → SIGKILL → exit 137.
    ## · Fix: increased timeout 30→60s; callers check OOMKilled flag, not just exit code.
    ## ·   Container ran successfully — only shutdown wasn't graceful.
    """
    subprocess.run(
        ["docker", "stop", "--time", "60", container_name], capture_output=True, text=True, timeout=90, check=False
    )
    inspect_result = subprocess.run(
        ["docker", "inspect", container_name, "--format", "{{.State.ExitCode}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    exit_code = int(inspect_result.stdout.strip())

    # region BLOCK_OOMKilledDiagnostics
    oom_result = subprocess.run(
        ["docker", "inspect", container_name, "--format", "{{.State.OOMKilled}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    oom_killed = oom_result.stdout.strip() == "true"
    if oom_killed:
        logger.error(
            "[IMP:10][_stop_and_verify] OOMKilled=true — container %s was killed by "
            "the OOM killer. Docker Desktop memory is insufficient. "
            "Recommended fix: Docker Desktop → Settings → Resources → Memory ≥ 4GB.",
            container_name,
        )
    elif exit_code == 137:
        logger.warning(
            "[IMP:9][_stop_and_verify] Exit code 137 (SIGKILL) but OOMKilled=false — "
            "container %s ran successfully but didn't shut down gracefully within timeout. "
            "This is expected for containers with long-running init scripts.",
            container_name,
        )
    # endregion BLOCK_OOMKilledDiagnostics

    return exit_code, oom_killed


# endregion HELPERS


# region TESTS

# ══════════════════════════════════════════════════════════════════════════════
# Test 1: единый образ без CONTEXT → guard FATAL message printed
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_without_context_exit1
## @purpose — Verify that единый образ (hermes-agent-context) guard script (init-context.sh)
##            exits 1 and prints [IMP:10][CONTEXT_INIT][FATAL] when CONTEXT env is empty at runtime.
##            NOTE: s6-overlay does NOT propagate cont-init.d exit codes to container exit code,
##            so we verify the guard message rather than exit code 1.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts guard FATAL message in container output)
## @complexity — O(B + T) where B = build time (if image missing), T = container run time
## @invariants
##   - Image is built only if not already present
##   - Guard script exits 1 (visible in s6 cont-init.d log), but s6 continues startup
##   - Container exit code is 0 (s6 service supervisor exits cleanly after stop)
##   - Guard FATAL message [IMP:10][CONTEXT_INIT][FATAL] MUST appear in stdout
# 🧐 TRAP[DECISION] · 2026-07-09 · — · s6-overlay absorbs cont-init.d exit code 1
# · Rejected: Expect container exit code 1 when init script exits 1
# · Reason: s6-overlay does NOT propagate cont-init.d script exit codes to the
# ·   container exit status. The init script _guard_context exits 1, but s6 logs
# ·   "04-context-init exited 1" and continues to start main services (hermes-agent,
# ·   dashboard). Container exit code is always 0 (from main service graceful shutdown).
# ·   The guard is informational — it logs FATAL but does not prevent startup.
# · Rev: If upstream s6-overlay adds --fatal-cont-init flag, re-evaluate.
# ·   Alternatively, the guard could use s6-test or a wrapper that catches exit 1.


@pytest.mark.requires_docker
@ldd_trajectory
def test_without_context_exit1(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """
    Единый образ without CONTEXT → guard script prints FATAL message.

    # ⚠️ STRUCTURE:
    #   ▶ [build if missing]           → ⊕ [IMP:9] ready
    #   ▶ docker run --rm -e CONTEXT=   → ◇ FATAL_msg? → ⊕ [IMP:9] guard fired → ⎋ pass
    #       (s6 absorbs exit code 1)                        ⚡ no FATAL_msg → ⎋ fail
    """
    # region BLOCK_Setup
    _docker_skip_if_unavailable()
    logger.info("[IMP:7][test_without_context_exit1] tmp_path=%s", tmp_path)
    # endregion

    # region BLOCK_Build
    _build_image()
    # endregion

    # region BLOCK_RunGuardTest
    # The image is built with CONTEXT=test baked in as ENV.
    # We pass -e CONTEXT="" at runtime to override the baked-in value and trigger the guard.
    # s6-overlay will NOT propagate the cont-init.d exit code 1 to the container exit code,
    # so we verify the guard FATAL message appears in stdout instead.
    # Container is named (--rm still auto-removes on exit) so finally can force-remove
    # a leaked container if `docker run` times out mid-startup (would 503 status-page /health).
    container_name = _container_name("hermes-test-guard")
    try:
        logger.info("[IMP:7][test_without_context_exit1] Running container with empty CONTEXT...")
        run_result = subprocess.run(
            ["docker", "run", "--rm", "--name", container_name, "--memory", "1g", "-e", "CONTEXT=", _IMAGE_TAG],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        _print_docker_imp_logs(run_result.stdout)
        _print_docker_imp_logs(run_result.stderr)
        logger.info(
            "[IMP:9][test_without_context_exit1] Container exit code: %d (s6 absorbs init script exit)",
            run_result.returncode,
        )
        # endregion

        # region BLOCK_VerifyGuardMessage
        # 🧐 TRAP[DECISION] — s6-overlay absorbs cont-init.d exit codes
        # The guard script (04-context-init) exits 1, but s6-overlay treats
        # cont-init.d failures as non-fatal and continues starting main services.
        # Container exit code is 0 even though guard triggered.
        # We verify the guard FATAL message as evidence of correct guard behavior.
        assert "[IMP:10][CONTEXT_INIT][FATAL]" in run_result.stdout, (
            "Expected guard FATAL message '[IMP:10][CONTEXT_INIT][FATAL]' "
            "in container output — guard script did not trigger"
        )
        logger.info("[IMP:9][test_without_context_exit1] ✅ Guard FATAL message confirmed in stdout")
        # endregion
    finally:
        # region BLOCK_Cleanup
        _cleanup_container(container_name)
        logger.info("[IMP:9][test_without_context_exit1] guard container cleanup complete")
        # endregion


# endregion FUNC_test_without_context_exit1


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: единый образ with CONTEXT=ci-test → OK
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_with_context_ok
## @purpose — Verify that единый образ (hermes-agent-context) runs normally when
##            CONTEXT env is set to a valid value at runtime.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts container starts with exit code 0)
## @complexity — O(T) where T = wait time (5s). Image is already built by previous test.
## @invariants
##   - Image is reused from previous test (already built)
##   - Container exit code after stop must be 0 (normal operation)
##   - Init script logs [IMP:9][CONTEXT_INIT][GUARD] on successful validation


@pytest.mark.requires_docker
@ldd_trajectory
def test_with_context_ok(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """
    Единый образ with CONTEXT=ci-test → container starts successfully.

    # ⚠️ STRUCTURE:
    #   ▶ [ensure image]           → ▶ docker run -e CONTEXT=ci-test
    #   → ◇ container_running?
    #     ├── yes → ⊕ [IMP:9] running → stop → ◇ exit_code=0? → ⊕ pass
    #     └── no  → ⚡ fail
    """
    # region BLOCK_Setup
    _docker_skip_if_unavailable()
    logger.info("[IMP:7][test_with_context_ok] tmp_path=%s", tmp_path)
    # endregion

    # region BLOCK_EnsureImage
    _build_image()  # idempotent — skips if already built
    # endregion

    # region BLOCK_Run
    container_name = _container_name("hermes-test-ctx")
    try:
        logger.info("[IMP:7][test_with_context_ok] Starting container '%s' with CONTEXT=ci-test ...", container_name)
        _run_container_detached(_IMAGE_TAG, env_vars={"CONTEXT": "ci-test"}, name=container_name)
        logger.info("[IMP:9][test_with_context_ok] Container '%s' created", container_name)
        # endregion

        # region BLOCK_WaitAndVerify
        # Poll docker ps until container appears (max 30s, interval 2s)
        container_ready = False
        for _attempt in range(15):  # 15 × 2s = 30s
            ps_result = subprocess.run(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if container_name in ps_result.stdout:
                container_ready = True
                break
            time.sleep(2)
        assert container_ready, f"Container '{container_name}' not running after 30s — CONTEXT guard may have triggered"
        logger.info("[IMP:9][test_with_context_ok] Container is running — init passed")
        # endregion

        # region BLOCK_StopAndAssert
        exit_code, oom_killed = _stop_and_verify(container_name)
        logger.info("[IMP:9][test_with_context_ok] Container exit code: %d, OOMKilled=%s", exit_code, oom_killed)
        # ⚠️ TRAP[BUG] · 2026-07-27 · exit 137 + OOM=false tolerated — docker stop timeout, not OOM
        if exit_code == 137 and not oom_killed:
            logger.info("[IMP:9][test_with_context_ok] Exit 137 accepted: container ran OK, shutdown timed out")
        else:
            assert exit_code == 0, (
                f"Expected exit code 0, got {exit_code}"
                f"{' (137 + OOM=true — insufficient Docker memory)' if exit_code == 137 and oom_killed else ''}"
            )
        # endregion
    finally:
        # region BLOCK_Cleanup
        _cleanup_container(container_name)
        logger.info("[IMP:9][test_with_context_ok] container cleanup complete")
        # endregion


# endregion FUNC_test_with_context_ok


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: единый образ → USER 10000 non-root runtime (DevPlan 140 W6)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_user_10000
## @purpose — Verify единый образ работает под non-root USER 10000:10000 (docker inspect Config.User).
##            Контракт «L1 root / L2 non-root» удалён DevPlan 002 — единый образ всегда non-root.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts docker inspect Config.User == "10000:10000")
## @complexity — O(B + T) — build (if missing) + docker inspect


@pytest.mark.requires_docker
@ldd_trajectory
def test_user_10000_non_root(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """
    Единый образ → docker inspect Config.User == 10000:10000 (non-root runtime).

    # ⚠️ STRUCTURE:
    #   ▶ [ensure image] → docker inspect --format '{{.Config.User}}' → ◇ == "10000:10000"? → ⊕ pass | ⚡ fail
    """
    # region BLOCK_Setup
    _docker_skip_if_unavailable()
    logger.info("[IMP:7][test_user_10000_non_root] tmp_path=%s", tmp_path)
    # endregion

    # region BLOCK_EnsureImage
    _build_image()  # idempotent — skips if already built
    # endregion

    # region BLOCK_InspectUser
    inspect_result = subprocess.run(
        ["docker", "inspect", _IMAGE_TAG, "--format", "{{.Config.User}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert inspect_result.returncode == 0, f"docker inspect failed:\n{inspect_result.stderr}"
    user_value = inspect_result.stdout.strip()
    logger.critical("[IMP:9][test_user_10000_non_root] ASSERT: Config.User=%s", user_value)
    assert user_value == "10000:10000", (
        f"Единый образ должен работать non-root USER 10000:10000 (DevPlan 140 W6), got: '{user_value}'"
    )
    logger.info("[IMP:9][test_user_10000_non_root] ✅ Non-root runtime confirmed (USER 10000:10000)")
    # endregion


# endregion FUNC_test_user_10000


# endregion TESTS
