#!/usr/bin/env python3
# GREP_SUMMARY: test-github-ops create-github-repo gh-cli dry-run graceful-skip remote push
# STRUCTURE: ┌6 test functions┐ → ◇ dry-run (1) → ◇ gh missing (1) → ◇ repo exists (2) → ◇ create+push (1) → ◇ create fail (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scaffold/github_ops.py — create_github_repo() direct tests
##           (DevPlan 117 G T58.1 extraction from project_scaffolder.py).
## @scope    No real gh/git calls — all subprocess runs mocked.
## @invariants
##   - All subprocess calls mocked (no network, no gh CLI)
##   - dry_run=True → no subprocess calls at all
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.1 §TEST_SPEC — github_ops direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.1 — created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest import mock

from core.internal.scaffold.github_ops import create_github_repo

logger = logging.getLogger(__name__)


class TestCreateGithubRepo:
    """Tests for create_github_repo() — all subprocess mocked."""

    # 🧪 TRAP[TEST] · Regression · Scenario: dry_run=True
    # · Expect: returns True, no subprocess/gh calls at all
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: dry_run logic changes
    def test_create_repo_dry_run(self, tmp_path: Path, caplog) -> None:
        """dry_run=True → True without touching subprocess."""
        caplog.set_level(0)
        with mock.patch(
            "core.internal.scaffold.github_ops.shutil.which", return_value="/usr/local/bin/gh"
        ) as mock_which:
            result = create_github_repo("org1", "proj1", str(tmp_path), dry_run=True)

        assert result is True
        mock_which.assert_called_once_with("gh")

    # 🧪 TRAP[TEST] · Regression · Scenario: gh CLI not installed
    # · Expect: returns True (graceful non-fatal skip), WARN logged
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: gh-missing graceful fallback changes
    def test_create_repo_no_gh(self, tmp_path: Path, caplog) -> None:
        """gh missing → True (non-fatal) + WARN log."""
        caplog.set_level(0)
        with (
            mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value=None),
            mock.patch("core.internal.scaffold.github_ops.subprocess.run") as mock_run,
        ):
            result = create_github_repo("org1", "proj1", str(tmp_path))

        assert result is True
        mock_run.assert_not_called()
        assert any("gh CLI not found" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: repo already exists on GitHub
    # · Expect: skips creation, adds remote if origin not set
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: repo-exists branch logic changes
    def test_create_repo_exists_adds_remote(self, tmp_path: Path, caplog) -> None:
        """Repo exists → skip creation; origin missing → remote add."""
        caplog.set_level(0)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "gh":
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "get-url" in cmd:
                return mock.MagicMock(returncode=1, stdout="", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/local/bin/gh"),
            mock.patch("core.internal.scaffold.github_ops.subprocess.run", side_effect=mock_run),
        ):
            result = create_github_repo("org1", "proj1", str(tmp_path))

        assert result is True
        add_remote_calls = [c for c in calls if c[:3] == ["git", "remote", "add"]]
        assert len(add_remote_calls) == 1
        assert add_remote_calls[0][4] == "git@github.com:org1/proj1.git"

    # 🧪 TRAP[TEST] · Regression · Scenario: repo exists and origin already set
    # · Expect: no remote add call
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: repo-exists branch logic changes
    def test_create_repo_exists_origin_set(self, tmp_path: Path, caplog) -> None:
        """Repo exists + origin set → no remote add."""
        caplog.set_level(0)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "gh":
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "get-url" in cmd:
                return mock.MagicMock(returncode=0, stdout="git@github.com:org1/proj1.git\n", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/local/bin/gh"),
            mock.patch("core.internal.scaffold.github_ops.subprocess.run", side_effect=mock_run),
        ):
            result = create_github_repo("org1", "proj1", str(tmp_path))

        assert result is True
        add_remote_calls = [c for c in calls if c[:3] == ["git", "remote", "add"]]
        assert len(add_remote_calls) == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: fresh repo → gh create + remote + push
    # · Expect: push succeeds → "Initial push to origin/main complete"
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: create+push branch logic changes
    def test_create_repo_fresh_push(self, tmp_path: Path, caplog) -> None:
        """Repo created + pushed → True."""
        caplog.set_level(0)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "gh" and cmd[1] == "repo" and cmd[2] == "view":
                return mock.MagicMock(returncode=1, stdout="", stderr="not found")
            if cmd[0] == "gh":
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/local/bin/gh"),
            mock.patch("core.internal.scaffold.github_ops.subprocess.run", side_effect=mock_run),
        ):
            result = create_github_repo("org1", "proj1", str(tmp_path))

        assert result is True
        create_calls = [c for c in calls if c[:3] == ["gh", "repo", "create"]]
        assert len(create_calls) == 1
        push_calls = [c for c in calls if c[:3] == ["git", "push", "-u"]]
        assert len(push_calls) == 1
        assert any("Initial push to origin/main complete" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: gh create fails (returncode != 0)
    # · Expect: WARN "Failed to create GitHub repo", returns True (non-fatal)
    # · Last fail: None (new test for DevPlan 117 G T58.1)
    # · Remove if: create-failure branch logic changes
    def test_create_repo_gh_create_fails(self, tmp_path: Path, caplog) -> None:
        """gh create fails → WARN + True (non-fatal)."""
        caplog.set_level(0)

        def mock_run(cmd, **kwargs):
            if cmd[0] == "gh":
                return mock.MagicMock(returncode=1, stdout="", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("core.internal.scaffold.github_ops.shutil.which", return_value="/usr/local/bin/gh"),
            mock.patch("core.internal.scaffold.github_ops.subprocess.run", side_effect=mock_run),
        ):
            result = create_github_repo("org1", "proj1", str(tmp_path))

        assert result is True
        assert any("Failed to create GitHub repo" in r.message for r in caplog.records)


# ── R5 ANTI-SURVIVORSHIP (DevPlan 118 D5) ─────────────────────────────────────
# Волна 117 G T58.1: реализация create_github_repo извлечена в github_ops.py,
# project_scaffolder.create_github_repo стал lazy-facade (0 дублей). DevPlan 118 D5
# верифицирует: дублирующая реализация НЕ должна вернуться в project_scaffolder.


# 🧪 TRAP[TEST] · 2026-08-02 · R5 NEGATIVE · create_github_repo дубль в project_scaffolder
# · Last fail: add-project.sh:591-628 — локальная реализация gh/git-логики в scaffolder
# · Remove if: github_ops.py удаляется (тогда delegator тоже должен быть удалён)
def test_project_scaffolder_no_duplicate_github_repo_impl(caplog) -> None:
    """project_scaffolder.create_github_repo — lazy facade, НЕ дублирующая реализация (D5)."""
    import inspect

    import core.internal.scaffold.project_scaffolder as ps

    caplog.set_level(0)

    logger.info("[IMP:7][test_github_ops][R5] Scanning project_scaffolder for duplicate gh/git logic")
    src = inspect.getsource(ps.create_github_repo)

    # Facade должен делегировать в github_ops (lazy import)…
    assert "from core.internal.scaffold.github_ops import create_github_repo" in src, (
        "D5 regression: project_scaffolder.create_github_repo не делегирует в github_ops"
    )
    # …и НЕ содержать локальную gh/git-реализацию (дубль удалён в 117 G T58.1).
    # Docstring-инварианты фасада упоминают "gh not found" (документация graceful-skip) —
    # проверяем ИСПОЛНЯЕМУЮ часть (тело функции), а не комментарии.
    body = src.split('"""')[1] if '"""' in src else src  # после docstring
    body = body.split("from core.internal.scaffold.github_ops")[0]  # до lazy import
    assert "subprocess" not in body and "shutil" not in body, (
        "D5 regression: локальная gh-реализация (subprocess/shutil) вернулась в project_scaffolder"
    )
    logger.info("[IMP:9][test_github_ops][R5] PASS: project_scaffolder — чистый lazy facade (0 дублей)")
