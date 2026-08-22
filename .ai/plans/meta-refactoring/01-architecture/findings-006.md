# Направление 6 — Infrastructure ↔ application/domain coupling

Метод: rg subprocess/docker/ssh/rsync/HTTP по бизнес-доменам с классификацией LEAK vs BOUNDARY-CALL; литералы путей вне deploy_paths.py; таймауты вне timeouts.py. Агент: explore, 15 tool calls. Дата 2026-08-22.
Итог: 19 хитов классифицированы как легальные boundary-calls (каналы деплоя, loadtest, llm/admin_client, bootstrap-инфраструктурные домены); inline SQL в бизнес-логике — 0; HTTP-клиенты вне dedicated-модулей — 0.

## ARCH-0030 — practices L2-check вызывает docker CLI напрямую мимо единственного адаптера
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/practices/check_project/checks/compose.py:51
- Symbols: check_compose_config
- Evidence: `subprocess_run(["docker", "compose", "config", "--quiet"], ...)` при существующем адаптере shared/docker_compose.py:386 `docker_compose_config()`; заголовок docker_ops.py декларирует «ЕДИНСТВЕННОЕ место прямых docker <op> вызовов», allowlist гейта пуст
- Scenario: смена версии compose-плагина/семантики флагов тихо меняет L2-verdict на машинах без docker
- Impact: формальное нарушение docker_sole_path гейта в бизнес-коде; вердикт качества проекта зависит от host-окружения
- Minimal fix: маршрутизировать через shared/docker_compose.docker_compose_config(), сохранив facts.which("docker") guard

## ARCH-0031 — HIGH: layout overlay `/opt/<ctx>/platform` захардкожен в ДВУХ оркестраторах
- Severity: HIGH · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: bootstrap/deploy/deploy_orchestrator.py:381; bootstrap/deploy/context_overlay.py:111
- Symbols: _resolve_overlay_dirs / ensure_context_repo
- Evidence: `candidate = f"/opt/{ctx}/platform/modules/{name}"` и `context_path = f"/opt/{context_name}/platform"` — дублированная топология вне SoT; deploy_paths.platform_remote_base() покрывает только /opt/platform
- Scenario: смена deployment root требует правки двух оркестраторских модулей; расползание литералов = silent overlay-resolution failure (класс P1, TRAP[BUG] 2026-08-03 рядом, :365)
- Impact: dual-delivery layout вшит в бизнес-роутинг
- Minimal fix: добавить context_overlay_base() в shared/deploy_paths.py; импортировать в обоих модулях

## ARCH-0032 — node_yaml/resolve.py повторяет path-SoT литералами внутри самого shared/
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/shared/node_yaml/resolve.py:105,111
- Symbols: ResolveMixin.resolve
- Evidence: `os.path.expanduser("~/projects")` и `f"/opt/node-configs/{node_name}/node.yaml"` при канонах projects_base()/node_configs_remote() в соседнем модуле того же слоя
- Impact: второй источник правды о путях внутри shared; перенос баз требует независимой правки resolve.py
- Minimal fix: вызывать deploy_paths.node_configs_remote()/projects_base() в candidates

## ARCH-0033 — healthcheck вычисляет множество модулей от захардкоженного пути
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/healthcheck/modules_healthcheck.py:287
- Symbols: _get_enabled_modules
- Evidence: `candidate = Path(f"/opt/node-configs/{node_name}/node.yaml")` — тот же класс утечки, второй случай
- Impact: health-verdict зависит от нестандартного root на ноде молча
- Minimal fix: import node_configs_remote() из shared/deploy_paths

## ARCH-0034 — grafana dashboards dir без env override и вне SoT
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: internal/monitoring/constants.py:43
- Symbols: DEFAULT_GRAFANA_DASHBOARDS_DIR = Path("/opt/grafana/provisioning/dashboards")
- Evidence: sibling-константы того же файла уже идут через deploy_paths.prometheus_rules_dir() (:24,:34) — grafana осталась непереведённой после миграции 170 W1-A3
- Impact: минорный; несогласованность с собственным паттерном файла
- Minimal fix: grafana_dashboards_dir() в shared/deploy_paths

## ARCH-0035 — new-project зависит от rsync для локальной копии шаблона
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: scaffold/project_scaffolder.py:199-211 (+fallback shutil.copytree :216)
- Symbols: _copy_template
- Evidence: subprocess.run(["rsync","-a",…], check=True) для чисто локального копирования; fallback есть, но семантики exclude дублированы и расходятся между путями
- Impact: make new-project на машине без rsync идёт другой веткой кода с другой семантикой
- Minimal fix: один путь — shutil.copytree(..., ignore=...)

## ARCH-0036 — остаточные timeout-литералы вне shared/timeouts.py
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: catalog_refresh.py:63 (timeout=60); project_scaffolder.py:465 (300); check_suite/diff.py:53,59, gate.py:85, fingerprint.py:82,155; python_deps.py:148,151; install_tor_proxy.py:561
- Evidence: subprocess.run(..., timeout=N) литералами после заявленной миграции 170 W1-A1
- Impact: тюнинг операционных таймаутов требует правок 5+ call sites; delivery-critical путей среди них нет
- Minimal fix: константы из shared/timeouts.py (аналоги DOCKER_CMD_TIMEOUT/SYSTEM_CMD_TIMEOUT)

## Boundary-calls OK (легальные, не находки)
deploy/channels/* (ssh/rsync через ssh_opts), loadtest/runner_remote.py, llm/admin_client.py (dedicated httpx), bootstrap/{docker_installer,firewall,docker_user_policy,reboot_policy,python_deps,cert_orchestrator,preflight,core_deliverer,overlay_deliverer}, remote_executor.py, healthcheck/watchdog.py, verify_sweep/http+tls_check, monitoring/catalog_refresh.sh-invocation, check_project/exec.py.
