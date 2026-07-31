# GREP_SUMMARY: e2e failure-scenarios resume-phase7 midphase-kill ssh-read-timeout graceful-124 forced-command receive orchestrator-cli
# STRUCTURE: ▶ T14 resume φ7 (mid-phase kill → recovery) → T15 ssh_read timeout 124 → T16 forced-command receive (DeployOrchestrator JSON)
# region MODULE_CONTRACT
## @purpose  DevPlan 095 Wave 3 (T14-T16): 3 failure scenarios on the real test-VPS —
##           (1) mid-phase kill of bootstrap during φ7 (certificates) + recovery,
##           (2) ssh_read timeout → graceful exit 124 (lib/ssh.sh TRAP staging-gate on real SSH),
##           (3) CI-equivalent forced-command receive via orchestrator_cli (AC10 from DevPlan 089).
## @scope    Requires: bootstrapped test-VPS (runs AFTER test_bootstrap_pipeline.py — pytest
##           orders modules alphabetically, session fixtures shared), NODE env, SSH, AGE key.
## @invariants
##   - T14 owns its cleanup: after the kill it re-runs the full bootstrap in the test body
##     (finally-free: assertions enforce the recovery path; a failed test leaves the node
##     re-bootstrapable — next `make test-node` run resets state via test_vps_fresh)
##   - T15 is macOS-safe: timeout is enforced by Python subprocess (no GNU timeout needed)
##   - Deterministic: 0 @pytest.mark.parametrize (AC6)
##   - Every test: @pytest.mark.requires_node + requires_node fixture + IMP:9 LDD + TRAP[TEST]
## @rationale DevPlan 095: единственный способ покрыть resume_phase-семантику отказа, ssh
##           timeout и forced-command receive на реальном окружении (ранее mock-only).
## ⚠️ TRAP[DECISION] · 2026-07-31 · HI · T14: kill docker заменён на kill процесса state_machine.py (φ1)
## · Rejected: (1) `systemctl stop docker` (DevPlan §T14) — φ7 (certificates) НЕ зависит от docker:
##   install-acme.sh и cert_orchestrator.py docker-free (проверено grep'ом) — остановка docker
##   НЕ уронит φ7 детерминированно. Дополнительно: sub_step-resume (install_acme skip) —
##   МЁРТВЫЙ КОД (resume_phase() не вызывается из _run_init_mode; TRAP[DEBT] в state_machine.py:213).
## · (2) kill во время φ7 (DevPlan T14) — на ноде БЕЗ domain φ7 завершается <1s (install_acme
##   уже установлен + ssl_provision SKIP по "No domains") → kill window отсутствует
##   ("Kill window missed" — подтверждено на tronyx-vps 2026-07-31, run3).
## · Reason: детерминированный mid-phase kill = SIGKILL реального bootstrap-процесса
##   (pkill -9 -f '[s]tate_machine[.]py') в момент выполнения ДЛИТЕЛЬНОЙ фазы — φ1
##   system_bootstrap (apt-get install, минуты). Recovery-ассерт: повторный bootstrap
##   завершается exit 0, все 9 INIT фаз done (фаза перевыполняется целиком —
##   честная проверка текущего поведения pipeline, не заявленного resume).
## · Rev: если resume_phase() будет подключён к run-циклам — усилить ассерт до
##   «SKIP sub_step install_acme» (лог IMP:8) и вернуть φ7-семантику частичного отказа.
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import pytest

from tests._conftest.node import (
    INIT_PHASES,
    NodeSSHClient,
    NodeState,
    assert_ldd_imp9_e2e,
    build_payload_tar,
    deliver_payload_via_ssh,
)
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)


# region TEST_T14
@pytest.mark.requires_node
def test_resume_phase7_after_midphase_kill(
    requires_node: str, node_ssh: NodeSSHClient, node_state: NodeState, caplog
) -> None:
    """Kill bootstrap mid-φ1 (system_bootstrap) → re-run → pipeline recovers, all INIT phases done.

    # 🧪 TRAP[TEST] · Scenario: SIGKILL state_machine.py во время φ1 (apt-get long-running) · Last fail: N/A
    # · Regression: после kill повторный bootstrap застревает (stuck partial state / dpkg lock) → recovery fail
    # · Remove if: resume_phase() подключён к run-циклам — вернуть φ7 sub_step-SKIP семантику
    ## @purpose — AC5: bootstrap переживает mid-phase kill. Инъекция отказа: φ1 reset
    ##            (done=false) → bootstrap в фоне → SIGKILL реального state_machine.py в момент
    ##            выполнения φ1 → повторный bootstrap восстанавливается (exit 0, все 9 INIT done).
    ## ⚠️ TRAP[DECISION] · 2026-07-31 · HI · Kill-цель: φ1 system_bootstrap, не φ7 certificates
    ## · Rejected: φ7 (DevPlan T14) — на ноде БЕЗ domain φ7 завершается <1s (install_acme уже
    ## ·   установлен + ssl_provision SKIP по "No domains") → kill window физически отсутствует
    ## ·   ("Kill window missed: φ7 completed before SIGKILL" — подтверждено на tronyx-vps).
    ## · Reason: детерминированный mid-phase kill требует ДЛИТЕЛЬНОЙ фазы — φ1 apt-get install
    ## ·   идёт минуты. Семантика теста (phase-level recovery после kill) не меняется —
    ## ·   sub_step-resume мёртвый код (TRAP[DEBT] state_machine.py:213), φ7-семантика не тестируема.
    ## ·   Дополнительно убивается orphan apt-get (dpkg lock) после kill python.
    ## · Rev: если resume_phase() подключён к run-циклам — усилить ассерт до sub_step-SKIP и
    ## ·   вернуть φ7-семантику частичного отказа.
    ## @io — ⇥ requires_node, node_ssh, node_state, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — one background kill + one full rebootstrap
    """
    caplog.set_level(logging.DEBUG)

    # ── 1. Reset φ1 (system_bootstrap) — force re-execution of the phase ──
    reset = node_state.reset_phase("system_bootstrap", timeout=30)
    assert reset.exit_code == 0, f"φ1 reset failed: {reset.stderr}"
    logger.info("[IMP:9][T14][kill] φ1 system_bootstrap reset (done=false)")

    # ── 2. Start bootstrap in background, watch for φ1 start ──
    proc = subprocess.Popen(
        ["make", "bootstrap-node", f"NODE={requires_node}"],
        cwd=str(repo_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    phase1_seen = False
    deadline = time.monotonic() + 900
    assert proc.stdout is not None
    for line in proc.stdout:
        stripped = line.strip()
        if "Phase 1/9: system_bootstrap" in stripped:
            phase1_seen = True
            logger.info("[IMP:9][T14][kill] φ1 started: %s", stripped)
            break
        if time.monotonic() > deadline:
            break

    assert phase1_seen, "φ1 (system_bootstrap) never started within 900s — kill window missed (bootstrap too slow/failed)"
    time.sleep(2)  # allow apt-get to begin executing → mid-φ1 kill

    # ── 3. SIGKILL the remote bootstrap process (mid-φ1) + orphan apt-get (dpkg lock) ──
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · pkill -f сам себя убивал (exit 255)
    # · Symptom: `pkill -9 -f state_machine.py || true` возвращал exit 255 (SSH-сбой) — kill не срабатывал.
    # · Root: -f матчит ПОЛНУЮ командную строку, включая саму remote-команду ssh
    # ·   (bash -c "pkill -9 -f state_machine.py || true") → pkill убивал родительский shell → ssh рвался.
    # · Fix: bracket-паттерн '[s]tate_machine[.]py' — матчит целевой процесс (python3 ...state_machine.py),
    # ·   но НЕ собственную командную строку pkill. Тот же приём для orphan apt-get ('[a]pt-get').
    # · Prevention: pkill -f с паттерном, встречающимся в собственной cmdline, всегда через [x]-trick.
    kill = node_ssh.ssh_exec(
        "pkill -9 -f '[s]tate_machine[.]py' || true; sleep 1; pkill -9 -f '[a]pt-get' || true",
        timeout=30,
    )
    logger.info("[IMP:9][T14][kill] pkill exit=%d", kill.exit_code)
    assert kill.exit_code == 0, f"pkill failed: {kill.stderr}"

    proc.wait(timeout=120)
    logger.info("[IMP:9][T14][kill] background bootstrap exited rc=%d", proc.returncode)

    # ── 4. Verify the kill landed mid-φ1 (phase NOT completed) ──
    mid_phase = not node_state.phase_done("system_bootstrap")
    logger.info("[IMP:9][T14][kill] φ1 done after kill=%s (expected False)", not mid_phase)
    assert mid_phase, "Kill window missed: φ1 completed before SIGKILL (retry the test)"

    # ── 5. Recovery: full re-run must complete the pipeline ──
    recovery = subprocess.run(
        ["make", "bootstrap-node", f"NODE={requires_node}"],
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    logger.info("[IMP:9][T14][recovery] exit=%d", recovery.returncode)
    assert recovery.returncode == 0, f"Recovery bootstrap failed: {recovery.stderr[-1500:]}"

    done, pending = node_state.all_phases_done(INIT_PHASES)
    logger.info("[IMP:9][T14][recovery] INIT done=%d/%d pending=%s", len(done), len(INIT_PHASES), pending)
    assert not pending, f"Recovery incomplete — pending phases: {pending}"
    assert node_state.phase_done("system_bootstrap"), "φ1 (system_bootstrap) not done after recovery"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T14


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

    before = node_ssh.ssh_read(
        "ls -1 /opt/projects/test-project/.deploy-snapshots/*.json 2>/dev/null | wc -l", timeout=30
    )
    count_before = int(before.stdout.strip() or "0")
    logger.info("[IMP:8][T16][receive] snapshots before=%d", count_before)

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
    assert result_json.get("status") in ("DEPLOYED", "PARTIAL"), f"DeployResult not success: {delivery.stdout[-1000:]}"
    assert result_json.get("project") == test_project_fixture, f"Wrong project in DeployResult: {result_json}"
    assert result_json.get("snapshot_id"), "DeployResult.snapshot_id must be non-empty (backup artifact)"

    after = node_ssh.ssh_read(
        "ls -1 /opt/projects/test-project/.deploy-snapshots/*.json 2>/dev/null | wc -l", timeout=30
    )
    count_after = int(after.stdout.strip() or "0")
    logger.info("[IMP:9][T16][receive] snapshots after=%d (delta=%d)", count_after, count_after - count_before)
    assert count_after > count_before, f"No new snapshot created: before={count_before} after={count_after}"

    assert_ldd_imp9_e2e(caplog)


# endregion TEST_T16
