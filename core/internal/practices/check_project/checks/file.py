# GREP_SUMMARY: check-project-file-checks, hygiene, commit-msg, grep-summary, docs-in-code, transition-traces-ban, agent-check, conventional-commits, GREP_SUMMARY-scan
# STRUCTURE: ▶ hygiene (trailing/CRLF/merge-conflict/private-key/toml-json) → ⊕ commit-msg (Conventional Commits regex) → ⊕ grep-summary (первые 10 строк) → ⊕ docs-in-code (docs/ + tracked .md allowlist) → ⊕ transition-traces-ban (legacy/переходн сканы) → ⊕ agent-check (grep-summary + ruff advisory SLF/FBT/ARG) → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  Файловые/SCM handler-и K1-канала (DevPlan 137/164, 170 W10-A декомпозиция):
##           проверки, сканирующие ФАЙЛЫ проекта (через files.py) и git-метаданные —
##           hygiene (трайлинг/CRLF/EOF/merge-conflict/private-key/toml-json, автофикс через
##           fixer.fix_hygiene), commit-msg (Conventional Commits, L3 non-blocking),
##           grep-summary (GREP_SUMMARY в первых 10 строках кода, full), docs-in-code
##           (инв.12: каталог docs/ + tracked .md вне allowlist → RED, baseline L3),
##           transition-traces-ban (следы перехода, full L3), agent-check (упрощённый 163 W-E:
##           grep-summary + ruff advisory SLF/FBT/ARG, full L3).
## @scope    Потребители: checks/__init__.py (реестр), runner (через _run_check). DI-канал
##           facts: EnvironmentFacts (which ruff для agent-check).
## @invariants
##   - hygiene: автофикс ТОЛЬКО при fix AND check.auto_fix; иначе FAIL с первым нарушением
##   - commit-msg: нет коммитов / git rc≠0 → PASS «no commits yet» (не блок)
##   - docs-in-code: allowlist .md (README/AGENTS/AI-PLATFORM в любом подкаталоге + .ai//.kilo/
##     префиксы); git ls-files — read-only (инв.12: git = truth); docs/ ловится ФС-сканом
##     (включая untracked), исключая EXCLUDED_DIRS
##   - transition: legacy/deprecated — в коде И комментариях; transition/переходн/временн —
##     ТОЛЬКО в комментариях (CSS/React-коллизии, TRAP[DECISION] W5-1); GENERATED-файлы вне
##   - agent-check: пустой код → SKIP (не PASS — честность R1); ruff advisory — python-only
## @rationale Группировка 6 файловых проверок: общий файловый слой (files.py) + SCM-граница
##            (git ls-files/log); отделён от tool-проверок (checks/tool.py).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:379-441,
##           692-729, 814-871, 1047-1195)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from core.internal.practices.check_project.exec import CMD_NOT_FOUND_RC, subprocess_run, tail
from core.internal.practices.check_project.files import (
    iter_code_files,
    iter_code_files_by_languages,
    iter_text_files,
    missing_grep_summary,
    parse_structured,
)
from core.internal.practices.check_project.fixer import fix_hygiene
from core.internal.practices.check_project.models import CheckResult
from core.internal.practices.check_project.runner import resolve_language
from core.internal.practices.constants import EXCLUDED_DIRS
from core.internal.practices.generators import GENERATED_HEADER
from core.internal.practices.manifest import PracticeCheck
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts

logger = logging.getLogger(__name__)

# ── Conventional Commits regex (commit-msg проверка) ──
_CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|init)(\([a-z0-9_-]+\))?!?: .+"
)


# ═══════════════════════════════════════════════════════════════════
# region CHECK_hygiene
def check_hygiene(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """hygiene: trailing/EOF/merge-conflict/private-key/toml/json (auto_fix в --fix)."""
    start = time.monotonic()
    violations: list[str] = []
    for path, _is_text in iter_text_files(project_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if content.endswith((" \n", "\t\n")) or "\r\n" in content:
            violations.append(f"trailing/CRLF: {path.relative_to(project_dir)}")
        if content and not content.endswith("\n"):
            violations.append(f"no-final-newline: {path.relative_to(project_dir)}")
        if "<<<<<<<" in content or ">>>>>>>" in content:
            violations.append(f"merge-conflict: {path.relative_to(project_dir)}")
        if "PRIVATE KEY" in content:
            violations.append(f"private-key: {path.relative_to(project_dir)}")
        if path.suffix in {".toml", ".json"} and not parse_structured(path):
            violations.append(f"invalid-syntax: {path.relative_to(project_dir)}")
    if violations:
        if fix and check.auto_fix:
            fix_hygiene(project_dir)
            message = (
                f"{len(violations)} violation(s) auto-fixed: {violations[0]} (+{max(0, len(violations) - 1)} more)"
            )
            return CheckResult(check.id, "PASS", message, time.monotonic() - start)
        return CheckResult(
            check.id,
            "FAIL",
            f"{len(violations)} violation(s): {violations[0]} (+{max(0, len(violations) - 1)} more)",
            time.monotonic() - start,
        )
    return CheckResult(check.id, "PASS", "no hygiene violations", time.monotonic() - start)


# endregion CHECK_hygiene


# ═══════════════════════════════════════════════════════════════════
# region CHECK_commit_msg
def check_commit_msg(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """commit-msg: последнее сообщение коммита — Conventional Commits (L3, non-blocking)."""
    rc, out, _, dur = subprocess_run(
        ["git", "-C", str(project_dir), "log", "-1", "--format=%s"], project_dir, check.timeout_sec
    )
    if rc != 0 or not out.strip():
        return CheckResult(check.id, "PASS", "no commits yet", dur)
    message = out.strip()
    if _CONVENTIONAL_COMMIT_RE.match(message):
        return CheckResult(check.id, "PASS", f"conventional: {message[:60]}", dur)
    return CheckResult(check.id, "FAIL", f"not conventional commit: {message[:60]}", dur)


# endregion CHECK_commit_msg


# ═══════════════════════════════════════════════════════════════════
# region CHECK_grep_summary
def check_grep_summary(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """grep-summary: GREP_SUMMARY в первых 10 строках файлов кода (full)."""
    missing = missing_grep_summary(project_dir, list(iter_code_files(project_dir)))
    if missing:
        return CheckResult(
            check.id,
            "FAIL",
            f"{len(missing)} file(s) missing GREP_SUMMARY: {missing[0]} (+{max(0, len(missing) - 1)})",
            0.0,
        )
    return CheckResult(check.id, "PASS", "GREP_SUMMARY present in code files", 0.0)


# endregion CHECK_grep_summary


# ═══════════════════════════════════════════════════════════════════
# region CHECK_docs_in_code
## @purpose  Инв.12 (docs-in-code) для проектов (DevPlan 164 W5-1): RED при каталоге docs/
##           в проекте ИЛИ tracked .md вне allowlist (README.md/AGENTS.md/AI-PLATFORM.md,
##           .ai/**, .kilo/**). Read-only: git ls-files "*.md" + ФС-скан docs/.
## @io       ⇥ check, project_dir, fix, facts → ⎋ CheckResult
## @complexity O(F + G) — ФС-файлы + git-tracked .md
_DOCS_IN_CODE_MD_ALLOWED_BASENAMES: frozenset[str] = frozenset({"README.md", "AGENTS.md", "AI-PLATFORM.md"})
_DOCS_IN_CODE_ALLOWED_PREFIXES: tuple[str, ...] = (".ai/", ".kilo/")


def _md_in_allowlist(rel: str) -> bool:
    """True если tracked .md в allowlist проекта (basename или .ai//.kilo/-префикс).

    ## @purpose  docs-in-code: единственная точка решения по .md (README/AGENTS/AI-PLATFORM
    ##           в любом подкаталоге + артефакты процессов .ai/ и конфиги .kilo/).
    ## @io       ⇥ rel: str → ⎋ bool
    ## @complexity O(1)
    """
    if Path(rel).name in _DOCS_IN_CODE_MD_ALLOWED_BASENAMES:
        return True
    return rel.startswith(_DOCS_IN_CODE_ALLOWED_PREFIXES)


def check_docs_in_code(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """docs-in-code (baseline L3): каталог docs/ ИЛИ tracked .md вне allowlist → FAIL."""
    start = time.monotonic()
    violations: list[str] = []
    # 1. Каталог docs/ — ФС-скан (ловит и untracked docs/), исключая EXCLUDED_DIRS
    for root, dirs, _files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        if "docs" in dirs:
            violations.append(str(Path(root) / "docs"))
    # 2. Tracked .md вне allowlist — git ls-files "*.md" (read-only, инв.12: git = truth)
    rc, out, err, _dur = subprocess_run(
        ["git", "-C", str(project_dir), "ls-files", "*.md"], project_dir, check.timeout_sec
    )
    if rc == 0:
        forbidden_md = [m for m in (line.strip() for line in out.splitlines()) if m and not _md_in_allowlist(m)]
        violations.extend(f"{m} (tracked .md вне allowlist)" for m in forbidden_md)
    else:
        logger.info(
            "[IMP:7][check_project][docs-in-code] git ls-files rc=%d — tracked .md check skipped: %s",
            rc,
            tail(err),
        )
    if violations:
        detail = violations[0] + (f" (+{len(violations) - 1} more)" if len(violations) > 1 else "")
        return CheckResult(check.id, "FAIL", f"docs-in-code violation: {detail}", time.monotonic() - start)
    return CheckResult(check.id, "PASS", "no docs/ dir; tracked .md in allowlist", time.monotonic() - start)


# endregion CHECK_docs_in_code


# ═══════════════════════════════════════════════════════════════════
# region CHECK_transition_traces_ban
## @purpose  Упрощённый S4-аналог (DevPlan 164 W5-1): следы перехода
##           (legacy/deprecated/transition/переходн/временн) в исходниках проекта (по языкам) —
##           RED вне allowlist (.ai/**, .kilo/**, GENERATED-файлы).
## @io       ⇥ check, project_dir, fix, facts → ⎋ CheckResult
## @complexity O(F * L) — файлы × строки
## @rationale Защита от ложных срабатываний: `transition` — CSS/React-идиома (transition-all,
##            transition={{...}}), `временн` встречается внутри «современн*» — для этих слов
##            матчим ТОЛЬКО в комментариях (\b уже отсекает «современных»); legacy/deprecated —
##            в коде и комментариях (однозначные маркеры).
# 🧐 TRAP[DECISION] · 2026-08-14 · — · transition-traces-ban: только комментарий-контекст для
# · transition/переходн/временн · Rejected: матч «transition» в любом месте строки · Reason:
# · CSS/React-коллизии (transition-all, transition={{...}}) — фронтенд-проекты были бы RED на
# · чистом коде (подтверждено botanika src/App.tsx) · Rev: появление кода-идентификатора
# · transition/переходн/временн как реального следа перехода (не комментария) — вернуть матч везде
_TRANSITION_TRACE_RE = re.compile(r"\blegacy\b|\bdeprecated\b|\btransition\b|\bпереходн|\bвременн", re.IGNORECASE)
_TRANSITION_ALLOWED_PREFIXES: tuple[str, ...] = (".ai/", ".kilo/")
_TRANSITION_CODE_COMMENT_ONLY: frozenset[str] = frozenset({"transition", "переходн", "временн"})


def _comment_before(line: str, pos: int, suffix: str) -> bool:
    """True если перед pos в строке есть маркер комментария (по суффиксу языка).

    ## @purpose  Комментарий-контекст для transition/переходн/временн (защита от CSS/React).
    ##           .py/.sh — # (+ docstring-маркеры); .ts/.tsx/.js/.jsx — // и /*.
    ## @io       ⇥ line: str, pos: int, suffix: str → ⎋ bool
    ## @complexity O(pos)
    """
    before = line[:pos]
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return "//" in before or "/*" in before
    return "#" in before or '"""' in before or "'''" in before


def _scan_transition_traces(content: str, suffix: str) -> list[tuple[int, str]]:
    """(lineno, word) для строк с transition-следами (legacy/deprecated — код и комментарии).

    ## @purpose  Построчный скан с правилом «комментарий-контекст» для слов-коллизий
    ##           (transition/переходн/временн); legacy/deprecated — где угодно в строке.
    ## @io       ⇥ content: str, suffix: str → ⎋ list[(lineno, word)]
    ## @complexity O(L * M) — строки × матчи
    """
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        for match in _TRANSITION_TRACE_RE.finditer(line):
            word = match.group(0)
            if word.lower() in _TRANSITION_CODE_COMMENT_ONLY and not _comment_before(line, match.start(), suffix):
                continue
            hits.append((lineno, word))
    return hits


def check_transition_traces_ban(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """transition-traces-ban (full L3): следы перехода вне allowlist → FAIL."""
    start = time.monotonic()
    languages = resolve_language(project_dir)
    violations: list[str] = []
    for path in iter_code_files_by_languages(project_dir, languages):
        rel = path.relative_to(project_dir).as_posix()
        if rel.startswith(_TRANSITION_ALLOWED_PREFIXES):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if content.startswith(GENERATED_HEADER):
            continue  # GENERATED-файлы вне allowlist (канон практик)
        for lineno, word in _scan_transition_traces(content, path.suffix.lower()):
            violations.append(f"{rel}:{lineno}: {word}")
    if violations:
        detail = violations[0] + (f" (+{len(violations) - 1} more)" if len(violations) > 1 else "")
        return CheckResult(check.id, "FAIL", f"transition traces: {detail}", time.monotonic() - start)
    return CheckResult(check.id, "PASS", "no legacy/deprecated/transition traces in sources", time.monotonic() - start)


# endregion CHECK_transition_traces_ban


# ═══════════════════════════════════════════════════════════════════
# region CHECK_agent_check
## @purpose  Упрощённый agent-check (DevPlan 163 W-E адаптация, 164 W5-1): grep-summary на
##           коде по языкам проекта + ruff advisory SLF/FBT/ARG (только python; иначе
##           «not applicable»). НЕ копия платформенного agent_check — только 2 быстрых шага.
## @io       ⇥ check, project_dir, fix, facts → ⎋ CheckResult
## @complexity O(F * S + R) — файлы × строки + один ruff-вызов
## @rationale  Фронтенд-проекты: ruff-шаг SKIP («not applicable for language»), grep-summary
##             остаётся (ts/react/sh несут GREP_SUMMARY). Пустой код → SKIP, не PASS (честность).
_ADVISORY_SELECT: str = "SLF,FBT,ARG"
_RUFF_FINDING_RE = re.compile(r"^\S+:\d+:\d+:\s+\S+\s+")


def _fmt_check_notes(notes: list[str]) -> str:
    """Форматировать суффикс-примечания сообщения: " (a; b)"."""
    return f" ({'; '.join(notes)})" if notes else ""


def check_agent_check(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """agent-check (full L3): grep-summary на коде проекта + ruff advisory SLF/FBT/ARG (python)."""
    start = time.monotonic()
    languages = resolve_language(project_dir)
    code_files = list(iter_code_files_by_languages(project_dir, languages))
    if not code_files:
        return CheckResult(check.id, "SKIP", "not applicable for language (no code files)", time.monotonic() - start)

    notes: list[str] = []
    violations: list[str] = []

    # 1. grep-summary-подобная проверка (канон gate grep-summary: GREP_SUMMARY в первых 10 строках)
    missing = missing_grep_summary(project_dir, code_files)
    violations.extend(f"grep-summary: {rel}" for rel in missing)

    # 2. ruff advisory SLF/FBT/ARG — только python (иначе not applicable for language)
    if "python" in languages:
        py_files = [p for p in code_files if p.suffix.lower() == ".py"]
        if not py_files:
            notes.append("no python files")
        elif (facts or default_env_facts()).which("ruff") is None:
            notes.append("ruff not installed — advisory step skipped")
        else:
            rc, out, _err, _dur = subprocess_run(
                ["ruff", "check", "--select", _ADVISORY_SELECT, *(str(p) for p in py_files)],
                project_dir,
                check.timeout_sec,
            )
            if rc == CMD_NOT_FOUND_RC:
                notes.append("ruff unavailable")
            else:
                finding_lines = [ln for ln in (out or "").splitlines() if _RUFF_FINDING_RE.match(ln)]
                violations.extend(f"ruff advisory {_ADVISORY_SELECT}: {ln.strip()[:120]}" for ln in finding_lines)
    else:
        notes.append(f"ruff advisory not applicable for language(s): {', '.join(sorted(languages))}")

    if violations:
        detail = violations[0] + (f" (+{len(violations) - 1} more)" if len(violations) > 1 else "")
        return CheckResult(check.id, "FAIL", f"{detail}{_fmt_check_notes(notes)}", time.monotonic() - start)
    message = "grep-summary + ruff advisory clean" + _fmt_check_notes(notes)
    return CheckResult(check.id, "PASS", message, time.monotonic() - start)


# endregion CHECK_agent_check
