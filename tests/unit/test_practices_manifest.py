#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-practices-manifest, load-manifest, PracticeCheck, PracticesManifest, schema-valid, unique-ids, thresholds, l1-checks, ConfigValidationError
# STRUCTURE: ▶ load_manifest valid → ◇ version=1, checks unique → ◇ thresholds 30/50 → ◇ checks_for filter → ◇ l1_checks → ◇ negative: broken manifest → ConfigValidationError (exit 4)
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/practices/manifest.py (DevPlan 137 W1): валидность канона
##           через schema_validator (Draft7), уникальность id, пороги из канона (не хардкод),
##           checks_for/applicable_checks/l1_checks фильтры, R5-negative на сломанный канон.
## @scope    $TEST_SPEC 137 W1: test_practices_manifest (schema valid, id уникальны).
## @invariants
##   - Native imports (no subprocess для бизнес-логики)
##   - tmp_path для negative-копии канона (zero hardcode)
##   - LDD: IMP:9-траектория через caplog (_print_ldd_trajectory)
##   - R5: negative-тест на ConfigValidationError (сломанная схема → exit 4 семантика)
## @rationale  Канон — SoT практик; тесты пинят контракт: валидный канон загружается,
##             структурная ошибка — ConfigValidationError (не тихий fallback).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT
"""

import logging
import shutil
from pathlib import Path

import pytest

from core.internal.practices.manifest import (
    DEFAULT_MANIFEST_PATH,
    checks_for,
    l1_checks,
    load_manifest,
    maturity_thresholds,
)
from core.internal.shared.exceptions import ConfigValidationError
from tests.conftest import _print_ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · канон загружается и валиден (Draft7)
# · Regression: schema_validator — единственная Draft7-точка (DevPlan 116 B6 T5)
# · Last fail: N/A (новый канон 137)
# · Remove if: practices_manifest.yaml schema меняется
def test_load_manifest_valid(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """load_manifest() возвращает PracticesManifest (version=1, checks непусты)."""
    with caplog.at_level(logging.INFO):
        manifest = load_manifest()

    assert manifest.version == 1
    assert len(manifest.checks) >= 10
    assert manifest.pins["gitleaks"] == "v8.30.1"
    assert manifest.pins["ruff_pre_commit"] == "v0.16.1"  # rev-значение (паритет корневому конфигу)
    assert len(manifest.allowed_external_networks) >= 6
    # каждая проверка — frozen dataclass с требуемыми полями
    first = manifest.checks[0]
    assert first.id and first.level in ("baseline", "full")
    assert first.klass in ("L1", "L2", "L3")
    assert first.timeout_sec > 0

    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога load_manifest"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · id проверок уникальны (kebab-case)
# · Regression: дубль id → неоднозначный selection в check_project
# · Last fail: N/A
# · Remove if: канон перестаёт требовать уникальность id
def test_checks_ids_unique() -> None:
    """Все check id уникальны и kebab-case."""
    manifest = load_manifest()
    ids = [c.id for c in manifest.checks]
    assert len(ids) == len(set(ids)), f"duplicate check ids: {ids}"
    for cid in ids:
        assert cid.replace("-", "").isalnum(), f"id not kebab-case: {cid}"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · пороги из канона (не хардкод)
# · Regression: пороги 30/50 — решение пользователя 2026-08-05, зеркала в manifest.py
# · Last fail: N/A
# · Remove if: пороги меняются решением пользователя
def test_maturity_thresholds_from_canon() -> None:
    """Пороги зрелости читаются из канона (30 дней / 50 файлов, автопромоута нет)."""
    thresholds = maturity_thresholds()
    assert thresholds["age_days_propose"] == 30
    assert thresholds["code_files_propose"] == 50
    # константа автопромоута НЕ существует (решение 2026-08-05)
    assert "K" not in thresholds


# 🧪 TRAP[TEST] · 2026-08-05 · unit · checks_for фильтрует по языку/уровню/каналу
# · Regression: "all"-проверки применяются к любому языку
# · Last fail: N/A
# · Remove if: checks_for API меняется
def test_checks_for_filters() -> None:
    """checks_for возвращает проверку только при совпадении всех измерений."""
    python = checks_for("ruff-format", language="python", level="baseline", channel="local")
    assert len(python) == 1 and python[0].id == "ruff-format"
    # ruff-format не применяется к typescript
    assert checks_for("ruff-format", language="typescript", level="baseline", channel="local") == ()
    # full-проверка не входит в baseline выборку
    assert checks_for("ruff-check", language="python", level="baseline", channel="local") == ()
    assert len(checks_for("ruff-check", language="python", level="full", channel="local")) == 1
    # verify-канал (K3) не исполняется локально
    assert checks_for("verify-contracts", language="python", level="full", channel="local") == ()


# 🧪 TRAP[TEST] · 2026-08-05 · unit · L1-проверки — всегда (безопасность платформы)
# · Regression: gitleaks + verify-contracts — L1 (блок всегда, §3.1 п.4)
# · Last fail: N/A
# · Remove if: классы L1/L2/L3 меняются
def test_l1_checks_always_executed() -> None:
    """l1_checks() содержит gitleaks и verify-contracts; все — класс L1."""
    l1 = l1_checks()
    ids = {c.id for c in l1}
    assert "gitleaks" in ids
    assert "verify-contracts" in ids
    assert all(c.klass == "L1" for c in l1)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · R5-negative: сломанный канон → ConfigValidationError
# · Regression: структурная ошибка канона НЕ должна молча деградировать (fail-fast, exit 4)
# · Last fail: N/A (negative-тест R5 на новый код)
# · Remove if: поведение fail-fast меняется осознанно
def test_invalid_manifest_raises_config_validation(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Сломанный канон (version=2 ≠ schema const 1) → ConfigValidationError (exit 4 семантика)."""
    broken = tmp_path / "practices_manifest.yaml"
    shutil.copy(DEFAULT_MANIFEST_PATH, broken)
    text = broken.read_text(encoding="utf-8")
    # заменить ИМЕННО YAML-ключ version (не строку в STRUCTURE-комментарии)
    text = text.replace("\nversion: 1\n", "\nversion: 2\n", 1)
    broken.write_text(text, encoding="utf-8")

    with caplog.at_level(logging.INFO), pytest.raises(ConfigValidationError) as excinfo:
        load_manifest(broken)
    assert excinfo.value.exit_code == 4
    assert "invalid" in str(excinfo.value).lower()
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9/IMP:10 лога валидации"
