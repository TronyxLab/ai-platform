# GREP_SUMMARY: gate verify-contracts contracts-registered L1-always negative-tests R5 allowlist-canon canon-registration
# STRUCTURE: ▶ ┌canon: practices_manifest.yaml (verify-contracts L1/verify)┐ → ◇ (a) 9 контрактов §5 W4 реализованы в verify_contracts.py → ◇ (b) allowlist из канона (не хардкод) → ◇ (c) R5 negative-тесты присутствуют → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Гейт контракт-проверок verify_contracts (DevPlan 137 W5 §5 W4 п.5): (a) все 9
##           контрактов таблицы §5 W4 реализованы в core/internal/deploy/verify_contracts.py;
##           (b) external-networks allowlist читается из канона practices_manifest.yaml
##           (allowed_external_networks), НЕ хардкод (TRAP §10.2); (c) R5 negative-тесты для
##           каждого L1-контракта присутствуют в tests/unit/test_verify_contracts.py
##           (Test Honesty R5 — anti-survivorship: детектор обязан ловить исходный вход);
##           (d) проверка verify-contracts зарегистрирована в каноне (class L1, channel verify).
## @scope    Read-only гейт (make gate MODE=fast, -m gate). НЕ исполняет контракты — только
##           статическая сверка реализации/покрытия (runtime-контракты — unit-тесты W4).
## @invariants
##   - Контракты §5 W4: secrets-in-compose, ports-published, healthcheck-present,
##     external-networks, env-file-contract, platform-labels (L1), compose-config-valid,
##     drift-practices, build-check (L2)
##   - L1-класс исполняется при ЛЮБОМ уровне (политика §4.5) — гейт сверяет отсутствие
##     state-гейта для L1 в _severity_for
##   - allowlist внешних сетей — только из канона (поле allowed_external_networks в источнике)
##   - R5: negative-тест присутствует для каждого L1-контракта + drift-practices
## @rationale Контракты verify — машиночитаемые инварианты платформы; рассинхрон таблицы
##            §5 W4 с реализацией = дрейф защиты. R5: negative-тесты обязательны (§5 W4
##            «Negative-тесты R5»), иначе детектор может молча потерять покрытие.
## @changes  2026-08-05 · DevPlan 137 W4 — создан
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.practices.manifest import load_manifest
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_VERIFY_SRC = ROOT / "core" / "internal" / "deploy" / "verify_contracts.py"
_UNIT_TEST_SRC = ROOT / "tests" / "unit" / "test_verify_contracts.py"

# ── 9 контрактов таблицы §5 W4 (id из спецификации) ──
CONTRACT_IDS: tuple[str, ...] = (
    "secrets-in-compose",
    "ports-published",
    "healthcheck-present",
    "external-networks",
    "env-file-contract",
    "platform-labels",
    "compose-config-valid",
    "drift-practices",
    "build-check",
)

# ── R5: negative-тест для каждого L1-контракта (+ drift-practices L2) — имена из §5 W4 ──
_R5_NEGATIVE_TESTS: dict[str, str] = {
    "ports-published": "test_verify_contracts_ports_blocked",
    "healthcheck-present": "test_verify_contracts_no_healthcheck_blocked",
    "secrets-in-compose": "test_verify_contracts_secret_literal_blocked",
    "external-networks": "test_verify_contracts_external_network_unknown_blocked",
    "env-file-contract": "test_verify_contracts_env_file_wrong_blocked",
    "drift-practices": "test_verify_contracts_drift_full_blocked",
}


@pytest.mark.gate
def test_gate_verify_contracts_all_contracts_implemented() -> None:
    """Все 9 контрактов §5 W4 реализованы в verify_contracts.py (id присутствуют в источнике)."""
    src = _VERIFY_SRC.read_text(encoding="utf-8")
    for cid in CONTRACT_IDS:
        assert cid in src, f"контракт '{cid}' не реализован в verify_contracts.py (таблица §5 W4)"


@pytest.mark.gate
def test_gate_verify_contracts_registered_in_canon() -> None:
    """Канон практик содержит проверку verify-contracts (class L1, channel verify)."""
    manifest = load_manifest()
    vc = [c for c in manifest.checks if c.id == "verify-contracts"]
    assert vc, "канон practices_manifest.yaml должен содержать проверку id=verify-contracts"
    assert vc[0].klass == "L1", f"verify-contracts обязан быть class L1 (безопасность платформы): {vc[0]}"
    assert "verify" in vc[0].channel, f"verify-contracts обязан исполняться в канале verify: {vc[0]}"


@pytest.mark.gate
def test_gate_verify_contracts_external_networks_allowlist_from_canon() -> None:
    """external-networks allowlist — из канона (allowed_external_networks), НЕ хардкод (TRAP §10.2)."""
    src = _VERIFY_SRC.read_text(encoding="utf-8")
    assert "allowed_external_networks" in src, (
        "verify_contracts.py обязан читать allowlist из канона (manifest.allowed_external_networks)"
    )
    assert "load_manifest" in src, "verify_contracts.py обязан загружать канон (load_manifest)"


@pytest.mark.gate
def test_gate_verify_contracts_l1_always_blocks() -> None:
    """L1-контракты блокируют при ЛЮБОМ уровне: _severity_for не гейтит L1 по state (кроме legacy-grace)."""
    src = _VERIFY_SRC.read_text(encoding="utf-8")
    assert "def _severity_for" in src, "verify_contracts.py обязан иметь централизованную политику severity"
    # L1 ветка возвращает warning ТОЛЬКО в legacy-grace — иначе block (политика §4.5)
    assert "if klass == KLASS_L1:" in src
    assert "legacy_grace" in src, "legacy-grace (PRACTICES_LEGACY_GRACE) обязан поддерживаться (TRAP §10.2)"


@pytest.mark.gate
def test_gate_verify_contracts_negative_tests_present() -> None:
    """R5: negative-тесты для каждого L1-контракта + drift-practices присутствуют в unit-тестах."""
    test_src = _UNIT_TEST_SRC.read_text(encoding="utf-8")
    for cid, test_name in _R5_NEGATIVE_TESTS.items():
        assert test_name in test_src, f"R5: отсутствует negative-тест {test_name} для контракта {cid}"
        assert cid in test_src, f"R5: unit-тест {test_name} не ссылается на контракт {cid}"


@pytest.mark.gate
def test_gate_verify_contracts_legacy_grace_negative() -> None:
    """R5: legacy-grace покрыт negative-тестом (TRAP §10.2 — L1 ломает легаси-деплои, HI риск)."""
    test_src = _UNIT_TEST_SRC.read_text(encoding="utf-8")
    assert "test_verify_contracts_legacy_grace" in test_src, "R5: нет negative-теста legacy-grace"
    assert "PRACTICES_LEGACY_GRACE" in test_src, "legacy-grace тест обязан использовать env PRACTICES_LEGACY_GRACE"


@pytest.mark.gate
def test_gate_verify_contracts_baseline_green_positive() -> None:
    """Позитивный тест baseline-green присутствует (0 violations, exit 0 — анти-survivorship)."""
    test_src = _UNIT_TEST_SRC.read_text(encoding="utf-8")
    assert "test_verify_contracts_baseline_green" in test_src, "нет позитивного baseline-green теста (AC W4)"
