# GREP_SUMMARY: test context_initializer scaffold context hermes-agent node-configs skeleton idempotent registration
# STRUCTURE: ┌fixture setup┐ → ○ 8 tests → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
# region MODULE_CONTRACT
## @purpose  Unit-тесты context_initializer.py: создание директорий, skeleton node.yaml,
##           идемпотентность, graceful degradation при отсутствии org, регистрация в platform node.yaml.
##           LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    Tests under tests/ (unit, no Docker). DI over Mocks для gh subprocess и context_registry.
## @invariants
##   - Все тесты используют tmp_path (R1: No hardcoded paths)
##   - gh_runner и git_runner внедряются как callable (DI)
##   - context_registry.register_context mock для теста регистрации (исключение:
##     test_register_in_platform_yaml — реальный путь записи в YAML + idempotency)
##   - Все тесты с @ldd_trajectory декоратором
##   - R1-R5 compliance
## @rationale AC4: 5 unit-тестов на context_initializer.py согласно DevPlan 092 §4.
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Дедупликация: unit-версия тестов удалена (import file mismatch)
## · Rejected: оставить tests/unit/test_context_initializer.py (риск: pytest import file mismatch —
##   одинаковый basename с tests/test_context_initializer.py ломает collection всего сьюта)
## · Reason: корневая версия каноническая (в test_inventory.yaml); уникальный сценарий
##   test_register_in_platform_yaml (реальная регистрация, не mock) перенесён сюда.
## · Rev: если unit-директория вернётся к полному покрытию — ресинхронизировать inventory.
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
## @changes 2026-07-31 · Dedup fix — test_register_in_platform_yaml перенесён из tests/unit/
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

from core.internal.scaffold.context_initializer import (
    check_idempotent,
    create_dirs,
    create_skeleton_node_yaml,
    gh_repo_create,
    register_in_platform_yaml,
    validate_name,
)


@ldd_trajectory
def test_create_dirs(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "test-context"
    logger.info("[IMP:9][test][context] test_create_dirs — creating %s", context_dir)
    create_dirs(context_dir)
    assert context_dir.exists(), f"Context dir not created: {context_dir}"
    assert (context_dir / "hermes-agent").exists(), "hermes-agent/ not created"
    assert (context_dir / "node-configs").exists(), "node-configs/ not created"
    assert (context_dir / "hermes-agent").is_dir(), "hermes-agent/ is not a directory"
    assert (context_dir / "node-configs").is_dir(), "node-configs/ is not a directory"


@ldd_trajectory
def test_create_skeleton_node_yaml(tmp_path: pathlib.Path, caplog) -> None:
    skeleton_path = tmp_path / "node-configs" / "tronyx-vps" / "node.yaml"
    context_name = "test-ctx"
    logger.info("[IMP:9][test][context] test_create_skeleton_node_yaml — %s", skeleton_path)
    create_skeleton_node_yaml(skeleton_path, context_name)
    assert skeleton_path.exists(), f"Skeleton node.yaml not created: {skeleton_path}"
    content = skeleton_path.read_text()
    assert "GREP_SUMMARY:" in content, "Missing GREP_SUMMARY in skeleton"
    assert "STRUCTURE:" in content, "Missing STRUCTURE in skeleton"
    assert f"context: {context_name}" in content, f"Missing 'context: {context_name}' in skeleton"
    assert "node:" in content, "Missing 'node:' section"
    assert "modules:" in content, "Missing 'modules:' section"
    assert "projects:" in content, "Missing 'projects:' section"


@ldd_trajectory
def test_existing_context_idempotent(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "existing-context"
    context_dir.mkdir(parents=True)
    (context_dir / "README.md").write_text("# existing")
    logger.info("[IMP:9][test][context] test_existing_context_idempotent — should SKIP")
    with pytest.raises(SystemExit) as exc_info:
        check_idempotent(context_dir)
    assert exc_info.value.code == 0, f"Expected exit code 0, got {exc_info.value.code}"
    idem_logs = [r for r in caplog.records if "SKIP" in r.message or "idempotent" in r.message.lower()]
    assert len(idem_logs) >= 1, f"Expected SKIP/idempotent log, got {len(idem_logs)}"


@ldd_trajectory
def test_gh_repo_create_mocked(tmp_path: pathlib.Path, caplog) -> None:
    def gh_not_found(cmd: list[str]) -> tuple[int, str, str]:
        return -1, "", "gh: command not found"

    logger.info("[IMP:9][test][context] test_gh_repo_create_mocked — gh not found")
    node_repo, agent_repo, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=False,
        context_dir=tmp_path,
        gh_runner=gh_not_found,
    )
    assert node_repo is None, f"Expected None node_repo, got {node_repo}"
    assert agent_repo is None, f"Expected None agent_repo, got {agent_repo}"
    assert warnings >= 1, f"Expected at least 1 warning, got {warnings}"


@ldd_trajectory
def test_gh_repo_create_skip_flag(tmp_path: pathlib.Path, caplog) -> None:
    call_count = [0]

    def counting_gh_runner(cmd: list[str]) -> tuple[int, str, str]:
        call_count[0] += 1
        return 0, "ok", ""

    logger.info("[IMP:9][test][context] test_gh_repo_create_skip_flag — skip")
    node_repo, agent_repo, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=True,
        context_dir=tmp_path,
        gh_runner=counting_gh_runner,
    )
    assert call_count[0] == 0, f"gh_runner called {call_count[0]} times despite skip=True"
    assert node_repo is None
    assert agent_repo is None
    assert warnings == 0, f"Expected 0 warnings with skip, got {warnings}"


@ldd_trajectory
def test_register_in_platform_yaml_mocked(tmp_path: pathlib.Path, monkeypatch, caplog) -> None:
    import yaml as _yaml

    platform_yaml = tmp_path / "platform-node.yaml"
    platform_yaml.write_text(_yaml.dump({"contexts": []}, default_flow_style=False))
    import core.internal.scaffold.context_registry as cr_mod

    call_args = []

    def mock_register(yaml_path, name, desc="", node_cfg_repo="", hermes_agent_repo=""):
        call_args.append(
            {
                "yaml_path": yaml_path,
                "name": name,
                "desc": desc,
                "node_cfg_repo": node_cfg_repo,
                "hermes_agent_repo": hermes_agent_repo,
            }
        )
        return "OK"

    monkeypatch.setattr(cr_mod, "register_context", mock_register)
    logger.info("[IMP:9][test][context] test_register_in_platform_yaml_mocked")
    rc = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-ctx",
        ctx_desc="Test context",
        node_cfg_repo="org/node-configs",
        hermes_agent_repo="org/hermes-agent",
    )
    assert rc == 0, f"Expected return code 0, got {rc}"
    assert len(call_args) == 1, f"Expected register_context called once, called {len(call_args)}"
    assert call_args[0]["name"] == "test-ctx"
    assert call_args[0]["desc"] == "Test context"


@ldd_trajectory
def test_register_in_platform_yaml(tmp_path: pathlib.Path, caplog) -> None:
    """Real registration path (no context_registry mock): YAML updated + idempotent re-register.

    # 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_register_in_platform_yaml · Scenario: fresh node.yaml → context registered · Last fail: N/A · Remove if: initializer API changes
    ## @purpose — Real-path coverage of register_in_platform_yaml(): writes contexts[] entries
    ##            into the platform node.yaml and is idempotent (re-register keeps 1 entry).
    ##            Persisted from tests/unit/ during dedup (import file mismatch fix).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — two register calls + YAML parse
    """
    platform_yaml = tmp_path / "platform" / "node.yaml"
    platform_yaml.parent.mkdir(parents=True)
    platform_yaml.write_text(
        yaml.dump(
            {"node": {"name": "test-node", "host": "127.0.0.1"}, "contexts": [], "modules": [], "projects": []},
            default_flow_style=False,
            sort_keys=False,
        )
    )

    logger.info("[IMP:9][test][context] test_register_in_platform_yaml — real registration path")
    rc = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-context",
        ctx_desc="Test context for unit tests",
        node_cfg_repo="test-org/test-context-node-configs",
        hermes_agent_repo="test-org/test-context-hermes-agent",
    )
    assert rc == 0, f"Expected return code 0, got {rc}"

    data = yaml.safe_load(platform_yaml.read_text())
    contexts = data.get("contexts", [])
    assert len(contexts) == 1, f"Expected 1 context after register, got {len(contexts)}"
    ctx = contexts[0]
    assert ctx["name"] == "test-context"
    assert ctx["description"] == "Test context for unit tests"
    assert ctx["node_configs_repo"] == "test-org/test-context-node-configs"
    assert ctx["hermes_agent_repo"] == "test-org/test-context-hermes-agent"

    # Idempotent: register again → still 1 entry
    rc2 = register_in_platform_yaml(yaml_path=str(platform_yaml), ctx_name="test-context")
    assert rc2 == 0, f"Expected idempotent register rc 0, got {rc2}"
    data2 = yaml.safe_load(platform_yaml.read_text())
    assert len(data2.get("contexts", [])) == 1, "Re-register must not duplicate the context entry"


@ldd_trajectory
def test_validate_name_invalid(caplog) -> None:
    logger.info("[IMP:9][test][context] test_validate_name_invalid")
    with pytest.raises(SystemExit) as exc_info:
        validate_name("bad name!@#")
    assert exc_info.value.code == 1, f"Expected exit code 1 for invalid name, got {exc_info.value.code}"
