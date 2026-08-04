#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-alert-rules alerting-enabled template-render created skipped failed provisioning loki backup-rules uid-unique
# STRUCTURE: ┌4 test functions (generate_alert_rules)┐ → ◇ alerting disabled (1) → ◇ template missing (1) → ◇ created (1) → ◇ render failure (1) → ┤ provisioning alert-rules.yml (uid unique, loki datasource, 3 backup rules)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/alert_rules.py — generate_alert_rules()
#            (DevPlan 117 G T54 extraction) + статическая валидация provisioning-файла
#            core/modules/monitoring/config/alerting/alert-rules.yml (DevPlan 132 W5).
## @scope    No Docker — tmp_path template/output fixtures; yaml-парс provisioning-файла (read-only).
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
##   - 132 W5: uid уникальны, datasourceUid="loki" для backup-правил, expr непустой,
##     новые правила присутствуют (backup_freshness/backup_upload_failure/wal_sync_failure)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — alert_rules direct tests after extraction.
##            DevPlan 132 W5 §TEST_SPEC — валидация структуры новых Loki-правил.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
## @changes  2026-08-04 · DevPlan 132 W5 — +provisioning-файл валидация (3 Loki-правила)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import yaml
from monitoring.alert_rules import generate_alert_rules
from monitoring_config_renderer import ProjectMonitoringConfig

logger = logging.getLogger(__name__)


def _config(**overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": "myapp",
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "alerting_enabled": True,
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


def _write_template(tmp_path: Path, name: str = "alert-rules.yml") -> Path:
    p = tmp_path / name
    p.write_text("groups:\n  - name: {{PROJECT}}\n", encoding="utf-8")
    return p


# 🧪 TRAP[TEST] · Regression · Scenario: alerting disabled
# · Expect: noop
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules logic changes
def test_alert_rules_disabled_noop(tmp_path: Path, caplog) -> None:
    """alerting_enabled=False → noop."""
    caplog.set_level(0)
    result = generate_alert_rules(_config(alerting_enabled=False), template_path=_write_template(tmp_path))

    assert result.status == "noop"
    assert result.component == "alerting"


# 🧪 TRAP[TEST] · Regression · Scenario: template missing
# · Expect: skipped
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: template-missing branch logic changes
def test_alert_rules_template_missing(tmp_path: Path, caplog) -> None:
    """Template not found → skipped."""
    caplog.set_level(0)
    result = generate_alert_rules(_config(), template_path=tmp_path / "missing.yml")

    assert result.status == "skipped"


# 🧪 TRAP[TEST] · Regression · Scenario: created
# · Expect: alert rules YAML written with PROJECT substituted
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules render logic changes
def test_alert_rules_created(tmp_path: Path, caplog) -> None:
    """Enabled + template → created, PROJECT substituted."""
    caplog.set_level(0)
    tmpl = _write_template(tmp_path)
    out_dir = tmp_path / "out"

    result = generate_alert_rules(_config(), template_path=tmpl, output_dir=out_dir)

    assert result.status == "created"
    out_file = out_dir / "myapp-alerts.yml"
    assert out_file.exists()
    assert "- name: myapp" in out_file.read_text()


# 🧪 TRAP[TEST] · Regression · Scenario: render failure
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules error handling changes
def test_alert_rules_render_failure(tmp_path: Path, caplog) -> None:
    """Unresolved placeholder → failed."""
    caplog.set_level(0)
    bad_tmpl = tmp_path / "bad.yml"
    bad_tmpl.write_text("groups:\n  - name: {{UNRESOLVED_VAR}}\n", encoding="utf-8")

    result = generate_alert_rules(_config(), template_path=bad_tmpl, output_dir=tmp_path / "out")

    assert result.status == "failed"


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 132 W5: статическая валидация provisioning alert-rules.yml
# ═══════════════════════════════════════════════════════════════════════

_PROVISIONING_RULES = (
    Path(__file__).resolve().parents[2] / "core" / "modules" / "monitoring" / "config" / "alerting" / "alert-rules.yml"
)


def _provisioning_rules() -> list[dict]:
    """Load all rules from the provisioning alert-rules.yml (groups[].rules)."""
    data = yaml.safe_load(_PROVISIONING_RULES.read_text(encoding="utf-8"))
    rules: list[dict] = []
    for group in data.get("groups", []):
        rules.extend(group.get("rules", []))
    return rules


# 🧪 TRAP[TEST] · Regression · Scenario: uid уникальны в provisioning-файле (132 W5)
# · Expect: 7 правил, 0 дублей uid
# · Last fail: N/A (new test for DevPlan 132 W5)
# · Remove if: provisioning-структура alert-rules.yml меняется
def test_provisioning_alert_rules_uid_unique(caplog) -> None:
    """Все uid правил уникальны (Grafana 12 provisioning contract)."""
    caplog.set_level(logging.INFO)
    rules = _provisioning_rules()
    assert len(rules) >= 7, f"ожидается ≥7 правил (4 prometheus + 3 loki), got {len(rules)}"
    uids = [r["uid"] for r in rules]
    assert len(uids) == len(set(uids)), f"дубли uid: {[u for u in uids if uids.count(u) > 1]}"
    logger.info("[IMP:9][test_monitoring_alert_rules] %d rules, uid unique PASS", len(uids))


# 🧪 TRAP[TEST] · Regression · Scenario: новые backup-правила присутствуют (132 W5)
# · Expect: backup_freshness / backup_upload_failure / wal_sync_failure
# · Last fail: N/A (new test for DevPlan 132 W5)
# · Remove if: backup-правила удаляются
def test_provisioning_alert_rules_backup_rules_present(caplog) -> None:
    """3 новых правила присутствуют с severity-лейблами."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    for uid, severity in (
        ("backup_freshness", "critical"),
        ("backup_upload_failure", "warning"),
        ("wal_sync_failure", "warning"),
    ):
        assert uid in rules, f"правило {uid} отсутствует в alert-rules.yml"
        assert rules[uid]["labels"]["severity"] == severity, f"{uid}: severity != {severity}"
    assert rules["backup_freshness"]["for"] == "30m", "backup_freshness for=30m (анти-флаппинг)"
    logger.info("[IMP:9][test_monitoring_alert_rules] 3 backup rules present PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: datasourceUid="loki" + expr непустой (132 W5)
# · Expect: первая data-запись каждого backup-правила ссылается на loki, expr непустой
# · Last fail: N/A (new test for DevPlan 132 W5)
# · Remove if: backup-правила меняют datasource
def test_provisioning_alert_rules_loki_datasource_and_expr(caplog) -> None:
    """Backup-правила: datasourceUid=loki, expr (LogQL) непустой."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    for uid in ("backup_freshness", "backup_upload_failure", "wal_sync_failure"):
        data = rules[uid]["data"]
        assert data[0]["datasourceUid"] == "loki", f"{uid}: datasourceUid != loki"
        expr = data[0]["model"].get("expr", "")
        assert expr.strip(), f"{uid}: expr пустой"
        assert 'compose_service="backup-cron"' in expr, f"{uid}: LogQL без compose_service=backup-cron"
    logger.info("[IMP:9][test_monitoring_alert_rules] loki datasource + expr PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: существующие 4 правила не изменены (132 W5)
# · Expect: service_down/high_memory/disk_space/llm_api_errors присутствуют, prometheus datasource
# · Last fail: N/A (new test for DevPlan 132 W5)
# · Remove if: prometheus-правила реструктурируются
def test_provisioning_alert_rules_prometheus_rules_intact(caplog) -> None:
    """Существующие Prometheus-правила сохранены (datasourceUid=prometheus)."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    for uid in ("service_down", "high_memory", "disk_space", "llm_api_errors"):
        assert uid in rules, f"существующее правило {uid} удалено"
        assert rules[uid]["data"][0]["datasourceUid"] == "prometheus", f"{uid}: datasource != prometheus"
    logger.info("[IMP:9][test_monitoring_alert_rules] 4 prometheus rules intact PASS")
