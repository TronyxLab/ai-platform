# GREP_SUMMARY: test-shared-deploy-paths-resolvers letsencrypt-live node-configs-remote platform-remote-base projects-base unit C7 run-artifacts state-spool-dirs
# STRUCTURE: ▶ test_letsencrypt_live (default + env) → test_node_configs_remote → test_platform_remote_base chain → test_projects_base → test_run_artifact_resolvers_142w2 → test_cert_expiry_state_file → test_spool_resolvers (wal/backup-spool/data-dirs) → test_state_resolvers (bootstrap/converge/context-pull/build-cache)
# region MODULE_CONTRACT
## @purpose  Unit tests for shared/deploy_paths.py резолверы (DevPlan 118 C7 + 170 W1-A2) —
##           letsencrypt_live, node_configs_remote, platform_remote_base, projects_base,
##           run-артефакты (142 W2), cert_expiry_state_file, spool-набор (wal-archive,
##           backup-spool, grafana/prometheus/loki/postgres-data), state-резолверы
##           (bootstrap_state_dir, converge_cooldown_file, context_pull_ts_path, build_cache_dir).
## @scope    Tests: резолверы с параметром env (без monkeypatch.setenv) + константные дефолты.
## @invariants
##   - env-переменные приоритетнее дефолтов; никогда не raise
##   - platform_remote_base chain: PLATFORM_REMOTE_BASE → /opt/platform (PLATFORM_ROOT не влияет, RC 121)
##   - Резолверы без env-переменной (spool-data/build-cache/cooldown/pull-ts) — чистые дефолты
## @rationale DevPlan 118 C7 §TEST + DevPlan 170 W1-A2 §TEST — unit-резолверы;
##            grep-гейт (см. test_gate_deploy_paths, test_gate_run_paths_sole).
## @changes 2026-08-02 | DevPlan 118 C7 — created
## @changes 2026-08-14 | DevPlan 170 W1-A2 — +13 резолверов (spool/state/run)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared.deploy_paths import (
    DEFAULT_BACKUP_SPOOL_DIR,
    DEFAULT_BOOTSTRAP_STATE_DIR,
    DEFAULT_BUILD_CACHE_DIR,
    DEFAULT_CONTEXT_PULL_TS_PATH,
    DEFAULT_CONVERGE_COOLDOWN_FILE,
    DEFAULT_GRAFANA_DATA,
    DEFAULT_LETSENCRYPT_LIVE,
    DEFAULT_LOKI_DATA,
    DEFAULT_NODE_CONFIGS_REMOTE,
    DEFAULT_PLATFORM_BASE,
    DEFAULT_POSTGRES_DATA,
    DEFAULT_PROMETHEUS_DATA,
    DEFAULT_RUN_BASE,
    DEFAULT_WAL_ARCHIVE_DIR,
    backup_spool_appdata_dir,
    backup_spool_dir,
    backup_spool_postgres_dir,
    bootstrap_state_dir,
    build_cache_dir,
    cert_expiry_state_file,
    context_pull_ts_path,
    converge_cooldown_file,
    grafana_data_dir,
    htpasswd_file,
    letsencrypt_live,
    loki_data_dir,
    node_configs_remote,
    platform_remote_base,
    postgres_data_dir,
    projects_base,
    prometheus_data_dir,
    run_base,
    secrets_env_file,
    status_metrics_json,
    wal_archive_dir,
    watchdog_state_file,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · letsencrypt_live default + env override (C7)
# · Scenario: без env → /etc/letsencrypt/live; с LETSENCRYPT_LIVE → env
# · Last fail: 20 копий литерала /etc/letsencrypt/live (DevPlan 118 C7 факты)
# · Remove if: letsencrypt_live resolver removed
def test_letsencrypt_live() -> None:
    """letsencrypt_live → /etc/letsencrypt/live (default) или env override."""
    assert str(letsencrypt_live({})) == DEFAULT_LETSENCRYPT_LIVE
    assert str(letsencrypt_live({"LETSENCRYPT_LIVE": "/tmp/live"})) == "/tmp/live"
    logger.info("[IMP:9][test] letsencrypt_live default=%s", DEFAULT_LETSENCRYPT_LIVE)


# 🧪 TRAP[TEST] · Regression · node_configs_remote default + env (C7)
# · Scenario: без env → /opt/node-configs; с NODE_CONFIGS_REMOTE_BASE → env
# · Last fail: core_deliverer/overlay_deliverer литералы /opt/node-configs
# · Remove if: node_configs_remote resolver removed
def test_node_configs_remote() -> None:
    """node_configs_remote → /opt/node-configs (default) или NODE_CONFIGS_REMOTE_BASE."""
    assert str(node_configs_remote({})) == DEFAULT_NODE_CONFIGS_REMOTE
    assert str(node_configs_remote({"NODE_CONFIGS_REMOTE_BASE": "/tmp/nc"})) == "/tmp/nc"


# 🧪 TRAP[TEST] · Regression · platform_remote_base chain (C7 + RC 121 fix)
# · Scenario: PLATFORM_REMOTE_BASE → /opt/platform; PLATFORM_ROOT НЕ влияет на remote-базу
#   (RC 121: локальный PLATFORM_ROOT ложно детектил VPS-self — см. remote_executor TRAP[BUG])
# · Last fail: core_deliverer.resolve_remote_base + overlay_deliverer расходились (TRAP[BUG] 2026-07-31);
#   RC 121 — PLATFORM_ROOT исключён из remote-цепочки
# · Remove if: platform_remote_base resolver removed
def test_platform_remote_base_chain() -> None:
    """platform_remote_base: PLATFORM_REMOTE_BASE → /opt/platform (PLATFORM_ROOT не влияет)."""
    assert str(platform_remote_base({})) == DEFAULT_PLATFORM_BASE
    # RC 121: локальный PLATFORM_ROOT НЕ должен менять REMOTE-базу
    assert str(platform_remote_base({"PLATFORM_ROOT": "/tmp/root"})) == DEFAULT_PLATFORM_BASE
    assert str(platform_remote_base({"PLATFORM_REMOTE_BASE": "/tmp/remote", "PLATFORM_ROOT": "/tmp/root"})) == (
        "/tmp/remote"
    )


# 🧪 TRAP[TEST] · Regression · projects_base (A3) + plan 012 T18 (F-017 dev-fallback)
# · Scenario: PROJECTS_BASE env приоритетнее; без env → /opt/projects если доступен,
#   иначе dev-fallback ~/projects (F-017 — операторская машина без /opt/projects)
# · Last fail: N/A (A3 — существующий резолвер); F-017 — ручная правка .env на dev
# · Remove if: projects_base resolver removed
def test_projects_base() -> None:
    """projects_base → PROJECTS_BASE env → /opt/projects → dev-fallback ~/projects (F-017)."""
    # env приоритетен всегда (нода/CI/явный dev)
    assert str(projects_base({"PROJECTS_BASE": "/tmp/projects"})) == "/tmp/projects"
    # Без env: канон /opt/projects, если он существует; иначе dev-fallback ~/projects
    resolved = projects_base({})
    if Path("/opt/projects").is_dir():
        assert str(resolved) == "/opt/projects"
    else:
        assert str(resolved) == str(Path("~/projects").expanduser()), f"dev-fallback F-017: {resolved}"
    logger.info("[IMP:9][test][projects_base] env-priority + dev-fallback PASS (resolved=%s)", resolved)


# 🧪 TRAP[TEST] · Regression · run-артефакты (142 W2, B21)
# · Scenario: без env → /var/lib/platform/run/* (persistent, замена tmpfs /run/platform);
# ·   с env → кастомные пути (dev-локали macOS сохраняются через .env)
# · Last fail: 2026-08-06 (цикл 2 141, chaos T11) — /run/platform (tmpfs) пуст после reboot →
# ·   nginx/status-page Exited(127); B21 из реестра ручных действий 142 §2
# · Remove if: run-артефакты снова переезжают (резолверы удаляются)
def test_run_artifact_resolvers_142w2() -> None:
    """142 W2: run_base + 4 артефакта → /var/lib/platform/run/* (persistent) с env-override."""
    assert str(run_base({})) == DEFAULT_RUN_BASE
    assert str(run_base({"PLATFORM_RUN_BASE": "/tmp/run"})) == "/tmp/run"

    # secrets.env — ключевой артефакт: переживает reboot (AGE-ключ недоступен на boot, S-13)
    assert str(secrets_env_file({})) == f"{DEFAULT_RUN_BASE}/secrets.env"
    assert str(secrets_env_file({"SECRETS_ENV_FILE": "/tmp/secrets.env"})) == "/tmp/secrets.env"
    # env-цепочка: SECRETS_ENV_FILE приоритетнее PLATFORM_RUN_BASE
    assert str(secrets_env_file({"PLATFORM_RUN_BASE": "/tmp/run", "SECRETS_ENV_FILE": "/tmp/s.env"})) == "/tmp/s.env"

    assert str(htpasswd_file({})) == f"{DEFAULT_RUN_BASE}/.htpasswd-platform"
    assert str(htpasswd_file({"HTPASSWD_FILE": "/tmp/htp"})) == "/tmp/htp"

    assert str(status_metrics_json({})) == f"{DEFAULT_RUN_BASE}/status-metrics.json"
    assert str(status_metrics_json({"STATUS_METRICS_JSON": "/tmp/sm.json"})) == "/tmp/sm.json"

    assert str(watchdog_state_file({})) == f"{DEFAULT_RUN_BASE}/watchdog-state.json"
    assert str(watchdog_state_file({"WATCHDOG_STATE_FILE": "/tmp/wd.json"})) == "/tmp/wd.json"
    logger.info("[IMP:9][test] 142 W2: run-артефакты → %s (persistent)", DEFAULT_RUN_BASE)


# 🧪 TRAP[TEST] · Regression · cert_expiry_state_file (170 W1-A2)
# · Scenario: без env → {run_base}/cert-expiry-state.json (persistent run, 142 W2);
# ·   PLATFORM_RUN_BASE env → кастомный base (уважает run-цепочку, собственной env нет)
# · Last fail: cert_expiry_check.py:44 литерал "/var/lib/platform/run/cert-expiry-state.json"
# · Remove if: cert_expiry_state_file resolver removed
def test_cert_expiry_state_file() -> None:
    """cert_expiry_state_file → {run_base}/cert-expiry-state.json (env-цепочка run_base)."""
    assert str(cert_expiry_state_file({})) == f"{DEFAULT_RUN_BASE}/cert-expiry-state.json"
    assert str(cert_expiry_state_file({"PLATFORM_RUN_BASE": "/tmp/run"})) == "/tmp/run/cert-expiry-state.json"


# 🧪 TRAP[TEST] · Regression · wal_archive_dir (170 W1-A2)
# · Scenario: без env → /var/lib/platform/wal-archive; WAL_ARCHIVE_DIR env (принят wal_sync.py) → env
# · Last fail: spool_validator.py:88 + wal_sync.py:78 литералы wal-archive
# · Remove if: wal_archive_dir resolver removed
def test_wal_archive_dir() -> None:
    """wal_archive_dir → /var/lib/platform/wal-archive (default) или WAL_ARCHIVE_DIR."""
    assert str(wal_archive_dir({})) == DEFAULT_WAL_ARCHIVE_DIR
    assert str(wal_archive_dir({"WAL_ARCHIVE_DIR": "/tmp/wal"})) == "/tmp/wal"


# 🧪 TRAP[TEST] · Regression · backup_spool_dir + дериваты (170 W1-A2)
# · Scenario: без env → /var/lib/platform/backup-spool{/postgres,/app-data}; BACKUP_SPOOL_DIR env
# ·   (принят backup_postgres.py) → дериваты следуют за env
# · Last fail: spool_validator.py:82-84 + backup_postgres.py:134 литералы backup-spool
# · Remove if: backup_spool_dir resolvers removed
def test_backup_spool_dirs() -> None:
    """backup_spool_dir + postgres/app-data дериваты (BACKUP_SPOOL_DIR env)."""
    assert str(backup_spool_dir({})) == DEFAULT_BACKUP_SPOOL_DIR
    assert str(backup_spool_dir({"BACKUP_SPOOL_DIR": "/tmp/spool"})) == "/tmp/spool"
    assert str(backup_spool_postgres_dir({})) == f"{DEFAULT_BACKUP_SPOOL_DIR}/postgres"
    assert str(backup_spool_postgres_dir({"BACKUP_SPOOL_DIR": "/tmp/spool"})) == "/tmp/spool/postgres"
    assert str(backup_spool_appdata_dir({})) == f"{DEFAULT_BACKUP_SPOOL_DIR}/app-data"
    assert str(backup_spool_appdata_dir({"BACKUP_SPOOL_DIR": "/tmp/spool"})) == "/tmp/spool/app-data"


# 🧪 TRAP[TEST] · Regression · spool-набор data-дир (170 W1-A2)
# · Scenario: grafana/prometheus/loki/postgres-data — чистые дефолты (env не принят)
# · Last fail: spool_validator.py:71-73,81 литералы *-data
# · Remove if: spool data-dir resolvers removed
def test_spool_data_dirs() -> None:
    """grafana/prometheus/loki/postgres-data → /var/lib/platform/*-data (константные дефолты)."""
    assert str(grafana_data_dir()) == DEFAULT_GRAFANA_DATA
    assert str(prometheus_data_dir()) == DEFAULT_PROMETHEUS_DATA
    assert str(loki_data_dir()) == DEFAULT_LOKI_DATA
    assert str(postgres_data_dir()) == DEFAULT_POSTGRES_DATA


# 🧪 TRAP[TEST] · Regression · bootstrap_state_dir (170 W1-A2)
# · Scenario: без env → /var/lib/platform/.bootstrap; PLATFORM_STATE_DIR env (принят
# ·   orchestrator_metrics/phases.docker) → env
# · Last fail: orchestrator_metrics.py:44 + phases/docker.py:372 литералы .bootstrap
# · Remove if: bootstrap_state_dir resolver removed
def test_bootstrap_state_dir() -> None:
    """bootstrap_state_dir → /var/lib/platform/.bootstrap (default) или PLATFORM_STATE_DIR."""
    assert str(bootstrap_state_dir({})) == DEFAULT_BOOTSTRAP_STATE_DIR
    assert str(bootstrap_state_dir({"PLATFORM_STATE_DIR": "/tmp/state"})) == "/tmp/state"


# 🧪 TRAP[TEST] · Regression · converge_cooldown_file / context_pull_ts_path / build_cache_dir (170 W1-A2)
# · Scenario: константные дефолты (env не принят) — /var/lib/platform/.converge_cooldown.json,
# ·   .context-pull-ts, .build-cache
# · Last fail: converge/infra.py:68, context_overlay.py:60, build_cache.py:204,261 литералы
# · Remove if: state resolvers removed
def test_state_resolvers_constants() -> None:
    """converge_cooldown_file/context_pull_ts_path/build_cache_dir — константные дефолты."""
    assert str(converge_cooldown_file()) == DEFAULT_CONVERGE_COOLDOWN_FILE
    assert str(context_pull_ts_path()) == DEFAULT_CONTEXT_PULL_TS_PATH
    assert str(build_cache_dir()) == DEFAULT_BUILD_CACHE_DIR
