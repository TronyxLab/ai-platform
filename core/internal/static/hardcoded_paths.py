"""Hardcoded-paths detector — cross-platform local/server path ban (DevPlan 163 W-C).

# GREP_SUMMARY: static hardcoded-paths user-homepath platform-root cross-platform CI-drift server-path /Users/ /home/ /opt/platform/
# STRUCTURE: ▶ scan tests/ (home) + core/ (home+server) *.py → ◇ autodetect/env-fallback file? → skip
#            → ◇ line comment/docstring? → skip → ⊕ line match home|server path → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор хардкоженных локальных/серверных путей (DevPlan 163 W-C C3; порт
##           tests/gates/test_gate_no_hardcoded_local_paths.py, P0 2026-07-23): хардкод
##           /Users/<user>/ и /home/<user>/ в tests/ + core/ и server-пути (PLATFORM_ROOT)
##           в core/ ломает кросс-платформенный CI или хардкодит деплой-предположения.
##           Легитимная альтернатива: os.path.dirname(__file__)-автодетект (home) или
##           os.environ.get("PLATFORM_ROOT", DEFAULT_PLATFORM_ROOT)-fallback (server; литерал —
##           shared/deploy_paths).
## @scope    Line-скан всех *.py под tests/ (home-паттерны) и core/ (home+server-паттерны).
##           Тесты легитимно ссылаются на /opt/platform/ как данные — server-паттерн
##           применяется ТОЛЬКО к core/. Allowlist файлов пуст; контент-allowlist
##           (автодетект/fallback-паттерн) пропускает файл целиком.
## @invariants
##   - RED: "/Users/<user>/..." или "/home/<user>/..." (кроме /home/runner/work/) в tests/ и core/
##   - RED: "/opt/platform/..." в core/ (только; в tests/ — легитимные test-data)
##   - PASS: файл содержит os.path.dirname(__file__)-автодетект или
##     os.environ.get(..., "/opt/...")-fallback (пропускается целиком)
##   - Комментарии (#, """, ''') и пустые строки не триггерят
##   - NOT flagged: /tmp/, /var/lib/platform, /etc/, /usr/ (generic system paths)
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale P0 2026-07-23: test_component_hermes.py:66 хардкод "/Users/tronyx/projects/
##            ai-platform" сломал hermes-тесты на CI; UF9: compose_preflight.py:45 хардкод
##            /opt/platform не покрывался сканом tests/ (coverage gap). Быстрый слой ловит
##            оба класса без pytest-гейта.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created (порт no_hardcoded_local_paths)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Хардкод-пути home-директорий (macOS/Linux): "/Users/tronyx/...", "/home/runner/..."
# /home/runner/work/ — легитимный CI-путь (не хардкод девелоперской машины).
_HARDCODED_HOME_PATH: re.Pattern[str] = re.compile(
    r'["\'](/Users/[\w.-]+/|/home/(?!runner/work/)[\w.-]+/[\w.-]+/)',
)

# Хардкод-серверные пути (без env-fallback): "/opt/platform/core/..." и т.п.
_HARDCODED_SERVER_PATH: re.Pattern[str] = re.compile(r'["\'](/opt/platform/)')

# Контент-allowlist: файл с автодетектом __file__ или env-fallback — легитимен целиком.
_ALLOWLISTED_CONTENT: re.Pattern[str] = re.compile(
    r"os\.(?:path\.abspath\(os\.path\.join\(os\.path\.dirname\(__file__\)"
    r"|environ\.get\(['\"]\w+['\"],\s*['\"]/opt/)",
)

# Директории скана: (имя, server-паттерн применять?) — home-паттерн применяется всегда.
_SCAN_CONFIGS: tuple[tuple[str, bool], ...] = (
    ("tests", False),
    ("core", True),
)


# region FUNC_scan_dir
def _scan_dir(root: Path, dir_name: str, *, apply_server: bool, changed: set[str] | None) -> list[Finding]:
    """Сканировать один каталог (*.py) на хардкод-пути.

    ## @purpose  rglob *.py под dir_name; файл с автодетект-паттерном пропускается;
    ##           построчно: home-паттерн всегда, server-паттерн при apply_server.
    ## @io       ⇥ root: Path, dir_name: str, apply_server: bool (keyword-only, FBT),
    ##              changed → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    """
    scan_root = root / dir_name
    if not scan_root.is_dir():
        return []
    findings: list[Finding] = []
    patterns = [_HARDCODED_HOME_PATH]
    if apply_server:
        patterns.append(_HARDCODED_SERVER_PATH)
    for py_file in sorted(scan_root.rglob("*.py")):
        if "__pycache__" in py_file.parts or py_file.name.startswith("."):
            continue
        rel = py_file.relative_to(root).as_posix()
        if changed is not None and rel not in changed:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("[IMP:7][hardcoded_paths] Cannot read %s", rel)
            continue
        if _ALLOWLISTED_CONTENT.search(content):
            logger.info("[IMP:8][hardcoded_paths][auto-detect] %s uses autodetect/env-fallback", rel)
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", '"""', "'''")):
                continue
            for pattern in patterns:
                for match in pattern.finditer(line):
                    findings.append(
                        Finding(
                            rule="hardcoded-paths",
                            file=rel,
                            line=lineno,
                            message="hardcoded local/server path (use os.path.dirname(__file__) or "
                            f"os.environ.get('PLATFORM_ROOT', ...)): {match.group()}",
                        )
                    )
                    logger.warning("[IMP:9][hardcoded_paths][RED] %s:%d — %s", rel, lineno, match.group())
    return findings


# endregion FUNC_scan_dir


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти хардкоженные локальные/серверные пути в tests/ и core/.

    # ▶ ┌tests/ + core/ *.py┐ → ○ autodetect-file skip → ○ line scan (home|server)
    #   → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry). Для probe-деревьев (без tests/ и
    ##           core/) — рекурсивный скан всех *.py с home-паттерном.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    ## @invariants  server-паттерн только для core/ (тесты легитимно ссылаются на /opt/platform/)
    """
    findings: list[Finding] = []
    for dir_name, apply_server in _SCAN_CONFIGS:
        findings.extend(_scan_dir(root, dir_name, apply_server=apply_server, changed=changed))

    # Probe-дерево тестов: нет tests/ и core/ → рекурсивный скан с home-паттерном
    if not (root / "tests").is_dir() and not (root / "core").is_dir():
        for py_file in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file()):
            rel = py_file.relative_to(root).as_posix()
            if changed is not None and rel not in changed:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _ALLOWLISTED_CONTENT.search(content):
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", '"""', "'''")):
                    continue
                for match in _HARDCODED_HOME_PATH.finditer(line):
                    findings.append(
                        Finding(
                            rule="hardcoded-paths",
                            file=rel,
                            line=lineno,
                            message="hardcoded local/server path: " + match.group(),
                        )
                    )
                    logger.warning("[IMP:9][hardcoded_paths][RED] %s:%d — %s", rel, lineno, match.group())

    logger.info("[IMP:9][hardcoded_paths] Findings=%d", len(findings))
    if not findings:
        logger.info("[IMP:9][hardcoded_paths] PASS: 0 hardcoded local/server paths")
    return findings


# endregion FUNC_detect
