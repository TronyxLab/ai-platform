#!/usr/bin/env python3
# GREP_SUMMARY: gate healthcheck-drift env-parametrization D5-canon check_docker_health check_http no-bare-literals T10.12 W10
# STRUCTURE: ▶ scan module healthcheck.sh → ◇ source lib/healthcheck.sh → ◇ env-parametrized names/ports (${VAR:-default}) → ◇ check_docker_health (не raw docker inspect) → ◇ deep: check_http → ⊕ violations → ⎋ assert 0
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 136 W10 T10.14): healthcheck-контракты модулей vs канон D5
##           (healthcheck_poller.py / lib/healthcheck.sh критерии). Закрывает тихий дрейф
##           (M-2..M-6): контейнерные имена/порты, захардкоженные в healthcheck.sh, ломали
##           smoke/тесты при docker-compose.test.yml rename (-test suffix) и shifted-портах.
## @scope    Статический скан core/modules/*/healthcheck.sh (Docker-модулей): env-параметризация
##           имён/портов (паттерн infra-metrics, W10 T10.12), делегирование канону lib/healthcheck.sh
##           (check_docker_health/check_http — критерий D5 «running-без-healthcheck = здоров» живёт
##           ТОЛЬКО в lib), отсутствие raw docker inspect/raw curl в некомментарном коде.
## @invariants
##   - Имена контейнеров в healthcheck.sh — через ${VAR:-default} (не голые литералы в массивах)
##   - Порты deep-проверок — через ${VAR:-default} (не захардкоженные 9090/3000/... )
##   - Каждый Docker healthcheck.sh source-ит ../../lib/healthcheck.sh и зовёт check_docker_health
##   - Deep-режим: check_http (НЕ raw curl -sf) — канон DRIFT-H4/DevPlan 083
## @rationale M-2..M-6 (DevPlan 136 §11.1): healthcheck.sh с захардкоженными именами — дрейф контракта
##            (F-7/2026-07-18 TRAP[BUG]: shifted-порты ломали smoke; 2026-07-27: -test suffix).
##            Gate фиксирует env-параметризацию как инвариант — будущие rename не ломают healthcheck.
## @changes 2026-08-05 · DevPlan 136 W10 T10.14 — Created
## @links   core/lib/healthcheck.sh (канон D5), core/internal/deploy/healthcheck_poller.py,
##          core/modules/infra-metrics/healthcheck.sh (эталон паттерна env-параметризации)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_MODULES_DIR: Path = repo_root() / "core" / "modules"

# Модули, охваченные W10 T10.12 (M-2..M-6) + эталон infra-metrics — обязательная env-параметризация
_ENV_PARAM_MODULES: tuple[str, ...] = (
    "infra-metrics",
    "monitoring",
    "logging",
    "nginx",
    "langfuse",
    "hermes-agent",
)

# Все Docker-модули с healthcheck.sh — обязательный source lib + check_docker_health
_DOCKER_MODULES: tuple[str, ...] = (
    "postgres",
    "redis",
    "clickhouse",
    "nginx",
    "backup-cron",
    "hermes-agent",
    "minio",
    "monitoring",
    "logging",
    "langfuse",
    "litellm",
    "status-page",
    "infra-metrics",
)

# ${VAR:-default} присваивание — с опциональными кавычками (CONTAINER="${VAR:-x}" / _PORT="${VAR:-x}")
_ENV_ASSIGN_RE = re.compile(r"(?:CONTAINER|CONTAINERS|_PORT|_URL|_NAME|AGENT_URL)\s*=\s*[\"']?\$\{[A-Z0-9_]+:-")
_BARE_LITERAL_RE = re.compile(r"(?:CONTAINER|CONTAINERS|_PORT|AGENT_URL)\s*=\s*[\"']?[a-z][a-z0-9-]*[\"']?\s*(?:#.*)?$")
# Строки вида CONTAINERS=("loki" "alloy") — голые литералы (без ${VAR:-})
_BARE_ARRAY_RE = re.compile(r'CONTAINERS=\s*\(\s*["\'][a-z0-9-]+["\']')
# URL-литерал с захардкоженным портом (исходная форма F-7: AGENT_URL="http://127.0.0.1:9119")
# — deep-проверка ломалась на shifted-портах; порт обязан идти через ${VAR}
_BARE_URL_PORT_RE = re.compile(r"http://127\.0\.0\.1:\d+")


def _read(module: str) -> str:
    path = _MODULES_DIR / module / "healthcheck.sh"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _non_comment_lines(content: str) -> list[str]:
    """Строки без комментариев (код + пустые)."""
    return [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.gate
def test_env_parametrized_container_names(caplog: pytest.LogCaptureFixture) -> None:
    """T10.12: имена контейнеров/порты в healthcheck.sh env-параметризованы (${VAR:-default})."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.12 (M-2..M-6) — захардкоженные имена
    # · Scenario: docker-compose.test.yml rename (-test) / shifted-порт → healthcheck.sh с литералом падает
    # · Last fail: 2026-07-18/27 (F-7: shifted 18081/19100; -test suffix) — infra-metrics TRAP[BUG]
    # · Remove if: healthcheck.sh заменён механизмом без имён контейнеров
    caplog.set_level(logging.INFO)

    violations: list[str] = []
    for module in _ENV_PARAM_MODULES:
        content = _read(module)
        if not content:
            violations.append(f"{module}: healthcheck.sh not found")
            continue
        code_lines = _non_comment_lines(content)
        # 1. Голые литералы в присваиваниях CONTAINER/CONTAINERS/..._PORT/AGENT_URL — RED
        for line in code_lines:
            if _BARE_LITERAL_RE.search(line) and "${" not in line:
                violations.append(f"{module}: bare literal assignment: {line}")
            if _BARE_ARRAY_RE.search(line):
                violations.append(f"{module}: bare literal array: {line}")
            # F-7 (M-2..M-6): http://127.0.0.1:<порт-литерал> — shifted-порт ломал deep-проверку
            if _BARE_URL_PORT_RE.search(line) and "${" not in line:
                violations.append(f"{module}: hardcoded port in URL literal (F-7 drift): {line}")
        # 2. Контракт: имя контейнера присваивается через env (${VAR:-...})
        if not any(_ENV_ASSIGN_RE.search(ln) for ln in code_lines):
            violations.append(f"{module}: no env-parametrized container/port assignment found")

    logger.info("[IMP:9][hc-drift] env-параметризация нарушений: %d", len(violations))

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert not violations, f"[IMP:9][hc-drift] FAIL: {len(violations)} нарушений: {'; '.join(violations[:8])}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][hc-drift] PASS: имена/порты healthcheck.sh env-параметризованы")


@pytest.mark.gate
def test_delegates_to_lib_healthcheck_d5_canon(caplog: pytest.LogCaptureFixture) -> None:
    """T10.14: каждый Docker healthcheck.sh source-ит lib/healthcheck.sh и зовёт check_docker_health."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.14 (D5 канон) — свой docker inspect вместо канона
    # · Scenario: healthcheck.sh реализует собственный docker-health (raw docker inspect) — дрейф критерия
    # · Last fail: N/A (preventive — контракт DevPlan 083 AC3)
    # · Remove if: healthcheck контракт заменён
    caplog.set_level(logging.INFO)

    violations: list[str] = []
    for module in _DOCKER_MODULES:
        content = _read(module)
        if not content:
            logger.info("[IMP:8][hc-drift] %s: нет healthcheck.sh (skip)", module)
            continue
        if "lib/healthcheck.sh" not in content:
            violations.append(f"{module}: не source-ит ../../lib/healthcheck.sh")
            continue
        if "check_docker_health" not in content:
            violations.append(f"{module}: не вызывает check_docker_health (канон D5)")
        code_lines = _non_comment_lines(content)
        raw = [ln for ln in code_lines if "docker inspect" in ln or "State.Health" in ln]
        if raw:
            violations.append(f"{module}: raw docker inspect в коде: {raw[:2]}")
        raw_curl = [
            ln for ln in code_lines if "curl -sf" in ln or (re.search(r"\bcurl\b", ln) and "check_http" not in ln)
        ]
        if raw_curl:
            violations.append(f"{module}: raw curl в коде (должен быть check_http): {raw_curl[:2]}")

    logger.info("[IMP:9][hc-drift] D5-канон нарушений: %d", len(violations))

    found_imp9 = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    assert not violations, f"[IMP:9][hc-drift] FAIL: {len(violations)} нарушений: {'; '.join(violations[:8])}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][hc-drift] PASS: модульные healthcheck делегируют канону lib/healthcheck.sh")
