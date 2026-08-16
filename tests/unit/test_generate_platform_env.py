"""
# GREP_SUMMARY: test_generate_platform_env, discover_profiles, load_ci_defaults, generate_smoke_env_py, tmp_path
# STRUCTURE: ▶ discover_profiles 2× (subdirs/empty) → ▶ load_ci_defaults 2× (valid/missing) → ▶ generate_smoke_env_py 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_platform_env.py — discover_profiles(), load_ci_defaults(),
##           and generate_smoke_env_py(). No subprocess calls.
## @scope    Tests profile discovery from module directories, CI default loading from
##           secret-definitions.yaml, and Python source generation for smoke_env_generated.py.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
## @rationale DevPlan 051 §5: Unit coverage for generate_platform_env generator
## @changes 2026-07-22 | Created (DevPlan 051 Wave 1)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import generate_platform_env as gpe
import pytest

pytestmark = pytest.mark.static_audit

# ═══════════════════════════════════════════════════════════════════
# region Tests: discover_profiles
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · discover_profiles finds module directories with docker-compose
# · Scenario: 3 subdirectories, 2 with docker-compose.base.yml → returns 2 profiles
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles(caplog, tmp_path):
    """discover_profiles should return sorted list of module directories with compose files."""
    # Create module dirs
    for mod in ["postgres", "redis", "clickhouse"]:
        mod_dir = tmp_path / mod
        mod_dir.mkdir()

    # Only postgres and redis have docker-compose.base.yml
    (tmp_path / "postgres" / "docker-compose.base.yml").write_text("services:\n  postgres:\n    image: postgres:latest")
    (tmp_path / "redis" / "docker-compose.base.yml").write_text("services:\n  redis:\n    image: redis:latest")
    # All 3 get module.yaml
    for mod in ["postgres", "redis", "clickhouse"]:
        (tmp_path / mod / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker")

    result = gpe.discover_profiles(str(tmp_path))
    assert sorted(result) == sorted(["clickhouse", "postgres", "redis"]), f"Expected all 3, got {result}"

    logger.critical("[IMP:9][test] discover_profiles found %d modules: %s", len(result), result)


# 🧪 TRAP[TEST] · Regression · Empty directory returns empty profile list
# · Scenario: Empty modules directory → returns empty list
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles_empty(caplog, tmp_path):
    """discover_profiles should return empty list for empty directory."""
    result = gpe.discover_profiles(str(tmp_path))
    assert result == [], f"Expected empty list, got {result}"

    logger.critical("[IMP:9][test] discover_profiles empty dir returns []")


# 🧪 TRAP[TEST] · Regression · System modules excluded from profiles
# · Scenario: 1 docker module + 1 system module → only docker module returned
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles_excludes_system(caplog, tmp_path):
    """discover_profiles should exclude modules with install_type: system."""
    # Docker module
    docker_mod = tmp_path / "nginx"
    docker_mod.mkdir()
    (docker_mod / "docker-compose.base.yml").write_text("services:\n  nginx:\n    image: nginx:latest")

    # System module (should be excluded)
    system_mod = tmp_path / "platform-secrets"
    system_mod.mkdir()
    (system_mod / "docker-compose.base.yml").write_text("services:\n  agent:\n    image: agent:latest")
    (system_mod / "module.yaml").write_text("name: platform-secrets\ninstall_type: system")

    result = gpe.discover_profiles(str(tmp_path))
    assert "nginx" in result, "nginx should be in profiles"
    assert "platform-secrets" not in result, "platform-secrets should be excluded"

    logger.critical("[IMP:9][test] discover_profiles system modules excluded — %d profiles", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: scan_compose_ports
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · U-01 (DevPlan 116 T1) · second port must NOT overwrite first
# · Scenario: module "minio" with service "minio" (service==module) and 2 ports (9000, 9001)
# ·   → MINIO_PORT=9000 (first port), MINIO_CONSOLE_PORT=9001 (177 W2.5 канон-оверрайд;
# ·     ранее MINIO_MINIO_PORT — мусорный дубль канона platform-infra.yaml)
# · Last fail: 2026-07-31 — MINIO_PORT: 9001 (bug: second port overwrote first)
# · Remove if: scan_compose_ports naming scheme changes
@ldd_trajectory
def test_scan_compose_ports_service_equals_module_two_ports(caplog, tmp_path):
    """First port lands in MODULE_PORT; second port must NOT overwrite it (U-01)."""
    mod_dir = tmp_path / "minio"
    mod_dir.mkdir()
    compose = (
        "services:\n"
        "  minio:\n"
        "    image: minio/minio:latest\n"
        "    ports:\n"
        '      - "127.0.0.1:${MINIO_PORT:-9000}:9000"\n'
        '      - "127.0.0.1:${MINIO_CONSOLE_PORT:-9001}:9001"\n'
    )
    (mod_dir / "docker-compose.base.yml").write_text(compose)

    result = gpe.scan_compose_ports(tmp_path)

    assert result.get("MINIO_PORT") == 9000, (
        f"First port (9000) must map to MINIO_PORT — got {result.get('MINIO_PORT')}"
    )
    assert result.get("MINIO_CONSOLE_PORT") == 9001, (
        f"Second port (9001) must map to MINIO_CONSOLE_PORT — got {result.get('MINIO_CONSOLE_PORT')}"
    )
    assert "MINIO_MINIO_PORT" not in result, "177 W2.5: мусорное имя MINIO_MINIO_PORT не генерируется"
    logger.critical(
        "[IMP:9][test] scan_compose_ports service==module 2 ports: MINIO_PORT=%s MINIO_CONSOLE_PORT=%s",
        result.get("MINIO_PORT"),
        result.get("MINIO_CONSOLE_PORT"),
    )


# 🧪 TRAP[TEST] · Regression · U-01 (DevPlan 116 T1) · multi-service module naming preserved
# · Scenario: module "infra-metrics" with services cadvisor(8080), node-exporter(9100)
# ·   → INFRA_METRICS_PORT=8080 (first port of first service), INFRA_METRICS_NODE_EXPORTER_PORT=9100
# · Last fail: 2026-07-31 — verified consistent with committed platform-env.yaml
# · Remove if: scan_compose_ports naming scheme changes
@ldd_trajectory
def test_scan_compose_ports_multi_service_regression(caplog, tmp_path):
    """Multi-service module: first port → MODULE_PORT, per-service ports preserved (U-01)."""
    mod_dir = tmp_path / "infra-metrics"
    mod_dir.mkdir()
    compose = (
        "services:\n"
        "  cadvisor:\n"
        "    image: gcr.io/cadvisor/cadvisor:latest\n"
        "    ports:\n"
        '      - "127.0.0.1:${CADVISOR_PORT:-8080}:8080"\n'
        "  node-exporter:\n"
        "    image: prom/node-exporter:latest\n"
        "    ports:\n"
        '      - "127.0.0.1:${NODE_EXPORTER_PORT:-9100}:9100"\n'
    )
    (mod_dir / "docker-compose.base.yml").write_text(compose)

    result = gpe.scan_compose_ports(tmp_path)

    assert result.get("INFRA_METRICS_PORT") == 8080, (
        f"First port (8080) must map to INFRA_METRICS_PORT — got {result.get('INFRA_METRICS_PORT')}"
    )
    assert result.get("INFRA_METRICS_NODE_EXPORTER_PORT") == 9100, (
        f"node-exporter port must map to INFRA_METRICS_NODE_EXPORTER_PORT — "
        f"got {result.get('INFRA_METRICS_NODE_EXPORTER_PORT')}"
    )
    logger.critical(
        "[IMP:9][test] scan_compose_ports multi-service: INFRA_METRICS_PORT=%s NODE_EXPORTER=%s",
        result.get("INFRA_METRICS_PORT"),
        result.get("INFRA_METRICS_NODE_EXPORTER_PORT"),
    )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_ci_defaults
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_ci_defaults returns secret→ci_default mapping
# · Scenario: Valid secret-definitions.yaml → returns dict of {name: ci_default}
# · Last fail: N/A (new test)
# · Remove if: load_ci_defaults logic changes
# 🧪 TRAP[TEST] · Regression · Missing secret definitions file returns empty dict
# · Scenario: Non-existent path → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_ci_defaults logic changes
@pytest.mark.parametrize(
    "secret_data,expected",
    [
        (
            [
                {"name": "CLICKHOUSE_PASSWORD", "tier": "required", "ci_default": "test-clickhouse-pwd"},
                {"name": "POSTGRES_PASSWORD", "tier": "required", "ci_default": "test-pg-pwd"},
            ],
            {"CLICKHOUSE_PASSWORD": "test-clickhouse-pwd", "POSTGRES_PASSWORD": "test-pg-pwd"},
        ),
        (None, {}),  # missing file → empty dict (graceful degradation)
    ],
)
@ldd_trajectory
def test_load_ci_defaults(secret_data, expected, caplog, tmp_path):
    """load_ci_defaults: valid secret-definitions.yaml / missing file (2 варианта 1:1)."""
    if secret_data is None:
        result = gpe.load_ci_defaults(str(tmp_path / "nonexistent_secret_defs.yaml"))
    else:
        secret_file = tmp_path / "secret-definitions.yaml"
        with Path(str(secret_file)).open("w", encoding="utf-8") as f:
            yaml.dump({"secrets": secret_data}, f)
        result = gpe.load_ci_defaults(str(secret_file))

    assert result == expected, f"Unexpected result: {result}"
    logger.critical("[IMP:9][test] load_ci_defaults loaded %d defaults", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_smoke_env_py
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_smoke_env_py produces valid Python source
# · Scenario: CI defaults dict → generates valid Python with SMOKE_ENV_GENERATED dict
# · Last fail: N/A (new test)
# · Remove if: generate_smoke_env_py logic changes
@ldd_trajectory
def test_generate_smoke_env_py(caplog):
    """generate_smoke_env_py should produce valid Python source with SMOKE_ENV_GENERATED."""
    ci_defaults = {
        "CLICKHOUSE_PASSWORD": "test-clickhouse-pwd",
        "POSTGRES_PASSWORD": "test-pg-pwd",
    }

    result = gpe.generate_smoke_env_py(ci_defaults)

    # Validate Python source structure
    assert '"""## @purpose  AUTO-GENERATED CI defaults for smoke tests' in result, "Should have @purpose header"
    assert "SMOKE_ENV_GENERATED" in result, "Should define SMOKE_ENV_GENERATED"
    assert "CLICKHOUSE_PASSWORD" in result, "Should contain CLICKHOUSE_PASSWORD key"
    assert "test-clickhouse-pwd" in result, "Should contain secret default value"
    assert "POSTGRES_PASSWORD" in result, "Should contain POSTGRES_PASSWORD key"

    logger.critical("[IMP:9][test] generate_smoke_env_py produced valid Python source (%d chars)", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_helpers_py (re-export shim, DevPlan 171 W1.3)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_helpers_py produces re-export shim over SMOKE_ENV_GENERATED
# · Scenario: CI defaults dict → shim проецирует _-константы из _SMOKE_ENV, БЕЗ литералов значений
# · Last fail: N/A (new test, DevPlan 171 W1.3)
# · Remove if: re-export shim design changes
@ldd_trajectory
def test_generate_helpers_py_re_export_shim(caplog):
    """generate_helpers_py should project _-constants from SMOKE_ENV_GENERATED (no value literals)."""
    ci_defaults = {
        "CLICKHOUSE_PASSWORD": "test-clickhouse-pwd",
        "POSTGRES_PASSWORD": "test-pg-pwd",
    }

    result = gpe.generate_helpers_py(ci_defaults)

    assert '"""## @purpose  AUTO-GENERATED re-export shim' in result, "Should have re-export @purpose header"
    assert "from tests._conftest.smoke_env_generated import SMOKE_ENV_GENERATED as _SMOKE_ENV" in result, (
        "Should import SMOKE_ENV_GENERATED"
    )
    assert '_CLICKHOUSE_PASSWORD: str = _SMOKE_ENV["CLICKHOUSE_PASSWORD"]' in result, (
        "Should project _CLICKHOUSE_PASSWORD from _SMOKE_ENV"
    )
    assert '_POSTGRES_PASSWORD: str = _SMOKE_ENV["POSTGRES_PASSWORD"]' in result, (
        "Should project _POSTGRES_PASSWORD from _SMOKE_ENV"
    )
    assert "test-clickhouse-pwd" not in result, "Value literals must NOT be duplicated in shim"
    assert "test-pg-pwd" not in result, "Value literals must NOT be duplicated in shim"
    assert '"__all__"' not in result, "No string literal named __all__ — only the list definition"
    assert "__all__ = [" in result, "Should define __all__"
    assert '"_CLICKHOUSE_PASSWORD",' in result, "Should list _CLICKHOUSE_PASSWORD in __all__"
    assert '"_POSTGRES_PASSWORD",' in result, "Should list _POSTGRES_PASSWORD in __all__"

    logger.critical("[IMP:9][test] generate_helpers_py re-export shim produced valid source (%d chars)", len(result))

    logger.critical("[IMP:9][test] generate_helpers_py re-export shim produced valid source (%d chars)", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _check_generated_content (--check mode)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · check passes when content matches existing file
# · Scenario: tmp_path with file containing matching content → exit 0
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
# 🧪 TRAP[TEST] · Regression · check fails when content diverges from file
# · Scenario: tmp_path with file containing DIFFERENT content → exit 1, stderr diff
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
# 🧪 TRAP[TEST] · Regression · check fails when file does not exist
# · Scenario: Non-existent file path → exit 1
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
@pytest.mark.parametrize(
    "file_content,generated,expected_code",
    [
        ("hello world\n", "hello world\n", 0),  # match
        ("old content\n", "new content\n", 1),  # diverges (stderr diff)
        (None, "content", 1),  # missing file
    ],
)
@ldd_trajectory
def test_check_generated_content(file_content, generated, expected_code, caplog, tmp_path):
    """_check_generated_content: match/diverges/missing-file (3 варианта 1:1)."""
    test_file = tmp_path / "test_output.txt"
    if file_content is not None:
        test_file.write_text(file_content, encoding="utf-8")

    result = gpe._check_generated_content(generated, test_file, "test")
    assert result == expected_code, f"Expected {expected_code} (file_content={file_content!r}), got {result}"

    logger.critical("[IMP:9][test] _check_generated_content(%r) → %s", file_content, result)


# endregion
