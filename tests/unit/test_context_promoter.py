# GREP_SUMMARY: test-context-promoter context-promote git-mirror ssh audit mock-subprocess tmp-path caplog ldds resolve-org
# STRUCTURE: ┌mock subprocess.run┐ → ○ 3× check_ssh_available → ○ 2× promote_via_ssh → ○ 2× verify_mirror → ○ promote_context (FATAL / audit trail) → ○ 5× resolve_org (platform positive / legacy negative / mixed-case / fallback) → ⎋ 15 tests
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/context_promoter.py — DevPlan 103 §9 $TEST_SPEC
##           + DevPlan 022 TASK-3 ($TEST_SPEC: platform positive + legacy-path negative).
##           Verifies the SSH availability probe (AC4), SSH mirror push (AC4),
##           mirror HEAD verification (AC6), fail-fast FATAL path (SSH-only, 177 W2.1),
##           the audit START/DONE trail (AC9), and the single-candidate org resolution
##           (DevPlan 022: platform/context.yaml — единственный путь, legacy игнорируется).
## @scope    Native imports only; all subprocess.run invocations mocked (no real git/ssh);
##           tmp_path for the audit log; caplog for LDD IMP:7-10 trajectory.
##           Test Honesty R1 (no pass-tests) / R2 (no unfalsifiable asserts) compliant.
## @invariants
##   - No hardcoded paths — tmp_path / monkeypatch env only
##   - No real network or git calls — subprocess.run fully mocked
##   - Every test carries a TRAP[TEST] annotation
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.internal.deploy import context_promoter
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT
from tests.helpers.fakes import make_proc as _proc
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

SSH_TARGET = "git@github.com:myctx/ai-platform.git"
MIRROR_SHA = "a" * 40  # ls-remote HEAD for mismatch scenarios
SOURCE_SHA = "b" * 40  # rev-parse HEAD for mismatch scenarios
SYNC_SHA = "c" * 40  # shared HEAD for the successful full-promote scenario


# T2.16a: _print_trajectory консолидирован в gate_helpers.assert_ldd_imp9 (require_imp9=False)


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

    assert_ldd_imp9(caplog, require_imp9=False)
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

    assert_ldd_imp9(caplog, require_imp9=False)
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

    assert_ldd_imp9(caplog, require_imp9=False)
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

    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
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


# region FUNC_test_promote_via_ssh_argv_ref0103
# 🧪 TRAP[TEST] · Regression · REF-0103 · mirror-argv: GIT_SSH_COMMAND + DEPLOY_TIMEOUT
# · Scenario: promote_via_ssh передаёт env.GIT_SSH_COMMAND (строка "ssh -o ..." из SoT
# ·   ssh_opts.build_rsync_ssh_opts) и timeout=DEPLOY_TIMEOUT на push / SSH_READ_TIMEOUT на ls-remote.
# · Last fail: REF-0103 — git push --mirror шёл без timeout/GIT_SSH_COMMAND → вечный hang
# ·   release-checklist step 4 и транспорт без канонических SSH-флагов (BatchMode/ServerAlive).
# · Remove if: mirror-канал перестаёт использовать SoT ssh_opts/DEPLOY_TIMEOUT
def test_promote_via_ssh_git_ssh_command_and_timeouts(caplog: pytest.LogCaptureFixture) -> None:
    """GIT_SSH_COMMAND присутствует в env; mirror-push/ls-remote несут канонные таймауты."""
    from core.internal.shared.ssh_opts import build_rsync_ssh_opts
    from core.internal.shared.timeouts import DEPLOY_TIMEOUT, SSH_READ_TIMEOUT

    caplog.set_level(logging.INFO)

    with mock.patch(
        "core.internal.deploy.context_promoter.subprocess.run",
        side_effect=[
            _proc(rc=0),  # git push --mirror
            _proc(rc=0, stdout=f"{MIRROR_SHA}\tHEAD\n"),  # git ls-remote
        ],
    ) as mocked:
        context_promoter.promote_via_ssh("myctx")

    push_call, ls_call = mocked.call_args_list

    # GIT_SSH_COMMAND из единого SoT флагов (ssh_opts) — присутствует в env push И ls-remote.
    expected_cmd = build_rsync_ssh_opts()
    for call in (push_call, ls_call):
        env = call.kwargs.get("env") or {}
        assert env.get("GIT_SSH_COMMAND") == expected_cmd, (
            f"GIT_SSH_COMMAND должен быть '{expected_cmd}', got {env.get('GIT_SSH_COMMAND')!r}"
        )
        assert env["GIT_SSH_COMMAND"].startswith("ssh "), "GIT_SSH_COMMAND — ssh-строка"
        assert "BatchMode=yes" in env["GIT_SSH_COMMAND"], "канонические флаги SoT в GIT_SSH_COMMAND"

    # Бюджеты REF-0103: тяжёлый mirror-push = DEPLOY_TIMEOUT, лёгкий ls-remote = SSH_READ_TIMEOUT.
    assert push_call.kwargs.get("timeout") == DEPLOY_TIMEOUT, (
        f"push timeout должен быть DEPLOY_TIMEOUT={DEPLOY_TIMEOUT}"
    )
    assert ls_call.kwargs.get("timeout") == SSH_READ_TIMEOUT, (
        f"ls-remote timeout должен быть SSH_READ_TIMEOUT={SSH_READ_TIMEOUT}"
    )
    logger.critical(
        "[IMP:9][test][REF-0103] mirror argv OK: GIT_SSH_COMMAND=%s… push_timeout=%ds ls_timeout=%ds",
        expected_cmd[:30],
        DEPLOY_TIMEOUT,
        SSH_READ_TIMEOUT,
    )


# endregion FUNC_test_promote_via_ssh_argv_ref0103

# ── verify_mirror (AC6) ───────────────────────────────────────────────────


# region FUNC_test_verify_mirror_match
def test_verify_mirror_match(caplog: pytest.LogCaptureFixture) -> None:
    """mirror_head == source_head → True with IMP:9 log."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: mirror HEAD equals source HEAD
    # · Last fail: N/A (new test, DevPlan 103 §9)
    # · Remove if: mirror verification semantics change

    assert context_promoter.verify_mirror(MIRROR_SHA, MIRROR_SHA) is True

    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
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

    assert context_promoter.verify_mirror(MIRROR_SHA, SOURCE_SHA) is False

    assert_ldd_imp9(caplog, require_imp9=False)
    assert "[IMP:10][verify_mirror] FAIL: mirror HEAD" in caplog.text


# endregion FUNC_test_verify_mirror_mismatch


# ── promote_context orchestrator (AC5/AC9) ────────────────────────────────


# region FUNC_test_no_ssh_fails
def test_no_ssh_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """SSH unavailable → CLI exits 1 (SystemExit) with IMP:10 FATAL (SSH-only channel, 177 W2.1)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: fail-fast when the only channel (SSH) is unavailable
    # · Last fail: N/A (rewritten 177 W2.1 — HTTPS-fallback удалён)
    # · Remove if: channel-selection fail-fast semantics change

    # __main__ block performs sys.exit(main()) — verify the CLI contract end-to-end (DI, W-H)
    with pytest.raises(SystemExit) as exc_info:
        sys.exit(
            context_promoter.main(
                ["myctx"],
                audit_log_file=str(tmp_path / "audit.jsonl"),
                ssh_available_fn=lambda: False,
            )
        )
    assert exc_info.value.code == 1, "CLI must exit 1 when SSH unavailable (no fallback)"

    assert_ldd_imp9(caplog, require_imp9=False)
    assert "[IMP:10][promote_context] FATAL: SSH unavailable" in caplog.text


# endregion FUNC_test_no_ssh_fails


# region FUNC_test_audit_step_imp9
def test_audit_step_imp9(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Successful promote → IMP:9 SUCCESS log + audit START/DONE entries in the JSON-lines log."""
    caplog.set_level(logging.INFO)
    audit_file = tmp_path / "audit.jsonl"

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
        rc = context_promoter.promote_context(
            "myctx", audit_log_file=str(audit_file), ssh_available_fn=lambda: True, secrets_fn=lambda _org, _ctx: True
        )

    assert rc == 0, "SSH promote with matching HEADs must return exit code 0"

    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
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
    # DI (W-H): env= dict (0 setenv PROJECTS_BASE)

    # 🧪 TRAP[TEST] · 2026-08-05 · Regression · D9 — org из overlay context.yaml (f572787)
    # · Scenario: PROJECTS_BASE/<ctx>/platform/context.yaml с org → resolve_org возвращает org
    # · Last fail: 2026-08-04 — org = имя контекста (lowercase) → push «Repository not found»
    # · Remove if: resolve_org перестаёт читать context.yaml#org
    assert context_promoter.resolve_org(ctx, env={"PROJECTS_BASE": str(tmp_path)}) == "TronyxLab"

    logger.critical("[IMP:9][test] _resolve_org из overlay context.yaml — канонический org")
    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
    assert "[IMP:8][resolve_org] org=TronyxLab" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in _resolve_org test"


# endregion FUNC_test_resolve_org_from_overlay_context_yaml


# region FUNC_test_resolve_org_mixed_case_context_name
def test_resolve_org_mixed_case_context_name(
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
    # 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D9 — mixed-case tronyx-lab vs TronyxLab
    # · Scenario: context.yaml org=TronyxLab при имени контекста tronyx-lab → вернётся TronyxLab
    # · Last fail: 2026-08-04 — GitHub SSH case-sensitive: push tronyx-lab/ai-platform «Repository not found»
    # · Remove if: resolve_org больше не читает context.yaml (org из другого источника)
    resolved = context_promoter.resolve_org(ctx, env={"PROJECTS_BASE": str(tmp_path)})
    assert resolved == "TronyxLab", f"Канонический org обязан прийти из context.yaml, got {resolved!r}"
    assert resolved != ctx, "Имя контекста (lowercase) не может быть org'ом (D9 regression)"

    logger.critical("[IMP:9][test] mixed-case org разрешён к каноническому TronyxLab (D9)")
    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in mixed-case org test"


# endregion FUNC_test_resolve_org_mixed_case_context_name


# region FUNC_test_resolve_org_fallback_context_name
def test_resolve_org_fallback_context_name(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D9 fallback: нет context.yaml → org = имя контекста (историческое поведение)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · 2026-08-05 · Regression · D9 — fallback на имя контекста
    # · Scenario: context.yaml отсутствует → resolve_org возвращает context name
    # · Last fail: N/A (fallback — историческое поведение, сохранено фиксом)
    # · Remove if: fallback на имя контекста удаляется
    assert context_promoter.resolve_org("myctx", env={"PROJECTS_BASE": str(tmp_path)}) == "myctx"

    logger.critical("[IMP:9][test] _resolve_org fallback на имя контекста (D9)")
    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
    assert "using context name" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in fallback test"


# endregion FUNC_test_resolve_org_fallback_context_name


# ── resolve_org: single candidate (DevPlan 022 TASK-3) ────────────────────


# region FUNC_test_resolve_org_platform_context_yaml_positive
def test_resolve_org_platform_context_yaml_positive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DevPlan 022 TASK-3 positive: org из <base>/<ctx>/platform/context.yaml#org — единственный кандидат-путь."""
    caplog.set_level(logging.INFO)

    # Изоляция: второй base жёстко = Path.home()/"projects" — на dev-машине оператора
    # ~/projects/<ctx>/platform/context.yaml существует → патчим Path.home на tmp_path.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    ctx = "tronyx-lab"
    platform_yaml = tmp_path / ctx / "platform" / "context.yaml"
    platform_yaml.parent.mkdir(parents=True)
    platform_yaml.write_text("org: TronyxLab\n", encoding="utf-8")
    # DI (W-H): env= dict (0 setenv PROJECTS_BASE)

    # 🧪 TRAP[TEST] · 2026-09-01 · Regression · DevPlan 022 TASK-3 — platform/context.yaml единственный кандидат
    # · Scenario: PROJECTS_BASE/<ctx>/platform/context.yaml с org → resolve_org возвращает org
    # · Last fail: N/A (positive-кейс; legacy-кандидат <ctx>/context.yaml удалён в этом же плане)
    # · Remove if: resolve_org перестаёт читать platform/context.yaml#org
    resolved = context_promoter.resolve_org(ctx, env={"PROJECTS_BASE": str(tmp_path)})
    assert resolved == "TronyxLab", f"org обязан прийти из platform/context.yaml, got {resolved!r}"

    logger.critical("[IMP:9][test] org из platform/context.yaml — единственный кандидат (DevPlan 022)")
    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
    assert "[IMP:8][resolve_org] org=TronyxLab" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in platform positive test"


# endregion FUNC_test_resolve_org_platform_context_yaml_positive


# region FUNC_test_resolve_org_legacy_path_ignored_negative
def test_resolve_org_legacy_path_ignored_negative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DevPlan 022 TASK-3 negative (R5): legacy <ctx>/context.yaml в корне контекста игнорируется."""
    caplog.set_level(logging.INFO)

    # Изоляция: без патча fallback-база ~/projects достаёт РЕАЛЬНЫЙ platform/context.yaml
    # dev-машины (~/projects/tronyx-lab/) — негатив-кейс теряет детерминизм. Обе базы → tmp_path.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    ctx = "tronyx-lab"
    # Точный вход старого бага: legacy-кандидат <ctx>/context.yaml (context_promoter.py:92
    # до DevPlan 022) — сестринский файл вне overlay-контейнера platform/.
    legacy = tmp_path / ctx / "context.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("org: LegacyOrg\n", encoding="utf-8")
    # platform/context.yaml НЕ существует → fallback на имя контекста, НЕ LegacyOrg.

    # 🧪 TRAP[TEST] · 2026-09-01 · NEGATIVE (R5) · DevPlan 022 TASK-3 — legacy-кандидат удалён
    # · Scenario: <ctx>/context.yaml существует, platform/context.yaml нет → org = имя контекста
    # · Last fail: на старом коде (кандидат <ctx>/context.yaml) тест вернул бы LegacyOrg
    # · Remove if: resolve_org снова получает второй кандидат-путь context.yaml
    resolved = context_promoter.resolve_org(ctx, env={"PROJECTS_BASE": str(tmp_path)})
    assert resolved == ctx, f"Legacy <ctx>/context.yaml обязан игнорироваться, got {resolved!r}"
    assert resolved != "LegacyOrg", "org из legacy-пути просочился (DevPlan 022 TASK-3 regression)"

    logger.critical("[IMP:9][test] legacy <ctx>/context.yaml игнорируется (DevPlan 022 TASK-3)")
    found_imp9 = assert_ldd_imp9(caplog, require_imp9=False)
    assert "No context.yaml org found" in caplog.text
    assert found_imp9, "Critical LDD Error: No IMP:9 log found in legacy-path negative test"


# endregion FUNC_test_resolve_org_legacy_path_ignored_negative
