"""Unit-тесты Row16 (аудит DevPlan 010): grafana datasource loki tenant header.

# GREP_SUMMARY: test_grafana_loki_tenant httpHeaderName1 X-Scope-OrgID secureJsonData LOKI_TENANT datasource row16
# STRUCTURE: ▶ datasources.yml (SoT provisioning) → ◇ Loki datasource block → ⊕ header/secure-value asserts → ⎋
# region MODULE_CONTRACT
## @purpose  Закрытие тестового пробела Row 16 аудита: цепочка loki tenant — alloy push
##           (test_alloy_config) и pg_hba покрыты, но grafana-консюм tenant'а НЕ тестируется.
## @scope    core/modules/monitoring/config/grafana/datasources.yml (статический SoT provisioning)
## @invariants
##   - jsonData.httpHeaderName1 == 'X-Scope-OrgID' (имя заголовка — канон T2.0b)
##   - secureJsonData.httpHeaderValue1 == '$__env{LOKI_TENANT}' (значение ТОЛЬКО в secureJsonData
##     — Grafana требование для заголовков 7.4+; env-подстановка в runtime контейнера)
##   - LOKI_TENANT passthrough в monitoring compose (дефолт "platform", единый по стеку)
## @rationale Tenant-изоляция трёхзвенная (alloy → loki-vhost → grafana): отсутствие любого звена
##            ломает изоляцию молча — каждый звено ассертится своим тестом.
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.static_audit

_DATASOURCES = (
    Path(__file__).resolve().parent.parent.parent
    / "core"
    / "modules"
    / "monitoring"
    / "config"
    / "grafana"
    / "datasources.yml"
)
_MONITORING_COMPOSE = (
    Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "monitoring" / "docker-compose.base.yml"
)


def _loki_datasource() -> dict:
    """Loki datasource block из SoT provisioning (fail, если переименован)."""
    with _DATASOURCES.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    for ds in data.get("datasources", []):
        if str(ds.get("type")) == "loki":
            return ds
    pytest.fail("grafana datasources.yml: Loki datasource не найден (переименование сломало tenant-цепочку)")


def test_loki_datasource_header_name_is_scope_orgid() -> None:
    """jsonData.httpHeaderName1 == 'X-Scope-OrgID' — имя заголовка tenant-изоляции (T2.0b)."""
    ds = _loki_datasource()
    header_name = (ds.get("jsonData") or {}).get("httpHeaderName1")

    assert header_name == "X-Scope-OrgID", f"неверное имя tenant-заголовка: {header_name!r}"


def test_loki_datasource_header_value_in_secure_json_data() -> None:
    """httpHeaderValue1 живёт ТОЛЬКО в secureJsonData через $__env{LOKI_TENANT}."""
    ds = _loki_datasource()
    secure = ds.get("secureJsonData") or {}
    value = secure.get("httpHeaderValue1")

    assert value == "$__env{LOKI_TENANT}", f"httpHeaderValue1 вне $__env{{LOKI_TENANT}}: {value!r}"
    # Значение НЕ дублируется в открытых jsonData (Grafana security-требование)
    assert "httpHeaderValue1" not in (ds.get("jsonData") or {}), "tenant-значение утекло в открытые jsonData"


def test_monitoring_compose_passes_loki_tenant_env() -> None:
    """Compose monitoring передаёт LOKI_TENANT в grafana (дефолт 'platform', единый по стеку)."""
    with _MONITORING_COMPOSE.open(encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)

    grafana_envs: list[object] = []
    for svc_name, svc in (compose.get("services") or {}).items():
        env = svc.get("environment") or {}
        if isinstance(env, dict):
            if "LOKI_TENANT" in env:
                grafana_envs.append((svc_name, env["LOKI_TENANT"]))
        elif isinstance(env, list):
            grafana_envs.extend((svc_name, e) for e in env if str(e).startswith("LOKI_TENANT"))

    assert grafana_envs, "LOKI_TENANT не передан ни одному сервису monitoring — grafana получит пустой tenant"
    # Дефолт 'platform' — единый стековый tenant (T2.0b); provision подставляет имя контекста в multi-node
    assert any("platform" in str(v) for _, v in grafana_envs), f"нет дефолта platform: {grafana_envs}"
