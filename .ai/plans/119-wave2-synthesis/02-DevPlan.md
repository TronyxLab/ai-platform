# 02-DevPlan — Бриф A: Gates & Protection

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Закрыть loopholes в gate-системе: зомби-гейты вне tests/gates/, слепое пятно timeout-гейта,
                  честный fail-mode для тестов без Docker, отсутствующий networks-parity гейт.
DESCRIPTION:      4 задачи (A1–A4). Все — чистые gate-тесты, без изменения продакшн-кода.
                  Самый дешёвый и безопасный бриф волны 119. Закрывает дыры обнаружения перед
                  остальными волнами.
RATIONALE:        Зомби-гейты (26 тестов) не исполняются ни одним таргетом — loophole обнаружения.
                  Timeout-гейт имеет слепое пятно (3 файла вне scope). Honesty mode всё ещё skip
                  (маркер), а не fail — R4 нарушение. Networks parity отсутствует при наличии
                  volumes_sot — асимметрия покрытия.
ACCEPTANCE_CRITERIA:
  - AC-A-1: `make gate MODE=fast` зелёный (включая новый зомби-гейт и networks-parity)
  - AC-A-2: 0 @pytest.mark.gate вне tests/gates/ (зомби-гейт enforce)
  - AC-A-3: CI workflow содержит `REQUIRE_HONESTY_MODE=fail`
  - AC-A-4: `pytest tests/gates/test_gate_networks_sot.py` проходит
IMPLEMENTS:       Бриф A из 01-Brief.md (волна 119) — Gates & Protection.
IMPACTS:          tests/gates/ (новые + модифицированные gate-тесты), tests/test_cross_layer_imports.py,
                  tests/test_smoke_test_isolation.py, tests/test_template_syntax_gate.py,
                  tests/unit/test_bootstrap_no_duplicate_steps.py, tests/test_no_backward_compat_markers.py,
                  .github/workflows/platform-gate-fast.yml, .github/workflows/platform-test.yml.
REQUIRES:         Результаты аудита 6 (манифест) и аудита 4 (SoT-дубли) — зомби-гейты и таймауты.
-->

# DevPlan A — Gates & Protection

## $START_DEVPLAN

### Контекст

Волна 119, бриф A. Первая волна — самая дешёвая и безопасная. Только gate-тесты, без изменения продакшн-кода. Закрывает 4 обнаруженных loopholes в CI/gate-системе.

---

## $TASKS

### TASK-A1: Анти-drift gate «ни одного @pytest.mark.gate вне tests/gates/»

| Поле | Значение |
|------|----------|
| **ID** | A1 |
| **Sev** | HIGH |
| **Сложность** | 4/10 |
| **Файлы** | `tests/gates/test_gate_marker_location.py` (NEW), 5 файлов с зомби-маркерами |
| **Зависимости** | нет |
| **Риск** | LOW — чистый gate-тест, не трогает продакшн |

**Описание:**
26 тестов имеют `@pytest.mark.gate` вне `tests/gates/`:
- `test_cross_layer_imports.py` — 12 маркеров
- `test_smoke_test_isolation.py` — 6 маркеров
- `test_template_syntax_gate.py` — 3 маркера
- `test_bootstrap_no_duplicate_steps.py` — 4 маркера
- `test_no_backward_compat_markers.py` — 1 маркер

**Шаги:**
1. Создать `tests/gates/test_gate_marker_location.py` — сканирует ВСЕ .py файлы в tests/ на наличие `@pytest.mark.gate`, проверяет что каждый находится в `tests/gates/`. Allowlist: legacy-юнит-файлы выше (если решено сохранить маркеры) ИЛИ перенести gate-функции в tests/gates/.
2. Для тестов, которые реально gate-тесты → перенести функции в tests/gates/ (новый файл или существующий).
3. Для тестов, которые НЕ gate-тесты → снять `@pytest.mark.gate` (оставить как unit-тесты).
4. После — `make generate-entrypoint-manifest` для обновления реестра.
5. R5 negative-тест: `test_gate_marker_location_enforcement` — файл с @pytest.mark.gate вне tests/gates/ → gate RED.

**Acceptance Criteria:**
- AC-A1.1: `test_gate_marker_location.py` проходит (0 зомби-маркеров)
- AC-A1.2: Все 26 тестов либо перенесены в tests/gates/, либо маркер снят
- AC-A1.3: `make check-manifests` зелёный после `make generate-entrypoint-manifest`
- AC-A1.4: R5 negative-тест: искусственный @pytest.mark.gate вне tests/gates/ → обнаружен

---

### TASK-A2: Расширение scope timeout-гейта

| Поле | Значение |
|------|----------|
| **ID** | A2 |
| **Sev** | HIGH |
| **Сложность** | 3/10 |
| **Файлы** | `tests/gates/test_gate_timeout_literals.py`, `docker_registry_auth.py`, `reconciler_projects.py`, `circuit_breaker.py` |
| **Зависимости** | нет |
| **Риск** | LOW — расширение allowlist гейта |

**Описание:**
`_DOMAIN_FILES` в `test_gate_timeout_literals.py` не включает:
- `docker_registry_auth.py:278` — `timeout=10` (→DOCKER_CMD_TIMEOUT), `:52` локальная `DOCKER_RESTART_TIMEOUT=60`, `:273` `sleep(5)×6`
- `reconciler_projects.py:196` — `timeout=30` (неверное значение, канон `IMAGE_CHECK_TIMEOUT=60`)
- `circuit_breaker.py:165` — `timeout=10`

**Шаги:**
1. Расширить `_DOMAIN_FILES` в `test_gate_timeout_literals.py` — добавить 3 файла.
2. В `docker_registry_auth.py`: импортировать `DOCKER_CMD_TIMEOUT`/`DOCKER_RESTART_TIMEOUT` из `shared/timeouts`, заменить литералы.
3. В `reconciler_projects.py`: импортировать `IMAGE_CHECK_TIMEOUT`, заменить `timeout=30` → `timeout=IMAGE_CHECK_TIMEOUT`.
4. В `circuit_breaker.py`: импортировать соответствующий таймаут из `shared/timeouts`.
5. R5 negative-тест: `test_timeout_literal_detected_in_new_domain` — литерал в новом файле детектится.

**Acceptance Criteria:**
- AC-A2.1: `test_gate_timeout_literals.py` проходит (0 неканоничных таймаутов в расширенном scope)
- AC-A2.2: `docker_registry_auth.py` использует импорты из shared/timeouts (grep подтверждает)
- AC-A2.3: `reconciler_projects.py:196 timeout=IMAGE_CHECK_TIMEOUT` (60, не 30)
- AC-A2.4: R5 negative-тест проходит

---

### TASK-A3: Honesty fail mode в CI

| Поле | Значение |
|------|----------|
| **ID** | A3 |
| **Sev** | HIGH |
| **Сложность** | 2/10 |
| **Файлы** | `.github/workflows/platform-gate-fast.yml`, `.github/workflows/platform-test.yml` |
| **Зависимости** | нет |
| **Риск** | MED — может сломать CI, если Docker недоступен в раннере |

**Описание:**
`REQUIRE_HONESTY_MODE` по умолчанию `marker` (skip при отсутствии Docker). В CI раннерах Docker ЕСТЬ — переключить на `fail`.

**Шаги:**
1. В `platform-gate-fast.yml:44` (или где определён env) — добавить/изменить `REQUIRE_HONESTY_MODE: fail`.
2. В `platform-test.yml:74` — аналогично.
3. Проверить, что все CI-раннеры имеют Docker (по документации workflow).
4. Для локальной dev-машины — оставить marker (через `.env` или отсутствие переменной).
5. R5 negative-тест: `test_honesty_mode_fail_on_missing_docker` — verify что без Docker тест FAIL (не skip).

**Acceptance Criteria:**
- AC-A3.1: `REQUIRE_HONESTY_MODE=fail` в обоих CI workflow
- AC-A3.2: Локально (без переменной) — поведение marker (skip) сохраняется
- AC-A3.3: R5 negative-тест: отсутствие Docker → FAIL (не skip)

---

### TASK-A4: Networks parity gate

| Поле | Значение |
|------|----------|
| **ID** | A4 |
| **Sev** | MED |
| **Сложность** | 4/10 |
| **Файлы** | `tests/gates/test_gate_networks_sot.py` (NEW), `platform-infra.yaml` |
| **Зависимости** | нет |
| **Риск** | LOW — новый gate по образцу существующего volumes_sot |

**Описание:**
Gates имеют `test_gate_volumes_sot.py` для проверки имён volume'ов против platform-infra.yaml, но аналогичного гейта для имён сетей нет. Имена сетей определены в 10+ docker-compose файлах и должны совпадать с platform-infra.yaml.

**Шаги:**
1. Создать `tests/gates/test_gate_networks_sot.py` по образцу `test_gate_volumes_sot.py`:
   - Читать platform-infra.yaml → список канонических имён сетей.
   - Сканировать все `docker-compose.base.yml` + `docker-compose.yml` → извлекать имена сетей.
   - Проверять, что каждое имя сети либо в каноне, либо в allowlist.
2. Allowlist: внутренние сети (типа `backup-net`, `default` — compose автоматические).
3. R5 negative-тест: неканоничное имя сети → gate RED.
4. `make generate-entrypoint-manifest` для регистрации гейта.

**Acceptance Criteria:**
- AC-A4.1: `test_gate_networks_sot.py` проходит (все имена сетей в каноне + allowlist)
- AC-A4.2: Gate зарегистрирован в entrypoint-manifest.yaml
- AC-A4.3: R5 negative-тест: неканоничное имя сети → обнаружено
- AC-A4.4: `make check-manifests` зелёный

---

## $PARALLEL_GROUPS

### Wave 1 (все задачи независимы, нет общих файлов)

```
coder Read .ai/plans/119-wave2-synthesis/02-DevPlan.md, implement Wave 1: A1, A2, A3, A4
```

Все 4 задачи не пересекаются по файлам — можно выполнять параллельно.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_marker_location.py` | `test_no_gate_markers_outside_gates_dir` | Все @pytest.mark.gate в tests/gates/ | gate marker location |
| `tests/gates/test_gate_marker_location.py` | `test_gate_marker_outside_detected_negative` | R5: маркер вне tests/gates/ → обнаружен | gate marker location |
| `tests/gates/test_gate_timeout_literals.py` | `test_timeout_in_docker_registry_auth` | docker_registry_auth использует shared/timeouts | timeout gate |
| `tests/gates/test_gate_timeout_literals.py` | `test_timeout_in_reconciler_projects` | reconciler_projects timeout=IMAGE_CHECK_TIMEOUT | timeout gate |
| `tests/gates/test_gate_timeout_literals.py` | `test_timeout_literal_detected_negative` | R5: литерал в новом файле → обнаружен | timeout gate |
| `tests/gates/test_gate_honesty_mode.py` | `test_honesty_fail_on_missing_docker` | R5: без Docker → FAIL (не skip) | honesty mode |
| `tests/gates/test_gate_networks_sot.py` | `test_all_network_names_in_canon` | Все имена сетей в platform-infra.yaml | networks parity |
| `tests/gates/test_gate_networks_sot.py` | `test_non_canonical_network_detected_negative` | R5: неканоничное имя сети → обнаружено | networks parity |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-A-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-A-R5 | Каждая задача имеет R5 negative-тест |
| AC-A-ZERO | 0 изменений продакшн-кода (только tests/ + CI workflow) |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/02-DevPlan.md, implement Wave 1: A1, A2, A3, A4
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
