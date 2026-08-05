#!/usr/bin/env python3
# GREP_SUMMARY: test-context-promoter context-promote git-mirror ssh https askpass audit mock-subprocess tmp-path caplog ldds ac7
# STRUCTURE: ┌mock subprocess.run┐ → ○ 3× check_ssh_available → ○ 2× promote_via_ssh → ○ 3× promote_via_https (AC7 token safety) → ○ 2× verify_mirror → ○ promote_context (FATAL / audit trail) → ⎋ 12 tests
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/context_promoter.py — DevPlan 103 §9 $TEST_SPEC.
##           Verifies the SSH availability probe (AC4), SSH/HTTPS mirror push (AC4/AC5),
##           GIT_ASKPASS token safety (AC7), mirror HEAD verification (AC6), fail-fast
##           FATAL path (AC5), and the audit START/DONE trail (AC9).
## @scope    Native imports only; all subprocess.run invocations mocked (no real git/ssh);
##           tmp_path for the audit log; caplog for LDD IMP:7-10 trajectory.
##           Test Honesty R1 (no pass-tests) / R2 (no unfalsifiable asserts) compliant.
## @invariants
##   - No hardcoded paths — tmp_path / monkeypatch env only
##   - No real network or git calls — subprocess.run fully mocked
##   - Token literal never appears in mocked argv (AC7 regression guard)
##   - Every test carries a TRAP[TEST] annotation
# endregion MODULE_CONTRACT

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.internal.deploy import context_promoter
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

SSH_TARGET = "git@github.com:myctx/ai-platform.git"
HTTPS_URL = "https://github.com/myctx/ai-platform.git"
MIRROR_SHA = "a" * 40  # ls-remote HEAD for mismatch scenarios
SOURCE_SHA = "b" * 40  # rev-parse HEAD for mismatch scenarios
SYNC_SHA = "c" * 40  # shared HEAD for the successful full-promote scenario
FAKE_TOKEN = "ghp_SUPER_SECRET_TOKEN_VALUE_1337"


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a CompletedProcess stub for mocked subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _print_trajectory(caplog: pytest.LogCaptureFixture) -> bool:
    """Print IMP:7-10 LDD trajectory; return True if an IMP:9 log was found.

    ## @purpose — LDD telemetry: surfaces the actual execution trajectory before assertions
    ##            so a failure shows the agent the real path, not just a red assert.
    ## @io — ⇥ caplog → ⎋ bool — True when at least one IMP:9 record was emitted
    """
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
    return found_imp9


# ── check_ssh_available (AC4) ─────────────────────────────────────────────


# region FUNC_test_check_ssh_available_success
def test_check_ssh_available_success(caplog: pytest.LogCaptureFixture) -> None:
    """SSH auth greeting on stderr → True, even with exit code 1 (content-based probe)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: authenticated greeting, exit code 1
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: SSH availability probing switches to a non-content mechanism

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        return_value=_proc(
            rc=1,  # ssh -T exits 1 even on success — exit code must NOT gate the result
            stderr="Hi tronyx! You've successfully authenticated, but GitHub does not provide shell access.",
        ),
    ) as mocked:
        assert context_promoter.check_ssh_available() is True

    args = mocked.call_args.args[0]
    assert args[:1] == ["ssh"], f"Expected ssh invocation, got: {args}"
    # DevPlan 116 B5 T2: ConnectTimeout унифицирован через timeouts.SSH_CONNECT_TIMEOUT (=30, U-15)
    assert "-o" in args and f"ConnectTimeout={SSH_CONNECT_TIMEOUT}" in args and "BatchMode=yes" in args

    _print_trajectory(caplog)
    assert "[IMP:8][check_ssh_available] SSH key for github.com available" in caplog.text


# endregion FUNC_test_check_ssh_available_success


# region FUNC_test_check_ssh_available_failure
def test_check_ssh_available_failure(caplog: pytest.LogCaptureFixture) -> None:
    """SSH timeout / connection refused (no auth marker) → False."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: network failure — no auth marker anywhere
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: SSH availability probing switches to a non-content mechanism

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        return_value=_proc(
            rc=255,
            stderr="ssh: connect to host github.com port 22: Connection refused",
        ),
    ):
        assert context_promoter.check_ssh_available() is False

    _print_trajectory(caplog)
    assert "[IMP:8][check_ssh_available] SSH key not available or timeout" in caplog.text


# endregion FUNC_test_check_ssh_available_failure


# region FUNC_test_check_ssh_available_not_authenticated
def test_check_ssh_available_not_authenticated(caplog: pytest.LogCaptureFixture) -> None:
    """Exit code 1 with NO auth marker in stderr → False (unauthenticated, not timeout)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: publickey denied — no "Hi"/"successfully authenticated"
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: SSH availability probing switches to a non-content mechanism

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        return_value=_proc(rc=1, stderr="git@github.com: Permission denied (publickey)."),
    ):
        assert context_promoter.check_ssh_available() is False

    _print_trajectory(caplog)
    assert "[IMP:8][check_ssh_available] SSH key not available or timeout" in caplog.text


# endregion FUNC_test_check_ssh_available_not_authenticated


# ── promote_via_ssh (AC4) ─────────────────────────────────────────────────


# region FUNC_test_promote_via_ssh_success
def test_promote_via_ssh_success(caplog: pytest.LogCaptureFixture) -> None:
    """git push --mirror exit 0 + ls-remote HEAD → returns MIRROR_HEAD."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: SSH mirror push + HEAD readback
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: SSH mirror channel is reworked

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        side_effect=[
            _proc(rc=0),  # git push --mirror
            _proc(rc=0, stdout=f"{MIRROR_SHA}\tHEAD\n"),  # git ls-remote
        ],
    ) as mocked:
        assert context_promoter.promote_via_ssh("myctx") == MIRROR_SHA

    push_call, ls_call = mocked.call_args_list
    assert push_call.args[0] == ["git", "push", "--mirror", SSH_TARGET]
    assert ls_call.args[0] == ["git", "ls-remote", SSH_TARGET, "HEAD"]

    found_imp9 = _print_trajectory(caplog)
    assert "[IMP:9][promote_via_ssh] SSH push to myctx/ai-platform successful" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_promote_via_ssh_success


# region FUNC_test_promote_via_ssh_failure
def test_promote_via_ssh_failure() -> None:
    """git push --mirror exit 1 → CalledProcessError propagates (check=True)."""
    # 🧪 TRAP[TEST] · Regression · Scenario: push rejected — CalledProcessError must surface
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: push failure handling moves out of subprocess.run check=True

    with (
        mock.patch(
            "core.internal.deploy.context_promoter.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["git", "push", "--mirror", SSH_TARGET]),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        context_promoter.promote_via_ssh("myctx")


# endregion FUNC_test_promote_via_ssh_failure


# ── promote_via_https (AC5/AC7) ───────────────────────────────────────────


# region FUNC_test_promote_via_https_success
def test_promote_via_https_success(caplog: pytest.LogCaptureFixture) -> None:
    """HTTPS push with GIT_ASKPASS env → returns MIRROR_HEAD; script content is LITERAL."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: HTTPS fallback with GIT_ASKPASS credential delivery
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: GIT_ASKPASS credential mechanism is replaced

    unlinked: list[str] = []

    def _fake_unlink(path: str) -> None:  # keep the temp file for content inspection
        unlinked.append(path)

    with (
        mock.patch(
            "core.internal.deploy.context_promoter.subprocess.run",
            side_effect=[
                _proc(rc=0),  # git push --mirror
                _proc(rc=0, stdout=f"{MIRROR_SHA}\tHEAD\n"),  # git ls-remote
            ],
        ) as mocked,
        mock.patch.object(context_promoter.os, "unlink", side_effect=_fake_unlink),
    ):
        assert context_promoter.promote_via_https("myctx", FAKE_TOKEN) == MIRROR_SHA

    askpass_path = mocked.call_args_list[0].kwargs["env"]["GIT_ASKPASS"]
    assert "GIT_ASKPASS" in mocked.call_args_list[0].kwargs["env"]

    # QA Review D4: script must contain the VARIABLE NAME, never the token value
    script = Path(askpass_path).read_text(encoding="utf-8")
    assert "${GIT_MIRROR_TOKEN}" in script, "GIT_ASKPASS script must echo the literal ${GIT_MIRROR_TOKEN}"
    assert FAKE_TOKEN not in script, "TOKEN LEAK: token value written to GIT_ASKPASS script on disk"
    assert unlinked == [askpass_path], "GIT_ASKPASS tempfile must be cleaned up exactly once"

    found_imp9 = _print_trajectory(caplog)
    assert "[IMP:9][promote_via_https] HTTPS push to myctx/ai-platform successful" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    Path(askpass_path).unlink(missing_ok=True)  # test hygiene — file was kept for inspection


# endregion FUNC_test_promote_via_https_success


# region FUNC_test_promote_via_https_token_not_in_argv
def test_promote_via_https_token_not_in_argv(caplog: pytest.LogCaptureFixture) -> None:
    """AC7: the token value never appears in any subprocess argv; URL is clean."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: AC7 token-leak guard (QA Review RR1)
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: GIT_ASKPASS credential mechanism is replaced

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        side_effect=[
            _proc(rc=0),  # git push --mirror
            _proc(rc=0, stdout=f"{MIRROR_SHA}\tHEAD\n"),  # git ls-remote
        ],
    ) as mocked:
        assert context_promoter.promote_via_https("myctx", FAKE_TOKEN) == MIRROR_SHA

    for call in mocked.call_args_list:
        argv = call.args[0]
        assert FAKE_TOKEN not in str(argv), f"TOKEN LEAK: token found in argv: {argv}"

    urls = [arg for call in mocked.call_args_list for arg in call.args[0] if str(arg).startswith("https://github.com/")]
    assert urls == [HTTPS_URL, HTTPS_URL], f"URL must be token-free {HTTPS_URL}, got: {urls}"
    assert "@" not in urls[0], "URL must not embed credentials (user@host form)"

    _print_trajectory(caplog)
    assert "[IMP:9][promote_via_https] HTTPS push to myctx/ai-platform successful" in caplog.text


# endregion FUNC_test_promote_via_https_token_not_in_argv


# region FUNC_test_promote_via_https_cleanup_tempfile
def test_promote_via_https_cleanup_tempfile(caplog: pytest.LogCaptureFixture) -> None:
    """AC7: after a successful push the GIT_ASKPASS temp script is deleted (os.unlink called)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: tempfile lifecycle — real os.unlink must run
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: GIT_ASKPASS tempfile lifecycle is reworked

    with (
        mock.patch(
            "core.internal.deploy.context_promoter.subprocess.run",
            side_effect=[
                _proc(rc=0),  # git push --mirror
                _proc(rc=0, stdout=f"{MIRROR_SHA}\tHEAD\n"),  # git ls-remote
            ],
        ) as mocked,
        mock.patch.object(context_promoter.os, "unlink", wraps=os.unlink) as unlink_mock,
    ):
        context_promoter.promote_via_https("myctx", FAKE_TOKEN)

    askpass_path = mocked.call_args_list[0].kwargs["env"]["GIT_ASKPASS"]
    assert unlink_mock.call_count == 1, "os.unlink must be called exactly once"
    assert not Path(askpass_path).exists(), "GIT_ASKPASS tempfile must not survive the operation"

    _print_trajectory(caplog)
    assert "[IMP:8][promote_via_https] Removed GIT_ASKPASS tempfile" in caplog.text


# endregion FUNC_test_promote_via_https_cleanup_tempfile


# ── verify_mirror (AC6) ───────────────────────────────────────────────────


# region FUNC_test_verify_mirror_match
def test_verify_mirror_match(caplog: pytest.LogCaptureFixture) -> None:
    """mirror_head == source_head → True with IMP:9 log."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: mirror HEAD equals source HEAD
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: mirror verification semantics change

    assert context_promoter.verify_mirror("myctx", MIRROR_SHA, MIRROR_SHA) is True

    found_imp9 = _print_trajectory(caplog)
    assert "[IMP:9][verify_mirror] Mirror sync verified" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_verify_mirror_match


# region FUNC_test_verify_mirror_mismatch
def test_verify_mirror_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """mirror_head != source_head → False with IMP:10 FAIL log."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: remote mirror HEAD diverges from local HEAD
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: mirror verification semantics change

    assert context_promoter.verify_mirror("myctx", MIRROR_SHA, SOURCE_SHA) is False

    _print_trajectory(caplog)
    assert "[IMP:10][verify_mirror] FAIL: mirror HEAD" in caplog.text


# endregion FUNC_test_verify_mirror_mismatch


# ── promote_context orchestrator (AC5/AC9) ────────────────────────────────


# region FUNC_test_no_ssh_no_token_fails
def test_no_ssh_no_token_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """SSH unavailable + no GIT_MIRROR_TOKEN → CLI exits 1 (SystemExit) with IMP:10 FATAL."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("GIT_MIRROR_TOKEN", raising=False)
    monkeypatch.setenv("AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(context_promoter, "check_ssh_available", lambda: False)

    # 🧪 TRAP[TEST] · Regression · Scenario: fail-fast when both channels unavailable
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: channel-selection fail-fast semantics change

    # __main__ block performs sys.exit(main()) — verify the CLI contract end-to-end
    with pytest.raises(SystemExit) as exc_info:
        sys.exit(context_promoter.main(["myctx"]))
    assert exc_info.value.code == 1, "CLI must exit 1 when SSH unavailable AND token missing"

    _print_trajectory(caplog)
    assert "[IMP:10][promote_context] FATAL: SSH unavailable AND GIT_MIRROR_TOKEN not set" in caplog.text


# endregion FUNC_test_no_ssh_no_token_fails


# region FUNC_test_audit_step_imp9
def test_audit_step_imp9(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Successful promote → IMP:9 SUCCESS log + audit START/DONE entries in the JSON-lines log."""
    caplog.set_level(logging.INFO)
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_FILE", str(audit_file))
    monkeypatch.setattr(context_promoter, "check_ssh_available", lambda: True)

    # 🧪 TRAP[TEST] · Regression · Scenario: full SSH promote with audit START/DONE trail
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: audit-trail semantics in promote_context change

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        side_effect=[
            _proc(rc=0),  # git push --mirror (SSH)
            _proc(rc=0, stdout=f"{SYNC_SHA}\tHEAD\n"),  # git ls-remote
            _proc(rc=0, stdout=f"{SYNC_SHA}\n"),  # git rev-parse HEAD
        ],
    ):
        rc = context_promoter.promote_context("myctx", token=None)

    assert rc == 0, "SSH promote with matching HEADs must return exit code 0"

    found_imp9 = _print_trajectory(caplog)
    assert "[IMP:9][promote_context] SUCCESS: platform promoted to myctx/ai-platform" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    # Audit trail: JSON-lines log contains START and DONE entries with the correct tag
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    statuses = [json.loads(line)["status"] for line in lines]
    assert "START" in statuses, f"Audit log missing START entry: {statuses}"
    assert "DONE" in statuses, f"Audit log missing DONE entry: {statuses}"
    for line in lines:
        assert json.loads(line)["tag"] == "context-promote:myctx"


# endregion FUNC_test_audit_step_imp9


# ── _resolve_org (D9 — DevPlan 136 W1 T1.6, GitHub SSH case-sensitivity) ─────


# region FUNC_test_resolve_org_from_overlay_context_yaml
def test_resolve_org_from_overlay_context_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D9: org резолвится из overlay context.yaml#org (SoT), а не из имени контекста."""
    caplog.set_level(logging.INFO)

    ctx = "tronyx-lab"
    overlay = tmp_path / ctx / "platform" / "context.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("org: TronyxLab\n", encoding="utf-8")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))

    # 🧪 TRAP[TEST] · 2026-08-05 · Regression · D9 — org из overlay context.yaml (f572787)
    # · Scenario: PROJECTS_ROOT/<ctx>/platform/context.yaml с org → resolve_org возвращает org
    # · Last fail: 2026-08-04 — org = имя контекста (lowercase) → push «Repository not found»
    # · Remove if: resolve_org перестаёт читать context.yaml#org
    assert context_promoter.resolve_org(ctx) == "TronyxLab"

    logger.critical("[IMP:9][test] _resolve_org из overlay context.yaml — канонический org")
    found_imp9 = _print_trajectory(caplog)
    assert "[IMP:8][resolve_org] org=TronyxLab" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in _resolve_org test"


# endregion FUNC_test_resolve_org_from_overlay_context_yaml


# region FUNC_test_resolve_org_mixed_case_context_name
def test_resolve_org_mixed_case_context_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5 negative (D9): mixed-case вход — context 'tronyx-lab', org 'TronyxLab' (канонический кейс)."""
    caplog.set_level(logging.INFO)

    # Точный вход бага: имя контекста в lowercase (tronyx-lab), org в context.yaml — канонический кейс
    ctx = "tronyx-lab"
    overlay = tmp_path / ctx / "platform" / "context.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("org: TronyxLab\n", encoding="utf-8")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))

    # 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D9 — mixed-case tronyx-lab vs TronyxLab
    # · Scenario: context.yaml org=TronyxLab при имени контекста tronyx-lab → вернётся TronyxLab
    # · Last fail: 2026-08-04 — GitHub SSH case-sensitive: push tronyx-lab/ai-platform «Repository not found»
    # · Remove if: resolve_org больше не читает context.yaml (org из другого источника)
    resolved = context_promoter.resolve_org(ctx)
    assert resolved == "TronyxLab", f"Канонический org обязан прийти из context.yaml, got {resolved!r}"
    assert resolved != ctx, "Имя контекста (lowercase) не может быть org'ом (D9 regression)"

    logger.critical("[IMP:9][test] mixed-case org разрешён к каноническому TronyxLab (D9)")
    found_imp9 = _print_trajectory(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in mixed-case org test"


# endregion FUNC_test_resolve_org_mixed_case_context_name


# region FUNC_test_resolve_org_fallback_context_name
def test_resolve_org_fallback_context_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D9 fallback: нет context.yaml → org = имя контекста (историческое поведение)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))

    # 🧪 TRAP[TEST] · 2026-08-05 · Regression · D9 — fallback на имя контекста
    # · Scenario: context.yaml отсутствует → resolve_org возвращает context name
    # · Last fail: N/A (fallback — историческое поведение, сохранено фиксом)
    # · Remove if: fallback на имя контекста удаляется
    assert context_promoter.resolve_org("myctx") == "myctx"

    logger.critical("[IMP:9][test] _resolve_org fallback на имя контекста (D9)")
    found_imp9 = _print_trajectory(caplog)
    assert "using context name" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in fallback test"


# endregion FUNC_test_resolve_org_fallback_context_name
