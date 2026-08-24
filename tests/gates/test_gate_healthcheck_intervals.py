#!/usr/bin/env python3
# GREP_SUMMARY: gate healthcheck-intervals U-63 compose-base classification 15s 30s 60s postgres anti-drift
# STRUCTURE: ▶ ┌classification dict (SoT)┐ → ○ for each core/modules/*/docker-compose.base.yml → ○ for each service healthcheck.interval → ◇ == expected class? → PASS | ⟦RED: offenders⟧ → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Healthcheck-intervals gate (DevPlan 116 B5 T10, U-63): каждый healthcheck.interval
##           в core/modules/*/docker-compose.base.yml соответствует классу модуля:
##           критичные данные = 15s, сервисы = 30s, фоновые = 60s.
## @scope    Читает core/modules/*/docker-compose.base.yml (yaml). Сервисы БЕЗ healthcheck.interval
##           пропускаются (например minio-createbuckets). Модуль вне классификации с healthcheck →
##           RED (классификация обязательна для нового модуля).
## @invariants
##   - Классификация — константа _MODULE_CLASS (SoT в гейте)
##   - Критичные (15s): postgres, clickhouse, minio, langfuse, litellm, hermes-agent
##   - Сервисы (30s): redis, nginx, status-page, monitoring, logging, infra-metrics
##   - Фоновые (60s): backup-cron
##   - postgres 10s → RED (самопротиворечие со start_period 15s — DevPlan 116 B5 T10.1)
## @rationale U-63: интервалы 10/15/30/60 без гейта; postgres сам себе противоречил
##            (interval 10s < start_period 15s). Класс-гейт делает политику enforce-емой.
## @changes 2026-08-01 | DevPlan 116 B5 T10 — Created
# endregion MODULE_CONTRACT

import logging

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_MODULES_DIR = ROOT / "core" / "modules"

# Классификация healthcheck-интервалов (SoT, DevPlan 116 B5 D4/U-63; 010 T3.1: +log-collector).
# Критичные данные — 15s; сервисы — 30s; фоновые — 60s.
_CRITICAL_15S = {"postgres", "clickhouse", "minio", "langfuse", "litellm", "hermes-agent"}
_SERVICES_30S = {"redis", "nginx", "status-page", "monitoring", "logging", "log-collector", "infra-metrics"}
_BACKGROUND_60S = {"backup-cron"}

_MODULE_CLASS: dict[str, str] = dict.fromkeys(_CRITICAL_15S, "15s")
_MODULE_CLASS.update(dict.fromkeys(_SERVICES_30S, "30s"))
_MODULE_CLASS.update(dict.fromkeys(_BACKGROUND_60S, "60s"))


@pytest.mark.gate
@ldd_trajectory
def test_healthcheck_intervals_match_classification(caplog) -> None:
    """healthcheck.interval в compose-base должен соответствовать классу модуля (U-63)."""
    violations: list[tuple[str, str, str, str]] = []  # (module, service, actual, expected)
    unknown: list[tuple[str, str, str]] = []  # (module, service, actual)
    checked = 0

    for compose in sorted(_MODULES_DIR.glob("*/docker-compose.base.yml")):
        mod = compose.parent.name
        try:
            data = yaml.safe_load(compose.read_text())
        except (OSError, yaml.YAMLError) as e:
            logger.error("[IMP:10][intervals] %s YAML error: %s", compose, e)
            pytest.fail(f"Failed to parse {compose}: {e}")
            continue
        services = (data or {}).get("services", {}) or {}
        for svc_name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            hc = svc.get("healthcheck")
            if not isinstance(hc, dict):
                continue  # сервис без healthcheck — вне политики
            interval = hc.get("interval")
            if not interval:
                continue
            checked += 1
            expected = _MODULE_CLASS.get(mod)
            if expected is None:
                unknown.append((mod, svc_name, str(interval)))
            elif str(interval) != expected:
                violations.append((mod, svc_name, str(interval), expected))

    if unknown:
        for mod, svc, actual in unknown:
            logger.error("[IMP:10][intervals] %s/%s interval=%s — модуль НЕ классифицирован", mod, svc, actual)
        pytest.fail(
            f"Модули с healthcheck.interval вне классификации ({len(unknown)}):\n"
            + "\n".join(f"  - {mod}/{svc}: {actual}" for mod, svc, actual in unknown)
            + "\n\nДобавь модуль в _MODULE_CLASS (15s критичные / 30s сервисы / 60s фоновые — D4)."
        )

    if violations:
        for mod, svc, actual, expected in violations:
            logger.error("[IMP:10][intervals] %s/%s interval=%s != expected %s", mod, svc, actual, expected)
        pytest.fail(
            f"healthcheck.interval не соответствует классу ({len(violations)}):\n"
            + "\n".join(f"  - {mod}/{svc}: {actual} != {expected}" for mod, svc, actual, expected in violations)
            + "\n\nКлассы (D4): критичные=15s, сервисы=30s, фоновые=60s."
        )

    logger.info(
        "[IMP:9][intervals] PASS: %d healthcheck.interval(-ов) соответствуют классификации (15/30/60s)",
        checked,
    )
