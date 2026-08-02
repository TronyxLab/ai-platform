# 07-DevPlan — Бриф F: Test Cleanup & Coverage

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Приведение тестового набора в соответствие с R4 (Test Honesty): skip→fail для
                  отсутствующих зависимостей. Удаление дублей тестов. Заплатка дыр покрытия
                  (nginx_reload, build_channel). Исправление хрупких тестов (env mutation, bare
                  subprocess). Ужесточение unfalsifiable asserts.
DESCRIPTION:      11 задач (F1–F11). После миграций (D) и декомпозиций (E) — чистка тестов.
                  R4 compliance: тесты без Docker/Linux/acme.sh должны FAIL, не skip.
RATIONALE:        R4 нарушение: 6 тестов с skip вместо fail. Дубли тестов (DUP-3, DUP-4) —
                  мёртвый код. Дыры покрытия (HOLE-1, HOLE-2) — код без тестов.
                  Хрупкие тесты (FRAG-1..4) — источники ложных провалов.
ACCEPTANCE_CRITERIA:
  - AC-F-1: `make gate MODE=fast` зелёный
  - AC-F-2: `pytest tests/ -m "not requires_node"` — 0 regressions
  - AC-F-3: 0 skip по причине отсутствия зависимостей (R4 compliance)
  - AC-F-4: nginx_reload() покрыт unit-тестом (HOLE-1 закрыта)
  - AC-F-5: 0 дублей тестов (DUP-1, DUP-2, DUP-3 resolved)
IMPLEMENTS:       Бриф F из 01-Brief.md (волна 119) — Test Cleanup & Coverage.
IMPACTS:          tests/test_component_hermes.py, tests/test_tls_wildcard.py,
                  tests/gates/test_gate_make_contract.py, tests/_conftest/ldd.py,
                  tests/e2e/test_e2e_litellm.py, tests/e2e/test_e2e_grafana_api.py,
                  tests/unit/test_unit_provision_environment.py,
                  tests/unit/test_shared_docker_compose.py (NEW для HOLE-1),
                  tests/test_status_page.py, tests/test_smoke_platform.py,
                  tests/e2e/test_deploy_e2e.py, tests/test_platform_export_metrics.py.
REQUIRES:         Результаты аудита 5 (тесты R4-2..R4-7, DUP-3..DUP-4, HOLE-1..HOLE-2,
                  FRAG-1..FRAG-4, DEAD-1, R2-1..R2-2, LDD-1).
-->

# DevPlan F — Test Cleanup & Coverage

## $START_DEVPLAN

### Контекст

Волна 119, бриф F. Шестая волна — чистка тестов после миграций (D) и декомпозиций (E). R4 compliance (Test Honesty Rules): тесты, требующие Docker/acme.sh/requests/трафик, должны FAIL при отсутствии зависимости, а не skip. Удаление дублей, заплатка дыр покрытия, исправление хрупких тестов.

---

## $TASKS

### TASK-F1: Skip→fail (R4 compliance, 6 тестов)

| Поле | Значение |
|------|----------|
| **ID** | F1 |
| **Sev** | HIGH |
| **Сложность** | 4/10 |
| **Файлы** | 6 тестовых файлов (см. ниже) |
| **Зависимости** | A3 (honesty fail mode в CI) |
| **Риск** | MED — тесты могут реально падать в окружениях без зависимостей |

**Описание:**
6 тестов нарушают R4 (skip при отсутствии зависимости вместо fail):

| Sub | Файл | Проблема | Fix |
|-----|------|----------|-----|
| R4-2 | `test_component_hermes.py:889` | skip "requests not installed" | Убрать try/except ImportError → FAIL |
| R4-3 | `test_tls_wildcard.py:819` | skip "acme.sh not found" | `require_script_or_fail("acme.sh")` |
| R4-4 | `test_gate_make_contract.py:376` | docker skip | `require_docker_or_fail()` |
| R4-5 | `_conftest/ldd.py:221` | Timeout → всегда skip | В CI → fail после 1 retry |
| R4-6 | `test_e2e_litellm.py:72` | skip по отсутствию трафика | Генерировать тестовый трафик или FAIL |
| R4-7 | `test_e2e_grafana_api.py` | 401 → fail | FAIL с понятным сообщением |

**Шаги:**
1. R4-2: Удалить `try/except ImportError` для requests. Если requests нет — тест FAIL.
2. R4-3: Заменить skip на `require_script_or_fail("acme.sh")` из honesty-модуля.
3. R4-4: Заменить skip на `require_docker_or_fail()`.
4. R4-5: В ldd.py — если `REQUIRE_HONESTY_MODE=fail` → не skip на Timeout, а fail.
5. R4-6: В litellm-тесте — генерировать тестовый запрос к API, при недоступности → FAIL.
6. R4-7: В grafana-тесте — при 401 → FAIL (не skip).
7. R5 negative-тест: `test_honesty_skip_to_fail` — verify что тесты FAIL без зависимостей.

**Acceptance Criteria:**
- AC-F1.1: 0 skip по причине отсутствия Docker/acme.sh/requests/трафика
- AC-F1.2: `REQUIRE_HONESTY_MODE=fail pytest ...` — тесты FAIL (не skip)
- AC-F1.3: `REQUIRE_HONESTY_MODE=marker pytest ...` — тесты skip (локальная разработка)
- AC-F1.4: R5 negative-тест: отсутствие Docker → FAIL

---

### TASK-F2: Duplicate provision tests removal

| Поле | Значение |
|------|----------|
| **ID** | F2 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/unit/test_unit_provision_environment.py` |
| **Зависимости** | нет |
| **Риск** | LOW — удаление дублей |

**Описание:**
`test_unit_provision_environment.py:141,153,159` — побайтовые копии тестов из `test_gate_platform_env_schema.py`. Удалить 3 теста.

**Шаги:**
1. Удалить 3 дублирующих теста из `test_unit_provision_environment.py`.
2. Проверить, что канонические тесты в `test_gate_platform_env_schema.py` покрывают те же сценарии.
3. R5 negative-тест: `test_gate_platform_env_schema_coverage` — покрытие не уменьшилось.

**Acceptance Criteria:**
- AC-F2.1: 3 дублирующих теста удалены
- AC-F2.2: `test_gate_platform_env_schema.py` проходит (покрытие сохранено)
- AC-F2.3: R5 gate: покрытие provision environment ≥ до удаления

---

### TASK-F3: Delegate tests → patch verification

| Поле | Значение |
|------|----------|
| **ID** | F3 |
| **Sev** | AMBER |
| **Сложность** | 2/10 |
| **Файлы** | Несколько тестовых файлов с 1-строчными обёртками |
| **Зависимости** | нет |
| **Риск** | LOW — тесты не удаляются, только патч-проверка |

**Описание:**
1-строчные делегат-тесты (вызывают одну функцию без проверки логики). Свести к patch-проверке: verify что делегат вызывает правильный модуль с правильными аргументами.

**Acceptance Criteria:**
- AC-F3.1: Делегат-тесты проверяют вызов (patch-assert), не просто pass-through

---

### TASK-F4: HOLE-1: nginx_reload() 0 tests

| Поле | Значение |
|------|----------|
| **ID** | F4 |
| **Sev** | HIGH |
| **Сложность** | 5/10 |
| **Файлы** | `tests/unit/test_shared_docker_compose.py` (NEW), `shared/docker_compose.py` |
| **Зависимости** | нет |
| **Риск** | LOW — новый тест |

**Описание:**
`shared/docker_compose.py:694 nginx_reload()` — создан в 118 D6, НЕ покрыт тестами. Критическая функция деплоя. Unit-тест + тест `_step_nginx_reload`.

**Шаги:**
1. Создать `tests/unit/test_shared_docker_compose.py`.
2. Тест `test_nginx_reload_success` — успешный reload.
3. Тест `test_nginx_reload_failure_mode` — контейнер не существует → handled.
4. Тест `test_nginx_reload_timeout` — таймаут → handled.
5. R5 negative-тест: `test_nginx_reload_container_missing` — отсутствующий контейнер.

**Acceptance Criteria:**
- AC-F4.1: `test_shared_docker_compose.py` существует с ≥3 тестами
- AC-F4.2: `nginx_reload()` покрыт: success, failure, timeout
- AC-F4.3: R5 negative-тест: отсутствующий контейнер

---

### TASK-F5: HOLE-2: build_channel uncovered

| Поле | Значение |
|------|----------|
| **ID** | F5 |
| **Sev** | AMBER |
| **Сложность** | 3/10 |
| **Файлы** | `tests/unit/test_orchestrator_cli.py` (расширение), `deploy/orchestrator_cli.py` |
| **Зависимости** | нет |
| **Риск** | LOW — дополнительное покрытие |

**Описание:**
`orchestrator_cli.build_channel()` не покрыт тестами. 3 кейса каналов: ForcedCommandChannel, SCPChannel, LocalChannel.

**Шаги:**
1. Добавить в существующий `test_orchestrator_cli.py`:
   - `test_build_channel_forced_command` — ForcedCommandChannel.
   - `test_build_channel_scp` — SCPChannel с metadata.
   - `test_build_channel_local` — LocalChannel.

**Acceptance Criteria:**
- AC-F5.1: 3 теста build_channel в `test_orchestrator_cli.py`

---

### TASK-F6: FRAG-1: test_status_page env mutation fix

| Поле | Значение |
|------|----------|
| **ID** | F6 |
| **Sev** | HIGH |
| **Сложность** | 3/10 |
| **Файлы** | `tests/test_status_page.py` |
| **Зависимости** | нет |
| **Риск** | LOW — фикстура с monkeypatch |

**Описание:**
`test_status_page.py:245-260` — `_setup_app_env` мутирует 5 env vars без restore, `sys.path.insert`. Обернуть в фикстуру с `monkeypatch`.

**Шаги:**
1. Создать фикстуру `isolated_app_env` с `monkeypatch.setenv` для каждого из 5 env vars.
2. Убрать `sys.path.insert` — использовать пакетный импорт.
3. R5 negative-тест: `test_status_page_env_isolation` — verify что env vars восстановлены после теста.

**Acceptance Criteria:**
- AC-F6.1: `_setup_app_env` → фикстура с monkeypatch
- AC-F6.2: 0 `sys.path.insert` в тесте
- AC-F6.3: R5: env изоляция между тестами

---

### TASK-F7: FRAG-2: test_smoke_platform bare subprocess fix

| Поле | Значение |
|------|----------|
| **ID** | F7 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/test_smoke_platform.py` |
| **Зависимости** | нет |
| **Риск** | LOW — добавить require_docker_or_fail |

**Описание:**
`test_smoke_platform.py:95,215` — голый `subprocess.run("docker ...")` без `require_docker_or_fail`.

**Шаги:**
1. Добавить `require_docker_or_fail()` перед вызовами docker.
2. R5: при отсутствии Docker → FAIL (не skip, не subprocess.CalledProcessError).

**Acceptance Criteria:**
- AC-F7.1: `test_smoke_platform.py` использует `require_docker_or_fail` на L95,215

---

### TASK-F8: FRAG-3: ldd.py offline-skip docs

| Поле | Значение |
|------|----------|
| **ID** | F8 |
| **Sev** | AMBER |
| **Сложность** | 1/10 |
| **Файлы** | `tests/_conftest/ldd.py` |
| **Зависимости** | F1 (R4-5 fix в ldd.py) |
| **Риск** | LOW — документация |

**Описание:**
Поведение ldd.py при Timeout в офлайн-режиме не задокументировано. После F1 (R4-5) — задокументировать новое поведение.

**Acceptance Criteria:**
- AC-F8.1: ldd.py docstring описывает поведение при Timeout в CI vs локально

---

### TASK-F9: FRAG-4: test_deploy_e2e dead branch removal

| Поле | Значение |
|------|----------|
| **ID** | F9 |
| **Sev** | AMBER |
| **Сложность** | 1/10 |
| **Файлы** | `tests/e2e/test_deploy_e2e.py` |
| **Зависимости** | нет |
| **Риск** | LOW — удаление мёртвого кода |

**Описание:**
`test_deploy_e2e.py:197-198` — `if imp_level >= 9: pass` — мёртвая ветка. Убрать, добавить `found_log = True` + `assert found_log`.

**Шаги:**
1. Заменить `if imp_level >= 9: pass` на `if imp_level >= 9: found_log = True`.
2. Добавить `assert found_log, "No IMP:9 log found"` после цикла.

**Acceptance Criteria:**
- AC-F9.1: `test_deploy_e2e.py` НЕ содержит `pass` в LDD-цикле
- AC-F9.2: `assert found_log` присутствует

---

### TASK-F10: R2 unfalsifiable asserts tightening

| Поле | Значение |
|------|----------|
| **ID** | F10 |
| **Sev** | AMBER |
| **Сложность** | 1/10 |
| **Файлы** | `tests/e2e/test_deploy_e2e.py`, `tests/test_platform_export_metrics.py` |
| **Зависимости** | нет |
| **Риск** | LOW — ужесточение asserts |

**Описание:**
- `test_deploy_e2e.py:205` — `assert len(entries) >= 0` (всегда истина, len ≥ 0 гарантирован языком) → `assert len(entries) > 0`.
- `test_platform_export_metrics.py:1050` — `assert len(errors) >= 0` → `assert len(errors) == 0, f"Unexpected errors: {errors}"`.

**Acceptance Criteria:**
- AC-F10.1: `len(entries) >= 0` → `len(entries) > 0`
- AC-F10.2: `len(errors) >= 0` → `len(errors) == 0`

---

### TASK-F11: LDD-1 IMP:9 coverage (low priority, at-touch)

| Поле | Значение |
|------|----------|
| **ID** | F11 |
| **Sev** | LOW |
| **Сложность** | 2/10 |
| **Файлы** | 46 файлов без IMP:9 (крупнейшие при касании в других задачах) |
| **Зависимости** | E1-E9 (делать вместе с декомпозициями) |
| **Риск** | LOW — косметика |

**Описание:**
46 файлов без IMP:9 логов. Добавить при касании в других задачах (E1-E9). НЕ отдельная задача — делается попутно.

**Acceptance Criteria:**
- AC-F11.1: Новые модули (E1-E9) содержат IMP:9 логи при создании

---

## $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
```
coder Read .ai/plans/119-wave2-synthesis/07-DevPlan.md, implement Wave 1: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10
```

F11 делается попутно с E-задачами.

**Файловые пересечения:**
- F1: 6 разных файлов (test_component_hermes, test_tls_wildcard, test_gate_make_contract, ldd.py, test_e2e_litellm, test_e2e_grafana_api)
- F2: test_unit_provision_environment.py
- F3: delegate-тесты
- F4: test_shared_docker_compose.py (NEW)
- F5: test_orchestrator_cli.py
- F6: test_status_page.py
- F7: test_smoke_platform.py
- F8: ldd.py (пересекается с F1 R4-5) → делать ПОСЛЕ F1
- F9: test_deploy_e2e.py
- F10: test_deploy_e2e.py + test_platform_export_metrics.py (F9 и F10 пересекаются на test_deploy_e2e.py)

**Скорректированные группы:**
- Group 1: F1 (R4-5 ldd.py), F2, F3, F4, F5, F6, F7
- Group 2 (после Group 1): F8 (документирует изменения F1 в ldd.py), F9, F10

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_honesty.py` | `test_skip_to_fail_without_docker_negative` | R5: без Docker → FAIL (не skip) | honesty mode |
| `tests/gates/test_gate_platform_env_schema.py` | `test_provision_coverage_preserved` | R5: покрытие после удаления дублей | platform env schema |
| `tests/unit/test_shared_docker_compose.py` | `test_nginx_reload_success` | nginx reload успешный | docker_compose |
| `tests/unit/test_shared_docker_compose.py` | `test_nginx_reload_container_missing_negative` | R5: отсутствующий контейнер | docker_compose |
| `tests/unit/test_orchestrator_cli.py` | `test_build_channel_forced_command` | ForcedCommandChannel | orchestrator_cli |
| `tests/unit/test_status_page.py` | `test_env_isolation_negative` | R5: env восстановлены после теста | status_page |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-F-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-F-SKIP | 0 skip по причине отсутствия зависимостей (R4 compliance) |
| AC-F-DUP | 0 дублей тестов (DUP-1..3) |
| AC-F-HOLES | nginx_reload и build_channel покрыты тестами |
| AC-F-FRAG | 4 хрупких теста исправлены (env изоляция, require_docker, dead branch, asserts) |

---

## Next Steps

### Group 1
```
coder Read .ai/plans/119-wave2-synthesis/07-DevPlan.md, implement Group 1: F1, F2, F3, F4, F5, F6, F7
```
### Group 2
```
coder Read .ai/plans/119-wave2-synthesis/07-DevPlan.md, implement Group 2: F8, F9, F10
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
