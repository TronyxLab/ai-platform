"""
# GREP_SUMMARY: test_secrets_validator, check-env, charset-validation, module-metadata, batch-metadata, transitive-deps, node-yaml-parser, detect-install-type
# STRUCTURE: ┌tmp_path + mock YAML stubs → ◇ [7 test functions] → ∋ check_env_requires ⋙ ⊕ validate_secret_charsets ⋙ ⊕ get_module_severity ⋙ ⊕ _batch_module_metadata ⋙ ⊕ _expand_transitive_deps ⋙ ⊕ parse_modules_from_node_yaml ⋙ ⊕ detect_install_type → ⎋ LDD trajectory(IMP:7-10) assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for secrets_validator.py — all 7 public functions, tmp_path-based YAML fixtures, no external dependencies
## @scope    Direct Python import of secrets_validator module; tests each function in isolation with mock YAML files
## @invariants
##   - All tests use tmp_path for temp YAML files (no hardcoded paths)
##   - Each test includes caplog-based LDD trajectory [IMP:7-10] verification via ldd_trajectory decorator
##   - _expand_transitive_deps cycle handling verified via visited-set convergence (no infinite loop)
##   - File-not-found graceful degradation tested for every relevant function
## @rationale Tests mirror shell functions 1:1 — each function has dedicated tests for happy-path, edge-cases,
##            file-not-found, and type-variants. Compatible with Anti-Loop protocol via ldd_trajectory decorator.
## @changes
##   2026-07-22 · Created (W4-E1 secrets_validator)
##   2026-08-12 · DevPlan 160 W2 T2.2 — MERGE test_deploy_modules_env.py: +test_orchestrator_uses_secrets_validator_batch_env
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

# Import the module under test — fully-qualified (conftest site.addsitedir покрывает
# repo root/core/core.internal; sys.path.insert избыточен по D47)
from core.internal.bootstrap.deploy.secrets_validator import (
    _batch_module_metadata,
    _expand_transitive_deps,
    check_env_requires,
    detect_install_type,
    get_module_severity,
    parse_modules_from_node_yaml,
    validate_secret_charsets,
)
from core.internal.shared.exceptions import ConfigValidationError
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_SECRETS_MANIFEST = """version: 1
secrets:
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres, litellm, backup-cron]
    charset: "^[A-Za-z0-9._-]+$"
  - name: POSTGRES_USER
    tier: required
    consumers: [postgres, service-exporters]
  - name: MINIO_ROOT_USER
    tier: required
    consumers: [minio]
    charset: "^[A-Za-z0-9._-]+$"
  - name: MINIO_ROOT_PASSWORD
    tier: required
    consumers: [minio]
    charset: "^[A-Za-z0-9._-]+$"
  - name: LITELLM_MASTER_KEY
    tier: generated
    consumers: [litellm]
  - name: LANGFUSE_SECRET_KEY
    tier: generated
    consumers: [langfuse]
"""

SAMPLE_MODULE_YAML = """name: postgres
install_type: docker
severity: critical
depends_on:
  - postgres-data
  - platform-network
"""

SAMPLE_MODULE_YAML_WARN = """name: monitoring
install_type: docker
severity: warn
"""

SAMPLE_MODULE_YAML_NO_SEVERITY = """name: redis
install_type: docker
"""

SAMPLE_NODE_YAML_DICT = """modules:
  postgres:
    enabled: true
    config_overlay: overlay-v2
  redis:
    enabled: false
  monitoring: true
"""

SAMPLE_NODE_YAML_LIST = """modules:
  - name: postgres
    enabled: true
    config_overlay: overlay-v2
  - name: redis
    enabled: false
"""

SAMPLE_NODE_YAML_EMPTY = """modules: {}
"""

SAMPLE_MODULE_YAML_DEPS_A = """name: module-a
install_type: docker
depends_on:
  - module-b
  - module-c
"""

SAMPLE_MODULE_YAML_DEPS_B = """name: module-b
install_type: docker
depends_on:
  - module-d
"""

SAMPLE_MODULE_YAML_DEPS_C = """name: module-c
install_type: system
depends_on: []
"""

SAMPLE_MODULE_YAML_DEPS_D = """name: module-d
install_type: docker
"""

SAMPLE_MODULE_YAML_CYCLE_A = """name: cycle-a
install_type: docker
depends_on:
  - cycle-b
"""

SAMPLE_MODULE_YAML_CYCLE_B = """name: cycle-b
install_type: docker
depends_on:
  - cycle-c
"""

SAMPLE_MODULE_YAML_CYCLE_C = """name: cycle-c
install_type: docker
depends_on:
  - cycle-a
"""


# region FUNC_secrets_manifest_file
@pytest.fixture
def secrets_manifest_file(tmp_path):
    """Create a temporary secrets-manifest.yaml."""
    manifest = tmp_path / "secrets-manifest.yaml"
    manifest.write_text(SAMPLE_SECRETS_MANIFEST)
    return manifest


# endregion FUNC_secrets_manifest_file


# region FUNC_module_yaml_file
@pytest.fixture
def module_yaml_file(tmp_path):
    """Create a temporary module.yaml."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML)
    return yaml_file


# endregion FUNC_module_yaml_file


# region FUNC_node_yaml_list_file
# (node_yaml fixtures консолидированы в параметризацию test_parse_modules, 168 F5)
# endregion FUNC_node_yaml_list_file


# region FUNC_modules_dir_with_deps
@pytest.fixture
def modules_dir_with_deps(tmp_path):
    """Create a modules directory with module-a, module-b, module-c, module-d with depends_on DAG.

    DAG structure:
      module-a → [module-b, module-c]
      module-b → [module-d]
      module-c → []
      module-d → []
    """
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    def _write_module(name: str, content: str):
        mod_dir = modules_dir / name
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(content)

    _write_module("module-a", SAMPLE_MODULE_YAML_DEPS_A)
    _write_module("module-b", SAMPLE_MODULE_YAML_DEPS_B)
    _write_module("module-c", SAMPLE_MODULE_YAML_DEPS_C)
    _write_module("module-d", SAMPLE_MODULE_YAML_DEPS_D)

    return modules_dir


# endregion FUNC_modules_dir_with_deps


# region FUNC_modules_dir_with_cycle
@pytest.fixture
def modules_dir_with_cycle(tmp_path):
    """Create a modules directory with a circular dependency DAG (cycle-a → cycle-b → cycle-c → cycle-a)."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    def _write_module(name: str, content: str):
        mod_dir = modules_dir / name
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(content)

    _write_module("cycle-a", SAMPLE_MODULE_YAML_CYCLE_A)
    _write_module("cycle-b", SAMPLE_MODULE_YAML_CYCLE_B)
    _write_module("cycle-c", SAMPLE_MODULE_YAML_CYCLE_C)

    return modules_dir


# endregion FUNC_modules_dir_with_cycle


# ═══════════════════════════════════════════════════════════════════════════════
# Test: check_env_requires
# ═══════════════════════════════════════════════════════════════════════════════
# 📝 2026-08-15 · DevPlan 171 W2.1 — 14 мёртвых тестов (testcheck_*/testvalidate_*/testget_*)
# воскрешены переименованием в test_-префикс; структурная защита — гейт test_gate_test_naming.


# region FUNC_test_check_env_requires_all_present
## @purpose  All required env vars for postgres are set via os.environ — returns empty missing list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires with all env vars set → empty missing list
# · Last fail: N/A · Remove if: check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_all_present(secrets_manifest_file, caplog, monkeypatch):
    """All required env vars are set — should return empty missing list."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret123")
    monkeypatch.setenv("POSTGRES_USER", "postgres")

    missing = check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert missing == [], f"Expected no missing vars, got: {missing}"


# endregion FUNC_test_check_env_requires_all_present


# region FUNC_test_check_env_requires_missing
## @purpose  Required env var is not set — returns list with the missing var name
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires with missing env var → returns missing list
# · Last fail: 2026-08-27 · dev-машина: /var/lib/platform/run/secrets.env (default path deploy_paths,
# ·   142 W2) существовал и содержал POSTGRES_PASSWORD; SECRETS_ENV_FILE не был изолирован →
# ·   check_runtime_env читал реальный файл и считал var present → missing=[]
# · Remove if: check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_missing(secrets_manifest_file, caplog, monkeypatch, tmp_path):
    """POSTGRES_PASSWORD not set (env и secrets.env) — should return missing list with it.

    Hermetic isolation: SECRETS_ENV_FILE указывает на пустой tmp-файл. Без этого
    check_runtime_env читает default /var/lib/platform/run/secrets.env — на dev-машине
    файл существует (остаток локального secrets-флоу) и содержит POSTGRES_PASSWORD,
    из-за чего тест падал с missing=[] (2026-08-27). Тест должен зависеть только от
    os.environ + управляемого tmp-файла, не от состояния машины.
    """
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    empty_secrets_env = tmp_path / "secrets-empty.env"
    empty_secrets_env.write_text("")  # существует, но не содержит POSTGRES_PASSWORD
    monkeypatch.setenv("SECRETS_ENV_FILE", str(empty_secrets_env))
    missing = check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert "POSTGRES_PASSWORD" in missing, "Expected POSTGRES_PASSWORD to be missing"


# endregion FUNC_test_check_env_requires_missing


# region FUNC_test_check_env_requires_secrets_env_file
## @purpose  Secret is set via SECRETS_ENV_FILE (not os.environ) — should pass
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires reads from secrets.env file
# · Last fail: N/A · Remove if: check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_secrets_env_file(secrets_manifest_file, caplog, monkeypatch, tmp_path):
    """Secret set in secrets.env file (not os.environ) — should pass."""
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("POSTGRES_PASSWORD=from_file\nPOSTGRES_USER=pgu\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))

    missing = check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert missing == [], f"Expected no missing vars (from file), got: {missing}"


# endregion FUNC_test_check_env_requires_secrets_env_file


# region FUNC_test_check_env_requires_generated_tier
## @purpose  Module with generated-tier secret checked — should require the var like required
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires checks generated-tier secrets too
# · Last fail: N/A · Remove if: check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_generated_tier(secrets_manifest_file, caplog, monkeypatch):
    """Generated-tier secret should be checked like required."""
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pg-test-password")

    missing = check_env_requires("litellm", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for litellm: %s", missing)
    assert missing == [], f"Expected no missing vars for litellm, got: {missing}"


# endregion FUNC_test_check_env_requires_generated_tier


# region FUNC_test_check_env_requires_no_manifest_found
## @purpose  Manifest file does not exist — STRICT mode raises FileNotFoundError
##           (graceful degradation removed, DevPlan 116 T4 / U-33 / invariant 7)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires with missing manifest → raises FileNotFoundError
# · Last fail: 2026-07-31 · Remove if: strict manifest reader is superseded
@ldd_trajectory
def test_check_env_requires_no_manifest_found(tmp_path, caplog):
    """Manifest not found — strict reader raises FileNotFoundError (fail-visible)."""
    with pytest.raises(FileNotFoundError):
        check_env_requires("postgres", str(tmp_path / "nonexistent.yaml"))

    logger.info("[IMP:9][test][check_env] Missing manifest raises FileNotFoundError — OK")


# endregion FUNC_test_check_env_requires_no_manifest_found


# region FUNC_test_check_env_requires_no_consumers_match
## @purpose  Module with no matching consumers in manifest — returns empty list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: check_env_requires with unmatched consumer → empty
# · Last fail: N/A · Remove if: check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_no_consumers_match(secrets_manifest_file, caplog):
    """Module not in any secret's consumers list — should return empty."""
    missing = check_env_requires("nonexistent-module", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for nonexistent: %s", missing)
    assert missing == [], "Expected empty missing list for unmatched consumer"


# endregion FUNC_test_check_env_requires_no_consumers_match


# ═══════════════════════════════════════════════════════════════════════════════
# Test: validate_secret_charsets
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_validate_secret_charsets_valid
## @purpose  All secrets with charset match their regex — returns (0, [])
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: validate_secret_charsets with valid charset values → zero failures
# · Last fail: N/A · Remove if: validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_valid(secrets_manifest_file, caplog, monkeypatch):
    """All charset values match — should return 0 failures."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "ValidPass.123")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "Secure.Pass-123")

    failed, errors = validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] Failed=%d, errors=%s", failed, errors)
    assert failed == 0, f"Expected 0 failures, got {failed}: {errors}"


# endregion FUNC_test_validate_secret_charsets_valid


# region FUNC_test_validate_secret_charsets_invalid
## @purpose  Secret value does not match charset regex — returns failure
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: validate_secret_charsets with invalid charset value → failure
# · Last fail: N/A · Remove if: validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_invalid(secrets_manifest_file, caplog, monkeypatch):
    """Secret value with invalid characters — should return 1 failure."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "invalid!!password@@")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "Secure.Pass-123")

    failed, errors = validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] Failed=%d, errors=%s", failed, errors)
    assert failed == 1, f"Expected 1 failure, got {failed}"
    assert any("POSTGRES_PASSWORD" in e for e in errors), "Expected POSTGRES_PASSWORD in errors"


# endregion FUNC_test_validate_secret_charsets_invalid


# region FUNC_test_validate_secret_charsets_no_charset_field
## @purpose  Secrets without charset field are skipped — no failures
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: validate_secret_charsets with no charset fields → zero failures
# · Last fail: N/A · Remove if: validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_no_charset_field(secrets_manifest_file, caplog, monkeypatch):
    """POSTGRES_USER has no charset — skip. MINIO_ROOT_USER matches — pass."""
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")

    failed, errors = validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] No-charset-field: Failed=%d, errors=%s", failed, errors)
    assert failed == 0, f"Expected 0 failures, got {failed}"


# endregion FUNC_test_validate_secret_charsets_no_charset_field


# region FUNC_test_validate_secret_charsets_manifest_missing
## @purpose  Manifest file not found — STRICT mode raises FileNotFoundError
##           (graceful degradation removed, DevPlan 116 T4 / U-33 / invariant 7)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: validate_secret_charsets with missing manifest → raises
# · Last fail: 2026-07-31 · Remove if: strict manifest reader is superseded
@ldd_trajectory
def test_validate_secret_charsets_manifest_missing(tmp_path, caplog):
    """Manifest not found — strict reader raises FileNotFoundError (fail-visible)."""
    with pytest.raises(FileNotFoundError):
        validate_secret_charsets(str(tmp_path / "nonexistent.yaml"))

    logger.info("[IMP:9][test][charset] Missing manifest raises FileNotFoundError — OK")


# endregion FUNC_test_validate_secret_charsets_manifest_missing


# ═══════════════════════════════════════════════════════════════════════════════
# Test: get_module_severity
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_get_module_severity_critical
## @purpose  module.yaml with severity: critical returns "critical"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: get_module_severity returns critical from yaml
# · Last fail: N/A · Remove if: get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_critical(module_yaml_file, caplog):
    """severity: critical → returns 'critical'."""
    sev = get_module_severity(str(module_yaml_file))
    logger.info("[IMP:9][test][severity] Value=%s", sev)
    assert sev == "critical", f"Expected 'critical', got {sev!r}"


# endregion FUNC_test_get_module_severity_critical


# region FUNC_test_get_module_severity_warn
## @purpose  module.yaml with severity: warn returns "warn"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: get_module_severity returns warn from yaml
# · Last fail: N/A · Remove if: get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_warn(tmp_path, caplog):
    """severity: warn in module.yaml → returns 'warn'."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML_WARN)

    sev = get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] Value=%s", sev)
    assert sev == "warn", f"Expected 'warn', got {sev!r}"


# endregion FUNC_test_get_module_severity_warn


# region FUNC_test_get_module_severity_normal
## @purpose  module.yaml with severity: normal (D5-канон, module.schema.json enum ["critical","normal"])
##           returns "normal" БЕЗ IMP:5-шума. Схема-дрейф 2026-08-27: runtime-словарь
##           {critical|warn} не знал D5-значения → 13/16 module.yaml default'ились в warn.
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: get_module_severity accepts D5-canonical 'normal'
# · Last fail: 2026-08-27 · НАБЛЮДЕНИЕ: live bootstrap — Invalid severity 'normal' ×3
# ·   (status-page, backup-cron, hermes-agent) → defaulting to warn (скрытое намерение автора)
# · Remove if: get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_normal(tmp_path, caplog):
    """severity: normal (D5-канон) → returns 'normal' без IMP:5-предупреждения."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text("name: status-page\ninstall_type: docker\nseverity: normal\n")

    sev = get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] D5-normal value=%s", sev)
    assert sev == "normal", f"Expected 'normal', got {sev!r}"
    assert not any("[IMP:5]" in r.message for r in caplog.records), (
        "D5-каноническое значение 'normal' не должно давать IMP:5-шум"
    )


# endregion FUNC_test_get_module_severity_normal


# region FUNC_test_get_module_severity_invalid
## @purpose  module.yaml с неизвестным severity (вне словаря critical|normal|warn) → default "warn"
##           + IMP:5 warning. R5 negative: invalid-ветка — детектор схема-дрейфа — обязана
##           ловить мусорные значения (падает, если валидацию уберут).
## @complexity 1
# 🧪 TRAP[TEST] · NEGATIVE (R5) · get_module_severity invalid-ветка — схема-дрейф
# · Last fail: 2026-08-27 · исходный вход: severity: normal считался invalid (легаси-словарь
# ·   {critical|warn} не знал D5-значения); negative фиксирует, что НЕ-словарное значение
# ·   по-прежнему падает в warn + warning (детектор жив)
# · Remove if: get_module_severity перестанет валидировать severity
@ldd_trajectory
def test_get_module_severity_invalid(tmp_path, caplog):
    """severity: bogus (вне словаря) → default 'warn' + IMP:5 Invalid severity warning."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text("name: ghost\ninstall_type: docker\nseverity: bogus\n")

    sev = get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] Invalid-value default=%s", sev)
    assert sev == "warn", f"Expected default 'warn' for unknown severity, got {sev!r}"
    invalid_msgs = [r.message for r in caplog.records if "Invalid severity" in r.message]
    assert invalid_msgs, "R5 FAIL: invalid-severity detector missed unknown value 'bogus'"
    assert "bogus" in invalid_msgs[0], f"Warning должен называть значение, got: {invalid_msgs[0]}"


# endregion FUNC_test_get_module_severity_invalid


# region FUNC_test_get_module_severity_default
## @purpose  module.yaml without severity field defaults to "warn"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: get_module_severity defaults to warn when absent
# · Last fail: N/A · Remove if: get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_default(tmp_path, caplog):
    """No severity field → defaults to 'warn'."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML_NO_SEVERITY)

    sev = get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] Default value=%s", sev)
    assert sev == "warn", f"Expected default 'warn', got {sev!r}"


# endregion FUNC_test_get_module_severity_default


# region FUNC_test_get_module_severity_file_not_found
## @purpose  module.yaml not found — returns "warn" (graceful degradation)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: get_module_severity with missing file → default warn
# · Last fail: N/A · Remove if: get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_file_not_found(tmp_path, caplog):
    """File not found → returns default 'warn'."""
    sev = get_module_severity(str(tmp_path / "nonexistent.yaml"))
    logger.info("[IMP:9][test][severity] Not-found default=%s", sev)
    assert sev == "warn"


# endregion FUNC_test_get_module_severity_file_not_found


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _batch_module_metadata
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_batch_module_metadata_multiple
## @purpose  Multiple module.yaml files with different severities and install_types
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata collects N modules correctly
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_multiple(tmp_path, caplog):
    """Multiple modules with different metadata — collected correctly."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # module-a: postgres (docker/critical)
    (modules_dir / "mod-a").mkdir()
    (modules_dir / "mod-a" / "module.yaml").write_text("name: postgres\ninstall_type: docker\nseverity: critical\n")
    # module-b: monitoring (docker/warn — explicit)
    (modules_dir / "mod-b").mkdir()
    (modules_dir / "mod-b" / "module.yaml").write_text("name: monitoring\ninstall_type: docker\nseverity: warn\n")
    # module-c: redis (docker/no severity — default warn)
    (modules_dir / "mod-c").mkdir()
    (modules_dir / "mod-c" / "module.yaml").write_text("name: redis\ninstall_type: docker\n")

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Collected %d modules", len(metadata))
    assert len(metadata) == 3, f"Expected 3 modules, got {len(metadata)}"

    mod_map = {m["name"]: m for m in metadata}
    assert mod_map["postgres"]["install_type"] == "docker"
    assert mod_map["postgres"]["severity"] == "critical"
    assert mod_map["monitoring"]["severity"] == "warn"
    assert mod_map["redis"]["severity"] == "warn"
    assert mod_map["redis"]["install_type"] == "docker"


# endregion FUNC_test_batch_module_metadata_multiple


# region FUNC_test_batch_module_metadata_empty_dir
## @purpose  No module.yaml files found — returns empty list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata with empty modules dir → []
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_empty_dir(tmp_path, caplog):
    """No module.yaml files → returns []."""
    modules_dir = tmp_path / "empty_modules"
    modules_dir.mkdir()

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Empty dir returned %d items", len(metadata))
    assert metadata == [], "Expected empty list for empty modules dir"


# endregion FUNC_test_batch_module_metadata_empty_dir


# region FUNC_test_batch_module_metadata_name_from_dir
## @purpose  module.yaml without name field uses parent directory name
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata falls back to dir name when name absent
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_name_from_dir(tmp_path, caplog):
    """Missing name in module.yaml → uses directory name."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "my-custom-module").mkdir()
    (modules_dir / "my-custom-module" / "module.yaml").write_text("install_type: system\n")

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Name-from-dir: %s", metadata)
    assert len(metadata) == 1
    assert metadata[0]["name"] == "my-custom-module"
    assert metadata[0]["install_type"] == "system"


# endregion FUNC_test_batch_module_metadata_name_from_dir


# region FUNC_test_orchestrator_uses_secrets_validator_batch_env
## @purpose  MERGED (W2 T2.2): S3 wiring — deploy_orchestrator.py использует batch env-check
##           secrets_validator (перенесено из tests/test_deploy_modules_env.py::test_batch_module_metadata,
##           статический текст-скан ORCHESTRATOR_PY).
## @complexity 1
# 🧪 TRAP[TEST] · MERGED (W2 T2.2) · S3 — orchestrator batch env-check wiring
# · Regression: routing вернётся на per-module фолбэки (DevPlan 100: deploy-modules.sh → orchestrator)
# · Last fail: N/A (перенесено из test_deploy_modules_env.py — статический audit)
# · Remove if: batch env-check подход заменён другой оптимизацией
@ldd_trajectory
def test_orchestrator_uses_secrets_validator_batch_env(caplog):
    """S3: deploy_orchestrator.py импортирует secrets_validator и использует batch env-check."""
    orchestrator_py = (
        Path(__file__).resolve().parent.parent.parent
        / "core"
        / "internal"
        / "bootstrap"
        / "deploy"
        / "deploy_orchestrator.py"
    )
    assert orchestrator_py.is_file(), f"deploy_orchestrator.py not found at {orchestrator_py}"

    orch_content = orchestrator_py.read_text()
    assert "secrets_validator" in orch_content, "S3 violation: secrets_validator not imported in deploy_orchestrator.py"
    assert "batch_check_env" in orch_content or "check_env_requires" in orch_content, (
        "S3 violation: batch env check functions not used in deploy_orchestrator.py"
    )

    logger.info("[IMP:9][test][orchestrator] secrets_validator imported + batch env check used in orchestrator — OK")


# endregion FUNC_test_orchestrator_uses_secrets_validator_batch_env


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _expand_transitive_deps
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_expand_transitive_deps
## @purpose  _expand_transitive_deps: BFS-closure/multi-seed/no-deps/cycle/empty/unknown-seed
##           (168 F5 параметризация — 6 кейсов 1:1)
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps resolves 3-step transitive deps
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with multiple seeds → union
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with no-deps module → singleton
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with cycle → converges (no infinite loop)
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with empty filter → ""
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with unknown module → ConfigValidationError
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@pytest.mark.parametrize(
    "seed,expected,modules_fixture,raises",
    [
        # basic: module-a → module-b → module-d (3-шаговое замыкание, sorted)
        ("module-a", "module-a module-b module-c module-d", "modules_dir_with_deps", None),
        # multiple seeds: объединение замыканий (module-a НЕ входит)
        ("module-b,module-c", "module-b module-c module-d", "modules_dir_with_deps", None),
        # no deps: синглтон
        ("module-d", "module-d", "modules_dir_with_deps", None),
        # empty filter: пустая строка
        ("", "", "modules_dir_with_deps", None),
        # cycle: BFS сходится (visited-set, без infinite loop)
        ("cycle-a", "cycle-a cycle-b cycle-c", "modules_dir_with_cycle", None),
        # unknown seed: T3.6 — sys.exit → raise ConfigValidationError
        ("ghost", None, "modules_dir_with_deps", ConfigValidationError),
    ],
)
@ldd_trajectory
def test_expand_transitive_deps(seed, expected, modules_fixture, raises, request, caplog):
    """_expand_transitive_deps: BFS-замыкание/no-deps/multi-seed/cycle/empty/unknown (1:1).

    Ровно ОДНА modules-фикстура на кейс (обе создают tmp_path/modules — одновременный
    запрос давал FileExistsError; request.getfixturevalue резолвит нужную).
    """
    modules_dir = request.getfixturevalue(modules_fixture)

    if raises is not None:
        with pytest.raises(raises) as exc_info:
            _expand_transitive_deps(seed, str(modules_dir))
        logger.info("[IMP:9][test][deps] Unknown seed error: %s", exc_info.value)
        assert "ghost" in str(exc_info.value), "Expected error message with module name"
        return

    result = _expand_transitive_deps(seed, str(modules_dir))
    logger.info("[IMP:9][test][deps] seed=%r expanded=%r (expected=%r)", seed, result, expected)
    assert result == expected


# endregion FUNC_test_expand_transitive_deps


# ═══════════════════════════════════════════════════════════════════════════════
# Test: parse_modules_from_node_yaml
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_parse_modules
## @purpose  parse_modules_from_node_yaml: dict/list/empty/missing (168 F5 параметризация)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with dict format → 3 tuples
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with list format → tuples
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with empty modules → []
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with missing file → []
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
@pytest.mark.parametrize(
    "yaml_text,expected_tuples",
    [
        (
            SAMPLE_NODE_YAML_DICT,
            {("postgres", "true", "overlay-v2"), ("redis", "false", ""), ("monitoring", "true", "")},
        ),
        (SAMPLE_NODE_YAML_LIST, {("postgres", "true", "overlay-v2"), ("redis", "false", "")}),
        (SAMPLE_NODE_YAML_EMPTY, set()),
        (None, set()),  # missing file → graceful degradation
    ],
)
@ldd_trajectory
def test_parse_modules(yaml_text, expected_tuples, tmp_path, caplog):
    """parse_modules_from_node_yaml: dict/list/empty/missing (4 формата 1:1)."""
    if yaml_text is None:
        yaml_path = tmp_path / "nonexistent.yaml"
    else:
        yaml_path = tmp_path / "node.yaml"
        yaml_path.write_text(yaml_text)

    modules = parse_modules_from_node_yaml(str(yaml_path))
    logger.info("[IMP:9][test][node-yaml] Parsed %d modules", len(modules))
    assert {tuple(m) for m in modules} == expected_tuples


# endregion FUNC_test_parse_modules


# ═══════════════════════════════════════════════════════════════════════════════
# Test: detect_install_type
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_detect_install_type
## @purpose  detect_install_type: docker/system/default/missing-file (168 F5 параметризация)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type returns docker
# · Last fail: N/A · Remove if: detect_install_type behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type returns system
# · Last fail: N/A · Remove if: detect_install_type behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type defaults to unknown
# · Last fail: N/A · Remove if: detect_install_type behavior changed
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type with missing file → unknown
# · Last fail: N/A · Remove if: detect_install_type behavior changed
@pytest.mark.parametrize(
    "yaml_text,expected",
    [
        (SAMPLE_MODULE_YAML, "docker"),
        ("name: nginx\ninstall_type: system\n", "system"),
        ("name: custom\n", "unknown"),
        (None, "unknown"),  # missing file → graceful degradation
    ],
)
@ldd_trajectory
def test_detect_install_type(yaml_text, expected, tmp_path, caplog):
    """detect_install_type: docker/system/default/missing-file (4 варианта 1:1)."""
    if yaml_text is None:
        yaml_path = tmp_path / "nonexistent.yaml"
    else:
        yaml_path = tmp_path / "module.yaml"
        yaml_path.write_text(yaml_text)

    itype = detect_install_type(str(yaml_path))
    logger.info("[IMP:9][test][detect] install_type=%s (expected=%s)", itype, expected)
    assert itype == expected


# endregion FUNC_test_detect_install_type
