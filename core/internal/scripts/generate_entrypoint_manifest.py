#!/usr/bin/env python3
# GREP_SUMMARY: generate_entrypoint_manifest, extract_phony_targets, collect_gate_tests, merge, load_structural_sections, manifest-generator, CI, g3-cycle-break, static-phony-parsing, gates-map-compaction
# STRUCTURE: ▶ статический .PHONY-парсинг (Makefile+makefiles/*.mk, детерминированный — P-14) → ▶ pytest --collect-only —▸ gate tests → ◇ load_structural_sections (allowed_verbs/gates EXCLUDED — G3 cycle break) → ⊕ merge (replace allowed_verbs + gates-маппинг test_file→[ids], preserve rest) → ⎋ write YAML
# region MODULE_CONTRACT
## @purpose  Generator for entrypoint-manifest.yaml — extracts .PHONY targets from Makefile
##           via СТАТИЧЕСКИЙ парсинг (DevPlan 123 T2/P-14 — детерминированный, заменил make -np),
##           collects gate tests via pytest --collect-only, loads STRUCTURAL sections from existing manifest
##           (NEVER allowed_verbs or gates — G3 cycle break), merges by replacing allowed_verbs and gates
##           while preserving all other sections.
##           gates-секция эмитится КОМПАКТНОЙ формой {test_file: [test_ids]} (T3.3 compacton):
##           527 развёрнутых записей {id, test_file, description} схлопнуты в маппинг файл→список id —
##           description имел НОЛЬ потребителей (единственный читатель — мёртвый helper
##           _load_entrypoint_manifest_gate_make_targets в test_gate_workflow_consistency.py, 0 вызовов).
## @scope    Used by `make generate-manifests` (Wave 2 of DevPlan 051). Run as CLI.
## @invariants
##   - G3 CYCLE BREAK (DevPlan 090 T6): allowed_verbs and gates are NEVER loaded from existing manifest.
##     They come EXCLUSIVELY from Makefile .PHONY targets and pytest gate markers.
##   - load_structural_sections() explicitly excludes allowed_verbs and gates keys.
##   - PRIMARY extraction: СТАТИЧЕСКИЙ парсинг .PHONY-строк из Makefile + makefiles/*.mk —
##     детерминированный между машинами (P-14: make -np вывод отличался gmake 4.4.1 vs make 4.4 CI).
##     make -np остаётся только fallback при пустом статическом результате.
##   - system_exceptions filtered out: help, venv, pre-commit-*, test-*, gate-*,
##     _get_all_profiles (технический помощник parity-гейта, DevPlan 138 S3)
##   - gates-секция: {test_file: [test_ids]} — маппинг, НЕ список развёрнутых записей;
##     ключи и id сортируются (детерминизм byte-level --check и test_gate_yaml_deterministic_output).
##   - All other sections preserved verbatim from existing manifest
##   - Empty lists are written as [] in YAML (never null); пустой gates-маппинг — {}
## @rationale DevPlan 051 Wave 2: automated sync eliminates drift between Makefile targets and
##            entrypoint-manifest.yaml allowed_verbs, and between pytest gate markers and gates.
##            DevPlan 090 T6: Breaking the G3 cyclic dependency — the generator must NOT read its own
##            output (allowed_verbs/gates) from the manifest, because this creates a self-reinforcing
##            drift mask. If a target is deleted from Makefile but remains in YAML, the old value
##            would be perpetuated. Atomic generation requires each section from authoritative sources.
##            DevPlan 123 T2 (P-14): make -np был главным кандидатом недетерминизма G3 —
##            статический парсинг устраняет класс (воспроизведение: make 3.81/4.4.1 локально
##            дают одинаковый .PHONY-вывод, но CI-окружение отличалось; статика одинакова везде).
##            T3.3 gates-map compaction: description генерировался как
##            'Auto-discovered gate: {id}' — производная от id, 0 живых потребителей
##            (git grep: единственный читатель — мёртвый helper, не вызывается ни одним тестом);
##            развёрнутый test_file на каждый id дублировал ключ маппинга. Схлопывание
##            в {test_file: [ids]} сохраняет весь читаемый контракт (test_file→ids)
##            и сокращает gates-секцию 85KB → ~25KB (527 записей → 130 ключей + id).
## @see      core/entrypoint-manifest.yaml — target manifest file
## @changes 2026-07-22 | Created (DevPlan 051 Wave 2)
##           2026-07-30 | Added --check mode: byte-level comparison, exit 0/1, stderr diff
##           2026-07-30 | G3 CYCLE BREAK: load_structural_sections() replaces load_existing_manifest()
##                        in main(). allowed_verbs and gates NEVER read from manifest (DevPlan 090 T6).
##           2026-08-03 | DevPlan 123 T2 (P-14): статический .PHONY-парсинг → PRIMARY;
##                        make -np → fallback; diff в --check — полный (не 20 строк)
##           2026-08-05 | DevPlan 138 S3 (W2): SYSTEM_EXCEPTIONS += _get_all_profiles
##                        (технический помощник parity-гейта test_gate_profiles_parity,
##                        не каноническая операция; таргет жив в helpers.mk .PHONY)
##           2026-08-22 | T3.3 gates-map compaction: gates-секция {test_file: [test_ids]}
##                        вместо списка {id, test_file, description}; description удалён
##                        (0 потребителей); размер 85KB → ~25KB
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, TextIO, cast

import yaml

# Standalone CLI bootstrap: `python3 core/internal/scripts/<script>.py` (makefile)
# не имеет `core` пакета на sys.path — добавляем repo root (паттерн sync_requirements.py).
if __name__ == "__main__" or not __package__:
    _REPO_ROOT = Path(Path(Path(__file__).parent, "..", "..", "..")).resolve()
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from core.internal.scripts.generated_check import check_generated

# endregion IMPORTS

# region TYPED_CONTRACTS
# W11: yaml/generated manifest boundaries — no Any (reportExplicitAny=error).

# T3.3 gates-map compaction: gates-секция = {test_file: [test_ids]} — маппинг файл → список id.
# Развёрнутая запись {id, test_file, description} упразднена: description имел 0 потребителей,
# test_file дублировался на каждый id (527 записей → 130 ключей + id-списки).
_GatesMap = dict[str, list[str]]


# Existing manifest: opaque top-level mapping (structural sections preserved verbatim).
_ManifestData = dict[str, object]


# open() seam (DevPlan 167 D1): TextIOWrapper/StringIO удовлетворяют TextIO.
_OpenFn = Callable[..., TextIO]


# endregion TYPED_CONTRACTS

# region CONSTANTS

# Категорийное правило (DevPlan 171 W3.6): перечень системных исключений заменён
# категориями — стандартные служебные таргеты make (help/venv), префиксы
# (pre-commit-/test-/gate-/_), `_`-префиксные имена. Новый служебный таргет
# не требует правки генератора — достаточно попасть в категорию.
SYSTEM_EXCEPTIONS: set[str] = {
    "help",
    "help-all",  # План 175 W1.3 — полный реестр глаголов (пара к help)
    "venv",
}

# Canonical targets that bypass SYSTEM_PREFIXES filter
# These are registered in AGENTS.md and MUST appear in allowed_verbs
ALLOWED_PREFIX_EXCEPTIONS: set[str] = {
    "test-summary",
    # DevPlan 095: E2E pipeline tests target — test-* prefix but canonical verb
    # (requires NODE env + test-VPS; NOT part of make check or make gate — DevPlan 165)
    "test-node",
}

SYSTEM_PREFIXES: tuple[str, ...] = (
    "pre-commit-",
    "test-",
    "gate-",
    "_",  # DevPlan 171 W3.6: _-префиксные — автоматически (бывш. _get_all_profiles в перечне)
)


# 🧐 TRAP[DECISION] · 2026-08-14 · — · Локальные таймауты вместо SoT-импорта (standalone-скрипт) · Rejected: from core.internal.shared.timeouts import ... · Reason: make запускает скрипт ФАЙЛОМ (makefiles/manifest.mk:68) без PYTHONPATH — импорт core.internal даёт ModuleNotFoundError; standalone-контракт генераторов (как watchdog/cert_expiry_check) · Rev: переход запуска на `python3 -m core.internal.scripts.generate_entrypoint_manifest` → вернуть импорт SoT
# Значения = канон shared/timeouts.py (CONVERGE_DOCKER_TIMEOUT=30, SYSTEM_CMD_TIMEOUT=60) — дубль значений намеренный (см. TRAP выше).
_MAKE_DRYRUN_TIMEOUT: int = 30
_PYTEST_COLLECT_TIMEOUT: int = 60

logger = logging.getLogger(__name__)

# endregion CONSTANTS


# region PUBLIC_API


# region FUNC_extract_phony_targets
## @purpose  Извлечение .PHONY-таргетов из Makefile + makefiles/*.mk.
##           СТАТИЧЕСКИЙ парсинг — PRIMARY (детерминированный, DevPlan 123 T2/P-14):
##           make -np вывод зависел от версии/окружения make (gmake 4.4.1 локально vs
##           make 4.4 в ubuntu-latest CI) → check-manifests RED в CI при локально GREEN.
##           Статический парсинг одинаков на любой машине — устраняет класс навсегда.
## @io       ⇥ makefile_dir: path to directory with Makefile
##           ⇥ gmake_path: path to GNU make binary (аргумент сохранён для обратной совместимости
##           CLI; НЕ используется в primary-пути)
##           → ⎋ list[str]: sorted unique .PHONY target names
## @complexity O(F * L) где F = число makefiles, L = строк на файл
## @invariants
##   - PRIMARY: статический парсинг .PHONY-строк из Makefile + makefiles/*.mk (grep-паттерн)
##   - fallback: gmake -np --dry-run (только если статический парсинг вернул пусто)
##   - system_exceptions excluded from result
##   - Targets matching system_prefixes excluded from result
##   - Returns sorted, deduplicated list
##   - ⚠️ .PHONY-строки с переменными ($(VAR)) статическим парсингом НЕ раскрываются —
##     в Makefile+makefiles/*.mk таких нет (проверено 2026-08-03); при появлении —
##     гейт/тест должен поймать, а строка перейти в явный .PHONY-список
## @changes 2026-08-03 | DevPlan 123 T2 (P-14): статический парсинг → PRIMARY;
##            make -np → fallback (устранён недетерминизм версии/окружения make)
def extract_phony_targets(makefile_dir: str, gmake_path: str) -> list[str]:
    print(f"[IMP:7][extract_phony_targets] Extracting .PHONY targets from {makefile_dir}", file=sys.stderr)
    targets: list[str] = []

    # Strategy 1 (PRIMARY): статический парсинг .PHONY-строк — детерминированный (P-14).
    # Find all .PHONY: declarations and extract targets from Makefile + makefiles/*.mk
    makefile_root = Path(makefile_dir)
    phony_lines: list[str] = []
    for mk_file in sorted(makefile_root.glob("Makefile")) + sorted(makefile_root.glob("makefiles/*.mk")):
        if mk_file.is_file():
            try:
                content = mk_file.read_text()
                for line in content.splitlines():
                    stripped = line.strip()
                    if re.match(r"^\.PHONY\s*:", stripped):
                        phony_lines.append(stripped)
            except OSError:
                continue

    for line in phony_lines:
        # Remove .PHONY: prefix and split
        rest = re.sub(r"^\.PHONY\s*:\s*", "", line).strip()
        targets.extend(rest.split())

    if targets:
        print(
            f"[IMP:8][extract_phony_targets] static parsing parsed {len(targets)} raw targets from {len(phony_lines)} .PHONY lines",
            file=sys.stderr,
        )
    else:
        print(
            "[IMP:6][extract_phony_targets] static parsing returned 0 targets — falling back to gmake -np",
            file=sys.stderr,
        )

    # Strategy 2 (fallback): gmake -np --dry-run — только если статический парсинг пуст.
    # DevPlan 123 T2: make -np вывод недетерминирован между make 4.4.1 (Homebrew) и make 4.4
    # (ubuntu-latest CI) — больше НЕ используется как primary (P-14).
    if not targets:
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            result = subprocess.run(
                [gmake_path, "-np", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=_MAKE_DRYRUN_TIMEOUT,
                cwd=makefile_dir,
                check=False,
            )
            if result.returncode == 0:
                phony_match = re.search(r"^\.PHONY:(.*)", result.stdout, re.MULTILINE)
                if phony_match:
                    raw = phony_match.group(1).strip()
                    targets = raw.split()
                    print(
                        f"[IMP:8][extract_phony_targets] gmake -np parsed {len(targets)} raw targets", file=sys.stderr
                    )
                print(
                    f"[IMP:6][extract_phony_targets] gmake exit code {result.returncode}, stderr: {result.stderr[:200]}",
                    file=sys.stderr,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            print(f"[IMP:6][extract_phony_targets] gmake unavailable ({e})", file=sys.stderr)

    # Filter: exclude system_exceptions and system_prefixes (with exceptions)
    filtered: list[str] = []
    for t in targets:
        if t in SYSTEM_EXCEPTIONS:
            continue
        if t in ALLOWED_PREFIX_EXCEPTIONS:
            filtered.append(t)
            continue
        if any(t.startswith(prefix) for prefix in SYSTEM_PREFIXES):
            continue
        filtered.append(t)

    # Deduplicate and sort
    unique = sorted(set(filtered))
    print(
        f"[IMP:9][extract_phony_targets] Extracted {len(unique)} canonical .PHONY targets (filtered from {len(targets)})",
        file=sys.stderr,
    )
    return unique


def collect_gate_tests(tests_dir: str) -> _GatesMap:
    """Run pytest --collect-only -m gate -q to get gate test definitions (pytest 9.x XML-like output format).

    ## @purpose  Collect gate test definitions from pytest markup.
    ##            Returns {test_file: [test_ids]} — компактный маппинг (T3.3 compaction).
    ## @io       ⇥ tests_dir: path to tests/ directory
    ##           → ⎋ dict[str, list[str]]: test_file → sorted list of gate ids
    ## @complexity O(N log N) where N = number of gate test items collected (сортировка)
    ## @invariants
    ##   - Falls back to filesystem scan if pytest unavailable
    ##   - Gate ID derived from test function name (test_gate_X → X)
    ##   - test_file derived from module path relative to tests/
    ##   - description УПРАЗДНЁН (T3.3): имел 0 потребителей, был производной от id
    ##   - Keys (test_file) и id-списки сортируются — детерминизм byte-level --check
    ##     и test_gate_yaml_deterministic_output
    """
    print(f"[IMP:7][collect_gate_tests] Collecting gate tests from {tests_dir}", file=sys.stderr)
    gates: dict[str, list[str]] = {}

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-m", "gate", "-q", tests_dir],
            capture_output=True,
            text=True,
            timeout=_PYTEST_COLLECT_TIMEOUT,
            check=False,
        )

        if result.returncode == 0:
            # Parse pytest 9.x --collect-only XML-like format
            # Lines: <Module test_gate_X.py> → <Function test_gate_X>
            pytest_test_dir = Path(tests_dir).resolve()
            current_module: str | None = None
            for raw_line in result.stdout.splitlines():
                line = raw_line.strip()
                # Match <Module test_gate_xxx.py>
                module_match = re.match(r"<Module\s+(\S+?)>", line)
                if module_match:
                    current_module = module_match.group(1)
                    continue
                # Match <Function test_gate_xxx>
                func_match = re.match(r"<Function\s+(\S+)>", line)
                if func_match and current_module:
                    test_name = func_match.group(1)
                    # Derive test_file: module basename relative to tests_dir
                    # If current_module is just "test_gate_X.py" (no path), prepend tests_dir
                    test_file_path = Path(current_module)
                    if not test_file_path.is_absolute() and not test_file_path.parent.name:
                        # Relative path like "test_gate_X.py" — join with tests_dir
                        test_file = str(pytest_test_dir / current_module)
                    else:
                        test_file = current_module
                    gate_id = (
                        test_name.replace("test_gate_", "", 1) if test_name.startswith("test_gate_") else test_name
                    )
                    test_file_rel = (
                        os.path.relpath(test_file, tests_dir) if Path(test_file).is_absolute() else test_file
                    )
                    gates.setdefault(test_file_rel, []).append(gate_id)
            total_ids = sum(len(v) for v in gates.values())
            print(
                f"[IMP:8][collect_gate_tests] pytest collected {total_ids} gate tests in {len(gates)} files",
                file=sys.stderr,
            )
            print(
                f"[IMP:6][collect_gate_tests] pytest exit code {result.returncode}: {result.stderr[:300]}",
                file=sys.stderr,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"[IMP:6][collect_gate_tests] pytest unavailable ({e}), falling back to filesystem scan", file=sys.stderr)

    # Fallback: filesystem scan for test_gate_*.py files
    if not gates:
        # Determine gates directory: if tests_dir already points to gates/, use it directly
        tests_path = Path(tests_dir)
        gates_dir = tests_path if tests_path.name == "gates" and tests_path.is_dir() else tests_path / "gates"
        if gates_dir.is_dir():
            for f in sorted(gates_dir.glob("test_gate_*.py")):
                gate_id = f.stem.replace("test_gate_", "", 1)
                test_file_rel = str(f.relative_to(tests_path))
                gates.setdefault(test_file_rel, []).append(gate_id)
        print(
            f"[IMP:8][collect_gate_tests] filesystem scan found {sum(len(v) for v in gates.values())} gate tests",
            file=sys.stderr,
        )

    # Determinism: sort keys (test_file) and id-lists (T3.3 — маппинг {test_file: [ids]}).
    gates = {k: sorted(v) for k, v in sorted(gates.items())}

    print(
        f"[IMP:9][collect_gate_tests] Collected {sum(len(v) for v in gates.values())} gate tests across {len(gates)} files",
        file=sys.stderr,
    )
    return gates


def load_existing_manifest(path: str) -> _ManifestData:
    """Load existing entrypoint-manifest.yaml (full load — backward compat).

    ## @purpose  Read YAML manifest from disk. Loads ALL sections including allowed_verbs/gates.
    ##            ⚠️ DEPRECATED for main() flow: use load_structural_sections() instead.
    ##            Kept for backward compatibility with external consumers importing this function.
    ## @io       ⇥ path: path to entrypoint-manifest.yaml
    ##           → ⎋ dict: parsed YAML content (empty dict if file missing)
    ## @complexity O(1) — single file read + parse
    ## @invariants
    ##   - Returns ALL keys from manifest, including allowed_verbs and gates
    ##   - Missing file returns empty dict
    """
    print(f"[IMP:7][load_existing_manifest] Loading existing manifest from {path}", file=sys.stderr)
    manifest_path = Path(path)
    if not manifest_path.is_file():
        print(f"[IMP:6][load_existing_manifest] Manifest not found at {path}, returning empty", file=sys.stderr)
        return {}
    with Path(str(manifest_path)).open(encoding="utf-8") as f:
        # W11: yaml.safe_load returns Any → cast to opaque mapping boundary
        data = cast(_ManifestData | None, yaml.safe_load(f))
    if data is None:
        data = {}
    print(f"[IMP:9][load_existing_manifest] Loaded manifest with {len(data)} top-level keys", file=sys.stderr)
    return data


def load_structural_sections(path: str) -> _ManifestData:
    """Load structural sections ONLY from entrypoint-manifest.yaml — explicitly excludes allowed_verbs and gates.

    ## @purpose  Load existing manifest for structural sections ONLY.
    ##            allowed_verbs and gates are NEVER loaded from the manifest —
    ##            they are generated EXCLUSIVELY from Makefile .PHONY targets and pytest gate markers.
    ##            This breaks the G3 cyclic dependency (DevPlan 090 T6) where the generator would
    ##            read its own output and perpetuate stale values.
    ## @io       ⇥ path: path to entrypoint-manifest.yaml
    ##           → ⎋ dict: structural sections only (allowed_verbs and gates explicitly excluded)
    ## @complexity O(1) — single file read + parse + filter
    ## @invariants
    ##   - allowed_verbs key is NEVER present in result
    ##   - gates key is NEVER present in result
    ##   - Missing file returns empty dict
    ##   - All other keys preserved verbatim
    ## @rationale G3 CYCLE BREAK (DevPlan 090 T6): Allowed_verbs and gates MUST come EXCLUSIVELY from
    ##            Makefile .PHONY targets and pytest gate markers. Loading them from the manifest would
    ##            create a self-reinforcing drift mask — if a target is deleted from Makefile but remains
    ##            in YAML, the generator would preserve it. Explicit exclusion makes the contract
    ##            impossible to violate at the data-loading layer.
    """
    print(f"[IMP:7][load_structural_sections] Loading structural sections from {path}", file=sys.stderr)
    manifest_path = Path(path)
    if not manifest_path.is_file():
        print(f"[IMP:6][load_structural_sections] Manifest not found at {path}, returning empty", file=sys.stderr)
        return {}
    with Path(str(manifest_path)).open(encoding="utf-8") as f:
        # W11: yaml.safe_load returns Any → cast to opaque mapping boundary
        data = cast(_ManifestData | None, yaml.safe_load(f))
    if data is None:
        data = {}

    # ⚠️ G3 CYCLE BREAK: allowed_verbs and gates are explicitly excluded.
    # They come EXCLUSIVELY from Makefile .PHONY targets and pytest gate markers.
    # Loading them from the manifest would create a self-reinforcing drift mask
    # where deleted Makefile targets persist in YAML forever.
    excluded: set[str] = {"allowed_verbs", "gates"}
    structural: _ManifestData = {k: v for k, v in data.items() if k not in excluded}

    excluded_count = sum(1 for k in data if k in excluded)
    print(
        f"[IMP:9][load_structural_sections] Loaded {len(structural)} structural keys "
        f"(excluded {excluded_count} generated keys: allowed_verbs/gates)",
        file=sys.stderr,
    )
    return structural


def _collect_repair_mappings(existing: _ManifestData) -> dict[str, dict[str, object]]:
    """Collect repair field mappings from repair: section for injection into gates[].

    ## @purpose  Read repair: section, extract repairs_gates mappings keyed by gate_id.
    ##           Supports both repairable (L1/L2) and non-repairable (L3) gates.
    ## @io       ⇥ existing: dict — existing manifest content
    ##           → ⎋ dict[str, dict]: gate_id → repair fields (including repairable flag)
    ## @complexity O(R * G) where R=repair entries, G=gates per entry
    ## @invariants
    ##   - gate_id is the lookup key matching gates[] entries
    ##   - repair_id is injected as a stable API identifier
    ##   - Non-repairable gates get repairable: false + repair_reason
    ## @see       DevPlan 060 — Repair Contract Infrastructure
    """
    repair_section = existing.get("repair", [])
    if not isinstance(repair_section, list) or not repair_section:
        return {}

    mappings: dict[str, dict[str, object]] = {}
    # W11: object list → cast for isinstance-checked iteration
    for repair_entry in cast(list[object], repair_section):
        if not isinstance(repair_entry, dict):
            continue
        repair_typed = cast(dict[str, object], repair_entry)
        for gate_repair in cast(list[object], repair_typed.get("repairs_gates", [])):
            if not isinstance(gate_repair, dict):
                continue
            gate_repair_typed = cast(dict[str, object], gate_repair)
            gate_id = gate_repair_typed.get("gate_id")
            if not gate_id:
                continue

            # Build repair fields dict (exclude gate_id — it's the lookup key, not a field)
            fields: dict[str, object] = {}
            for k, v in gate_repair_typed.items():
                if k == "gate_id":
                    continue
                fields[k] = v

            # If repairable not explicitly set, default to true (has repair_command)
            if "repairable" not in fields:
                fields["repairable"] = True

            # W11: gate_id is a YAML string key — cast (no runtime coercion)
            mappings[cast(str, gate_id)] = fields

    if mappings:
        print(
            f"[IMP:8][merge] Collected {len(mappings)} repair mappings from repair: section",
            file=sys.stderr,
        )
    return mappings


def merge(allowed_verbs: list[str], gates: _GatesMap, existing: _ManifestData) -> _ManifestData:
    """Merge: replace allowed_verbs and gates, preserve everything else.

    Also injects repair fields from the repair: section into matching gates
    entries (DevPlan 060 — Repair Contract Infrastructure).
    NOTE: repair field injection is currently SUPPRESSED (B4).

    ## @purpose  Merge extracted targets and gate tests into structural sections.
    ##            Replaces allowed_verbs and gates ENTIRELY from generated values.
    ##            Preserves all other sections verbatim.
    ##            This is the last line of defense for the G3 cycle break:
    ##            even if load_structural_sections() mistakenly returned
    ##            allowed_verbs/gates, merge() overwrites them anyway.
    ## @io       ⇥ allowed_verbs: list[str] — extracted .PHONY targets
    ##           ⇥ gates: dict[str, list[str]] — {test_file: [test_ids]} маппинг (T3.3)
    ##           ⇥ existing: dict — structural sections from manifest (allowed_verbs/gates SHOULD be absent)
    ##           → ⎋ dict: merged manifest ready for YAML output
    ## @complexity O(G log G) where G=gate ids (сортировка маппинга)
    ## @invariants
    ##   - allowed_verbs in output always from extracted targets, NEVER from existing
    ##   - gates in output always from collected tests (маппинг test_file→[ids]), NEVER from existing
    ##   - G3 cycle break: merge() overwrites allowed_verbs/gates unconditionally.
    ##     This is a safety net even if load_structural_sections() fails to exclude them.
    ##   - Repair fields injection from repair: section is SUPPRESSED (B4)
    ##   - All other sections from existing preserved unchanged
    ##   - Result dict maintains YAML-compatible structure (dict, not None)
    ## @changes  2026-07-23 | DevPlan 060: repair fields injection from repair: section (suppressed B4)
    ##            2026-07-30 | G3 cycle break defensive: merge() explicitly overwrites allowed_verbs/gates
    ##                         regardless of what `existing` contains. This is a safety net — the real
    ##                         cycle break is in load_structural_sections() excluding these keys at load time.
    ##            2026-08-22 | T3.3 gates-map compaction: gates = {test_file: [ids]} вместо списка
    ##                         развёрнутых записей; сортировка ключей/id (детерминизм byte-level)
    """
    gate_id_total = sum(len(v) for v in gates.values())
    print(
        f"[IMP:7][merge] Merging {len(allowed_verbs)} verbs + {gate_id_total} gate ids ({len(gates)} files) into existing manifest",
        file=sys.stderr,
    )

    # Start with existing manifest
    result = dict(existing)

    # Replace allowed_verbs entirely
    result["allowed_verbs"] = list(allowed_verbs)

    # B4 (DevPlan 046 W2-2): repair→gate injection SUPPRESSED.
    # Collect repair mappings from repair: section (kept for API stability)
    # repair_mappings = _collect_repair_mappings(existing)
    # repair: section's repairs_gates is the single source of truth.
    # Injecting into gates[] creates DRY violation — same metadata in both places.
    # test_repair_contract_integrity gate reads from gates[] — it now sees
    # repairable=False (default) for all gates and skips repair field validation.
    # If repair contract validation from gates[] is needed, update the gate test
    # to read from `repair:` section's repairs_gates instead.
    # injected_count = 0
    # for test_file, ids in gates.items():
    #     for gate_id in ids:
    #         if gate_id in repair_mappings:
    #             gate_entry[gate_id].update(repair_mappings[gate_id])
    #             injected_count += 1

    # Replace gates entirely (no repair field injection) — T3.3 compact map form.
    result["gates"] = {k: sorted(v) for k, v in sorted(gates.items())}

    # Ensure manual sections are preserved (if present in existing).
    # forbidden-тройка упразднена DevPlan 171 W3.3 — секции больше не существуют.
    for key in (
        "name_linter",
        "module_lifecycle",
        "system_module_lifecycle",
        "lib",
        "module_hooks",
    ):
        if key in existing:
            result[key] = existing[key]

    print(
        f"[IMP:9][merge] Merge complete — {len(cast(list[object], result.get('allowed_verbs', [])))} verbs, "
        f"{sum(len(v) for v in cast(_GatesMap, result.get('gates', {})).values())} gate ids across "
        f"{len(cast(_GatesMap, result.get('gates', {})))} files",
        file=sys.stderr,
    )
    return result


# endregion FUNC_extract_phony_targets

# endregion PUBLIC_API


# region CHECK_HELPERS

# Гибридная граница манифеста (DevPlan 171 W4.5): генератор перезаписывает ТОЛЬКО
# GENERATED-блок (allowed_verbs + gates) между маркерами; MANUAL-часть — verbatim.
_GENERATED_START_MARKER = (
    "# ═══ GENERATED SECTION START (allowed_verbs + gates — make generate-entrypoint-manifest) ═══"
)
_GENERATED_END_MARKER = "# ═══ GENERATED SECTION END ═══"


def _generate_output(merged: _ManifestData, existing_raw: str = "") -> str:
    """Generate the YAML output string — hybrid boundary (DevPlan 171 W4.5).

    ## @purpose  Гибрид-граница: генератор перезаписывает ТОЛЬКО GENERATED-блок
    ##            (allowed_verbs + gates) между маркерами `### GENERATED SECTION START/END`;
    ##            MANUAL-часть (всё остальное) переносится из существующего файла
    ##            байт-в-байт (никогда не пере-дамплится yaml.dump).
    ## @io        ⇥ merged: dict — merged manifest data
    ##           ⇥ existing_raw: str — текущее содержимое файла ("" при первой генерации)
    ##           → ⎋ str: полный документ (manual + generated block)
    ## @complexity O(K) where K = number of keys in merged dict
    ## @invariants
    ##   - GENERATED-блок = allowed_verbs + gates (единственные генерируемые секции)
    ##   - Маркеры: "# ═══ GENERATED SECTION START ... ═══" / END
    ##   - Маркеры отсутствуют в existing → блок добавляется в конец (первая генерация)
    ##   - MANUAL-часть — verbatim (ручные правки не затираются регенерацией)
    ## @changes  2026-08-15 | DevPlan 171 W4.5 — гибридная граница (было: полный дамп merged)
    """
    generated_block = (
        _GENERATED_START_MARKER
        + "\n"
        + yaml.dump(
            {"allowed_verbs": merged.get("allowed_verbs", []), "gates": merged.get("gates", [])},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        + _GENERATED_END_MARKER
    )

    if _GENERATED_START_MARKER in existing_raw:
        manual = existing_raw.split(_GENERATED_START_MARKER, 1)[0]
        tail_parts = existing_raw.split(_GENERATED_END_MARKER, 1)
        after = tail_parts[1] if len(tail_parts) > 1 else ""
        return manual + generated_block + after

    # Первая генерация / файл без маркеров: append generated block в конец.
    return existing_raw.rstrip("\n") + "\n" + generated_block + "\n"


def _check_generated_content(content: str, path: Path) -> int:
    """Compare generated content with existing file byte-by-byte.

    ## @purpose  Тонкая обёртка над каноном generated_check.check_generated (AI-0063,
    ##            DevPlan 17 T2.3): полная diff-диагностика P-14 — единственная реализация.
    ## @io        ⇥ content: generated string, path: existing file → ⎋ int: 0=match, 1=diverges
    """
    return check_generated(path, content)


# endregion CHECK_HELPERS


# region CLI


class _ManifestArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    makefile_dir: ClassVar[str]
    gmake_path: ClassVar[str]
    existing_manifest: ClassVar[str]
    tests_dir: ClassVar[str]
    output: ClassVar[str]
    check: ClassVar[bool]


def main(
    argv: list[str] | None = None,
    open_fn: _OpenFn | None = None,
) -> int:
    """CLI entrypoint for entrypoint manifest generator.

    ▶ argparse → ◇ extract_phony_targets + collect_gate_tests + load_structural_sections
      (G3 cycle break: allowed_verbs/gates NEVER loaded from manifest)
      → ⊕ merge → ◇ --check ? compare byte-by-byte : write YAML output → ⎋ exit 0/1

    ## @purpose  CLI for make generate-manifests integration.
    ## @io       ⇥ CLI args: --makefile-dir, --gmake-path, --existing-manifest,
    ##             --tests-dir, --output, --check
    ##           → ⎋ exit code 0 on success/match, 1 on error/divergence
    ## @complexity O(T + N) where T=targets, N=gate tests
    ## @invariants
    ##   - Uses load_structural_sections() NOT load_existing_manifest() — G3 cycle break
    ##   - allowed_verbs/gates come EXCLUSIVELY from Makefile/pytest, never from manifest
    ## @rationale G3 CYCLE BREAK (DevPlan 090 T6): structural sections only from manifest
    ## @changes 2026-08-14 | DevPlan 167 D1 — +argv/open_fn DI-параметры (AF-4 + open-fn seam)
    """
    parser = argparse.ArgumentParser(
        prog="generate_entrypoint_manifest.py",
        description="Generate entrypoint-manifest.yaml — extract .PHONY targets and gate tests",
    )
    parser.add_argument(
        "--makefile-dir",
        default=".",
        help="Path to directory containing root Makefile (default: .)",
    )
    parser.add_argument(
        "--gmake-path",
        default="/opt/homebrew/bin/gmake",
        help="Path to GNU make binary (default: /opt/homebrew/bin/gmake)",
    )
    parser.add_argument(
        "--existing-manifest",
        default="core/entrypoint-manifest.yaml",
        help="Path to existing entrypoint-manifest.yaml (default: core/entrypoint-manifest.yaml)",
    )
    parser.add_argument(
        "--tests-dir",
        default="tests",
        help="Path to tests/ directory (default: tests)",
    )
    parser.add_argument(
        "--output",
        default="core/entrypoint-manifest.yaml",
        help="Output path for generated manifest (default: core/entrypoint-manifest.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with existing file byte-by-byte. "
        "Never writes to disk. Exit 0 if match, 1 if divergence.",
    )
    args = parser.parse_args(argv, namespace=_ManifestArgs())

    print("[IMP:7][main] Starting entrypoint manifest generation", file=sys.stderr)

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        targets = extract_phony_targets(args.makefile_dir, args.gmake_path)
        gates = collect_gate_tests(args.tests_dir)
        existing = load_structural_sections(args.existing_manifest)
        merged = merge(targets, gates, existing)
        # Гибридная граница (DevPlan 171 W4.5): MANUAL-часть переносится из файла verbatim.
        output_path_raw = Path(args.output)
        existing_raw = output_path_raw.read_text(encoding="utf-8") if output_path_raw.is_file() else ""
        output_content: str = _generate_output(merged, existing_raw)
        if args.check:
            logger.info("[IMP:7][main][CHECK] Running check mode — comparing with %s", args.output)
            output_path = Path(args.output)
            exit_code = _check_generated_content(output_content, output_path)
            if exit_code == 0:
                print("[IMP:9][main][CHECK] Manifest is up-to-date — exit 0", file=sys.stderr)
                return 0
            print("[IMP:6][main][CHECK] Manifest is stale — exit 1", file=sys.stderr)
            return 1
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · open_fn DI-параметр main() (DevPlan 167 D1)
        # · Rejected: патч builtins.open в тесте · Reason: seam = тестируемость реального вызова
        # ·   (test_gate_atomic_generation_no_partial_writes передаёт fault-open) · Rev: при смене
        # ·   write-механизма генератора
        open_impl = open_fn if open_fn is not None else open
        with open_impl(output_path, "w", encoding="utf-8") as f:
            f.write(output_content)
        print(
            f"[IMP:9][main] Manifest written to {args.output} — {len(targets)} verbs, "
            f"{sum(len(v) for v in gates.values())} gate ids across {len(gates)} files",
            file=sys.stderr,
        )

    # ruff: ignore[BLE001] — top-level CLI handler for unexpected errors
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        print(f"[IMP:1][main] CRITICAL: Manifest generation failed: {e}", file=sys.stderr)
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
