# GREP_SUMMARY: gate env-example-template .env.example platform-env.yaml provides variables subset
# STRUCTURE: ▶ load platform-env.yaml → ◇ compute provided PLATFORM_* names (gen_env_platform contract) → ◇ scan .env.example files → ◇ assert subset → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 141 A2/B5): каждая PLATFORM_* переменная в .env.example шаблонов
##           должна существовать в платформенном окружении (platform-env.yaml#provides +
##           networks + core-переменные gen_env_platform). Подмножество разрешено
##           (не все сервисы нужны frontend), лишние переменные — RED (дрейф).
## @scope    Read-only гейт (make gate MODE=fast).
## @invariants
##   - Имена переменных выводятся по контракту gen_env_platform.py:
##     PLATFORM_{SVC}_HOST/PORT/DSN/URL (по полям provides) + PLATFORM_{NET} (networks)
##     + PLATFORM_DOMAIN/PROVIDES/NO_PROXY
##   - .env.example может содержать ПОДМНОЖЕСТВО, но не переменные вне множества
## @rationale .env.example — reference для разработчика; дрейф от платформенного канона
##            (опечатка/устаревшее имя) детектится на CI.
## @changes  2026-08-06 · DevPlan 141 W4 — создан
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
VAR_RE = re.compile(r"^(PLATFORM_[A-Z_]+)=")


# region FUNC_provided_vars
def _provided_vars(env_data: dict) -> set[str]:
    """Ожидаемое множество PLATFORM_* имён по контракту gen_env_platform (provides + networks + core).

    ## @purpose  Вычислить канонический набор имён переменных — не ключи provides,
    ##            а реальные имена, которые генерирует gen_env_platform.py.
    ## @io        ⇥ env_data: dict (platform-env.yaml) → ⎋ set[str]
    ## @complexity O(N) где N = сервисы + сети
    """
    names: set[str] = set()

    for svc, svc_data in (env_data.get("provides") or {}).items():
        svc_upper = str(svc).upper()
        if svc_data.get("host"):
            names.add(f"PLATFORM_{svc_upper}_HOST")
        if svc_data.get("port"):
            names.add(f"PLATFORM_{svc_upper}_PORT")
        if svc_data.get("dsn_template"):
            names.add(f"PLATFORM_{svc_upper}_DSN")
        if svc_data.get("url_template"):
            names.add(f"PLATFORM_{svc_upper}_URL")

    for net in env_data.get("networks") or []:
        net_name = net.get("name") if isinstance(net, dict) else net
        if net_name:
            names.add(f"PLATFORM_{str(net_name).upper().replace('-', '_')}")

    names.update({"PLATFORM_DOMAIN", "PLATFORM_PROVIDES", "PLATFORM_NO_PROXY"})
    return names


# endregion FUNC_provided_vars


@pytest.mark.gate
@ldd_trajectory
def test_env_example_subset_of_provides(caplog) -> None:
    """Каждая PLATFORM_* в .env.example шаблонов должна быть в платформенном окружении."""
    platform_env = ROOT / "platform-env.yaml"
    with open(platform_env) as f:
        env_data = yaml.safe_load(f)
    provided = _provided_vars(env_data)
    logger.info("[IMP:8][env_example] Provided PLATFORM_* names: %d", len(provided))

    violations: list[str] = []
    checked_templates = 0

    for template_name in ("template-backend", "template-frontend"):
        example_file = ROOT / "templates" / template_name / ".env.example"
        if not example_file.is_file():
            logger.info("[IMP:7][env_example] %s: .env.example отсутствует — skip", template_name)
            continue
        checked_templates += 1
        for line_no, line in enumerate(example_file.read_text().splitlines(), 1):
            m = VAR_RE.match(line.strip())
            if not m:
                continue
            var = m.group(1)
            if var not in provided:
                violations.append(f"{template_name}:{line_no}: {var}")

    assert checked_templates >= 2, f"Ожидались .env.example в обоих шаблонах, проверено: {checked_templates}"

    if violations:
        pytest.fail(
            ".env.example содержит переменные, отсутствующие в платформенном окружении "
            "(platform-env.yaml#provides/networks, контракт gen_env_platform):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nДобавьте переменную в platform-env.yaml или удалите из .env.example."
        )

    logger.info("[IMP:9][env_example] PASS: все PLATFORM_* в .env.example ∈ provided (%d vars)", len(provided))
