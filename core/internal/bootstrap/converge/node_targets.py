# !/usr/bin/env python3 — NOT a CLI entry (unit invoked from reconciler.py); converge.py — CLI
# GREP_SUMMARY: converge R11 prometheus node-targets file-sd single-node fallback reconcile monitoring
# STRUCTURE: ▶ reconcile_prometheus_node_targets ┌node_yaml+core_dir┐ → ◇ dry-run/report-only? → skipped
#            → render_node_targets_if_placement (multi-node placement | single-node fallback)
#            → ◇ nodes/*.json non-empty? → converged | warn → ⎋ report dict
# region MODULE_CONTRACT
## @purpose  R11 converge-юнит (018 W4, F-21c): прометей-таргеты ноды (file_sd nodes/*.json)
##           должны существовать ВСЕГДА — multi-node рендер по placement, single-node —
##           fallback Docker-DNS target'ы (байт-паритет прежней статике 010 T3.3).
##           Регрессия: wiring render-цепочки skipал single-node → job'ы node-exporter/
##           cadvisor/exporters выпали из скрейпа молча (F-21c root, TRAP[BUG] в
##           config_renderer.render_node_targets_if_placement). Converge — node-level
##           канал реконсилиации: идемпотентный рендер на каждом converge.
## @scope    Node-level converge (root): пишет /opt/platform/prometheus-targets/nodes/*.json
##           (2775 root:platform — receive-канал совместим). НЕ перезапускает prometheus:
##           file_sd refresh_interval 30s подхватывает файлы сам.
## @invariants
##   - Идемпотентность: generate_node_targets пишет ТОЛЬКО при изменении содержимого
##   - dry-run/report-only: 0 мутаций (report skipped)
##   - Post-condition честный: nodes/node-exporter.json существует после рендера
##     (при резолвимом node.yaml) — иначе warn + exit 1, не «converged» вслепую
##   - Non-fatal домен: сбой рендера = warn (exit 1), не ломает остальные R-юниты
## @rationale Q: почему converge, а не per-project render-цепочка? A: node targets —
##            node-level артефакт; цепочка запускается только для проектов с
##            monitoring-секцией (single-node без таких проектов никогда не рендерит —
##            корень F-21c). Converge идемпотентен и каноничен для node state.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from typing import cast

from core.internal.bootstrap.converge.infra import report_add, set_exit

logger = logging.getLogger(__name__)

_TARGETS_SUBDIR = Path("prometheus-targets") / "nodes"
_SENTINEL_TARGET_FILE = "node-exporter.json"  # all-nodes job — обязан быть в любом рендере


# region FUNC_reconcile_prometheus_node_targets
def reconcile_prometheus_node_targets(
    node_yaml_path: str,
    core_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """R11: render prometheus node targets (multi-node placement | single-node fallback).

    ▶ ┌node_yaml_path + core_dir┐ → ◇ dry-run? → skipped → render (idempotent)
    → ◇ nodes/node-exporter.json существует? → converged | warn(+exit 1) → ⎋ report dict

    ## @io       ⇥ node_yaml_path: str — резолвленный node.yaml (converge 3-path канон)
    ##           ⇥ core_dir: str — <platform_root>/core (platform_root = parent)
    ##           ⎋ dict[str, str] — {status: converged|warn|skipped}
    ## @complexity O(J) — 8 file_sd jobs, byte-skip на идентичном содержимом
    ## @invariants
    ##   - dry_run/report_only → report skipped, 0 файловых мутаций
    ##   - fail-loud пост-условие: sentinel-файл отсутствует после рендера → warn (exit 1)
    """
    unit = "R11"
    platform_root = Path(core_dir).parent
    if dry_run or report_only:
        logger.info("[IMP:8][converge][%s] dry-run/report-only — render skipped", unit)
        report_add(unit, "skipped", "dry-run/report-only — no mutation")
        return {"status": "skipped"}

    from core.internal.monitoring.config_renderer import render_node_targets_if_placement

    render_node_targets_if_placement(platform_root=platform_root, node_yaml_path=node_yaml_path)

    sentinel = platform_root / _TARGETS_SUBDIR / _SENTINEL_TARGET_FILE
    if not sentinel.is_file():
        msg = f"node targets render produced no {sentinel} (node.yaml резолвим? NODE_YAML/конфиг ноды?)"
        logger.warning("[IMP:8][converge][%s] %s", unit, msg)
        report_add(unit, "warn", msg)
        set_exit(1)
        return cast("dict[str, str]", {"status": "warn"})

    logger.info("[IMP:9][converge][%s] node targets converged: %s", unit, sentinel)
    report_add(unit, "converged", f"prometheus node targets present ({_TARGETS_SUBDIR}/)")
    return cast("dict[str, str]", {"status": "converged"})


# endregion FUNC_reconcile_prometheus_node_targets
