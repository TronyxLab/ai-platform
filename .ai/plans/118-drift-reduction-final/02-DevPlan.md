# 02-DevPlan — Бриф A: критические фиксы деплоя

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Реализация задач A1-A8 брифа 118 — устранение критических дефектов deploy-канала ПЕРЕД ручным тестированием на tronyx-vps.
DESCRIPTION:      8 задач: A1 SCPChannel bugfix, A2 compose-списки SoT, A3 reconciler paths, A4 payload-dedup, A5 importlib-bypass,
                  A6 status/remove/stub консолидация, A7 snapshot-dedup, A8 deploy_engine схлопывание.
RATIONALE:        K1 (SCPChannel без metadata) гарантированно ломает deploy-context; K2 (compose-списки) — расхождение converge vs deploy;
                  K8 (2 tar-пути) — дрейф формата payload. Все 8 задач блокируют честное ручное тестирование.
ACCEPTANCE_CRITERIA:
  - AC-A1: SCPChannel→LocalChannel: deploy-context возвращает deployed (test_context_deployer_channel, negative-тест до фикса).
  - AC-A2: Единый COMPOSE_FILENAMES в shared/compose_files.py; 4+ потребителя делегируют; нет второго кортежа.
  - AC-A3: reconciler_projects резолвит PROJECTS_BASE из env-цепочки (совпадает с deploy_engine/payload_deliverer).
  - AC-A4: _assemble_payload удалён из orchestrator; единственный путь tar — payload_deliverer.assemble_payload.
  - AC-A5: context_deployer использует нормальный импорт cert_orchestrator; приватный _is_cert_valid заменён на public API.
  - AC-A6: status/remove/stub — единственная реализация в deploy_engine; orchestrator делегирует.
  - AC-A7: один snapshot-механизм (deploy_history); _capture_deploy_snapshot удалён или делегирует.
  - AC-A8: deploy()/_deploy_inner схлопнуты в одну функцию без дублированной валидации.
  - AC-A9: make gate MODE=fast, check-manifests, ruff — зелёные.
IMPLEMENTS:       118 01-Brief задачи A1-A8.
IMPACTS:          core/internal/bootstrap/deploy/context_deployer.py, core/internal/deploy/{orchestrator.py,deploy_engine.py,payload_deliverer.py,deploy_history.py},
                  core/internal/bootstrap/converge/{runtime.py,volumes.py}, core/internal/shared/, core/internal/reconciler_projects.py, tests/.
REQUIRES:         118 01-Brief, grep-верификация фактов (в §1).
-->

---

## 1. Технический анализ и решения

### A1 (CRITICAL) — SCPChannel без metadata → LocalChannel

**Факты (верифицированы):** `context_deployer.py:287` создаёт `channel = SCPChannel()` без аргументов. `channels.py:228` требует `payload.metadata["host"]` → delivery всегда FAILED → `orchestrator.deploy()` получает failed → deploy-context возвращает status="failed" для всех проектов.

**Решение:** заменить `SCPChannel()` → `LocalChannel()`. Обоснование (TRAP channels.py:327): LocalChannel создан именно для VPS-side receive — payload уже на месте после context_overlay, транспорт не нужен.

**Тест (R5):** `tests/unit/test_context_deployer_channel.py` — воспроизводит сценарий ДО фикса (failed) и ПОСЛЕ (deployed). Проверить `payload_deliverer`-путь: LocalChannel должен принимать уже собранный payload.

**Файлы:** context_deployer.py:287, channels.py (убедиться в контракте LocalChannel), тест.

**Риск:** LOW (TRAP-обоснование), блокирует AC7 брифа.

### A2 (HIGH) — единый SoT списков compose-файлов

**Факты (верифицированы):**
- `docker_orchestrator.py:133` — `COMPOSE_FILENAMES = ("compose.yaml", "docker-compose.yaml", "docker-compose.base.yml")`
- `converge/runtime.py:224` — `("compose.yaml", "compose.yml", "docker-compose.yml")`
- `converge/volumes.py:160` — тот же (без `docker-compose.base.yml`, с `compose.yml`)
- `payload_deliverer.py:60-61` — `("docker-compose.yml", "compose.yaml")`
- `project_adopter.py:477` — `("compose.yaml", "docker-compose.yml")`

**Расхождение:** converge лечит модули (с `compose.yml`), которые docker_orchestrator никогда не деплоит, и наоборот (`docker-compose.base.yml` не виден converge).

**Решение:** `shared/compose_files.py` — канонический кортеж + порядок + `resolve_compose_file(module_dir) -> Path | None` + `requires_compose_project(module_dir) -> bool`. Все 5 потребителей делегируют. Значение канона: приоритет `compose.yaml` → `docker-compose.yaml` → `docker-compose.yml` → `docker-compose.base.yml` (совмещённый порядок, покрывающий оба сценария; `compose.yml` убрать как не-канонический — проверить, нет ли реальных модулей с `compose.yml`).

**Тест:** unit на resolve/requires + gate-тест «нет второго кортежа в core/» (расширить существующий compose_sole_path или новый).

**Файлы:** shared/compose_files.py (новый), docker_orchestrator.py, converge/runtime.py, converge/volumes.py, payload_deliverer.py, project_adopter.py, tests/.

**Риск:** MED — перед фиксацией канона проверить git-историю: были ли реальные модули только с `compose.yml` / только с `docker-compose.base.yml`.

### A3 (HIGH) — reconciler_projects хардкод `/opt/projects`

**Факты:** `reconciler_projects.py:392` — `f"/opt/projects/{org_prefix}{spec.name}"` без env-резолва, тогда как deploy_engine/payload_deliverer/orchestrator_cli резолвят `PROJECTS_BASE` из env.

**Решение:** ввести общий резолвер `shared/deploy_paths.projects_base(env) -> Path` (см. C7 — там активация deploy_paths; здесь — точечный фикс через существующую env-модель orchestrator_cli). Заменить литерал на резолвер.

**Тест:** unit-тест: reconciler строит путь по PROJECTS_BASE=... (tmp_path), а не хардкод.

**Файлы:** reconciler_projects.py:392, shared/deploy_paths.py (добавить projects_base), тест.

**Риск:** LOW (точечная замена, покрыта тестом).

### A4 (HIGH) — `_assemble_payload` дубль

**Факты (верифицированы):** `orchestrator.py:949` `_assemble_payload(...)` и `payload_deliverer.py:119` `assemble_payload(...)` — два пути сборки tar.gz. Метаданные/whitelist могут разойтись → дрейф формата payload.

**Решение:** `DeployOrchestrator._assemble_payload` → делегирует `payload_deliverer.PayloadAssembler.assemble_payload` (или static-метод). Удалить локальную реализацию (проверить расхождения аргументов: version, metadata — выровнять контракт).

**Тест:** существующие deploy-тесты + новый: orchestrator собирает payload через тот же код, что payload_deliverer (set-сравнение содержимого tar).

**Файлы:** orchestrator.py:949, payload_deliverer.py:119, tests/.

**Риск:** MED — контракт вызова (импорт снаружи) не менять, только внутреннюю делегацию.

### A5 (HIGH) — importlib-обход в context_deployer

**Факты (верифицированы):** `context_deployer.py:647` — `importlib.util.spec_from_file_location("cert_orchestrator", ...)` + приватный `cert_mod._is_cert_valid` (строки 645-653). Обход системы импорта ломается тихо при рефакторинге cert-кода.

**Решение:** заменить на нормальный импорт `from core.internal.bootstrap.cert_orchestrator import CertOrchestrator` (проверить имя класса) + заменить `_is_cert_valid` на public API (`shared/ssl_certs.cert_is_valid(domain, cert_dir)` — см. C9, либо существующий public-метод cert_orchestrator). Убрать `_extract_domains_from_yaml`-дубль при наличии.

**Тест:** существующий test_context_deployer + negative-тест: отсутствие файла cert_orchestrator даёт обычный ImportError (не silent).

**Файлы:** context_deployer.py:645-653, тест.

**Риск:** LOW (замена на прямой импорт), зависит от C9 (cert-политика) или существующего public API.

### A6 (HIGH) — status/remove/stub консолидация

**Факты (верифицированы):**
- `status()`: orchestrator.py:619 (ProjectStatus, ручной `docker compose ps --format json`) + deploy_engine.py:501 (StatusResult).
- `remove()`: orchestrator.py:681 (`down --timeout 30` без -v) + deploy_engine.py:462 (RemoveResult).
- stub-детекция `"GENERATED-STUB"` в первой строке: orchestrator.py:639 + deploy_engine.py:525, при существующем `shared/stub_detection.is_stub_ai_platform_yaml`.

**Решение:**
1. Канон = DeployEngine (StatusResult/RemoveResult). DeployOrchestrator.status()/remove() делегируют и преобразуют типы.
2. Обе инлайн-копии stub-детекции → `is_stub_ai_platform_yaml`.

**Тест:** set-сравнение ключей ProjectStatus/StatusResult (расширить существующий test_deploy_orchestrator), negative: stub-проект определяется единым детектором.

**Файлы:** orchestrator.py:619,639,681; deploy_engine.py:462,501,525; tests/.

**Риск:** MED — изменение JSON-канона status ломает orchestrator_cli/фасады; покрыть тестом.

### A7 (MED) — двойной snapshot

**Факты (верифицированы):** `deploy_engine.py:392` `_capture_deploy_snapshot(project_dir)` (ps/images в файлы) + `deploy_history.py:104` `create_snapshot(version=...)` (JSON-снимок). Оба на каждый deploy.

**Решение:** DeployEngine-snapshot — дубль (DeployHistory покрывает rollback). Удалить `_capture_deploy_snapshot` + `SnapshotInfo`-запись, ЕСЛИ rollback не читает engine-snapshot (проверить `_rollback`/`restore` пути). Иначе — делегировать в deploy_history.

**Тест:** rollback-сценарий после удаления — восстановление работает через deploy_history.

**Файлы:** deploy_engine.py:392,626-672; deploy_history.py; tests/.

**Риск:** MED — проверить читателей SnapshotInfo перед удалением.

### A8 (MED) — deploy()/_deploy_inner схлопывание

**Факты (верифицированы):** `deploy_engine.py:294` `deploy()` → `:343` `_deploy_inner()` — дублированный docstring, дублированный validate_project_name, лишний слой.

**Решение:** объединить в одну функцию с `contextlib.chdir`; убрать дублирующую валидацию. Без изменения внешнего контракта.

**Тест:** существующий test_deploy_engine (без регрессий).

**Файлы:** deploy_engine.py:294-374, тест.

**Риск:** LOW.

---

## 2. Порядок выполнения

```
A1 (SCPChannel)  ← первым: блокирует deploy-context
   │
A5 (importlib)   ← вторым: тихий полом cert-пути
   │
A2 → A3 → A4     ← SoT-фиксы (независимы друг от друга)
   │
A6 → A7 → A8     ← deploy_engine консолидация (общие файлы, после A4)
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 8 |
| LOC | −60…−100 (A4 −40, A6 −80, A7 −45, A8 −30, A2 −10, net) |
| Bugfix'ов | 2 (A1 CRIT, A5 HIGH) |
| Тестов | +3 новых (A1 channel, A2 compose, A3 paths), остальные — расширение существующих |

## $END

Открытые вопросы:
1. **A2 канон порядка** — есть ли реальные модули только с `compose.yml`? Проверить git-историю перед фиксацией кортежа.
2. **A7** — читает ли rollback() engine-snapshot? Если да — делегирование, не удаление.
