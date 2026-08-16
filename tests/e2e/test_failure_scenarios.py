# GREP_SUMMARY: e2e failure-scenarios ssh-read-timeout graceful-124 forced-command receive orchestrator-cli
# STRUCTURE: ▶ T15 ssh_read timeout 124 → T16 forced-command receive (DeployOrchestrator JSON)
# region MODULE_CONTRACT
## @purpose  DevPlan 095 Wave 3 (T15-T16): 2 failure scenarios on the real test-VPS —
##           (1) ssh_read timeout → graceful exit 124 (lib/ssh.sh TRAP staging-gate on real SSH),
##           (2) CI-equivalent forced-command receive via orchestrator_cli (AC10 from DevPlan 089).
##           T14 (mid-phase kill → sub-step resume recovery) removed in DevPlan 116 B8 U-66 (D4):
##           the resume machinery was dead code — kill-recovery семантика перевыполняет фазу целиком,
##           что покрыто статически (test_bootstrap_dry_run skip-тесты) без реальной VPS-инъекции.
## @scope    Requires: bootstrapped test-VPS (runs AFTER test_bootstrap_pipeline.py — pytest
##           orders modules alphabetically, session fixtures shared), NODE env, SSH, AGE key.
## @invariants
##   - T15 is macOS-safe: timeout is enforced by Python subprocess (no GNU timeout needed)
##   - Deterministic: 0 @pytest.mark.parametrize (AC6)
##   - Every test: @pytest.mark.requires_node + requires_node fixture + IMP:9 LDD + TRAP[TEST]
## @rationale DevPlan 095: единственный способ покрыть ssh timeout и forced-command receive
##           на реальном окружении (ранее mock-only).
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tests._conftest.node import (
    NodeSSHClient,
    assert_ldd_imp9_e2e,
    build_payload_tar,
    deliver_payload_via_ssh,
)

logger = logging.getLogger(__name__)


# region TEST_T15
@pytest.mark.requires_node
def test_ssh_read_timeout_graceful_error(requires_node: str, node_ssh: NodeSSHClient, caplog) -> None:
    """ssh_read with timeout=1 on `sleep 5` → graceful exit 124 (lib/ssh.sh timeout contract).

    # 🧪 TRAP[TEST] · Scenario: ssh_read('sleep 5', timeout=1) · Last fail: N/A
    # · Regression: NodeSSHClient без catch TimeoutExpired → exception вместо graceful SSHResult
    # · Remove if: ssh-фасад мигрирован на Python-native SSH (timeout семантика изменится)
    ## @purpose — AC5/TRAP lib/ssh.sh staging-gate: ssh_read с превышенным timeout возвращает
    ##            exit 124 + timed_out=True + graceful сообщение (не hang, не crash).
    ##            Python-side timeout (без GNU timeout) — macOS-safe.
    ## @io — ⇥ requires_node, node_ssh, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — single 1s-timeout SSH round-trip
    """
    caplog.set_level(logging.DEBUG)

    result = node_ssh.ssh_read("sleep 5", timeout=1)
    logger.info(
        "[IMP:9][T15][timeout] exit=%d timed_out=%s stderr=%s", result.exit_code, result.timed_out, result.stderr[:120]
    )
    assert result.exit_code == 124, f"Expected graceful timeout exit 124, got {result.exit_code}"
    assert result.timed_out is True, "timed_out flag must be True for timeout"
    assert "TIMEOUT" in result.stderr, f"Graceful timeout message missing: {result.stderr}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T15


# region TEST_T16
@pytest.mark.requires_node
def test_deploy_forced_command_receive(
    requires_node: str,
    node_ssh: NodeSSHClient,
    test_project_fixture: str,
    test_project_dir: str,
    caplog,
) -> None:
    """CI-equivalent forced-command receive: payload tar via SSH stdin → DeployResult JSON, exit 0.

    # 🧪 TRAP[TEST] · Scenario: tar.gz → ssh stdin → orchestrator_cli receive · Last fail: N/A
    # · Regression: receive() SCPChannel-баг (metadata host) → exit 1 «SCPChannel requires host»
    # · Remove if: forced-command receive заменён другим CI-каналом доставки
    ## @purpose — AC5/AC10(089) T6.6: orchestrator_cli receive работает на реальном SSH
    ##            forced-command: exit 0, DeployResult JSON (status DEPLOYED, project,
    ##            snapshot_id non-empty), новый DeployHistory snapshot создан.
    ## @io — ⇥ requires_node, node_ssh, test_project_fixture, test_project_dir, caplog → ⎋ None
    ## @complexity — O(P) — payload assembly + SSH stdin delivery + compose up
    """
    caplog.set_level(logging.DEBUG)

    tar_path = build_payload_tar(Path(test_project_dir))
    delivery = deliver_payload_via_ssh(node_ssh, tar_path, timeout=600)
    logger.info("[IMP:9][T16][receive] exit=%d", delivery.exit_code)
    assert delivery.exit_code == 0, f"receive failed: {delivery.stdout[-1000:]} {delivery.stderr[-1000:]}"

    # DeployResult JSON on stdout (last line — receive prints exactly one JSON doc)
    try:
        result_json = json.loads(delivery.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as exc:
        result_json = {}
        logger.warning("[IMP:7][T16][receive] stdout not JSON: %s (%s)", delivery.stdout[-500:], exc)
    logger.info(
        "[IMP:9][T16][receive] DeployResult: status=%s project=%s snapshot=%s",
        result_json.get("status"),
        result_json.get("project"),
        result_json.get("snapshot_id"),
    )
    assert result_json.get("status") in {"DEPLOYED", "PARTIAL"}, f"DeployResult not success: {delivery.stdout[-1000:]}"
    assert result_json.get("project") == test_project_fixture, f"Wrong project in DeployResult: {result_json}"
    assert result_json.get("snapshot_id"), "DeployResult.snapshot_id must be non-empty (backup artifact)"

    after = node_ssh.ssh_read(
        "ls -1 /opt/projects/test-project/.deploy-snapshots/*.json 2>/dev/null | wc -l", timeout=30
    )
    count_after = int(after.stdout.strip() or "0")
    logger.info("[IMP:9][T16][receive] snapshots after=%d", count_after)
    # ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Ассерт на snapshot-ФАЙЛ, не на count-delta
    # · Rejected: `count_after > count_before` — DeployHistory PRUNE сохраняет последние 10
    # ·   снапшотов (deploy_history.py: "prune to keep last 10"): после 10 накопленных деплоев
    #   count не растёт (10→10) — "No new snapshot created" при успешном receive (status=PARTIAL,
    #   snapshot_id присутствует). Подтверждено на tronyx-vps (run4, before=10 after=10).
    # · Reason: детерминированный признак нового снапшота = файл <snapshot_id>.json из
    # ·   DeployResult (имя файла = snapshot_id, deploy_history.py _snapshot_path) —
    # ·   prune-устойчив и проверяет именно артефакт бэкапа.
    # · Rev: если DeployHistory изменит формат имён — обновить проверку файла.
    assert count_after >= 1, f"No snapshots at all after receive: {after.stdout}"
    snap_id = result_json.get("snapshot_id")
    assert snap_id, "DeployResult.snapshot_id must be non-empty (backup artifact)"
    snap_check = node_ssh.ssh_read(
        f"test -f /opt/projects/test-project/.deploy-snapshots/{snap_id}.json && echo EXISTS || echo MISSING",
        timeout=30,
    )
    logger.info("[IMP:9][T16][receive] snapshot file %s.json -> %s", snap_id, snap_check.stdout.strip())
    assert "EXISTS" in snap_check.stdout, f"Snapshot file {snap_id}.json not found after receive"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T16
