# GREP_SUMMARY: test-gen-env-platform-multinode cross-node emission placement service-host target-node dsn-url-substitution devplan-010-t2-1
# STRUCTURE: ▶ platform-env data + load_placement(s3) → ◇ consumer_node → ⊕ PLATFORM_*_HOST/DSN/URL → ⎋ asserts (remote/local/no-op)
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T2.1 (TEST_SPEC): cross-node эмиссия .env.platform — сервис на чужой
##           ноде → HOST=<node>.host (+подстановка в DSN/URL шаблонах); своя нода / no placement
##           → Docker DNS alias (байт-идентично легаси).
## @scope    core/internal/scaffold/gen_env_platform.generate / resolve_placement_for_project
## @invariants
##   - Native imports only (без subprocess); fixture s3.yaml через Path(__file__) relative
##   - LDD telemetry: caplog IMP:9 в successful-сценариях (Anti-Illusion Rule)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from core.internal.scaffold.gen_env_platform import generate
from core.internal.shared.placement import load_placement

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "placement"

_PLATFORM_ENV: dict[str, Any] = {
    "profiles": ["postgres", "redis", "minio", "clickhouse", "litellm", "langfuse", "nginx"],
    "provides": {
        "postgres": {
            "host": "pgbouncer",
            "port": 6432,
            "dsn_template": "postgresql://${NAME}_user:***@pgbouncer:6432/${NAME}_db",
            "networks": ["shared-db-net"],
        },
        "redis": {"host": "redis", "port": 6379, "url_template": "redis://redis:6379/0"},
        "litellm": {"host": "litellm", "port": 4000, "url_template": "http://litellm:4000"},
    },
}


def _env_dict(lines: list[str]) -> dict[str, str]:
    """Parse emitted lines 'KEY=V' → dict (комментарии/пустые пропускаются)."""
    out: dict[str, str] = {}
    for line in lines:
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · TEST_SPEC строка 421 (test_remote_postgres_host_emitted)
# · Scenario: S3 fixture, проект на apps-ноде → PLATFORM_POSTGRES_HOST=10.8.0.11 в .env.platform;
# ·   DSN-шаблон несёт host data-ноды (@10.8.0.11:6432), НЕ Docker-DNS pgbouncer
# · Last fail: N/A — T2.1 отсутствовал в реализации eb97ef6 (emission без placement)
# · Remove if: cross-node .env.platform адресация перестанет быть каноном DevPlan 010 §6.1
def test_remote_postgres_host_emitted(caplog: pytest.LogCaptureFixture) -> None:
    """S3: проект на apps-1 получает PLATFORM_POSTGRES_HOST=10.8.0.11 и DSN с host data-ноды."""
    caplog.set_level(logging.DEBUG)
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None, "s3.yaml fixture must load as Placement"

    lines = generate(
        dict(_PLATFORM_ENV), domain="tronyx.ru", project_name="my-app", placement=placement, consumer_node="apps-1"
    )
    env = _env_dict(lines)

    # Acceptance W2: `.env.platform` проекта на apps-1 содержит PLATFORM_POSTGRES_HOST=10.8.0.11
    assert env["PLATFORM_POSTGRES_HOST"] == "10.8.0.11", f"cross-node host обязателен: {env}"
    assert env["PLATFORM_POSTGRES_DSN"] == "postgresql://my-app_user:***@10.8.0.11:6432/my-app_db", (
        f"DSN обязан нести host data-ноды: {env['PLATFORM_POSTGRES_DSN']}"
    )
    # Redis URL тоже кросс-нодовый (redis на data-1)
    assert env["PLATFORM_REDIS_URL"] == "redis://10.8.0.11:6379/0"
    # litellm на agent-1 → фасадный host agent-ноды
    assert env["PLATFORM_LITELLM_URL"] == "http://10.8.0.12:4000"
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    assert any("[IMP:8]" in r.message or "[IMP:7]" in r.message for r in caplog.records), (
        "service_host remote-resolution обязан логироваться (LDD)"
    )


# 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · co-located потребитель сохраняет Docker DNS alias
# · Scenario: тот же S3, но проект на data-1 (postgres локален) → HOST=pgbouncer, DSN без подстановок
# · Last fail: N/A
# · Remove if: co-location перестанет оставлять Docker DNS алиасы (§6.1 T2.1)
def test_local_consumer_keeps_docker_alias(caplog: pytest.LogCaptureFixture) -> None:
    """S3, consumer=data-1: postgres локален → PLATFORM_POSTGRES_HOST=pgbouncer (Docker DNS)."""
    caplog.set_level(logging.DEBUG)
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None

    lines = generate(
        dict(_PLATFORM_ENV), domain="tronyx.ru", project_name="my-app", placement=placement, consumer_node="data-1"
    )
    env = _env_dict(lines)

    assert env["PLATFORM_POSTGRES_HOST"] == "pgbouncer"
    assert "@pgbouncer:6432/" in env["PLATFORM_POSTGRES_DSN"]
    # redis локален на data-1 → дефолтный alias
    assert env["PLATFORM_REDIS_URL"] == "redis://redis:6379/0"


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · single-node no-op байт-идентичен легаси (§1.1 плана)
# · Scenario: placement=None / consumer_node="" → эмиссия совпадает с legacy-вызовом дословно
#   (строка Generated-таймстампа исключается — она невоспроизводима по определению)
# · Last fail: N/A
# · Remove if: single-node больше не является каноном поведения по умолчанию
def test_single_node_noop_byte_parity() -> None:
    """Без placement эмиссия байт-идентична legacy-пути (инвариант 1 DevPlan 010)."""

    def _payload(lines: list[str]) -> list[str]:
        return [line for line in lines if not line.startswith("# Generated:")]

    legacy = generate(dict(_PLATFORM_ENV), domain="tronyx.ru", project_name="my-app")
    noop = generate(dict(_PLATFORM_ENV), domain="tronyx.ru", project_name="my-app", placement=None, consumer_node="")
    assert _payload(legacy) == _payload(noop), "single-node no-op обязан быть байт-идентичным легаси-эмиссии"
    assert "PLATFORM_POSTGRES_HOST=pgbouncer" in legacy


# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · https://${DOMAIN} URL не подвергается host-подстановке
# · Scenario: url_template БЕЗ host-токена ("https://${DOMAIN}") остаётся неизменным при remote
# · Last fail: N/A — guard против regex-подстановки в ${DOMAIN}-плейсхолдер
# · Remove if: шаблоны URL без host-токена исчезнут из platform-env.yaml
def test_domain_only_url_untouched() -> None:
    """URL без host-токена (${DOMAIN}-форма) не мутируется кросс-нодовой подстановкой."""
    data: dict[str, Any] = {
        "profiles": ["nginx"],
        "provides": {"nginx": {"host": "nginx-proxy", "port": 443, "url_template": "https://${DOMAIN}"}},
    }
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None
    env = _env_dict(generate(data, domain="tronyx.ru", project_name="", placement=placement, consumer_node="data-1"))
    assert env["PLATFORM_NGINX_URL"] == "https://tronyx.ru"
