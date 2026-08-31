# GREP_SUMMARY: converge R11 node-targets prometheus file-sd single-node fallback dry-run postcondition
# STRUCTURE: ▶ test_r11_renders_single_node_fallback (converged + sentinel) → ▶ test_r11_dry_run_skipped (0 мутаций) → ▶ test_r11_postcondition_warn (нет node.yaml → warn + exit 1)
# region MODULE_CONTRACT
## @purpose  Unit-тесты R11 reconcile_prometheus_node_targets (018 W4, F-21c) — file_sd
##           nodes/*.json рендер: single-node fallback, dry-run skip, честный post-condition.
## @scope    tests/unit — без docker, tmp_path (Zero Hardcode), DI через временные деревья.
## @invariants
##   - tmp_path для всех файлов
##   - TRAP[TEST] на каждом тесте (Regression/Last fail/Remove if)
##   - infra.reset_state() перед каждым тестом (модульные глобалы report/exit)
## @rationale F-21c root: node-exporter job выпал из скрейпа single-node молчи — R11 закрывает
##            node-level реконсилиацией; тесты фиксируют контракты fallback/skip/warn.
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest

from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.converge.node_targets import reconcile_prometheus_node_targets

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _reset_infra_state():
    """Модульные глобалы infra (report/exit_code/has_*) чисты перед каждым тестом."""
    infra.reset_state()
    yield
    infra.reset_state()


def _make_single_node_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """node.yaml (без placement) + platform_root с core/ subdir (канон R11: platform_root=parent(core_dir))."""
    nc_root = tmp_path / "nc"
    node_dir = nc_root / "tronyx-vps"
    node_dir.mkdir(parents=True)
    (node_dir / "node.yaml").write_text("node:\n  name: tronyx-vps\ncontexts: [{name: ctx}]\n", encoding="utf-8")
    platform_root = tmp_path / "platform"
    (platform_root / "core").mkdir(parents=True)
    return node_dir / "node.yaml", platform_root


# 🧪 TRAP[TEST] · REGRESSION (R5) · 018 W4 (F-21c) — single-node fallback рендерится R11
# · Scenario: node.yaml есть, placement.yaml нет → R11 пишет nodes/node-exporter.json с
# ·   Docker-DNS target'ом (байт-паритет статике) → report converged
# · Last fail: F-21c — wiring skipал single-node → node-exporter job отсутствовал в
# ·   prometheus targets (проверено на ноде 2026-08-29: 0 файл(ов) в prometheus-targets/)
# · Remove if: node targets снова станут статикой в prometheus.yml.tmpl
def test_r11_renders_single_node_fallback(tmp_path) -> None:
    node_yaml, platform_root = _make_single_node_tree(tmp_path)

    report = reconcile_prometheus_node_targets(str(node_yaml), str(platform_root / "core"))

    sentinel = platform_root / "prometheus-targets" / "nodes" / "node-exporter.json"
    assert sentinel.is_file(), f"R11 обязан писать fallback targets: {sentinel}"
    assert report["status"] == "converged", f"converged ожидался: {report}"
    body = sentinel.read_text(encoding="utf-8")
    assert "node-exporter:9100" in body, f"Docker-DNS fallback target: {body}"
    logger.critical("[IMP:9][test] R11: single-node fallback отрендерен, sentinel на месте")


# 🧪 TRAP[TEST] · R11 dry-run/report-only — 0 файловых мутаций (контракт R-юнитов)
# · Scenario: dry_run=True → report skipped, nodes/ НЕ создаётся
# · Last fail: N/A (контракт)
# · Remove if: R11 перестанет быть мутацией (переедет в detect-only)
def test_r11_dry_run_skipped(tmp_path) -> None:
    node_yaml, platform_root = _make_single_node_tree(tmp_path)

    report = reconcile_prometheus_node_targets(str(node_yaml), str(platform_root / "core"), dry_run=True)

    assert report["status"] == "skipped", f"skipped ожидался: {report}"
    assert not (platform_root / "prometheus-targets").exists(), "dry-run не должен создавать targets/"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R11 post-condition — рендер без node.yaml → warn (fail-loud)
# · Scenario: node_yaml_path указывает на несуществующий файл → render skip (NODE_YAML
# ·   недоступен) → sentinel отсутствует → R11 ЧЕСТНО warn + set_exit(1), НЕ «converged»
# · Last fail: N/A (детектор добавлен вместе с юнитом; guard против silent-pass рендера)
# · Remove if: пост-условие R11 изменится (тогда warn-ветка станет недостижимой)
def test_r11_postcondition_warn_when_render_skipped(tmp_path) -> None:
    platform_root = tmp_path / "platform"
    (platform_root / "core").mkdir(parents=True)
    ghost_yaml = tmp_path / "missing" / "node.yaml"

    report = reconcile_prometheus_node_targets(str(ghost_yaml), str(platform_root / "core"))

    assert report["status"] == "warn", f"warn ожидался (post-condition): {report}"
    assert infra.exit_code >= 1, f"set_exit(1) обязан проставить exit_code: {infra.exit_code}"
    assert not (platform_root / "prometheus-targets" / "nodes").exists(), "silent-рендер без node.yaml запрещён"
    logger.critical("[IMP:9][test] R11: post-condition честный — без node.yaml → warn + exit 1")
