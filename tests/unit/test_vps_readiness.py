# GREP_SUMMARY: unit-test, vps-readiness, check-vps-ready, ssh-runner, mock, node-host-map, preflight, json-diagnostics, devplan-105
# STRUCTURE: ▶ T1 all-ok → ▶ T2 no-map → ▶ T3 node-not-found → ▶ T4-T7 per-check FAIL → ▶ T8 quick-skip-docker → ▶ T9/T10 JSON round-trip → ▶ T11 ANTI-SURVIVORSHIP ($first) → ⎋ 11 pass
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/vps_readiness.py — check_vps_ready(),
##           _resolve_node_host(), _build_json_diagnostics(). 11 tests per DevPlan 105 §9 $TEST_SPEC.
## @scope    Pure unit tests — native imports, no subprocess (SSH mocked via DI callable),
##           no Docker. DI ssh_runner через lambda/функцию (НЕ monkeypatch внутренностей).
##           monkeypatch — только для env NODE_HOST_MAP (T2). tmp_path — там где нужен.
## @invariants
##   - Mock ssh_runner: (host, user, cmd, timeout) -> (rc, stdout); диспетчеризация по cmd
##   - Неожиданный cmd → AssertionError (доказывает fail-fast/quick-skip без docker)
##   - Каждый тест имеет реальные asserts (Test Honesty R1/R2) + TRAP[TEST] + LDD IMP:9
##   - T11 ANTI-SURVIVORSHIP (R5): баг $first из bash-версии не воспроизводится
## @rationale DevPlan 105 §9 $TEST_SPEC: T1-T11 — mock ssh_runner, LDD caplog IMP:9, R1/R2/R5
## @changes  2026-07-31 | DevPlan 105 — Created
# endregion MODULE_CONTRACT

import json
import logging

import pytest

from core.internal.shared.vps_readiness import (
    _build_json_diagnostics,
    check_vps_ready,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

NODE = "node1"
HOST = "1.2.3.4"
NODE_HOST_MAP = {NODE: HOST}


def _mock_ssh_runner(responses: dict[str, tuple[int, str]]):
    """Build a DI ssh_runner dispatching on cmd content.

    ▶ ∋ responses: {cmd_substring: (rc, stdout)} → ⎋ runner(host, user, cmd, timeout) -> (rc, stdout)
    @invariants — Неожиданный cmd → AssertionError (fail-fast/quick-skip доказательство)
    """

    def runner(host: str, user: str, cmd: str, timeout: int) -> tuple[int, str]:
        logger.info("[IMP:7][test_vps_readiness][mock] ssh_runner %s@%s cmd=%.60r", user, host, cmd)
        for pattern, response in responses.items():
            if pattern in cmd:
                return response
        msg = f"Unexpected SSH command: {cmd!r}"
        raise AssertionError(msg)

    return runner


# region FUNC_test_all_checks_pass
## @purpose — T1: все 4 проверки успешны → (True, ready diagnostics) (AC3)
## @io — ⇥ DI runner + node_host_map → ⎋ None (asserts (True, status ready))
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · all-4-checks-pass path (DevPlan 105 §9 T1)
# · Last fail: N/A (new test)
# · Remove if: check_vps_ready check orchestration is reworked
def test_all_checks_pass(caplog: pytest.LogCaptureFixture) -> None:
    """All 4 checks pass → (True, {"status": "ready", ...})."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({
        "exit": (0, ""),
        "ping": (0, "pong"),
        "/opt/projects": (0, "OK"),
        "docker info": (0, "26.1.3"),
    })

    logger.info("[IMP:7][test_vps_readiness] T1: all-checks-pass scenario")
    all_ok, result = check_vps_ready(NODE, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is True, f"Expected ready, got all_ok={all_ok}, result={result}"
    assert result["status"] == "ready", f"Expected status=ready, got {result}"
    assert result["node"] == NODE and result["host"] == HOST
    assert result["checks"] == ["ssh", "forced-command", "projects", "docker"]
    logger.info("[IMP:9][test_vps_readiness] T1 PASS: 4/4 checks OK — status=ready")


# endregion FUNC_test_all_checks_pass


# region FUNC_test_no_node_host_map
## @purpose — T2: NODE_HOST_MAP unset → (False, remediation) (AC8)
## @io — ⇥ monkeypatch.delenv → ⎋ None (asserts False + remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · NODE_HOST_MAP unset path (DevPlan 105 §9 T2)
# · Last fail: N/A (new test)
# · Remove if: _resolve_node_host env-unset handling changes
def test_no_node_host_map(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """NODE_HOST_MAP not set → (False, failure with remediation hint)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("NODE_HOST_MAP", raising=False)

    logger.info("[IMP:7][test_vps_readiness] T2: NODE_HOST_MAP unset scenario")
    all_ok, result = check_vps_ready(NODE, node_host_map=None)
    assert all_ok is False, f"Expected not_ready, got all_ok={all_ok}"
    assert result["status"] == "not_ready"
    assert not result["host"], "Host must be empty when resolution fails"
    failures = result["failures"]
    assert len(failures) == 1, f"Expected exactly 1 failure, got {failures}"
    assert "NODE_HOST_MAP not set" in failures[0]["check"]
    assert "Set NODE_HOST_MAP env var" in failures[0]["remediation"]
    logger.info("[IMP:9][test_vps_readiness] T2 PASS: unset map → not_ready + remediation")


# endregion FUNC_test_no_node_host_map


# region FUNC_test_node_not_in_map
## @purpose — T3: node отсутствует в NODE_HOST_MAP → (False, available keys) (AC8)
## @io — ⇥ node_host_map DI → ⎋ None (asserts False + keys in remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · node-not-found in map (DevPlan 105 §9 T3)
# · Last fail: N/A (new test)
# · Remove if: _resolve_node_host lookup handling changes
def test_node_not_in_map(caplog: pytest.LogCaptureFixture) -> None:
    """Node missing from NODE_HOST_MAP → (False, remediation with available keys)."""
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_vps_readiness] T3: node-not-found scenario")
    all_ok, result = check_vps_ready("unknown-node", node_host_map=NODE_HOST_MAP)
    assert all_ok is False
    assert result["status"] == "not_ready"
    failures = result["failures"]
    assert len(failures) == 1
    assert "unknown-node" in failures[0]["check"] and "not found" in failures[0]["check"]
    assert "Current keys: node1" in failures[0]["remediation"], (
        f"Available keys must be listed, got {failures[0]['remediation']}"
    )
    logger.info("[IMP:9][test_vps_readiness] T3 PASS: unknown node → keys listed in remediation")


# endregion FUNC_test_node_not_in_map


# region FUNC_test_ssh_unreachable
## @purpose — T4: SSH check fail → (False, remediation hint) (AC7)
## @io — ⇥ DI runner (exit≠0) → ⎋ None (asserts False + ssh remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · ssh-unreachable path (DevPlan 105 §9 T4)
# · Last fail: N/A (new test)
# · Remove if: check 1 (SSH accessibility) semantics change
def test_ssh_unreachable(caplog: pytest.LogCaptureFixture) -> None:
    """SSH check fail (rc≠0) → (False, failure with remediation hint)."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({"exit": (255, "Permission denied")})

    logger.info("[IMP:7][test_vps_readiness] T4: ssh-unreachable scenario")
    all_ok, result = check_vps_ready(NODE, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is False
    failures = result["failures"]
    assert len(failures) == 1, "Fail-fast: only SSH failure expected"
    assert "SSH to ci-deploy@1.2.3.4 failed" in failures[0]["check"]
    assert "verify network, SSH key" in failures[0]["remediation"]
    logger.info("[IMP:9][test_vps_readiness] T4 PASS: ssh unreachable → remediation hint")


# endregion FUNC_test_ssh_unreachable


# region FUNC_test_ping_no_pong
## @purpose — T5: forced-command ping не возвращает "pong" → (False, remediation) (AC7)
## @io — ⇥ DI runner (ping → non-pong) → ⎋ None (asserts False + bootstrap remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · ping-no-pong path (DevPlan 105 §9 T5)
# · Last fail: N/A (new test)
# · Remove if: check 2 (forced-command ping) semantics change
def test_ping_no_pong(caplog: pytest.LogCaptureFixture) -> None:
    """Forced-command ping without 'pong' → (False, remediation 'make bootstrap-node ... first')."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({"exit": (0, ""), "ping": (0, "busy")})

    logger.info("[IMP:7][test_vps_readiness] T5: ping-no-pong scenario")
    all_ok, result = check_vps_ready(NODE, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is False
    failures = result["failures"]
    assert len(failures) == 1
    assert "did not respond with pong" in failures[0]["check"]
    assert failures[0]["remediation"] == "Core not delivered. Run: make bootstrap-node NODE=node1 first"
    logger.info("[IMP:9][test_vps_readiness] T5 PASS: ping no-pong → bootstrap remediation")


# endregion FUNC_test_ping_no_pong


# region FUNC_test_projects_missing
## @purpose — T6: /opt/projects/ отсутствует → (False, remediation) (AC7)
## @io — ⇥ DI runner (projects → FAIL) → ⎋ None (asserts False + bootstrap remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · projects-missing path (DevPlan 105 §9 T6)
# · Last fail: N/A (new test)
# · Remove if: check 3 (/opt/projects/) semantics change
def test_projects_missing(caplog: pytest.LogCaptureFixture) -> None:
    """/opt/projects/ not OK → (False, failure with remediation hint)."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({"exit": (0, ""), "ping": (0, "pong"), "/opt/projects": (0, "FAIL")})

    logger.info("[IMP:7][test_vps_readiness] T6: projects-missing scenario")
    all_ok, result = check_vps_ready(NODE, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is False
    failures = result["failures"]
    assert len(failures) == 1
    assert "/opt/projects/ missing or not writable" in failures[0]["check"]
    assert failures[0]["remediation"] == "Project base missing. Run: make bootstrap-node NODE=node1"
    logger.info("[IMP:9][test_vps_readiness] T6 PASS: projects missing → bootstrap remediation")


# endregion FUNC_test_projects_missing


# region FUNC_test_docker_unreachable
## @purpose — T7: Docker daemon не отвечает → (False, remediation) (AC7)
## @io — ⇥ DI runner (docker → FAIL) → ⎋ None (asserts False + systemctl remediation)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · docker-unreachable path (DevPlan 105 §9 T7)
# · Last fail: N/A (new test)
# · Remove if: check 4 (Docker daemon) semantics change
def test_docker_unreachable(caplog: pytest.LogCaptureFixture) -> None:
    """Docker daemon returns FAIL → (False, failure with remediation hint)."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({
        "exit": (0, ""),
        "ping": (0, "pong"),
        "/opt/projects": (0, "OK"),
        "docker info": (0, "FAIL"),
    })

    logger.info("[IMP:7][test_vps_readiness] T7: docker-unreachable scenario")
    all_ok, result = check_vps_ready(NODE, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is False
    failures = result["failures"]
    assert len(failures) == 1
    assert "Docker daemon not reachable on 1.2.3.4" in failures[0]["check"]
    assert "systemctl start docker" in failures[0]["remediation"]
    logger.info("[IMP:9][test_vps_readiness] T7 PASS: docker unreachable → systemctl remediation")


# endregion FUNC_test_docker_unreachable


# region FUNC_test_quick_skips_docker
## @purpose — T8: --quick → Docker check skipped, остальные 3 проходят (AC4)
## @io — ⇥ DI runner без docker-ответа → ⎋ None (AssertionError если docker вызван)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · quick-mode docker skip (DevPlan 105 §9 T8)
# · Last fail: N/A (new test)
# · Remove if: quick_mode semantics change
def test_quick_skips_docker(caplog: pytest.LogCaptureFixture) -> None:
    """--quick: docker check skipped → (True, ready); docker cmd никогда не вызывается."""
    caplog.set_level(logging.DEBUG)
    # Нет ключа "docker info" — если check 4 выполнится, _mock_ssh_runner бросит AssertionError
    runner = _mock_ssh_runner({"exit": (0, ""), "ping": (0, "pong"), "/opt/projects": (0, "OK")})

    logger.info("[IMP:7][test_vps_readiness] T8: quick-mode scenario")
    all_ok, result = check_vps_ready(NODE, quick_mode=True, ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is True, f"Quick mode: 3 checks pass → ready, got {result}"
    assert result["status"] == "ready"
    assert result["checks"] == ["ssh", "forced-command", "projects", "docker"], (
        "checks list preserved (bash-версия включала docker даже при --quick)"
    )
    logger.info("[IMP:9][test_vps_readiness] T8 PASS: quick mode skipped docker, ready")


# endregion FUNC_test_quick_skips_docker


# region FUNC_test_json_output_ready
## @purpose — T9: --json ready → валидный JSON {"status":"ready",...} (AC5)
## @io — ⇥ DI runner + json round-trip → ⎋ None (asserts valid JSON, status ready)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · json-ready output (DevPlan 105 §9 T9)
# · Last fail: N/A (new test)
# · Remove if: ready diagnostics structure changes
def test_json_output_ready(caplog: pytest.LogCaptureFixture) -> None:
    """--json при успехе: диагностика сериализуется в валидный JSON со status=ready."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({
        "exit": (0, ""),
        "ping": (0, "pong"),
        "/opt/projects": (0, "OK"),
        "docker info": (0, "26.1.3"),
    })

    logger.info("[IMP:7][test_vps_readiness] T9: json-ready scenario")
    all_ok, result = check_vps_ready(NODE, output_mode="json", ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is True

    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    assert parsed["status"] == "ready"
    assert parsed["node"] == NODE and parsed["host"] == HOST
    assert parsed["checks"] == ["ssh", "forced-command", "projects", "docker"]
    logger.info("[IMP:9][test_vps_readiness] T9 PASS: ready JSON round-trips with status=ready")


# endregion FUNC_test_json_output_ready


# region FUNC_test_json_output_failures
## @purpose — T10: --json при ошибках → валидный JSON с failures array (AC5)
## @io — ⇥ DI runner (docker FAIL) + json round-trip → ⎋ None (asserts failures array)
## @complexity — O(1)
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · json-failures output (DevPlan 105 §9 T10)
# · Last fail: N/A (new test)
# · Remove if: not_ready diagnostics structure changes
def test_json_output_failures(caplog: pytest.LogCaptureFixture) -> None:
    """--json при ошибке: валидный JSON с failures array (check + remediation)."""
    caplog.set_level(logging.DEBUG)
    runner = _mock_ssh_runner({
        "exit": (0, ""),
        "ping": (0, "pong"),
        "/opt/projects": (0, "OK"),
        "docker info": (0, "FAIL"),
    })

    logger.info("[IMP:7][test_vps_readiness] T10: json-failures scenario")
    all_ok, result = check_vps_ready(NODE, output_mode="json", ssh_runner=runner, node_host_map=NODE_HOST_MAP)
    assert all_ok is False

    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    assert parsed["status"] == "not_ready"
    assert isinstance(parsed["failures"], list) and len(parsed["failures"]) == 1
    failure = parsed["failures"][0]
    assert "Docker daemon not reachable" in failure["check"]
    assert "systemctl start docker" in failure["remediation"]
    logger.info("[IMP:9][test_vps_readiness] T10 PASS: not_ready JSON round-trips with failures array")


# endregion FUNC_test_json_output_failures


# region FUNC_test_json_no_extra_commas
## @purpose — T11 R5 ANTI-SURVIVORSHIP: баг $first из bash не воспроизводится (AC6).
##           В bash: $first || json_diag+="," после first=false выполнял `false` → сломанный JSON.
##           В Python: _build_json_diagnostics через структуры данных — json.dumps всегда валиден.
## @io — ⇥ _build_json_diagnostics(3 failures) → ⎋ None (asserts valid JSON, no ",," no "false")
## @complexity — O(F) где F = failures
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · ANTI-SURVIVORSHIP (R5) · $first bug from bash vps-readiness.sh:170
# · Last fail: 2026-07-26 (латентный — баг воспроизводился бы при ≥2 failures в bash)
# · Remove if: _build_json_diagnostics переходит на строковую конкатенацию (запрещено)
def test_json_no_extra_commas(caplog: pytest.LogCaptureFixture) -> None:
    """≥2 failures → JSON валиден, без ',,' и без следов `false`-команды из бага $first."""
    caplog.set_level(logging.DEBUG)
    failures: list[dict[str, str]] = [
        {"check": "check-A", "remediation": "hint-A"},
        {"check": "check-B", "remediation": "hint-B"},
        {"check": "check-C", "remediation": "hint-C"},
    ]

    logger.info("[IMP:7][test_vps_readiness] T11: multi-failure JSON serialization (ANTI-SURVIVORSHIP)")
    diag = _build_json_diagnostics(NODE, HOST, failures)
    serialized = json.dumps(diag)

    assert ",," not in serialized, f"Stray comma (bash $first bug symptom): {serialized}"
    assert "false" not in serialized, f"Residue of `false` command execution: {serialized}"
    parsed = json.loads(serialized)  # должна быть валидным JSON
    assert parsed["status"] == "not_ready"
    assert parsed["node"] == NODE and parsed["host"] == HOST
    assert len(parsed["failures"]) == 3
    assert parsed["failures"][0] == {"check": "check-A", "remediation": "hint-A"}
    assert parsed["failures"][1] == {"check": "check-B", "remediation": "hint-B"}
    assert parsed["failures"][2] == {"check": "check-C", "remediation": "hint-C"}
    # Канонический round-trip: повторная сериализация парса = исходная строка (нет мусора)
    assert json.dumps(json.loads(serialized)) == serialized
    logger.info("[IMP:9][test_vps_readiness] T11 PASS: 3 failures → valid JSON, no extra commas")


# endregion FUNC_test_json_no_extra_commas
