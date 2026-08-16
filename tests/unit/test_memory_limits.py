# GREP_SUMMARY: test-memory-limits cadvisor loki clickhouse memory-limit module-yaml-sync compose-base resources
# STRUCTURE: ┌parse memory strings┐ → ◇ compose limits detector (cadvisor/loki/clickhouse канон) → ◇ module.yaml sync detector (3 модуля) → ◇ R5 negatives (3 исходных лимита) → ┘
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 144 W3 (D2) memory limits канона:
##           docker-compose.base.yml limits (cadvisor ≥256M, loki ≥512M, clickhouse ≥2G) +
##           module.yaml resources.limits.memory sync по сумме limits сервисов модуля.
## @scope    No Docker — read-only yaml-парс реальных compose/module.yaml; R5 negative через
##           tmp_path-фикстуры (реальные файлы НЕ модифицируются).
## @invariants
##   - Компоуз-лимиты читаются из реальных core/modules/*/docker-compose.base.yml (read-only)
##   - module.yaml resources.limits.memory ≥ суммы limits всех сервисов соответствующего compose
##     (канон «синхронизировано с base.yml») — детектор рассинхрона
##   - R5 negative: исходные лимиты (cadvisor 128M / loki 256M / clickhouse 1G), при которых
##     HighMemory-алерты были firing (127.3/128MiB = 99.4%, 216/256MiB = 91.3%, 91.5% при 1G)
## @rationale DevPlan 144 W3 §TEST_SPEC — детектор лимитов + sync; R5 negative ловит регрессию
##            отката лимитов к исходным значениям.
## @changes  2026-08-09 · DevPlan 144 W3 — created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from tests._conftest.r1 import r1_delegates

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_COMPOSE_FILES = {
    "infra-metrics": _REPO_ROOT / "core" / "modules" / "infra-metrics" / "docker-compose.base.yml",
    "logging": _REPO_ROOT / "core" / "modules" / "logging" / "docker-compose.base.yml",
    "clickhouse": _REPO_ROOT / "core" / "modules" / "clickhouse" / "docker-compose.base.yml",
}

_MODULE_YAMLS = {
    "infra-metrics": _REPO_ROOT / "core" / "modules" / "infra-metrics" / "module.yaml",
    "logging": _REPO_ROOT / "core" / "modules" / "logging" / "module.yaml",
    "clickhouse": _REPO_ROOT / "core" / "modules" / "clickhouse" / "module.yaml",
}

# Канон DevPlan 144 W3: минимальный лимит каждого ключевого сервиса (bytes)
# cadvisor 512M (деплой-верификация 2026-08-09: 250MiB при 256M = 98% — поднят до 512M)
_CANON_MIN_LIMITS = {
    "cadvisor": 512 * 1024 * 1024,  # 512M
    "loki": 512 * 1024 * 1024,  # 512M
    "clickhouse": 2 * 1024 * 1024 * 1024,  # 2G
}

_MEMORY_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_memory(value: object) -> int:
    """'128M'/'1G'/int → bytes (int)."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().upper()
    assert s, f"memory value пустой: {value!r}"
    if s[-1] in _MEMORY_UNITS:
        return round(float(s[:-1]) * _MEMORY_UNITS[s[-1]])
    return int(s)


def _compose_data(module: str) -> dict:
    """YAML-парс docker-compose.base.yml модуля (read-only, без docker)."""
    return yaml.safe_load(_COMPOSE_FILES[module].read_text(encoding="utf-8"))


def _service_memory_limit(compose: dict, service: str) -> int:
    """deploy.resources.limits.memory сервиса → bytes (AssertionError если отсутствует)."""
    deploy = compose["services"][service].get("deploy", {})
    limits = deploy.get("resources", {}).get("limits", {})
    assert "memory" in limits, f"service {service}: deploy.resources.limits.memory отсутствует"
    return _parse_memory(limits["memory"])


def _assert_memory_limit(compose: dict, service: str, min_bytes: int) -> None:
    """144 W3 детектор: лимит сервиса ≥ канона (DevPlan 144 W3)."""
    actual = _service_memory_limit(compose, service)
    assert actual >= min_bytes, (
        f"144 W3 FAIL: {service} limits.memory = {actual} bytes "
        f"({actual / (1024**2):.0f}MiB) < канона {min_bytes / (1024**2):.0f}MiB"
    )


def _assert_module_yaml_sync(module_yaml_path: Path, compose: dict) -> None:
    """144 W3 детектор: module.yaml resources.limits.memory ≥ суммы limits всех сервисов compose.

    Канон «синхронизировано с base.yml»: module.yaml — агрегат по модулю; рассинхрон
    (лимит меньше суммы) = риски OOM-заявки при оркестрации/статус-рендеринге.
    """
    data = yaml.safe_load(module_yaml_path.read_text(encoding="utf-8"))
    module_limit = _parse_memory(data["resources"]["limits"]["memory"])
    service_sum = sum(_service_memory_limit(compose, svc) for svc in compose["services"])
    assert module_limit >= service_sum, (
        f"144 W3 FAIL: {module_yaml_path.name} resources.limits.memory = {module_limit} bytes "
        f"< суммы compose-лимитов {service_sum} bytes ({service_sum / (1024**2):.0f}MiB)"
    )


# 🧪 TRAP[TEST] · Regression · Scenario: compose-лимиты ≥ канона (144 W3 D2)
# · Expect: cadvisor ≥512M, loki ≥512M, clickhouse ≥2G (docker-compose.base.yml)
# · Last fail: cadvisor 128M (127.3/128MiB = 99.4% — HighMemory firing), loki 256M
# ·   (216/256MiB = 91.3%), clickhouse 1G (cAdvisor usage 91.5%); cadvisor 256M после
# ·   деплой-верификации 2026-08-09 (250MiB = 98% — лимит поднят до 512M)
# · Remove if: лимиты снижаются ниже канона намеренно (архитектурное решение)
# 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
#   _assert_memory_limit (assert + AssertionError при нарушении канона).
@r1_delegates
def test_compose_limits_canon(caplog) -> None:
    """144 W3: лимиты ключевых сервисов ≥ канона."""
    caplog.set_level(logging.INFO)
    service_to_module = {
        "cadvisor": "infra-metrics",
        "loki": "logging",
        "clickhouse": "clickhouse",
    }
    for service, min_bytes in _CANON_MIN_LIMITS.items():
        _assert_memory_limit(_compose_data(service_to_module[service]), service, min_bytes)
    logger.info("[IMP:9][test_memory_limits] compose limits >= canon (cadvisor 512M, loki 512M, clickhouse 2G) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: module.yaml sync по сумме compose-лимитов (144 W3)
# · Expect: resources.limits.memory ≥ суммы limits всех сервисов модуля (все 3 модуля)
# · Last fail: clickhouse module.yaml 512M vs compose 1G (рассинхрон);
# ·   infra-metrics 224M vs сумма 288M (рассинхрон до 144)
# · Remove if: канон «синхронизировано с base.yml» меняется
# 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
#   _assert_module_yaml_sync (assert + AssertionError при рассинхроне).
@r1_delegates
def test_module_yaml_sync_all(caplog) -> None:
    """144 W3: module.yaml resources синхронизирован с compose (по каждому из 3 модулей)."""
    caplog.set_level(logging.INFO)
    for module in ("infra-metrics", "logging", "clickhouse"):
        _assert_module_yaml_sync(_MODULE_YAMLS[module], _compose_data(module))
    logger.info("[IMP:9][test_memory_limits] module.yaml resources sync (3 modules) PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · cadvisor 128M/256M — DevPlan 144 W3 (D2)
# · Last fail: исходный лимит 128M — факт. потребление 127.3MiB (99.4%) → HighMemory firing;
# ·   256M после деплой-верификации 2026-08-09 (250MiB = 98% — лимит поднят до 512M)
# · Remove if: канон cadvisor ≥512M меняется
def test_cadvisor_limit_negative_removed(tmp_path: Path) -> None:
    """R5 negative (144 W3): лимит 128M — исходный вход, поймавший баг — детектор обязан упасть."""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "services:\n  cadvisor:\n    deploy:\n      resources:\n        limits:\n          memory: 128M\n",
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError):
        _assert_memory_limit(compose, "cadvisor", _CANON_MIN_LIMITS["cadvisor"])
    # 256M тоже ниже канона 512M (деплой-верификация: 250MiB = 98% при 256M)
    compose_file.write_text(
        "services:\n  cadvisor:\n    deploy:\n      resources:\n        limits:\n          memory: 256M\n",
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError):
        _assert_memory_limit(compose, "cadvisor", _CANON_MIN_LIMITS["cadvisor"])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · loki 256M — DevPlan 144 W3 (D2)
# · Last fail: исходный лимит 256M — факт. потребление 216MiB (91.3%) → HighMemory firing
# · Remove if: канон loki ≥512M меняется
def test_loki_limit_negative_removed(tmp_path: Path) -> None:
    """R5 negative (144 W3): лимит 256M — исходный вход, поймавший баг — детектор обязан упасть."""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "services:\n  loki:\n    deploy:\n      resources:\n        limits:\n          memory: 256M\n",
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError):
        _assert_memory_limit(compose, "loki", _CANON_MIN_LIMITS["loki"])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · clickhouse 1G — DevPlan 144 W3 (D2)
# · Last fail: исходный лимит 1G — cAdvisor usage 91.5% (вкл. page cache) → HighMemory firing
# · Remove if: канон clickhouse ≥2G меняется
def test_clickhouse_limit_negative_removed(tmp_path: Path) -> None:
    """R5 negative (144 W3): лимит 1G — исходный вход, поймавший баг — детектор обязан упасть."""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "services:\n  clickhouse:\n    deploy:\n      resources:\n        limits:\n          memory: 1G\n",
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError):
        _assert_memory_limit(compose, "clickhouse", _CANON_MIN_LIMITS["clickhouse"])
