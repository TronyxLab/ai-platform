#!/usr/bin/env python3
# GREP_SUMMARY: generate_help, make-help, two-role, scenarios, registry, visibility, entrypoint-manifest, internal-verbs
# STRUCTURE: ▶ load manifest → ◇ load_verb_map (make_target → entry) → ◇ render_scenarios (public, 7 групп) → ◇ render_registry (все, internal-пометки) → ⊕ main (--mode) → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Двухролевой генератор `make help` (План 175 W1.3): читает
##           core/entrypoint-manifest.yaml (verb, operation_ru, signature, description,
##           visibility) + секцию `scenarios:` и выводит help для двух ролей:
##           человек (scenarios — 7 сценариев, только public-глаголы) и полный реестр
##           (registry — все глаголы с internal-пометками).
## @scope    Вызывается из makefiles/helpers.mk (`help:` → --mode scenarios,
##           `help-all:` → --mode registry). Standalone-скрипт (yaml-only, без core.* импортов —
##           контракт генераторов: make запускает скрипт ФАЙЛОМ без PYTHONPATH).
## @invariants
##   - visibility ∈ {public, internal}; отсутствие поля → default public
##   - render_scenarios выводит ТОЛЬКО public-глаголы; internal-глагол в scenarios → skip + warning
##   - render_registry выводит ВСЕ глаголы (make_target-записи) с пометкой [internal]/[public]
##   - Порядок scenarios — из манифеста (вставка-упорядоченная mapping); registry — сортировка
##     по имени глагола (детерминизм)
##   - Scenarios-секция — MANUAL (генератор манифеста её сохраняет verbatim, инвариант 11)
## @rationale Плоский help (grep '^## ' из makefiles) не различает роли и не знает visibility.
##            Единый SoT-реестр глаголов (entrypoint-manifest.yaml) — источник обоих выводов;
##            сценарии дают человеку навигацию, реестр — полную фактуру агентам.
## @changes  2026-08-16 | Created (План 175 W1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar, cast

import yaml

# region CONSTANTS

# Человекочитаемые заголовки сценариев (порядок = порядок в манифесте).
_SCENARIO_TITLES: dict[str, str] = {
    "stack": "СТЕК (запуск/остановка/статус)",
    "deploy": "ДЕПЛОЙ (проект/контекст/каналы)",
    "project": "ПРОЕКТЫ (создание/управление/практики)",
    "node": "НОДА (bootstrap/converge/verify/security)",
    "generate": "ГЕНЕРАЦИЯ (манифесты/мониторинг)",
    "quality": "ДИАГНОСТИКА И КАЧЕСТВО",
    "dev-dr": "DEV-ИНФРАСТРУКТУРА И DR",
}

_VISIBILITY_VALUES: frozenset[str] = frozenset({"public", "internal"})
_DEFAULT_VISIBILITY: str = "public"

# endregion CONSTANTS

# region TYPED_CONTRACTS

_ManifestData = dict[str, object]


# endregion TYPED_CONTRACTS


# region PUBLIC_API


# region FUNC_load_manifest
def load_manifest(path: str | Path) -> _ManifestData:
    """Load entrypoint-manifest.yaml → opaque mapping (empty dict if missing).

    ## @purpose  Чтение SoT-реестра глаголов. Missing file → {} (caller обрабатывает).
    ## @io       ⇥ path: путь к core/entrypoint-manifest.yaml → ⎋ dict
    ## @complexity O(1) — чтение одного файла
    ## @invariants — yaml.safe_load; None → {}
    """
    manifest_path = Path(path)
    if not manifest_path.is_file():
        print(f"[IMP:6][generate_help] Manifest not found at {manifest_path} — empty registry", file=sys.stderr)
        return {}
    with manifest_path.open(encoding="utf-8") as f:
        data = cast(_ManifestData | None, yaml.safe_load(f))
    if data is None:
        return {}
    print(f"[IMP:8][generate_help] Loaded manifest with {len(data)} top-level keys", file=sys.stderr)
    return data


# endregion FUNC_load_manifest


# region FUNC_load_verb_map
def load_verb_map(manifest: _ManifestData) -> dict[str, dict[str, object]]:
    """Build verb name → entry map from ALL manifest sections with make_target entries.

    ## @purpose  Единая точка join'а: имя глагола → {operation_ru, signature, description,
    ##            visibility}. Итерирует ВСЕ list-секции манифеста (bootstrap, deploy, …).
    ## @io       ⇥ manifest → ⎋ dict[str, dict]: verb → entry-поля (копия, без механизмов)
    ## @complexity O(S*E) — S секций, E записей
    ## @invariants
    ##   - make_target-записи без make_target пропускаются
    ##   - visibility валидируется: неожиданное значение → default public + warning
    ##   - Первая запись побеждает (дублей make_target быть не должно — гейт 118 G2)
    """
    verb_map: dict[str, dict[str, object]] = {}
    for entries in manifest.values():
        if not isinstance(entries, list):
            continue
        for entry in cast(list[object], entries):
            if not isinstance(entry, dict):
                continue
            target = entry.get("make_target")
            if not isinstance(target, str) or not target:
                continue
            if target in verb_map:
                continue
            visibility = entry.get("visibility", _DEFAULT_VISIBILITY)
            if visibility not in _VISIBILITY_VALUES:
                print(
                    f"[IMP:6][generate_help] Unknown visibility={visibility!r} for '{target}' — fallback public",
                    file=sys.stderr,
                )
                visibility = _DEFAULT_VISIBILITY
            verb_map[target] = {
                "operation_ru": entry.get("operation_ru") or entry.get("description") or "—",
                "signature": entry.get("signature") or f"make {target}",
                "visibility": visibility,
            }
    print(f"[IMP:9][generate_help] Verb map built — {len(verb_map)} verb(s)", file=sys.stderr)
    return verb_map


# endregion FUNC_load_verb_map


# region FUNC_render_scenarios
def render_scenarios(manifest: _ManifestData) -> str:
    """Render human help: 7 scenario groups, public verbs only.

    ## @purpose  Вывод для человека: сценарии из секции `scenarios:` манифеста.
    ##            Внутренние (visibility=internal) глаголы в сценарии → skip + warning
    ##            (R5-negative контракт: internal-имя не попадает в public-вывод).
    ## @io       ⇥ manifest → ⎋ str — markdown/plain-text help
    ## @complexity O(V + S) — V глаголов, S сценариев
    ## @invariants
    ##   - Только public-глаголы выводятся (internal → skip + warning)
    ##   - Глагол не в verb_map (нет записи) → '—' + warning (не RED)
    ##   - Порядок сценариев — из манифеста (mapping insertion order)
    """
    verb_map = load_verb_map(manifest)
    scenarios = manifest.get("scenarios", {})
    if not isinstance(scenarios, dict) or not scenarios:
        print("[IMP:6][generate_help] No 'scenarios:' section in manifest — empty help", file=sys.stderr)
        return "(no scenarios defined)"

    lines: list[str] = []
    lines.append("")
    lines.append("ДОСТУПНЫЕ КОМАНДЫ (make <verb>) — сгруппированы по сценариям")
    lines.append("Полный реестр (включая internal): make help-all")
    lines.append("")
    for group, verbs in scenarios.items():
        if not isinstance(verbs, list):
            continue
        title = _SCENARIO_TITLES.get(cast(str, group), cast(str, group).upper())
        lines.append("=" * 72)
        lines.append(f"  {title}")
        lines.append("=" * 72)
        for verb in verbs:
            if not isinstance(verb, str) or not verb:
                continue
            entry = verb_map.get(verb)
            if entry is None:
                lines.append(f"  make {verb:<30} —")
                print(f"[IMP:6][generate_help] Scenario verb '{verb}' not in manifest", file=sys.stderr)
                continue
            if entry["visibility"] == "internal":
                print(
                    f"[IMP:6][generate_help] Internal verb '{verb}' in scenarios → skipped (public surface)",
                    file=sys.stderr,
                )
                continue
            op = cast(str, entry["operation_ru"])
            lines.append(f"  make {verb:<30} {op}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# endregion FUNC_render_scenarios


# region FUNC_render_registry
def render_registry(manifest: _ManifestData) -> str:
    """Render full registry: all verbs sorted, with [public]/[internal] markers.

    ## @purpose  Полный реестр для агентов/операторов: каждый глагол с visibility-пометкой,
    ##            operation_ru и signature. Сортировка по имени (детерминизм).
    ## @io       ⇥ manifest → ⎋ str — таблица-подобный текст
    ## @complexity O(V log V) — сортировка
    ## @invariants
    ##   - ВСЕ make_target-глаголы выводятся (public + internal)
    ##   - Пометка [internal] для internal-глаголов (человек отличает скрытые)
    """
    verb_map = load_verb_map(manifest)
    lines: list[str] = []
    lines.append("")
    lines.append("ПОЛНЫЙ РЕЕСТР ГЛАГОЛОВ (make <verb>) — [internal] скрыты из `make help`")
    lines.append("")
    for verb in sorted(verb_map):
        entry = verb_map[verb]
        mark = "[internal]" if entry["visibility"] == "internal" else "[public] "
        op = cast(str, entry["operation_ru"])
        sig = cast(str, entry["signature"])
        lines.append(f"  {mark} make {verb:<28} {op}")
        lines.append(f"         {'':<9} {sig}")
    return "\n".join(lines).rstrip() + "\n"


# endregion FUNC_render_registry

# endregion PUBLIC_API


# region CLI


class _HelpArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    mode: ClassVar[str]
    manifest: ClassVar[str]


def main(argv: list[str] | None = None) -> int:
    """CLI: --mode scenarios (default) | registry → печать help → exit 0.

    ## @purpose  Точка входа make help / make help-all.
    ## @io       ⇥ argv → ⎋ exit 0 (печать на stdout; ошибки чтения — на stderr)
    ## @complexity O(V) — рендер одного режима
    ## @invariants — scenarios по умолчанию; registry — полный; exit 0 при любом рендере
    """
    parser = argparse.ArgumentParser(
        prog="generate_help.py",
        description="Two-role make help from entrypoint-manifest.yaml (План 175 W1)",
    )
    parser.add_argument(
        "--mode",
        choices=("scenarios", "registry"),
        default="scenarios",
        help="scenarios (default): человеко-читаемые сценарии (public only); registry: полный реестр",
    )
    parser.add_argument(
        "--manifest",
        default="core/entrypoint-manifest.yaml",
        help="Path to entrypoint-manifest.yaml (default: core/entrypoint-manifest.yaml)",
    )
    args = parser.parse_args(argv, namespace=_HelpArgs())

    manifest = load_manifest(args.manifest)
    if args.mode == "registry":
        print(render_registry(manifest))
    else:
        print(render_scenarios(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
