# GREP_SUMMARY: gate compose-profiles consistency COMPOSE_PROFILES mismatch drift callsites
# STRUCTURE: ┌fixture: canonical profiles from make┐ → ◇ test: cross-check 7 callsites → ⎋ assert all match
# region MODULE_CONTRACT
## @purpose — Gate: verify COMPOSE_PROFILES list is identical across all callsites
##            (Makefile, shell scripts, CI YAML workflows, Python code).
##            Catches drift when Docker modules are added/removed without updating
##            all locations. Read-only gate — does NOT modify any production code.
## @scope — 7 files: Makefile, deploy-project.sh, adopt-project.sh, push-gate.yml,
##          platform-test.yml, docker_orchestrator.py, helpers.mk (_get_all_profiles)
## @invariants
##   - Canonical value obtained from `make _get_all_profiles` (single source of truth)
##   - All extractors are read-only — no file modifications
##   - Test is marked @pytest.mark.gate — runs in `make gate MODE=fast`
##   - On mismatch, test fails with exact file:line guidance for developer
## @rationale — MISMATCH-1 from VerificationReport-postfix (Wave 3).
##              Consistency gate chosen over deduplication (Option A over B/C/D):
##              LOW severity + zero regression risk + fail-fast detection.
##              If module count changes >=2 times by 2026-10-22, reconsider B/D.
## @changes — 2026-07-22 | Created per 037-DevPlan GOAL_MISMATCH
# endregion MODULE_CONTRACT

import os
import re
import subprocess
from pathlib import Path

import pytest

# === Constants ===

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# === Helpers ===


def _get_canonical_profiles() -> str:
    """Get canonical COMPOSE_PROFILES from `make _get_all_profiles`.

    ▶ subprocess.run(make _get_all_profiles) → stdout.strip → ⎋ str
    """
    result = subprocess.run(
        ["make", "_get_all_profiles"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(PROJECT_ROOT),
        env={**os.environ},
    )
    if result.returncode != 0:
        pytest.fail(f"make _get_all_profiles failed (exit {result.returncode}): {result.stderr.strip()}")
    # GNU Make ≥4.x outputs make[1]: Entering/Leaving directory messages to stdout.
    # Strip these to get only the profile string.
    lines = result.stdout.strip().splitlines()
    profile_lines = [
        line
        for line in lines
        if not line.startswith("make[") and "Entering directory" not in line and "Leaving directory" not in line
    ]
    return "".join(profile_lines).strip()


def _extract_makefile_value(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from Makefile export line.

    ⚡ regex: export COMPOSE_PROFILES \\?= (.+) → ⎋ value
    """
    content = filepath.read_text()
    m = re.search(r"export COMPOSE_PROFILES \?= (.+)", content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES export found in {filepath}")
    return m.group(1).strip()


def _extract_shell_default(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from shell ${COMPOSE_PROFILES:-...} pattern.

    ⚡ regex: COMPOSE_PROFILES="${COMPOSE_PROFILES:-(.+?)}" → ⎋ default value
    """
    content = filepath.read_text()
    m = re.search(r'COMPOSE_PROFILES[=:][\'"]?\$\{COMPOSE_PROFILES:-(.+?)\}[\'"]?', content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES default found in {filepath}")
    return m.group(1).strip()


def _extract_ci_workflow_value(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from GitHub Actions workflow env section.

    ⚡ regex: COMPOSE_PROFILES:\\s*"(.+?)" → ⎋ value
    """
    content = filepath.read_text()
    m = re.search(r'COMPOSE_PROFILES:\s*"(.+?)"', content)
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES found in {filepath}")
    return m.group(1).strip()


def _extract_python_setdefault(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from os.environ.setdefault() call.

    ⚡ multi-line regex: os.environ.setdefault("COMPOSE_PROFILES",\n"value") → ⎋ value
    """
    content = filepath.read_text()
    m = re.search(
        r'os\.environ\.setdefault\(\s*"COMPOSE_PROFILES",\s*\n\s*"(.+?)"',
        content,
        re.MULTILINE,
    )
    if not m:
        raise ValueError(f"No COMPOSE_PROFILES setdefault found in {filepath}")
    return m.group(1).strip()


def _extract_helpers_mk_value(filepath: Path) -> str:
    """Extract COMPOSE_PROFILES from helpers.mk _get_all_profiles @echo line.

    ⚡ regex: _get_all_profiles: + @echo "(.+?)" → ⎋ value
    """
    content = filepath.read_text()
    # Match _get_all_profiles target followed by @echo "..." (with tab)
    m = re.search(r'_get_all_profiles:\s+@echo "(.+?)"', content)
    if not m:
        raise ValueError(f"No _get_all_profiles @echo value found in {filepath}")
    return m.group(1).strip()


# === Fixtures ===


@pytest.fixture(scope="module")
def canonical_profiles() -> str:
    """Canonical COMPOSE_PROFILES from `make _get_all_profiles`.

    ◇ side effect: subprocess call to make → ⎋ canonical string
    """
    return _get_canonical_profiles()


# === CALLSITES ===
# region CALLSITES — each entry: (label, filepath, extractor_func)

CALLSITES: list[tuple[str, Path, callable]] = [
    (
        "Makefile:30",
        PROJECT_ROOT / "Makefile",
        _extract_makefile_value,
    ),
    (
        "deploy-project.sh:719",
        PROJECT_ROOT / "core/internal/deploy/deploy-project.sh",
        _extract_shell_default,
    ),
    (
        "adopt-project.sh:387",
        PROJECT_ROOT / "core/internal/scaffold/adopt-project.sh",
        _extract_shell_default,
    ),
    (
        "push-gate.yml:47",
        PROJECT_ROOT / ".github/workflows/push-gate.yml",
        _extract_ci_workflow_value,
    ),
    (
        "platform-test.yml:71",
        PROJECT_ROOT / ".github/workflows/platform-test.yml",
        _extract_ci_workflow_value,
    ),
    (
        "docker_orchestrator.py:455",
        PROJECT_ROOT / "core/internal/bootstrap/deploy/docker_orchestrator.py",
        _extract_python_setdefault,
    ),
    (
        "helpers.mk:78 (_get_all_profiles)",
        PROJECT_ROOT / "makefiles/helpers.mk",
        _extract_helpers_mk_value,
    ),
]

# endregion CALLSITES


# === Tests ===


# 🧪 TRAP[TEST] · Regression · Scenarios: AC-2 (consistency gate) · Last fail: 2026-07-22 · Remove if: COMPOSE_PROFILES centralized to single source
# · Check 7 callsites for identical 13-module COMPOSE_PROFILES list
# · Any mismatch = drift detection after adding/removing Docker modules
@pytest.mark.gate
def test_compose_profiles_consistency(canonical_profiles: str, caplog) -> None:
    """Verify COMPOSE_PROFILES is identical across all 7 callsites.

    ◇ canonical_profiles → ⚡ for each callsite → extract → compare
       → ∋ mismatch? → ⎋ fail with line-guidance | pass

    ## @purpose  Cross-check all COMPOSE_PROFILES definitions against canonical value.
    ##            Any mismatch means a developer added/removed a Docker module without
    ##            updating all 6+1 locations. Fail shows exact file:line guidance.
    ## @io        Input: canonical_profiles (str) from make _get_all_profiles
    ##            Output: pass or pytest.fail with per-callsite mismatch details
    ## @complexity O(N) where N = len(CALLSITES) = 7
    """
    import logging

    logger = logging.getLogger(__name__)

    mismatches: list[str] = []

    for label, filepath, extractor in CALLSITES:
        logger.info("[IMP:8][test_compose_profiles_consistency] Checking: %s (%s)", label, filepath)

        if not filepath.exists():
            mismatches.append(f"[{label}] File not found: {filepath}")
            logger.error("[IMP:4][test_compose_profiles_consistency] NOT FOUND: %s", filepath)
            continue

        try:
            value = extractor(filepath)
        except (ValueError, OSError) as exc:
            mismatches.append(f"[{label}] Extraction error: {exc}")
            logger.error("[IMP:4][test_compose_profiles_consistency] FAIL extraction: %s — %s", label, exc)
            continue

        if value != canonical_profiles:
            mismatches.append(
                f"[{label}] COMPOSE_PROFILES MISMATCH:\n"
                f"  expected (from make _get_all_profiles): {canonical_profiles}\n"
                f"  actual (in {filepath}):                 {value}"
            )
            logger.error("[IMP:4][test_compose_profiles_consistency] MISMATCH: %s", label)
        else:
            logger.info("[IMP:9][test_compose_profiles_consistency] ✅ %s: consistent", label)

    if mismatches:
        logger.error(
            "[IMP:10][test_compose_profiles_consistency] FAIL: %d callsite(s) out of sync",
            len(mismatches),
        )
        pytest.fail(
            f"COMPOSE_PROFILES inconsistency detected in {len(mismatches)} callsite(s):\n"
            + "\n".join(mismatches)
            + "\n\nCanonical value (from `make _get_all_profiles`): "
            + canonical_profiles
            + "\n\nUpdate ALL locations when adding/removing Docker modules."
        )

    logger.info(
        "[IMP:9][test_compose_profiles_consistency] ✅ All %d callsites consistent with canonical value: %s",
        len(CALLSITES),
        canonical_profiles,
    )
