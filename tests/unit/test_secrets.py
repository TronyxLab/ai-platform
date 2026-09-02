"""
# GREP_SUMMARY: test-secrets, allow-autogen, devplan-029, fail-loud, autogen-only, required-sops, phase-fatal, platform-fatal
# STRUCTURE: ▶ verify_required_sops_secrets → ◇ allow_autogen=True + no enc → ⎋ skip → ◇ allow_autogen=False + no enc + required∧sops → ⚡ConfigValidationError
#           → ▶ ensure_secrets_exist (node.yaml резолв флага) → ◇ флаг отсутствует → ⚡ConfigValidationError → ◇ флаг true → ⎋ pass
#           → ▶ phase_secrets_provision (φ4) → ◇ ensure ConfigValidationError → ⚡PlatformFatalError (exit 10)
# region MODULE_CONTRACT
## @purpose  Unit tests for the DevPlan 029 T1 allow_autogen gate — fail-loud vs autogen:
##           чистая нода без enc-файла с объявленными required∧sops секретами НЕ должна
##           молча деградировать до autogen (RC2-класс silent-success), если node.yaml не
##           разрешает autogen (node.yaml#secrets.allow_autogen, D2). Gate живёт в
##           helpers/secrets.py (verify/ensure + _resolve_allow_autogen), φ4-конверсия в
##           phases/secrets.py (PlatformFatalError exit 10).
## @scope    Pure unit tests — tmp_path файлы, 0 subprocess, 0 Docker. YAML node.yaml —
##           через NodeYaml (резолв из NODE_CONFIGS_DIR env, тот же канон enabled_modules).
## @invariants
##   - allow_autogen=None (node.yaml недоступен / не передан) → легаси-skip (обратная совместимость)
##   - allow_autogen=False + no enc + required∧sops → ConfigValidationError; имена в сообщении
##   - allow_autogen=True + no enc → skip (IMP:8 лог), без raise
##   - φ4: ConfigValidationError из ensure → PlatformFatalError (существующая FATAL-обёртка)
## @changes 2026-09-02 · DevPlan 029 T1 — created
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import secrets as helpers_secrets
from core.internal.bootstrap.lifecycle.phases.secrets import phase_secrets_provision
from core.internal.shared.exceptions import ConfigValidationError, PlatformFatalError

logger = logging.getLogger(__name__)


def _write_manifest_at(core_dir: Path, entries: list[tuple[str, list[str]]]) -> Path:
    """Write secrets-manifest.yaml with required∧sops + consumers entries."""
    lines = ["secrets:"]
    for name, consumers in entries:
        lines.append(f"  - name: {name}")
        lines.append("    tier: required")
        lines.append("    source: sops")
        lines.append(f"    consumers: {consumers}")
    manifest = core_dir / "secrets-manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def _write_node_yaml(nc_dir: Path, node_name: str, *, allow_autogen: bool | None = None) -> None:
    """Write minimal node.yaml for module-aware resolution (nginx enabled)."""
    node_dir = nc_dir / node_name
    node_dir.mkdir(parents=True, exist_ok=True)
    lines = ["modules:", "  - name: nginx", "    enabled: true"]
    if allow_autogen is not None:
        lines += ["secrets:", f"  allow_autogen: {str(allow_autogen).lower()}"]
    (node_dir / "node.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_target_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Удаление целевых переменных — детерминированный verifier (как test_secrets_postcondition)."""
    for var in ("GHCR_PULL_TOKEN", "POSTGRES_PASSWORD", "POSTGRES_USER", "CLICKHOUSE_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


# ═══════════════════════════════════════════════════════════════════
# Tests: verify_required_sops_secrets (allow_autogen gate, unit)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_no_autogen_required_missing_fails
## @purpose  T1 AC1: без флага (allow_autogen=False) + нет enc-файла + required∧sops →
##           ConfigValidationError со списком имён и hint'ом (φ4 оборачивает в exit 10).
def test_no_autogen_required_missing_fails(tmp_path: Path) -> None:
    """Node has no enc-file and no allow_autogen flag → required∧sops contract violation fails."""
    manifest = _write_manifest_at(tmp_path / "core", [("WEBNAMES_API_KEY", ["nginx"])])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("", encoding="utf-8")
    enc_file = tmp_path / "nc" / "secrets" / "node.enc.yaml"  # отсутствует

    with pytest.raises(ConfigValidationError, match="allow_autogen"):
        helpers_secrets.verify_required_sops_secrets(
            manifest_path=str(manifest),
            secrets_env=str(env_file),
            enc_file=str(enc_file),
            enabled_modules={"nginx"},
            allow_autogen=False,
        )
    logger.info("[IMP:9][test_no_autogen_required_missing_fails] PASS: fail-loud without allow_autogen")


# endregion FUNC_test_no_autogen_required_missing_fails


# region FUNC_test_allow_autogen_true_permits_autogen
## @purpose  T1: флаг true (lab/arena, D2) + нет enc-файла → verifier skip (IMP:8), без raise.
def test_allow_autogen_true_permits_autogen(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """allow_autogen=true permits autogen-only bootstrap even with required∧sops declared."""
    manifest = _write_manifest_at(tmp_path / "core", [("WEBNAMES_API_KEY", ["nginx"])])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("", encoding="utf-8")
    enc_file = tmp_path / "nc" / "secrets" / "node.enc.yaml"  # отсутствует
    caplog.set_level(logging.INFO)

    helpers_secrets.verify_required_sops_secrets(
        manifest_path=str(manifest),
        secrets_env=str(env_file),
        enc_file=str(enc_file),
        enabled_modules={"nginx"},
        allow_autogen=True,
    )
    assert any("allow_autogen=true" in r.message for r in caplog.records), (
        "Missing IMP:8 skip log for allow_autogen=true autogen-only node"
    )
    logger.info("[IMP:9][test_allow_autogen_true_permits_autogen] PASS: autogen permitted by flag")


# endregion FUNC_test_allow_autogen_true_permits_autogen


# ═══════════════════════════════════════════════════════════════════
# Tests: ensure_secrets_exist (node.yaml flag resolution, integration-lite)
# ═══════════════════════════════════════════════════════════════════


def _di_env(tmp_path: Path, node_name: str = "mynode") -> dict[str, str]:
    """DI-env: NODE_CONFIGS_DIR=tmp/nc, NODE_NAME, SECRETS_ENV_FILE."""
    return {
        "NODE_CONFIGS_DIR": str(tmp_path / "nc"),
        "NODE_NAME": node_name,
        "SECRETS_ENV_FILE": str(tmp_path / "secrets.env"),
    }


# region FUNC_test_ensure_no_flag_resolved_from_node_yaml_fails
## @purpose  Интеграция резолва: node.yaml БЕЗ secrets.allow_autogen + no enc + required∧sops
##           (consumer nginx enabled) → ensure_secrets_exist поднимает ConfigValidationError
##           (реальный _resolve_allow_autogen → False → fail-loud).
def test_ensure_no_flag_resolved_from_node_yaml_fails(tmp_path: Path) -> None:
    """ensure_secrets_exist resolves allow_autogen from node.yaml — absent flag fails loud."""
    _write_node_yaml(tmp_path / "nc", "mynode", allow_autogen=None)
    core_dir = tmp_path / "core"
    _write_manifest_at(core_dir, [("WEBNAMES_API_KEY", ["nginx"])])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="allow_autogen"):
        helpers_secrets.ensure_secrets_exist(str(core_dir), env=_di_env(tmp_path))
    logger.info("[IMP:9][test_ensure_no_flag_resolved_from_node_yaml_fails] PASS: node.yaml flag absent → fail")


# endregion FUNC_test_ensure_no_flag_resolved_from_node_yaml_fails


# region FUNC_test_ensure_allow_autogen_true_resolved_from_node_yaml_passes
## @purpose  Интеграция резолва: node.yaml С secrets.allow_autogen: true + no enc →
##           ensure_secrets_exist завершается без raise (autogen-only валиден).
def test_ensure_allow_autogen_true_resolved_from_node_yaml_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_secrets_exist honours allow_autogen=true from node.yaml — no fail."""
    _write_node_yaml(tmp_path / "nc", "mynode", allow_autogen=True)
    core_dir = tmp_path / "core"
    _write_manifest_at(core_dir, [("WEBNAMES_API_KEY", ["nginx"])])
    env_file = tmp_path / "secrets.env"
    env_file.write_text("", encoding="utf-8")

    # R1: реальный assert — spy на postcondition-verifier доказывает, что allow_autogen=True
    # из node.yaml ДОШЁЛ до гейта (иначе autogen-only нода fail-loud-илась бы).
    verify_calls: list[dict[str, object]] = []

    def _spy_verify(*, allow_autogen: bool | None = None, **_: object) -> None:
        verify_calls.append({"allow_autogen": allow_autogen})

    monkeypatch.setattr(helpers_secrets, "verify_required_sops_secrets", _spy_verify)
    helpers_secrets.ensure_secrets_exist(str(core_dir), env=_di_env(tmp_path))

    assert len(verify_calls) == 1, f"verifier обязан быть вызван ровно раз, got {len(verify_calls)}"
    assert verify_calls[0]["allow_autogen"] is True, f"allow_autogen из node.yaml обязан дойти: {verify_calls}"
    logger.info("[IMP:9][test_ensure_allow_autogen_true_resolved_from_node_yaml_passes] PASS: flag true → autogen ok")


# endregion FUNC_test_ensure_allow_autogen_true_resolved_from_node_yaml_passes


# ═══════════════════════════════════════════════════════════════════
# Tests: phase_secrets_provision (φ4 fail-loud conversion → exit 10)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_phase4_ensure_failure_becomes_platform_fatal
## @purpose  T1/AC1 φ4-уровень: ConfigValidationError из ensure_secrets_exist (allow_autogen
##           gate) конвертируется _run_secrets_step в PlatformFatalError (exit 10) — фаза
##           НЕ возвращает done с WARN.
def test_phase4_ensure_failure_becomes_platform_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """φ4 wraps ensure ConfigValidationError into PlatformFatalError (exit 10 semantics)."""
    monkeypatch.setattr(helpers_secrets, "decrypt_secrets", lambda _core_dir: None)

    def _raise_gate(*_args: object, **_kwargs: object) -> None:
        gate_msg = "Required SOPS secrets missing — set secrets.allow_autogen: true"
        raise ConfigValidationError(gate_msg)

    monkeypatch.setattr(helpers_secrets, "ensure_secrets_exist", _raise_gate)

    with pytest.raises(PlatformFatalError, match="Secrets verification failed"):
        phase_secrets_provision(str(tmp_path), "mynode", "{}", env={})
    logger.info("[IMP:9][test_phase4_ensure_failure_becomes_platform_fatal] PASS: φ4 → PlatformFatalError")


# endregion FUNC_test_phase4_ensure_failure_becomes_platform_fatal
