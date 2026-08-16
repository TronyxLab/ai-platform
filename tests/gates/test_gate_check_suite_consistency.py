# GREP_SUMMARY: gate check-suite-consistency anti-drift SoT-manifest golden-parity registration no-hardcoded-checks hole-coverage
# STRUCTURE: ▶ parse makefiles (ci.mk gate/repair.mk check*) → ◇ 0 hardcoded pytest/check-списков → ◇ validate_manifest (schema v1) → ◇ hole-coverage (check-manifests/ruff/gates-docker) → ◇ golden-паритет шагов gate (fast/full) → ◇ регистрация (allowed_verbs/check/check-diff/preflight-удалён) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Anti-drift consistency gate for the check-suite SoT manifest (DevPlan 120 §3.7,
##           по образцу parity-гейтов 116 T9). Пять проверок:
##           1. 0 hardcoded проверок вне манифеста (ci.mk gate / repair.mk check* — только
##              вызовы check_suite run; pytest-маркерные выражения и списки чеков — RED)
##           2. Каждый check манифеста валиден (schema v1: kebab-case id, tier, timeout,
##              gate_modes, cmd|cmds, junit-уникальность)
##           3. Покрытие дыр AC-2: check-manifests, ruff-check, gates-docker присутствуют
##           4. Паритет gate-шагов: `check_suite list --gate-mode fast|full` == golden-списки
##              (сняты с прежнего ci.mk ДО порта — Wave 1 фиксирует, этот тест сверяет)
##           5. Регистрация: make-таргеты чеков в allowed_verbs entrypoint-manifest.yaml;
##              check/check-diff зарегистрированы; preflight — удалён (DevPlan 138 W1)
## @scope    Read-only скан: makefiles/ci.mk, makefiles/repair.mk, core/check-suite.yaml,
##           core/entrypoint-manifest.yaml. Никаких subprocess-запусков проверок.
## @invariants
##   - Детект хардкода: целевое тело таргета (после «target:» до следующего таргета)
##   - allowlist пуст — любое pytest-выражение в теле gate/check-таргетов = RED
##   - Golden-списки = КОНСТАНТЫ теста (сняты с ci.mk до порта, DevPlan §3.7 п.4)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale DevPlan 120 AC-1: дрейф 3 hardcoded-списков (ci.mk/preflight.py/workflows)
##            устраняется конструктивно — оба executor'а читают один манифест; гейт
##            блокирует возврат хардкода. AC-2-регресс ловится п.3; регистрация (п.5)
##            связывает манифест с entrypoint-manifest.yaml (триада Makefile/AGENTS.md/манифест).
## @changes 2026-08-02 | Created (DevPlan 120 Wave 1, golden-списки сняты с ci.mk:136-239)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

from core.internal.check_suite import list_checks, validate_manifest
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

_MANIFEST_PATH = ROOT / "core" / "check-suite.yaml"
_ENTRYPOINT_MANIFEST_PATH = ROOT / "core" / "entrypoint-manifest.yaml"
_CI_MK_PATH = ROOT / "makefiles" / "ci.mk"
_REPAIR_MK_PATH = ROOT / "makefiles" / "repair.mk"

# ── Golden-списки шагов gate (сняты с ci.mk ДО порта, DevPlan §3.7 п.4) ──────────
# fast (прежний ci.mk:138-168 + DevPlan 163 статический стек): pre-commit → validate →
#   check-dead-code → static-ast → arch-imports → vulture → deptry →
#   check-exception-patterns → doxygen-check → gates → gates-docker → contract →
#   static_audit → predeploy
_GOLDEN_FAST: tuple[str, ...] = (
    "pre-commit",
    "validate",
    "check-dead-code",
    "static-ast",
    "arch-imports",
    "vulture",
    "deptry",
    "check-exception-patterns",
    "doxygen-check",
    "pyright",
    "gates",
    "gates-docker",
    "contract",
    "ai-instructions",  # DevPlan 001 T4.6: сьют конвенционного компилятора инструкций
    "static_audit",
    "predeploy",
)

# full (прежний ci.mk:169-213 + DevPlan 163 статический стек): pre-commit → validate →
#   check-dead-code → static-ast → arch-imports → vulture → deptry → lint →
#   doxygen-check → check-file-lines → gates → contract → ai-instructions →
#   static_audit → predeploy → smoke → component
_GOLDEN_FULL: tuple[str, ...] = (
    "pre-commit",
    "validate",
    "check-dead-code",
    "static-ast",
    "arch-imports",
    "vulture",
    "deptry",
    "lint",
    "doxygen-check",
    "check-file-lines",
    "pyright",
    "gates",
    "contract",
    "ai-instructions",  # DevPlan 001 T4.6
    "static_audit",
    "predeploy",
    "smoke",
    "component",
)

# Дыры AC-2 (DevPlan §3.7 п.3): обязательное присутствие в диагностическом наборе
_HOLE_COVERAGE_IDS: tuple[str, ...] = ("check-manifests", "ruff-check", "gates-docker")


# region HELPER_extract_target_body
## @purpose  Извлечь тело make-таргета: строки от «target:» (колонка 0) до границы
##           следующего блока — таргет (^name:) ИЛИ колонка-0 комментарий (#/## — доки
##           следующего таргета или TRAP-аннотации). Комментарии ## (доки) до таргета
##           не включаются.
## @io       ⇥ content: str, target: str → str (тело таргета, пусто если не найден)
## @complexity O(L) где L = строки файла
def _extract_target_body(content: str, target: str) -> str:
    """Extract a make target body (lines after `target:` until next block boundary)."""
    lines = content.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if re.fullmatch(rf"\s*{re.escape(target)}:.*", line):
            start = i
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start + 1 :]:
        # Граница: следующий таргет (^name:) или колонка-0 комментарий (доки/TRAP следующего блока)
        if re.match(r"^[a-zA-Z0-9_\-]+:", line) or line.startswith("#"):
            break
        body.append(line)
    return "\n".join(body)


# endregion HELPER_extract_target_body


# region HELPER_extract_target_def
## @purpose  Извлечь строку определения таргета (например «check:» с make-зависимостями).
## @io       ⇥ content: str, target: str → str (строка определения, пусто если не найден)
## @complexity O(L)
def _extract_target_def(content: str, target: str) -> str:
    """Extract the make target definition line (may carry dependencies, e.g. `up: discover-modules`)."""
    for line in content.splitlines():
        if re.fullmatch(rf"\s*{re.escape(target)}:.*", line):
            return line.strip()
    return ""


# endregion HELPER_extract_target_def


# ═══════════════════════════════════════════════════════════════
# П.1: 0 hardcoded проверок вне манифеста (AC-1)
# ═══════════════════════════════════════════════════════════════


# region FUNC_test_no_hardcoded_checks_in_makefiles
@pytest.mark.gate
@ldd_trajectory
def test_no_hardcoded_checks_in_makefiles(caplog) -> None:
    """AC-1: gate/check/check-diff таргеты вызывают ТОЛЬКО check_suite.

    # ▶ извлечь тела таргетов → ◇ pytest/-m-выражения в телах? → RED · └→ PASS

    ## @purpose — DevPlan 120 §3.7 п.1 (AC-1): pytest-маркерные выражения и списки чеков
    ##            НЕ захардкожены в makefiles/ci.mk (gate) и makefiles/repair.mk
    ##            (check/check-diff) — разрешены только вызовы check_suite run.
    ##            preflight-портал удалён (DevPlan 138 W1 — таргет удалён, literal-бан).
    ## @io — caplog → ⎋ None (pytest.fail со списком нарушений)
    ## @complexity O(L) где L = строки make-файлов
    """
    # 🧪 TRAP[TEST] · DevPlan 120 §3.7 п.1 · AC-1 анти-дрейф: возврат хардкода в makefiles
    # · Regression: добавление pytest -m "..." или списка чеков прямо в gate/check-таргеты
    # · Scenario: скан тел таргетов {gate, check, check-diff}
    # · Last fail: N/A (новый гейт — прежний ci.mk:138-239 содержал 8+ hardcoded-выражений)
    # · Remove if: makefiles перестанут быть точкой входа проверок (полный Python-диспетчер)
    caplog.set_level(logging.INFO)

    ci_content = _CI_MK_PATH.read_text(encoding="utf-8")
    repair_content = _REPAIR_MK_PATH.read_text(encoding="utf-8")

    # Таргеты-порталы: тело должно содержать check_suite и НЕ содержать pytest/-m "…"
    # preflight-портал удалён в DevPlan 138 W1 (таргет удалён; возврат = незарегистрированный таргет → namelint).
    portals: dict[str, tuple[str, str]] = {
        "gate (ci.mk)": (
            _extract_target_def(ci_content, "gate"),
            _extract_target_body(ci_content, "gate"),
        ),
        "check (repair.mk)": (
            _extract_target_def(repair_content, "check"),
            _extract_target_body(repair_content, "check"),
        ),
        "check-diff (repair.mk)": (
            _extract_target_def(repair_content, "check-diff"),
            _extract_target_body(repair_content, "check-diff"),
        ),
    }

    violations: list[str] = []
    for name, (_def_line, body) in portals.items():
        has_portal = "check_suite" in body
        if not has_portal:
            violations.append(f"{name}: тело таргета не содержит вызов check_suite")
        # pytest-инвокация или маркерное выражение в теле = хардкод проверки
        if r"pytest" in body or re.search(r'-m\s+"', body):
            violations.append(f"{name}: hardcoded pytest/-m-выражение в теле таргета")

    logger.info(
        "[IMP:8][consistency][no-hardcode] Проверено %d таргета-портала, %d нарушений",
        len(portals),
        len(violations),
    )

    if violations:
        for v in violations:
            logger.error("[IMP:10][consistency][no-hardcode] %s", v)
        pytest.fail(
            "[IMP:10][consistency][no-hardcode] Hardcoded проверки вне манифеста (AC-1 RED):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    logger.critical("[IMP:9][consistency][no-hardcode] PASS: 0 hardcoded проверок в makefiles — только check_suite run")


# endregion FUNC_test_no_hardcoded_checks_in_makefiles


# ═══════════════════════════════════════════════════════════════
# П.2: валидность каждого check манифеста (schema v1)
# ═══════════════════════════════════════════════════════════════


# region FUNC_test_manifest_checks_valid
@pytest.mark.gate
@ldd_trajectory
def test_manifest_checks_valid(caplog) -> None:
    """П.2: каждый check манифеста проходит schema v1 (validate_manifest).

    # ▶ load core/check-suite.yaml → ◇ validate_manifest → ◇ ошибки? → RED · └→ PASS

    ## @purpose — DevPlan 120 §3.7 п.2: id kebab-case/уникален, tier ∈ {fix,static,pytest},
    ##            timeout > 0, gate_modes ⊆ {fast,full,ci-docker}, cmd ИЛИ cmds для каждого
    ##            gate-режима, junit-пути уникальны. Используется ТА ЖЕ функция валидации,
    ##            что и executor (fail-fast до запуска).
    ## @io — caplog → ⎋ None (pytest.fail со списком schema-ошибок)
    ## @complexity O(C) где C = чеков
    """
    # 🧪 TRAP[TEST] · DevPlan 120 §3.7 п.2 · schema-дрейф манифеста
    # · Regression: невалидная запись (битый tier, дубль junit, id не kebab-case) в манифесте
    # · Scenario: validate_manifest(load_manifest()) == []
    # · Last fail: N/A (новый гейт)
    # · Remove if: validate_manifest удалена или схема v2 заменила проверки
    caplog.set_level(logging.INFO)

    with Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    errors = validate_manifest(manifest)
    logger.info(
        "[IMP:8][consistency][schema] manifest version=%s, checks=%d, errors=%d",
        manifest.get("version"),
        len(manifest.get("checks", [])),
        len(errors),
    )

    assert not errors, "[IMP:10][consistency][schema] Manifest schema v1 violation(s):\n" + "\n".join(
        f"  - {e}" for e in errors
    )
    assert manifest.get("version") == 1, (
        f"version должен быть 1 (bump инвалидирует fingerprint-кэш), got {manifest.get('version')}"
    )
    logger.critical(
        "[IMP:9][consistency][schema] PASS: все %d чеков валидны (schema v1)", len(manifest.get("checks", []))
    )


# endregion FUNC_test_manifest_checks_valid


# ═══════════════════════════════════════════════════════════════
# П.3: покрытие дыр AC-2
# ═══════════════════════════════════════════════════════════════


# region FUNC_test_hole_coverage_present
@pytest.mark.gate
@ldd_trajectory
def test_hole_coverage_present(caplog) -> None:
    """П.3 (AC-2-регресс): check-manifests, ruff-check, gates-docker в манифесте.

    # ▶ ids манифеста → ◇ HOLE_COVERAGE_IDS ⊆ ids? → RED · └→ PASS

    ## @purpose — DevPlan 120 §3.7 п.3: дыры покрытия закрыты — check-manifests
    ##            (G1-G6 byte-сверка) и ruff check . входили только в pre-commit,
    ##            gates-docker (с allow_no_tests) был вне диагностики. Регресс = RED.
    ## @io — caplog → ⎋ None (pytest.fail с отсутствующими id)
    ## @complexity O(C)
    """
    # 🧪 TRAP[TEST] · DevPlan 120 §3.7 п.3 · AC-2 регресс покрытия дыр
    # · Regression: удаление check-manifests/ruff-check/gates-docker из манифеста
    # · Scenario: _HOLE_COVERAGE_IDS ⊆ set(manifest check ids)
    # · Last fail: 2026-08-02 — дыры: check-manifests/ruff вне preflight, gates-docker пуст (rc=5)
    # · Remove if: манифест заменён новой системой проверок
    caplog.set_level(logging.INFO)

    with Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    ids = {c.get("id") for c in manifest.get("checks", []) if isinstance(c, dict)}

    missing = [hid for hid in _HOLE_COVERAGE_IDS if hid not in ids]
    logger.info("[IMP:8][consistency][holes] id set size=%d, missing=%s", len(ids), missing)

    assert not missing, "[IMP:10][consistency][holes] Дыры AC-2 открылись (id отсутствуют в манифесте): " + ", ".join(
        missing
    )
    logger.critical("[IMP:9][consistency][holes] PASS: %s присутствуют в манифесте", ", ".join(_HOLE_COVERAGE_IDS))


# endregion FUNC_test_hole_coverage_present


# ═══════════════════════════════════════════════════════════════
# П.4: паритет gate-шагов (golden)
# ═══════════════════════════════════════════════════════════════


# region FUNC_test_gate_step_parity_golden
@pytest.mark.gate
@ldd_trajectory
def test_gate_step_parity_golden(caplog) -> None:
    """П.4: состав и порядок шагов gate (fast/full) == golden-списки прежнего ci.mk.

    # ▶ list_checks(fast|full) → ◇ ids == _GOLDEN_FAST/_GOLDEN_FULL? → RED · └→ PASS

    ## @purpose — DevPlan 120 §3.7 п.4: порт ci.mk → executor не изменил семантику gate
    ##            (порядок шагов и состав из манифеста == прежний ci.mk). Golden-списки
    ##            зафиксированы ДО порта (Wave 1), тест сверяет каждый прогон (Wave 2+).
    ## @io — caplog → ⎋ None (pytest.fail с diff-списком)
    ## @complexity O(C)
    """
    # 🧪 TRAP[TEST] · DevPlan 120 §3.7 п.4 · golden-паритет gate-шагов
    # · Regression: изменение состава/порядка шагов gate относительно прежнего ci.mk
    # · Scenario: list_checks(gate_mode=fast|full) ids сравниваются с _GOLDEN_FAST/_GOLDEN_FULL
    # · Last fail: N/A (новый гейт; прежний порядок снят с ci.mk:136-239)
    # · Remove if: gate-семантика осознанно изменена (обновить golden-константы + DevPlan)
    caplog.set_level(logging.INFO)

    with Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    fast_ids = [s.id for s in list_checks(manifest, gate_mode="fast")]
    full_ids = [s.id for s in list_checks(manifest, gate_mode="full")]

    failures: list[str] = []
    if tuple(fast_ids) != _GOLDEN_FAST:
        failures.append(f"fast: {fast_ids} != golden {list(_GOLDEN_FAST)}")
    if tuple(full_ids) != _GOLDEN_FULL:
        failures.append(f"full: {full_ids} != golden {list(_GOLDEN_FULL)}")

    logger.info("[IMP:8][consistency][parity] fast=%d шагов, full=%d шагов", len(fast_ids), len(full_ids))

    if failures:
        for f_ in failures:
            logger.error("[IMP:10][consistency][parity] %s", f_)
        pytest.fail(
            "[IMP:10][consistency][parity] Gate-паритет нарушен (AC-4):\n" + "\n".join(f"  - {f_}" for f_ in failures)
        )

    logger.critical("[IMP:9][consistency][parity] PASS: fast/full шаги совпадают с golden ci.mk (паритет AC-4)")


# endregion FUNC_test_gate_step_parity_golden


# ═══════════════════════════════════════════════════════════════
# П.5: регистрация в entrypoint-manifest.yaml
# ═══════════════════════════════════════════════════════════════


# region FUNC_test_registration_in_entrypoint_manifest
@pytest.mark.gate
@ldd_trajectory
def test_registration_in_entrypoint_manifest(caplog) -> None:
    """П.5: make-таргеты чеков в allowed_verbs; check/check-diff зарегистрированы; preflight удалён.

    # ▶ repair: preflight ОТСУТСТВУЕТ + check/check-diff записи → ◇ allowed_verbs покрытие
    #   (cmd «make X» → X ∈ allowed_verbs ∪ system_exceptions) → RED · └→ PASS

    ## @purpose — DevPlan 120 §3.7 п.5: каждый check, чья команда вызывает make-таргет,
    ##            регистрируется в allowed_verbs entrypoint-manifest.yaml (триада
    ##            Makefile/AGENTS.md/манифест); check/check-diff — канонические глаголы;
    ##            preflight — таргет УДАЛЁН (DevPlan 138 W1), запись и глагол отсутствуют.
    ## @io — caplog → ⎋ None (pytest.fail со списком нарушений регистрации)
    ## @complexity O(C * V) где C = чеков, V = allowed_verbs
    """
    # 🧪 TRAP[TEST] · DevPlan 120 §3.7 п.5 · регистрационный дрейф
    # · Regression: новый make-таргет в манифесте без allowed_verbs; возврат preflight
    # · Scenario: скан cmd «make X» чеков против allowed_verbs + system_exceptions
    # · Last fail: N/A (новый гейт)
    # · Remove if: entrypoint-manifest.yaml перестанет быть реестром глаголов
    caplog.set_level(logging.INFO)

    with Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    with Path(_ENTRYPOINT_MANIFEST_PATH).open(encoding="utf-8") as f:
        entrypoint = yaml.safe_load(f)

    allowed_verbs = set(entrypoint.get("allowed_verbs", []))
    name_linter = entrypoint.get("name_linter", {}) or {}
    system_prefixes = tuple(name_linter.get("system_prefixes", []))
    # Категорийное правило (DevPlan 171 W3.6): стандартные служебные таргеты + _-префикс.
    service_targets = {"help", "venv"}

    violations: list[str] = []
    for c in manifest.get("checks", []):
        if not isinstance(c, dict):
            continue
        cmd = c.get("cmd")
        cmds = c.get("cmds") or {}
        for raw in [cmd, *cmds.values()]:
            if not isinstance(raw, str) or not raw.startswith("make "):
                continue
            target = raw.split()[1]
            if (
                target not in allowed_verbs
                and target not in service_targets
                and not target.startswith(system_prefixes)
                and not target.startswith("_")
            ):
                violations.append(f"check {c.get('id')!r}: make-таргет {target!r} не в allowed_verbs/категориях")

    if "check" not in allowed_verbs:
        violations.append("глагол 'check' не зарегистрирован в allowed_verbs")
    if "check-diff" not in allowed_verbs:
        violations.append("глагол 'check-diff' не зарегистрирован в allowed_verbs")

    # DevPlan 138 W1: preflight-таргет удалён — запись в repair-секции и глагол запрещены
    repair_section = entrypoint.get("repair", []) or []
    preflight_entries = [e for e in repair_section if isinstance(e, dict) and e.get("make_target") == "preflight"]
    if preflight_entries:
        violations.append("preflight: запись в repair-секции manifest существует — таргет удалён (DevPlan 138 W1)")
    if "preflight" in allowed_verbs:
        violations.append("preflight: глагол в allowed_verbs — таргет удалён (DevPlan 138 W1)")

    logger.info(
        "[IMP:8][consistency][registration] allowed_verbs=%d, violations=%d", len(allowed_verbs), len(violations)
    )

    if violations:
        for v in violations:
            logger.error("[IMP:10][consistency][registration] %s", v)
        pytest.fail(
            "[IMP:10][consistency][registration] Регистрация нарушена (AC-1/AC-5):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    logger.critical(
        "[IMP:9][consistency][registration] PASS: make-таргеты чеков зарегистрированы; "
        "check/check-diff в allowed_verbs; preflight удалён (DevPlan 138 W1)"
    )


# endregion FUNC_test_registration_in_entrypoint_manifest
