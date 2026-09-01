"""
# GREP_SUMMARY: test_deploy_context_cli, deploy-context, local-mode, node-passthrough, context-deployer, subprocess, cache-drill-fix, --node
# STRUCTURE: ▶ tmp_path + node.yaml + monkeypatch subprocess.run → ◇ local mode → ⊕ captured cmd → ◇ assert --node/--context contract → ⎋ LDD trajectory IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy_context_cli.py local mode — node passthrough to
##           context_deployer.py subprocess (cache-drill fix 2026-09-01).
## @scope    Tests local-mode subprocess cmd construction: --node (НЕ пробрасывался — потеря
##           node-имени на VPS, vhost-рендер в некорректный путь) и --context passthrough.
## @invariants
##   - subprocess.run monkeypatched (никаких реальных запусков context_deployer.py)
##   - node.yaml создаётся в tmp_path (Zero Hardcode)
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory
## @rationale standalone deploy-context терял node (cache-drill прогон 2026-09-01):
##            local cmd = [python, context_deployer.py, --node-yaml, ...] БЕЗ --node →
##            node_name="" → _step_vhosts рендерил в {NODE_CONFIGS_DIR}/overlays/nginx.
##            Регрессионный guard на cmd-контракт local-режима.
## @changes  2026-09-01 | cache-drill fix — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import deploy_context_cli

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path):
    """Create a minimal node.yaml with node.name and contexts[0].name."""
    yaml_content = """\
node:
  name: test-node
contexts:
  - name: test-ctx
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return str(yaml_path)


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: local-mode cmd contract (cache-drill fix 2026-09-01)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · local mode passes --node to context_deployer.py
# · Scenario: NODE= задан + --local + реальный node.yaml → subprocess cmd содержит --node <name>
# · Last fail: standalone deploy-context на VPS — cmd без --node → node_name="" → vhost-рендер
# ·   в {NODE_CONFIGS_DIR}/overlays/nginx (cache-drill прогон 2026-09-01)
# · Remove if: local subprocess delegation removed
@ldd_trajectory
def test_cli_local_passes_node_to_deployer(caplog, node_yaml_file, monkeypatch):
    """deploy_context_cli local mode: subprocess cmd must contain --node <name>."""
    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(deploy_context_cli.subprocess, "run", fake_run)
    rc = deploy_context_cli.main(["--node", "tronyx-vps", "--local", "--node-yaml", node_yaml_file])
    assert rc == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list), f"cmd must be a list, got {type(cmd)}"
    assert "--node" in cmd, f"--node must be passed to context_deployer.py, got {cmd}"
    assert cmd[cmd.index("--node") + 1] == "tronyx-vps", f"expected node value, got {cmd}"
    logger.critical("[IMP:9][test] deploy-context local cmd passes --node to deployer — OK")


# 🧪 TRAP[TEST] · Regression · local mode without NODE → no --node in cmd (resolved on deployer side)
# · Scenario: --node НЕ задан, --local, реальный node.yaml → cmd НЕ содержит --node (deployer
# ·   резолвит node из node.yaml#node.name или fail-fast IMP:10)
# · Last fail: N/A (new guard)
# · Remove if: --node optionality changes
@ldd_trajectory
def test_cli_local_without_node_omits_flag(caplog, node_yaml_file, monkeypatch):
    """deploy_context_cli local mode without NODE → no --node flag in cmd."""
    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(deploy_context_cli.subprocess, "run", fake_run)
    rc = deploy_context_cli.main(["--local", "--node-yaml", node_yaml_file])
    assert rc == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list), f"cmd must be a list, got {type(cmd)}"
    assert "--node" not in cmd, f"--node must be omitted when NODE empty, got {cmd}"
    logger.critical("[IMP:9][test] deploy-context local cmd omits --node when empty — OK")


# 🧪 TRAP[TEST] · Regression · local mode passes --context when CONTEXT= given
# · Scenario: CONTEXT= задан + --local → subprocess cmd содержит --context <ctx>
# · Last fail: N/A (new guard — cmd-контракт контекста)
# · Remove if: context passthrough removed
@ldd_trajectory
def test_cli_local_passes_context_to_deployer(caplog, node_yaml_file, monkeypatch):
    """deploy_context_cli local mode: subprocess cmd must contain --context <ctx>."""
    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(deploy_context_cli.subprocess, "run", fake_run)
    rc = deploy_context_cli.main([
        "--node",
        "tronyx-vps",
        "--local",
        "--node-yaml",
        node_yaml_file,
        "--context",
        "my-ctx",
    ])
    assert rc == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list), f"cmd must be a list, got {type(cmd)}"
    assert "--context" in cmd, f"--context must be passed to context_deployer.py, got {cmd}"
    assert cmd[cmd.index("--context") + 1] == "my-ctx", f"expected context value, got {cmd}"
    logger.critical("[IMP:9][test] deploy-context local cmd passes --context to deployer — OK")


# endregion Tests: local-mode cmd contract
