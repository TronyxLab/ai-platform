#!/usr/bin/env python3
# GREP_SUMMARY: monitoring loki-retention runtime-config retention-stream idempotent catch-all selector
# STRUCTURE: ▶ update_loki_retention(config, runtime_config_path) → ◇ parse retention → ◇ selector exists? → ◇ insert before catch-all → ⊕ yaml.dump → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Loki runtime config retention stream generator — extracted from
##           monitoring_config_renderer.py (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Retention is always applied (no flag gate — Loki retention is universal)
##   - Idempotent: if selector for this project already exists → status="skipped"
##   - New rules inserted before catch-all (compose_project=~) rules
##   - Missing runtime config file → created with just this project's stream
##   - Non-fatal: YAML/dict errors logged, continue
## @rationale  DevPlan 117 G T54 — extracted verbatim (update_loki_retention, ~79 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path
from typing import cast

import yaml

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
        load_yaml_config,
    )
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        parse_retention_hours as _parse_retention_hours,
    )
    from monitoring.constants import DEFAULT_LOKI_RUNTIME_CONFIG  # pyright: ignore[reportImplicitRelativeImport]
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    # W2 T2.6 (DevPlan 136, латентный класс A): канон config_renderer.py — корень репо
    # (fallback добавляет И core/internal/ для top-level monitoring-импортов, И корень
    # для core.internal.* — единый документированный канон self-bootstrap).
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
        load_yaml_config,
    )
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        parse_retention_hours as _parse_retention_hours,
    )
    from monitoring.constants import DEFAULT_LOKI_RUNTIME_CONFIG  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)


# region FUNC_update_loki_retention
def update_loki_retention(
    config: ProjectMonitoringConfig,
    runtime_config_path: Path | None = None,
) -> RenderResult:
    """Update Loki runtime config YAML with project retention stream.

    ## @purpose  Idempotently add or verify a retention stream rule for the project
    ##           in Loki's runtime config YAML. New rules are inserted BEFORE any
    ##           catch-all rules (selectors containing 'compose_project=~').
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ runtime_config_path: Path — Loki runtime config path (default: platform-relative)
    ##   ⎋ RenderResult — outcome: "updated", "skipped" (exists), "failed", "noop"
    ## @complexity O(S) where S = number of existing retention streams
    ## @invariants
    ##   - Retention is always applied (no flag gate — Loki retention is universal)
    ##   - Idempotent: if selector for this project already exists → status="skipped"
    ##   - New rules inserted before catch-all (compose_project=~) rules
    ##   - Missing runtime config file → created with just this project's stream
    ##   - Non-fatal: YAML/dict errors logged, continue
    """
    retention_hours = _parse_retention_hours(config.logs_retention)
    config_path = runtime_config_path or (config.platform_root / DEFAULT_LOKI_RUNTIME_CONFIG)
    project = config.project_name

    # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
    try:
        # Load existing config or start fresh
        existing = load_yaml_config(config_path)

        # W11: вложенные setdefault-цепочки → object-граница (dict[str, object] + явные сужения)
        limits_raw = existing.get("limits_config")
        limits = cast("dict[str, object]", limits_raw) if isinstance(limits_raw, dict) else None
        if limits is None:
            limits = cast("dict[str, object]", {})
            existing["limits_config"] = limits
        streams_raw = limits.get("retention_stream")
        streams: list[object]
        if isinstance(streams_raw, list):
            streams = cast("list[object]", streams_raw)
        else:
            streams = cast("list[object]", [])
            limits["retention_stream"] = streams

        # Check if selector already exists (idempotent)
        selector = '{compose_project="' + project + '"}'
        exists = any(
            isinstance(s, dict) and str(cast("dict[str, object]", s).get("selector", "")) == selector for s in streams
        )

        if exists:
            logger.info("[IMP:8][loki] Retention stream already exists for %s — skipping", project)
            return RenderResult(component="loki", status="skipped", detail=f"stream for {project} already exists")

        # Build new rule
        new_rule: dict[str, object] = {
            "selector": selector,
            "priority": 0,
            "period": str(retention_hours) + "h",
        }

        # Insert before catch-all (compose_project=~) rules
        inserted = False
        for i, s in enumerate(streams):
            if isinstance(s, dict) and "compose_project=~" in str(cast("dict[str, object]", s).get("selector", "")):
                streams.insert(i, new_rule)
                inserted = True
                break

        if not inserted:
            streams.append(new_rule)

        # Write back
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with Path(config_path).open("w", encoding="utf-8") as f:
            yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        logger.info(
            "[IMP:9][loki] Loki runtime config updated for %s: %s (%dh)",
            project,
            config.logs_retention,
            retention_hours,
        )
        return RenderResult(
            component="loki", status="updated", detail=f"retention={config.logs_retention} ({retention_hours}h)"
        )
    except (OSError, yaml.YAMLError) as e:
        logger.info("[IMP:6][loki] Failed to update Loki retention for %s: %s", project, e)
        return RenderResult(component="loki", status="failed", detail=str(e))


# endregion FUNC_update_loki_retention
