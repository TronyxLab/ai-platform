#!/usr/bin/env python3
# GREP_SUMMARY: gate deploy-honesty silent-success static-detector devplan-029 T8 RC2 bootstrap-deploy converge deny-by-default allowlist-empty
# STRUCTURE: ▶ AST-scan bootstrap/deploy/*.py + converge/*.py → ◇ per-function: qualitative success-лог без evidence → violation → R5 negative (F-01 trigger) + real-tree scan → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 029 T8, DD-3): статический детектор silent-success паттернов в
##           деплой-коде (bootstrap/deploy/ + converge/) — класс RC2 «успех = заявление, а не
##           проверка» закрывается КАК класс (deny-by-default, allowlist пуст), а не инстанс.
## @scope    tests/gates/test_gate_deploy_honesty.py: _scan_tree() → violations.
## @invariants
##   - Сканируются ТОЛЬКО core/internal/bootstrap/deploy/*.py и core/internal/bootstrap/converge/*.py
##   - Success-лог с числом/мерой в сообщении (%d, {count}, len() …) = измеренный успех — OK
##   - Качественный success-лог (converged/no-op/success/done/complete/healthy/rendered/deployed)
##     БЕЗ меры: функция обязана нести evidence в рабочем теле (rc/status/is_file/docker_*/
##     count/missing/healed/== 0/!= 0/report_add/set_exit/… или вызовы render_sudoers_rules/
##     _write_sudoers_file/do_deploy/…); иначе violation
##   - Allowlist ПУСТ (DD-3): любой новый паттерн — RED; расширение только с ревью
##   - R5 negative: исходный F-01 триггер («Rendered … success» без evidence) детектируется
## @rationale DD-3: deny-by-default (REF-0107 honesty_mode) — цена FP ниже цены ложного зелёного
##            (RC2 = 16 фиксов по постмортемам 028/deploy-postmortem).
## @changes 2026-09-02 · DevPlan 029 T8 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()

# Сканируемые директории (деплой-код — скоуп T8/DD-3)
_SCAN_DIRS = ("core/internal/bootstrap/deploy", "core/internal/bootstrap/converge")

# Качественные success-терминалы (заявление об успехе БЕЗ числа/факта)
_SUCCESS_TOKEN_RE = re.compile(
    r"(converged|no-op|success|done|complete|healthy|rendered|deployed|up to date)", re.IGNORECASE
)
# Evidence-маркеры (рабочее тело функции): наличие любого = есть измерение/проверка состояния
_EVIDENCE_RE = re.compile(
    r"(returncode|status\s*[=!]=|is_file\(|\.exists\(|docker_info|docker_ps|docker_exec|"
    r"docker_inspect|docker compose|compose up|compose down|compose_args|len\(|missing|healed|count|"
    r"== 0|!= 0|> 0|%d|report_add|set_exit|verify|check|write_text|write_bytes|atomic_write|"
    r"\.write\(|subprocess|\.run\(|chmod|os\.replace|\.result|stdout|stderr|mkdir|exit_code|"
    r"render_sudoers_rules\(|_write_sudoers_file\(|_render_template\(|visudo\b|do_deploy\(|"
    r"deploy_context_projects|reconcile_orphans|detect_orphan_containers|docker_rm|docker_stop|docker image prune)",
    re.IGNORECASE,
)


def _iter_functions(source: str) -> list[tuple[str, int]]:
    """Yield (function_name, lineno) for every FunctionDef in the module."""
    tree = ast.parse(source)
    return [
        (node.name, node.lineno) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _function_body_lines(source: str, name: str) -> str | None:
    """Return the full text of the named top-level function (line-based slice), or None."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = source.splitlines()
            # lineno/end_lineno — 1-based inclusive line numbers
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def _is_log_line(line: str) -> bool:
    """True if the line is a logging/report statement."""
    stripped = line.strip()
    return any(tok in stripped for tok in ("logger.", "report_add(", "print("))


def _scan_source(path: Path, source: str) -> list[str]:
    """Return silent-success violations in one module source."""
    violations: list[str] = []
    try:
        funcs = _iter_functions(source)
    except SyntaxError as exc:
        return [f"{path.name}: syntax error: {exc}"]
    for fname, lineno in funcs:
        body = _function_body_lines(source, fname)
        if body is None:
            continue
        lines = body.splitlines()
        log_lines = [ln for ln in lines if _is_log_line(ln)]
        has_success_log = any(_SUCCESS_TOKEN_RE.search(ln) for ln in log_lines)
        if not has_success_log:
            continue
        # Число/мера в самом success-логе (N results / N orphan / rc=%d) = измеренный успех
        has_measured_success = any("%" in ln or "len(" in ln or "{" in ln for ln in log_lines)
        if has_measured_success:
            continue
        # Evidence ищется НЕ в success-логах и НЕ в def-строке (само-заявление проверкой не считается)
        evidence_body = "\n".join(ln for idx, ln in enumerate(lines) if idx > 0 and not _is_log_line(ln))
        if not _EVIDENCE_RE.search(evidence_body):
            violations.append(
                f"{path.name}:{lineno}: function {fname}() — success-лог без evidence (silent-success, T8)"
            )
    return violations


def _scan_tree() -> list[str]:
    """Scan the deploy/converge dirs for silent-success patterns."""
    violations: list[str] = []
    for rel in _SCAN_DIRS:
        d = ROOT / rel
        if not d.is_dir():
            continue
        for py in sorted(d.glob("*.py")):
            if py.name.startswith("_"):
                continue
            try:
                source = py.read_text(encoding="utf-8")
            except OSError as exc:
                violations.append(f"{py.name}: unreadable: {exc}")
                continue
            violations.extend(_scan_source(py, source))
    return violations


# region TEST_deploy_honesty_tree_clean
## @purpose  Реальное дерево deploy/converge: НОЛЬ silent-success нарушений (текущий код честен —
##           успех везде подкреплён evidence: rc/count/is_file/status/мера в логе).
@pytest.mark.gate
@ldd_trajectory
def test_deploy_honesty_tree_clean(caplog) -> None:
    """Real deploy/converge code must not contain silent-success patterns (T8)."""
    caplog.set_level(logging.INFO)
    violations = _scan_tree()
    assert not violations, "Deploy/converge содержит silent-success паттерны:\n" + "\n".join(violations)
    logger.info("[IMP:9][gate][deploy-honesty] tree clean — %d silent-success violations", len(violations))


# endregion TEST_deploy_honesty_tree_clean


# region TEST_deploy_honesty_negative_f01
## 🧪 TRAP[TEST] · NEGATIVE (R5) · T8 — исходный F-01 триггер (silent success: rendered без проверки)
## · Scenario: функция только логгирует «Rendered … success» без какого-либо evidence/проверки
## · Last fail: F-01 — «success-marker до доказательства» (RC2 класс)
## · Remove if: статический детектор отменён
@pytest.mark.gate
def test_deploy_honesty_negative_f01(tmp_path) -> None:
    """R5 negative: synthetic silent-success function is detected (F-01 trigger)."""
    synthetic = tmp_path / "module.py"
    synthetic.write_text(
        'def render_vhosts():\n    logger.info("Rendered vhosts — success")\n    return True\n',
        encoding="utf-8",
    )
    violations = _scan_source(synthetic, synthetic.read_text(encoding="utf-8"))
    assert len(violations) >= 1, "R5 FAIL: детектор не поймал исходный F-01 silent-success триггер"
    assert "render_vhosts" in violations[0]
    logger.info("[IMP:9][gate][deploy-honesty] R5 negative F-01 detected — OK")


# endregion TEST_deploy_honesty_negative_f01


# region TEST_deploy_honesty_negative_count_zero
## 🧪 TRAP[TEST] · NEGATIVE (R5) · T8 — «rendered при 0» класс (F-01b): count-claim без evidence
## · Scenario: «All 0 … converged» как строка-заявление (число-литерал ≠ evidence)
## · Remove if: статический детектор отменён
@pytest.mark.gate
def test_deploy_honesty_negative_count_zero(tmp_path) -> None:
    """R5 negative: count-claim success without evidence is detected."""
    synthetic = tmp_path / "module.py"
    synthetic.write_text(
        'def reconcile():\n    logger.info("All 0 vhosts rendered — converged")\n    return 0\n',
        encoding="utf-8",
    )
    violations = _scan_source(synthetic, synthetic.read_text(encoding="utf-8"))
    assert len(violations) >= 1, "R5 FAIL: детектор не поймал count-claim без evidence"
    logger.info("[IMP:9][gate][deploy-honesty] R5 negative count-zero detected — OK")


# endregion TEST_deploy_honesty_negative_count_zero
