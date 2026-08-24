# 🧪 TRAP[TEST] · REF-0013 · postcondition: parsed ⊇ {required ∧ source=sops} (DATA-1006)
# GREP_SUMMARY: test-secrets-postcondition, required-sops, data-1006, verifier, ensure-secrets-exist, manifest-drift, fail-fast
# STRUCTURE: ▶ verify_required_sops_secrets → ◇ enc отсутствует → ⎋ skip (autogen-only) → ◇ required∧sops ⊆ parsed∪environ ⎋ OK | ✗ ⚡ConfigValidationError (имена в сообщении) → ▶ integration ensure_secrets_exist → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for the REF-0013/DATA-1006 postcondition-verifier in
##           core/internal/bootstrap/lifecycle/helpers/secrets.py: после decrypt+autogen каждый
##           манифестный секрет tier=required ∧ source=sops обязан присутствовать с непустым
##           значением в secrets.env ИЛИ os.environ. Плюс интеграционный прогон через
##           helpers.ensure_secrets_exist (fatal при отсутствии, pass при наличии).
## @scope    Pure unit tests — файлы в tmp_path, htpasswd/sops замоканы на интеграционном уровне.
## @invariants
##   - Verifier gated на существование enc-файла: нет enc → no-op (autogen-only нода,
##     TRAP[BUG] 2026-07-31 — чистая нода остаётся валидной)
##   - Missing required∧sops → ConfigValidationError со списком имён в сообщении
##   - os.environ fallback засчитывается (autogen мог положить значение только в environ)
## @rationale REF-0013: φ4 глотала ошибки source/autogen как WARN → фаза done → skip навсегда;
##            отсутствие секретов вскрывалось отложенным взрывом на первом использовании.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.bootstrap.lifecycle.helpers import secrets as helpers_secrets
from core.internal.shared.exceptions import ConfigValidationError
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _write_manifest_with_required(tmp_path: Path, names: list[str]) -> Path:
    """Write a secrets-manifest.yaml with tier=required/source=sops entries.

    Пишет в <tmp_path>/core/secrets-manifest.yaml; для интеграционных тестов,
    где core_dir уже равен <tmp_path>/core, используйте _write_manifest_at().
    """
    return _write_manifest_at(tmp_path / "core", names)


def _write_manifest_at(core_dir: Path, names: list[str]) -> Path:
    """Write secrets-manifest.yaml directly into the given core_dir."""
    lines = ["secrets:"]
    for name in names:
        lines.append(f"  - name: {name}")
        lines.append("    tier: required")
        lines.append("    source: sops")
    manifest = core_dir / "secrets-manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


@pytest.fixture(autouse=True)
def _clean_target_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Удаление целевых переменных из os.environ — детерминированный verifier."""
    for var in ("POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD", "GHCR_PULL_TOKEN"):
        monkeypatch.delenv(var, raising=False)


# ═══════════════════════════════════════════════════════════════════
# Tests: verify_required_sops_secrets (unit)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_verifier_pass_when_all_present_in_file
## @purpose  Все required∧sops присутствуют в secrets.env с непустыми значениями → OK + IMP:9 лог.
@ldd_trajectory
def test_verifier_pass_when_all_present_in_file(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """All required∧sops present in secrets.env → verifier passes."""
    manifest = _write_manifest_with_required(tmp_path, ["POSTGRES_PASSWORD"])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("POSTGRES_PASSWORD='pg-pass'\n", encoding="utf-8")
    enc_file = tmp_path / "node.enc.yaml"
    enc_file.write_text("dummy: encrypted\n", encoding="utf-8")

    helpers_secrets.verify_required_sops_secrets(
        manifest_path=str(manifest), secrets_env=str(env_file), enc_file=str(enc_file)
    )

    assert any("[IMP:9]" in r.message and "Postcondition OK" in r.message for r in caplog.records), (
        "Missing IMP:9 postcondition-OK log"
    )
    logger.info("[IMP:9][test_verifier_pass_when_all_present_in_file] PASS")


# endregion FUNC_test_verifier_pass_when_all_present_in_file


# region FUNC_test_verifier_counts_os_environ_fallback
## @purpose  Значение только в os.environ (autogen положил мимо файла) тоже закрывает постусловие.
@ldd_trajectory
def test_verifier_counts_os_environ_fallback(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Value present only in os.environ satisfies the postcondition."""
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "ch-pass-from-autogen")
    manifest = _write_manifest_with_required(tmp_path, ["CLICKHOUSE_PASSWORD"])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("", encoding="utf-8")  # пустой файл
    enc_file = tmp_path / "node.enc.yaml"
    enc_file.write_text("dummy: encrypted\n", encoding="utf-8")

    helpers_secrets.verify_required_sops_secrets(
        manifest_path=str(manifest), secrets_env=str(env_file), enc_file=str(enc_file)
    )
    # R1: отсутствие исключения — не механизм; fallback подтверждается IMP:9-логом постусловия
    assert any("[IMP:9]" in r.message and "Postcondition OK" in r.message for r in caplog.records), (
        "Missing IMP:9 postcondition-OK log (os.environ fallback must satisfy postcondition)"
    )
    logger.info("[IMP:9][test_verifier_counts_os_environ_fallback] PASS: os.environ fallback counted")


# endregion FUNC_test_verifier_counts_os_environ_fallback


# region FUNC_test_verifier_raises_listing_missing_names
## @purpose  Отсутствующие required∧sops → ConfigValidationError; сообщение содержит имена.
@ldd_trajectory
def test_verifier_raises_listing_missing_names(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Missing required∧sops vars → ConfigValidationError naming each missing secret."""
    manifest = _write_manifest_with_required(tmp_path, ["POSTGRES_PASSWORD", "GHCR_PULL_TOKEN"])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("POSTGRES_PASSWORD='ok-value'\n", encoding="utf-8")  # GHCR_PULL_TOKEN отсутствует
    enc_file = tmp_path / "node.enc.yaml"
    enc_file.write_text("dummy: encrypted\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="GHCR_PULL_TOKEN"):
        helpers_secrets.verify_required_sops_secrets(
            manifest_path=str(manifest), secrets_env=str(env_file), enc_file=str(enc_file)
        )

    imp10 = any("[IMP:10]" in r.message and "POSTCONDITION FAILED" in r.message for r in caplog.records)
    assert imp10, "Missing IMP:10 POSTCONDITION FAILED log"
    logger.info("[IMP:9][test_verifier_raises_listing_missing_names] ✅ Fatal lists missing names")


# endregion FUNC_test_verifier_raises_listing_missing_names


# region FUNC_test_verifier_skips_without_enc_file
## @purpose  Gating: enc-файл отсутствует → autogen-only нода → verifier no-op даже без значений
##           (TRAP[BUG] 2026-07-31: чистая нода без операторских секретов валидна).
@ldd_trajectory
def test_verifier_skips_without_enc_file(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """No enc file → postcondition skipped regardless of values."""
    manifest = _write_manifest_with_required(tmp_path, ["POSTGRES_PASSWORD"])
    env_file = tmp_path / "secrets.env"

    helpers_secrets.verify_required_sops_secrets(
        manifest_path=str(manifest),
        secrets_env=str(env_file),
        enc_file=str(tmp_path / "nonexistent.enc.yaml"),
    )

    assert any("[IMP:8]" in r.message and "skipped" in r.message for r in caplog.records), (
        "Missing IMP:8 skip log for autogen-only node"
    )
    logger.info("[IMP:9][test_verifier_skips_without_enc_file] PASS: autogen-only node not blocked")


# endregion FUNC_test_verifier_skips_without_enc_file


# region FUNC_test_verifier_trivially_ok_without_required_entries
## @purpose  Манифест без required∧sops записей → тривиальный pass.
@ldd_trajectory
def test_verifier_trivially_ok_without_required_entries(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Manifest without required∧sops entries → trivially satisfied."""
    manifest = tmp_path / "core" / "secrets-manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "secrets:\n  - name: LITELLM_MASTER_KEY\n    tier: generated\n    source: autogen\n",
        encoding="utf-8",
    )
    enc_file = tmp_path / "node.enc.yaml"
    enc_file.write_text("dummy: encrypted\n", encoding="utf-8")

    helpers_secrets.verify_required_sops_secrets(
        manifest_path=str(manifest), secrets_env=str(tmp_path / "absent.env"), enc_file=str(enc_file)
    )
    # R1: отсутствие исключения — не механизм; тривиальный pass подтверждается IMP:8-логом
    assert any("[IMP:8]" in r.message and "trivially satisfied" in r.message for r in caplog.records), (
        "Missing IMP:8 trivially-satisfied log"
    )
    logger.info("[IMP:9][test_verifier_trivially_ok_without_required_entries] PASS")


# endregion FUNC_test_verifier_trivially_ok_without_required_entries


# ═══════════════════════════════════════════════════════════════════
# Tests: integration через helpers.ensure_secrets_exist
# ═══════════════════════════════════════════════════════════════════


def _ensure_flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """DI-env для ensure_secrets_exist + предустановка autogen-переменных (no-op)."""
    configs_dir = tmp_path / "opt" / "node-configs"
    secrets_dir = configs_dir / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "testnode.enc.yaml").write_text("dummy: encrypted\n", encoding="utf-8")

    for var in (
        "PLATFORM_MASTER_EMAIL",
        "PLATFORM_MASTER_PASSWORD",
        "HERMES_DASHBOARD_PASSWORD",
        "GF_SECURITY_ADMIN_PASSWORD",
        "LANGFUSE_INIT_USER_PASSWORD",
    ):
        monkeypatch.setenv(var, "pre-set-noop")

    return {
        "SECRETS_ENV_FILE": str(tmp_path / "run" / "secrets.env"),
        "NODE_NAME": "testnode",
        "NODE_CONFIGS_DIR": str(configs_dir),
    }


# region FUNC_test_ensure_secrets_exist_postcondition_fatal_on_missing
## @purpose  Интеграция: enc существует, secrets.env БЕЗ required∧sops → ensure_secrets_exist
##           прокидывает ConfigValidationError (φ4-обёртка превратит его в PlatformFatalError).
@ldd_trajectory
def test_ensure_secrets_exist_postcondition_fatal_on_missing(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_secrets_exist fails loudly when required∧sops secret is missing after autogen."""
    di_env = _ensure_flow_env(monkeypatch, tmp_path)
    core_dir = tmp_path / "core"
    _write_manifest_at(core_dir, ["POSTGRES_PASSWORD"])
    # secrets.env пишется БЕЗ POSTGRES_PASSWORD
    Path(di_env["SECRETS_ENV_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(di_env["SECRETS_ENV_FILE"]).write_text("UNRELATED_VAR=x\n", encoding="utf-8")

    with (
        patch("core.internal.bootstrap.lifecycle.secrets_manager._ensure_htpasswd", return_value=True),
        pytest.raises(ConfigValidationError, match="POSTGRES_PASSWORD"),
    ):
        helpers_secrets.ensure_secrets_exist(str(core_dir), env=di_env)

    logger.info("[IMP:9][test_ensure_secrets_exist_postcondition_fatal_on_missing] ✅ φ4-level fatal confirmed")


# endregion FUNC_test_ensure_secrets_exist_postcondition_fatal_on_missing


# region FUNC_test_ensure_secrets_exist_postcondition_pass
## @purpose  Интеграция green-path: required∧sops присутствует → ensure проходит без исключений.
@ldd_trajectory
def test_ensure_secrets_exist_postcondition_pass(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_secrets_exist completes when all required∧sops are present."""
    di_env = _ensure_flow_env(monkeypatch, tmp_path)
    core_dir = tmp_path / "core"
    _write_manifest_at(core_dir, ["POSTGRES_PASSWORD"])
    Path(di_env["SECRETS_ENV_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(di_env["SECRETS_ENV_FILE"]).write_text("POSTGRES_PASSWORD='pg-pass'\n", encoding="utf-8")

    with patch("core.internal.bootstrap.lifecycle.secrets_manager._ensure_htpasswd", return_value=True):
        helpers_secrets.ensure_secrets_exist(str(core_dir), env=di_env)

    assert any("[IMP:9]" in r.message and "Postcondition OK" in r.message for r in caplog.records), (
        "Missing postcondition-OK log on green path"
    )
    logger.info("[IMP:9][test_ensure_secrets_exist_postcondition_pass] PASS")


# endregion FUNC_test_ensure_secrets_exist_postcondition_pass
