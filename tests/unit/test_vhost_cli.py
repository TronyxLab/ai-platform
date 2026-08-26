"""
# GREP_SUMMARY: test_vhost_cli, platform-domain, resolution, fallback, CLI, env, node-yaml, F-10
# STRUCTURE: ▶ _resolve_platform_domain ┌cli_value/env/node/node_configs_dir┐ → ◇ CLI>env>node.yaml>None → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit-тесты резолва platform_domain в core/internal/scaffold/vhost_cli.py (F-10,
##           DevPlan 015): цепочка CLI arg > env PLATFORM_DOMAIN > node.yaml#domain > None.
## @scope    tests/unit (без Docker). Прямой вызов _resolve_platform_domain с tmp_path
##           фикстурным node.yaml (DI: env передаётся Mapping — нет скрытых os.environ).
## @invariants
##   - tmp_path (R1: no hardcoded paths)
##   - Каждый кейс резолва (CLI/env/node.yaml/None) покрыт
##   - @ldd_trajectory (IMP:9 assertion)
## @rationale DevPlan 015 F-10-test: резолв platform_domain (фикс F-10 в fde3fe8) не имел
##            unit-покрытия — регрессионная защита цепочки CLI>env>node.yaml>None.
## @changes 2026-08-27 | DevPlan 015 F-10-test — создан
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.scaffold.vhost_cli import _resolve_platform_domain
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


def _write_node_yaml(node_configs_dir: Path, node: str, domain: str) -> Path:
    """Создать фикстурный node.yaml с top-level domain (F-10 читает get("domain"))."""
    node_dir = node_configs_dir / node
    node_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_dir / "node.yaml"
    node_yaml.write_text(f"domain: {domain}\n", encoding="utf-8")
    return node_yaml


# 🧪 TRAP[TEST] · 2026-08-27 · F-10-test (P2) · цепочка резолва CLI>env>node.yaml>None
# · Regression: F-10 — platform_domain-резолв (фикс fde3fe8) без unit-покрытия; прежний код
# ·   читал env/CLI вперемешку и падал AttributeError на add/remove без --node
# · Last fail: session 014 — vhost-резолв не покрыт тестом (регрессионная дыра)
# · Remove if: _resolve_platform_domain цепочка меняется
@ldd_trajectory
def test_platform_domain_resolution_fallback(tmp_path: Path, caplog) -> None:
    """F-10: CLI arg > env PLATFORM_DOMAIN > node.yaml#domain > None (все 4 кейса)."""
    caplog.set_level(logging.INFO)
    node_configs_dir = tmp_path / "node-configs"
    _write_node_yaml(node_configs_dir, "test-node", "tronyx.ru")

    env: dict[str, str] = {}
    node = "test-node"
    ncd = str(node_configs_dir)

    # 1. CLI arg побеждает всё
    assert _resolve_platform_domain("cli.example.com", {"PLATFORM_DOMAIN": "env.example.com"}, node, ncd) == (
        "cli.example.com"
    )

    # 2. env PLATFORM_DOMAIN (CLI пуст)
    assert _resolve_platform_domain(None, {"PLATFORM_DOMAIN": "env.example.com"}, node, ncd) == "env.example.com"

    # 3. node.yaml#domain (CLI и env пусты)
    assert _resolve_platform_domain(None, env, node, ncd) == "tronyx.ru"

    # 4. None (CLI/env пусты, node не задан / node.yaml отсутствует)
    assert _resolve_platform_domain(None, env, None, ncd) is None, "без node → None (не AttributeError)"
    assert _resolve_platform_domain(None, env, "missing-node", ncd) is None, "нет node.yaml → None (не raise)"

    # 5. node.yaml без domain → None
    bare = tmp_path / "bare"
    (bare / "test-node").mkdir(parents=True)
    (bare / "test-node" / "node.yaml").write_text("node:\n  name: test-node\n", encoding="utf-8")
    assert _resolve_platform_domain(None, {}, "test-node", str(bare)) is None, "node.yaml без domain → None"

    logger.critical("[IMP:9][test][vhost_cli] F-10: цепочка CLI>env>node.yaml>None verified (5 кейсов)")
