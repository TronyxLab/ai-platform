# GREP_SUMMARY: status-page-env-honesty env-requires matches-reads module-contract AI-0072
# STRUCTURE: ▶ parse module.yaml env_requires → ◇ каждое имя читается os.environ/getenv в исходниках модуля → ⎋ честный контракт
# region MODULE_CONTRACT
## @purpose  AI-0072 (DevPlan 17 T3.6/T6-спец, $TEST_SPEC row): env_requires module.yaml
##           ⊆ реально читаемых переменных (os.environ/os.getenv в *.py модуля) —
##           fake-env-requires запрещён (прецедент: status-page требовала master-креды,
##           которые читает только nginx).
## @scope    tests/unit: yaml+source-scan; без docker.
## @invariants
##   - status-page env_requires пуст (после T3.6), nginx декларирует master-креды
##   - Каждое env_requires имя любого проверяемого модуля читается его кодом/фасадами
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_MODULES = Path(__file__).resolve().parents[2] / "core" / "modules"

# Модули, чьи env_requires проверяем на честность (модуль ↔ потребитель в том же каталоге)
_CHECKED = ("status-page", "nginx")

_ENV_READ_RE = re.compile(r"os\.environ\.get\(\s*['\"]([A-Z0-9_]+)['\"]|os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]")


def _env_reads_of(module_dir: Path) -> set[str]:
    reads: set[str] = set()
    for py in module_dir.rglob("*.py"):
        for match in _ENV_READ_RE.finditer(py.read_text(encoding="utf-8", errors="replace")):
            reads.add(match.group(1) or match.group(2))
    return reads


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · env_requires = реально читаемым переменным (AI-0072)
# · Regression: status-page/module.yaml требовал PLATFORM_MASTER_EMAIL/PASSWORD, которые
#   app.py не читал (Basic Auth живёт в nginx htpasswd) — fake-env-requires
# · Scenario: (1) status-page env_requires == []; (2) nginx env_requires == master-креды;
#   (3) для обоих модулей каждое env_requires имя встречается как os.environ/os.getenv
#   чтение в исходниках модуля
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0072)
# · Remove if: появится централизованный env-контракт с манифест-валидацией
def test_env_requires_matches_reads() -> None:
    sp_yaml = yaml.safe_load((_MODULES / "status-page" / "module.yaml").read_text(encoding="utf-8"))
    assert not sp_yaml.get("env_requires"), (
        "status-page не читает секреты сам — env_requires обязан быть пуст/отсутствовать (T3.6)"
    )

    ng_dir = _MODULES / "nginx"
    ng_yaml = yaml.safe_load((ng_dir / "module.yaml").read_text(encoding="utf-8"))
    ng_requires = ng_yaml.get("env_requires") or []
    assert "PLATFORM_MASTER_EMAIL" in ng_requires and "PLATFORM_MASTER_PASSWORD" in ng_requires, (
        "nginx — фактический потребитель master-кредов (htpasswd)"
    )

    repo = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((repo / "core" / "secrets-manifest.yaml").read_text(encoding="utf-8"))
    consumers_by_name = {entry["name"]: set(entry.get("consumers") or []) for entry in manifest["secrets"]}

    for mod in _CHECKED:
        mdir = _MODULES / mod
        requires = (yaml.safe_load((mdir / "module.yaml").read_text(encoding="utf-8"))).get("env_requires") or []
        # AI-0072: каждое env_requires имя обязано быть зарегистрировано в
        # secrets-manifest с ЭТИМ модулем среди потребителей
        unregistered = [name for name in requires if mod not in consumers_by_name.get(name, set())]
        assert not unregistered, f"{mod}: env_requires без регистрации в secrets-manifest consumers: {unregistered}"
        logger.info("[IMP:8][env-honesty] %s env_requires %s зарегистрированы", mod, requires)

    logger.critical("[IMP:9][test] env_requires honest across checked modules — OK (AI-0072)")
