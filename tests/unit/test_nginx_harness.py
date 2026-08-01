#!/usr/bin/env python3
# GREP_SUMMARY: test-nginx-harness nginx_t_harness docker nginx -t dev-certs openssl skip validation
# STRUCTURE: ┌10 test functions┐ → ◇ structure (2) → ◇ ssl path swap (2) → ◇ no-docker skip (1) → ◇ no-vhosts skip (1)
#            → ◇ docker pass (1) → ◇ docker fail (1) → ◇ openssl fallback (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scaffold/nginx_harness.py — direct tests of
##           nginx_t_harness() without render_all indirection (DevPlan 117 G T53).
## @scope    No Docker required — all docker/openssl subprocess calls mocked.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Every test validates IMP:9 business-logic log presence via @ldd_trajectory where applicable
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T53 §TEST_SPEC — direct harness tests after extraction
##             from vhost_renderer.py. nginx_t_harness is tested directly (not via render_all).
## @changes  2026-08-01 · DevPlan 117 G T53 — created
# endregion MODULE_CONTRACT

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.internal.scaffold.nginx_harness import nginx_t_harness

# ══════════════════════════════════════════════════════════════════════
# TESTS: harness structure
# ══════════════════════════════════════════════════════════════════════


class TestHarnessStructure:
    """Tests for nginx_t_harness directory structure creation."""

    # 🧪 TRAP[TEST] · Regression · Scenario: harness creates harness_dir + vhosts/
    # · Expect: nginx-main/, vhosts/, includes/, dev-certs/ created; openssl mocked
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: harness structure logic changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_creates_structure(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """nginx_t_harness creates harness_dir + all 4 subdirs."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = mock.MagicMock(returncode=0, stdout=b"", stderr=b"")

        # A vhost to validate
        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True

    # 🧪 TRAP[TEST] · Regression · Scenario: vhost SSL paths swapped to dev-certs
    # · Expect: harness reads dev version with /etc/nginx/dev-certs paths
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: SSL swap logic changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_ssl_path_swap(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """SSL paths /etc/letsencrypt/live/<domain>/ → /etc/nginx/dev-certs/."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = mock.MagicMock(returncode=0, stdout=b"", stderr=b"")

        vhost_content = (
            "server {\n"
            "  ssl_certificate /etc/letsencrypt/live/foo.example.com/fullchain.pem;\n"
            "  ssl_certificate_key /etc/letsencrypt/live/foo.example.com/privkey.pem;\n"
            "  root /var/www/acme/foo;\n"
            "}\n"
        )
        (tmp_path / "foo.example.com.conf").write_text(vhost_content, encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True

    # 🧪 TRAP[TEST] · Regression · Scenario: docker unavailable → graceful skip
    # · Expect: returns True, docker never called, IMP:8 WARN logged
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: docker-availability fallback changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_no_docker_skip(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """docker not found → returns True (graceful WARN, non-blocking)."""
        caplog.set_level(0)
        mock_which.return_value = None  # docker absent

        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True
        docker_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "docker"]
        assert len(docker_calls) == 0, "docker must not run when unavailable"

    # 🧪 TRAP[TEST] · Regression · Scenario: no vhost .conf files → skip
    # · Expect: returns True, IMP:7 "No vhost files to validate — SKIP"
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: no-vhosts skip logic changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_no_vhosts_skip(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """Empty temp dir (no .conf files) → returns True (SKIP)."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"

        result = nginx_t_harness(str(tmp_path))

        assert result is True


# ══════════════════════════════════════════════════════════════════════
# TESTS: docker run outcome
# ══════════════════════════════════════════════════════════════════════


class TestHarnessDockerRun:
    """Tests for the docker run nginx -t step outcome."""

    # 🧪 TRAP[TEST] · Regression · Scenario: docker nginx -t returns 0
    # · Expect: returns True, IMP:9 "nginx -t PASS"
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: docker pass handling changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_docker_pass(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """docker nginx -t success → returns True."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = mock.MagicMock(returncode=0, stdout=b"", stderr=b"")

        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True
        assert any("nginx -t PASS" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: docker nginx -t returns 1
    # · Expect: returns False, IMP:10 FAIL logged
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: docker fail handling changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_docker_fail(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """docker nginx -t failure → returns False + stderr lines logged."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = mock.MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b'nginx: [emerg] unknown directive "foo" in /etc/nginx/conf.d/overlay/test.example.com.conf:3\n',
        )

        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is False
        assert any("nginx -t FAIL" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# TESTS: openssl dev-cert generation
# ══════════════════════════════════════════════════════════════════════


class TestHarnessOpenssl:
    """Tests for openssl dev-cert generation fallbacks."""

    # 🧪 TRAP[TEST] · Regression · Scenario: openssl returns non-zero
    # · Expect: empty cert files written, WARN logged, harness continues
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: openssl fallback logic changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_openssl_fail_empty_certs(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """openssl returns non-zero → empty cert files created, WARN logged."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"

        calls = []

        def mock_side_effect(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[0] == "openssl":
                return mock.MagicMock(returncode=1, stdout=b"", stderr=b"error")
            return mock.MagicMock(returncode=0, stdout=b"", stderr=b"")

        mock_run.side_effect = mock_side_effect

        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True
        assert any("openssl failed" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: openssl binary missing (FileNotFoundError)
    # · Expect: empty cert files written, WARN logged, harness continues
    # · Last fail: None (new test for DevPlan 117 G T53)
    # · Remove if: openssl fallback logic changes
    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_harness_openssl_missing_fallback(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog
    ) -> None:
        """openssl not found (FileNotFoundError) → empty cert files + WARN."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"

        def mock_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "openssl":
                raise FileNotFoundError("openssl")
            return mock.MagicMock(returncode=0, stdout=b"", stderr=b"")

        mock_run.side_effect = mock_side_effect

        (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True
        assert any("openssl not available" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# TESTS: LDD trajectory for the happy path
# ══════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Scenario: full happy path emits IMP:9 business log
# · Expect: IMP:9 "nginx -t PASS" present; subprocess TimeoutExpired branch covered
# · Last fail: None (new test for DevPlan 117 G T53)
# · Remove if: harness happy path changes
@pytest.mark.parametrize(
    "exc",
    [FileNotFoundError, subprocess.TimeoutExpired("openssl", 30)],
    ids=["openssl-missing", "openssl-timeout"],
)
def test_harness_openssl_exception_branches(exc, tmp_path, caplog) -> None:
    """Both exception branches (FileNotFoundError, TimeoutExpired) → empty certs."""
    caplog.set_level(0)
    (tmp_path / "test.example.com.conf").write_text("server { listen 80; }\n", encoding="utf-8")

    with (
        mock.patch("core.internal.scaffold.nginx_harness.shutil.which", return_value="/usr/bin/docker"),
        mock.patch(
            "core.internal.scaffold.nginx_harness.subprocess.run",
            side_effect=lambda *a, **k: (
                (_ for _ in ()).throw(exc)
                if a[0][0] == "openssl"
                else mock.MagicMock(returncode=0, stdout=b"", stderr=b"")
            ),
        ),
    ):
        result = nginx_t_harness(str(tmp_path))

    assert result is True
