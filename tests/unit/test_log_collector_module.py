# GREP_SUMMARY: test-log-collector-module module-gates compose-profiles healthcheck module-yaml loki-url tenant-header X-Scope-OrgID no-depends-on auth-enabled DevPlan-010
# STRUCTURE: ▶ module-gates (compose profiles + healthcheck.sh + module.yaml) → ◇ config.alloy ${LOKI_URL} + X-Scope-OrgID → ◇ compose БЕЗ depends_on loki → ◇ loki-config.yml auth_enabled:true → ⎋ IMP:9 PASS
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/log-collector/ (DevPlan 010 T3.1: выделен из logging) —
##           канонические module-гейты для нового модуля + требования сплита:
##           LOKI_URL параметризован, tenant header X-Scope-OrgID (T2.0b), НЕТ depends_on loki
##           (WAL self-heal), logging loki-config auth_enabled: true.
## @scope    Static YAML/HCL-парс реальных файлов модуля — без Docker. Читает:
##           core/modules/log-collector/{docker-compose.base.yml, module.yaml, healthcheck.sh,
##           config/config.alloy} + core/modules/logging/config/loki-config.yml.
## @invariants
##   - compose: профиль [log-collector] на каждом сервисе, x-logging anchor, healthcheck, container_name alloy
##   - healthcheck.sh: executable, source lib/healthcheck.sh, check_docker_health, БЕЗ loki /ready
##   - module.yaml: name/install_type/spool (alloy-data), depends_on [logging]
##   - config.alloy: ${LOKI_URL} endpoint (не захардкоженный http://loki:3100) + X-Scope-OrgID ${LOKI_TENANT}
##   - compose: НЕ содержит depends_on loki (T3.1 — WAL буферизует, self-heal)
##   - logging/loki-config.yml: auth_enabled: true (T2.0b — tenant-изоляция)
## @rationale Сплит-требования (010 §7 T3.1, §6.0 T2.0b) фиксируются тестами на уровне модуля —
##            захардкоженный endpoint / depends_on loki / auth_enabled:false — регрессия.
## @changes 2026-08-22 | DevPlan 010 T3.1 + T2.0b — Created
# endregion MODULE_CONTRACT

import logging
import os

import yaml
from _conftest.ldd import ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_ROOT = repo_root()
_MODULE_DIR = _ROOT / "core" / "modules" / "log-collector"
_COMPOSE = _MODULE_DIR / "docker-compose.base.yml"
_MODULE_YAML = _MODULE_DIR / "module.yaml"
_HEALTHCHECK = _MODULE_DIR / "healthcheck.sh"
_ALLOY_CONFIG = _MODULE_DIR / "config" / "config.alloy"
_LOKI_CONFIG = _ROOT / "core" / "modules" / "logging" / "config" / "loki-config.yml"


# region HELPERS
def _compose() -> dict:
    """YAML-парс docker-compose.base.yml модуля (read-only)."""
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _read_healthcheck() -> str:
    return _HEALTHCHECK.read_text(encoding="utf-8")


def _read_alloy() -> str:
    return _ALLOY_CONFIG.read_text(encoding="utf-8")


# endregion HELPERS


# ═══════════════════════════════════════════════════════════════════════════
# MODULE-GATES (канонический контракт модуля — по образцу test_module_domains_static)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-22 · gate/module-contract · Регресс: log-collector compose без profiles
# · Scenario: канон core/modules/AGENTS.md — profiles: [log-collector] на каждом сервисе
# · Last fail: N/A (новый модуль 010 T3.1)
# · Remove if: profiles-механизм заменён в compose-архитектуре
@ldd_trajectory
def test_compose_services_have_profile(caplog) -> None:
    """docker-compose.base.yml: каждый сервис имеет profiles: [log-collector]."""
    caplog.set_level(logging.INFO)
    data = _compose()
    services = data.get("services", {})
    assert services, "log-collector: no services in compose"

    for svc_name, svc in services.items():
        profiles = svc.get("profiles", [])
        logger.info("[IMP:8][compose-profiles][%s] profiles=%s", svc_name, profiles)
        assert "log-collector" in profiles, f"Service {svc_name} missing profile 'log-collector', got: {profiles}"

    logger.critical("[IMP:9][compose-profiles][log-collector] ✅ все сервисы имеют profiles [log-collector]")


# 🧪 TRAP[TEST] · 2026-08-22 · gate/module-contract · Регресс: x-logging/container_name/healthcheck
# · Scenario: канон test_gate_compose_base_contract — x-logging anchor, container_name, healthcheck
# · Last fail: N/A (новый модуль 010 T3.1)
# · Remove if: compose-контракт модуля меняется кардинально
@ldd_trajectory
def test_compose_base_contract(caplog) -> None:
    """compose: x-logging anchor + container_name alloy + healthcheck у каждого long-running сервиса."""
    caplog.set_level(logging.INFO)
    data = _compose()
    raw = _COMPOSE.read_text(encoding="utf-8")
    assert "x-logging" in data, "docker-compose.base.yml missing x-logging anchor"
    assert "x-logging: &default-logging" in raw, "x-logging без anchor &default-logging (DD1 канон)"

    services = data.get("services", {})
    container_names = [svc.get("container_name", "") for svc in services.values() if isinstance(svc, dict)]
    assert "alloy" in container_names, f"log-collector primary container_name 'alloy' not found in {container_names}"

    for svc_name, svc in services.items():
        if svc.get("restart") == "no":  # init-контейнеры вне политики
            continue
        assert "healthcheck" in svc, f"{svc_name}: missing healthcheck block"

    logger.critical("[IMP:9][compose-base-contract] ✅ x-logging + alloy + healthcheck — OK")


# 🧪 TRAP[TEST] · 2026-08-22 · gate/healthcheck-drift · Регресс: healthcheck.sh контракт D5
# · Scenario: env-параметризация + source lib + check_docker_health (W10 T10.12/D5 канон)
# · Last fail: N/A (новый модуль 010 T3.1)
# · Remove if: healthcheck-контракт заменён
@ldd_trajectory
def test_healthcheck_contract(caplog) -> None:
    """healthcheck.sh: executable, source lib/healthcheck.sh, check_docker_health, env-имя, БЕЗ loki /ready."""
    caplog.set_level(logging.INFO)
    assert _HEALTHCHECK.exists(), f"healthcheck.sh not found: {_HEALTHCHECK}"
    assert os.access(_HEALTHCHECK, os.X_OK), "healthcheck.sh must be executable"

    content = _read_healthcheck()
    assert "source" in content and "lib/healthcheck.sh" in content, (
        "healthcheck.sh must source ../../lib/healthcheck.sh"
    )
    assert "check_docker_health" in content, "healthcheck.sh must use check_docker_health (канон D5)"
    assert "ALLOY_CONTAINER_NAME" in content, "имя контейнера env-параметризовано (${ALLOY_CONTAINER_NAME:-alloy})"
    assert "exit 0" in content, "healthcheck.sh missing 'exit 0' (healthy path)"

    # 010 T3.1: healthcheck БЕЗ локального loki /ready — никакого HTTP-проба на loki.
    # Проверяем структурно: единственный контейнер в массиве — alloy; deep-режим без check_http.
    assert "check_http" not in content, (
        "log-collector healthcheck НЕ должен делать HTTP-проб (БЕЗ loki /ready — 010 T3.1: alloy здоров БЕЗ loki)"
    )
    assert "${LOKI_PORT}" not in content, "log-collector healthcheck не должен знать loki-порт (010 T3.1)"

    logger.critical(
        "[IMP:9][healthcheck][log-collector] ✅ executable, source lib, check_docker_health, no loki /ready"
    )


# 🧪 TRAP[TEST] · 2026-08-22 · gate/module-yaml · Регресс: module.yaml контракт D4/D5
# · Scenario: name/install_type/depends_on/spool; depends_on: [logging] — data-plane (010 §2.2 правило 8)
# · Last fail: N/A (новый модуль 010 T3.1)
# · Remove if: module.yaml контракт меняется кардинально
@ldd_trajectory
def test_module_yaml_contract(caplog) -> None:
    """module.yaml: name/install_type/depends_on [logging]/spool alloy-data."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_MODULE_YAML.read_text(encoding="utf-8"))

    assert data["name"] == "log-collector", f"name={data.get('name')}, expected log-collector"
    assert data["install_type"] == "docker", f"install_type={data.get('install_type')}, expected docker"
    assert "logging" in data.get("depends_on", []), (
        "depends_on должен включать logging (010 §2.2 правило 8: log-collector требует logging — иначе оба off)"
    )
    # DR-M3 fix: alloy-data — docker-managed volume ⇒ spool_dir: none (канон U-67, прецедент minio)
    assert data.get("spool_dir") == "none"
    assert data.get("spool_volume") == "alloy-data"
    assert data.get("interfaces", []) == ["healthcheck"]

    logger.critical("[IMP:9][module-yaml][log-collector] ✅ contract OK: name/depends_on[logging]/spool alloy-data")


# ═══════════════════════════════════════════════════════════════════════════
# СПЛИТ-ТРЕБОВАНИЯ (DevPlan 010 T3.1 + T2.0b)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-22 · 010 T3.1 · Регресс: endpoint захардкожен http://loki:3100
# · Scenario: config.alloy loki.write url — обязан идти через ${LOKI_URL} (env-подстановка expand-env)
# · Last fail: config.alloy:31 → http://loki:3100 (захардкожен, кросс-нодовый push ломался)
# · Remove if: endpoint-параметризация заменена другим механизмом
@ldd_trajectory
def test_alloy_endpoint_parameterized(caplog) -> None:
    """config.alloy: ${LOKI_URL} endpoint (не захардкоженный http://loki:3100)."""
    caplog.set_level(logging.INFO)
    text = _read_alloy()
    assert "${LOKI_URL}/loki/api/v1/push" in text, "config.alloy endpoint обязан содержать ${LOKI_URL} (010 T3.1)"
    assert "http://loki:3100/loki/api/v1/push" not in text, (
        "config.alloy endpoint ЗАХАРКОЖЕН — параметризуй через ${LOKI_URL} (010 T3.1)"
    )
    logger.critical("[IMP:9][alloy-endpoint] ✅ loki.write url = ${LOKI_URL}/loki/api/v1/push — OK")


# 🧪 TRAP[TEST] · 2026-08-22 · 010 T2.0b · Регресс: tenant-изоляция отсутствует
# · Scenario: loki.write несёт header X-Scope-OrgID: ${LOKI_TENANT} (tenant = имя контекста, дефолт platform)
# · Last fail: auth_enabled:false — любой пир читал/писал логи контекста
# · Remove if: tenant-механизм Loki заменён
@ldd_trajectory
def test_alloy_tenant_header_present(caplog) -> None:
    """config.alloy: tenant header X-Scope-OrgID из ${LOKI_TENANT} (T2.0b)."""
    caplog.set_level(logging.INFO)
    text = _read_alloy()
    assert "X-Scope-OrgID" in text, "tenant header X-Scope-OrgID обязателен (T2.0b)"
    assert "${LOKI_TENANT}" in text, "header обязан брать tenant из ${LOKI_TENANT} (T2.0b)"
    logger.critical("[IMP:9][alloy-tenant] ✅ X-Scope-OrgID: ${LOKI_TENANT} — OK")


# 🧪 TRAP[TEST] · 2026-08-22 · 010 T3.1 · Регресс: depends_on loki возвращён
# · Scenario: compose alloy-сервис БЕЗ depends_on (WAL буферизует, self-heal)
# · Last fail: depends_on loki condition: service_healthy (ложный ready-гейт коллектора)
# · Remove if: ordering-зависимость осознанно возвращена (архитектурное решение)
@ldd_trajectory
def test_compose_no_depends_on_loki(caplog) -> None:
    """compose: alloy-сервис НЕ содержит depends_on (010 T3.1 — WAL self-heal)."""
    caplog.set_level(logging.INFO)
    data = _compose()
    for svc_name, svc in data.get("services", {}).items():
        depends = svc.get("depends_on", {})
        assert not depends, (
            f"{svc_name}: depends_on УДАЛЁН (010 T3.1) — WAL буферизует несостоявшиеся пуш-батчи, "
            f"self-heal при появлении loki; got: {depends}"
        )
    logger.critical("[IMP:9][no-depends-on] ✅ alloy без depends_on loki (WAL self-heal) — OK")


# 🧪 TRAP[TEST] · 2026-08-22 · 010 T2.0b · Регресс: auth_enabled:false возвращён
# · Scenario: logging/loki-config.yml auth_enabled: true (tenant-изоляция по X-Scope-OrgID)
# · Last fail: auth_enabled:false — push+read на одном порту без изоляции
# · Remove if: Loki-аутентификация переезжает на другой механизм (multitenancy)
@ldd_trajectory
def test_loki_auth_enabled(caplog) -> None:
    """logging/loki-config.yml: auth_enabled: true (T2.0b — tenant-изоляция)."""
    caplog.set_level(logging.INFO)
    assert _LOKI_CONFIG.exists(), f"loki-config.yml not found: {_LOKI_CONFIG}"
    data = yaml.safe_load(_LOKI_CONFIG.read_text(encoding="utf-8"))
    assert data.get("auth_enabled") is True, (
        f"loki-config.yml auth_enabled={data.get('auth_enabled')}, expected True (DevPlan 010 T2.0b)"
    )
    logger.critical("[IMP:9][loki-auth] ✅ auth_enabled: true — tenant-изоляция по X-Scope-OrgID — OK")
