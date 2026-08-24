# GREP_SUMMARY: test-ref0010 monitoring honesty path-parity render-dir alert-rule presence smoke pgbouncer langfuse-redis minio alloy noeviction noDataState contact-points
# STRUCTURE: ┌6 test functions┐ → ◇ path-parity ALERT_RULES_DIR == compose-mount (1) → ◇ tmpl jobs + renderer file_sd 1:1 (2) → ◇ alert-rule presence smoke (3) → ◇ noDataState/contact-points honesty (4) → ◇ redis memory/policy config (5) → ◇ exporter compose digest+networks (6)
# region MODULE_CONTRACT
## @purpose  REF-0010 (Волна 0 «Честные сигналы») structural tests:
##           (a) path-parity — рендер alert-rules landит в смонтированный каталог
##           Prometheus (AI-0004: silent alert loss, rank#1);
##           (b) alert-rule presence smoke — ключевые правила из карточки REF-0010
##           присутствуют в platform-alerts.yml / Grafana provisioning;
##           (c) scrape-jobs ↔ renderer ↔ compose триада (job_name 1:1, digest-pin).
## @scope    No Docker — читает конфиг-файлы monitoring/service-exporters/langfuse/redis/
##           minio compose + shared/deploy_paths + monitoring/constants (read-only).
## @invariants
##   - All tmp-операции через tmp_path (zero hardcoded paths); рабочее дерево read-only
##   - job_name 1:1 (ЛОВУШКА T3.3): nodes/<job>.json ↔ tmpl file_sd ↔ alert-rules селекторы
##   - LDD: IMP:9 траектория через @ldd_trajectory / явные logger.info
##   - Structural: тест ломается при дрейфе ЛЮБОЙ из трёх сторон (render/mount/rules) —
##     это и есть цель (path-parity гейт карточки «renders land in mounted dir»)
## @rationale Карточка REF-0010 Tests required: «gate: renders land in mounted dir
##            (path-parity тест); alert-rule presence smoke (yaml-parse)». Размещён в
##            tests/unit/ со static_audit-маркером (прецедент test_monitoring_multinode.py);
##            runtime-валидация — test-VPS в В4 (по карточке).
## @changes  2026-08-24 | REF-0010 — created (config-часть Волны 0)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MONITORING_DIR = _PROJECT_ROOT / "core" / "modules" / "monitoring"
_PROMETHEUS_TMPL = _MONITORING_DIR / "config" / "prometheus.yml.tmpl"
_PLATFORM_ALERTS_YML = _MONITORING_DIR / "config" / "platform-alerts.yml"
_GRAFANA_ALERTS_YML = _MONITORING_DIR / "config" / "alerting" / "alert-rules.yml"
_CONTACT_POINTS_YML = _MONITORING_DIR / "config" / "alerting" / "contact-points.yml"
_MONITORING_COMPOSE = _MONITORING_DIR / "docker-compose.base.yml"

_LANGFUSE_COMPOSE = _PROJECT_ROOT / "core" / "modules" / "langfuse" / "docker-compose.base.yml"
_SERVICE_EXPORTERS_COMPOSE = _PROJECT_ROOT / "core" / "modules" / "service-exporters" / "docker-compose.base.yml"
_REDIS_COMPOSE = _PROJECT_ROOT / "core" / "modules" / "redis" / "docker-compose.base.yml"
_MINIO_COMPOSE = _PROJECT_ROOT / "core" / "modules" / "minio" / "docker-compose.base.yml"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ═════════════════════════════════════════════════════════════════════════════
# (а) PATH-PARITY: render output dir == mounted dir (AI-0004, silent alert loss)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Scenario: render-dir parity (REF-0010/AI-0004)
# · Expect: ALERT_RULES_DIR (константа рендера) == host-side fallback монтирования
# ·   ${PROMETHEUS_RULES_DIR:-...} в monitoring compose; container-side mount target
# ·   (/opt/prometheus/rules) == rule_files glob в prometheus.yml.tmpl.
# ·   Дрейф любой из трёх сторон = правила пишутся мимо смонтированного каталога →
# ·   Prometheus молча не загружает проектные алерты (silent alert loss).
# · Last fail: AI-0004 — deploy_paths fallback /opt/prometheus/rules vs SoT mount
# ·   /opt/platform/prometheus-rules; config_renderer не пробрасывал output_dir.
# · Remove if: механизм доставки правил в Prometheus меняется целиком
@ldd_trajectory
def test_alert_rules_render_dir_parity_with_compose_mount(caplog) -> None:
    """ALERT_RULES_DIR == compose-mount host-fallback; container-mount == rule_files glob."""
    caplog.set_level(0)

    from core.internal.monitoring.constants import ALERT_RULES_DIR
    from core.internal.shared.deploy_paths import DEFAULT_PROMETHEUS_RULES_DIR, prometheus_rules_dir_sot

    # Сторона 1: канонический резолвер и константа рендера согласованы
    assert prometheus_rules_dir_sot({}) == Path(DEFAULT_PROMETHEUS_RULES_DIR)
    assert Path(DEFAULT_PROMETHEUS_RULES_DIR) == ALERT_RULES_DIR, (
        f"ALERT_RULES_DIR={ALERT_RULES_DIR} != SoT {DEFAULT_PROMETHEUS_RULES_DIR} "
        "(рендер уйдёт мимо смонтированного каталога — AI-0004 regression)"
    )

    # env перекрывает дефолт (контракт резолвера жив)
    override = {"PROMETHEUS_RULES_DIR": "/custom/rules"}
    assert prometheus_rules_dir_sot(override) == Path("/custom/rules")

    # Сторона 2: compose-mount host-fallback == тот же путь
    compose = _load_yaml(_MONITORING_COMPOSE)
    mounts: list[str] = []
    for svc in ("prometheus", "prometheus-config-init"):
        mounts.extend(compose["services"][svc].get("volumes", []))
    rules_mounts = [m for m in mounts if isinstance(m, str) and m.startswith("${PROMETHEUS_RULES_DIR:-")]
    assert rules_mounts, f"PROMETHEUS_RULES_DIR mount отсутствует в {_MONITORING_COMPOSE}: {mounts}"
    for mount in rules_mounts:
        match = re.match(r"\$\{PROMETHEUS_RULES_DIR:-([^}]+)\}:", mount)
        assert match is not None, f"неожиданная форма mount'а: {mount}"
        host_fallback = match.group(1)
        assert host_fallback == DEFAULT_PROMETHEUS_RULES_DIR, (
            f"compose-mount fallback '{host_fallback}' != deploy_paths SoT "
            f"'{DEFAULT_PROMETHEUS_RULES_DIR}' (AI-0004 drift)"
        )

    # Сторона 3: container-side mount target == rule_files glob каталог
    # Форма mount'а: ${PROMETHEUS_RULES_DIR:-<host>}:<container-path>[:ro|rw]
    def _mount_container_side(mount: str) -> str | None:
        body = re.sub(r":(?:ro|rw)$", "", mount)
        if body.startswith("${PROMETHEUS_RULES_DIR:-"):
            _, sep, rest = body.partition("}:")
            return rest if sep else None
        return body.split(":", 1)[1] if ":" in body else None

    prometheus_container_targets = {
        side
        for m in compose["services"]["prometheus"]["volumes"]
        if isinstance(m, str) and (side := _mount_container_side(m))
    }
    assert "/opt/prometheus/rules" in prometheus_container_targets, (
        f"prometheus не монтирует /opt/prometheus/rules: {sorted(prometheus_container_targets)}"
    )
    tmpl_text = _PROMETHEUS_TMPL.read_text(encoding="utf-8")
    assert '"/opt/prometheus/rules/*-alerts.yml"' in tmpl_text, (
        "rule_files glob в prometheus.yml.tmpl не указывает на смонтированный /opt/prometheus/rules"
    )

    logger.info(
        "[IMP:9][test_ref0010] path-parity OK: render=%s == mount-fallback=%s == rule_files glob",
        ALERT_RULES_DIR,
        DEFAULT_PROMETHEUS_RULES_DIR,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Scrape-jobs ↔ renderer ↔ compose триада (job_name 1:1, digest-pin)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Scenario: REF-0010 jobs в tmpl + renderer (job_name 1:1)
# · Expect: pgbouncer-exporter/langfuse-redis-exporter/minio — file_sd на
# ·   nodes/<job_name>.json; renderer имеет одноимённые _NODE_TARGET_JOBS записи;
# ·   alloy — static target alloy:12345; minio job несёт metrics_path кластера.
# · Last fail: None (new for REF-0010)
# · Remove if: honesty-jobs переезжают на другой механизм discovery
def test_tmpl_ref0010_jobs_match_renderer(caplog) -> None:
    """tmpl honesty-jobs ↔ renderer _NODE_TARGET_JOBS file_sd 1:1; alloy static."""
    caplog.set_level(0)

    from core.internal.monitoring.prometheus_targets import _NODE_TARGET_JOBS

    data = _load_yaml(_PROMETHEUS_TMPL)
    jobs = {cfg.get("job_name"): cfg for cfg in data.get("scrape_configs", [])}

    ref0010_file_sd_jobs = {
        "pgbouncer-exporter": "pgbouncer-exporter.json",
        "langfuse-redis-exporter": "langfuse-redis-exporter.json",
        "minio": "minio.json",
    }
    renderer_by_file = {j.file_name: j for j in _NODE_TARGET_JOBS}

    for job_name, file_name in ref0010_file_sd_jobs.items():
        cfg = jobs.get(job_name)
        assert cfg is not None, f"tmpl: job '{job_name}' отсутствует (REF-0010)"
        file_sd = cfg.get("file_sd_configs", [])
        assert file_sd and f"/prometheus-targets/nodes/{file_name}" in file_sd[0].get("files", []), (
            f"tmpl: job '{job_name}' обязан скрейпить nodes/{file_name} через file_sd"
        )
        entry = renderer_by_file.get(file_name)
        assert entry is not None, (
            f"renderer: запись для {file_name} отсутствует в _NODE_TARGET_JOBS "
            "(file_sd будет ссылаться на несуществующий/нерендеримый файл)"
        )
        logger.info("[IMP:8][test_ref0010] job '%s' ↔ renderer %s (port=%d)", job_name, file_name, entry.port)

    # required_module-gating: выключенный модуль → targets=[] → up-серия отсутствует → алерт НЕ firing
    assert renderer_by_file["minio.json"].required_module == "minio"
    assert renderer_by_file["langfuse-redis-exporter.json"].required_module == "langfuse"
    assert renderer_by_file["pgbouncer-exporter.json"].required_module == "postgres"

    # alloy — static (all-nodes, порт не публикуется наружу)
    alloy = jobs.get("alloy")
    assert alloy is not None, "tmpl: job 'alloy' отсутствует (REF-0010)"
    assert "alloy:12345" in alloy["static_configs"][0]["targets"], "alloy static target должен быть alloy:12345"

    # minio — встроенные cluster-metrics endpoint
    assert jobs["minio"].get("metrics_path") == "/minio/v2/metrics/cluster", (
        "minio job обязан скрейпить /minio/v2/metrics/cluster"
    )

    logger.info("[IMP:9][test_ref0010] tmpl/renderer triad OK: 3 file_sd jobs + alloy static")


# 🧪 TRAP[TEST] · Regression · Scenario: exporter-сервисы в compose с digest-pin и сетями
# · Expect: pgbouncer-exporter в service-exporters (shared-db-net + observability-net,
# ·   DSN @pgbouncer:6432/pgbouncer), langfuse-redis-exporter в langfuse (REDIS_ADDR без
# ·   пароля — langfuse-redis unauthenticated), оба с healthcheck --version (liveness-канон).
# · Last fail: None (new for REF-0010)
# · Remove if: экспортеры мигрируют между модулями
def test_exporters_compose_services_present(caplog) -> None:
    """pgbouncer-exporter и langfuse-redis-exporter объявлены с digest-pin/сетями/healthcheck."""
    caplog.set_level(0)

    se = _load_yaml(_SERVICE_EXPORTERS_COMPOSE)
    pgb = se["services"].get("pgbouncer-exporter")
    assert pgb is not None, "service-exporters: нет сервиса pgbouncer-exporter (REF-0010)"
    image = pgb["image"]
    assert "@sha256:" in image, f"pgbouncer-exporter образ без digest-pin: {image}"
    dsn = pgb["environment"]["DATA_SOURCE_NAME"]
    assert "@pgbouncer:6432/pgbouncer" in dsn, f"pgbouncer-exporter DSN должен идти в admin-db пула: {dsn}"
    networks = pgb.get("networks", {})
    assert "observability-net" in networks and "shared-db-net" in networks, (
        f"pgbouncer-exporter сети: {list(networks)} — нужны scrape + pool доступ"
    )
    assert "--version" in " ".join(pgb["healthcheck"]["test"]), "pgbouncer-exporter healthcheck via --version"

    lf = _load_yaml(_LANGFUSE_COMPOSE)
    lre = lf["services"].get("langfuse-redis-exporter")
    assert lre is not None, "langfuse: нет сервиса langfuse-redis-exporter (REF-0010)"
    assert "@sha256:" in lre["image"], f"langfuse-redis-exporter образ без digest-pin: {lre['image']}"
    assert lre["environment"]["REDIS_ADDR"] == "redis://langfuse-redis:6379", (
        "второй redis_exporter должен смотреть в langfuse-redis (без requirepass)"
    )
    lre_networks = lre.get("networks", {})
    assert "observability-net" in lre_networks and "shared-db-net" in lre_networks
    ports = " ".join(lre.get("ports", []))
    assert ":9121" in ports, f"host-publish второго экспортера обязан вести на container 9121: {ports}"

    # minio: аддитивный attach observability-net + anonymous cluster metrics
    mn = _load_yaml(_MINIO_COMPOSE)
    minio_nets = mn["services"]["minio"].get("networks", {})
    assert "observability-net" in minio_nets, "minio: нужен observability-net attach для scrape (REF-0010)"
    assert mn["services"]["minio"]["environment"].get("MINIO_PROMETHEUS_ANONYMOUS_ACCESS") == "on"

    logger.info("[IMP:9][test_ref0010] exporters compose OK: pgbouncer + langfuse-redis + minio attach")


# ═════════════════════════════════════════════════════════════════════════════
# (б) ALERT-RULE PRESENCE SMOKE (yaml-parse; ключевые имена из карточки REF-0010)
# ═════════════════════════════════════════════════════════════════════════════


def _collect_prometheus_alerts(data: dict) -> dict[str, dict]:
    alerts: dict[str, dict] = {}
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            if "alert" in rule:
                alerts[rule["alert"]] = rule
    return alerts


# 🧪 TRAP[TEST] · Regression · Scenario: presence smoke платформенных правил (REF-0010)
# · Expect: PgBouncerFacadeDown/RedisCacheDown/LangfuseQueueRedisDown critical;
# ·   LangfuseQueueEviction (детектор дрейфа noeviction) + MemoryPressure + MinioScrapeDown +
# ·   AlloyCollectorDown warning; redis_up-селекторы бьют в ДЕМОН, а не в экспортёр.
# · Last fail: None (new for REF-0010)
# · Remove if: состав infra-up группы меняется по решению архитектора
@ldd_trajectory
def test_platform_alerts_ref0010_rule_presence(caplog) -> None:
    """platform-alerts.yml содержит honesty-rules REF-0010 с корректными селекторами/severity."""
    caplog.set_level(0)

    data = _load_yaml(_PLATFORM_ALERTS_YML)
    alerts = _collect_prometheus_alerts(data)

    expected_critical = {
        "PgBouncerFacadeDown": ('up{job="pgbouncer-exporter"} == 0', "platform-db-pool"),
        "RedisCacheDown": ('redis_up{job="redis-exporter"} == 0', "platform-cache"),
        "LangfuseQueueRedisDown": ('redis_up{job="langfuse-redis-exporter"} == 0', "langfuse-queue"),
    }
    for name, (expr_fragment, service_label) in expected_critical.items():
        rule = alerts.get(name)
        assert rule is not None, f"platform-alerts.yml: отсутствует {name} (REF-0010 presence smoke)"
        assert expr_fragment in str(rule.get("expr")), f"{name}: expr обязан содержать '{expr_fragment}'"
        assert rule["labels"]["severity"] == "critical", f"{name}: severity обязан быть critical"
        assert rule["labels"]["service"] == service_label
        logger.info("[IMP:9][test_ref0010] %s present (critical, expr OK)", name)

    expected_warning = ("LangfuseQueueEviction", "LangfuseQueueMemoryPressure", "MinioScrapeDown", "AlloyCollectorDown")
    for name in expected_warning:
        rule = alerts.get(name)
        assert rule is not None, f"platform-alerts.yml: отсутствует {name} (REF-0010 presence smoke)"
        assert rule["labels"]["severity"] == "warning", f"{name}: severity обязан быть warning"
        logger.info("[IMP:8][test_ref0010] %s present (warning)", name)

    # Детектор дрейфа noeviction: evicted_keys рост > 0 на langfuse-экспортёре
    eviction = alerts["LangfuseQueueEviction"]
    assert 'increase(redis_evicted_keys_total{job="langfuse-redis-exporter"}[15m]) > 0' in str(eviction["expr"]), (
        "LangfuseQueueEviction обязан ловить рост evicted_keys (регрессия noeviction → allkeys-lru)"
    )
    # Memory pressure: used/max > 0.9 (предупреждение ДО громких OOM-ошибок записей)
    assert 'redis_memory_used_bytes{job="langfuse-redis-exporter"}' in str(
        alerts["LangfuseQueueMemoryPressure"]["expr"]
    )
    assert 'redis_memory_max_bytes{job="langfuse-redis-exporter"}' in str(alerts["LangfuseQueueMemoryPressure"]["expr"])

    logger.info("[IMP:9][test_ref0010] platform-alerts presence smoke OK: 7 honesty-rules")


# 🧪 TRAP[TEST] · Regression · Scenario: Grafana honesty — noDataState + delivery policy
# · Expect: DiskSpace/HighMemory noDataState=Alerting (ENOSPC больше не гасит сигнализацию);
# ·   PsiMemoryPressure сохраняет noDataState=OK (легитимный no-data, контракт 162 W4-1);
# ·   warning-push включён (disable_notifications=false); critical repeat_interval=2h.
# · Last fail: FAIL-0402 — disk-full гасил собственную сигнализацию (noDataState OK);
# ·   warning доставлялся с disable_notifications=true («в никуда»).
# · Remove if: Grafana provisioning формат/политика доставки меняется целиком
def test_grafana_honesty_nodata_and_delivery() -> None:
    """noDataState Alerting у DiskSpace/HighMemory; warning push on; critical repeat 2h."""

    grafana_alerts = _load_yaml(_GRAFANA_ALERTS_YML)
    by_uid = {r.get("uid"): r for g in grafana_alerts.get("groups", []) for r in g.get("rules", [])}

    for uid in ("disk_space", "high_memory"):
        rule = by_uid.get(uid)
        assert rule is not None, f"Grafana provisioning: правило uid={uid} отсутствует"
        assert rule.get("noDataState") == "Alerting", (
            f"{uid}: noDataState обязан быть Alerting (FAIL-0402: нет данных при disk-full — сам инцидент)"
        )
    # Контраст: PSI-правило легитимно остаётся OK (нет stalled-серий = нет давления)
    psi = by_uid.get("psi_memory_pressure")
    assert psi is not None and psi.get("noDataState") == "OK", (
        "psi_memory_pressure: noDataState OK — осознанное решение (162 W4-1), не трогать"
    )

    cp = _load_yaml(_CONTACT_POINTS_YML)
    receivers = {r["uid"]: r for c in cp.get("contactPoints", []) for r in c.get("receivers", [])}
    warning = receivers.get("telegram-warning")
    critical = receivers.get("telegram-critical")
    assert warning is not None and critical is not None, "contact-points: telegram receiver'ы отсутствуют"
    # disable_notifications — setting Telegram receiver'а (внутри settings, не на верхнем уровне)
    assert warning.get("settings", {}).get("disable_notifications") is False, (
        "warning-push обязан быть включён (REF-0010: disable_notifications=true терял предупреждения молча)"
    )

    policies = cp.get("policies", [])
    assert policies, "contact-points: notification policies отсутствуют"
    root = policies[0]
    routes = {r["receiver"]: r for r in root.get("routes", [])}
    crit_route = routes.get("Telegram Critical")
    warn_route = routes.get("Telegram Warning")
    assert crit_route is not None and crit_route.get("repeat_interval") == "2h", (
        f"critical repeat_interval обязан быть 2h (REF-0010), получено: {crit_route and crit_route.get('repeat_interval')}"
    )
    assert warn_route is not None, "severity-routing: warning route отсутствует"

    logger.info("[IMP:9][test_ref0010] grafana honesty OK: noDataState + warning-push + repeat 2h")


# ═════════════════════════════════════════════════════════════════════════════
# Redis память/политика: очередь noeviction, кэш headroom (конфиг-ядро В0)
# ═════════════════════════════════════════════════════════════════════════════


def _compose_command_text(service: dict) -> str:
    """Command из compose как плоский текст: folded-scalar (str) ИЛИ list-форма."""
    cmd = service.get("command", "")
    if isinstance(cmd, str):
        return " ".join(cmd.split())
    return " ".join(str(x) for x in cmd)


# 🧪 TRAP[TEST] · Regression · Scenario: redis конфиг-ядро REF-0010 (В0, не скользит)
# · Expect: langfuse-redis — maxmemory-policy noeviction (очередь НЕ выбрасывает backlog),
# ·   maxmemory 96mb < cgroup 128M (headroom, FAIL-0202); main redis — maxmemory 192mb
# ·   < cgroup 256M, политика остаётся allkeys-lfu (cache-only owner verdict).
# · Last fail: langfuse-redis работал allkeys-lru @64mb — тихая потеря трейсов при 200-OK.
# · Remove if: политика очередей/кэша пересматривается владельцем
@ldd_trajectory
def test_redis_memory_policies_config_core(tmp_path: Path, caplog) -> None:
    """langfuse-redis noeviction@96mb; main redis allkeys-lfu@192mb; оба < cgroup-лимита."""
    del tmp_path  # сигнатурная симметрия; реальные данные — статические YAML
    caplog.set_level(0)

    lf_data = _load_yaml(_LANGFUSE_COMPOSE)["services"]["langfuse-redis"]
    lf_command = _compose_command_text(lf_data)
    assert "--maxmemory-policy noeviction" in lf_command, f"langfuse-redis обязан noeviction (очередь!): {lf_command}"
    match_lf = re.search(r"--maxmemory (\d+)mb", lf_command)
    assert match_lf is not None, f"maxmemory не найден в команде langfuse-redis: {lf_command}"
    lf_maxmemory_mb = int(match_lf.group(1))
    assert 64 < lf_maxmemory_mb <= 96, (
        f"langfuse-redis maxmemory поднят с 64mb, но ≤96mb (лимит контейнера 128M): {lf_maxmemory_mb}"
    )

    rd_data = _load_yaml(_REDIS_COMPOSE)["services"]["redis"]
    rd_command = _compose_command_text(rd_data)
    match_rd = re.search(r"--maxmemory (\d+)mb", rd_command)
    assert match_rd is not None, f"maxmemory не найден в команде redis: {rd_command}"
    assert int(match_rd.group(1)) == 192, (
        f"main redis maxmemory обязан быть 192mb (REF-0010 headroom): {match_rd.group(1)}"
    )
    assert "--maxmemory-policy allkeys-lfu" in rd_command, (
        "main redis остаётся allkeys-lfu (cache-only owner verdict; noeviction — только очередь langfuse)"
    )

    # Headroom против cgroup-лимитов (FAIL-0202: maxmemory==limit → OOM до eviction/error-path)
    lf_limit = lf_data["deploy"]["resources"]["limits"]["memory"]
    rd_limit = rd_data["deploy"]["resources"]["limits"]["memory"]

    def _limit_mb(raw: object) -> int:
        text = str(raw).upper()
        return int(re.search(r"(\d+)\s*M", text).group(1))  # type: ignore[union-attr]

    assert lf_maxmemory_mb < _limit_mb(lf_limit), "langfuse-redis maxmemory обязан быть < cgroup-лимита (headroom)"
    assert int(match_rd.group(1)) < _limit_mb(rd_limit), "main redis maxmemory обязан быть < cgroup-лимита (headroom)"

    logger.info(
        "[IMP:9][test_ref0010] redis config-core OK: queue noeviction@%dmb (<%s), cache lfu@192mb (<%s)",
        lf_maxmemory_mb,
        _limit_mb(lf_limit),
        _limit_mb(rd_limit),
    )


# 🧪 TRAP[TEST] · Regression · Scenario: tsdb retention.size ENOSPC-guard
# · Expect: prometheus command содержит retention.size потолок (первичная защита от
# ·   исчерпания диска самим TSDB) рядом с time-retention.
# · Last fail: None (new for REF-0010)
# · Remove if: storage-стратегия Prometheus меняется
def test_prometheus_tsdb_retention_size_guard() -> None:
    """retention.size присутствует в command прометеуса (ENOSPC-guard REF-0010)."""
    cmd = _load_yaml(_MONITORING_COMPOSE)["services"]["prometheus"]["command"]
    joined = " ".join(cmd)
    assert any(a.startswith("--storage.tsdb.retention.size=") for a in cmd), f"retention.size отсутствует: {joined}"
    assert any(a.startswith("--storage.tsdb.retention.time=") for a in cmd), "time-retention потерян"
    logger.info("[IMP:8][test_ref0010] tsdb retention.size guard present")
