# GREP_SUMMARY: test-hermes-version hermes-agent-context nousresearch-hermes-agent Dockerfile CONTEXT_IMAGE LDD IMP caplog L1-collapse
# STRUCTURE: ◇ test_platform_base_image_name[read Dockerfile→LABEL+FROM assert] → ◇ test_context_image_default[read compose→image assert]
# region MODULE_CONTRACT
## @purpose  Verify platform Docker image names follow canonical naming after L1→L2 коллапс
##           (DevPlan 002): единый образ hermes-agent-context (L2) собирается из единого
##           multi-stage Dockerfile (base-стадия = бывш. L1, final = context overlay).
##           L0 = nousresearch/hermes-agent (immutable upstream).
## @scope    Unit tests; no Docker daemon required. Reads YAML and Dockerfile from disk.
## @invariants
##   - Единый Dockerfile LABEL = hermes-agent-context (L1-лейбл удалён)
##   - L2 image = ghcr.io/<context\>/hermes-agent-context (publishable)
##   - L0 FROM = nousresearch/hermes-agent (immutable upstream)
##   - hermes-agent-base НЕ должен встречаться ни в Dockerfile, ни в compose (кроме негативных
##     drift-детекторов) — L1 коллапс
## @rationale — DevPlan 002 W5 T5.4: L1 label удалён; единый образ. Organisation-agnostic.
## @changes  2026-08-16 | DevPlan 002 W5 T5.4 — rewrite под единый образ (build/Dockerfile удалён)


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
    """Verify единый Dockerfile uses hermes-agent-context (L1 hermes-agent-base удалён)."""
    dockerfile_path = (
        Path(pathlib.Path(__file__).resolve().parent.parent.parent) / "core" / "modules" / "hermes-agent" / "Dockerfile"
    )
    assert pathlib.Path(dockerfile_path).is_file(), f"Dockerfile not found at {dockerfile_path}"
    logger.info("[IMP:7][test_platform_base_image_name] Checking Dockerfile: %s", dockerfile_path)

    content = pathlib.Path(dockerfile_path).read_text(encoding="utf-8")

    label_line = _read_file_line(dockerfile_path, "org.opencontainers.image.name=")
    assert label_line is not None, "Dockerfile missing LABEL org.opencontainers.image.name"
    assert "hermes-agent-context" in label_line, f"LABEL does not contain hermes-agent-context: '{label_line}'"
    assert "hermes-agent-base" not in label_line, (
        f"L1 LABEL удалён — hermes-agent-base не должен присутствовать: '{label_line}'"
    )
    logger.critical("[IMP:9][test_platform_base_image_name] ASSERT: label=%s", label_line)

    # FROM-директивы — строки, реально начинающиеся с FROM (не STRUCTURE-комментарий)
    from_lines = [line for line in content.splitlines() if line.strip().startswith("FROM ")]
    assert from_lines, "Dockerfile missing FROM statement"
    assert any("nousresearch/hermes-agent" in line for line in from_lines), (
        f"FROM does not reference nousresearch/hermes-agent: {from_lines}"
    )
    logger.critical("[IMP:9][test_platform_base_image_name] ASSERT: from=%s", from_lines[0].strip())

    # L1 коллапс: единый Dockerfile не содержит hermes-agent-base (нет L1-стадии, публикующейся отдельно)
    assert "hermes-agent-base" not in content, (
        "L1 hermes-agent-base удалён (DevPlan 002) — не должен встречаться в Dockerfile"
    )


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

    # L1 коллапс: compose build.dockerfile → единый Dockerfile
    dockerfile_line = _read_file_line(compose_path, "dockerfile: ")
    assert dockerfile_line is not None, "docker-compose.base.yml missing build.dockerfile"
    assert "core/modules/hermes-agent/Dockerfile" in dockerfile_line, (
        f"build.dockerfile должен указывать на единый Dockerfile: '{dockerfile_line}'"
    )
    logger.critical("[IMP:9][test_context_image_default] ASSERT: dockerfile=%s", dockerfile_line)


# 🧐 TRAP[DECISION] · 2026-07-11 · — · test_hermes_version_module_present removed
# · Rejected: keeping as conditional L2 gate
# · Reason: always SKIP without PLATFORM_CONTEXT_REPO env var — dead test in local/CI
# ·   Added no value; test_context_image_default already validates L2 image naming.
# · Rev: restore if PLATFORM_CONTEXT_REPO becomes a mandatory env var in CI
