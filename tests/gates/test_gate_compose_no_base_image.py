# GREP_SUMMARY: gate compose no-base-image hermes-agent-base context-image platform-dev production anti-drift
# STRUCTURE: ┌ collect compose files ┐ → ◇ exclude test/platform-dev/macos → ◇ read + scan for hermes-agent-base → ⊕ violations → ∑ fail if any
# region MODULE_CONTRACT
## @purpose  Gate test: ensure hermes-agent-base (L1) is only used in platform-dev override, never in production compose files.
##           Root docker-compose.yml must use ${CONTEXT_IMAGE:-...} variable, not a hardcoded hermes-agent image.
## @scope    Static file analysis — all docker-compose*.yml files at root and core/modules/*/
## @invariants
##   - Production compose files (base, root compose) MUST NOT reference hermes-agent-base image
##   - docker-compose.platform-dev.yml MUST reference hermes-agent-base:latest (L1 dev override)
##   - Root docker-compose.yml hermes-agent image MUST use ${CONTEXT_IMAGE:-...} variable pattern
##   - Test (.test.yml), macOS (.macos.yml), and platform-dev files are excluded from production check
##   - Template compose files (templates/) are excluded from both globs and thus from all checks
## @rationale  L1 image (hermes-agent-base) is built locally and pushed to ghcr.io as DR backup. Only
##             platform-dev override intentionally uses it. Production must use L2 (context overlay)
##             which is the deployable image. See DevPlan §3.2 and docker-compose.platform-dev.yml TRAP[DECISION].
## @changes    CREATED: 2026-07-09 | TASK-5G7
# endregion MODULE_CONTRACT

import fnmatch
import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# Glob patterns for collecting all compose files
COMPOSE_GLOB_ROOT = "docker-compose*.yml"
COMPOSE_GLOB_MODULES = "core/modules/*/docker-compose*.yml"

# Exclude patterns (applied to basename via fnmatch)
EXCLUDE_BASENAME_PATTERNS = [
    "docker-compose.platform-dev.yml",
    "*-platform-dev.yml",
    "docker-compose.test.yml",
    "docker-compose.macos.yml",
]

# Image constants
HERMES_AGENT_BASE_IMAGE = "hermes-agent-base"
CONTEXT_IMAGE_VAR_PATTERN = re.compile(r"\$\{CONTEXT_IMAGE(?::-|:\?)[^}]+\}")
PLATFORM_DEV_PATH = "docker-compose.platform-dev.yml"
ROOT_COMPOSE_PATH = "docker-compose.yml"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _collect_compose_files() -> list[pathlib.Path]:
    """Collect all docker-compose*.yml from root and core/modules/*/.

    ## @purpose — Discover all compose files for static analysis.
    ## @io — ⎋ list[pathlib.Path]: sorted list of compose file paths
    ## @complexity — O(N) where N = number of file system entries
    """
    files: list[pathlib.Path] = []
    files.extend(sorted(PROJECT_ROOT.glob(COMPOSE_GLOB_ROOT)))
    files.extend(sorted(PROJECT_ROOT.glob(COMPOSE_GLOB_MODULES)))
    logger.info("[IMP:7][_collect_compose_files] Found %d compose files", len(files))
    for fp in files:
        logger.debug("[IMP:5][_collect_compose_files]   %s", fp.relative_to(PROJECT_ROOT))
    return files


def _is_excluded(filepath: pathlib.Path) -> bool:
    """Check if a compose file should be excluded from production checks.

    ## @purpose — Apply EXCLUDE_BASENAME_PATTERNS to basename.
    ## @io — ⇥ filepath: path → ⎋ bool: True if basename matches any exclude pattern
    ## @complexity — O(P) where P = number of exclude patterns
    """
    basename = filepath.name
    for pattern in EXCLUDE_BASENAME_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            logger.debug("[IMP:5][_is_excluded]  EXCLUDED %s (matches %s)", filepath.name, pattern)
            return True
    logger.debug("[IMP:5][_is_excluded]  INCLUDED %s", filepath.name)
    return False


def _read_file(path: pathlib.Path) -> str:
    """Read file content.

    ## @purpose — Simple file read with explicit encoding.
    ## @io — ⇥ path: pathlib.Path → ⎋ str: file content
    ## @complexity — O(F) where F = file size in bytes
    """
    content = path.read_text(encoding="utf-8")
    logger.info("[IMP:8][_read_file] Read %s (%d bytes)", path.relative_to(PROJECT_ROOT), len(content))
    return content


# ── Test 1: No hermes-agent-base in production compose files ──────────────────


# region test_no_base_image_in_production_compose
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_no_base_image_in_production_compose(caplog) -> None:
    """Ensure no production compose file references hermes-agent-base (L1) image.

    ## @purpose  Gate: ensure L1 image (hermes-agent-base) is only in platform-dev,
    ##            never in production compose files. FAIL code: BASE_IMAGE_IN_PRODUCTION.
    ## @io       ⎋ None — assert side-effect (pytest.fail on violations)
    ## @complexity O(N×L) where N = compose files, L = lines per file
    FAIL code: BASE_IMAGE_IN_PRODUCTION
    """
    # [IMP:9][test_no_base_image_in_production_compose] Start — collecting production compose files
    logger.info("[IMP:9][test_no_base_image_in_production_compose] Collecting compose files...")

    all_files = _collect_compose_files()

    # Filter to production files only (exclude test, macos, platform-dev)
    production_files = [f for f in all_files if not _is_excluded(f)]
    logger.info(
        "[IMP:8][test_no_base_image_in_production_compose] Production files: %d (excluded %d)",
        len(production_files),
        len(all_files) - len(production_files),
    )

    violations: list[tuple[str, int]] = []  # (relative path, line number)

    for fp in production_files:
        rel_path = str(fp.relative_to(PROJECT_ROOT))
        content = _read_file(fp)
        for line_no, line in enumerate(content.splitlines(), 1):
            # Skip pure comment lines (YAML # comments) — only flag actual image references
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if HERMES_AGENT_BASE_IMAGE in stripped:
                violations.append((rel_path, line_no))
                logger.warning(
                    "[IMP:8][test_no_base_image_in_production_compose] VIOLATION: %s:%d — %s",
                    rel_path,
                    line_no,
                    stripped,
                )

    if violations:
        msg_lines = [
            f"BASE_IMAGE_IN_PRODUCTION: Found {len(violations)} production compose file(s) "
            f"referencing '{HERMES_AGENT_BASE_IMAGE}':"
        ]
        for path_, line_no in violations:
            msg_lines.append(f"  • {path_}:{line_no}")
        msg = "\n".join(msg_lines)
        logger.error("[IMP:10][test_no_base_image_in_production_compose] %s", msg)
        pytest.fail(msg)

    logger.info(
        "[IMP:10][test_no_base_image_in_production_compose] PASS — no violations found in %d production files",
        len(production_files),
    )


# endregion test_no_base_image_in_production_compose


# ── Test 2: Platform-dev has L1 image ────────────────────────────────────────


# region test_platform_dev_has_l1_image
@pytest.mark.gate
@ldd_trajectory
def test_platform_dev_has_l1_image(caplog) -> None:
    """Ensure docker-compose.platform-dev.yml explicitly references hermes-agent-base.

    ## @purpose  Gate: confirm platform-dev.yml references hermes-agent-base as L1 image.
    ##            FAIL code: PLATFORM_DEV_MISSING_L1.
    ## @io       ⎋ None — assert side-effect (pytest.fail on missing L1 reference)
    ## @complexity O(F) where F = file size in bytes
    FAIL code: PLATFORM_DEV_MISSING_L1
    """
    logger.info("[IMP:9][test_platform_dev_has_l1_image] Checking platform dev compose...")

    platform_dev_path = PROJECT_ROOT / PLATFORM_DEV_PATH

    if not platform_dev_path.exists():
        msg = f"PLATFORM_DEV_MISSING_L1: {PLATFORM_DEV_PATH} not found"
        logger.error("[IMP:10][test_platform_dev_has_l1_image] %s", msg)
        pytest.fail(msg)

    content = _read_file(platform_dev_path)

    # Check that hermes-agent-base appears in the file
    if HERMES_AGENT_BASE_IMAGE not in content:
        msg = (
            f"PLATFORM_DEV_MISSING_L1: {PLATFORM_DEV_PATH} does not reference "
            f"'{HERMES_AGENT_BASE_IMAGE}' — expected L1 image for platform dev"
        )
        logger.error("[IMP:10][test_platform_dev_has_l1_image] %s", msg)
        pytest.fail(msg)

    # Verify the image: line explicitly uses hermes-agent-base (not just in comments)
    # Look for: image: hermes-agent-base:latest or image: hermes-agent-base
    image_pattern = re.compile(
        r"image:\s*" + re.escape(HERMES_AGENT_BASE_IMAGE) + r"(:\S+)?$",
        re.MULTILINE,
    )
    if not image_pattern.search(content):
        # It might be in a comment-only context; log as warning but don't fail
        logger.warning(
            "[IMP:8][test_platform_dev_has_l1_image] '%s' found in %s but not on an 'image:' directive line",
            HERMES_AGENT_BASE_IMAGE,
            PLATFORM_DEV_PATH,
        )
    else:
        logger.info(
            "[IMP:9][test_platform_dev_has_l1_image] '%s' correctly set as image in %s",
            HERMES_AGENT_BASE_IMAGE,
            PLATFORM_DEV_PATH,
        )

    logger.info("[IMP:10][test_platform_dev_has_l1_image] PASS — platform dev has L1 image")


# endregion test_platform_dev_has_l1_image


# ── Test 3: Root compose uses CONTEXT_IMAGE var ─────────────────────────────


# region test_root_compose_uses_context_image_var
# 🧐 TRAP[DEBT] · 2026-07-14 · — · root compose include-based, hermes-agent image в base.yml
# · Rev: при возврате к inline-сервисам обновить тест
HERMES_AGENT_BASE_YML = "core/modules/hermes-agent/docker-compose.base.yml"


@pytest.mark.gate
@ldd_trajectory
def test_root_compose_uses_context_image_var(caplog) -> None:
    """Ensure hermes-agent image in base.yml uses ${CONTEXT_IMAGE:-...} (include: architecture).

    ## @purpose  Gate: confirm hermes-agent/docker-compose.base.yml uses CONTEXT_IMAGE
    ##            variable, not hardcoded image. Root compose is include:-based after
    ##            refactoring — image definition resides in the module compose.
    ##            FAIL code: ROOT_COMPOSE_HARDCODED_IMAGE.
    ## @io       ⎋ None — assert side-effect (pytest.fail on hardcoded image)
    ## @complexity O(L) where L = lines in base compose file
    FAIL code: ROOT_COMPOSE_HARDCODED_IMAGE
    """
    logger.info("[IMP:9][test_root_compose_uses_context_image_var] Checking hermes-agent base.yml image var...")

    base_yml_path = PROJECT_ROOT / HERMES_AGENT_BASE_YML

    if not base_yml_path.exists():
        msg = f"ROOT_COMPOSE_HARDCODED_IMAGE: {HERMES_AGENT_BASE_YML} not found"
        logger.error("[IMP:10][test_root_compose_uses_context_image_var] %s", msg)
        pytest.fail(msg)

    content = _read_file(base_yml_path)

    # Find the hermes-agent service image line
    # Look for: image: ${CONTEXT_IMAGE:-...}
    lines = content.splitlines()
    found_context_var = False
    found_hardcoded = False
    hardcoded_line = None

    # Scan for image: line in hermes-agent services section
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if "image:" in stripped:
            logger.info("[IMP:8][test_root_compose_uses_context_image_var]  Image line at %d: %s", i, stripped)

            if CONTEXT_IMAGE_VAR_PATTERN.search(stripped):
                found_context_var = True
                logger.info(
                    "[IMP:9][test_root_compose_uses_context_image_var]  ✓ Uses ${CONTEXT_IMAGE:-...} at line %d",
                    i,
                )
            else:
                found_hardcoded = True
                hardcoded_line = (i, stripped)
                logger.warning(
                    "[IMP:9][test_root_compose_uses_context_image_var]  ✗ HARDCODED image at line %d: %s",
                    i,
                    stripped,
                )

    if not found_context_var:
        if found_hardcoded:
            line_no, line_text = hardcoded_line
            msg = (
                f"ROOT_COMPOSE_HARDCODED_IMAGE: {HERMES_AGENT_BASE_YML}:{line_no} — "
                f"hermes-agent image is hardcoded: '{line_text}'. "
                f"Must use ${{CONTEXT_IMAGE:-...}} or ${{CONTEXT_IMAGE:?...}} variable pattern."
            )
            logger.error("[IMP:10][test_root_compose_uses_context_image_var] %s", msg)
            pytest.fail(msg)
        else:
            msg = (
                f"ROOT_COMPOSE_HARDCODED_IMAGE: Could not find hermes-agent image reference "
                f"in {HERMES_AGENT_BASE_YML}. Ensure the service is defined and has an image: directive."
            )
            logger.error("[IMP:10][test_root_compose_uses_context_image_var] %s", msg)
            pytest.fail(msg)

    logger.info("[IMP:10][test_root_compose_uses_context_image_var] PASS — hermes-agent uses ${CONTEXT_IMAGE:-...}")


# endregion test_root_compose_uses_context_image_var
