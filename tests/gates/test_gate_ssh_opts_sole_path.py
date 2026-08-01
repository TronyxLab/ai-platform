#!/usr/bin/env python3
# GREP_SUMMARY: gate ssh-opts-sole-path SSH_OPTS BatchMode ConnectTimeout mirror anti-drift sole-path U-15
# STRUCTURE: ▶ (a) rg "Mirror lib/ssh.sh" → 0 → ◇ (b) AST: списки "-o"+"BatchMode=yes" вне ssh_opts.py → RED → ◇ (c) AST: "ConnectTimeout=\d+" литералы → только ssh_opts.py → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Sole-path gate (DevPlan 116 B5 T10, U-15): SSH_OPTS определён РОВНО в одном месте —
##           core/internal/shared/ssh_opts.py. 5 Python-копий («Mirror lib/ssh.sh») заменены
##           импортом волной B5; lib/ssh.sh — тонкий shell-фасад через `python3 -m ... --shell`.
## @scope    (a) grep «Mirror lib/ssh.sh» по core/ → 0;
##           (b) AST-скан core/internal/*.py: списки, содержащие "-o"+"BatchMode=yes" вне
##               shared/ssh_opts.py → RED (копия SSH_OPTS); allowlist: github-probe "ssh -T"
##               (context_promoter — отдельная команда, не копия SSH_OPTS);
##           (c) AST: строковые литералы "ConnectTimeout=<число>" → только в ssh_opts.py
##               (context_promoter github-probe использует f"ConnectTimeout={SSH_CONNECT_TIMEOUT}"
##               — f-string НЕ литерал, разрешён).
## @invariants
##   - Комментарии «Mirror lib/ssh.sh» устраняются (импорт канона вместо копирования, инвариант 2)
##   - ConnectTimeout литералы → только ssh_opts.py (единый SSH_CONNECT_TIMEOUT из timeouts)
##   - f-строки (f"ConnectTimeout={...}") НЕ являются литералами — не триггерят (c)
## @rationale U-15: ConnectTimeout=10 outlier (context_promoter) vs 30 (канон). Единый SoT
##            делает расхождение структурно невозможным; гейт запрещает возврат копий.
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE = ROOT / "core"
_CORE_INTERNAL = ROOT / "core" / "internal"
_ALLOWED_FILE = pathlib.Path("core/internal/shared/ssh_opts.py")

_CONNECT_TIMEOUT_LITERAL = re.compile(r"ConnectTimeout=\d+")


# ── (a) «Mirror lib/ssh.sh» → 0 ──────────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_no_mirror_lib_ssh_sh_comments(caplog) -> None:
    """«Mirror lib/ssh.sh» comments must be gone (U-15 — импорт канона вместо копирования)."""
    violations: list[tuple[str, int]] = []
    for p in sorted(_CORE.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if "Mirror lib/ssh.sh" in line:
                violations.append((p.relative_to(ROOT).as_posix(), i))

    if violations:
        for rel, lineno in violations:
            logger.error("[IMP:10][ssh_opts][a] %s:%d Mirror lib/ssh.sh", rel, lineno)
        pytest.fail(
            f"«Mirror lib/ssh.sh» comments found ({len(violations)}):\n"
            + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in violations)
            + "\n\nSSH_OPTS — единый SoT: core/internal/shared/ssh_opts.py (D1, U-15)."
        )

    logger.info("[IMP:9][ssh_opts][a] PASS: 0 «Mirror lib/ssh.sh» comments in core/")


# ── (b) списки "-o"+"BatchMode=yes" вне ssh_opts.py → RED ────────────────────


def _is_ssh_t_probe(cmd_node: ast.AST) -> bool:
    """True если список — github-probe `ssh -T` (context_promoter, allowlist по DevPlan T10).

    ▶ ┌cmd list┐ → ◇ head=="ssh" and "-T" in values? → ⎋ bool
    """
    if not isinstance(cmd_node, ast.List):
        return False
    values = [e.value for e in cmd_node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return bool(values) and values[0] == "ssh" and "-T" in values


def _find_ssh_opts_list_copies() -> list[tuple[str, int]]:
    """Find list literals containing both "-o" and "BatchMode=yes" outside ssh_opts.py.

    ▶ ┌_CORE_INTERNAL┐ → ○ AST walk → ◇ ast.List с "-o"+"BatchMode=yes", не в ssh_opts.py,
    │                     и НЕ github-probe ("ssh -T") → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        # Собираем списки, являющиеся cmd github-probe ("ssh -T") — allowlist
        probe_cmd_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess":
                    cmd_node = node.args[0] if node.args else None
                    if cmd_node is not None and _is_ssh_t_probe(cmd_node):
                        probe_cmd_ids.add(id(cmd_node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            if id(node) in probe_cmd_ids:
                continue  # github-probe (context_promoter) — allowlist DevPlan T10
            values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "-o" in values and "BatchMode=yes" in values:
                offenders.append((rel, node.lineno))
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_ssh_opts_list_sole_path(caplog) -> None:
    """SSH_OPTS copy lists (-o + BatchMode=yes) must exist ONLY in shared/ssh_opts.py."""
    offenders = _find_ssh_opts_list_copies()
    if offenders:
        for rel, lineno in offenders:
            logger.error("[IMP:10][ssh_opts][b] %s:%d SSH_OPTS copy list", rel, lineno)
        pytest.fail(
            f"SSH_OPTS copy lists found outside shared/ssh_opts.py ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in offenders)
            + "\n\nImport from core.internal.shared.ssh_opts (D1, U-15)."
        )

    logger.info("[IMP:9][ssh_opts][b] PASS: 0 SSH_OPTS copy lists outside shared/ssh_opts.py")


# ── (c) ConnectTimeout=<число> литералы → только ssh_opts.py ─────────────────


def _find_connect_timeout_literals() -> list[tuple[str, int, str]]:
    """Find string literals matching ConnectTimeout=<digits> outside ssh_opts.py.

    ▶ ┌_CORE_INTERNAL┐ → ○ AST walk → ◇ ast.Constant str с "ConnectTimeout=\d+" → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        offenders.extend(
            (rel, node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _CONNECT_TIMEOUT_LITERAL.search(node.value)
        )
    return offenders


@pytest.mark.gate
@ldd_trajectory
def test_connect_timeout_literals_sole_path(caplog) -> None:
    """ConnectTimeout=<number> literals must exist ONLY in shared/ssh_opts.py (единый таймаут)."""
    offenders = _find_connect_timeout_literals()
    if offenders:
        for rel, lineno, val in offenders:
            logger.error("[IMP:10][ssh_opts][c] %s:%d ConnectTimeout literal: %s", rel, lineno, val)
        pytest.fail(
            f"ConnectTimeout literals found outside shared/ssh_opts.py ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {val!r}" for rel, lineno, val in offenders)
            + "\n\nUse timeouts.SSH_CONNECT_TIMEOUT (f-string) — единый таймаут (U-15)."
        )

    logger.info("[IMP:9][ssh_opts][c] PASS: 0 ConnectTimeout literals outside shared/ssh_opts.py")
