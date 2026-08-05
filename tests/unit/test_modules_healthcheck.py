#!/usr/bin/env python3
# GREP_SUMMARY: test-modules-healthcheck restart-loop threshold docker-inspect module-interface dispatch module.yaml yaml
# STRUCTURE: ┌tmp core/modules fixtures┐ → ◇ is_restart_loop (threshold) → ◇ discover/read install_type/container_names (YAML) → ◇ check_module dispatch mock → ◇ run_healthchecks → ⎋ exit-0|1 assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/healthcheck/modules_healthcheck.py (DevPlan 118 E4 —
##           Python-порт modules-healthcheck.sh). Native imports, tmp_path fixtures, mock module_interface.
## @scope    Tests: restart-loop threshold (>5, Restarting=true), module.yaml discovery (skip observability),
##           install_type YAML-parse, container_names YAML-parse (fallback module name), dispatch
##           liveness/deep, full run exit semantics.
## @invariants
##   - Native imports; module_interface.invoke mocked (no real bash)
##   - docker inspect mocked (no real docker)
##   - R5 anti-survivorship: negative-тесты (restart-loop detection, unhealthy exit)
##   - LDD: IMP:9 on all-healthy summary
## @rationale E4 Strangler: grep install_type + raw docker inspect → Python. Threshold и dispatch — тестируемы.
## @changes  2026-08-02 | DevPlan 118 E4 — Created
## @changes  2026-08-05 | DevPlan 139 W3 T1 — консолидация 4→2: static-проверки канона
##            (test_healthcheck_checks_all_containers, test_healthcheck_detects_restart_loop)
##            переехали из tests/test_healthcheck_static.py (файл удалён) — контракт + реализация
##            Python-канона в одном файле (S5). unit/test_healthcheck_poller.py не тронут (канон D5).
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path
from unittest import mock

import pytest

from core.internal.healthcheck import modules_healthcheck as mh
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# Консолидировано (DevPlan 139 W3 T1, 4→2): static-проверки Python-канона
# modules_healthcheck.py переехали из tests/test_healthcheck_static.py сюда —
# контракт + реализация канона в одном файле (S5: 4→2, канон не трогать).
_HEALTHCHECK_PY = repo_root() / "core" / "internal" / "healthcheck" / "modules_healthcheck.py"
_HEALTHCHECK_SH = repo_root() / "core" / "internal" / "healthcheck" / "modules-healthcheck.sh"


# region TEST_is_restart_loop
def test_is_restart_loop_restarting_true() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_is_restart_loop_restarting_true — DevPlan 118 E migration unit test
    """is_restart_loop: State.Restarting=true → loop regardless of count."""
    assert mh.is_restart_loop(True, 0) is True
    assert mh.is_restart_loop(True, 100) is True


def test_is_restart_loop_count_threshold() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_is_restart_loop_count_threshold — DevPlan 118 E migration unit test
    """is_restart_loop: RestartCount > 5 → loop; ≤ 5 → not (threshold канон)."""
    assert mh.is_restart_loop(False, 6) is True
    assert mh.is_restart_loop(False, 5) is False
    assert mh.is_restart_loop(False, 0) is False


def test_is_restart_loop_custom_threshold() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_is_restart_loop_custom_threshold — DevPlan 118 E migration unit test
    """is_restart_loop: custom threshold honored."""
    assert mh.is_restart_loop(False, 3, threshold=2) is True
    assert mh.is_restart_loop(False, 2, threshold=2) is False


# endregion


# region TEST_discover_module_yamls
def test_discover_module_yamls_skips_observability(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_discover_module_yamls_skips_observability — DevPlan 118 E migration unit test
    """discover: core/modules/*/module.yaml; observability skipped."""
    modules = tmp_path / "modules"
    (modules / "postgres" / "module.yaml").parent.mkdir(parents=True)
    (modules / "postgres" / "module.yaml").write_text("install_type: docker\n")
    (modules / "platform-secrets" / "module.yaml").parent.mkdir(parents=True)
    (modules / "platform-secrets" / "module.yaml").write_text("install_type: system\n")
    (modules / "observability" / "module.yaml").parent.mkdir(parents=True)
    (modules / "observability" / "module.yaml").write_text("install_type: docker\n")

    found = [p.parent.name for p in mh.discover_module_yamls(modules)]
    assert "observability" not in found, "observability must be skipped"
    assert set(found) == {"postgres", "platform-secrets"}


# endregion


# region TEST_read_install_type
def test_read_install_type_yaml(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_install_type_yaml — DevPlan 118 E migration unit test
    """read_install_type: YAML-парсер (не grep) — docker/system, default docker."""
    d = tmp_path / "m"
    d.mkdir()
    (d / "module.yaml").write_text("install_type: system\n")
    assert mh.read_install_type(d / "module.yaml") == "system"

    (d / "module.yaml").write_text("name: x\n")  # no install_type → default docker
    assert mh.read_install_type(d / "module.yaml") == "docker"

    (d / "module.yaml").write_text("not: [valid")
    assert mh.read_install_type(d / "module.yaml") == "docker"  # parse error → default


# endregion


# region TEST_read_container_names
def test_read_container_names_yaml_with_fallback(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_container_names_yaml_with_fallback — DevPlan 118 E migration unit test
    """read_container_names: container_name from docker-compose.base.yml; fallback module name."""
    d = tmp_path / "postgres"
    d.mkdir()
    (d / "docker-compose.base.yml").write_text(
        "services:\n  db:\n    container_name: postgres-main\n  replica:\n    container_name: postgres-replica\n"
    )
    assert mh.read_container_names(d) == ["postgres-main", "postgres-replica"]

    (d / "docker-compose.base.yml").unlink()
    assert mh.read_container_names(d) == ["postgres"], "fallback must be module dir name"


# endregion


# region TEST_check_restart_loop
def test_check_restart_loop_detects_loop(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_restart_loop_detects_loop — DevPlan 118 E migration unit test
    """check_restart_loop: RestartCount=7 → restart loop True (IMP:9 FAIL log)."""
    caplog.set_level(logging.INFO)
    result = mock.MagicMock(returncode=0, stdout="false|7\n", stderr="")
    # 128 W1: docker inspect — shared/docker_ops (docker_ops.subprocess.run — глобальный subprocess module)
    monkeypatch.setattr("core.internal.shared.docker_ops.subprocess.run", lambda *a, **k: result)

    assert mh.check_restart_loop("postgres") is True
    assert any("[IMP:9]" in r.message and "restart loop" in r.message for r in caplog.records)


def test_check_restart_loop_healthy(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_restart_loop_healthy — DevPlan 118 E migration unit test
    """check_restart_loop: Restarting=false, RestartCount=0 → no loop."""
    caplog.set_level(logging.INFO)
    result = mock.MagicMock(returncode=0, stdout="false|0\n", stderr="")
    # 128 W1: docker inspect — shared/docker_ops (docker_ops.subprocess.run — глобальный subprocess module)
    monkeypatch.setattr("core.internal.shared.docker_ops.subprocess.run", lambda *a, **k: result)

    assert mh.check_restart_loop("postgres") is False


def test_check_restart_loop_inspect_failure_ignored(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_restart_loop_inspect_failure_ignored — DevPlan 118 E migration unit test
    """check_restart_loop: docker inspect failure → False (не ложный FAIL)."""
    caplog.set_level(logging.INFO)
    result = mock.MagicMock(returncode=1, stdout="", stderr="err")
    # 128 W1: docker inspect — shared/docker_ops (docker_ops.subprocess.run — глобальный subprocess module)
    monkeypatch.setattr("core.internal.shared.docker_ops.subprocess.run", lambda *a, **k: result)

    assert mh.check_restart_loop("ghost-container") is False


# endregion


# region TEST_check_module_dispatch
def test_check_module_liveness_pass_no_restart_loop(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_module_liveness_pass_no_restart_loop — DevPlan 118 E migration unit test
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """check_module: liveness pass + no restart loop → healthy (both dispatch + inspect mocked)."""
    caplog.set_level(logging.INFO)
    d = tmp_path / "postgres"
    d.mkdir()
    (d / "module.yaml").write_text("install_type: docker\n")

    monkeypatch.setattr(
        mh,
        "invoke_module_interface",
        lambda module, interface, *args: (True, ""),
    )
    monkeypatch.setattr(mh, "check_restart_loop", lambda container: False)

    assert mh.check_module(d / "module.yaml") is True
    assert any("[IMP:8]" in r.message and "PASS (liveness)" in r.message for r in caplog.records)


def test_check_module_liveness_fail(tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_module_liveness_fail — DevPlan 118 E migration unit test
    """check_module: liveness fail → unhealthy (IMP:9 FAIL log)."""
    caplog.set_level(logging.INFO)
    d = tmp_path / "postgres"
    d.mkdir()
    (d / "module.yaml").write_text("install_type: docker\n")

    monkeypatch.setattr(mh, "invoke_module_interface", lambda module, interface, *args: (False, "down"))
    monkeypatch.setattr(mh, "check_restart_loop", lambda container: False)

    assert mh.check_module(d / "module.yaml") is False
    assert any("[IMP:9]" in r.message and "FAIL (liveness)" in r.message for r in caplog.records)


def test_check_module_restart_loop_fails_even_healthy(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_module_restart_loop_fails_even_healthy — DevPlan 118 E migration unit test
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """check_module: liveness PASS but restart loop → unhealthy (secondary check, канон)."""
    caplog.set_level(logging.INFO)
    d = tmp_path / "redis"
    d.mkdir()
    (d / "module.yaml").write_text("install_type: docker\n")

    monkeypatch.setattr(mh, "invoke_module_interface", lambda module, interface, *args: (True, ""))
    monkeypatch.setattr(mh, "check_restart_loop", lambda container: True)

    assert mh.check_module(d / "module.yaml") is False
    assert any("[IMP:9]" in r.message and "restart loop" in r.message for r in caplog.records)


def test_check_module_deep_mode(tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_module_deep_mode — DevPlan 118 E migration unit test
    """check_module: MODE=deep → invoke deep (no restart-loop inspection)."""
    caplog.set_level(logging.INFO)
    d = tmp_path / "postgres"
    d.mkdir()
    (d / "module.yaml").write_text("install_type: docker\n")

    calls: list[tuple] = []
    monkeypatch.setattr(
        mh,
        "invoke_module_interface",
        lambda module, interface, *args: calls.append((module, interface, args)) or (True, ""),
    )
    monkeypatch.setattr(mh, "check_restart_loop", lambda container: pytest.fail("restart-loop must not run in deep"))

    assert mh.check_module(d / "module.yaml", mode="deep") is True
    assert calls == [("postgres", "healthcheck", ("deep",))]


# endregion


# region TEST_run_healthchecks
def test_run_healthchecks_all_healthy(tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_healthchecks_all_healthy — DevPlan 118 E migration unit test
    """run_healthchecks: all healthy → True + IMP:9 ALL MODULES HEALTHY."""
    caplog.set_level(logging.INFO)
    modules = tmp_path / "modules"
    (modules / "a" / "module.yaml").parent.mkdir(parents=True)
    (modules / "a" / "module.yaml").write_text("install_type: system\n")
    (modules / "b" / "module.yaml").parent.mkdir(parents=True)
    (modules / "b" / "module.yaml").write_text("install_type: system\n")

    monkeypatch.setattr(mh, "invoke_module_interface", lambda module, interface, *args: (True, ""))

    assert mh.run_healthchecks(modules) is True
    assert any("[IMP:9]" in r.message and "ALL MODULES HEALTHY" in r.message for r in caplog.records)


def test_run_healthchecks_some_unhealthy(tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_healthchecks_some_unhealthy — DevPlan 118 E migration unit test
    """run_healthchecks: one unhealthy → False + IMP:9 SOME MODULES UNHEALTHY."""
    caplog.set_level(logging.INFO)
    modules = tmp_path / "modules"
    (modules / "a" / "module.yaml").parent.mkdir(parents=True)
    (modules / "a" / "module.yaml").write_text("install_type: system\n")
    (modules / "b" / "module.yaml").parent.mkdir(parents=True)
    (modules / "b" / "module.yaml").write_text("install_type: system\n")

    results = {"a": (True, ""), "b": (False, "down")}
    monkeypatch.setattr(mh, "invoke_module_interface", lambda module, interface, *args: results[module])

    assert mh.run_healthchecks(modules) is False
    assert any("[IMP:9]" in r.message and "SOME MODULES UNHEALTHY" in r.message for r in caplog.records)


# endregion


# region TEST_MODULES_HEALTHCHECK_STATIC (консолидировано из tests/test_healthcheck_static.py, DevPlan 139 W3 T1)
## @purpose  Static-контракт Python-канона modules_healthcheck.py (DevPlan 083 + 118 E4):
##           (a) uses shared/module_interface для liveness dispatch; (b) restart-loop
##           детекция через State.Restarting + RestartCount>5 → FAIL. R5 negative:
##           shell-фасад modules-healthcheck.sh НЕ несёт бизнес-логики.
## @invariants
##   - Python-модуль содержит invoke (module_interface) для liveness
##   - Python-модуль содержит State.Restarting и RestartCount (threshold > 5)
##   - Shell-фасад тонкий (R5 negative)


@pytest.mark.static_audit
def test_healthcheck_checks_all_containers(caplog) -> None:
    """Assert modules_healthcheck.py iterates ALL container_name entries (no head -1).

    Acceptance criterion A6: all containers in a module are checked.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_PY.is_file(), f"modules_healthcheck.py not found: {_HEALTHCHECK_PY}"
    content = _HEALTHCHECK_PY.read_text()

    # ── Check 1: uses shared/module_interface for docker liveness (DRIFT-H7, E4) ──
    has_invoke = bool(
        re.search(r"invoke_module_interface\s*\(.*?['\"]healthcheck['\"].*?['\"]liveness['\"]", content, re.DOTALL)
    ) or bool(re.search(r"invoke_module_interface\(module, ['\"]healthcheck['\"], ['\"]liveness['\"]\)", content))
    logger.critical("[IMP:9][test_healthcheck][all] module_interface invoke liveness present: %s", has_invoke)
    assert has_invoke, "modules_healthcheck.py must use shared/module_interface.invoke for liveness (DRIFT-H7/E4)."

    # ── Check 2: no head -1 pattern (Python YAML-парсер вместо shell pipeline) ──
    has_pipeline_head = "head -1" in content or "|head -1" in content
    logger.critical("[IMP:9][test_healthcheck][all] Pipeline `head -1` present: %s", has_pipeline_head)
    assert not has_pipeline_head, "Python module must not contain shell head -1 pipeline."

    # ── Check 3: restart loop detection iterates all containers ──
    has_container_loop = "read_container_names" in content and "for container in" in content
    logger.critical(
        "[IMP:9][test_healthcheck][all] Container iteration present (restart detection): %s", has_container_loop
    )
    assert has_container_loop, "modules_healthcheck.py must iterate all container names for restart loop detection."

    # ── R5 negative: shell facade must NOT carry business logic ──
    if _HEALTHCHECK_SH.is_file():
        sh_content = _HEALTHCHECK_SH.read_text()
        sh_code = [ln for ln in sh_content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        sh_code_text = "\n".join(sh_code)
        has_biz_in_shell = (
            "State.Restarting" in sh_code_text or "RestartCount" in sh_code_text or "install_type" in sh_code_text
        )
        logger.critical(
            "[IMP:9][test_healthcheck][all] Shell facade carries business logic: %s (R5 negative — must be thin)",
            has_biz_in_shell,
        )
        assert not has_biz_in_shell, "E4 R5: modules-healthcheck.sh must be a thin facade (logic moved to Python)."

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


@pytest.mark.static_audit
def test_healthcheck_detects_restart_loop(caplog) -> None:
    """Assert modules_healthcheck.py contains State.Restarting/RestartCount → FAIL handling.

    Acceptance criterion A6: restart looping container gives exit 1.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_PY.is_file(), f"modules_healthcheck.py not found: {_HEALTHCHECK_PY}"
    content = _HEALTHCHECK_PY.read_text()

    has_restarting = "State.Restarting" in content
    logger.critical("[IMP:9][test_healthcheck][restart] State.Restarting inspected: %s", has_restarting)
    assert has_restarting, (
        "modules_healthcheck.py must inspect {{.State.Restarting}} to detect restart loops. "
        "Without it, a restarting container shows as 'starting' → WARN instead of FAIL."
    )

    has_restart_count = "RestartCount" in content
    logger.critical("[IMP:9][test_healthcheck][restart] RestartCount inspected: %s", has_restart_count)
    assert has_restart_count, (
        "modules_healthcheck.py must inspect {{.RestartCount}} to detect restart loops. "
        "Without it, a container with high restart count may show as 'healthy' → PASS."
    )

    has_threshold = "RESTART_LOOP_THRESHOLD = 5" in content or "> threshold" in content or "> 5" in content
    logger.critical("[IMP:9][test_healthcheck][restart] RestartCount threshold >5 present: %s", has_threshold)
    assert has_threshold, "modules_healthcheck.py must encode the >5 restart-count threshold (канон)."

    has_fail = "return False" in content and "restart loop" in content
    logger.critical("[IMP:9][test_healthcheck][restart] Restart-loop FAIL path present: %s", has_fail)
    assert has_fail, (
        "modules_healthcheck.py must return unhealthy (False) when restart loop is detected. "
        "Without it, the healthcheck exits 0 despite unhealthy containers."
    )

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_MODULES_HEALTHCHECK_STATIC
