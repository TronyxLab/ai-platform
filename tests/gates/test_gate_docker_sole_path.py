#!/usr/bin/env python3
# GREP_SUMMARY: gate docker-sole-path subprocess docker-compose shared-only AST anti-drift sole-path
# STRUCTURE: ▶ AST-скан core/internal/*.py → ○ subprocess.* вызов с cmd: docker+compose → ◇ файл == shared/docker_compose.py? → PASS | ⟦RED: offenders⟧ → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Sole-path gate (DevPlan 116 B5 T10, U-13): docker compose subprocess-вызовы
##           разрешены ТОЛЬКО в core/internal/shared/docker_compose.py. 4 локальные копии
##           (docker_orchestrator, DeployEngine, reconciler, healthcheck_poller) удалены волной B5.
## @scope    AST-скан всех core/internal/*.py: subprocess.run/check_call/check_output/Popen/call,
##           где cmd содержит "docker"+"compose" (список `["docker", "compose", ...]` или
##           строка "docker compose"). Комментарии/докстринги исключаются (только AST-узлы вызовов).
## @invariants
##   - RED: любой docker compose subprocess-вызов вне shared/docker_compose.py
##   - allowlist: entrypoints/shell вне скоупа (сканируется только core/internal/*.py)
##   - Строковые литералы в docstring/log-сообщениях НЕ триггерят (только AST-вызовы)
##   - Звёздные элементы (["docker", "compose", *args, ...]) ловятся по строковым константам
## @rationale U-13: каждая волна добавляла 4-ю копию docker compose up/pull. Структурный
##            запрет возврата копий делает sole-path enforce-емым (парадигма self-verifying waves).
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
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
