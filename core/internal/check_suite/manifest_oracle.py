#!/usr/bin/env python3
# GREP_SUMMARY: manifest-oracle, semantic-validator, secrets-manifest, independent, no-generator-import, consumers-parity, REF-0107
# STRUCTURE: ▶ _load_yaml ×3 (definitions + generated + module.yaml glob) → ○ oracle_secrets_manifest
#            → ⊕ O1 names-parity · O2 tier/source-parity · O3 consumers-parity · O4 structural
#            → ⎋ list[violation] (пусто = fresh по семантике, независимо от git-состояния)
# region MODULE_CONTRACT
## @purpose  НЕЗАВИСИМЫЙ semantic-validator манифеста секретов (REF-0107 problem 6):
##           «парити судится тем же генератором» — существующий check-manifests запускает
##           генератор с --check (генератор судит сам себя), а pytest-вариант делает вакуумный
##           git-diff на свежем checkout. Oracle валидирует СЕМАНТИКУ generated-манифеста
##           против авторитетных источников БЕЗ импорта/исполнения генератора: собственные
##           yaml-ридеры, собственный расчёт consumers из module.yaml#env_requires.
## @scope    core/internal/check_suite/manifest_oracle.py — только G1 (secrets-manifest).
##           Потребители: tests/gates (freshness-семантика), unit-тесты (негативы).
## @invariants
##   - НИКАКОГО импорта core.internal.scripts.generate_secrets_manifest (иначе теряется
##     независимость вердикта — та же логика не может быть и судьёй, и подсудимым)
##   - O1: имена definitions ↔ generated совпадают в ОБЕ стороны (missing = stale, extra = drift)
##   - O2: tier/source каждой записи совпадают с definitions
##   - O3: consumers[name] == {модули, чей module.yaml#env_requires содержит name} (строки и
##     {name: ...}-формы), sorted-сравнение
##   - O4: структурные инварианты схемы v1: O4a closed-set tier/source; O4b ci-secret ⇒
##     consumers пуст; O4c generated∧autogen ⇒ gen_command (provisioner генерирует на VPS
##     через API — локальной команды нет по схеме secret-definitions @invariants)
##   - Вердикт = список человекочитаемых violation-строк; пустой = ok
##   - _BASELINE_VIOLATIONS — накопленные нарушения живого дерева, вскрытые первым честным
##     прогоном (REF-0107): явный allowlist {(name, invariant)} с TRAP[DEBT] и Rev-датой
##     (буфер Волна 4); каждое срабатывание логируется IMP:8 — тихого прощения нет
## @rationale Независимая реализация пересечения двух каналов проверки: git-diff ловит только
##            незакоммиченные правки, генератор --check доверен автору. Oracle читает факты
##            (YAML) и проверяет семантические инварианты — свежий checkout со stale
##            закоммиченным манифестом им ПОЙМАН (git-diff там вакуумно зелёный).
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)

_DEFINITIONS_REL = Path("core") / "secret-definitions.yaml"
_MANIFEST_REL = Path("core") / "secrets-manifest.yaml"
_MODULES_DIR_REL = Path("core") / "modules"

_TIERS = frozenset({"required", "generated", "optional", "removed"})
_SOURCES = frozenset({"sops", "autogen", "ci-secret", "provisioner"})
# gen_command обязателен для локально-генерируемых секретов; provisioner генерирует ключи
# НА VPS через API (key_provisioner.py) — локальной команды нет по схеме (secret-definitions @invariants).
_GEN_COMMAND_SOURCES = frozenset({"autogen"})

# REF-0107 (2026-08-25): накопленные нарушения живого манифеста, вскрытые оракулом при первом
# честном прогоне («всплытие — это цель», DevPlan 11 риск #3). Буфер триажа — Волна 4.
# Формат: {(secret_name, invariant)} — запись = осознанный baseline, НЕ ослабление правила.
# ⚠️ TRAP[DEBT] · 2026-08-25 · MED · DEEPSEEK_API_KEY нарушает два документированных
# · инварианта схемы: source=ci-secret с непустым consumers=[litellm] И с ci_default
# · (заголовок secrets-manifest: «ci-secret секреты имеют consumers: []»); либо источник
# · переклассифицировать, либо заголовочный инвариант пересмотреть · Observed: oracle O4b/O4c
# · When: REF-0107 первый прогон · Impact: схема манифеста не самосогласованна
# · Rev: 2026-09-30 (Волна 4)
_BASELINE_VIOLATIONS: frozenset[tuple[str, str]] = frozenset({("DEEPSEEK_API_KEY", "O4b")})


# region FUNC_oracle_secrets_manifest
def oracle_secrets_manifest(
    repo_root: Path,
    *,
    definitions_path: Path | None = None,
    manifest_path: Path | None = None,
    modules_dir: Path | None = None,
) -> list[str]:
    """Семантическая сверка generated secrets-manifest с источниками (БЕЗ генератора).

    ▶ ┌repo_root┐ → ○ load definitions/generated/module-yamls → ⊕ O1..O4 violations → ⎋ list[str]

    ## @purpose  Единственный вход oracle: вернуть нарушения семантических инвариантов O1-O4.
    ## @io       ⇥ repo_root: корень репозитория (или fixture-дерево);
    ##             *_path/modules_dir: DI для негативных тестов на tmp-копиях
    ##           ⎋ list[str] — пусто = манифест семантически свеж
    ## @complexity O(S + M×E) где S = секреты, M = модули, E = env_requires на модуль
    ## @invariants  Генератор НЕ импортируется (см. MODULE_CONTRACT)
    """
    root = Path(repo_root)
    defs_path = definitions_path if definitions_path is not None else root / _DEFINITIONS_REL
    man_path = manifest_path if manifest_path is not None else root / _MANIFEST_REL
    mods_dir = modules_dir if modules_dir is not None else root / _MODULES_DIR_REL

    # W11 object-граница: yaml.safe_load → Any; типизация через cast (basedpyright strict)
    defs_doc = cast("dict[str, object]", yaml.safe_load(defs_path.read_text(encoding="utf-8")) or {})
    man_doc = cast("dict[str, object]", yaml.safe_load(man_path.read_text(encoding="utf-8")) or {})
    defs: dict[str, dict[str, object]] = {
        str(s["name"]): s
        for s in cast("list[object]", defs_doc.get("secrets", []))
        if isinstance(s, dict) and "name" in s
    }
    gen: dict[str, dict[str, object]] = {
        str(s["name"]): s
        for s in cast("list[object]", man_doc.get("secrets", []))
        if isinstance(s, dict) and "name" in s
    }

    # Реестр потребителей: name секрета → sorted[модули] из module.yaml#env_requires
    expected_consumers: dict[str, list[str]] = {}
    for mod_yaml in sorted(Path(mods_dir).glob("*/module.yaml")):
        module_name = mod_yaml.parent.name
        doc = cast("dict[str, object]", yaml.safe_load(mod_yaml.read_text(encoding="utf-8")) or {})
        for entry in doc.get("env_requires", []) or []:
            required_name: str | None = None
            if isinstance(entry, str):
                required_name = entry
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                required_name = cast("str", entry["name"])
            else:
                continue
            expected_consumers.setdefault(cast("str", required_name), []).append(module_name)

    violations: list[str] = []

    # O1 — полнота в обе стороны
    violations += [
        f"O1: '{name}' есть в secret-definitions.yaml, отсутствует в secrets-manifest (stale)"
        for name in sorted(defs.keys() - gen.keys())
    ]
    violations += [
        f"O1: '{name}' есть в secrets-manifest, но незарегистрирован в definitions (drift)"
        for name in sorted(gen.keys() - defs.keys())
    ]

    # O2 — tier/source паритет
    for name in sorted(defs.keys() & gen.keys()):
        for field in ("tier", "source"):
            if defs[name].get(field) != gen[name].get(field):
                violations.append(
                    f"O2: '{name}'.{field}: definitions={defs[name].get(field)!r} ≠ manifest={gen[name].get(field)!r}"
                )

    # O3 — consumers-parity (собственный расчёт из module.yaml, не из генератора)
    for name in sorted(gen.keys()):
        actual = sorted(str(c) for c in gen[name].get("consumers", []) or [])
        expected = sorted(expected_consumers.get(name, []))
        if actual != expected:
            violations.append(f"O3: '{name}'.consumers: manifest={actual} ≠ вычислено из module.yaml={expected}")

    # O4 — структурные инварианты схемы v1
    # O4a: closed-set tier/source; O4b: ci-secret ⇒ consumers пуст; O4c: локальная генерация ⇒ gen_command.
    for name, entry in sorted(gen.items()):
        if entry.get("tier") not in _TIERS:
            violations.append(f"O4a: '{name}'.tier={entry.get('tier')!r} вне закрытого множества {sorted(_TIERS)}")
        if entry.get("source") not in _SOURCES:
            violations.append(
                f"O4a: '{name}'.source={entry.get('source')!r} вне закрытого множества {sorted(_SOURCES)}"
            )
        if entry.get("source") == "ci-secret" and entry.get("consumers"):
            if (name, "O4b") in _BASELINE_VIOLATIONS:
                logger.warning(
                    "[IMP:8][manifest_oracle][baseline] '%s' O4b (ci-secret с consumers) — baseline до Волны 4", name
                )
            else:
                violations.append(f"O4b: '{name}' source=ci-secret с непустым consumers")
        if (
            entry.get("tier") == "generated"
            and entry.get("source") in _GEN_COMMAND_SOURCES
            and not entry.get("gen_command")
        ):
            violations.append(f"O4c: '{name}' tier=generated∧source={entry.get('source')} без gen_command")

    logger.info("[IMP:9][manifest_oracle] %d secret(s), %d violation(s)", len(gen), len(violations))
    return violations


# endregion FUNC_oracle_secrets_manifest
