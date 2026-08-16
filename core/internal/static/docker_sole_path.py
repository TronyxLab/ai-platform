"""Docker-sole-path detector — subprocess docker calls only in shared facades (DevPlan 163 W-C).

# GREP_SUMMARY: static docker-sole-path subprocess docker-compose docker-ops ps inspect exec shared-only AST shell-make-scan
# STRUCTURE: ▶ AST-скан core/internal/*.py → ○ subprocess.* docker+compose → ◇ shared/docker_compose.py? → ⊕ Finding
#            → ○ subprocess.* docker+{ps|inspect|exec} → ◇ shared/docker_ops.py|watchdog.py? → ⊕ Finding
#            → ⎋ shell/make-скан (`docker compose` вне фасадов → Finding, D70)
"""
# region MODULE_CONTRACT
## @purpose  Детектор sole-path для docker subprocess-вызовов (DevPlan 163 W-C C1; порт
##           tests/gates/test_gate_docker_sole_path.py, DevPlan 116 B5 T10 U-13 + 128 W1 +
##           117 D70): docker compose subprocess-вызовы — ТОЛЬКО в
##           core/internal/shared/docker_compose.py; docker ps/inspect/exec (не compose) —
##           ТОЛЬКО в core/internal/shared/docker_ops.py (+ healthcheck/watchdog.py,
##           stdlib-only cron, DevPlan 132 W1); прямые `docker compose` в shell/make —
##           только в документированных фасадах (D70). Находки — rule="docker-sole-path".
## @scope    AST-скан core/internal/**/*.py (правила A/B) + line-скан shell/make
##           (core/entrypoints, core/lib, core/internal/bootstrap, core/templates +
##           модульные Makefile, правило C). Комментарии/docstring'и не триггерят (AST).
## @invariants
##   - RED: любой docker compose subprocess вне shared/docker_compose.py
##   - RED: любой docker ps/inspect/exec subprocess (не compose) вне
##     shared/docker_ops.py / healthcheck/watchdog.py (allowlist пуст)
##   - RED (D70): прямая строка `docker compose` в shell/make вне фасадов
##     (module.mk, install-docker.sh, setup-node.sh, healthcheck.sh)
##   - Звёздные cmd-элементы (["docker", "compose", *args]) ловятся по строкам-константам
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale U-13: каждая волна добавляла копию docker compose up/pull; 128 W1:
##            docker ps/inspect/exec были в 3+ копиях (drift-акселератор). Структурный
##            запрет возврата копий — парадигма self-verifying waves; быстрый слой
##            детектирует возврат без pytest-гейта.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт B5 T10/128 W1/D70)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_SUBPROCESS_FUNCS: frozenset[str] = frozenset(("run", "check_call", "check_output", "Popen", "call"))

# Правило A: docker compose — только shared/docker_compose.py
_COMPOSE_ALLOWED = "core/internal/shared/docker_compose.py"

# Правило B: docker ps/inspect/exec — только docker_ops.py (+ watchdog.py stdlib-only cron)
_OPS_ALLOWED: tuple[str, ...] = (
    "core/internal/shared/docker_ops.py",
    "core/internal/healthcheck/watchdog.py",
)
_OPS_TOKENS: tuple[str, ...] = ("ps", "inspect", "exec")

# Правило C (D70): разрешённые shell/make точки для прямого `docker compose`
_SHELL_MAKE_ALLOWLIST: frozenset[str] = frozenset((
    "core/templates/module.mk",  # canonical compose wrapper (COMPOSE_CMD)
    "core/internal/bootstrap/install-docker.sh",  # docker compose version — проверка плагина
    "core/internal/bootstrap/setup-node.sh",  # sudoers правило `docker compose *`
    "core/entrypoints/healthcheck.sh",  # echo-текст справки (не вызов)
))
_SHELL_MAKE_SCOPES: tuple[str, ...] = (
    "core/entrypoints",
    "core/lib",
    "core/internal/bootstrap",
    "core/templates",
)
_SHELL_MAKE_EXTENSIONS: tuple[str, ...] = (".sh", ".mk")
_SHELL_MAKE_FILENAMES: tuple[str, ...] = ("Makefile",)


# region FUNC_cmd_values
def _cmd_values(node: ast.AST) -> list[str] | None:
    """Извлечь строковые константы из cmd-аргумента (list или str).

    ## @purpose  Поддержка `subprocess.run(["docker", "compose", ...])` и
    ##           `subprocess.run("docker compose ...")` форм.
    ## @io       ⇥ node: ast.AST → ⎋ list[str] | None
    ## @complexity  O(E) — элементы списка
    """
    if isinstance(node, ast.List):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return None


# endregion FUNC_cmd_values


# region FUNC_scan_py_subprocess
def _scan_py_subprocess(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """AST-скан одного .py файла на docker compose / docker ops subprocess-нарушения.

    ## @purpose  Правила A+B: subprocess.* с docker+compose или docker+{ps|inspect|exec}
    ##           вне разрешённых sole-path файлов.
    ## @io       ⇥ path: Path, root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(N) — AST-узлы
    """
    rel = path.relative_to(root).as_posix()
    if changed is not None and rel not in changed:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        if fn.attr not in _SUBPROCESS_FUNCS:
            continue
        cmd_node: ast.AST | None = node.args[0] if node.args else None
        if cmd_node is None:
            for kw in node.keywords:
                if kw.arg in {"args", "cmd", "command"}:
                    cmd_node = kw.value
                    break
        if cmd_node is None:
            continue
        values = _cmd_values(cmd_node)
        if not values:
            continue
        has_compose = "compose" in values or any("docker compose" in v for v in values)
        has_docker = "docker" in values or any("docker compose" in v for v in values)
        if has_docker and has_compose:
            if rel != _COMPOSE_ALLOWED:
                findings.append(
                    _finding(rel, node.lineno, "docker compose subprocess outside shared/docker_compose.py")
                )
            continue
        if has_docker and not has_compose and any(tok in values for tok in _OPS_TOKENS) and rel not in _OPS_ALLOWED:
            findings.append(
                _finding(rel, node.lineno, "docker ps/inspect/exec subprocess outside shared/docker_ops.py")
            )
    return findings


# endregion FUNC_scan_py_subprocess


# region FUNC_scan_shell_make
def _scan_shell_make(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """Line-скан одного shell/make файла на прямые `docker compose` (D70).

    ## @purpose  Правило C: `docker compose` вне фасадов-allowlist → RED.
    ## @io       ⇥ path: Path, root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(L) — строки файла
    """
    rel = path.relative_to(root).as_posix()
    if rel in _SHELL_MAKE_ALLOWLIST:
        return []
    if changed is not None and rel not in changed:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    findings: list[Finding] = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "docker compose" not in stripped:
            continue
        findings.append(_finding(rel, lineno, "direct `docker compose` in shell/make outside allowed facades (D70)"))
    return findings


# endregion FUNC_scan_shell_make


# region FUNC_finding
def _finding(file_rel: str, lineno: int, message: str) -> Finding:
    """Собрать Finding с логированием RED.

    ## @purpose  Единая точка создания находки docker-sole-path (DRY внутри детектора).
    ## @io       ⇥ file_rel: str, lineno: int, message: str → ⎋ Finding
    ## @complexity  O(1)
    """
    logger.warning("[IMP:9][docker_sole_path][RED] %s:%d %s", file_rel, lineno, message)
    return Finding(rule="docker-sole-path", file=file_rel, line=lineno, message=message)


# endregion FUNC_finding


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти docker subprocess-вызовы вне sole-path фасадов.

    # ▶ ┌core/internal/**/*.py┐ → ○ subprocess-скан (A+B) → ⊕ findings
    #   ▶ ┌shell/make scopes┐ → ○ `docker compose` line-скан (C) → ⊕ findings → ⎋

    ## @purpose  Главный вход детектора (registry): правила A (compose sole-path),
    ##           B (ops sole-path), C (shell/make D70).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N + F2 * L) — py-файлы × AST, shell/make × строки
    ## @invariants  Сканирует core/internal/**/*.py + scopes shell/make; для probe-
    ##              деревьев (без core/) — только core/internal/**/*.py по layout
    """
    findings: list[Finding] = []
    internal_dir = root / "core" / "internal"
    if internal_dir.is_dir():
        py_files = sorted(p for p in internal_dir.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
        for path in py_files:
            findings.extend(_scan_py_subprocess(path, root, changed))

    core_dir = root / "core"
    if core_dir.is_dir():
        shell_make_paths: list[Path] = []
        for scope in _SHELL_MAKE_SCOPES:
            scope_dir = root / scope
            if not scope_dir.is_dir():
                continue
            shell_make_paths.extend(
                p
                for p in sorted(scope_dir.rglob("*"))
                if p.is_file() and (p.suffix in _SHELL_MAKE_EXTENSIONS or p.name in _SHELL_MAKE_FILENAMES)
            )
        modules_mk = root / "core" / "modules"
        if modules_mk.is_dir():
            shell_make_paths.extend(sorted(modules_mk.rglob("Makefile")))
        for path in shell_make_paths:
            findings.extend(_scan_shell_make(path, root, changed))

    logger.info("[IMP:9][docker_sole_path] Findings=%d", len(findings))
    if not findings:
        logger.info("[IMP:9][docker_sole_path] PASS: 0 docker subprocess calls outside sole-path facades")
    return findings


# endregion FUNC_detect
