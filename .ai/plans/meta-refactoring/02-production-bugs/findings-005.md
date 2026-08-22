# Direction 5: state transitions — forensic bug hunt

Date: 2026-08-22 · Commit: 10c1bf2 · Mode: read-only audit

---

## BUG-0501

**Severity:** HIGH
**Confidence:** 85%
**File:** core/internal/bootstrap/lifecycle/phases/docker.py (+ core/internal/bootstrap/deploy/deploy_orchestrator.py)
**Symbol:** `_registry_step_healthcheck` (consumer) / `_set_hc_marker` (writer)

**Trigger:** Любой `node-update` после первого параллельного деплоя (DEPLOY_PARALLEL=true), или первый `node-update` после bootstrap с DEPLOY_PARALLEL=true.

**Execution path:**
1. Run N, фаза φ12 (deploy_update) → deploy-modules.sh → deploy_orchestrator.py:554 `_set_hc_marker()` пишет `/var/lib/platform/.bootstrap/.hc_done_in_deploy` (healthcheck выполнен внутри topo-групп деплоя).
2. Фазовый порядок UPDATE-режима: φ11 (registry_update, единственный потребитель маркера) выполняется **ДО** φ12. Маркер остаётся на диске после завершения run N.
3. Run N+1: φ11 выполняет свежую работу (GHCR auth → provision env → overlays → LLM keys) и на шаге healthcheck читает маркер — phases/docker.py:599-609: файл существует → «Healthcheck already done during deploy — skipping» + unlink. Пропущен healthcheck **текущего** состояния по маркеру, валидировавшему **прошлый** run.
4. Усиление через INIT: при bootstrap φ8 (deploy_services) маркер пишется, но ни одна INIT-фаза (φ8/φ8.5) его не потребляет — маркер переживает весь bootstrap, и первый же `node-update` φ11 пропускает standalone healthcheck по бутстрап-эпохе.

**Actual behavior:** Standalone healthcheck φ11 молча пропускается при каждом запуске после первого параллельного деплоя; цикл «unlink в φ11 → повторная запись в φ12» делает пропуск перманентным.

**Expected behavior:** Маркер валиден только внутри того же lifecycle-run, который его создал (потребление в том же run или очистка при старте каждого init/update).

**Impact:** Деградация ноды (упавшие контейнеры после overlays/provision) не обнаруживается штатным healthcheck-каналом; сигнал уходит только в мониторинг/прод. Статус фазы при этом done — state.json честный, но проверка не выполнялась.

**Minimal fix:** Удалять маркер в начале каждого run (`cli.run_init_mode`/`run_update_mode`) или включать run-id в имя маркера; потребитель игнорирует чужие run-id.

**Required regression test:** Unit: последовательность set_marker → run φ12 → новый StateMachine → execute φ11 с fake run_healthchecks_fn — assert runner вызван (маркер от предыдущего run не подавляет). Плюс тест INIT→UPDATE: marker после φ8, φ11 первого update вызывает healthcheck.

---

## BUG-0502

**Severity:** HIGH
**Confidence:** 80%
**File:** core/internal/deploy/orchestrator.py (+ core/internal/deploy/audit/history.py)
**Symbol:** `_verify_deploy` / `DeployHistory.create_snapshot` / `_rollback_compose`

**Trigger:** Любой ручной rollback проекта (`rollback()`), либо авто-rollback в `_apply_deploy` после неудачного compose-up.

**Execution path:**
1. Деплой через receive: orchestrator.py:549-554 `_verify_deploy` вызывает `create_snapshot(project, version, health_status, payload_backup_dir)` — аргумент `compose_state` НЕ передаётся.
2. history.py:171: `"compose_state": compose_state or {}` → каждый снапшот DeployOrchestrator несёт `compose_state={}`; ключ `previous_image` никогда не записывается.
3. Rollback: orchestrator.py:728 `history.rollback` → снапшот → `_restore_payload_files(payload_dir, project_dir)` **сначала** перезаписывает docker-compose.yml/.env.platform старыми версиями → orchestrator.py:1147-1160 `_rollback_compose`: `prev_image_id = {}.get("previous_image")` → None → `docker_tag` пропущен → `engine.deploy(ref="previous-rollback")`.
4. engine/engine.py:218-220: `pull_images` с `IMAGE_TAG=previous-rollback` — тег `{service}:previous-rollback` никем не создавался (единственный создатель — сама `_rollback_compose` при непустом `previous_image`) → 5 неудачных пулов → `handle_first_deploy` → `PlatformFatalError` (exit 10).
5. В авто-пути двойной rollback: engine внутри своего `deploy()` уже восстановил предыдущий образ (`perform_rollback`, engine.py:227-228/256), затем orchestrator.py:493-506 запускает второй rollback по снапшоту — падает тем же fatal-крэшем посреди обработки сбоя.

**Actual behavior:** Rollback завершается PlatformFatalError (exit 10) вместо восстановления версии; при этом payload-файлы проекта уже заменены старыми до падения compose-шага — директория проекта дрейфует относительно работающего контейнера. CI получает fatal вместо структурированного ROLLED_BACK JSON.

**Expected behavior:** Снапшот содержит фактическое состояние для отката (previous_image), rollback восстанавливает образ и атомарен по отношению к payload-файлам; повторный rollback поверх уже выполненного engine-rollback не инициируется.

**Impact:** Механизм rollback фактически неработоспособен для всех проектов, задеплоенных через receive/deploy-канал (основной прод-путь); аварийная процедура ухудшает состояние (payload-drift + exit 10).

**Minimal fix:** Перед compose-up в `_apply_deploy` захватывать текущий image id и писать `compose_state={"previous_image": id}` в снапшот (или ре-тегать предыдущий образ в `service:previous-rollback` перед `engine.deploy`); выполнять restore payload только после успешного compose-rollback; не запускать snapshot-rollback, если `ServiceDeployResult.rollback_performed=True`.

**Required regression test:** Unit: create_snapshot без compose_state → rollback с fake engine, assert engine.deploy получил валидный существующий тег и payload restore произошёл только при успехе compose. Интеграционный: compose_fail → результат OrchestratorDeployResult.ROLLED_BACK (не исключение), payload_dir соответствует откоченной версии.

---

## BUG-0503

**Severity:** MEDIUM
**Confidence:** 75%
**File:** core/internal/deploy/audit/history.py (+ core/internal/deploy/orchestrator.py)
**Symbol:** `DeployHistory.latest_snapshot` / `_verify_deploy`

**Trigger:** Последовательность: деплой v2 с упавшим healthcheck (PARTIAL) → следующий деплой v3 с ошибкой compose.

**Execution path:**
1. Деплой v2: orchestrator.py:544-556 — healthcheck unhealthy → статус PARTIAL, но `create_snapshot` вызывается **до** вычисления статуса и безусловно: снапшот S2 записан с `health_status="unhealthy"`.
2. Деплой v3: `_apply_deploy` compose-fail → orchestrator.py:495 `latest_snapshot(project_name)`.
3. history.py:333-345: `latest_snapshot` возвращает новейший *.json без фильтра по `health_status` → цель отката S2 (известно нездоровый деплой v2), а не последний здоровый v1.
4. `_rollback_deploy` откатывает проект к состоянию v2 — статус ROLLED_BACK сообщается оператору/CI как успех.

**Actual behavior:** «Успешный» rollback восстанавливает деплой, о нездоровости которого система уже знает из собственного поля снапшота.

**Expected behavior:** Rollback выбирает новейший снапшот с `health_status == "healthy"`; unhealthy/PARTIAL-снапшоты не являются целью отката (или явно помечаются ineligible).

**Impact:** После аварийного деплоя проект остаётся нездоровым под флагом ROLLED_BACK; оператор считает инцидент закрытым. Независимо от BUG-0502 (сломанный механики отката) выбор цели неверен даже при починенном механизме.

**Minimal fix:** `latest_snapshot(project, require_healthy=True)` — фильтрация по `health_status=="healthy"` с fallback-WARN, если здоровых нет; либо поле `eligible_for_rollback` в снапшоте, проставляемое только при DEPLOYED.

**Required regression test:** Unit: снапшоты [S1 healthy, S2 unhealthy] → `latest_snapshot(require_healthy)` возвращает S1; сценарий deploy(PARTIAL)→deploy(fail) → rollback-цель = healthy-снапшот.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0501 | HIGH | 85% | Stale `.hc_done_in_deploy`: пишется в φ12/φ8 после единственного потребителя (φ11) — standalone healthcheck подавляется навсегда маркером прошлого run |
| BUG-0502 | HIGH | 80% | Снапшоты всегда с `compose_state={}` → rollback деплоит несуществующий тег `service:previous-rollback` → PlatformFatalError exit 10 + payload-drift (двойной rollback поверх engine-rollback) |
| BUG-0503 | MEDIUM | 75% | `latest_snapshot` игнорирует `health_status` — целью отката становится известный unhealthy PARTIAL-деплой под флагом ROLLED_BACK |
