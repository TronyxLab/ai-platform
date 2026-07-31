$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Создать автоматизированный E2E тест полного bootstrap-pipeline на пересоздаваемой тестовой ноде. Единственный способ верифицировать, что 14 фаз (из 087) + DeployOrchestrator (из 089) + scaffold (из 092) + lib/ssh.sh работают end-to-end на реальном VPS-подобном окружении после всех Strangler-Fig миграций. Закрывает GAP-4 (3 экспертизы), AC10/AC12 из DevPlan 087/089 (ранее manual-only).
DESCRIPTION:           Новый pytest-маркер `requires_node` (не путать с существующим `e2e` — это HTTP-проверки *.tronyx.ru). Тесты живут в `tests/e2e/`, прогоняются через новый `make test-node NODE=<name>`. 8 happy-path сценариев (cold-start bootstrap 9 INIT фаз → converge → deploy test-project → healthcheck → backup → restore round-trip) + 3 failure-сценария (resume φ7 после mid-phase kill, ssh_read timeout graceful error, forced-command receive через orchestrator_cli). Fixtures: `_conftest/node.py` (проверка доступности ноды, skip-as-FAIL per Rule R4, сброс state.json через `--force`). Тестовая нода — пересоздаваемая test-VPS (инвариант 9), cold-start only (state_migration удалён в Wave B 091).
RATIONALE:             Все 271+ тестов — unit/static/gate/mocked-integration. `test_bootstrap_dry_run.py` (8 тестов) полностью мокает subprocess — реальный SSH/Docker никогда не вызывается. `test_deploy_e2e.py` (4 теста) использует `IntegrationMockChannel`. После Strangler-Fig (087-094) единственная гарантия, что `ssh_exec`/`ssh_read` (single point of failure per TRAP), `resume_phase()`, `DeployOrchestrator.receive()` работают на реальном окружении — это E2E. Бrief отмечает: AC10/AC12 отмечены ⚠️ NOT_VERIFIABLE / NOT_VERIFIED в VR 091.
ACCEPTANCE_CRITERIA:
  - AC1: Новый pytest-маркер `requires_node` зарегистрирован в `pyproject.toml [tool.pytest.ini_options] markers` (gate `test_pyproject_toml_has_all_markers` зелёный)
  - AC2: `make test-node NODE=test-e2e` target добавлен в `makefiles/ci.mk`, зарегистрирован в `entrypoint-manifest.yaml#allowed_verbs`, не входит в `make gate` и `make test MARKER=all`
  - AC3: `tests/e2e/conftest.py` содержит `requires_node` fixture: проверяет `NODE` env, при отсутствии → `pytest.fail` (R4: NO_SERVICE = FAIL), не skip
  - AC4: 8 happy-path тестов PASS на чистой test-VPS: cold-start bootstrap (9 INIT фаз) → converge → deploy → healthcheck → backup → destroy → restore → verify-data
  - AC5: 3 failure-сценария PASS: resume φ7 (certificates) после mid-phase kill, ssh_read timeout → graceful error, forced-command receive через orchestrator_cli
  - AC6: Все E2E тесты детерминированы (фиксированный test-project, фиксированная конфигурация, 0 parameterized matrix — per Anti-Loop Note)
  - AC7: Каждый тест имеет LDD trajectory (IMP:9 assertion) + TRAP[TEST] marker
  - AC8: `tests/e2e/README.md` документирует: подготовку test-VPS, переменные окружения, как запускать, troubleshooting
  - AC9: `make gate MODE=fast` остаётся зелёным (E2E не входит в gate)
  - AC10: Существующий маркер `e2e` (HTTP-проверки *.tronyx.ru) НЕ изменён — новый маркер ортогонален
  - AC11: `tests/e2e/fixtures/test-project/` содержит canonical test-project (docker-compose.yml + ai-platform.yaml + .env.platform)
IMPLEMENTS:            Brief 095 §Required Actions (Waves 1-4), закрытие GAP-4 (2-я и 3-я экспертизы), AC10 (089 dry-run) + AC12 (087 dry-run) автоматизация, VR 091 AC-B3 (NOT_VERIFIABLE → verifiable)
IMPACTS:               CREATE: tests/e2e/__init__.py, tests/e2e/conftest.py, tests/e2e/test_bootstrap_pipeline.py, tests/e2e/test_failure_scenarios.py, tests/e2e/fixtures/test-project/ (3 файла), tests/e2e/README.md, tests/_conftest/node.py, node-configs/test-e2e/node.yaml. MODIFY: pyproject.toml (markers), makefiles/ci.mk (test-node target), core/entrypoint-manifest.yaml (allowed_verbs + registered verb), tests/AGENTS.md (taxonomy entry). Не меняет production-код (только читает). Подробно в §5 File Manifest.
REQUIRES:              **DP-091 STABLE** (087 dispatch переключён на 14 фаз, 089 orchestrator готов, state_migration.py удалён — cold-start only). DP-092 (scaffold) — желательно для `make deploy PROJECT=` сценария. Доступная пересоздаваемая test-VPS (инвариант 9). AGE_SECRET_KEY, SSH-доступ к test-VPS. Без test-VPS тесты FAIL (R4), не skip.
$END_ARTIFACT_CONTRACT

---

# DevPlan 095: E2E Bootstrap Pipeline Test

**Severity:** HIGH — критический gap test-coverage (0 E2E тестов на реальном окружении после Strangler-Fig), single point of failure `lib/ssh.sh` не покрыт на реальном SSH
**Created:** 2026-07-31
**Author:** Kilo (architect agent)
**Source:** Brief 095 (audit 2026-07-30), VR 091 (AC-B3 NOT_VERIFIABLE), 3 экспертизы (GAP-4)
**Sequenced:** AFTER DP-091 (STABLE), AFTER DP-092 (scaffold — желательно). Ортогонален DP-093/094 (не зависит).
**Pattern:** Test-only (zero production-code changes) + Strangler-Fig test infra (новый маркер ортогонален существующему `e2e`)

---

## §1. Current State

### 1.1 Test coverage audit (2026-07-31)

| Тип теста | Количество | Покрытие реального окружения | GAP |
|-----------|------------|------------------------------|-----|
| unit (`tests/unit/`) | 68+ | 0% — чистый Python, mock всё | — |
| static_audit (`tests/test_*.py`) | 50+ | 0% — schema/grep проверки | — |
| gate (`tests/gates/`) | 20+ | 0% — CI invariant checks | — |
| integration mocked (`tests/integration/`) | 12 | 0% — `test_bootstrap_dry_run.py` мокает subprocess, `test_deploy_e2e.py` использует `IntegrationMockChannel` | **GAP-4** |
| e2e HTTP (`tests/test_e2e_*.py`) | 4 | HTTP-only — проверяют grafana/langfuse/prometheus/loki endpoints `*.tronyx.ru` | Не покрывают bootstrap/deploy pipeline |
| **E2E VPS pipeline** | **0** | **0%** | **🔴 CRITICAL — закрытие этим планом** |

### 1.2 Ключевые находки (verificated)

1. **`test_bootstrap_dry_run.py` (1029 LOC, 8 тестов)** — полностью мокает `subprocess.run`, `os.geteuid()`, `os.path.expanduser()`, `_add_ssh_key`, `_ensure_projects_base`. Реальный SSH/Docker НИКОГДА не вызывается. Это unit-уровень despite `integration/` расположения.

2. **`test_deploy_e2e.py` (246 LOC, 4 теста)** — использует `IntegrationMockChannel(DeliveryChannel)` — mock-реализацию канала доставки. Реальный `scp`/`ssh` forced-command НИКОГДА не вызывается. DeployOrchestrator тестируется с mock-Docker (`contextlib.suppress(SystemExit)`).

3. **Маркер `e2e` уже занят** (pyproject.toml L23): `"e2e: manual end-to-end tests against external *.tronyx.ru (no Docker, dev-only)"`. Используется в `test_e2e_health.py`, `test_e2e_loki.py`, `test_e2e_litellm.py`, `test_e2e_prometheus.py`, `test_e2e_grafana_api.py`. **Конфликт** — нужен отдельный маркер.

4. **`lib/ssh.sh` — single point of failure** (TRAP[DECISION] в AGENTS.md). `ssh_exec`/`ssh_read` — единственный source of truth для всех remote-операций. `tests/test_lib_ssh.py` — статический (grep-based). E2E — единственный способ покрыть на реальном SSH. macOS dev-machine не имеет GNU `timeout` (TRAP DRIFT-note) — E2E должен запускаться на Linux test-VPS или CI runner.

5. **`resume_phase()` (state_machine.py:923) — никогда не тестировался на реальном отказе.** Dry-run тест `test_resume_phase_partial_failure` (L710) мокает `execute_phase` через `machine.execute_phase = _tracking_execute` — реальный `_execute_sub_step` не вызывается. Phase 7 (CERTIFICATES) имеет sub_steps `install_acme` + `ssl_provision` — идеальный кандидат для mid-phase kill теста.

6. **`bootstrap-node` сигнатура**: `make bootstrap-node NODE=<name>` → транслируется в `--node <name> --resolve`. Поддерживает `DRY_RUN=1`, `AUTO_RECONCILE=1`. `node-update` требует NODE (required). `converge NODE=<name> [RECONCILE=1]`.

7. **`node-configs/` не существует в репозитории** — конфигурации нод хранятся в `/opt/node-configs/` на VPS. Для test-data используется `tests/test_data/node.yaml`. Бrief требует `node-configs/test-e2e.yaml` — нужно создать директорию `node-configs/test-e2e/node.yaml` (по convention: `<node-name>/node.yaml`).

8. **VR 091 AC-B3** (`make bootstrap-node --mode init → 9 INIT фаз` (всего 14 фаз в pipeline: 9 INIT + 5 UPDATE, см. DevPlan 087)) — ⚠️ NOT_VERIFIABLE: "Требуется тестовая нода". AC-G1/G2 (`make gate MODE=fast`, `make check-manifests`) — NOT_VERIFIED из-за bash-политики. Этот DevPlan делает AC-B3 verifiable.

### 1.3 Constraints (из Brief + AGENTS.md)

| Constraint | Источник | Влияние на план |
|------------|----------|-----------------|
| Тестовая нода пересоздаваема (инвариант 9) | AGENTS.md | Не нужна backward-compat. Cold start с нуля. |
| ❌ НЕ тестировать миграцию state.json со старого формата | Brief User Constraint | state_migration.py удалён (Wave B 091) — test только cold-start + fresh-failure resume |
| ✅ Детерминированный (фиксированный test-project, 0 parameterized) | Brief Anti-Loop Note | Один canonical happy-path + 3 failure-сценария |
| E2E не входит в `make gate` / `make test MARKER=all` | Brief Verification | Новый `make test-node` target, отдельный маркер |
| NO_SERVICE = FAIL, не skip (Rule R4) | .kilo/rules/testing.md | `requires_node` fixture: нет NODE env → `pytest.fail`, не `pytest.skip` |

---

## §2. Target State

### 2.1 Новый pytest-маркер: `requires_node`

**Проблема:** существующий `e2e` = HTTP-проверки внешних сервисов. Новый E2E = pipeline на test-VPS. Смешивание создаст путаницу (R4 enforcement, gate-фильтры).

**Решение:** маркер `requires_node` — требует доступную test-VPS через `NODE` env var.

```python
# tests/e2e/conftest.py
@pytest.fixture
def requires_node() -> str:
    """Node fixture: returns NODE name. FAIL (not skip) if NODE env not set (Rule R4)."""
    node = os.environ.get("NODE")
    if not node:
        pytest.fail(
            "NODE environment variable not set. E2E pipeline tests require a test-VPS. "
            "Usage: make test-node NODE=test-e2e. "
            "Per Rule R4: environmental absence is a configuration error — surfaced, not hidden.",
            pytrace=False,
        )
    return node
```

**Маркер в `pyproject.toml`:**
```toml
markers = [
    # ... existing markers ...
    "requires_node: E2E pipeline tests against a recreatable test-VPS (needs NODE env, SSH, Docker)",
]
```

### 2.2 Makefile target: `test-node`

**Локация:** `makefiles/ci.mk` (рядом с существующим `test` target).

```makefile
.PHONY: test-node

## test-node: Run E2E pipeline tests against a test-VPS (requires NODE, AGE_SECRET_KEY, SSH access)
##   Usage: make test-node NODE=<name> [AGE_SECRET_KEY_FILE=<file>]
##   NOT included in `make test MARKER=all` or `make gate` — expensive, requires dedicated test-VPS
##   Per AGENTS.md invariant 9: test-VPS is recreatable — tests use cold-start only
test-node:
	@if [ -z "$(NODE)" ]; then \
		echo "[IMP:9][make][test-node] ERROR: NODE not set — usage: make test-node NODE=<name>" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][test-node] Running E2E pipeline tests NODE=$(NODE)..."
	PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/e2e/ -m "requires_node" -v --tb=short -rs \
		--junitxml=tests/report-node.xml
	@echo "[IMP:9][make][test-node] E2E pipeline tests complete NODE=$(NODE)"
```

**Регистрация:** `entrypoint-manifest.yaml#allowed_verbs` → `test-node` (иначе gate `test_all_makefile_targets_in_allowed_verbs` RED).

### 2.3 Test taxonomy

| Файл | Маркер | Сценарии | Docker | SSH | VPS |
|------|--------|----------|--------|-----|-----|
| `tests/e2e/test_bootstrap_pipeline.py` | `requires_node` | 8 happy-path | ✅ | ✅ | ✅ |
| `tests/e2e/test_failure_scenarios.py` | `requires_node` | 3 failure | ✅ | ✅ | ✅ |
| `tests/e2e/conftest.py` | — | fixtures | — | — | — |

**Исключение из `make test MARKER=all`:** фильтр `-m "not requires_node"` добавляется в canonical order (ci.mk:32, 39, 140) — по аналогии с существующим `not e2e`.

---

## §3. Draft Code Graph

```
tests/e2e/__init__.py                                   [CREATE] — package marker (обязателен для загрузки conftest.py)

tests/e2e/conftest.py                                   [CREATE] — fixtures: requires_node, node_ssh, node_state
    ├── requires_node() -> str                          — FAIL if NODE env missing (R4)
    ├── node_ssh(requires_node) -> NodeSSHClient        — wrapper over lib/ssh.sh ssh_exec/ssh_read
    ├── node_state(node_ssh) -> NodeState               — read/reset state.json, check phase done
    ├── test_vps_fresh(node_state) -> None              — session-scoped: reset state.json before suite
    └── test_project_fixture() -> str                   — canonical test-project name

tests/e2e/test_bootstrap_pipeline.py                    [CREATE] — 8 happy-path scenarios
    ├── test_cold_start_bootstrap_9_phases              — make bootstrap-node → 9 INIT фаз done
    ├── test_update_mode_5_phases                       — make node-update → 5 UPDATE фаз done
    ├── test_converge_idempotent                        — make converge → desired state, exit 0/1
    ├── test_deploy_test_project                        — make deploy PROJECT= → контейнер running
    ├── test_healthcheck_all_healthy                    — make healthcheck → все модули healthy
    ├── test_backup_creates_snapshot                    — make backup → backup artifact существует
    ├── test_restore_roundtrip                          — backup → destroy → restore → данные восстановлены
    └── test_pipeline_idempotent_rebootstrap            — повторный bootstrap → все фазы skip (done)

tests/e2e/test_failure_scenarios.py                     [CREATE] — 3 failure scenarios
    ├── test_resume_phase7_after_midphase_kill          — kill docker mid-φ7 → resume → ssl_provision retry
    ├── test_ssh_read_timeout_graceful_error            — simulate SSH timeout → graceful error (TRAP lib/ssh.sh)
    └── test_deploy_forced_command_receive              — orchestrator_cli receive через SSH forced-command

tests/_conftest/node.py                                 [CREATE] — NodeSSHClient, NodeState helpers
    ├── class NodeSSHClient                             — ssh_exec/ssh_read wrapper, parse exit codes
    ├── class NodeState                                 — read_state(), reset_state(), mark_phase_done()
    └── assert_ldd_imp9_e2e(caplog)                     — LDD trajectory assertion for E2E

tests/e2e/fixtures/test-project/                        [CREATE] — canonical test-project
    ├── docker-compose.yml                              — nginx:alpine, port 8080
    ├── ai-platform.yaml                                — project: test-project, type: backend
    └── .env.platform                                   — ENV=test, минимальный набор

tests/e2e/README.md                                     [CREATE] — документация test-VPS preparation, env, troubleshooting

node-configs/test-e2e/node.yaml                         [CREATE] — node config для test-VPS
    └── node.name=test-e2e, host, owner_key, domain, modules=[], projects=[test-project]

pyproject.toml                                          [MODIFY] — добавить маркер requires_node
makefiles/ci.mk                                         [MODIFY] — добавить test-node target + not requires_node в фильтры
core/entrypoint-manifest.yaml                           [MODIFY] — allowed_verbs += test-node
tests/AGENTS.md                                         [MODIFY] — taxonomy entry для tests/e2e/
```

---

## §4. Wave Structure

### Wave 1: Test Infrastructure — маркер, target, fixtures

| Task | Описание | Effort |
|------|----------|--------|
| **T1** | Добавить маркер `requires_node` в `pyproject.toml [tool.pytest.ini_options] markers`. Описание: `"requires_node: E2E pipeline tests against a recreatable test-VPS (needs NODE env, SSH, Docker)"`. Gate `test_pyproject_toml_has_all_markers` должен остаться зелёным. | 1 |
| **T2** | Добавить `test-node` target в `makefiles/ci.mk` (см. §2.2). Зарегистрировать в `core/entrypoint-manifest.yaml#allowed_verbs` (иначе gate RED). Обновить фильтры `not e2e` → `not e2e and not requires_node` в ci.mk:32, 39, 140 (static/test runs). | 2 |
| **T3** | Создать `tests/_conftest/node.py`: `NodeSSHClient` (wrapper над `core/lib/ssh.sh` через subprocess с timeout), `NodeState` (read/reset state.json через SSH, check phase done status), `assert_ldd_imp9_e2e(caplog)`. NodeSSHClient использует `ssh_exec(host, user, cmd, timeout)` сигнатуру lib/ssh.sh. | 3 |
| **T4** | Создать `tests/e2e/__init__.py` (package marker — обязателен для загрузки conftest.py) и `tests/e2e/conftest.py`: `requires_node` fixture (FAIL not skip per R4), `node_ssh` (session-scoped NodeSSHClient), `node_state` (session-scoped NodeState), `test_vps_fresh` (session-scoped autouse: reset state.json через `bootstrap-node --force` перед suite), `test_project_fixture` (canonical test-project name). | 2 |
| **T5** | Создать `node-configs/test-e2e/node.yaml` — node config для test-VPS: node.name=test-e2e, host, owner_key (placeholder, реальный ключ из env), domain=test-e2e.local, modules=[], projects=[{name: test-project, domain, type: backend}]. | 1 |

### ⚠️ IMPLEMENTATION NOTE: requires_node — и маркер, и параметр

Каждый E2E тест ДОЛЖЕН иметь **оба**:
1. `@pytest.mark.requires_node` — для `-m "requires_node"` селекции (marker в pyproject.toml)
2. `requires_node` как параметр функции — для инъекции NODE имени

Без декоратора тест не будет выбран `-m "requires_node"`. Без параметра фикстура `requires_node()` не вызовется — проверка NODE не выполнится и тест упадёт с непонятной ошибкой.

```python
@pytest.mark.requires_node
def test_example(requires_node: str, node_ssh: NodeSSHClient) -> None:
    ...
```

**Verify Wave 1:**
```bash
make gate MODE=fast                                    # gate зелёный (маркер зарегистрирован)
python3 -c "import pytest; print(pytest.mark.requires_node)"  # маркер импортируется
make test-node NODE=test-e2e --dry-run 2>&1 | grep ERROR  # ERROR: NODE required если без NODE
```

### Wave 2: Happy-path scenarios (8 тестов)

| Task | Описание | Effort |
|------|----------|--------|
| **T6** | `test_cold_start_bootstrap_9_phases`: `make bootstrap-node NODE=$(node)` → проверить через `node_state.read_state()` что все 9 INIT фаз (φ1-φ8.5) имеют `done=true`. Проверить IMP:9 лог в stdout. AC-B3 из VR 091 теперь verifiable (декомпозиция 14 фаз: 9 INIT φ1-φ8.5 + 5 UPDATE φ9-φ13, см. DevPlan 087 — T6 покрывает INIT-часть, T7 — UPDATE-часть). | 2 |
| **T7** | `test_update_mode_5_phases`: `make node-update NODE=$(node)` → проверить 5 UPDATE фаз (φ9-φ13) done. Запускается ПОСЛЕ T6 (зависимость: INIT выполнен). | 1 |
| **T8** | `test_converge_idempotent`: `make converge NODE=$(node)` → exit code 0 (clean) или 1 (warnings). Проверить desired state достигнут (проекты из node.yaml запущены). Повторный converge → тот же результат (idempotent). | 1 |
| **T9** | `test_deploy_test_project`: `make deploy PROJECT=test-project NODE=$(node)` → проверить через `docker ps` (через node_ssh) что контейнер test-project running. Это тестирует DeployOrchestrator через ForcedCommandChannel end-to-end (AC10 из 089). | 2 |
| **T10** | `test_healthcheck_all_healthy`: `make healthcheck NODE=$(node)` → все модули healthy. Проверить через `docker inspect` health status. | 1 |
| **T11** | `test_backup_creates_snapshot`: `make backup NODE=$(node)` (или module-level `make backup MODULE=<m>`) → проверить что backup artifact создан в backup storage. Записать artifact path для T12. | 1 |
| **T12** | `test_restore_roundtrip`: использует backup из T11 → destroy test-project (`docker compose down -v`) → `make restore NODE=$(node)` → проверить что данные восстановлены (HTTP 200 на test-project endpoint или запись в БД существует). | 2 |
| **T13** | `test_pipeline_idempotent_rebootstrap`: повторный `make bootstrap-node NODE=$(node)` → все 9 INIT фаз skip (state.json уже done). Проверить через IMP:8 лог "SKIP sub_step". Тестирует grouped-phase skip logic на реальном окружении. | 1 |

**Verify Wave 2:**
```bash
make test-node NODE=test-e2e -k "bootstrap_pipeline" -v
# Ожидание: 8 PASSED (если test-VPS подготовлена)
```

### Wave 3: Failure scenarios (3 теста)

| Task | Описание | Effort |
|------|----------|--------|
| **T14** | `test_resume_phase7_after_midphase_kill`: 1) Сбросить φ7 в state.json (done=false), 2) запустить `bootstrap-node` в фоне, 3) во время φ7 (certificates — install_acme) kill docker daemon на VPS (`systemctl stop docker`), 4) дождаться fail фазы, 5) `systemctl start docker`, 6) повторный `bootstrap-node` → φ7 resume: install_acme skip (done+unchanged), ssl_provision execute → done. Проверить через `resume_phase()` IMP:8 лог. **⚠️ TRAP:** kill docker = destructive, нужен `test_vps_fresh` reset после теста. | 3 |
| **T15** | `test_ssh_read_timeout_graceful_error`: simulate SSH timeout — `ssh_read` с `timeout=1` на медленную команду (`sleep 5`). Проверить что exit code = 124 (timeout per lib/ssh.sh L32), graceful error message (не hang, не crash). Тестирует TRAP[DECISION] lib/ssh.sh staging-gate на реальном SSH. **⚠️ TRAP:** macOS dev-machine не имеет GNU `timeout` — тест ДОЛЖЕН запускаться на Linux test-VPS/CI runner. | 2 |
| **T16** | `test_deploy_forced_command_receive`: CI-equivalent deploy — собрать payload tar локально, доставить через `ssh node "python3 -m core.internal.deploy.orchestrator_cli receive test-e2e"` (forced-command), проверить что orchestrator_cli receive работает, DeployResult JSON в stdout, exit 0. Тестирует T6.6 из 089 на реальном SSH forced-command. | 2 |

**Verify Wave 3:**
```bash
make test-node NODE=test-e2e -k "failure_scenarios" -v
# Ожидание: 3 PASSED
```

### Wave 4: Documentation + Gate verification

| Task | Описание | Effort |
|------|----------|--------|
| **T17** | Создать `tests/e2e/README.md`: (1) подготовка test-VPS (OS, Docker, SSH key, AGE key), (2) переменные окружения (NODE, AGE_SECRET_KEY, SSH key path), (3) как запускать (`make test-node NODE=test-e2e`), (4) troubleshooting (SSH timeout, Docker kill recovery, state.json reset), (5) что НЕ покрывает (production deploys, real ACME certs). | 2 |
| **T18** | Создать `tests/e2e/fixtures/test-project/`: `docker-compose.yml` (nginx:alpine, port 8080), `ai-platform.yaml` (project: test-project, service: web, version: v1.0.0, type: backend), `.env.platform` (ENV=test, минимальный набор). Canonical fixture — детерминированный per Anti-Loop Note. | 1 |
| **T19** | Обновить `tests/AGENTS.md` Directory Taxonomy: добавить `tests/e2e/` — "E2E pipeline tests against test-VPS (requires_node marker, make test-node)". NOT в `make test MARKER=all`. | 1 |
| **T20** | `make fix-gate && make gate MODE=fast` — зелёный. Проверить: новый маркер в pyproject.toml проходит gate `test_pyproject_toml_has_all_markers`, новый target `test-node` в allowed_verbs проходит gate `test_all_makefile_targets_in_allowed_verbs`, фильтр `not requires_node` в ci.mk не ломает существующие test runs. | 1 |

---

## §4.1 Fixtures Architecture

### Session-scoped vs function-scoped

| Fixture | Scope | Autouse | Назначение |
|---------|-------|---------|------------|
| `requires_node` | function | no | Возвращает NODE name, FAIL если env missing |
| `node_ssh` | session | no | NodeSSHClient instance (SSH connection pool) |
| `node_state` | session | no | NodeState instance (state.json reader/writer) |
| `test_vps_fresh` | session | **yes** | Reset state.json перед suite (cold start per инвариант 9) |
| `test_project_fixture` | session | no | Canonical test-project name ("test-project") |

### test_vps_fresh (autouse, session)

```python
@pytest.fixture(scope="session", autouse=True)
def test_vps_fresh(node_ssh: NodeSSHClient, requires_node: str) -> None:
    """Reset test-VPS to clean state before E2E suite.

    Per AGENTS.md invariant 9: test-VPS is recreatable — cold start only.
    Runs `make bootstrap-node NODE=$(node) --force` which clears state.json
    (state_machine.py:1333 — [IMP:9][main] --force: Clearing state).
    """
    # --force clears state.json → all phases re-execute
    result = node_ssh.ssh_exec(f"make bootstrap-node NODE={requires_node} --force", timeout=600)
    assert result.exit_code == 0, f"Fresh bootstrap failed: {result.stderr}"
```

**⚠️ TRAP[DECISION] · 2026-07-31 · HI · Session-scoped autouse для cold start**
· Rejected: function-scoped reset (risk: каждый тест пересоздаёт state → 11×600s = 2+ часа)
· Reason: Session-scoped reset даёт 1 cold start (~10min) + 11 incremental тестов (~5min каждый). Тесты упорядочены по pipeline-flow (bootstrap → converge → deploy → healthcheck → backup → restore).
· Rev: если тесты начинают мешать друг другу (state leak) → перейти на function-scoped с reset только state.json (не полный rebootstrap).

---

## §5. File Manifest

### CREATE (10)
| Файл | Назначение |
|------|-----------|
| `tests/e2e/__init__.py` | Package marker |
| `tests/e2e/conftest.py` | Fixtures: requires_node, node_ssh, node_state, test_vps_fresh (autouse session), test_project_fixture |
| `tests/e2e/test_bootstrap_pipeline.py` | 8 happy-path E2E тестов (T6-T13) |
| `tests/e2e/test_failure_scenarios.py` | 3 failure-сценария (T14-T16) |
| `tests/_conftest/node.py` | NodeSSHClient, NodeState, assert_ldd_imp9_e2e helpers |
| `tests/e2e/fixtures/test-project/docker-compose.yml` | Canonical test-project compose |
| `tests/e2e/fixtures/test-project/ai-platform.yaml` | Canonical test-project metadata |
| `tests/e2e/fixtures/test-project/.env.platform` | Canonical test-project env |
| `tests/e2e/README.md` | Документация test-VPS preparation, env, troubleshooting |
| `node-configs/test-e2e/node.yaml` | Node config для test-VPS |

### MODIFY (4)
| Файл | Изменение |
|------|-----------|
| `pyproject.toml` | `markers += "requires_node: E2E pipeline tests against a recreatable test-VPS (needs NODE env, SSH, Docker)"` |
| `makefiles/ci.mk` | (1) Добавить `test-node` target (§2.2). (2) Обновить фильтры: `not e2e` → `not e2e and not requires_node` в строках static (32, 39), gate static (140). (3) Добавить `test-node` в `.PHONY`. |
| `core/entrypoint-manifest.yaml` | `allowed_verbs += test-node` (gate `test_all_makefile_targets_in_allowed_verbs`) |
| `tests/AGENTS.md` | Directory Taxonomy: добавить `tests/e2e/` row |

### DELETE (0)
Нет удалений — план только добавляет test-артефакты.

---

## §6. Acceptance Criteria (Detailed)

- [ ] **AC1:** `grep "requires_node" pyproject.toml` → 1 match в секции markers. Gate `test_pyproject_toml_has_all_markers` PASS.
- [ ] **AC2:** `grep "^test-node:" makefiles/ci.mk` → 1 match. `grep "test-node" core/entrypoint-manifest.yaml` → в allowed_verbs. `make test-node` без NODE → ERROR exit 1.
- [ ] **AC3:** `grep "pytest.fail" tests/e2e/conftest.py` → в `requires_node` fixture. При `NODE` unset → FAIL (не skip).
- [ ] **AC4:** `make test-node NODE=test-e2e -k "bootstrap_pipeline" -v` → 8 PASSED (после подготовки test-VPS).
- [ ] **AC5:** `make test-node NODE=test-e2e -k "failure_scenarios" -v` → 3 PASSED.
- [ ] **AC6:** `grep "parametrize" tests/e2e/` → 0 matches (детерминированный, 0 matrix per Anti-Loop Note).
- [ ] **AC7:** Каждый тест-файл имеет `assert found_imp9` (LDD trajectory assertion). `grep -c "IMP:9" tests/e2e/*.py` → ≥1 per test function.
- [ ] **AC8:** `ls tests/e2e/README.md` → exists. Содержит секции: "Test-VPS Preparation", "Environment Variables", "Running Tests", "Troubleshooting".
- [ ] **AC9:** `make gate MODE=fast` → зелёный (E2E не входит: фильтр `not requires_node`).
- [ ] **AC10:** `grep '"e2e:' pyproject.toml` → unchanged ("manual end-to-end tests against external *.tronyx.ru").
- [ ] **AC11:** `ls tests/e2e/fixtures/test-project/{docker-compose.yml,ai-platform.yaml,.env.platform}` → все 3 файла существуют.
- [ ] **AC12:** `make test MARKER=all` → не запускает requires_node тесты (фильтр updated).
- [ ] **AC13:** VR 091 AC-B3 теперь verifiable: `test_cold_start_bootstrap_9_phases` → 9 INIT фаз done на test-VPS.

---

## §7. Design Decisions

### DD1: Почему новый маркер `requires_node`, а не расширение существующего `e2e`?

Существующий маркер `e2e` (pyproject.toml:23) = "manual end-to-end tests against external *.tronyx.ru (no Docker, dev-only)". Используется 4 тестами (`test_e2e_health.py`, `test_e2e_loki.py`, и др.) для HTTP-проверок Grafana/Langfuse/Prometheus/Loki. Эти тесты:
- Не требуют SSH/Docker на VPS
- Целят внешние домены `*.tronyx.ru`
- Используют `_conftest/e2e.py` (`_handle_e2e_error`, `_load_test_env`)

E2E pipeline тесты принципиально другие:
- Требуют SSH + Docker на test-VPS
- Целят bootstrap/deploy pipeline (не HTTP endpoints)
- Используют `_conftest/node.py` (новый)

Смешивание создаст: (1) путаницу в `make test MARKER=e2e` (HTTP vs pipeline), (2) поломку gate-фильтров (`not e2e` исключит pipeline тесты из CI по ошибке), (3) нарушение R4 enforcement (разные NO_SERVICE семантики). Маркер `requires_node` ортогонален и явно выражает зависимость.

### DD2: Почему `make test-node`, а не расширение `make test MARKER=e2e`?

`make test MARKER=e2e` уже существует и запускает HTTP-проверки. Расширение его pipeline-тестами сделает target неоднозначным. Новый `make test-node NODE=<name>`:
- Явно выражает зависимость от test-VPS (NODE required)
- Не входит в canonical order `make test MARKER=all` (expensive, ~30min)
- Не входит в `make gate` (per Brief: e2e = manual/expensive)
- Имеет явную регистрацию в allowed_verbs (иначе gate RED)

### DD3: Почему session-scoped autouse fixture для cold start, а не function-scoped?

Function-scoped reset = каждый из 11 тестов пересоздаёт state.json + полный rebootstrap (~10min × 11 = ~2 часа). Session-scoped = 1 cold start (~10min) + 11 incremental тестов (~5min каждый, ~55min total). Тесты упорядочены по pipeline-flow: bootstrap → converge → deploy → healthcheck → backup → restore. Это имитирует реальный lifecycle ноды.

**Риск:** state leak между тестами. **Mitigation:** (1) `test_pipeline_idempotent_rebootstrap` (T13) валидирует idempotency в конце, (2) failure-сценарии (T14-T16) имеют собственный reset через `node_state.reset_phase(phase)`, (3) `test_vps_fresh` можно переключить на function-scoped если обнаружится leak (см. TRAP в §4.1).

### DD4: Почему failure-сценарий φ7 (certificates), а не φ4 (secrets_provision) как в dry-run тесте?

Dry-run тест (`test_resume_phase_partial_failure`, L710) использует φ4 (secrets_provision: decrypt_secrets + ensure_secrets + secrets_init). Но φ4 на реальной VPS требует AGE-ключ и реальные секреты — сложно симулировать частичный отказ детерминированно.

φ7 (certificates) имеет 2 sub_steps: `install_acme` + `ssl_provision`. `install_acme` — установка acme.sh (детерминированная). `ssl_provision` — выпуск сертификатов через ACME (зависит от Let's Encrypt rate limits, DNS propagation — НЕдетерминированная). **Mid-phase kill:** `kill docker` во время φ7 → фаза падает → resume: `install_acme` skip (done+unchanged), `ssl_provision` retry. Это тестирует реальный resume на детерминированной части (install_acme) без ACME flakiness.

**Альтернатива рассмотренная:** kill во время φ8 (deploy_services). Отвергнута: φ8 деплоит контейнеры — kill docker во время deploy оставляет half-deployed state, который сложнее очистить.

### DD5: Почему `tests/e2e/`, а не `tests/integration/e2e/` или `tests/test_e2e_pipeline.py`?

Per `tests/AGENTS.md` Directory Taxonomy: `tests/integration/` = "full hermes LLM stack (needs Docker)" — это mocked-integration (`test_bootstrap_dry_run.py`, `test_deploy_e2e.py`). Реальный E2E на VPS — концептуально другой уровень (нужна test-VPS, не просто Docker). `tests/e2e/` — отдельная категория, явно отделённая от mocked-integration. Соответствует convention: `tests/test_e2e_*.py` уже существует для HTTP-проверок, но pipeline-тесты логически группируются в поддиректории (conftest + fixtures + multiple test files).

### DD6: Почему `node-configs/test-e2e/node.yaml`, а не в `tests/test_data/`?

`tests/test_data/node.yaml` — fixture для static/gate тестов (schema validation, predeploy). `node-configs/test-e2e/node.yaml` — реальная конфигурация для test-VPS, которая будет использоваться при `make bootstrap-node NODE=test-e2e` (bootstrap.sh резолвит node config через `--resolve` из `node-configs/<name>/node.yaml`). Это не test-data — это infra-config. Convention: `/opt/node-configs/<name>/node.yaml` на VPS, `node-configs/<name>/node.yaml` в репозитории для тестовых нод.

---

## §8. Implementation Commands

```bash
# === WAVE 1: Test Infrastructure ===
coder implement DevPlan 095 Wave 1:
  T1 (requires_node marker in pyproject.toml),
  T2 (test-node target in ci.mk + allowed_verbs + filter updates),
  T3 (tests/_conftest/node.py — NodeSSHClient, NodeState),
  T4 (tests/e2e/conftest.py — fixtures),
  T5 (node-configs/test-e2e/node.yaml)

# Verify Wave 1
make gate MODE=fast
python3 -c "import pytest; pytest.mark.requires_node"
make test-node 2>&1 | grep "ERROR: NODE not set"

# === WAVE 2: Happy-path scenarios ===
coder implement DevPlan 095 Wave 2:
  T6 (test_cold_start_bootstrap_9_phases),
  T7 (test_update_mode_5_phases),
  T8 (test_converge_idempotent),
  T9 (test_deploy_test_project),
  T10 (test_healthcheck_all_healthy),
  T11 (test_backup_creates_snapshot),
  T12 (test_restore_roundtrip),
  T13 (test_pipeline_idempotent_rebootstrap)

# Verify Wave 2 (требует подготовленную test-VPS)
make test-node NODE=test-e2e -k "bootstrap_pipeline" -v

# === WAVE 3: Failure scenarios ===
coder implement DevPlan 095 Wave 3:
  T14 (test_resume_phase7_after_midphase_kill),
  T15 (test_ssh_read_timeout_graceful_error),
  T16 (test_deploy_forced_command_receive)

# Verify Wave 3
make test-node NODE=test-e2e -k "failure_scenarios" -v

# === WAVE 4: Documentation + Gate ===
coder implement DevPlan 095 Wave 4:
  T17 (tests/e2e/README.md),
  T18 (tests/e2e/fixtures/test-project/ — 3 files),
  T19 (tests/AGENTS.md taxonomy update),
  T20 (make fix-gate && make gate MODE=fast)

# Final verification
make fix-gate && git add -u && make gate MODE=fast
make test MARKER=static -v   # requires_node не запускается (фильтр not requires_node)
```

---

## §9. Test-VPS Preparation Checklist (for README.md)

```bash
# 1. Provision test-VPS (пересоздаваемая per инвариант 9)
ssh root@test-e2e.vps "uname -a && docker --version"

# 2. Install platform core (SCP, not git — per Triple Delivery Model)
make bootstrap-node NODE=test-e2e --force

# 3. Set environment variables
export NODE=test-e2e
export AGE_SECRET_KEY_FILE=~/.config/age/keys/test-e2e.key
export SSH_KEY=~/.ssh/test-e2e_ed25519

# 4. Run E2E suite
make test-node NODE=test-e2e

# 5. After suite — recreate test-VPS for next run (инвариант 9)
# (manual: terraform destroy && terraform apply, or provider-specific)
```

---

## §10. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| test-VPS недоступна во время CI → все E2E FAIL (R4) | MEDIUM | Принять: per R4 environmental absence = FAIL. CI должен иметь dedicated test-VPS. Альтернатива: `@pytest.mark.skipif(not os.environ.get("CI_E2E_ENABLED"))` — НО это нарушает R4. Решение: E2E в отдельном CI job (не в gate). |
| `kill docker` в T14 оставляет VPS в broken state | HIGH | `test_vps_fresh` autouse session fixture не покроет (session scope). Решение: T14 имеет собственный cleanup (`systemctl start docker` + `make bootstrap-node NODE=test-e2e --force` в finally блоке). |
| ACME rate limits в φ7 (T14) | MEDIUM | φ7 test использует `install_acme` (детерминированная установка), НЕ `ssl_provision` (ACME challenge). Если ssl_provision всё же вызывается → использовать staging ACME endpoint (Let's Encrypt staging). |
| SSH timeout в T15 на macOS dev-machine (нет GNU timeout) | LOW | Тест ДОЛЖЕН запускаться на Linux test-VPS/CI runner. Зафиксировать в README.md. TRAP из AGENTS.md lib/ssh.sh DRIFT-note. |
| State leak между тестами (session-scoped fixtures) | MEDIUM | T13 (idempotent rebootstrap) валидирует в конце. Если leak обнаружен → переключить test_vps_fresh на function-scoped (TRAP в §4.1). |
| `make test-node` не зарегистрирован в allowed_verbs → gate RED | HIGH | T2 явно регистрирует. T20 verifies `make gate MODE=fast` зелёный. |

---

$END_DEVPLAN
