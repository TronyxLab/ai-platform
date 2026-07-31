# GREP_SUMMARY: node-ssh-client, ssh-exec, ssh-read, timeout-124, node-state, state-json, reset-state, reset-phase, phase-done, payload-tar, receive-delivery, ldd-imp9-e2e, e2e-vps
# STRUCTURE: ▶ _require_node_env → ◇ NodeSSHClient (ssh_exec/ssh_read, timeout→124) → ◇ NodeState (read/reset/reset_phase/phase_done) → ◇ build_payload_tar + deliver_payload_via_ssh → ◇ assert_ldd_imp9_e2e → ⎋
# region MODULE_CONTRACT
## @purpose  E2E helpers for DevPlan 095: NodeSSHClient (Python wrapper over ssh CLI mirroring
##           core/lib/ssh.sh semantics), NodeState (state.json reader/resetter via SSH),
##           payload tar builder + forced-command receive delivery, LDD IMP:9 assertion.
## @scope    Consumed by tests/e2e/conftest.py and tests/e2e/test_*.py. NOT re-exported via
##           _conftest/__init__.py (direct module import per infra Singleton Import Protocol).
## @invariants
##   - NODE env missing → pytest.fail (Rule R4: NO_SERVICE = FAIL, not skip)
##   - ssh_exec/ssh_read: timeout wrapper mirrors lib/ssh.sh (exit 124 on timeout, never hang)
##   - SSH opts: BatchMode=yes, StrictHostKeyChecking=accept-new, ConnectTimeout=30,
##     ServerAliveInterval=30, ServerAliveCountMax=10 (same as core/lib/ssh.sh SSH_OPTS_COMMON)
##   - Host/user resolution from node-configs/<NODE>/node.yaml (Path 1 of NodeYaml.resolve)
##   - state.json path: /var/lib/platform/.bootstrap/state.json (state_machine.DEFAULT_STATE_FILE)
##   - reset_state() = rm state.json (documented reset per core/internal/bootstrap/AGENTS.md)
## @rationale DevPlan 095 T3: single place for SSH/state helpers consumed by all 11 E2E tests.
##           Mirrors lib/ssh.sh contract so the E2E covers the real SSH path semantics.
## @changes 2026-07-31 | DevPlan 095 T3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ── Constants (mirror core/lib/ssh.sh SSH_OPTS_COMMON + state_machine.DEFAULT_STATE_FILE) ──
_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"
_DEFAULT_SSH_TIMEOUT = 60  # ssh_read default (lib/ssh.sh: 60s)
_DEPLOY_SSH_TIMEOUT = 600  # ssh_exec deploy default (lib/ssh.sh: 600s)

# 9 INIT phases (φ1-φ8.5) + 5 UPDATE phases (φ9-φ13) — BootstrapPhase enum canonical keys
INIT_PHASES: list[str] = [
    "system_bootstrap",
    "user_accounts",
    "platform_setup",
    "secrets_provision",
    "node_configuration",
    "registry_auth",
    "certificates",
    "deploy_services",
    "converge_services",
]
UPDATE_PHASES: list[str] = [
    "secrets_update",
    "node_config_update",
    "registry_update",
    "deploy_update",
    "converge_update",
]


# region FUNC__require_node_env
def _require_node_env() -> str:
    """Return NODE env value or FAIL the test (Rule R4: NO_SERVICE = FAIL, not skip).

    ## @purpose — Single R4 enforcement point for all requires_node fixtures.
    ##            Environmental absence is a configuration error — surfaced, not hidden.
    ## @io — ⇥ None → ⎋ str (NODE name) | pytest.fail
    ## @complexity — O(1)
    ## @invariants
    ##   - Never calls pytest.skip — R4 forbids skip-as-bug-masking for missing service
    """
    node = os.environ.get("NODE", "").strip()
    if not node:
        pytest.fail(
            "NODE environment variable not set. E2E pipeline tests require a test-VPS. "
            "Usage: make test-node NODE=test-e2e. "
            "Per Rule R4: environmental absence is a configuration error — surfaced, not hidden.",
            pytrace=False,
        )
    return node


# endregion FUNC__require_node_env


# region CLASS_NodeSSHClient

# 🧐 TRAP[DECISION] · 2026-07-31 · HI · NodeSSHClient поверх ssh CLI, не lib/ssh.sh subprocess
# · Rejected: bash -c 'source core/lib/ssh.sh && ssh_exec ...' (риск: GNU timeout отсутствует
#   на macOS dev-машине — TRAP DRIFT-note в AGENTS.md; subprocess-обёртка ломает exit-code)
# · Reason: Python subprocess.run(timeout=) даёт ту же семантику (exit 124 на timeout)
#   кросс-платформенно, без GNU timeout. SSH_OPTS_COMMON идентичны lib/ssh.sh.
# · Rev: если платформа перейдёт на Python-native SSH (paramiko) — заменить ssh CLI целиком.


@dataclass
class SSHResult:
    """Result of a remote SSH command.

    ## @purpose — Structured ssh_exec/ssh_read return: exit code, stdout, stderr, timed_out.
    ## @io — ⇥ constructor params → ⎋ SSHResult
    ## @complexity — O(1)
    ## @invariants
    ##   - exit_code == 124 means timeout (mirrors lib/ssh.sh L32)
    ##   - timed_out is True iff exit_code == 124
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = field(default=False)


class NodeSSHClient:
    """SSH client wrapper for E2E tests — mirrors core/lib/ssh.sh semantics.

    ## @purpose — Execute remote commands on the test-VPS with lib/ssh.sh-compatible
    ##            timeout semantics (exit 124 = timeout, graceful error, never hang).
    ## @io — ⇥ host, user (default root) → ⎋ SSHResult per command
    ## @complexity — O(1) per command
    ## @invariants
    ##   - ssh_exec: timeout param in seconds, exit 124 on timeout (lib/ssh.sh parity)
    ##   - ssh_read: alias with 60s default timeout (lib/ssh.sh parity)
    ##   - SSH_OPTS_COMMON identical to lib/ssh.sh: BatchMode, accept-new, ConnectTimeout=30,
    ##     ServerAliveInterval=30, ServerAliveCountMax=10
    ##   - Optional SSH_KEY env (-i keyfile) and SSH_USER env override
    ## @rationale DevPlan 095 T3: Python-side mirror of lib/ssh.sh so E2E tests exercise
    ##            the same SSH contract (timeout 124, BatchMode) without GNU timeout.
    """

    def __init__(self, host: str, user: str = "root") -> None:
        self.host = host
        self.user = user
        key_file = os.environ.get("SSH_KEY", "").strip()
        self.key_file = key_file if key_file else None

    def _base_cmd(self) -> list[str]:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=10",
        ]
        if self.key_file:
            cmd += ["-i", self.key_file]
        return cmd

    def ssh_exec(self, command: str, timeout: int = _DEPLOY_SSH_TIMEOUT) -> SSHResult:
        """Execute a command on the remote node with timeout wrapper (lib/ssh.sh parity).

        ▶ ┌cmd + timeout┐ → ⚡ subprocess.run(timeout=) → ◇ TimeoutExpired? → exit 124 | ⎋ SSHResult

        ## @purpose — Blocking SSH execution with explicit timeout detection.
        ##            Never hangs: subprocess timeout → exit 124 (lib/ssh.sh L32 contract).
        ## @io — ⇥ command: str, timeout: int (s) → ⎋ SSHResult
        ## @complexity — O(1) — single subprocess
        ## @invariants
        ##   - Timeout → exit_code=124, timed_out=True, graceful stderr message
        ##   - Non-zero remote exit → propagated as-is
        """
        logger.info(
            "[IMP:7][NodeSSHClient][ssh_exec] ssh %s@%s (timeout=%ss): %s", self.user, self.host, timeout, command[:160]
        )
        try:
            proc = subprocess.run(
                [*self._base_cmd(), f"{self.user}@{self.host}", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Graceful timeout — mirror lib/ssh.sh exit=124 contract (never hang, never crash)
            logger.info(
                "[IMP:9][NodeSSHClient][ssh_exec] TIMEOUT after %ss — exit 124 (lib/ssh.sh parity)",
                timeout,
            )
            return SSHResult(
                exit_code=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"SSH TIMEOUT: command exceeded {timeout}s (graceful error per lib/ssh.sh L32)",
                timed_out=True,
            )
        logger.info(
            "[IMP:9][NodeSSHClient][ssh_exec] exit=%d (timeout=%s)",
            proc.returncode,
            proc.returncode == 124,
        )
        return SSHResult(
            exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, timed_out=proc.returncode == 124
        )

    def ssh_read(self, command: str, timeout: int = _DEFAULT_SSH_TIMEOUT) -> SSHResult:
        """Read-only SSH command with shorter default timeout (lib/ssh.sh ssh_read parity).

        ## @purpose — Read-only probes (docker ps, state.json cat, curl) with 60s default.
        ## @io — ⇥ command: str, timeout: int (s, default 60) → ⎋ SSHResult
        ## @complexity — O(1) — delegates to ssh_exec
        """
        return self.ssh_exec(command, timeout=timeout)

    def docker_ps(self, project: str | None = None) -> SSHResult:
        """List docker containers (optionally filtered by compose project name)."""
        if project:
            return self.ssh_read(
                f"docker ps --filter 'label=com.docker.compose.project={project}' --format '{{{{.Names}}}} {{{{.Status}}}}'"
            )
        return self.ssh_read("docker ps --format '{{.Names}} {{.Status}}'")

    def http_status(self, port: int, path: str = "/") -> SSHResult:
        """Fetch HTTP status code from the node (curl, no proxy)."""
        return self.ssh_read(f"curl -s -o /dev/null -w '%{{http_code}}' --noproxy '*' http://127.0.0.1:{port}{path}")


# endregion CLASS_NodeSSHClient


# region CLASS_NodeState
class NodeState:
    """Read/reset state.json on the test-VPS — /var/lib/platform/.bootstrap/state.json.

    ## @purpose — E2E assertions over bootstrap state: read phase done flags, reset whole
    ##            state (cold start), reset a single phase (failure-scenario injection).
    ## @io — ⇥ node_ssh: NodeSSHClient → ⎋ per-method results
    ## @complexity — O(N) where N = state.json size (single SSH round-trip per call)
    ## @invariants
    ##   - read_state() returns {} when state.json absent (fresh VPS)
    ##   - phase_done(p) checks status=="done" OR done==true (StepState vs raw-dict formats)
    ##   - reset_state() = rm state.json (documented reset per core/internal/bootstrap/AGENTS.md)
    ##   - reset_phase(p) sets done=false/status=pending via remote python3 (no full reboot)
    ## @rationale DevPlan 095 T3: state.json is the single source of truth for checkpoint
    ##            verification (9 INIT + 5 UPDATE phases) and failure injection (T14).
    """

    def __init__(self, node_ssh: NodeSSHClient) -> None:
        self.ssh = node_ssh
        self.state_file = _STATE_FILE

    def read_state(self) -> dict[str, Any]:
        """Read and parse state.json from the node. Returns {} if absent/corrupt."""
        result = self.ssh.ssh_read(f"cat {self.state_file} 2>/dev/null || echo '{{}}'", timeout=30)
        if result.exit_code != 0:
            logger.warning("[IMP:7][NodeState][read_state] cat failed (exit=%d): %s", result.exit_code, result.stderr)
            return {}
        import json

        try:
            data = json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("[IMP:7][NodeState][read_state] Corrupt state.json: %s", exc)
            return {}
        return data

    def phases(self, mode: str = "init") -> dict[str, Any]:
        """Return the steps dict for the given mode's phase list (init: 9, update: 5)."""
        state = self.read_state()
        steps = state.get("steps", state) if isinstance(state, dict) else {}
        phases = INIT_PHASES if mode == "init" else UPDATE_PHASES
        return {p: steps.get(p) for p in phases if p in steps}

    def phase_done(self, phase: str) -> bool:
        """Return True if the phase is done (status==done OR done==true)."""
        state = self.read_state()
        steps = state.get("steps", state) if isinstance(state, dict) else {}
        entry = steps.get(phase)
        if entry is None:
            return False
        if isinstance(entry, dict):
            return bool(entry.get("done")) or entry.get("status") == "done"
        return bool(getattr(entry, "status", "") == "done")

    def reset_state(self, timeout: int = 60) -> SSHResult:
        """Reset ALL bootstrap state: rm state.json (documented reset mechanism).

        ## @purpose — Cold-start reset per AGENTS.md invariant 9. Equivalent to
        ##            state_machine --force (Clearing state) but without a full bootstrap.
        ## @rationale — make bootstrap-node NODE=X --force is NOT supported by the Makefile
        ##            (make treats --force as a target). rm state.json is the documented
        ##            operator reset (core/internal/bootstrap/AGENTS.md «Сброс»).
        """
        logger.info("[IMP:9][NodeState][reset_state] Removing %s on %s", self.state_file, self.ssh.host)
        result = self.ssh.ssh_exec(f"rm -f {self.state_file}", timeout=timeout)
        if result.exit_code != 0:
            logger.error("[IMP:10][NodeState][reset_state] rm failed: %s", result.stderr)
        return result

    def reset_phase(self, phase: str, timeout: int = 30) -> SSHResult:
        """Reset a single phase to not-done (failure-scenario injection, T14).

        ## @purpose — Set phase done=false/status=pending in state.json via remote python3.
        ##            Sub-steps are preserved so the next bootstrap re-runs the phase.
        ## @io — ⇥ phase: BootstrapPhase key → ⎋ SSHResult
        ## @complexity — O(1) — single remote python3 -c
        """
        py = (
            "import json,sys;"
            f"p='{self.state_file}';"
            "d=json.load(open(p)) if __import__('os').path.exists(p) else {};"
            "s=d.setdefault('steps',{});"
            f"e=s.setdefault('{phase}',{{}});"
            "e['done']=False;e['status']='pending';"
            "json.dump(d,open(p,'w'),indent=2)"
        )
        logger.info("[IMP:9][NodeState][reset_phase] Reset phase '%s' on %s", phase, self.ssh.host)
        result = self.ssh.ssh_exec(f'python3 -c "{py}"', timeout=timeout)
        if result.exit_code != 0:
            logger.error("[IMP:10][NodeState][reset_phase] reset failed: %s", result.stderr)
        return result

    def all_phases_done(self, phases: list[str]) -> tuple[list[str], list[str]]:
        """Return (done_phases, pending_phases) for the given phase list."""
        done: list[str] = []
        pending: list[str] = []
        for phase in phases:
            (done if self.phase_done(phase) else pending).append(phase)
        return done, pending


# endregion CLASS_NodeState


# region FUNC_payload_helpers
def build_payload_tar(project_dir: Path, out_path: Path | None = None) -> Path:
    """Assemble a deploy payload tar.gz from a project directory (orchestrator payload format).

    ▶ ┌project_dir┐ → ○ for fname in (docker-compose.yml, ai-platform.yaml, .env.platform) → ⊕ tar.add → ⎋ tar.gz path

    ## @purpose — Build the tar streamed to `orchestrator_cli receive` on the VPS
    ##            (same file set as DeployOrchestrator._assemble_payload: compose + metadata + env).
    ## @io — ⇥ project_dir: Path, out_path: Path|None → ⎋ Path (tar.gz)
    ## @complexity — O(F) where F = payload file count
    ## @invariants
    ##   - Includes only: docker-compose.yml, compose.yaml, ai-platform.yaml, .env.platform
    ##   - Missing optional files are skipped; empty tar is a valid (but failing) payload
    """
    if out_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".tar.gz", prefix="e2e-payload-")
        os.close(fd)
        out_path = Path(tmp)
    with tarfile.open(out_path, "w:gz") as tar:
        for fname in ("docker-compose.yml", "compose.yaml", "ai-platform.yaml", ".env.platform"):
            fpath = project_dir / fname
            if fpath.is_file():
                tar.add(str(fpath), arcname=fname)
    logger.info("[IMP:9][payload] Built payload tar %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


def deliver_payload_via_ssh(
    node_ssh: NodeSSHClient, tar_path: Path, timeout: int = 300, remote_root: str | None = None
) -> SSHResult:
    """Stream a payload tar to `orchestrator_cli receive` on the node via SSH stdin.

    ▶ ┌tar_path┐ → ⚡ ssh node: cd {remote_root} && PYTHONPATH={remote_root} python3 -m core.internal.deploy.orchestrator_cli receive ⇦ stdin(tar) → ⎋ SSHResult

    ## @purpose — CI-equivalent forced-command delivery (DevPlan 095 T9/T16): the tar is
    ##            piped through stdin to the VPS-side DeployOrchestrator.receive().
    ##            Requires the platform core to be deployed on the VPS (bootstrap SCP).
    ## @io — ⇥ node_ssh, tar_path, remote_root → ⎋ SSHResult (exit 0 = DeployResult success JSON on stdout)
    ## @complexity — O(P) where P = payload size / bandwidth
    ## @invariants
    ##   - Remote command: cd {remote_root} && PYTHONPATH={remote_root} python3 -m core.internal.deploy.orchestrator_cli receive
    ##   - stdin = tar.gz bytes; stdout = DeployResult JSON
    ##   - remote_root resolution: PLATFORM_REMOTE_BASE → PLATFORM_ROOT → repo_root() —
    ##     единая конвенция с scp-deliver.sh:129 / remote-cmd.sh / overlay_deliverer.sync_core_to_vps
    ##     (core на VPS лежит по {remote_root}/core — mirror пути локального репозитория,
    ##     т.к. make bootstrap-node задаёт PLATFORM_ROOT=_platform_root)
    ## 🧐 TRAP[DECISION] · 2026-07-31 · HI · remote_root вместо hardcoded /opt/platform
    ## · Rejected: /opt/platform — DevPlan-эскиз предполагал классическую базу, но bootstrap
    ##   на dev-машине доставляет core в mirror-путь PLATFORM_ROOT (scp-deliver.sh:129),
    ##   /opt/platform на VPS отсутствует → receive падал "cd: /opt/platform: No such file".
    ## · Reason: тест должен делить remote-базу с фактической доставкой (AC4), не с эскизом.
    ## · Rev: если bootstrap перейдёт на PLATFORM_REMOTE_BASE=/opt/platform по умолчанию —
    ##   конвенция сохранится (env имеет приоритет).
    """
    if remote_root is None:
        remote_root = os.environ.get("PLATFORM_REMOTE_BASE") or os.environ.get("PLATFORM_ROOT") or str(repo_root())
    logger.info(
        "[IMP:8][deliver_payload] Streaming %s to receive on %s (remote_root=%s)",
        tar_path.name,
        node_ssh.host,
        remote_root,
    )
    remote_cmd = (
        f"cd {remote_root} && PYTHONPATH={remote_root} python3 -m core.internal.deploy.orchestrator_cli receive"
    )
    with open(tar_path, "rb") as tar_in:
        proc = subprocess.run(
            [*node_ssh._base_cmd(), f"{node_ssh.user}@{node_ssh.host}", remote_cmd],
            stdin=tar_in,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if proc.returncode == 124:
        return SSHResult(
            exit_code=124, stdout=proc.stdout, stderr="SSH TIMEOUT during receive delivery", timed_out=True
        )
    return SSHResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def wait_for_condition(
    node_ssh: NodeSSHClient,
    predicate,
    timeout_s: int,
    interval_s: float = 0.5,
    description: str = "condition",
) -> bool:
    """Poll a remote predicate until True or timeout. Returns False on timeout.

    ## @purpose — Deterministic polling for mid-phase kill windows (T14) and container readiness.
    ## @io — ⇥ predicate: () -> bool (performs its own SSH round-trip) → ⎋ bool
    ## @complexity — O(timeout_s / interval_s) SSH round-trips
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception as exc:
            logger.info("[IMP:7][wait_for_condition] Transient error: %s", exc)
        time.sleep(interval_s)
    logger.warning("[IMP:7][wait_for_condition] TIMEOUT after %ss waiting for %s", timeout_s, description)
    return False


# endregion FUNC_payload_helpers


# region FUNC_assert_ldd_imp9_e2e
def assert_ldd_imp9_e2e(caplog, min_count: int = 1) -> None:
    """Assert LDD trajectory: print IMP:7-10 logs and require ≥min_count IMP:9+ records.

    ## @purpose — DevPlan 095 AC7: every E2E test emits and asserts IMP:9 business logic logs.
    ##            Same semantics as gate_helpers.assert_ldd_imp9 + _print_ldd_trajectory.
    ## @io — ⇥ caplog, min_count → ⎋ None (assert side-effect)
    ## @complexity — O(R) where R = caplog records
    """
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = 0
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found += 1
    print("--- END LDD TRAJECTORY ---")
    assert found >= min_count, (
        f"Critical LDD Error: expected >={min_count} IMP:9+ logs, got {found}. "
        "Tests must emit [IMP:9] business logic logs (Test Honesty LDD)."
    )


# endregion FUNC_assert_ldd_imp9_e2e
