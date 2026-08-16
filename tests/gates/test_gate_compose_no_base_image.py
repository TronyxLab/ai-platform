# GREP_SUMMARY: gate compose no-base-image hermes-agent-base-nowhere context-image production anti-drift L1-collapse
# STRUCTURE: ┌ collect compose files ┐ → ◇ read + scan for hermes-agent-base → ⊕ violations → ∑ fail if any → ◇ base.yml CONTEXT_IMAGE var → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate test: ensure hermes-agent-base (L1) НЕ встречается НИГДЕ (DevPlan 002 L1→L2 коллапс) —
##           ни в production, ни в test/macos, ни в dev-оверрайдах (docker-compose.platform-dev.yml удалён).
##           docker-compose.base.yml hermes-agent должен использовать ${CONTEXT_IMAGE:-...} variable.
## @scope    Static file analysis — all docker-compose*.yml files at root and core/modules/*/
## @invariants
##   - hermes-agent-base НЕ встречается ни в одном compose файле (L1-образ не существует)
##   - docker-compose.platform-dev.yml удалён (T3.4) — никаких dev-оверрайдов L1
##   - hermes-agent image MUST use ${CONTEXT_IMAGE:-...} variable pattern
##   - Test (.test.yml) и macOS (.macos.yml) файлы сканируются тоже — hermes-agent-base banned everywhere
## @rationale  L1 (hermes-agent-base) схлопнут в L2 (hermes-agent-context) — единый образ собирается
##             из source. Любое упоминание hermes-agent-base = drift (L1-образ не существует,
##             bare-tag D18 невозможен). DevPlan 002 W5 T5.6.
## @changes    CREATED: 2026-07-09 | TASK-5G7
## @changes    2026-08-16 | DevPlan 002 W5 T5.6 — rewrite: «hermes-agent-base НИГДЕ» + base.yml CONTEXT_IMAGE var
##             (test_platform_dev_has_l1_image удалён — platform-dev.yml удалён)
# endregion MODULE_CONTRACT

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

# Image constants
HERMES_AGENT_BASE_IMAGE = "hermes-agent-base"
CONTEXT_IMAGE_VAR_PATTERN = re.compile(r"\$\{CONTEXT_IMAGE(?::-|:\?)[^}]+\}")
HERMES_AGENT_BASE_YML = "core/modules/hermes-agent/docker-compose.base.yml"


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


def _read_file(path: pathlib.Path) -> str:
    """Read file content.

    ## @purpose — Simple file read with explicit encoding.
    ## @io — ⇥ path: pathlib.Path → ⎋ str: file content
    ## @complexity — O(F) where F = file size in bytes
    """
    content = path.read_text(encoding="utf-8")
    logger.info("[IMP:8][_read_file] Read %s (%d bytes)", path.relative_to(PROJECT_ROOT), len(content))
    return content


# ── Test 1: hermes-agent-base banned EVERYWHERE (L1 коллапс) ──────────────────


# region test_no_base_image_anywhere
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
# 🧪 TRAP[TEST] · 2026-08-16 · DevPlan 002 W5 T5.6 — hermes-agent-base banned EVERYWHERE (L1 коллапс)
def test_no_base_image_anywhere(caplog) -> None:
    """Ensure NO compose file references hermes-agent-base (L1) — image doesn't exist after collapse.

    ## @purpose  Gate: L1 (hermes-agent-base) удалён (DevPlan 002) — любой compose-файл,
    ##            упоминающий его, — drift. FAIL code: BASE_IMAGE_FOUND.
    ## @io       ⎋ None — assert side-effect (pytest.fail on violations)
    ## @complexity O(N×L) where N = compose files, L = lines per file
    FAIL code: BASE_IMAGE_FOUND
    """
    logger.info("[IMP:9][test_no_base_image_anywhere] Collecting compose files...")

    all_files = _collect_compose_files()
    violations: list[tuple[str, int]] = []  # (relative path, line number)

    for fp in all_files:
        rel_path = str(fp.relative_to(PROJECT_ROOT))
        content = _read_file(fp)
        for line_no, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            # Сканируем ВСЕ строки (включая комментарии) — L1-образ не существует,
            # любое упоминание — антипаттерн (drift-детектор, не только image: директивы).
            if HERMES_AGENT_BASE_IMAGE in stripped:
                violations.append((rel_path, line_no))
                logger.warning(
                    "[IMP:8][test_no_base_image_anywhere] VIOLATION: %s:%d — %s",
                    rel_path,
                    line_no,
                    stripped,
                )

    if violations:
        msg_lines = [
            (
                f"BASE_IMAGE_FOUND: L1 '{HERMES_AGENT_BASE_IMAGE}' встречается в {len(violations)} compose-файлах "
                f"(DevPlan 002: L1 схлопнут в L2 — образ не существует):"
            )
        ]
        for path_, line_no in violations:
            msg_lines.append(f"  • {path_}:{line_no}")
        msg = "\n".join(msg_lines)
        logger.error("[IMP:10][test_no_base_image_anywhere] %s", msg)
        pytest.fail(msg)

    logger.info(
        "[IMP:10][test_no_base_image_anywhere] PASS — hermes-agent-base отсутствует во всех %d compose-файлах",
        len(all_files),
    )


# endregion test_no_base_image_anywhere


# ── Test 2: Root compose uses CONTEXT_IMAGE var ─────────────────────────────


# region test_root_compose_uses_context_image_var
# 2026-08-04 (DevPlan 129 W5, D13): include-архитектура = канон
# (root docker-compose.yml include:-based, образ hermes-agent определён в base.yml с
# CONTEXT_IMAGE var); тест адаптирован test_root_compose_uses_context_image_var ниже.
# Rev-условие снято: возврат к inline-сервисам в root compose запрещён инвариантом include-канона.


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

    lines = content.splitlines()
    found_context_var = False
    found_hardcoded = False
    hardcoded_line = None

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
