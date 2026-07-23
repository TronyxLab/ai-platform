# DevPlan 055 — Bootstrap Bugfixes: deploy_context Step 23 Cascade

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Исправить 5 багов, обнаруженных при полном bootstrap tronyx-vps (StatusReport 054):
           каскадный сбой deploy_context (step 23) — add-vhost.sh, verify-domains.sh, NoneType в Python-оркестраторах,
           плюс неверный NODE_NAME в status-page.
  DESCRIPTION: Bugs 1-4 — кластер в deploy_context step 23 (state_machine.py + steps.py).
               Bug 5 — изолированный (docker-compose.base.yml).
               Односимвольные правки в 5 файлах, 4 unit-теста, gate-валидация.
  RATIONALE: StatusReport 054 §Known Issues: без этих правок `make bootstrap-node` и `make deploy-context`
             не доводят ноду до полной готовности — проекты остаются без nginx-vhosts, verify падает.
  ACCEPTANCE_CRITERIA:
    AC-1: `make deploy-context NODE=tronyx-vps` выполняет add-vhost.sh --render-all успешно (с --node-configs-dir)
    AC-2: verify-domains.sh не падает с `unbound variable` (platform_root передан или default)
    AC-3: context_deployer.py / cert_orchestrator.py не выбрасывают NoneType (результаты захвачены + None-guard)
    AC-4: status-page контейнер не использует test-node/node.yaml (NODE_NAME fallback → unknown)
    AC-5: 4+ unit-тестов: test_render_vhosts_passes_config_dir, test_verify_domains_no_unbound,
          test_context_deployer_result_captured, test_status_page_node_name_fallback
    AC-6: `make gate MODE=fast` PASS (без регрессий)
    AC-7: `make test MARKER=static` PASS
  IMPLEMENTS: StatusReport 054 §Known Issues 1-5
  IMPACTS:
    - core/internal/bootstrap/lifecycle/state_machine.py (MODIFIED — _step_deploy_context_inline: строки 1929-1966)
    - core/internal/bootstrap/lifecycle/steps.py (MODIFIED — _step_deploy_context: строки 861-907)
    - core/internal/bootstrap/cert_orchestrator.py (MODIFIED — add() None-guard: строка 148)
    - core/internal/verify/verify-domains.sh (MODIFIED — $2 default: строка 247)
    - core/modules/status-page/docker-compose.base.yml (MODIFIED — NODE_NAME fallback: строки 39, 45)
    - tests/unit/test_deploy_context_integration.py (NEW — 4 теста)
    - .ai/plans/055-bootstrap-bugfixes/ (NEW — настоящий артефакт)
  REQUIRES: Python ≥3.10, pytest, доступ к state_machine.py/steps.py/cert_orchestrator.py
  TASK_SIZE: STANDARD (5 файлов, 4 теста, односимвольные правки)
-->

$START_DEVPLAN

## Overview

**Status:** Draft → Architect review
**DevPlan:** 055
**Session:** 2026-07-22
**Priority:** HIGH — блокирует deploy_context step 23
**Size:** STANDARD

### Problem Statement

Из StatusReport 054 (Full Re-bootstrap tronyx-vps):

| # | Баг | Severity | Симптом | Файл |
|---|-----|----------|---------|------|
| B4 | add-vhost.sh падает | **CRITICAL** | `--node-configs-dir <path> is required` → vhosts не генерируются → проекты без HTTPS | state_machine.py, steps.py |
| B3 | verify-domains.sh unbound | **HIGH** | `$2: unbound variable` → verify падает | state_machine.py, steps.py, verify-domains.sh |
| B1 | context_deployer NoneType | **HIGH** | `'NoneType' object has no attribute '__dict__'` — non-fatal, маскирует ошибки | state_machine.py |
| B2 | cert_orchestrator NoneType | **HIGH** | Та же ошибка — non-fatal | state_machine.py, cert_orchestrator.py |
| B5 | status-page test-node | **MEDIUM** | NODE_NAME fallback `test-node` вместо реального нода | docker-compose.base.yml |

**B1–B4 — каскадный кластер** в `deploy_context` step 23. Две параллельные реализации (`_step_deploy_context` в steps.py, `_step_deploy_context_inline` в state_machine.py) имеют одинаковые ошибки вызова shell-скриптов.

---

## $TASKS

### TASK-1: Fix add-vhost.sh + verify-domains.sh вызовы (CRITICAL + HIGH)

**Приоритет:** 🔴 HIGH
**Файлы:** `state_machine.py`, `steps.py`, `verify-domains.sh`

**Корень:** Оба вызывающих места передают недостаточные аргументы shell-скриптам.

**Исправления:**

#### 1a. state_machine.py `_step_deploy_context_inline()` (строки ~1929–1966)

Добавить `node_configs_dir` и `platform_root` в вызовы:

```python
# BEFORE (строка 1929):
cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)

# AFTER:
cert_result = cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)
logger.info("[IMP:9][deploy_context] Cert orchestration complete: %s", cert_result.to_dict())
```

```python
# BEFORE (строка 1946):
deployer_mod.deploy_context_projects(node_yaml, context)

# AFTER:
results = deployer_mod.deploy_context_projects(node_yaml, context) or []
logger.info("[IMP:9][deploy_context] Project deploy complete: %d projects", len(results))
```

```python
# BEFORE (строки 1955–1958):
_subprocess_run(
    ["bash", vhost_script, "--render-all", "--node", node_name],
    "render_vhosts", non_fatal=True,
)

# AFTER:
node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
_subprocess_run(
    ["bash", vhost_script, "--render-all", "--node", node_name,
     "--node-configs-dir", node_configs_dir],
    "render_vhosts", non_fatal=True,
)
```

```python
# BEFORE (строка 1966):
_subprocess_run(["bash", verify_script, node_name], "final_verify", non_fatal=True)

# AFTER:
platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
_subprocess_run(
    ["bash", verify_script, node_name, platform_root], "final_verify", non_fatal=True
)
```

#### 1b. steps.py `_step_deploy_context()` (строки ~861–907)

Аналогичные исправления:

```python
# BEFORE (строка 861):
cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)

# AFTER:
cert_result = cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)
logger.info("[IMP:9][deploy_context] Cert orchestration: %d domains", len(cert_result.domains))
```

```python
# BEFORE (строка 877):
results = deployer_mod.deploy_context_projects(node_yaml, context)

# AFTER (добавить or []):
results = deployer_mod.deploy_context_projects(node_yaml, context) or []
```

```python
# BEFORE (строки 890–891):
subprocess.run(
    ["bash", vhost_script, "--render-all", "--node", node_name],
    ...
)

# AFTER:
node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
subprocess.run(
    ["bash", vhost_script, "--render-all", "--node", node_name,
     "--node-configs-dir", node_configs_dir],
    ...
)
```

```python
# BEFORE (строки 907–908):
subprocess.run(
    ["bash", verify_script, node_name],
    ...
)

# AFTER:
platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
subprocess.run(
    ["bash", verify_script, node_name, platform_root],
    ...
)
```

#### 1c. verify-domains.sh defence-in-depth (строка 247)

```bash
# BEFORE:
local node_name="$1" platform_root="$2"

# AFTER:
local node_name="${1:-}" platform_root="${2:-${PLATFORM_ROOT:-/opt/platform}}"
```

---

### TASK-2: Fix cert_orchestrator.py None-guard (HIGH)

**Приоритет:** 🟡 MEDIUM
**Файл:** `core/internal/bootstrap/cert_orchestrator.py`

**Корень:** `result.add(domain_result)` может получить None.

**Исправление (строка 148):**

```python
# BEFORE:
result.add(domain_result)

# AFTER:
if domain_result is not None:
    result.add(domain_result)
```

---

### TASK-3: Fix status-page NODE_NAME fallback (MEDIUM)

**Приоритет:** 🟢 LOW
**Файл:** `core/modules/status-page/docker-compose.base.yml`

**Корень:** `test-node` fallback скрывает проблему отсутствия NODE_NAME.

**Исправления (строки 39, 45):**

```yaml
# BEFORE (строка 39):
- ${NODE_CONFIGS_DIR:-/opt/node-configs}/${NODE_NAME:-test-node}/node.yaml:/opt/node-configs/${NODE_NAME:-test-node}/node.yaml:ro

# AFTER:
- ${NODE_CONFIGS_DIR:-/opt/node-configs}/${NODE_NAME:-unknown}/node.yaml:/opt/node-configs/${NODE_NAME:-unknown}/node.yaml:ro
```

```yaml
# BEFORE (строка 45):
NODE_NAME: ${NODE_NAME:-test-node}

# AFTER:
NODE_NAME: ${NODE_NAME:-unknown}
```

---

## $PARALLEL_GROUPS

```
Wave 1 (parallel — независимые файлы):
├── Group A: TASK-1 (state_machine.py + steps.py + verify-domains.sh) — CRITICAL
├── Group B: TASK-2 (cert_orchestrator.py) — MEDIUM
└── Group C: TASK-3 (docker-compose.base.yml) — LOW

Wave 2 (sequential — depends on Wave 1):
└── Group D: TASK-1b (unit-тесты для Wave 1 фиксов)
```

---

## $TEST_SPEC

Файл: `tests/unit/test_deploy_context_integration.py` (NEW)

| # | Тест | Что проверяет | Подход |
|---|------|---------------|--------|
| T1 | `test_add_vhost_passes_config_dir` | state_machine вызывает add-vhost.sh с --node-configs-dir | mock subprocess, assert '--node-configs-dir' in args |
| T2 | `test_verify_domains_passes_platform_root` | state_machine вызывает verify-domains.sh с двумя аргументами | mock subprocess, assert len(cmd) > 2 |
| T3 | `test_context_deployer_result_captured` | deploy_context_projects результат захвачен и проверен на None | прямой вызов с mock node_yaml, assert isinstance(results, list) |
| T4 | `test_cert_orchestrator_result_captured` | orchestrate_certs результат захвачен | mock cert_orchestrator, assert cert_result is not None |

---

## Implementation Notes

### Файловая карта

```
core/internal/bootstrap/lifecycle/state_machine.py   # TASK-1a
core/internal/bootstrap/lifecycle/steps.py           # TASK-1b
core/internal/bootstrap/cert_orchestrator.py         # TASK-2
core/internal/verify/verify-domains.sh               # TASK-1c
core/modules/status-page/docker-compose.base.yml     # TASK-3
tests/unit/test_deploy_context_integration.py        # NEW (Wave 2)
```

### Backward Compatibility

- `verify-domains.sh` → добавлен default для $2 (не ломает существующие вызовы с двумя аргументами)
- `docker-compose.base.yml` → `test-node` → `unknown` (несовместимый, но осознанный: лучше явный fail чем скрытый)
- Остальные изменения — добавление аргументов / захват результатов (strictly additive)

### Rollback

```bash
git revert <merge-commit>  # Все изменения в одном коммите
```

### Timeline

| Task | Time |
|------|------|
| TASK-1a | 30min |
| TASK-1b | 30min |
| TASK-1c | 5min |
| TASK-2 | 10min |
| TASK-3 | 5min |
| Tests (Wave 2) | 45min |
| Gate validation | 15min |
| **Total** | **~2h** |

$END_DEVPLAN
