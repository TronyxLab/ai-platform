# GREP_SUMMARY: gate template metrics port endpoint consistency ai-platform.yaml src/main.py metrics_port
# STRUCTURE: ┌glob templates/*/ai-platform.yaml┐ → ◇ metrics: true? → ◇ assert metrics_port present → ◇ assert /metrics endpoint → ◇ check port consistency
# region MODULE_CONTRACT
## @purpose — Gate test A2: validate that every project template with metrics: true has:
##            1. A metrics_port field in ai-platform.yaml
##            2. A /metrics endpoint in src/main.py (if src/main.py exists)
##            3. Consistent port between ai-platform.yaml and src/main.py (if port specified in main.py)
## @scope — Scans templates/*/ai-platform.yaml for monitoring.metrics flag, validates metrics_port,
##          and checks for /metrics endpoint in template source code.
## @invariants
##   - templates with metrics: false are skipped entirely
##   - Missing src/main.py logs IMP:7 skip, not FAIL
##   - Port consistency is checked when main.py contains explicit port reference
## @rationale — Post-refactoring audit C4: templates must have consistent metrics configuration
## @changes — 2026-07-12 | Created per 004-automation-plan TASK-2
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_TEMPLATES_DIR: pathlib.Path = _PROJECT_ROOT / "templates"

logger = logging.getLogger(__name__)


def _find_template_yamls() -> list[tuple[str, pathlib.Path, dict]]:
    """Find all templates/*/ai-platform.yaml with metrics: true.

    ## @purpose — Glob templates/*/ai-platform.yaml, parse YAML, collect those with metrics: true.
    ## @io — ⎋ list[tuple[name, yaml_path, parsed_dict]] for templates with metrics enabled
    ## @complexity — O(N) where N = template directories
    """
    results: list[tuple[str, pathlib.Path, dict]] = []
    if not _TEMPLATES_DIR.is_dir():
        logger.warning("[IMP:7][_find_template_yamls] Templates directory not found: %s", _TEMPLATES_DIR)
        return results

    for entry in sorted(_TEMPLATES_DIR.iterdir()):
        yaml_path = entry / "ai-platform.yaml"
        if not yaml_path.is_file():
            continue

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        # Check metrics flag
        monitoring = data.get("monitoring", {}) or {}
        if monitoring.get("metrics") is True:
            results.append((entry.name, yaml_path, data))
            logger.info(
                "[IMP:8][_find_template_yamls] Template '%s' has metrics: true — queued for validation",
                entry.name,
            )
        else:
            logger.info(
                "[IMP:8][_find_template_yamls] Template '%s' has metrics: false — skipped",
                entry.name,
            )

    logger.info("[IMP:8][_find_template_yamls] Found %d template(s) with metrics: true", len(results))
    return results


def _get_main_py_path(template_name: str) -> pathlib.Path | None:
    """Get path to src/main.py for a template, if it exists.

    ## @purpose — Check if templates/<name>/src/main.py exists.
    ## @io — ⎋ pathlib.Path | None
    ## @complexity — O(1)
    """
    main_py = _TEMPLATES_DIR / template_name / "src" / "main.py"
    if main_py.is_file():
        return main_py
    return None


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_templates_metrics_port_present(caplog) -> None:
    """Verify every template with metrics: true has metrics_port.

    ## @purpose — For each templates/*/ai-platform.yaml where monitoring.metrics is true,
    ##            assert that monitoring.metrics_port is present and is a positive integer.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_port_present] === Metrics port audit ===")

    templates = _find_template_yamls()
    assert len(templates) > 0, "No templates with metrics: true found — at least one expected"

    violations: list[str] = []
    for name, yaml_path, data in templates:
        monitoring = data.get("monitoring", {}) or {}
        metrics_port = monitoring.get("metrics_port")

        if metrics_port is None:
            violations.append(f"Template '{name}' ({yaml_path}): metrics: true but metrics_port is missing")
            logger.warning("[IMP:7] %s", violations[-1])
        elif not isinstance(metrics_port, int) or metrics_port <= 0:
            violations.append(f"Template '{name}' ({yaml_path}): metrics_port={metrics_port} is not a valid port")
            logger.warning("[IMP:7] %s", violations[-1])
        else:
            logger.info(
                "[IMP:8][test_templates_metrics_port_present] Template '%s': metrics_port=%d ✓",
                name,
                metrics_port,
            )

    if violations:
        logger.critical(
            "[IMP:9][test_templates_metrics_port_present] FAIL — %d template(s) missing metrics_port",
            len(violations),
        )
        pytest.fail("\n".join(violations))

    logger.critical(
        "[IMP:9][test_templates_metrics_port_present] PASS — all %d templates with metrics: true have metrics_port",
        len(templates),
    )


@pytest.mark.gate
@ldd_trajectory
def test_templates_metrics_endpoint_in_main(caplog) -> None:
    """Verify templates with metrics: true have /metrics endpoint in src/main.py.

    ## @purpose — For each template with metrics: true, check that src/main.py (if exists)
    ##            contains a /metrics endpoint definition. If src/main.py doesn't exist,
    ##            log IMP:7 skip (normal for templates using shared Dockerfile).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_endpoint_in_main] === Metrics endpoint audit ===")

    templates = _find_template_yamls()
    assert len(templates) > 0, "No templates with metrics: true found — at least one expected"

    violations: list[str] = []
    skipped: list[str] = []

    for name, _yaml_path, _data in templates:
        main_py = _get_main_py_path(name)
        if main_py is None:
            skipped.append(name)
            logger.info(
                "[IMP:7][test_templates_metrics_endpoint_in_main] Template '%s': no src/main.py — "
                "skipping endpoint check (shared Dockerfile pattern)",
                name,
            )
            continue

        with open(main_py) as f:
            content = f.read()

        # Check for /metrics endpoint definition
        has_metrics_endpoint = bool(
            re.search(
                r'@(app|router)\.(get|route)\s*\(\s*["\']/metrics["\']',
                content,
            )
        )

        if not has_metrics_endpoint:
            violations.append(f"Template '{name}' ({main_py}): metrics: true but no /metrics endpoint found in main.py")
            logger.warning("[IMP:7] %s", violations[-1])
        else:
            logger.info(
                "[IMP:8][test_templates_metrics_endpoint_in_main] Template '%s': /metrics endpoint found ✓",
                name,
            )

    if violations:
        logger.critical(
            "[IMP:9][test_templates_metrics_endpoint_in_main] FAIL — %d template(s) missing /metrics endpoint",
            len(violations),
        )

    if skipped:
        logger.info(
            "[IMP:8][test_templates_metrics_endpoint_in_main] %d template(s) skipped (no src/main.py): %s",
            len(skipped),
            ", ".join(skipped),
        )

    if violations:
        pytest.fail("\n".join(violations))

    logger.critical(
        "[IMP:9][test_templates_metrics_endpoint_in_main] PASS — all %d templates with src/main.py have /metrics endpoint, %d skipped",
        len(templates) - len(skipped),
        len(skipped),
    )


@pytest.mark.gate
@ldd_trajectory
def test_templates_metrics_port_consistency(caplog) -> None:
    """Verify port in ai-platform.yaml matches port reference in src/main.py.

    ## @purpose — For each template with metrics: true, compare metrics_port from
    ##            ai-platform.yaml with any explicit port reference in src/main.py
    ##            (e.g., app.run(host=..., port=8080) or uvicorn.run(..., port=8080)).
    ##            If main.py doesn't exist or has no explicit port, skip with IMP:7.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_port_consistency] === Port consistency audit ===")

    templates = _find_template_yamls()
    assert len(templates) > 0, "No templates with metrics: true found — at least one expected"

    violations: list[str] = []

    for name, _yaml_path, data in templates:
        monitoring = data.get("monitoring", {}) or {}
        yaml_port = monitoring.get("metrics_port")

        main_py = _get_main_py_path(name)
        if main_py is None:
            logger.info(
                "[IMP:7][test_templates_metrics_port_consistency] Template '%s': no src/main.py — skipping port consistency",
                name,
            )
            continue

        with open(main_py) as f:
            content = f.read()

        # Look for explicit port references in main.py
        # Patterns: port=XXXX, :XXXX (in uvicorn.run or app.run)
        port_matches = re.findall(r"port\s*=\s*(\d+)", content)

        if not port_matches:
            logger.info(
                "[IMP:7][test_templates_metrics_port_consistency] Template '%s': no explicit port in main.py — "
                "skipping consistency check (port from env var or default)",
                name,
            )
            continue

        main_py_port = int(port_matches[0])
        if yaml_port is not None and main_py_port != yaml_port:
            violations.append(
                f"Template '{name}': ai-platform.yaml port={yaml_port} but main.py port={main_py_port} (from {main_py})"
            )
            logger.warning("[IMP:7] %s", violations[-1])
        else:
            logger.info(
                "[IMP:8][test_templates_metrics_port_consistency] Template '%s': port %d consistent ✓",
                name,
                yaml_port,
            )

    if violations:
        logger.critical(
            "[IMP:9][test_templates_metrics_port_consistency] FAIL — %d port mismatch(es)",
            len(violations),
        )
        pytest.fail("\n".join(violations))

    logger.critical(
        "[IMP:9][test_templates_metrics_port_consistency] PASS — all template ports are consistent",
    )
