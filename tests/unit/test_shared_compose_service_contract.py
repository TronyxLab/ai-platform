# GREP_SUMMARY: test shared compose-service-contract analyzer service-network-coverage env-var-unresolved db-consumed-not-declared _extract_refs interpolation $$-escape load_env_keys load_provides frozen F-1 F-2
# STRUCTURE: ▶ _extract_refs (все формы + $$-escape + TRAP[BUG]-regex) → ◇ analyze (coverage dict-форм / incident R5 / unresolved / db-needs / $$-no-FP / resilience) → ◇ load_env_keys (missing/комментарии/без =) → ◇ load_provides (fail-fast / not-dict / SoT) → ⊕ frozen-семантика dataclass → ⎋ IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Прямые unit-тесты ЕДИНСТВЕННОГО статического анализатора service-контрактов
##           core/internal/shared/compose_service_contract.py (Plan 019 TASK-4, QA F-1): закрытие
##           formal-нарушения shared/AGENTS.md п.3(б) — «новый модуль в shared/ требует unit-тесты
##           в tests/unit/» — до этого покрытие было ТОЛЬКО косвенным (K1-хендлер + K3-гейты) и не
##           ловило регрессию edge-cases парсера интерполяции ($$-escape, bare $VAR, :?/?-формы,
##           dict-form networks/args). Тест-файл — 1:1 по $TEST_SPEC контрактам DevPlan 019 TASK-4
##           (правила (a) service-network-coverage, (b) env-var-unresolved, (c) db-consumed-not-declared).
## @scope    tests/unit (без Docker). Нативные импорты, tmp_path строго (Zero Hardcode), caplog,
##           ldd_trajectory (tests.conftest), pytestmark = static_audit (конвенция test_shared_*).
##           Покрывает ТОЛЬКО shared-модуль: verify_contracts (K3) и check_project (K1) — вне скоупа
##           (их собственные регресс-наборы 50/50 и 24/24); единственная граница-исключение —
##           test_db_flag_upstream_false_string_normalization проверяет upstream-контракт
##           needs.database="false"-строки (verify_contracts._needs_database_declared), т.к. это
##           часть семантики правила (c) «у анализатора флаг, нормализация у потребителя».
## @invariants
##   - Каждый тест использует tmp_path ИЛИ чистые строки (R1, Zero Hardcode)
##   - Каждый тест эмитит IMP:9 (свой маркер или IMP:9-лог анализатора) — Anti-Illusion (T6)
##   - R5-негативы на точном инцидентном инпуте пилотов asi-group (план 019 F1-F3/F5)
##   - TRAP[BUG] 019 P1 (regex group-2 IndexError) закреплён прямым тестом на инпуте ${VAR:-http://...}
##   - Resilience: compose/services/сервис не dict → () БЕЗ exception (контракт анализатора #4)
##   - frozen-семантика ServiceContractViolation/ServiceContractInput — через setattr()
##     (object.__setattr__ ОБХОДИТ dataclass-frozen-guard — setattr триггерит __setattr__ → raise)
## @rationale QA F-1 (03-VerificationReport): косвенное покрытие (K1/K3) не ловит регрессию
##            edge-cases парсера интерполяции — гейт может молча ослепнуть на части входов.
##            Прямой unit-слой на каждый публичный символ (analyze/_extract_refs/load_env_keys/
##            load_provides) + frozen-контракты входных dataclass — детерминированный рубеж
##            до K1/K3-интеграции. pytestmark static_audit — детерминированный слой (карантин
##            запрещён, tests/AGENTS.md инвариант 11).
## @changes 2026-08-31 · QA F-1 (Plan 019) — создан (прямое покрытие shared-анализатора)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from core.internal.deploy.verify_contracts import _needs_database_declared
from core.internal.shared.compose_service_contract import (
    RULE_DB_CONSUMED_NOT_DECLARED,
    RULE_ENV_VAR_UNRESOLVED,
    RULE_SERVICE_NETWORK_COVERAGE,
    ServiceContractInput,
    ServiceContractViolation,
    _extract_refs,
    analyze_service_contracts,
    load_env_keys,
    load_provides,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── SoT-формат provides (platform-infra.yaml#provides, DR-M4) — зеркало секции 128-172 ──
_DEFAULT_PROVIDES: dict[str, object] = {
    "postgres": {"networks": ["shared-db-net"]},
    "litellm": {"networks": ["hermes-agent-net", "shared-db-net"]},
}


# region HELPER_analyze
def _analyze(
    compose_yaml: str,
    *,
    env_keys: frozenset[str] = frozenset(),
    secret_names: frozenset[str] = frozenset(),
    needs_database: bool = True,
    provides: dict[str, object] | None = None,
) -> tuple[ServiceContractViolation, ...]:
    """Прогнать analyze_service_contracts на YAML-compose (fixture-хелпер, Zero Hardcode).

    ## @purpose  Единая точка построения ServiceContractInput из YAML-строки: compose приходит
    ##           как parsed YAML (Any → dict[str, object] — тот же путь, что K3/K1-потребители:
    ##           yaml.safe_load результата compose-файла). Дефолтные provides — SoT-формат.
    ## @io       ⇥ compose_yaml + опции входа → ⎋ tuple[ServiceContractViolation, ...]
    ## @complexity O(1) — делегирование в analyze_service_contracts
    """
    inp = ServiceContractInput(
        compose=yaml.safe_load(compose_yaml),
        env_keys=env_keys,
        secret_names=secret_names,
        needs_database=needs_database,
        provides=provides if provides is not None else _DEFAULT_PROVIDES,
    )
    return analyze_service_contracts(inp)


# endregion HELPER_analyze


# region TEST_extract_refs
# 🧪 TRAP[TEST] · Regression · _extract_refs — все формы интерполяции compose (план 019 TASK-4)
# · Scenario: ${VAR} без дефолта → (name, False); ${VAR:-def}/${VAR-def} → has_default=True;
# ·   ${VAR:?err}/${VAR?err} — error-формы ТРЕБУЮТ резолва → has_default=False;
# ·   bare $VAR → (name, False); ${VAR:-} (пустой дефолт) — тоже дефолт
# · Last fail: N/A — edge-cases не имели прямого покрытия (QA F-1) · Remove if: _extract_refs удалён
@ldd_trajectory
def test_extract_refs_all_forms(caplog) -> None:
    """Все формы ${...}/$VAR: семантика has_default (дефолт только у :- и -)."""
    logger.info("[IMP:9][test][extract_refs] all interpolation forms")
    assert _extract_refs("${FOO}") == [("FOO", False)]
    assert _extract_refs("${FOO:-bar}") == [("FOO", True)]
    assert _extract_refs("${FOO-def}") == [("FOO", True)]
    assert _extract_refs("${FOO:?err}") == [("FOO", False)]
    assert _extract_refs("${FOO?err}") == [("FOO", False)]
    assert _extract_refs("$FOO") == [("FOO", False)]
    assert _extract_refs("${FOO:-}") == [("FOO", True)]
    assert _extract_refs("a=${A} b=${B:-x} c=$C d=${D?e} e=${E-f}") == [
        # Порядок контракта: brace-матчи (в порядке матча) → bare-скан по остатку (C — последний)
        ("A", False),
        ("B", True),
        ("D", False),
        ("E", True),
        ("C", False),
    ]


# 🧪 TRAP[TEST] · Regression · $$-escape + $VAR внутри дефолта (план 019 TASK-4 инвариант 2)
# · Scenario: $${VAR}/$$VAR/$$$$VAR — литеральный $ (compose-escape), НЕ интерполируются → [];
# ·   ${X:-$FOO} — $FOO внутри дефолта НЕ ловится как самостоятельная ссылка (brace-матч
# ·   удаляется целиком до bare-скана); $5 — цифры после $ не имя → не ссылка
# · Last fail: N/A — edge-cases не имели прямого покрытия (QA F-1) · Remove if: _extract_refs удалён
@ldd_trajectory
def test_extract_refs_escape_and_default_interior(caplog) -> None:
    """$$-escape не интерполируется; $VAR внутри ${..:-$VAR}-дефолта не самостоятельная ссылка."""
    logger.info("[IMP:9][test][extract_refs] $$-escape + default-interior bare")
    assert _extract_refs("$${FOO}") == []
    assert _extract_refs("$$FOO") == []
    assert _extract_refs("$$$$FOO") == []
    assert _extract_refs("${X:-$FOO}") == [("X", True)]
    assert _extract_refs("prefix ${B:-$C} $A") == [("B", True), ("A", False)]
    assert _extract_refs("cost $5 and ${A}") == [("A", False)]


# 🧪 TRAP[TEST] · Regression · _BRACE_RE группа оператора (TRAP[BUG] 019 P1)
# · Scenario: ${PLATFORM_LITELLM_URL:-http://litellm:4000} — default-форма с '://' в дефолте;
# ·   спека DevPlan имела НЕ-захватывающую группу `(?::?[-?][^}]*)?` → m.group(2) кидал
# ·   IndexError на КАЖДОМ вызове; фикс — захватывающая `((:?[-?])[^}]*)?`
# · Last fail: 2026-08-31 — первый прогон K3-гейта 4/4 failed (IndexError: no such group 2)
# · Remove if: _BRACE_RE переписан на иной механизм извлечения ссылок
@ldd_trajectory
def test_extract_refs_brace_default_url_regression(caplog) -> None:
    """TRAP[BUG] 019: ${VAR:-http://...} — default-форма с URL не кидает IndexError (regex group 2)."""
    logger.info("[IMP:9][test][extract_refs] TRAP[BUG] 019 — brace-default with ://")
    assert _extract_refs("${PLATFORM_LITELLM_URL:-http://litellm:4000}") == [("PLATFORM_LITELLM_URL", True)]
    assert _extract_refs("${PLATFORM_POSTGRES_DSN:?must-be-set}") == [("PLATFORM_POSTGRES_DSN", False)]


# endregion TEST_extract_refs


# region TEST_analyze_service_contracts
# 🧪 TRAP[TEST] · CONTROL · канонический compose — dict-form env + dict-form networks (aliases)
# · Scenario: service-network-coverage на dict-форме environment (K=V) + dict-форме networks
# ·   (ключи = имена, long-form aliases): PLATFORM_POSTGRES_DSN/PLATFORM_LITELLM_URL ∈ env_keys,
# ·   сети ∩ provides ≠ ∅ → 0 violations; int-значение env (PORT: 8080) str()-ится — не ссылка
# · Last fail: N/A (позитив — анти-survivorship) · Remove if: контракт coverage меняется
@ldd_trajectory
def test_analyze_coverage_passes_dict_forms(caplog) -> None:
    """dict-form env + dict-form networks (aliases) + int-значение → 0 violations."""
    logger.info("[IMP:9][test][analyze] canonical dict-form compose passes coverage")
    compose_yaml = """
services:
  bot:
    environment:
      DATABASE_URL: ${PLATFORM_POSTGRES_DSN}
      LLM_BASE_URL: ${PLATFORM_LITELLM_URL}
      PORT: 8080
    networks:
      shared-db-net:
        aliases: [bot]
      hermes-agent-net: {}
"""
    violations = _analyze(
        compose_yaml,
        env_keys=frozenset({"PLATFORM_POSTGRES_DSN", "PLATFORM_LITELLM_URL"}),
        needs_database=True,
    )
    assert violations == ()


# 🧪 TRAP[TEST] · NEGATIVE (R5) · service-network-coverage — потребление без сети провайдера
# · Scenario: DATABASE_URL=${PLATFORM_POSTGRES_DSN} (env-resolved) + networks [proxy-net] →
# ·   networks(svc) ∩ provides.networks(postgres) = ∅ → ровно coverage violation
# ·   (не unresolved — DSN в env_keys; не db — needs_database=True)
# · Last fail: 2026-08-31 — production-compose пилотов: proxy-net only, pgbouncer недостижим (F1-F3)
# · Remove if: service-network-coverage контракт меняется
@ldd_trajectory
def test_analyze_coverage_violation_missing_provider_network(caplog) -> None:
    """PG-потребление на proxy-net (без shared-db-net) → ровно service-network-coverage."""
    logger.info("[IMP:9][test][analyze] coverage violation on missing provider network")
    compose_yaml = """
services:
  bot:
    environment:
      DATABASE_URL: ${PLATFORM_POSTGRES_DSN}
    networks:
      proxy-net: {}
"""
    violations = _analyze(compose_yaml, env_keys=frozenset({"PLATFORM_POSTGRES_DSN"}))
    assert len(violations) == 1
    assert violations[0].rule == RULE_SERVICE_NETWORK_COVERAGE
    assert violations[0].service == "bot"
    assert "shared-db-net" in violations[0].message, "message обязан указывать недостающую сеть провайдера"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · инцидент пилотов asi-group — оба правила (план 019 F1-F3/F5)
# · Scenario: точный инцидентный compose client-bot ДО фикса — networks [client-bot-net, proxy-net];
# ·   DATABASE_URL=${DATABASE_URL} (нет в .env.platform → env-var-unresolved);
# ·   LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000} (дефолт → env-ok, но
# ·   networks ∩ provides.networks(litellm)=∅ → service-network-coverage)
# · Last fail: 2026-08-31 — K3 слеп к классу (проверял только «external-сеть вне allowlist», F5)
# · Remove if: контракты coverage/env-unresolved меняются (пересмотр K3/K1)
@ldd_trajectory
def test_analyze_incident_compose_both_rules(caplog) -> None:
    """Инцидентный compose пилотов (list-form env, только proxy-net) → coverage + unresolved."""
    logger.info("[IMP:9][test][analyze] incident compose hits both rules (R5)")
    compose_yaml = """
services:
  client-bot:
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000}
    networks:
      - client-bot-net
      - proxy-net
"""
    violations = _analyze(compose_yaml, env_keys=frozenset())
    rules = {v.rule for v in violations}
    assert RULE_ENV_VAR_UNRESOLVED in rules, "инцидентный ${DATABASE_URL} обязан быть unresolved"
    assert RULE_SERVICE_NETWORK_COVERAGE in rules, "litellm на proxy-net обязан быть coverage violation"


# 🧪 TRAP[TEST] · Regression · build.args list/dict-формы сканируются (TASK-4 правило (b))
# · Scenario: dict-form args + shared-db-net + DSN в env → 0 violations; list-form args с
# ·   GIT_SHA=${GIT_SHA} (без дефолта, вне env) → env-var-unresolved
# · Last fail: N/A — build.args не имел прямого покрытия (QA F-1) · Remove if: _iter_scan_strings удалён
@ldd_trajectory
def test_analyze_build_args_list_and_dict(caplog) -> None:
    """build.args dict-form (корректная сеть → pass) и list-form (нерезолвимая ссылка → violation)."""
    logger.info("[IMP:9][test][analyze] build.args list/dict scan")
    dict_ok = """
services:
  bot:
    build:
      args:
        PLATFORM_POSTGRES_DSN: ${PLATFORM_POSTGRES_DSN}
    networks:
      - shared-db-net
"""
    assert _analyze(dict_ok, env_keys=frozenset({"PLATFORM_POSTGRES_DSN"})) == ()

    list_bad = """
services:
  bot:
    build:
      args:
        - GIT_SHA=${GIT_SHA}
    networks:
      - proxy-net
"""
    violations = _analyze(list_bad, env_keys=frozenset())
    assert [v.rule for v in violations] == [RULE_ENV_VAR_UNRESOLVED]
    assert violations[0].service == "bot"
    assert "GIT_SHA" in violations[0].message


# 🧪 TRAP[TEST] · Regression · env-var-unresolved — семантика дефолта/error-форм (TASK-4 правило (b))
# · Scenario: ${RESOLVED_VAR} ∈ env_keys → ok; ${DEFAULTED_VAR:-fallback} — дефолт → skip;
# ·   ${MISSING_VAR:?err} и $MISSING_BARE — без дефолта вне источников → unresolved
# · Last fail: N/A — error-формы :?/? и bare $VAR не имели прямого покрытия (QA F-1)
# · Remove if: env-var-unresolved контракт меняется
@ldd_trajectory
def test_analyze_env_unresolved_default_and_resolvable(caplog) -> None:
    """Резолвится/дефолт-пропуск/error-форма/bare — точная семантика env-var-unresolved."""
    logger.info("[IMP:9][test][analyze] env-unresolved semantics (default vs error-form vs bare)")
    compose_yaml = """
services:
  app:
    environment:
      - RESOLVED=${RESOLVED_VAR}
      - DEFAULTS=${DEFAULTED_VAR:-fallback}
      - ERROR_FORM=${MISSING_VAR:?must-be-set}
      - BARE=$MISSING_BARE
"""
    violations = _analyze(compose_yaml, env_keys=frozenset({"RESOLVED_VAR", "DEFAULTED_VAR"}))
    assert all(v.rule == RULE_ENV_VAR_UNRESOLVED for v in violations)
    msgs = " ".join(v.message for v in violations)
    assert len(violations) == 2, f"ровно 2 unresolved (error-form + bare; RESOLVED/DEFAULTS не флагаются): {msgs}"
    assert "MISSING_VAR" in msgs and "MISSING_BARE" in msgs
    assert "RESOLVED_VAR" not in msgs and "DEFAULTED_VAR" not in msgs


# 🧪 TRAP[TEST] · NEGATIVE (R5) · db-consumed-not-declared — класс ***-DSN roadmap (план 019 F6/F8)
# · Scenario: DATABASE_URL=${PLATFORM_POSTGRES_DSN} + shared-db-net (coverage OK) + DSN в env
# ·   (env-resolved), НО needs_database=False → ровно db-consumed-not-declared (не coverage/unresolved)
# · Last fail: 2026-08-31 — roadmap: DSN с литеральным *** (needs.database нет → пароль не инжектируется)
# · Remove if: db-consumed-not-declared контракт меняется
@ldd_trajectory
def test_analyze_db_consumed_not_declared(caplog) -> None:
    """PG-потребление при needs_database=False → ровно db-consumed-not-declared."""
    logger.info("[IMP:9][test][analyze] db-consumed-not-declared on needs_database=False")
    compose_yaml = """
services:
  app:
    environment:
      - DATABASE_URL=${PLATFORM_POSTGRES_DSN}
    networks:
      - shared-db-net
"""
    violations = _analyze(compose_yaml, env_keys=frozenset({"PLATFORM_POSTGRES_DSN"}), needs_database=False)
    assert len(violations) == 1
    assert violations[0].rule == RULE_DB_CONSUMED_NOT_DECLARED
    assert violations[0].service == "app"
    assert "needs.database" in violations[0].message


# 🧪 TRAP[TEST] · Regression · needs.database="false"-СТРОКА — нормализация у ПОТРЕБИТЕЛЯ (K3)
# · Scenario: анализатор принимает needs_database: bool (флаг); потребление PG + flag=True → 0;
# ·   flag=False → db-consumed-not-declared. needs.database="false"-строка (YAML-ловушка bare false)
# ·   нормализуется ВЫШЕ — verify_contracts._needs_database_declared: truthy И str(v).lower() != "false"
# ·   (строковый "false" и булев false → False; имя БД → True)
# · Last fail: 2026-08-31 — F6: пилоты без needs.database → ручной .platform-db.env (канон нарушен)
# · Remove if: контракт db-флага / upstream-нормализация меняется
@ldd_trajectory
def test_db_flag_upstream_false_string_normalization(caplog, tmp_path: Path) -> None:
    """needs.database 'false'-строка/bare-false → upstream flag False; имя БД → True (граница K3)."""
    logger.info("[IMP:9][test][db-flag] needs.database false-string normalization is upstream")
    declared = tmp_path / "declared"
    declared.mkdir()
    (declared / "ai-platform.yaml").write_text("needs:\n  database: w7-smoke\n", encoding="utf-8")
    false_str = tmp_path / "false-str"
    false_str.mkdir()
    (false_str / "ai-platform.yaml").write_text('needs:\n  database: "false"\n', encoding="utf-8")
    bare_false = tmp_path / "bare-false"
    bare_false.mkdir()
    (bare_false / "ai-platform.yaml").write_text("needs:\n  database: false\n", encoding="utf-8")

    assert _needs_database_declared(declared) is True, "имя БД → флаг True (потребление объявлено)"
    assert _needs_database_declared(false_str) is False, "строка 'false' → флаг False (не declared)"
    assert _needs_database_declared(bare_false) is False, "bare false (YAML-ловушка) → флаг False"


# 🧪 TRAP[TEST] · Regression · $$-ссылки не дают false positive (TASK-4 инвариант 2)
# · Scenario: PASSWORD=$${NOT_RESOLVABLE}, HOME_DIR=$$HOME — $$-escape снимается ДО скана → ни одна
# ·   нерезолвимая «ссылка» не флагается; MIXED=a$${X}b${REAL} — REAL резолвится из env_keys
# · Last fail: N/A — $$-escape не имел прямого покрытия (QA F-1) · Remove if: escape-механика меняется
@ldd_trajectory
def test_analyze_escape_refs_no_false_positive(caplog) -> None:
    """$$-экранированные ссылки не флагаются env-var-unresolved (0 violations)."""
    logger.info("[IMP:9][test][analyze] $$-escaped refs produce no false positive")
    compose_yaml = """
services:
  bot:
    environment:
      - PASSWORD=$${NOT_RESOLVABLE}
      - HOME_DIR=$$HOME
      - MIXED=a$${X}b${REAL}
    networks:
      - proxy-net
"""
    violations = _analyze(compose_yaml, env_keys=frozenset({"REAL"}))
    assert violations == ()


# 🧪 TRAP[TEST] · Regression · resilience — никогда не кидать на данных (TASK-4 инвариант 4)
# · Scenario: compose не dict (list/str), services не dict (list), сервис не dict (str),
# ·   пустой compose → () БЕЗ exception (parse-fail флагает verify_contracts сам)
# · Last fail: N/A — resilience-границы не имели прямого покрытия (QA F-1) · Remove if: анализатор
# ·   меняет контракт resilience (начнёт кидать на не-dict)
@ldd_trajectory
def test_analyze_resilience_non_dict_inputs(caplog) -> None:
    """compose/services/сервис не dict → () без exception; пустой compose → ()."""
    logger.info("[IMP:9][test][analyze] resilience — non-dict inputs return ()")
    assert _analyze("[1, 2]") == ()
    assert _analyze("just-a-string") == ()
    assert _analyze("services: [a, b]") == ()
    assert _analyze("services:\n  bot: not-a-dict") == ()
    assert _analyze("{}") == ()


# endregion TEST_analyze_service_contracts


# region TEST_load_env_keys
# 🧪 TRAP[TEST] · Regression · load_env_keys — отсутствующий файл → frozenset() (TASK-4 инвариант 5)
# · Scenario: .env.platform может отсутствовать (проект без env) → frozenset() + IMP:7 лог,
# ·   НЕ exception (честная пустая интерполяция, не silent-fail)
# · Last fail: N/A — missing-file ветка не имела прямого покрытия (QA F-1) · Remove if: семантика
# ·   load_env_keys меняется (начнёт кидать на отсутствующий файл)
@ldd_trajectory
def test_load_env_keys_missing_file(caplog, tmp_path: Path) -> None:
    """Отсутствующий env-файл → frozenset() + IMP:7 лог (не exception)."""
    logger.info("[IMP:9][test][env-keys] missing file → frozenset()")
    keys = load_env_keys(tmp_path / "no-such.env")
    assert keys == frozenset()
    assert "file not found" in caplog.text


# 🧪 TRAP[TEST] · Regression · load_env_keys — парсинг KEY= (TASK-4 правило (b) источник резолва)
# · Scenario: комментарии/пустые строки/индент-комментарии skip; KEY с пробелами вокруг '='
# ·   (SPACED = value) strip'ится; MULTI=b=c=d — split по ПЕРВОМУ '='; EMPTY= — пустое значение
# · Last fail: N/A — комбинация edge-cases не имела прямого покрытия (QA F-1) · Remove if: парсер
# ·   load_env_keys меняет семантику (начнёт учитывать значения)
@ldd_trajectory
def test_load_env_keys_parses_keys_skips_comments(caplog, tmp_path: Path) -> None:
    """KEY= парсится (сплит по первому =, strip), комментарии/пустые строки пропускаются."""
    logger.info("[IMP:9][test][env-keys] KEY= parsing + comments/blank skip")
    env_file = tmp_path / ".env.platform"
    env_file.write_text(
        "# comment\n"
        "PLATFORM_POSTGRES_DSN=postgresql://u:p@pgbouncer:6432/db\n"
        "EMPTY=\n"
        "  # indented comment\n"
        "\n"
        "SPACED = value with spaces\n"
        "MULTI=b=c=d\n",
        encoding="utf-8",
    )
    keys = load_env_keys(env_file)
    assert keys == frozenset({"PLATFORM_POSTGRES_DSN", "EMPTY", "SPACED", "MULTI"})


# 🧪 TRAP[TEST] · Regression · load_env_keys — строки без '=' → 0 ключей
# · Scenario: файл без единого '=' (не env-формат) → frozenset() (ни одного ключа)
# · Last fail: N/A — ветка «файл без =» не имела прямого покрытия (QA F-1) · Remove if: парсер
# ·   load_env_keys меняет семантику
@ldd_trajectory
def test_load_env_keys_lines_without_equals(caplog, tmp_path: Path) -> None:
    """Файл без '=' → frozenset() (ни одного ключа, без exception)."""
    logger.info("[IMP:9][test][env-keys] no '=' lines → empty key set")
    env_file = tmp_path / "weird.env"
    env_file.write_text("just-text\nno-equals-here\n", encoding="utf-8")
    assert load_env_keys(env_file) == frozenset()


# endregion TEST_load_env_keys


# region TEST_load_provides
# 🧪 TRAP[TEST] · Regression · load_provides — fail-fast FileNotFoundError (TASK-4 инвариант 4)
# · Scenario: SoT core/platform-infra.yaml НЕ найден (resolve_infra_path → None) →
# ·   FileNotFoundError (SoT всегда доставляется с core/, DR-M4); run make generate-platform-env
# · Last fail: N/A — fail-fast ветка не имела прямого покрытия (QA F-1) · Remove if: load_provides
# ·   меняет fail-fast семантику (начнёт silent {} на отсутствие SoT)
@ldd_trajectory
def test_load_provides_fail_fast_missing_sot(caplog, monkeypatch) -> None:
    """platform-infra.yaml отсутствует → FileNotFoundError (fail-fast, никогда silent {})."""
    logger.info("[IMP:9][test][provides] missing SoT → FileNotFoundError")

    def _no_infra(env=None) -> None:
        del env  # контракт resolve_infra_path(env) — None на обоих кандидатах (implicit return)

    monkeypatch.setattr("core.internal.shared.compose_service_contract.resolve_infra_path", _no_infra)
    with pytest.raises(FileNotFoundError, match=r"platform-infra\.yaml not found"):
        load_provides()


# 🧪 TRAP[TEST] · Regression · load_provides — provides не dict → {} + error-лог (fail-open)
# · Scenario: platform-infra.yaml `provides: [one, two]` (list вместо dict) → {} + громкий
# ·   IMP:9 error-лог (coverage-правило молчит, env/db-правила работают — K3 fail-open контракт)
# · Last fail: N/A — not-dict ветка не имела прямого покрытия (QA F-1) · Remove if: fail-open
# ·   семантика load_provides меняется
@ldd_trajectory
def test_load_provides_not_dict_fail_open(caplog, tmp_path: Path) -> None:
    """provides-секция не dict → {} + error-лог (fail-open, не exception)."""
    logger.info("[IMP:9][test][provides] non-dict provides → {} + error log")
    infra_dir = tmp_path / "core"
    infra_dir.mkdir()
    (infra_dir / "platform-infra.yaml").write_text("provides:\n  - one\n  - two\n", encoding="utf-8")
    provides = load_provides(env={"PLATFORM_ROOT": str(tmp_path)})
    assert provides == {}
    assert "'provides' не dict" in caplog.text


# 🧪 TRAP[TEST] · Regression · load_provides — SoT provides-секция (DR-M4)
# · Scenario: tmp PLATFORM_ROOT с core/platform-infra.yaml (provides postgres/litellm) →
# ·   dict-секция возвращается как есть + IMP:9 лог (DI env-параметр load_provides(env),
# ·   зеркало resolve_infra_path — тот же путь, что K3 verify_contracts)
# · Last fail: N/A — happy-path не имел прямого покрытия (QA F-1) · Remove if: формат provides-секции
# ·   platform-infra.yaml меняется (помимо DR-M4)
@ldd_trajectory
def test_load_provides_returns_sot_section(caplog, tmp_path: Path) -> None:
    """provides-секция SoT (postgres/litellm) возвращается как dict + IMP:9 лог."""
    logger.info("[IMP:9][test][provides] SoT provides section loaded")
    infra_dir = tmp_path / "core"
    infra_dir.mkdir()
    (infra_dir / "platform-infra.yaml").write_text(
        "provides:\n"
        "  postgres:\n"
        "    networks: [shared-db-net]\n"
        "  litellm:\n"
        "    networks: [hermes-agent-net, shared-db-net, observability-net]\n",
        encoding="utf-8",
    )
    provides = load_provides(env={"PLATFORM_ROOT": str(tmp_path)})
    assert set(provides) == {"postgres", "litellm"}
    assert provides["postgres"] == {"networks": ["shared-db-net"]}
    assert "loaded 2 service(s)" in caplog.text


# endregion TEST_load_provides


# region TEST_dataclasses
# 🧪 TRAP[TEST] · Regression · frozen-семантика входных dataclass (TASK-4 контракт)
# · Scenario: ServiceContractViolation/ServiceContractInput — @dataclass(frozen=True):
# ·   setattr (типизированный, триггерит dataclass __setattr__) → FrozenInstanceError;
# ·   равенство/хэш по полям (v1 == v2, hash совпадает, set-дедупликация)
# ·   (v1 == v2, hash совпадает, set-дедупликация)
# · Last fail: N/A — frozen-контракт не имел прямого покрытия (QA F-1) · Remove if: dataclass
# ·   перестаёт быть frozen (мутабельный вход — дрейф контракта)
@ldd_trajectory
def test_dataclasses_frozen_immutability(caplog) -> None:
    """ServiceContractViolation/ServiceContractInput — frozen (FrozenInstanceError) + eq/hash."""
    logger.info("[IMP:9][test][dataclasses] frozen semantics + equality/hash")
    v = ServiceContractViolation(rule="r", service="s", message="m")
    with pytest.raises(FrozenInstanceError):
        v.rule = "other"  # type: ignore[attr-defined]
    inp = ServiceContractInput(
        compose={},
        env_keys=frozenset(),
        secret_names=frozenset(),
        needs_database=False,
        provides={},
    )
    with pytest.raises(FrozenInstanceError):
        inp.needs_database = True  # type: ignore[attr-defined]

    v2 = ServiceContractViolation(rule="r", service="s", message="m")
    assert v == v2 and hash(v) == hash(v2)
    assert len({v, v2}) == 1, "равные frozen-инстансы дедуплицируются в set"


# endregion TEST_dataclasses
