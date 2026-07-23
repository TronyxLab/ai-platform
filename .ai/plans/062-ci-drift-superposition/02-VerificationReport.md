# GREP_SUMMARY: VerificationReport 062 CI-drift plan QA pre-implementation gate-coverage inline-python3 retry-gap
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Static Audit (DevPlan quality) → ◇ Drift Analysis (UF1-UF10 state) → ◇ Runtime (gates: 202 pass) → ⊕ Semantic Verdict

$START_VERIFICATION_REPORT
$ARTIFACT_CONTRACT
PURPOSE:               Пред-имплементационная верификация DevPlan 062 — проверка актуальности всех 10 unfixed findings (UF1-UF10) относительно текущего состояния кодовой базы.
DESCRIPTION:           Phase 1 (static audit DevPlan markup), Phase 2 (пофайловая проверка каждого UF на актуальность + cross-file drift), Phase 5 (runtime gate tests).
RATIONALE:             Пользователь сообщил что «подобные фиксы уже применены местами» и «гейт на локальные пути точно уже есть». Необходимо установить какие finding'и DevPlan'а всё ещё актуальны, а какие уже исправлены.
ACCEPTANCE_CRITERIA:   (1) Каждый из 10 UF проверен на актуальность в текущем коде, (2) G1/G2 gate-тесты запущены и проходят, (3) Выявлены противоречия между DevPlan и реальностью, (4) Semantic verdict с рекомендациями.
IMPLEMENTS:            QA pre-implementation gate для DevPlan 062
IMPACTS:               DevPlan 062 remediation plan — валидация или корректировка перед запуском Coder
REQUIRES:              git rev-parse HEAD (SHA: 119d259dd9642b1fffe95844f336a7a90824bbbd)
$END_ARTIFACT_CONTRACT

---

🔒 **Verified against SHA:** `119d259dd9642b1fffe95844f336a7a90824bbbd`
⚠️ Working tree: clean (no uncommitted changes)

---

## 1. Static Audit (Phase 1)

### DevPlan markup compliance

| Check | File | Status |
|-------|------|--------|
| GREP_SUMMARY | DevPlan.md:1 | ✅ PASS |
| STRUCTURE | DevPlan.md:2 | ✅ PASS |
| $ARTIFACT_CONTRACT (7 fields) | DevPlan.md:5-13 | ✅ PASS |
| $DOCUMENT_PLAN | DevPlan.md:17-31 | ✅ PASS |
| GOAL/USE_CASE defined | DevPlan.md:20-31 | ✅ PASS |
| $START_DEVPLAN / $END_DEVPLAN | DevPlan.md:4, 305 | ✅ PASS |
| Wave plan with risk assessment | DevPlan.md:272-276 | ✅ PASS |
| Verification commands | DevPlan.md:282-294 | ✅ PASS |

**Verdict:** DevPlan markup — без замечаний.

---

## 2. Drift Analysis (Phase 2) — UF1-UF10 State Verification

### 2.1 P0-P2 Fixed Bugs (базовые баги — уже исправлены)

Все 6 базовых багов (P0-1…P2-6) исправлены. Gate-тесты G1 и G2 предотвращают рецидив:

| Gate | File | Status | Evidence |
|------|------|--------|----------|
| G1 (hardcoded paths) | `tests/gates/test_gate_no_hardcoded_local_paths.py` | ✅ PASS | Runtime: gate test passes, scans `tests/` directory |
| G2 (checkout order) | `tests/gates/test_gate_workflow_checkout_order.py` | ✅ PASS | Runtime: gate test passes, scans `.github/workflows/*.yml` |

Оба gate зарегистрированы в `core/entrypoint-manifest.yaml` (lines 865, 1097).

**⚠️ WARNING — G1 coverage gap (UF9):** G1 сканирует **только** `tests/` директорию (line 53: `tests_dir = repo_root() / "tests"`). Файлы в `core/` НЕ проверяются. Это именно то что DevPlan называет UF9 — gate существует, но coverage неполный.

---

### 2.2 UF1 — Inline python3 в discover-modules action

| Поле | Значение |
|------|----------|
| DevPlan claim | `.github/actions/discover-modules/action.yml:36` содержит `python3 -c "import json,sys;..."` |
| **Status** | ✅ **CONFIRMED — всё ещё актуально** |
| Evidence | `action.yml:36` — `COUNT=$(python3 ... \| python3 -c "import json,sys; print(len(json.load(sys.stdin)))")` |
| Severity | **P1** — нарушение языковой политики AGENTS.md §Языковая политика п.3 |

**Дополнительное обнаружение — документационный дрифт:**

`action.yml:1` в GREP_SUMMARY заявляет `zero-inline-python3`, но строка 36 содержит inline python3. Это противоречие — файл был создан в StatusReport 046 T2 (CICD-01a) с宣称 that it "eliminates inline python3", но устранение неполное: inline python3 мигрирован из workflow-файлов (`platform-test.yml`, `nightly-gate.yml`) в composite action, но не удалён полностью.

`module_discovery.py` **не имеет** флага `--count` — только `--format json` и `--format lines`. Добавление `--count` флага (как предложено в DevPlan) — правильное решение.

**Cross-file check:** Больше нигде в `.github/` нет inline python3 (проверено grep по всем `*.yml` и `*.sh` в `.github/`).

---

### 2.3 UF2 — Hardcoded `/opt/platform` в compose_preflight.py

| Поле | Значение |
|------|----------|
| DevPlan claim | `compose_preflight.py:45` — `_MANIFEST_DEFAULT = "/opt/platform/core/secrets-manifest.yaml"` без env-var fallback |
| **Status** | ✅ **CONFIRMED — всё ещё актуально** |
| Evidence | `compose_preflight.py:45` — жёсткая строка, нет `os.environ.get("PLATFORM_ROOT", ...)` |
| Severity | **P2** — не блокирует, есть graceful degradation (manifest missing → WARN + exit 0, line 19 MODULE_CONTRACT) |

**Контекст:** В отличие от большинства других файлов в `core/internal/bootstrap/`, которые используют паттерн `os.environ.get("PLATFORM_ROOT", "/opt/platform")` (steps.py:908, state_machine.py:774/961/1214/1978, docker_orchestrator.py:176), `compose_preflight.py` — единственный где `/opt/platform` захардкожен без env-var. Остальные хардкоды `/opt/platform` в `core/` — это argparse defaults (переопределяемые через CLI), а не жёсткие константы.

**Caveat:** `/opt/platform` — канонический путь на VPS (инвариант 4: «Артефакты: /opt/platform/core/»). Жёсткий путь корректно работает на production-серверах, но не на dev-машинах с нестандартным PLATFORM_ROOT.

---

### 2.4 UF3-UF8 — HTTP retry gap в тестах

| UF | File | `requests.get/post` calls | Retry? | Exception handling | Status |
|----|------|---------------------------|--------|--------------------|--------|
| **UF3** | `test_smoke_langfuse.py` | 5 вызовов (lines 62, 77, 96, 124, 126) | ❌ Нет | ❌ Нет | ✅ CONFIRMED |
| **UF4** | `test_smoke_monitoring.py` | 3 вызова (lines 348, 385, 423) | ❌ Нет | ✅ `RequestException → pytest.fail` | ✅ CONFIRMED |
| **UF5** | `test_platform_endpoints.py` | 5+ вызовов (lines 89, 135, 161, 197, 269) | ❌ Нет | ✅ `_handle_e2e_error` (fail/skip) | ✅ CONFIRMED |
| **UF6** | `test_smoke_hermes.py` | 3 вызова (lines 91, 141, 196) | ❌ Нет | ✅ `_handle_e2e_error` (fail/skip) | ✅ CONFIRMED |
| **UF7** | `test_e2e_health.py` | 1 вызов (line 97) | ❌ Нет | ✅ `_handle_e2e_error` (fail/skip) | ✅ CONFIRMED |
| **UF8** | `test_e2e_langfuse.py` | 1 вызов (line 50) | ❌ Нет | ✅ `_handle_e2e_error` (fail/skip) | ✅ CONFIRMED |

Все 6 файлов: **retry-логика отсутствует**. Важное уточнение к DevPlan:

- **UF4 (test_smoke_monitoring.py):** обработка исключений ЕСТЬ (`except RequestException: pytest.fail()`), но это HARD FAIL без retry — тест падает при первой же ошибке соединения. Это хуже чем UF5/UF6 где `_handle_e2e_error` хотя бы делает skip для timeout.
- **UF5/UF6/UF7/UF8:** используют `_handle_e2e_error()` из `tests/_conftest/ldd.py:127` — это стандартизированный обработчик который делает `pytest.fail` (SSLError/ConnectionError/ProxyError) или `pytest.skip` (Timeout). Retry НЕТ.
- **UF3 (test_smoke_langfuse.py):** самая опасная ситуация — ни retry, ни обработки исключений. Любой `ConnectionError` → необработанное исключение → тест падает с трейсом.

**Сравнение с уже исправленным:** `test_smoke_litellm.py:89-113` — эталонный паттерн retry (3 попытки, exponential backoff 1s/2s/4s, TRAP[BUG] документирует root cause). Именно этот паттерн DevPlan предлагает распространить на UF3-UF8.

---

### 2.5 UF9 — G1 gate coverage gap (не сканирует core/)

| Поле | Значение |
|------|----------|
| DevPlan claim | G1 сканирует только `tests/` — хардкоды в `core/` не детектятся |
| **Status** | ✅ **CONFIRMED — всё ещё актуально** |
| Evidence | `test_gate_no_hardcoded_local_paths.py:53` — `tests_dir = repo_root() / "tests"` |
| Severity | **P2** |

**Что G1 НЕ ловит в core/:**
- `compose_preflight.py:45` — `_MANIFEST_DEFAULT = "/opt/platform/core/secrets-manifest.yaml"` (UF2)
- Все остальные `/opt/platform` в core/ используют `os.environ.get("PLATFORM_ROOT", "/opt/platform")` — корректный паттерн

**Что G1 ловит в tests/ (и правильно):**
- Паттерн `r'["\'](/Users/[\w.-]+/\|/home/[\w.-]+/(?!runner/work/)[\w.-]+/)'` — macOS/Linux домашние директории
- НЕ `/opt/platform`, `/tmp/`, `/var/lib/platform`, `/etc/`, `/usr/` — это системные пути, не user-specific

**Рекомендация к UF9 fix:** При расширении G1 на `core/`, нужно добавить отдельный паттерн для хардкода `/opt/platform` БЕЗ env-var fallback (в дополнение к существующему паттерну для домашних директорий). Текущий паттерн G1 для `/Users/` и `/home/` не сработает на `/opt/platform`.

---

### 2.6 UF10 — Отсутствует gate для HTTP retry policy

| Поле | Значение |
|------|----------|
| DevPlan claim | Нет gate-теста проверяющего что `requests.get/post` обёрнуты в retry |
| **Status** | ✅ **CONFIRMED — gate отсутствует** |
| Evidence | `grep -r "http_retry\|retry_policy\|no_retry" tests/gates/` → 0 результатов |
| Severity | **P1** — prevention gap |

**Проверка:** 56 gate-файлов в `tests/gates/`, ни один не проверяет HTTP retry policy. Ближайший аналог — `test_gate_thin_wrapper.py` (проверяет что shell-скрипты тонкие), но для Python-тестов аналога нет.

---

## 3. Runtime Validation (Phase 5)

### Gate tests: **202 passed, 15 skipped** ✅

```
=============== 202 passed, 15 skipped, 26 deselected in 26.06s ================
```

Все skip имеют легитимные причины:
- `test_gate_makefile_targets.py` — `make -n` не полностью dry на GNU Make
- `test_gate_module_hooks.py` ×11 — модули без hooks (не gate failure)
- `test_gate_project_context.py` — нет `projects/` в dev-окружении
- `test_gate_project_env.py` — нет `projects/` в dev-окружении
- `test_gate_pytest_markers.py` — extra markers (non-critical)

**Skip rate:** 15/217 = 6.9% — ниже порога 15%, OK.

### G1/G2 specific:
- `test_no_hardcoded_local_paths_in_tests` — **PASSED** (0.20s)
- `test_local_actions_after_checkout` — **PASSED** (0.20s)

### LDD trace analysis:
- IMP:9 логи присутствуют в обоих gate-тестах
- Anti-illusion verdict: **PASS** (IMP:9 business-logic assertions подтверждены)

---

## 4. Summary Matrix

| # | Severity | Description | DevPlan Status | Current State | Action |
|---|----------|-------------|----------------|---------------|--------|
| UF1 | **P1** | Inline python3 в action.yml | Не исправлено | ✅ Актуально | Добавить `--count` в module_discovery.py |
| UF2 | **P2** | Hardcoded path в compose_preflight.py | Не исправлено | ✅ Актуально | Добавить `PLATFORM_ROOT` env fallback |
| UF3 | **P2** | langfuse smoke без retry | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF4 | **P2** | monitoring smoke без retry (hard fail) | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF5 | **P2** | platform_endpoints без retry | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF6 | **P2** | hermes smoke без retry | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF7 | **P2** | e2e_health без retry | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF8 | **P2** | e2e_langfuse без retry | Не исправлено | ✅ Актуально | Добавить retry-цикл |
| UF9 | **P2** | G1 gate coverage gap (core/) | Не исправлено | ✅ Актуально | Расширить G1 на core/ |
| UF10 | **P1** | Нет http_retry gate | Не исправлено | ✅ Актуально | Создать новый gate |

**Итого:** 10/10 findings актуальны. **Ни один UF не был исправлен с момента написания DevPlan.**

---

## 5. Additional Findings

### F1 — action.yml GREP_SUMMARY дрифт

`[INFO]` `.github/actions/discover-modules/action.yml:1` — GREP_SUMMARY утверждает `zero-inline-python3`, но строка 36 содержит `python3 -c "import json,sys;..."`. Файл был создан StatusReport 046 T2 с宣称 об устранении inline python3, но устранение неполное.

### F2 — UF4 severity занижена в DevPlan

`[WARNING]` `test_smoke_monitoring.py` использует паттерн `except RequestException: pytest.fail()` — HARD FAIL без retry. Это жёстче чем UF5-UF8 где `_handle_e2e_error` хотя бы делает skip для timeout. Рекомендуется поднять UF4 до P1 в remediation plan.

### F3 — UF3 severity занижена в DevPlan

`[WARNING]` `test_smoke_langfuse.py` — 5 вызовов `requests.get/post` вообще без какой-либо обработки исключений (ни retry, ни try/except). Любой transient network error → необработанное исключение → crash. Рекомендуется поднять UF3 до P1.

---

## 6. Semantic Verdict

```
VERDICT: STABLE (with 10 unfixed delta)
```

**Обоснование:**
- Все 6 P0-P2 базовых багов исправлены ✅
- G1/G2 gate-тесты предотвращают рецидив ✅
- Gate suite: 202/202 pass ✅
- Все 10 UF findings **подтверждены как актуальные** — DevPlan корректен, remediation plan не требует изменений
- Критического дрифта или BROKEN-инвариантов не обнаружено
- Рекомендации по severity апгрейду UF3/UF4 — опциональные, не блокируют реализацию

**Рекомендация:** DevPlan готов к реализации. Wave 1 (UF1 + UF10) и Wave 2 (UF2-UF9) могут запускаться как описано.

**Pre-implementation check:**
```bash
make fix-gate && git add -u && make gate MODE=fast
# → должно быть зелёным (202 pass, 15 skip)
```

$END_VERIFICATION_REPORT
