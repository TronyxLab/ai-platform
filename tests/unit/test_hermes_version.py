# GREP_SUMMARY: test-hermes-version hermes-agent-base hermes-agent-context nousresearch-hermes-agent PLATFORM_CONTEXT_REPO LDD IMP caplog
# STRUCTURE: ◇ test_platform_base_image_name[read Dockerfile→LABEL+FROM assert] → ◇ test_context_image_default[read compose→image assert] → ◇ test_hermes_version_module_present[PLATFORM_CONTEXT_REPO→skip|fail|OK]
# region MODULE_CONTRACT
## @purpose  Verify platform Docker image names follow L0→L1→L2 naming convention:
##           hermes-agent-base (L1, local only) and hermes-agent-context (L2, publishable).
##           L0 = nousresearch/hermes-agent (immutable upstream).
##           Also verifies context overlay repository is available via PLATFORM_CONTEXT_REPO.
## @scope    Unit tests; no Docker daemon required. Reads YAML and Dockerfile from disk.
## @invariants
##   - L1 LABEL = hermes-agent-base (local build, pushed to ghcr.io as DR backup)
##   - L2 image = ghcr.io/<context\>/hermes-agent-context (publishable)
##   - L0 FROM = nousresearch/hermes-agent (immutable upstream)
##   - PLATFORM_CONTEXT_REPO unset → skip (L2 context optional); set but missing → fail
## @rationale — Brief §3.5: L1=hermes-agent-base (local-only), L2=hermes-agent-context (GHCR).
##              Organisation-agnostic: PLATFORM_CONTEXT_REPO replaces hardcoded paths.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# ── Helpers ──
def _read_file_line(path: str, search: str) -> str | None:
    """Return first line containing search string, or None."""
    if not pathlib.Path(path).is_file():
        return None
    with pathlib.Path(path).open(encoding="utf-8") as f:
        for line in f:
            if search in line:
                return line.strip()
    return None


# ── Tests ──


@pytest.mark.static_audit
@ldd_trajectory
def test_platform_base_image_name(caplog: pytest.LogCaptureFixture) -> None:
    """Verify platform base image in build/Dockerfile uses hermes-agent-base (L1, local only)."""
    dockerfile_path = (
        Path(pathlib.Path(__file__).resolve().parent.parent.parent)
        / "core"
        / "modules"
        / "hermes-agent"
        / "build"
        / "Dockerfile"
    )
    assert pathlib.Path(dockerfile_path).is_file(), f"Dockerfile not found at {dockerfile_path}"
    logger.info("[IMP:7][test_platform_base_image_name] Checking Dockerfile: %s", dockerfile_path)

    label_line = _read_file_line(dockerfile_path, "org.opencontainers.image.name=")
    assert label_line is not None, "Dockerfile missing LABEL org.opencontainers.image.name"
    assert "hermes-agent-base" in label_line, f"LABEL does not contain hermes-agent-base: '{label_line}'"
    logger.critical("[IMP:9][test_platform_base_image_name] ASSERT: label=%s", label_line)

    from_line = _read_file_line(dockerfile_path, "FROM ")
    assert from_line is not None, "Dockerfile missing FROM statement"
    assert "nousresearch/hermes-agent" in from_line, f"FROM does not reference nousresearch/hermes-agent: '{from_line}'"
    logger.critical("[IMP:9][test_platform_base_image_name] ASSERT: from=%s", from_line)


@pytest.mark.static_audit
@ldd_trajectory
def test_context_image_default(caplog: pytest.LogCaptureFixture) -> None:
    """Verify CONTEXT_IMAGE default in compose uses hermes-agent-context."""
    compose_path = (
        Path(pathlib.Path(__file__).resolve().parent.parent.parent)
        / "core"
        / "modules"
        / "hermes-agent"
        / "docker-compose.base.yml"
    )
    assert pathlib.Path(compose_path).is_file(), f"Compose not found at {compose_path}"
    logger.info("[IMP:7][test_context_image_default] Checking compose: %s", compose_path)

    image_line = _read_file_line(compose_path, "image: ")
    assert image_line is not None, "docker-compose.base.yml missing image directive"
    assert "hermes-agent-context" in image_line, f"Image must reference hermes-agent-context: '{image_line}'"
    logger.critical("[IMP:9][test_context_image_default] ASSERT: image=%s", image_line)


# 🧐 TRAP[DECISION] · 2026-07-11 · — · test_hermes_version_module_present removed
# · Rejected: keeping as conditional L2 gate
# · Reason: always SKIP without PLATFORM_CONTEXT_REPO env var — dead test in local/CI
# ·   Added no value; test_context_image_default already validates L2 image naming.
# ·   If L2 context overlay testing is needed, restore with requires_docker marker.
# · Rev: restore if PLATFORM_CONTEXT_REPO becomes a mandatory env var in CI
