#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-secrets-phase, secrets-provision, phase-secrets-provision, age-key, etc-age, persist, W4, devplan-140, unit-tests
# STRUCTURE: ▶ mock helpers_secrets.decrypt_secrets + ensure_secrets_exist → ◇ phase_secrets_provision(tmp core_dir, AGE_SECRET_KEY env) → ◇ assert /etc/age/key.txt НЕ создан → ◇ assert return True + IMP:9 → ⎋ 2 pass
# region MODULE_CONTRACT
## @purpose  Unit tests for phase_secrets_provision (core/internal/bootstrap/lifecycle/phases/secrets.py) —
##           DevPlan 140 W4 (W12-on-node-age-key): φ4 НЕ персистит AGE-ключ на диск ноды.
##           Канон — env (AGE_SECRET_KEY: CI / AGE_SECRET_KEY_FILE: bootstrap оператора) →
##           tmpfs decrypt-only (S-13, decrypt_secrets.py); /etc/age/key.txt — ТОЛЬКО
##           restore-first fallback (ручной перенос оператором), НЕ создаётся φ4.
## @scope    Pure unit tests — native imports, no subprocess, no Docker. helpers_secrets
##           I/O (decrypt_secrets/ensure_secrets_exist) мокается (shell-фасад lib/secrets.sh
##           вне скоупа); core_dir — tmp_path (pre-check os.path.isdir).
## @invariants
##   - φ4 НЕ создаёт /etc/age/key.txt даже при AGE_SECRET_KEY env (persist-блок удалён, W4)
##   - φ4 возвращает True при env-only канале (ключ приходит env, файл-на-диске не требуется)
##   - Каждый тест: real asserts (R1/R2) + TRAP[TEST] + IMP:9 LDD log
## @rationale DevPlan 140 §5 W4 AC-W4-1: «φ4 на свежей ноде НЕ создаёт /etc/age/key.txt».
##            Тест фиксирует отсутствие persist-поведения (negative на удалённый persist-блок).
## @changes  2026-08-06 · DevPlan 140 W4 — Created
# endregion MODULE_CONTRACT
"""

import logging
import os

import pytest

from core.internal.bootstrap.lifecycle.phases.secrets import phase_secrets_provision
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

TEST_AGE_KEY = "AGE-SECRET-KEY-0123456789abcdef"
_ETC_AGE_KEY_FILE = "/etc/age/key.txt"


@pytest.fixture
def mock_secrets_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock secrets-domain I/O helpers — shell-фасад (lib/secrets.sh) вне unit-скоупа."""
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.secrets.decrypt_secrets",
        lambda core_dir: None,
    )
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.secrets.ensure_secrets_exist",
        lambda core_dir: None,
    )


# region FUNC_test_secrets_provision_does_not_create_etc_age_key_txt
## @purpose — W4 AC-W4-1: φ4 с AGE_SECRET_KEY env НЕ создаёт /etc/age/key.txt (persist удалён).
## @io — ⇥ caplog, monkeypatch, tmp_path, mock_secrets_helpers → ⎋ None (asserts file absent + True)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-06 · REGRESSION (negative на удалённый persist) · W4 — φ4 не пишет key.txt
# · Scenario: AGE_SECRET_KEY env установлен (канон CI) + helpers мокаются → phase_secrets_provision
# ·   отрабатывает, /etc/age/key.txt НЕ существует (persist-блок удалён из phases/secrets.py)
# · Last fail: 2026-08-05 — /etc/age/key.txt (plaintext 0600) персистился φ4 на ноде
# ·   (W12-on-node-age-key, DevPlan 136 W1 T1.4); зафиксировано пользователем + docs DR
# · Remove if: persist-блок вернётся в phases/secrets.py (канон env → tmpfs decrypt-only, S-13)
def test_secrets_provision_does_not_create_etc_age_key_txt(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
    mock_secrets_helpers,
) -> None:
    """W4: φ4 с AGE_SECRET_KEY env НЕ создаёт /etc/age/key.txt (persist удалён)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)

    logger.info("[IMP:7][test_secrets_phase] Running φ4 with AGE_SECRET_KEY env (W4)")
    result = phase_secrets_provision(str(tmp_path), "test-node", "{}")
    assert result is True, f"φ4 должен вернуть True, got {result}"
    # Guard: реальный /etc/age/key.txt на тестовой машине отсутствует (dev/CI); если бы φ4
    # персистил ключ (регрессия persist-блока) — файл появился бы, assert это поймает.
    assert not os.path.isfile(_ETC_AGE_KEY_FILE), (
        f"W4 FAIL: φ4 НЕ должен создавать {_ETC_AGE_KEY_FILE} — persist удалён (env → tmpfs, S-13)"
    )
    logger.info("[IMP:9][test_secrets_phase] φ4 завершился БЕЗ создания /etc/age/key.txt (W4) — OK")


# endregion FUNC_test_secrets_provision_does_not_create_etc_age_key_txt


# region FUNC_test_secrets_provision_env_only_success
## @purpose — W4: φ4 завершается успешно по env-only каналу (AGE_SECRET_KEY env, без файла-на-диске)
##            — канон CI node-update; логирует IMP:9 decrypt.
## @io — ⇥ caplog, monkeypatch, tmp_path, mock_secrets_helpers → ⎋ None (asserts True + IMP:9 log)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-06 · REGRESSION · W4 — φ4 env-only канал (CI node-update)
# · Scenario: AGE_SECRET_KEY env (канон) + helpers мокаются → φ4 True; decrypt-путь не требует
# ·   файла-на-диске (ключ в env на время команды → tmpfs decrypt-only, S-13)
# · Last fail: N/A (new test — DevPlan 140 W4)
# · Remove if: env-only канал φ4 меняется
def test_secrets_provision_env_only_success(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
    mock_secrets_helpers,
) -> None:
    """W4: φ4 успешен по env-only каналу (AGE_SECRET_KEY env, файл-на-диске не нужен)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)

    logger.info("[IMP:7][test_secrets_phase] Running φ4 env-only channel (W4)")
    result = phase_secrets_provision(str(tmp_path), "test-node", "{}")
    assert result is True, f"φ4 env-only должен вернуть True, got {result}"
    assert "Secrets decrypted successfully" in caplog.text, "IMP:9 decrypt log отсутствует (env-only канал)"
    logger.info("[IMP:9][test_secrets_phase] φ4 env-only канал успешен (CI node-update, W4) — OK")


# endregion FUNC_test_secrets_provision_env_only_success
