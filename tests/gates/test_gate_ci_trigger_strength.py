# GREP_SUMMARY: gate ci-trigger-strength workflow-run deploy-precondition fast-gate full-gate push-filter typo-protection C-1 canary
# STRUCTURE: ▶ scan .github/workflows → ◇ downstream = workflow_run-только (0 PR-direct) → ◇ gate file exists + name matches (typo) → ◇ gate содержит make gate MODE=fast (сила ≥ fast) → ◇ job if: conclusion==success + event==push → ⎋ violations
# region MODULE_CONTRACT
## @purpose  Gate-тест силы CI-trigger (C-1, DevPlan 136 W11 T11.1): deploy-цепочка
##           (core-deploy/mirror/build-platform) триггерится ТОЛЬКО через workflow_run от
##           gate-workflow; gate-workflow обязан выполнять `make gate MODE=fast` (никогда слабее);
##           typo-защита (workflow_run ref = name файла); push-фильтр + conclusion==success.
##           Ограничение (задокументировано): полный full-gate (platform-test) НЕ является
##           precondition деплоя by design (D2, TRAP[DECISION] в platform-gate-fast.yml) —
##           этот тест enforce-ит enforce-емое (канал, силу ≥ fast, push-фильтр), не remote
##           branch protection (API: protected: false на private Free-плане).
## @scope    Только .github/workflows/*.yml структура. Не проверяет GitHub-remote конфигурацию
##           (branch protection/rulesets — вне репозитория).
## @invariants
##   - Downstream workflows (core-deploy/mirror/build-platform): НЕ имеют pull_request/pull_request_target trigger
##   - workflow_run.workflows ссылается на platform-gate-fast; файл существует; name == ref (typo → silent fail)
##   - platform-gate-fast.yml содержит `make gate MODE=fast` (даунгрейд до pre-commit-only = RED)
##   - Каждый deploy-job `if` содержит workflow_run.conclusion == 'success' И workflow_run.event == 'push'
##   - platform-test.yml (full gate) имеет pull_request_target trigger — full-gate бежит на PR
## @rationale C-1 (DevPlan 136 §11.2): «CI зелёный, система врёт» — деплой по слабому гейту.
##            Верификация 2026-08-05: branch protection отсутствует (protected: false) → прямые
##            push в main возможны; deploy-гейт = fast-gate by design (D2 U-57, TRAP в
##            platform-gate-fast.yml). Этот тест — canary: гарантирует, что deploy-канал НЕ
##            ослабнет (PR-direct trigger, гейт слабее fast, typo в имени, снятие push-фильтра).
## @changes 2026-08-05 | DevPlan 136 W11 T11.1 — Created (C-1 gate-test + TRAP[DECISION])
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml

from tests.helpers.gate_helpers import assert_ldd_imp9, load_yaml, repo_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"

# Downstream deploy-цепочка — триггерится workflow_run от platform-gate-fast (D2, 116 B11 T4)
_DOWNSTREAM_WORKFLOWS: tuple[str, ...] = ("core-deploy.yml", "mirror.yml", "build-platform.yml")
_GATE_WORKFLOW: str = "platform-gate-fast.yml"
_FULL_GATE_WORKFLOW: str = "platform-test.yml"
_FAST_GATE_MARKER: str = "make gate MODE=fast"


def _on_section(workflow: dict) -> dict:
    """Normalized 'on' section (YAML parses `on:` → boolean True key)."""
    on_section = workflow.get("on") or workflow.get(True) or {}
    return on_section if isinstance(on_section, dict) else {}


def _gate_references(workflow: dict) -> list[str]:
    """workflow_run.workflows refs (list of workflow NAMES)."""
    wf_run = _on_section(workflow).get("workflow_run", {})
    workflows = wf_run.get("workflows", []) if isinstance(wf_run, dict) else []
    return [w for w in workflows if isinstance(w, str)]


def _triggers_directly(workflow: dict, trigger: str) -> bool:
    """True if the workflow has a direct PR-type trigger (deploy-on-PR is forbidden)."""
    return trigger in _on_section(workflow)


def _gate_strength_findings(wf_dir: pathlib.Path) -> list[str]:
    """Скан контракта силы trigger'а. Возвращает список нарушений (пусто = контракт соблюдён).

    ## @purpose  Проверить, что deploy-цепочка гейтится ТОЛЬКО workflow_run от gate-workflow,
    ##            gate не слабее fast, typo-безопасна, push-фильтрована.
    ## @io        ⇥ wf_dir: Path (.github/workflows или tmp_path-фикстура) → ⎋ list[str] нарушений
    ## @complexity O(D * S) — downstream workflows × их steps/refs
    ## @invariants
    ##   - Работает на реальном дереве И tmp_path-фикстурах (negative-тесты R5) — единый скан
    ##   - Не проверяет remote branch protection (вне репозитория, задокументировано в контракте)
    """
    findings: list[str] = []

    # 1. Gate workflow: существует + выполняет fast-gate (сила ≥ fast)
    gate_path = wf_dir / _GATE_WORKFLOW
    if not gate_path.is_file():
        findings.append(f"{_GATE_WORKFLOW} MISSING — deploy-цепочка не имеет gate")
    elif _FAST_GATE_MARKER not in gate_path.read_text():
        findings.append(f"{_GATE_WORKFLOW} НЕ содержит `{_FAST_GATE_MARKER}` — gate слабее fast (C-1)")

    # 2. Downstream контракты
    for name in _DOWNSTREAM_WORKFLOWS:
        path = wf_dir / name
        if not path.is_file():
            continue  # частичная фикстура в negative-тестах
        try:
            wf = load_yaml(path)
        except Exception as exc:  # parse-failure = violation (fail loud)
            findings.append(f"{name}: unparseable YAML ({exc})")
            continue

        # 2a. Deploy НИКОГДА не триггерится напрямую PR-событием
        findings.extend(
            f"{name}: прямой {trigger}-триггер — deploy обязан идти через workflow_run гейта"
            for trigger in ("pull_request", "pull_request_target")
            if _triggers_directly(wf, trigger)
        )

        # 2b. workflow_run ссылается на gate-workflow (ref — имя workflow, файл = <ref>.yml)
        refs = _gate_references(wf)
        if not any(f"{ref}.yml" == _GATE_WORKFLOW or ref == _GATE_WORKFLOW for ref in refs):
            findings.append(f"{name}: workflow_run не ссылается на {_GATE_WORKFLOW} (refs: {refs})")

        # 2c. Typo-защита: workflow_run ref == name файла гейта (typo → тихий отказ downstream)
        for ref in refs:
            ref_file = wf_dir / f"{ref}.yml"
            if not ref_file.is_file():
                findings.append(f"{name}: workflow_run ссылается на '{ref}' — файл {ref}.yml отсутствует")
                continue
            try:
                ref_wf = load_yaml(ref_file)
            except Exception as exc:  # parse-failure = violation (fail loud)
                findings.append(f"{name}: gate {ref}.yml unparseable ({exc})")
                continue
            if ref_wf.get("name") != ref:
                findings.append(
                    f"{name}: workflow_run ref '{ref}' != gate name {ref_wf.get('name')!r} — typo → silent failure"
                )

        # 2d. Каждый deploy-job: conclusion==success + event==push (никаких деплоев по PR/failed)
        jobs = wf.get("jobs", {})
        for job_name, job_cfg in jobs.items():
            if not isinstance(job_cfg, dict):
                continue
            job_if = str(job_cfg.get("if", ""))
            if "workflow_run.conclusion == 'success'" not in job_if:
                findings.append(f"{name}/{job_name}: job if не содержит workflow_run.conclusion == 'success'")
            if "workflow_run.event == 'push'" not in job_if:
                findings.append(f"{name}/{job_name}: job if не содержит workflow_run.event == 'push' фильтр")

    return findings


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (C-1) · Deploy-цепочка гейтится workflow_run от fast-gate
# · Last fail: C-1 — deploy после fast-gate; full-gate PR-only (by design, D2); protected: false
# · Remove if: downstream переключается на полный full-gate (platform-test) как precondition деплоя
@pytest.mark.gate
def test_ci_trigger_strength_contract(caplog) -> None:
    """Canary: deploy-канал не слабее fast-gate, push-фильтрован, typo-безопасен."""
    caplog.set_level(logging.INFO)
    findings = _gate_strength_findings(_WORKFLOW_DIR)

    if findings:
        logger.error("[IMP:9][gate][ci-trigger-strength] ⛔ %d violation(s)", len(findings))
        for f in findings:
            logger.error("[IMP:10][gate][ci-trigger-strength]   - %s", f)
        pytest.fail("Deploy trigger-strength contract violated (C-1, DevPlan 136 W11 T11.1):\n" + "\n".join(findings))

    logger.info(
        "[IMP:9][gate][ci-trigger-strength] ✅ downstream deploy-цепочка гейтится workflow_run от %s (fast, min), "
        "push-фильтрована, typo-безопасна; full-gate бежит на PR (platform-test)",
        _GATE_WORKFLOW,
    )
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (C-1) · full-gate (platform-test) присутствует на PR
# · Last fail: если platform-test лишится PR-триггера — full-gate вообще не запускается (полная слепота)
# · Remove if: full-gate удаляется как концепция
@pytest.mark.gate
def test_full_gate_runs_on_pr(caplog) -> None:
    """platform-test.yml (full gate) обязан иметь PR-триггер — full-сила проверяется на PR."""
    caplog.set_level(logging.INFO)
    wf = load_yaml(_WORKFLOW_DIR / _FULL_GATE_WORKFLOW)
    has_pr = _triggers_directly(wf, "pull_request_target") or _triggers_directly(wf, "pull_request")
    logger.info("[IMP:9][gate][ci-trigger-strength] %s PR-триггер: %s", _FULL_GATE_WORKFLOW, has_pr)
    assert has_pr, f"{_FULL_GATE_WORKFLOW} должен иметь pull_request/pull_request_target trigger"
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · C-1 вход: gate без `make gate MODE=fast` детектируется
# · Last fail: C-1 гипотеза «gate слабее fast деплоит» (даунгрейд гейта до pre-commit-only)
# · Remove if: канон fast-gate-минимум отменяется
@pytest.mark.gate
def test_downgraded_gate_detected_negative(tmp_path, caplog) -> None:
    """R5 negative: даунгрейд gate-workflow (нет fast-gate) → скан ловит нарушение."""
    caplog.set_level(logging.INFO)
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()

    # Gate БЕЗ fast-gate (C-1 вход): только pre-commit — сила ниже fast
    (wf_dir / _GATE_WORKFLOW).write_text(
        "name: platform-gate-fast\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  quick:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: make pre-commit-run\n"
    )
    # Downstream с корректным каналом (остальные проверки проходят — изолируем именно силу гейта)
    (wf_dir / "core-deploy.yml").write_text(
        "name: core-deploy\n"
        "on:\n"
        "  workflow_run:\n"
        "    workflows: ['platform-gate-fast']\n"
        "    types: [completed]\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  deploy:\n"
        "    if: ${{ github.event_name == 'workflow_dispatch' || (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push') }}\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo ok\n"
    )

    findings = _gate_strength_findings(wf_dir)
    logger.info("[IMP:9][gate][ci-trigger-strength][negative] findings: %s", findings)
    assert any(_FAST_GATE_MARKER in f and "содержит" in f for f in findings), (
        f"R5 FAIL: даунгрейд гейта не детектирован. findings={findings}"
    )
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · C-1 вход: deploy по прямому PR-триггеру детектируется
# · Last fail: C-1 — деплой по PR-событию (workflow_run push-фильтр снят / PR-direct trigger)
# · Remove if: deploy-on-PR разрешается каноном (не планируется)
@pytest.mark.gate
def test_pr_triggered_deploy_detected_negative(tmp_path, caplog) -> None:
    """R5 negative: downstream с прямым pull_request-триггером → скан ловит нарушение."""
    caplog.set_level(logging.INFO)
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()

    # Корректный gate (изолируем именно PR-trigger нарушение)
    (wf_dir / _GATE_WORKFLOW).write_text(
        "name: platform-gate-fast\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  fast-gate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - run: {_FAST_GATE_MARKER}\n"
    )
    # Downstream с ПРЯМЫМ pull_request trigger (C-1 вход)
    (wf_dir / "mirror.yml").write_text(
        "name: mirror\n"
        "on:\n"
        "  pull_request:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  mirror:\n"
        "    if: ${{ github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push' }}\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo ok\n"
    )

    findings = _gate_strength_findings(wf_dir)
    logger.info("[IMP:9][gate][ci-trigger-strength][negative] findings: %s", findings)
    assert any("прямой pull_request-триггер" in f for f in findings), (
        f"R5 FAIL: PR-direct deploy не детектирован. findings={findings}"
    )
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · Сканер парсит все workflow YAML (предусловие)
# · Last fail: unparseable workflow → сканер молча пропускал файл (drift невидим)
# · Remove if: workflow-файлы переводятся на не-YAML формат
@pytest.mark.gate
def test_gate_strength_scanner_yaml_parseable(caplog) -> None:
    """Все workflow YAML-файлы парсятся (предусловие сканера — fail loud, не skip)."""
    caplog.set_level(logging.INFO)
    unparseable: list[str] = []
    for yml in sorted(_WORKFLOW_DIR.glob("*.yml")):
        try:
            with open(yml) as f:
                yaml.safe_load(f)
        except Exception as exc:  # parse-failure = violation (fail loud)
            unparseable.append(f"{yml.name}: {exc}")
    logger.info("[IMP:9][gate][ci-trigger-strength] scanned %d workflow files", len(list(_WORKFLOW_DIR.glob("*.yml"))))
    assert not unparseable, "Unparseable workflow YAML:\n" + "\n".join(unparseable)
    assert_ldd_imp9(caplog)
