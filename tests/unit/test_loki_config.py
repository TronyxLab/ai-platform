# GREP_SUMMARY: test-loki-config limits-config out-of-order window max-chunk-age reject-old-samples d8
# STRUCTURE: ▶ parse loki-config.yml (repo root) → ◇ window = max_chunk_age/2 ≥ 24h ? (detector) → ◇ R5 negative (исходный T4-вход 1h → 30m → fail) → ◇ removed oow-param guard → ◇ limits preserved → ⎋ IMP:9 PASS
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/logging/config/loki-config.yml — D-8 (DevPlan 140 W3):
##           out-of-order toleration window (Loki 3.x: window = ingester.max_chunk_age/2) and
##           preservation of the remaining limits_config keys.
## @scope    Static config parsing — no Docker. Reads the real config from repo root; R5-negative
##           case uses tmp_path with the original T4-bug input (max_chunk_age: 1h → window 30m).
## @invariants
##   - Reading from repo_root() — zero hardcoded paths
##   - Detector `_assert_window_ge` raises AssertionError when out-of-order window < required
##   - AC-W3: skew ±24h → 0 rejected ⇒ window ≥ 24h
##   - R5 negative: exact input that triggered T4 (max_chunk_age: 1h) fails the detector
##   - Guard: `out_of_order_time_window` (removed from Loki 3.x schema) is NOT present —
##     setting it crashes Loki at startup (dskit yaml.UnmarshalStrict)
## @rationale D-8: `out_of_order_time_window` removed in Loki 3.7.3 (config error at startup);
##            the only knob is ingester.max_chunk_age (window = max_chunk_age/2, docs + source
##            pkg/ingester/stream.go:441-443). Test guards the toleration against regression.
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_LOKI_CONFIG = repo_root() / "core" / "modules" / "logging" / "config" / "loki-config.yml"

# AC-W3 (DevPlan 140): chaos T4 injects clock skew ±24h — out-of-order window MUST be ≥ 24h.
_MIN_SKEW_TOLERANCE_H = 24.0
# Параметр УДАЛЁН из схемы Loki 3.x (deprecated 3.0 → удалён) — его присутствие = crash на старте.
_REMOVED_OOW_PARAM = "out_of_order_time_window"


# region HELPERS
def _load_config(path: Path) -> dict:
    """Load loki-config.yml (yaml.safe_load)."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _duration_hours(value: object) -> float:
    """Parse Go-style duration ('72h', '30m', '168h') into hours.

    ## @purpose — Loki durations use Go syntax; convert to hours for window math.
    ## @io — ⇥ value: str|int → ⎋ float hours
    ## @complexity — O(1) regex
    """
    text = str(value).strip()
    if text == "0":
        return 0.0
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)?", text)
    if not m:
        msg = f"invalid Go duration: {value!r}"
        raise ValueError(msg)
    num = float(m.group(1))
    unit = m.group(2) or "s"
    factors = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0 / 3600, "m": 1.0 / 60, "h": 1.0}
    return num * factors[unit]


def _out_of_order_window_hours(config: dict) -> float:
    """Loki 3.x out-of-order window = ingester.max_chunk_age / 2 (docs + stream.go:441-443)."""
    ingester = config.get("ingester", {})
    if "max_chunk_age" not in ingester:
        msg = (
            "loki-config.yml: ingester.max_chunk_age is REQUIRED — in Loki 3.x it is the only "
            "lever for the out-of-order window (`out_of_order_time_window` was removed from schema)"
        )
        raise AssertionError(msg)
    return _duration_hours(ingester["max_chunk_age"]) / 2.0


def _assert_window_ge(config: dict, min_hours: float) -> float:
    """Detector: raise AssertionError when out-of-order window < min_hours. Returns window hours."""
    window = _out_of_order_window_hours(config)
    assert window >= min_hours, (
        f"out-of-order window {window}h < required {min_hours}h "
        f"(D-8: skew ±24h → 0 rejected). Loki 3.x: window = max_chunk_age/2 — "
        f"increase ingester.max_chunk_age"
    )
    return window


# endregion HELPERS


# 🧪 TRAP[TEST] · Regression · Scenario: D-8 loki-config out-of-order window ≥ 24h
# · Expect: ingester.max_chunk_age / 2 ≥ 24h (AC-W3: skew ±24h → 0 rejected)
# · Last fail: T4 chaos (tests/e2e/test_chaos_resilience.py) — 1943 rejected «entry too far
# ·   behind» при max_chunk_age: 1h (window 30m); воспроизведено локально 2026-08-06
# ·   (docker run grafana/loki:3.7.3, push записи 24h назад → HTTP 400)
# · Remove if: Loki reintroduces a dedicated out-of-order window parameter
@ldd_trajectory
def test_loki_config_out_of_order_window_ge_24h(caplog) -> None:
    """Real config: out-of-order window (max_chunk_age/2) ≥ 24h."""
    assert _LOKI_CONFIG.exists(), f"loki-config.yml not found: {_LOKI_CONFIG}"
    config = _load_config(_LOKI_CONFIG)

    window_h = _assert_window_ge(config, _MIN_SKEW_TOLERANCE_H)

    logger.info(
        "[IMP:9][test_loki_config][window] out-of-order window=%.1fh (max_chunk_age=%s/2) >= %sh → skew ±24h tolerated",
        window_h,
        config["ingester"]["max_chunk_age"],
        _MIN_SKEW_TOLERANCE_H,
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · detector _assert_window_ge — D-8/T4
# · Last fail: исходный вход T4 — max_chunk_age: 1h (window 30m < 24h) → «entry too far behind»
# · Remove if: window lever changes (e.g., new out-of-order parameter replaces max_chunk_age)
@ldd_trajectory
def test_loki_config_out_of_order_window_negative_r5(tmp_path: Path, caplog) -> None:
    """R5 negative: original T4-bug input (max_chunk_age: 1h → window 30m) fails the detector."""
    cfg = tmp_path / "loki-config.yml"
    cfg.write_text(
        "ingester:\n"
        "  max_chunk_age: 1h\n"  # ← исходный T4-вход (window 0.5h < 24h)
        "limits_config:\n"
        "  reject_old_samples: true\n",
        encoding="utf-8",
    )
    config = _load_config(cfg)

    with pytest.raises(AssertionError, match="out-of-order window"):
        _assert_window_ge(config, _MIN_SKEW_TOLERANCE_H)

    logger.info(
        "[IMP:9][test_loki_config][negative-r5] window=%.1fh < %sh — исходный T4-вход детектирован",
        _out_of_order_window_hours(config),
        _MIN_SKEW_TOLERANCE_H,
    )


# 🧪 TRAP[TEST] · Regression · Scenario: guard против `out_of_order_time_window` в конфиге
# · Expect: параметр отсутствует (в Loki 3.7.3 его настройка = отказ старта)
# · Last fail: DevPlan 140 §4.3 предложил out_of_order_time_window как вариант; локальная
# ·   проверка 2026-08-06 показала crash: «yaml: unmarshal errors: field
# ·   out_of_order_time_window not found in type validation.plain»
# · Remove if: Loki reintroduces the parameter in a future version
@ldd_trajectory
def test_loki_config_no_removed_oow_param(caplog) -> None:
    """Config MUST NOT contain out_of_order_time_window (removed from Loki 3.x schema → crash)."""
    config = _load_config(_LOKI_CONFIG)

    # Проверяем именно YAML-ключ (строка без #-префикса), не упоминания в TRAP-комментариях.
    key_pattern = re.compile(rf"^\s*{re.escape(_REMOVED_OOW_PARAM)}\s*:", re.MULTILINE)
    assert key_pattern.search(_LOKI_CONFIG.read_text(encoding="utf-8")) is None, (
        f"{_REMOVED_OOW_PARAM} removed from Loki 3.x schema — its presence crashes Loki at "
        f"startup (dskit yaml.UnmarshalStrict). Use ingester.max_chunk_age (window = max_chunk_age/2)."
    )
    assert _REMOVED_OOW_PARAM not in str(config), f"{_REMOVED_OOW_PARAM} present in parsed config"

    logger.info("[IMP:9][test_loki_config][oow-guard] %s absent — no startup-crash risk", _REMOVED_OOW_PARAM)


# 🧪 TRAP[TEST] · Regression · Scenario: остальные limits_config ключи не тронуты (D-8)
# · Expect: retention_period 168h, reject_old_samples true + 168h, max_query_series 500,
# ·   max_query_parallelism 8, max_query_lookback 168h, allow_structured_metadata true
# · Last fail: None (guard against collateral damage при правке D-8)
# · Remove if: limits политика меняется осознанно
@ldd_trajectory
def test_loki_config_limits_preserved(caplog) -> None:
    """D-8 правка не сломала остальные limits_config."""
    config = _load_config(_LOKI_CONFIG)
    limits = config.get("limits_config", {})

    assert limits.get("retention_period") == "168h", f"retention_period={limits.get('retention_period')}"
    assert limits.get("reject_old_samples") is True, "reject_old_samples must stay true (absolute floor)"
    assert limits.get("reject_old_samples_max_age") == "168h"
    assert limits.get("max_query_series") == 500, f"max_query_series={limits.get('max_query_series')}"
    assert limits.get("max_query_parallelism") == 8
    assert limits.get("max_query_lookback") == "168h"
    assert limits.get("allow_structured_metadata") is True

    logger.info(
        "[IMP:9][test_loki_config][limits] retention=%s reject_old_samples=%s max_query_series=%s "
        "structured_metadata=%s — limits не изменены",
        limits.get("retention_period"),
        limits.get("reject_old_samples"),
        limits.get("max_query_series"),
        limits.get("allow_structured_metadata"),
    )
