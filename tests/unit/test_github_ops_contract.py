# GREP_SUMMARY: github-ops-contract timeout GITHUB_OPS_TIMEOUT false-on-failure push AI-0017 AI-0037
# STRUCTURE: ▶ mock subprocess.run → ◇ TimeoutExpired (gh/push) → ⎋ False + IMP:9 │ ◇ push rc≠0 → ⎋ False │ ▶ все вызовы несут timeout
# region MODULE_CONTRACT
## @purpose  AI-0017+AI-0037 (DevPlan 17 T3.3): gh/git подвызовы несут GITHUB_OPS_TIMEOUT
##           (зависшая сеть → False по таймауту, не вечный hang); фейл создания repo и
##           начального push возвращает False — неудача больше НЕ репортится успехом.
## @scope    tests/unit: monkeypatched subprocess.run; без сети/gh.
## @invariants
##   - Каждый subprocess.run вызов получает timeout=GITHUB_OPS_TIMEOUT
##   - TimeoutExpired при gh repo create / git push → return False + IMP:9 ERROR
##   - push rc≠0 → False; create rc≠0 → False; skip-пути (нет gh, dry-run, repo exists) → True
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.internal.scaffold.github_ops import create_github_repo
from core.internal.shared.timeouts import GITHUB_OPS_TIMEOUT

logger = logging.getLogger(__name__)


def _run_capture(calls: list[tuple[list[str], dict[str, object]]], behavior):
    """Фабрика side_effect для subprocess.run: пишет cmd+kwargs и применяет behavior."""

    def _run(cmd: list[str], **kwargs: object) -> object:
        calls.append((cmd, kwargs))
        result = behavior(cmd)
        if isinstance(result, Exception):
            raise result
        return result

    return _run


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · таймауты + честный False на фейлах (AI-0017+AI-0037)
# · Regression: gh/git без timeout висели вечно на зависшей сети; фейл создания/push
#   репортился успехом (return True) — scaffolder молча продолжал без repo
# · Scenario: (1) gh view/create timeout → TimeoutExpired перехвачен → False;
#   (2) push rc≠0 → False; (3) КАЖДЫЙ subprocess.run получил timeout=GITHUB_OPS_TIMEOUT
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0017/AI-0037)
# · Remove if: github_ops переезжает на run_subprocess-канон с централизованным таймаутом
def test_timeout_and_false_on_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Зависший gh → False по таймауту; фейлы → False; таймаут передан во все вызовы."""
    caplog.set_level(0)

    # ── 1. Зависший gh repo view (repo-exists probe) → TimeoutExpired → False ──
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _hang_view(cmd: list[str]):
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.TimeoutExpired(cmd, timeout=GITHUB_OPS_TIMEOUT)
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    with (
        mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/bin/gh"),
        mock.patch(
            "core.internal.scaffold.github_ops.subprocess.run",
            side_effect=_run_capture(calls, _hang_view),
        ),
    ):
        assert create_github_repo("org", "proj", str(tmp_path)) is False

    for cmd, kwargs in calls:
        print(f"[IMP:8][timeout-contract] {cmd[0]} {cmd[1]}: timeout={kwargs.get('timeout')}")
        assert kwargs.get("timeout") == GITHUB_OPS_TIMEOUT, f"вызов {cmd[:3]} обязан нести таймаут"
    assert any("timed out" in r.getMessage() for r in caplog.records), "IMP:9 ошибка таймаута обязательна"

    # ── 2. Зависший git push после успешного создания → False ──
    caplog.clear()
    calls.clear()

    def _hang_push(cmd: list[str]):
        if cmd[:3] == ["gh", "repo", "view"]:
            return mock.MagicMock(returncode=1, stdout="", stderr="not found")
        if cmd[:3] == ["gh", "repo", "create"]:
            return mock.MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "push"]:
            return subprocess.TimeoutExpired(cmd, timeout=GITHUB_OPS_TIMEOUT)
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    with (
        mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/bin/gh"),
        mock.patch(
            "core.internal.scaffold.github_ops.subprocess.run",
            side_effect=_run_capture(calls, _hang_push),
        ),
    ):
        assert create_github_repo("org", "proj", str(tmp_path)) is False, (
            "фейл начального push обязан возвращать False (AI-0037)"
        )

    # ── 3. push rc≠0 → False ──
    caplog.clear()

    def _push_fails(cmd: list[str]):
        if cmd[:3] == ["gh", "repo", "view"]:
            return mock.MagicMock(returncode=1, stdout="", stderr="not found")
        if cmd[:2] == ["git", "push"]:
            return mock.MagicMock(returncode=128, stdout="", stderr="rejected")
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    with (
        mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/bin/gh"),
        mock.patch(
            "core.internal.scaffold.github_ops.subprocess.run",
            side_effect=_run_capture([], _push_fails),
        ),
    ):
        assert create_github_repo("org", "proj", str(tmp_path)) is False

    # Skip-пути остаются честным True (graceful): нет gh CLI
    with (
        mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value=None),
        mock.patch("core.internal.scaffold.github_ops.subprocess.run") as never_run,
    ):
        assert create_github_repo("org", "proj", str(tmp_path)) is True
        never_run.assert_not_called()

    logger.critical("[IMP:9][test] timeouts enforced + honest failure returns — OK (T3.3)")
