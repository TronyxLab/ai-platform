"""Cross-layer import detector — dotted-import layer isolation (DevPlan 163 W-C).

# GREP_SUMMARY: static cross-layer dotted-imports python3-m layer-isolation entrypoints internal modules direction-allowlist deploy-bootstrap
# STRUCTURE: ▶ discover core/**/*.{py,sh} → ○ classify source layer → ▶ scan imports
#            (dotted `core.<layer>.*` + `python3 -m core.<layer>.*`) → ◇ target layer
#            ∈ allowed(_IMPORT_RULES)? → ◇ direction-allowlist? → ◇ deploy→bootstrap?
#            → ⊕ violations → ⎋ list[Finding]
"""
# region MODULE_CONTRACT
## @purpose  Детектор cross-layer импортной изоляции для dotted-импортов (DevPlan 163
##           W-C C1; порт правил tests/helpers/cross_layer_linter.py + W5 контракт
##           deploy→bootstrap из 02-DevPlan.md): Python `import/from core.<layer>...`
##           и shell `python3 -m core.<layer>...` вне разрешённых направлений слоёв
##           (entrypoints → {internal, lib}; internal → {internal, lib, modules};
##           modules → {lib, templates}) → Finding rule="cross-layer" (blocking).
## @scope    Сканирует core/**/*.py и core/**/*.sh. Только dotted-импорты вида
##           core.<layer>.<...>; не-dotted относительные/абсолютные пути — вне скоупа
##           (shell-source-направления переезжают в import-linter, W-D D1).
## @invariants
##   - Под правила подпадают только слои entrypoints/internal/modules (источник)
##   - Direction-allowlist (S7): (modules→internal, scope core/modules/postgres/hooks/)
##     — postgres-hook по дизайну (D1); фикстуры вне scope → RED
##   - internal/deploy → internal/bootstrap — запрещённое направление (W5: deploy→bootstrap)
##   - internal→modules через dotted — разрешено (typed contract Gate #8 v2)
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale Быстрый слой агента (<5 s) должен ловить нарушения изоляции слоёв без
##            полного pytest-гейта (инверсия слоёв, 01-Brief §2). Dotted-импорты —
##            класс дефекта U-09.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт cross_layer_linter)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# ── Классификация слоёв (пути относительно корня core/) ──────────────────────
_LAYER_PREFIXES: dict[str, str] = {
    "entrypoints": "entrypoints/",
    "internal": "internal/",
    "modules": "modules/",
    "lib": "lib/",
    "templates": "templates/",
    "scripts": "scripts/",
    "bootstrap": "bootstrap/",
    "schemas": "schemas/",
}

# Разрешённые направления импортов: source_layer → set(target_layers)
_IMPORT_RULES: dict[str, frozenset[str]] = {
    "entrypoints": frozenset(("internal", "lib")),
    "internal": frozenset(("internal", "lib", "modules")),  # modules — typed contract (Gate #8 v2)
    "modules": frozenset(("lib", "templates")),
}

# Только эти слои-источники подпадают под правила
_IMPORTING_LAYERS: frozenset[str] = frozenset(("entrypoints", "internal", "modules"))

# Direction-allowlist (S7, DevPlan 139 W3 T5): (source_layer, target_layer, path_prefix)
# Модули контейнеризированы и импортируют internal/shared по дизайну (D1).
_DIRECTION_ALLOWLIST: tuple[tuple[str, str, str], ...] = (("modules", "internal", "core/modules/postgres/hooks/"),)

# Запрет deploy→bootstrap (W5, 02-DevPlan.md T5.1): внутренний слой deploy не
# импортирует bootstrap (изоляция фаз bootstrap-конвейера).
_DEPLOY_PREFIX = "core/internal/deploy/"
_BOOTSTRAP_MODULE_PREFIX = "core.internal.bootstrap"

# Dotted-имя модуля: core.<layer>.<submodule...>
_RE_DOTTED_MODULE: re.Pattern[str] = re.compile(r"^core\.([a-z_]\w*)(?:\.[a-z_]\w*)+$")
# Python-импорты: `import core.X.Y` / `from core.X.Y import ...`
_RE_PY_IMPORT: re.Pattern[str] = re.compile(r"(?:^|\s)(?:from|import)\s+(core\.[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)")
# Shell: python3 -m core.X.Y
_RE_PY3M: re.Pattern[str] = re.compile(r"python3\s+-m\s+(core\.[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)")


# region FUNC_classify_layer
def _classify_layer(file_rel: str) -> str | None:
    """Определить слой файла по repo-relative пути.

    ## @purpose  Сопоставить "core/<layer>/..." → имя слоя.
    ## @io       ⇥ file_rel: str → ⎋ str | None
    ## @complexity  O(L) — число слоёв
    """
    for layer, prefix in _LAYER_PREFIXES.items():
        if f"core/{prefix}" in file_rel:
            return layer
    return None


# endregion FUNC_classify_layer


# region FUNC_target_layer_of_module
def _target_layer_of_module(module: str) -> str | None:
    """Определить слой-цель dotted-модуля core.<layer>.<...>.

    ## @purpose  Первый компонент после "core." — слой цели.
    ## @io       ⇥ module: str (core.<layer>...) → ⎋ str | None
    ## @complexity  O(1)
    """
    match = _RE_DOTTED_MODULE.match(module)
    if match is None:
        return None
    return match.group(1)


# endregion FUNC_target_layer_of_module


# region FUNC_is_direction_allowlisted
def _is_direction_allowlisted(file_rel: str, source_layer: str, target_layer: str) -> bool:
    """Проверить направление + scope-префикс против direction-allowlist.

    ## @purpose  Разрешённое направление с ограничением по префиксу пути (S7).
    ## @io       ⇥ file_rel: str, source_layer: str, target_layer: str → ⎋ bool
    ## @complexity  O(A) — записи allowlist
    """
    for src, tgt, prefix in _DIRECTION_ALLOWLIST:
        if source_layer == src and target_layer == tgt and file_rel.startswith(prefix):
            logger.info(
                "[IMP:9][cross_layer][allowlist] %s — direction %s→%s allowlisted (scope '%s')",
                file_rel,
                src,
                tgt,
                prefix,
            )
            return True
    return False


# endregion FUNC_is_direction_allowlisted


# region FUNC_check_dotted_import
def _check_dotted_import(
    file_rel: str, lineno: int, module: str, source_layer: str, import_kind: str
) -> Finding | None:
    """Проверить один dotted-импорт на нарушение правил слоёв.

    ## @purpose  Слой-источник ∈ importing-layers, слой-цель резолвится из модуля,
    ##           направление сверяется с _IMPORT_RULES + allowlist + deploy→bootstrap.
    ## @io       ⇥ file_rel: str, lineno: int, module: str, source_layer: str,
    ##              import_kind: str ("py"|"sh")
    ##           ⎋ Finding | None
    ## @complexity  O(1)
    """
    target_layer = _target_layer_of_module(module)
    if target_layer is None:
        return None

    # Запрет deploy→bootstrap (W5): source core/internal/deploy/, target core.internal.bootstrap
    if file_rel.startswith(_DEPLOY_PREFIX) and module.startswith(_BOOTSTRAP_MODULE_PREFIX):
        msg = f"[deploy→bootstrap] '{module}' — deploy не импортирует bootstrap (W5, изоляция фаз)"
        logger.warning("[IMP:9][cross_layer][RED] %s:%d %s", file_rel, lineno, msg)
        return Finding(rule="cross-layer", file=file_rel, line=lineno, message=msg)

    allowed = _IMPORT_RULES.get(source_layer)
    if allowed is None:
        return None
    if target_layer in allowed:
        return None
    if _is_direction_allowlisted(file_rel, source_layer, target_layer):
        return None
    msg = f"[{source_layer}→{target_layer}] '{module}' (forbidden, {import_kind})"
    logger.warning("[IMP:9][cross_layer][RED] %s:%d %s", file_rel, lineno, msg)
    return Finding(rule="cross-layer", file=file_rel, line=lineno, message=msg)


# endregion FUNC_check_dotted_import


# region FUNC_scan_py_lines
def _scan_py_lines(lines: list[str], file_rel: str, source_layer: str) -> list[Finding]:
    """Сканировать строки Python-файла на dotted-импорты core.<layer>.

    ## @purpose  Линейный скан (как scan_py_file в cross_layer_linter) — простые
    ##           императивные блоки, детерминированный порядок строк.
    ## @io       ⇥ lines: list[str], file_rel: str, source_layer: str → ⎋ list[Finding]
    ## @complexity  O(N * M) — строки × матчи
    """
    findings: list[Finding] = []
    for lineno, line in enumerate(lines, 1):
        for match in _RE_PY_IMPORT.finditer(line):
            module = match.group(1)
            finding = _check_dotted_import(file_rel, lineno, module, source_layer, "py")
            if finding is not None:
                findings.append(finding)
    return findings


# endregion FUNC_scan_py_lines


# region FUNC_scan_sh_lines
def _scan_sh_lines(lines: list[str], file_rel: str, source_layer: str) -> list[Finding]:
    """Сканировать строки shell-файла на python3 -m core.<layer>.<module>.

    ## @purpose  Dotted-импорт через python3 -m (U-09).
    ## @io       ⇥ lines: list[str], file_rel: str, source_layer: str → ⎋ list[Finding]
    ## @complexity  O(N * M)
    """
    findings: list[Finding] = []
    for lineno, line in enumerate(lines, 1):
        for match in _RE_PY3M.finditer(line):
            module = match.group(1)
            finding = _check_dotted_import(file_rel, lineno, module, source_layer, "sh")
            if finding is not None:
                findings.append(finding)
    return findings


# endregion FUNC_scan_sh_lines


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти dotted-нарушения изоляции слоёв в core/**/*.{py,sh}.

    # ▶ core/**/*.{py,sh} → ○ classify source layer → ○ dotted-скан → ◇ rules/allowlist
    #   → ⊕ Findings → ⎋ sorted

    ## @purpose  Главный вход детектора (registry): dotted-импорты `core.<layer>.*`
    ##           из entrypoints/internal/modules вне разрешённых направлений.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    ## @invariants  Сканирует core/**/*.py и core/**/*.sh; только слои-источники
    ##              entrypoints/internal/modules; модули вне core/ игнорируются
    """
    core_dir = root / "core"
    if not core_dir.is_dir():
        return []
    findings: list[Finding] = []
    candidates = [p for p in core_dir.rglob("*") if p.is_file() and p.suffix in {".py", ".sh"}]
    candidates = [p for p in candidates if "__pycache__" not in p.parts]

    for path in candidates:
        file_rel = path.relative_to(root).as_posix()
        if changed is not None and file_rel not in changed:
            continue
        source_layer = _classify_layer(file_rel)
        if source_layer is None or source_layer not in _IMPORTING_LAYERS:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if path.suffix == ".py":
            findings.extend(_scan_py_lines(lines, file_rel, source_layer))
        else:
            findings.extend(_scan_sh_lines(lines, file_rel, source_layer))

    logger.info("[IMP:9][cross_layer] Scanned %d file(s), findings=%d", len(candidates), len(findings))
    if not findings:
        logger.info("[IMP:9][cross_layer] PASS: 0 cross-layer dotted-import violations")
    return findings


# endregion FUNC_detect
