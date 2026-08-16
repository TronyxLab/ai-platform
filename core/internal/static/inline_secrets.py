"""Inline-secrets detector — secrets.env parsing only in shared module (DevPlan 163 W-C).

# GREP_SUMMARY: static inline-secrets secrets-env-patterns source_secrets_env shared-only DevPlan-086 anti-drift
# STRUCTURE: ▶ scan core/internal/ + core/entrypoints/ (*.py/*.sh, ∖ shared/) → ○ 5 regex-паттернов
#            инлайн-парсинга secrets.env → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор инлайн-парсинга secrets.env (DevPlan 163 W-C C3; порт
##           tests/gates/test_gate_no_inline_secrets_parsing.py, DevPlan 086): НИ ОДИН файл
##           вне core/internal/shared/ не парсит secrets.env старыми inline-паттернами.
##           5 паттернов: (P1) for-line-итерация open(secrets); (P2) source_secrets_env;
##           (P3) shell set -a; source secrets; (P4) dot-sourcing /var/lib/platform/run/secrets;
##           (P5) source $secrets_env. Находки — rule="inline-secrets" (blocking).
## @scope    Line-скан core/internal/ и core/entrypoints/ — Python и shell файлы.
##           core/internal/shared/ — канонический модуль (исключение скана).
## @invariants
##   - core/internal/shared/ — ЕДИНСТВЕННАЯ директория, которой разрешено парсить
##     secrets.env напрямую
##   - Любой match вне shared/ → RED (список файлов + паттерн)
##   - 5 паттернов (id/name/regex/include): P1 *.py, P2 *.{py,sh}, P3-P5 *.sh
##   - P2 (имя source_secrets_env): файлы, ДЕЛЕГИРУЮЩИЕ в shared secrets_env_parser
##     (обёртки — cert_orchestrator._source_secrets_env, secrets_manager.
##     source_secrets_env), исключаются — это канонический канал, не инлайн-парсинг.
##     170 W6-D3: делегат в secrets_env_apply (apply_secrets_env сам читает shared
##     secrets_env_parser) — тот же канонический канал, исключается.
##   - Скан нативно (Python I/O), без subprocess-grep (перенос с гейта)
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale DevPlan 086: 7 inline-парсеров консолидированы в shared/secrets_env_parser.py.
##            Детектор предотвращает регрессию — новый код НЕ должен возвращать
##            inline-парсинг (быстрый слой, без pytest-гейта).
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created (порт 086-гейта)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_SCAN_DIRS: tuple[str, ...] = ("core/internal", "core/entrypoints")
_EXCLUDE_DIRS: tuple[str, ...] = ("core/internal/shared",)

# (id, pattern, включаемые суффиксы)
_PATTERNS: tuple[tuple[str, re.Pattern[str], frozenset[str]], ...] = (
    ("P1", re.compile(r"for\s+\w+\s+in.*open.*secrets"), frozenset((".py",))),
    ("P2", re.compile(r"source_secrets_env"), frozenset((".py", ".sh"))),
    ("P3", re.compile(r"set\s+-a;.*source.*secrets"), frozenset((".sh",))),
    ("P4", re.compile(r"\.\s+/var/lib/platform/run/secrets"), frozenset((".sh",))),
    ("P5", re.compile(r"source\s+\$secrets_env"), frozenset((".sh",))),
)


# region FUNC_scan_path
def _scan_path(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """Line-скан одного файла на 5 inline-secrets паттернов.

    ## @purpose  Для каждого паттерна: применим к суффиксу файла, ищем regex в строках.
    ##           P2 (имя source_secrets_env) имеет точную семантику: RED только для
    ##           НОВОГО инлайн-парсера — файл НЕ делегирует в shared secrets_env_parser,
    ##           НЕ импортирует обёртку source_secrets_env (helpers/secrets.py каноничен)
    ##           и строка не комментарий/docstring. Обёртки (cert_orchestrator.
    ##           _source_secrets_env, secrets_manager.source_secrets_env) делегируют
    ##           в shared — это канонический канал, не нарушение.
    ## @io       ⇥ path: Path, root: Path, changed → ⎋ list[Finding]
    ## @complexity  O(L * P) — строки × паттерны
    """
    rel = path.relative_to(root).as_posix()
    if changed is not None and rel not in changed:
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
    except OSError:
        return []
    p2_delegates_to_shared = "secrets_env_parser" in content
    # Легитимный потребитель: импортирует обёртку source_secrets_env из канонического
    # модуля (helpers/secrets.py pattern — delegating wrapper chain).
    p2_imports_wrapper = "from core.internal.bootstrap.lifecycle.secrets_manager import source_secrets_env" in content
    # 170 W6-D3: обёртка source_secrets_env делегирует в secrets_env_apply.apply_secrets_env
    # (канонический канал 086: apply сам читает shared secrets_env_parser) — легитимный делегат.
    p2_delegates_to_apply = "secrets_env_apply" in content
    findings: list[Finding] = []
    for pattern_id, regex, suffixes in _PATTERNS:
        if path.suffix not in suffixes:
            continue
        for lineno, line in enumerate(lines, 1):
            if not regex.search(line):
                continue
            if pattern_id == "P2":
                if p2_delegates_to_shared or p2_imports_wrapper or p2_delegates_to_apply:
                    logger.info("[IMP:8][inline_secrets][skip] %s:%d P2 — delegated/imported wrapper", rel, lineno)
                    continue
                stripped = line.strip()
                if stripped.startswith(("#", '"""', "'''")):
                    logger.info("[IMP:8][inline_secrets][skip] %s:%d P2 — comment/docstring", rel, lineno)
                    continue
            findings.append(
                Finding(
                    rule="inline-secrets",
                    file=rel,
                    line=lineno,
                    message=f"inline secrets.env parsing pattern [{pattern_id}] — use shared/secrets_env_parser",
                )
            )
            logger.warning("[IMP:9][inline_secrets][RED] %s:%d [%s] %s", rel, lineno, pattern_id, line.strip()[:120])
    return findings


# endregion FUNC_scan_path


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти inline secrets.env parsing вне core/internal/shared/.

    # ▶ ┌core/internal + core/entrypoints┐ → ○ *.py/*.sh ∖ shared/ → ○ 5 паттернов → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — DevPlan 086 инвариант.
    ##           Для probe-деревьев (без core/) — рекурсивный скан всех файлов.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L * P) — файлы × строки × паттерны
    """
    findings: list[Finding] = []
    core_dir = root / "core"
    suffixes = frozenset((".py", ".sh"))
    if core_dir.is_dir():
        for dir_name in _SCAN_DIRS:
            scan_dir = root / dir_name
            if not scan_dir.is_dir():
                continue
            for path in sorted(p for p in scan_dir.rglob("*") if p.is_file()):
                rel = path.relative_to(root).as_posix()
                if any(rel.startswith(ep) for ep in _EXCLUDE_DIRS):
                    continue
                if path.suffix not in suffixes:
                    continue
                findings.extend(_scan_path(path, root, changed))
    else:
        for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes):
            findings.extend(_scan_path(path, root, changed))
    logger.info("[IMP:9][inline_secrets] Findings=%d", len(findings))
    if not findings:
        logger.info("[IMP:9][inline_secrets] PASS: no inline secrets.env parsing outside shared/")
    return findings


# endregion FUNC_detect
