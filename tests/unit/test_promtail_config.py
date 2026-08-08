#!/usr/bin/env python3
# GREP_SUMMARY: test-promtail-config journald-scrape journal-job max-age host-label compose-volumes yaml-parse grep-summary backup-logs file-scrape
# STRUCTURE: ┌YAML-парс promtail-config┐ → ◇ journal job (path/max_age/relabel/drop) → ┌compose base volumes (journal+machine-id+backup-logs)┐ → ◇ backup-logs job (static_config/labels/relabel) → ◇ GREP_SUMMARY headers → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for W3 (DevPlan 132): promtail journal-скрейп в promtail-config.yml +
##           journald-тома в docker-compose.base.yml (logging module) — закрывает 126 D-1.
##           W1A (DevPlan 143): backup-logs file-scrape job + backup-logs volume mount.
## @scope    Pure YAML/compose static parsing (0 Docker). tmp_path не требуется (read-only файлы).
## @invariants
##   - journal job присутствует: path=/var/log/journal, max_age=24h, host relabel, drop debug
##   - backup-logs job присутствует (143 W1A): static_configs, labels compose_service=backup-cron,
##     __path__ glob, relabel __path__→log_file
##   - promtail compose volumes: /var/log/journal:ro + /etc/machine-id:ro + backup-logs:/var/log/platform/backup:ro
##   - GREP_SUMMARY/STRUCTURE заголовки на месте (семантический маркап конфигов)
## @rationale  DevPlan 132 W3 §TEST_SPEC: YAML-парс (journal job, path/max_age), compose тома,
##             GREP_SUMMARY-гейты не сломаны.
##             DevPlan 143 W1A §TEST_SPEC: backup-logs job (static_config/labels/relabel) +
##             backup-logs volume mount — file-scrape маркеров бэкапа в Loki.
## @changes  2026-08-04 | DevPlan 132 W3 — created
## @changes  2026-08-08 | DevPlan 143 W1A — +backup-logs job тест +backup-logs mount
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_PROMTAIL_CONFIG = _ROOT / "core" / "modules" / "logging" / "config" / "promtail-config.yml"
_LOGGING_COMPOSE = _ROOT / "core" / "modules" / "logging" / "docker-compose.base.yml"


# region PROMTAIL_CONFIG


# 🧪 TRAP[TEST] · Regression · Scenario: journal job в promtail-config (126 D-1)
# · Last fail: N/A (new test — DevPlan 132 W3)
# · Remove if: journal-скрейп убирается из promtail
def test_promtail_journal_job_present(caplog) -> None:
    """scrape_configs содержит job_name: journal с path/max_age."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_PROMTAIL_CONFIG.read_text(encoding="utf-8"))
    scrape_configs = data["scrape_configs"]

    journal_jobs = [j for j in scrape_configs if j.get("job_name") == "journal"]
    assert journal_jobs, "promtail-config.yml должен содержать job_name: journal (126 D-1)"
    job = journal_jobs[0]

    assert job["journal"]["path"] == "/var/log/journal", "journal path = /var/log/journal"
    assert job["journal"]["max_age"] == "24h", "journal max_age = 24h"

    relabels = job.get("relabel_configs", [])
    assert any(
        r.get("source_labels") == ["__journal__hostname"] and r.get("target_label") == "host" for r in relabels
    ), "host label из __journal__hostname"

    assert "pipeline_stages" in job, "journal job должен иметь pipeline_stages (drop debug)"
    logger.info("[IMP:9][test_promtail_config] journal job present (path/max_age/relabel) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: docker job не изменён + backup-logs добавлен (3 scrape configs)
# · Last fail: N/A (new test — DevPlan 132 W3; updated 143 W1A)
# · Remove if: docker-скрейп реструктурируется
def test_promtail_docker_job_unchanged(caplog) -> None:
    """docker_sd job сохранён; всего 3 scrape configs (docker + journal + backup-logs)."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_PROMTAIL_CONFIG.read_text(encoding="utf-8"))
    job_names = [j.get("job_name") for j in data["scrape_configs"]]
    assert "docker" in job_names, "docker_sd job must remain"
    assert job_names == ["docker", "journal", "backup-logs"], (
        f"expected [docker, journal, backup-logs], got {job_names}"
    )
    logger.info("[IMP:9][test_promtail_config] docker job unchanged (3 configs) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: backup-logs file-scrape job (143 W1A)
# · Expect: static_configs targets localhost, labels compose_service=backup-cron,
#   __path__ glob /var/log/platform/backup/*.log, relabel __path__→log_file
# · Last fail: N/A (new test — DevPlan 143 W1A)
# · Remove if: backup-logs file-scrape убирается из promtail
def test_promtail_backup_logs_job_present(caplog) -> None:
    """backup-logs job: static_config glob + labels compose_service=backup-cron + log_file relabel."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_PROMTAIL_CONFIG.read_text(encoding="utf-8"))
    jobs = [j for j in data["scrape_configs"] if j.get("job_name") == "backup-logs"]
    assert jobs, "promtail-config.yml должен содержать job_name: backup-logs (143 W1A)"
    job = jobs[0]

    # static_configs: targets [localhost], labels, __path__
    static_configs = job.get("static_configs", [])
    assert static_configs, "backup-logs job должен иметь static_configs"
    sc = static_configs[0]
    assert sc["targets"] == ["localhost"], f"backup-logs targets != [localhost]: {sc['targets']}"
    labels = sc.get("labels", {})
    assert labels.get("compose_service") == "backup-cron", (
        f"backup-logs labels.compose_service != backup-cron: {labels}"
    )
    assert labels.get("job") == "backup-logs", f"backup-logs labels.job != backup-logs: {labels}"
    assert labels.get("__path__") == "/var/log/platform/backup/*.log", (
        f"backup-logs __path__ != /var/log/platform/backup/*.log: {labels.get('__path__')}"
    )

    # relabel: __path__ → log_file
    relabels = job.get("relabel_configs", [])
    assert any(r.get("source_labels") == ["__path__"] and r.get("target_label") == "log_file" for r in relabels), (
        f"backup-logs relabel __path__→log_file missing: {relabels}"
    )
    logger.info("[IMP:9][test_promtail_config] backup-logs job (static_config/labels/relabel) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: GREP_SUMMARY/STRUCTURE заголовки не сломаны
# · Last fail: N/A (new test — DevPlan 132 W3)
# · Remove if: маркап-контракт конфигов меняется
def test_promtail_config_markup_headers(caplog) -> None:
    """GREP_SUMMARY + STRUCTURE присутствуют в первых строках promtail-config.yml."""
    caplog.set_level(logging.INFO)
    head = "\n".join(_PROMTAIL_CONFIG.read_text(encoding="utf-8").splitlines()[:5])
    assert "GREP_SUMMARY:" in head, "promtail-config.yml без GREP_SUMMARY"
    assert "STRUCTURE:" in head, "promtail-config.yml без STRUCTURE"
    logger.info("[IMP:9][test_promtail_config] GREP_SUMMARY/STRUCTURE headers PASS")


# endregion PROMTAIL_CONFIG


# region LOGGING_COMPOSE


# 🧪 TRAP[TEST] · Regression · Scenario: promtail journald-тома + backup-logs mount в compose (D-1, 143 W1A)
# · Last fail: N/A (new test — DevPlan 132 W3; updated 143 W1A)
# · Remove if: journal-скрейп убирается (тома станут не нужны)
def test_promtail_compose_journald_volumes(caplog) -> None:
    """promtail сервис монтирует /var/log/journal:ro + /etc/machine-id:ro + backup-logs:ro."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_LOGGING_COMPOSE.read_text(encoding="utf-8"))
    services = data["services"]
    assert "promtail" in services, "promtail service missing"
    volumes = services["promtail"].get("volumes", [])

    assert "/var/log/journal:/var/log/journal:ro" in volumes, "journal volume missing (126 D-1)"
    assert "/etc/machine-id:/etc/machine-id:ro" in volumes, "machine-id volume missing"
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in volumes, "docker.sock mount must remain"
    assert "backup-logs:/var/log/platform/backup:ro" in volumes, (
        "backup-logs mount missing (143 W1A — file-scrape backup-logs)"
    )
    logger.info("[IMP:9][test_promtail_config] compose journald + backup-logs volumes PASS")


# endregion LOGGING_COMPOSE
