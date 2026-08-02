#!/usr/bin/env python3
# GREP_SUMMARY: compose-profiles, COMPOSE_PROFILES, platform-infra, loader, SoT, profiles-parity, shared
# STRUCTURE: ▶ ┌env chain (PLATFORM_ROOT → script-relative)┐ → ○ find platform-infra.yaml → ○ yaml.safe_load → ◇ env_defaults.COMPOSE_PROFILES → ○ split(',') → ⎋ list[str]
# region MODULE_CONTRACT
## @purpose  Единый loader COMPOSE_PROFILES (DevPlan 118 C3) — единственная точка чтения
##           platform-infra.yaml env_defaults.COMPOSE_PROFILES (SoT). Дедупликация двух
##           потребителей с разными источниками: scaffold_helpers читал generated
##           platform-env.yaml, docker_orchestrator — SoT через platform_config.
##           Parity-гейт check-profiles-parity закрепляет platform-infra.yaml как SoT.
## @scope    Импортируется scaffold_helpers.py и docker_orchestrator.py (≥2 потребителя —
##           критерий shared/, AC-C3). Публичный API: load_profiles() -> list[str].
## @invariants
##   1. Читает platform-infra.yaml (SoT), НЕ generated platform-env.yaml — единый loader
##      устраняет расхождение SoT↔generated (DevPlan 117 D23 канон)
##   2. Fail-fast: отсутствие файла/ключа → FileNotFoundError/KeyError (никогда silent [])
##   3. Path-резолвинг: (1) env PLATFORM_ROOT → Path(PLATFORM_ROOT)/core/platform-infra.yaml;
##      (2) script-relative корень репо (parents[3]/core/) — зеркало platform_config (T8.3/D23)
##   4. Возвращает list[str] — split(',') c strip; пустые токены отбрасываются
##   5. Параметр env позволяет тестировать без monkeypatch.setenv (tmp_path в тестах)
##   6. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale C3 (DevPlan 118): Два потребителя COMPOSE_PROFILES читали РАЗНЫЕ источники
##            (scaffold_helpers → platform-env.yaml generated, docker_orchestrator → platform-infra.yaml
##            SoT) — правка SoT применялась только в одном месте. Единый loader в shared/
##            устраняет источник дрейфа; оба потребителя делегируют.
## @changes  2026-08-02 | DevPlan 118 C3 — Created (единый loader COMPOSE_PROFILES)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# region FUNC_resolve_infra_path
## @purpose  Resolve core/platform-infra.yaml (SoT) — env PLATFORM_ROOT → script-relative repo root.
## @io       ⇥ env: dict | None (None = os.environ) → ⎋ Path | None (None = файл не найден)
## @complexity O(1) — два кандидата
## @invariants
##   - PLATFORM_ROOT env приоритетнее script-relative (тот же канон, что platform_config D23)
##   - Возвращает None если ни один кандидат не существует (fail-fast выше — _load raise)
def resolve_infra_path(env: dict | None = None) -> Path | None:
    """Resolve platform-infra.yaml path (SoT) — env PLATFORM_ROOT → script-relative repo root."""
    source = os.environ if env is None else env
    platform_root = source.get("PLATFORM_ROOT")
    if platform_root:
        candidate = Path(platform_root) / "core" / "platform-infra.yaml"
        if candidate.is_file():
            logger.info("[IMP:8][compose_profiles] Using platform-infra.yaml from PLATFORM_ROOT: %s", candidate)
            return candidate
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "core" / "platform-infra.yaml"
    if candidate.is_file():
        logger.info("[IMP:8][compose_profiles] Using platform-infra.yaml script-relative: %s", candidate)
        return candidate
    logger.warning("[IMP:7][compose_profiles] platform-infra.yaml not found (PLATFORM_ROOT=%s)", platform_root)
    return None


# endregion FUNC_resolve_infra_path


# region FUNC_load_profiles
## @purpose  Загрузить COMPOSE_PROFILES из platform-infra.yaml env_defaults (SoT) как list[str].
## @io       ⇥ env: dict | None (None = os.environ) → ⎋ list[str] (профили в порядке SoT)
##           ⚡ FileNotFoundError (SoT отсутствует) / KeyError (ключ отсутствует) — fail-fast
## @complexity O(1) — single YAML load + split
## @invariants
##   - Читает ТОЛЬКО platform-infra.yaml (SoT, инвариант 1)
##   - Пустой/отсутствующий COMPOSE_PROFILES → KeyError (никогда silent [] — AC-C3)
##   - Токены strip'ятся; пустые отбрасываются (защита от "a,,b")
def load_profiles(env: dict | None = None) -> list[str]:
    """Load COMPOSE_PROFILES from platform-infra.yaml (SoT) as list[str] (DevPlan 118 C3).

    ▶ ┌env┐ → ○ resolve_infra_path → ◇ None? ⚡ FileNotFoundError → ○ yaml.safe_load →
      → ◇ env_defaults.COMPOSE_PROFILES missing? ⚡ KeyError → ○ split(',') → ⎋ list[str]
    """
    infra_path = resolve_infra_path(env)
    if infra_path is None:
        raise FileNotFoundError(
            "[IMP:10][compose_profiles] platform-infra.yaml not found — run `make generate-platform-env` "
            "(SoT: core/platform-infra.yaml env_defaults, DevPlan 118 C3)"
        )
    with open(infra_path) as f:
        data = yaml.safe_load(f) or {}
    profiles_raw = (data or {}).get("env_defaults", {}).get("COMPOSE_PROFILES")
    if not profiles_raw:
        raise KeyError(
            f"[IMP:10][compose_profiles] env_defaults.COMPOSE_PROFILES missing in {infra_path} — "
            "run `make generate-platform-env` (DevPlan 116 T2, U-02)."
        )
    profiles = [token.strip() for token in str(profiles_raw).split(",") if token.strip()]
    logger.info("[IMP:9][compose_profiles] COMPOSE_PROFILES from SoT: %d profile(s)", len(profiles))
    return profiles


# endregion FUNC_load_profiles
