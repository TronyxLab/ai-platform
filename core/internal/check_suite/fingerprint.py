"""
# GREP_SUMMARY: check-suite, fingerprint, cache, tree-files, sha256, git-ls-files, check-cache.json, atomic-write
# STRUCTURE: ▶ tree_files ┌git ls-files -c -o --exclude-standard┐ → ○ compute_fingerprint ┌sha256(extra + files)┐ → ⎋ fp → ◇ cache_path ┌git rev-parse --git-dir┐ → ○ load_cache / save_cache ┌tmp + os.replace┐ → ⎋ dict|None
# region MODULE_CONTRACT
## @purpose  Fingerprint-кэш пакета check_suite (DevPlan 170 W3 — извлечено из монолита
##           core/internal/check_suite.py): fingerprint всего дерева (git ls-files +
##           hashlib.sha256), путь кэша внутри git-dir, атомарное чтение/запись кэш-JSON.
## @scope    core/internal/check_suite/fingerprint.py — stdlib-only. Потребители: diagnostic.py
##           (run_diagnostic: compute_fingerprint/cache_path/load_cache/save_cache),
##           __init__.py (re-export FINGERPRINT_EXCLUDE_PARTS/RE, compute_fingerprint).
## @invariants
##   - Байт-идентичное дерево → тот же fingerprint; любая правка/untracked-файл → miss
##   - Excludes: FINGERPRINT_EXCLUDE_PARTS (.venv/__pycache__/.pytest_cache/node_modules/.git)
##     + FINGERPRINT_EXCLUDE_RE (tests/report*.xml, .test_counter.json[.lock])
##   - None = git недоступен (кэш off); cache_path: $(git rev-parse --git-dir)/check-cache.json
##   - save_cache атомарно: tmp + os.replace (конкурентные executor'ы не портят файл)
##   - monkeypatch-контракт: tree_files резолвится через пакетную атрибуцию (check_suite.tree_files)
## @rationale Чистый Python вместо xargs sha256sum (TRAP[DECISION] ниже): один git subprocess
##            + hashlib; формат-контракт DevPlan §3.4 сохранён.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TypedDict, cast

from core.internal import check_suite as cs
from core.internal.check_suite.report import CheckPayload

logger = logging.getLogger(__name__)

# region FINGERPRINT_CACHE


# TypedDict-граница кэш-JSON (W11-G4): подмножество CheckReportDict (fingerprint/status/report)
class CheckCacheDict(TypedDict, total=False):
    """Содержимое check-cache.json (fingerprint + статус + report + checks)."""

    fingerprint: str
    status: str
    duration_ms: float
    report: str
    checks: list[CheckPayload]


# Пути/базлайны, исключаемые из fingerprint-дерева (вдобавок к gitignore)
FINGERPRINT_EXCLUDE_PARTS = (".venv", "__pycache__", ".pytest_cache", "node_modules", ".git")
# report*.xml + .test_counter.json + flock-локи (.test_counter.json.lock — артефакты тест-прогонов)
FINGERPRINT_EXCLUDE_RE = re.compile(r"(^|/)(tests/report[^/]*\.xml|\.test_counter\.json(\.lock)?)$")

_CACHE_FILENAME = "check-cache.json"

# ⚠️ TRAP[DECISION] · 2026-08-02 · — · fingerprint — чистый Python вместо `xargs -0 sha256sum`
# · Rejected: git ls-files -c -o --exclude-standard -z | xargs -0 sha256sum (DevPlan §3.4)
# · Reason: sha256sum отсутствует на macOS по умолчанию (только shasum -a 256); xargs-пайплайн
# ·   нестабилен между GNU/BSD coreutils. Эквивалент: один subprocess git ls-files + hashlib в Python
# ·   (тот же байт-набор дерева, тот же fingerprint-контракт).
# · Rev: если дерево вырастет >100k файлов и хеширование станет бутылочным горлышком → xargs -P.
_FINGERPRINT_EXTRA_FILES = ("core/check-suite.yaml", ".pre-commit-config.yaml", "pyproject.toml")


# region FUNC_tree_files
## @purpose  Список файлов дерева: git ls-files -c -o --exclude-standard (tracked + untracked,
##           gitignore уважается) + явные исключения (report*.xml, .test_counter.json, venv-пути).
## @io       ⇥ root: Path → ⎋ list[str] | None (None = git недоступен)
## @complexity O(N) где N = файлы
def tree_files(root: Path) -> list[str] | None:
    """List tree files (tracked + untracked non-ignored) via one git subprocess."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
            capture_output=True,
            cwd=str(root),
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    files: list[str] = []
    for raw in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not raw:
            continue
        if any(part in FINGERPRINT_EXCLUDE_PARTS for part in raw.split("/")):
            continue
        if FINGERPRINT_EXCLUDE_RE.search(raw):
            continue
        files.append(raw)
    return files


# endregion FUNC_tree_files


# region FUNC_compute_fingerprint
## @purpose  Fingerprint целого дерева (DevPlan §3.4): sha256(манифест + .pre-commit-config.yaml +
##           pyproject.toml + содержимое ВСЕХ файлов дерева). Байт-идентичное дерево → тот же
##           fingerprint; любая правка/untracked-файл → miss. None если git недоступен (кэш off).
## @io       ⇥ root: Path → ⎋ str | None
## @complexity O(N * S) где N = файлы, S = средний размер
## @rationale Чистый Python вместо xargs sha256sum (TRAP[DECISION] выше): один git subprocess
##            + hashlib; формат-контракт DevPlan сохранён.
def compute_fingerprint(root: Path) -> str | None:
    """Compute the whole-tree fingerprint; None when git is unavailable."""
    files = cs.tree_files(root)  # late-binding: monkeypatch-контракт (DI-HYG)
    if files is None:
        logger.warning("[IMP:7][fingerprint][skip] git недоступен — fingerprint-кэш отключён")
        return None

    hasher = hashlib.sha256()
    for rel in _FINGERPRINT_EXTRA_FILES:
        p = root / rel
        if p.is_file():
            hasher.update(rel.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(p.read_bytes())
    for rel in sorted(files):
        p = root / rel
        try:
            data = p.read_bytes()
        except OSError:
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(data)
    fp = hasher.hexdigest()
    logger.info("[IMP:8][fingerprint][compute] %d файлов → %s", len(files), fp[:16])
    return fp


# endregion FUNC_compute_fingerprint


# region FUNC_cache_path
## @purpose  Путь кэша: $(git rev-parse --git-dir)/check-cache.json (не коммитится).
## @io       ⇥ root: Path → ⎋ Path | None (None = git недоступен)
## @complexity O(1) — один git subprocess
# region FUNC__plw_body__cache_path
## @purpose  Тело try-блока (PLW0717 extraction из cache_path) — семантика except не меняется.
## @io       ⇥ root → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__cache_path(root):
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"], capture_output=True, text=True, cwd=str(root), timeout=15, check=False
    )
    if result.returncode != 0:
        return None
    gitdir = result.stdout.strip()
    gitdir_path = Path(gitdir)
    if not gitdir_path.is_absolute():
        gitdir_path = root / gitdir_path
    return gitdir_path / _CACHE_FILENAME


# endregion FUNC__plw_body__cache_path


def cache_path(root: Path) -> Path | None:
    """Resolve cache file inside the git dir (not committed)."""
    try:
        return _plw_body__cache_path(root)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# endregion FUNC_cache_path


# region FUNC_load_cache
## @purpose  Чтение кэш-JSON (fingerprint/status/report); битый/отсутствующий → None.
## @io       ⇥ path: Path → ⎋ dict | None
## @complexity O(1)
def load_cache(path: Path | None) -> CheckCacheDict | None:
    """Read cache JSON; malformed/missing → None."""
    if path is None or not path.is_file():
        return None
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)  # pyright: ignore[reportAny] W11-G4: stdlib json → Any (граница сериализации)
        if not isinstance(data, dict):
            return None
        return cast(CheckCacheDict, cast(object, data))
    except (json.JSONDecodeError, OSError):
        return None


# endregion FUNC_load_cache


# region FUNC_save_cache
## @purpose  Запись кэш-JSON (атомарно: tmp + os.replace — конкурентные executor'ы не портят файл).
## @io       ⇥ path: Path | None, data: dict → None
## @complexity O(1)
def save_cache(path: Path | None, data: CheckCacheDict) -> None:
    """Write cache JSON atomically (tmp + os.replace)."""
    if path is None:
        return
    try:
        tmp = path.with_suffix(".json.tmp")
        with Path(tmp).open("w", encoding="utf-8") as f:
            json.dump(data, f)
            f.write("\n")
        Path(tmp).replace(path)
    except OSError as exc:
        logger.warning("[IMP:7][cache][write] cache write failed: %s", exc)


# endregion FUNC_save_cache

# endregion FINGERPRINT_CACHE
