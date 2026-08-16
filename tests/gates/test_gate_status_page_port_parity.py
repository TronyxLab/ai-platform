# GREP_SUMMARY: gate status-page port parity STATUS_PAGE_PORT compose env healthcheck no-op T3
# STRUCTURE: ▶ ┌SoT: platform-infra env_defaults.STATUS_PAGE_PORT┐ → ◇ compose env инжектит ${STATUS_PAGE_PORT} → ◇ healthcheck ссылается на переменную → ⊕ дефолт == SoT → ⎋ pass|fail (R5 negative)
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 122 T3, P-3): STATUS_PAGE_PORT объявлен в SoT и ПОТРЕБЛЯЕТСЯ
#            compose — смена порта в .env меняет порт приложения И healthcheck (не тихий no-op).
## @scope    Read-only gate. Проверяет core/modules/status-page/docker-compose.base.yml:
##           1. environment содержит STATUS_PAGE_PORT: ${STATUS_PAGE_PORT:-<SoT>}
##           2. healthcheck ссылается на ${STATUS_PAGE_PORT}
##           3. compose-дефолт == env_defaults.STATUS_PAGE_PORT в platform-infra.yaml (SoT)
## @invariants
##   - compose environment содержит ${STATUS_PAGE_PORT}
##   - healthcheck test содержит ${STATUS_PAGE_PORT} (не hardcode-порт)
##   - дефолт в compose == SoT (platform-infra env_defaults.STATUS_PAGE_PORT)
##   - R5 negative: healthcheck с hardcode-портом без переменной → RED (исходный вход P-3)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale P-3: platform-infra:233 STATUS_PAGE_PORT=8080, status-page base.yml НЕ инжектил env,
##            healthcheck hardcode localhost:8080, app.py:91 default 8080 — смена порта = no-op.
## @changes 2026-08-03 | Created (DevPlan 122 T3)
# endregion MODULE_CONTRACT

import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
STATUS_PAGE_BASE = ROOT / "core" / "modules" / "status-page" / "docker-compose.base.yml"

# healthcheck-строка с hardcode-портом БЕЗ переменной (исходный вход P-3)
_HARDCODED_HEALTHCHECK_RE = re.compile(r"localhost:(\d+)/healthz")


def _read_status_page_compose() -> str:
    return STATUS_PAGE_BASE.read_text()


def _sot_status_page_port() -> str:
    with pathlib.Path(PLATFORM_INFRA).open(encoding="utf-8") as f:
        infra = yaml.safe_load(f)
    return str((infra.get("env_defaults") or {}).get("STATUS_PAGE_PORT", ""))


@pytest.mark.gate
class TestGateStatusPagePortParity:
    """Gate: STATUS_PAGE_PORT потребляется compose и healthcheck — не no-op (P-3)."""

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · тихий no-op порта (DevPlan 122 T3, P-3)
    # · Last fail: status-page base.yml НЕ инжектил env; healthcheck:70 hardcode localhost:8080
    # · Remove if: STATUS_PAGE_PORT канонизируется иначе
    def test_compose_env_injects_port(self):
        """compose environment содержит STATUS_PAGE_PORT (${STATUS_PAGE_PORT:-<SoT>})."""
        text = _read_status_page_compose()
        assert "STATUS_PAGE_PORT" in text, (
            "GATE_STATUS_PAGE_PORT_PARITY: compose environment не инжектит STATUS_PAGE_PORT — "
            "смена порта в .env = тихий no-op (P-3)"
        )

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · healthcheck hardcode (DevPlan 122 T3)
    # · Last fail: healthcheck:70 `http://localhost:8080/healthz` — hardcode без переменной
    # · Remove if: healthcheck перестанет использовать переменную
    def test_healthcheck_references_variable(self):
        """healthcheck test ссылается на ${STATUS_PAGE_PORT} (не hardcode)."""
        text = _read_status_page_compose()
        healthcheck_block = text.split("healthcheck:", 1)[1].split("networks:", 1)[0]
        assert "${STATUS_PAGE_PORT" in healthcheck_block, (
            "GATE_STATUS_PAGE_PORT_PARITY: healthcheck не ссылается на ${STATUS_PAGE_PORT} — "
            "смена порта ломает healthcheck (P-3)"
        )

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · дефолт != SoT (DevPlan 122 T3)
    # · Last fail: platform-infra:233 STATUS_PAGE_PORT=8080 (SoT), compose fallback отсутствовал
    # · Remove if: STATUS_PAGE_PORT канонизируется иначе
    def test_compose_default_matches_sot(self):
        """compose-дефолт ${STATUS_PAGE_PORT:-X} == SoT env_defaults.STATUS_PAGE_PORT."""
        sot = _sot_status_page_port()
        assert sot, "GATE_STATUS_PAGE_PORT_PARITY: STATUS_PAGE_PORT отсутствует в platform-infra env_defaults"
        text = _read_status_page_compose()
        m = re.search(r"\$\{STATUS_PAGE_PORT:-(\d+)\}", text)
        assert m, "GATE_STATUS_PAGE_PORT_PARITY: compose fallback ${STATUS_PAGE_PORT:-N} не найден"
        assert m.group(1) == sot, (
            f"GATE_STATUS_PAGE_PORT_PARITY: compose дефолт {m.group(1)} != SoT {sot} — скрытый второй пин порта"
        )

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · исходный вход P-3 (DevPlan 122 T3)
    # · Last fail: healthcheck:70 `urllib.request.urlopen('http://localhost:8080/healthz')` hardcode
    # · Remove if: healthcheck перестанет использовать переменную
    def test_hardcoded_healthcheck_detected_negative(self):
        """R5 negative: inline-фикстура healthcheck с hardcode-портом → RED (детектор ловит P-3)."""
        inline_healthcheck = (
            'test: ["CMD-SHELL", "python3 -c \\"urllib.request.urlopen(\'http://localhost:8080/healthz\')\\""]'
        )
        m = _HARDCODED_HEALTHCHECK_RE.search(inline_healthcheck)
        assert m, "R5 FAIL: inline fixture must contain hardcoded localhost:PORT/healthz"
        assert "${STATUS_PAGE_PORT" not in inline_healthcheck, "R5 FAIL: fixture must not use the variable"
