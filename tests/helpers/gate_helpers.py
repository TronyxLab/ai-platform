# GREP_SUMMARY: gate-helpers, load-yaml, repo-root, assert-ldd-imp9, write-yaml, boilerplate-dedup
# STRUCTURE: ▶ load_yaml(path) → ◇ yaml.safe_load → ⎋ dict
#            ▶ write_yaml(path, data) → ◇ dict|str → yaml.safe_dump/write_text → ⎋ Path
#            ▶ repo_root() → ◇ __file__ resolution (cached) → ⎋ Path
#            ▶ module_yaml_paths() → ◇ glob core/modules/ → ⎋ list[Path]
#            ▶ assert_ldd_imp9(caplog, needle, min_count, require_imp9) → ◇ filter records →
#              ⊕ print trajectory → ⊕ assert count → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Единый source of truth для boilerplate в тестах.
##           Устраняет 6 копий _load_yaml, 57 объявлений PROJECT_ROOT, ~45 локальных LDD-хелперов
##           (assert_ldd / _assert_ldd_imp9 / _print_trajectory / _assert_imp9 / _assert_imp9_logged),
##           10+ копий записи YAML в tmp_path (консолидация T2.16).
## @scope    All tests under tests/ использующие YAML loading, project root, LDD assertions, YAML writing.
## @invariants
##   - repo_root() кешируется (module-level) — вычисление один раз за сессию
##   - load_yaml использует yaml.safe_load (не FullLoader) для security
##   - write_yaml: dict → yaml.safe_dump, str → write_text; создаёт parent-директории
##   - assert_ldd_imp9 fails test если нет ни одного [IMP:9]+ log (Test Honesty LDD)
##   - assert_ldd_imp9 печатает траекторию IMP:7-10 (Anti-Illusion: агент видит путь исполнения)
##   - needle=None → любой IMP:9; needle=str → IMP:9+ запись, содержащая needle
##   - require_imp9=False → print-only режим (возвращает bool, без assert) для failure-path тестов
## @rationale Brief 027 §3.1 W1-E4: −25-30% строк в gate-тестах, единый source of truth.
##            T2.16: консолидация ~45 локальных LDD-хелперов и YAML-писателей в один канон.
## @changes
##   LAST_CHANGE: 2026-08-22 | T2.16: assert_ldd_imp9 расширен (needle/require_imp9/print/return-bool),
##                 добавлен write_yaml — консолидация локальных LDD/YAML-хелперов
##   2026-07-21 | Created (DevPlan 028 W1-E4)
# endregion MODULE_CONTRACT

import functools
import io
import logging
import pathlib
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# region REPO_ROOT


@functools.lru_cache(maxsize=1)
def repo_root() -> pathlib.Path:
    """Cached project root. Resolves from this file: tests/helpers/ → tests/ → project root."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


# endregion REPO_ROOT


# region YAML_HELPERS


def load_yaml(path: pathlib.Path | str) -> Any:
    """Load YAML file. Uses yaml.safe_load for security.

    Handles !override compose tags by stripping them before parsing.
    """
    p = pathlib.Path(path)
    if not p.exists():
        msg = f"[gate_helpers] YAML file not found: {p}"
        raise FileNotFoundError(msg)
    raw = p.read_text(encoding="utf-8")
    # Strip !override tags (compose merge marker, not valid YAML)
    raw = re.sub(r":\s*!override\b", ":", raw)
    return yaml.safe_load(io.StringIO(raw))


def write_yaml(path: pathlib.Path | str, data: dict | str) -> pathlib.Path:
    """Write YAML data to a file and return the path.

    ## @purpose — Единый YAML-писатель для тестов (T2.16b): устраняет ~10 локальных копий
    ##            записи YAML в tmp_path (yaml.dump/write_text). dict → yaml.safe_dump
    ##            (security-канон, как load_yaml); str → write_text (raw YAML-текст).
    ## @io — ⇥ path: Path|str, data: dict|str → ⎋ Path (записанный файл)
    ## @complexity — O(N) где N = размер data
    ## @invariants
    ##   - parent-директории создаются (mkdir parents=True) — tmp_path вложенные пути работают
    ##   - dict → yaml.safe_dump (не yaml.dump — безопасный канон), sort_keys=True (дефолт)
    ##   - str → write_text (raw-контент, без YAML-парсинга)
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def module_yaml_paths() -> list[pathlib.Path]:
    """Glob all module.yaml files under core/modules/."""
    root = repo_root()
    return sorted((root / "core" / "modules").glob("*/module.yaml"))


# endregion YAML_HELPERS


# region LDD_ASSERTIONS


def assert_ldd_imp9(
    caplog,
    needle: str | None = None,
    min_count: int = 1,
    require_imp9: bool = True,
) -> bool:
    """Assert that at least min_count [IMP:9+] log records exist in caplog.

    ## @purpose — Единый LDD-хелпер (T2.16a): консолидирует ~45 локальных вариантов
    ##            (_assert_ldd / _assert_ldd_imp9 / _assert_ldd_trajectory / _print_trajectory /
    ##            _assert_imp9 / _assert_imp9_logged). Печатает траекторию IMP:7-10
    ##            (Anti-Illusion: агент видит реальный путь исполнения при фейле).
    ## @io — ⇥ caplog: pytest fixture; needle: str|None (подстрока обязательной IMP-записи);
    ##       min_count: int (≥ сколько записей); require_imp9: bool (False = print-only,
    ##       без assert — для failure-path тестов) → ⎋ bool (True если найдено ≥1 запись)
    ## @complexity — O(R) где R = записи caplog
    ## @invariants
    ##   - needle=None → любой [IMP:9]+ лог (числовой уровень ≥9, как канон gate_helpers)
    ##   - needle="text" → ЛЮБАЯ [IMP:-запись, содержащая text (Family C семантика: ssl_certs
    ##     needle "not Let's Encrypt" живёт в IMP:7/8 записях — уровень не фильтруется)
    ##   - require_imp9=False → возвращает bool без assert (print-only; семантика _print_trajectory)
    ##   - Malformed IMP-тегов пропускаются (не падают), как в _conftest/ldd.py каноне
    ##   - Печать через module logger создаёт копии-записи в caplog (как и локальные варианты) —
    ##     семантика счётчика не меняется: iteration по snapshot list(caplog.records)
    ## @rationale Implements Test Honesty LDD enforcement (RULES.md §TESTING). Единая точка
    ##            заменяет 5 семейств локальных копий с разной семантикой (bool vs assert,
    ##            needle vs любой IMP:9) — оба поведения сохранены в одном API.
    """
    matched: list[str] = []
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        msg = getattr(record, "message", "")
        if "[IMP:" in str(msg):
            try:
                imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
            except (IndexError, ValueError):
                continue
            if imp_level >= 7:
                logger.info("%s", msg)
            if needle is not None:
                # Family C семантика: needle матчит ЛЮБУЮ IMP-запись (не только IMP:9+)
                if needle in str(msg):
                    matched.append(str(msg))
            elif imp_level >= 9:
                matched.append(str(msg))
    logger.info("--- END LDD TRAJECTORY ---")
    if require_imp9:
        if needle is not None:
            assert len(matched) >= min_count, f"Critical LDD Error: No IMP:9 log containing {needle!r} found"
        else:
            assert len(matched) >= min_count, (
                f"[gate_helpers] LDD assertion failed: expected >={min_count} [IMP:9+] logs, "
                f"got {len(matched)}. Records: {[r.message for r in caplog.records[:5]]}"
            )
    return bool(matched)


# endregion LDD_ASSERTIONS


# region WORKFLOW_HELPERS


def load_workflow(workflow_name: str) -> dict:
    """Load a workflow YAML file from .github/workflows/.

    ## @purpose — Parse a CI workflow YAML for structural validation tests.
    ## @io — workflow_name → ⎋ dict (parsed YAML)
    ## @complexity — O(1)
    """
    path = repo_root() / ".github" / "workflows" / workflow_name
    logger.info("[IMP:8][load_workflow] Loading workflow: %s", path)
    return load_yaml(path)


def get_on_section(workflow: dict) -> dict:
    """Get the 'on' trigger section from a workflow, handling PyYAML 'on'→True conversion.

    ## @purpose — YAML parses 'on:' as boolean key True. This helper normalizes
    ##            access by trying both 'on' (string) and True (boolean) keys.
    ## @io — workflow → ⎋ dict (on section)
    ## @complexity — O(1)
    """
    on_section = workflow.get("on") or workflow.get(True) or {}
    if not isinstance(on_section, dict):
        return {}
    return on_section


# endregion WORKFLOW_HELPERS
