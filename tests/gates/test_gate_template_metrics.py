# GREP_SUMMARY: gate template metrics port endpoint consistency gen_ai_platform_yaml runtime metrics_port
# STRUCTURE: ▶ ┌gen_ai_platform_yaml (runtime SoT)┐ → ◇ metrics: true? → ◇ assert metrics_port present → ◇ assert /metrics endpoint → ◇ check port consistency
# region MODULE_CONTRACT
## @purpose — Gate test A2 (DevPlan 141 runtime-модель): для каждого типа шаблона с metrics: true:
##            1. A metrics_port field in ai-platform.yaml (сгенерированного gen_ai_platform_yaml)
##            2. A /metrics endpoint in src/main.py (if src/main.py exists)
##            3. Consistent port between ai-platform.yaml and src/main.py (if port specified in main.py)
## @scope — Runtime-валидация: ai-platform.yaml больше НЕ хранится в шаблонах (DevPlan 141 W1),
##          генерируется gen_ai_platform_yaml при scaffold — гейт проверяет генератор (SoT).
## @invariants
##   - templates with metrics: false are skipped entirely
##   - Missing src/main.py logs IMP:7 skip, not FAIL
##   - Port consistency is checked when main.py contains explicit port reference
## @rationale — Post-refactoring audit C4: templates must have consistent metrics configuration.
##              DevPlan 141: статический источник (templates/*/ai-platform.yaml) заменён runtime-
##              генерацией — шаблоны не несут манифест, генератор — единственный SoT.
## @changes — 2026-07-12 | Created per 004-automation-plan TASK-2
## @changes — 2026-08-06 | DevPlan 141 W1 — runtime-источник через gen_ai_platform_yaml
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

# Типы шаблонов платформы (scaffold: --template backend|frontend). Источник конфига — генератор.
_TEMPLATE_TYPES: tuple[str, ...] = ("backend", "frontend")


@pytest.fixture()
def template_metrics_configs(tmp_path: pathlib.Path) -> list[tuple[str, pathlib.Path, dict]]:
    """Сгенерировать ai-platform.yaml для каждого типа шаблона через gen_ai_platform_yaml.

    ## @purpose — Runtime SoT (DevPlan 141): шаблоны не хранят ai-platform.yaml;
    ##            гейт валидирует output генератора для каждого типа шаблона.
    ## @io — ⎋ list[tuple[name, yaml_path, parsed_dict]] для типов с metrics enabled
    ## @complexity — O(N) где N = типы шаблонов
    """
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    results: list[tuple[str, pathlib.Path, dict]] = []
    for ptype in _TEMPLATE_TYPES:
        name = f"template-{ptype}"
        yaml_path = tmp_path / name / "ai-platform.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        gen_ai_platform_yaml(
            name=f"test-{ptype}",
            ptype=ptype,
            org="tronyx161",
            node="test-node",
            domain="test.local",
            database="",
            mode="",
            output_path=str(yaml_path),
        )
        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        monitoring = data.get("monitoring", {}) or {}
        if monitoring.get("metrics") is True:
            results.append((name, yaml_path, data))
            logger.info(
                "[IMP:8][_find_template_yamls] Template '%s' has metrics: true — queued for validation",
                name,
            )
        else:
            logger.info(
                "[IMP:8][_find_template_yamls] Template '%s' has metrics: false — skipped",
                name,
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
def test_templates_metrics_port_present(caplog, template_metrics_configs) -> None:
    """Verify every template with metrics: true has metrics_port.

    ## @purpose — For each generated template config where monitoring.metrics is true,
    ##            assert that monitoring.metrics_port is present and is a positive integer.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_port_present] === Metrics port audit ===")

    templates = template_metrics_configs
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
def test_templates_metrics_endpoint_in_main(caplog, template_metrics_configs) -> None:
    """Verify templates with metrics: true have /metrics endpoint in src/main.py.

    ## @purpose — For each template with metrics: true, check that src/main.py (if exists)
    ##            contains a /metrics endpoint definition. If src/main.py doesn't exist,
    ##            log IMP:7 skip (normal for templates using shared Dockerfile).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_endpoint_in_main] === Metrics endpoint audit ===")

    templates = template_metrics_configs
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
def test_templates_metrics_port_consistency(caplog, template_metrics_configs) -> None:
    """Verify port in ai-platform.yaml matches port reference in src/main.py.

    ## @purpose — For each template with metrics: true, compare metrics_port from
    ##            ai-platform.yaml with any explicit port reference in src/main.py
    ##            (e.g., app.run(host=..., port=8080) or uvicorn.run(..., port=8080)).
    ##            If main.py doesn't exist or has no explicit port, skip with IMP:7.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(N) where N = templates with metrics: true
    """
    logger.info("[IMP:8][test_templates_metrics_port_consistency] === Port consistency audit ===")

    templates = template_metrics_configs
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
