"""
# GREP_SUMMARY: test_node_yaml_consumers, consumers, importability, NodeYaml, unchanged, H3, 21-consumers
# STRUCTURE: ▶ список ~21 файла-потребителя NodeYaml → ◇ importlib.import_module каждый → ◇ assert NodeYaml доступен → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests (DevPlan 119 H3): ~21 прямой потребитель NodeYaml.get() продолжает
##           импортировать NodeYaml корректно после декомпозиции монолита в пакет (H1).
## @scope    Проверяет импортируемость всех файлов-потребителей `core.internal.shared.node_yaml`.
##           verify-then-delete (AC-H3.1): импорты НЕ меняются — агрегатор сохраняет API.
## @invariants
##   - Native imports (importlib) — никаких subprocess для бизнес-логики
##   - Полный список потребителей из grep-скана `grep -rln "NodeYaml" core/ --include="*.py"`
##   - Каждый потребитель импортируется без ошибок (NodeYaml re-export'ится из пакета)
## @changes 2026-08-03 · DevPlan 119 H1/H3 — создан (verify-then-delete, AC-H3.1)
# endregion MODULE_CONTRACT
"""

import importlib
import logging
import sys
from pathlib import Path

from core.internal.shared.node_yaml import NodeYaml
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Все ~21 файл-потребитель NodeYaml в core/ (grep-скан `grep -rln "NodeYaml" core/ --include="*.py"`,
# исключая сам node_yaml/ пакет и node_yaml_cli — он re-export'ится через __getattr__).
# Кандидаты с тяжёлыми зависимостями (docker/boto3) помечены: импортируются только если
# их зависимости доступны — verify-then-delete фокусируется на NodeYaml-импорте.
CONSUMERS = [
    "core.internal.bootstrap.converge.projects",
    "core.internal.bootstrap.converge.vhosts",
    "core.internal.bootstrap.converge.volumes",
    "core.internal.bootstrap.deploy.context_deployer",
    "core.internal.bootstrap.deploy.context_overlay",
    "core.internal.bootstrap.deploy.deploy_orchestrator",
    "core.internal.bootstrap.deploy.secrets_validator",
    "core.internal.bootstrap.lifecycle.helpers.reporting",
    "core.internal.bootstrap.lifecycle.helpers.validation",
    "core.internal.bootstrap.overlay_deliverer",
    "core.internal.bootstrap.preflight",
    "core.internal.bootstrap.remote_executor",
    "core.internal.bootstrap.s3_ssl_cache",
    "core.internal.healthcheck.metrics.cert_collector",
    "core.internal.healthcheck.metrics.project_collector",
    "core.internal.healthcheck.platform_export_metrics",
    "core.internal.reconciler_projects",
    "core.internal.scaffold.context_registry",
    "core.internal.scaffold.project_adopter",
    "core.internal.scaffold.project_lister",
    "core.internal.scaffold.project_remover",
    "core.internal.scaffold.scaffold_helpers",
    "core.internal.scaffold.vhost_renderer",
    "core.internal.shared.node_yaml_cli",
    "core.internal.shared.project_registry",
    "core.internal.shared.project_yaml",
    "core.internal.shared.schema_validator",
    "core.internal.verify.domain_verifier",
    "core.modules.postgres.hooks.on_project_deploy",
]


# 🧪 TRAP[TEST] · Regression (H3) · все потребители NodeYaml импортируются после декомпозиции
# · Scenario: монолит node_yaml.py → пакет node_yaml/ (H1); все ~28 потребителей должны
# ·   импортироваться без изменений (verify-then-delete, AC-H3.1)
# · Last fail: до H1 — потребители импортировали node_yaml.py (файл)
# · Remove if: consumer-импорты намеренно мигрируются на новый API
@ldd_trajectory
def test_all_consumers_unchanged(caplog):
    """H3: все потребители NodeYaml импортируют агрегатор корректно (verify-then-delete)."""
    # status-page — модуль с дефисом в имени, подключается module-specific path (tests/AGENTS.md).
    # xdist-инвариант 4 (DevPlan 139 W2): относительный sys.path → абсолютный Path(__file__)-based
    # (относительный путь зависел от CWD воркера — флак при -n auto).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "modules" / "status-page"))

    failures: list[str] = []
    imported_ok = 0
    for mod_name in CONSUMERS:
        try:
            mod = importlib.import_module(mod_name)
            # Каждый модуль обязан иметь (прямо или косвенно) NodeYaml-импорт.
            # Прямая проверка атрибута невозможна (модули импортируют from-import),
            # поэтому проверяем, что модуль загрузился без ImportError.
            _ = mod
            imported_ok += 1
            logger.info("[IMP:8][consumers] OK: %s", mod_name)
        except ImportError as e:
            failures.append(f"{mod_name}: {e}")
            logger.error("[IMP:9][consumers] FAIL: %s → %s", mod_name, e)

    assert not failures, "H3 FAIL: потребители не импортируются после декомпозиции:\n" + "\n".join(failures)
    assert imported_ok >= 21, f"H3 FAIL: ожидалось ≥21 потребителя, импортировано {imported_ok}"
    logger.critical(
        "[IMP:9][consumers] PASS: %d потребителей NodeYaml импортируются без изменений (AC-H3.1)", imported_ok
    )


# 🧪 TRAP[TEST] · Negative (R5, H3) · NodeYaml доступен по каноническому пути
# · Scenario: from core.internal.shared.node_yaml import NodeYaml — канонический импорт (не сломан)
# · Last fail: до H1 — from node_yaml.py import NodeYaml (файл)
# · Remove if: путь импорта NodeYaml намеренно меняется
@ldd_trajectory
def test_node_yaml_canonical_import_negative(caplog):
    """R5 negative (H3): канонический импорт NodeYaml работает из пакета (AC-H3.1)."""
    # Уже импортирован наверху — повторная проверка as-import + типов
    from core.internal.shared.node_yaml import NodeYaml as NY

    assert NY is NodeYaml
    assert isinstance(NodeYaml, type)
    logger.critical("[IMP:9][consumers] canonical import NodeYaml — OK")
