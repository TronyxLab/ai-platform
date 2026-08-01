# 10-VerificationReport — Волна 117 пост-рефакторинг дрейф (брифики C–F)

$ARTIFACT_CONTRACT
- PURPOSE: Приёмочная верификация слияния брификов C, D, E, F волны 117 post-refactor-drift в main.
- DESCRIPTION: QA-аудит по 7 контрольным точкам: gate, cross-file drift, Branch C (SoT-унификация), Branch D (реестры+гейты), Branch E (docs sync), Branch F (Test Honesty), итоговый вердикт.
- RATIONALE: Обеспечить целостность кодовой базы после слияния 4 параллельных веток перед ручным тестированием на tronyx-vps.
- ACCEPTANCE_CRITERIA: AC2 (gate MODE=fast зелёный + check-manifests) — частично.
- IMPLEMENTS: Брифики C (D18-D26), D (D27-D36,D67-D71), E (D37-D45), F (D46-D50) из 01-Brief.md.
- IMPACTS: core/modules/hermes-agent/watchdog/agent_watchdog.py, tests/test_cross_layer_imports.py, tests/test_s3_client.py.
- REQUIRES: Исправление DRIFT-1 (allowlist) и DRIFT-2 (test_s3_client import).

---

🔒 **Verified against SHA** `18c87f5596871358281921c8313835312f895d07` (HEAD, чистое рабочее дерево)

---

## 1. GATE: `make gate MODE=fast`

**Результат:** 🔴 RED — 1 FAIL из 40 тестов.

| Статус | Количество | Файл |
|--------|----------|------|
| PASSED | 39 | — |
| FAILED | 1 | `tests/gates/test_gate_cross_layer.py::test_gate_cross_layer` |

**Детали падения:**

```
AssertionError: Gate #8 v2 FAILED: 1 cross-layer violation(s):
    core/modules/hermes-agent/watchdog/agent_watchdog.py:58 —
    [modules→internal] import 'core.internal.shared.timeouts' (forbidden)
```

**Root cause:** Cross-layer allowlist `_CROSS_LAYER_ALLOWLIST` (test_cross_layer_imports.py:153) содержит запись для `agent_watchdog.py:51` с reason `shared.timeouts`, но реальный импорт `core.internal.shared.timeouts` находится на строке **58**, а не 51. Запись с неверным номером строки (51 — это последняя строка импорта `docker_compose`) не покрывает реальный violations.

**Severity:** HIGH (блокирует CI gate, но fix тривиален).

### Попутные предупреждения (WARNING, не блокируют):

Тест выдал 4 WARNING о «LINT-EXEMPT present but no longer suppresses violations (TASK-6C)» для строк `agent_watchdog.py:47, 52, 55, 58`. Это не ошибки — комментарии `# LINT-EXEMPT` избыточны, поскольку enforcement теперь через allowlist. Рекомендуется удалить.

---

## 2. CROSS-FILE DRIFT (инварианты 1–11 root AGENTS.md)

### 2a. Merge-конфликты

| Проверка | Результат | Evidence |
|----------|----------|----------|
| `<<<<<<< HEAD \| >>>>>>>` в `core/**/*.py` | ✅ PASS | 0 совпадений |
| `<<<<<<< HEAD \| >>>>>>>` в `tests/**/*.py` | ✅ PASS | 0 совпадений |
| `=======` в `core/**/*.py` | ✅ PASS | Только разделители логов/комментариев (8 строк), не конфликтные маркеры |

### 2b. docker_orchestrator.py — слияние C+D

| Проверка | Результат | Evidence |
|----------|----------|----------|
| `_reconcile_orphan_containers` удалена | ✅ PASS | 0 упоминаний в `core/internal/` |
| `DOCKER_CMD_TIMEOUT` используется | ✅ PASS | orchestrator.py:1045, deploy_engine.py:617,733,792 |
| `class DeployResult` удалён | ✅ PASS | 0 упоминаний в `core/internal/` |

### 2c. Инвариант 5 (entrypoint-manifest)

Gate `test_gate_manifest_integrity.py` — PASSED. Манифест синхронизирован.

### 2d. Инвариант 11 (Manifest Generation Contract)

Выполнить `make check-manifests` не удалось (блокировка tool permissions), но косвенные признаки целостности:
- `test_gate_manifest_integrity.py` PASSED
- `test_gate_test_inventory.py` (9/9) PASSED
- Gate-тесты глоссария G4 PASSED

---

## 3. БРИФ C: SoT-унификация (D18–D26)

| # | Проверка | Ожидание | Результат | Evidence |
|---|----------|----------|----------|----------|
| C1 | `class DeployResult` в `core/internal/` | 0 | ✅ PASS | grep: 0 совпадений |
| C2 | `_reconcile_orphan_containers` | 0 | ✅ PASS | grep: 0 совпадений |
| C3 | `shared/ssl_certs.py` существует | Да | ✅ PASS | `core/internal/shared/ssl_certs.py` |
| C4 | `shared/s3_client.py` существует | Да | ✅ PASS | `core/internal/shared/s3_client.py` |
| C5 | `shared/timeouts.py` существует | Да | ✅ PASS | `core/internal/shared/timeouts.py` |
| C6 | `cert_orchestrator.py` импортирует `shared/ssl_certs` | Да | ✅ PASS | cert_orchestrator.py:41 |
| C7 | `s3_ssl_cache.py` импортирует `shared/ssl_certs` + `shared/s3_client` | Да | ✅ PASS | s3_ssl_cache.py:52-53 |
| C8 | `preflight.py` импортирует `shared/s3_client` | Да | ✅ PASS | preflight.py:39 |
| C9 | `agent_watchdog.py`: AuditLogger удалён | Да | ✅ PASS | Только в docstring (стр.14,814) |
| C10 | `agent_watchdog.py`: DockerManager.compose_* → `shared/docker_compose` | Да | ✅ PASS | watchdog:47 импорт shared.docker_compose |
| C11 | Unit-тесты (ssl_certs, agent_watchdog, orphan_reconciler, docker_orchestrator, cert_orchestrator, platform_config) | Все PASS | ✅ PASS | 77 passed, 0 failed |

### ❌ Нарушения Branch C:

| ID | Severity | Файл:строка | Проблема | Fix |
|----|----------|-------------|----------|-----|
| **DRIFT-2** | HIGH | `tests/test_s3_client.py:23` | `from s3_client import S3Client` → `ModuleNotFoundError: No module named 's3_client'`. Импорт не учитывает, что модуль находится в `core/internal/shared/s3_client.py`, а conftest не добавляет `core/internal/shared/` в `sys.path`. | Заменить на `from core.internal.shared.s3_client import S3Client` |
| **NOTE-1** | LOW | `tests/unit/test_key_provisioner.py` | Файл не существует. Однако существует `tests/unit/test_llm_key_provisioner.py` (7 passed). Имя в задании пользователя не совпадает с фактическим именем файла — вероятно, опечатка в спецификации. `test_llm_key_provisioner.py` существует и проходит. | Уточнить у пользователя, является ли `test_llm_key_provisioner.py` целевым файлом |

---

## 4. БРИФ D: Реестры таймаутов/портов/env + CI-гейты (D27–D36, D67–D71)

| # | Проверка | Ожидание | Результат | Evidence |
|---|----------|----------|----------|----------|
| D1 | `ConnectTimeout=\d+` в `.github/workflows/` | 0 | ✅ PASS | grep: 0 совпадений |
| D2 | `timeout=15` в `core/internal/` (docker/ssh/healthcheck) | 0 | ✅ PASS | grep: 0 совпадений |
| D3 | Watchdog таймауты из `timeouts.py` | Да | ✅ PASS | watchdog:58 импорт WATCHDOG_* |
| D4 | `STATUS_PAGE_PORT` в `platform-infra.yaml` env_defaults | Да | ✅ PASS | platform-infra.yaml:233: `STATUS_PAGE_PORT: 8080` |
| D5 | `test_gate_timeout_literals.py` | PASS | ✅ PASS | 2/2 passed |
| D6 | `test_gate_ssh_opts_sole_path.py` | PASS | ✅ PASS | 4/4 passed |
| D7 | `test_gate_docker_sole_path.py` | PASS | ✅ PASS | 2/2 passed |
| D8 | `REQUIRE_HONESTY_MODE: marker` в CI workflows | Присутствует | ✅ PASS | platform-test.yml:74, platform-gate-fast.yml:44 |

### ❌ Нарушения Branch D:

| ID | Severity | Файл:строка | Проблема | Fix |
|----|----------|-------------|----------|-----|
| **DRIFT-1** | HIGH | `tests/test_cross_layer_imports.py:181` | Allowlist содержит `agent_watchdog.py:51` для `shared.timeouts`, но реальный импорт на строке **58**. Несоответствие номера строки → gate RED. | Изменить `51` → `58` в `_CROSS_LAYER_ALLOWLIST` |
| **NOTE-2** | LOW | `core/modules/hermes-agent/watchdog/agent_watchdog.py:47,52,55,58` | 4 строки с `# LINT-EXEMPT` — комментарий избыточен при allowlist-режиме. Gate-тест выдаёт WARNING (не блокирует). | Удалить `# LINT-EXEMPT` комментарии (косметика) |

---

## 5. БРИФ F: Test Honesty R1–R5 (D46–D50)

| # | Проверка | Ожидание | Результат | Evidence |
|---|----------|----------|----------|----------|
| F1 | `pytest.skip.*Port.*not reachable` в tests/ | 0 (только докстринг) | ✅ PASS | Только `honesty.py:110` (комментарий) |
| F2 | `@pytest.mark.skipif.*docker` в tests/ | 0 | ✅ PASS | grep: 0 совпадений |
| F3 | `pytest.skip.*not set.*cannot authenticate` в tests/ | 0 | ✅ PASS | grep: 0 совпадений |
| F4 | R4: `require_service_healthy` импортируется | Да | ✅ PASS | 2 файла + honesty.py:98 (определение) |
| F5 | R5: negative-тесты `test_gate_test_inventory.py` | Да | ✅ PASS | 3 negative (undocumented removal, rename exempt, documented removal) — 9/9 passed |
| F6 | R5: negative-тест `test_gate_image_tag_form.py` | Да | ✅ PASS | `test_bare_latest_rejected_negative` — 4/4 passed |
| F7 | R5: negative-тест `test_gate_volumes_sot.py` | Да | ✅ PASS | 5/5 passed |
| F8 | `test_inventory.yaml` idempotent | `test_inventory_header_count_matches_entries` PASS | ✅ PASS | Header: 2757 tests; gate match PASS |
| F9 | `REQUIRE_HONESTY_MODE: marker` в CI | Да | ✅ PASS | В обоих CI workflow |

**Все проверки Branch F — PASS.** Ни одного skip-as-bug-masking, R4 enforcement через `require_service_healthy`, R5 negative-тесты на месте и проходят.

---

## 6. БРИФ E: Docs/Manifest/TRAP sync (D37–D45)

| # | Проверка | Ожидание | Результат | Evidence |
|---|----------|----------|----------|----------|
| E1 | `platform-deliver` в AGENTS.md | 0 | ✅ PASS | grep: 0 совпадений |
| E2 | `deploy-modules.sh.*ensure_context_repo` в AGENTS.md | 0 | ✅ PASS | grep: 0 совпадений |
| E3 | `shared/healthcheck_poll` в AGENTS.md | 0 | ✅ PASS | grep: 0 совпадений |
| E4 | `bootstrap/deploy/config_renderer` в AGENTS.md | 0 | ✅ PASS | grep: 0 совпадений |
| E5 | Navigation: ровно 3 «Канонический» в root AGENTS.md | ✅ PASS | AGENTS.md:278-280 — AGENTS.md, core/AGENTS.md, core/modules/AGENTS.md (ровно 3) |

**Все проверки Branch E — PASS.**

---

## 7. Сводка тестов

| Группа | Тесты | Результат |
|--------|-------|----------|
| Gate-тесты (все) | 40 | 39 PASS, 1 FAIL |
| Gate-тесты D (timeout/ssh/docker) | 8 | 8 PASS |
| Gate-тесты F (image_tag, volumes, inventory) | 9+4+5=18 | 18 PASS |
| Unit-тесты Branch C | 77 | 77 PASS |
| Unit-тест key_provisioner (llm) | 7 | 7 PASS |
| test_s3_client.py | — | ❌ ImportError (DRIFT-2) |
| test_inventory gate | 9 | 9 PASS |

---

## 8. Семантический вердикт

**Verdict: PARTIAL (HIGH severity)**

### Обоснование

1. **CRITICAL/HIGH (блокирует CI):**
   - **DRIFT-1:** `test_gate_cross_layer` FAIL — allowlist line number mismatch (51→58) для `agent_watchdog.py` timeouts import. Блокирует `make gate MODE=fast` на CI.
   - **DRIFT-2:** `tests/test_s3_client.py` — `ImportError` из-за неверного пути импорта (`from s3_client import ...` вместо `from core.internal.shared.s3_client import ...`).

2. **LOW (не блокирует, косметика):**
   - **NOTE-1:** `tests/unit/test_key_provisioner.py` не существует (вероятно, опечатка в спецификации — есть `test_llm_key_provisioner.py`).
   - **NOTE-2:** 4 избыточных `# LINT-EXEMPT` комментария в `agent_watchdog.py`.

### Таблица проверок (сводная)

| Проверка | Результат | Evidence |
|----------|----------|----------|
| Gate MODE=fast (все гейты) | ❌ 1 FAIL | test_gate_cross_layer: allowlist mismatch |
| Gate-тесты Branch D (timeout/ssh/docker) | ✅ 8/8 PASS | Новые скоупы покрыты, false-positive нет |
| Merge-конфликты (C+D в docker_orchestrator) | ✅ PASS | 0 конфликтных маркеров |
| `_reconcile_orphan_containers` удалена | ✅ PASS | 0 упоминаний |
| `DOCKER_CMD_TIMEOUT` используется | ✅ PASS | orchestrator + deploy_engine |
| `class DeployResult` удалён | ✅ PASS | 0 упоминаний |
| `shared/ssl_certs.py`, `s3_client.py`, `timeouts.py` | ✅ PASS | Все 3 существуют |
| `cert_orchestrator`/`s3_ssl_cache` → `shared/ssl_certs` | ✅ PASS | Импорты на месте |
| `agent_watchdog` → `shared/docker_compose` | ✅ PASS | Импорт на месте, AuditLogger удалён |
| Unit-тесты Branch C (77) | ✅ PASS | Все проходят |
| `test_s3_client.py` | ❌ ImportError | Неверный путь импорта |
| `ConnectTimeout=\d+` в CI workflows | ✅ PASS | 0 сырых таймаутов |
| `timeout=15` в core/internal | ✅ PASS | Все через timeouts.py |
| `STATUS_PAGE_PORT` в platform-infra.yaml | ✅ PASS | 8080 |
| `REQUIRE_HONESTY_MODE` в CI | ✅ PASS | В обоих workflow |
| R4: 0 skip при недоступности сервиса | ✅ PASS | Честный механизм |
| R5: negative-тесты (inventory, image_tag, volumes) | ✅ PASS | Все на месте, проходят |
| Inventory header count = entries | ✅ PASS | 2757 |
| AGENTS.md: `platform-deliver` | ✅ PASS | 0 упоминаний |
| AGENTS.md: `deploy-modules.sh.*ensure_context_repo` | ✅ PASS | 0 упоминаний |
| AGENTS.md: `shared/healthcheck_poll` | ✅ PASS | 0 упоминаний |
| AGENTS.md: `bootstrap/deploy/config_renderer` | ✅ PASS | 0 упоминаний |
| AGENTS.md: Navigation «Канонический» | ✅ PASS | Ровно 3 строки |

### Рекомендация по циклу фикса

Передать **Coder** (subagent_type: Coder) два исправления:

1. **DRIFT-1 (HIGH):** В `tests/test_cross_layer_imports.py:181` изменить номер строки `51` → `58` для записи `agent_watchdog.py` в `_CROSS_LAYER_ALLOWLIST`.

2. **DRIFT-2 (HIGH):** В `tests/test_s3_client.py:23` заменить `from s3_client import S3Client` на `from core.internal.shared.s3_client import S3Client`.

После исправлений — перепрогнать `python3 -m pytest tests/gates/ -x -q` и `python3 -m pytest tests/test_s3_client.py -v` для подтверждения зелёного гейта.
