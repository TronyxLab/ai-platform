"""Exception-patterns detector — bare except + broad-except allowlist (DevPlan 163 W-C, 170 W2-A2).

# GREP_SUMMARY: static exception-patterns bare-except broad-except allowlist noqa-EXC policy-marker U-39 AST-scan
# STRUCTURE: ▶ AST-обход core/**/*.py → ◇ ExceptHandler.type is None? → ⊕ bare-except Finding
#            → ◇ type == Exception в (core/internal|core/modules|core/loadtest) →
#            ◇ "noqa: EXC"? → ◇ policy marker (только core/internal)? → ⊕ Finding → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор паттернов исключений (DevPlan 163 W-C C1, расширен 170 W2-A2 B3):
##           (1) bare except (`except:` без типа) в core/**/*.py → RED; (2) широкий
##           `except Exception` в core/internal, core/modules и core/loadtest ДОПУСТИМ
##           только с inline `noqa: EXC` на той же строке; для core/internal ДОПОЛНИТЕЛЬНО
##           требуется маркер политики (DEPLOY_BEST_EFFORT | best-effort | top-level CLI handler)
##           — порт tests/gates/test_gate_broad_except_allowlist.py (DevPlan 116 B4 T8,
##           U-39). Находки — rule="exception-patterns".
## @scope    AST-скан всех core/**/*.py (правило 1) и core/{internal,modules,loadtest}/**/*.py
##           (правило 2). Комментарии/docstring'и не триггерят (AST-узлы ExceptHandler).
## @invariants
##   - Каждый `except Exception` в core/internal обязан иметь `noqa: EXC` на той же
##     строке + маркер политики из _POLICY_MARKERS
##   - Каждый `except Exception` в core/modules и core/loadtest обязан иметь `noqa: EXC`
##     на той же строке (policy-marker — только core/internal: маркеры политики
##     сформулированы под internal-контракты; modules используют свои контекстные
##     формулировки, напр. "HTTP handler boundary", "retention non-fatal по контракту D3/D4")
##   - Bare except (`except:`) запрещён всюду в core/ (маскирует любую ошибку)
##   - `except (A, B)` с кортежем и `except ValueError as e` — не триггерят
##   - Гейт не сокращает число широких except (D6) — только запрещает НОВЫЕ неразмеченные
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale bare except и широкий except без маркера — классы дефекта «тихая ошибка»
##            (конституция §4: все ошибки видимы). Быстрый слой детектирует оба класса
##            без pytest-гейта. Расширение на modules/loadtest (170 W2-A2 B3): 7 неразмеченных
##            широких except вне internal-скоупа (backup-cron retention/upload/s3_client,
##            loadtest db.py) — единый канон маркировки для всего core/.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт B4 T8 + bare-except)
##           2026-08-14 | DevPlan 170 W2-A2 — scope-расширение на core/modules + core/loadtest
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Допустимые маркеры политики в inline-комментарии except (DevPlan T8.1).
# +"retry policy" — 177 W3.1: catch-all retry-абстракции (shared/retry.py) — любое
# Exception передаётся retryable-предикату, не-retryable/исчерпание → re-raise.
_POLICY_MARKERS: tuple[str, ...] = (
    "DEPLOY_BEST_EFFORT",
    "best-effort",
    "top-level CLI handler",
    "retry policy",
)

# Скоупы правила 2 (широкий `except Exception` требует noqa: EXC). core/modules и
# core/loadtest добавлены DevPlan 170 W2-A2 (B3) — 7 неразмеченных мест вне internal.
_WIDE_EXCEPT_SCOPES: tuple[str, ...] = (
    "core/internal/",
    "core/modules/",
    "core/loadtest/",
)

# Скоупы, где дополнительно к noqa: EXC требуется policy-marker (_POLICY_MARKERS).
# Только core/internal: маркеры политики сформулированы под internal-контракты
# (DEPLOY_BEST_EFFORT/top-level CLI handler); modules/loadtest маркируют контекстно.
_POLICY_MARKER_SCOPES: tuple[str, ...] = ("core/internal/",)


# region FUNC_scan_py_file
def _scan_py_file(path: Path, root: Path) -> list[Finding]:
    """AST-скан одного .py файла на bare except и неразмеченный broad except.

    ## @purpose  Обход ExceptHandler: (1) type is None → bare-except RED;
    ##           (2) type Name "Exception" в скоупе _WIDE_EXCEPT_SCOPES + отсутствие
    ##           noqa:EXC → RED; (3) в _POLICY_MARKER_SCOPES дополнительно требуется
    ##           policy-marker из _POLICY_MARKERS → RED.
    ## @io       ⇥ path: Path, root: Path → ⎋ list[Finding]
    ## @complexity  O(N) — AST-узлы
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    lines = text.splitlines()
    rel = path.relative_to(root).as_posix()
    in_wide_scope = any(rel.startswith(scope) for scope in _WIDE_EXCEPT_SCOPES)
    in_policy_scope = any(rel.startswith(scope) for scope in _POLICY_MARKER_SCOPES)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            findings.append(
                Finding(
                    rule="exception-patterns",
                    file=rel,
                    line=node.lineno,
                    message="bare except without exception type — masks errors",
                )
            )
            logger.warning("[IMP:9][exception_patterns][RED] %s:%d bare except", rel, node.lineno)
            continue
        if not (in_wide_scope and isinstance(node.type, ast.Name) and node.type.id == "Exception"):
            continue
        lineno = node.lineno
        line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
        if "# noqa: EXC" not in line:
            findings.append(
                Finding(
                    rule="exception-patterns",
                    file=rel,
                    line=lineno,
                    message="broad `except Exception` missing '# noqa: EXC' + policy marker",
                )
            )
            logger.warning("[IMP:9][exception_patterns][RED] %s:%d missing # noqa: EXC", rel, lineno)
            continue
        if in_policy_scope and not any(marker in line for marker in _POLICY_MARKERS):
            findings.append(
                Finding(
                    rule="exception-patterns",
                    file=rel,
                    line=lineno,
                    message=f"broad `except Exception` missing policy marker ({' | '.join(_POLICY_MARKERS)})",
                )
            )
            logger.warning("[IMP:9][exception_patterns][RED] %s:%d missing policy marker", rel, lineno)
    return findings


# endregion FUNC_scan_py_file


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти паттерны исключений: bare except и неразмеченные broad except.

    # ▶ ┌core/**/*.py┐ → ○ walk ExceptHandler → ◇ type None / Exception+marker → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry): правила bare-except + B4 T8.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    ## @invariants  Сканирует root/core/**/*.py (либо root/**/*.py для probe-деревьев)
    """
    core_dir = root / "core"
    scan_root = core_dir if core_dir.is_dir() else root
    files = sorted(p for p in scan_root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
    findings: list[Finding] = []
    for path in files:
        rel_to_root = path.relative_to(root).as_posix()
        if changed is not None and rel_to_root not in changed:
            continue
        findings.extend(_scan_py_file(path, root))
    logger.info("[IMP:9][exception_patterns] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][exception_patterns] PASS: 0 bare/broad-except violations")
    return findings


# endregion FUNC_detect
