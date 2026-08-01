#!/usr/bin/env python3
# GREP_SUMMARY: gate docker-sole-path subprocess docker-compose shared-only AST anti-drift sole-path shell-make-scan
# STRUCTURE: ▶ AST-скан core/internal/*.py → ○ subprocess.* вызов с cmd: docker+compose → ◇ файл == shared/docker_compose.py? → PASS | ⟦RED: offenders⟧ → ⎋ shell/make-скан (docker compose вне фасадов → RED, D70)
# region MODULE_CONTRACT
## @purpose  Sole-path gate (DevPlan 116 B5 T10, U-13 + DevPlan 117 D70): docker compose subprocess-вызовы
##           разрешены ТОЛЬКО в core/internal/shared/docker_compose.py. 4 локальные копии
##           (docker_orchestrator, DeployEngine, reconciler, healthcheck_poller) удалены волной B5.
##           DevPlan 117 D70: расширение на shell/make — прямые `docker compose` вызовы в
##           core/entrypoints/, core/lib/, core/internal/bootstrap/*.sh, *.mk → RED вне
##           разрешённых фасадов (compose-wrapper.sh, module.mk canonical wrapper).
## @scope    AST-скан всех core/internal/*.py: subprocess.run/check_call/check_output/Popen/call,
##           где cmd содержит "docker"+"compose" (список `["docker", "compose", ...]` или
##           строка "docker compose"). Комментарии/докстринги исключаются (только AST-узлы вызовов).
##           Shell/make-скан (D70): core/entrypoints/*.sh, core/lib/*.sh, core/internal/bootstrap/*.sh,
##           core/modules/*/Makefile, core/templates/module*.mk — строки `docker compose` вне allowlist.
## @invariants
##   - RED: любой docker compose subprocess-вызов вне shared/docker_compose.py
##   - allowlist: entrypoints/shell вне скоупа (сканируется только core/internal/*.py)
##   - Строковые литералы в docstring/log-сообщениях НЕ триггерят (только AST-вызовы)
##   - Звёздные элементы (["docker", "compose", *args, ...]) ловятся по строковым константам
##   - D70 shell-скан: RED на `docker compose` в shell/make вне фасадов; allowlist:
##     core/entrypoints/compose-wrapper.sh (легитимный фасад), core/templates/module.mk
##     (canonical compose wrapper), комментарии/документация исключаются
## @rationale U-13: каждая волна добавляла 4-ю копию docker compose up/pull. Структурный
##            запрет возврата копий делает sole-path enforce-емым (парадигма self-verifying waves).
##            D70: слепая зона — shell/make точки (module.mk COMPOSE_CMD, compose-wrapper)
##            не сканировались; теперь закрыта (задача 70, DevPlan 117).
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
## @changes 2026-08-01 | DevPlan 117 D70 — shell/make-скан (test_shell_and_make_no_direct_docker_compose)
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"
_ALLOWED_FILE = pathlib.Path("core/internal/shared/docker_compose.py")

_SUBPROCESS_FUNCS = {"run", "check_call", "check_output", "Popen", "call"}

# DevPlan 117 D70: разрешённые shell/make точки для прямого `docker compose`.
# Скоуп скана (DevPlan D70): core/entrypoints/, core/lib/, core/internal/bootstrap/*.sh,
# .mk (core/templates/module*.mk) + модульные Makefile (включают module.mk COMPOSE_CMD).
# core/modules/*.sh (nginx_reload_hook и др.) — вне скоупа D70 (shell-фасады модулей,
#   не core-поверхность; отслеживаются отдельными модульными гейтами).
_SHELL_MAKE_ALLOWLIST: tuple[str, ...] = (
    "core/entrypoints/compose-wrapper.sh",  # легитимный compose-фасад (up-safe, D70)
    "core/templates/module.mk",  # canonical compose wrapper (make start/up/status, COMPOSE_CMD)
    "core/internal/bootstrap/install-docker.sh",  # docker compose version — проверка установки плагина, не compose-операция
    "core/internal/bootstrap/setup-node.sh",  # sudoers правило `docker compose *` (конфигурация прав, не вызов)
    "core/entrypoints/healthcheck.sh",  # echo-текст справки «check local docker compose services» (не вызов)
)
_SHELL_MAKE_SCOPES: tuple[str, ...] = (
    "core/entrypoints",
    "core/lib",
    "core/internal/bootstrap",
    "core/templates",
)
_SHELL_MAKE_EXTENSIONS: tuple[str, ...] = (".sh", ".mk")
_SHELL_MAKE_FILENAMES: tuple[str, ...] = ("Makefile",)


def _cmd_values(node: ast.AST) -> list[str] | None:
    """Extract string-constant values from a command argument (list or str).

    ▶ ┌cmd node┐ → ◇ ast.List → ⊕ [str constants] | ◇ ast.Constant str → ⊕ [value] | ⎋ None
    """
    if isinstance(node, ast.List):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return None


def _find_offenders() -> list[tuple[str, int, str]]:
    """Scan core/internal/*.py for docker compose subprocess calls outside the shared module.

    ▶ ┌_CORE_INTERNAL┐ → ○ for each .py → ○ walk AST → ◇ subprocess.* call + cmd docker+compose → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
                continue
            if fn.attr not in _SUBPROCESS_FUNCS:
                continue
            # cmd: первый позиционный аргумент или args= kwarg
            cmd_node: ast.AST | None = None
            if node.args:
                cmd_node = node.args[0]
            else:
                for kw in node.keywords:
                    if kw.arg in ("args", "cmd", "command"):
                        cmd_node = kw.value
                        break
            if cmd_node is None:
                continue
            values = _cmd_values(cmd_node)
            if not values:
                continue
            if "docker" not in values and not any("docker compose" in v for v in values):
                continue
            if "compose" not in values and not any("docker compose" in v for v in values):
                continue
            # Команда содержит docker+compose — разрешено только в shared/docker_compose.py
            if rel == _ALLOWED_FILE.as_posix():
                continue
            cmd_preview = " ".join(values[:6])
            offenders.append((rel, node.lineno, cmd_preview))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_docker_compose_subprocess_sole_path(caplog) -> None:
    """docker compose subprocess calls must live ONLY in shared/docker_compose.py (U-13)."""
    offenders = _find_offenders()
    if offenders:
        for rel, lineno, cmd in offenders:
            logger.error("[IMP:10][docker_sole_path] %s:%d docker compose subprocess: %s", rel, lineno, cmd)
        pytest.fail(
            f"docker compose subprocess calls found outside shared/docker_compose.py ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} [{cmd}]" for rel, lineno, cmd in offenders)
            + "\n\nSole path: core/internal/shared/docker_compose.py (DevPlan 116 B5 T10, U-13). "
            "Migrate to shared docker_compose_* functions."
        )

    logger.info("[IMP:9][docker_sole_path] PASS: 0 docker compose subprocess calls outside shared/docker_compose.py")


# ── (D70) shell/make-скан: `docker compose` вне фасадов → RED ────────────────


def _find_shell_make_docker_compose() -> list[tuple[str, int, str]]:
    """Scan shell/make files for direct `docker compose` calls outside allowlist.

    ▶ ┌scopes (entrypoints/lib/bootstrap/templates + модульные Makefile)┐ → ○ for *.sh/*.mk/Makefile →
    │   ○ line scan → ◇ "docker compose" (не комментарий, не #-строка) → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int, str]] = []
    for scope in _SHELL_MAKE_SCOPES:
        scope_dir = ROOT / scope
        if not scope_dir.is_dir():
            continue
        for p in sorted(scope_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix not in _SHELL_MAKE_EXTENSIONS and p.name not in _SHELL_MAKE_FILENAMES:
                continue
            if any(part == "__pycache__" for part in p.parts):
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel in _SHELL_MAKE_ALLOWLIST:
                continue
            try:
                lines = p.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "docker compose" in stripped:
                    offenders.append((rel, i, stripped[:100]))
    # Модульные Makefile (core/modules/*/Makefile) — включают module.mk (allowlist),
    # прямые вызовы в них запрещены
    for p in sorted((ROOT / "core" / "modules").rglob("Makefile")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "docker compose" in stripped:
                offenders.append((rel, i, stripped[:100]))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_shell_and_make_no_direct_docker_compose(caplog) -> None:
    """Shell/make files must not call `docker compose` directly (DevPlan 117 D70).

    Прямые docker compose вызовы разрешены ТОЛЬКО в канонических фасадах:
    core/entrypoints/compose-wrapper.sh + core/templates/module.mk. Остальное —
    через core.internal.shared.docker_compose (Python SoT).
    """
    offenders = _find_shell_make_docker_compose()
    if offenders:
        for rel, lineno, snippet in offenders:
            logger.error("[IMP:10][docker_sole_path][shell] %s:%d docker compose: %s", rel, lineno, snippet)
        pytest.fail(
            f"Direct `docker compose` in shell/make outside allowlist ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} [{snippet}]" for rel, lineno, snippet in offenders)
            + "\n\nSole path: core/internal/shared/docker_compose.py (U-13). "
            "Разрешённые shell-фасады: core/entrypoints/compose-wrapper.sh, core/templates/module.mk (D70)."
        )

    logger.info("[IMP:9][docker_sole_path][shell] PASS: 0 direct docker compose in shell/make outside allowlist")
