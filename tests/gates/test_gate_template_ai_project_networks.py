# GREP_SUMMARY: gate template-ai-project provider-networks SoT-parity REF-0017 platform-infra provides shared-db-net hermes-agent-net proxy-net PLATFORM_POSTGRES_DSN DATABASE_URL LLM_BASE_URL
# STRUCTURE: ▶ read templates/template-ai-project/docker-compose.yml (repo-root path) → ◇ assert DSN-маппинг (DATABASE_URL=${PLATFORM_POSTGRES_DSN}) + LLM-потребление в raw → ○ analyze_service_contracts (env_keys/needs/provides=load_provides real SoT) → ◇ assert 0 service-network-coverage violations → ⎋
# region MODULE_CONTRACT
## @purpose  Gate-тест шаблона template-ai-project (Plan 019 TASK-4, AC2): производственный compose,
##           генерируемый шаблоном, обязан декларировать сети ВСЕХ потребляемых платформенных
##           сервисов — networks(сервиса) ∩ provides.networks(SoT) ≠ ∅ для каждого
##           ${PLATFORM_<SVC>_*} в environment (SoT-парити REF-0017: platform-infra.yaml#provides
##           — единственный источник сетей). Инцидент пилотов (proxy-net only) закрыт на уровне
##           источника шаблона — 4 будущих проекта W7 (managers/clients/partners/executive)
##           стартуют из корректного каркаса.
## @scope    templates/template-ai-project/docker-compose.yml (repo root);
##           provides из РЕАЛЬНОГО core/platform-infra.yaml через load_provides (анализатор).
##           Статический — docker не требуется.
## @invariants
##   - pytestmark = pytest.mark.gate (тринити: файл tests/gates/ + маркер + entrypoint-manifest)
##   - Путь шаблона — Path(__file__).resolve().parents[2] / templates/... (Zero Hardcode, repo-root)
##   - Фальсифицируемость: raw-assert «DATABASE_URL=${PLATFORM_POSTGRES_DSN}» + LLM-потребление
##     присутствуют ДО вызова анализатора (удаление потребления = тривиальный pass → RED)
##   - {{PROJECT_NAME}}-плейсхолдеры — не ${VAR}: анализатор их не трогает; {{PROJECT_NAME}}-net —
##     own-net сервиса, не платформенная сеть (не влияет на проверки coverage)
## @rationale AC2 (план 019): шаблон — источник инцидента (дефектный compose генерировался всем
##            пилотам). Гейт на шаблоне = защита будущих scaffold-проектов (W7 §3.6); SoT-парити
##            через тот же shared-анализатор, что K3/K1 (dual-mechanism ban §1.10).
## @changes  2026-08-31 · Plan 019 TASK-4 — создан (template network parity)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from core.internal.shared.compose_service_contract import (
    RULE_SERVICE_NETWORK_COVERAGE,
    ServiceContractInput,
    analyze_service_contracts,
    load_provides,
)
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.gate

logger = logging.getLogger(__name__)

# ── Шаблонный compose (repo root: parents[2] от tests/gates/ = корень репо) ──
_TEMPLATE_COMPOSE: Path = (
    Path(__file__).resolve().parents[2] / "templates" / "template-ai-project" / "docker-compose.yml"
)

# ── Ключи .env.platform, которые шаблон резолвит при деплое (gen_env_platform, needs.database) ──
_TEMPLATE_ENV_KEYS: frozenset[str] = frozenset({"PLATFORM_POSTGRES_DSN", "PLATFORM_LITELLM_URL"})


# 🧪 TRAP[TEST] · REGRESSION · template-ai-project compose ⊇ сети потребляемых платформенных
# ·   сервисов (SoT-парити REF-0017, план 019 AC2)
# · Scenario: каждый ${PLATFORM_<SVC>_*} в environment сервиса шаблона обязан иметь
# ·   networks(сервиса) ∩ provides[SVC].networks(platform-infra.yaml) ≠ ∅; DSN-маппинг
# ·   DATABASE_URL=${PLATFORM_POSTGRES_DSN} в raw-тексте; LLM-потребление присутствует
# · Last fail: 2026-08-31 — дефектный шаблон генерировал compose «только proxy-net» +
# ·   ${DATABASE_URL} → pgbouncer/litellm недостижимы (инцидент пилотов asi-group, F1-F3)
# · Remove if: template-ai-project заменён иным каналом генерации compose (не шаблон платформы)
@ldd_trajectory
def test_template_ai_project_declares_provider_networks(caplog) -> None:
    """Шаблонный compose ⊇ сети потребляемых сервисов (platform-infra#provides, SoT-парити)."""
    assert _TEMPLATE_COMPOSE.is_file(), f"template compose не найден: {_TEMPLATE_COMPOSE}"
    raw = _TEMPLATE_COMPOSE.read_text(encoding="utf-8")

    # ── Фальсифицируемость: потребление обязано присутствовать ДО анализатора ──
    assert "DATABASE_URL=${PLATFORM_POSTGRES_DSN}" in raw, (
        "template-ai-project обязан маппить DATABASE_URL ← PLATFORM_POSTGRES_DSN (план 019 TASK-1/AC2)"
    )
    assert "LLM_BASE_URL=${PLATFORM_LITELLM_URL" in raw, (
        "template-ai-project обязан потреблять PLATFORM_LITELLM_URL (LLM_BASE_URL, план 019 TASK-1)"
    )
    logger.info("[IMP:8][gate-template-networks] DSN-маппинг + LLM-потребление присутствуют в шаблоне")

    # {{PROJECT_NAME}} рендерится шаблонизатором (template_engine) до деплоя — не-quoted ключи
    # сетей ("{{PROJECT_NAME}}-net:") невалидный YAML до рендера; парсим РЕНДЕРНУТЫЙ compose
    # (как его видит deploy). {{ORG_NAME}}/{{DOMAIN}} — только в quoted-скалярах, рендер не нужен.
    # 🧐 TRAP[DECISION] · 2026-08-31 · — · template-gate парсит РЕНДЕРНУТЫЙ compose ({{PROJECT_NAME}} → managers)
    # · Rejected: regex-скан raw-текста шаблона на networks — дублировал бы логику анализатора
    # ·   и терял SoT-парити (проверка шла бы не через analyze_service_contracts)
    # · Reason: raw-шаблон с unquoted "{{PROJECT_NAME}}-net:" не парсится yaml.safe_load (ParserError);
    # ·   рендер = ровно то, что видит deploy (template_engine, {{UPPER_SNAKE}} strict regex)
    # · Rev: если шаблон перейдёт на quoted-ключи сетей (напр. "{{PROJECT_NAME}}-net":) —
    # ·   рендер можно убрать, парсить raw
    rendered = raw.replace("{{PROJECT_NAME}}", "managers")
    assert "{{PROJECT_NAME}}" not in rendered, "рендер {{PROJECT_NAME}} обязателен для YAML-парсинга"
    data = yaml.safe_load(rendered)
    provides = load_provides()
    inp = ServiceContractInput(
        compose=data,
        env_keys=_TEMPLATE_ENV_KEYS,
        secret_names=frozenset(),
        needs_database=True,  # needs.database default = имя проекта для ai-project (TASK-8)
        provides=provides,
    )
    violations = analyze_service_contracts(inp)
    coverage = [v for v in violations if v.rule == RULE_SERVICE_NETWORK_COVERAGE]

    logger.info("[IMP:8][gate-template-networks] all violations: %s", violations)
    assert coverage == [], (
        "SoT-парити (REF-0017): шаблон потребляет платформенные сервисы без сети провайдера — "
        "дрейф template-ai-project ↔ platform-infra#provides:\n"
        + "\n".join(f"  {v.service}: {v.message}" for v in coverage)
    )
    logger.info("[IMP:9][gate-template-networks] template networks ⊇ provides for all consumed services ✓")
