# GREP_SUMMARY: gate service-network-coverage env-var-unresolved db-consumed-not-declared K3 verify-contracts L1 R5 incident pilots asi-group platform-infra provides shared-db-net hermes-agent-net PLATFORM_POSTGRES_DSN
# STRUCTURE: ▶ ┌tmp_path project (compose + .env.platform + ai-platform.yaml)┐ → ○ verify_project_contracts(l1_only, статика) → ◇ filter 3 new rule-ids → ⊕ assert (negative incident: coverage+unresolved; canonical: 0; db-needs; env-unresolved) → ⎋
# region MODULE_CONTRACT
## @purpose  Gate-тесты L1 service-contracts (Plan 019 TASK-4, K3-рубеж, AC3): деплой compose,
##           потребляющего платформенный сервис без сети провайдера / с неразрешимым ${VAR} /
##           с PG-потреблением без needs.database — БЛОК. Инцидентный инпут пилотов asi-group
##           (proxy-net only + DATABASE_URL=${DATABASE_URL} + LLM_BASE_URL=${PLATFORM_LITELLM_URL:-...})
##           обязан давать violations по ОБОИМ правилам (service-network-coverage + env-var-unresolved)
##           — R5 anti-survivorship: детектор ловит точный вход, поймавший инцидент.
## @scope    verify_project_contracts (core/internal/deploy/verify_contracts.py) через shared-
##           анализатор compose_service_contract. Статические фикстуры в tmp_path (Zero Hardcode,
##           docker НЕ требуется). Секреты НЕ используются в фикстурах — load_secret_definitions
##           читает РЕАЛЬНЫЙ core/secret-definitions.yaml (план 019 TASK-4: не полагаться на него).
## @invariants
##   - pytestmark = pytest.mark.gate (тринити: файл tests/gates/ + маркер + entrypoint-manifest)
##   - Фильтр по 3 новым rule-id: другие L1-контракты (healthcheck/limits/labels) не в скоупе
##   - verify_project_contracts(l1_only=True) — только L1-статика (без docker-L2)
##   - ldd_trajectory: IMP:9 [verify_contracts][done] пишется всегда → декоратор валиден
## @rationale AC3 (план 019): класс «потребляемый сервис без сети провайдера» был слепым пятном
##            K3 (F5) — инцидент пилотов (F1-F3). Негативные тесты на точном инцидентном инпуте
##            (R5) закрепляют, что детектор НЕ потеряет покрытие; позитив — канонический compose
##            (исправленный пилот) не блокируется.
## @changes  2026-08-31 · Plan 019 TASK-4 — создан (K3-гейт service-contracts)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from core.internal.deploy.verify_contracts import verify_project_contracts
from core.internal.shared.compose_service_contract import (
    RULE_DB_CONSUMED_NOT_DECLARED,
    RULE_ENV_VAR_UNRESOLVED,
    RULE_SERVICE_NETWORK_COVERAGE,
)
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.gate

logger = logging.getLogger(__name__)

# ── Новые rule-id (фильтр находок: другие L1-контракты вне скоупа этого гейта) ──
_NEW_RULES: frozenset[str] = frozenset({
    RULE_SERVICE_NETWORK_COVERAGE,
    RULE_ENV_VAR_UNRESOLVED,
    RULE_DB_CONSUMED_NOT_DECLARED,
})


# region HELPER_write_project
def _write_project(
    tmp_path: Path,
    name: str,
    compose: dict,
    env_platform: str | None = None,
    ai_platform_yaml: str | None = None,
) -> Path:
    """Write fixture project dir (docker-compose.yml + optional .env.platform/ai-platform.yaml).

    ## @purpose  Zero Hardcode фикстура: compose строится dict-ом → yaml.safe_dump в tmp_path.
    ## @io        ⇥ tmp_path/name/compose/env_platform/ai_platform_yaml → ⎋ Path (project_dir)
    ## @complexity O(1) — 3 файла
    """
    project = tmp_path / name
    project.mkdir()
    (project / "docker-compose.yml").write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    if env_platform is not None:
        (project / ".env.platform").write_text(env_platform, encoding="utf-8")
    if ai_platform_yaml is not None:
        (project / "ai-platform.yaml").write_text(ai_platform_yaml, encoding="utf-8")
    logger.info("[IMP:8][gate-service-contracts] fixture project written: %s", project)
    return project


# endregion HELPER_write_project


# region HELPER_new_rule_findings
def _new_rule_findings(report) -> list[tuple[str, str, str]]:
    """(contract_id, severity, message) находки ТОЛЬКО по 3 новым service-contract правилам."""
    return [(f.contract_id, f.severity, f.message) for f in report.findings if f.contract_id in _NEW_RULES]


# endregion HELPER_new_rule_findings


# 🧪 TRAP[TEST] · NEGATIVE (R5) · service-network-coverage + env-var-unresolved — инцидент пилотов
# ·   asi-group (план 019 F1-F3/F5: production-compose «только proxy-net» + DATABASE_URL=${DATABASE_URL})
# · Scenario: compose client-bot ДО фикса — networks [client-bot-net, proxy-net] только;
# ·   DATABASE_URL=${DATABASE_URL} (нет в .env.platform → env-var-unresolved);
# ·   LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000} (потребление litellm с дефолтом —
# ·   env-resolved, но networks ∩ provides.networks(litellm) = ∅ → service-network-coverage)
# · Last fail: 2026-08-31 — production-compose client-bot/managers-bot деплоился, K3 слеп к классу
# ·   (проверял только «external-сеть вне allowlist», обратного не было)
# · Remove if: service-network-coverage/env-var-unresolved контракты меняются (пересмотр K3)
@ldd_trajectory
def test_gate_network_coverage_blocks_db_without_shared_db_net(caplog, tmp_path: Path) -> None:
    """R5 negative: инцидентный compose пилота → violations по ОБОИМ правилам (coverage + unresolved)."""
    compose = {
        "services": {
            "client-bot": {
                "image": "ghcr.io/tronyxlab/asi-faq:latest",
                "environment": [
                    "DATABASE_URL=${DATABASE_URL}",
                    "LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000}",
                ],
                "networks": ["client-bot-net", "proxy-net"],
            }
        },
        "networks": {
            "client-bot-net": {"driver": "bridge"},
            "proxy-net": {"external": True},
        },
    }
    project = _write_project(
        tmp_path,
        "incident",
        compose,
        env_platform="PLATFORM_DOMAIN=faq.asiteam.ru\n",
        ai_platform_yaml="name: client-bot\ntype: typescript\n",
    )

    report = verify_project_contracts(project, l1_only=True, audit_log_file=str(tmp_path / "audit.jsonl"))
    findings = _new_rule_findings(report)
    rules = {rule for rule, _sev, _msg in findings}

    logger.info("[IMP:8][gate-service-contracts] incident findings: %s", findings)
    assert RULE_SERVICE_NETWORK_COVERAGE in rules, (
        f"R5 FAIL: coverage-детектор не поймал инцидент (litellm без hermes-agent-net): {findings}"
    )
    assert RULE_ENV_VAR_UNRESOLVED in rules, (
        f"R5 FAIL: env-var-unresolved не поймал ${'DATABASE_URL'} (нет в .env.platform): {findings}"
    )
    blocked_rules = {rule for rule, sev, _msg in findings if sev == "block"}
    assert {RULE_SERVICE_NETWORK_COVERAGE, RULE_ENV_VAR_UNRESOLVED} <= blocked_rules, (
        f"L1-нарушения обязаны быть severity=block: {findings}"
    )
    logger.info("[IMP:9][gate-service-contracts] incident blocked by both rules ✓")


# 🧪 TRAP[TEST] · CONTROL · канонический compose (исправленный пилот) — 0 service-contract violations
# · Scenario: managers-bot ПОСЛЕ фикса — networks [managers-bot-net, proxy-net, shared-db-net,
# ·   hermes-agent-net]; DATABASE_URL=${PLATFORM_POSTGRES_DSN} (в .env.platform);
# ·   LLM_BASE_URL=${PLATFORM_LITELLM_URL:-...}; ${IMAGE_TAG:-latest}/${HEALTH_PORT:-8787} — дефолты,
# ·   не флагаются; ai-platform.yaml needs.database объявлен
# · Last fail: N/A (позитив — анти-survivorship: исправленный compose не должен блокироваться)
# · Remove if: канон сетей/DSN-маппинга меняется
@ldd_trajectory
def test_gate_network_coverage_passes_canonical_compose(caplog, tmp_path: Path) -> None:
    """Позитив: исправленный compose пилота → 0 findings по новым правилам."""
    compose = {
        "services": {
            "managers-bot": {
                "image": "${IMAGE_REGISTRY:-ghcr.io}/tronyxlab/asi-managers:${IMAGE_TAG:-latest}",
                "environment": [
                    "NODE_ENV=production",
                    "DATABASE_URL=${PLATFORM_POSTGRES_DSN}",
                    "LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000}",
                    "HEALTH_PORT=${HEALTH_PORT:-8787}",
                ],
                "networks": ["managers-bot-net", "proxy-net", "shared-db-net", "hermes-agent-net"],
            }
        },
        "networks": {
            "managers-bot-net": {"driver": "bridge"},
            "proxy-net": {"external": True},
            "shared-db-net": {"external": True},
            "hermes-agent-net": {"external": True},
        },
    }
    project = _write_project(
        tmp_path,
        "managers-bot",
        compose,
        env_platform="PLATFORM_POSTGRES_DSN=postgresql://managers-bot_user:***@pgbouncer:6432/managers-bot_db\n",
        ai_platform_yaml="name: managers-bot\ntype: typescript\nneeds:\n  database: managers-bot_db\n",
    )

    report = verify_project_contracts(project, l1_only=True, audit_log_file=str(tmp_path / "audit.jsonl"))
    findings = _new_rule_findings(report)

    logger.info("[IMP:8][gate-service-contracts] canonical findings: %s", findings)
    assert findings == [], f"канонический compose не должен давать service-contract violations: {findings}"
    logger.info("[IMP:9][gate-service-contracts] canonical compose passes all 3 new rules ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · db-consumed-not-declared — PLATFORM_POSTGRES_DSN без needs.database
# · Scenario: DATABASE_URL=${PLATFORM_POSTGRES_DSN} + shared-db-net подключена (coverage OK),
# ·   .env.platform содержит DSN (env-resolved), НО ai-platform.yaml БЕЗ needs.database →
# ·   ровно db-consumed-not-declared violation (класс ***-DSN roadmap, план 019 F6/F8)
# · Last fail: 2026-08-31 — roadmap: DSN с литеральным *** (needs.database нет, пароль не инжектируется)
# · Remove if: db-consumed-not-declared контракт меняется
@ldd_trajectory
def test_gate_db_consumed_without_needs_blocks(caplog, tmp_path: Path) -> None:
    """PG-потребление без needs.database → ровно db-consumed-not-declared (block), не coverage."""
    compose = {
        "services": {
            "app": {
                "image": "busybox:latest",
                "environment": ["DATABASE_URL=${PLATFORM_POSTGRES_DSN}"],
                "networks": ["shared-db-net"],
            }
        },
        "networks": {"shared-db-net": {"external": True}},
    }
    project = _write_project(
        tmp_path,
        "db-undeclared",
        compose,
        env_platform="PLATFORM_POSTGRES_DSN=postgresql://app_user:p@pgbouncer:6432/app_db\n",
        ai_platform_yaml="name: app\ntype: backend\n",  # needs.database ОТСУТСТВУЕТ
    )

    report = verify_project_contracts(project, l1_only=True, audit_log_file=str(tmp_path / "audit.jsonl"))
    findings = _new_rule_findings(report)

    logger.info("[IMP:8][gate-service-contracts] db-needs findings: %s", findings)
    db_findings = [f for f in findings if f[0] == RULE_DB_CONSUMED_NOT_DECLARED]
    assert len(db_findings) == 1, f"ровно одно db-consumed-not-declared violation: {findings}"
    assert db_findings[0][1] == "block", f"db-consumed-not-declared обязан быть block: {db_findings}"
    assert db_findings == findings, (
        f"только db-consumed-not-declared (НЕ coverage — сеть есть; НЕ unresolved — DSN в env): {findings}"
    )
    logger.info("[IMP:9][gate-service-contracts] PG-consumption without needs.database blocked ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · env-var-unresolved — ${VAR} без дефолта вне источников резолва
# · Scenario: FOO_URL=${FOO_URL} (нет в .env.platform, нет в secret-definitions) →
# ·   env-var-unresolved block (интерполяция compose даст пустую строку — класс F3-инцидента)
# · Last fail: 2026-08-31 — DATABASE_URL=${DATABASE_URL} резолвился в пусто при --env-file .env.platform
# · Remove if: env-var-unresolved контракт меняется
@ldd_trajectory
def test_gate_env_var_unresolved_blocks(caplog, tmp_path: Path) -> None:
    """${VAR} без дефолта, вне .env.platform и secret-definitions → env-var-unresolved block."""
    compose = {
        "services": {
            "app": {
                "image": "busybox:latest",
                "environment": ["FOO_URL=${FOO_URL}"],
                "networks": ["proxy-net"],
            }
        },
        "networks": {"proxy-net": {"external": True}},
    }
    project = _write_project(
        tmp_path,
        "unresolved",
        compose,
        env_platform="PLATFORM_DOMAIN=example.com\n",  # FOO_URL отсутствует
        ai_platform_yaml="name: app\ntype: backend\n",
    )

    report = verify_project_contracts(project, l1_only=True, audit_log_file=str(tmp_path / "audit.jsonl"))
    findings = _new_rule_findings(report)

    logger.info("[IMP:8][gate-service-contracts] env-unresolved findings: %s", findings)
    unresolved = [f for f in findings if f[0] == RULE_ENV_VAR_UNRESOLVED]
    assert unresolved, f"нет env-var-unresolved violation для ${{FOO_URL}}: {findings}"
    assert any("FOO_URL" in msg for _r, _s, msg in unresolved), f"violation не про FOO_URL: {unresolved}"
    assert unresolved[0][1] == "block", f"env-var-unresolved обязан быть block: {unresolved}"
    logger.info("[IMP:9][gate-service-contracts] unresolved ${FOO_URL} blocked ✓")
