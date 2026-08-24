"""agent_check.runners — исполнение L1-сигнала (T3.2): git-changed, ruff/basedpyright/static/bespoke.

# GREP_SUMMARY: agent-check runners ruff basedpyright static bespoke fp-registry orchestration run dedupe T3.2
# STRUCTURE: ▶ _git_changed (py/sh/mk) → ⊕ run_ruff blocking → ⊕ run_ruff advisory (×fp-verdict)
#            → ⊕ run_basedpyright (--level error) → ⊕ run_static (--changed subprocess)
#            → ⊕ check_doc_headers (py+sh) → ⊕ _dedupe → ⎋ run() → report (json|human в __init__ CLI)
"""
# region MODULE_CONTRACT
## @purpose  Исполнение L1-сигнала agent-check (DevPlan 163 W-E): changed-набор (git diff
##           HEAD + untracked), шаги ruff blocking/advisory, basedpyright, static check
##           --changed, bespoke doc-headers, FP-реестр advisory-вердиктов (fp_registry.yaml),
##           дедупликация, оркестрация run() и форматирование отчёта (json/human).
##           Выделен из __init__.py (T3.2) — здесь ВСЯ бизнес-логика и I/O.
## @scope    core/internal/agent_check/runners.py — импортирует types (данные, ТОЛЬКО публичные
##           имена — детектор static private-imports); __init__.py (фасад+CLI) реэкспортирует
##           run/шаги для `from core.internal.agent_check import X`. Форматирование human-отчёта
##           (_human_report) живёт в __init__.py (CLI-слой), НЕ здесь.
## @invariants (перенесены из __init__.py 1:1)
##   - stdout: ровно один отчёт (JSON при --json, иначе human); логи — только stderr
##   - exit 0 = нет blocking-находок; exit 1 = есть blocking; advisory/off — не влияют
##   - Пустой diff / changed без .py/.sh → exit 0 (allow_no_tests-семантика T1.3)
##   - git-сбой → WARNING + пустой changed-набор (не блокирует сигнал; полную гарантию
##     дают make check / pre-commit / gate — L2+ слои)
##   - Вердикт advisory-правил — из core/internal/agent_check/fp_registry.yaml
##     (verdict: blocking | advisory | off); файл отсутствует → все advisory (WARNING)
##   - Прогон только по changed (N файлов = N-список в одном вызове тула)
##   - Дедупликация находок по (rule, file, line, message) — правило, ставшее blocking
##     в fp_registry, не дублируется между ruff-шагом и advisory-шагом
##   - DI-HYG (DevPlan 163 §5): env-чтения ТОЛЬКО через параметр environ — никаких
##     скрытых os.environ внутри функций
##   - Инфраструктурный сбой шага (тул не найден / не-JSON вывод) → видимая находка
##     rule="agent-check-infra" severity=error (fail-visible, конституция §4)
## @rationale Декомпозиция god-модуля (T3.2, прецедент check_suite W3): 1092 LOC → types/
##            runners/__init__. ЛDD-логи [IMP:*] и тексты отчётов сохранены байт-в-байт
##            (гейты/тесты пинят). Импортирует только types + shared (subprocess_io/timeouts)
##            — ацикличность пакета сохраняется (import-linter acyclic-internal-domains).
## @changes 2026-08-22 | T3.2 (1787342045763) — extracted from __init__.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 30 (команды-пробы) → CONVERGE_DOCKER_TIMEOUT; 60 (ruff/static) → SYSTEM_CMD_TIMEOUT;
# 120 (basedpyright) → LIFECYCLE_CMD_TIMEOUT.
from core.internal.agent_check.types import (
    SEVERITY_ERROR,
    VERDICTS,
    AgentCheckReport,
    AgentFinding,
    BasedpyrightOutputDict,
    ChangedFiles,
    CheckEntryDict,
    FpRegistryDict,
    RuffItemDict,
    StaticOutputDict,
)
from core.internal.shared.subprocess_io import run_subprocess_streaming
from core.internal.shared.timeouts import (
    CONVERGE_DOCKER_TIMEOUT,
    LIFECYCLE_CMD_TIMEOUT,
    SYSTEM_CMD_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Корень репозитория: core/internal/agent_check/runners.py → parents[0]=agent_check,
# [1]=internal, [2]=core, [3]=repo (T3.2: исполнение перенесено из __init__.py → runners.py;
#  parents[3] инвариантен к имени файла пакета — та же глубина, 170 W10-C)
_REPO_ROOT = Path(__file__).resolve().parents[3]

# FP-реестр advisory-правил (W-E E4) — путь ОТНОСИТЕЛЬНО root
_FP_REGISTRY_REL = Path("core") / "internal" / "agent_check" / "fp_registry.yaml"

# Advisory-правила вне select ruff.toml (C90 → defer-реестр §4.4: complexity → advisory-сигнал)
_ADVISORY_SELECT = "SLF,FBT,ARG,C90"

# Дефолтные вердикты advisory-селекторов (перекрываются fp_registry.yaml)
_ADVISORY_DEFAULT_VERDICTS: dict[str, str] = {
    "SLF": "advisory",
    "FBT": "advisory",
    "ARG": "advisory",
    "C90": "advisory",
}

# Код ruff-правила → селектор (для маппинга находки в вердикт реестра)
_RULE_SELECTOR_BY_CODE: dict[str, str] = {
    "SLF001": "SLF",
    "FBT001": "FBT",
    "FBT002": "FBT",
    "FBT003": "FBT",
    "ARG001": "ARG",
    "ARG002": "ARG",
    "ARG003": "ARG",
    "ARG004": "ARG",
    "ARG005": "ARG",
    "C901": "C90",
}

# Bespoke doc-header: GREP_SUMMARY обязан быть в первых N строках (канон gate grep-summary)
_HEADER_LINES = 10


# region FUNC__venv_tool
def _venv_tool(name: str, environ: Mapping[str, str]) -> str | None:
    """Найти бинарь тула: venv-сосед sys.executable, затем PATH.

    # ▶ ┌name, environ┐ → ◇ (sys.executable.parent/name) executable? → ⎋ venv-путь | shutil.which(name)

    ## @purpose  Детерминированный резолв ruff/basedpyright/git: make-таргет зовёт
    ##           $(PYTHON) (.venv/bin/python) → тулы рядом (.venv/bin/ruff). Fallback —
    ##           PATH из environ (DI: никаких скрытых os.environ).
    ## @io       ⇥ name: str, environ: Mapping[str, str] → ⎋ str | None
    ## @complexity  O(1)
    ## @invariants  venv-путь проверяется на executable; PATH читается ТОЛЬКО из environ.
    ##              ⚠️ БЕЗ resolve(): sys.executable = .venv/bin/python (invoked path),
    ##              а .venv/bin/python → python3.14 → /opt/homebrew/... (двойной symlink);
    ##              resolve() уводит в Cellar, где ruff/basedpyright отсутствуют
    """
    for base in (Path(sys.executable).parent, Path(sys.executable).resolve().parent):
        venv_bin = base / name
        if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
            logger.info("[IMP:7][tool][venv] %s -> %s", name, venv_bin)
            return str(venv_bin)
    found = shutil.which(name, path=environ.get("PATH"))
    logger.info("[IMP:7][tool][path] %s -> %s", name, found or "NOT FOUND")
    return found


# endregion FUNC__venv_tool


# region FUNC__rel
def _rel(root: Path, path: str) -> str:
    """Нормализовать путь тула (абсолютный) в repo-relative posix.

    # ▶ ┌root, path┐ → ○ os.path.relpath → ○ as_posix → ⎋ str

    ## @purpose  ruff/basedpyright отдают АБСОЛЮТНЫЕ filenames в JSON — конвертация
    ##           в единый repo-relative формат находок.
    ## @io       ⇥ root: Path, path: str → ⎋ str
    ## @complexity  O(1)
    """
    if Path(path).is_absolute():
        try:
            return Path(os.path.relpath(path, root)).as_posix()
        except ValueError:
            return Path(path).as_posix()
    return Path(path).as_posix()


# endregion FUNC__rel


# region FUNC__git_changed
def _git_changed(root: Path, environ: Mapping[str, str]) -> ChangedFiles:
    """Собрать изменённые файлы: git diff --name-only HEAD + untracked.

    # ▶ ┌root┐ → ○ git diff --name-only HEAD → ○ git ls-files --others → ⊕ set-union
    #   → ○ существующие файлы → ○ категоризация (py/sh/mk) → ⎋ ChangedFiles

    ## @purpose  Источник changed-набора L1-сигнала. Удалённые файлы (в diff, но не на
    ##           диске) отфильтровываются — тулы не умеют анализировать отсутствующие.
    ## @io       ⇥ root: Path, environ: Mapping[str, str] → ⎋ ChangedFiles
    ## @complexity  O(N log N) — git-выводы + сортировка
    ## @invariants  git-сбой (не репозиторий/нет HEAD) → WARNING + пустой набор
    ##              (L1 не блокируется инфраструктурой; полная гарантия — L2+)
    """
    git_bin = _venv_tool("git", environ) or "git"
    rels: set[str] = set()
    commands: tuple[list[str], ...] = (
        [git_bin, "diff", "--name-only", "HEAD", "--"],
        [git_bin, "ls-files", "--others", "--exclude-standard"],
    )
    for cmd in commands:
        try:
            # DevPlan 006 W4: streaming-канон (killpg/heartbeat; stream=False — быстрый git-вызов)
            proc = run_subprocess_streaming(
                cmd,
                cwd=str(root),
                timeout=CONVERGE_DOCKER_TIMEOUT,
                stream=False,
                heartbeat=0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("[IMP:7][git][changed] %s failed: %s — empty changed set", cmd[1], exc)
            continue
        if proc.returncode != 0:
            logger.warning(
                "[IMP:7][git][changed] %s rc=%d stderr=%s — empty changed set",
                cmd[1],
                proc.returncode,
                (proc.stderr or "").strip()[-200:],
            )
            continue
        rels.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
        logger.info("[IMP:8][git][changed] %s -> %d path(s)", cmd[1], len(rels))

    existing = sorted(r for r in rels if (root / r).is_file())
    py = tuple(r for r in existing if r.endswith(".py"))
    sh = tuple(r for r in existing if r.endswith(".sh"))
    makefiles = tuple(r for r in existing if r == "Makefile" or (r.startswith("makefiles/") and r.endswith(".mk")))
    logger.info(
        "[IMP:9][git][changed] total=%d py=%d sh=%d makefiles=%d",
        len(existing),
        len(py),
        len(sh),
        len(makefiles),
    )
    return ChangedFiles(py=py, sh=sh, makefiles=makefiles)


# endregion FUNC__git_changed


# region FUNC__infra_finding
def _infra_finding(step: str, message: str) -> AgentFinding:
    """Инфраструктурная находка шага (тул не найден / не-JSON вывод).

    # ▶ ┌step, message┐ → ⎋ AgentFinding(agent-check-infra, severity=error)

    ## @purpose  Fail-visible (конституция §4): сбой шага НЕ выглядит «чисто».
    ## @io       ⇥ step: str, message: str → ⎋ AgentFinding
    ## @complexity  O(1)
    """
    logger.error("[IMP:10][%s][infra] %s", step, message)
    return AgentFinding(
        rule="agent-check-infra",
        file="",
        line=0,
        message=f"[{step}] {message}",
        fixable=False,
        severity=SEVERITY_ERROR,
        source="agent-check",
    )


# endregion FUNC__infra_finding


# region FUNC_run_ruff
def run_ruff(
    root: Path,
    files: tuple[str, ...],
    select: str | None,
    environ: Mapping[str, str],
) -> tuple[list[AgentFinding], float]:
    """Прогнать ruff check по списку файлов (JSON-вывод).

    # ▶ ┌root, files, select┐ → ◇ files пусто? skip → ◇ ruff найден? → ○ subprocess
    #   → ○ json.loads → ⊕ AgentFinding[code, rel, row, msg, fixable] → ⎋ (findings, ms)

    ## @purpose  Blocking-шаг (select=None → текущий ruff.toml) и advisory-шаг
    ##           (select=SLF,FBT,ARG,C90 — CLI --select ЗАМЕНЯЕТ select конфига, проверено).
    ## @io       ⇥ root: Path, files: tuple[str, ...], select: str | None,
    ##           environ: Mapping[str, str] → ⎋ (list[AgentFinding], float ms)
    ## @complexity  O(F) — один ruff-вызов на весь changed-список
    ## @invariants  exit-код ruff НЕ влияет (нарушения = rc 1, JSON в stdout);
    ##              не-JSON stdout при rc≠0 → infra-находка; timeout=60
    """
    start = time.monotonic()
    if not files:
        logger.info("[IMP:7][ruff][skip] no changed .py files")
        return [], 0.0
    ruff = _venv_tool("ruff", environ)
    if ruff is None:
        return [_infra_finding("ruff", "ruff binary not found in venv or PATH")], _elapsed_ms(start)
    cmd = [ruff, "check", "--output-format", "json"]
    if select:
        cmd += ["--select", select]
    cmd += list(files)
    logger.info("[IMP:8][ruff][run] %s (%d file(s))", "advisory" if select else "blocking", len(files))
    try:
        # DevPlan 006 W4: streaming-канон; stream=False — stdout = ruff JSON (machine-readable)
        proc = run_subprocess_streaming(cmd, cwd=str(root), timeout=SYSTEM_CMD_TIMEOUT, stream=False, heartbeat=0)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_infra_finding("ruff", f"subprocess failed: {exc}")], _elapsed_ms(start)
    if proc.returncode != 0 and not proc.stdout.strip():
        return [
            _infra_finding("ruff", f"rc={proc.returncode} without JSON — stderr: {(proc.stderr or '').strip()[-300:]}")
        ], _elapsed_ms(start)
    try:
        # W11-G4: stdlib json → Any; ruff-элемент — типизированный контракт (обязательные ключи)
        raw = cast(list[RuffItemDict], json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError as exc:
        return [_infra_finding("ruff", f"unparseable JSON output: {exc}")], _elapsed_ms(start)
    findings = [
        AgentFinding(
            rule=str(item["code"]),
            file=_rel(root, str(item["filename"])),
            line=int(item["location"]["row"]),
            message=str(item["message"]),
            fixable="fix" in item,
            severity=SEVERITY_ERROR,
            source="ruff",
        )
        for item in raw
    ]
    logger.info(
        "[IMP:9][ruff][%s] %d finding(s) in %.0f ms",
        "advisory" if select else "blocking",
        len(findings),
        _elapsed_ms(start),
    )
    return findings, _elapsed_ms(start)


# endregion FUNC_run_ruff


# region FUNC_run_basedpyright
def run_basedpyright(
    root: Path,
    files: tuple[str, ...],
    environ: Mapping[str, str],
) -> tuple[list[AgentFinding], float]:
    """Прогнать basedpyright в файловом режиме (--level error, --outputjson).

    # ▶ ┌root, files┐ → ◇ files пусто? skip → ◇ basedpyright найден? → ○ subprocess
    #   → ○ parse generalDiagnostics (severity=error, line 0-based → +1) → ⎋ (findings, ms)

    ## @purpose  Type-ошибки в changed-файлах (M2: recommended без baseline, --level error).
    ##           Файловый режим поддерживает список файлов (проверено 1.39.9).
    ## @io       ⇥ root: Path, files: tuple[str, ...], environ: Mapping[str, str]
    ##           → ⎋ (list[AgentFinding], float ms)
    ## @complexity  O(F) — один basedpyright-процесс на весь список (шаринг кэша типов)
    ## @invariants  exit-код basedpyright с --outputjson НЕ надёжен (rc=1 при 0 ошибок,
    ##              quirk 1.39.9) → exit выводится из summary.errorCount; timeout=120
    """
    start = time.monotonic()
    if not files:
        logger.info("[IMP:7][basedpyright][skip] no changed .py files")
        return [], 0.0
    basedpyright = _venv_tool("basedpyright", environ)
    if basedpyright is None:
        return [_infra_finding("basedpyright", "basedpyright binary not found in venv or PATH")], _elapsed_ms(start)
    cmd = [basedpyright, "--level", "error", "--outputjson", *files]
    logger.info("[IMP:8][basedpyright][run] %d file(s)", len(files))
    try:
        # DevPlan 006 W4: streaming-канон; stream=False — stdout = basedpyright JSON
        proc = run_subprocess_streaming(cmd, cwd=str(root), timeout=LIFECYCLE_CMD_TIMEOUT, stream=False, heartbeat=0)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_infra_finding("basedpyright", f"subprocess failed: {exc}")], _elapsed_ms(start)
    if not proc.stdout.strip():
        return [
            _infra_finding(
                "basedpyright", f"rc={proc.returncode} without JSON — stderr: {(proc.stderr or '').strip()[-300:]}"
            )
        ], _elapsed_ms(start)
    try:
        # W11-G4: stdlib json → Any; basedpyright-контракт типизирован (summary/generalDiagnostics)
        data = cast(BasedpyrightOutputDict, json.loads(proc.stdout))
    except json.JSONDecodeError as exc:
        return [_infra_finding("basedpyright", f"unparseable JSON output: {exc}")], _elapsed_ms(start)
    error_count = int((data.get("summary") or {}).get("errorCount", 0))
    findings = [
        AgentFinding(
            rule=str(diag.get("rule") or "basedpyright"),
            file=_rel(root, str(diag["file"])),
            line=(
                int(diag["range"]["start"]["line"]) + 1 if "range" in diag else 0
            ),  # LSP 0-based → 1-based; file-level diag → 0
            message=str(diag["message"]),
            fixable=False,
            severity=SEVERITY_ERROR,
            source="basedpyright",
        )
        for diag in data.get("generalDiagnostics") or []
        if diag.get("severity") == "error"
    ]
    if len(findings) != error_count:
        logger.warning(
            "[IMP:7][basedpyright][warn] errorCount=%d but %d error diag(s) parsed",
            error_count,
            len(findings),
        )
    logger.info("[IMP:9][basedpyright] %d error(s) in %.0f ms", len(findings), _elapsed_ms(start))
    return findings, _elapsed_ms(start)


# endregion FUNC_run_basedpyright


# region FUNC_run_static
def run_static(root: Path) -> tuple[list[AgentFinding], float]:
    """Прогнать статический слой W-C: `python3 -m core.internal.static check --changed --json`.

    # ▶ ┌root┐ → ○ subprocess(static check --changed --json) → ○ parse {findings}
    #   → ⊕ AgentFinding (fixable=False) → ⎋ (findings, ms)

    ## @purpose  AST/структурные детекторы (dead-code, cross-layer, verb-register,
    ##           exception-patterns и др. — реестр W-C) по changed-файлам. Субпроцесс
    ##           сохраняет границу пакетов; --root передаётся ЯВНО — root-консистентность
    ##           (и тестируемость в изолированном git-репо). cwd=_REPO_ROOT гарантирует
    ##           импортируемость `core` при любом --root (python -m добавляет cwd в
    ##           sys.path), а git diff внутри static исполняется с cwd=root (его код).
    ## @io       ⇥ root: Path → ⎋ (list[AgentFinding], float ms)
    ## @complexity  ∑ детекторов static (changed-скоуп)
    ## @invariants  НЕ-JSON stdout / rc≠0 без JSON → infra-находка; timeout=60
    """
    start = time.monotonic()
    cmd = [sys.executable, "-m", "core.internal.static", "check", "--changed", "--json", "--root", str(root)]
    logger.info("[IMP:8][static][run] %s", " ".join(cmd))
    try:
        # DevPlan 006 W4: streaming-канон; stream=False — stdout = static JSON
        proc = run_subprocess_streaming(cmd, cwd=str(_REPO_ROOT), timeout=SYSTEM_CMD_TIMEOUT, stream=False, heartbeat=0)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_infra_finding("static", f"subprocess failed: {exc}")], _elapsed_ms(start)
    if not proc.stdout.strip():
        return [
            _infra_finding(
                "static", f"rc={proc.returncode} without JSON — stderr: {(proc.stderr or '').strip()[-300:]}"
            )
        ], _elapsed_ms(start)
    try:
        # W11-G4: stdlib json → Any; статический слой отдаёт FindingDict-совместимый JSON
        data = cast(StaticOutputDict, json.loads(proc.stdout))
    except json.JSONDecodeError as exc:
        return [_infra_finding("static", f"unparseable JSON output: {exc}")], _elapsed_ms(start)
    findings = [
        AgentFinding(
            rule=str(f["rule"]),
            file=str(f.get("file") or ""),
            line=int(f.get("line") or 0),
            message=str(f.get("message") or ""),
            fixable=False,
            severity=str(f.get("severity") or SEVERITY_ERROR),
            source="static",
        )
        for f in data.get("findings") or []
    ]
    logger.info("[IMP:9][static] %d finding(s) in %.0f ms", len(findings), _elapsed_ms(start))
    return findings, _elapsed_ms(start)


# endregion FUNC_run_static


# region FUNC_check_doc_headers
def check_doc_headers(
    root: Path,
    files: tuple[str, ...],
) -> tuple[list[AgentFinding], float]:
    """Bespoke-проверка doc-заголовков (упрощённая) на changed .py/.sh.

    # ▶ ┌root, files┐ → ○ read_text → ◇ GREP_SUMMARY в первых 10 строках? → ◇ STRUCTURE?
    #   → ◇ # region MODULE_CONTRACT пара? → ◇ region-баланс? → ⊕ Findings → ⎋ (findings, ms)

    ## @purpose  Быстрый сигнал анти-дрейфа разметки (GREP_SUMMARY-канон gate grep-summary +
    ##           проектные STRUCTURE/MODULE_CONTRACT/region-баланс). Упрощённая версия —
    ##           полный валидатор в core/internal/lint/, полный детектор — W-C фаза 2.
    ## @io       ⇥ root: Path, files: tuple[str, ...] → ⎋ (list[AgentFinding], float ms)
    ## @complexity  O(F * S) — файлы × строки
    ## @invariants  GREP_SUMMARY обязателен в первых 10 строках (канон гейта);
    ##              STRUCTURE — в первых 10 строках; MODULE_CONTRACT — пара region;
    ##              region-баланс: # region == # endregion по всему файлу
    """
    start = time.monotonic()
    findings: list[AgentFinding] = []
    for rel in files:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("[IMP:7][bespoke][doc-header] cannot read %s: %s", rel, exc)
            continue
        lines = text.splitlines()
        head = "\n".join(lines[:_HEADER_LINES])
        if "GREP_SUMMARY:" not in head:
            findings.append(
                AgentFinding(
                    rule="grep-summary",
                    file=rel,
                    line=1,
                    message=f"missing GREP_SUMMARY: in first {_HEADER_LINES} lines",
                    source="bespoke",
                )
            )
        if "STRUCTURE:" not in head:
            findings.append(
                AgentFinding(
                    rule="structure",
                    file=rel,
                    line=1,
                    message=f"missing STRUCTURE: in first {_HEADER_LINES} lines",
                    source="bespoke",
                )
            )
        if not any(line.strip().startswith("# region MODULE_CONTRACT") for line in lines):
            findings.append(
                AgentFinding(
                    rule="module-contract",
                    file=rel,
                    line=1,
                    message="missing # region MODULE_CONTRACT marker",
                    source="bespoke",
                )
            )
        opens = sum(1 for line in lines if line.strip().startswith("# region "))
        closes = sum(1 for line in lines if line.strip().startswith("# endregion "))
        if opens != closes:
            findings.append(
                AgentFinding(
                    rule="region-balance",
                    file=rel,
                    line=1,
                    message=f"# region/# endregion imbalance: {opens} open vs {closes} close",
                    source="bespoke",
                )
            )
        if not findings or any(f.file == rel for f in findings):
            logger.info(
                "[IMP:8][bespoke][doc-header] %s: %d finding(s)",
                rel,
                sum(1 for f in findings if f.file == rel),
            )
    logger.info(
        "[IMP:9][bespoke][doc-header] %d finding(s) on %d file(s) in %.0f ms",
        len(findings),
        len(files),
        _elapsed_ms(start),
    )
    return findings, _elapsed_ms(start)


# endregion FUNC_check_doc_headers


# region FUNC_load_fp_registry
def load_fp_registry(root: Path) -> dict[str, str]:
    """Загрузить вердикты advisory-правил из fp_registry.yaml.

    # ▶ ┌root┐ → ○ read fp_registry.yaml → ○ yaml.safe_load → ○ rules[] → ⊕ {selector: verdict} → ⎋

    ## @purpose  Механическая связка FP-журнала (W-E E4) и L1-сигнала: verdict правила
    ##           (blocking|advisory|off) решает, куда попадают находки advisory-прогона.
    ## @io       ⇥ root: Path → ⎋ dict[str, str]
    ## @complexity  O(R) — число записей реестра
    ## @invariants  Файл отсутствует/не парсится → дефолты (все advisory) + WARNING;
    ##              невалидный verdict записи игнорируется (fail-visible WARNING)
    """
    verdicts = dict(_ADVISORY_DEFAULT_VERDICTS)
    path = root / _FP_REGISTRY_REL
    if not path.is_file():
        logger.warning("[IMP:8][registry] %s missing — advisory defaults used", _FP_REGISTRY_REL)
        return verdicts
    try:
        # W11-G4: pyyaml без stubs → Any; cast на границе (структура fp_registry — rules[])
        data = cast(FpRegistryDict, yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {})
    except yaml.YAMLError as exc:
        logger.warning("[IMP:8][registry] cannot parse %s: %s — advisory defaults", _FP_REGISTRY_REL, exc)
        return verdicts
    rules = data.get("rules") or []
    for entry in rules:
        rule = str(entry.get("rule") or "").strip().upper()
        verdict = str(entry.get("verdict") or "").strip().lower()
        if rule and verdict in VERDICTS:
            verdicts[rule] = verdict
            logger.info("[IMP:8][registry] rule=%s verdict=%s", rule, verdict)
        elif rule:
            logger.warning("[IMP:7][registry] invalid verdict %r for rule %r — ignored", verdict, rule)
    logger.info("[IMP:9][registry] %d verdict(s) loaded from %s", len(verdicts), _FP_REGISTRY_REL)
    return verdicts


# endregion FUNC_load_fp_registry


# region FUNC__selector_verdict
def _selector_verdict(code: str, verdicts: dict[str, str]) -> str:
    """Вердикт реестра для кода ruff-правила (SLF001 → SLF).

    # ▶ ┌code, verdicts┐ → ◇ code in _RULE_SELECTOR_BY_CODE? → verdicts.get(selector, "advisory") → ⎋ str

    ## @purpose  Маппинг кода находки в verdict FP-реестра; неизвестный код → advisory.
    ## @io       ⇥ code: str, verdicts: dict[str, str] → ⎋ str ("blocking"|"advisory"|"off")
    ## @complexity  O(1)
    """
    selector = _RULE_SELECTOR_BY_CODE.get(code)
    if selector is None:
        return "advisory"
    return verdicts.get(selector, "advisory")


# endregion FUNC__selector_verdict


# region FUNC__dedupe
def _dedupe(findings: list[AgentFinding]) -> list[AgentFinding]:
    """Дедупликация по (rule, file, line, message) — стабильный порядок.

    # ▶ ┌findings┐ → ○ dict key=(rule,file,line,message) → ⎋ list (первое вхождение)

    ## @purpose  Правило, ставшее blocking в fp_registry, может встретиться и в
    ##           ruff-шаге (если оно в select ruff.toml), и в advisory-шаге — дубль недопустим.
    ## @io       ⇥ findings: list[AgentFinding] → ⎋ list[AgentFinding]
    ## @complexity  O(N)
    """
    seen: set[tuple[str, str, int, str]] = set()
    unique: list[AgentFinding] = []
    for finding in findings:
        key = (finding.rule, finding.file, finding.line, finding.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


# endregion FUNC__dedupe


# region FUNC__elapsed_ms
def _elapsed_ms(start: float) -> float:
    """Разница от start в миллисекундах (округление 1 знак).

    ## @purpose  Единая метрика длительности шага (телеметрия agent-loop G4.2).
    ## @io       ⇥ start: float (time.monotonic) → ⎋ float ms
    ## @complexity  O(1)
    """
    return round((time.monotonic() - start) * 1000, 1)


# endregion FUNC__elapsed_ms


# region FUNC_run
# ⚡ TRAP[PERF] · 2026-08-13 · >250 changed .py · full-repo change ≈8.1 s (basedpyright 5.85 s)
# · Root: basedpyright — единственный масштабируемый шаг (0.7 s / 13 файлов → 5.9 s / ~250 файлов);
# ·   типовое изменение агента (2-20 файлов) = 0.9-1.5 s — метрика §10 (<5 s) достигнута на типике.
# · Mit: шаги независимы → распараллелить (ThreadPoolExecutor: ruff∥advisory∥basedpyright∥static∥bespoke)
# ·   урезает worst-case до max(basedpyright, static+ruff+bespoke) ≈ 6 s; опция «срез changed-скоупа»
# ·   (пропуск basedpyright для чисто-косметических изменений). Не внедрено: типика 0.9-1.5 s,
# ·   full-repo правки идут через make check/gate (L2+), параллелизм добавил бы сложность.
# · Rev: если типовое изменение стабильно >5 s ИЛИ full-repo правки чаще 1/день — внедрить
# ·   распараллеливание шагов (структура шагов уже готова: (findings, ms) кортежи).
def run(root: Path, environ: Mapping[str, str]) -> tuple[int, AgentCheckReport]:
    """Оркестрация всех шагов L1-сигнала: changed → шаги → отчёт → exit code.

    # ▶ ┌root, environ┐ → ○ _git_changed → ○ load_fp_registry → ○ ruff-blocking →
    #   advisory (×verdict) → basedpyright → static → bespoke → ⊕ dedupe blocking →
    #   ◇ clean? → ⎋ (exit_code, report_dict)

    ## @purpose  Единственная точка конвейера (E1 пп. 1-7): собирает blocking-находки
    ##           (exit 1) и advisory-секцию (не влияет на exit). Возвращает кортеж
    ##           (int, dict) — тестируемо без sys.exit (exit-code-контракт core).
    ## @io       ⇥ root: Path, environ: Mapping[str, str]
    ##           ⎋ (exit_code: int, report: dict[str, Any])
    ## @complexity  ∑ шагов — каждый шаг по changed-списку
    ## @invariants  blocking = ruff(select=конфиг) + advisory-находки c verdict=blocking
    ##              + basedpyright + static + bespoke; advisory-секция — только verdict=advisory;
    ##              exit = 1 ⇔ есть blocking; пустой changed → clean exit 0
    """
    started = time.monotonic()
    changed = _git_changed(root, environ)
    logger.info("[IMP:8][run] changed: %s", changed.to_dict())

    verdicts = load_fp_registry(root)

    # Blocking-шаг ruff (текущий ruff.toml select)
    ruff_findings, ruff_ms = run_ruff(root, changed.py, None, environ)

    # Advisory-шаг ruff (SLF/FBT/ARG/C90) — фильтр и разбиение по вердикту реестра
    advisory_raw, advisory_ms = run_ruff(root, changed.py, _ADVISORY_SELECT, environ)
    advisory_blocking: list[AgentFinding] = []
    advisory_report: list[AgentFinding] = []
    for finding in advisory_raw:
        verdict = _selector_verdict(finding.rule, verdicts)
        if verdict == "off":
            logger.info(
                "[IMP:7][advisory][off] %s %s:%s — suppressed by fp_registry", finding.rule, finding.file, finding.line
            )
        elif verdict == "blocking":
            advisory_blocking.append(finding)
        else:
            advisory_report.append(finding)

    basedpyright_findings, basedpyright_ms = run_basedpyright(root, changed.py, environ)

    static_findings, static_ms = run_static(root)

    bespoke_findings, bespoke_ms = check_doc_headers(root, changed.py + changed.sh)

    blocking = _dedupe(ruff_findings + advisory_blocking + basedpyright_findings + static_findings + bespoke_findings)
    blocking.sort(key=lambda f: (f.file, f.line, f.rule))
    advisory_report.sort(key=lambda f: (f.file, f.line, f.rule))

    total_ms = _elapsed_ms(started)
    clean = not blocking
    report: AgentCheckReport = {
        "schema_version": "1.0",
        "tool": "agent-check",
        "changed": changed.to_dict(),
        "checks": {
            "ruff": _check_entry(ruff_findings, ruff_ms),
            "advisory": {
                "status": "pass" if not advisory_report else "info",
                "verdicts": verdicts,
                "findings": [f.to_dict() for f in advisory_report],
                "duration_ms": advisory_ms,
            },
            "basedpyright": _check_entry(basedpyright_findings, basedpyright_ms),
            "static": _check_entry(static_findings, static_ms),
            "bespoke": _check_entry(bespoke_findings, bespoke_ms),
        },
        "findings": [f.to_dict() for f in blocking] + [f.to_dict() for f in advisory_report],
        "summary": {
            "blocking": len(blocking),
            "advisory": len(advisory_report),
            "total": len(blocking) + len(advisory_report),
            "clean": clean,
            "duration_ms": total_ms,
        },
    }
    logger.info(
        "[IMP:9][run] blocking=%d advisory=%d total_ms=%.0f clean=%s",
        len(blocking),
        len(advisory_report),
        total_ms,
        clean,
    )
    return (0 if clean else 1), report


# endregion FUNC_run


# region FUNC__check_entry
def _check_entry(findings: list[AgentFinding], duration_ms: float) -> CheckEntryDict:
    """Секция шага в отчёте: статус + находки + длительность.

    ## @purpose  Единый формат секций checks.* (ruff/basedpyright/static/bespoke).
    ## @io       ⇥ findings: list[AgentFinding], duration_ms: float → ⎋ dict[str, Any]
    ## @complexity  O(N)
    """
    return {
        "status": "pass" if not findings else "fail",
        "findings": [f.to_dict() for f in findings],
        "duration_ms": duration_ms,
    }


# endregion FUNC__check_entry
