#!/usr/bin/env python3
# GREP_SUMMARY: gate workflow-inputs-interp deploy-project env-indirect raw-interpolation inputs defense-in-depth P1-19 R5-negative DevPlan-16-T2C
# STRUCTURE: ▶ collect workflows run:-блоки → ◇ ${{ inputs.* }} вне env: → ⊕ R5-негатив (raw fixture → RED) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Гейт defense-in-depth интерполяции workflow-inputs (DevPlan 16 T2.C, P1-19):
##           `${{ inputs.* }}` разрешён ТОЛЬКО в env:/with: контекстах шага; внутри `run:`-строк
##           запрещён — GitHub разворачивает интерполяцию ДО bash: вход с $( ) / backticks /
##           `; rm` исполнился бы в раннере (канонический паттерн — env-INDIRECT + quoted "$VAR").
## @scope    .github/workflows/*.yml. Комментарии (#) не сканируются.
## @invariants
##   - `${{ inputs.* }}` в run:-строке → RED
##   - env:-значения вида NAME: ${{ inputs.x }} — канон (не violation)
##   - concurrency/group и прочие не-run контексты — вне скоупа гейта
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_WORKFLOWS_DIR = ROOT / ".github" / "workflows"


def _iter_run_lines(text: str) -> list[tuple[int, str]]:
    """Извлечь (line_no, line) из run:-блоков workflow (без комментариев).

    ## @io — ⇥ text YAML → ⎋ список непустых исполнимых строк run-блоков
    """
    out: list[tuple[int, str]] = []
    in_run = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^(\s*)run:\s*[|>-]?\s*$", line) or re.match(r"^\s*run:\s*\S", line):
            in_run = True
            continue
        if in_run:
            # Новый ключ шага/воркфлоу (не продолжение bash) закрывает run-блок
            if re.match(r"^\s*-?\s*[\w.-]+:(\s|$)", line):
                in_run = False
                continue
            out.append((lineno, line))
    return out


def _raw_inputs_interpolations(text: str) -> list[str]:
    """Строки run-блоков с ${{ inputs.* }} (запрещённая прямая интерполяция)."""
    return [line for _, line in _iter_run_lines(text) if re.search(r"\$\{\{\s*inputs\.", line)]


@pytest.mark.gate
def test_inputs_only_in_env_blocks() -> None:
    """P1-19: ни одного ${{ inputs.* }} внутри run:-блоков платформенных workflows."""
    files = sorted(_WORKFLOWS_DIR.glob("*.yml"))
    assert files, "workflows directory пуста — скан не настроен?"
    violations: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        violations.extend(f"{f.relative_to(ROOT)}: {line.strip()}" for line in _raw_inputs_interpolations(text))
    assert not violations, (
        "inputs.* интерполируются прямо в run:-блоках — инъекция через workflow_call input "
        '(канон: env-INDIRECT + quoted "$VAR", DevPlan 16 T2.C):\n' + "\n".join(violations[:8])
    )
    logger.info("[IMP:9][gate][ok] %d workflow(s): 0 raw inputs-интерполяций в run:", len(files))


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T2.C · исходный дефект детектируется
# · Last fail: аудит 15 P1-19 — EXPECTED_NAME/TARGET_NODE/receive-строки интерполировали
#   inputs сырыми в run-блоки deploy-project.yml
# · Scenario: probe-fixture с прямой интерполяцией в run → детектор ловит
# · Remove if: вместе с test_inputs_only_in_env_blocks
@pytest.mark.gate
def test_negative_raw_interpolation_red(tmp_path: pathlib.Path) -> None:
    probe = (
        "      - name: Validate\n"
        "        run: |\n"
        '          EXPECTED_NAME="${{ inputs.project_name }}"\n'
        '          echo "[IMP:9][x] ok"\n'
    )
    hits = _raw_inputs_interpolations(probe)
    assert any("inputs.project_name" in h for h in hits), "R5 FAIL: детектор пропустил исходный дефект P1-19"
    logger.info("[IMP:9][gate][r5-negative] raw inputs-интерполяция детектируется")
