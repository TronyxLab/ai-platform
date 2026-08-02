# 03-DevPlan — Бриф B: SoT Unification

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Унификация разрозненных Source-of-Truth: парсер ai-platform.yaml (9 файлов с yaml.safe_load),
                  пути /opt/* (27+ call sites с литералами), subprocess-канон (2 реализации run_subprocess),
                  таймауты (5+ источников), healthcheck-порты.
DESCRIPTION:      8 задач (B1–B8). Фундамент для декомпозиций (E) и shell→Python миграций (D).
                  Каждая задача сводит N копий к одному каноническому модулю.
RATIONALE:        Без унификации SoT декомпозиции и миграции будут плодить новые дубли. Пути,
                  таймауты и парсеры — наиболее многочисленные дубли (27+ call sites).
ACCEPTANCE_CRITERIA:
  - AC-B-1: `make gate MODE=fast` зелёный после каждой задачи
  - AC-B-2: 0 новых литералов путей /opt/* (гейт T2/T3 enforce)
  - AC-B-3: `shared/subprocess_io` — единственная реализация run_subprocess в core/
  - AC-B-4: `shared/project_yaml` — единственный парсер ai-platform.yaml (9→1)
IMPLEMENTS:       Бриф B из 01-Brief.md (волна 119) — SoT Unification.
IMPACTS:          core/internal/shared/project_yaml.py, core/internal/shared/deploy_paths.py,
                  core/internal/shared/subprocess_io.py, core/internal/shared/timeouts.py,
                  core/internal/bootstrap/lifecycle/helpers/subprocess_io.py (удаление копии),
                  core/internal/bootstrap/vhost_renderer.py, core/internal/bootstrap/vhost_configurator.py,
                  core/internal/scaffold/conflict_checks.py, core/internal/monitoring_config_renderer.py,
                  core/internal/deploy/ (deploy_engine, payload_deliverer, deploy_history, orchestrator,
                  orchestrator_cli, context_deployer, channels, project_collector),
                  core/internal/bootstrap/converge/ (infra, runtime, volumes),
                  core/internal/bootstrap/ (cert_orchestrator, docker_registry_auth, issue-cert.sh),
                  core/internal/healthcheck/healthcheck_poller.py.
REQUIRES:         Результаты аудита 2 (Python-монолиты S3) и аудита 4 (SoT-дубли T2-T6, K2).
-->

# DevPlan B — SoT Unification

## $START_DEVPLAN

### Контекст

Волна 119, бриф B. Вторая волна — фундамент для декомпозиций и миграций. Задачи сводят множественные копии конфигурации/путей/парсеров к единому каноническому модулю. Без этого брифа задачи E (монолиты) и D (shell→Python) будут плодить новые дубли.

---

## $TASKS

### TASK-B1: Единый парсер ai-platform.yaml (S3)

| Поле | Значение |
|------|----------|
| **ID** | B1 |
| **Sev** | HIGH |
| **Сложность** | 6/10 |
| **Файлы** | `shared/project_yaml.py` (расширение API), `vhost_renderer.py`, `vhost_configurator.py`, `conflict_checks.py`, `monitoring_config_renderer.py` + ~5 других c yaml.safe_load |
| **Зависимости** | нет |
| **Риск** | MED — 9 файлов меняют способ чтения ai-platform.yaml |

**Описание:**
`vhost_renderer.py:127` имеет собственный `read_project_yaml` (expose/needs/domain/target_node) НЕ делегирующий `shared/project_yaml.py` (создан в 118 E11). Расширить API project_yaml для покрытия всех полей (expose, needs, domain, target_node), мигрировать 9 файлов на единый reader.

**Шаги:**
1. Расширить `shared/project_yaml.py`: добавить `get_expose_config()`, `get_needs()`, `get_domain()`, `get_target_node()` — типизированные аксессоры.
2. Мигрировать `vhost_renderer.py` — заменить `read_project_yaml` на вызовы shared/project_yaml.
3. Мигрировать `vhost_configurator.py` — аналогично.
4. Мигрировать `conflict_checks.py` — аналогично.
5. Мигрировать `monitoring_config_renderer.py` — аналогично.
6. Найти grep-ом ВСЕ `yaml.safe_load.*ai-platform.yaml` → мигрировать остальные 4-5 файлов.
7. R5 negative-тест: `test_project_yaml_missing_field` — отсутствующее поле → ConfigValidationError.

**Acceptance Criteria:**
- AC-B1.1: `grep -rn "yaml.safe_load.*ai-platform" core/internal/ | grep -v project_yaml.py` → 0 результатов (кроме самого project_yaml.py)
- AC-B1.2: `vhost_renderer.py` НЕ содержит собственный `read_project_yaml`
- AC-B1.3: Все 9 мигрированных файлов проходят существующие тесты
- AC-B1.4: R5 negative-тест: отсутствующее поле → ошибка, не None

---

### TASK-B2: `/opt/projects` literals → deploy_paths

| Поле | Значение |
|------|----------|
| **ID** | B2 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `shared/deploy_paths.py`, ~12 файлов с `/opt/projects` литералами |
| **Зависимости** | нет |
| **Риск** | LOW — замена литералов на импорт |

**Описание:**
12+ мест с хардкодом `/opt/projects` вне `deploy_paths`: deploy_engine, payload_deliverer, deploy_history, orchestrator, orchestrator_cli, context_deployer, channels, project_collector, generate_catalog, converge/infra, users, project_registry. Часть используют `DEFAULT_PROJECTS_ROOT` под чужим именем.

**Шаги:**
1. Добавить `projects_base()` и `DEFAULT_PROJECTS_BASE` в `shared/deploy_paths.py`.
2. Заменить все литералы `/opt/projects` на импорт из deploy_paths.
3. Удалить локальные `DEFAULT_PROJECTS_ROOT`/`PROJECTS_ROOT` константы.
4. R5 negative-тест: литерал `/opt/projects` в новом файле → gate RED.

**Acceptance Criteria:**
- AC-B2.1: `grep -rn '"/opt/projects"' core/internal/ | grep -v deploy_paths.py | grep -v test_` → 0
- AC-B2.2: Все импорты консистентны (projects_base() / DEFAULT_PROJECTS_BASE)
- AC-B2.3: R5 negative-тест: новый хардкод → обнаружен

---

### TASK-B3: `/opt/platform`, `/opt/node-configs` → deploy_paths

| Поле | Значение |
|------|----------|
| **ID** | B3 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `shared/deploy_paths.py`, ~15 файлов с литералами |
| **Зависимости** | нет |
| **Риск** | LOW — аналогично B2 |

**Описание:**
~15 call sites с литералами `/opt/platform`, `/opt/node-configs` вне канона `deploy_paths`.

**Шаги:**
1. Добавить `platform_base()` и `node_configs_base()` в `shared/deploy_paths.py`.
2. Заменить все литералы.
3. R5 negative-тест: литерал в новом файле → обнаружен.

**Acceptance Criteria:**
- AC-B3.1: `grep -rn '"/opt/platform"\|"/opt/node-configs"' core/internal/ | grep -v deploy_paths.py | grep -v test_` → 0
- AC-B3.2: R5 negative-тест проходит

---

### TASK-B4: subprocess_io canonical → удаление копии

| Поле | Значение |
|------|----------|
| **ID** | B4 |
| **Sev** | MED |
| **Сложность** | 5/10 |
| **Файлы** | `shared/subprocess_io.py` (канон), `lifecycle/helpers/subprocess_io.py` (копия, удалить), ~5 bootstrap-фаз |
| **Зависимости** | нет |
| **Риск** | MED — разные семантики таймаутов/ошибок |

**Описание:**
`lifecycle/helpers/subprocess_io.py` — второй канон `run_subprocess` (default timeout=120 литерал, не из shared/timeouts). Мигрировать bootstrap-фазы на `shared/subprocess_io.py` (создан в 118 C10), удалить копию.

**Шаги:**
1. Сравнить API: `shared/subprocess_io.run_subprocess()` vs `lifecycle/helpers/subprocess_io.run_subprocess()`.
2. Для несовместимых вызовов — расширить `shared/subprocess_io` параметрами (graceful_rc, non_fatal_codes).
3. Мигрировать bootstrap-фазы (phases.py, helpers/*.py) на shared/subprocess_io.
4. Удалить `lifecycle/helpers/subprocess_io.py`.
5. R5 negative-тест: `test_subprocess_io_single_canon` — verify что только один run_subprocess.

**Acceptance Criteria:**
- AC-B4.1: `lifecycle/helpers/subprocess_io.py` удалён
- AC-B4.2: Все bootstrap-фазы импортируют из `shared/subprocess_io`
- AC-B4.3: Все существующие тесты bootstrap проходят
- AC-B4.4: R5 negative-тест: только один run_subprocess в кодовой базе

---

### TASK-B5: OpenSSL timeout → shared/timeouts

| Поле | Значение |
|------|----------|
| **ID** | B5 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `shared/timeouts.py`, `cert_orchestrator.py`, `nginx_harness.py` |
| **Зависимости** | нет |
| **Риск** | LOW — замена литералов |

**Описание:**
`cert_orchestrator.py:452,474` и `nginx_harness.py:159` используют `timeout=30` для openssl вместо канона `DEFAULT_OPENSSL_TIMEOUT=10`.

**Шаги:**
1. Импортировать `DEFAULT_OPENSSL_TIMEOUT` из `shared/timeouts` в оба файла.
2. Заменить `timeout=30` → `timeout=DEFAULT_OPENSSL_TIMEOUT`.
3. Проверить, что 10 секунд достаточно для openssl x509 операций (так и есть — канон).
4. R5 negative-тест: openssl timeout литерал → обнаружен gate.

**Acceptance Criteria:**
- AC-B5.1: `cert_orchestrator.py` использует `DEFAULT_OPENSSL_TIMEOUT`
- AC-B5.2: `nginx_harness.py` использует `DEFAULT_OPENSSL_TIMEOUT`
- AC-B5.3: `make gate MODE=fast` зелёный (timeout gate не жалуется)

---

### TASK-B6: PROJECT_HEALTHCHECK_PORTS расширение

| Поле | Значение |
|------|----------|
| **ID** | B6 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `shared/timeouts.py` (или `shared/deploy_paths.py`), `platform-infra.yaml` |
| **Зависимости** | нет |
| **Риск** | LOW — расширение списка портов |

**Описание:**
`PROJECT_HEALTHCHECK_PORTS=[8080,8000]` не пересекается с реально используемыми портами проектов (4000/3000/9000). Расширить список или генерировать из platform-infra.yaml.

**Шаги:**
1. Найти реальные порты проектов в platform-infra.yaml или compose-шаблонах.
2. Расширить константу: `[3000, 4000, 8000, 8080, 9000]` (покрывает Node/React/Flask/Django/Go).
3. ИЛИ: генерировать из platform-infra.yaml → динамический список.
4. R5 negative-тест: порт вне списка → предупреждение (AMBER, не RED).

**Acceptance Criteria:**
- AC-B6.1: `PROJECT_HEALTHCHECK_PORTS` включает 3000, 4000, 8000, 8080, 9000
- AC-B6.2: healthcheck успешно находит проект на порту 3000

---

### TASK-B7: Converge/infra timeouts → shared/timeouts

| Поле | Значение |
|------|----------|
| **ID** | B7 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `shared/timeouts.py`, `converge/infra.py` |
| **Зависимости** | нет |
| **Риск** | LOW — замена литералов |

**Описание:**
`converge/infra.py` — локальные `DOCKER_TIMEOUT=30`, `FILE_OP_TIMEOUT=15`. Заменить на импорт из `shared/timeouts`.

**Шаги:**
1. Импортировать соответствующие таймауты из `shared/timeouts`.
2. Удалить локальные константы.
3. R5: timeout-гейт проверяет converge/infra.

**Acceptance Criteria:**
- AC-B7.1: `converge/infra.py` импортирует таймауты из shared/timeouts
- AC-B7.2: Локальные DOCKER_TIMEOUT/FILE_OP_TIMEOUT удалены

---

### TASK-B8: vps_readiness SSH_TIMEOUT → SSH_CONNECT_TIMEOUT

| Поле | Значение |
|------|----------|
| **ID** | B8 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `shared/ssh_opts.py`, `vps_readiness.py` |
| **Зависимости** | нет |
| **Риск** | LOW — замена константы |

**Описание:**
`vps_readiness.py` — `SSH_TIMEOUT=30`. Импортировать `SSH_CONNECT_TIMEOUT` из `shared/ssh_opts.py` (канон).

**Шаги:**
1. Импортировать `SSH_CONNECT_TIMEOUT` из `shared/ssh_opts`.
2. Заменить `SSH_TIMEOUT=30` → `SSH_CONNECT_TIMEOUT`.

**Acceptance Criteria:**
- AC-B8.1: `vps_readiness.py` использует `SSH_CONNECT_TIMEOUT` из shared/ssh_opts

---

## $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
```
coder Read .ai/plans/119-wave2-synthesis/03-DevPlan.md, implement Wave 1: B1, B2, B3, B4, B5, B6, B7, B8
```

B1 (project_yaml) не пересекается с B2/B3 (deploy_paths) и B4 (subprocess_io). B5-B8 — точечные замены в разных файлах.

**Внимание:** B1 — самый объёмный (9 файлов). Остальные — ≤3 файла каждая.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_project_yaml.py` | `test_read_expose_config` | Чтение expose из ai-platform.yaml | shared/project_yaml |
| `tests/unit/test_project_yaml.py` | `test_missing_field_raises_negative` | R5: отсутствующее поле → ошибка | shared/project_yaml |
| `tests/unit/test_deploy_paths.py` | `test_projects_base_from_env` | projects_base() уважает PROJECTS_BASE env | shared/deploy_paths |
| `tests/gates/test_gate_timeout_literals.py` | `test_opt_projects_literal_detected_negative` | R5: `/opt/projects` литерал → обнаружен | deploy_paths gate |
| `tests/unit/test_subprocess_io.py` | `test_run_subprocess_graceful_rc` | run_subprocess с graceful_rc=127 | shared/subprocess_io |
| `tests/gates/test_gate_subprocess_io_sole.py` | `test_single_run_subprocess_canon_negative` | R5: второй run_subprocess → обнаружен | subprocess_io gate |
| `tests/unit/test_ssl_certs.py` | `test_openssl_timeout_default` | cert_check_expiry использует DEFAULT_OPENSSL_TIMEOUT | shared/ssl_certs |
| `tests/unit/test_project_healthcheck.py` | `test_healthcheck_port_3000` | healthcheck находит порт 3000 | healthcheck poller |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-B-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-B-SOT | 0 дублирующих парсеров ai-platform.yaml (9→1) |
| AC-B-PATHS | 0 литералов /opt/* вне deploy_paths |
| AC-B-SUBPROC | Единственный run_subprocess в core/ |
| AC-B-R5 | Каждая задача имеет R5 negative-тест |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/03-DevPlan.md, implement Wave 1: B1, B2, B3, B4, B5, B6, B7, B8
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
