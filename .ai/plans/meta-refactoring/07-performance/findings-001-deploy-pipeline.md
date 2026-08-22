# Findings 001 — Deploy pipeline (bootstrap/deploy + internal/deploy)

Scope: `core/internal/bootstrap/deploy/`, `core/internal/deploy/` · Agent wave 1 · 2026-08-22

### PERF-001 | HIGH | conf=High
- Category: nested polling windows (fixed-sleep loops)
- Hot path: yes — every CI `git push` deploy (receive → DeployOrchestrator.deploy) и каждый deploy-context
- File/symbol: `core/internal/deploy/healthcheck_poller.py::HealthcheckPoller.poll_until_healthy` (+ caller `orchestrator.py::_verify_deploy`)
- Trigger: проект не healthy на первом probe (ровно launch-week failure сценарий)
- Complexity/cost: outer loop max_retries=20 × inner full-window polls: HTTP probe ≤60s + `_try_docker` → `healthcheck_poll(timeout=60)` + `sleep(3)` ⇒ до ~123s/attempt ≈ **41 мин/проект**; docstring обещает "Total poll window = interval × max_retries" (=60s), фактически 20–40× больше
- Expected impact: один unhealthy проект блокирует deploy (и всю последовательную батч-очередь, см. PERF-004) на 20–40 мин вместо 60s; N failing проектов сериализуются в часы
- Evidence: `core/internal/deploy/healthcheck_poller.py:143-157`; `core/internal/deploy/orchestrator.py:544`
  ```python
  for attempt in range(1, self.max_retries + 1):
      result = self.poll_project(project_name, project_dir)
      ...
      time.sleep(self.interval)
  ```
- Minimal fix: сделать `poll_project` single-shot (без внутреннего 60s-окна) — внешний retry-loop владеет всем бюджетом; либо `max_retries=1` при делегировании внутреннему docker-poll
- Measurement: deploy wall time per project (`duration_s` в OrchestratorDeployResult) на заведомо unhealthy проекте
- Phase: Pre-launch

### PERF-002 | HIGH | conf=High — ⚠️ correctness-adjacent (rollback не срабатывает)
- Category: exit status игнорируется → silent no-rollback
- Hot path: yes — когда модуль группы падает в финальном drain (последние ≤4 детей при parallel_limit=4)
- File/symbol: `core/internal/bootstrap/deploy/parallel_runner.py::drain_all_count`
- Trigger: модуль exit≠0 в финальном drain — статистически частый случай (падают обычно самые медленные)
- Complexity/cost: `waitpid` status отбрасывается, **каждый** завершившийся ребёнок считается `deployed += 1`; `group_failed` остаётся 0 ⇒ атомарный rollback (`docker compose down` группы, W5-E1) не срабатывает, `failed_names` пуст ⇒ агрегация репортит успех
- Expected impact: упавший модуль остаётся сломанным при exit 0; launch-blocking observability/rollback gap на пути, помеченном "atomic rollback"
- Evidence: `parallel_runner.py:498-507`
  ```python
  _ = os.waitpid(pids[i], 0)
  mod_name = pid_to_name.pop(pids[i], "?")
  # Success — waitpid returned without error means process exited
  deployed += 1
  ```
  Контраст: `drain_completed_count` (`:467-475`) корректно проверяет `WEXITSTATUS`
- Minimal fix: в `drain_all_count` проверять `os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0` как в `drain_completed_count`
- Measurement: chaos-тест — падение последнего модуля группы >4; assert `group_failed>0` и rollback выполнен (сейчас RED)
- Phase: Pre-launch

### PERF-003 | HIGH | conf=High
- Category: fixed 60s sleep на холодный проект (skip-gate)
- Hot path: yes — холодный bootstrap φ8 / первый deploy-context на новой ноде
- File/symbol: `core/internal/bootstrap/deploy/context_deployer.py::_is_project_healthy` (вызывается из `_deploy_single_project_via_orchestrator` ДО проверки существования compose-файла)
- Trigger: у проекта ещё нет контейнеров — `docker ps --filter name=X` пуст, poller спит весь window прежде чем ответить "not healthy"
- Complexity/cost: `healthcheck_poll(timeout=60, interval=3)` на пустом списке = 20 итераций × `docker ps` + `sleep(3)` = **60s впустую на каждый ещё-не-задеплоенный проект**; healthy-skip проверка идёт раньше проверки `docker-compose.yml` (`:376` перед `:386-389`) — поллинг контейнера, который не может существовать
- Expected impact: первый bootstrap из P проектов добавляет P×60s чистого сна (10 проектов ≈ +10 мин к φ8) до начала реальной работы
- Evidence: `context_deployer.py:716-719`
  ```python
  return (
      healthcheck_poll(project_name, timeout=HEALTHCHECK_POLL_TIMEOUT, interval=HEALTHCHECK_POLL_INTERVAL)
      == "healthy"
  )
  ```
- Minimal fix: single-shot проверка существования контейнера (`docker ps` один раз) для skip-gate; polling window — только для пост-деплой верификации
- Measurement: φ8 wall time на холодной ноде; число `docker ps` на проект
- Phase: Pre-launch

### PERF-004 | HIGH | conf=High
- Category: последовательный fan-out при существующем механизме параллелизма
- Hot path: yes — bootstrap φ8/φ12 (node-update), `make deploy-context`, deploy-many
- File/symbol: `core/internal/deploy/orchestrator.py::DeployOrchestrator.deploy_many`; `context_deployer.py::deploy_context_projects`; `deploy_orchestrator.py::_route_deploy/_deploy_parallel`
- Trigger: >1 проект/модуль в deploy-ране
- Complexity/cost: три слоя сериализации: (a) default `DEPLOY_PARALLEL=false` гоняет ~21 модуль через for-loop (`deploy_orchestrator.py:428-429`); (b) при `DEPLOY_ORCHESTRATOR=true` ОДИН subprocess deploy-many **заменяет** fork-parallel group runner (`:510-519`); (c) `deploy_many` — явный последовательный for-loop (`orchestrator.py:666-674`). Каждый юнит платит pull-retry (backoff до ~135s worst), compose up и ДВЕ health-gate фазы (PERF-005)
- Expected impact: bootstrap/node-update wall time = Σ(юниты) вместо ÷parallel_limit; с одним failing проектом (PERF-001) легко 30–60+ мин; риск CI-timeout в launch week
- Evidence: `orchestrator.py:641,666-667` — `# Projects are deployed sequentially (not parallel)`; `context_deployer.py:499-509`; `orchestrator_cli.py:174` `"Deploy multiple projects sequentially"`
- Minimal fix: прогонять `deploy_many` юниты через bounded worker pool (fork+slot паттерн уже доказан в `parallel_runner`), сохраняя topo-порядок зависимостей
- Measurement: end-to-end `make node-update` / φ8 duration vs число модулей
- Phase: Pre-launch

### PERF-005 | MED | conf=High
- Category: repeated computation — двойной health-gate на деплой
- Hot path: yes — каждый успешный деплой платит дважды
- File/symbol: `core/internal/deploy/engine/engine.py::DeployEngine.deploy` (`wait_health`) vs `orchestrator.py::_verify_deploy` (HealthcheckPoller)
- Trigger: каждый `DeployOrchestrator.deploy()` → engine.deploy() (внутреннее 60s-окно) → затем `_verify_deploy` → `poll_until_healthy` снова
- Complexity/cost: 2 полных polling-фазы на деплой; контракт HealthcheckPoller заявляет устранение именно этой дупликации ("DevPlan 089 DD4: ...both do healthcheck → double work"), но engine по-прежнему гейтит внутри
- Expected impact: +5–15s на каждый healthy деплой; удваивает штраф PERF-001 на unhealthy
- Evidence: `engine.py:240-241`, `engine/flow.py:92-94`, `orchestrator.py:544`
- Minimal fix: engine.deploy возвращает health verdict, оркестратор переиспользует (или убрать engine-side gate)
- Measurement: число docker-процессов на деплой / `duration_s` breakdown
- Phase: Pre-launch

### PERF-006 | MED | conf=High
- Category: последовательная блокирующая разборка на failure-пути (rollback)
- Hot path: no — только при падении group-deploy (инцидентные условия, когда скорость важнее всего)
- File/symbol: `core/internal/bootstrap/deploy/parallel_runner.py::deploy_docker_group` (atomic rollback block)
- Trigger: `group_failed > 0` → per-module последовательный `docker compose down` с DOCKER_STOP_TIMEOUT каждый, затем healthchecks перефоркиваются для ВСЕХ модулей включая откатанные
- Complexity/cost: G модулей × stop-timeout (serial) + G healthchecks; группа из 10 с 10s timeout ≈ 100s+ teardown до первого сигнала оператору
- Expected impact: минуты к каждому failed-deploy recovery; компаундится с PERF-001/004 в том же окне
- Evidence: `parallel_runner.py:360-371`
- Minimal fix: rollback `compose down` тем же fork/slot паттерном (или один объединённый `docker compose down`)
- Measurement: время от первого падения модуля до возврата функции на группе из G модулей
- Phase: Pre-launch

### PERF-007 | LOW | conf=High
- Category: unbounded temp-file growth (leak)
- Hot path: yes — каждый `deploy()` создаёт и никогда не удаляет
- File/symbol: `core/internal/deploy/payload_deliverer.py::PayloadDeliverer.assemble_payload`
- Trigger: каждый деплой пишет `payload-<project>-*.tar.gz` через `tempfile.mkstemp` в /tmp; unlink/rmtree по `core/internal/deploy/` отсутствует
- Complexity/cost: O(deploys) файлов; мелкие (KB), но unbounded на long-lived VPS; если /tmp = tmpfs — это RAM
- Expected impact: минорный disk/RAM creep; релевантно через месяцы частых деплоев
- Evidence: `payload_deliverer.py:170-178`
- Minimal fix: удалять `payload.tar_path` в `finally` вокруг `_apply_deploy`
- Measurement: `ls /tmp/payload-* | wc -l` на staging за день CI-деплоев
- Phase: Post-launch
