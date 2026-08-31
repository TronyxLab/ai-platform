"""
# GREP_SUMMARY: test check compose-service-networks K1 mirror analyzer coverage unresolved db-declared PASS FAIL R5 negative tmp_path
# STRUCTURE: ▶ handler check_compose_service_networks ◇ fixtures tmp_path (compose + .env.platform + ai-platform.yaml) → ⊕ сценарии: coverage-FAIL / canonical-PASS / db-declared-FAIL / R5-negative (DATABASE_URL=${DATABASE_URL}) → ⎋ LDD IMP:9 verdict trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests для K1-handler-а compose-service-networks (план 019 TASK-5,
##           $TEST_SPEC): K1-зеркало K3-чеков — потребление PLATFORM_* без сети провайдера →
##           FAIL; исправленный compose пилота (сети + DSN-маппинг) → PASS;
##           db-consumed-not-declared → FAIL; R5-негатив: точный инцидентный вход
##           DATABASE_URL=${DATABASE_URL} детектируется (env-var-unresolved).
## @scope    tests/unit; потребляет handler напрямую (native pytest, tmp_path, Zero Hardcode).
## @invariants
##   - Анализатор НЕ тестируется здесь дважды (его детальные тесты — в гейтах TASK-4);
##     этот файл проверяет ПРОВОДКУ K1: реестр + handler + PracticeCheck-контракт
##   - fixtures строго tmp_path; provides/secret-definitions — реальные SoT репо (read-only)
##   - Каждый тест: TRAP[TEST]-комментарий + IMP:9-верикт в caplog (LDD Semantic Trace)
## @rationale K1-канал обязан ловить инцидент-класс локально (до деплоя) — тест фиксирует
##            именно K1-рубеж, а не семантику анализатора.
## @changes  2026-08-31 · Plan 019 TASK-5 — создан
# endregion MODULE_CONTRACT
"""

import logging
import textwrap
from pathlib import Path

import pytest

from core.internal.practices.check_project.checks import compose as compose_checks
from core.internal.practices.manifest import PracticeCheck
from core.internal.shared.compose_service_contract import (
    RULE_DB_CONSUMED_NOT_DECLARED,
    RULE_ENV_VAR_UNRESOLVED,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_CHECK = PracticeCheck(
    id="compose-service-networks",
    level="baseline",
    languages=("all",),
    channel=("local", "ci"),
    klass="L1",
    auto_fix=False,
    timeout_sec=5,
)

_INCIDENT_COMPOSE = textwrap.dedent(
    """\
    services:
      managers-bot:
        image: ghcr.io/tronyxlab/asi-managers:latest
        env_file:
          - .env.platform
        environment:
          - NODE_ENV=production
          - DATABASE_URL=${DATABASE_URL}
          - LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000}
        networks:
          asi-managers-net:
          proxy-net:
            aliases:
              - asi-managers

    networks:
      asi-managers-net:
        name: asi-managers-net
        driver: bridge
      proxy-net:
        name: proxy-net
        external: true
    """
)

_CANONICAL_COMPOSE = textwrap.dedent(
    """\
    services:
      managers-bot:
        image: ghcr.io/tronyxlab/asi-managers:latest
        env_file:
          - .env.platform
        environment:
          - NODE_ENV=production
          - DATABASE_URL=${PLATFORM_POSTGRES_DSN}
          - LLM_BASE_URL=${PLATFORM_LITELLM_URL:-http://litellm:4000}
          - IMAGE_TAG=${IMAGE_TAG:-latest}
        networks:
          managers-bot-net:
          proxy-net:
            aliases:
              - managers-bot
          shared-db-net:
          hermes-agent-net:

    networks:
      managers-bot-net:
        name: managers-bot-net
        driver: bridge
      proxy-net:
        name: proxy-net
        external: true
      shared-db-net:
        name: shared-db-net
        external: true
      hermes-agent-net:
        name: hermes-agent-net
        external: true
    """
)


def _make_project(tmp_path: Path, compose_text: str, *, ai_platform: str = "") -> Path:
    """Собрать фикстуру проекта в tmp_path: compose + .env.platform + ai-platform.yaml."""
    (tmp_path / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
    (tmp_path / ".env.platform").write_text(
        "PLATFORM_DOMAIN=managers.asiteam.ru\n"
        "PLATFORM_POSTGRES_DSN=postgresql://managers-bot_user:pw@pgbouncer:6432/managers-bot_db\n",
        encoding="utf-8",
    )
    if ai_platform:
        (tmp_path / "ai-platform.yaml").write_text(ai_platform, encoding="utf-8")
    return tmp_path


def _run(project_dir: Path) -> object:
    """Прогнать handler напрямую (native pytest; runner-обвязку K1 не тестируем)."""
    return compose_checks.check_compose_service_networks(_CHECK, project_dir, fix=False)


def _print_ldd(caplog: object, records: list[logging.LogRecord]) -> None:
    """LDD-траектория IMP>=7 в stdout (Semantic Trace Verification)."""
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")


# 🧪 TRAP[TEST] · Regression · инцидент 019: PLATFORM_LITELLM_URL потребляется, hermes-agent-net нет
# · Last fail: production-compose пилотов (proxy-net only) деплоился без LLM-сети — crash-loop
# · Remove if: правило service-network-coverage упразднено (K3+K3 удалены вместе)
def test_check_compose_service_networks_blocks_uncovered_service(caplog, tmp_path) -> None:
    """Потребление PLATFORM_LITELLM_URL без hermes-agent-net → FAIL (coverage)."""
    with caplog.at_level(logging.INFO):
        project_dir = _make_project(tmp_path, _INCIDENT_COMPOSE)
        result = _run(project_dir)
    _print_ldd(caplog, caplog.records)
    assert result.status == "FAIL", f"expected FAIL, got {result.status}: {result.message}"
    assert "service-network-coverage" in result.message or "service-contract" in result.message
    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "LDD Error: no IMP:9 verdict log in FAIL path"


# 🧪 TRAP[TEST] · Scenario · canonical compose пилота (TASK-2 фикс) проходит K1
# · Last fail: — (позитивный сценарий)
# · Remove if: compose-канон ai-project изменён (сети/DSN-маппинг) — обновить fixture
def test_check_compose_service_networks_passes_canonical(caplog, tmp_path) -> None:
    """Исправленный compose (4 сети + DSN-маппинг + needs.database) → PASS."""
    ai_platform = (
        "name: managers-bot\ntype: typescript\nneeds:\n  database: managers-bot_db\n"
        "  domain: managers.asiteam.ru\n  expose: true\n"
    )
    with caplog.at_level(logging.INFO):
        project_dir = _make_project(tmp_path, _CANONICAL_COMPOSE, ai_platform=ai_platform)
        result = _run(project_dir)
    _print_ldd(caplog, caplog.records)
    assert result.status == "PASS", f"expected PASS, got {result.status}: {result.message}"
    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "LDD Error: no IMP:9 verdict log in PASS path"


# 🧪 TRAP[TEST] · Regression · PLATFORM_POSTGRES_DSN потребляется без needs.database
# · Last fail: ***-DSN класс (F8): DSN с литеральным *** у проектов без needs.database
# · Remove if: правило db-consumed-not-declared упразднено
def test_check_compose_service_networks_blocks_db_without_needs(caplog, tmp_path) -> None:
    """PLATFORM_POSTGRES_DSN потребляется, needs.database не объявлен → FAIL."""
    with caplog.at_level(logging.INFO):
        project_dir = _make_project(tmp_path, _CANONICAL_COMPOSE)  # без ai-platform.yaml
        result = _run(project_dir)
    _print_ldd(caplog, caplog.records)
    assert result.status == "FAIL", f"expected FAIL, got {result.status}: {result.message}"
    assert "db-consumed-not-declared" in result.message or RULE_DB_CONSUMED_NOT_DECLARED in result.message


# 🧪 TRAP[TEST] · NEGATIVE (R5) · детектор env-var-unresolved ловит точный инцидентный вход 019
# · Last fail: DATABASE_URL=${DATABASE_URL} — переменной нет в .env.platform → пустая интерполяция
# · Remove if: если детектор перестаёт ловить этот вход (регрессия правила)
def test_check_compose_service_networks_negative_incident_input(caplog, tmp_path) -> None:
    """R5: ${DATABASE_URL} без дефолта и без env-источника → env-var-unresolved в детали."""
    with caplog.at_level(logging.INFO):
        project_dir = _make_project(tmp_path, _INCIDENT_COMPOSE)
        result = _run(project_dir)
    _print_ldd(caplog, caplog.records)
    assert result.status == "FAIL"
    assert RULE_ENV_VAR_UNRESOLVED in result.message or "env-var-unresolved" in result.message, (
        f"R5 FAIL: detector missed original 019 trigger (DATABASE_URL=${{DATABASE_URL}}): {result.message}"
    )
