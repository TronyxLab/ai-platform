# GREP_SUMMARY: test-alloy-config journald-scrape docker-source backup-logs file-scrape labels-contract compose-volumes grep-summary hcl-parse DevPlan-164
# STRUCTURE: ┌HCL-парс config.alloy┐ → ◇ journal source (path/max_age/relabel/drop) → ┌compose base volumes (journal+machine-id+backup-logs+alloy-data)┐ → ◇ backup-logs (file_match/labels) → ◇ labels-контракт (compose_service/compose_project/container) → ◇ GREP_SUMMARY headers → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests для config.alloy (DevPlan 164 W1-5: promtail→Alloy EOL REPLACE) —
##           port promtail-config.yml тестов на HCL: 3 источника + labels-контракт 1:1.
## @scope    Читает core/modules/logging/config/config.alloy + logging compose. Без Docker.
## @invariants
##   - journal source: path /var/log/journal, max_age 24h, host-relabel (__journal__hostname)
##   - docker source: discovery.docker + discovery.relabel (container/compose_service/compose_project)
##   - backup-logs: local.file_match glob + labels compose_service=backup-cron + log_file relabel
##   - compose volumes: docker.sock/journal/machine-id :ro + backup-logs:ro + alloy-data rw
##   - labels-контракт (alert-rules): compose_service/compose_project/container/host/log_file
## @rationale Labels-паритет критичен: alert-rules (BackupFreshness/5xx) и per-project retention
##            молча ломаются при потере лейбла при REPLACE promtail→Alloy.
## @changes  2026-08-13 | DevPlan 164 W1-5 — Created (rename из test_promtail_config.py)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_ALLOY_CONFIG = _ROOT / "core" / "modules" / "logging" / "config" / "config.alloy"
_COMPOSE_BASE = _ROOT / "core" / "modules" / "logging" / "docker-compose.base.yml"


def _read() -> str:
    return _ALLOY_CONFIG.read_text(encoding="utf-8")


@ldd_trajectory
def test_alloy_journal_source_present(caplog) -> None:
    """journal-скрейп: path/max_age 24h + host-relabel + drop debug (126 D-1)."""
    caplog.set_level(logging.INFO)
    text = _read()
    assert 'loki.source.journal "journal"' in text, "config.alloy должен содержать journal source (126 D-1)"
    assert 'path          = "/var/log/journal"' in text
    assert 'max_age       = "24h"' in text
    assert "__journal__hostname" in text, "host-relabel из __journal__hostname обязателен"
    assert '"(?i)debug"' in text, "drop debug level обязателен (journal pipeline)"
    logger.critical("[IMP:9][test_alloy_config] journal source (path/max_age/relabel/drop) — OK")


@ldd_trajectory
def test_alloy_docker_source_relabels(caplog) -> None:
    """docker-скрейп: discovery.docker + relabel container/compose_service/compose_project."""
    caplog.set_level(logging.INFO)
    text = _read()
    assert 'discovery.docker "containers"' in text, "docker discovery обязателен"
    assert 'loki.source.docker "containers"' in text
    for label, meta in (
        ("container", "__meta_docker_container_name"),
        ("compose_service", "__meta_docker_container_label_com_docker_compose_service"),
        ("compose_project", "__meta_docker_container_label_com_docker_compose_project"),
    ):
        assert f'target_label  = "{label}"' in text, f"relabel {label} из {meta} обязателен (labels-контракт)"
    logger.critical("[IMP:9][test_alloy_config] docker source relabels (labels-контракт) — OK")


@ldd_trajectory
def test_alloy_backup_logs_file_scrape(caplog) -> None:
    """backup-logs file-scrape: glob + compose_service=backup-cron + log_file relabel (143 W1A)."""
    caplog.set_level(logging.INFO)
    text = _read()
    assert 'local.file_match "backup_logs"' in text, "file_match обязателен (143 W1A)"
    assert '"/var/log/platform/backup/*.log"' in text
    assert 'compose_service = "backup-cron"' in text, (
        "лейбл compose_service=backup-cron обязателен (BackupFreshness alert)"
    )
    assert 'target_label  = "log_file"' in text, "__path__ → log_file relabel обязателен"
    logger.critical("[IMP:9][test_alloy_config] backup-logs file-scrape (labels/relabel) — OK")


@ldd_trajectory
def test_alloy_nginx_labels_and_metadata(caplog) -> None:
    """nginx json-лейблы (status/request_method/host) + structured_metadata session_id/user_id (D7)."""
    caplog.set_level(logging.INFO)
    text = _read()
    for field in ("status", "request_method", "host"):
        assert field in text, f"nginx json-лейбл {field} обязателен (5xx-алерт)"
    assert "stage.structured_metadata" in text, "structured_metadata обязателен (D7)"
    assert '"session_id"' in text and '"user_id"' in text
    logger.critical("[IMP:9][test_alloy_config] nginx labels + structured_metadata — OK")


@ldd_trajectory
def test_alloy_compose_volumes_contract(caplog) -> None:
    """compose volumes: docker.sock/journal/machine-id :ro + backup-logs:ro + alloy-data rw."""
    caplog.set_level(logging.INFO)
    text = _COMPOSE_BASE.read_text(encoding="utf-8")
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in text
    assert "/var/log/journal:/var/log/journal:ro" in text
    assert "/etc/machine-id:/etc/machine-id:ro" in text
    assert "backup-logs:/var/log/platform/backup:ro" in text
    assert "alloy-data:/var/lib/alloy/data" in text, "WAL storage обязателен (Alloy не стартует без storage.path)"
    assert "--storage.path=/var/lib/alloy/data" in text
    logger.critical("[IMP:9][test_alloy_config] compose volumes contract — OK")


@ldd_trajectory
def test_alloy_config_markup_headers(caplog) -> None:
    """GREP_SUMMARY + STRUCTURE присутствуют в первых строках config.alloy."""
    caplog.set_level(logging.INFO)
    head = "\n".join(_read().splitlines()[:5])
    assert "GREP_SUMMARY:" in head, "config.alloy без GREP_SUMMARY"
    assert "STRUCTURE:" in head, "config.alloy без STRUCTURE"
    logger.critical("[IMP:9][test_alloy_config] GREP_SUMMARY/STRUCTURE headers — OK")
