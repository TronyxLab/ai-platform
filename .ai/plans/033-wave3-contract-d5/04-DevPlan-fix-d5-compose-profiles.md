# 04-DevPlan-fix: COMPOSE_PROFILES Propagation (Wave 3 D5 Hotfix)

**Parent:** VerificationReport 033 (§7 Fix Plan)
**Task size:** SMALL (~8 files, config-only)
**Priority:** CRITICAL — blocks production deploy

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить CRITICAL дрейф: COMPOSE_PROFILES не пропагирована в production/testing скрипты,
                       что ломает `docker compose config` с `${VAR:?error}` после имплементации DevPlan 033 Option A.
DESCRIPTION:           8 файлов: добавить COMPOSE_PROFILES в platform-test.yml, push-gate.yml (status-page fix),
                       deploy-project.sh, adopt-project.sh, deploy-modules.sh, test_predeploy_gate.py,
                       test_smoke_platform.py, Makefile. Единый source of truth через Makefile `_get_all_profiles`.
RATIONALE:             QA VerificationReport 033 выявил 4 CRITICAL + 3 HIGH дрейфа. W3-R5a материализовался:
                       `${VAR:?error}` работает в CI (push-gate.yml) но ломает production (deploy-project.sh на VPS).
ACCEPTANCE_CRITERIA:
  AC-1: `Makefile` имеет target `_get_all_profiles`, возвращающий список из 13 модулей
  AC-2: `platform-test.yml` имеет `COMPOSE_PROFILES` env var (job level)
  AC-3: `push-gate.yml` содержит `status-page` в COMPOSE_PROFILES (13/13)
  AC-4: `deploy-project.sh`, `adopt-project.sh`, `deploy-modules.sh` имеют COMPOSE_PROFILES fallback перед `docker compose config`
  AC-5: `test_predeploy_gate.py`, `test_smoke_platform.py` имеют COMPOSE_PROFILES в test setup
  AC-6: `COMPOSE_PROFILES="<all-13>" docker compose config` exit 0
  AC-7: `make gate MODE=fast` зелёный (regression)
IMPLEMENTS:            VerificationReport 033 §7 Fix Plan (DRIFT-1..8)
IMPACTS:               8 files modified: Makefile, push-gate.yml, platform-test.yml, deploy-project.sh,
                       deploy-modules.sh, adopt-project.sh, test_predeploy_gate.py, test_smoke_platform.py
REQUIRES:              Чистый working tree, прочитанный VerificationReport 033
$END_ARTIFACT_CONTRACT

---

## TASK-1 (F1): Makefile — Source of Truth для COMPOSE_PROFILES

**File:** `Makefile`
**Change:** Добавить target `_get_all_profiles` и использовать в `gate` target.

```makefile
# L1 (новый target, перед validate-modules):
# COMPOSE_PROFILES source of truth: все 13 Docker-модулей с profiles.
# Используется CI и production-скриптами для ${VAR:?error} совместимости (DevPlan 033 Option A).
_get_all_profiles:
	@echo "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"

# L2 (в gate target, перед вызовом docker compose):
gate:
	@export COMPOSE_PROFILES="$$(make -s _get_all_profiles)"; \
	...
```

**Примечание:** `_`-префикс = internal target (не документируется в help).

---

## TASK-2 (F2): push-gate.yml — добавить status-page

**File:** `.github/workflows/push-gate.yml`
**Change:** Line 47: добавить `status-page` в список COMPOSE_PROFILES.

**Было:**
```yaml
COMPOSE_PROFILES: "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio"
```

**Стало:**
```yaml
COMPOSE_PROFILES: "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
```

**Check:** `rg "status-page" .github/workflows/push-gate.yml` → 1 match (COMPOSE_PROFILES list)

---

## TASK-3 (F3): platform-test.yml — добавить COMPOSE_PROFILES

**File:** `.github/workflows/platform-test.yml`
**Change:** Добавить `COMPOSE_PROFILES` env var на уровне job (перед steps), аналогично push-gate.yml.

**Добавить после `env:` блока (или создать новый):**
```yaml
env:
  COMPOSE_PROFILES: "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
```

**Check:** `rg "COMPOSE_PROFILES" .github/workflows/platform-test.yml` → 1 match

---

## TASK-4 (F4): deploy-project.sh — COMPOSE_PROFILES перед docker compose config

**File:** `core/internal/deploy/deploy-project.sh`
**Change:** Перед строкой 718 (`config_output="$(docker compose config ...`)` добавить:

```bash
# COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-$(make -s _get_all_profiles 2>/dev/null || echo 'postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page')}"
```

**Check:** `rg "COMPOSE_PROFILES" core/internal/deploy/deploy-project.sh` → ≥1 match

---

## TASK-5 (F5): adopt-project.sh — COMPOSE_PROFILES перед docker compose config

**File:** `core/internal/scaffold/adopt-project.sh`
**Change:** Перед строкой 386 (`_validate_networks()`, где вызывается `docker compose config`) добавить:

```bash
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-$(make -s _get_all_profiles 2>/dev/null || echo 'postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page')}"
```

**Check:** `rg "COMPOSE_PROFILES" core/internal/scaffold/adopt-project.sh` → ≥1 match

---

## TASK-6 (F6): deploy-modules.sh — review --profile usage

**File:** `core/internal/bootstrap/deploy-modules.sh`
**Change:** Review lines 462, 501, 537. Если используется `--profile` флаг в `docker compose` вызове — COMPOSE_PROFILES не нужен (явный profile переопределяет env var). Если `--profile` НЕ используется — добавить fallback:

```bash
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-$(make -s _get_all_profiles 2>/dev/null || echo 'postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page')}"
```

**N.B. (DevPlan 033 §4 W3-E3.3):** `deploy-modules.sh` line 534 строит `docker compose` команду с `--profile "$module_name"` для hermes-agent — явный профиль переопределяет COMPOSE_PROFILES, так что этот вызов безопасен БЕЗ изменений. Lines 462 и 501 требуют проверки.

---

## TASK-7 (F7): test_predeploy_gate.py — COMPOSE_PROFILES в test setup

**File:** `tests/test_predeploy_gate.py`
**Change:** В `test_project_compose_configs_valid` (строка ~769), перед вызовом `docker compose config --dry-run`, добавить в subprocess env:

```python
import os
compose_profiles = os.environ.get(
    "COMPOSE_PROFILES",
    "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
)
env_override = {**os.environ, "COMPOSE_PROFILES": compose_profiles}
# передать env=env_override в subprocess.run(...)
```

**Check:** `rg "COMPOSE_PROFILES" tests/test_predeploy_gate.py` → ≥1 match

---

## TASK-8 (F8): test_smoke_platform.py — COMPOSE_PROFILES в test_all_compose_configs_valid

**File:** `tests/test_smoke_platform.py`
**Change:** В `test_all_compose_configs_valid` (строка ~203-260), где вызывается `docker compose config`, добавить COMPOSE_PROFILES в subprocess env (аналогично TASK-7).

**Check:** `rg "COMPOSE_PROFILES" tests/test_smoke_platform.py` → существующие 3 + 1 новый = ≥4 matches

---

## TASK-9 (F9): Верификация

1. `COMPOSE_PROFILES="postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page" docker compose config` → exit 0
2. `make gate MODE=fast` → зелёный
3. `python -m pytest tests/ -m gate -v` → все gate-тесты зелёные
4. `make validate-modules` → exit 0

---

## Порядок выполнения

```
TASK-1 (Makefile _get_all_profiles) → параллельно:
  ├─ TASK-2 (push-gate.yml status-page fix)
  ├─ TASK-3 (platform-test.yml)
  ├─ TASK-4 (deploy-project.sh)
  ├─ TASK-5 (adopt-project.sh)
  ├─ TASK-6 (deploy-modules.sh review)
  ├─ TASK-7 (test_predeploy_gate.py)
  └─ TASK-8 (test_smoke_platform.py)
       ↓
  TASK-9 (верификация)
```

$END_DEVPLAN
