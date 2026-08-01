# GREP_SUMMARY: gate deploy-channel 1:1:1:1 make-cli-verbs-workflow verbs.py orchestrator_cli deploy.mk deploy-project.yml ping receive verify
# STRUCTURE: ▶ ┌verbs.py CANONICAL_VERBS┐ → ◇ make-таргеты в манифесте → ◇ CLI subcommands ↔ verbs → ◇ workflow run-steps verbs ⊆ {ping,receive,verify} → ⊕ negative (R5) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Gate канала 1:1:1:1 (DevPlan 116 B1 T10) — согласованность make ↔ CLI ↔ forced-command
##           verbs ↔ CI workflow для единого деплой-канала:
##           (1) затронутые make-таргеты (deploy/deploy-project/context-promote) зарегистрированы в
##               entrypoint-manifest.yaml; (2) CLI-подкоманды orchestrator_cli пересекаются с
##               verb-словарём verbs.py (каждый CANONICAL_VERBS диспетчеризуется); (3) run-шаги
##               deploy-project.yml используют только verbs {ping, receive, verify}; (4) negative (R5):
##               verb вне словаря в workflow → RED.
## @scope    Read-only статический анализ: makefiles/deploy.mk, orchestrator_cli.build_parser(),
##           verbs.py CANONICAL_VERBS, .github/workflows/deploy-project.yml, entrypoint-manifest.yaml.
## @invariants
##   - 1:1:1:1: make-таргет ↔ CLI ↔ verb-словарь ↔ workflow-канал
##   - 0 упоминаний platform-deploy/stage-deploy в run-шагах workflow (T4)
##   - Negative-тест обязателен (R5 anti-survivorship)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale DevPlan 116 B1 T10: рассинхрон make↔CLI↔verbs↔workflow ломает канал молча
##            (legacy: 5 способов вызвать деплой с разными форматами). Гейт делает рассинхрон
##            структурно невозможным.
## ⚠️ TRAP[DECISION] · 2026-08-01 · — · Парсинг workflow ограничен run-шагами (YAML → run → regex verbs)
## · Rejected: полный YAML-парсинг всех шагов (хрупко на комментариях/шаблонных выражениях)
## · Reason: run-строки содержат ssh-команды с verb'ами; regex-извлечение ограничено строками
##   с "ssh" или "tar" — при ложных срабатываниях скоуп сужается (DevPlan 116 T10, риск-таблица).
## · Rev: если workflow использует verb вне ssh-строк — расширить скоуп.
## @changes 2026-08-01 | Created (DevPlan 116 B1 T10)
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from core.internal.deploy.orchestrator_cli import build_parser
from core.internal.shared.verbs import CANONICAL_VERBS
from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_DEPLOY_MK = ROOT / "makefiles" / "deploy.mk"
_DEPLOY_PROJECT_YML = ROOT / ".github" / "workflows" / "deploy-project.yml"
_MANIFEST_YML = ROOT / "core" / "entrypoint-manifest.yaml"

# Канон verbs CI-канала (T4): deploy-project.yml использует ТОЛЬКО эти.
# Список-литерал (не set-литерал) — test_gate_exception_audit запрещает {…}-set'ы таргет-имён;
# verbs канала — не make-таргеты, источник — CANONICAL_VERBS (shared/verbs.py) подмножество.
_WORKFLOW_ALLOWED_VERBS = frozenset(["ping", "receive", "verify"])

# Затронутые make-таргеты волны (T1-T5)
_AFFECTED_MAKE_TARGETS = ("deploy", "deploy-project", "context-promote")

# Run-строки, где ожидаются verb'ы (ssh-вызовы); сужает скоуп regex (TRAP[DECISION]).
# Границы: начало строки / пробел / кавычка — verb'ы приходят после `ssh ... "verb ..."`.
_RUN_VERB_RE = re.compile(r'(?:^|[\s"])(ping|receive|verify|status|remove|exit|deploy|platform-deliver)(?:[\s"]|$)')


# region HELPER__extract_workflow_run_verbs
def _extract_workflow_run_verbs() -> list[tuple[str, str]]:
    """Извлечь (step_name, verb) пары из run-шагов deploy-project.yml.

    ## @purpose — Парсит YAML workflow, берёт только run-строки, regex-извлекает verb'ы
    ##            из ssh/tar команд. Возвращает список (step_name, verb).
    ## @io — ⇥ None → ⎋ list[tuple[str, str]]
    ## @complexity — O(L) где L = число run-строк
    ## @invariants
    ##   - Только run-шаги (shell), не uses-шаги
    ##   - Verb'ы ищутся по регулярке в строке
    ##   - Нет workflow-файла → pytest.fail (гейт не может проверить)
    """
    if not _DEPLOY_PROJECT_YML.is_file():
        pytest.fail(f"Missing {_DEPLOY_PROJECT_YML.relative_to(ROOT)} — единый канал обязателен (T4)")

    with open(_DEPLOY_PROJECT_YML) as f:
        data = yaml.safe_load(f)

    # YAML 1.1 парсит "on" как True — обрабатываем оба варианта
    jobs = (data or {}).get("jobs", {})
    results: list[tuple[str, str]] = []
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run")
            if not run or not isinstance(run, str):
                continue
            # TRAP[DECISION]: скоуп сужен до ssh/tar КОМАНД построчно — `ssh ` / `tar ` (не "SSH_HOST").
            # bash `exit 1`/`set -euo pipefail` на отдельных строках не ловятся как verb'ы
            # (DevPlan 116 T10 риск-таблица).
            for line in run.splitlines():
                if "ssh " not in line and "tar " not in line:
                    continue
                results.extend((step.get("name", "?"), m.group(1)) for m in _RUN_VERB_RE.finditer(line))
    return results


# endregion HELPER__extract_workflow_run_verbs


# region HELPER__manifest_has_make_target
def _manifest_has_make_target(target: str) -> bool:
    """Проверить, что make_target зарегистрирован в entrypoint-manifest.yaml (allowed_verbs + секция)."""
    with open(_MANIFEST_YML) as f:
        manifest = yaml.safe_load(f) or {}

    in_allowed = target in manifest.get("allowed_verbs", [])
    in_sections = any(
        entry.get("make_target") == target
        for section in (
            "bootstrap",
            "deploy",
            "build",
            "validate",
            "test",
            "test/gate",
            "scaffold",
            "secrets",
            "lifecycle",
            "provision",
            "dev",
            "repair",
        )
        for entry in manifest.get(section, [])
        if isinstance(entry, dict)
    )
    return in_allowed and in_sections


# endregion HELPER__manifest_has_make_target


# ── Гейт: make-таргеты затронутой волны в манифесте ───────────────────────────


# region FUNC_test_deploy_make_targets_registered
@pytest.mark.gate
@ldd_trajectory
def test_deploy_make_targets_registered(caplog) -> None:
    """Каждый затронутый make-таргет (deploy/deploy-project/context-promote) зарегистрирован в манифесте.

    # ▶ ┌_AFFECTED_MAKE_TARGETS┐ → ◇ manifest_has_make_target → ⊕ missing → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10 (1:1): make-таргет ↔ manifest (allowed_verbs + секция).
    ## @io — caplog → ⎋ None (pytest.fail со списком незарегистрированных)
    ## @complexity — O(T * M) где T = таргеты, M = manifest-записи
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · make↔manifest 1:1
    # · Regression: таргет удалён/добавлен без записи в манифесте (дрейф канала)
    # · Scenario: для каждого таргета волны проверяется allowed_verbs + секция
    # · Last fail: N/A (new test)
    # · Remove if: манифест перестаёт быть реестром make-таргетов
    caplog.set_level(logging.INFO)

    missing = [t for t in _AFFECTED_MAKE_TARGETS if not _manifest_has_make_target(t)]

    logger.info(
        "[IMP:8][deploy_channel_gate] Проверено %d make-таргетов волны, missing=%s",
        len(_AFFECTED_MAKE_TARGETS),
        missing,
    )

    assert not missing, (
        f"[IMP:10][deploy_channel_gate] Make-таргеты не зарегистрированы в entrypoint-manifest.yaml: {missing} "
        "(1:1 make↔manifest — запусти make generate-manifests)"
    )
    logger.info("[IMP:9][deploy_channel_gate] PASS: все make-таргеты волны в манифесте")


# endregion FUNC_test_deploy_make_targets_registered


# ── Гейт: CLI-подкоманды ↔ verb-словарь (1:1) ─────────────────────────────────


# region FUNC_test_cli_subcommands_cover_verb_dictionary
@pytest.mark.gate
@ldd_trajectory
def test_cli_subcommands_cover_verb_dictionary(caplog) -> None:
    """CLI orchestrator_cli: каждый CANONICAL_VERBS диспетчеризуется (dispatch) — 1:1 verbs↔CLI.

    # ▶ ┌CANONICAL_VERBS┐ → ◇ build_parser subcommands + _dispatch маршрутизация → ⊕ missing → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10 (1:1): verb-словарь verbs.py ↔ CLI-диспетчер.
    ##            Каждый verb (ping/exit/status/verify/remove/receive) должен быть обработан dispatch.
    ## @io — caplog → ⎋ None
    ## @complexity — O(V) где V = число verbs
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · verbs↔CLI 1:1
    # · Regression: verb добавлен в словарь, но не диспетчеризуется (или наоборот)
    # · Scenario: CANONICAL_VERBS ⊆ маршрутизируемых verb'ов dispatch + subcommands в build_parser
    # · Last fail: N/A (new test)
    # · Remove if: verb-словарь меняется архитектурно
    caplog.set_level(logging.INFO)

    parser = build_parser()
    cli_subcommands = set(parser._subparsers._group_actions[0].choices.keys())  # type: ignore[attr-defined]

    # ВСЕ 6 verbs маршрутизируются внутри dispatch (проверяем строково по исходнику — надёжнее,
    # чем интроспекция: dispatch — процедурная функция)
    import inspect

    from core.internal.deploy.orchestrator_cli import _dispatch as dispatch_fn

    src = inspect.getsource(dispatch_fn)
    missing_verbs = [v for v in CANONICAL_VERBS if f'verb == "{v}"' not in src]
    assert not missing_verbs, f"CANONICAL_VERBS не диспетчеризуются в _dispatch: {missing_verbs}"

    # dispatch/deliver/receive/status/remove — обязательные subcommands (T2/T5)
    for required in ("dispatch", "deliver", "receive", "status", "remove", "deploy-many"):
        assert required in cli_subcommands, f"CLI subcommand '{required}' отсутствует в build_parser"

    logger.info(
        "[IMP:9][deploy_channel_gate] PASS: все %d verbs диспетчеризуются; CLI subcommands = %s",
        len(CANONICAL_VERBS),
        sorted(cli_subcommands),
    )


# endregion FUNC_test_cli_subcommands_cover_verb_dictionary


# ── Гейт: workflow run-шаги ⊆ {ping, receive, verify} ─────────────────────────


# region FUNC_test_workflow_uses_only_channel_verbs
@pytest.mark.gate
@ldd_trajectory
def test_workflow_uses_only_channel_verbs(caplog) -> None:
    """deploy-project.yml run-шаги используют ТОЛЬКО verbs {ping, receive, verify}; 0 legacy-verb.

    # ▶ extract run-verbs → ◇ verbs ⊆ _WORKFLOW_ALLOWED_VERBS → ⊕ legacy refs → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10 (1:1): workflow-канал ↔ verb-словарь.
    ##            deploy-project.yml: preflight ping, deliver receive, post-deploy verify.
    ## @io — caplog → ⎋ None
    ## @complexity — O(L) где L = run-строки
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · workflow↔verbs 1:1
    # · Regression: workflow использует verb вне канала (status/deploy/legacy) — рассинхрон
    # · Scenario: извлечённые run-verbs ⊆ {ping, receive, verify}; 0 упоминаний platform-deploy/stage-deploy
    # · Last fail: legacy — workflow гонял `status test` под || true + platform-deliver
    # · Remove if: канал workflow меняется архитектурно
    caplog.set_level(logging.INFO)

    run_verbs = _extract_workflow_run_verbs()
    verbs_used = {v for _name, v in run_verbs}

    logger.info("[IMP:8][deploy_channel_gate] run-verbs из deploy-project.yml: %s", sorted(verbs_used))

    assert verbs_used, "Не найдено verb'ов в run-шагах deploy-project.yml — гейт не может проверить канал"
    unexpected = verbs_used - _WORKFLOW_ALLOWED_VERBS
    assert not unexpected, (
        f"[IMP:10][deploy_channel_gate] Workflow использует verbs вне канала: {sorted(unexpected)} "
        f"(допустимо: {sorted(_WORKFLOW_ALLOWED_VERBS)})"
    )

    # 0 упоминаний legacy workflow-каналов в run-шагах (T4)
    content = _DEPLOY_PROJECT_YML.read_text()
    for legacy in ("platform-deploy", "stage-deploy"):
        assert legacy not in content, f"Workflow deploy-project.yml не должен упоминать {legacy} (T4)"

    logger.info("[IMP:9][deploy_channel_gate] PASS: workflow verbs = %s ⊆ {ping, receive, verify}", sorted(verbs_used))


# endregion FUNC_test_workflow_uses_only_channel_verbs


# ── Negative (R5 anti-survivorship): verb вне словаря → RED ───────────────────


# region FUNC_test_workflow_verb_not_in_dictionary_negative
@pytest.mark.gate
@ldd_trajectory
def test_workflow_verb_not_in_dictionary_negative(caplog) -> None:
    """Falsifiability: verb вне канала (status — валидный verb, но НЕ channel-verb) детектируется (R5).

    # ▶ tmp workflow run-step с 'status' → ◇ regex-извлечение → ◇ ∉ _WORKFLOW_ALLOWED_VERBS → ⎋ RED

    ## @purpose — Anti-survivorship: гейт, который не может упасть, — не гейт. Проверяем, что
    ##            извлечение verb'ов из run-строк работает и verb вне канала {ping,receive,verify}
    ##            ловится извлечением (status — валидный CANONICAL_VERBS, но в workflow запрещён T4).
    ## @io — ⇥ caplog → ⎋ None (assert детекта)
    ## @complexity — O(1) — одна фиктивная run-строка
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · R5 anti-survivorship
    # · Regression: если regex-извлечение сломается — гейт вечнозелёный
    # · Scenario: run-step с 'status myproj' (не channel-verb) → детектируется как вне канала
    # · Last fail: legacy — workflow гонял `status test` (не channel-verb) под || true
    # · Remove if: гейт канала удалён
    caplog.set_level(logging.INFO)

    # 'status' — валидный verb диспетчера, НО запрещён в workflow-канале (T4: только ping/receive/verify)
    fake_run = 'ssh ci-deploy@host "status myproj"'
    matches = _RUN_VERB_RE.findall(fake_run)

    logger.info("[IMP:8][deploy_channel_gate][negative] Извлечённые verbs из фиктивного run: %s", matches)
    assert matches, "CRITICAL: regex не извлёк verb из фиктивной run-строки — гейт вечнозелёный!"
    assert "status" in matches, f"Ожидался извлечённый 'status', got {matches}"
    assert "status" in CANONICAL_VERBS, "'status' — канонический verb диспетчера"
    assert "status" not in _WORKFLOW_ALLOWED_VERBS, "'status' НЕ должен быть channel-verb'ом (T4)"
    logger.info("[IMP:9][deploy_channel_gate][negative] PASS: детектор ловит verb вне канала (status → RED)")


# endregion FUNC_test_workflow_verb_not_in_dictionary_negative


# region FUNC_test_canonical_verbs_no_legacy
@pytest.mark.gate
@ldd_trajectory
def test_canonical_verbs_no_legacy(caplog) -> None:
    """CANONICAL_VERBS не содержит legacy verb'ов (platform-deliver/platform-deploy) — 1:1 словарь.

    # ▶ CANONICAL_VERBS → ◇ legacy in? → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10: закрытое verb-множество (D1) — ровно 6 verbs.
    ## @io — caplog → ⎋ None
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · D1 закрытое множество
    # · Regression: legacy verb возвращается в словарь (легegacy-канал жив)
    # · Scenario: platform-deliver/platform-deploy ∉ CANONICAL_VERBS
    # · Last fail: legacy — platform-deliver был каноническим verb'ом
    # · Remove if: verb-множество расширяется архитектурно
    caplog.set_level(logging.INFO)

    assert CANONICAL_VERBS == ("ping", "exit", "status", "verify", "remove", "receive")
    assert "platform-deliver" not in CANONICAL_VERBS
    assert "platform-deploy" not in CANONICAL_VERBS
    assert "deploy" not in CANONICAL_VERBS, "legacy deploy-фолбэк не может быть verb'ом (D2)"

    logger.info("[IMP:9][deploy_channel_gate] PASS: CANONICAL_VERBS = %s (закрытое множество, D1)", CANONICAL_VERBS)


# endregion FUNC_test_canonical_verbs_no_legacy
