# GREP_SUMMARY: test-platform-export-metrics docker cert project host collector json-writer cache coordinator tls letsencrypt openssl x509
# STRUCTURE: ▶ test_docker_collector_containers → ◇ mock subprocess.run (docker inspect + stats) → assert fields
#            ▶ test_docker_collector_batch → ◇ one subprocess call for inspect all → assert
#            ▶ test_cert_collector_wildcard → ◇ mock cryptography.x509 + SAN *.domain → assert domains[]
#            ▶ test_cert_collector_expiry_dates → ◇ ISO 8601 not_after → assert days_remaining
#            ▶ test_project_collector_mtime_cache → ◇ mock mtime unchanged → assert no du call
#            ▶ test_project_collector_du_sb → ◇ du -sb output → assert code_size_bytes int
#            ▶ test_host_collector → ◇ mock shutil.disk_usage → assert total/free/used
#            ▶ test_json_writer_atomic → ◇ tmp file exists before rename → assert final file complete
#            ▶ test_json_writer_schema_version → ◇ assert schema_version: 2 in output
#            ▶ test_coordinator_partial_failure → ◇ certs fail, docker OK → assert errors[] + partial data
#            ▶ test_coordinator_empty_state → ◇ no docker, no certs → assert empty arrays + no crash
#            ▶ test_tls_section_* (017 C4) → ◇ letsencrypt live dir + openssl x509 → assert tls-секция
#            ▶ test_cache_ttl_hit → ◇ cache fresh → assert no recompute
#            ▶ test_cache_ttl_miss → ◇ cache expired → assert recompute
#            ▶ test_cache_mtime_invalidation → ◇ source mtime newer → assert cache miss
# @file test_platform_export_metrics.py
# @purpose  Unit tests for the platform metrics export package (collectors + writer + cache + coordinator)
# @scope    Unit-level: tests call collector functions directly with mocked external dependencies.
#           No Docker required — all tests use tmp_path, subprocess mocking, cryptography mocking.
# @invariants
#   - All tests use tmp_path fixture (Zero Hardcode Rule)
#   - LDD trajectory (IMP:7-10) printed before every assert
#   - No docker required — static unit tests only
#   - Test Honesty Rules: R1 (no pass-tests), R2 (no unfalsifiable asserts)
# @rationale  Testing business logic directly avoids docker dependency while validating core behavior.
# @changes 2026-07-23 | CREATED | META Δ14 — new test suite for metrics package
# @changes 2026-08-27 | DevPlan 017 C4 — +TestTlsCollector (tls-секция: self-signed parse, missing-dir,
#           openssl absent, per-domain skip, coordinator end-to-end)
# region MODULE_CONTRACT
## @purpose  Unit tests for platform metrics export package
## @scope    Unit tests — no Docker, no HTTP server, mocked subprocess run, cryptography, shutil
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - At least one IMP:9 log in successful scenarios
##   - Covers: docker_collector, cert_collector, project_collector, host_collector, json_writer, cache, coordinator
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

# W4e (DevPlan 160 E2): main()/_get_* принимают env=Mapping (DI) — импорт на module level,
# reload/importlib не требуется (координатор читает env только через env-параметр).
# intentional-seam: приватный API тестируется напрямую по дизайну (unit-seam, T8.1)
from core.internal.healthcheck.platform_export_metrics import (
    _collect_tls_metrics,
    _get_node_name,
    _get_node_yaml_path,
    main,
)
from tests._conftest.ldd import _print_ldd_trajectory

# Реальный subprocess.run на момент импорта модуля (ДО fixture-патчей) — для диспетчера
# openssl/docker в test_coordinator_writes_tls_section (tls-парсинг — реальный бинар).
_REAL_SUBPROCESS_RUN = subprocess.run

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_node_yaml(tmp_path: Path) -> str:
    """Create a minimal node.yaml."""
    import yaml

    node_data = {
        "node": {"name": "test-node", "platform_domain": "test.local"},
        "projects": [
            {"name": "test-app", "domain": "test-app.example.com", "expose": True},
            {"name": "internal-app", "domain": "internal.example.com", "expose": False},
        ],
        "modules": ["nginx", "postgres"],
    }
    path = tmp_path / "node.yaml"
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.dump(node_data, f)
    return str(path)


@pytest.fixture
def mock_node_yaml_no_projects(tmp_path: Path) -> str:
    """Create a node.yaml with no projects."""
    import yaml

    node_data = {"node": {"name": "test-node"}, "projects": [], "modules": []}
    path = tmp_path / "node-empty.yaml"
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.dump(node_data, f)
    return str(path)


@pytest.fixture
def mock_cache_dir(tmp_path: Path) -> str:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


@pytest.fixture
def tls_live_dir(tmp_path: Path) -> Path:
    """tmp letsencrypt live dir с ЗАКОММИЧЕННЫМ dummy-сертом (017 C4, tests/test_data/).

    ## @purpose — Live-каталог для tls-тестов: домен-поддиректория example.test/fullchain.pem
    ##            из tests/test_data/tls_example_test.crt (self-signed CN=example.test,
    ##            validity 365d, сгенерирован openssl одноразово; key не хранится).
    ## @io — ⎋ Path (letsencrypt live dir) — источник env LETSENCRYPT_LIVE для _collect_tls_metrics
    ## @complexity — O(1) — копирование файла
    ## @invariants
    ##   - Коммиченный PEM-файл — БЕЗ сети, БЕЗ runtime-генерации (детерминированный тест)
    ##   - Файл переиспользуется всеми tls-тестами (не мутируется)
    """
    live = tmp_path / "letsencrypt" / "live"
    domain_dir = live / "example.test"
    domain_dir.mkdir(parents=True, exist_ok=True)
    crt = Path(__file__).resolve().parent.parent / "test_data" / "tls_example_test.crt"
    assert crt.is_file(), f"закоммиченный тест-серт не найден: {crt}"
    shutil.copy(crt, domain_dir / "fullchain.pem")
    return live


@pytest.fixture
def mock_docker_subprocess():
    """Boundary fixture (T3 D1): ONE docker_ops.subprocess.run mock for the file.

    ## @purpose — Replaces 9 inline docker_collector.subprocess.run patch blocks.
    ##            Default: empty docker (no containers). Tests configure
    ##            return_value / side_effect per scenario (ps → inspect → stats).
    ## @io — ⎋ MagicMock (docker_ops.subprocess.run) — assert on parsed structure, not on calls
    ## @complexity — O(1)
    ## @invariants
    ##   - 128 W1: docker_collector делегирует shared/docker_ops — патчим docker_ops.subprocess.run
    ##     (глобальный subprocess module — покрывает все docker-подвызовы)
    ##   - Assertions on observable collector output (containers list / sizes dict / exit) — D1
    """
    with mock.patch("core.internal.shared.docker_ops.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        yield mock_run


# ═══════════════════════════════════════════════════════════════════
# TESTS: docker_collector
# ═══════════════════════════════════════════════════════════════════


class TestDockerCollector:
    """Tests for docker_collector.py."""

    def test_docker_collector_containers(self, caplog, mock_docker_subprocess):
        """get_containers returns parsed container data from mock subprocess."""
        caplog.set_level(0)

        # Mock subprocess.run for docker ps -aq, inspect, and stats
        # docker ps -aq → container IDs
        # docker inspect → JSON array
        # docker stats → JSON lines

        # We need to set up side_effect for 3 calls
        mock_docker_subprocess.side_effect = [
            # Call 1: docker ps -aq
            mock.Mock(returncode=0, stdout="abc123\ndef456\n", stderr=""),
            # Call 2: docker inspect abc123 def456
            mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "Id": "sha256:abc123",
                        "Name": "/nginx",
                        "State": {
                            "Running": True,
                            "ExitCode": 0,
                            # docker inspect API-значение State.Status (НЕ ps-строка «Up …»);
                            # DevPlan 17 T1.3: фикстура приведена к реальному формату
                            "Status": "running",
                            "Health": {"Status": "healthy"},
                        },
                        "Config": {"Image": "nginx:latest"},
                        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                    },
                    {
                        "Id": "sha256:def456",
                        "Name": "/redis",
                        "State": {
                            "Running": False,
                            "ExitCode": 137,
                            "Status": "exited",
                            "Health": {"Status": "unhealthy"},
                        },
                        "Config": {"Image": "redis:alpine"},
                        "HostConfig": {"RestartPolicy": {"Name": "always"}},
                    },
                ]),
                stderr="",
            ),
            # Call 3: docker stats --no-stream
            mock.Mock(
                returncode=0,
                stdout=(
                    '{"Name":"nginx","CPUPerc":"0.45%","MemUsage":"12.5MiB / 1GiB","MemLimit":"1GiB"}\n'
                    '{"Name":"redis","CPUPerc":"0.10%","MemUsage":"5.2MiB / 512MiB","MemLimit":"512MiB"}\n'
                ),
                stderr="",
            ),
        ]

        from core.internal.healthcheck.metrics.docker_collector import get_containers

        containers = get_containers()

        # ── LDD TRAJECTORY ──
        _print_ldd_trajectory(caplog)

        assert len(containers) == 2, f"Expected 2 containers, got {len(containers)}"
        # First container: nginx
        assert containers[0]["name"] == "nginx", f"Expected nginx, got {containers[0]['name']}"
        assert containers[0]["running"] is True
        assert containers[0]["healthy"] is True
        assert containers[0]["cpu_percent"] == 0.45
        assert containers[0]["memory_usage_bytes"] > 0
        assert containers[0]["memory_limit_bytes"] > 0
        # Second container: redis (stopped)
        assert containers[1]["name"] == "redis"
        assert containers[1]["running"] is False
        assert containers[1]["healthy"] is False

    @pytest.mark.parametrize(
        "mock_setup",
        [
            pytest.param(
                lambda m: m.configure_mock(return_value=mock.Mock(returncode=0, stdout="", stderr="")),
                id="empty",
            ),
            pytest.param(
                lambda m: m.configure_mock(side_effect=OSError("docker CLI not found")),
                id="cli_missing",
            ),
        ],
    )
    # 🧪 TRAP[TEST] · Regression: get_containers graceful degradation → []
    # · Scenario: пустой docker ps ИЛИ docker CLI недоступен (OSError) → []
    # · Last fail: never (new feature)
    # · Remove if: graceful-degradation контракт get_containers изменён
    def test_docker_collector_empty(self, caplog, mock_docker_subprocess, mock_setup):
        """get_containers → [] при пустом stdout ИЛИ недоступном docker CLI (graceful degradation)."""
        caplog.set_level(0)

        mock_setup(mock_docker_subprocess)

        from core.internal.healthcheck.metrics.docker_collector import get_containers

        containers = get_containers()

        _print_ldd_trajectory(caplog)

        assert containers == [], f"Expected empty list, got {containers}"

    # 🧪 TRAP[TEST] · TASK-13 · Regression: docker_collector started_at field
    # · Scenario: Container with State.StartedAt ISO timestamp
    # · Last fail: never (new feature)
    # · Remove if: started_at field removed from docker_collector
    def test_docker_collector_started_at(self, caplog, mock_docker_subprocess):
        """get_containers includes started_at ISO timestamp from docker inspect State.StartedAt."""
        caplog.set_level(0)

        mock_docker_subprocess.side_effect = [
            # Call 1: docker ps -aq
            mock.Mock(returncode=0, stdout="abc123\n", stderr=""),
            # Call 2: docker inspect
            mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "Id": "sha256:abc123",
                        "Name": "/nginx",
                        "State": {
                            "Running": True,
                            "ExitCode": 0,
                            "Status": "Up 2 hours",
                            "StartedAt": "2026-07-24T00:00:00.000000000Z",
                            "Health": {"Status": "healthy"},
                        },
                        "Config": {"Image": "nginx:latest"},
                        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                    }
                ]),
                stderr="",
            ),
            # Call 3: docker stats
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        from core.internal.healthcheck.metrics.docker_collector import get_containers

        containers = get_containers()

        _print_ldd_trajectory(caplog)

        assert len(containers) == 1
        assert containers[0]["started_at"] == "2026-07-24T00:00:00.000000000Z", (
            f"Expected ISO timestamp, got {containers[0].get('started_at')}"
        )

    # 🧪 TRAP[TEST] · TASK-13 · Regression: docker_collector started_at None
    # · Scenario: Container without State.StartedAt → started_at: None
    # · Remove if: started_at field removed
    def test_docker_collector_started_at_missing(self, caplog, mock_docker_subprocess):
        """get_containers returns started_at=None when State.StartedAt is absent."""
        caplog.set_level(0)

        mock_docker_subprocess.side_effect = [
            mock.Mock(returncode=0, stdout="abc123\n", stderr=""),
            mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "Id": "sha256:abc123",
                        "Name": "/redis",
                        "State": {
                            "Running": False,
                            "ExitCode": 0,
                            "Status": "Exited (0)",
                            # No StartedAt field
                        },
                        "Config": {"Image": "redis:alpine"},
                        "HostConfig": {"RestartPolicy": {"Name": "always"}},
                    }
                ]),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        from core.internal.healthcheck.metrics.docker_collector import get_containers

        containers = get_containers()

        _print_ldd_trajectory(caplog)

        assert len(containers) == 1
        assert containers[0]["started_at"] is None, (
            f"Expected started_at=None when State.StartedAt absent, got {containers[0].get('started_at')}"
        )


class TestDockerImageSizes:
    """Tests for get_image_sizes."""

    def test_docker_image_sizes(self, caplog, mock_docker_subprocess):
        """get_image_sizes returns {sha256: size} from docker image inspect."""
        caplog.set_level(0)

        # docker image inspect --format '{{json .}}' outputs one JSON object per line
        stdout_lines = [
            '{"Id":"sha256:abc123","Size":150000000}',
            '{"Id":"sha256:def456","Size":45000000}',
        ]
        mock_docker_subprocess.return_value = mock.Mock(
            returncode=0,
            stdout="\n".join(stdout_lines),
            stderr="",
        )

        from core.internal.healthcheck.metrics.docker_collector import get_image_sizes

        sizes = get_image_sizes({"sha256:abc123", "sha256:def456"})

        _print_ldd_trajectory(caplog)

        assert sizes.get("sha256:abc123") == 150000000
        assert sizes.get("sha256:def456") == 45000000

    # GUARD-PRESERVE (168): единственное покрытие empty-input ветки get_image_sizes (пустой set → {}, subprocess не вызывается)
    def test_docker_image_sizes_empty(self, caplog):
        """get_image_sizes returns empty dict for empty input."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.docker_collector import get_image_sizes

        sizes = get_image_sizes(set())

        _print_ldd_trajectory(caplog)

        assert sizes == {}


# ═══════════════════════════════════════════════════════════════════
# TESTS: cert_collector
# ═══════════════════════════════════════════════════════════════════


class TestCertCollector:
    """Tests for cert_collector.py — _load_cert live-path scenarios (not duplicated in test_cert_collector.py).

    W2 T2.1 (DevPlan 160): _san_match сценарии (test_san_match_exact/wildcard/no_match) и
    test_cert_collector_file_missing удалены — дублируют tests/test_cert_collector.py::TestSanMatch
    (канон, включая wildcard_multiple_entries, empty_san, trailing_dot). Оставлены сценарии,
    проходящие РЕАЛЬНЫЙ cryptography-парсинг (не mock) — уникальное live-path покрытие.
    """

    def test_cert_collector_expiry_dates(self, tmp_path, caplog):
        """_load_cert returns ISO 8601 dates and days_remaining."""
        caplog.set_level(0)

        # Create a self-signed cert using cryptography
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509
            .CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(12345)
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("test.example.com")]), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        cert_path = tmp_path / "test-cert.pem"
        with Path(cert_path).open("wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        from core.internal.healthcheck.metrics.cert_collector import _load_cert

        result = _load_cert(str(cert_path))

        _print_ldd_trajectory(caplog)

        assert result is not None, "Cert should be parsed"
        assert "T" in result["not_after_iso"], f"Expected ISO 8601 date, got {result['not_after_iso']}"
        assert result["days_remaining"] >= 28, f"Expected ~30 days remaining, got {result['days_remaining']}"
        assert "test.example.com" in result["san"]
        assert "test.example.com" in result.get("subject", "")


# ═══════════════════════════════════════════════════════════════════
# TESTS: host_collector
# ═══════════════════════════════════════════════════════════════════


class TestHostCollector:
    """Tests for host_collector.py."""

    def test_host_collector(self, caplog):
        """get_host_disk returns disk stats from shutil."""
        caplog.set_level(0)

        with mock.patch("core.internal.healthcheck.metrics.host_collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = mock.Mock(total=100 * 1024**3, free=30 * 1024**3)

            from core.internal.healthcheck.metrics.host_collector import get_host_disk

            result = get_host_disk()

            _print_ldd_trajectory(caplog)

            assert result["disk_total_gb"] == 100.0
            assert result["disk_free_gb"] == 30.0
            assert result["disk_used_percent"] == 70.0

    # 🧪 TRAP[TEST] · TASK-13 · Regression: host_collector uptime & load
    # · Scenario: /proc/uptime and /proc/loadavg present → parsed correctly
    # · Remove if: get_host_uptime removed from host_collector
    def test_host_collector_uptime(self, tmp_path, caplog):
        """get_host_uptime returns uptime and load avg from /proc files."""
        caplog.set_level(0)

        # Create fake /proc files
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()

        uptime_file = proc_dir / "uptime"
        uptime_file.write_text("12345.67 67890.12\n")

        loadavg_file = proc_dir / "loadavg"
        loadavg_file.write_text("0.50 0.30 0.10 1/234 56789\n")

        # 167 D6 (DI-zero): open_fn DI-параметр — fake /proc через параметр, 0 monkeypatch builtins.open
        original_open = open

        def _mock_open(path, *args, **kwargs):
            if path == "/proc/uptime":
                return original_open(str(uptime_file), *args, **kwargs)
            if path == "/proc/loadavg":
                return original_open(str(loadavg_file), *args, **kwargs)
            return original_open(path, *args, **kwargs)

        from core.internal.healthcheck.metrics.host_collector import get_host_uptime

        result = get_host_uptime(open_fn=_mock_open)

        _print_ldd_trajectory(caplog)

        assert result["uptime_seconds"] == 12345.67, f"Expected 12345.67, got {result['uptime_seconds']}"
        assert result["load_1m"] == 0.5, f"Expected 0.5, got {result['load_1m']}"
        assert result["load_5m"] == 0.3, f"Expected 0.3, got {result['load_5m']}"
        assert result["load_15m"] == 0.1, f"Expected 0.1, got {result['load_15m']}"

    # 🧪 TRAP[TEST] · TASK-13 · Regression: host_collector uptime graceful degradation
    # · Scenario: /proc files missing → all null
    # · Remove if: get_host_uptime removed
    def test_host_collector_uptime_missing_files(self, caplog):
        """get_host_uptime returns all nulls when /proc files are missing."""
        caplog.set_level(0)

        # 167 D6 (DI-zero): open_fn DI — FileNotFoundError через параметр, 0 monkeypatch builtins.open
        original_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open

        def _mock_open(path, *args, **kwargs):
            path_str = str(path)
            if "/proc/uptime" in path_str or "/proc/loadavg" in path_str:
                msg_0 = f"No such file: {path_str}"
                raise FileNotFoundError(msg_0)
            return original_open(path, *args, **kwargs)

        from core.internal.healthcheck.metrics.host_collector import get_host_uptime

        result = get_host_uptime(open_fn=_mock_open)

        _print_ldd_trajectory(caplog)

        assert result["uptime_seconds"] is None
        assert result["load_1m"] is None
        assert result["load_5m"] is None
        assert result["load_15m"] is None


# ═══════════════════════════════════════════════════════════════════
# TESTS: json_writer
# ═══════════════════════════════════════════════════════════════════


class TestJsonWriter:
    """Tests for json_writer.py."""

    def test_json_writer_atomic(self, tmp_path, caplog):
        """atomic_write creates a complete and valid JSON file."""
        caplog.set_level(0)

        target = tmp_path / "status-metrics.json"
        data = {"node": "test", "containers": [{"name": "nginx"}]}

        from core.internal.healthcheck.metrics.json_writer import atomic_write

        atomic_write(data, str(target))

        _print_ldd_trajectory(caplog)

        assert target.exists(), "Target file should exist"
        content = json.loads(target.read_text())
        assert content["node"] == "test"
        assert len(content["containers"]) == 1

    def test_json_writer_schema_version(self, tmp_path, caplog):
        """atomic_write injects schema_version: 2 (SCHEMA_VERSION)."""
        caplog.set_level(0)

        target = tmp_path / "metrics.json"
        data = {"node": "test"}

        from core.internal.healthcheck.metrics.json_writer import SCHEMA_VERSION, atomic_write

        atomic_write(data, str(target))

        _print_ldd_trajectory(caplog)

        loaded = json.loads(target.read_text())
        assert loaded["schema_version"] == SCHEMA_VERSION, (
            f"Expected schema_version={SCHEMA_VERSION}, got {loaded.get('schema_version')}"
        )

    def test_json_writer_tmp_cleanup_on_failure(self, tmp_path, caplog):
        """atomic_write cleans up temp file on failure."""
        caplog.set_level(0)

        target = tmp_path / "metrics-fail.json"
        # Data with non-serializable value
        data = {"bad_data": object()}  # This will fail json.dump

        from core.internal.healthcheck.metrics.json_writer import atomic_write

        with contextlib.suppress(TypeError, RuntimeError, OSError):
            atomic_write(data, str(target))

        _print_ldd_trajectory(caplog)

        # Target should NOT exist (write failed atomically)
        assert not target.exists(), "Target should not exist after failed write"
        # Temp files should be cleaned up
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Temp files should be cleaned up: {tmp_files}"


# ═══════════════════════════════════════════════════════════════════
# TESTS: cache
# ═══════════════════════════════════════════════════════════════════


class TestCacheManager:
    """Tests for cache.py CacheManager."""

    def test_cache_ttl_hit(self, tmp_path, caplog):
        """Cache hit when data is fresh (within TTL)."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.cache import CacheManager

        cache = CacheManager(str(tmp_path))
        cache.set("test_key", {"value": 42})

        # Immediately get — should be HIT
        result = cache.get("test_key", ttl_seconds=3600)

        _print_ldd_trajectory(caplog)

        assert result is not None, "Expected cache HIT"
        assert result["value"] == 42

    def test_cache_ttl_miss(self, tmp_path, caplog):
        """Cache miss when TTL expired."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.cache import CacheManager

        cache = CacheManager(str(tmp_path))

        # Set with timestamp in the past by manipulating the file
        old_data = {"timestamp": time.time() - 7200, "data": {"value": 42}}  # 2 hours old
        cache_path = cache._cache_path("test_key")
        with Path(cache_path).open("w", encoding="utf-8") as f:
            json.dump(old_data, f)

        # Get with 1 hour TTL — should MISS
        result = cache.get("test_key", ttl_seconds=3600)

        _print_ldd_trajectory(caplog)

        assert result is None, "Expected cache MISS (TTL expired)"

    def test_cache_mtime_invalidation(self, tmp_path, caplog):
        """Cache miss when source mtime is newer than cache timestamp."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.cache import CacheManager

        cache = CacheManager(str(tmp_path))
        cache.set("test_mtime", {"value": "old"})

        # Source mtime in the future → should miss
        future_mtime = time.time() + 100
        result = cache.get("test_mtime", ttl_seconds=3600, source_mtime=future_mtime)

        _print_ldd_trajectory(caplog)

        assert result is None, "Expected cache MISS (mtime invalidated)"


# ═══════════════════════════════════════════════════════════════════
# TESTS: project_collector
# ═══════════════════════════════════════════════════════════════════


class TestProjectCollector:
    """Tests for project_collector.py."""

    def test_project_collector_mtime_cache(self, mock_node_yaml, mock_cache_dir, tmp_path, caplog):  # ruff: ignore[ARG002]
        """get_projects uses cache when mtime unchanged."""
        caplog.set_level(0)

        # Create project directory in the default path /opt/projects/<name>
        # Mock os.path.isdir to simulate project dir exists
        proj_dir = Path("/opt/projects/test-app")

        from core.internal.healthcheck.metrics.cache import CacheManager
        from core.internal.healthcheck.metrics.project_collector import get_projects

        cache = CacheManager(mock_cache_dir)

        # First call: cache MISS, runs du -sb
        with (
            mock.patch("core.internal.healthcheck.metrics.project_collector.subprocess.run") as mock_run,
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.isdir", return_value=True),
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.getmtime", return_value=1000.0),
        ):
            mock_run.return_value = mock.Mock(returncode=0, stdout=f"12345\t{proj_dir}\n", stderr="")

            projects1 = get_projects(mock_node_yaml, image_cache={}, cache_mgr=cache)

            _print_ldd_trajectory(caplog)

            assert len(projects1) > 0
            assert mock_run.called, "du should have been called on first call"

        # Second call: mtime unchanged → cache HIT, NO du call
        with (
            mock.patch("core.internal.healthcheck.metrics.project_collector.subprocess.run") as mock_run2,
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.isdir", return_value=True),
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.getmtime", return_value=1000.0),
        ):
            projects2 = get_projects(mock_node_yaml, image_cache={}, cache_mgr=cache)

            _print_ldd_trajectory(caplog)

            assert len(projects2) > 0
            assert not mock_run2.called, "du should NOT be called on second call (cache HIT)"

    # GUARD-PRESERVE (168): единственное покрытие ветки «node.yaml без проектов» get_projects → [] (без du/cache)
    def test_project_collector_empty_node(self, mock_node_yaml_no_projects, caplog):
        """get_projects returns empty list when no projects in node.yaml."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.project_collector import get_projects

        projects = get_projects(mock_node_yaml_no_projects)

        _print_ldd_trajectory(caplog)

        assert projects == []


# ═══════════════════════════════════════════════════════════════════
# TESTS: coordinator (partial/e2e simulation)
# ═══════════════════════════════════════════════════════════════════


class TestCoordinator:
    """Tests for platform_export_metrics.py coordinator."""

    def test_coordinator_empty_state(self, mock_node_yaml_no_projects, tmp_path, caplog, mock_docker_subprocess):
        """Coordinator handles empty state gracefully — no crash, empty arrays + errors."""
        caplog.set_level(0)

        metrics_file = tmp_path / "status-metrics.json"
        # env-дикт (DI — W4e): координатор читает конфиг через main(env=), без reload
        env = {
            "STATUS_METRICS_JSON": str(metrics_file),
            "NODE_YAML_PATH": mock_node_yaml_no_projects,
            "NODE_NAME": "test-node",
            "METRICS_CACHE_DIR": str(tmp_path / "cache"),
        }

        # Mock all subprocess.run to return empty (no docker)
        mock_docker_subprocess.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        exit_code = main(env=env)

        _print_ldd_trajectory(caplog)

        assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
        assert metrics_file.exists(), "Metrics file should exist"
        data = json.loads(metrics_file.read_text())
        assert "containers" in data
        assert "certs" in data
        assert "projects" in data
        assert "host" in data
        assert "errors" in data

    def test_coordinator_partial_failure(self, mock_node_yaml, tmp_path, caplog, mock_docker_subprocess):
        """Coordinator produces partial data with errors when some collectors fail."""
        caplog.set_level(0)

        metrics_file = tmp_path / "status-metrics-partial.json"
        env = {
            "STATUS_METRICS_JSON": str(metrics_file),
            "NODE_YAML_PATH": mock_node_yaml,
            "NODE_NAME": "test-node",
            "METRICS_CACHE_DIR": str(tmp_path / "cache"),
        }

        # Mock docker to succeed but cert to fail
        mock_docker_subprocess.side_effect = [
            # docker ps -aq
            mock.Mock(returncode=0, stdout="abc123\n", stderr=""),
            # docker inspect
            mock.Mock(
                returncode=0,
                stdout=json.dumps([
                    {
                        "Id": "sha256:abc123",
                        "Name": "/nginx",
                        "State": {
                            "Running": True,
                            "ExitCode": 0,
                            "Status": "Up 2 hours (healthy)",
                            "Health": {"Status": "healthy"},
                        },
                        "Config": {"Image": "nginx:latest"},
                        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                    }
                ]),
                stderr="",
            ),
            # docker stats
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        exit_code = main(env=env)

        _print_ldd_trajectory(caplog)

        assert exit_code == 0, f"Expected exit code 0 (partial data OK), got {exit_code}"
        data = json.loads(metrics_file.read_text())
        assert len(data["containers"]) > 0, "Containers should be present"
        assert "errors" in data

    def test_coordinator_invalid_yaml(self, tmp_path, caplog, mock_docker_subprocess):
        """Coordinator handles invalid node.yaml gracefully."""
        caplog.set_level(0)

        bad_yaml = tmp_path / "bad-node.yaml"
        bad_yaml.write_text("{{invalid_yaml: [broken")

        metrics_file = tmp_path / "status-metrics.json"
        env = {
            "STATUS_METRICS_JSON": str(metrics_file),
            "NODE_YAML_PATH": str(bad_yaml),
            "NODE_NAME": "test-node",
            "METRICS_CACHE_DIR": str(tmp_path / "cache"),
        }

        # Mock docker to succeed
        mock_docker_subprocess.side_effect = [
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        exit_code = main(env=env)

        _print_ldd_trajectory(caplog)

        assert exit_code == 0, "Should still exit 0 with partial data"
        data = json.loads(metrics_file.read_text())
        # R2 (DevPlan 119 F10): len(errors) >= 0 — всегда истина (unfalsifiable).
        # Ужесточено до == 0 — при invalid yaml + успешном docker mock коллекторы
        # graceful-degrade без ошибок (errors[] пуст).
        errors = data.get("errors", [])
        assert len(errors) == 0, f"Unexpected errors: {errors}"


# ═══════════════════════════════════════════════════════════════════
# TESTS: tls-секция (017 C4 — owner: «мониторинг видит TLS-бандл»)
# ═══════════════════════════════════════════════════════════════════


class TestTlsCollector:
    """017 C4: tls-секция — letsencrypt live dirs → openssl x509 → days_left/self_signed.

    ## @purpose — Покрытие _collect_tls_metrics (fail-safe: пустая секция без live-каталога,
    ##            per-domain skip, openssl-отсутствие) + end-to-end main() → status-metrics.json.
    ## @invariants
    ##   - Фикстура tls_live_dir: ЗАКОММИЧЕННЫЙ tests/test_data/tls_example_test.crt
    ##     (self-signed CN=example.test, 365d) — без сети, без runtime-генерации
    ##   - openssl — реальный системный бинар (не мокается в успешном сценарии)
    ##   - Тест-честность: R1 (asserts есть), R2 (days_left > 360 — falsifiable при 365d-серте)
    """

    # 🧪 TRAP[TEST] · C4 · Regression: tls-секция парсит self-signed серт из live-каталога
    # · Scenario: example.test/fullchain.pem (закоммиченный dummy-серт) → openssl x509 →
    # ·   not_after ISO UTC, days_left > 360 (365d-валидность), self_signed True (issuer==subject)
    # · Last fail: never (new feature, 017 C4)
    # · Remove if: tls-секция удалена из platform_export_metrics
    def test_tls_section_parses_self_signed_cert(self, tls_live_dir, caplog):
        """_collect_tls_metrics возвращает {domain: {not_after, days_left, self_signed}}."""
        caplog.set_level(0)

        section = _collect_tls_metrics(env={"LETSENCRYPT_LIVE": str(tls_live_dir)})

        _print_ldd_trajectory(caplog)

        assert "example.test" in section, f"tls-секция должна содержать example.test: {section}"
        entry = section["example.test"]
        assert entry["self_signed"] is True, f"dummy-серт self-signed: {entry}"
        assert isinstance(entry["days_left"], int), f"days_left должен быть int: {entry}"
        assert entry["days_left"] > 360, f"365d-серт должен иметь >360 дней до истечения, got {entry['days_left']}"
        assert "T" in entry["not_after"], f"not_after должен быть ISO 8601: {entry['not_after']}"
        assert entry["not_after"].endswith("Z"), f"not_after должен быть UTC (Z-суффикс): {entry['not_after']}"
        assert any("[IMP:9][tls][collect]" in r.message for r in caplog.records), "ожидался IMP:9 лог успешного домена"

    # 🧪 TRAP[TEST] · C4 · Regression: отсутствующий live-каталог → пустая секция, не исключение
    # · Scenario: LETSENCRYPT_LIVE указывает на несуществующий путь → {} + IMP:8 warning
    # · Last fail: never (new feature)
    # · Remove if: fail-safe контракт tls-секции изменён
    def test_tls_section_missing_dir_empty(self, tmp_path, caplog):
        """Нет live-каталога → {} (пустая секция), НЕ исключение (fail-safe C4)."""
        caplog.set_level(0)

        section = _collect_tls_metrics(env={"LETSENCRYPT_LIVE": str(tmp_path / "no-such-live")})

        _print_ldd_trajectory(caplog)

        assert section == {}, f"пустая секция ожидалась: {section}"
        assert any("[IMP:8][tls][collect]" in r.message for r in caplog.records), (
            "ожидался IMP:8 warning о пустой секции"
        )

    # 🧪 TRAP[TEST] · C4 · Regression: openssl отсутствует → per-domain skip, не валит export
    # · Scenario: subprocess.run поднимает FileNotFoundError (openssl не установлен) →
    # ·   домен пропускается с IMP:7, секция пуста, исключение НЕ пробрасывается
    # · Last fail: never (new feature)
    # · Remove if: fail-safe контракт tls-секции изменён
    def test_tls_section_openssl_missing(self, tls_live_dir, caplog, monkeypatch):
        """openssl-бинар недоступен → {} + IMP:7, без проброса исключения."""
        caplog.set_level(0)

        def _raise_missing(*args, **kwargs):
            msg = "openssl binary not found"
            raise FileNotFoundError(msg)

        monkeypatch.setattr("core.internal.healthcheck.platform_export_metrics.subprocess.run", _raise_missing)

        section = _collect_tls_metrics(env={"LETSENCRYPT_LIVE": str(tls_live_dir)})

        _print_ldd_trajectory(caplog)

        assert section == {}, f"при отсутствии openssl секция должна быть пуста: {section}"
        assert any("[IMP:7][tls][collect]" in r.message for r in caplog.records), "ожидался IMP:7 skip-лог"

    # 🧪 TRAP[TEST] · C4 · Regression: домен без fullchain.pem пропускается per-domain
    # · Scenario: live-каталог содержит ok.example.test (серт) и bad.example.test (без pem) →
    # ·   bad пропускается с IMP:7, ok парсится — секция не валится целиком
    # · Last fail: never (new feature)
    # · Remove if: per-domain fail-safe изменён
    def test_tls_section_skips_domain_without_fullchain(self, tmp_path, caplog):
        """Домен без fullchain.pem → skip с IMP:7, остальные домены парсятся."""
        caplog.set_level(0)

        live = tmp_path / "live"
        (live / "ok.example.test").mkdir(parents=True)
        (live / "bad.example.test").mkdir()
        crt = Path(__file__).resolve().parent.parent / "test_data" / "tls_example_test.crt"
        shutil.copy(crt, live / "ok.example.test" / "fullchain.pem")

        section = _collect_tls_metrics(env={"LETSENCRYPT_LIVE": str(live)})

        _print_ldd_trajectory(caplog)

        assert "ok.example.test" in section, f"ok-домен должен быть распарсен: {section}"
        assert "bad.example.test" not in section, "домен без pem должен быть пропущен"
        assert any("[IMP:7][tls][collect] skip domain=bad.example.test" in r.message for r in caplog.records), (
            "ожидался IMP:7 skip-лог для bad.example.test"
        )

    # 🧪 TRAP[TEST] · C4 · Regression: main() пишет tls-секцию в status-metrics.json (e2e)
    # · Scenario: координатор с LETSENCRYPT_LIVE=fixture + docker-mock (openssl — реальный) →
    # ·   status-metrics.json содержит tls.example.test.days_left > 360 / self_signed True
    # · Last fail: never (new feature)
    # · Remove if: tls-секция удалена из output main()
    def test_coordinator_writes_tls_section(
        self, mock_node_yaml_no_projects, tmp_path, caplog, mock_docker_subprocess, tls_live_dir
    ):
        """main() пишет tls-секцию в status-metrics.json (owner-критерий C4 end-to-end)."""
        caplog.set_level(0)

        metrics_file = tmp_path / "status-metrics-tls.json"
        env = {
            "STATUS_METRICS_JSON": str(metrics_file),
            "NODE_YAML_PATH": mock_node_yaml_no_projects,
            "NODE_NAME": "test-node",
            "METRICS_CACHE_DIR": str(tmp_path / "cache"),
            "LETSENCRYPT_LIVE": str(tls_live_dir),
        }

        # docker-команды мокаются (пустой вывод); openssl-команда — реальный бинар
        # (mock_docker_subprocess патчит глобальный subprocess.run — диспетчеризуем по argv[0];
        # реальная функция захвачена на module-import, ДО активации fixture-патча)
        def _dispatch(cmd, *args, **kwargs):
            if cmd and cmd[0] == "openssl":
                return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)
            return mock.Mock(returncode=0, stdout="", stderr="")

        mock_docker_subprocess.side_effect = _dispatch

        exit_code = main(env=env)

        _print_ldd_trajectory(caplog)

        assert exit_code == 0, f"координатор должен завершиться 0, got {exit_code}"
        data = json.loads(metrics_file.read_text())
        assert "tls" in data, "status-metrics.json должен содержать tls-секцию"
        assert "example.test" in data["tls"], f"tls-секция должна содержать example.test: {data['tls']}"
        assert data["tls"]["example.test"]["self_signed"] is True
        assert data["tls"]["example.test"]["days_left"] > 360, (
            f"days_left > 360 ожидался, got {data['tls']['example.test']['days_left']}"
        )


# ═══════════════════════════════════════════════════════════════════
# TESTS: NODE_NAME auto-detection (P2 fix — DevPlan 066 Wave 2)
# ═══════════════════════════════════════════════════════════════════


class TestNodeNameAutoDetection:
    """P2 fix: NODE_NAME auto-detection from /opt/node-configs/ or env (DI — env= параметр)."""

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({"NODE_NAME": "my-production-node"}, "my-production-node", id="from_env"),
            pytest.param({}, "unknown", id="default"),
        ],
    )
    # 🧪 TRAP[TEST] · Regression: NODE_NAME auto-detection (P2 fix, DevPlan 066 W2)
    # · Scenario: env NODE_NAME → значение; без env → 'unknown' fallback
    # · Last fail: never (new feature)
    # · Remove if: NODE_NAME fallback-семантика изменена
    def test_auto_detect_node_name(self, env, expected) -> None:
        """_get_node_name: NODE_NAME из env → значение; без env → 'unknown' fallback."""
        result = _get_node_name(env=env)
        assert result == expected, f"Expected {expected!r}, got '{result}'"

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({"NODE_YAML_PATH": "/custom/path/node.yaml"}, "/custom/path/node.yaml", id="from_env"),
            pytest.param({"NODE_NAME": "test-node"}, "/opt/node-configs/test-node/node.yaml", id="default"),
        ],
    )
    # 🧪 TRAP[TEST] · Regression: NODE_YAML_PATH auto-detection (P2 fix, DevPlan 066 W2)
    # · Scenario: env NODE_YAML_PATH → custom; NODE_NAME → /opt/node-configs/<name>/node.yaml
    # · Last fail: never (new feature)
    # · Remove if: node.yaml path resolution изменена
    def test_auto_detect_node_yaml_path(self, env, expected) -> None:
        """_get_node_yaml_path: NODE_YAML_PATH env → custom; NODE_NAME → /opt/node-configs/<name>/node.yaml."""
        result = _get_node_yaml_path(env=env)
        assert result.endswith(expected), f"Expected path ending with {expected!r}, got '{result}'"


# ══════════════════════════════════════════════════════════════════════
# WRAPPER CONTRACT: PYTHONPATH order (TRAP[BUG] 2026-08-12)
# ══════════════════════════════════════════════════════════════════════


class TestExportMetricsWrapperOrdering:
    """Regression: platform-export-metrics.sh must export PYTHONPATH BEFORE node_detect.

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · wrapper PYTHONPATH order — TRAP[BUG] 2026-08-12
    # · Last fail: cron env (no PYTHONPATH, cwd=/) → `python3 -m core.internal.shared.node_detect`
    #   падал ModuleNotFoundError (ошибка глушится 2>/dev/null) → NODE_NAME=unknown →
    #   node.yaml не найден → status-metrics.json писал projects=[], certs=[] →
    #   status-page показывал «0 project(s)» при живых проектах в node.yaml
    # · Remove if: wrapper перестанет полагаться на node_detect через python3 -m
    """

    @pytest.fixture()
    def wrapper_path(self) -> Path:
        p = (
            Path(__file__).resolve().parent.parent.parent
            / "core"
            / "internal"
            / "healthcheck"
            / "platform-export-metrics.sh"
        )
        assert p.is_file(), f"wrapper not found: {p}"
        return p

    def test_pythonpath_export_before_node_detect(self, wrapper_path: Path) -> None:
        """PYTHONPATH export line must precede node_detect invocation in the wrapper."""
        lines = wrapper_path.read_text(encoding="utf-8").splitlines()
        py_export = next((i for i, line in enumerate(lines) if "export PYTHONPATH" in line), None)
        node_detect = next((i for i, line in enumerate(lines) if "node_detect --detect-node-name" in line), None)
        assert py_export is not None, "export PYTHONPATH line missing from wrapper"
        assert node_detect is not None, "node_detect invocation missing from wrapper"
        assert py_export < node_detect, (
            f"PYTHONPATH export (line {py_export}) must precede node_detect (line {node_detect}) "
            "— иначе в cron-окружении NODE_NAME=unknown и метрики пустеют"
        )
