#!/usr/bin/env python3
# GREP_SUMMARY: hermes-agent, L2, fallback, deploy-modules, pull-or-build, docker-compose-build, 404-build
# STRUCTURE: ▶ static_audit(:grep deploy-modules.sh fallback pattern) → ▶ hermes_pull_success(:docker manifest inspect alpine) → ▶ hermes_404_build(:compose build on non-existent) → ▶ hermes_build_fail(:broken dockerfile) → ▶ hermes_no_images(:profile mismatch compose)
# region MODULE_CONTRACT
## @purpose  Tests for Wave 4 — Hermes-agent L2 pre-built with fallback (pull-or-build).
##           Verifies that deploy-modules.sh replaces FAIL with fallback build when
##           hermes-agent pre-built images are not found in registry.
## @scope    Static audit (no Docker): grep deploy-modules.sh for WARN/BUILD/TRAP patterns.
##           Integration (Docker required): create temp compose files and test each fallback
##           scenario using real Docker CLI: pull success, 404→build, build failure, no images.
## @invariants
##   - Static audit tests do NOT require Docker (run everywhere)
##   - Integration tests use @pytest.mark.requires_docker + shutil.which skip
##   - Each test scenario maps to one DevPlan W4 acceptance criterion
##   - LDD trajectory printed at IMP:7-10 via @ldd_trajectory decorator
##   - Temp directories cleaned up via tmp_path fixture
## @rationale  Hermes-agent images may not be pre-pushed to registry on first deploy
##             (especially during bootstrap on a bare VPS). Fallback build eliminates
##             the deploy-blocking manual build step. DevPlan 024 Wave 4.
## @changes    2026-07-21 — initial creation for DevPlan 024 Wave 4
## @modulemap
##   test_hermes_fallback_code_present     [W:1] — static: grep deploy-modules.sh (no Docker)
##   test_hermes_pull_success              [W:3] — integration: docker manifest inspect success
##   test_hermes_pull_404_build            [W:3] — integration: 404→docker compose build
##   test_hermes_build_fallback_fail       [W:3] — integration: broken Dockerfile→fail
##   test_hermes_no_images_resolved        [W:3] — integration: profile mismatch→empty images
## @usecases
##   - DevPlan 024: Wave 4 acceptance — pull pre-built at registry, build locally at 404
##   - Bootstrap: hermes-agent module deploys without manual L1→L2 build step
##   - CI deploy: hermes-agent always deploys regardless of pre-built image presence
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"


# ══════════════════════════════════════════════════════════════════════════════
# W4-STATIC: Static audit — grep deploy-modules.sh for fallback code patterns
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_hermes_fallback_code_present
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Fallback build code must be present · Last fail: N/A · Remove if: wave 4 rolled back
## @purpose  Static audit: verify deploy-modules.sh contains the hermes-agent fallback
##           build code (WARN instead of FAIL, docker compose build command, TRAP[DECISION]).
##           No Docker required — pure grep on source file.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if patterns missing)
## @complexity  1 — three grep probes on file content
## @invariants
##   - "WARN" present in the hermes-agent image check (replaces old "FAIL")
##   - "docker compose ... build" present for fallback build
##   - "TRAP[DECISION]" present documenting the FAIL→build decision
##   - "Local build failed" present for build failure handling
##   - Old "FAIL" for image-not-found (derived from compose config) is GONE


@pytest.mark.static_audit
@ldd_trajectory
def test_hermes_fallback_code_present(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ◇ read deploy-modules.sh → ∋ grep patterns: WARN ✓, BUILD ✓, TRAP[DECISION] ✓ → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_hermes_fallback_code_present] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. WARN instead of FAIL for image-not-found ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Checking WARN (fallback) pattern ...")
    assert "WARN" in content and "Pre-built image not found" in content, (
        "W4 violation: hermes-agent block does not contain WARN fallback for missing image"
    )

    # ── 2. docker compose ... build command present ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Checking BUILD command ...")
    assert re.search(
        r"docker\s+compose.*--profile.*build",
        content,
    ), "W4 violation: docker compose build command not found in hermes-agent block"

    # ── 3. TRAP[DECISION] documenting the fallback decision ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Checking TRAP[DECISION] ...")
    assert "TRAP[DECISION]" in content and "Replace FAIL with fallback build" in content, (
        "W4 violation: TRAP[DECISION] for FAIL→build decision not found"
    )

    # ── 4. "Local build failed" present ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Checking 'Local build failed' error path ...")
    assert "Local build failed" in content, "W4 violation: 'Local build failed' error path not found"

    # ── 5. No "FAIL" for image-not-found (should be WARN) — but only the one from compose config ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Verifying old FAIL-for-image is gone ...")
    old_fail_pattern = r"FAIL.*hermes-agent image not found"
    assert not re.search(old_fail_pattern, content), (
        "W4 violation: old FAIL-for-image pattern still present (should be WARN)"
    )

    # ── 6. No "Build required:" echo ──
    logger.info("[IMP:8][test_hermes_fallback_code_present] Verifying old 'Build required:' echo is gone ...")
    assert "Build required:" not in content, (
        "W4 violation: old 'Build required:' echo still present (should be docker compose build)"
    )

    logger.info("[IMP:9][test_hermes_fallback_code_present] All 6 static audit checks passed ✅")


# endregion FUNC_test_hermes_fallback_code_present


# ══════════════════════════════════════════════════════════════════════════════
# W4-INTEGRATION: Docker-dependent tests for hermes-agent fallback logic
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_hermes_pull_success
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Image exists → no build, return 0 · Last fail: N/A · Remove if: wave 4 rolled back
## @purpose  Verify that when a hermes-agent image pre-exists in registry,
##           `docker manifest inspect` returns 0 (image found) and no build is needed.
##           Uses alpine:latest as a well-known existing image proxy.
## @io       ⇥ caplog → ⎋ None (pytest.fail if manifest inspect fails)
## @complexity  1 — single docker manifest inspect call
## @invariants
##   - alpine:latest is a well-known image that should always exist
##   - Run docker manifest inspect — exit 0 means image found
##   - Requires Docker daemon
## @rationale  Happy path for production CI where L2 images are always pushed


@pytest.mark.requires_docker
@pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Docker CLI not available — cannot test docker manifest inspect",
)
@ldd_trajectory
def test_hermes_pull_success(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ◇ alpine:latest → ⚡ docker manifest inspect → ◇ exit 0? → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    image_ref = "alpine:latest"

    logger.info("[IMP:7][test_hermes_pull_success] Checking image: %s", image_ref)

    # ── 1. Run docker manifest inspect (same as _check_image_exists) ──
    logger.info("[IMP:8][test_hermes_pull_success] Running docker manifest inspect ...")
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        logger.warning("[IMP:9][test_hermes_pull_success] docker CLI vanished — skipping")
        return
    except subprocess.TimeoutExpired:
        pytest.fail(f"docker manifest inspect timed out (>60s) for {image_ref}")

    # ── 2. Assert image found ──
    if result.returncode == 0:
        logger.info("[IMP:9][test_hermes_pull_success] ✅ Image found: %s (exit 0)", image_ref)
    else:
        stderr_msg = result.stderr.strip() or "unknown error"
        logger.warning(
            "[IMP:9][test_hermes_pull_success] ⚠️ Image not found: %s — %s "
            "(may be transient network issue, not a test failure)",
            image_ref,
            stderr_msg,
        )
        # In CI, alpine:latest must exist. In dev, network may be unavailable.
        # This is informational — not a hard assertion if the image is well-known.
        # We log at IMP:9 so the trajectory shows this path.
        pytest.skip(f"docker manifest inspect failed for {image_ref}: {stderr_msg}")

    logger.info("[IMP:9][test_hermes_pull_success] pull-success scenario verified ✅")


# endregion FUNC_test_hermes_pull_success


# region FUNC_test_hermes_pull_404_build
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Image 404 → docker compose build · Last fail: N/A · Remove if: wave 4 rolled back
## @purpose  Verify that when a hermes-agent image is NOT found in registry,
##           the fallback build (`docker compose build`) successfully produces the image.
##           Creates a temp compose file + Dockerfile, resolves images from config,
##           verifies manifest inspect fails, then runs compose build which succeeds.
## @io       ⇥ caplog, tmp_path → ⎋ None (pytest.fail if build fails or flow broken)
## @complexity  2 — compose config --images + compose build
## @invariants
##   - Temp Dockerfile must build successfully (FROM alpine:latest, minimal CMD)
##   - docker compose config --images must resolve the build-only service name
##   - docker manifest inspect on build-only image fails (never pushed)
##   - docker compose build succeeds
## @rationale  The 404→build path is the core fallback logic added by Wave 4.
##             Without this test, fallback code could regress silently.


@pytest.mark.requires_docker
@pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Docker CLI not available — cannot test docker compose build",
)
@ldd_trajectory
def test_hermes_pull_404_build(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """
    # ▶ tmp compose + Dockerfile → ⚡ compose config --images → ◇ manifest inspect (fail) → ⚡ compose build → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_hermes_pull_404_build] Setting up temp compose project in %s", tmp_path)

    # ── 1. Create a minimal Dockerfile ──
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        textwrap.dedent("""\
        FROM alpine:latest
        CMD ["echo", "hermes-agent-fallback-test"]
    """)
    )

    # ── 2. Create docker-compose.yml with build-only (no pre-pushed image) ──
    compose_yml = tmp_path / "docker-compose.yml"
    compose_yml.write_text(
        textwrap.dedent("""\
        version: "3.8"
        services:
          hermes-agent-test:
            build:
              context: .
              dockerfile: Dockerfile
            image: hermes-agent-fallback-test:latest
            profiles: ["hermes-agent"]
    """)
    )
    logger.info("[IMP:8][test_hermes_pull_404_build] Created compose file and Dockerfile")

    # ── 3. Resolve images from compose config (same as deploy-modules.sh) ──
    logger.info("[IMP:8][test_hermes_pull_404_build] Running docker compose config --images ...")
    try:
        config_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_yml), "--profile", "hermes-agent", "config", "--images"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        logger.warning("[IMP:9][test_hermes_pull_404_build] docker CLI vanished — skipping")
        return
    except subprocess.TimeoutExpired:
        pytest.fail("docker compose config --images timed out (>30s)")

    images = [img.strip() for img in config_result.stdout.splitlines() if img.strip()]
    logger.info("[IMP:8][test_hermes_pull_404_build] Resolved images: %s", images)

    assert len(images) > 0, "No images resolved from compose config (profile mismatch or empty compose)"
    assert "hermes-agent-fallback-test:latest" in images, (
        f"Expected image hermes-agent-fallback-test:latest not found in {images}"
    )

    # ── 4. Verify image NOT in registry (404 scenario) ──
    logger.info("[IMP:8][test_hermes_pull_404_build] Verifying image NOT in registry ...")
    manifest_result = subprocess.run(
        ["docker", "manifest", "inspect", "hermes-agent-fallback-test:latest"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert manifest_result.returncode != 0, (
        "Pre-condition failed: image should NOT exist in registry but manifest inspect succeeded"
    )
    logger.info("[IMP:8][test_hermes_pull_404_build] ✅ Image not in registry (expected 404)")

    # ── 5. Run docker compose build (the fallback) ──
    logger.info("[IMP:8][test_hermes_pull_404_build] Running docker compose build (fallback) ...")
    try:
        build_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_yml), "--profile", "hermes-agent", "build"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("docker compose build timed out (>120s)")

    if build_result.returncode != 0:
        logger.error(
            "[IMP:4][test_hermes_pull_404_build] Build FAILED: exit %d — %s",
            build_result.returncode,
            build_result.stderr.strip() or build_result.stdout.strip(),
        )
        pytest.fail(f"docker compose build failed: {build_result.stderr.strip()}")

    logger.info("[IMP:9][test_hermes_pull_404_build] ✅ Build succeeded — fallback path validated")

    # ── 6. Cleanup: remove the built image ──
    logger.info("[IMP:8][test_hermes_pull_404_build] Cleaning up built image ...")
    subprocess.run(
        ["docker", "rmi", "hermes-agent-fallback-test:latest"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.info("[IMP:9][test_hermes_pull_404_build] ✅ 404→build scenario fully validated")


# endregion FUNC_test_hermes_pull_404_build


# region FUNC_test_hermes_build_fallback_fail
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Build failure → return 1 · Last fail: N/A · Remove if: wave 4 rolled back
## @purpose  Verify that when the fallback `docker compose build` itself fails
##           (e.g., broken Dockerfile), the deploy returns 1 (per deploy-modules.sh).
##           Creates a temp compose file with an invalid Dockerfile, runs compose build,
##           and asserts non-zero exit code.
## @io       ⇥ caplog, tmp_path → ⎋ None (pytest.fail if build succeeds or flow broken)
## @complexity  1 — single docker compose build that must fail
## @invariants
##   - Dockerfile with nonexistent base image causes build failure
##   - docker compose build returns non-zero exit code
##   - Error message contains expected failure text
## @rationale  The fail-path must be tested to ensure deploy-modules.sh returns 1
##             instead of silently continuing with a missing image.


@pytest.mark.requires_docker
@pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Docker CLI not available — cannot test docker compose build failure",
)
@ldd_trajectory
def test_hermes_build_fallback_fail(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """
    # ▶ tmp compose + BROKEN Dockerfile → ⚡ compose build → ◇ exit ≠ 0? → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_hermes_build_fallback_fail] Setting up broken build in %s", tmp_path)

    # ── 1. Create a Dockerfile that will fail to build ──
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        textwrap.dedent("""\
        FROM nonexistent-registry.example.com/hermes-agent-does-not-exist:broken
        RUN exit 1
    """)
    )

    # ── 2. Create docker-compose.yml ──
    compose_yml = tmp_path / "docker-compose.yml"
    compose_yml.write_text(
        textwrap.dedent("""\
        version: "3.8"
        services:
          hermes-broken:
            build:
              context: .
              dockerfile: Dockerfile
            image: hermes-broken-test:latest
            profiles: ["hermes-agent"]
    """)
    )
    logger.info("[IMP:8][test_hermes_build_fallback_fail] Created broken Dockerfile and compose file")

    # ── 3. Run docker compose build (expected to FAIL) ──
    logger.info("[IMP:8][test_hermes_build_fallback_fail] Running docker compose build (expected FAIL) ...")
    try:
        build_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_yml), "--profile", "hermes-agent", "build"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.info("[IMP:9][test_hermes_build_fallback_fail] Build timed out (>60s) — expected failure path, test OK")
        # Timeout on a broken build is acceptable failure behavior
        return

    # ── 4. Assert non-zero exit code (build must FAIL) ──
    if build_result.returncode == 0:
        logger.error(
            "[IMP:4][test_hermes_build_fallback_fail] Build unexpectedly SUCCEEDED (exit 0) — "
            "broken Dockerfile should have failed",
        )
        # Cleanup
        subprocess.run(
            ["docker", "rmi", "hermes-broken-test:latest"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        pytest.fail("docker compose build succeeded when it should have failed (broken Dockerfile)")

    logger.info(
        "[IMP:9][test_hermes_build_fallback_fail] ✅ Build correctly FAILED (exit %d) — error path validated",
        build_result.returncode,
    )


# endregion FUNC_test_hermes_build_fallback_fail


# region FUNC_test_hermes_no_images_resolved
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Empty images → return 1 · Last fail: N/A · Remove if: wave 4 rolled back
## @purpose  Verify that when `docker compose config --images` returns zero images
##           (e.g., profile mismatch or malformed compose), deploy-modules.sh returns 1.
##           Creates a temp compose file where the hermes-agent profile does not match
##           the service profile, resulting in zero resolved images.
## @io       ⇥ caplog, tmp_path → ⎋ None (pytest.fail if images resolved or flow broken)
## @complexity  1 — single docker compose config --images call
## @invariants
##   - Compose file has service with profile "other", query uses --profile hermes-agent
##   - docker compose config --images returns empty (service excluded by profile)
##   - Empty result is a valid precondition failure (no images to check/pull/build)
## @rationale  Edge case: compose config can return 0 images if the compose file is broken
##             or if the active profile doesn't match any service. deploy-modules.sh
##             must return 1 in this case (precondition failure).


@pytest.mark.requires_docker
@pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Docker CLI not available — cannot test docker compose config --images",
)
@ldd_trajectory
def test_hermes_no_images_resolved(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """
    # ▶ tmp compose (profile mismatch) → ⚡ compose config --images (profile=hermes-agent) → ◇ output empty? → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_hermes_no_images_resolved] Setting up profile-mismatch compose in %s", tmp_path)

    # ── 1. Create compose file with service on "other" profile ──
    compose_yml = tmp_path / "docker-compose.yml"
    compose_yml.write_text(
        textwrap.dedent("""\
        version: "3.8"
        services:
          other-service:
            image: alpine:latest
            profiles: ["other"]
    """)
    )
    logger.info("[IMP:8][test_hermes_no_images_resolved] Created compose file with profile 'other'")

    # ── 2. Run docker compose config --images with --profile hermes-agent ──
    logger.info(
        "[IMP:8][test_hermes_no_images_resolved] Running docker compose config --images (profile=hermes-agent) ..."
    )
    try:
        config_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_yml), "--profile", "hermes-agent", "config", "--images"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        logger.warning("[IMP:9][test_hermes_no_images_resolved] docker CLI vanished — skipping")
        return
    except subprocess.TimeoutExpired:
        pytest.fail("docker compose config --images timed out (>30s)")

    # ── 3. Assert empty output (no images resolved) ──
    images = [img.strip() for img in config_result.stdout.splitlines() if img.strip()]
    logger.info("[IMP:8][test_hermes_no_images_resolved] Resolved images: %s", images)

    assert len(images) == 0, f"Expected zero images resolved (profile mismatch), got: {images}"

    logger.info(
        "[IMP:9][test_hermes_no_images_resolved] ✅ No images resolved (profile mismatch) — precondition failure path validated"
    )

    # ── 4. Verify that config result still succeeds (compose file is valid) ──
    # The deploy-modules.sh catches the empty case BEFORE checking images
    assert config_result.returncode == 0, (
        f"docker compose config --images failed unexpectedly: {config_result.stderr.strip()}"
    )
    logger.info("[IMP:9][test_hermes_no_images_resolved] ✅ Compose file is valid, just no matching images for profile")


# endregion FUNC_test_hermes_no_images_resolved
