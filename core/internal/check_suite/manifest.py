"""
# GREP_SUMMARY: check-suite, manifest, load, validate, parse, list-checks, schema-v1, SoT
# STRUCTURE: ▶ load_manifest ┌root/core/check-suite.yaml┐ → ◇ validate_manifest (schema v1) → ◇ parse_checks → ◇ list_checks ┌gate_mode?┐ → ⎋ list[CheckSpec]
# region MODULE_CONTRACT
## @purpose  SoT-манифест core/check-suite.yaml: загрузка (load_manifest), структурная
##           валидация schema v1 (validate_manifest), парсинг в CheckSpec (parse_checks),
##           фильтрация наборов по режиму (list_checks). DevPlan 170 W3 — извлечено
##           из монолита core/internal/check_suite.py.
## @scope    core/internal/check_suite/manifest.py — stdlib-only (yaml — lazy). Потребители:
##           diagnostic.py, gate.py, single.py, __init__.py (CLI), consistency-гейты.
## @invariants
##   - Манифест — единственный источник состава проверок; validate_manifest возвращает список
##     ошибок (пустой = валидно); executor'ы fail-fast до запуска
##   - id: ^[a-z0-9]+([-_][a-z0-9]+)*$ и уникален; tier ∈ {fix,static,pytest}; timeout > 0
##   - gate_modes ⊆ {fast,full,ci-docker}; для каждого режима команда резолвится (cmd|cmds)
##   - junit-пути уникальны В ПРЕДЕЛАХ КАЖДОГО gate-режима (predeploy/predeploy-docker делят путь
##     намеренно — режимы не пересекаются)
## @rationale Выделение манифест-слоя — декомпозиция монолита (research-A §1): те же функции,
##            та же семантика; load_manifest поддерживает monkeypatch-контракт тестов через
##            пакетную атрибуцию (check_suite.load_manifest).
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TypedDict, cast

from core.internal.check_suite import PROJECT_ROOT, VALID_GATE_MODES, VALID_TIERS
from core.internal.check_suite.models import CheckSpec

logger = logging.getLogger(__name__)


# region MANIFEST_TYPES
# TypedDict-граница YAML-манифеста (W11-G4): schema v1-структура; ключи опциональны —
# load_manifest не выполняет структурную валидацию (её делает validate_manifest/consistency-гейт).
# Имя Manifest ПУБЛИЧНОЕ (U-07): diagnostic.py импортирует его кросс-модульно для аннотации.
class _ManifestCheck(TypedDict, total=False):
    """Одна запись checks[] в core/check-suite.yaml (schema v1)."""

    id: str
    tier: str
    timeout: int
    gate_modes: list[str]
    cmd: str
    cmds: dict[str, str]
    diagnostic: bool
    xdist: bool
    sequential: bool
    allow_no_tests: bool
    non_blocking: bool
    junit: str
    project_filter: bool
    docker: bool
    repair: str


class Manifest(TypedDict, total=False):
    """Корень core/check-suite.yaml (schema v1) — публичное имя для кросс-модульных аннотаций."""

    version: int
    checks: list[_ManifestCheck]


# endregion MANIFEST_TYPES

# region MANIFEST_LOAD


# region FUNC_load_manifest
## @purpose  Загрузка SoT-манифеста core/check-suite.yaml (или явного пути для тестов).
## @io       ⇥ root: Path — корень проекта → ⎋ dict (распарсенный YAML)
## @complexity O(1) — чтение одного файла
def load_manifest(root: Path | None = None) -> Manifest:
    """Load the check-suite SoT manifest from disk."""
    root = root or PROJECT_ROOT
    manifest_path = root / "core" / "check-suite.yaml"
    if not manifest_path.is_file():
        msg = f"check-suite manifest not found: {manifest_path}"
        raise FileNotFoundError(msg)
    import yaml

    try:
        with Path(manifest_path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)  # pyright: ignore[reportAny] W11-G4: pyyaml без stubs → Any (граница untyped-библиотеки)
    except yaml.YAMLError as exc:
        # T3.6 (DevPlan 116 B4): бизнес-ошибки → иерархия PlatformError (exit 3), НЕ bare ValueError
        from core.internal.shared.exceptions import ConfigParseError

        msg = f"check-suite manifest YAML parse error: {exc}"
        raise ConfigParseError(msg) from exc
    if not isinstance(data, dict):
        from core.internal.shared.exceptions import ConfigParseError

        msg = f"check-suite manifest must be a mapping: {manifest_path}"
        raise ConfigParseError(msg)
    logger.info("[IMP:7][load_manifest][io] Loaded manifest from %s", manifest_path)
    # W11-G4: двухшаговый cast (dict → object → Manifest) — pyright запрещает прямой
    # dict[Unknown, Unknown] → TypedDict (reportInvalidCast); структура — schema v1
    return cast(Manifest, cast(object, data))


# endregion FUNC_load_manifest


# region FUNC_validate_manifest
## @purpose  Структурная валидация манифеста (схема v1): id-формат/уникальность,
##           tier, timeout, gate_modes, cmd|cmds-покрытие, junit-уникальность ПО РЕЖИМАМ.
##           Возвращает список ошибок (пустой = валидно) — consistency-гейт и executor
##           используют одну и ту же функцию (fail-fast до запуска).
## @io       ⇥ manifest: dict → ⎋ list[str] ошибок (пустой = валидно)
## @complexity O(C) где C = число чеков
## @invariants
##   - id: ^[a-z0-9]+([-_][a-z0-9]+)*$ (kebab ИЛИ snake — static_audit каноничен, DevPlan §3.1)
##     и уникален
##   - tier ∈ {fix, static, pytest}; timeout > 0
##   - gate_modes ⊆ {fast, full, ci-docker} (отсутствие = диагностика-only)
##   - Для каждого gate-режима из gate_modes команда резолвится (cmd ИЛИ cmds[mode])
##   - junit-пути уникальны В ПРЕДЕЛАХ КАЖДОГО gate-режима (predeploy и predeploy-docker
##     делят tests/report-predeploy.xml намеренно — режимы fast/full vs ci-docker не пересекаются)
def validate_manifest(manifest: Manifest) -> list[str]:
    """Validate manifest schema v1; returns list of errors (empty = valid)."""
    errors: list[str] = []
    checks = manifest.get("checks", [])
    if not isinstance(checks, list) or not checks:
        return ["manifest.checks must be a non-empty list"]

    seen_ids: set[str] = set()
    # junit-пути по каждому gate-режиму (раздельные режимы могут делить путь — predeploy/predeploy-docker)
    junit_by_mode: dict[str, dict[str, str]] = {m: {} for m in VALID_GATE_MODES}
    for i, c in enumerate(checks):
        prefix = f"checks[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        cid = c.get("id", "")
        if not re.fullmatch(r"[a-z0-9]+([-_][a-z0-9]+)*", cid):
            errors.append(f"{prefix}: id={cid!r} не kebab/snake-case")
        if cid in seen_ids:
            errors.append(f"{prefix}: duplicate id={cid!r}")
        seen_ids.add(cid)

        tier = c.get("tier")
        if tier not in VALID_TIERS:
            errors.append(f"{prefix} ({cid}): tier={tier!r} ∉ {VALID_TIERS}")
        timeout = c.get("timeout", 0)
        if not isinstance(timeout, int) or timeout <= 0:
            errors.append(f"{prefix} ({cid}): timeout={timeout!r} must be int > 0")

        gate_modes = c.get("gate_modes", [])
        if not isinstance(gate_modes, list) or not set(gate_modes).issubset(set(VALID_GATE_MODES)):
            errors.append(f"{prefix} ({cid}): gate_modes={gate_modes!r} ⊄ {VALID_GATE_MODES}")

        cmd = c.get("cmd")
        cmds = c.get("cmds")
        if cmd is None and cmds is None:
            errors.append(f"{prefix} ({cid}): neither cmd nor cmds present")
        errors.extend(
            f"{prefix} ({cid}): команда для gate-режима {mode!r} не резолвится (cmd|cmds)"
            for mode in gate_modes
            if cmd is None and (not isinstance(cmds, dict) or mode not in cmds)
        )

        junit = c.get("junit")
        if junit:
            for mode in gate_modes:
                if junit in junit_by_mode[mode]:
                    errors.append(
                        f"{prefix} ({cid}): duplicate junit path {junit!r} в режиме {mode!r} "
                        f"(уже у {junit_by_mode[mode][junit]!r})"
                    )
                junit_by_mode[mode][junit] = cid

    logger.info("[IMP:8][validate_manifest][check] %d check(s), %d error(s)", len(checks), len(errors))
    return errors


# endregion FUNC_validate_manifest


# region FUNC_parse_checks
## @purpose  Манифест → список CheckSpec с дефолтами схемы v1: diagnostic=True (tier
##           fix/static/pytest), xdist=True (tier pytest; явные false — в манифесте).
## @io       ⇥ manifest: dict → ⎋ list[CheckSpec] (порядок манифеста = канонический порядок gate)
## @complexity O(C)
def parse_checks(manifest: Manifest) -> list[CheckSpec]:
    """Parse manifest checks into CheckSpec dataclasses with schema defaults."""
    specs: list[CheckSpec] = []
    for c in manifest.get("checks", []):
        if not isinstance(c, dict):
            continue
        tier = c.get("tier", "static")
        specs.append(
            CheckSpec(
                id=c.get("id", ""),
                tier=tier,
                timeout=c.get("timeout", 60),
                gate_modes=list(c.get("gate_modes", [])),
                diagnostic=c.get("diagnostic", True),
                xdist=c.get("xdist", tier == "pytest"),
                sequential=c.get("sequential", False),
                allow_no_tests=c.get("allow_no_tests", False),
                non_blocking=c.get("non_blocking", False),
                junit=c.get("junit"),
                project_filter=c.get("project_filter", False),
                docker=c.get("docker", False),
                cmd=c.get("cmd"),
                cmds=c.get("cmds"),
            )
        )
    return specs


# endregion FUNC_parse_checks


# region FUNC_list_checks
## @purpose  Фильтрация чеков: gate_mode=None → диагностический набор (diagnostic=True);
##           gate_mode=fast|full|ci-docker → чек с данным режимом (порядок манифеста).
## @io       ⇥ manifest: dict, gate_mode: str | None → ⎋ list[CheckSpec]
## @complexity O(C)
def list_checks(manifest: Manifest, gate_mode: str | None = None) -> list[CheckSpec]:
    """Return checks: diagnostic set (gate_mode=None) or checks for a gate mode."""
    specs = parse_checks(manifest)
    if gate_mode is None:
        return [s for s in specs if s.diagnostic]
    return [s for s in specs if gate_mode in s.gate_modes]


# endregion FUNC_list_checks

# endregion MANIFEST_LOAD
