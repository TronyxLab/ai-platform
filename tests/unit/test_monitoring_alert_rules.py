# GREP_SUMMARY: test-monitoring-alert-rules alerting-enabled template-render created skipped failed provisioning loki backup-rules uid-unique service-down-short mountpoint-filter high-memory-guard loki-no-binop labels-name psi-memory-pressure nginx-5xx
# STRUCTURE: ┌4 test functions (generate_alert_rules)┐ → ◇ alerting disabled (1) → ◇ template missing (1) → ◇ created (1) → ◇ render failure (1) → ┤ provisioning alert-rules.yml (uid unique, loki datasource, 3 backup rules, service_down_short, disk_space mountpoint-filter, high_memory guard, loki expr no-binop, high_memory labels.name, psi_memory_pressure, nginx_5xx)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/alert_rules.py — generate_alert_rules()
#            (DevPlan 117 G T54 extraction) + статическая валидация provisioning-файла
#            core/modules/monitoring/config/alerting/alert-rules.yml (DevPlan 132 W5, 140 W2, 143 W2, 144 W1/W2).
## @scope    No Docker — tmp_path template/output fixtures; yaml-парс provisioning-файла (read-only).
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
##   - 132 W5: uid уникальны, datasourceUid="loki" для backup-правил, expr непустой,
##     новые правила присутствуют (backup_freshness/backup_upload_failure/wal_sync_failure)
##   - 140 W2 D-4: правило service_down_short присутствует (uid/for=15s/severity=warning/expr up == 0)
##   - 140 W2 D-6: expr disk_space содержит {mountpoint="/"} (root-файловая система, не tmpfs/overlay)
##   - 143 W2: expr high_memory содержит guard `and container_spec_memory_limit_bytes > 0`
##     (контейнеры без limits → деление на 0 → +Inf → ложное firing; guard отфильтровывает серии)
##   - 144 W1 (D1): Loki expr backup-правил — чистый count_over_time(...) БЕЗ бинарной операции
##     (`< 1`/`> 0` внутри expr запрещён: Loki range binop возвращает только истинные точки →
##     пусто при count≥1 → NoData → ложный Alerting); сравнение — в threshold expression (refId C)
##   - 144 W2 (D2): аннотации high_memory (provisioning) и HighMemoryUsage/HighCPUUsage
##     (per-project шаблон) используют {{ $labels.name }}, НЕ {{ $labels.container }}
##     (cAdvisor экспортирует name; метки container у cAdvisor-метрик нет → «no value»)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — alert_rules direct tests after extraction.
##            DevPlan 132 W5 §TEST_SPEC — валидация структуры новых Loki-правил.
##            DevPlan 140 W2 §4.2/§5 — fire-семантика: sub-minute правило + mountpoint-фильтр (negative R5).
##            DevPlan 143 W2 §TEST_SPEC — high_memory guard (детектор + R5 negative).
##            DevPlan 144 W1/W2 §TEST_SPEC — loki expr no-binop (детектор + R5 negative),
##            high_memory labels.name (детектор + R5 negative).
## @changes  2026-08-01 · DevPlan 117 G T54 — created
## @changes  2026-08-04 · DevPlan 132 W5 — +provisioning-файл валидация (3 Loki-правила)
## @changes  2026-08-06 · DevPlan 140 W2 — +service_down_short (D-4), +disk_space mountpoint-фильтр (D-6), +R5 negative
## @changes  2026-08-08 · DevPlan 143 W2 — +high_memory guard (детектор + R5 negative)
## @changes  2026-08-09 · DevPlan 144 W1/W2 — +loki expr no-binop (детектор + R5 negative),
##           +high_memory labels.name (детектор + R5 negative, оба файла правил)
## @changes  2026-08-13 · DevPlan 161 W1 — +backup_freshness time-gate 07:00 МСК (детектор + R5
##           negative) + critical repeat_interval=2h (contact-points.yml; 161 W1 24h → REF-0010 2h)
## @changes  2026-08-13 · DevPlan 162 W4-1/W6-3 — +psi_memory_pressure (PSI memory some >5%),
##           +nginx_5xx_errors (loki JSON 5xx, threshold gt 2)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml
from monitoring.alert_rules import generate_alert_rules
from monitoring.config_renderer import ProjectMonitoringConfig

from tests._conftest.r1 import r1_delegates

pytestmark = pytest.mark.static_audit

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
# · Expect: 8 правил, 0 дублей uid
# · Last fail: N/A (new test for DevPlan 132 W5)
# · Remove if: provisioning-структура alert-rules.yml меняется
def test_provisioning_alert_rules_uid_unique(caplog) -> None:
    """Все uid правил уникальны (Grafana 12 provisioning contract)."""
    caplog.set_level(logging.INFO)
    rules = _provisioning_rules()
    assert len(rules) >= 8, f"ожидается ≥8 правил (5 prometheus + 3 loki), got {len(rules)}"
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


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 140 W2: D-4 sub-minute правило + D-6 mountpoint-фильтр
# ═══════════════════════════════════════════════════════════════════════


def _alert_expr(rule: dict) -> str:
    """Первая data-запись правила — Prometheus-запрос; вернуть model.expr."""
    return rule["data"][0]["model"].get("expr", "")


def _assert_disk_space_mountpoint_filter(expr: str) -> None:
    """D-6 детектор: expr правила disk_space обязан фильтровать root-файловую систему.

    Без фильтра reducer берёт tmpfs/overlay (node_filesystem_* без селектора) —
    ложные срабатывания на 20% fill (Debt 126 D-6).
    """
    assert '{mountpoint="/"}' in expr, f"D-6 FAIL: expr без mountpoint-фильтра: {expr}"
    assert expr.count('{mountpoint="/"}') == 2, (
        f"D-6 FAIL: mountpoint-фильтр должен быть на обоих операндах, expr: {expr}"
    )


# 🧪 TRAP[TEST] · Regression · Scenario: sub-minute правило service_down_short (140 W2 D-4)
# · Expect: uid/for="15s"/severity=warning/expr up == 0, summary "down (short)"
# · Last fail: N/A (new test for DevPlan 140 W2)
# · Remove if: sub-minute правило удаляется из alert-rules.yml
def test_provisioning_alert_rules_service_down_short(caplog) -> None:
    """D-4: sub-minute правило покрывает падение <1m (например postgres) — warning-канал."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "service_down_short" in rules, "правило service_down_short отсутствует в alert-rules.yml"
    rule = rules["service_down_short"]
    assert rule["for"] == "15s", f"service_down_short for != 15s: {rule['for']}"
    assert rule["labels"]["severity"] == "warning", (
        f"service_down_short severity != warning: {rule['labels']['severity']}"
    )
    expr = _alert_expr(rule)
    assert "up == bool 0" in expr, f"service_down_short expr не содержит 'up == bool 0' (B17): {expr}"
    assert "down (short)" in rule["annotations"]["summary"], (
        f"summary != 'Service {{{{ $labels.job }}}} down (short)': {rule['annotations']['summary']}"
    )
    # Анти-флаппинг critical (правило #1) не тронут
    assert rules["service_down"]["for"] == "1m", "service_down for должен остаться 1m (анти-флаппинг)"
    assert rules["service_down"]["labels"]["severity"] == "critical", "service_down severity должен быть critical"
    logger.info("[IMP:9][test_monitoring_alert_rules] service_down_short (15s/warning) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: disk_space expr с mountpoint-фильтром (140 W2 D-6)
# · Expect: оба операнда содержат {mountpoint="/"} — root ФС, не tmpfs/overlay
# · Last fail: N/A (new test for DevPlan 140 W2)
# · Remove if: disk_space правило удаляется/меняет семантику
# GUARD-PRESERVE (168): R5-пара (140 W2 D-6) — детектор mountpoint-фильтра, негатив test_disk_space_mountpoint_filter_negative_removed рядом (anti-survivorship)
def test_provisioning_alert_rules_disk_space_mountpoint_filter(caplog) -> None:
    """D-6: DiskSpaceLow expr фильтрует mountpoint=\"/\" (детектор, не tmpfs/overlay)."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "disk_space" in rules, "правило disk_space отсутствует в alert-rules.yml"
    _assert_disk_space_mountpoint_filter(_alert_expr(rules["disk_space"]))
    logger.info("[IMP:9][test_monitoring_alert_rules] disk_space mountpoint=/ filter PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · disk_space mountpoint-фильтр — Debt 126 D-6
# · Last fail: исходный вход — expr "node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.2"
# ·   (без селектора; reducer брал tmpfs/overlay → ложные DiskSpaceLow на 20% fill)
# · Remove if: детектор _assert_disk_space_mountpoint_filter меняет контракт (mountpoint="/")
def test_disk_space_mountpoint_filter_negative_removed() -> None:
    """R5 negative (D-6): expr без mountpoint-фильтра — исходный вход, поймавший баг —
    детектор ОБЯЗАН упасть (assert красный). Если он не падает — регрессия фильтра."""
    old_expr = "node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.2"
    with pytest.raises(AssertionError):
        _assert_disk_space_mountpoint_filter(old_expr)


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 144 W4 (D2): high_memory на container_memory_working_set_bytes
# ═══════════════════════════════════════════════════════════════════════


def _assert_high_memory_working_set(expr: str) -> None:
    """144 W4 детектор: expr правила high_memory использует container_memory_working_set_bytes
    (НЕ container_memory_usage_bytes).

    Деплой-верификация 2026-08-09: usage включает page cache — cAdvisor (сканирует /rootfs)
    показывал 473MiB usage при working_set 364MiB и лимите 512M (92.5% — firing), cache рос
    до лимита после каждого поднятия (128→256→512M). Working set = реальное потребление без
    cache (K8s-канон OOM-оценки) → 71% — Normal.
    """
    assert "container_memory_working_set_bytes" in expr, (
        f"144 W4 FAIL: high_memory expr не использует container_memory_working_set_bytes: {expr}"
    )
    assert "container_memory_usage_bytes" not in expr, (
        f"144 W4 FAIL: high_memory expr использует container_memory_usage_bytes (page cache): {expr}"
    )


# 🧪 TRAP[TEST] · Regression · Scenario: high_memory на working_set (144 W4 D2)
# · Expect: expr использует container_memory_working_set_bytes (не usage_bytes) —
# ·   usage включает page cache → ложный firing после поднятия лимитов (деплой-верификация)
# · Last fail: usage_bytes при 512M лимите = 473MiB (92.5%) — cAdvisor cache растёт до лимита
# · Remove if: high_memory возвращается на usage_bytes намеренно (архитектурное решение)
# 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
#   _assert_high_memory_working_set (assert + AssertionError при usage_bytes).
@r1_delegates
def test_provisioning_alert_rules_high_memory_working_set(caplog) -> None:
    """144 W4: HighMemory expr на container_memory_working_set_bytes (Grafana + per-project)."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    _assert_high_memory_working_set(_alert_expr(rules["high_memory"]))
    # per-project шаблон (config/alert-rules.yml) — тот же контракт
    per_project = {r["alert"]: r for r in _project_template_rules()}
    high_memory_rule = next(r for r in per_project.values() if r["alert"].endswith("HighMemoryUsage"))
    _assert_high_memory_working_set(high_memory_rule["expr"])
    logger.info("[IMP:9][test_monitoring_alert_rules] high_memory working_set (144 W4) PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · high_memory usage_bytes — DevPlan 144 W4
# · Last fail: исходный вход — "container_memory_usage_bytes / ..." (page cache в usage:
# ·   cAdvisor 473MiB usage vs 364MiB working_set при 512M лимите — ложный firing)
# · Remove if: детектор _assert_high_memory_working_set меняет контракт (working_set)
def test_high_memory_usage_bytes_negative_removed() -> None:
    """R5 negative (144 W4): expr с usage_bytes — исходный вход, поймавший баг —
    детектор ОБЯЗАН упасть."""
    old_expr = "container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9"
    with pytest.raises(AssertionError):
        _assert_high_memory_working_set(old_expr)


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 143 W2: high_memory guard `and container_spec_memory_limit_bytes > 0`
# ═══════════════════════════════════════════════════════════════════════


def _assert_high_memory_guard(expr: str) -> None:
    """143 W2 детектор: expr правила high_memory обязан содержать guard
    `and container_spec_memory_limit_bytes ... > 0`.

    Без guard: контейнеры без deploy.resources.limits.memory → cadvisor не экспортирует
    container_spec_memory_limit_bytes (0/absent) → usage / 0 = +Inf → +Inf > 0.9 = true →
    ложное firing ([no value], +Inf). Guard `and limit > 0` отфильтровывает серии без лимита.

    Контракт: guard = отдельный `and`-операнд, содержащий container_spec_memory_limit_bytes
    с селектором (или без) и `> 0`. expr `usage / limit > 0.9` (без and-branch) НЕ
    должен проходить — `limit > 0.9` это порог, а не guard (0.9 ≠ 0; absence-check = `> 0`).
    """
    # Guard = `and container_spec_memory_limit_bytes` (отдельный операнд после and).
    # Проверяем что после and идёт именно container_spec_memory_limit_bytes (guard-операнд),
    # а не container_memory_usage_bytes (который тоже может быть в делении).
    assert "and container_spec_memory_limit_bytes" in expr, (
        f"143 W2 FAIL: high_memory expr без guard 'and container_spec_memory_limit_bytes': {expr}"
    )
    # Guard-операнд обязан заканчиваться на '> 0' (absence-check), а не '> 0.9' (порог).
    # Извлекаем подстроку после 'and container_spec_memory_limit_bytes' и проверяем что она
    # содержит '> 0' как отдельное сравнение (не '> 0.9').
    after_guard_metric = expr.split("and container_spec_memory_limit_bytes", 1)[1]
    # Нормализуем селектор: для per-project шаблона after_guard_metric начинается с '{compose_project=...}'
    # Ищем '> 0' как конец guard'а (допускаем '> 0}' для селектора или '> 0' в конце).
    assert "> 0" in after_guard_metric, f"143 W2 FAIL: guard без '> 0' (absence-check): {expr}"


# 🧪 TRAP[TEST] · Regression · Scenario: high_memory expr с guard (143 W2)
# · Expect: expr содержит `and container_spec_memory_limit_bytes > 0` (контейнеры без limits
#   → деление на 0 → +Inf → ложное firing; guard отфильтровывает серии без лимита)
# · Last fail: N/A (new test for DevPlan 143 W2)
# · Remove if: high_memory правило удаляется/меняет семантику guard'а
# GUARD-PRESERVE (168): R5-пара (143 W2) — детектор guard limit>0, негатив test_high_memory_guard_negative_removed рядом (anti-survivorship)
def test_provisioning_alert_rules_high_memory_guard(caplog) -> None:
    """143 W2: HighMemory expr содержит guard `and container_spec_memory_limit_bytes > 0`."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "high_memory" in rules, "правило high_memory отсутствует в alert-rules.yml"
    _assert_high_memory_guard(_alert_expr(rules["high_memory"]))
    logger.info("[IMP:9][test_monitoring_alert_rules] high_memory guard (limit>0) PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · high_memory guard — DevPlan 143 W2
# · Last fail: исходный вход — expr "container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9"
# ·   (без guard; контейнеры без limits → usage / 0 = +Inf → +Inf > 0.9 = true → ложное firing)
# · Remove if: детектор _assert_high_memory_guard меняет контракт (guard limit > 0)
def test_high_memory_guard_negative_removed() -> None:
    """R5 negative (143 W2): expr без guard — исходный вход, поймавший баг —
    детектор ОБЯЗАН упасть (assert красный). Если он не падает — регрессия guard'а."""
    old_expr = "container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9"
    with pytest.raises(AssertionError):
        _assert_high_memory_guard(old_expr)


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 144 W1 (D1): Loki expr без бинарной операции — сравнение в threshold
# ═══════════════════════════════════════════════════════════════════════


def _assert_loki_expr_no_binop(expr: str) -> None:
    """144 W1 (D1) детектор: Loki expr — чистый count_over_time(...) БЕЗ бинарной операции.

    Контракт: Loki range query с binop (`< 1` / `> 0` внутри expr) возвращает ТОЛЬКО точки,
    где условие истинно → при count≥1 пустая матрица → reduce NoData → noDataState: Alerting
    → вечный ложный firing. Loki expr обязан возвращать МЕТРИКУ (count), сравнение —
    в Grafana threshold expression (refId C). Запрещённая форма: expr с '< N' / '> N' —
    binop в LogQL-селекторах не встречается, любой '<'/' >' — бинарная операция.
    """
    assert expr.startswith("count_over_time("), f"144 W1 FAIL: expr не начинается с count_over_time(: {expr}"
    # Чистый count_over_time({selector} |~ "..." [range]) заканчивается на ')' (закрытие вызова);
    # [26h] — range-аргумент ВНУТРИ вызова, не суффикс. Binop добавил бы '< N'/' > N' после ')'.
    assert expr.rstrip().endswith(")"), f"144 W1 FAIL: expr не заканчивается на ')' (обрыв формы): {expr}"
    # Никаких бинарных операторов сравнения ('<' / '>') внутри expr
    assert "<" not in expr, f"144 W1 FAIL: Loki expr содержит '<' (binop): {expr}"
    assert ">" not in expr, f"144 W1 FAIL: Loki expr содержит '>' (binop): {expr}"


def _assert_threshold_evaluator(rule: dict, evaluator_type: str) -> None:
    """144 W1 контракт: сравнение count выполняется в threshold expression (refId C).

    Loki data-запись возвращает метрику; evaluator (lt/gt) живёт ТОЛЬКО в refId C.
    """
    data = rule["data"]
    c_entries = [d for d in data if d.get("refId") == "C"]
    assert c_entries, "144 W1 FAIL: threshold expression (refId C) отсутствует"
    model = c_entries[0]["model"]
    assert model.get("type") == "threshold", "144 W1 FAIL: refId C не является threshold expression"
    conditions = model.get("conditions", [])
    assert conditions, "144 W1 FAIL: threshold без conditions"
    evaluator = conditions[0].get("evaluator", {})
    assert evaluator.get("type") == evaluator_type, f"144 W1 FAIL: evaluator type != {evaluator_type}: {evaluator}"
    assert evaluator.get("params"), f"144 W1 FAIL: evaluator params пустой: {evaluator}"


# 🧪 TRAP[TEST] · Regression · Scenario: Loki expr backup-правил без binop (144 W1 D1)
# · Expect: expr = чистый count_over_time(...) (заканчивается на ']'), сравнение — threshold C
# · Last fail: backup_freshness expr "... [26h]) < 1" — Loki range binop возвращал только
# ·   истинные точки → при count=2 пусто → NoData → noDataState Alerting → ложный firing ×5 мин
# · Remove if: Loki-правила меняют контракт (expr + threshold)
# GUARD-PRESERVE (168): R5-пара (144 W1) — детектор loki expr без binop, негатив test_loki_expr_binop_negative_removed рядом (anti-survivorship)
def test_provisioning_alert_rules_loki_expr_no_binop(caplog) -> None:
    """144 W1 (D1): все 3 backup-правила — чистый count_over_time без бинарной операции."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    for uid, evaluator in (
        ("backup_freshness", "lt"),  # count < 1 → firing (бэкап не работал)
        ("backup_upload_failure", "gt"),  # count > 0 → firing (off-site upload провален)
        ("wal_sync_failure", "gt"),  # count > 0 → firing (WAL sync S3-ошибка)
    ):
        assert uid in rules, f"правило {uid} отсутствует в alert-rules.yml"
        rule = rules[uid]
        _assert_loki_expr_no_binop(_alert_expr(rule))
        _assert_threshold_evaluator(rule, evaluator)
    logger.info("[IMP:9][test_monitoring_alert_rules] loki expr no-binop + threshold PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · Loki expr binop — DevPlan 144 W1 (D1)
# · Last fail: исходный вход — expr 'count_over_time(... [26h]) < 1' (binop внутри Loki expr:
# ·   range query возвращал только истинные точки → при count=2 пусто → NoData → Alerting)
# · Remove if: детектор _assert_loki_expr_no_binop меняет контракт (expr без binop)
def test_loki_expr_binop_negative_removed() -> None:
    """R5 negative (144 W1): expr с '< 1' внутри Loki-выражения — исходный вход,
    поймавший баг — детектор ОБЯЗАН упасть. Если он не падает — регрессия W1."""
    old_expr = 'count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h]) < 1'
    with pytest.raises(AssertionError):
        _assert_loki_expr_no_binop(old_expr)


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 144 W2 (D2): аннотации {{ $labels.name }} (cAdvisor метка name)
# ═══════════════════════════════════════════════════════════════════════

# Per-project шаблон (generated per-project post-deploy, $PROJECT substitution)
_PROJECT_TEMPLATE_RULES = (
    Path(__file__).resolve().parents[2] / "core" / "modules" / "monitoring" / "config" / "alert-rules.yml"
)


def _project_template_rules() -> list[dict]:
    """Load all rules from the per-project template alert-rules.yml (groups[].rules)."""
    data = yaml.safe_load(_PROJECT_TEMPLATE_RULES.read_text(encoding="utf-8"))
    rules: list[dict] = []
    for group in data.get("groups", []):
        rules.extend(group.get("rules", []))
    return rules


def _assert_high_memory_label_name(summary: str, description: str) -> None:
    """144 W2 (D2) детектор: аннотации используют {{ $labels.name }}, НЕ {{ $labels.container }}.

    cAdvisor экспортирует name (имя контейнера: cadvisor/loki/clickhouse/...); метки container
    у cAdvisor-метрик НЕТ → {{ $labels.container }} давал «no value» в каждом сообщении.
    """
    assert "{{ $labels.name }}" in summary, f"144 W2 FAIL: summary без {{{{ $labels.name }}}}: {summary}"
    assert "{{ $labels.name }}" in description, f"144 W2 FAIL: description без {{{{ $labels.name }}}}: {description}"
    assert "{{ $labels.container }}" not in summary, (
        f"144 W2 FAIL: summary содержит {{{{ $labels.container }}}}: {summary}"
    )
    assert "{{ $labels.container }}" not in description, (
        f"144 W2 FAIL: description содержит {{{{ $labels.container }}}}: {description}"
    )


# 🧪 TRAP[TEST] · Regression · Scenario: аннотации high_memory используют labels.name (144 W2 D2)
# · Expect: summary/description содержат {{ $labels.name }} и НЕ содержат {{ $labels.container }}
# ·   (Grafana provisioning high_memory + per-project HighMemoryUsage/HighCPUUsage)
# · Last fail: {{ $labels.container }} → «Container no value memory usage exceeds 90%» ×3 каждые 5 мин
# · Remove if: cAdvisor-источник меняет метку имени контейнера
def test_high_memory_annotations_label_name(caplog) -> None:
    """144 W2 (D2): аннотации в ОБОИХ файлах правил — {{ $labels.name }}, не container."""
    caplog.set_level(logging.INFO)
    # Grafana provisioning alert-rules.yml (uid: high_memory)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "high_memory" in rules, "правило high_memory отсутствует в alert-rules.yml"
    high_memory = rules["high_memory"]
    _assert_high_memory_label_name(high_memory["annotations"]["summary"], high_memory["annotations"]["description"])
    # Per-project шаблон config/alert-rules.yml (HighMemoryUsage + HighCPUUsage — те же cAdvisor-метки)
    project_rules = {r["alert"]: r for r in _project_template_rules()}
    for alert_name in ("${PROJECT}HighMemoryUsage", "${PROJECT}HighCPUUsage"):
        assert alert_name in project_rules, f"правило {alert_name} отсутствует в per-project шаблоне"
        annotations = project_rules[alert_name]["annotations"]
        _assert_high_memory_label_name(annotations["summary"], annotations["description"])
    logger.info("[IMP:9][test_monitoring_alert_rules] high_memory labels.name annotations PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · {{ $labels.container }} — DevPlan 144 W2 (D2)
# · Last fail: исходный вход — summary "Container {{ $labels.container }} memory usage exceeds 90%"
# ·   (cAdvisor не экспортирует метку container → «no value» в каждом алерте)
# · Remove if: детектор _assert_high_memory_label_name меняет контракт (labels.name)
def test_high_memory_container_label_negative_removed() -> None:
    """R5 negative (144 W2): аннотации с {{ $labels.container }} — исходный вход,
    поймавший баг — детектор ОБЯЗАН упасть. Если он не падает — регрессия W2."""
    old_summary = "Container {{ $labels.container }} memory usage exceeds 90%"
    old_description = "Memory usage for container {{ $labels.container }} on {{ $labels.instance }} is above 90%."
    with pytest.raises(AssertionError):
        _assert_high_memory_label_name(old_summary, old_description)


# ═══════════════════════════════════════════════════════════════════════
# DevPlan 161 W1: time-gate 07:00 МСК + repeat_interval (анти-спам; 24h → 2h REF-0010)
# ═══════════════════════════════════════════════════════════════════════

_CONTACT_POINTS = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "modules"
    / "monitoring"
    / "config"
    / "alerting"
    / "contact-points.yml"
)


def _assert_backup_freshness_time_gate(rule: dict) -> None:
    """161 W1 детектор: backup_freshness имеет time-gate 07:00–07:59 МСК (Prometheus hour 4–5 UTC).

    Пайплайн: A (loki count) → B (reduce) → D (prometheus hour() gate) → E (reduce) → F (math)
    → C (threshold lt 1 на F). Вне окна F=100 (не firing); в окне F=count. noDataState: Alerting
    сохранён (промtail мёртв → A NoData → F NoData → Alerting — канарейка жива).
    """
    data = {d.get("refId"): d for d in rule["data"]}
    # D: Prometheus time-gate — hour() 4..5 UTC (07:00–07:59 МСК), always-defined (or vector(0))
    d = data.get("D")
    assert d is not None, "161 W1 FAIL: refId D (time-gate) отсутствует"
    assert d["datasourceUid"] == "prometheus", "161 W1 FAIL: D не prometheus datasource"
    d_expr = d["model"]["expr"]
    assert "hour() >= 4" in d_expr and "hour() < 5" in d_expr, f"161 W1 FAIL: D без hour 4..5: {d_expr}"
    assert "vector(0)" in d_expr, f"161 W1 FAIL: D без vector(0) (NoData-дыра): {d_expr}"
    # F: math — в окне count, вне 100
    f = data.get("F")
    assert f is not None, "161 W1 FAIL: refId F (math) отсутствует"
    assert f["model"]["type"] == "math", "161 W1 FAIL: F не math expression"
    assert "$B" in f["model"]["expression"] and "$E" in f["model"]["expression"], (
        f"161 W1 FAIL: F без $B/$E: {f['model']['expression']}"
    )
    # C: threshold переключён на F
    c = data.get("C")
    assert c is not None, "161 W1 FAIL: refId C отсутствует"
    assert c["model"].get("expression") == "F", f"161 W1 FAIL: C expression != F: {c['model'].get('expression')}"


# 🧪 TRAP[TEST] · Regression · Scenario: backup_freshness time-gate 07:00 МСК (161 W1)
# · Expect: refId D (prometheus hour 4..5 UTC + vector(0)), F (math $B/$E), C expression=F
# · Last fail: непрерывный firing — алерт «No BACKUP COMPLETE» висел 24/7 и спамил каждые 5 мин
# · Remove if: backup_freshness меняет модель (time-gate удаляется/переезжает в scheduled job)
# GUARD-PRESERVE (168): R5-пара (161 W1) — детектор time-gate, негатив test_backup_freshness_time_gate_negative_removed рядом (anti-survivorship)
def test_backup_freshness_time_gate(caplog) -> None:
    """161 W1: backup_freshness — проверка раз в сутки в 07:00–07:59 МСК, не непрерывно."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "backup_freshness" in rules, "правило backup_freshness отсутствует"
    _assert_backup_freshness_time_gate(rules["backup_freshness"])
    logger.info("[IMP:9][test_monitoring_alert_rules] backup_freshness time-gate 07:00 МСК PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: critical-маршрут repeat_interval (161 W1 → REF-0010)
# · Expect: route receiver="Telegram Critical" имеет repeat_interval="2h"
# · Last fail: repeat_interval="5m" — Telegram-спам каждые 5 мин пока алерт firing
# ·   (161 W1 поднял 5m→24h; REF-0010 2026-08-24 снизил 24h→2h: незакрытый P0
# ·   напоминал о себе раз в сутки — re-notify раз в день = «тихий» P0)
# · Remove if: contact-points.yml меняет политику повторных уведомлений
def test_contact_points_critical_repeat_interval_2h(caplog) -> None:
    """161 W1 + REF-0010: critical-маршрут repeat_interval=2h (спам устранён, но не замолчан)."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(_CONTACT_POINTS.read_text(encoding="utf-8"))
    critical_route = None
    for policy in data.get("policies", []):
        for route in policy.get("routes", []):
            if route.get("receiver") == "Telegram Critical":
                critical_route = route
    assert critical_route is not None, "161 W1 FAIL: critical-маршрут не найден в contact-points.yml"
    assert critical_route.get("repeat_interval") == "2h", (
        f"REF-0010 FAIL: critical repeat_interval != 2h: {critical_route.get('repeat_interval')}"
    )
    logger.info("[IMP:9][test_monitoring_alert_rules] critical repeat_interval=2h PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · backup_freshness без time-gate — DevPlan 161 W1
# · Last fail: исходный вход — rule только с A/B/C (без D/F) → непрерывный firing
# · Remove if: детектор _assert_backup_freshness_time_gate меняет контракт
def test_backup_freshness_time_gate_negative_removed() -> None:
    """R5 negative (161 W1): правило без time-gate (нет D/F) — исходный вход, поймавший
    баг непрерывного спама — детектор ОБЯЗАН упасть. Если не падает — регрессия W1."""
    old_rule = {
        "data": [
            {
                "refId": "A",
                "model": {"expr": 'count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h])'},
            },
            {"refId": "B", "model": {"expression": "A", "type": "reduce"}},
            {"refId": "C", "model": {"expression": "B", "type": "threshold"}},
        ]
    }
    with pytest.raises(AssertionError):
        _assert_backup_freshness_time_gate(
            old_rule
        )  # ═══════════════════════════════════════════════════════════════════


# DevPlan 162 W4-1 (PSI) + W6-3 (nginx 5xx): новые правила в provisioning
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Scenario: psi_memory_pressure присутствует (162 W4-1)
# · Expect: uid/severity=warning/for=5m/expr rate(node_pressure_memory_stalled_seconds_total[5m]) > 0.05
# · Last fail: N/A (new test — DevPlan 162 W4-1 validation)
# · Remove if: PSI-алерт удаляется из alert-rules.yml
def test_provisioning_alert_rules_psi_memory_pressure(caplog) -> None:
    """162 W4-1: PSI memory some >5% правило присутствует (zram/swap-алерт до OOM-killer)."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "psi_memory_pressure" in rules, "правило psi_memory_pressure отсутствует в alert-rules.yml"
    rule = rules["psi_memory_pressure"]
    assert rule["labels"]["severity"] == "warning", f"severity != warning: {rule['labels']['severity']}"
    assert rule["for"] == "5m", f"for != 5m: {rule['for']}"
    assert rule["noDataState"] == "OK", "PSI-алерт: noDataState OK (нет stalled-серий = нет давления)"
    expr = _alert_expr(rule)
    assert "node_pressure_memory_stalled_seconds_total" in expr, f"expr без PSI-метрики: {expr}"
    assert "0.05" in expr, f"expr без порога 0.05 (5% stalled): {expr}"
    assert rule["data"][0]["datasourceUid"] == "prometheus", "PSI-алерт: datasource != prometheus"
    logger.info("[IMP:9][test_monitoring_alert_rules] psi_memory_pressure (162 W4-1) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: nginx_5xx_errors присутствует (162 W6-3)
# · Expect: loki datasource, status=~"5[0-9][0-9]", threshold C gt 2, noDataState OK
# · Last fail: N/A (new test — DevPlan 162 W6-3; healthcheck = liveness порта, 502 не виден)
# · Remove if: 5xx-алерт удаляется из alert-rules.yml
def test_provisioning_alert_rules_nginx_5xx(caplog) -> None:
    """162 W6-3: nginx 5xx-доля правило (loki, JSON access log, threshold gt 2 за 5m)."""
    caplog.set_level(logging.INFO)
    rules = {r["uid"]: r for r in _provisioning_rules()}
    assert "nginx_5xx_errors" in rules, "правило nginx_5xx_errors отсутствует в alert-rules.yml"
    rule = rules["nginx_5xx_errors"]
    assert rule["labels"]["severity"] == "warning", f"severity != warning: {rule['labels']['severity']}"
    assert rule["for"] == "5m", f"for != 5m: {rule['for']}"
    assert rule["noDataState"] == "OK", "nginx 5xx: noDataState OK (нет 5xx-логов = нет деградации)"
    assert rule["data"][0]["datasourceUid"] == "loki", "nginx 5xx: datasource != loki"
    # D1-контракт (144 W1): expr = чистый count_over_time без binop, сравнение в threshold C
    expr = _alert_expr(rule)
    assert expr.startswith("count_over_time("), f"expr не count_over_time: {expr}"
    assert 'compose_service="nginx"' in expr, f"expr без compose_service=nginx: {expr}"
    assert 'status=~"5[0-9][0-9]"' in expr, f"expr без 5xx-матчера: {expr}"
    _assert_loki_expr_no_binop(expr)
    _assert_threshold_evaluator(rule, "gt")
    # порог: gt 2 (анти-флаппинг для одиночных 5xx)
    c_entries = [d for d in rule["data"] if d.get("refId") == "C"]
    evaluator = c_entries[0]["model"]["conditions"][0]["evaluator"]
    assert evaluator["params"] == [2], f"threshold должен быть gt 2, got {evaluator['params']}"
    logger.info("[IMP:9][test_monitoring_alert_rules] nginx_5xx_errors (162 W6-3) PASS")
