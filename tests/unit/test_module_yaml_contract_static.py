# GREP_SUMMARY: test-module-yaml-contract-static module-yaml contract infra-metrics logging log-collector monitoring parametrized D4
# STRUCTURE: ▶ _MODULE_YAML_EXPECTATIONS (4 домена) → ◇ parametrize по domain → ◇ yaml.safe_load module.yaml → ◇ assert name/install_type/domain-fields → ⎋ IMP:9 PASS
# region MODULE_CONTRACT
## @purpose  Параметризованный static-контракт module.yaml для observability-модулей.
##           Консолидировано (DevPlan 139 W3 T2, 7→3): test_module_yaml_contract из
##           test_infra_metrics_static.py / test_logging_static.py / test_monitoring_static.py
##           (3 почти идентичные вариации) → 1 параметризованный тест по домену.
## @scope    Только static-вариации module.yaml контракта (name/install_type + доменные поля).
##           НЕ трогает: tests/gates/test_gate_module_yaml_contract.py (gate-контракт),
##           tests/gates/test_gate_module_yaml_contract_d5_negative.py + test_gate_module_schema_d4_negative.py
##           (negative), tests/test_validate_module_yaml.py (unit-тесты валидатора).
## @invariants
##   - Параметризация по домену (id = module_dir) — каждому домену свои ожидаемые поля
##   - Проверяются ТОЛЬКО поля, объявленные в expectation (нет жёсткого общего шаблона)
##   - @pytest.mark.static_audit — без Docker
##   - IMP:9 лог в каждом параметризованном прогоне (LDD)
## @rationale 3 идентичные проверки в разных файлах — дубль без добавочной обнаруживаемости
##            (gate-контракт test_all_modules_have_required_fields уже покрывает общие поля).
##            Параметризация сохраняет доменную специфику (env_requires/spool) при 3→1.
## @changes  2026-08-05 | DevPlan 139 W3 T2 — создан (консолидация 3 static вариаций)
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent / "../.."
_MODULES_DIR = Path(_PROJECT_ROOT) / "core" / "modules"

# Ожидания per-domain: ключи = поля module.yaml, проверяемые дополнительно к name/install_type.
# Консолидировано из test_infra_metrics_static.py (env_requires), test_logging_static.py (spool),
# test_monitoring_static.py (env_requires) — значения идентичны исходным.
_MODULE_YAML_EXPECTATIONS: list[dict] = [
    {
        "id": "node-metrics",
        "module_dir": "node-metrics",
        "expected": {
            "name": "node-metrics",
            "install_type": "docker",
            "env_requires": [],
        },
    },
    {
        "id": "service-exporters",
        "module_dir": "service-exporters",
        "expected": {
            "name": "service-exporters",
            "install_type": "docker",
            "env_requires": ["POSTGRES_USER", "POSTGRES_PASSWORD", "REDIS_PASSWORD"],
        },
    },
    {
        "id": "logging",
        "module_dir": "logging",
        "expected": {
            "name": "logging",
            "install_type": "docker",
            "spool_dir": "/var/lib/platform/loki-data",
            "spool_volume": "loki-data",
        },
    },
    {
        "id": "log-collector",
        "module_dir": "log-collector",
        "expected": {
            "name": "log-collector",
            "install_type": "docker",
            "spool_dir": "/var/lib/platform/alloy-data",
            "spool_volume": "alloy-data",
        },
    },
    {
        "id": "monitoring",
        "module_dir": "monitoring",
        "expected": {
            "name": "monitoring",
            "install_type": "docker",
            "env_requires": ["GF_SECURITY_ADMIN_PASSWORD", "LITELLM_MASTER_KEY"],
        },
    },
]


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize(
    "expectation",
    _MODULE_YAML_EXPECTATIONS,
    ids=lambda e: e["id"],
)
def test_module_yaml_contract(expectation: dict, caplog) -> None:
    """module.yaml имеет name/install_type + доменные поля (D4 контракт).

    ## @purpose — Параметризованная валидация module.yaml для observability-модулей:
    ##            обязательные D4 поля (name, install_type) + доменная специфика
    ##            (env_requires для infra-metrics/monitoring, spool_dir/spool_volume
    ##            для logging). Консолидация 3 static-вариаций (DevPlan 139 W3 T2).
    ## @io — ⇥ expectation: dict {id, module_dir, expected} → ⚡ yaml.safe_load → ⎋ None
    ## @complexity — O(1) — single YAML parse
    """
    module_yaml = Path(_MODULES_DIR) / expectation["module_dir"] / "module.yaml"
    assert pathlib.Path(module_yaml).exists(), f"module.yaml not found: {module_yaml}"

    with pathlib.Path(module_yaml).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    expected = expectation["expected"]
    for key, expected_value in expected.items():
        actual = data.get(key)
        logger.info("[IMP:8][module-yaml][%s] %s=%s", expectation["id"], key, actual)
        assert actual == expected_value, (
            f"module.yaml {expectation['module_dir']} {key}={actual!r}, expected {expected_value!r}"
        )

    logger.critical("[IMP:9][module-yaml][%s] ✅ module.yaml contract OK: name=%s", expectation["id"], data["name"])
