# GREP_SUMMARY: provisioner, unit-test, platform-env, yaml, networks, volumes, env, profiles, dry-run, idempotency
# STRUCTURE: ◇ test_load_platform_env (4 tests) → ◇ test_provision_networks (4 tests) → ◇ test_provision_volumes (4 tests) → ◇ test_provision_env (3 tests) → ◇ test_provision_profiles (1 test) → ◇ test_cli (2 tests)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/provisioner.py — native Python imports,
##           monkeypatch for subprocess/os, caplog for LDD telemetry.
## @scope    tests/unit/ — runs without Docker, marked as unit tests (no requires_docker).
## @invariants
##   - All tests use tmp_path fixture (no hardcoded paths)
##   - Docker-dependent tests use monkeypatch for subprocess.run (no Docker daemon)
##   - Volume tests use tmp_path for directories
##   - Each test verifies [IMP:9] log presence via caplog fixture
##   - LDD block name: [provision] (matching shell wrapper and existing tests)
## @rationale Complements existing integration tests (test_unit_provision_environment.py)
##            with native-function-level coverage for Python business logic.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.internal.provisioner import (
    PlatformEnv,
    VolumeConfig,
    load_platform_env,
    main,
    provision_env,
    provision_networks,
    provision_profiles,
    provision_volumes,
)

# ── Sample YAML for tests ─────────────────────────────────────────────────────

SAMPLE_YAML = """\
networks:
  - name: proxy-net
    driver: bridge
  - name: shared-db-net
    driver: bridge
volumes:
  - path: /var/lib/platform/postgres-data
  - path: /var/lib/platform/prometheus-data
env_defaults:
  POSTGRES_PASSWORD: test-pg-pwd
  POSTGRES_USER: postgres
profiles:
  - backup-cron
  - monitoring
"""

MINIMAL_YAML = """\
profiles:
  - backup-cron
"""

MALFORMED_YAML = """\
networks:
  - name: bad
   driver: bridge
"""


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_yaml_path(tmp_path: Path) -> Path:
    """Write sample platform-env.yaml to tmp_path."""
    yaml_path = tmp_path / "platform-env.yaml"
    yaml_path.write_text(SAMPLE_YAML)
    return yaml_path


@pytest.fixture
def minimal_yaml_path(tmp_path: Path) -> Path:
    """Write minimal platform-env.yaml (only profiles) to tmp_path."""
    yaml_path = tmp_path / "minimal-env.yaml"
    yaml_path.write_text(MINIMAL_YAML)
    return yaml_path


@pytest.fixture
def malformed_yaml_path(tmp_path: Path) -> Path:
    """Write malformed YAML to tmp_path."""
    yaml_path = tmp_path / "malformed.yaml"
    yaml_path.write_text(MALFORMED_YAML)
    return yaml_path


@pytest.fixture
def sample_env(sample_yaml_path: Path) -> PlatformEnv:
    """Load PlatformEnv from sample YAML."""
    return load_platform_env(sample_yaml_path)


# ── T4.1-T4.4: load_platform_env ─────────────────────────────────────────────


class TestLoadPlatformEnv:
    """Unit tests for load_platform_env() — YAML parsing, error handling."""

    def test_load_platform_env_parses_all_sections(self, sample_yaml_path: Path, caplog) -> None:
        """T4.1: Parse valid platform-env.yaml → verify all 4 sections populated."""
        caplog.set_level(logging.DEBUG)

        env = load_platform_env(sample_yaml_path)

        # Networks
        assert len(env.networks) == 2
        assert env.networks[0].name == "proxy-net"
        assert env.networks[0].driver == "bridge"
        assert env.networks[1].name == "shared-db-net"

        # Volumes
        assert len(env.volumes) == 2
        assert env.volumes[0].path == "/var/lib/platform/postgres-data"
        assert env.volumes[1].path == "/var/lib/platform/prometheus-data"

        # Env defaults
        assert len(env.env_defaults) == 2
        assert env.env_defaults["POSTGRES_PASSWORD"] == "test-pg-pwd"
        assert env.env_defaults["POSTGRES_USER"] == "postgres"

        # Profiles
        assert len(env.profiles) == 2
        assert "backup-cron" in env.profiles
        assert "monitoring" in env.profiles

        # Verify LDD IMP:7-9 logs
        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("[IMP:7][provision]" in r.message for r in imp_records)
        assert any("[IMP:8][provision] Parsed:" in r.message for r in imp_records)

    def test_load_platform_env_missing_file(self, tmp_path: Path, caplog) -> None:
        """T4.2: Parse non-existent path → FileNotFoundError."""
        caplog.set_level(logging.DEBUG)

        nonexistent = tmp_path / "does-not-exist.yaml"
        with pytest.raises(FileNotFoundError):
            load_platform_env(nonexistent)

    def test_load_platform_env_malformed_yaml(self, malformed_yaml_path: Path, caplog) -> None:
        """T4.3: Parse invalid YAML → yaml.YAMLError."""
        caplog.set_level(logging.DEBUG)

        with pytest.raises(yaml.YAMLError):
            load_platform_env(malformed_yaml_path)

    def test_load_platform_env_missing_sections(self, minimal_yaml_path: Path, caplog) -> None:
        """T4.4: Parse YAML with no networks/volumes → empty lists."""
        caplog.set_level(logging.DEBUG)

        env = load_platform_env(minimal_yaml_path)

        assert env.networks == []
        assert env.volumes == []
        assert env.env_defaults == {}
        assert env.profiles == ["backup-cron"]

        assert any("[IMP:8][provision] Parsed:" in r.message for r in caplog.records)


# ── T4.5-T4.8: provision_networks ────────────────────────────────────────────


class TestProvisionNetworks:
    """Unit tests for provision_networks() — monkeypatch subprocess, no Docker."""

    def test_provision_networks_dry_run(self, sample_env: PlatformEnv, caplog) -> None:
        """T4.5: Dry-run mode → no subprocess calls, output printed."""
        caplog.set_level(logging.DEBUG)

        result = provision_networks(sample_env, dry_run=True)

        assert result.scope == "networks"
        assert result.created == 2  # Two networks in sample YAML
        assert result.skipped == 0

        # Verify LDD log
        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("[IMP:9][provision][networks] Networks provisioned:" in r.message for r in imp_records)
        assert any("DRY-RUN: Would create network: proxy-net" in r.message for r in imp_records)
        assert any("DRY-RUN: Would create network: shared-db-net" in r.message for r in imp_records)

    def test_provision_networks_creates_new(self, sample_env: PlatformEnv, caplog, monkeypatch) -> None:
        """T4.6: Network does not exist → docker network create called."""
        caplog.set_level(logging.DEBUG)

        # Track subprocess calls
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            # First call (inspect) fails → network doesn't exist
            # Second call (create) succeeds
            if "inspect" in cmd:
                result.returncode = 1  # Not found
            else:
                result.returncode = 0  # Created
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = provision_networks(sample_env, dry_run=False)

        assert result.scope == "networks"
        assert result.created == 2
        assert result.skipped == 0

        # Should have 4 calls: inspect+create for each of 2 networks
        assert len(calls) == 4

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any(
            "[IMP:9][provision][networks] Networks provisioned: 2 created, 0 skipped" in r.message for r in imp_records
        )

    def test_provision_networks_skips_existing(self, sample_env: PlatformEnv, caplog, monkeypatch) -> None:
        """T4.7: Network already exists → skip, zero docker create calls."""
        caplog.set_level(logging.DEBUG)

        create_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "inspect" in cmd:
                result.returncode = 0  # Exists
            else:
                create_calls.append(cmd)
                result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = provision_networks(sample_env, dry_run=False)

        assert result.scope == "networks"
        assert result.created == 0
        assert result.skipped == 2
        assert len(create_calls) == 0

    def test_provision_networks_idempotent(self, sample_env: PlatformEnv, caplog, monkeypatch) -> None:
        """T4.8: Same config twice → same result (created=0 on second run)."""
        caplog.set_level(logging.DEBUG)

        # First run: networks don't exist → create them
        call_count = {"inspect": 0, "create": 0}

        class FakeRunFirst:
            def __init__(self, calls):
                self._calls = calls

            def __call__(self, cmd, **kwargs):
                self._calls["inspect" if "inspect" in cmd else "create"] += 1
                result = MagicMock()
                result.returncode = 1 if "inspect" in cmd else 0
                return result

        monkeypatch.setattr(subprocess, "run", FakeRunFirst(call_count))
        result1 = provision_networks(sample_env, dry_run=False)
        assert result1.created == 2
        assert result1.skipped == 0

        # Second run: all networks exist → skip
        class FakeRunSecond:
            def __call__(self, cmd, **kwargs):
                result = MagicMock()
                result.returncode = 0  # All exist
                return result

        monkeypatch.setattr(subprocess, "run", FakeRunSecond())
        result2 = provision_networks(sample_env, dry_run=False)
        assert result2.created == 0
        assert result2.skipped == 2


# ── T4.9-T4.12: provision_volumes ────────────────────────────────────────────


class TestProvisionVolumes:
    """Unit tests for provision_volumes() — monkeypatch os, no Docker."""

    def test_provision_volumes_dry_run(self, sample_env: PlatformEnv, caplog) -> None:
        """T4.9: Dry-run mode → no mkdir calls."""
        caplog.set_level(logging.DEBUG)

        result = provision_volumes(sample_env, dry_run=True)

        assert result.scope == "volumes"
        assert result.created == 2
        assert result.skipped == 0

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("[IMP:9][provision][volumes] Volumes provisioned:" in r.message for r in imp_records)
        assert any("DRY-RUN: Would create directory: /var/lib/platform/postgres-data" in r.message for r in imp_records)

    def test_provision_volumes_creates_dirs(self, sample_env: PlatformEnv, caplog, tmp_path, monkeypatch) -> None:
        """T4.10: Directories do not exist → mkdir -p called."""
        caplog.set_level(logging.DEBUG)

        # Override volume paths to use tmp_path
        env = PlatformEnv(
            networks=[],
            volumes=[
                VolumeConfig(path=str(tmp_path / "new-dir-1")),
                VolumeConfig(path=str(tmp_path / "new-dir-2")),
            ],
            env_defaults={},
            profiles=[],
        )

        # Ensure dirs don't exist
        assert not os.path.isdir(str(tmp_path / "new-dir-1"))

        result = provision_volumes(env, dry_run=False)

        assert result.scope == "volumes"
        assert result.created == 2
        assert result.skipped == 0

        # Verify directories were created
        assert os.path.isdir(str(tmp_path / "new-dir-1"))
        assert os.path.isdir(str(tmp_path / "new-dir-2"))

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any(
            "[IMP:9][provision][volumes] Volumes provisioned: 2 created, 0 skipped" in r.message for r in imp_records
        )

    def test_provision_volumes_skips_existing(self, sample_env: PlatformEnv, caplog, tmp_path) -> None:
        """T4.11: Directories exist → skip."""
        caplog.set_level(logging.DEBUG)

        # Pre-create directories
        dir1 = tmp_path / "existing-dir"
        dir1.mkdir()

        env = PlatformEnv(
            networks=[],
            volumes=[VolumeConfig(path=str(dir1))],
            env_defaults={},
            profiles=[],
        )

        result = provision_volumes(env, dry_run=False)

        assert result.scope == "volumes"
        assert result.created == 0
        assert result.skipped == 1

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any(
            "[IMP:9][provision][volumes] Volumes provisioned: 0 created, 1 skipped" in r.message for r in imp_records
        )

    def test_provision_volumes_permission_error_nonfatal(
        self, sample_env: PlatformEnv, caplog, tmp_path, monkeypatch
    ) -> None:
        """T4.12: mkdir fails with PermissionError → logged, not fatal."""
        caplog.set_level(logging.DEBUG)

        env = PlatformEnv(
            networks=[],
            volumes=[VolumeConfig(path="/root/forbidden-dir")],
            env_defaults={},
            profiles=[],
        )

        def fake_makedirs(path, exist_ok=False):
            raise PermissionError("Permission denied")

        monkeypatch.setattr(os, "makedirs", fake_makedirs)
        monkeypatch.setattr(os.path, "isdir", lambda p: False)

        result = provision_volumes(env, dry_run=False)

        assert result.scope == "volumes"
        assert result.created == 0
        assert result.skipped == 1  # Permission error → counted as skipped

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("WARN: Cannot create" in r.message for r in imp_records)
        assert any(
            "[IMP:9][provision][volumes] Volumes provisioned: 0 created, 1 skipped" in r.message for r in imp_records
        )


# ── T4.13-T4.15: provision_env ───────────────────────────────────────────────


class TestProvisionEnv:
    """Unit tests for provision_env() — GITHUB_ENV / local / dry-run modes."""

    def test_provision_env_dry_run(self, sample_env: PlatformEnv, caplog, capsys) -> None:
        """T4.13: Dry-run mode → vars printed with 'DRY-RUN:' prefix."""
        caplog.set_level(logging.DEBUG)

        result = provision_env(sample_env, dry_run=True)

        assert result.scope == "env"
        assert result.created == 2

        # Dry-run output goes to stdout
        stdout = capsys.readouterr().out
        assert "DRY-RUN: Would export POSTGRES_PASSWORD=test-pg-pwd" in stdout
        assert "DRY-RUN: Would export POSTGRES_USER=postgres" in stdout

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("DRY-RUN: Would export 2 env vars" in r.message for r in imp_records)

    def test_provision_env_github_env(self, sample_env: PlatformEnv, caplog, tmp_path) -> None:
        """T4.14: GITHUB_ENV file path set → KEY=VALUE written to file."""
        caplog.set_level(logging.DEBUG)

        github_env_file = tmp_path / "github_env_output"
        github_env_file.write_text("")  # Create empty file

        result = provision_env(sample_env, dry_run=False, github_env=str(github_env_file))

        assert result.scope == "env"
        assert result.created == 2

        content = github_env_file.read_text()
        assert "POSTGRES_PASSWORD=test-pg-pwd" in content
        assert "POSTGRES_USER=postgres" in content

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("2 env vars exported to GITHUB_ENV" in r.message for r in imp_records)

    def test_provision_env_local_mode(self, sample_env: PlatformEnv, caplog, capsys) -> None:
        """T4.15: No GITHUB_ENV → vars printed to stderr."""
        caplog.set_level(logging.DEBUG)

        # Clear GITHUB_ENV if set
        old_github_env = os.environ.pop("GITHUB_ENV", None)
        try:
            result = provision_env(sample_env, dry_run=False)
        finally:
            if old_github_env is not None:
                os.environ["GITHUB_ENV"] = old_github_env

        assert result.scope == "env"
        assert result.created == 2

        # Local mode prints to stderr
        stderr = capsys.readouterr().err
        assert "POSTGRES_PASSWORD=test-pg-pwd" in stderr
        assert "POSTGRES_USER=postgres" in stderr

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("Env vars printed (GITHUB_ENV not set — local mode)" in r.message for r in imp_records)


# ── T4.16: provision_profiles ────────────────────────────────────────────────


class TestProvisionProfiles:
    """Unit tests for provision_profiles()."""

    def test_provision_profiles_count(self, sample_env: PlatformEnv, caplog) -> None:
        """T4.16: Profiles list parsed → correct count returned."""
        caplog.set_level(logging.DEBUG)

        result = provision_profiles(sample_env)

        assert result.scope == "profiles"
        assert result.created == 2  # backup-cron, monitoring

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("Profiles available: 2" in r.message for r in imp_records)


# ── T4.17-T4.18: CLI ─────────────────────────────────────────────────────────


class TestCLI:
    """Unit tests for main() CLI entry point — argparse parsing."""

    def test_cli_unknown_scope(self, sample_yaml_path: Path, caplog) -> None:
        """T4.17: --scope invalid → exit 1."""
        caplog.set_level(logging.DEBUG)

        # argparse exits with SystemExit for invalid choices
        old_argv = sys.argv
        try:
            sys.argv = [
                "provisioner.py",
                "--scope",
                "invalid",
                "--platform-env",
                str(sample_yaml_path),
            ]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0
        finally:
            sys.argv = old_argv

    def test_cli_dry_run_flag(self, sample_yaml_path: Path, caplog, monkeypatch) -> None:
        """T4.18: --dry-run flag → dry_run=True propagated, no side effects."""
        caplog.set_level(logging.DEBUG)

        # Prevent actual docker calls
        subprocess_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        old_argv = sys.argv
        try:
            sys.argv = [
                "provisioner.py",
                "--scope",
                "networks",
                "--platform-env",
                str(sample_yaml_path),
                "--dry-run",
            ]
            exit_code = main()
            assert exit_code == 0
        finally:
            sys.argv = old_argv

        # Dry-run should NOT call docker
        assert len(subprocess_calls) == 0

        # Should have IMP:9 completion log
        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("[IMP:9][provision][networks] Networks provisioned:" in r.message for r in imp_records)
        assert any("DRY-RUN: Would create network:" in r.message for r in imp_records)

    def test_cli_missing_platform_env(self, tmp_path: Path, caplog) -> None:
        """--platform-env pointing to nonexistent file → exit 1."""
        caplog.set_level(logging.DEBUG)

        old_argv = sys.argv
        try:
            sys.argv = [
                "provisioner.py",
                "--scope",
                "networks",
                "--platform-env",
                str(tmp_path / "nonexistent.yaml"),
            ]
            exit_code = main()
            assert exit_code == 1
        finally:
            sys.argv = old_argv

        imp_records = [r for r in caplog.records if "[IMP:" in r.message]
        assert any("FATAL: platform-env.yaml not found" in r.message for r in imp_records)
