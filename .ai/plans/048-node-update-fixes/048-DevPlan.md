$ARTIFACT_CONTRACT
PURPOSE: DevPlan for fixing 4 post-node-update-test issues: sys.modules crash, FORCE_MODE double-run, healthcheck race condition, PLATFORM_DOMAIN export
DESCRIPTION: Comprehensive fix plan with verified hypotheses, code locations, and verification strategy
RATIONALE: node-update test revealed 4 bugs; all prevent "green CI" parity for bootstrap/node-update lifecycle
ACCEPTANCE_CRITERIA:
  1. `make node-update NODE=tronyx-vps` — healthcheck 14/14 PASS (or <2 WARN) после свежего deploy
  2. `make node-update NODE=tronyx-vps` — NO double-run (state machine ≤8 steps total)
  3. deploy_context step — cert_orchestrator and context_deployer execute (no `NoneType.__dict__` crash)
  4. PLATFORM_DOMAIN из node.yaml → ssl_provision выполняется (не skipped)
  5. `make gate MODE=fast` — все зелёные
IMPLEMENTS: 048
IMPACTS:
  - core/internal/bootstrap/lifecycle/state_machine.py (3 change sites)
  - core/internal/bootstrap/node-lifecycle.sh (1 change site)
REQUIRES: node.yaml domain field (уже присутствует), Python yaml lib (уже используется)

---

# DevPlan 048 — Node-Update Fixes

## Executive Summary

После тестирования `make node-update NODE=tronyx-vps` выявлено 4 бага. Все 4 имеют подтверждённые root cause и изолированные фиксы (не рефакторинг). После исправлений ожидается: node-update = bootstrap = CI green parity.

---

## Problem Matrix

| ID | Severity | Symptom | Root Cause | Fix Lines |
|----|----------|---------|------------|-----------|
| P0 | **BLOCKER** | `'NoneType' object has no attribute '__dict__'` в deploy_context | `importlib.util.spec_from_file_location` не регистрирует модуль в `sys.modules` → `@dataclass` падает | state_machine.py +4 |
| P1 | **HI** | State machine двойной прогон (шаги 1-6 ×2) | `FORCE_MODE=false` — непустая строка → `${FORCE_MODE:+--force}` всегда `--force` | node-lifecycle.sh +1 |
| P2 | **MED** | Healthcheck 14/14 FAILED после свежего deploy | Race: retry 4×3s=12s, Docker HEALTHCHECK start_period=60-120s | state_machine.py +2 |
| P3 | **MED** | SSL provisioning skipped (PLATFORM_DOMAIN) | `_ssl_provision` читает только `os.environ`, не извлекает `domain` из node.yaml | state_machine.py +5 |

---

## Fix Specifications

### FIX 1: P0 — sys.modules registration (state_machine.py)

**File:** `core/internal/bootstrap/lifecycle/state_machine.py`
**Lines:** 1960–1962 (cert_orchestrator), 1978–1980 (context_deployer)
**Type:** 4-line addition

**Before:**
```python
# Line 1961-1962
cert_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cert_mod)
```

**After:**
```python
# Line 1961-1963
cert_mod = importlib.util.module_from_spec(spec)
sys.modules["cert_orchestrator"] = cert_mod  # ← FIX: register before exec_module
spec.loader.exec_module(cert_mod)
```

**Same pattern for context_deployer** (line 1979-1980):
```python
deployer_mod = importlib.util.module_from_spec(spec)
sys.modules["context_deployer"] = deployer_mod  # ← FIX
spec.loader.exec_module(deployer_mod)
```

**Why this works:** Python's `@dataclass` decorator calls `dataclasses._is_type()` at class definition time, which resolves `cls.__module__` via `sys.modules.get(module_name)`. Without registration, returns `None` → `.get().__dict__` → `AttributeError`.

**Verification:**
```bash
# After fix, run deploy_context on VPS:
ssh root@VPS "python3 -c 'from lifecycle.state_machine import deploy_context; ...'"
# Expect: cert orchestration and project deploy execute (no AttributeError in warnings)
```

---

### FIX 2: P1 — FORCE_MODE empty default (node-lifecycle.sh)

**File:** `core/internal/bootstrap/node-lifecycle.sh`
**Line:** 11
**Type:** 1-character change (`false` → `""`)

**Before:**
```bash
set -euo pipefail; MODE=""; RESUME_MODE=false; FORCE_MODE=false; DRY_RUN_MODE=false
```

**After:**
```bash
set -euo pipefail; MODE=""; RESUME_MODE=false; FORCE_MODE=""; DRY_RUN_MODE=false
```

**Why this works:** `${FORCE_MODE:+--force}` in bash expands to `--force` when `FORCE_MODE` is **any** non-empty string (including `"false"`). Empty string → no expansion → `--force` only passed when user explicitly uses `--force` flag (which sets `FORCE_MODE=true`, a non-empty string). Shell-level `[[ "$FORCE_MODE" == "true" ]]` checkpoint checks are unaffected — but there are NONE using string comparison with `${FORCE_MODE:+--force}` (all shell checks use `[[ "$FORCE_MODE" == "true" ]]` which correctly handles `""` as `false`).

**⚠️ Compatibility check:**
```bash
# Verify no other code uses ${FORCE_MODE:+...} pattern expecting "false" to suppress:
grep -n 'FORCE_MODE:+' core/internal/bootstrap/node-lifecycle.sh
grep -n 'FORCE_MODE:+' core/internal/bootstrap/lifecycle/state_machine.py
grep -n 'FORCE_MODE' core/entrypoints/node-update.sh
```
Lines 185, 217 of node-lifecycle.sh use `${FORCE_MODE:+--force}` — this is the ONLY usage, and the fix is correct.

---

### FIX 3: P2 — Healthcheck retry window (state_machine.py)

**File:** `core/internal/bootstrap/lifecycle/state_machine.py`
**Lines:** 1773–1774
**Type:** 2-value change

**Before:**
```python
hc_max_retries = 4
hc_retry_interval = 3
```

**After:**
```python
hc_max_retries = 10
hc_retry_interval = 10
```

**Why this works:** Docker compose HEALTHCHECK настроен с `start_period=60-120s` (зависит от модуля), `interval=30s`, `retries=3`. Минимальное время до первого healthy: `start_period + interval × retries = 60 + 90 = 150s`. Текущие `4 × (3s + subprocess) ≈ 12-15s` недостаточны.

Новые параметры: `10 × (10s + subprocess) ≈ 100-130s` эффективного ожидания. С учётом того что deploy-modules (step 5) занимает время на pull + compose up (≈60-120s для всех модулей), healthcheck с новыми параметрами будет иметь достаточно времени.

**Max impact на время node-update:** было ~15s, станет ~130s (увеличение на ~2 минуты в худшем случае). Для update-режима на работающей ноде (где контейнеры уже запущены) — healthcheck instant (все уже healthy), retries не срабатывают → 0s overhead.

**Альтернатива (отклонена):** добавить `time.sleep(warmup_period)` между deploy и healthcheck. Отклонено — добавляет фиксированную задержку всегда, даже когда контейнеры не перезапускались (idempotent deploy → no containers restarted → no warmup needed).

---

### FIX 4: P3 — PLATFORM_DOMAIN from node.yaml (state_machine.py)

**File:** `core/internal/bootstrap/lifecycle/state_machine.py`
**Line:** 1717 (функция `_ssl_provision`)
**Type:** 5-line fallback addition

**Before:**
```python
platform_domain = os.environ.get("PLATFORM_DOMAIN", "")
if not platform_domain:
    logger.warning("[IMP:7][ssl] PLATFORM_DOMAIN not set — skipping SSL provisioning")
    return
```

**After:**
```python
platform_domain = os.environ.get("PLATFORM_DOMAIN", "")
# Fallback: extract domain from node.yaml (SSH env doesn't carry PLATFORM_DOMAIN)
if not platform_domain and node_yaml and os.path.isfile(node_yaml):
    try:
        import yaml
        with open(node_yaml) as f:
            node_data = yaml.safe_load(f)
        if isinstance(node_data, dict):
            platform_domain = node_data.get("domain", "") or ""
            if platform_domain:
                logger.info("[IMP:7][ssl] PLATFORM_DOMAIN resolved from node.yaml: %s", platform_domain)
    except Exception:
        pass
if not platform_domain:
    logger.warning("[IMP:7][ssl] PLATFORM_DOMAIN not set — skipping SSL provisioning")
    return
```

**Why this works:** node.yaml содержит поле `domain` (подтверждено на tronyx-vps: `domain: tronyx.ru`). Функция `_extract_domains` уже делает точно такой же парсинг. При локальном запуске (dev machine) PLATFORM_DOMAIN приходит из .env → os.environ; при remote SSH-запуске — парсится из node.yaml.

**Контракт:** node.yaml `domain` field = platform domain. Это существующий контракт (`_extract_domains` line 2064).

---

## Verification Plan

### Pre-merge (локально)

```bash
# 1. Unit tests (state_machine healthcheck, import, ssl)
python -m pytest tests/unit/test_state_machine.py -v

# 2. Test FORCE_MODE fix (bash dry-run)
bash core/internal/bootstrap/node-lifecycle.sh --mode update --dry-run --node-name test \
  --node-yaml /dev/null 2>&1 | grep -c "force"  # Expect: 0 (no --force passed)

# 3. CI gate
make gate MODE=fast
```

### Post-merge (на VPS)

```bash
# 1. Доставить исправленный код
make node-update NODE=tronyx-vps

# 2. Проверить healthcheck (ВСЕ модули должны быть PASS или <2 WARN)
ssh root@VPS "python3 -c '
import json
with open(\"/var/lib/platform/.bootstrap/state.json\") as f:
    state = json.load(f)
step6 = state[\"steps\"].get(\"6\", {})
print(f\"Healthcheck step status: {step6.get(\"status\")}\")
'"

# 3. Проверить SSL (НЕ должен выдать skipped)
ssh root@VPS "grep 'PLATFORM_DOMAIN not set' /var/log/platform/audit.log | tail -5"
# Expect: NO output (PLATFORM_DOMAIN resolved from node.yaml)

# 4. Проверить deploy_context (НЕ должен выдать NoneType.__dict__)
ssh root@VPS "grep 'NoneType.*__dict__\|Cannot load cert_orchestrator\|Cannot load context_deployer' /var/log/platform/audit.log | tail -5"
# Expect: NO output

# 5. Проверить single-run (state machine должен иметь ровно 8 шагов, не 16)
ssh root@VPS "python3 -c '
import json
with open(\"/var/lib/platform/.bootstrap/state.json\") as f:
    state = json.load(f)
print(f\"Total steps: {len(state[\"steps\"])}\")
# Expect: 8 (update mode)
# NOT: 16 (double run)
'"
```

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| P0 fix breaks deploying modules that DON'T use @dataclass | NONE | — | `sys.modules` registration is unconditional — always safe |
| P1 fix breaks checkpoint when --force IS intended | LOW | Steps may run when they should skip | Shell checkpoints use `[[ "$FORCE_MODE" == "true" ]]` — NOT affected by the change |
| P2 fix increases node-update time | MEDIUM (update on running node) | LOW (only adds time when containers are actually restarting) | Already-running containers → healthcheck instant (PASS on attempt 1) |
| P3 fix — node.yaml missing `domain` field | LOW | Same as current behavior (skip SSL) | `try/except` gracefully falls back to existing behavior |
| P3 fix — node.yaml `domain` field renamed in future | LOW | yaml.safe_load handles missing keys gracefully | `.get("domain", "")` returns empty string for missing key |

---

## Change Summary

```
Files changed: 2
  core/internal/bootstrap/lifecycle/state_machine.py  — 3 change sites, +12 lines
  core/internal/bootstrap/node-lifecycle.sh           — 1 change site, +1 char

Total diff: +13 lines, 1 character change
```

Все фиксы минимальны, изолированы, без рефакторинга. Ни один не затрагивает CI pipeline, Makefile targets, или архитектурные инварианты.
