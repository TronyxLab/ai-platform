# GREP_SUMMARY: gate workflow-org-agnostic caller-context deploy-project quality-steps no-platform-actions no-inline-python3 gitleaks-pin-parity R5
# STRUCTURE: ▶ parse deploy-project.yml → ◇ quality-block (validate→setup-ssh) → ◇ 0 non-stdlib uses: → ◇ 0 inline python3 / make → ◇ 0 relative+qualified platform action-literals → ◇ gitleaks pin parity (v8.30.1) → ⊕ R5 negatives (dotted import RED / python3 -m RED / qualified action RED / relative action RED) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Паритет-гейт org-agnostic deploy-project.yml (DevPlan 137 §5 W2, TRAP §10.2
##           caller-context): inline quality-шаги — ТОЛЬКО stdlib actions (actions/*) + CLI;
##           0 платформенных action-литералов (relative ./ и qualified */ai-platform/);
##           0 inline python3 в новых quality-шагах (гейт no-new-inline-python3); gitleaks
##           binary pin == канону (practices_manifest.yaml#pins.gitleaks, паритет setup-gitleaks).
##           Workflow исполняется в контексте CALLER'а (проекта), где платформы НЕТ — занос
##           платформенной зависимости молча ломает CI проекта (TRAP[BUG-risk] 2026-08-05).
## @scope    Read-only статический анализ .github/workflows/deploy-project.yml +
##           core/internal/practices/practices_manifest.yaml (pins).
## @invariants
##   - quality-шаги (между "Validate project payload" и "Setup SSH key") не содержат
##     `uses:` кроме actions/setup-node@v4 (stdlib-allowlist)
##   - quality-шаги не содержат inline python3 (python3 -c / python3 -m / import core);
##     исполняемые make gate/make deploy в run-шагах RED-ятся test_gate_deploy_channel T2
##   - Весь workflow: `uses:` ⊆ префикс actions/ — 0 relative (./), 0 qualified (*/ai-platform/)
##   - Gitleaks scan step: VERSION="8.30.1" == pins.gitleaks канона (анти-дрейф, W2-4)
##   - Negative (R5 anti-survivorship): dotted py import RED, python3 -m RED,
##     qualified action RED, relative action RED — детектор жив
## @rationale DevPlan 137 §5 W2 + §10.2 TRAP: inline quality-шаги исполняются в caller'е —
##            reusable workflow quality-gate.yml ОТКЛОНЁН (аудит 137), level/language читаются
##            из ai-platform.yaml в рантайме; структурная защита от заноса платформенных
##            зависимостей (паритет test_gate_deploy_channel T2 + no-new-inline-python3).
## @changes  2026-08-05 · DevPlan 137 W2 — создан (паритет-гейт inline quality-шагов)
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_DEPLOY_PROJECT_YML: pathlib.Path = ROOT / ".github" / "workflows" / "deploy-project.yml"
_PRACTICES_MANIFEST_YML: pathlib.Path = ROOT / "core" / "internal" / "practices" / "practices_manifest.yaml"

# Quality-блок: шаги между validate и setup-ssh (DevPlan 137 §5 W2: «после validate, до deliver»)
_QUALITY_BLOCK_START = "Validate project payload"
_QUALITY_BLOCK_END = "Setup SSH key"

# Stdlib-экшены, разрешённые в caller-контексте (TRAP §10.2): только actions/*
_ALLOWED_USES_PREFIX = "actions/"

# Инлайн-python3 ИСПОЛНЕНИЯ в quality-шагах → RED (не слово "python" в прозе/комментариях):
# python3 -c / python3 -m / python3 <<heredoc / python -c. Плюс dotted-импорт платформенного
# модуля (import core.internal) — ломается в caller'е "Cannot find module 'core.internal...'".
# make-таргеты НЕ сканируются здесь: текстовые рекомендации Maturity warn («run make
# project-sync-practices») — интенциональная проза, а исполняемые make gate/make deploy в
# run-шагах уже RED-ятся test_gate_deploy_channel T2.
_INLINE_PYTHON_EXEC_RE = re.compile(r"python3?(?:\s+-[A-Za-z])?\s+(?:-c\b|-m\b|<<)")
_DOTTED_PLATFORM_IMPORT_RE = re.compile(r"import\s+core\.internal")

# Платформенные action-литералы во всём workflow → RED
_RELATIVE_ACTION_RE = re.compile(r"uses:\s*\./")
_QUALIFIED_ACTION_RE = re.compile(r"uses:\s*[^#\n]*?/ai-platform/")

# Pin gitleaks (W2-4 паритет): VERSION="X" в Gitleaks scan step
_GITLEAKS_PIN_RE = re.compile(r'VERSION="([0-9][0-9.]*)"\s*#\s*pin канона')


# region HELPER__load_workflow_steps
def _load_workflow_steps(yaml_path: pathlib.Path | None = None) -> list[dict]:
    """Загрузить steps джобы deploy из deploy-project.yml (или probe-файла для негативов)."""
    target = yaml_path or _DEPLOY_PROJECT_YML
    if not target.is_file():
        pytest.fail(f"Missing {target.relative_to(ROOT)} — единый канал обязателен (T4)")
    with pathlib.Path(target).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    jobs = data.get("jobs") or {}
    if "deploy" not in jobs:
        pytest.fail(f"{target.relative_to(ROOT)}: нет job 'deploy' — single-job инвариант (AC W2)")
    steps = jobs["deploy"].get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


# endregion HELPER__load_workflow_steps


# region HELPER__quality_block
def _quality_block(steps: list[dict]) -> list[dict]:
    """Подмножество шагов между validate и setup-ssh (индексно, по именам — устойчиво к переименованию шагов)."""
    names = [s.get("name", "") for s in steps]
    try:
        start = names.index(_QUALITY_BLOCK_START)
        end = names.index(_QUALITY_BLOCK_END)
    except ValueError as exc:
        pytest.fail(f"deploy-project.yml: quality-блок границы не найдены ({exc}) — AC W2 нарушен")
    return steps[start + 1 : end]


# endregion HELPER__quality_block


# region HELPER__scan_quality_block_violations
def _scan_quality_block_violations(steps: list[dict]) -> list[str]:
    """Сканировать quality-блок: 0 non-stdlib uses:, 0 inline python3, 0 make (caller-контекст)."""
    violations: list[str] = []
    for step in _quality_block(steps):
        name = step.get("name", "(unnamed)")
        uses = step.get("uses", "")
        if uses and not uses.startswith(_ALLOWED_USES_PREFIX):
            violations.append(f"{name}: non-stdlib action '{uses}' (TRAP §10.2: caller не имеет платформы)")
        run = step.get("run", "") or ""
        if _INLINE_PYTHON_EXEC_RE.search(run) or _DOTTED_PLATFORM_IMPORT_RE.search(run):
            violations.append(f"{name}: inline python3 / платформенный импорт (гейт no-new-inline-python3)")
    return violations


# endregion HELPER__scan_quality_block_violations


# region HELPER__scan_platform_action_literals
def _scan_platform_action_literals(steps: list[dict]) -> list[str]:
    """Сканировать ВЕСЬ workflow на платформенные action-литералы: relative (./) и qualified (*/ai-platform/)."""
    violations: list[str] = []
    for step in steps:
        uses = step.get("uses", "") or ""
        if _RELATIVE_ACTION_RE.search(f"uses: {uses}"):
            violations.append(f"{step.get('name', '(unnamed)')}: relative action '{uses}' — резолвится в caller'е")
        if _QUALIFIED_ACTION_RE.search(f"uses: {uses}"):
            violations.append(f"{step.get('name', '(unnamed)')}: qualified org action '{uses}' — хардкод org (DD9 RED)")
    return violations


# endregion HELPER__scan_platform_action_literals


# region HELPER__scan_gitleaks_pin_parity
def _scan_gitleaks_pin_parity(steps: list[dict]) -> list[str]:
    """Паритет gitleaks pin (W2-4): VERSION в Gitleaks scan == pins.gitleaks канона (v8.30.1)."""
    canon = yaml.safe_load(_PRACTICES_MANIFEST_YML.read_text(encoding="utf-8")) or {}
    canon_pin = (canon.get("pins") or {}).get("gitleaks", "")
    violations: list[str] = []
    for step in steps:
        if step.get("name") != "Gitleaks scan (L1)":
            continue
        run = step.get("run", "") or ""
        match = _GITLEAKS_PIN_RE.search(run)
        if not match:
            violations.append('Gitleaks scan: VERSION pin не найден (формат VERSION="x.y.z" # pin канона)')
            continue
        version = match.group(1)
        if f"v{version}" != canon_pin:
            violations.append(f"Gitleaks scan: VERSION={version} != канон pins.gitleaks={canon_pin} (анти-дрейф, W2-4)")
    if not any(s.get("name") == "Gitleaks scan (L1)" for s in steps):
        violations.append("Gitleaks scan (L1) шаг отсутствует — L1 блок в CI не исполняется")
    return violations


# endregion HELPER__scan_gitleaks_pin_parity


# region FUNC_test_workflow_single_job_org_agnostic
@pytest.mark.gate
@ldd_trajectory
def test_workflow_single_job_org_agnostic(caplog) -> None:
    """deploy-project.yml — single-job (AC W2) и org-agnostic: quality-шаги без платформенных зависимостей.

    # ▶ load steps → ◇ quality-block violations → ◇ platform action literals → ⊕ assert 0 → ⎋ PASS|FAIL
    """
    # 🧪 TRAP[TEST] · DevPlan 137 §5 W2 · caller-context trap (TRAP §10.2)
    # · Regression: inline quality-шаги падают в CI проекта с "Cannot find module 'core.internal...'"
    # ·   или "Unable to resolve action './.github/actions/...'" — платформы нет в caller'е
    # · Scenario: quality-блок deploy-project.yml — только stdlib actions + CLI, 0 inline python3
    # · Last fail: N/A (preventive — паритет deploy-project.yml 2026-08-03)
    # · Remove if: caller-контекст начинает поставлять платформу (архитектурно)
    caplog.set_level(logging.INFO)

    steps = _load_workflow_steps()

    # AC W2: single-job
    with pathlib.Path(_DEPLOY_PROJECT_YML).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert len(data.get("jobs") or {}) == 1, "AC W2: deploy-project.yml должен оставаться single-job"

    violations = _scan_quality_block_violations(steps) + _scan_platform_action_literals(steps)

    logger.info("[IMP:8][org-agnostic] Проверено %d шагов, %d violation(ов)", len(steps), len(violations))
    for v in violations:
        logger.warning("[IMP:10][org-agnostic] %s", v)

    assert not violations, (
        "[IMP:10][org-agnostic] deploy-project.yml содержит платформенные зависимости в caller-контексте:\n"
        + "\n".join(violations)
    )
    logger.info("[IMP:9][org-agnostic] PASS: workflow single-job и org-agnostic (stdlib actions + CLI)")


# endregion FUNC_test_workflow_single_job_org_agnostic


# region FUNC_test_gitleaks_pin_parity
@pytest.mark.gate
@ldd_trajectory
def test_gitleaks_pin_parity(caplog) -> None:
    """Gitleaks binary pin в deploy-project.yml == канон pins.gitleaks (v8.30.1) — W2-4 паритет.

    # ▶ Gitleaks scan run → ◇ VERSION literal → ◇ vs canon pins.gitleaks → ⎋ PASS|FAIL
    """
    # 🧪 TRAP[TEST] · DevPlan 137 W2-4 · gitleaks pin parity (анти-дрейф)
    # · Regression: апгрейд gitleaks в каноне без правки workflow — рассинхрон скана CI и платформы
    # · Scenario: VERSION="8.30.1" в шаге Gitleaks scan == practices_manifest.yaml#pins.gitleaks
    # · Last fail: N/A (preventive)
    # · Remove if: gitleaks уходит из inline quality-шагов
    caplog.set_level(logging.INFO)

    steps = _load_workflow_steps()
    violations = _scan_gitleaks_pin_parity(steps)

    for v in violations:
        logger.warning("[IMP:10][org-agnostic][gitleaks] %s", v)

    assert not violations, "[IMP:10][org-agnostic][gitleaks] паритет pin нарушен:\n" + "\n".join(violations)
    logger.info("[IMP:9][org-agnostic][gitleaks] PASS: pin == канон (v8.30.1)")


# endregion FUNC_test_gitleaks_pin_parity


# region FUNC_test_negative_dotted_py_import_detected
@pytest.mark.gate
@ldd_trajectory
def test_negative_dotted_py_import_detected(tmp_path, caplog) -> None:
    """R5 negative: dotted py import (import core.internal) в quality-шаге → RED.

    # ▶ probe quality-step с python3 -c "import core.internal..." → ◇ violation ≥ 1 → ⎋ RED (детектор жив)
    """
    # 🧪 TRAP[TEST] · DevPlan 137 W2 · NEGATIVE (R5) — detector не сломан
    # · Regression: если детектор inline python3 перестанет ловить dotted-импорт — гейт вечнозелёный
    # · Scenario: python3 -c "import core.internal.practices.check_project" в quality-шаге → RED
    # · Last fail: 2026-08-03 — исходный занос python3 -m core в deploy-project.yml (caller-контекст)
    # · Remove if: гейт org-agnostic удалён
    caplog.set_level(logging.INFO)

    probe = tmp_path / "deploy-project.yml"
    probe.write_text(
        """\
jobs:
  deploy:
    steps:
      - name: Validate project payload
        run: echo validate
      - name: Quality lint/test
        run: |
          python3 -c "import core.internal.practices.check_project"
          echo ok
      - name: Setup SSH key
        run: echo ssh
"""
    )

    steps = _load_workflow_steps(probe)
    violations = _scan_quality_block_violations(steps)

    logger.info("[IMP:8][org-agnostic][negative-dotted] Violations: %s", violations)
    assert violations, "CRITICAL: детектор не поймал dotted py import (import core.internal) — гейт вечнозелёный (R5)"
    assert any("inline python3" in v for v in violations), f"ожидался inline python3 violation, got {violations}"
    logger.info("[IMP:9][org-agnostic][negative-dotted] PASS: dotted py import детектируется (RED)")


# endregion FUNC_test_negative_dotted_py_import_detected


# region FUNC_test_negative_python3_m_detected
@pytest.mark.gate
@ldd_trajectory
def test_negative_python3_m_detected(tmp_path, caplog) -> None:
    """R5 negative: python3 -m core.internal.* в quality-шаге → RED.

    # ▶ probe quality-step с python3 -m core.internal... → ◇ violation ≥ 1 → ⎋ RED (детектор жив)
    """
    # 🧪 TRAP[TEST] · DevPlan 137 W2 · NEGATIVE (R5) — detector не сломан
    # · Regression: если детектор перестанет ловить python3 -m core — гейт вечнозелёный
    # · Scenario: python3 -m core.internal.practices.check_project в quality-шаге → RED
    # · Last fail: 2026-08-03 — исходный занос python3 -m core (TRAP[BUG] caller-контекст)
    # · Remove if: гейт org-agnostic удалён
    caplog.set_level(logging.INFO)

    probe = tmp_path / "deploy-project.yml"
    probe.write_text(
        """\
jobs:
  deploy:
    steps:
      - name: Validate project payload
        run: echo validate
      - name: Quality lint/test
        run: |
          python3 -m core.internal.practices.check_project --project-dir .
          echo ok
      - name: Setup SSH key
        run: echo ssh
"""
    )

    steps = _load_workflow_steps(probe)
    violations = _scan_quality_block_violations(steps)

    logger.info("[IMP:8][org-agnostic][negative-python3-m] Violations: %s", violations)
    assert violations, "CRITICAL: детектор не поймал python3 -m core.internal — гейт вечнозелёный (R5)"
    assert any("inline python3" in v for v in violations), f"ожидался inline python3 violation, got {violations}"
    logger.info("[IMP:9][org-agnostic][negative-python3-m] PASS: python3 -m core.internal детектируется (RED)")


# endregion FUNC_test_negative_python3_m_detected


# region FUNC_test_negative_qualified_and_relative_action_detected
@pytest.mark.gate
@ldd_trajectory
def test_negative_qualified_and_relative_action_detected(tmp_path, caplog) -> None:
    """R5 negative: qualified (*/ai-platform/) и relative (./) action-литералы → RED.

    # ▶ probe workflow с uses: tronyx161/ai-platform/... + uses: ./.github/actions/... → ◇ ≥ 2 → ⎋ RED
    """
    # 🧪 TRAP[TEST] · DevPlan 137 W2 · NEGATIVE (R5) — detector не сломан
    # · Regression: если детектор action-литералов перестанет ловить qualified/relative — гейт вечнозелёный
    # · Scenario: uses: <org>/ai-platform/... (DD9 RED) и uses: ./.github/actions/... → RED
    # · Last fail: 2026-08-03 — relative actions в deploy-project.yml (TRAP[BUG] caller-контекст)
    # · Remove if: гейт org-agnostic удалён
    caplog.set_level(logging.INFO)

    probe = tmp_path / "deploy-project.yml"
    probe.write_text(
        """\
jobs:
  deploy:
    steps:
      - name: Qualified action
        uses: tronyx161/ai-platform/.github/actions/setup-platform
      - name: Relative action
        uses: ./.github/actions/setup-project
      - name: Good stdlib
        uses: actions/setup-python@v5
"""
    )

    steps = _load_workflow_steps(probe)
    violations = _scan_platform_action_literals(steps)

    logger.info("[IMP:8][org-agnostic][negative-actions] Violations: %s", violations)
    assert violations, "CRITICAL: детектор не поймал платформенные action-литералы — гейт вечнозелёный (R5)"
    joined = "\n".join(violations)
    assert "qualified org action" in joined, f"qualified action должен детектироваться, got: {joined}"
    assert "relative action" in joined, f"relative action должен детектироваться, got: {joined}"
    logger.info("[IMP:9][org-agnostic][negative-actions] PASS: qualified+relative action-литералы детектируются (RED)")


# endregion FUNC_test_negative_qualified_and_relative_action_detected
