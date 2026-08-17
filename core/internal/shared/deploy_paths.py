#!/usr/bin/env python3
# GREP_SUMMARY: deploy-paths, canonical, deprecated, registry, bootstrap-compose-stub, removal-plan, projects-base, resolver, letsencrypt-live, node-configs-remote, platform-remote-base
# STRUCTURE: ▶ CANONICAL_DEPLOY_PATHS (6 paths) → ◇ DEPRECATED_DEPLOY_PATHS (1 stub) → ⊕ get_canonical_paths() →
#            ▶ projects_base() ┌env┐ → ◇ PROJECTS_BASE → ⎋ Path (default /opt/projects) →
#            ▶ letsencrypt_live() / node_configs_remote() / platform_remote_base() (C7) → ⎋ Path
# region MODULE_CONTRACT
## @purpose  Canonical deploy path registry — single source of truth for all code delivery
##           mechanisms. Gate test validates every deploy path in entrypoint-manifest.yaml
##           is registered here. DEPRECATED_DEPLOY_PATHS includes explicit removal plans.
##           DevPlan 118 C7: +реальные прод-резолверы (letsencrypt_live, node_configs_remote,
##           platform_remote_base) — дедупликация литералов /etc/letsencrypt/live (20 копий),
##           /opt/node-configs, /opt/platform у топ-5 потребителей.
##           DevPlan 170 W1-A2: +локальные state/spool-резолверы (wal_archive_dir,
##           backup_spool_dir, spool-набор grafana/prometheus/loki/postgres-data,
##           bootstrap_state_dir, converge_cooldown_file, context_pull_ts_path,
##           build_cache_dir, cert_expiry_state_file) — дедупликация raw-литералов
##           /var/lib/platform/* в core/internal (гейт test_gate_run_paths_sole).
## @scope    Read by tests/gates/test_gate_deploy_paths.py for CI enforcement.
##           Prod-потребители резолверов: s3_ssl_cache, cert_orchestrator, cert_collector,
##           core_deliverer, overlay_deliverer (C7). No runtime dependencies — pure data module.
## @invariants
##   1. Every deploy-related make_target in entrypoint-manifest.yaml must map to a
##      canonical path defined here. Adding a new deploy mechanism without registering
##      it in CANONICAL_DEPLOY_PATHS blocks CI merge.
##   2. Every DEPRECATED entry must have target_date, removal_mechanism, and verification.
##   3. This module has zero imports from other shared modules — it is Phase A independent.
##   4. Резолверы (C7): дефолты определены ТОЛЬКО здесь; env-переменные приоритетнее;
##      никогда не raise — всегда возвращают Path.
## @rationale DRIFT-D1 (Brief 077): deploy pipelines are the most critical production domain,
##           yet there was no canonical inventory of deploy paths. CI gate enforcement
##           prevents accidental divergence between manifest, code, and documentation.
##           C7 (DevPlan 118): литералы путей размножены (20 копий /etc/letsencrypt/live,
##           /opt/node-configs, /opt/platform) — резолверы централизуют канон.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created deploy path registry
##           2026-08-02 | DevPlan 118 C7 — +letsencrypt_live/node_configs_remote/platform_remote_base
##           2026-08-14 | DevPlan 170 W1-A2 — +cert_expiry_state_file/wal_archive_dir/backup_spool_dir/
##                      spool-набор (grafana/prometheus/loki/postgres-data)/bootstrap_state_dir/
##                      converge_cooldown_file/context_pull_ts_path/build_cache_dir
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# ── Canonical Deploy Paths ──────────────────────────────────────────────────
# These are the 6 documented code delivery mechanisms. Every deploy-related
# make_target in entrypoint-manifest.yaml must be traceable to one of these paths.

CANONICAL_DEPLOY_PATHS: list[str] = [
    # 1. CI → receive verb + dispatcher (DevPlan 116 B1)
    #    git push → GitHub CI → tar via SSH forced-command → orchestrator_cli dispatch receive
    "CI → receive + dispatcher (orchestrator_cli dispatch)",
    # 2. make deploy-project (direct)
    #    deliver (ForcedCommandChannel receive <project> <version>), bypass CI, emergency fallback
    "make deploy-project (deliver, ForcedCommandChannel receive)",
    # 3. context_deployer.py (Python)
    #    ghcr.io pull (primary) + build-on-node fallback, idempotent health-gate
    "context_deployer.py (Python)",
    # 4. deploy-modules.sh (system modules)
    #    docker compose up for system modules (install.sh path)
    "deploy-modules.sh (system modules)",
    # 5. Core SCP/rsync
    #    CI workflow core-deploy → SCP/rsync core/ to VPS /opt/platform/core/
    "Core SCP/rsync",
    # 6. Context-overlay git
    #    git clone/pull via ensure_context_repo() on VPS
    "Context-overlay git",
]

# ── Deprecated Deploy Paths ────────────────────────────────────────────────
# Each entry must have a removal plan with target_date, removal_mechanism,
# verification, fallback, and rev_date. Gate test enforces this structure.

DEPRECATED_DEPLOY_PATHS: dict[str, dict[str, str]] = {
    "Bootstrap compose stub": {
        "description": (
            "Temporary nginx:alpine container generated during node bootstrap, "
            "replaced by first real project deployment via "
            "context_deployer._deploy_single_project_via_orchestrator()"
        ),
        "removal_mechanism": ("docker compose up -d на реальный проект заменяет заглушку автоматически"),
        "verification": "docker compose ps --format '{{.Image}}' | grep -c 'nginx:alpine' returns 0",
        "target_date": "2026-08-15",
        "fallback": "docker compose down nginx-stub && docker rm nginx-stub",
        "rev_date": "2026-09-01",
    },
}

# ── Разрешения single-orchestrator гейта (DevPlan 171 W3.5) ──────────────────
# SoT-конфиг вместо гейт-литералов: пути относительно core/, которым разрешён
# прямой вызов `docker compose` (Python) или scp/rsync (shell). Потребитель:
# tests/gates/test_gate_single_orchestrator.py (импортирует эти константы).
# ⚠️ TRAP[DECISION] · — · Разрешения compose/scp-rsync живут ЗДЕСЬ, а не флагом
#   allow_direct_compose в module.yaml (171 W3.5): единый Python-SoT вместо флага на
#   каждый модуль — точечные пути читаемее и покрыты гейтом single_orchestrator.
#   · Rev: если число разрешённых путей перевалит за ~15 ИЛИ появится потребность
#   запретить вызовы по-модульно (а не по-файлово) — перенести в module.yaml.

# Layer 1: Python-файлы (относительно core/), которым разрешён docker compose через subprocess.
DOCKER_COMPOSE_ALLOWED_MODULES: tuple[str, ...] = (
    "internal/deploy/orchestrator.py",
    "internal/deploy/deploy_engine.py",  # DeployEngine.deploy_compose()
    "internal/bootstrap/deploy/docker_orchestrator.py",  # Not yet migrated
    "internal/bootstrap/deploy/compose_preflight.py",  # Compose preflight
    "internal/shared/docker_compose.py",  # Shared docker compose wrapper
    # K1 project-check: docker compose config --quiet (read-only validation, DevPlan 137;
    # путь обновлён 170 W10-A — декомпозиция check_project.py → пакет)
    "internal/practices/check_project/checks/compose.py",
    # DevPlan 163 W-C: AST-детектор ЛОВИТ `docker compose` в чужом коде (rule
    # docker-sole-path); сам не вызывает — паттерн-литералы в правилах (163 W-G)
    "internal/static/docker_sole_path.py",
)

# Layer 2: Shell-файлы (относительно core/), которым разрешён прямой scp/rsync.
SCP_RSYNC_ALLOWED_PATHS: tuple[str, ...] = (
    "internal/deploy/channels/",  # SCPChannel (W4-B1, план 170: channels.py → пакет channels/)
    "internal/bootstrap/",  # Bootstrap scripts (nature: scp/rsync for core delivery)
    "entrypoints/bootstrap.sh",  # Bootstrap entrypoint
    "entrypoints/converge.sh",  # Тонкий фасад → python3 -m core.internal.bootstrap.converge (164 W3.5-1)
    "internal/bootstrap/converge.sh",  # Тонкий фасад → converge.py (rsync-делегирование — природа bootstrap)
)


# region FUNC_get_canonical_paths
## @purpose — Return immutable list of canonical deploy paths.
## @io — ⇥ None → ⎋ list[str] (6 paths)
## @complexity — O(1)
def get_canonical_paths() -> list[str]:
    """Return the list of canonical deploy paths."""
    return list(CANONICAL_DEPLOY_PATHS)


# endregion FUNC_get_canonical_paths


# region FUNC_get_deprecated_paths
## @purpose — Return immutable dict of deprecated deploy paths with removal plans.
## @io — ⇥ None → ⎋ dict[str, dict[str, str]]
## @complexity — O(1)
def get_deprecated_paths() -> dict[str, dict[str, str]]:
    """Return the dict of deprecated deploy paths with removal plans."""
    return dict(DEPRECATED_DEPLOY_PATHS)


# endregion FUNC_get_deprecated_paths


# ── PROJECTS_BASE resolver (DevPlan 118 A3) ──────────────────────────────────

DEFAULT_PROJECTS_BASE: str = "/opt/projects"
"""## @invariant Канонический дефолт PROJECTS_BASE (совпадает с orchestrator/deploy_history/context_deployer)."""


# region FUNC_projects_base
## @purpose — Резолвер PROJECTS_BASE из env-цепочки (PROJECTS_BASE env → /opt/projects).
##            Единый резолвер для reconciler_projects и будущих потребителей (C7 — активация deploy_paths).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env PROJECTS_BASE приоритетнее дефолта (тот же канон, что orchestrator_cli/receive)
##   - Никогда не raise — всегда возвращает Path
##   - Параметр env позволяет тестировать без monkeypatch.setenv (tmp_path в тестах)
def projects_base(env: Mapping[str, str] | None = None) -> Path:
    """Resolve PROJECTS_BASE from the environment chain (env → /opt/projects)."""
    source = os.environ if env is None else env
    return Path(str(source.get("PROJECTS_BASE", DEFAULT_PROJECTS_BASE)))


# endregion FUNC_projects_base


# ── Remote path resolvers (DevPlan 118 C7 — активация deploy_paths) ──────────
# Реальные прод-потребители: s3_ssl_cache, cert_orchestrator, cert_collector,
# core_deliverer, overlay_deliverer (топ-5 по DevPlan 118 C7).

DEFAULT_LETSENCRYPT_LIVE: str = "/etc/letsencrypt/live"
"""## @invariant Каноническая директория Let's Encrypt live certs (VPS-дефолт)."""

DEFAULT_NODE_CONFIGS_REMOTE: str = "/opt/node-configs"
"""## @invariant Каноническая remote-директория node-configs (core_deliverer NODE_CONFIGS_REMOTE_BASE)."""

DEFAULT_PLATFORM_BASE: str = "/opt/platform"
"""## @invariant Канонический remote platform base (PLATFORM_REMOTE_BASE → /opt/platform; PLATFORM_ROOT исключён из remote-цепочки — TRAP[BUG] 2026-08-03)."""


# region FUNC_letsencrypt_live
## @purpose — Резолвер /etc/letsencrypt/live (DevPlan 118 C7). Дедупликация 20 копий литерала.
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env LETSENCRYPT_LIVE приоритетнее дефолта (тесты/альтернативные окружения)
##   - Никогда не raise — всегда возвращает Path
def letsencrypt_live(env: Mapping[str, str] | None = None) -> Path:
    """Resolve Let's Encrypt live dir (env → /etc/letsencrypt/live, C7)."""
    source = os.environ if env is None else env
    return Path(str(source.get("LETSENCRYPT_LIVE", DEFAULT_LETSENCRYPT_LIVE)))


# endregion FUNC_letsencrypt_live


# region FUNC_node_configs_remote
## @purpose — Резолвер remote node-configs base (DevPlan 118 C7). Тот же канон, что
##            core_deliverer.resolve_node_configs_base (NODE_CONFIGS_REMOTE_BASE → /opt/node-configs).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env NODE_CONFIGS_REMOTE_BASE приоритетнее дефолта (мигрировано из core_deliverer)
##   - Никогда не raise — всегда возвращает Path
def node_configs_remote(env: Mapping[str, str] | None = None) -> Path:
    """Resolve remote node-configs base (env → /opt/node-configs, C7)."""
    source = os.environ if env is None else env
    return Path(str(source.get("NODE_CONFIGS_REMOTE_BASE", DEFAULT_NODE_CONFIGS_REMOTE)))


# endregion FUNC_node_configs_remote


# region FUNC_platform_remote_base
## @purpose — Резолвер remote platform base (DevPlan 118 C7). Цепочка:
##            PLATFORM_REMOTE_BASE → /opt/platform (тот же канон, что
##            core_deliverer.resolve_remote_base / scp-deliver.sh:129; PLATFORM_ROOT
##            исключён из remote-цепочки — TRAP[BUG] 2026-08-03 ниже).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env PLATFORM_REMOTE_BASE — единственный env-override remote-базы (PLATFORM_ROOT НЕ участвует)
##   - Никогда не raise — всегда возвращает Path
def platform_remote_base(env: Mapping[str, str] | None = None) -> Path:
    """Resolve remote platform base (PLATFORM_REMOTE_BASE → /opt/platform, C7).

    ⚠️ TRAP[BUG] · 2026-08-03 · P1 · PLATFORM_ROOT УБРАН из remote-цепочки (RC 121 e2e)
    · Symptom: remote_executor VPS_NODE_LIFECYCLE ложно детектил «мы на VPS» на dev-машине —
    ·   make передаёт PLATFORM_ROOT=<локальный>, node-lifecycle.sh существует локально.
    · Root: локальный PLATFORM_ROOT не должен влиять на REMOTE-резолюцию; PLATFORM_REMOTE_BASE
    ·   — единственный env-override remote-базы (задаётся явно, не наследует локальный корень).
    · Fix: цепочка PLATFORM_REMOTE_BASE → /opt/platform. Локальный поиск node.yaml использует
    ·   PLATFORM_ROOT напрямую (см. remote_executor._resolve_host).
    """
    source = os.environ if env is None else env
    return Path(str(source.get("PLATFORM_REMOTE_BASE") or DEFAULT_PLATFORM_BASE))


# endregion FUNC_platform_remote_base


# ── Run-артефакты (142 W2, B21): /run/platform → /var/lib/platform/run ──────────
# Решение Q2 (вариант «а»): tmpfs /run/platform НЕ переживает reboot (B21, chaos T11) —
# nginx/status-page Exited(127) после reboot: bind-mount источники (secrets.env,
# .htpasswd-platform, status-metrics.json) пусты. Перенос в persistent
# /var/lib/platform/run (тот же каталог, что state.json/.bootstrap — persistent disk).
# Каждый артефакт: env-override > прод-дефолт (dev-локали macOS сохраняются).

DEFAULT_RUN_BASE: str = "/var/lib/platform/run"
"""## @invariant Каноническая persistent-директория run-артефактов (142 W2 — замена tmpfs /run/platform)."""


# region FUNC_run_base
## @purpose — Резолвер базы run-артефактов: PLATFORM_RUN_BASE → /var/lib/platform/run.
##            Единая точка для всех файлов, которые раньше жили в tmpfs /run/platform
##            (secrets.env, .htpasswd-platform, status-metrics.json, watchdog-state.json).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env PLATFORM_RUN_BASE приоритетнее дефолта (тесты/нестандартные окружения)
##   - Никогда не raise — всегда возвращает Path
## @rationale 142 W2: 65 литералов /run/platform в 27 модулях — рассинхрон дефолтов
##            (W2-риск §8); единый резолвер + env-параметризация сохраняет dev-локали.
def run_base(env: Mapping[str, str] | None = None) -> Path:
    """Resolve run-artifacts base (PLATFORM_RUN_BASE → /var/lib/platform/run, 142 W2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("PLATFORM_RUN_BASE") or DEFAULT_RUN_BASE))


# endregion FUNC_run_base


# region FUNC_secrets_env_file
## @purpose — Резолвер SECRETS_ENV_FILE: env → {run_base}/secrets.env.
##            Ключевой артефакт W2: secrets.env переживает reboot (AGE-ключ недоступен
##            на boot по канону S-13 — только persistent dir решает полностью, B21).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def secrets_env_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve secrets.env path (SECRETS_ENV_FILE → /var/lib/platform/run/secrets.env, 142 W2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("SECRETS_ENV_FILE") or run_base(source) / "secrets.env"))


# endregion FUNC_secrets_env_file


# region FUNC_prometheus_rules_dir
## @purpose — Резолвер PROMETHEUS_RULES_DIR (170 W12 C5, drift-фикс): env → /opt/prometheus/rules.
##            3-way рассинхрон закрыт: ALERT_RULES_DIR (monitoring/constants), env-дефолт
##            sync_env_defaults, compose-mount — всё на единый резолвер (канон W1).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def prometheus_rules_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve Prometheus rules dir (PROMETHEUS_RULES_DIR → /opt/prometheus/rules)."""
    source = os.environ if env is None else env
    return Path(str(source.get("PROMETHEUS_RULES_DIR") or "/opt/prometheus/rules"))


# endregion FUNC_prometheus_rules_dir


# region FUNC_htpasswd_file
## @purpose — Резолвер HTPASSWD_FILE: env → {run_base}/.htpasswd-platform.
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def htpasswd_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve htpasswd path (HTPASSWD_FILE → /var/lib/platform/run/.htpasswd-platform, 142 W2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("HTPASSWD_FILE") or run_base(source) / ".htpasswd-platform"))


# endregion FUNC_htpasswd_file


# region FUNC_status_metrics_json
## @purpose — Резолвер STATUS_METRICS_JSON: env → {run_base}/status-metrics.json.
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def status_metrics_json(env: Mapping[str, str] | None = None) -> Path:
    """Resolve status-metrics.json path (STATUS_METRICS_JSON → /var/lib/platform/run/status-metrics.json, 142 W2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("STATUS_METRICS_JSON") or run_base(source) / "status-metrics.json"))


# endregion FUNC_status_metrics_json


# region FUNC_watchdog_state_file
## @purpose — Резолвер watchdog state-файла: env → {run_base}/watchdog-state.json.
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def watchdog_state_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve watchdog state path (WATCHDOG_STATE_FILE → /var/lib/platform/run/watchdog-state.json, 142 W2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("WATCHDOG_STATE_FILE") or run_base(source) / "watchdog-state.json"))


# endregion FUNC_watchdog_state_file


# region FUNC_cert_expiry_state_file
## @purpose — Резолвер cert-expiry state-файла: env → {run_base}/cert-expiry-state.json
##            (DevPlan 170 W1-A2 — дедупликация литерала cert_expiry_check.py:44).
##            Собственной env-переменной НЕ имеет (не принята в SoT) — уважает PLATFORM_RUN_BASE.
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def cert_expiry_state_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve cert-expiry state path ({run_base}/cert-expiry-state.json, 170 W1-A2)."""
    source = os.environ if env is None else env
    return run_base(source) / "cert-expiry-state.json"


# endregion FUNC_cert_expiry_state_file


# ── Локальные state/spool-диры (170 W1-A2): /var/lib/platform/* ─────────────────
# Дедупликация raw-литералов /var/lib/platform/{wal-archive,backup-spool,*-data,.bootstrap,
# .converge_cooldown.json,.context-pull-ts,.build-cache} в core/internal (гейт
# test_gate_run_paths_sole). Env-оверрайды — ТОЛЬКО там, где переменная уже принята
# потребителями (WAL_ARCHIVE_DIR: wal_sync.py; BACKUP_SPOOL_DIR: backup_postgres.py;
# PLATFORM_STATE_DIR: orchestrator_metrics/phases.docker/key_provisioner).

DEFAULT_WAL_ARCHIVE_DIR: str = "/var/lib/platform/wal-archive"
"""## @invariant Каноническая wal-archive директория (WAL_ARCHIVE_DIR → /var/lib/platform/wal-archive)."""

DEFAULT_BACKUP_SPOOL_DIR: str = "/var/lib/platform/backup-spool"
"""## @invariant Канонический backup-spool base (BACKUP_SPOOL_DIR → /var/lib/platform/backup-spool)."""

DEFAULT_GRAFANA_DATA: str = "/var/lib/platform/grafana-data"
"""## @invariant Каноническая grafana-data директория (spool_validator OBSERVABILITY_DIRS)."""

DEFAULT_PROMETHEUS_DATA: str = "/var/lib/platform/prometheus-data"
"""## @invariant Каноническая prometheus-data директория (spool_validator OBSERVABILITY_DIRS)."""

DEFAULT_LOKI_DATA: str = "/var/lib/platform/loki-data"
"""## @invariant Каноническая loki-data директория (spool_validator OBSERVABILITY_DIRS)."""

DEFAULT_POSTGRES_DATA: str = "/var/lib/platform/postgres-data"
"""## @invariant Каноническая postgres-data директория (spool_validator FALLBACK_DIRS)."""

DEFAULT_BOOTSTRAP_STATE_DIR: str = "/var/lib/platform/.bootstrap"
"""## @invariant Каноническая bootstrap state-директория (PLATFORM_STATE_DIR → /var/lib/platform/.bootstrap)."""

DEFAULT_CONVERGE_COOLDOWN_FILE: str = "/var/lib/platform/.converge_cooldown.json"
"""## @invariant Канонический converge cooldown-файл (converge/infra.py COOLDOWN_FILE)."""

DEFAULT_CONTEXT_PULL_TS_PATH: str = "/var/lib/platform/.context-pull-ts"
"""## @invariant Канонический context-overlay pull timestamp-файл (context_overlay.py)."""

DEFAULT_BUILD_CACHE_DIR: str = "/var/lib/platform/.build-cache"
"""## @invariant Каноническая build-cache директория (build_cache.py)."""


# region FUNC_wal_archive_dir
## @purpose — Резолвер wal-archive директории: WAL_ARCHIVE_DIR → /var/lib/platform/wal-archive.
##            Тот же канон, что wal_sync.py DEFAULT_WAL_ARCHIVE_DIR (WAL_ARCHIVE_DIR env принят).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def wal_archive_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve wal-archive dir (WAL_ARCHIVE_DIR → /var/lib/platform/wal-archive, 170 W1-A2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("WAL_ARCHIVE_DIR", DEFAULT_WAL_ARCHIVE_DIR)))


# endregion FUNC_wal_archive_dir


# region FUNC_backup_spool_dir
## @purpose — Резолвер backup-spool base: BACKUP_SPOOL_DIR → /var/lib/platform/backup-spool.
##            Тот же канон, что backup_postgres.py spool_base (BACKUP_SPOOL_DIR env принят).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def backup_spool_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve backup-spool base (BACKUP_SPOOL_DIR → /var/lib/platform/backup-spool, 170 W1-A2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("BACKUP_SPOOL_DIR", DEFAULT_BACKUP_SPOOL_DIR)))


# endregion FUNC_backup_spool_dir


# region FUNC_backup_spool_postgres_dir
## @purpose — Резолвер postgres sub-spool: {backup_spool_dir}/postgres (дериват BACKUP_SPOOL_DIR).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def backup_spool_postgres_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve backup-spool/postgres (derived from backup_spool_dir, 170 W1-A2)."""
    source = os.environ if env is None else env
    return backup_spool_dir(source) / "postgres"


# endregion FUNC_backup_spool_postgres_dir


# region FUNC_backup_spool_appdata_dir
## @purpose — Резолвер app-data sub-spool: {backup_spool_dir}/app-data (дериват BACKUP_SPOOL_DIR).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def backup_spool_appdata_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve backup-spool/app-data (derived from backup_spool_dir, 170 W1-A2)."""
    source = os.environ if env is None else env
    return backup_spool_dir(source) / "app-data"


# endregion FUNC_backup_spool_appdata_dir


# region FUNC_grafana_data_dir
## @purpose — Резолвер grafana-data директории (spool_validator OBSERVABILITY_DIRS).
##            Без env-переменной (не принята потребителями) — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def grafana_data_dir() -> Path:
    """Resolve grafana-data dir (/var/lib/platform/grafana-data, 170 W1-A2)."""
    return Path(DEFAULT_GRAFANA_DATA)


# endregion FUNC_grafana_data_dir


# region FUNC_prometheus_data_dir
## @purpose — Резолвер prometheus-data директории (spool_validator OBSERVABILITY_DIRS).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def prometheus_data_dir() -> Path:
    """Resolve prometheus-data dir (/var/lib/platform/prometheus-data, 170 W1-A2)."""
    return Path(DEFAULT_PROMETHEUS_DATA)


# endregion FUNC_prometheus_data_dir


# region FUNC_loki_data_dir
## @purpose — Резолвер loki-data директории (spool_validator OBSERVABILITY_DIRS).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def loki_data_dir() -> Path:
    """Resolve loki-data dir (/var/lib/platform/loki-data, 170 W1-A2)."""
    return Path(DEFAULT_LOKI_DATA)


# endregion FUNC_loki_data_dir


# region FUNC_postgres_data_dir
## @purpose — Резолвер postgres-data директории (spool_validator FALLBACK_DIRS).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def postgres_data_dir() -> Path:
    """Resolve postgres-data dir (/var/lib/platform/postgres-data, 170 W1-A2)."""
    return Path(DEFAULT_POSTGRES_DATA)


# endregion FUNC_postgres_data_dir


# region FUNC_bootstrap_state_dir
## @purpose — Резолвер bootstrap state-директории: PLATFORM_STATE_DIR → /var/lib/platform/.bootstrap.
##            Тот же канон, что orchestrator_metrics._HC_DONE_MARKER / phases.docker (env принят).
## @io — ⇥ env: dict | None → ⎋ Path
## @complexity — O(1)
def bootstrap_state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve bootstrap state dir (PLATFORM_STATE_DIR → /var/lib/platform/.bootstrap, 170 W1-A2)."""
    source = os.environ if env is None else env
    return Path(str(source.get("PLATFORM_STATE_DIR", DEFAULT_BOOTSTRAP_STATE_DIR)))


# endregion FUNC_bootstrap_state_dir


# region FUNC_converge_cooldown_file
## @purpose — Резолвер converge cooldown-файла (R9 runtime state, converge/infra.py COOLDOWN_FILE).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def converge_cooldown_file() -> Path:
    """Resolve converge cooldown file (/var/lib/platform/.converge_cooldown.json, 170 W1-A2)."""
    return Path(DEFAULT_CONVERGE_COOLDOWN_FILE)


# endregion FUNC_converge_cooldown_file


# region FUNC_context_pull_ts_path
## @purpose — Резолвер context-overlay pull timestamp-файла (context_overlay.py CONTEXT_PULL_TS_PATH).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def context_pull_ts_path() -> Path:
    """Resolve context pull-ts path (/var/lib/platform/.context-pull-ts, 170 W1-A2)."""
    return Path(DEFAULT_CONTEXT_PULL_TS_PATH)


# endregion FUNC_context_pull_ts_path


# region FUNC_build_cache_dir
## @purpose — Резолвер build-cache директории (build_cache.py cache_dir default).
##            Без env-переменной — чистый канонический дефолт.
## @io — ⇥ None → ⎋ Path
## @complexity — O(1)
def build_cache_dir() -> Path:
    """Resolve build-cache dir (/var/lib/platform/.build-cache, 170 W1-A2)."""
    return Path(DEFAULT_BUILD_CACHE_DIR)


# endregion FUNC_build_cache_dir
