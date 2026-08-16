#!/usr/bin/env python3
# GREP_SUMMARY: gate gitleaks security-scan workflow-step secret-scan R5 commented-step detection
# STRUCTURE: ▶ parse .github/workflows/security-scan.yml → ◇ find steps with gitleaks in uses/run/name → ◇ if: false отключение? → ◇ commented-out detection (сырые строки) → ⎋ verdict (+R5 negative: отсутствующий/закомментированный шаг)
# region MODULE_CONTRACT
## @purpose  Gate: шаг gitleaks ОБЯЗАН присутствовать в .github/workflows/security-scan.yml
##           и быть активным (НЕ закомментирован, НЕ отключён if: false). DevPlan 160 W3 T3.4.
## @scope    Статический скан единственного workflow security-scan.yml (YAML-парс + сырые строки
##           для детекции комментариев). Без Docker, без subprocess.
## @invariants
##   - Активный шаг = в YAML-структуре есть step, где uses/run/name содержит «gitleaks»
##   - Строки-комментарии («# - name: Gitleaks ...») НЕ считаются шагом (YAML-парс их не видит)
##   - Все gitleaks-строки в сыром тексте закомментированы + YAML-парс не нашёл шаг → RED
##   - if: false на gitleaks-шаге → RED (шаг существует, но отключён)
##   - R5: negative-тесты — YAML без шага / закомментированный шаг → RED
## @rationale W3 T3.4 (Protection Gaps): gitleaks был в pre-commit (локально, обходится
##            --no-verify) и deploy-project.yml L1 (только деплой проектов). Главный security-гейт
##            (security-scan.yml на push/PR main) gitleaks не имел — утечка секрета проходила CI.
##            Гейт фиксирует присутствие/активность шага — защита от тихого удаления/комментирования.
## @changes 2026-08-13 | DevPlan 160 W3 T3.4 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

SECURITY_SCAN_PATH: Path = repo_root() / ".github" / "workflows" / "security-scan.yml"


# region SCANNER


def _scan_gitleaks_step(content: str) -> list[str]:
    """Проверить наличие АКТИВНОГО gitleaks-шага в security-scan.yml. Возвращает violations.

    ## @purpose — Детектор T3.4: шаг с gitleaks в uses/run/name обязан существовать в YAML-структуре,
    ##            не быть закомментирован (сырые строки) и не быть отключён if: false.
    ## @io — ⇥ content: str (сырое содержимое security-scan.yml) → ⎋ list[str] violations
    ## @complexity — O(L) где L = строки
    ## @invariants
    ##   - YAML-парс: шаг = step с «gitleaks» в uses|run|name (case-insensitive)
    ##   - if: false на найденном шаге → violation (шаг отключён)
    ##   - Шаг не найден в YAML: если ВСЕ gitleaks-строки закомментированы → «закомментирован»,
    ##     иначе → «отсутствует»
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return [f"security-scan.yml не парсится как YAML: {exc}"]
    if not isinstance(data, dict):
        return ["security-scan.yml пуст или не dict — gitleaks-шаг отсутствует"]

    violations: list[str] = []
    active_steps: list[str] = []
    jobs = data.get("jobs", {}) or {}
    for job_name, job_cfg in jobs.items():
        if not isinstance(job_cfg, dict):
            continue
        for step in job_cfg.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            blob = " ".join(str(step.get(k, "")) for k in ("uses", "run", "name") if step.get(k)).lower()
            if "gitleaks" not in blob:
                continue
            step_if = step.get("if")
            # YAML парсит `if: false` в bool False; строковая форма 'false' тоже возможна
            if step_if is False or str(step_if).strip().lower() == "false":
                violations.append(f"job '{job_name}': gitleaks-шаг отключён через 'if: false'")
                continue
            active_steps.append(f"{job_name}/{step.get('name', '(unnamed)')}")

    if active_steps:
        return violations  # есть как минимум один активный шаг — остальные нарушения (если есть) в списке

    # Активных шагов нет — различаем «отсутствует» vs «закомментирован» по сырым строкам
    if _all_gitleaks_lines_commented(content):
        violations.append("gitleaks-шаг ЗАКОММЕНТИРОВАН в security-scan.yml (строки-комментарии не считаются)")
    else:
        violations.append("АКТИВНЫЙ gitleaks-шаг ОТСУТСТВУЕТ в security-scan.yml (uses/run/name с 'gitleaks')")
    return violations


def _all_gitleaks_lines_commented(content: str) -> bool:
    """True, если КАЖДАЯ строка с «gitleaks» является комментарием (stripped начинается с '#').

    ## @purpose — Детекция закомментированного шага: YAML-парс комментарии не видит, поэтому
    ##            закомментированный шаг выглядит как «отсутствующий». Различие важно для
    ##            repair-подсказки («раскомментируй» vs «добавь заново»).
    ## @io — ⇥ content: str → ⎋ bool
    ## @complexity — O(L)
    """
    hits = [line for line in content.splitlines() if "gitleaks" in line.lower()]
    if not hits:
        return False
    return all(line.strip().startswith("#") for line in hits)


# endregion SCANNER


# region TESTS


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · gitleaks-шаг в security-scan.yml (W3 T3.4)
# · Scenario: шаг удалён / закомментирован / отключён if: false — утечка секрета проходит CI
# · Last fail: N/A (preventive — шаг добавлен в той же волне W3 T3.4)
# · Remove if: gitleaks переносится в другой workflow/механизм (обновить таргет скан)
def test_gitleaks_step_present_and_active(caplog) -> None:
    """security-scan.yml содержит АКТИВНЫЙ gitleaks-шаг (не закомментирован, не if: false)."""
    assert SECURITY_SCAN_PATH.is_file(), f"security-scan.yml не найден: {SECURITY_SCAN_PATH}"
    content = SECURITY_SCAN_PATH.read_text(encoding="utf-8")

    violations = _scan_gitleaks_step(content)
    if violations:
        for v in violations:
            logger.warning("[IMP:7][gitleaks-step] %s", v)
    assert not violations, (
        "[GATE:FAIL][id:gitleaks-step-exists][class:L2]\n"
        ">>> REPAIR_RECIPE_START >>>\n"
        "Восстанови активный gitleaks-шаг в .github/workflows/security-scan.yml: "
        "1) uses: ./.github/actions/setup-gitleaks (composite SoT v8.30.1), "
        "2) run: gitleaks git --no-banner -v (exit 1 при finding). НЕ комментируй и НЕ ставь if: false.\n"
        "<<< REPAIR_RECIPE_END <<<\n" + "\n".join(violations)
    )
    logger.info("[IMP:9][gitleaks-step] PASS: активный gitleaks-шаг присутствует в security-scan.yml")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · gitleaks-step — YAML без шага
# · Last fail: security-scan.yml без gitleaks-шага (состояние до W3 T3.4)
# · Remove if: gitleaks-детекция мигрирует вне security-scan.yml
def test_negative_gitleaks_step_missing_detected(caplog) -> None:
    """R5 negative: security-scan.yml БЕЗ gitleaks-шага → violations ≥ 1."""
    synthetic = (
        "name: security-scan\n"
        "on: {push: {branches: [main]}}\n"
        "jobs:\n"
        "  security:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v7\n"
        "      - name: Trivy scan\n"
        "        uses: aquasecurity/trivy-action@0.28.0\n"
    )
    violations = _scan_gitleaks_step(synthetic)
    logger.info("[IMP:8][gitleaks-step][negative] violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: отсутствующий gitleaks-шаг не детектирован: {violations!r}"
    assert "ОТСУТСТВУЕТ" in violations[0], f"R5 FAIL: неверная категория violation: {violations!r}"
    logger.info("[IMP:9][gitleaks-step][negative] PASS: отсутствующий шаг детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · gitleaks-step — закомментированный шаг
# · Last fail: «# - uses: ./.github/actions/setup-gitleaks» — YAML-парс не видит закомментированный шаг
# · Remove if: gitleaks-детекция мигрирует вне security-scan.yml
def test_negative_gitleaks_step_commented_detected(caplog) -> None:
    """R5 negative: закомментированный gitleaks-шаг → violations ≥ 1 (комментарии не считаются)."""
    synthetic = (
        "name: security-scan\n"
        "on: {push: {branches: [main]}}\n"
        "jobs:\n"
        "  security:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v7\n"
        "      # - name: Setup gitleaks\n"
        "      #   uses: ./.github/actions/setup-gitleaks\n"
        "      # - name: Gitleaks scan\n"
        "      #   run: gitleaks git --no-banner -v\n"
    )
    violations = _scan_gitleaks_step(synthetic)
    logger.info("[IMP:8][gitleaks-step][negative] violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: закомментированный gitleaks-шаг не детектирован: {violations!r}"
    assert "ЗАКОММЕНТИРОВАН" in violations[0], f"R5 FAIL: неверная категория violation: {violations!r}"
    logger.info("[IMP:9][gitleaks-step][negative] PASS: закомментированный шаг детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · gitleaks-step — if: false отключение
# · Last fail: шаг существует, но отключён «if: false» — активной защиты нет
# · Remove if: gitleaks-детекция мигрирует вне security-scan.yml
def test_negative_gitleaks_step_disabled_detected(caplog) -> None:
    """R5 negative: gitleaks-шаг с 'if: false' → violations ≥ 1 (шаг существует, но отключён)."""
    synthetic = (
        "name: security-scan\n"
        "on: {push: {branches: [main]}}\n"
        "jobs:\n"
        "  security:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Gitleaks secret scan\n"
        "        if: false\n"
        "        run: gitleaks git --no-banner -v\n"
    )
    violations = _scan_gitleaks_step(synthetic)
    logger.info("[IMP:8][gitleaks-step][negative] violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: отключённый gitleaks-шаг не детектирован: {violations!r}"
    assert "if: false" in violations[0], f"R5 FAIL: violation не про if: false: {violations!r}"
    logger.info("[IMP:9][gitleaks-step][negative] PASS: отключённый шаг детектируется")


# endregion TESTS
