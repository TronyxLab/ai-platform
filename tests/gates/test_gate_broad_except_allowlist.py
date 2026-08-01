#!/usr/bin/env python3
# GREP_SUMMARY: gate broad-except allowlist noqa-EXC DEPLOY_BEST_EFFORT legacy-parity best-effort top-level-CLI-handler policy-marker U-39 anti-drift
# STRUCTURE: ▶ AST-обход core/internal/*.py → ◇ except Exception? → ◇ строка содержит "# noqa: EXC"? → ◇ маркер политики (DEPLOY_BEST_EFFORT|legacy parity|best-effort|top-level CLI handler)? → ⊕ violations → ⎋ PASS/RED
# region MODULE_CONTRACT
## @purpose  Allowlist-гейт на широкие except (DevPlan 116 B4 T8, U-39): `except Exception`
##           в core/internal ДОПУСТИМ только если строка содержит `# noqa: EXC` И маркер
##           политики (DEPLOY_BEST_EFFORT | legacy parity | best-effort | top-level CLI handler).
##           Legacy parity формализована контрактом shared/contracts.py (D5) — существующие
##           14 мест deploy_orchestrator проходят по маркерам; НОВЫЕ широкие except без
##           маркера → RED. Сжатие числа широких except — волны B1/B8 (D6), не B4.
## @scope    Все core/internal/**/*.py. Комментарии и docstrings не триггерят (AST).
## @invariants
##   - Каждый `except Exception` в core/internal обязан иметь inline-комментарий
##     `# noqa: EXC` на той же строке + маркер политики из _POLICY_MARKERS.
##   - Маркеры: "DEPLOY_BEST_EFFORT", "legacy parity", "best-effort", "top-level CLI handler".
##   - Гейт не сокращает число широких except (D6) — только запрещает НОВЫЕ неразмеченные.
## @rationale U-39: «legacy parity» декларировался комментариями ×12 вместо контракта.
##            Гейт делает отсутствие маркера структурно невозможным для нового кода.
## @changes 2026-08-01 | DevPlan 116 B4 T8 — Created
# endregion MODULE_CONTRACT

import ast
import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"

# Допустимые маркеры политики в inline-комментарии except (константа гейта, DevPlan T8.1)
_POLICY_MARKERS: tuple[str, ...] = (
    "DEPLOY_BEST_EFFORT",
    "legacy parity",
    "best-effort",
    "top-level CLI handler",
)


def _scan_broad_excepts() -> list[tuple[str, int, str]]:
    """Find `except Exception` lines in core/internal lacking noqa:EXC or policy marker.

    ▶ ┌_CORE_INTERNAL┐ → ○ AST walk (Try/ExceptHandler) → ◇ name=="Exception" → ◇ inline-комментарий
    │    строки содержит "# noqa: EXC"? → ◇ маркер политики? → ⊕ violations → ⎋ list
    """
    violations: list[tuple[str, int, str]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None or not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
                continue
            lineno = node.lineno
            line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            if "# noqa: EXC" not in line:
                violations.append((rel, lineno, "missing # noqa: EXC"))
                continue
            if not any(marker in line for marker in _POLICY_MARKERS):
                violations.append((rel, lineno, f"missing policy marker ({' | '.join(_POLICY_MARKERS)})"))
    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · broad except without noqa:EXC+policy marker → RED (DevPlan 116 B4 T8)
def test_broad_except_requires_noqa_and_policy_marker(caplog) -> None:
    """Every `except Exception` in core/internal must carry # noqa: EXC + policy marker."""
    violations = _scan_broad_excepts()
    if violations:
        for rel, lineno, reason in violations:
            logger.error("[IMP:10][broad-except][RED] %s:%d — %s", rel, lineno, reason)
        pytest.fail(
            f"Широкие except без маркера политики ({len(violations)}):\n"
            + "\n".join(f"  - {rel}:{lineno} — {reason}" for rel, lineno, reason in violations)
            + "\n\nФормат: `except Exception as e:  # noqa: EXC — <reason> (best-effort: DEPLOY_BEST_EFFORT policy)`."
        )

    logger.info("[IMP:9][broad-except][done] PASS: все except Exception размечены (noqa: EXC + policy marker)")
