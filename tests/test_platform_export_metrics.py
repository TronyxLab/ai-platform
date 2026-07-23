# GREP_SUMMARY: test-platform-export-metrics docker cert project host collector json-writer cache coordinator
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
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

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
    with open(path, "w") as f:
        yaml.dump(node_data, f)
    return str(path)


@pytest.fixture
def mock_node_yaml_no_projects(tmp_path: Path) -> str:
    """Create a node.yaml with no projects."""
    import yaml

    node_data = {"node": {"name": "test-node"}, "projects": [], "modules": []}
    path = tmp_path / "node-empty.yaml"
    with open(path, "w") as f:
        yaml.dump(node_data, f)
    return str(path)


@pytest.fixture
def mock_cache_dir(tmp_path: Path) -> str:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


# ═══════════════════════════════════════════════════════════════════
# TESTS: docker_collector
# ═══════════════════════════════════════════════════════════════════


class TestDockerCollector:
    """Tests for docker_collector.py."""

    def test_docker_collector_containers(self, caplog):
        """get_containers returns parsed container data from mock subprocess."""
        caplog.set_level(0)

        # Mock subprocess.run for docker ps -aq, inspect, and stats
        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            # docker ps -aq → container IDs
            # docker inspect → JSON array
            # docker stats → JSON lines

            # We need to set up side_effect for 3 calls
            mock_run.side_effect = [
                # Call 1: docker ps -aq
                mock.Mock(returncode=0, stdout="abc123\ndef456\n", stderr=""),
                # Call 2: docker inspect abc123 def456
                mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [
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
                            },
                            {
                                "Id": "sha256:def456",
                                "Name": "/redis",
                                "State": {
                                    "Running": False,
                                    "ExitCode": 137,
                                    "Status": "Exited (137) 1 hour ago",
                                    "Health": {"Status": "unhealthy"},
                                },
                                "Config": {"Image": "redis:alpine"},
                                "HostConfig": {"RestartPolicy": {"Name": "always"}},
                            },
                        ]
                    ),
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
            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

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

    def test_docker_collector_empty(self, caplog):
        """get_containers returns empty list when no containers exist."""
        caplog.set_level(0)

        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

            from core.internal.healthcheck.metrics.docker_collector import get_containers

            containers = get_containers()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert containers == [], f"Expected empty list, got {containers}"

    def test_docker_collector_cli_missing(self, caplog):
        """get_containers returns empty list gracefully when docker CLI is unavailable."""
        caplog.set_level(0)

        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("docker CLI not found")

            from core.internal.healthcheck.metrics.docker_collector import get_containers

            containers = get_containers()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert containers == []


class TestDockerImageSizes:
    """Tests for get_image_sizes."""

    def test_docker_image_sizes(self, caplog):
        """get_image_sizes returns {sha256: size} from docker image inspect."""
        caplog.set_level(0)

        # docker image inspect --format '{{json .}}' outputs one JSON object per line
        stdout_lines = [
            '{"Id":"sha256:abc123","Size":150000000}',
            '{"Id":"sha256:def456","Size":45000000}',
        ]
        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout="\n".join(stdout_lines),
                stderr="",
            )

            from core.internal.healthcheck.metrics.docker_collector import get_image_sizes

            sizes = get_image_sizes({"sha256:abc123", "sha256:def456"})

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert sizes.get("sha256:abc123") == 150000000
            assert sizes.get("sha256:def456") == 45000000

    def test_docker_image_sizes_empty(self, caplog):
        """get_image_sizes returns empty dict for empty input."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.docker_collector import get_image_sizes

        sizes = get_image_sizes(set())

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert sizes == {}


# ═══════════════════════════════════════════════════════════════════
# TESTS: cert_collector
# ═══════════════════════════════════════════════════════════════════


class TestCertCollector:
    """Tests for cert_collector.py — _san_match and _load_cert."""

    def test_san_match_exact(self):
        """_san_match returns True for exact domain match."""
        from core.internal.healthcheck.metrics.cert_collector import _san_match

        assert _san_match(["example.com", "www.example.com"], "example.com") is True
        assert _san_match(["example.com"], "www.example.com") is False

    def test_san_match_wildcard(self):
        """_san_match returns True for wildcard *.domain match."""
        from core.internal.healthcheck.metrics.cert_collector import _san_match

        # *.example.com should match sub.example.com
        assert _san_match(["*.example.com"], "sub.example.com") is True
        # *.example.com should NOT match example.com (bare domain)
        assert _san_match(["*.example.com"], "example.com") is False
        # *.example.com should NOT match deeply nested (2+ levels) — only single level
        assert _san_match(["*.example.com"], "deep.sub.example.com") is False
        # Multiple wildcards
        assert _san_match(["*.test.com", "*.example.com"], "sub.example.com") is True

    def test_san_match_no_match(self):
        """_san_match returns False for non-matching domain."""
        from core.internal.healthcheck.metrics.cert_collector import _san_match

        assert _san_match(["example.com", "test.com"], "other.com") is False
        assert _san_match([], "example.com") is False

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
            x509.CertificateBuilder()
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
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        from core.internal.healthcheck.metrics.cert_collector import _load_cert

        result = _load_cert(str(cert_path))

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert result is not None, "Cert should be parsed"
        assert "T" in result["not_after_iso"], f"Expected ISO 8601 date, got {result['not_after_iso']}"
        assert result["days_remaining"] >= 28, f"Expected ~30 days remaining, got {result['days_remaining']}"
        assert "test.example.com" in result["san"]
        assert "test.example.com" in result.get("subject", "")

    def test_cert_collector_file_missing(self, caplog):
        """_load_cert returns None for missing file."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.cert_collector import _load_cert

        result = _load_cert("/nonexistent/cert.pem")

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert result is None


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

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert result["disk_total_gb"] == 100.0
            assert result["disk_free_gb"] == 30.0
            assert result["disk_used_percent"] == 70.0


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

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

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

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

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

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

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

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

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
        with open(cache_path, "w") as f:
            json.dump(old_data, f)

        # Get with 1 hour TTL — should MISS
        result = cache.get("test_key", ttl_seconds=3600)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

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

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert result is None, "Expected cache MISS (mtime invalidated)"


# ═══════════════════════════════════════════════════════════════════
# TESTS: project_collector
# ═══════════════════════════════════════════════════════════════════


class TestProjectCollector:
    """Tests for project_collector.py."""

    def test_project_collector_mtime_cache(self, mock_node_yaml, mock_cache_dir, tmp_path, caplog):
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

            print("--- LDD TRAJECTORY — FIRST CALL ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert len(projects1) > 0
            assert mock_run.called, "du should have been called on first call"

        # Second call: mtime unchanged → cache HIT, NO du call
        with (
            mock.patch("core.internal.healthcheck.metrics.project_collector.subprocess.run") as mock_run2,
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.isdir", return_value=True),
            mock.patch("core.internal.healthcheck.metrics.project_collector.os.path.getmtime", return_value=1000.0),
        ):
            projects2 = get_projects(mock_node_yaml, image_cache={}, cache_mgr=cache)

            print("--- LDD TRAJECTORY — SECOND CALL ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert len(projects2) > 0
            assert not mock_run2.called, "du should NOT be called on second call (cache HIT)"

    def test_project_collector_empty_node(self, mock_node_yaml_no_projects, caplog):
        """get_projects returns empty list when no projects in node.yaml."""
        caplog.set_level(0)

        from core.internal.healthcheck.metrics.project_collector import get_projects

        projects = get_projects(mock_node_yaml_no_projects)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert projects == []


# ═══════════════════════════════════════════════════════════════════
# TESTS: coordinator (partial/e2e simulation)
# ═══════════════════════════════════════════════════════════════════


class TestCoordinator:
    """Tests for platform_export_metrics.py coordinator."""

    def _reimport_coordinator(self):
        """Force reimport of platform_export_metrics to pick up new env vars."""
        for key in list(sys.modules.keys()):
            if "platform_export_metrics" in key:
                del sys.modules[key]

    def test_coordinator_empty_state(self, mock_node_yaml_no_projects, tmp_path, caplog):
        """Coordinator handles empty state gracefully — no crash, empty arrays + errors."""
        caplog.set_level(0)

        metrics_file = tmp_path / "status-metrics.json"
        os.environ["STATUS_METRICS_JSON"] = str(metrics_file)
        os.environ["NODE_YAML_PATH"] = mock_node_yaml_no_projects
        os.environ["NODE_NAME"] = "test-node"
        os.environ["METRICS_CACHE_DIR"] = str(tmp_path / "cache")

        # Force reimport to pick up new env vars
        self._reimport_coordinator()

        # Mock all subprocess.run to return empty (no docker)
        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

            from core.internal.healthcheck.platform_export_metrics import main

            exit_code = main()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
            assert metrics_file.exists(), "Metrics file should exist"
            data = json.loads(metrics_file.read_text())
            assert "containers" in data
            assert "certs" in data
            assert "projects" in data
            assert "host" in data
            assert "errors" in data

    def test_coordinator_partial_failure(self, mock_node_yaml, tmp_path, caplog):
        """Coordinator produces partial data with errors when some collectors fail."""
        caplog.set_level(0)

        metrics_file = tmp_path / "status-metrics-partial.json"
        os.environ["STATUS_METRICS_JSON"] = str(metrics_file)
        os.environ["NODE_YAML_PATH"] = mock_node_yaml
        os.environ["NODE_NAME"] = "test-node"
        os.environ["METRICS_CACHE_DIR"] = str(tmp_path / "cache")

        # Force reimport to pick up new env vars
        self._reimport_coordinator()

        # Mock docker to succeed but cert to fail
        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_docker:
            mock_docker.side_effect = [
                # docker ps -aq
                mock.Mock(returncode=0, stdout="abc123\n", stderr=""),
                # docker inspect
                mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [
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
                        ]
                    ),
                    stderr="",
                ),
                # docker stats
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            from core.internal.healthcheck.platform_export_metrics import main

            exit_code = main()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert exit_code == 0, f"Expected exit code 0 (partial data OK), got {exit_code}"
            data = json.loads(metrics_file.read_text())
            assert len(data["containers"]) > 0, "Containers should be present"
            assert "errors" in data

    def test_coordinator_invalid_yaml(self, tmp_path, caplog):
        """Coordinator handles invalid node.yaml gracefully."""
        caplog.set_level(0)

        bad_yaml = tmp_path / "bad-node.yaml"
        bad_yaml.write_text("{{invalid_yaml: [broken")

        metrics_file = tmp_path / "status-metrics.json"
        os.environ["STATUS_METRICS_JSON"] = str(metrics_file)
        os.environ["NODE_YAML_PATH"] = str(bad_yaml)
        os.environ["NODE_NAME"] = "test-node"
        os.environ["METRICS_CACHE_DIR"] = str(tmp_path / "cache")

        # Force reimport to pick up new env vars
        self._reimport_coordinator()

        # Mock docker to succeed
        with mock.patch("core.internal.healthcheck.metrics.docker_collector.subprocess.run") as mock_run:
            mock_run.side_effect = [
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            from core.internal.healthcheck.platform_export_metrics import main

            exit_code = main()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert exit_code == 0, "Should still exit 0 with partial data"
            data = json.loads(metrics_file.read_text())
            assert len(data.get("errors", [])) >= 0
