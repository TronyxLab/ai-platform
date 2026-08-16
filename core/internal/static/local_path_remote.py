"""Local-path-in-remote detector — FL6 passthrough guard (DevPlan 163 W-C).

# GREP_SUMMARY: static local-path-remote passthrough build-ssh-cmd execute-remote forbidden-path-vars age-secret-key-file FL6
# STRUCTURE: ▶ scan makefiles/*.mk + core/entrypoints/*.sh + core/internal/bootstrap/*.sh
#            → ○ line scan → ◇ passthrough-конструкция ∧ запрещённая переменная-путь/флаг
#            (одна строка) → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор «локальные пути НЕ уходят в remote-аргументы» (DevPlan 163 W-C C1;
##           порт FL6 из tests/gates/test_gate_structural_consistency.py, DevPlan 123 T9):
##           строка passthrough/build_*_ssh_cmd/execute_remote_* + $AGE_SECRET_KEY_FILE /
##           $PLATFORM_ROOT / $NODE_CONFIGS_DIR / $PROJECTS_BASE / $NODE_YAML / флаг
##           --age-secret-key-file → Finding rule="local-path-remote" (blocking).
## @scope    Line-scan makefiles/*.mk, core/entrypoints/*.sh, core/internal/bootstrap/*.sh.
##           Allowlist ПУСТ (легитимных кейсов форварда локальных путей в remote НЕТ).
## @invariants
##   - RED: одна строка содержит passthrough-конструкцию И запрещённую переменную
##     пути ($VAR/${VAR}) или флаг --age-secret-key-file
##   - Комментарии (строки с #) исключаются
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale RC 121 прод: --age-secret-key-file уходил в remote passthrough
##            (bootstrap.sh:48-50) — локальный ключ читался на удалённой ноде.
##            Ключ/секрет читаются ЛОКАЛЬНО, в remote передаётся КОНТЕНТ, не путь.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт FL6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Passthrough-конструкции (DevPlan 123 T9)
_PASSTHROUGH_CONSTRUCT: re.Pattern[str] = re.compile(
    r"PASSTHROUGH_ARGS\+=|passthrough_args|execute_remote_|"
    r"build_update_ssh_cmd|build_converge_ssh_cmd|build_ssh_cmd"
)

# Запрещённые переменные-пути (локальные значения) — $VAR / ${VAR}
_FORBIDDEN_VAR_REF: re.Pattern[str] = re.compile(
    r"\$(?:\{)?(AGE_SECRET_KEY_FILE|PLATFORM_ROOT|NODE_CONFIGS_DIR|PROJECTS_BASE|NODE_YAML)"
    r"(?:\}|[^A-Za-z0-9_]|$)"
)

# Флаг-ловушка (локальный путь AGE-ключа как remote-флаг)
_FORBIDDEN_FLAG = "--age-secret-key-file"

# Скоуп скана (как в гейте FL6)
_SCAN_GLOBS: tuple[str, ...] = (
    "makefiles/*.mk",
    "core/entrypoints/*.sh",
    "core/internal/bootstrap/*.sh",
)


# region FUNC_line_has_forbidden_path
def _line_has_forbidden_path(line: str) -> bool:
    """Проверить, что строка содержит запрещённую переменную-путь или флаг.

    ## @purpose  $AGE_SECRET_KEY_FILE / $PLATFORM_ROOT / $NODE_CONFIGS_DIR /
    ##           $PROJECTS_BASE / $NODE_YAML (в любой $VAR/${VAR} форме) или флаг.
    ## @io       ⇥ line: str → ⎋ bool
    ## @complexity  O(L) — длина строки
    """
    if _FORBIDDEN_FLAG in line:
        return True
    return _FORBIDDEN_VAR_REF.search(line) is not None


# endregion FUNC_line_has_forbidden_path


# region FUNC_scan_paths
def _scan_paths(paths: list[Path], root: Path, changed: set[str] | None) -> list[Finding]:
    """Сканировать список файлов на строки passthrough + запрещённый путь.

    ## @purpose  Линейный скан каждого файла; RED только при одновременном наличии
    ##           passthrough-конструкции и запрещённой переменной/флага на строке.
    ## @io       ⇥ paths: list[Path], root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    """
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if changed is not None and rel not in changed:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not _PASSTHROUGH_CONSTRUCT.search(stripped):
                continue
            if not _line_has_forbidden_path(stripped):
                continue
            findings.append(
                Finding(
                    rule="local-path-remote",
                    file=rel,
                    line=lineno,
                    message="local path forwarded to remote argument (passthrough/build_ssh_cmd): " + stripped[:120],
                )
            )
            logger.warning("[IMP:9][local_path_remote][RED] %s:%d %s", rel, lineno, stripped[:120])
    return findings


# endregion FUNC_scan_paths


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти строки, форвардящие локальные пути в remote-аргументы (FL6).

    # ▶ ┌_SCAN_GLOBS┐ → ○ line scan → ◇ passthrough ∧ forbidden-var? → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило FL6 (DevPlan 123 T9).
    ##           Для probe-деревьев (root без core/) — рекурсивный скан всех файлов.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    """
    core_dir = root / "core"
    if core_dir.is_dir():
        paths: list[Path] = []
        for glob_pattern in _SCAN_GLOBS:
            paths.extend(sorted(root.glob(glob_pattern)))
    else:
        paths = sorted(p for p in root.rglob("*") if p.is_file())
    findings = _scan_paths(paths, root, changed)
    logger.info("[IMP:9][local_path_remote] Scanned %d file(s), findings=%d", len(paths), len(findings))
    if not findings:
        logger.info("[IMP:9][local_path_remote] PASS: 0 local paths in remote arguments")
    return findings


# endregion FUNC_detect
