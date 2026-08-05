#!/usr/bin/env python3
# GREP_SUMMARY: practices-manifest, load-manifest, PracticeCheck, PracticesManifest, checks_for, l1_checks, schema-validate, fail-fast, Draft7
# STRUCTURE: ▶ load_manifest(path) → ◇ schema_validator.validate_yaml_against_schema (Draft7) → ◇ errors? → ⚡ ConfigValidationError (exit 4) → ⊕ dataclasses (PracticeCheck/PracticesManifest, frozen) → ⎋ канон
# region MODULE_CONTRACT
## @purpose  Чтение + валидация канона практик (DevPlan 137 §2.1A): load_manifest() с fail-fast
##           через shared/schema_validator (единственная Draft7Validator-точка, DevPlan 116 B6 T5).
##           Frozen-dataclasses PracticeCheck/PracticesManifest — типизированный доступ к канону
##           (уровень, языки, каналы, класс блокировки, auto_fix, timeout). Автопромоута НЕТ
##           (решение пользователя 2026-08-05) — константа K не существует, пороги из канона.
## @scope    Потребители: generators.py, check_project.py, sync_practices.py, set_practices.py,
##           maturity.py (пороги), escalator.py, гейт test_gate_practices_manifest.
## @invariants
##   - Структурная ошибка канона → ConfigValidationError (exit 4), НЕ bare ValueError
##   - Поля dataclass-ов frozen (неизменяемый канон); `class` YAML-ключ → атрибут `klass` (keyword)
##   - checks_for(check_id, language, level, channel) — фильтр по всем 4 измерениям
##   - l1_checks() — L1-проверки (безопасность платформы, исполняются при ЛЮБОМ уровне)
##   - Пороги зрелости читаются из канона (НЕ хардкод): maturity_thresholds()
## @rationale Единая точка валидации канона + типизированный доступ вместо сырых dict —
##            fail-fast на старте (сломанный канон виден сразу, не в середине прогона).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.schema_validator import validate_yaml_against_schema

logger = logging.getLogger(__name__)

# ── Default paths (рядом с модулем) ──
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "practices_manifest.yaml"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "manifest_schema.json"

# ── Канонические имена каналов/классов (readability, НЕ хардкод порогов) ──
CHANNEL_LOCAL = "local"
CHANNEL_CI = "ci"
CHANNEL_VERIFY = "verify"
KLASS_L1 = "L1"
KLASS_L2 = "L2"
KLASS_L3 = "L3"

# ── Маппинг type (ai-platform.yaml) → языки канона (DevPlan 137 §3.2) ──
LANGUAGE_FOR_TYPE: dict[str, tuple[str, ...]] = {
    "backend": ("python",),
    "frontend": ("typescript", "react"),
    "python": ("python",),
    "typescript": ("typescript", "react"),
    "react": ("typescript", "react"),
    "sh": ("sh",),
}


# region FUNC_PracticeCheck
## @purpose  Frozen-dataclass одной проверки канона (DevPlan 137 §2.1A).
## @io       ⇥ id/level/languages/channel/klass/auto_fix/timeout_sec → ⎋ PracticeCheck
## @complexity O(1)
@dataclass(frozen=True)
class PracticeCheck:
    """Одна проверка канона практик (frozen — неизменяемый SoT)."""

    id: str
    level: str
    languages: tuple[str, ...]
    channel: tuple[str, ...]
    klass: str
    auto_fix: bool
    timeout_sec: int

    ## @purpose  Применима ли проверка к языку проекта.
    ## @io       ⇥ language: str → ⎋ bool — "all" в languages ∨ language ∈ languages
    ## @complexity O(L) где L = len(languages)
    def applies_to(self, language: str) -> bool:
        """True если проверка применяется к языку (languages содержит "all" или language)."""
        return "all" in self.languages or language in self.languages

    ## @purpose  Исполняется ли проверка в канале.
    ## @io       ⇥ channel: str → ⎋ bool
    ## @complexity O(C) где C = len(channel)
    def runs_in(self, channel: str) -> bool:
        """True если проверка исполняется в канале (local|ci|verify)."""
        return channel in self.channel


# endregion FUNC_PracticeCheck


# region FUNC_PracticesManifest
## @purpose  Frozen-dataclass канона практик: version + maturity + checks + pins + allowlist.
## @io       ⇥ version/maturity/pins/allowed_external_networks/checks → ⎋ PracticesManifest
## @complexity O(C) где C = число проверок
@dataclass(frozen=True)
class PracticesManifest:
    """Канон практик (frozen — неизменяемый SoT)."""

    version: int
    maturity: dict[str, int]
    pins: dict[str, str]
    allowed_external_networks: tuple[str, ...]
    checks: tuple[PracticeCheck, ...]

    ## @purpose  Индекс проверок по id (для точечного доступа).
    ## @io       ⎋ dict[str, PracticeCheck]
    ## @complexity O(C)
    def by_id(self) -> dict[str, PracticeCheck]:
        """Index checks by id (kebab-case → PracticeCheck)."""
        return {c.id: c for c in self.checks}


# endregion FUNC_PracticesManifest


# region FUNC_load_manifest
## @purpose  Загрузить + провалидировать канон практик (fail-fast). Валидация через
##           schema_validator.validate_yaml_against_schema (Draft7, единая точка).
##           Структурная ошибка → ConfigValidationError (exit 4 — контракт main()).
## @io       ⇥ path: Path | None (default = core/internal/practices/practices_manifest.yaml)
##           ⎋ PracticesManifest — frozen-канон
##           ⚡ ConfigValidationError — файл отсутствует (exit 2 семантика: ConfigNotFoundError)
##              ИЛИ структурная ошибка схемы (exit 4)
## @complexity O(S * C) где S = размер схемы, C = размер канона (Draft7)
## @invariants
##   - Пустой список ошибок схемы ⇔ канон валиден (exit 0 контракт)
##   - ALL violations агрегируются (iter_errors) — первая-ошибка НЕ скрывает остальные
##   - Не-dict корень канона (YAML не-объект) → ConfigValidationError
def load_manifest(path: Path | None = None) -> PracticesManifest:
    """Load + validate practices manifest (Draft7 via shared/schema_validator, fail-fast)."""
    manifest_path = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    schema_path = manifest_path.parent / "manifest_schema.json"
    if not schema_path.is_file():
        schema_path = DEFAULT_SCHEMA_PATH

    if not manifest_path.is_file():
        from core.internal.shared.exceptions import ConfigNotFoundError

        raise ConfigNotFoundError(f"Practices manifest not found: {manifest_path}")

    logger.info("[IMP:7][practices_manifest][load] Validating %s against %s", manifest_path, schema_path)
    try:
        errors = validate_yaml_against_schema(manifest_path, schema_path)
    except yaml.YAMLError as exc:
        # Синтаксическая ошибка YAML канона → ConfigParseError (exit 3), не тихий fallback
        from core.internal.shared.exceptions import ConfigParseError

        raise ConfigParseError(f"Practices manifest is not valid YAML: {exc}") from exc
    if errors:
        detail = "\n".join(errors)
        logger.error("[IMP:10][practices_manifest][load] Manifest schema validation FAILED:\n%s", detail)
        raise ConfigValidationError(
            f"Practices manifest is invalid (schema Draft7):\n{detail}\n"
            f"Repair: fix core/internal/practices/practices_manifest.yaml"
        )

    data = _read_manifest(manifest_path)
    checks = tuple(
        PracticeCheck(
            id=str(entry["id"]),
            level=str(entry["level"]),
            languages=tuple(str(lang) for lang in entry["languages"]),
            channel=tuple(str(ch) for ch in entry["channel"]),
            klass=str(entry["class"]),
            auto_fix=bool(entry["auto_fix"]),
            timeout_sec=int(entry["timeout_sec"]),
        )
        for entry in data["checks"]
    )
    manifest = PracticesManifest(
        version=int(data["version"]),
        maturity={str(k): int(v) for k, v in data["maturity"].items()},
        pins={str(k): str(v) for k, v in data["pins"].items()},
        allowed_external_networks=tuple(str(n) for n in data["allowed_external_networks"]),
        checks=checks,
    )
    logger.info(
        "[IMP:9][practices_manifest][load] Manifest v%d loaded: %d checks, %d pins, %d networks",
        manifest.version,
        len(manifest.checks),
        len(manifest.pins),
        len(manifest.allowed_external_networks),
    )
    return manifest


# endregion FUNC_load_manifest


# region FUNC__read_manifest
## @purpose  YAML-парсинг канона (после успешной schema-валидации — парсинг гарантированно
##           даёт dict с требуемыми ключами).
## @io       ⇥ path: Path → ⎋ dict[str, Any]
## @complexity O(C)
def _read_manifest(path: Path) -> dict[str, Any]:
    """Parse manifest YAML → dict (safe_load; schema already validated structure)."""
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigValidationError(f"Practices manifest root must be an object: {path}")
    return data


# endregion FUNC__read_manifest


# region FUNC_checks_for
## @purpose  Фильтр проверок по id + language + level + channel (DevPlan 137 §2.1A).
##           Возвращает кортеж (обычно 0..1 элемент — id уникален) проверок, удовлетворяющих
##           ВСЕМ измерениям. Level: "baseline"|"full"|"any" — "any" = без фильтра по уровню.
## @io       ⇥ check_id: str, language: str, level: str, channel: str → ⎋ tuple[PracticeCheck, ...]
## @complexity O(C)
## @invariants
##   - language "all"-проверки применяются к любому языку (applies_to)
##   - level="any" → фильтр уровня пропускается (для L1-селекции независимо от уровня)
def checks_for(
    check_id: str,
    *,
    language: str,
    level: str = "any",
    channel: str = CHANNEL_LOCAL,
) -> tuple[PracticeCheck, ...]:
    """Return checks matching id + language + level + channel (empty tuple if none)."""
    manifest = load_manifest()
    found = manifest.by_id().get(check_id)
    if found is None:
        return ()
    if not found.applies_to(language) or not found.runs_in(channel):
        return ()
    if level != "any" and found.level != level:
        return ()
    return (found,)


# endregion FUNC_checks_for


# region FUNC_applicable_checks
## @purpose  Все проверки канона, применимые к проекту: language × level × channel.
##           level: "baseline" — только baseline-проверки; "full" — baseline + full;
##           "any" — все. channel: локальный канал K1 ("local").
## @io       ⇥ language: str, level: str, channel: str → ⎋ tuple[PracticeCheck, ...]
## @complexity O(C)
def applicable_checks(
    *,
    language: str,
    level: str = "baseline",
    channel: str = CHANNEL_LOCAL,
) -> tuple[PracticeCheck, ...]:
    """Return ALL checks applicable to project (language × level × channel)."""
    manifest = load_manifest()
    result: list[PracticeCheck] = []
    for check in manifest.checks:
        if not check.applies_to(language) or not check.runs_in(channel):
            continue
        if level == "baseline" and check.level != "baseline":
            continue
        if level == "full" and check.level not in ("baseline", "full"):
            continue
        result.append(check)
    return tuple(result)


# endregion FUNC_applicable_checks


# region FUNC_l1_checks
## @purpose  L1-проверки канона (безопасность платформы) — исполняются ПРИ ЛЮБОМ уровне
##           и состоянии (DevPlan 137 §3.1 п.4, §4.5). Возвращает все L1 независимо от
##           channel (channel-фильтр применяет вызывающий: verify на VPS vs local).
## @io       ⎋ tuple[PracticeCheck, ...]
## @complexity O(C)
def l1_checks() -> tuple[PracticeCheck, ...]:
    """Return ALL L1-class checks (always executed — platform security)."""
    manifest = load_manifest()
    return tuple(c for c in manifest.checks if c.klass == KLASS_L1)


# endregion FUNC_l1_checks


# region FUNC_maturity_thresholds
## @purpose  Пороги зрелости из канона (НЕ хардкод — DevPlan 137 §5 W1 п.3).
##           Константы MATURITY_AGE_DAYS_PROPOSE / MATURITY_CODE_FILES_PROPOSE ниже —
##           зеркала канона для гейта паритета (гейт сверяет канон == 30/50).
## @io       ⇥ manifest: PracticesManifest | None → ⎋ dict[str, int] {age_days_propose, code_files_propose}
## @complexity O(1)
def maturity_thresholds(manifest: PracticesManifest | None = None) -> dict[str, int]:
    """Return maturity thresholds from canon (age_days_propose, code_files_propose)."""
    m = manifest if manifest is not None else load_manifest()
    return m.maturity


# endregion FUNC_maturity_thresholds


# ── Зеркала порогов (для гейта паритета; реальный источник — канон) ──
MATURITY_AGE_DAYS_PROPOSE: int = 30
MATURITY_CODE_FILES_PROPOSE: int = 50
# Автопромоута НЕТ (решение пользователя 2026-08-05): константа K (счётчик автопромоута) НЕ существует.
