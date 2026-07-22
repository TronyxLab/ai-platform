# DevPlan 047 — Bootstrap Context Deploy: Pipeline Redesign for Full Node Readiness

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Глубокая переработка bootstrap-pipeline для достижения «запущенного сервера из голого VPS» за один вызов make bootstrap-node. Текущий pipeline (17 init + 7 update шагов) поднимает инфраструктуру, но НЕ деплоит проекты контекста, НЕ настраивает Docker Hub auth (rate-limit), и НЕ восстанавливает сертификаты из S3. Из StatusReport 045: после bootstrap нода жива, но сайты не работают.
  DESCRIPTION: Добавить 3 новых компонента в pipeline: (1) Pre-flight gate — проверка доступности ghcr.io/Docker Hub/S3 до начала init, (2) Docker Hub auth + registry-mirror — шаг 4.5 после install-docker, (3) Фаза «deploy-context» — новый шаг 18 после converge: bulk-restore сертификатов из S3 + авто-деплой проектов контекста из node.yaml + финальный verify всех доменов. Гибридный канал образов: ghcr.io primary + fallback build on-node.
  RATIONALE: StatusReport 045 (direct-deploy) вскрыл 6 системных проблем: 14/20 контейнеров down после bootstrap, self-signed сертификаты, Docker Hub rate-limit, отсутствие ghcr.io-образов, vps-readiness.sh parsing failure, verify.sh design mismatch. Корень: bootstrap не покрывает «последнюю милю» — проекты контекста. Пользователь выбрал Option B (эволюционное расширение) из 4-option superposition: сохранить state_machine.py, добавить 2 новых шага + pre-flight gate. Это соответствует инварианту 6 (идемпотентность) и Strangler-Fig pattern.
  ACCEPTANCE_CRITERIA:
    1. `make bootstrap-node NODE=<n>` на голом VPS → все проекты контекста запущены и healthy, все домены отвечают по HTTPS с валидными LE-сертификатами (не self-signed)
    2. Pre-flight gate: если ghcr.io/S3 недоступны → WARN с diagnostic, но не FATAL (graceful degradation)
    3. Docker Hub auth настроен + registry-mirror в /etc/docker/daemon.json → docker pull nginx/postgres работает без 429
    4. Шаг deploy-context: bulk-restore сертификатов из S3 для всех доменов node.yaml → если S3-miss → acme.sh issue → если issue fails → WARN (non-fatal)
    5. Шаг deploy-context: авто-деплой проектов из node.yaml.projects[] where context==<context> → ghcr.io pull primary → если pull fails → build on-node из исходников
    6. Новый шаг 18 в state_machine.py INIT_STEPS, новый update-шаг 8 «deploy-context» (для incremental)
    7. Новый канонический таргет `make deploy-context NODE=<n>` для standalone запуска фазы
    8. Финальный verify: все проекты healthy + все домены HTTPS 200 (с cert valid >30 days)
    9. Идемпотентность: повторный bootstrap-node → deploy-context пропускает уже-deployed проекты (через healthcheck), сертификаты skip если валидны
    10. 7+ новых unit-тестов: pre-flight gate, docker-auth step, cert-bulk-restore, project-context-filter, deploy-context orchestrator
  IMPLEMENTS:
    - AGENTS.md инвариант 6 (идемпотентный bootstrap), инвариант 2 (git push → CI → deploy), инвариант 8 (LiteLLM PostgreSQL)
    - Superposition Option B (эволюционное расширение pipeline)
    - Решения пользователя: гибрид ghcr.io+build, node.yaml как реестр, bulk-restore+issue, Docker Hub+mirror, глубокий рефакторинг
  IMPACTS:
    - core/internal/bootstrap/lifecycle/state_machine.py — +2 шага (docker_auth, deploy_context), +1 pre-flight check
    - core/internal/bootstrap/lifecycle/steps.py — +3 step implementations (_step_docker_auth, _step_deploy_context, _preflight_checks)
    - core/internal/bootstrap/node-lifecycle.sh — +1 step wrapper (step_18_deploy_context)
    - core/internal/bootstrap/s3-ssl-cache.sh — +bulk-restore mode (восстановление всех доменов из node.yaml)
    - core/internal/bootstrap/issue-cert.sh — интеграция с bulk-restore (check S3 first, issue if missing)
    - НОВЫЙ: core/internal/bootstrap/deploy/context_deployer.py — оркестратор деплоя проектов контекста
    - НОВЫЙ: core/internal/bootstrap/preflight.py — pre-flight checks (ghcr.io/S3/Docker Hub probe)
    - НОВЫЙ: core/internal/bootstrap/docker_registry_auth.py — настройка Docker Hub auth + registry-mirror
    - НОВЫЙ: core/entrypoints/deploy-context.sh — standalone entrypoint для `make deploy-context`
    - core/entrypoint-manifest.yaml — +1 canonical target (deploy-context)
    - core/AGENTS.md — +1 строка в таблице canonical operations
    - core/schemas/node.schema.json — без изменений (projects[].context уже в schema)
    - AGENTS.md (root) — +1 глагол в glossary (deploy-context), +TRAP[DECISION] для pipeline redesign
  REQUIRES:
    - Python 3.10+ (уже есть на ноде после step 2 apt-deps)
    - Рабочий ghcr.io auth (GHCR_PULL_TOKEN из secrets) для primary channel
    - S3-credentials (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET) для cert cache
    - AGE-decrypted secrets.env (WEBNAMES_API_KEY для acme.sh DNS-01)
    - node.yaml с projects[] и context-полем для каждого проекта
    - platform-env.yaml с Docker Hub credentials (DOCKER_HUB_USERNAME/TOKEN)
-->

$START

## Overview

**Status:** Draft — pending architect review
**DevPlan:** 047
**Session:** 2026-07-22
**Priority:** CRITICAL — делает `make bootstrap-node` достаточным для запуска полного стека

### Problem Statement

Из StatusReport 045 (direct-deploy-projects):

| Проблема | Симптом | Корень |
|----------|---------|--------|
| 14/20 контейнеров down после bootstrap | postgres, redis, litellm, langfuse — не поднялись | root docker-compose.yml отсутствует на VPS, deploy-modules не покрывает context-проекты |
| Self-signed сертификаты | acme.sh установлен, но не выпустил сертификаты | /run/platform/secrets.env отсутствовал → WEBNAMES_API_KEY не загружен → DNS-01 failed |
| Docker Hub rate-limit (429) | nginx pull заблокирован | Нет Docker Hub auth, нет registry-mirror |
| ghcr.io-образы отсутствуют | tronyx-site, dance-site, botanika — manifest unknown | CI pipeline не пушит образы, нет fallback build on-node |
| vps-readiness.sh parsing failure | NODE_HOST_MAP parse returns empty | Env-var transport через Makefile сломан |
| verify.sh design mismatch | expose: true ожидается в node.yaml, а находится в ai-platform.yaml | Schema drift |

**Цель:** `make bootstrap-node NODE=<n>` → нода полностью запущена: все модули платформы + все проекты контекста + все сертификаты валидны.

---

## Superposition — Полный анализ (5 гипотез)

### Option A: Единый поток init+update с post-init фазой [score: 8/10]

**Подход:** Объединить текущие init+update в один pipeline из 3 фаз: Foundation → Platform → Context.

| Критерий | Оценка |
|----------|--------|
| Решает проблему | ✅ Полностью |
| Риск регрессии | 🟠 Высокий (полная переработка state machine) |
| Совместимость | ❌ Нарушает инвариант 6 (init vs update семантика) |
| Change-cost | Высокий (17+7 → 3×N шагов, переписать state_machine.py) |
| Test-cost | Высокий (все 7 unit-тестов state_machine нужно переписать) |

**Rejected:** Слишком рискованно после Decision Gate 043 (HARD STOP на новые крупные рефакторинги до стабилизации на production).

### Option B: Новый шаг 18 «deploy-context» + pre-flight gate + Docker Hub auth [score: 9/10] ⭐

**Подход:** Эволюционное расширение: +pre-flight gate, +шаг 4.5 docker-auth, +шаг 18 deploy-context.

| Критерий | Оценка |
|----------|--------|
| Решает проблему | ✅ Полностью |
| Риск регрессии | ✅ Низкий (3 новых шага, существующие не трогаются) |
| Совместимость | ✅ Сохраняет инвариант 6, Strangler-Fig pattern |
| Change-cost | Средний (3 новых Python-модуля, +2 шага в state_machine) |
| Test-cost | Средний (7 новых unit-тестов, существующие не трогаются) |

**Принято.** Соответствует решениям пользователя и архитектурным инвариантам.

### Option C: Внешний orchestrator «bootstrap-full» [score: 7/10]

**Подход:** Новый таргет `make bootstrap-full` = preflight + bootstrap-node + deploy-context + verify-all.

**Rejected:** Добавляет слой абстракции, нарушает принцип «одно имя = одна операция», идемпотентность сложнее (3 фазы).

### Option D: Converge-driven (R10 projects-deploy, R11 cert-restore, R12 docker-auth) [score: 6/10]

**Подход:** Всё через converge — добавить R-units.

**Rejected:** Converge на HARD STOP 7/10 K8s-parity. Расширять рискованно. Bootstrap и converge — разные ответственности (init vs reconcile).

### Option E: CI-driven (CI деплоит проекты после bootstrap) [score: 5/10]

**Подход:** Bootstrap поднимает ноду, CI pipeline деплоит проекты через SSH forced-command.

**Rejected:** Нарушает цель «из голого сервера — запущенный за один вызов». Требует рабочего CI, который сейчас сломан (StatusReport 045).

---

## Architecture — Option B

### Новый pipeline (изменения жирным)

```
make bootstrap-node NODE=<n> CONTEXT=<context>
├── ── PRE-FLIGHT GATE (новый, до state machine) ──
│   └── preflight.py: probe ghcr.io / S3 / Docker Hub / SSH / disk-space
│
├── ── INIT MODE (17 → 19 шагов) ──
│   ├── 1. ssh_access
│   ├── 2. apt_deps
│   ├── 3. [tor_proxy]
│   ├── 4. install_docker
│   ├── 4.5. docker_auth          ← НОВЫЙ: Docker Hub login + registry-mirror
│   ├── 5. create_platform_user
│   ├── 6. create_ci_deploy_user
│   ├── 6b. create_projects_base
│   ├── 7. firewall
│   ├── 8. verify_core
│   ├── 9. verify_node_configs
│   ├── 10. decrypt_secrets
│   ├── 12b. ensure_secrets
│   ├── secrets_init
│   ├── 11. read_node_yaml
│   ├── 12. ghcr_auth
│   ├── 13. sudoers
│   ├── 13b. install_acme
│   ├── 14. node_update          → triggers update mode
│   ├── 15. converge
│   ├── 16. audit_log
│   ├── 17. telegram
│   └── 18. deploy_context       ← НОВЫЙ: bulk-cert-restore + projects + verify
│
├── ── UPDATE MODE (7 → 8 шагов) ──
│   ├── 1. verify_core
│   ├── 2. provision
│   ├── 2.5. deliver_overlays
│   ├── 3. ssl_provision
│   ├── 4. deploy_modules
│   ├── 6. healthcheck
│   ├── 7. converge
│   └── 8. deploy_context        ← НОВЫЙ: incremental project deploy + cert check
│
└── ── STANDALONE ──
    └── make deploy-context NODE=<n>  ← НОВЫЙ канонический таргет
```

### Шаг 18 «deploy-context» — детальная декомпозиция

```
deploy_context (state_machine step 18)
├── 18.1 resolve_context_projects
│   └── parse node.yaml → filter projects[] where context == <context>
│   └── output: [{name, repo, type, domain, database}, ...]
│
├── 18.2 bulk_restore_certs
│   └── for each project.domain + platform_domain:
│       ├── s3-ssl-cache.sh check <domain>    → if valid: skip
│       ├── s3-ssl-cache.sh download <domain> → if S3-miss: continue
│       └── (defer to 18.3 if not in S3)
│
├── 18.3 issue_missing_certs
│   └── for each domain without restored cert:
│       └── issue-cert.sh (acme.sh DNS-01)
│       └── s3-ssl-cache.sh upload <domain> (cache for future)
│
├── 18.4 deploy_context_projects
│   └── for each project in context_projects:
│       ├── docker compose pull (ghcr.io)     → primary channel
│       ├── if pull fails:
│       │   ├── clone project repo (context-overlay)
│       │   ├── docker compose build           → fallback channel
│       │   └── log WARN: ghcr.io unavailable, built on-node
│       ├── docker compose up -d
│       ├── wait healthcheck (≤60s)
│       └── if unhealthy: WARN (non-fatal, continue)
│
├── 18.5 render_vhosts
│   └── add-vhost.sh --render-all --node <n>  (nginx vhost configs)
│   └── docker exec nginx nginx -s reload
│
└── 18.6 final_verify
    └── verify-domains.sh for all project domains + platform domain
    └── output: summary table (domain, HTTP status, cert validity)
    └── exit 0 if all pass, 1 if warnings, 2 if errors
```

### Pre-flight Gate (до state machine)

```python
# preflight.py — вызывается из node-lifecycle.sh main() ДО state machine

checks = [
    ("ssh_connectivity",   probe_ssh(host, key)),      # FATAL if fail
    ("disk_space",         probe_disk("/opt", 10GB)),   # FATAL if < 10GB
    ("s3_connectivity",    probe_s3(bucket, key)),      # WARN if fail (graceful)
    ("ghcr_auth",          probe_ghcr(token)),          # WARN if fail (fallback build)
    ("docker_hub_probe",   probe_dockerhub()),          # WARN if rate-limited
    ("dns_resolution",     probe_dns(domain)),          # WARN if fail (cert issue will fail)
]
# FATAL → exit 1 с diagnostic
# WARN → continue с warning в state.warnings
```

---

## Phase 1: Pre-flight Gate — 1.5h

### 1.1 Новый модуль `preflight.py`

**Файл:** `core/internal/bootstrap/preflight.py`

```python
# region MODULE_CONTRACT
## @purpose  Pre-flight checks ДО начала bootstrap state machine
## @scope    Вызывается из node-lifecycle.sh main() перед _delegate --mode init
## @invariants
##   - FATAL checks: ssh_connectivity, disk_space → exit 1
##   - WARN checks: s3, ghcr, docker_hub, dns → add to warnings, continue
##   - Timeout: 10s per probe (parallel asyncio.gather)
##   - Output: JSON to stdout {check: {status, latency, detail}}
## endregion MODULE_CONTRACT

async def run_preflight(node_yaml: str, context: str) -> PreflightResult:
    """Run all pre-flight checks in parallel, return aggregated result."""
    ...
```

### 1.2 Интеграция в node-lifecycle.sh

Добавить в `main()` после resolve NODE_YAML, до state machine:

```bash
# ── Pre-flight gate (DevPlan 047) ──
PREFLIGHT_RESULT=$(python3 "${SCRIPT_DIR}/preflight.py" --node-yaml "$NODE_YAML" --context "$CONTEXT" 2>&1) || {
    echo "$PREFLIGHT_RESULT" >&2
    # FATAL checks failed → exit 1
    exit 1
}
echo "[IMP:8][node-lifecycle][preflight] Pre-flight checks passed (warnings: ...)" >&2
```

### 1.3 Unit-тесты

- `tests/unit/test_preflight.py`:
  - `test_ssh_connectivity_ok`
  - `test_ssh_connectivity_fail_fatal`
  - `test_disk_space_threshold`
  - `test_s3_graceful_degradation`
  - `test_ghcr_unavailable_warn`
  - `test_parallel_execution`

---

## Phase 2: Docker Hub Auth + Registry Mirror — 1.5h

### 2.1 Новый модуль `docker_registry_auth.py`

**Файл:** `core/internal/bootstrap/docker_registry_auth.py`

```python
# region MODULE_CONTRACT
## @purpose  Настройка Docker Hub auth + registry-mirror для устранения rate-limit
## @scope    Вызывается из state_machine.py шаг 4.5 (после install_docker)
## @invariants
##   - Docker Hub login: берёт DOCKER_HUB_USERNAME/TOKEN из secrets.env
##   - Registry-mirror: пишет /etc/docker/daemon.json с registry-mirrors
##   - Idempotent: если уже настроено → skip
##   - Non-fatal: если Docker Hub creds отсутствуют → WARN, продолжить
##   - Restart: systemctl restart docker после изменения daemon.json
## endregion MODULE_CONTRACT

def configure_docker_auth(username: str, token: str, mirror_url: str | None = None) -> bool:
    """
    Configure Docker Hub auth + optional registry mirror.
    Returns True if configured, False if skipped (already configured).
    """
    ...
```

### 2.2 Новый шаг 4.5 в state_machine.py

Добавить в `INIT_STEPS` между `install_docker` (4) и `create_platform_user` (5):

```python
INIT_STEPS = [
    "ssh_access",           # 1
    "apt_deps",             # 2
    "tor_proxy",            # 3
    "install_docker",       # 4
    "docker_auth",          # 4.5 ← НОВЫЙ
    "create_platform_user", # 5
    ...
]
```

В `_execute_init_step()`:

```python
elif step_name == "docker_auth":
    from . import docker_registry_auth as dra
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][init][docker_auth] Docker Hub creds not set — rate-limit may apply")
        return
    dra.configure_docker_auth(username, token)
```

### 2.3 Registry-mirror config

`/etc/docker/daemon.json`:

```json
{
  "registry-mirrors": ["https://mirror.gcr.io"],
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
```

**TRAP[DECISION]:** mirror.gcr.io — публичный Google mirror, не требует auth. Альтернатива: private mirror в ghcr.io (требует auth).

### 2.4 Unit-тесты

- `tests/unit/test_docker_registry_auth.py`:
  - `test_docker_login_success`
  - `test_daemon_json_idempotent`
  - `test_missing_creds_warn`

---

## Phase 3: Bulk Cert Restore из S3 — 2h

### 3.1 Расширение `s3-ssl-cache.sh`

Добавить новый режим `bulk-restore`:

```bash
s3-ssl-cache.sh bulk-restore --node-yaml <path> [--context <ctx>]
```

**Логика:**
1. Парсит node.yaml → извлекает все домены: `domain` (platform) + `projects[].domain` (filtered by context)
2. Для каждого домена:
   - `s3-ssl-cache.sh check <domain>` → если валиден (>30 days), skip
   - `s3-ssl-cache.sh download <domain>` → если S3-miss, defer to issue-cert
3. Возвращает JSON: `{domain: {status: restored|miss|error}, ...}`

### 3.2 Новый Python-модуль `cert_orchestrator.py`

**Файл:** `core/internal/bootstrap/cert_orchestrator.py`

```python
# region MODULE_CONTRACT
## @purpose  Оркестратор сертификатов: bulk-restore из S3 + issue для отсутствующих
## @scope    Вызывается из deploy_context шаг 18.2 + 18.3
## @invariants
##   - Restore-first strategy: сначала S3, потом acme.sh
##   - Idempotent: валидные сертификаты пропускаются
##   - Non-fatal: failure одного домена не блокирует остальные
##   - Cache: успешный issue → upload в S3 для будущего restore
## endregion MODULE_CONTRACT

def orchestrate_certs(
    domains: list[str],
    s3_cache_script: str,
    issue_cert_script: str,
    secrets_env: str,
) -> CertResult:
    """
    Restore certs from S3 first, issue missing ones via acme.sh.
    Returns per-domain status.
    """
    ...
```

### 3.3 Интеграция с issue-cert.sh

`issue-cert.sh` уже вызывает `s3-ssl-cache.sh upload` после успешного issue. Нужно добавить **перед** issue:

```bash
# В issue-cert.sh main(), перед issue_tls_cert:
if bash "$s3_cache" check "$domain" 2>/dev/null; then
    bash "$s3_cache" download "$domain" && {
        log_step "main" "SKIP" "Cert restored from S3 for $domain"
        # Skip issue, proceed to project certs
        main_cert_exists=true
    }
fi
```

### 3.4 Unit-тесты

- `tests/unit/test_cert_orchestrator.py`:
  - `test_bulk_restore_all_from_s3`
  - `test_partial_restore_then_issue`
  - `test_s3_unavailable_graceful`
  - `test_idempotent_skip_valid`

---

## Phase 4: Context Deployer (проекты контекста) — 3h

### 4.1 Новый модуль `context_deployer.py`

**Файл:** `core/internal/bootstrap/deploy/context_deployer.py`

```python
# region MODULE_CONTRACT
## @purpose  Деплой всех проектов контекста из node.yaml после bootstrap
## @scope    Вызывается из deploy_context шаг 18.4
## @invariants
##   - Источник проектов: node.yaml → projects[] where context == <context>
##   - Канал образов: ghcr.io pull primary → build on-node fallback
##   - Idempotent: healthcheck перед deploy, skip если healthy
##   - Health-gate: ≤60s per project (как в deploy-project.sh)
##   - Non-fatal: failure одного проекта не блокирует остальные
##   - Audit: каждый deploy записывается в /var/log/platform/audit.log
## endregion MODULE_CONTRACT

@dataclass
class ProjectDeployResult:
    name: str
    status: str  # deployed | skipped | failed
    channel: str  # ghcr | build | skip
    health: str   # healthy | unhealthy | unknown
    error: str | None = None

def deploy_context_projects(
    node_yaml: str,
    context: str,
    projects_base: str = "/opt/projects",
    ghcr_fallback_build: bool = True,
) -> list[ProjectDeployResult]:
    """
    Deploy all projects from node.yaml where context matches.
    Uses ghcr.io pull as primary, falls back to on-node build.
    """
    ...
```

### 4.2 Алгоритм деплоя

```python
for project in context_projects:
    # 1. Check if already healthy (idempotent)
    if is_project_healthy(project):
        results.append(ProjectDeployResult(project.name, "skipped", "skip", "healthy"))
        continue

    # 2. Try ghcr.io pull (primary)
    try:
        docker_compose_pull(project)
        channel = "ghcr"
    except PullError:
        if not ghcr_fallback_build:
            results.append(ProjectDeployResult(project.name, "failed", "ghcr", "unhealthy", str(e)))
            continue
        # 3. Fallback: build on-node
        clone_project_repo(project)  # context-overlay
        docker_compose_build(project)
        channel = "build"

    # 4. Deploy
    docker_compose_up(project)

    # 5. Health-gate
    if wait_until_healthy(project, timeout=60):
        results.append(ProjectDeployResult(project.name, "deployed", channel, "healthy"))
    else:
        results.append(ProjectDeployResult(project.name, "deployed", channel, "unhealthy"))
```

### 4.3 Standalone entrypoint

**Файл:** `core/entrypoints/deploy-context.sh`

```bash
#!/usr/bin/env bash
# Thin wrapper: make deploy-context NODE=<n>
# Calls context_deployer.py directly
python3 "${CORE_DIR}/internal/bootstrap/deploy/context_deployer.py" \
    --node-yaml "$NODE_YAML" \
    --context "$CONTEXT" \
    --projects-base /opt/projects
```

### 4.4 Unit-тесты

- `tests/unit/test_context_deployer.py`:
  - `test_filter_projects_by_context`
  - `test_ghcr_pull_success`
  - `test_ghcr_fails_fallback_build`
  - `test_idempotent_skip_healthy`
  - `test_health_gate_timeout`
  - `test_non_fatal_continues_on_failure`
  - `test_audit_log_written`

---

## Phase 5: Интеграция в state_machine.py — 2h

### 5.1 Добавить шаг 18 в INIT_STEPS

```python
INIT_STEPS = [
    ...
    "telegram",          # 17
    "deploy_context",    # 18 ← НОВЫЙ
]
```

### 5.2 Добавить шаг 8 в UPDATE_STEPS

```python
UPDATE_STEPS = [
    ...
    "converge",          # 7
    "deploy_context",    # 8 ← НОВЫЙ
]
```

### 5.3 Реализация в `_execute_init_step()` и `_execute_update_step()`

```python
elif step_name == "deploy_context":
    _step_deploy_context(core_dir, node_name, node_yaml)
```

В `steps.py`:

```python
def _step_deploy_context(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Deploy all context projects + restore certs + verify."""
    context = os.environ.get("CONTEXT", "")
    if not context:
        # Auto-detect from path: projects/<context>/<project>/
        context = _detect_context_from_path(node_yaml)
    
    # 18.1 Resolve projects
    from ..deploy.context_deployer import deploy_context_projects
    from ..cert_orchestrator import orchestrate_certs
    
    # 18.2 + 18.3 Cert orchestration
    domains = _extract_domains(node_yaml, context)
    orchestrate_certs(domains, ...)
    
    # 18.4 Deploy projects
    results = deploy_context_projects(node_yaml, context)
    
    # 18.5 Render vhosts
    _run_subprocess(["bash", f"{core_dir}/internal/scaffold/add-vhost.sh", "--render-all", "--node", node_name])
    _run_subprocess(["docker", "exec", "nginx", "nginx", "-s", "reload"], non_fatal=True)
    
    # 18.6 Final verify
    _run_subprocess(["bash", f"{core_dir}/internal/verify/verify-domains.sh", "--node", node_name], non_fatal=True)
```

### 5.4 Обновить node-lifecycle.sh

Добавить `step_18_deploy_context()` wrapper:

```bash
step_18_deploy_context(){ _delegate --mode "${MODE}" --run-step 18; }
```

И в init-цикл:
```bash
CHECKPOINT_STEP_HASH="$(_step_hash "deploy-context")" checkpoint_step "deploy-context" step_18_deploy_context
```

### 5.5 Обновить AGENTS.md (bootstrap)

Обновить pipeline-диаграмму в `core/internal/bootstrap/AGENTS.md`:
- Добавить шаг 4.5 docker_auth
- Добавить шаг 18 deploy_context
- Добавить pre-flight gate

---

## Phase 6: Каноническая регистрация — 30min

### 6.1 entrypoint-manifest.yaml

```yaml
bootstrap:
  - make_target: bootstrap-node
    mechanism: ssh+rsync
    delegates_to: core/entrypoints/bootstrap.sh → preflight.py → node-lifecycle.sh --mode init → ... → deploy_context
    description: "Idempotent bootstrap of a new node to FULL readiness (infra + context projects + certs)"
  - make_target: deploy-context
    mechanism: ssh+python
    delegates_to: core/entrypoints/deploy-context.sh → core/internal/bootstrap/deploy/context_deployer.py
    description: "Deploy all projects of a context on a bootstrapped node (standalone or post-bootstrap)"
```

### 6.2 core/AGENTS.md

Добавить строку в таблицу canonical operations:

```markdown
| `make deploy-context` | Деплой всех проектов контекста на ноде | `make deploy-context NODE=<n> [CONTEXT=<ctx>]` | `core/entrypoints/deploy-context.sh` → `core/internal/bootstrap/deploy/context_deployer.py` |
```

### 6.3 AGENTS.md (root) — глоссарий

Добавить глагол:

```markdown
| ✅ | `deploy-context` | Деплой всех проектов контекста на ноде (post-bootstrap, standalone) |
```

### 6.4 TRAP[DECISION] в root AGENTS.md

```markdown
⚠️ TRAP[DECISION] · 2026-07-22 · HI · Bootstrap pipeline redesign — deploy-context as step 18
· Rejected: Option A (full rewrite of state machine, risk: regression after Decision Gate HARD STOP)
· Reason: Option B (evolutionary extension) preserves invariants, adds 3 components: preflight gate, docker-auth step 4.5, deploy-context step 18. Solves StatusReport 045 problems (projects not deployed, certs missing, Docker Hub rate-limit).
· Rev: if deploy-context step adds >5min to bootstrap → make it async (background job + telegram notify)
```

---

## Phase 7: Verification — 1.5h

### 7.1 Unit-тесты (всего 7+ новых)

| Тест-файл | Покрытие |
|-----------|----------|
| `tests/unit/test_preflight.py` | 6 тестов: ssh, disk, s3, ghcr, docker_hub, parallel |
| `tests/unit/test_docker_registry_auth.py` | 3 теста: login, daemon.json, missing creds |
| `tests/unit/test_cert_orchestrator.py` | 4 теста: bulk-restore, partial, graceful, idempotent |
| `tests/unit/test_context_deployer.py` | 7 тестов: filter, ghcr, fallback, idempotent, health, non-fatal, audit |
| `tests/unit/test_state_machine.py` | +2 теста: step 18 init, step 8 update (existing file extended) |

### 7.2 Integration gate

```bash
# Все unit-тесты pass
python3 -m pytest tests/unit/ -v

# Gate green
make gate MODE=fast
```

### 7.3 Staging test (на тестовом сервере)

```bash
# 1. Pre-flight dry-run
make bootstrap-node NODE=<test> --dry-run

# 2. Full bootstrap
make bootstrap-node NODE=<test>

# 3. Verify
make verify NODE=<test>
make project-status NODE=<test>

# 4. Standalone deploy-context (idempotent re-run)
make deploy-context NODE=<test>
```

**AC:** Все проекты healthy, все домены HTTPS 200, сертификаты валидны >30 days.

---

## Rollback Plan

| Изменение | Rollback |
|-----------|----------|
| preflight.py | Удалить файл + убрать вызов из node-lifecycle.sh |
| docker_auth шаг | Убрать из INIT_STEPS + удалить docker_registry_auth.py |
| deploy_context шаг | Убрать из INIT_STEPS + UPDATE_STEPS + удалить context_deployer.py |
| s3-ssl-cache bulk-restore | Убрать bulk-restore mode, оставить upload/download/check |
| deploy-context таргет | Убрать из entrypoint-manifest.yaml + Makefile |
| AGENTS.md изменения | `git checkout -- AGENTS.md core/AGENTS.md core/internal/bootstrap/AGENTS.md` |

Все изменения изолированы в новых файлах + минимальные правки state_machine.py (добавление строк в списки).

---

## Timeline

| Phase | Описание | Время |
|-------|----------|-------|
| Phase 1 | Pre-flight gate (preflight.py) | 1.5h |
| Phase 2 | Docker Hub auth + registry-mirror | 1.5h |
| Phase 3 | Bulk cert restore (cert_orchestrator.py) | 2h |
| Phase 4 | Context deployer (context_deployer.py) | 3h |
| Phase 5 | Интеграция в state_machine.py | 2h |
| Phase 6 | Каноническая регистрация | 30min |
| Phase 7 | Verification + staging test | 1.5h |
| **Total** | | **~12h** |

---

## After Completion

**Bootstrap-ready состояние (полное):**
- ✅ `make bootstrap-node NODE=<n>` → нода полностью запущена
- ✅ Все модули платформы healthy
- ✅ Все проекты контекста deployed + healthy
- ✅ Все сертификаты валидны (LE или S3-restored)
- ✅ Docker Hub auth + registry-mirror (no rate-limit)
- ✅ Pre-flight gate предотвращает «слепые» bootstrap-попытки
- ✅ `make deploy-context NODE=<n>` — standalone re-deploy проектов контекста

**Открывает:** 
- CI pipeline может деплоить обновления проектов через `make deploy` (git push → CI → SSH forced-command)
- Disaster recovery: `make bootstrap-node NODE=<n>` на новой ноде → полный стек восстановлен

$END
