#!/usr/bin/env python3
# GREP_SUMMARY: sync_env_defaults, env-example, generator, check, atomic-write
# STRUCTURE: ▶ parse_args → ◇ shared.load_platform_env→.env_defaults → ◇ shared.load_secret_definitions→_project_secret_defs → merge → generate → write_atomic
# region MODULE_CONTRACT
## @purpose  Generate .env.example from platform-env.yaml + secret-definitions.yaml.
##           Consolidates env defaults from BOTH SoT sources into a documented .env template.
## @scope    CLI utility; called from Makefile (make generate-env-example; freshness — G5 в check-manifests, DevPlan 171 W1.2).
## @invariants
##   - .env.example is GENERATED — never edit manually
##   - All values come from SoT (platform-env.yaml env_defaults section)
##   - Secret charset constraints and gen_commands are pulled from secret-definitions.yaml
##   - --check mode produces byte-identical output or fails with exit code 1
##   - Atomic write (tempfile + os.rename)
##   - Порты — ОБЯЗАТЕЛЬНОЕ чтение SoT (_get_val_required): без fallback-литералов
## @rationale Eliminates manual sync between .env, .env.example, and compose defaults.
##            Single SoT → single generator → zero drift.
## @changes  Plan 082 — created
##           2026-08-02 | DevPlan 118 C4 — 19 fallback-литералов портов → _get_val_required
##                      (обязательное чтение SoT, fail-fast при отсутствии ключа)
##           2026-08-02 | DevPlan 119 G4 (AUDIT-4 S1) — fallback-литералы
##                      AGE_SECRET_KEY/TELEGRAM_BOT_TOKEN → _get_secret_def_field(..., "ci_default")
##                      из secret-definitions.yaml (SoT); _section_telegram принял secret_defs
##           2026-08-16 | DevPlan 177 W3.5 — локальные load_platform_env/load_secret_defs →
##                      shared/yaml_loader (типизированные читатели); локальна только
##                      проекция полей secret-defs (_project_secret_defs, правило 5 инвентаря)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import difflib
import logging
import sys
from pathlib import Path
from typing import ClassVar, cast

# Standalone CLI bootstrap: `python3 core/internal/scripts/sync_env_defaults.py` (makefile)
# не имеет `core` пакета на sys.path — добавляем repo root (паттерн project_registry.py L36-39,
# позволяет lazy-импорты core.internal.shared.deploy_paths в codegen-секциях, B2/B3).
# ⚠️ ДОЛЖЕН быть ВЫШЕ импортов core.* (DevPlan 119 E5: atomic_writer импорт — после bootstrap;
#   иначе system python3 падает ModuleNotFoundError: No module named 'core').
if __name__ == "__main__" or not __package__:
    # ⚠️ TRAP[BUG] · 2026-08-13 · P1 · Path-объект в sys.path ломал standalone-запуск (DevPlan 163 W-B)
    # · Symptom: ModuleNotFoundError: No module named 'core' при python3 core/internal/scripts/sync_env_defaults.py
    # · Root: PTH-автофикс заменил строку на Path; sys.path требует str (сравнение importlib
    # ·   с str-путями; Path-элемент молча не матчится) → G5/G6 gate падал
    # · Fix: _REPO_ROOT остаётся Path для читаемости, в sys.path кладётся str(_REPO_ROOT)
    # · Prevention: TID251/запрет не-path-в-sys.path; ruff-автофиксы PTH не трогают sys.path.insert
    _REPO_ROOT = Path(Path(Path(__file__).parent, "..", "..", "..")).resolve()
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_text as _atomic_write_text

# DevPlan 177 W3.5: типизированные SoT-YAML читатели — единый shared/yaml_loader.py
# (load_platform_env → PlatformEnv; load_secret_definitions → raw secrets list).
from core.internal.shared.yaml_loader import load_platform_env
from core.internal.shared.yaml_loader import load_secret_definitions as _load_secret_definitions

logger = logging.getLogger(__name__)

# region CONSTANTS — Section definitions with comments and variable assignments
# Each section is a dict with:
#   - name: section header comment
#   - vars: list of variable entries (var_name, comment_lines, constraint, gen_command)
#   - standalone_comments: strings to emit before the vars
# endregion CONSTANTS


# region FUNC_load_secret_defs
DIFF_LINES_MAX: int = 20  # обрезка дифф-вывода при --check

# Проекция полей secret-definitions, специфичная для .env.example генератора
# (charset/gen_command/ci_default/note). YAML-чтение — в shared/yaml_loader.load_secret_definitions
# (DevPlan 177 W3.5); здесь только field-экстракция — инвентарь shared: правило 5
# («одноразовая утилита для одного потребителя → живёт рядом с потребителем»).
_SECRET_FIELDS: tuple[str, ...] = ("charset", "gen_command", "ci_default", "note")


def _project_secret_defs(raw_secrets: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    """Project raw secret-definitions entries into {name: {field: str}} for section builders.

    ## @purpose  Преобразует сырой список secret-записей из shared-читателя в dict-форму
    ##            генератора: name → {charset, gen_command, ci_default, note} (str, "" дефолт).
    ##            Non-dict/без-имени записи пропускаются (семантика прежнего load_secret_defs).
    ## @io        ⇥ raw_secrets: list[dict[str, object]] → ⎋ dict[str, dict[str, str]]
    ## @complexity O(S * F) где S = число секретов, F = число извлекаемых полей
    """
    result: dict[str, dict[str, str]] = {}
    for entry in raw_secrets:
        name = entry.get("name")
        if name:
            # W11: name is a YAML string key — cast (no runtime coercion)
            result[cast(str, name)] = {field_name: str(entry.get(field_name, "")) for field_name in _SECRET_FIELDS}
    logger.info("[IMP:9][sync_env] Projected %d secret definitions", len(result))
    return result


def load_secret_defs(secret_defs_path: Path) -> dict[str, dict[str, str]]:
    """Load secret definitions (charset, gen_command, ci_default, note) via shared reader.

    ## @purpose  Фасад проекции над shared/yaml_loader.load_secret_definitions — call-семантика
    ##            прежнего локального load_secret_defs сохранена (DevPlan 177 W3.5).
    ##            Отсутствующий файл → [] → {} (shared-читатель логирует warning; main()
    ##            pre-flight проверяет существование файла до вызова).
    ## @io        ⇥ secret_defs_path: Path → ⎋ dict[str, dict[str, str]]
    ## @complexity O(S * F) — чтение + проекция
    """
    logger.info("[IMP:7][sync_env] Loading secret-defs from %s", secret_defs_path)
    return _project_secret_defs(_load_secret_definitions(secret_defs_path))


# endregion FUNC_load_secret_defs


# ── Module-level helpers (extracted from generate_env_example closures, DevPlan 117 G T57) ──


# region HELPER__get_env_val
def _get_env_val(env_defaults: dict[str, str], name: str, default: str = "") -> str:
    """Lookup env_defaults with fallback (module-level version of get_val closure)."""
    return env_defaults.get(name, default)


# endregion HELPER__get_env_val


# region HELPER__get_val_required
def _get_val_required(env_defaults: dict[str, str], name: str) -> str:
    """Fail-fast lookup — no silent fallback for SoT keys (DevPlan 116 invariant 7).

    ## @purpose  Keys that MUST exist in platform-env.yaml env_defaults (e.g.
    ##            PLATFORM_DOMAIN, COMPOSE_PROFILES) raise instead of silently
    ##            emitting an empty value — eliminates «gate зелёный, система врёт».
    ## @io        ⇥ name: str → ⎋ str value ⚡ raise KeyError if absent
    ## @complexity O(1)
    """
    if name not in env_defaults:
        msg = (
            f"[IMP:10][sync_env] Missing required env_defaults key: {name} — "
            "run `make generate-platform-env` (SoT: core/platform-infra.yaml env_defaults)"
        )
        raise KeyError(msg)
    return str(env_defaults[name])


# endregion HELPER__get_val_required


# region HELPER__get_secret_def_field
def _get_secret_def_field(secret_defs: dict[str, dict[str, str]], name: str, field: str) -> str:
    """Lookup a field from secret-definitions (module-level version of sd_get closure)."""
    entry = secret_defs.get(name, {})
    val = entry.get(field, "")
    return val if val else ""


# endregion HELPER__get_secret_def_field


# region HELPER__get_platform_root
def _get_platform_root() -> str:
    """Canonical platform root — matches gate test allowlist; base for deployment path defaults.

    B3: резолвер канона shared/deploy_paths (PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform).
    """
    from core.internal.shared.deploy_paths import platform_remote_base

    return str(platform_remote_base())


# endregion HELPER__get_platform_root


# ── Section builders (DevPlan 117 G T57 — generate_env_example decomposition) ──


# region SECTION_header
def _section_header() -> list[str]:
    """Header + MODULE_CONTRACT docstring."""
    lines: list[str] = []
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
    lines.append("##           (стресс-тест pgbouncer, 2026-07-20). Единый формат упрощает grep-поиск и")
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
    lines.append("#    Регенерация: make generate-env-example")
    lines.append("# ══════════════════════════════════════════════════════════════════════════════")
    lines.append("")
    return lines


# endregion SECTION_header


# region SECTION_platform_context
def _section_platform_context(env_defaults: dict[str, str]) -> list[str]:
    """Platform / Context section."""
    lines: list[str] = []
    lines.append("# ── Platform / Context ─────────────────────────────────────────────────────")
    lines.append("# Контекст (GitHub org) для изоляции окружения")
    lines.append("CONTEXT=" + _get_env_val(env_defaults, "CONTEXT", "test"))
    lines.append("# Имя ноды (для деплоя и алертов)")
    lines.append("NODE_NAME=" + _get_env_val(env_defaults, "NODE_NAME", "test-node"))
    lines.append("# Локальные оверрайды dev-локалей (RC-сессия 2026-08-03). Пустой дефолт = прод-")
    lines.append("# поведение compose (${VAR:-default} в base.yml): NODE_CONFIGS_DIR → /opt/node-configs,")
    lines.append(
        "# STATUS_METRICS_JSON / HTPASSWD_FILE → /var/lib/platform/run (persistent, 142 W2). Локально (macOS, Docker"
    )
    lines.append("# Desktop не шарит /opt и /run) .env переопределяет абсолютными путями.")
    lines.append("NODE_CONFIGS_DIR=" + _get_env_val(env_defaults, "NODE_CONFIGS_DIR", ""))
    lines.append("STATUS_METRICS_JSON=" + _get_env_val(env_defaults, "STATUS_METRICS_JSON", ""))
    lines.append("HTPASSWD_FILE=" + _get_env_val(env_defaults, "HTPASSWD_FILE", ""))
    lines.append("# PLATFORM_DOMAIN — домен платформы для dev-сертификатов и vhost-роутинга.")
    lines.append("# Базовое значение: ai-platform.local. При загруженном контексте (context = GitHub org):")
    lines.append("#   PLATFORM_DOMAIN=<context>.local, SAN дополнительно включает *.${PLATFORM_DOMAIN}.")
    lines.append("# Сертификаты генерируются автоматически через dev_cert_generator.py (make dev-certs).")
    lines.append("# @domain-scheme DevPlan 012 — тестовый домен упразднён.")
    lines.append("PLATFORM_DOMAIN=" + _get_val_required(env_defaults, "PLATFORM_DOMAIN"))
    lines.append("# ⚠️ Pin to a specific version — avoid float tag (W7 fix).")
    lines.append("# Update this tag when you want to use a newer context overlay image.")
    lines.append("# Available tags: ghcr.io/<context>/hermes-agent-context")
    lines.append(
        "CONTEXT_IMAGE="
        # 170 W12 C1: fallback-дефолт = digest-pin (parity platform-infra.yaml env_defaults)
        + _get_env_val(
            env_defaults,
            "CONTEXT_IMAGE",
            "ghcr.io/tronyxlab/hermes-agent-context:latest@sha256:0105cd5f9ff969b2f6aff035e7e4ff7ec0677546667616423ac2dd6cc690e986",
        )
    )
    return lines


# endregion SECTION_platform_context


# region SECTION_platform_secrets
def _section_platform_secrets(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """Platform secrets section (master credentials, AGE key)."""
    lines: list[str] = []
    lines.append("# ── Platform secrets ───────────────────────────────────────────────────────")
    lines.append(
        "# Master credentials — единый ЛОГИН для всех сервисов платформы (status-page, Prometheus, Loki, Grafana, Langfuse, Hermes)."
    )
    lines.append(
        "# Пароли per-secret (DevPlan 176 B.8): PLATFORM_MASTER_PASSWORD + HERMES_DASHBOARD_PASSWORD + GF_SECURITY_ADMIN_PASSWORD"
    )
    lines.append(
        "# + LANGFUSE_INIT_USER_PASSWORD — каждый СВОЁ случайное значение при первом bootstrap (не единый пароль)."
    )
    lines.append("# Любой пароль переопределяется явно — operator-defined значения сохраняются (override через SOPS).")
    lines.append("# ⚠️ NOT for production — set via SOPS/age encrypted secrets on VPS.")
    constraint_master_pwd = _get_secret_def_field(secret_defs, "PLATFORM_MASTER_PASSWORD", "charset")
    if constraint_master_pwd:
        lines.append("# ⚠️ CONSTRAINT: PLATFORM_MASTER_PASSWORD must match " + constraint_master_pwd)
    lines.append("# Per-secret autogen: каждый сервис-пароль (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD,")
    lines.append("# LANGFUSE_INIT_USER_PASSWORD) получает СОБСТВЕННОЕ случайное значение (secrets.token_urlsafe) —")
    lines.append("# разрыв unified-auth конвенции (единый пароль = blast radius, M7).")
    lines.append("# Сервис-пользователи (HERMES_DASHBOARD_USERNAME, GF_SECURITY_ADMIN_USER, LANGFUSE_INIT_USER_EMAIL)")
    lines.append("# инициализируются из PLATFORM_MASTER_EMAIL (admin@PLATFORM_DOMAIN).")
    lines.append("# Любая переменная может быть переопределена явно — operator-defined значения сохраняются.")
    gen_master_pwd = _get_secret_def_field(secret_defs, "PLATFORM_MASTER_PASSWORD", "gen_command")
    if gen_master_pwd:
        lines.append("# Генерация: " + gen_master_pwd)
    # Fail-fast: PLATFORM_MASTER_EMAIL всегда в env_defaults (ci_default из secret-definitions.yaml).
    # fallback домен не используется (parity-гейт domain_parity).
    lines.append("PLATFORM_MASTER_EMAIL=" + _get_val_required(env_defaults, "PLATFORM_MASTER_EMAIL"))
    lines.append(
        "PLATFORM_MASTER_PASSWORD=" + _get_env_val(env_defaults, "PLATFORM_MASTER_PASSWORD", "test-master-password")
    )
    lines.append("")
    lines.append("# AGE_SECRET_KEY — master age-key для SOPS-расшифровки platform-secrets.")
    lines.append("# ⚠️ REQUIRED (env_requires of platform-secrets module) — без него systemd oneshot fails-closed.")
    lines.append("# Генерация: age-keygen -o keys.txt → извлечь публичный/приватный ключ")
    lines.append("# ⚠️ NOT for production .env — только SOPS-encrypted secrets на VPS.")
    constraint_age = _get_secret_def_field(secret_defs, "AGE_SECRET_KEY", "charset")
    if constraint_age:
        lines.append("# ⚠️ CONSTRAINT: AGE_SECRET_KEY must match " + constraint_age)
    # ci_default берётся из secret-definitions.yaml (SoT), литералы не дублируются.
    age_ci_default = _get_secret_def_field(secret_defs, "AGE_SECRET_KEY", "ci_default")
    lines.append("AGE_SECRET_KEY=" + _get_env_val(env_defaults, "AGE_SECRET_KEY", age_ci_default))
    return lines


# endregion SECTION_platform_secrets


# region SECTION_postgres
def _section_postgres(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """Postgres (shared-db) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Postgres (shared-db) ───────────────────────────────────────────────────")
    lines.append("POSTGRES_USER=" + _get_env_val(env_defaults, "POSTGRES_USER", "postgres"))
    constraint_pg_pwd = _get_secret_def_field(secret_defs, "POSTGRES_PASSWORD", "charset")
    if constraint_pg_pwd:
        lines.append("# ⚠️ CONSTRAINT: POSTGRES_PASSWORD must match " + constraint_pg_pwd)
    lines.append("# Пароль встраивается в DATABASE_URL/DATABASE_URLS без URL-encoding. Генерация: openssl rand -hex 32")
    lines.append("POSTGRES_PASSWORD=" + _get_env_val(env_defaults, "POSTGRES_PASSWORD", "test-pg-pwd"))
    lines.append("POSTGRES_DB=" + _get_env_val(env_defaults, "POSTGRES_DB", "platform"))
    lines.append("# Postgres через pgbouncer (default: 6432)")
    lines.append("POSTGRES_PORT=" + _get_val_required(env_defaults, "POSTGRES_PORT"))
    lines.append("POSTGRES_HOST=" + _get_env_val(env_defaults, "POSTGRES_HOST", "pgbouncer"))
    return lines


# endregion SECTION_postgres


# region SECTION_pgbouncer
def _section_pgbouncer(env_defaults: dict[str, str]) -> list[str]:
    """PgBouncer section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── PgBouncer (connection pooler for Postgres) ─────────────────────────────")
    lines.append("PGBOUNCER_IMAGE=" + _get_env_val(env_defaults, "PGBOUNCER_IMAGE", "edoburu/pgbouncer:v1.25.2-p0"))
    return lines


# endregion SECTION_pgbouncer


# region SECTION_redis
def _section_redis(env_defaults: dict[str, str]) -> list[str]:
    """Redis section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Redis (cache) ──────────────────────────────────────────────────────────")
    lines.append("REDIS_PORT=" + _get_val_required(env_defaults, "REDIS_PORT"))
    lines.append("REDIS_HOST=" + _get_env_val(env_defaults, "REDIS_HOST", "redis"))
    return lines


# endregion SECTION_redis


# region SECTION_clickhouse
def _section_clickhouse(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """ClickHouse section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── ClickHouse (Analytical DB) ─────────────────────────────────────────────")
    lines.append("CLICKHOUSE_URL=" + _get_env_val(env_defaults, "CLICKHOUSE_URL", "http://clickhouse:8123"))
    lines.append("CLICKHOUSE_USER=" + _get_env_val(env_defaults, "CLICKHOUSE_USER", "default"))
    constraint_ch_pwd = _get_secret_def_field(secret_defs, "CLICKHOUSE_PASSWORD", "charset")
    if constraint_ch_pwd:
        lines.append("# ⚠️ CONSTRAINT: CLICKHOUSE_PASSWORD must match " + constraint_ch_pwd)
    lines.append("# Пароль встраивается в CLICKHOUSE_MIGRATION_URL без URL-encoding. Генерация: openssl rand -hex 32")
    lines.append(
        "CLICKHOUSE_PASSWORD=" + _get_env_val(env_defaults, "CLICKHOUSE_PASSWORD", "test-clickhouse-pwd-not-for-prod")
    )
    # 177 W1.1: CLICKHOUSE_HTTP_PORT/CLICKHOUSE_NATIVE_PORT удалены — мёртвые env (0 чтений),
    # порты захардкожены в clickhouse compose
    return lines


# endregion SECTION_clickhouse


# region SECTION_minio
def _section_minio(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """MinIO (local S3, dev) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── MinIO (local S3, dev) ────────────────────────────────────────────────")
    lines.append("MINIO_PORT=" + _get_val_required(env_defaults, "MINIO_PORT"))
    lines.append("MINIO_CONSOLE_PORT=" + _get_val_required(env_defaults, "MINIO_CONSOLE_PORT"))
    lines.append("# ⚠️ REQUIRED (env_requires of minio module) — no defaults in production; set via SOPS secrets")
    constraint_minio_user = _get_secret_def_field(secret_defs, "MINIO_ROOT_USER", "charset")
    if constraint_minio_user:
        lines.append("# ⚠️ CONSTRAINT: MINIO_ROOT_USER must match " + constraint_minio_user)
    lines.append("MINIO_ROOT_USER=" + _get_env_val(env_defaults, "MINIO_ROOT_USER", "minioadmin"))
    lines.append("# ⚠️ REQUIRED (env_requires of minio module) — no defaults in production; set via SOPS secrets")
    constraint_minio_pwd = _get_secret_def_field(secret_defs, "MINIO_ROOT_PASSWORD", "charset")
    if constraint_minio_pwd:
        lines.append("# ⚠️ CONSTRAINT: MINIO_ROOT_PASSWORD must match " + constraint_minio_pwd)
    lines.append("MINIO_ROOT_PASSWORD=" + _get_env_val(env_defaults, "MINIO_ROOT_PASSWORD", "minioadmin"))
    return lines


# endregion SECTION_minio


# region SECTION_s3_backup
def _section_s3_backup(env_defaults: dict[str, str]) -> list[str]:
    """S3 / Backup section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── S3 / Backup ──────────────────────────────────────────────────────────────")
    lines.append("S3_ENDPOINT_URL=" + _get_env_val(env_defaults, "S3_ENDPOINT_URL", "https://s3.timeweb.cloud"))
    lines.append("S3_REGION=" + _get_env_val(env_defaults, "S3_REGION", "ru-1"))
    lines.append("S3_PREFIX=" + _get_env_val(env_defaults, "S3_PREFIX", "platform/backups"))
    lines.append("S3_BUCKET=" + _get_env_val(env_defaults, "S3_BUCKET", "test-bucket"))
    lines.append("S3_ACCESS_KEY=" + _get_env_val(env_defaults, "S3_ACCESS_KEY", "test-access-key"))
    lines.append("S3_SECRET_KEY=" + _get_env_val(env_defaults, "S3_SECRET_KEY", "test-secret-key"))
    # S3 read-only creds для heartbeat-checker (DevPlan 003 B5): отдельный read-only IAM ключ
    # для CI-крона heartbeat-check.yml — НЕ мастер-ключи; значения — GitHub Secrets, не .env
    lines.append("S3_READONLY_ACCESS_KEY=" + _get_env_val(env_defaults, "S3_READONLY_ACCESS_KEY", ""))
    lines.append("S3_READONLY_SECRET_KEY=" + _get_env_val(env_defaults, "S3_READONLY_SECRET_KEY", ""))
    # Дублирующие ключи для upload-s3.sh (AWS SDK совместимость) — значения из SoT
    # (platform-infra.yaml env_defaults; compose резолвит алиасы через S3_*). Генератор без хардкода.
    lines.append("AWS_ACCESS_KEY_ID=" + _get_env_val(env_defaults, "AWS_ACCESS_KEY_ID"))
    lines.append("AWS_SECRET_ACCESS_KEY=" + _get_env_val(env_defaults, "AWS_SECRET_ACCESS_KEY"))
    lines.append("PLATFORM_CONTEXT=" + _get_env_val(env_defaults, "PLATFORM_CONTEXT", "personal"))
    return lines


# endregion SECTION_s3_backup


# region SECTION_llm_provider
def _section_llm_provider(env_defaults: dict[str, str]) -> list[str]:
    """LLM Provider API Key section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── LLM Provider API Key ───────────────────────────────────────────────────")
    lines.append("DEEPSEEK_API_KEY=" + _get_env_val(env_defaults, "DEEPSEEK_API_KEY", "sk-placeholder-key-for-ci"))
    return lines


# endregion SECTION_llm_provider


# region SECTION_litellm
def _section_litellm(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """LiteLLM (LLM Gateway) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── LiteLLM (LLM Gateway) ────────────────────────────────────────────────────")
    lines.append("# ⚠️ LITELLM_MASTER_KEY — ОБЯЗАТЕЛЕН для production. Без него LiteLLM стартует")
    lines.append("#    но все API-запросы требующие авторизации будут отклонены (401).")
    gen_litellm = _get_secret_def_field(secret_defs, "LITELLM_MASTER_KEY", "gen_command")
    if gen_litellm:
        lines.append("#    Генерировать: " + gen_litellm)
    lines.append("LITELLM_MASTER_KEY=" + _get_env_val(env_defaults, "LITELLM_MASTER_KEY", "sk-ci-test-master-key"))
    lines.append("# Опциональная лицензия (оставить пустым для community-версии)")
    lines.append("LITELLM_LICENSE=" + _get_env_val(env_defaults, "LITELLM_LICENSE", ""))
    # LITELLM_METRICS_TOKEN — unified with LITELLM_MASTER_KEY
    lines.append("LITELLM_PORT=" + _get_val_required(env_defaults, "LITELLM_PORT"))
    lines.append(
        "# URL для healthcheck LiteLLM (default: http://litellm:4000/health/liveliness — единый канон, DevPlan 122 T2)"
    )
    lines.append(
        "LITELLM_HEALTH_URL="
        + _get_env_val(env_defaults, "LITELLM_HEALTH_URL", "http://litellm:4000/health/liveliness")
    )
    lines.append("# Клиенты (Hermes, внешние тулы) шлют запросы через LiteLLM")
    lines.append("OPENAI_BASE_URL=" + _get_env_val(env_defaults, "OPENAI_BASE_URL", "http://litellm:4000"))
    lines.append("# Virtual key for Hermes-agent — provisioned by make provision-llm (unlimited profile)")
    lines.append("LITELLM_API_KEY=" + _get_env_val(env_defaults, "LITELLM_API_KEY", "sk-placeholder-litellm-api-key"))
    return lines


# endregion SECTION_litellm


# region SECTION_langfuse
def _section_langfuse(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """Langfuse (LLM Tracing) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Langfuse (LLM Tracing) ───────────────────────────────────────────────────")
    lines.append(
        "NEXTAUTH_SECRET=" + _get_env_val(env_defaults, "NEXTAUTH_SECRET", "ci-test-nextauth-secret-32-chars-min!!")
    )
    lines.append("NEXTAUTH_URL=" + _get_env_val(env_defaults, "NEXTAUTH_URL", "http://langfuse:3000"))
    lines.append("SALT=" + _get_env_val(env_defaults, "SALT", "ci-test-salt-value"))
    lines.append("# ENCRYPTION_KEY — v4 required: 64 hex (openssl rand -hex 32); тот же ключ у web и worker")
    lines.append(
        "ENCRYPTION_KEY="
        + _get_env_val(
            env_defaults, "ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
    )
    lines.append("# Headless init — ТРЕБУЕТСЯ только при ПЕРВОМ запуске Langfuse.")
    lines.append("# После инициализации эти переменные можно удалить из secrets.env.")
    lines.append("# Генерировать:")
    gen_org = _get_secret_def_field(secret_defs, "LANGFUSE_INIT_ORG_ID", "gen_command")
    gen_proj = _get_secret_def_field(secret_defs, "LANGFUSE_INIT_PROJECT_ID", "gen_command")
    gen_pub = _get_secret_def_field(secret_defs, "LANGFUSE_PUBLIC_KEY", "gen_command")
    gen_sec = _get_secret_def_field(secret_defs, "LANGFUSE_SECRET_KEY", "gen_command")
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
    lines.append("LANGFUSE_INIT_ORG_ID=" + _get_env_val(env_defaults, "LANGFUSE_INIT_ORG_ID", "ci-test-org"))
    lines.append(
        "LANGFUSE_INIT_PROJECT_ID=" + _get_env_val(env_defaults, "LANGFUSE_INIT_PROJECT_ID", "ci-test-project")
    )
    lines.append(
        "LANGFUSE_INIT_USER_EMAIL=" + _get_env_val(env_defaults, "LANGFUSE_INIT_USER_EMAIL", "admin@ai-platform.local")
    )
    lines.append(
        "LANGFUSE_INIT_USER_PASSWORD="
        + _get_env_val(env_defaults, "LANGFUSE_INIT_USER_PASSWORD", "ci-test-langfuse-pwd")
    )
    lines.append("# ⚠️ LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY — генерируются после init")
    lines.append("LANGFUSE_PUBLIC_KEY=" + _get_env_val(env_defaults, "LANGFUSE_PUBLIC_KEY", "ci-test-public-key"))
    lines.append("LANGFUSE_SECRET_KEY=" + _get_env_val(env_defaults, "LANGFUSE_SECRET_KEY", "ci-test-secret-key"))
    lines.append("LANGFUSE_PORT=" + _get_val_required(env_defaults, "LANGFUSE_PORT"))
    lines.append("# S3 для Langfuse event-логов (bucket и path-style)")
    lines.append("LANGFUSE_S3_BUCKET=" + _get_env_val(env_defaults, "LANGFUSE_S3_BUCKET", "langfuse-events"))
    lines.append("LANGFUSE_S3_FORCE_PATH_STYLE=" + _get_env_val(env_defaults, "LANGFUSE_S3_FORCE_PATH_STYLE", "true"))
    return lines


# endregion SECTION_langfuse


# region SECTION_hermes_dashboard
def _section_hermes_dashboard(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """Hermes Agent — Dashboard section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Hermes Agent — Dashboard ──────────────────────────────────────────────")
    # 177 W1.2: HERMES_DASHBOARD удалён — мёртвое env (0 чтений; compose хардкодит "1",
    # docker-compose.base.yml:132)
    lines.append(
        "HERMES_DASHBOARD_USERNAME="
        + _get_env_val(env_defaults, "HERMES_DASHBOARD_USERNAME", "admin@ai-platform.local")
    )
    constraint_hd_pwd = _get_secret_def_field(secret_defs, "HERMES_DASHBOARD_PASSWORD", "charset")
    if constraint_hd_pwd:
        lines.append("# ⚠️ CONSTRAINT: HERMES_DASHBOARD_PASSWORD must match " + constraint_hd_pwd)
    lines.append("# Per-secret random-autogen при первом bootstrap (DevPlan 176 B.8) — собственное значение,")
    lines.append("# НЕ копия PLATFORM_MASTER_PASSWORD (разрыв unified-auth конвенции, M7)")
    lines.append("HERMES_DASHBOARD_PASSWORD=" + _get_env_val(env_defaults, "HERMES_DASHBOARD_PASSWORD", "test-db-pwd"))
    lines.append("# 🔗 Цепочка: HERMES_DASHBOARD_USERNAME/PASSWORD → compose (hermes-agent:110-111)")
    lines.append("#    → контейнерные BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD.")
    lines.append("#    Переменные HERMES_DASHBOARD_BASIC_AUTH_* не используются — не потребляются ни одним")
    lines.append("#    compose-сервисом. Единственный consumer Basic Auth — сам Hermes Agent Dashboard.")
    lines.append("#    nginx-модуль не использует эти переменные; htpasswd в nginx только для Prometheus/Loki.")
    lines.append("# Dashboard UI порт (default: 9119)")
    lines.append("HERMES_DASHBOARD_PORT=" + _get_val_required(env_defaults, "HERMES_DASHBOARD_PORT"))
    lines.append("# Десктопный порт Hermes Agent (default: 8642)")
    lines.append("HERMES_DESKTOP_PORT=" + _get_val_required(env_defaults, "HERMES_DESKTOP_PORT"))
    return lines


# endregion SECTION_hermes_dashboard


# region SECTION_hermes_api
def _section_hermes_api(env_defaults: dict[str, str]) -> list[str]:
    """Hermes Agent — API Server section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Hermes Agent — API Server ─────────────────────────────────────────────")
    lines.append("# Встроенный HTTP API-сервер Hermes Agent.")
    lines.append("# Генерировать ключ: openssl rand -hex 32")
    lines.append("API_SERVER_ENABLED=" + _get_env_val(env_defaults, "API_SERVER_ENABLED", "false"))
    lines.append("API_SERVER_KEY=" + _get_env_val(env_defaults, "API_SERVER_KEY", "test-api-server-key-for-ci-only"))
    lines.append("API_SERVER_HOST=" + _get_env_val(env_defaults, "API_SERVER_HOST", "0.0.0.0"))  # nosec B104 — CI test default, not production
    return lines


# endregion SECTION_hermes_api


# region SECTION_telegram
def _section_telegram(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> list[str]:
    """Telegram section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Telegram ─────────────────────────────────────────────────────────────────")
    lines.append("# Bot token для Hermes Agent и Grafana Alerting")
    # ci_default берётся из secret-definitions.yaml (SoT), литералы не дублируются.
    tg_ci_default = _get_secret_def_field(secret_defs, "TELEGRAM_BOT_TOKEN", "ci_default")
    lines.append("TELEGRAM_BOT_TOKEN=" + _get_env_val(env_defaults, "TELEGRAM_BOT_TOKEN", tg_ci_default))
    lines.append("# Разрешённые пользователи (user IDs, comma-separated)")
    lines.append("TELEGRAM_ALLOWED_USERS=" + _get_env_val(env_defaults, "TELEGRAM_ALLOWED_USERS", ""))
    lines.append("# Базовый/info-канал нотификаций (fallback CRITICAL/WARNING, D3)")
    lines.append("TELEGRAM_CHAT_ID=" + _get_env_val(env_defaults, "TELEGRAM_CHAT_ID", ""))
    lines.append("# Чат для критических алертов")
    lines.append("TELEGRAM_CHAT_ID_CRITICAL=" + _get_env_val(env_defaults, "TELEGRAM_CHAT_ID_CRITICAL", ""))
    lines.append("# Чат для warning-алертов (можно совпадать с CRITICAL)")
    lines.append("TELEGRAM_CHAT_ID_WARNING=" + _get_env_val(env_defaults, "TELEGRAM_CHAT_ID_WARNING", ""))
    # CI-флаг присутствия Docker Hub секретов (job env, platform-test.yml; 141 B-фикс secrets-in-if)
    lines.append("DOCKER_HUB_AUTH=" + _get_env_val(env_defaults, "DOCKER_HUB_AUTH", "false"))
    return lines


# endregion SECTION_telegram


# region SECTION_nginx
def _section_nginx(env_defaults: dict[str, str]) -> list[str]:
    """Nginx (Edge) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Nginx (Edge) ──────────────────────────────────────────────────────────")
    lines.append("# Директория конфигурации nginx.")
    lines.append("# Прод-дефолт: ./config (Let's Encrypt TLS vhost'ы, envsubst-templates).")
    lines.append("# Путь резолвится относительно core/modules/nginx/ (include-семантика compose).")
    lines.append("# Dev-режим: docker-compose.dev.yml (override поверх config/), НЕ NGINX_CONF_DIR (DevPlan 116 D3).")
    lines.append("NGINX_CONF_DIR=" + _get_env_val(env_defaults, "NGINX_CONF_DIR", "./config"))
    lines.append("# NGINX_CERT_DIR — директория TLS-сертификатов (Let's Encrypt).")
    lines.append('# VPS: не задавать (default "/etc/letsencrypt", смонтирован в контейнер).')
    lines.append("# Локально: ./dev-certs (macOS Docker Desktop не шарит /etc).")
    lines.append("NGINX_CERT_DIR=" + _get_env_val(env_defaults, "NGINX_CERT_DIR", "./dev-certs"))
    lines.append("# NGINX_OVERLAY_DIR — каталог overlay-vhosts из node-configs.")
    lines.append("# VPS: /opt/node-configs/<node>/overlays/nginx (см. add-vhost.sh конвенцию).")
    lines.append('# Локально не задавать — default "./overlays" пустой и не влияет.')
    lines.append("# Конвенция сиблингов: плоский каталог *.conf (глубина 1, non-recursive include).")
    lines.append("NGINX_OVERLAY_DIR=" + _get_env_val(env_defaults, "NGINX_OVERLAY_DIR", ""))
    lines.append("# HTTP/HTTPS порты nginx (default: 80/443; кастомные для dev-окружения без sudo)")
    lines.append("NGINX_HTTP_PORT=" + _get_val_required(env_defaults, "NGINX_HTTP_PORT"))
    lines.append("NGINX_HTTPS_PORT=" + _get_val_required(env_defaults, "NGINX_HTTPS_PORT"))
    lines.append("NGINX_EXPORTER_PORT=" + _get_val_required(env_defaults, "NGINX_EXPORTER_PORT"))
    return lines


# endregion SECTION_nginx


# region SECTION_ssl_dns
def _section_ssl_dns(env_defaults: dict[str, str]) -> list[str]:
    """SSL / DNS Challenge (acme.sh) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── SSL / DNS Challenge (acme.sh) ──────────────────────────────────────────────")
    lines.append("# WEBNAMES_API_KEY — API-ключ webnames.ru для acme.sh DNS-01 wildcard TLS.")
    lines.append("# Требуется при PLATFORM_CERTBOT_DNS_PLUGIN=webnames.")
    lines.append("# Хранится в SOPS-encrypted файле на VPS (/opt/node-configs/secrets/<node>.enc.yaml).")
    lines.append("# На VPS: sourced из /var/lib/platform/run/secrets.env перед step 14 (ssl provision) (142 W2).")
    lines.append("# Локально: не требуется (используются dev-certs через mkcert/openssl).")
    lines.append("WEBNAMES_API_KEY=" + _get_env_val(env_defaults, "WEBNAMES_API_KEY", "*test-webnames-api-key"))
    return lines


# endregion SECTION_ssl_dns


# region SECTION_proxy
def _section_proxy(env_defaults: dict[str, str]) -> list[str]:
    """Proxy (Tor/Privoxy) section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Proxy (Tor/Privoxy, опционально) ─────────────────────────────────────────")
    lines.append("HTTP_PROXY=" + _get_env_val(env_defaults, "HTTP_PROXY", ""))
    lines.append("HTTPS_PROXY=" + _get_env_val(env_defaults, "HTTPS_PROXY", ""))
    lines.append("# Список адресов, исключённых из прокси.")
    lines.append("# ⚠️ Канонический источник: platform-env.yaml proxy.no_proxy_internal.")
    lines.append("#    Этот список должен ⊇ no_proxy_internal — гейт T8.5 валидирует.")
    lines.append("# base: внутренние Docker-сервисы; внешние API-хосты добавляются по контексту.")
    lines.append(
        "NO_PROXY="
        + _get_env_val(
            env_defaults,
            "NO_PROXY",
            "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus",
        )
    )
    return lines


# endregion SECTION_proxy


# region SECTION_monitoring
def _section_monitoring(env_defaults: dict[str, str]) -> list[str]:
    """Monitoring / Observability section."""
    lines: list[str] = []
    platform_root = _get_platform_root()
    lines.append("")
    lines.append("# ── Monitoring / Observability ───────────────────────────────────────────")
    lines.append("# Grafana")
    lines.append(
        "GF_SECURITY_ADMIN_USER=" + _get_env_val(env_defaults, "GF_SECURITY_ADMIN_USER", "admin@ai-platform.local")
    )
    lines.append("GF_SECURITY_ADMIN_PASSWORD=" + _get_env_val(env_defaults, "GF_SECURITY_ADMIN_PASSWORD", "testpass"))
    lines.append("GRAFANA_PORT=" + _get_val_required(env_defaults, "GRAFANA_PORT"))
    lines.append("# Prometheus")
    lines.append(
        "PROMETHEUS_TARGETS_DIR="
        + _get_env_val(env_defaults, "PROMETHEUS_TARGETS_DIR", f"{platform_root}/prometheus-targets")
    )
    lines.append(
        "PROMETHEUS_RULES_DIR="
        # 170 W12 C5: единый дефолт с deploy_paths.prometheus_rules_dir (3-way drift-фикс)
        + _get_env_val(env_defaults, "PROMETHEUS_RULES_DIR", "/opt/prometheus/rules")
    )
    lines.append("PROMETHEUS_PORT=" + _get_val_required(env_defaults, "PROMETHEUS_PORT"))
    lines.append("# Loki (logging backend)")
    lines.append("LOKI_PORT=" + _get_val_required(env_defaults, "LOKI_PORT"))
    lines.append("# Infra-metrics exporters")
    lines.append("CADVISOR_PORT=" + _get_val_required(env_defaults, "CADVISOR_PORT"))
    lines.append("NODE_EXPORTER_PORT=" + _get_val_required(env_defaults, "NODE_EXPORTER_PORT"))
    lines.append("# Status Page (внутренний HTTP-порт модуля, DevPlan 117 D31 — зарегистрирован в SoT)")
    lines.append("STATUS_PAGE_PORT=" + _get_val_required(env_defaults, "STATUS_PAGE_PORT"))
    return lines


# endregion SECTION_monitoring


# region SECTION_compose_profiles
def _section_compose_profiles(env_defaults: dict[str, str]) -> list[str]:
    """Compose Profiles section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Compose Profiles ──────────────────────────────────────────────────────")
    # Fail-fast: COMPOSE_PROFILES обязателен в SoT (platform-infra.yaml env_defaults).
    compose_profiles = _get_val_required(env_defaults, "COMPOSE_PROFILES")
    lines.append(
        f"# Все {len(compose_profiles.split(','))} профилей — активирует все модули при make up (без явного MODULES=)"
    )
    lines.append("COMPOSE_PROFILES=" + compose_profiles)
    return lines


# endregion SECTION_compose_profiles


# region SECTION_misc
def _section_misc(env_defaults: dict[str, str]) -> list[str]:
    """Misc section."""
    lines: list[str] = []
    lines.append("")
    lines.append("# ── Misc ──────────────────────────────────────────────────────────────────────")
    lines.append("# Таймаут проверки зависимостей в секундах (default: 2.0)")
    lines.append("DEPENDENCY_CHECK_TIMEOUT=" + _get_env_val(env_defaults, "DEPENDENCY_CHECK_TIMEOUT", "2.0"))
    lines.append("")
    lines.append("# PROJECTS_BASE — базовая директория проектов для DeployOrchestrator (default: /opt/projects)")
    # B2: канонический дефолт — shared/deploy_paths (lazy import: standalone CLI без core на sys.path)
    from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE

    lines.append("PROJECTS_BASE=" + _get_env_val(env_defaults, "PROJECTS_BASE", DEFAULT_PROJECTS_BASE))
    lines.append("# PLATFORM_DEPLOY_TIMEOUT — таймаут деплоя в секундах для DeliveryChannel (default: 600)")
    lines.append("PLATFORM_DEPLOY_TIMEOUT=" + _get_env_val(env_defaults, "PLATFORM_DEPLOY_TIMEOUT", "600"))
    return lines


# endregion SECTION_misc


# region SECTION_github_actions
def _section_github_actions(env_defaults: dict[str, str]) -> list[str]:
    """GitHub Actions secrets section — only comments, does not generate variables."""
    lines: list[str] = []
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
    lines.append("# E2E_GRAFANA_URL — Grafana URL for E2E smoke test health check (used by platform-deploy.yml)")
    lines.append("# GHCR_PULL_TOKEN — Fine-grained PAT for ghcr.io read:packages")
    lines.append("GHCR_PULL_TOKEN=" + _get_env_val(env_defaults, "GHCR_PULL_TOKEN", "ghp_test-token-for-ci-only"))
    lines.append("# GHCR_PUSH_TOKEN — Fine-grained PAT for ghcr.io write:packages (L2 push)")
    lines.append("#   Create: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens")
    lines.append("#   Repository access: tronyxlab/ai-platform")
    lines.append("#   Permissions: Packages → Write")
    lines.append("GHCR_PUSH_TOKEN=" + _get_env_val(env_defaults, "GHCR_PUSH_TOKEN", ""))
    lines.append("")
    lines.append("# NODE_HOST_MAP — JSON mapping of node names to SSH hosts, org variable (used by deploy-project.yml)")
    return lines


# endregion SECTION_github_actions


# region FUNC_generate_env_example
def generate_env_example(env_defaults: dict[str, str], secret_defs: dict[str, dict[str, str]]) -> str:
    """Generate complete .env.example content from SoT data (orchestrator).

    ## @purpose  Decomposed (DevPlan 117 G T57): each section is a _section_* builder.
    ##            Signature unchanged — generate_env_example(env_defaults, secret_defs) -> str.
    ## @io — ⇥ env_defaults, secret_defs → ⎋ str .env.example content
    ## @complexity — O(S * L) where S = sections, L = lines per section
    ## @invariants
    ##   - Output is byte-identical to the pre-decomposition monolithic generator
    ##   - Section ordering is structural (see _section_header STRUCTURE comment)
    """
    lines: list[str] = []
    lines.extend(_section_header())
    lines.extend(_section_platform_context(env_defaults))
    lines.extend(_section_platform_secrets(env_defaults, secret_defs))
    lines.extend(_section_postgres(env_defaults, secret_defs))
    lines.extend(_section_pgbouncer(env_defaults))
    lines.extend(_section_redis(env_defaults))
    lines.extend(_section_clickhouse(env_defaults, secret_defs))
    lines.extend(_section_minio(env_defaults, secret_defs))
    lines.extend(_section_s3_backup(env_defaults))
    lines.extend(_section_llm_provider(env_defaults))
    lines.extend(_section_litellm(env_defaults, secret_defs))
    lines.extend(_section_langfuse(env_defaults, secret_defs))
    lines.extend(_section_hermes_dashboard(env_defaults, secret_defs))
    lines.extend(_section_hermes_api(env_defaults))
    lines.extend(_section_telegram(env_defaults, secret_defs))
    lines.extend(_section_nginx(env_defaults))
    lines.extend(_section_ssl_dns(env_defaults))
    lines.extend(_section_proxy(env_defaults))
    lines.extend(_section_monitoring(env_defaults))
    lines.extend(_section_compose_profiles(env_defaults))
    lines.extend(_section_misc(env_defaults))
    lines.extend(_section_github_actions(env_defaults))

    return "\n".join(lines) + "\n"


# endregion FUNC_generate_env_example


# region FUNC_write_atomic
def write_atomic(content: str, output_path: Path) -> None:
    """Write content atomically via shared atomic_writer (E5 — tempfile + fsync + os.replace)."""
    logger.info("[IMP:7][sync_env] Writing %d bytes to %s", len(content), output_path)
    _atomic_write_text(output_path, content)
    logger.info("[IMP:9][sync_env] Written atomically to %s", output_path)


# endregion FUNC_write_atomic


# region FUNC_main
class _SyncEnvArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    platform_env: ClassVar[str]
    secret_defs: ClassVar[str]
    output: ClassVar[str]
    check: ClassVar[bool]
    verbose: ClassVar[bool]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate .env.example from SoT")
    parser.add_argument("--platform-env", required=True, type=str, help="Path to platform-env.yaml")
    parser.add_argument("--secret-defs", required=True, type=str, help="Path to core/secret-definitions.yaml")
    parser.add_argument("--output", required=True, type=str, help="Path to write .env.example")
    parser.add_argument("--check", action="store_true", help="Dry-run: diff with existing, exit 1 on divergence")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(namespace=_SyncEnvArgs())

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[IMP:%(levelno)s][sync_env] %(message)s", stream=sys.stderr)

    platform_env_path = Path(args.platform_env).resolve()
    secret_defs_path = Path(args.secret_defs).resolve()
    output_path = Path(args.output).resolve()

    if not platform_env_path.is_file():
        logger.error("platform-env.yaml not found: %s", platform_env_path)
        return 1
    if not secret_defs_path.is_file():
        logger.error("secret-definitions.yaml not found: %s", secret_defs_path)
        return 1

    env_defaults = load_platform_env(platform_env_path).env_defaults
    secret_defs = load_secret_defs(secret_defs_path)
    generated = generate_env_example(env_defaults, secret_defs)

    if args.check:
        if not output_path.is_file():
            logger.error("[IMP:9][sync_env][CHECK] Output file %s does not exist — cannot compare", output_path)
            return 1
        existing = output_path.read_text()
        if existing != generated:
            diff_lines = list(
                difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    generated.splitlines(keepends=True),
                    fromfile=str(output_path),
                    tofile="generated",
                )
            )
            for line in diff_lines[:20]:
                sys.stderr.write(line)
            if len(diff_lines) > DIFF_LINES_MAX:
                sys.stderr.write(f"... ({len(diff_lines) - DIFF_LINES_MAX} more lines)\n")
            logger.error(
                "[IMP:9][sync_env][CHECK] Divergence detected — .env.example is stale. Run: make generate-env-example"
            )
            return 1
        logger.info("[IMP:9][sync_env][CHECK] .env.example is up-to-date")
        return 0

    write_atomic(generated, output_path)
    logger.info("[IMP:9][sync_env] .env.example generated at %s", output_path)
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
