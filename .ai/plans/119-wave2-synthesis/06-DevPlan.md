# 06-DevPlan — Бриф E: Monolith Decomposition

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Декомпозиция крупных Python-монолитов (>900 LOC) на модули по ответственностям:
                  docker_orchestrator (907, CC=25), orchestrator (1154), phases (1080),
                  deploy_engine (874), deploy_orchestrator (941), context_deployer (927).
                  Создание канонического atomic_writer. Снижение цикломатической сложности.
DESCRIPTION:      9 задач (E1–E9). Самый высокий риск регресса — каждый монолит имеет десятки
                  потребителей. Зависит от брифа B (shared/project_yaml, atomic_writer канон).
RATIONALE:        Монолиты с CC>20 — источник багов. Декомпозиция по фазам/ответственностям
                  улучшает тестируемость и снижает риск регресса при изменениях.
ACCEPTANCE_CRITERIA:
  - AC-E-1: `make gate MODE=fast` зелёный после каждой задачи
  - AC-E-2: 0 regressions в существующих тестах (pytest tests/ -m "not requires_node")
  - AC-E-3: Каждый выделенный модуль имеет unit-тесты
  - AC-E-4: CC reduction: deploy_docker_module CC=25→≤10, receive CC=15→≤8
  - AC-E-5: atomic_writer.py — канонический модуль, 12+ генераторов мигрировано
IMPLEMENTS:       Бриф E из 01-Brief.md (волна 119) — Monolith Decomposition.
IMPACTS:          core/internal/deploy/docker_orchestrator.py, core/internal/deploy/orchestrator.py,
                  core/internal/deploy/receive_flow.py (NEW), core/internal/deploy/deploy_engine.py,
                  core/internal/deploy/deploy_orchestrator.py, core/internal/deploy/context_deployer.py,
                  core/internal/deploy/preflight.py (NEW), core/internal/deploy/first_deploy.py (NEW),
                  core/internal/deploy/orchestrator_metrics.py (NEW),
                  core/internal/deploy/upload/ (NEW), core/internal/bootstrap/lifecycle/phases.py,
                  core/internal/bootstrap/lifecycle/phases/ (NEW),
                  core/internal/shared/atomic_writer.py (NEW),
                  core/internal/bootstrap/vhost_renderer.py.
REQUIRES:         Результаты аудита 2 (Python-монолиты M2-M10, S5). Зависит от B1 (project_yaml),
                  B4 (subprocess_io), E5 (atomic_writer → используется в E1-E4).
-->

# DevPlan E — Monolith Decomposition

## $START_DEVPLAN

### Контекст

Волна 119, бриф E. Пятая волна — декомпозиция Python-монолитов. Самый высокий риск регресса. Каждый монолит (>900 LOC) разбивается на модули по ответственностям с сохранением публичного API. Зависит от брифа B (shared модули — atomic_writer создаётся здесь же как E5, используется остальными).

---

## $TASKS

### TASK-E1: docker_orchestrator deploy_docker_module → phase dispatch

| Поле | Значение |
|------|----------|
| **ID** | E1 |
| **Sev** | HIGH |
| **Сложность** | 7/10 |
| **Файлы** | `deploy/docker_orchestrator.py` (907 LOC), `deploy/hermes_workflow.py` (NEW), `deploy/observability.py` (NEW) |
| **Зависимости** | E5 (atomic_writer для docker-операций) |
| **Риск** | HIGH — CC=25, 13 if-веток, критический деплой-путь |

**Описание:**
`deploy_docker_module()` — 195 LOC, CC=25, 13 if-веток. Разбить по фазам с dispatch-таблицей.

**Шаги:**
1. Выделить `_phase_hermes()` — сборка/пулл hermes-образа → `deploy/hermes_workflow.py`.
2. Выделить `_phase_observability()` — monitoring/logging/metrics → `deploy/observability.py`.
3. Выделить `_phase_rebuild()` — пересборка контейнеров → оставить в docker_orchestrator.
4. Dispatch-таблица: `PHASES = {"hermes": _phase_hermes, "observability": _phase_observability, ...}`.
5. Unit-тесты на каждую фазу изолированно.
6. R5 negative-тест: `test_deploy_docker_module_phases` — все фазы вызываются в правильном порядке.

**Acceptance Criteria:**
- AC-E1.1: `deploy_docker_module()` CC ≤ 10, LOC ≤ 60
- AC-E1.2: `hermes_workflow.py` — изолированный модуль с тестами
- AC-E1.3: `observability.py` — изолированный модуль с тестами
- AC-E1.4: Все существующие тесты docker_orchestrator проходят
- AC-E1.5: R5 negative-тест: порядок фаз

---

### TASK-E2: orchestrator receive_flow → receive_flow.py

| Поле | Значение |
|------|----------|
| **ID** | E2 |
| **Sev** | MED |
| **Сложность** | 6/10 |
| **Файлы** | `deploy/orchestrator.py` (1154 LOC), `deploy/receive_flow.py` (NEW) |
| **Зависимости** | E1 (docker_orchestrator — receive использует deploy) |
| **Риск** | MED — receive() CC=15, deploy() CC=13 |

**Описание:**
`orchestrator.py` — 1154 LOC. `deploy()` 186 LOC CC=13, `receive()` 127 LOC CC=15. Вынести receive в `deploy/receive_flow.py`, deploy разбить на шаги.

**Шаги:**
1. Выделить `receive()` → `deploy/receive_flow.py` (класс `ReceiveFlow` с методами `unpack`, `validate`, `deploy`).
2. `deploy()` разбить на `_prepare()` / `_apply()` / `_verify()` / `_rollback()` — приватные методы.
3. Unit-тесты на ReceiveFlow.
4. R5 negative-тест: `test_orchestrator_receive_flow_parity` — старый и новый код возвращают одинаковый результат.

**Acceptance Criteria:**
- AC-E2.1: `receive_flow.py` существует с unit-тестами
- AC-E2.2: `orchestrator.receive()` → делегирует в `ReceiveFlow`
- AC-E2.3: `deploy()` разбит на 4 шага (CC ≤ 8 каждый)
- AC-E2.4: R5 parity-тест проходит

---

### TASK-E3: phases domain split

| Поле | Значение |
|------|----------|
| **ID** | E3 |
| **Sev** | MED |
| **Сложность** | 6/10 |
| **Файлы** | `lifecycle/phases.py` (1080 LOC), `lifecycle/phases/system.py` (NEW), `docker.py` (NEW), `secrets.py` (NEW), `certs.py` (NEW) |
| **Зависимости** | B4 (subprocess_io — phases активно используют) |
| **Риск** | MED — `phase_registry_update` CC=23, ключевая функция |

**Описание:**
`phases.py` 1080 LOC — фазы по доменам. Разбить на `phases/system.py`, `docker.py`, `secrets.py`, `certs.py` (паттерн `lifecycle/helpers`). `phase_registry_update` (CC=23) — кандидат №1.

**Шаги:**
1. Выделить system-фазы (users, packages, sudoers) → `phases/system.py`.
2. Выделить docker-фазы (registry_auth, docker_daemon) → `phases/docker.py`.
3. Выделить secrets-фазы → `phases/secrets.py`.
4. Выделить certs-фазы → `phases/certs.py`.
5. `phase_registry_update` — декомпозировать registry-логику (снизить CC с 23 до ≤10).
6. Unit-тесты на каждый domain-модуль.
7. R5 negative-тест: `test_phases_domain_imports` — все фазы регистрируются в registry.

**Acceptance Criteria:**
- AC-E3.1: `phases.py` — тонкий агрегатор, импортирующий domain-модули
- AC-E3.2: 4 domain-модуля созданы с тестами
- AC-E3.3: `phase_registry_update` CC ≤ 10
- AC-E3.4: R5 negative-тест: все фазы в registry

---

### TASK-E4: deploy_engine preflight/first_deploy extraction

| Поле | Значение |
|------|----------|
| **ID** | E4 |
| **Sev** | MED |
| **Сложность** | 4/10 |
| **Файлы** | `deploy/deploy_engine.py` (874 LOC), `deploy/preflight.py` (NEW), `deploy/first_deploy.py` (NEW) |
| **Зависимости** | E1, E2 |
| **Риск** | MED — deploy_engine — центральный модуль деплоя |

**Описание:**
`deploy_engine.py` 874 LOC — `_preflight_checks` и `_handle_first_deploy` вынести в отдельные модули.

**Шаги:**
1. Выделить `_preflight_checks()` → `deploy/preflight.py`.
2. Выделить `_handle_first_deploy()` → `deploy/first_deploy.py`.
3. Unit-тесты на каждый.
4. R5 negative-тест: `test_deploy_engine_preflight_parity`.

**Acceptance Criteria:**
- AC-E4.1: `preflight.py` и `first_deploy.py` существуют с тестами
- AC-E4.2: `deploy_engine.py` <600 LOC после экстракции
- AC-E4.3: R5 parity-тест

---

### TASK-E5: atomic_writer canonical → migrate generators

| Поле | Значение |
|------|----------|
| **ID** | E5 |
| **Sev** | MED |
| **Сложность** | 5/10 |
| **Файлы** | `shared/atomic_writer.py` (NEW), 12 файлов с os.replace, 16 с NamedTemporaryFile |
| **Зависимости** | нет (БЛОКИРУЕТ E1, H2) |
| **Риск** | MED — изменение 12+ файлов, разная семантика атомарности |

**Описание:**
`shared/atomic_writer.py` НЕ существует; 12 файлов с `os.replace`, 16 с `NamedTemporaryFile`. Создать канон `atomic_write(path, content, mode, validator)` — tempfile + os.replace + optional validator. Исключение: `json_writer.py` (Docker bind mount — TRAP задокументирован, НЕ мигрировать).

**Шаги:**
1. Создать `shared/atomic_writer.py`:
   ```python
   def atomic_write(path, content, mode=0o644, validator=None, tmp_dir=None) -> Path
   def atomic_write_json(path, data, mode=0o644) -> Path
   def atomic_write_text(path, text, mode=0o644) -> Path
   ```
2. Мигрировать генераторы: secrets_env_parser, docker_registry_auth, s3_ssl_cache, docker_daemon, sudoers, system (cron), secrets_manager, cache (metrics).
3. Пропустить: json_writer.py (Docker bind mount TRAP).
4. Unit-тесты: `test_atomic_write_success`, `test_atomic_write_validator_rejects`, `test_atomic_write_idempotent`.
5. R5 negative-тест: `test_atomic_writer_no_partial_write` — прерывание не оставляет мусора.

**Acceptance Criteria:**
- AC-E5.1: `shared/atomic_writer.py` существует с docstring и тестами
- AC-E5.2: 8+ генераторов мигрировано на atomic_write
- AC-E5.3: `json_writer.py` НЕ тронут (TRAP)
- AC-E5.4: `make gate MODE=fast` зелёный (новый модуль не нарушает gate)
- AC-E5.5: R5 negative-тест: нет partial write

---

### TASK-E6: deploy_orchestrator → pure functions

| Поле | Значение |
|------|----------|
| **ID** | E6 |
| **Sev** | MED |
| **Сложность** | 4/10 |
| **Файлы** | `deploy/deploy_orchestrator.py` (941 LOC), `deploy/orchestrator_metrics.py` (NEW) |
| **Зависимости** | E2 (orchestrator) |
| **Риск** | LOW — чистые функции, легко тестировать |

**Описание:**
`deploy_orchestrator.py` 941 LOC — вынести severity/exit-code агрегацию, status-metrics JSON, hc-маркеры, llm-рендер в чистые функции.

**Шаги:**
1. Выделить `aggregate_severity()`, `exit_code_from_results()`, `status_metrics_json()`, `hc_markers()`, `render_llm_summary()` → `orchestrator_metrics.py`.
2. Все функции — чистые (без сайд-эффектов, без I/O).
3. Unit-тесты на каждую.
4. R5 negative-тест: `test_orchestrator_metrics_pure` — нет сайд-эффектов.

**Acceptance Criteria:**
- AC-E6.1: `orchestrator_metrics.py` — чистые функции с тестами
- AC-E6.2: `deploy_orchestrator.py` <750 LOC
- AC-E6.3: R5: все функции pure (детерминированные)

---

### TASK-E7: context_deployer llm-layer extraction

| Поле | Значение |
|------|----------|
| **ID** | E7 |
| **Sev** | MED |
| **Сложность** | 5/10 |
| **Файлы** | `deploy/context_deployer.py` (927 LOC), `llm/provision_flow.py` (NEW) |
| **Зависимости** | E2 |
| **Риск** | MED — LLM provisioning, сложная логика |

**Описание:**
`context_deployer.py` 927 LOC — `_render_and_provision_llm` вынести в `llm/provision_flow.py`. `deploy_context` ветвления → dispatch-таблица.

**Шаги:**
1. Выделить `_render_and_provision_llm()` → `llm/provision_flow.py`.
2. `deploy_context()` — заменить цепочку if-elif на dispatch-таблицу.
3. Unit-тесты для provision_flow.
4. R5 negative-тест: `test_context_deployer_llm_flow` — flow не нарушен.

**Acceptance Criteria:**
- AC-E7.1: `provision_flow.py` существует с тестами
- AC-E7.2: `context_deployer.py` <750 LOC
- AC-E7.3: `deploy_context()` dispatch-таблица (не if-elif цепочка)
- AC-E7.4: R5 negative-тест

---

### TASK-E8: upload.py split

| Поле | Значение |
|------|----------|
| **ID** | E8 |
| **Sev** | LOW |
| **Сложность** | 3/10 |
| **Файлы** | `backup-cron/scripts/upload.py` (745 LOC), `upload/uploader.py` (NEW), `upload/verifier.py` (NEW) |
| **Зависимости** | C1 (backup-cron fix) |
| **Риск** | LOW — изолированный модуль |

**Описание:**
`upload.py` 745 LOC — разбить `_upload` и `_verify` на отдельные модули.

**Шаги:**
1. Выделить `_upload()` → `upload/uploader.py`.
2. Выделить `_verify()` → `upload/verifier.py`.
3. Unit-тесты.
4. R5 negative-тест: `test_upload_verify_split` — загрузка+верификация работают.

**Acceptance Criteria:**
- AC-E8.1: `uploader.py` и `verifier.py` существуют с тестами
- AC-E8.2: `upload.py` <300 LOC (тонкий агрегатор)

---

### TASK-E9: vhost_renderer parser → depends on B1

| Поле | Значение |
|------|----------|
| **ID** | E9 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `vhost_renderer.py` |
| **Зависимости** | B1 (project_yaml API расширен) |
| **Риск** | LOW — после B1 тривиально |

**Описание:**
После расширения `shared/project_yaml` в B1, удалить собственный `read_project_yaml` из `vhost_renderer.py`.

**Шаги:**
1. Заменить вызовы `read_project_yaml` на `project_yaml.get_*`.
2. Удалить локальную функцию.
3. R5 negative-тест: `test_vhost_renderer_uses_shared_project_yaml`.

**Acceptance Criteria:**
- AC-E9.1: `vhost_renderer.py` НЕ содержит `def read_project_yaml`
- AC-E9.2: Все импорты ai-platform.yaml идут через shared/project_yaml

---

## $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
- E5 (atomic_writer) — БЛОКИРУЕТ E1 (не прямой dep, но E1 использует atomic_writer)
- E6 (orchestrator_metrics) — чистые функции, не зависит от других
- E8 (upload.py split) — изолирован

### Wave 2 (зависят от Wave 1)
- E1 (docker_orchestrator) — зависит от E5
- E2 (orchestrator receive_flow) — зависит от E1 концептуально
- E3 (phases domain) — зависит от B4 (subprocess_io)
- E4 (deploy_engine) — зависит от E1, E2

### Wave 3 (зависят от Wave 2 + B1)
- E7 (context_deployer) — зависит от E2
- E9 (vhost_renderer) — зависит от B1 (должен быть готов из волны 2)

```
Wave 1: E5, E6, E8
Wave 2: E1, E2, E3, E4
Wave 3: E7, E9
```

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_hermes_workflow.py` | `test_phase_hermes_build` | Сборка hermes-образа | hermes_workflow |
| `tests/unit/test_docker_orchestrator.py` | `test_deploy_docker_module_phases_negative` | R5: все фазы в правильном порядке | docker_orchestrator |
| `tests/unit/test_receive_flow.py` | `test_receive_unpack_validate` | Receive flow: unpack + validate | receive_flow |
| `tests/unit/test_receive_flow.py` | `test_orchestrator_receive_flow_parity_negative` | R5: старый/новый код — одинаковый результат | receive_flow vs orchestrator |
| `tests/unit/test_phases_docker.py` | `test_docker_phases_registry` | Docker-фазы в registry | phases/docker |
| `tests/unit/test_phases_docker.py` | `test_phases_domain_imports_negative` | R5: все фазы в registry | phases |
| `tests/unit/test_deploy_preflight.py` | `test_preflight_checks` | Preflight проверки | deploy/preflight |
| `tests/unit/test_atomic_writer.py` | `test_atomic_write_success` | Успешная запись | atomic_writer |
| `tests/unit/test_atomic_writer.py` | `test_atomic_write_no_partial_write_negative` | R5: нет partial write при прерывании | atomic_writer |
| `tests/unit/test_orchestrator_metrics.py` | `test_aggregate_severity` | Агрегация severity | orchestrator_metrics |
| `tests/unit/test_provision_flow.py` | `test_render_and_provision_llm` | LLM provisioning flow | provision_flow |
| `tests/unit/test_upload.py` | `test_upload_verify_split_negative` | R5: загрузка + верификация | upload |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-E-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-E-CC | Цикломатическая сложность ключевых функций снижена (CC=25→≤10, CC=15→≤8) |
| AC-E-LOC | 5 монолитов суммарно сокращены на ≥800 LOC (вынесено в целевые модули) |
| AC-E-ATOMIC | atomic_writer.py — канон, 8+ генераторов мигрировано |
| AC-E-R5 | Каждая экстракция имеет parity-тест (старый/новый код) |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/06-DevPlan.md, implement Wave 1: E5, E6, E8
```
### Wave 2
```
coder Read .ai/plans/119-wave2-synthesis/06-DevPlan.md, implement Wave 2: E1, E2, E3, E4
```
### Wave 3
```
coder Read .ai/plans/119-wave2-synthesis/06-DevPlan.md, implement Wave 3: E7, E9
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
