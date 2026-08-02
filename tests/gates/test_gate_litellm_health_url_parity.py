# GREP_SUMMARY: gate litellm health-url liveliness readiness parity SoT single-endpoint T2
# STRUCTURE: ▶ ┌SoT: platform-infra env_defaults.LITELLM_HEALTH_URL┐ → ◇ 5 источников → ⊕ все содержат /health/liveliness → ⎋ pass|fail (R5 negative)
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 122 T2, P-2): ЕДИНЫЙ health-эндпоинт LiteLLM
#            `/health/liveliness` во всех источниках. Устраняет 3 расходящихся эндпоинта
#            (/health vs /health/readiness vs /health/liveliness) — P-2 Problem Registry 121.
## @scope    Read-only gate. Проверяет 5 источников:
##           1. platform-infra.yaml env_defaults.LITELLM_HEALTH_URL (SoT)
##           2. litellm/docker-compose.base.yml compose HEALTHCHECK
##           3. litellm/docker-compose.test.yml compose HEALTHCHECK (test-overlay)
##           4. hermes-agent/docker-compose.base.yml LITELLM_HEALTH_URL default
##           5. sync_env_defaults.py fallback-литерал
## @invariants
##   - Все 5 источников содержат `/health/liveliness`
##   - 0 вхождений `/health/readiness` и `:4000/health"`/`':4000/health'` (без суффикса)
##     в compose-источниках модулей (base.yml) и sync_env_defaults
##   - R5 negative: источник с `/health/readiness` → RED (исходный вход P-2)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale P-2: bare `/health` требует Bearer в production (disable_auth_for_health_check
##            только в test.yml) → 401 для unauth-пробы; `/health/readiness` проверяет коннект
##            к БД — сбой БД = рестарт-цикл. `/health/liveliness` — unauth, «сервер поднят».
## @changes 2026-08-03 | Created (DevPlan 122 T2)
# endregion MODULE_CONTRACT

import re

import pytest

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()

LITELLM_ENDPOINT = "/health/liveliness"
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
LITELLM_BASE = ROOT / "core" / "modules" / "litellm" / "docker-compose.base.yml"
LITELLM_TEST = ROOT / "core" / "modules" / "litellm" / "docker-compose.test.yml"
HERMES_BASE = ROOT / "core" / "modules" / "hermes-agent" / "docker-compose.base.yml"
SYNC_ENV_DEFAULTS = ROOT / "core" / "internal" / "scripts" / "sync_env_defaults.py"

# Без-суффиксные URL (латентный 401-баг P-2) — RED в compose-источниках
_BARE_HEALTH_RE = re.compile(r":4000/health[\"'\s}]|:4000/health$")
_READINESS_RE = re.compile(r"/health/readiness")


def _collect_sources() -> dict[str, str]:
    """Collect text of all 5 health-URL sources.

    ## @purpose — Map source name → text for parity scanning.
    ## @io — ⎋ dict[str, str]
    """
    return {
        "platform-infra.yaml": PLATFORM_INFRA.read_text(),
        "litellm/base.yml": LITELLM_BASE.read_text(),
        "litellm/test.yml": LITELLM_TEST.read_text(),
        "hermes-agent/base.yml": HERMES_BASE.read_text(),
        "sync_env_defaults.py": SYNC_ENV_DEFAULTS.read_text(),
    }


@pytest.mark.gate
class TestGateLitellmHealthUrlParity:
    """Gate: единый health-эндпоинт LiteLLM /health/liveliness во всех источниках (P-2)."""

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · расходящиеся эндпоинты (DevPlan 122 T2, P-2)
    # · Last fail: platform-infra=/health, litellm base.yml=/health/readiness,
    # ·   litellm test.yml=/health/liveliness — 3 расходящихся источника
    # · Remove if: health-эндпоинт канонизируется иначе
    def test_all_sources_use_liveliness(self):
        """Все 5 источников содержат /health/liveliness."""
        violations: list[str] = []
        for name, text in _collect_sources().items():
            if LITELLM_ENDPOINT not in text:
                violations.append(f"{name}: missing '{LITELLM_ENDPOINT}'")
        assert not violations, "GATE_LITELLM_HEALTH_URL_PARITY: " + "; ".join(violations)

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · readiness-дрейф (DevPlan 122 T2)
    # · Last fail: litellm base.yml:135 healthcheck → /health/readiness
    # · Remove if: readiness больше не используется нигде как health-проба LiteLLM
    def test_no_readiness_in_compose_sources(self):
        """0 вхождений /health/readiness в активных (не комментарийных) строках."""
        violations: list[str] = []
        for name, text in _collect_sources().items():
            for line_no, line in enumerate(text.splitlines(), 1):
                # TRAP-комментарии с обоснованием rejected-варианта разрешены —
                # детектор ловит активные healthcheck-строки (исходный вход P-2).
                if line.lstrip().startswith("#"):
                    continue
                if _READINESS_RE.search(line):
                    violations.append(f"{name}:{line_no}: /health/readiness в активной строке")
        assert not violations, "GATE_LITELLM_HEALTH_URL_PARITY: " + "; ".join(violations)

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · bare /health без суффикса (DevPlan 122 T2)
    # · Last fail: platform-infra LITELLM_HEALTH_URL=http://litellm:4000/health (401-баг production)
    # · Remove if: bare /health перестанет требовать auth
    def test_no_bare_health_url(self):
        """0 вхождений :4000/health без суффикса (латентный 401-баг)."""
        violations: list[str] = []
        for name, text in _collect_sources().items():
            for line_no, line in enumerate(text.splitlines(), 1):
                if "health" in line and _BARE_HEALTH_RE.search(line):
                    violations.append(f"{name}:{line_no}: bare :4000/health without suffix")
        assert not violations, "GATE_LITELLM_HEALTH_URL_PARITY: " + "; ".join(violations)

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · исходный вход P-2 (DevPlan 122 T2)
    # · Last fail: litellm/docker-compose.base.yml:135 `.../health/readiness` (реальный вход P-2)
    # · Remove if: health-эндпоинт канонизируется иначе
    def test_readiness_detected_negative(self):
        """R5 negative: inline-фикстура со /health/readiness → RED (детектор ловит P-2)."""
        inline_healthcheck = (
            'test: ["CMD", "python3", "-c", "urllib.request.urlopen(\'http://127.0.0.1:4000/health/readiness\')"]'
        )
        assert _READINESS_RE.search(inline_healthcheck), "R5 FAIL: inline fixture must contain /health/readiness"

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · bare /health (DevPlan 122 T2)
    # · Last fail: platform-infra.yaml:190 LITELLM_HEALTH_URL=http://litellm:4000/health
    # · Remove if: bare /health перестанет требовать auth
    def test_bare_health_detected_negative(self):
        """R5 negative: inline-фикстура bare :4000/health → RED."""
        inline_default = 'LITELLM_HEALTH_URL: "http://litellm:4000/health"'
        assert _BARE_HEALTH_RE.search(inline_default), "R5 FAIL: inline fixture must contain bare :4000/health"
