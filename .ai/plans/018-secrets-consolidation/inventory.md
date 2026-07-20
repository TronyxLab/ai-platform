# Inventory Audit: Secrets Registry — Plan 018

<!--
$ARTIFACT_CONTRACT
  PURPOSE:      Complete inventory of all platform secrets, grouped by data source.
                Foundation for TASK-2 (secrets-manifest.yaml creation).
  DESCRIPTION:  Собранные из трёх источников: (1) env_requires из 13 module.yaml,
                (2) autogen-секреты из core/lib/secrets.sh::step_12b_ensure_secrets(),
                (3) CI secrets из .env.example (§GitHub Actions secrets),
                (4) инфраструктурные секреты из DevPlan контекста.
  SOURCE_FILES:
    - core/modules/postgres/module.yaml
    - core/modules/redis/module.yaml
    - core/modules/nginx/module.yaml
    - core/modules/clickhouse/module.yaml
    - core/modules/minio/module.yaml
    - core/modules/langfuse/module.yaml
    - core/modules/litellm/module.yaml
    - core/modules/logging/module.yaml
    - core/modules/hermes-agent/module.yaml
    - core/modules/monitoring/module.yaml
    - core/modules/backup-cron/module.yaml
    - core/modules/platform-secrets/module.yaml
    - core/modules/infra-metrics/module.yaml
    - core/lib/secrets.sh (step_12b_ensure_secrets, строки 225-231)
    - .env.example (строки 210-226, GitHub Actions secrets section)
  CREATED:      2026-07-20
  CREATED_BY:   Plan 018 Wave 1 (TASK-1)
  STATUS:       read-only audit — used by TASK-2 (secrets-manifest.yaml)
$END_ARTIFACT_CONTRACT
-->

## 1. env_requires — All 13 module.yaml

### 1.1 Модули без env_requires (3)

| Модуль | env_requires | Причина |
|--------|-------------|---------|
| redis | `[]` | Cache-only, no secrets |
| logging | `[]` | Logs are ephemeral, no secrets |
| nginx | _(none)_ | Stateless reverse proxy |

### 1.2 Модули с env_requires (10)

| Модуль | env_requires |
|--------|-------------|
| **postgres** | `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| **clickhouse** | `CLICKHOUSE_PASSWORD` |
| **minio** | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` |
| **langfuse** | `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_INIT_ORG_ID`, `LANGFUSE_INIT_PROJECT_ID`, `CLICKHOUSE_PASSWORD` |
| **litellm** | `LITELLM_MASTER_KEY`, `POSTGRES_PASSWORD`, `OPENAI_API_KEY` |
| **hermes-agent** | `HERMES_DASHBOARD_PASSWORD` |
| **monitoring** | `GF_SECURITY_ADMIN_PASSWORD` |
| **backup-cron** | `POSTGRES_PASSWORD`, `S3_BUCKET` |
| **platform-secrets** | `AGE_SECRET_KEY` |
| **infra-metrics** | `POSTGRES_USER`, `POSTGRES_PASSWORD` |

### 1.3 Сводная таблица — уникальные секреты и потребители

| # | Secret name | Consumers | Count |
|---|-------------|-----------|-------|
| 1 | `POSTGRES_PASSWORD` | postgres, litellm, backup-cron, infra-metrics | 4 |
| 2 | `POSTGRES_USER` | postgres, infra-metrics | 2 |
| 3 | `CLICKHOUSE_PASSWORD` | clickhouse, langfuse | 2 |
| 4 | `MINIO_ROOT_USER` | minio | 1 |
| 5 | `MINIO_ROOT_PASSWORD` | minio | 1 |
| 6 | `NEXTAUTH_SECRET` | langfuse | 1 |
| 7 | `SALT` | langfuse | 1 |
| 8 | `LANGFUSE_INIT_ORG_ID` | langfuse | 1 |
| 9 | `LANGFUSE_INIT_PROJECT_ID` | langfuse | 1 |
| 10 | `LITELLM_MASTER_KEY` | litellm | 1 |
| 11 | `OPENAI_API_KEY` | litellm | 1 |
| 12 | `HERMES_DASHBOARD_PASSWORD` | hermes-agent | 1 |
| 13 | `GF_SECURITY_ADMIN_PASSWORD` | monitoring | 1 |
| 14 | `S3_BUCKET` | backup-cron | 1 |
| 15 | `AGE_SECRET_KEY` | platform-secrets | 1 |

**Итого:** 15 уникальных имён из env_requires (13 module.yaml, из них 3 без env_requires, 10 с записями).

---

## 2. Autogen-секреты — secrets.sh::step_12b_ensure_secrets()

Источник: `core/lib/secrets.sh`, строки 225-231 (7 вызовов `_ensure_secret`).

| # | Secret name | Gen pattern | Также в env_requires |
|---|-------------|-------------|---------------------|
| 1 | `LITELLM_MASTER_KEY` | `sk-$(openssl rand -hex 32)` | ✅ litellm |
| 2 | `LANGFUSE_INIT_ORG_ID` | `org_$(openssl rand -hex 4)` | ✅ langfuse |
| 3 | `LANGFUSE_INIT_PROJECT_ID` | `proj_$(openssl rand -hex 4)` | ✅ langfuse |
| 4 | `LANGFUSE_PUBLIC_KEY` | `pk-lf_$(openssl rand -hex 16)` | ❌ NOT in any env_requires |
| 5 | `LANGFUSE_SECRET_KEY` | `sk-lf_$(openssl rand -hex 16)` | ❌ NOT in any env_requires |
| 6 | `NEXTAUTH_SECRET` | `openssl rand -hex 32` | ✅ langfuse |
| 7 | `SALT` | `openssl rand -hex 16` | ✅ langfuse |

**Итого:** 7 autogen-секретов, из них 2 (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`) — только в autogen, не в env_requires.

---

## 3. CI secrets — .env.example (§GitHub Actions secrets)

Источник: `.env.example`, строки 210-226 (12 переменных).

| # | Secret name | Workflow usage (из .env.example) |
|---|-------------|----------------------------------|
| 1 | `VPS_HOST` | platform-deploy.yml |
| 2 | `VPS_SSH_KEY` | platform-deploy.yml |
| 3 | `CI_DEPLOY_KEY` | deploy-project.yml, platform-deploy.yml |
| 4 | `GHCR_TOKEN` | ~~platform-test.yml, build-platform.yml~~ (TASK-3: к удалению) |
| 5 | `DOCKER_HUB_USERNAME` | platform-test.yml, platform-deploy.yml |
| 6 | `DOCKER_HUB_TOKEN` | platform-test.yml, platform-deploy.yml |
| 7 | `SSH_HOST` | platform-deploy.yml (workflow_call secret) |
| 8 | `SSH_KEY` | platform-deploy.yml (workflow_call secret) |
| 9 | `E2E_BASE_URL` | platform-deploy.yml (E2E smoke test) |
| 10 | `E2E_GRAFANA_URL` | platform-deploy.yml (E2E smoke test) |
| 11 | `GIT_MIRROR_TOKEN` | platform-deploy.yml |
| 12 | `NODE_HOST_MAP` | deploy-project.yml (org variable) |

**Примечание:** `SSH_KEY` и `CI_DEPLOY_KEY` — один ключ с разными ролями (rsync vs forced-command). `VPS_SSH_KEY` — отдельный ключ для VPS rsync.

**Итого:** 12 CI-секретов.

---

## 4. Инфраструктурные секреты (из контекста DevPlan)

| # | Secret name | Source | Назначение |
|---|-------------|--------|------------|
| 1 | `AGE_SECRET_KEY` | sops (env) | Age-ключ для расшифровки SOPS-файлов |
| 2 | `GHCR_PULL_TOKEN` | sops | Fine-grained PAT для ghcr.io pull (read:packages) |
| 3 | `GIT_MIRROR_TOKEN` | ci-secret | HTTPS-токен для git mirror (Tronyx161→TronyxLab) |
| 4 | `GHCR_TOKEN` | ci-secret | (DEPRECATED — будет удалён) |

---

## 5. Cross-Reference Summary

### 5.1 Все уникальные секреты (25+)

| # | Secret name | Source | Tier (proposed) | Consumers |
|---|-------------|--------|-----------------|-----------|
| 1 | `POSTGRES_PASSWORD` | sops | required | postgres, litellm, backup-cron, infra-metrics |
| 2 | `POSTGRES_USER` | sops | required | postgres, infra-metrics |
| 3 | `CLICKHOUSE_PASSWORD` | sops | required | clickhouse, langfuse |
| 4 | `MINIO_ROOT_USER` | sops | required | minio |
| 5 | `MINIO_ROOT_PASSWORD` | sops | required | minio |
| 6 | `NEXTAUTH_SECRET` | autogen | generated | langfuse |
| 7 | `SALT` | autogen | generated | langfuse |
| 8 | `LANGFUSE_INIT_ORG_ID` | autogen | generated | langfuse |
| 9 | `LANGFUSE_INIT_PROJECT_ID` | autogen | generated | langfuse |
| 10 | `LANGFUSE_PUBLIC_KEY` | autogen | generated | (in-memory only) |
| 11 | `LANGFUSE_SECRET_KEY` | autogen | generated | (in-memory only) |
| 12 | `LITELLM_MASTER_KEY` | autogen | generated | litellm |
| 13 | `OPENAI_API_KEY` | sops | required | litellm |
| 14 | `HERMES_DASHBOARD_PASSWORD` | sops | required | hermes-agent |
| 15 | `GF_SECURITY_ADMIN_PASSWORD` | sops | required | monitoring |
| 16 | `S3_BUCKET` | sops | required | backup-cron |
| 17 | `AGE_SECRET_KEY` | sops | required | platform-secrets |
| 18 | `GHCR_PULL_TOKEN` | sops | required | docker-login |
| 19 | `VPS_HOST` | ci-secret | required | platform-deploy.yml |
| 20 | `VPS_SSH_KEY` | ci-secret | required | platform-deploy.yml |
| 21 | `CI_DEPLOY_KEY` | ci-secret | required | deploy-project.yml, platform-deploy.yml |
| 22 | `GHCR_TOKEN` | ci-secret | optional (deprecated) | ~~platform-test.yml, build-platform.yml~~ |
| 23 | `DOCKER_HUB_USERNAME` | ci-secret | required | platform-test.yml, platform-deploy.yml |
| 24 | `DOCKER_HUB_TOKEN` | ci-secret | required | platform-test.yml, platform-deploy.yml |
| 25 | `SSH_HOST` | ci-secret | required | platform-deploy.yml |
| 26 | `SSH_KEY` | ci-secret | required | platform-deploy.yml |
| 27 | `E2E_BASE_URL` | ci-secret | required | platform-deploy.yml |
| 28 | `E2E_GRAFANA_URL` | ci-secret | required | platform-deploy.yml |
| 29 | `GIT_MIRROR_TOKEN` | ci-secret | optional | mirror.yml, context-promote.sh (fallback) |
| 30 | `NODE_HOST_MAP` | ci-secret | required | deploy-project.yml |

### 5.2 Матрица env_requires ↔ autogen покрытие

| Secret | env_requires | autogen | CI secret |
|--------|-------------|---------|-----------|
| `POSTGRES_PASSWORD` | ✅ 4 модуля | ❌ | ❌ |
| `POSTGRES_USER` | ✅ 2 модуля | ❌ | ❌ |
| `CLICKHOUSE_PASSWORD` | ✅ 2 модуля | ❌ | ❌ |
| `MINIO_ROOT_USER` | ✅ 1 модуль | ❌ | ❌ |
| `MINIO_ROOT_PASSWORD` | ✅ 1 модуль | ❌ | ❌ |
| `NEXTAUTH_SECRET` | ✅ 1 модуль | ✅ | ❌ |
| `SALT` | ✅ 1 модуль | ✅ | ❌ |
| `LANGFUSE_INIT_ORG_ID` | ✅ 1 модуль | ✅ | ❌ |
| `LANGFUSE_INIT_PROJECT_ID` | ✅ 1 модуль | ✅ | ❌ |
| `LANGFUSE_PUBLIC_KEY` | ❌ | ✅ | ❌ |
| `LANGFUSE_SECRET_KEY` | ❌ | ✅ | ❌ |
| `LITELLM_MASTER_KEY` | ✅ 1 модуль | ✅ | ❌ |
| `OPENAI_API_KEY` | ✅ 1 модуль | ❌ | ❌ |
| `HERMES_DASHBOARD_PASSWORD` | ✅ 1 модуль | ❌ | ❌ |
| `GF_SECURITY_ADMIN_PASSWORD` | ✅ 1 модуль | ❌ | ❌ |
| `S3_BUCKET` | ✅ 1 модуль | ❌ | ❌ |
| `AGE_SECRET_KEY` | ✅ 1 модуль | ❌ | ❌ |
| `GHCR_PULL_TOKEN` | ❌ | ❌ | ❌ (infra) |

---

## 6. Noteworthy Observations

1. **POSTGRES_PASSWORD — самый потребляемый секрет** (4 модуля: postgres, litellm, backup-cron, infra-metrics).
2. **LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY — не в env_requires**: auto-generated, не требуются ни одному модулю как env_requires. Генерируются `_ensure_secret()` для langfuse headless init.
3. **GHCR_TOKEN — мёртвый CI secret**: будет удалён в TASK-3 (заменён на GHCR_PULL_TOKEN?).
4. **SSH_KEY / CI_DEPLOY_KEY — один ключ, две роли**: задокументировано, но не валидируется гейтом.
5. **AGE_SECRET_KEY — критический**: без него platform-secrets не стартует → Docker не стартует.
6. **GIT_MIRROR_TOKEN — optional**: используется mirror.yml (HTTPS) и как fallback для context-promote.sh (SSH primary — Plan 015 T3.4).
7. **2 autogen-секрета не покрыты env_requires** (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`) — должны быть в манифесте как `tier: generated` с пустым/специальным consumers.
