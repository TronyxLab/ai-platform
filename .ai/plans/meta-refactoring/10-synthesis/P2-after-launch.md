# P2 — After Launch (10-Synthesis)

Критерий: осмысленные улучшения, отложенные сознательно, чтобы не жечь launch-week бюджет риска. Формат облегчённый (тема → состав → первый шаг → условие активации). Порядок внутри — по приоритету.

## P2.1 Структурные рефакторинги (после characterization-тестов)

| # | Тема | Состав | Первый шаг |
|---|------|--------|-----------|
| 1 | Deploy god-cluster split | context_deployer.py 1276 LOC; orchestrator.py (rollback_manager extract); lifecycle cli/phases/helpers ~3400 LOC | characterization тесты step-order + receive-dispatch через существующие DI-швы (A-33) |
| 2 | Wire-DTO разделение | OrchestratorDeployResult (god-DTO 11 полей = internal result + CI wire schema); StatusResult↔ProjectStatus дубликаты | split wire-DTO от internal; stop import класса в context_deployer (DEP-0048/0049/0050) |
| 3 | bootstrap домен 35.8k LOC | механические сплиты запрещены до фиксации внешних фазовых контрактов | контракты фаз (REF-0106), затем phases/converge|node_config|user_accounts extraction |
| 4 | Dual identity пакетов | monitoring/* sys.path try/except ×9; два пакета "deploy" (agent правит не тот) | canonicalize `core.internal.monitoring.*`; rename bootstrap/deploy→modules_deploy (A-18/A-42) |
| 5 | Lazy-import инвентаризация | DEP-0013 сайты + converge/projects.py scaffold-import вопреки TRAP (DEP-0008) | grep-audit gate на function-body imports |
| 6 | tests/_conftest/compose.py 1261 LOC fixture-оркестратор | A-40 | extract lifecycle helpers → tests/helpers/module_lifecycle.py |

Условие активации: стабильные characterization-тесты + отсутствие открытых P0/P1 в затронутых файлах.

## P2.2 Security maturation (multi-tenant готовность)

- **docker socket-proxy** для log-collector/alloy/cadvisor вместо :ro-сокета и SYS_ADMIN cAdvisor (SEC-0012/0013; keep-list пересмотреть после первого security-инцидента).
- **Redis ACL per-project** + key-pattern `<project>:*` (SEC-0007/DATA-505) — перед первыми внешними tenant'ами.
- **REVOKE CONNECT FROM PUBLIC** — если не вошло в REF-0002 rider.
- **Retention/PII**: TTL на Langfuse CH tables + S3 lifecycle (SEC-0020·B16 / SEC-0048 / SEC-0023 status-inventory trimming).
- **Per-surface credentials**: htpasswd split (status/prometheus/loki), отдельный S3-ключ langfuse vs backup (SEC-0021/0022).
- **authorized_keys rebuild-from-desired** + dated quarantine (SEC-0001) — к первой реальной ротации ключей.
- **fail2ban/MaxAuthTries** (если не вошёл в REF-0016), privoxy listen сузить до declared subnets, CIDR SoT DOCKER_BRIDGE_NETS (SEC-0035/0036/0004).
- **Rootless/userns-remap** для project workloads — стратегическое решение по SEC-0013 residual.
- **pull_request_target**: полная развязка secrets-job'ов для fork-scenario (если repo станет public/forkable — SEC-0009 остаток).

## P2.3 Performance (за пределами reliability-critical)

- Параллельный deploy fan-out поверх topo-групп (PERF-004/006) — только после стабильности REF-0005.
- Батчинг docker inspect (PERF-034/051), openssl single-pass (PERF-035), curl→stdlib probe опция (PERF-037 HYP).
- Interpreter consolidation: manifest_driver in-process (PERF-055), project_lister import (PERF-065), static AST shared cache (PERF-070, measured 5.38s→~2.5s).
- practices check_project: ThreadPoolExecutor non-mutating checks (PERF-060), strict-superset pytest dedup (PERF-061), file snapshot (PERF-063/067).
- e2e sweep parallelization ÷6–8 (PERF-072); load-test spawn-rate tuning глубже минимума (PERF-084), pgwire log demotion (PERF-085).

## P2.4 Test-suite hygiene (churn-reduction, не риск)

- Удаления/слияния: TEST-44 (healthcheck triplet subset), TEST-45 (root test_e2e_health dup — сначала проверить внешних runner'ов), TEST-46 (parametrize 8+9 bodies + rollback pair), TEST-47 (dead helper).
- R5 positive-path wiring (TEST-42): drive probes через позитивный scan-path; meta-test exclusion↔creating-test.
- agent_check package tests (TEST-09, 1266 LOC без единого теста) + manifest_driver tests (TEST-10).
- LDD-trajectory decorator band (TEST-41/AI-0051): импорт канонного хелпера вместо ≥20 ручных копий.
- Integration theater resolution (TEST-17/TEST-22 MARKER_MAP derive from SoT; TEST-23 local_auth/e2e orphan suites — attach или явный manual-only статус).
- Watchdog corrupt-state fail-loud decision (TEST-34); healthcheck-gate order-aware rewrite (TEST-24 substring→block-parse) — частично может уйти в P1 при ёмкости.
- hermes patch-basic-auth-provider test (TEST-43).

## P2.5 Dependency/gate amplification (архитектура верификации)

- Единая parity-table fixture для портов/таймаутов/profiles/domain вместо 4× дублирования гейтов (DEP-0055..0059).
- Derive gate-whitelists из directory glob + exemption lists (DEP-0054) — убирает «забытый whitelist = модуль без healthcheck».
- load_module_yaml() единый validated reader (DEP-0052); node.schema.json versioning + deprecate raw() (DEP-0053).
- constants module для dispatched dotted-paths (DEP-0020); единый local-root resolver (DEP-0038≡0041); env-defaults через env_defaults_generated + grep-gate (DEP-0034); network-names из generated platform-env (DEP-0030).
- `make new-verb` scaffold против ×6 amplification (A-28); GENERATED markers для entrypoint-manifest (A-29); subprocess-call registry (A-30).
- Exit-code contracts emitted shell-sourceable (DEP-0047); hook-vs-CI invocation derive (DEP-0046); cardinality pins → floors (DEP-0059).

## P2.6 Dead code / false contracts cleanup

- Dead routes: SCPChannel + --scp + deploy-many; DEPLOY_ORCHESTRATOR=true third route (A-35; координировать с SEC-0031 hardening).
- Dead flags/knobs: --no-fallback-build, keep_images, STATUS_PAGE_HOST, module.yaml#systemd.*, postgres_init_databases/repos.* schema blocks (AI-0026/27/30/32/72/28-29).
- Dead code minor: AI-0053..0062 band, DEP-0014 (check_project.py shadowed), load_existing_manifest + его gate-clause, metadata_defaults attr, export_shell судьба (решить совместно с dotenv-грамматиками AI-0055/0061).
- Doc-contract reconciliation: PostgreSQL 18.4 docs (AI-0069 — если не ушло в REF-0009), .env.platform password doc (SEC-0025), hermes ports text (AI-0071), healthcheck_poller docstring (AI-0040), deploy/__init__ exports (run-c ARCH-0005), stale STRUCTURE cluster (AI-0042..45), github_ops «never creates repos» (AI-0037 + timeout/return False AI-0017).

## P2.7 Observability improvements

- Throttle persistence file-backed per (event,fingerprint) (A-21); notification retry after failed send (FAIL-0307); tor Restart drop-in verify (FAIL-0407 HYP — 1 команда).
- blackbox/verify_sweep cron для low-traffic vhosts (FAIL-1006); retention raise Prometheus 30d (FAIL-1007/0506) после disk estimate; LITELLM_MASTER_KEY empty guard (FAIL-1008); dedicated freshness gauge вместо false BackupStale semantics (FAIL-0902/1005).
- Env-mutation hygiene: sanitized env= dicts, S3_BUCKET resolve-once (A-22/23); watchdog flap test (TEST-34); journald SystemMaxUse=1G (FAIL-0508); wal-archive du cap (SEC-0049 remainder).
- pgbouncer capacity alert (FAIL-0108 pair), litellm facade observability (FAIL-0105), CH TTL (SEC-0048 execution).
