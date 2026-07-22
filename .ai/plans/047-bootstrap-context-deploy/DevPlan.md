# DevPlan 047 — Bootstrap Context Deploy: Pipeline Redesign for Full Node Readiness

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Глубокая переработка bootstrap-pipeline для достижения «запущенного сервера из голого VPS» за один вызов make bootstrap-node. Текущий pipeline (17 init + 7 update шагов) поднимает инфраструктуру, но НЕ деплоит проекты контекста, НЕ настраивает Docker Hub auth (rate-limit), и НЕ восстанавливает сертификаты из S3. Из StatusReport 045: после bootstrap нода жива, но сайты не работают.
  DESCRIPTION: Добавить 3 новых компонента в pipeline: (1) Pre-flight gate — проверка доступности ghcr.io/Docker Hub/S3 до начала init, (2) Docker Hub auth + registry-mirror — шаг 4.5 после install-docker, (3) Фаза «deploy-context» — новый шаг 18 после converge: bulk-restore сертификатов из S3 + авто-деплой проектов контекста из node.yaml + финальный verify всех доменов. Гибридный канал образов: ghcr.io primary + fallback build on-node.
  RATIONALE: StatusReport 045 (direct-deploy) вскрыл 6 системных проблем: 14/20 контейнеров down после bootstrap, self-signed сертификаты, Docker Hub rate-limit, отсутствие ghcr.io-образов, vps-readiness.sh parsing failure, verify.sh design mismatch. Корень: bootstrap не покрывает «последнюю милю» — проекты контекста. Пользователь выбрал Option B (эволюционное расширение) из 4-option superposition: сохранить state_machine.py, добавить 2 новых шага + pre-flight gate. Это соответствует инварианту 6 (идемпотентность) и Strangler-Fig pattern.
   ACCEPTANCE_CRITERIA:
     1. `make bootstrap-node NODE=<n>` на голом VPS → все проекты контекста запущены и healthy, все домены отвечают по HTTPS с валидными LE-сертификатами (не self-signed)
     2. Pre-flight gate: если ghcr.io/S3 недоступны → WARN с diagnostic, но не FATAL (graceful degradation); если SSH/disk < 10GB → FATAL, abort bootstrap
     3. Docker Hub auth настроен + registry-mirror в /etc/docker/daemon.json → docker pull nginx/postgres работает без 429
     4. Шаг deploy-context: bulk-restore сертификатов из S3 для всех доменов node.yaml → если S3-miss → acme.sh issue → если issue fails → WARN (non-fatal)
     5. Шаг deploy-context: авто-деплой проектов из node.yaml.projects[] where context==<context> → ghcr.io pull primary → если pull fails → build on-node из исходников
     6. Новый шаг deploy_context (индекс 23 в INIT_STEPS), новый update-шаг 8 «deploy_context» (для incremental)
     7. Новый канонический таргет `make deploy-context NODE=<n>` для standalone запуска фазы
     8. Финальный verify: все проекты healthy + все expose:true домены возвращают HTTPS 200 (verify-domains.sh)
     9. Идемпотентность: повторный bootstrap-node → deploy-context пропускает уже-deployed проекты (через healthcheck), сертификаты skip если валидны
     10. 7+ новых unit-тестов: pre-flight gate, docker-auth step, cert-bulk-restore, project-context-filter, deploy-context orchestrator
     11. Shell facade перенумерован корректно: все step-функции (5-17) имеют правильные --run-step индексы (6-22)
  IMPLEMENTS:
    - AGENTS.md инвариант 6 (идемпотентный bootstrap), инвариант 2 (git push → CI → deploy), инвариант 8 (LiteLLM PostgreSQL)
    - Superposition Option B (эволюционное расширение pipeline)
    - Решения пользователя: гибрид ghcr.io+build, node.yaml как реестр, bulk-restore+issue, Docker Hub+mirror, глубокий рефакторинг
   IMPACTS:
     - core/internal/bootstrap/lifecycle/state_machine.py — +2 шага (docker_auth, deploy_context), +1 pre-flight check, перенумерация индексов
     - core/internal/bootstrap/lifecycle/steps.py — +1 step implementation (_step_deploy_context)
     - core/internal/bootstrap/node-lifecycle.sh — +2 step wrappers (step_4_5_docker_auth, step_18_deploy_context), перенумерация 14 функций, +--context arg
     - core/internal/bootstrap/s3-ssl-cache.sh — +bulk-restore mode (восстановление всех доменов из node.yaml)
     - core/internal/bootstrap/issue-cert.sh — интеграция с bulk-restore (check S3 first, issue if missing)
     - НОВЫЙ: core/internal/bootstrap/deploy/context_deployer.py — оркестратор деплоя проектов контекста
     - НОВЫЙ: core/internal/bootstrap/preflight.py — pre-flight checks (ghcr.io/S3/Docker Hub/disk/SSH probe)
     - НОВЫЙ: core/internal/bootstrap/docker_registry_auth.py — настройка Docker Hub auth + registry-mirror
     - НОВЫЙ: core/internal/bootstrap/cert_orchestrator.py — оркестратор сертификатов: bulk-restore + issue
     - НОВЫЙ: core/entrypoints/deploy-context.sh — standalone entrypoint для `make deploy-context`
     - core/entrypoint-manifest.yaml — +1 canonical target (deploy-context), обновление bootstrap-node delegation, +allowed_verbs
     - Makefile — +1 .PHONY target (deploy-context)
     - core/AGENTS.md — +1 строка в таблице canonical operations
     - core/internal/bootstrap/AGENTS.md — обновление pipeline диаграммы (docker_auth, deploy_context, ensure_secrets, secrets_init)
     - AGENTS.md (root) — +1 глагол в glossary (deploy-context), +TRAP[DECISION] для pipeline redesign
   REQUIRES:
     - Python 3.10+ (уже есть на ноде после step 2 apt-deps)
     - Рабочий ghcr.io auth (GHCR_PULL_TOKEN из secrets) для primary channel
     - S3-credentials (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET) для cert cache
     - AGE-decrypted secrets.env (WEBNAMES_API_KEY для acme.sh DNS-01)
     - node.yaml с projects[] и context-полем для каждого проекта
     - ОДНА нода = ОДИН контекст. CONTEXT берётся из node.yaml context (строка) или contexts[0].name. Множественные контексты на одной ноде НЕ поддерживаются.
     - platform-env.yaml с Docker Hub credentials (DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN)
     - --context CLI-аргумент в node-lifecycle.sh и state_machine.py (DEVPLAN 047)
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

**Ограничение:** Одна нода = один контекст. CONTEXT извлекается из `node.yaml` (поле `context` или `contexts[0].name`). Множественные контексты на одной ноде не поддерживаются — это упрощает CONTEXT flow и устраняет неоднозначность.

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
├── ── INIT MODE (21 → 23 шагов, array-index based) ──
│   ├── 1.  ssh_access
│   ├── 2.  apt_deps
│   ├── 3.  [tor_proxy]
│   ├── 4.  install_docker
│   ├── 5.  docker_auth             ← НОВЫЙ: Docker Hub login + registry-mirror
│   ├── 6.  create_platform_user    ← shifted +1
│   ├── 7.  create_ci_deploy_user   ← shifted +1
│   ├── 8.  create_projects_base    ← shifted +1
│   ├── 9.  firewall                ← shifted +1
│   ├── 10. verify_core             ← shifted +1
│   ├── 11. verify_node_configs     ← shifted +1
│   ├── 12. decrypt_secrets         ← shifted +1
│   ├── 13. ensure_secrets          ← shifted +1
│   ├── 14. secrets_init            ← shifted +1
│   ├── 15. read_node_yaml          ← shifted +1
│   ├── 16. ghcr_auth               ← shifted +1
│   ├── 17. sudoers                 ← shifted +1
│   ├── 18. install_acme            ← shifted +1
│   ├── 19. node_update             ← shifted +1 → triggers update mode
│   ├── 20. converge                ← shifted +1
│   ├── 21. audit_log               ← shifted +1
│   ├── 22. telegram                ← shifted +1
│   └── 23. deploy_context          ← НОВЫЙ: bulk-cert-restore + projects + verify
│
├── ── UPDATE MODE (7 → 8 шагов) ──
│   ├── 1. verify_core
│   ├── 2. provision
│   ├── 3. deliver_overlays
│   ├── 4. ssl_provision
│   ├── 5. deploy_modules
│   ├── 6. healthcheck
│   ├── 7. converge
│   └── 8. deploy_context           ← НОВЫЙ: incremental project deploy + cert check
│
└── ── STANDALONE ──
    └── make deploy-context NODE=<n>  ← НОВЫЙ канонический таргет
```

### ⚠️ TRAP[INDEX] · Array-index based step numbering

State machine использует **1-based array index** (не логическую нумерацию). `INIT_STEPS` — это список из 21 элемента (индексы 1-21). Вставка нового элемента на позицию 5 сдвигает ВСЕ последующие индексы на +1. Shell facade (`node-lifecycle.sh`) жёстко привязан к индексам через `--run-step N`. Любое изменение списка требует перенумерации shell-обёрток.

**Таблица перенумерации INIT_STEPS после вставки docker_auth (позиция 5):**

| Старый индекс | Новый индекс | Лог.номер | Шаг | Shell функция |
|:---:|:---:|:---:|---|---|
| 1 | 1 | 1 | ssh_access | `step_1_ssh_access` |
| 2 | 2 | 2 | apt_deps | `step_2_apt_deps` |
| 3 | 3 | 3 | tor_proxy | `step_3_tor_proxy` |
| 4 | 4 | 4 | install_docker | `step_4_install_docker` |
| — | **5** | **4.5** | **docker_auth** | **`step_4_5_docker_auth` ← NEW** |
| 5 | **6** | 5 | create_platform_user | `step_5_create_platform_user` → `--run-step 6` |
| 6 | **7** | 6 | create_ci_deploy_user | `step_6_create_ci_deploy_user` → `--run-step 7` |
| 7 | **8** | 6b | create_projects_base | `step_6b_create_projects_base` → `--run-step 8` |
| 8 | **9** | 7 | firewall | `step_7_firewall` → `--run-step 9` |
| 9 | **10** | 8 | verify_core | `step_8_verify_core` → `--run-step 10` |
| 10 | **11** | 9 | verify_node_configs | `step_9_verify_node_configs` → `--run-step 11` |
| 11 | **12** | 10 | decrypt_secrets | `step_10_decrypt_secrets` → `--run-step 12` |
| 12 | **13** | 12b | ensure_secrets | (обрабатывается state_machine.py — shell не имеет отдельной обёртки) |
| 13 | **14** | — | secrets_init | (обрабатывается state_machine.py — shell не имеет отдельной обёртки) |
| 14 | **15** | 11 | read_node_yaml | `step_11_read_node_yaml` → `--run-step 15` |
| 15 | **16** | 12 | ghcr_auth | `step_12_ghcr_auth` → `--run-step 16` |
| 16 | **17** | 13 | sudoers | `step_13_sudoers` → `--run-step 17` |
| 17 | **18** | 13b | install_acme | (обрабатывается state_machine.py) |
| 18 | **19** | 14 | node_update | `step_14_node_update` → `--run-step 19` |
| 19 | **20** | 15 | converge | `step_15_converge` → `--run-step 20` |
| 20 | **21** | 16 | audit_log | `step_16_audit_log` → `--run-step 21` |
| 21 | **22** | 17 | telegram | `step_17_telegram` → `--run-step 22` |
| — | **23** | **18** | **deploy_context** | **`step_18_deploy_context` ← NEW** |

**Примечание:** `ensure_secrets`, `secrets_init`, и `install_acme` не имеют отдельных shell-обёрток в checkpoint_step блоке — они выполняются внутри state_machine.py при делегировании `--mode init`. Их индексы сдвигаются, но shell-код не требует изменений.

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

Preflight вызывается в `main()` init-режима **после** валидации env vars и `detect_tor_enabled`, но **до** первого `checkpoint_step`:

```bash
main() {
    if [[ "$MODE" == "init" ]]; then
        # ... existing validation: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY ...
        # ... existing TOR detection ...

        # ── Pre-flight gate (DevPlan 047) ──
        PREFLIGHT_RESULT=$(python3 "${SCRIPT_DIR}/preflight.py" \
            --node-yaml "$NODE_YAML" \
            --context "${CONTEXT:-}" \
            --node-name "${NODE_NAME}" 2>&1) || {
            echo "$PREFLIGHT_RESULT" >&2
            # FATAL checks failed (ssh, disk) → exit 1
            exit 1
        }
        echo "[IMP:8][node-lifecycle][preflight] Pre-flight checks passed" >&2
        echo "$PREFLIGHT_RESULT" | python3 -c "
import sys, json
result = json.load(sys.stdin)
warnings = [k for k,v in result.items() if v.get('status') == 'warn']
if warnings:
    print(f'[IMP:7][node-lifecycle][preflight] Warnings (non-fatal): {warnings}', flush=True)
" >&2

        # ── Checkpoint steps (existing, renumbered per TRAP[INDEX]) ──
        CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
        ...
```

**Ключевое:** Preflight НЕ должен вызываться при `--dry-run` и `--resume` режимах (только при полном bootstrap с нуля). При `--dry-run` preflight-проверки выполняются как часть dry-run вывода.

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

### 2.2 Новый шаг docker_auth (index 5) в state_machine.py

Добавить в `INIT_STEPS` между `install_docker` (индекс 4) и `create_platform_user` (был индекс 5 → стал 6):

```python
INIT_STEPS = [
    "ssh_access",           # 1
    "apt_deps",             # 2
    "tor_proxy",            # 3
    "install_docker",       # 4
    "docker_auth",          # 5  ← НОВЫЙ
    "create_platform_user", # 6  ← был 5 (shifted +1)
    ...
]
```

В `_execute_init_step()` (state_machine.py, после блока `install_docker`, до `create_platform_user`):

```python
elif step_name == "docker_auth":
    from . import docker_registry_auth as dra  # type: ignore[import-untyped]
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][init][docker_auth] Docker Hub creds not set — rate-limit may apply")
        return
    dra.configure_docker_auth(username, token)
```

**Важно:** `DOCKER_HUB_USERNAME` и `DOCKER_HUB_TOKEN` уже принимаются `node-lifecycle.sh` (строки 25-30 текущей версии — `--docker-hub-username`/`--docker-hub-token`), но НЕ пробрасываются в `state_machine.py`. Необходимо добавить проброс в `_delegate` вызове или через env vars (os.environ — уже работает, т.к. shell экспортирует их).

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

### 5.1 Добавить docker_auth и deploy_context в INIT_STEPS

```python
INIT_STEPS: list[str] = [
    "ssh_access",           # 1
    "apt_deps",             # 2
    "tor_proxy",            # 3 (conditional — TOR_ENABLED)
    "install_docker",       # 4
    "docker_auth",          # 5  ← НОВЫЙ (сдвигает индексы 5→6, 6→7, ..., 21→22)
    "create_platform_user", # 6  ← был 5
    "create_ci_deploy_user",# 7  ← был 6
    "create_projects_base", # 8  ← был 7
    "firewall",             # 9  ← был 8
    "verify_core",          # 10 ← был 9
    "verify_node_configs",  # 11 ← был 10
    "decrypt_secrets",      # 12 ← был 11
    "ensure_secrets",       # 13 ← был 12
    "secrets_init",         # 14 ← был 13
    "read_node_yaml",       # 15 ← был 14
    "ghcr_auth",            # 16 ← был 15
    "sudoers",              # 17 ← был 16
    "install_acme",         # 18 ← был 17
    "node_update",          # 19 ← был 18
    "converge",             # 20 ← был 19
    "audit_log",            # 21 ← был 20
    "telegram",             # 22 ← был 21
    "deploy_context",       # 23 ← НОВЫЙ (в конец)
]
```

### 5.2 Добавить deploy_context в UPDATE_STEPS

```python
UPDATE_STEPS: list[str] = [
    "verify_core",      # 1
    "provision",        # 2
    "deliver_overlays", # 3
    "ssl_provision",    # 4
    "deploy_modules",   # 5
    "healthcheck",      # 6
    "converge",         # 7
    "deploy_context",   # 8 ← НОВЫЙ
]
```

### 5.3 Реализация в `_execute_init_step()` и `_execute_update_step()`

Добавить в `_execute_init_step()` (state_machine.py, после `elif step_name == "install_docker":` и до `elif step_name == "create_platform_user":`):

```python
elif step_name == "docker_auth":
    from . import docker_registry_auth as dra  # type: ignore[import-untyped]
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][init][docker_auth] Docker Hub creds not set — rate-limit may apply")
    else:
        dra.configure_docker_auth(username, token)
```

Добавить в конец `_execute_init_step()` (перед `# endregion`):

```python
elif step_name == "deploy_context":
    _step_deploy_context(core_dir, node_name, node_yaml)
```

В `steps.py`:

```python
def _step_deploy_context(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Deploy all context projects + restore certs + verify. Idempotent: skips already-deployed."""
    # CONTEXT: одна нода = один контекст. Берётся из node.yaml context (строка) или contexts[0].name
    context = os.environ.get("CONTEXT", "")
    if not context:
        context = _extract_context_from_node_yaml(node_yaml)
    if not context:
        logger.error("[IMP:10][deploy_context] CONTEXT not set and cannot be extracted from node.yaml")
        raise RuntimeError("CONTEXT not set — pass via --context or ensure node.yaml has context/contexts[0]")

    # 18.1 Resolve projects
    from ..deploy.context_deployer import deploy_context_projects  # type: ignore[import-untyped]
    from ..cert_orchestrator import orchestrate_certs             # type: ignore[import-untyped]

    # 18.2 + 18.3 Cert orchestration
    domains = _extract_domains(node_yaml, context)
    orchestrate_certs(domains, s3_cache_script=os.path.join(core_dir, "internal", "bootstrap", "s3-ssl-cache.sh"),
                      issue_cert_script=os.path.join(core_dir, "internal", "bootstrap", "issue-cert.sh"))

    # 18.4 Deploy projects
    results = deploy_context_projects(node_yaml, context)

    # 18.5 Render vhosts
    _run_subprocess(["bash", os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh"),
                     "--render-all", "--node", node_name])
    _run_subprocess(["docker", "exec", "nginx", "nginx", "-s", "reload"], non_fatal=True)

    # 18.6 Final verify
    verify_script = os.path.join(core_dir, "internal", "verify", "verify-domains.sh")
    _run_subprocess(["bash", verify_script, node_name], non_fatal=True)
```

**Добавить в `_execute_update_step()`:**

```python
elif step_name == "deploy_context":
    _step_deploy_context(core_dir, node_name, node_yaml)
```

**Добавить хелпер `_extract_context_from_node_yaml()` в steps.py:**

```python
def _extract_context_from_node_yaml(node_yaml_path: str) -> str:
    """Extract context name from node.yaml. One node = one context.
    Reads context (string) or contexts[0].name (array, first element).
    Returns empty string on parse error or if no context found."""
    import yaml
    try:
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        # Primary: context field (string)
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][context] Context from node.yaml context field: %s", ctx)
            return ctx
        # Fallback: contexts array (first element)
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            ctx = contexts[0].get("name", "")
            if ctx:
                logger.info("[IMP:8][context] Context from node.yaml contexts[0].name: %s", ctx)
                return ctx
    except Exception as e:
        logger.warning("[IMP:7][context] Failed to parse %s: %s", node_yaml_path, e)
    return ""
```

### 5.4 Обновить node-lifecycle.sh — перенумерация shell-обёрток

**Добавить `--context` в arg parser** (после `--auto-reconcile`):

```bash
--context) export CONTEXT="$2"; shift 2 ;;
```

**Добавить новые step-функции:**

```bash
step_4_5_docker_auth(){ _delegate --mode "${MODE}" --run-step 5; }       # NEW
step_18_deploy_context(){ _delegate --mode "${MODE}" --run-step 23; }    # NEW
```

**Перенумеровать существующие step-функции (все сдвинуты на +1 после вставки docker_auth):**

```bash
# БЫЛО: step_5_create_platform_user(){ _delegate --mode "${MODE}" --run-step 5; }
step_5_create_platform_user(){ _delegate --mode "${MODE}" --run-step 6; }  # changed 5→6
# БЫЛО: step_6_create_ci_deploy_user(){ _delegate --mode "${MODE}" --run-step 6; }
step_6_create_ci_deploy_user(){ _delegate --mode "${MODE}" --run-step 7; } # changed 6→7
# БЫЛО: step_6b_create_projects_base(){ _delegate --mode "${MODE}" --run-step 7; }
step_6b_create_projects_base(){ _delegate --mode "${MODE}" --run-step 8; } # changed 7→8
# БЫЛО: step_7_firewall(){ _delegate --mode "${MODE}" --run-step 8; }
step_7_firewall(){ _delegate --mode "${MODE}" --run-step 9; }              # changed 8→9
# БЫЛО: step_8_verify_core(){ _delegate --mode "${MODE}" --run-step 9; }
step_8_verify_core(){ _delegate --mode "${MODE}" --run-step 10; }          # changed 9→10
# БЫЛО: step_9_verify_node_configs(){ _delegate --mode "${MODE}" --run-step 10; }
step_9_verify_node_configs(){ _delegate --mode "${MODE}" --run-step 11; }  # changed 10→11
# БЫЛО: step_10_decrypt_secrets(){ _delegate --mode "${MODE}" --run-step 11; }
step_10_decrypt_secrets(){ _delegate --mode "${MODE}" --run-step 12; }     # changed 11→12
# БЫЛО: step_11_read_node_yaml(){ _delegate --mode "${MODE}" --run-step 14; }
step_11_read_node_yaml(){ _delegate --mode "${MODE}" --run-step 15; }      # changed 14→15
# БЫЛО: step_12_ghcr_auth(){ _delegate --mode "${MODE}" --run-step 15; }
step_12_ghcr_auth(){ _delegate --mode "${MODE}" --run-step 16; }           # changed 15→16
# БЫЛО: step_13_sudoers(){ _delegate --mode "${MODE}" --run-step 16; }
step_13_sudoers(){ _delegate --mode "${MODE}" --run-step 17; }             # changed 16→17
# БЫЛО: step_14_node_update(){ _delegate --mode "${MODE}" --run-step 18; }
step_14_node_update(){ _delegate --mode "${MODE}" --run-step 19; }         # changed 18→19
# БЫЛО: step_15_converge(){ _delegate --mode "${MODE}" --run-step 19; }
step_15_converge(){ _delegate --mode "${MODE}" --run-step 20; }            # changed 19→20
# БЫЛО: step_16_audit_log(){ _delegate --mode "${MODE}" --run-step 20; }
step_16_audit_log(){ _delegate --mode "${MODE}" --run-step 21; }           # changed 20→21
# БЫЛО: step_17_telegram(){ _delegate --mode "${MODE}" --run-step 21; }
step_17_telegram(){ _delegate --mode "${MODE}" --run-step 22; }            # changed 21→22
```

**Добавить в checkpoint_step блок init-режима** (между install-docker и user-platform):

```bash
CHECKPOINT_STEP_HASH="$(_step_hash "docker-auth")" checkpoint_step "docker-auth" step_4_5_docker_auth
```

**Добавить в `_do_update_steps()`:**

```bash
CHECKPOINT_STEP_HASH="$(_step_hash "deploy-context")" checkpoint_step "deploy-context" update_step_8_deploy_context
```

Где:

```bash
update_step_8_deploy_context(){ _delegate --mode "${MODE}" --run-step 8; }
```

### 5.5 Обновить state_machine.py CLI — добавить `--context`

В `build_parser()`:

```python
parser.add_argument("--context", help="Deployment context name (CONTEXT)")
```

В `main()` (после `--ghcr-token` блока):

```python
if args.context:
    os.environ.setdefault("CONTEXT", args.context)
```

### 5.6 Обновить _compute_step_hash() — добавить пути новых скриптов

```python
path_map: dict[str, list[str]] = {
    ...
    "docker_auth": [os.path.join(core_dir, "internal", "bootstrap", "docker_registry_auth.py")],
    "deploy_context": [
        os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py"),
        os.path.join(core_dir, "internal", "bootstrap", "cert_orchestrator.py"),
        os.path.join(core_dir, "internal", "verify", "verify-domains.sh"),
        os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh"),
    ],
}
```

### 5.7 Обновить INIT_STEP_COUNT

```python
INIT_STEP_COUNT = 23  # was 17
UPDATE_STEP_COUNT = 8  # was 7
```

### 5.8 Обновить AGENTS.md (bootstrap)

Обновить pipeline-диаграмму в `core/internal/bootstrap/AGENTS.md`:
- Добавить шаг 5 docker_auth (с пометкой shifted)
- Добавить шаг 23 deploy_context
- Добавить pre-flight gate
- Включить ensure_secrets и secrets_init в визуальное представление (сейчас пропущены)

---

## Phase 6: Каноническая регистрация — 30min

### 6.1 Makefile — новый таргет

В корневом `Makefile` (или соответствующем include) добавить:

```makefile
.PHONY: deploy-context
deploy-context: ## Deploy all projects of a context on a bootstrapped node
	@core/entrypoints/deploy-context.sh "$(NODE)" "$(CONTEXT)"
```

### 6.2 entrypoint-manifest.yaml

Добавить в секцию `bootstrap`:

```yaml
  - make_target: deploy-context
    mechanism: ssh+python
    delegates_to: core/entrypoints/deploy-context.sh → core/internal/bootstrap/deploy/context_deployer.py
    description: "Deploy all projects of a context on a bootstrapped node (standalone or post-bootstrap)"
```

Обновить существующую запись `bootstrap-node` — добавить новые delegation targets:

```yaml
  - make_target: bootstrap-node
    mechanism: ssh+rsync
    delegates_to: core/entrypoints/bootstrap.sh → core/internal/bootstrap/preflight.py → core/internal/bootstrap/node-lifecycle.sh --mode init → ... → core/internal/bootstrap/deploy/context_deployer.py + core/internal/bootstrap/docker_registry_auth.py + core/internal/bootstrap/cert_orchestrator.py
    description: "Idempotent bootstrap of a new node to FULL readiness (infra + context projects + certs + Docker Hub auth)"
```

Добавить `deploy-context` в `allowed_verbs`:

```yaml
allowed_verbs:
  ...
  - deploy-context
```

### 6.3 core/AGENTS.md

Добавить строку в таблицу канонических операций:

```markdown
| `make deploy-context` | Деплой всех проектов контекста на ноде | `make deploy-context NODE=<n> [CONTEXT=<ctx>]` | `core/entrypoints/deploy-context.sh` → `core/internal/bootstrap/deploy/context_deployer.py` |
```

### 6.4 AGENTS.md (root) — глоссарий

Добавить глагол:

```markdown
| ✅ | `deploy-context` | Деплой всех проектов контекста на ноде (post-bootstrap, standalone) |
```

### 6.5 TRAP[DECISION] в root AGENTS.md

```markdown
⚠️ TRAP[DECISION] · 2026-07-22 · HI · Bootstrap pipeline redesign — deploy-context as step 18 (index 23)
· Rejected: Option A (full rewrite of state machine, risk: regression after Decision Gate HARD STOP)
· Reason: Option B (evolutionary extension) preserves invariants, adds 4 components: preflight.py gate, docker_registry_auth.py step (index 5), cert_orchestrator.py, context_deployer.py step (index 23). Solves StatusReport 045 problems (projects not deployed, certs missing, Docker Hub rate-limit).
· Constraint: 1 node = 1 context (CONTEXT extracted from node.yaml context field). No multi-context ambiguity.
· ⚠️ Shell facade step functions renumbered (indices 5→6 through 21→22) — see TRAP[INDEX] in DevPlan.
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
| preflight.py | Удалить файл + убрать вызов из node-lifecycle.sh main() |
| docker_auth шаг | Убрать из INIT_STEPS + удалить docker_registry_auth.py + восстановить исходные --run-step индексы в shell facade |
| deploy_context шаг | Убрать из INIT_STEPS + UPDATE_STEPS + удалить context_deployer.py + восстановить исходные --run-step индексы |
| cert_orchestrator.py | Удалить файл + убрать импорт из state_machine.py |
| s3-ssl-cache bulk-restore | Убрать bulk-restore mode, оставить upload/download/check |
| deploy-context таргет | Убрать из entrypoint-manifest.yaml (allowed_verbs + bootstrap) + Makefile .PHONY |
| node-lifecycle.sh перенумерация | `git checkout -- core/internal/bootstrap/node-lifecycle.sh` |
| state_machine.py перенумерация | `git checkout -- core/internal/bootstrap/lifecycle/state_machine.py` |
| AGENTS.md изменения | `git checkout -- AGENTS.md core/AGENTS.md core/internal/bootstrap/AGENTS.md` |

---

## Timeline

| Phase | Описание | Время |
|-------|----------|-------|
| Phase 1 | Pre-flight gate (preflight.py) + интеграция в main() | 1.5h |
| Phase 2 | Docker Hub auth + registry-mirror (docker_registry_auth.py) | 1.5h |
| Phase 3 | Bulk cert restore (cert_orchestrator.py + s3-ssl-cache.sh bulk-restore mode) | 2h |
| Phase 4 | Context deployer (context_deployer.py) | 3h |
| Phase 5 | Интеграция в state_machine.py + перенумерация shell facade | 2h |
| Phase 6 | Каноническая регистрация (Makefile, entrypoint-manifest.yaml, AGENTS.md, allowed_verbs) | 30min |
| Phase 7 | Verification + staging test | 1.5h |
| **Total** | | **~12h** |

**⚠️ Самый рискованный этап:** Phase 5 (перенумерация shell facade). Каждая step-функция с индексом 5-21 должна быть обновлена. Пропуск одного индекса = silent failure при `--run-step N`.

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
