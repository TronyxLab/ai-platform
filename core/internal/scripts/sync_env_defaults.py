#!/usr/bin/env python3
# GREP_SUMMARY: sync_env_defaults, env-example, generator, check, atomic-write
# STRUCTURE: ▶ parse_args → load_platform_env → load_secret_defs → merge → generate → write_atomic
# region MODULE_CONTRACT
## @purpose  Generate .env.example from platform-env.yaml + secret-definitions.yaml.
##           Consolidates env defaults from BOTH SoT sources into a documented .env template.
## @scope    CLI utility; called from Makefile (make sync-env-defaults, make check-env-defaults).
## @invariants
##   - .env.example is GENERATED — never edit manually
##   - All values come from SoT (platform-env.yaml env_defaults section)
##   - Secret charset constraints and gen_commands are pulled from secret-definitions.yaml
##   - --check mode produces byte-identical output or fails with exit code 2
##   - Atomic write (tempfile + os.rename)
## @rationale Eliminates manual sync between .env, .env.example, and compose defaults.
##            Single SoT → single generator → zero drift.
## @changes  Plan 082 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# region CONSTANTS — Section definitions with comments and variable assignments
# Each section is a dict with:
#   - name: section header comment
#   - vars: list of variable entries (var_name, comment_lines, constraint, gen_command)
#   - standalone_comments: strings to emit before the vars
# endregion CONSTANTS


# region FUNC_load_platform_env
def load_platform_env(platform_env_path: Path) -> dict[str, str]:
    """Load merged env_defaults from platform-env.yaml."""
    logger.info("[IMP:7][sync_env] Loading platform-env from %s", platform_env_path)
    with open(platform_env_path) as f:
        data: dict[str, Any] = yaml.safe_load(f)
    env_defaults: dict[str, str] = {}
    raw = data.get("env_defaults", {})
    if isinstance(raw, dict):
        for k, v in raw.items():
            env_defaults[k] = str(v) if v is not None else ""
    logger.info("[IMP:9][sync_env] Loaded %d env_defaults", len(env_defaults))
    return env_defaults


# endregion FUNC_load_platform_env


# region FUNC_load_secret_defs
def load_secret_defs(secret_defs_path: Path) -> dict[str, dict[str, str]]:
    """Load secret definitions (charset, gen_command, ci_default, note)."""
    logger.info("[IMP:7][sync_env] Loading secret-defs from %s", secret_defs_path)
    with open(secret_defs_path) as f:
        data: dict[str, Any] = yaml.safe_load(f)
    secrets: list[dict[str, Any]] = data.get("secrets", [])
    result: dict[str, dict[str, str]] = {}
    for s in secrets:
        name = s.get("name", "")
        if name:
            result[name] = {
                "charset": s.get("charset", ""),
                "gen_command": s.get("gen_command", ""),
                "ci_default": str(s.get("ci_default", "")),
                "note": s.get("note", ""),
            }
    logger.info("[IMP:9][sync_env] Loaded %d secret definitions", len(result))
    return result


# endregion FUNC_load_secret_defs


# region FUNC_generate_env_example
def generate_env_example(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> str:
    """Generate complete .env.example content from SoT data."""
    sd = secret_defs  # alias for brevity

    def get_val(name: str, default: str = "") -> str:
        return env_defaults.get(name, default)

    def sd_get(name: str, field: str) -> str:
        entry = sd.get(name, {})
        val = entry.get(field, "")
        return val if val else ""

    lines: list[str] = []

    # ── Header ──
    lines.append(
        "# GREP_SUMMARY: env-example docker-compose variables grouped-by-module platform context platform-secrets postgres redis clickhouse minio s3 litellm langfuse hermes telegram nginx webnames ssl dns proxy monitoring compose-profiles misc"
    )
    lines.append(
        "# STRUCTURE: Platform/Context → Platform secrets → Postgres → PgBouncer → Redis → ClickHouse → MinIO → S3/Backup → LLM Provider API Keys → LiteLLM → Langfuse → Hermes Dashboard → Hermes API → Telegram → Nginx → SSL/DNS (webnames) → Proxy → Monitoring/Observability → Compose Profiles → Misc"
    )
    lines.append("")
    lines.append("# region MODULE_CONTRACT")
    lines.append(
        "## @purpose  Env-шаблон для docker compose — единый источник всех переменных окружения для platform-модулей."
    )
    lines.append("##           Канонический документ для локальной разработки и CI тестов.")
    lines.append(
        "## @scope    Все compose-переменные, сгруппированные по модулям: postgres, pgbouncer, redis, clickhouse,"
    )
    lines.append(
        "##           minio, s3/backup, litellm, langfuse, hermes-agent, telegram, nginx, proxy, monitoring, misc."
    )
    lines.append("## @invariants")
    lines.append(
        "##   1. ⚠️ CONSTRAINT-комментарии — единый формат \\`# ⚠️ CONSTRAINT: <VAR> must match <regex>\\` для всех"
    )
    lines.append("##      паролей, встраиваемых в URL без URL-encoding.")
    lines.append("##   2. REGEX: ^[A-Za-z0-9._-]+$ — charset constraint для всех URL-безопасных паролей.")
    lines.append("##   3. Канонический источник — .env.example. .env — зеркало без документации.")
    lines.append("##   4. Каждая переменная определена ровно в одной секции (no duplicate keys).")
    lines.append("##   5. Secrets не содержат реальных production-значений — только test-заглушки.")
    lines.append(
        "##   6. GENERATED by sync_env_defaults.py — DO NOT EDIT. SoT: platform-env.yaml env_defaults section."
    )
    lines.append(
        "## @rationale Charset constraint предотвращает баг pgbouncer-краша при спецсимволах в POSTGRES_PASSWORD"
    )
    lines.append("##           (DevPlan 014 STRESS_TEST_REPORT 2026-07-20). Единый формат упрощает grep-поиск и")
    lines.append("##           автоматическую валидацию constraint-покрытия.")
    lines.append("## @changes")
    lines.append(
        "##   2026-07-21 — TASK-3: добавлены CONSTRAINT-комментарии для POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD,"
    )
    lines.append(
        "##               MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, PLATFORM_MASTER_PASSWORD, HERMES_DASHBOARD_PASSWORD"
    )
    lines.append(
        "##   2026-07-22 — Добавлен WEBNAMES_API_KEY (SSL/DNS Challenge секция) для acme.sh DNS-01 wildcard TLS"
    )
    lines.append("##   2026-07-26 — Plan 082: auto-generated from platform-env.yaml + secret-definitions.yaml")
    lines.append("# endregion MODULE_CONTRACT")
    lines.append("")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("# .env.example — переменные окружения для docker compose")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("# Назначение: шаблон для локальной разработки и тестов.")
    lines.append("# Копировать: cp .env.example .env (НЕ коммитить .env!)")
    lines.append("# Используется: core/modules/*/docker-compose.base.yml + CI workflow platform-test.yml")
    lines.append("#")
    lines.append("# ⚠️ GENERATED by sync_env_defaults.py — DO NOT EDIT MANUALLY.")
    lines.append("#    SoT: platform-env.yaml env_defaults section + secret-definitions.yaml.")
    lines.append("#    Регенерация: make sync-env-defaults")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("")

    # ── Platform / Context ──
    lines.append("# ── Platform / Context ─────────────────────────────────────────────────────")
    lines.append("# Контекст (GitHub org) для изоляции окружения")
    lines.append("CONTEXT=" + get_val("CONTEXT", "test"))
    lines.append("# Имя ноды (для деплоя и алертов)")
    lines.append("NODE_NAME=" + get_val("NODE_NAME", "test-node"))
    lines.append("# PLATFORM_DOMAIN — домен платформы для dev-сертификатов и vhost-роутинга.")
    lines.append("# Базовое значение: ai-platform.local. При загруженном контексте (context = GitHub org):")
    lines.append("#   PLATFORM_DOMAIN=<context>.local, SAN дополнительно включает *.${PLATFORM_DOMAIN}.")
    lines.append("# Сертификаты генерируются автоматически через generate-dev-certs.sh (make dev-certs).")
    lines.append("# @domain-scheme DevPlan 012 — test.local упразднён.")
    lines.append("PLATFORM_DOMAIN=" + get_val("PLATFORM_DOMAIN", "ai-platform.local"))
    lines.append("# ⚠️ Pin to a specific version — avoid float tag (W7 fix).")
    lines.append("# Update this tag when you want to use a newer context overlay image.")
    lines.append("# Available tags: ghcr.io/<context>/hermes-agent-context")
    lines.append("CONTEXT_IMAGE=" + get_val("CONTEXT_IMAGE", "ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1"))

    # ── Platform secrets ──
    lines.append("# ── Platform secrets ───────────────────────────────────────────────────────")
    lines.append(
        "# Master credentials — unified auth for all platform services (status-page, Prometheus, Loki, Grafana, Langfuse, Hermes)."
    )
    lines.append(
        "# Overridable per-service via service-specific env var (e.g. GF_SECURITY_ADMIN_PASSWORD overrides PLATFORM_MASTER_PASSWORD for Grafana)."
    )
    lines.append("# ⚠️ NOT for production — set via SOPS/age encrypted secrets on VPS.")
    constraint_master_pwd = sd_get("PLATFORM_MASTER_PASSWORD", "charset")
    if constraint_master_pwd:
        lines.append("# ⚠️ CONSTRAINT: PLATFORM_MASTER_PASSWORD must match " + constraint_master_pwd)
    lines.append("# Unified auth: все сервис-пароли (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD,")
    lines.append("# LANGFUSE_INIT_USER_PASSWORD) инициализируются из PLATFORM_MASTER_PASSWORD через secrets-init.sh.")
    lines.append("# Сервис-пользователи (HERMES_DASHBOARD_USERNAME, GF_SECURITY_ADMIN_USER, LANGFUSE_INIT_USER_EMAIL)")
    lines.append("# инициализируются из PLATFORM_MASTER_EMAIL (admin@PLATFORM_DOMAIN) через secrets-init.sh.")
    lines.append("# Любая переменная может быть переопределена явно — operator-defined значения сохраняются.")
    gen_master_pwd = sd_get("PLATFORM_MASTER_PASSWORD", "gen_command")
    if gen_master_pwd:
        lines.append("# Генерация: " + gen_master_pwd)
    lines.append("PLATFORM_MASTER_EMAIL=" + get_val("PLATFORM_MASTER_EMAIL", "admin@test.local"))
    lines.append("PLATFORM_MASTER_PASSWORD=" + get_val("PLATFORM_MASTER_PASSWORD", "test-master-password"))
    lines.append("")
    lines.append("# AGE_SECRET_KEY — master age-key для SOPS-расшифровки platform-secrets.")
    lines.append("# ⚠️ REQUIRED (env_requires of platform-secrets module) — без него systemd oneshot fails-closed.")
    lines.append("# Генерация: age-keygen -o keys.txt → извлечь публичный/приватный ключ")
    lines.append("# ⚠️ NOT for production .env — только SOPS-encrypted secrets на VPS.")
    constraint_age = sd_get("AGE_SECRET_KEY", "charset")
    if constraint_age:
        lines.append("# ⚠️ CONSTRAINT: AGE_SECRET_KEY must match " + constraint_age)
    lines.append(
        "AGE_SECRET_KEY=" + get_val("AGE_SECRET_KEY", "AGE-SECRET-KEY-TEST1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    )

    # ── Postgres ──
    lines.append("")
    lines.append("# ── Postgres (shared-db) ───────────────────────────────────────────────────")
    lines.append("POSTGRES_USER=" + get_val("POSTGRES_USER", "postgres"))
    constraint_pg_pwd = sd_get("POSTGRES_PASSWORD", "charset")
    if constraint_pg_pwd:
        lines.append("# ⚠️ CONSTRAINT: POSTGRES_PASSWORD must match " + constraint_pg_pwd)
    lines.append("# Пароль встраивается в DATABASE_URL/DATABASE_URLS без URL-encoding. Генерация: openssl rand -hex 32")
    lines.append("POSTGRES_PASSWORD=" + get_val("POSTGRES_PASSWORD", "test-pg-pwd"))
    lines.append("POSTGRES_DB=" + get_val("POSTGRES_DB", "platform"))
    lines.append("# Postgres через pgbouncer (default: 6432)")
    lines.append("POSTGRES_PORT=" + str(get_val("POSTGRES_PORT", "6432")))
    lines.append("POSTGRES_HOST=" + get_val("POSTGRES_HOST", "pgbouncer"))

    # ── PgBouncer ──
    lines.append("")
    lines.append("# ── PgBouncer (connection pooler for Postgres) ─────────────────────────────")
    lines.append("PGBOUNCER_IMAGE=" + get_val("PGBOUNCER_IMAGE", "edoburu/pgbouncer:v1.25.2-p0"))

    # ── Redis ──
    lines.append("")
    lines.append("# ── Redis (cache) ──────────────────────────────────────────────────────────")
    lines.append("REDIS_PORT=" + str(get_val("REDIS_PORT", "6379")))
    lines.append("REDIS_HOST=" + get_val("REDIS_HOST", "redis"))

    # ── ClickHouse ──
    lines.append("")
    lines.append("# ── ClickHouse (Analytical DB) ─────────────────────────────────────────────")
    lines.append("CLICKHOUSE_URL=" + get_val("CLICKHOUSE_URL", "http://clickhouse:8123"))
    lines.append("CLICKHOUSE_USER=" + get_val("CLICKHOUSE_USER", "default"))
    constraint_ch_pwd = sd_get("CLICKHOUSE_PASSWORD", "charset")
    if constraint_ch_pwd:
        lines.append("# ⚠️ CONSTRAINT: CLICKHOUSE_PASSWORD must match " + constraint_ch_pwd)
    lines.append("# Пароль встраивается в CLICKHOUSE_MIGRATION_URL без URL-encoding. Генерация: openssl rand -hex 32")
    lines.append("CLICKHOUSE_PASSWORD=" + get_val("CLICKHOUSE_PASSWORD", "test-clickhouse-pwd-not-for-prod"))
    lines.append("CLICKHOUSE_HTTP_PORT=" + str(get_val("CLICKHOUSE_HTTP_PORT", "8123")))
    lines.append("CLICKHOUSE_NATIVE_PORT=" + str(get_val("CLICKHOUSE_NATIVE_PORT", "9000")))

    # ── MinIO ──
    lines.append("")
    lines.append("# ── MinIO (local S3, dev) ────────────────────────────────────────────────")
    lines.append("MINIO_PORT=" + str(get_val("MINIO_PORT", "9000")))
    lines.append("MINIO_CONSOLE_PORT=" + str(get_val("MINIO_CONSOLE_PORT", "9001")))
    lines.append("# ⚠️ REQUIRED (env_requires of minio module) — no defaults in production; set via SOPS secrets")
    constraint_minio_user = sd_get("MINIO_ROOT_USER", "charset")
    if constraint_minio_user:
        lines.append("# ⚠️ CONSTRAINT: MINIO_ROOT_USER must match " + constraint_minio_user)
    lines.append("MINIO_ROOT_USER=" + get_val("MINIO_ROOT_USER", "minioadmin"))
    lines.append("# ⚠️ REQUIRED (env_requires of minio module) — no defaults in production; set via SOPS secrets")
    constraint_minio_pwd = sd_get("MINIO_ROOT_PASSWORD", "charset")
    if constraint_minio_pwd:
        lines.append("# ⚠️ CONSTRAINT: MINIO_ROOT_PASSWORD must match " + constraint_minio_pwd)
    lines.append("MINIO_ROOT_PASSWORD=" + get_val("MINIO_ROOT_PASSWORD", "minioadmin"))

    # ── S3 / Backup ──
    lines.append("")
    lines.append("# ── S3 / Backup ──────────────────────────────────────────────────────────────")
    lines.append("S3_ENDPOINT_URL=" + get_val("S3_ENDPOINT_URL", "https://s3.timeweb.cloud"))
    lines.append("S3_REGION=" + get_val("S3_REGION", "ru-1"))
    lines.append("S3_PREFIX=" + get_val("S3_PREFIX", "platform/backups"))
    lines.append("S3_BUCKET=" + get_val("S3_BUCKET", "test-bucket"))
    lines.append("S3_ACCESS_KEY=" + get_val("S3_ACCESS_KEY", "test-access-key"))
    lines.append("S3_SECRET_KEY=" + get_val("S3_SECRET_KEY", "test-secret-key"))
    lines.append("# Дублирующие ключи для upload-s3.sh (AWS SDK совместимость)")
    lines.append("AWS_ACCESS_KEY_ID=${S3_ACCESS_KEY}")
    lines.append("AWS_SECRET_ACCESS_KEY=${S3_SECRET_KEY}")
    lines.append("PLATFORM_CONTEXT=" + get_val("PLATFORM_CONTEXT", "personal"))

    # ── LLM Provider API Keys ──
    lines.append("")
    lines.append("# ── LLM Provider API Key ───────────────────────────────────────────────────")
    lines.append("DEEPSEEK_API_KEY=" + get_val("DEEPSEEK_API_KEY", "sk-placeholder-key-for-ci"))

    # ── LiteLLM ──
    lines.append("")
    lines.append("# ── LiteLLM (LLM Gateway) ────────────────────────────────────────────────────")
    lines.append("# ⚠️ LITELLM_MASTER_KEY — ОБЯЗАТЕЛЕН для production. Без него LiteLLM стартует")
    lines.append("#    но все API-запросы требующие авторизации будут отклонены (401).")
    gen_litellm = sd_get("LITELLM_MASTER_KEY", "gen_command")
    if gen_litellm:
        lines.append("#    Генерировать: " + gen_litellm)
    lines.append("LITELLM_MASTER_KEY=" + get_val("LITELLM_MASTER_KEY", "sk-ci-test-master-key"))
    lines.append("# Опциональная лицензия (оставить пустым для community-версии)")
    lines.append("LITELLM_LICENSE=" + get_val("LITELLM_LICENSE", ""))
    # LITELLM_METRICS_TOKEN removed — unified with LITELLM_MASTER_KEY
    lines.append("LITELLM_PORT=" + str(get_val("LITELLM_PORT", "4000")))
    lines.append("# URL для healthcheck LiteLLM (default: http://litellm:4000/health)")
    lines.append("LITELLM_HEALTH_URL=" + get_val("LITELLM_HEALTH_URL", "http://litellm:4000/health"))
    lines.append("# Клиенты (Hermes, внешние тулы) шлют запросы через LiteLLM")
    lines.append("OPENAI_BASE_URL=" + get_val("OPENAI_BASE_URL", "http://litellm:4000"))
    lines.append("# Virtual key for Hermes-agent — provisioned by make provision-llm (unlimited profile)")
    lines.append("LITELLM_API_KEY=" + get_val("LITELLM_API_KEY", "sk-placeholder-litellm-api-key"))

    # ── Langfuse ──
    lines.append("")
    lines.append("# ── Langfuse (LLM Tracing) ───────────────────────────────────────────────────")
    lines.append("NEXTAUTH_SECRET=" + get_val("NEXTAUTH_SECRET", "ci-test-nextauth-secret-32-chars-min!!"))
    lines.append("NEXTAUTH_URL=" + get_val("NEXTAUTH_URL", "http://langfuse:3000"))
    lines.append("SALT=" + get_val("SALT", "ci-test-salt-value"))
    lines.append("# Headless init — ТРЕБУЕТСЯ только при ПЕРВОМ запуске Langfuse.")
    lines.append("# После инициализации эти переменные можно удалить из secrets.env.")
    lines.append("# Генерировать:")
    gen_org = sd_get("LANGFUSE_INIT_ORG_ID", "gen_command")
    gen_proj = sd_get("LANGFUSE_INIT_PROJECT_ID", "gen_command")
    gen_pub = sd_get("LANGFUSE_PUBLIC_KEY", "gen_command")
    gen_sec = sd_get("LANGFUSE_SECRET_KEY", "gen_command")
    if gen_org:
        lines.append("#   LANGFUSE_INIT_ORG_ID:      " + gen_org)
    if gen_proj:
        lines.append("#   LANGFUSE_INIT_PROJECT_ID:  " + gen_proj)
    if gen_pub:
        lines.append("#   LANGFUSE_PUBLIC_KEY:       " + gen_pub)
    if gen_sec:
        lines.append("#   LANGFUSE_SECRET_KEY:       " + gen_sec)
    lines.append("# · Headless init: эти же ключи передаются в langfuse как LANGFUSE_INIT_PROJECT_*")
    lines.append("# · Источник: https://langfuse.com/self-hosting/administration/headless-initialization")
    lines.append("LANGFUSE_INIT_ORG_ID=" + get_val("LANGFUSE_INIT_ORG_ID", "ci-test-org"))
    lines.append("LANGFUSE_INIT_PROJECT_ID=" + get_val("LANGFUSE_INIT_PROJECT_ID", "ci-test-project"))
    lines.append("LANGFUSE_INIT_USER_EMAIL=" + get_val("LANGFUSE_INIT_USER_EMAIL", "admin@ai-platform.local"))
    lines.append("LANGFUSE_INIT_USER_PASSWORD=" + get_val("LANGFUSE_INIT_USER_PASSWORD", "ci-test-langfuse-pwd"))
    lines.append("# ⚠️ LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY — генерируются после init")
    lines.append("LANGFUSE_PUBLIC_KEY=" + get_val("LANGFUSE_PUBLIC_KEY", "ci-test-public-key"))
    lines.append("LANGFUSE_SECRET_KEY=" + get_val("LANGFUSE_SECRET_KEY", "ci-test-secret-key"))
    lines.append("LANGFUSE_PORT=" + str(get_val("LANGFUSE_PORT", "3001")))
    lines.append("# S3 для Langfuse event-логов (bucket и path-style)")
    lines.append("LANGFUSE_S3_BUCKET=" + get_val("LANGFUSE_S3_BUCKET", "langfuse-events"))
    lines.append("LANGFUSE_S3_FORCE_PATH_STYLE=" + get_val("LANGFUSE_S3_FORCE_PATH_STYLE", "true"))

    # ── Hermes Dashboard ──
    lines.append("")
    lines.append("# ── Hermes Agent — Dashboard ──────────────────────────────────────────────")
    lines.append("# Boolean — включает/выключает dashboard UI (default: false)")
    lines.append("HERMES_DASHBOARD=" + get_val("HERMES_DASHBOARD", "false"))
    lines.append("HERMES_DASHBOARD_USERNAME=" + get_val("HERMES_DASHBOARD_USERNAME", "admin@ai-platform.local"))
    constraint_hd_pwd = sd_get("HERMES_DASHBOARD_PASSWORD", "charset")
    if constraint_hd_pwd:
        lines.append("# ⚠️ CONSTRAINT: HERMES_DASHBOARD_PASSWORD must match " + constraint_hd_pwd)
    lines.append("# Инициализируется из PLATFORM_MASTER_PASSWORD через secrets-init.sh (если не задан явно)")
    lines.append("HERMES_DASHBOARD_PASSWORD=" + get_val("HERMES_DASHBOARD_PASSWORD", "test-db-pwd"))
    lines.append("# 🔗 Цепочка: HERMES_DASHBOARD_USERNAME/PASSWORD → compose (hermes-agent:110-111)")
    lines.append("#    → контейнерные BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD.")
    lines.append("#    Переменные HERMES_DASHBOARD_BASIC_AUTH_* УДАЛЕНЫ — они не потребляются ни одним")
    lines.append("#    compose-сервисом. Единственный consumer Basic Auth — сам Hermes Agent Dashboard.")
    lines.append("#    nginx-модуль не использует эти переменные; htpasswd в nginx только для Prometheus/Loki.")
    lines.append("# Dashboard UI порт (default: 9119)")
    lines.append("HERMES_DASHBOARD_PORT=" + str(get_val("HERMES_DASHBOARD_PORT", "9119")))
    lines.append("# Десктопный порт Hermes Agent (default: 8642)")
    lines.append("HERMES_DESKTOP_PORT=" + str(get_val("HERMES_DESKTOP_PORT", "8642")))

    # ── Hermes API ──
    lines.append("")
    lines.append("# ── Hermes Agent — API Server ─────────────────────────────────────────────")
    lines.append("# Встроенный HTTP API-сервер Hermes Agent.")
    lines.append("# Генерировать ключ: openssl rand -hex 32")
    lines.append("API_SERVER_ENABLED=" + get_val("API_SERVER_ENABLED", "false"))
    lines.append("API_SERVER_KEY=" + get_val("API_SERVER_KEY", "test-api-server-key-for-ci-only"))
    lines.append("API_SERVER_HOST=" + get_val("API_SERVER_HOST", "0.0.0.0"))

    # ── Telegram ──
    lines.append("")
    lines.append("# ── Telegram ─────────────────────────────────────────────────────────────────")
    lines.append("# Bot token для Hermes Agent и Grafana Alerting")
    lines.append("TELEGRAM_BOT_TOKEN=" + get_val("TELEGRAM_BOT_TOKEN", "1234567890:test-telegram-bot-token-for-ci"))
    lines.append("# Разрешённые пользователи (user IDs, comma-separated)")
    lines.append("TELEGRAM_ALLOWED_USERS=" + get_val("TELEGRAM_ALLOWED_USERS", ""))
    lines.append("# Чат для критических алертов")
    lines.append("TELEGRAM_CHAT_ID_CRITICAL=" + get_val("TELEGRAM_CHAT_ID_CRITICAL", ""))
    lines.append("# Чат для warning-алертов (можно совпадать с CRITICAL)")
    lines.append("TELEGRAM_CHAT_ID_WARNING=" + get_val("TELEGRAM_CHAT_ID_WARNING", ""))

    # ── Nginx ──
    lines.append("")
    lines.append("# ── Nginx (Edge) ──────────────────────────────────────────────────────────")
    lines.append("# Директория конфигурации nginx.")
    lines.append("# Локальный стек: dev-config (Docker-DNS upstreams + mkcert TLS).")
    lines.append("# Путь резолвится относительно core/modules/nginx/ (include-семантика compose).")
    lines.append("#   docker compose config: source → .../core/modules/nginx/dev-config/nginx.conf ✓")
    lines.append("# config/ требует letsencrypt-сертификаты (VPS) — не работает локально.")
    lines.append("NGINX_CONF_DIR=" + get_val("NGINX_CONF_DIR", "./dev-config"))
    lines.append("# NGINX_CERT_DIR — директория TLS-сертификатов (Let's Encrypt).")
    lines.append('# VPS: не задавать (default "/etc/letsencrypt", смонтирован в контейнер).')
    lines.append("# Локально: ./dev-certs (macOS Docker Desktop не шарит /etc).")
    lines.append("NGINX_CERT_DIR=" + get_val("NGINX_CERT_DIR", "./dev-certs"))
    lines.append("# NGINX_OVERLAY_DIR — каталог overlay-vhosts из node-configs.")
    lines.append("# VPS: /opt/node-configs/<node>/overlays/nginx (см. add-vhost.sh конвенцию).")
    lines.append('# Локально не задавать — default "./overlays" пустой и не влияет.')
    lines.append("# Конвенция сиблингов: плоский каталог *.conf (глубина 1, non-recursive include).")
    lines.append("NGINX_OVERLAY_DIR=" + get_val("NGINX_OVERLAY_DIR", ""))
    lines.append("# HTTP/HTTPS порты nginx (default: 80/443; кастомные для dev-окружения без sudo)")
    lines.append("NGINX_HTTP_PORT=" + str(get_val("NGINX_HTTP_PORT", "80")))
    lines.append("NGINX_HTTPS_PORT=" + str(get_val("NGINX_HTTPS_PORT", "443")))
    lines.append("NGINX_EXPORTER_PORT=" + str(get_val("NGINX_EXPORTER_PORT", "9113")))

    # ── SSL / DNS Challenge ──
    lines.append("")
    lines.append("# ── SSL / DNS Challenge (acme.sh) ──────────────────────────────────────────────")
    lines.append("# WEBNAMES_API_KEY — API-ключ webnames.ru для acme.sh DNS-01 wildcard TLS.")
    lines.append("# Требуется при PLATFORM_CERTBOT_DNS_PLUGIN=webnames.")
    lines.append("# Хранится в SOPS-encrypted файле на VPS (/opt/node-configs/secrets/<node>.enc.yaml).")
    lines.append("# На VPS: sourced из /run/platform/secrets.env перед step 14 (ssl provision).")
    lines.append("# Локально: не требуется (используются dev-certs через mkcert/openssl).")
    lines.append("WEBNAMES_API_KEY=" + get_val("WEBNAMES_API_KEY", "*test-webnames-api-key"))

    # ── Proxy ──
    lines.append("")
    lines.append("# ── Proxy (Tor/Privoxy, опционально) ─────────────────────────────────────────")
    lines.append("HTTP_PROXY=" + get_val("HTTP_PROXY", ""))
    lines.append("HTTPS_PROXY=" + get_val("HTTPS_PROXY", ""))
    lines.append("# Список адресов, исключённых из прокси.")
    lines.append("# ⚠️ Канонический источник: platform-env.yaml proxy.no_proxy_internal.")
    lines.append("#    Этот список должен ⊇ no_proxy_internal — гейт T8.5 валидирует.")
    lines.append("# base: внутренние Docker-сервисы; внешние API-хосты добавляются по контексту.")
    lines.append(
        "NO_PROXY="
        + get_val(
            "NO_PROXY",
            "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus",
        )
    )

    # ── Monitoring ──
    lines.append("")
    lines.append("# ── Monitoring / Observability ───────────────────────────────────────────")
    lines.append("# Grafana")
    lines.append("GF_SECURITY_ADMIN_USER=" + get_val("GF_SECURITY_ADMIN_USER", "admin@ai-platform.local"))
    lines.append("GF_SECURITY_ADMIN_PASSWORD=" + get_val("GF_SECURITY_ADMIN_PASSWORD", "testpass"))
    lines.append("GRAFANA_PORT=" + str(get_val("GRAFANA_PORT", "3000")))
    lines.append("# Prometheus")
    lines.append("PROMETHEUS_TARGETS_DIR=" + get_val("PROMETHEUS_TARGETS_DIR", "/opt/platform/prometheus-targets"))
    lines.append("PROMETHEUS_RULES_DIR=" + get_val("PROMETHEUS_RULES_DIR", "/opt/platform/prometheus-rules"))
    lines.append("PROMETHEUS_PORT=" + str(get_val("PROMETHEUS_PORT", "9090")))
    lines.append("# Loki (logging backend)")
    lines.append("LOKI_PORT=" + str(get_val("LOKI_PORT", "3100")))
    lines.append("# Infra-metrics exporters")
    lines.append("CADVISOR_PORT=" + str(get_val("CADVISOR_PORT", "8080")))
    lines.append("NODE_EXPORTER_PORT=" + str(get_val("NODE_EXPORTER_PORT", "9100")))

    # ── Compose Profiles ──
    lines.append("")
    lines.append("# ── Compose Profiles ──────────────────────────────────────────────────────")
    lines.append("# Все 12 профилей — активирует все модули при make up (без явного MODULES=)")
    lines.append(
        "COMPOSE_PROFILES="
        + get_val(
            "COMPOSE_PROFILES",
            "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio",
        )
    )

    # ── Misc ──
    lines.append("")
    lines.append("# ── Misc ──────────────────────────────────────────────────────────────────────")
    lines.append("# Таймаут проверки зависимостей в секундах (default: 2.0)")
    lines.append("DEPENDENCY_CHECK_TIMEOUT=" + get_val("DEPENDENCY_CHECK_TIMEOUT", "2.0"))

    # ── GitHub Actions secrets ──
    lines.append("")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("# GitHub Actions secrets (NOT .env vars — create in repo Settings → Secrets)")
    lines.append("# These are consumed by CI/CD workflows, NOT by docker-compose services.")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("#")
    lines.append("# VPS_HOST — SSH hostname/IP for VPS node (used by platform-deploy.yml)")
    lines.append("# VPS_SSH_KEY — SSH private key for VPS access (used by platform-deploy.yml)")
    lines.append(
        "# CI_DEPLOY_KEY — SSH deploy key for ci-deploy forced-command (used by deploy-project.yml, platform-deploy.yml)"
    )
    lines.append(
        "# DOCKER_HUB_USERNAME — Docker Hub username for authenticated pulls (used by platform-test.yml, platform-deploy.yml)"
    )
    lines.append(
        "# DOCKER_HUB_TOKEN — Docker Hub PAT for authenticated pulls (used by platform-test.yml, platform-deploy.yml)"
    )
    lines.append("# SSH_HOST — SSH hostname passed as workflow_call secret (used by platform-deploy.yml)")
    lines.append(
        "# SSH_KEY — SSH private key for ci-deploy passed as workflow_call secret. ≡ CI_DEPLOY_KEY (один ключ, две роли: rsync + forced-command)"
    )
    lines.append("# E2E_BASE_URL — Base URL for E2E smoke test health endpoint (used by platform-deploy.yml)")
    lines.append("# E2E_GRAFANA_URL — Grafana URL for E2E smoke test health check (used by platform-deploy.yml)")
    lines.append("# GHCR_PULL_TOKEN — Fine-grained PAT for ghcr.io read:packages")
    lines.append("GHCR_PULL_TOKEN=" + get_val("GHCR_PULL_TOKEN", "ghp_test-token-for-ci-only"))
    lines.append("# GHCR_PUSH_TOKEN — Fine-grained PAT for ghcr.io write:packages (L2 push)")
    lines.append("#   Create: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens")
    lines.append("#   Repository access: tronyxlab/ai-platform")
    lines.append("#   Permissions: Packages → Write")
    lines.append("GHCR_PUSH_TOKEN=" + get_val("GHCR_PUSH_TOKEN", ""))
    lines.append("")
    lines.append(
        "# GIT_MIRROR_TOKEN — Token for git mirror operations (optional, SSH fallback — used by platform-deploy.yml)"
    )
    lines.append("# NODE_HOST_MAP — JSON mapping of node names to SSH hosts, org variable (used by deploy-project.yml)")

    return "\n".join(lines) + "\n"


# endregion FUNC_generate_env_example


# region FUNC_write_atomic
def write_atomic(content: str, output_path: Path) -> None:
    """Write content atomically using tempfile + os.rename."""
    logger.info("[IMP:7][sync_env] Writing %d bytes to %s", len(content), output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".env.example",
        dir=output_path.parent,
        delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.rename(tmp.name, output_path)
    except Exception:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise
    logger.info("[IMP:9][sync_env] Written atomically to %s", output_path)


# endregion FUNC_write_atomic


# region FUNC_main
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate .env.example from SoT")
    parser.add_argument("--platform-env", required=True, type=str, help="Path to platform-env.yaml")
    parser.add_argument("--secret-defs", required=True, type=str, help="Path to core/secret-definitions.yaml")
    parser.add_argument("--output", required=True, type=str, help="Path to write .env.example")
    parser.add_argument("--check", action="store_true", help="Dry-run: diff with existing, exit 2 on divergence")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[IMP:%(levelno)s][sync_env] %(message)s", stream=sys.stderr)

    platform_env_path = Path(args.platform_env).resolve()
    secret_defs_path = Path(args.secret_defs).resolve()
    output_path = Path(args.output).resolve()

    if not platform_env_path.is_file():
        logger.error("platform-env.yaml not found: %s", platform_env_path)
        sys.exit(1)
    if not secret_defs_path.is_file():
        logger.error("secret-definitions.yaml not found: %s", secret_defs_path)
        sys.exit(1)

    env_defaults = load_platform_env(platform_env_path)
    secret_defs = load_secret_defs(secret_defs_path)
    generated = generate_env_example(env_defaults, secret_defs)

    if args.check:
        if not output_path.is_file():
            logger.error("[IMP:9][sync_env][CHECK] Output file %s does not exist — cannot compare", output_path)
            sys.exit(2)
        existing = output_path.read_text()
        if existing != generated:
            diff = difflib.unified_diff(
                existing.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile=str(output_path),
                tofile="generated",
            )
            sys.stderr.writelines(diff)
            logger.error(
                "[IMP:9][sync_env][CHECK] Divergence detected — .env.example is stale. Run: make sync-env-defaults"
            )
            sys.exit(2)
        logger.info("[IMP:9][sync_env][CHECK] .env.example is up-to-date")
        sys.exit(0)

    write_atomic(generated, output_path)
    logger.info("[IMP:9][sync_env] .env.example generated at %s", output_path)


# endregion FUNC_main

if __name__ == "__main__":
    main()
