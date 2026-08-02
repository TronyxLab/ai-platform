#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-compose-profiles loader COMPOSE_PROFILES platform-infra SoT fail-fast unit
# STRUCTURE: ▶ test_load_profiles (SoT → list) → test_missing_key_raises → test_missing_file_raises → test_resolve_env_override
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/compose_profiles.py — единый loader COMPOSE_PROFILES
##           (DevPlan 118 C3). Чтение platform-infra.yaml env_defaults (SoT).
## @scope    Tests: load_profiles(), resolve_infra_path(). tmp_path-based, no real repo deps.
## @invariants
##   - load_profiles: читает ТОЛЬКО platform-infra.yaml (SoT); fail-fast FileNotFoundError/KeyError
##   - resolve_infra_path: env PLATFORM_ROOT приоритетнее script-relative
##   - LDD: IMP:9 в успешных сценариях
## @rationale DevPlan 118 C3 §TEST — unit loader читает platform-infra.yaml; parity-гейт остаётся зелёным.
## @changes 2026-08-02 | DevPlan 118 C3 — created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest import mock

import pytest
import yaml

from core.internal.shared.compose_profiles import load_profiles, resolve_infra_path

logger = logging.getLogger(__name__)


def _write_infra(tmp_path: Path, profiles: str) -> Path:
    """Write a minimal platform-infra.yaml env_defaults with COMPOSE_PROFILES."""
    infra = tmp_path / "core" / "platform-infra.yaml"
    infra.parent.mkdir(parents=True, exist_ok=True)
    infra.write_text(yaml.safe_dump({"env_defaults": {"COMPOSE_PROFILES": profiles}}))
    return infra


# 🧪 TRAP[TEST] · Regression · load_profiles читает SoT (DevPlan 118 C3)
# · Scenario: platform-infra.yaml env_defaults.COMPOSE_PROFILES="a,b,c" → list["a","b","c"]
# · Last fail: scaffold_helpers читал generated platform-env.yaml — расхождение SoT↔generated
# · Remove if: compose_profiles loader removed
def test_load_profiles_from_soT(caplog, tmp_path) -> None:
    """load_profiles → list[str] из platform-infra.yaml env_defaults (SoT)."""
    caplog.set_level(logging.INFO)
    _write_infra(tmp_path, "postgres,redis,nginx")
    with mock.patch.dict("os.environ", {"PLATFORM_ROOT": str(tmp_path)}, clear=False):
        profiles = load_profiles()
    assert profiles == ["postgres", "redis", "nginx"]
    assert any("[IMP:9]" in r.message for r in caplog.records), "LDD: no IMP:9 log"


# 🧪 TRAP[TEST] · Regression · missing key → KeyError (fail-fast, AC-C3)
# · Scenario: env_defaults без COMPOSE_PROFILES → KeyError (никогда silent [])
# · Last fail: scaffold_helpers.read platform-env.yaml — silent "" fallback
# · Remove if: fail-fast семантика loader'а меняется
def test_load_profiles_missing_key_raises(caplog, tmp_path) -> None:
    """COMPOSE_PROFILES отсутствует в SoT → KeyError (fail-fast, без silent [])."""
    caplog.set_level(logging.INFO)
    infra = tmp_path / "core" / "platform-infra.yaml"
    infra.parent.mkdir(parents=True, exist_ok=True)
    infra.write_text(yaml.safe_dump({"env_defaults": {"OTHER": "x"}}))
    with mock.patch.dict("os.environ", {"PLATFORM_ROOT": str(tmp_path)}, clear=False), pytest.raises(KeyError):
        load_profiles()


# 🧪 TRAP[TEST] · Regression · missing file → FileNotFoundError (fail-fast)
# · Scenario: platform-infra.yaml отсутствует → FileNotFoundError (resolve_infra_path=None)
# · Last fail: scaffold_helpers — silent fallback на пустой список
# · Remove if: fail-fast семантика loader'а меняется
def test_load_profiles_missing_file_raises(caplog, tmp_path) -> None:
    """platform-infra.yaml отсутствует → FileNotFoundError (fail-fast)."""
    caplog.set_level(logging.INFO)
    with (
        mock.patch("core.internal.shared.compose_profiles.resolve_infra_path", return_value=None),
        pytest.raises(FileNotFoundError),
    ):
        load_profiles()


# 🧪 TRAP[TEST] · Regression · resolve_infra_path env PLATFORM_ROOT приоритетнее
# · Scenario: PLATFORM_ROOT → core/platform-infra.yaml найден
# · Last fail: N/A (C3 unit)
# · Remove if: path-резолвинг loader'а меняется
def test_resolve_infra_path_env_override(caplog, tmp_path) -> None:
    """resolve_infra_path → PLATFORM_ROOT/core/platform-infra.yaml."""
    caplog.set_level(logging.INFO)
    infra = _write_infra(tmp_path, "a")
    with mock.patch.dict("os.environ", {"PLATFORM_ROOT": str(tmp_path)}, clear=False):
        resolved = resolve_infra_path()
    assert resolved == infra


# 🧪 TRAP[TEST] · Regression · load_profiles split/strip токенов
# · Scenario: "a, b ,,c" → ["a","b","c"] (strip + пустые токены отбрасываются)
# · Last fail: N/A (C3 unit)
# · Remove if: split-семантика loader'а меняется
def test_load_profiles_strips_tokens(caplog, tmp_path) -> None:
    """Токены strip'ятся, пустые отбрасываются."""
    caplog.set_level(logging.INFO)
    _write_infra(tmp_path, "a, b ,,c")
    with mock.patch.dict("os.environ", {"PLATFORM_ROOT": str(tmp_path)}, clear=False):
        profiles = load_profiles()
    assert profiles == ["a", "b", "c"]
