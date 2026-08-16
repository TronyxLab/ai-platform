# GREP_SUMMARY: gate compose restart-policies healthcheck unless-stopped always-allowlist init-no long-running 164-W1-4
# STRUCTURE: ┌discover modules + templates┐ → ◇ restart: absent→RED │ always→allowlist-check │ no→init-allowlist │ прочее→RED │ ◇ long-running без healthcheck→RED → ⊕ assertions
# region MODULE_CONTRACT
## @purpose — Gate test: канон restart-policies и healthcheck-контракт (DevPlan 164 W1-4).
## @scope — Парсит core/modules/*/docker-compose.base.yml (discover_docker_modules) +
##           templates/template-{frontend,backend}/docker-compose.yml. Проверяет:
##           1. Явный restart у каждого сервиса (absent → RED)
##           2. `always` — только allowlist (postgres/redis/backup-cron с обоснованием)
##           3. `no` — только init-контейнеры allowlist (minio-createbuckets,
##              prometheus-config-init)
##           4. Прочие политики (on-failure...) → RED
##           5. Long-running (restart != "no") без healthcheck → RED
## @invariants
##   - Module list discovered dynamically — no hardcoded list (канон T7)
##   - Allowlist — единственное место хардкода (обоснование в AGENTS.md канон-секции)
##   - Templates: unless-stopped обязателен (шаблоны = пример для подражания)
## @rationale Аудит 2026-08-13 (25 сервисов): 4 `always` (2 — без обоснования в шаблонах),
##            2 init `no`, аномалии start_period. Канон зафиксирован в core/modules/AGENTS.md;
##            гейт блокирует дрейф (restart: always без allowlist-записи, отсутствие healthcheck).
## @changes  2026-08-13 | DevPlan 164 W1-4 — Created
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

from tests._conftest.audit import discover_docker_modules
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.gate

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
MODULES_DIR = ROOT / "core" / "modules"

# Allowlist `restart: always` — обоснование: core/modules/AGENTS.md §Restart-policies (164 W1-4)
ALWAYS_ALLOWLIST: dict[str, set[str]] = {
    "postgres": {"postgres"},  # severity=critical — stateful-ядро платформы (module.yaml)
    "redis": {"redis"},  # no-volume cache-only — потеря = пересоздание
    "backup-cron": {"backup-cron"},  # cron-демон должен пережить crash
}
# Init-контейнеры (`restart: "no"`) — by design, condition: service_completed_successfully
NO_ALLOWLIST: dict[str, set[str]] = {
    "minio": {"minio-createbuckets"},
    "monitoring": {"prometheus-config-init"},
}


# Политики restart — enum из module.schema.json (SoT D5, не хардкод: static-гейт
# verb-register — hardcoded target sets в gate-файлах → RED)
def _restart_enum() -> set[str]:
    """restart-policy enum из core/schemas/module.schema.json (SoT D5)."""
    schema_path = ROOT / "core" / "schemas" / "module.schema.json"
    with schema_path.open(encoding="utf-8") as f:
        schema = yaml.safe_load(f) or {}
    enum = (schema.get("properties", {}).get("restart", {}) or {}).get("enum", [])
    return {str(v) for v in enum}


ALLOWED_RESTART_VALUES = _restart_enum() - {"on-failure"}

TEMPLATE_COMPOSE = [
    ROOT / "templates" / "template-frontend" / "docker-compose.yml",
    ROOT / "templates" / "template-backend" / "docker-compose.yml",
]

# {{PROJECT_NAME}}/{{DOMAIN}} — template-плейсхолдеры ломают PyYAML (`{` = flow-mapping):
# заменяются безопасными токенами перед safe_load (гейт проверяет restart/healthcheck,
# а не имена проектов).
_TEMPLATE_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def _load_compose(path: Path) -> dict:
    """yaml.safe_load с защитой от template-плейсхолдеров {{VAR}}."""
    text = path.read_text(encoding="utf-8")
    if "{{" in text:
        text = _TEMPLATE_TOKEN_RE.sub("TEMPLATEVAR", text)
    return yaml.safe_load(text) or {}


def _load_module_compose():
    """(module, path, services) для всех docker-модулей + шаблонов."""
    results: list[tuple[str, Path, dict]] = []
    for module_name in discover_docker_modules(str(MODULES_DIR)):
        path = MODULES_DIR / module_name / "docker-compose.base.yml"
        if not path.is_file():
            continue
        results.append((module_name, path, _load_compose(path).get("services") or {}))
    for path in TEMPLATE_COMPOSE:
        if not path.is_file():
            continue
        results.append((path.parent.name, path, _load_compose(path).get("services") or {}))
    return results


@ldd_trajectory
def test_restart_policy_canonical(caplog: pytest.LogCaptureFixture) -> None:
    """Канон: explicit restart; always — только allowlist; no — только init; остальное — RED."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []
    for module, _path, services in _load_module_compose():
        for service_name, spec in services.items():
            if not isinstance(spec, dict):
                continue
            restart = spec.get("restart")
            if restart is None:
                violations.append(f"{module}/{service_name}: restart отсутствует (требуется явная политика)")
                continue
            if restart not in ALLOWED_RESTART_VALUES:
                violations.append(f"{module}/{service_name}: restart={restart} — неканоническая политика")
                continue
            if restart == "always":
                allowed = ALWAYS_ALLOWLIST.get(module, set())
                if service_name not in allowed:
                    violations.append(
                        f"{module}/{service_name}: restart=always без allowlist-записи "
                        "(обоснование — core/modules/AGENTS.md §Restart-policies)"
                    )
            elif restart == "no":
                allowed = NO_ALLOWLIST.get(module, set())
                if service_name not in allowed:
                    violations.append(
                        f"{module}/{service_name}: restart=no вне init-allowlist "
                        "(minio-createbuckets/prometheus-config-init)"
                    )
    assert not violations, "restart-policy violations:\n" + "\n".join(violations)
    logger.critical("[IMP:9][gate][restart] All restart policies canonical (164 W1-4)")


@ldd_trajectory
def test_long_running_have_healthcheck(caplog: pytest.LogCaptureFixture) -> None:
    """Long-running (restart != no) сервисы обязаны иметь healthcheck."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []
    for module, _path, services in _load_module_compose():
        for service_name, spec in services.items():
            if not isinstance(spec, dict):
                continue
            restart = spec.get("restart")
            if restart == "no":
                continue  # init-контейнеры — healthcheck не требуется (или disable: true)
            if not spec.get("healthcheck"):
                violations.append(f"{module}/{service_name}: long-running без healthcheck")
    assert not violations, "healthcheck violations:\n" + "\n".join(violations)
    logger.critical("[IMP:9][gate][healthcheck] All long-running services have healthcheck (164 W1-4)")


@ldd_trajectory
def test_templates_use_unless_stopped(caplog: pytest.LogCaptureFixture) -> None:
    """Шаблоны проектов: restart — ТОЛЬКО unless-stopped (пример без оверхеда)."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []
    for _module, path, services in _load_module_compose():
        if path not in TEMPLATE_COMPOSE:
            continue
        for service_name, spec in services.items():
            if not isinstance(spec, dict):
                continue
            restart = spec.get("restart")
            if restart != "unless-stopped":
                violations.append(f"{path.name}:{service_name}: restart={restart} — шаблон должен быть unless-stopped")
    assert not violations, "template violations:\n" + "\n".join(violations)
    logger.critical("[IMP:9][gate][templates] Templates use unless-stopped (164 W1-4)")
