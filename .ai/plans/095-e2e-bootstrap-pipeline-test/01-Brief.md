$START_BRIEF
# Brief 095 — E2E Bootstrap Pipeline Test

## $ARTIFACT_CONTRACT
- **PURPOSE:** Создать автоматизированный E2E тест полного bootstrap-pipeline на пересоздаваемой тестовой ноде. Единственный способ верифицировать, что 14 фаз (из 087) + DeployOrchestrator (из 089) + scaffold (из 092) работают end-to-end после всех миграций.
- **DESCRIPTION:** Smoke/integration тест, прогоняющий: cold-start bootstrap → converge → deploy test-project → healthcheck → backup → restore. Плюс сценарий отказа mid-bootstrap (проверка `phases.py` precondition/resume). Запускается на пересоздаваемой test-VPS (инвариант 9: можно ронять).
- **RATIONALE:** Все 271+ тестов — unit/static/gate. Ни один не прогоняет реальный pipeline на VPS-подобном окружении. После Strangler-Fig (087-094) единственная гарантия работоспособности — E2E. 3 экспертизы единогласно отметили этот gap как критический.
- **ACCEPTANCE_CRITERIA:** E2E тест проходит на чистой test-VPS: bootstrap init (14 фаз) → converge → deploy → healthcheck → backup/restore round-trip; сценарий отказа: kill контейнер mid-phase 7 → resume → завершается корректно.
- **IMPLEMENTS:** Закрытие GAP-4 (из 2-й и 3-й экспертизы) + AC10/AC12 из DevPlan 087/089.
- **IMPACTS:** Новый test-артефакт в `tests/` (или `tests/e2e/`). Не меняет production-код (только читает).
- **REQUIRES:** **091 STABLE** (087 dispatch переключён, 089 orchestrator готов). Желательно 092 (scaffold) для deploy test-project.

## User Constraint (CRITICAL)
- Тестовая нода **пересоздаваема** (инвариант 9). Не нужна backward-compat. Cold start с нуля.
- ❌ НЕ тестировать миграцию state.json со старого формата (старый формат не поддерживается).
- ✅ Тестировать только чистый cold-start + resume после fresh-failure.

## Current Status (Audit 2026-07-30)
- **Coverage:** 0 E2E тестов. AC10 (089 dry-run) и AC12 (087 dry-run) — manual verification, не автоматизированы.
- **Test types present:** unit, static, gate (271). Нет integration/e2e на реальном окружении.
- **Test infra:** `tests/gates/` — статические. Нет VPS-targeting test runner.

## Key Findings (verificated)
- `lib/ssh.sh` — single point of failure (TRAP[DECISION] в AGENTS.md). E2E — единственный способ покрыть `ssh_exec`/`ssh_read` на реальном SSH.
- `phases.py` (087) имеет `_resume_phase()` — никогда не тестировался на реальном отказе.
- DeployOrchestrator (089, 842 LOC) имеет 57 unit-тестов, но ни одного end-to-end на реальном deploy через SSH forced-command.

## Required Actions

### Wave 1: test infrastructure
1. Определить тестовую ноду (test-VPS, пересоздаваемая). Зафиксировать в `node-configs/test-e2e.yaml`.
2. Создать `tests/e2e/test_bootstrap_pipeline.py` — pytest с `@pytest.mark.requires_node` (маркер, не входит в `make test` по умолчанию — запуск через `make test-node`).
3. Setup/teardown: пересоздание ноды (или reset state) перед прогоном.

### Wave 2: happy-path scenarios
4. `test_cold_start_bootstrap`: `make bootstrap-node NODE=test-e2e --mode init` → все 14 фаз PASS.
5. `test_converge`: `make converge NODE=test-e2e` → desired state достигнут.
6. `test_deploy_test_project`: `make deploy PROJECT=test-proj NODE=test-e2e` → проект запущен.
7. `test_healthcheck`: `make healthcheck NODE=test-e2e` → все модули healthy.
8. `test_backup_restore_roundtrip`: backup → destroy → restore → данные восстановлены.

### Wave 3: failure scenarios
9. `test_resume_after_failure`: kill docker mid-phase 7 → `make bootstrap-node --mode init` повторно → resume с phase 7 → завершается.
10. `test_ssh_read_timeout`: simulate SSH timeout → graceful error (TRAP lib/ssh.sh).
11. `test_deploy_forced_command`: CI-equivalent deploy через SSH forced-command → orchestrator_cli receive работает.

### Wave 4: runner + documentation
12. `make test-node` target (отдельный от `make test` — долго, требует VPS).
13. Документация: `tests/e2e/README.md` — как подготовить test-VPS, как запускать.

## Verification
- `make test-node NODE=test-e2e` → зелёный (после подготовки ноды).
- Все 11 сценариев PASS.
- Не входит в `make gate` (e2e = manual/expensive), но в CI можно запускать по тегу.
- Маркер `requires_node` ортогонален существующему `e2e` (HTTP-проверки *.tronyx.ru) — см. DD1 в DevPlan.

## Anti-Loop Note
E2E тест должен быть **детерминированным** (фиксированный test-project, фиксированная конфигурация). Не делать parameterized matrix — это замедлит и сделает хрупким. Один canonical happy-path + 3 failure-сценария.

$END_BRIEF
