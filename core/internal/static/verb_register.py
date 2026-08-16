"""Verb-register detector — Makefile .PHONY ↔ allowed_verbs parity (DevPlan 163 W-C).

# GREP_SUMMARY: static verb-register makefile phony-targets allowed-verbs system-exceptions entrypoint-manifest parity
# STRUCTURE: ▶ collect .PHONY targets (root Makefile + makefiles/*.mk) → ⊕ expected
#            = allowed_verbs ∪ service-categories → ◇ missing (verb без таргета)? → ◇ extra
#            (таргет без регистрации)? → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор канонического реестра make-глаголов (DevPlan 163 W-C C1; порт
##           семантики tests/gates/test_gate_makefile_targets.py::test_all_phony_targets_discovered,
##           name-linter): каждый .PHONY таргет (root Makefile + makefiles/*.mk) обязан
##           быть в core/entrypoint-manifest.yaml allowed_verbs ИЛИ в
##           категории служебных таргетов (help/venv, префиксы, _-имена — DevPlan 171 W3.6);
##           каждый allowed_verb обязан иметь .PHONY таргет. Находки — rule="verb-register" (blocking).
##           (forbidden_verbs упразднены DevPlan 171 W3.3 — категорийное правило
##           «таргет вне глоссария = запрещён» покрывает класс целиком)
## @scope    Структурный скан Makefile, makefiles/*.mk, core/entrypoint-manifest.yaml.
##           Системные исключения (help/venv/pre-commit-install/pre-commit-run/
##           _get_all_profiles) и системные префиксы (test-, gate-, pre-commit-) —
##           легитимны вне глоссария.
## @invariants
##   - expected = allowed_verbs ∪ служебные категории (help/venv + префиксы + _-имена)
##   - missing = expected − phony_targets → RED (глагол в манифесте, таргета нет)
##   - extra = phony_targets − expected → RED (таргет без регистрации)
##   - `changed`: при --changed прогон только если Makefile/.mk/манифест в changed
## @rationale Манифест — единый SoT операций (AGENTS.md инвариант 5); расхождение
##            Makefile↔манифест — класс дрейфа G1.2 (hardcoded target sets). Быстрый
##            слой ловит расхождение без pytest-гейта.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт name-linter семантики)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import yaml

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_PHONY_RE: re.Pattern[str] = re.compile(r"\.PHONY\s*:")
_MAKEFILES_DIR = "makefiles"
_MANIFEST_REL = "core/entrypoint-manifest.yaml"
_TARGET_SET_MIN_ELEMENTS = 3


# region FUNC_collect_phony_targets
def _collect_phony_targets(root: Path) -> set[str]:
    """Собрать ВСЕ .PHONY таргеты из root Makefile и makefiles/*.mk.

    ## @purpose  Парсинг `.PHONY:` строк. Глобальная сверка — фильтр по changed
    ##           применяется на УРОВНЕ запуска детектора (detect), не к сбору:
    ##           сравнение требует полного множества таргетов.
    ## @io       ⇥ root: Path → ⎋ set[str]
    ## @complexity  O(F * L) — файлы × строки
    """
    targets: set[str] = set()
    makefile = root / "Makefile"
    candidates: list[Path] = []
    if makefile.is_file():
        candidates.append(makefile)
    mk_dir = root / _MAKEFILES_DIR
    if mk_dir.is_dir():
        candidates.extend(sorted(mk_dir.glob("*.mk")))

    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith(".PHONY:"):
                continue
            targets.update(name for name in stripped[len(".PHONY:") :].split() if name)
    return targets


# endregion FUNC_collect_phony_targets


# region FUNC_load_manifest_verbs
def _load_manifest_verbs(root: Path) -> tuple[set[str], set[str], tuple[str, ...]]:
    """Загрузить allowed_verbs / служебные категории из манифеста (DevPlan 171 W3.3/W3.6).

    ## @purpose  Единый SoT операций (core/entrypoint-manifest.yaml).
    ## @io       ⇥ root: Path → ⎋ (allowed: set[str], service: set[str], prefixes: tuple[str, ...])
    ## @complexity  O(1) — один YAML-файл
    """
    manifest = root / _MANIFEST_REL
    if not manifest.is_file():
        logger.warning("[IMP:7][verb_register] Manifest not found: %s", manifest)
        return set(), set(), ()
    try:
        with manifest.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("[IMP:7][verb_register] Cannot parse manifest: %s", manifest)
        return set(), set(), ()
    allowed = set(data.get("allowed_verbs") or [])
    name_linter = data.get("name_linter") or {}
    # Категорийное правило (DevPlan 171 W3.6): стандартные служебные таргеты make —
    # реальные имена; префиксы + `_`-имена — категории. help-all — План 175 W1.3.
    service = {"help", "help-all", "venv"}
    prefixes = tuple(name_linter.get("system_prefixes") or [])
    return allowed, service, prefixes


# endregion FUNC_load_manifest_verbs


# region FUNC_finding
def _finding(file_rel: str, message: str) -> Finding:
    """Собрать Finding (file-уровень, строка 0) с логированием RED.

    ## @purpose  Единая точка создания находки verb-register (DRY внутри детектора).
    ## @io       ⇥ file_rel: str, message: str → ⎋ Finding
    ## @complexity  O(1)
    """
    logger.warning("[IMP:9][verb_register][RED] %s: %s", file_rel, message)
    return Finding(rule="verb-register", file=file_rel, line=0, message=message)


# endregion FUNC_finding


# region FUNC_scan_gate_target_sets
# ── Расширение G1.2 (порт tests/gates/test_gate_exception_audit.py): ──────────
# hardcoded target sets в gate-файлах — drift-класс «один писатель инварианта».
# Наборы make-таргетов читаются ТОЛЬКО из entrypoint-manifest.yaml; set-литерал
# из 3+ таргет-паттернов в tests/gates/*.py → RED (кроме allowlisted non-target имён).

# Target-like pattern: lowercase слова с дефисами, минимум 2 символа
_TARGET_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9-]+$")

# Известные non-target set-имена, разрешённые в gate-файлах (не make-таргеты):
# healthcheck interval-классы, домен-маркеры, field-validation сеты и т.п.
_ALLOWED_NON_TARGET_SETS: frozenset[str] = frozenset((
    "_DEPRECATED_PATTERNS",
    "_EXCLUDED_DIRS",
    "_SCAN_EXTENSIONS",
    "_EXCEPTION_FILES",
    "_SHEBANG_EXCEPTION_PATTERNS",
    "_EXCLUDE_DIRS",
    "_SCAN_SPECIFIC",
    "_CONVENIENCE_TARGETS",
    "_MODULE_SCOPED_VERBS",
    "env_dependent",
    "required_fields",  # test_gate_deploy_paths.py:151 — field validation set
    "_CRITICAL_15S",  # healthcheck interval классы (D4)
    "_SERVICES_30S",  # healthcheck interval классы (D4)
    "_BACKGROUND_60S",  # healthcheck interval классы (D4)
    "_DOCKER_SSH_MARKERS",  # test_gate_timeout_literals.py — домен-маркеры
    "_WORKFLOW_ALLOWED_VERBS",  # test_gate_deploy_channel.py — SSH-verbs канала
    "_STATEFUL_MODULES",  # test_gate_make_contract.py — имена модулей, не таргеты
    "_SKIP_PARTS",  # test_gate_no_empty_dirs.py + test_gate_generated_marker_orphan.py — runtime-категории скана
))

_GATES_DIR = "tests/gates"


def _is_target_set(elements: list[str]) -> bool:
    """3+ элемента выглядят как make-таргеты?

    ## @purpose  Heuristic гейта: len ≥ 3 и ≥3 элемента матчат _TARGET_PATTERN.
    ## @io       ⇥ elements: list[str] → ⎋ bool
    ## @complexity  O(N)
    """
    if len(elements) < _TARGET_SET_MIN_ELEMENTS:
        return False
    return sum(1 for e in elements if _TARGET_PATTERN.match(e)) >= _TARGET_SET_MIN_ELEMENTS


def _scan_gate_target_sets(root: Path, changed: set[str] | None) -> list[Finding]:
    """Сканировать tests/gates/*.py на hardcoded target sets.

    ## @purpose  AST-скан set(...) вызовов и set-литералов {..} в gate-файлах;
    ##           целевой set с 3+ таргет-паттернами, НЕ присвоенный allowlisted
    ##           non-target имени → RED (G1.2 anti-drift).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    ## @invariants  allowlist non-target имён; set-литералы, присвоенные таким
    ##              именам, не триггерят (порт _ALLOWED_NON_TARGET_SETS гейта)
    """
    gates_dir = root / _GATES_DIR
    if not gates_dir.is_dir():
        return []
    findings: list[Finding] = []
    for gf in sorted(gates_dir.glob("test_gate_*.py")):
        rel = gf.relative_to(root).as_posix()
        if changed is not None and rel not in changed:
            continue
        try:
            tree = ast.parse(gf.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            elements: list[str] = []
            lineno = 0
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set":
                if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    elements = [
                        e.value for e in node.args[0].elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
                    lineno = node.lineno
            elif isinstance(node, ast.Set):
                elements = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                lineno = node.lineno
            if not elements or not _is_target_set(elements) or lineno == 0:
                continue
            if _is_allowed_target_set(node, tree):
                continue
            findings.append(
                _finding(
                    rel,
                    "hardcoded target set in gate (read from entrypoint-manifest.yaml instead): "
                    + ", ".join(sorted(elements)),
                )
            )
    return findings


def _is_allowed_target_set(node: ast.AST, tree: ast.Module) -> bool:
    """Set-узел присвоен allowlisted non-target имени?

    ## @purpose  Порт _is_allowed_target_set гейта: Assign/AnnAssign target.id ∈
    ##           _ALLOWED_NON_TARGET_SETS и parent.value is node.
    ## @io       ⇥ node: AST-узел, tree: ast.Module → ⎋ bool
    ## @complexity  O(N) — walk
    """
    for parent in ast.walk(tree):
        if isinstance(parent, ast.Assign):
            for target in parent.targets:
                if isinstance(target, ast.Name) and target.id in _ALLOWED_NON_TARGET_SETS and parent.value is node:
                    return True
        if (
            isinstance(parent, ast.AnnAssign)
            and isinstance(parent.target, ast.Name)
            and parent.target.id in _ALLOWED_NON_TARGET_SETS
            and parent.value is node
        ):
            return True
    return False


# endregion FUNC_scan_gate_target_sets


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Сверить .PHONY таргеты с allowed_verbs/system_exceptions.

    # ▶ ┌.PHONY targets┐ ⊕ ┌manifest verbs┐ → ◇ missing / extra → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry): parity Makefile↔манифест (G1.2).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L + Y) — makefiles × строки + YAML
    ## @invariants  expected = allowed ∪ system_exceptions; findings.file —
    ##              Makefile/manifest (файл-источник несоответствия)
    """
    if changed is not None:
        relevant = changed & {_MANIFEST_REL, "Makefile"}
        if not relevant:
            mk_dir = root / _MAKEFILES_DIR
            if mk_dir.is_dir():
                relevant = changed & {p.relative_to(root).as_posix() for p in mk_dir.glob("*.mk")}
        if not relevant:
            logger.info("[IMP:8][verb_register][changed] No changed verb source — skipping")
            return []

    allowed, service, prefixes = _load_manifest_verbs(root)
    targets = _collect_phony_targets(root)
    expected = allowed | service
    findings: list[Finding] = []

    missing = expected - targets
    if missing:
        findings.append(
            _finding(
                _MANIFEST_REL,
                "registered verb(s) without .PHONY target: " + ", ".join(sorted(missing)),
            )
        )
    extra = {t for t in targets - allowed if t not in service and not t.startswith(prefixes) and not t.startswith("_")}
    if extra:
        findings.append(
            _finding(
                "Makefile",
                ".PHONY target(s) not registered in manifest allowed_verbs: " + ", ".join(sorted(extra)),
            )
        )

    # Расширение G1.2: hardcoded target sets в gate-файлах (порт exception_audit)
    findings.extend(_scan_gate_target_sets(root, changed))

    logger.info(
        "[IMP:9][verb_register] targets=%d allowed=%d service=%d findings=%d",
        len(targets),
        len(allowed),
        len(service),
        len(findings),
    )
    if not findings:
        logger.info("[IMP:9][verb_register] PASS: Makefile .PHONY targets match manifest allowed_verbs")
    return findings


# endregion FUNC_detect
