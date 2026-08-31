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
##            (ранее: 5 способов вызвать деплой с разными форматами). Гейт делает рассинхрон
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
from pathlib import Path

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

# DevPlan 125 T2: платформенные зависимости в deploy-project.yml → RED.
# (а) `uses:` — только стандартные actions (actions/*); relative actions (./.github/actions/*)
#     резолвятся в caller'е (проектная org), где платформы НЕТ — молча ломают канал (TRAP[BUG] 2026-08-03)
# (б) run-строки без `python3 -m core` / `make gate` / `make deploy` — платформенный код
#     недоступен в caller-контексте
_ALLOWED_USES_PREFIXES = ("actions/",)
_FORBIDDEN_RUN_PATTERNS = ("python3 -m core", "make gate", "make deploy")


# region HELPER__scan_platform_dependencies
def _scan_platform_dependencies(yaml_path: Path | None = None) -> list[str]:
    """Сканировать workflow на платформенные зависимости (DevPlan 125 T2).

    ## @purpose — Детектор повторного заноса платформенных зависимостей в deploy-project.yml:
    ##            relative actions (`uses: ./.github/actions/*`) и run-строки с
    ##            `python3 -m core` / `make gate` / `make deploy`.
    ## @io — ⇥ yaml_path: Path | None (None = канонический deploy-project.yml) → ⎋ list[str] (violations)
    ## @complexity — O(steps + run-lines)
    ## @invariants
    ##   - `uses:` allowlist: только префикс actions/ (стандартные GitHub actions)
    ##   - run-строки сканируются ВСЕ (не только ssh/tar — платформенные паттерны не verb'ы)
    ##   - Пустой результат = workflow чист; violation'ы — человекочитаемые строки
    """
    target = yaml_path or _DEPLOY_PROJECT_YML
    if not target.is_file():
        pytest.fail(f"Missing {target.relative_to(ROOT)} — единый канал обязателен (T4)")

    with Path(target).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    violations: list[str] = []
    jobs = (data or {}).get("jobs", {})
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses and not str(uses).startswith(_ALLOWED_USES_PREFIXES):
                violations.append(
                    f"{job_name}: step '{step.get('name', '?')}' uses '{uses}' — "
                    f"нестандартный/relative action (allowlist: {_ALLOWED_USES_PREFIXES})"
                )
            run = step.get("run")
            if not run or not isinstance(run, str):
                continue
            for line in run.splitlines():
                violations.extend(
                    f"{job_name}: step '{step.get('name', '?')}' run содержит '{pat}' "
                    f"(платформенная зависимость, недоступна в caller-контексте)"
                    for pat in _FORBIDDEN_RUN_PATTERNS
                    if pat in line
                )
    return violations


# endregion HELPER__scan_platform_dependencies


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

    with Path(_DEPLOY_PROJECT_YML).open(encoding="utf-8") as f:
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
    with Path(_MANIFEST_YML).open(encoding="utf-8") as f:
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

    # ▶ ┌CANONICAL_VERBS┐ → ◇ _VERB_HANDLERS (реестр verb→handler) + build_parser subcommands → ⊕ missing → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10 (1:1): verb-словарь verbs.py ↔ CLI-диспетчер.
    ##            Каждый verb (ping/exit/status/verify/remove/receive) должен быть в реестре
    ##            _VERB_HANDLERS (170 W4-B3 — декомпозиция _dispatch).
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

    # ВСЕ 8 verbs диспетчеризуются через реестр verb→handler (_VERB_HANDLERS, 170 W4-B3) —
    # интроспекция реестра надёжнее строкового анализа (dispatch — тонкий, маршрут = таблица)
    from core.internal.deploy.orchestrator_cli import _VERB_HANDLERS

    missing_verbs = [v for v in CANONICAL_VERBS if v not in _VERB_HANDLERS]
    assert not missing_verbs, f"CANONICAL_VERBS не диспетчеризуются в _VERB_HANDLERS: {missing_verbs}"

    # dispatch/deliver/receive/status/remove/rollback — обязательные subcommands (T2/T5; D8 rollback)
    for required in ("dispatch", "deliver", "receive", "status", "remove", "deploy-many", "rollback"):
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
    """deploy-project.yml run-шаги используют ТОЛЬКО verbs {ping, receive, verify}; 0 removed verb.

    # ▶ extract run-verbs → ◇ verbs ⊆ _WORKFLOW_ALLOWED_VERBS → ⊕ refs → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10 (1:1): workflow-канал ↔ verb-словарь.
    ##            deploy-project.yml: preflight ping, deliver receive, post-deploy verify.
    ## @io — caplog → ⎋ None
    ## @complexity — O(L) где L = run-строки
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · workflow↔verbs 1:1
    # · Regression: workflow использует verb вне канала (status/deploy/старый) — рассинхрон
    # · Scenario: извлечённые run-verbs ⊆ {ping, receive, verify}; 0 упоминаний platform-deploy/stage-deploy
    # · Last fail: — workflow гонял `status test` под || true + platform-deliver
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

    # 0 упоминаний workflow-каналов в run-шагах (T4)
    content = _DEPLOY_PROJECT_YML.read_text()
    for removed_verb in ("platform-deploy", "stage-deploy"):
        assert removed_verb not in content, f"Workflow deploy-project.yml не должен упоминать {removed_verb} (T4)"

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
    # · Last fail: — workflow гонял `status test` (не channel-verb) под || true
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


# region FUNC_test_canonical_verbs_closed_set
@pytest.mark.gate
@ldd_trajectory
def test_canonical_verbs_closed_set(caplog) -> None:
    """CANONICAL_VERBS не содержит verb'ов (platform-deliver/platform-deploy) — 1:1 словарь.

    # ▶ CANONICAL_VERBS → ◇ in? → ⎋ PASS|FAIL

    ## @purpose — DevPlan 116 B1 T10: закрытое verb-множество (D1) — ровно 8 verbs
    ##            (ping/exit/status/health/verify/remove/receive + rollback, D8 launch-validation).
    ## @io — caplog → ⎋ None
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B1 T10 · D1 закрытое множество
    # · Regression: verb возвращается в словарь (old-канал жив)
    # · Scenario: platform-deliver/platform-deploy ∉ CANONICAL_VERBS
    # · Last fail: — platform-deliver был каноническим verb'ом
    # · Remove if: verb-множество расширяется архитектурно
    caplog.set_level(logging.INFO)

    assert CANONICAL_VERBS == ("ping", "exit", "status", "health", "verify", "remove", "receive", "rollback")
    assert "platform-deliver" not in CANONICAL_VERBS
    assert "platform-deploy" not in CANONICAL_VERBS
    assert "deploy" not in CANONICAL_VERBS, "deploy-фолбэк не может быть verb'ом (D2)"

    logger.info("[IMP:9][deploy_channel_gate] PASS: CANONICAL_VERBS = %s (закрытое множество, D1)", CANONICAL_VERBS)


# endregion FUNC_test_canonical_verbs_closed_set


# ── DevPlan 125 T2: платформенные зависимости в deploy-project.yml ────────────


# region FUNC_test_workflow_no_platform_dependencies
@pytest.mark.gate
@ldd_trajectory
def test_workflow_no_platform_dependencies(caplog) -> None:
    """deploy-project.yml чист от платформенных зависимостей (relative actions / python3 -m core / make gate).

    # ▶ scan platform dependencies → ◇ violations? → ⎋ PASS|FAIL

    ## @purpose — DevPlan 125 T2: защита от повторного заноса платформенных зависимостей
    ##            (TRAP[BUG] 2026-08-03: relative actions + python3 -m core + make gate ломались
    ##            в caller-контексте, где платформы нет). Гейт делает занос структурно
    ##            невозможным — не только комментарием-инвариантом.
    ## @io — caplog → ⎋ None (pytest.fail со списком violation'ов)
    ## @complexity — O(steps + run-lines)
    """
    # 🧪 TRAP[TEST] · DevPlan 125 T2 · платформенные зависимости в CI-канале
    # · Regression: relative actions (uses: ./.github/actions/*) или python3 -m core /
    # ·   make gate / make deploy в run-шагах deploy-project.yml — сломает все caller-контексты молча
    # · Scenario: _scan_platform_dependencies() по текущему workflow → 0 violations
    # · Last fail: 2026-08-03 — deploy-project.yml защищался только комментариями (TRAP[BUG])
    # · Remove if: caller-контекст начинает поставлять платформу (архитектурно)
    caplog.set_level(logging.INFO)

    violations = _scan_platform_dependencies()

    logger.info("[IMP:8][deploy_channel_gate][platform-deps] Проверено uses+run: %d violation(ов)", len(violations))
    for v in violations:
        logger.warning("[IMP:10][deploy_channel_gate][platform-deps] %s", v)

    assert not violations, (
        f"[IMP:10][deploy_channel_gate] deploy-project.yml содержит платформенные зависимости: {violations} "
        "(reusable workflow исполняется в caller'е, где платформы нет — DevPlan 125 T2)"
    )
    logger.info("[IMP:9][deploy_channel_gate] PASS: workflow чист от платформенных зависимостей")


# endregion FUNC_test_workflow_no_platform_dependencies


# region FUNC_test_workflow_platform_dependencies_negative
@pytest.mark.gate
@ldd_trajectory
def test_workflow_platform_dependencies_negative(tmp_path, caplog) -> None:
    """Falsifiability: probe-workflow с платформенными зависимостями детектируется (R5).

    # ▶ tmp probe workflow → ◇ scan → ◇ violations ≥ 1 → ⎋ RED (детектор жив)

    ## @purpose — Anti-survivorship (R5): гейт, который не может упасть, — не гейт.
    ##            Probe-workflow с relative action + python3 -m core + make gate должен
    ##            детектироваться — иначе T2-гейт вечнозелёный.
    ## @io — tmp_path → ⎋ None (assert детекта)
    ## @complexity — O(1) — один фиктивный workflow
    """
    # 🧪 TRAP[TEST] · DevPlan 125 T2 · NEGATIVE (R5) — detector не сломан
    # · Regression: если детектор платформенных зависимостей перестанет ловить занос — гейт вечнозелёный
    # · Scenario: probe-workflow с `uses: ./.github/actions/setup-platform`, run с
    # ·   `python3 -m core.internal...` и `make gate` → ≥1 violation
    # · Last fail: 2026-08-03 — исходный занос relative actions (TRAP[BUG] caller-контекст)
    # · Remove if: гейт канала удалён
    caplog.set_level(logging.INFO)

    probe = tmp_path / "deploy-project.yml"
    probe.write_text(
        """\
jobs:
  deploy:
    steps:
      - name: Setup platform
        uses: ./.github/actions/setup-platform
      - name: Bad run
        run: |
          python3 -m core.internal.deploy.orchestrator_cli dispatch status x
          make gate MODE=fast
          make deploy PROJECT=x
      - name: Good run
        uses: actions/setup-python@v5
        run: pip install pyyaml
"""
    )

    violations = _scan_platform_dependencies(probe)

    logger.info("[IMP:8][deploy_channel_gate][negative] Violations из probe-workflow: %s", violations)
    assert violations, "CRITICAL: детектор не поймал платформенные зависимости в probe — гейт вечнозелёный (R5)"
    joined = "\n".join(violations)
    assert "./.github/actions/setup-platform" in joined, "relative action должен детектироваться"
    assert "python3 -m core" in joined, "python3 -m core должен детектироваться"
    assert "make gate" in joined, "make gate должен детектироваться"
    assert "make deploy" in joined, "make deploy должен детектироваться"
    logger.info("[IMP:9][deploy_channel_gate][negative] PASS: детектор ловит все 4 класса платформенных зависимостей")


# endregion FUNC_test_workflow_platform_dependencies_negative
