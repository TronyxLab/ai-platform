# GREP_SUMMARY: test-project-healthcheck healthcheck-poller ports 3000 4000 8080 9000 B6 unit
# STRUCTURE: ▶ test_ports_include_real (3000/4000/9000 ∈ PROJECT_HEALTHCHECK_PORTS) → test_healthcheck_port_3000 (mock _try_url → healthy)
# region MODULE_CONTRACT
## @purpose  Unit tests for PROJECT_HEALTHCHECK_PORTS эвристики healthcheck_poller (DevPlan 119 B6):
##           список портов расширен [8080,8000] → [3000,4000,8000,8080,9000] — покрытие реальных
##           compose-портов платформы (grafana/langfuse 3000, litellm 4000, minio 9000).
## @scope    Tests: PROJECT_HEALTHCHECK_PORTS membership + HealthcheckPoller._try_http на порту 3000.
##           Native imports, mock urllib через _try_url (без сети).
## @invariants
##   - PROJECT_HEALTHCHECK_PORTS включает 3000, 4000, 8000, 8080, 9000 (AC-B6.1)
##   - _try_http строит URL http://{project}:{port}/health для каждого порта
##   - 200-ответ на порту 3000 → poll_project status=healthy (AC-B6.2)
## @rationale B6 (AUDIT-4 K2): [8080,8000] не пересекался с реальными портами (4000/3000/9000) —
##            healthcheck не находил проекты на реальных портах. Расширение + тест-покрытие.
## @changes  2026-08-02 | DevPlan 119 B6 — Created
# endregion MODULE_CONTRACT

import logging
from unittest import mock

import pytest

from core.internal.deploy.healthcheck_poller import HealthcheckPoller
from core.internal.shared.timeouts import PROJECT_HEALTHCHECK_PORTS

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · PROJECT_HEALTHCHECK_PORTS включает реальные порты (B6)
# · Scenario: 3000/4000/8000/8080/9000 ∈ PROJECT_HEALTHCHECK_PORTS
# · Last fail: [8080,8000] — не пересекался с реальными (litellm 4000, grafana/langfuse 3000, minio 9000)
# · Remove if: эвристика портов заменяется генерацией из platform-infra.yaml
# GUARD-PRESERVE (168): единственное покрытие состава PROJECT_HEALTHCHECK_PORTS (AC-B6.1) —
# регресс-контракт пересечения эвристики с реальными compose-портами платформы
def test_ports_include_real(caplog: pytest.LogCaptureFixture) -> None:
    """PROJECT_HEALTHCHECK_PORTS покрывает реальные compose-порты (AC-B6.1)."""
    caplog.set_level(logging.INFO)
    for port in (3000, 4000, 8000, 8080, 9000):
        assert port in PROJECT_HEALTHCHECK_PORTS, f"port {port} missing from PROJECT_HEALTHCHECK_PORTS"
    logger.info("[IMP:9][test][ports] PROJECT_HEALTHCHECK_PORTS=%s", PROJECT_HEALTHCHECK_PORTS)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · healthcheck находит проект на порту 3000 (B6)
# · Scenario: _try_url возвращает True для http://myapp:3000/health → poll_project healthy
# · Last fail: порт 3000 отсутствовал в списке → проекты Node/React (3000) не находились
# · Remove if: HTTP-эвристика удаляется
def test_healthcheck_port_3000(caplog: pytest.LogCaptureFixture) -> None:
    """poll_project: 200 на порту 3000 → healthy (AC-B6.2)."""
    caplog.set_level(logging.INFO)
    poller = HealthcheckPoller(timeout=1, interval=1, max_retries=1)

    # T2.8: _try_url вызывается с keyword timeout= (per-URL scaling) — мок обязан принять kwarg;
    # параметр назван timeout, чтобы принять keyword (значение не используется — сетевая симуляция)
    def _fake_try_url(url: str, timeout: int | None = None) -> bool:
        return url == "http://myapp:3000/health"

    with mock.patch.object(poller, "_try_url", side_effect=_fake_try_url) as mock_try:
        result = poller.poll_project("myapp")
    assert result.status == "healthy"
    assert result.method == "http"
    # _try_http перебирает ВСЕ канонические порты — 3000 в списке URL
    tried_urls = [c.args[0] for c in mock_try.call_args_list]
    assert "http://myapp:3000/health" in tried_urls
    logger.info("[IMP:9][test][port3000] poll_project healthy via http://myapp:3000/health")
