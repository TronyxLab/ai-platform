# 01-Brief — Программа пост-рефакторинговой волны: системное снижение дрейфа

$ARTIFACT_CONTRACT
- PURPOSE: Полный реестр задач пост-рефакторинговой волны (shell→Python, DevPlan 116 завершён) с разбиением на брифы A–H для точечной проработки. Цель — системное снижение дрейфа и унификация, БЕЗ добавления нового функционала.
- DESCRIPTION: 9 параллельных аудиторов (shell-инвентаризация, Python-монолиты, drift 12 измерений, CI/TRAP/документация, тесты R1–R5, архитектура/манифесты, dead code, bootstrap-конвейер, модули) → ~190 находок → консолидация в 10 доменов / 71 задачу / 8 брифов.
- RATIONALE: После двух недель рефакторинга (4114→392 LOC shell) нужен независимый аудит остаточного дрейфа перед переходом к ручному тестированию на пересозданном тестовом сервере tronyx-vps. Ручное тестирование начнётся ПОСЛЕ этой волны.
- ACCEPTANCE_CRITERIA:
  - AC1: Все 71 задача закрыты или явно отклонены с обоснованием (каждая волна = свой DevPlan/VerificationReport).
  - AC2: `make gate MODE=fast` и `make check-manifests` зелёные после каждой волны.
  - AC3: `make bootstrap-node NODE=tronyx-vps` проходит без CRITICAL-ошибок (ручное тестирование после волны A).
  - AC4: `make deploy PROJECT=<test>` через CI-канал (dispatch) на тестовом сервере работает.
  - AC5: Ноль новых глаголов/механизмов; все изменения — унификация/удаление.
- IMPLEMENTS: решение пользователя 2026-08-01 (утверждён список, старт с брифа A).
- IMPACTS: core/ (bootstrap, deploy, shared, modules), .github/workflows, тесты, AGENTS.md, entrypoint-manifest.yaml.
- REQUIRES: результаты 9 аудитов от 2026-08-01 (данные в чате сессии), верификация ключевых находок (см. §Критические факты).

---

## 1. Контекст

- Волна 116 (hardening program, B1–B11) завершена: Strangler-Fig shell→Python, parity-гейты, глоссарий G4, audit-format R2, debt-freshness.
- Остаток shell: 95 файлов / ~9.2k LOC (легитимные lib + entrypoint-фасады + bootstrap-шаги + модульные скрипты).
- Сервер tronyx-vps пересоздан — тестовый, проекты не запущены, мигрировать нечего. Ручное тестирование — после волны.
- Рабочее дерево чистое (main, f4dcebf B11).

## 2. Критические факты (верифицированы лично в коде)

| # | Факт | Evidence |
|---|------|----------|
| K1 | **CI-деплой сломается на новых нодах**: два писателя ci-deploy authorized_keys — phases.py:256 пишет `orchestrator_cli receive`, setup-node.sh:116 — `dispatch`. Порядок φ2→φ3: ключ φ2 (receive) уже в файле, setup-node.sh пропускает (grep-guard) → на ноде остаётся receive, игнорирующий SSH_ORIGINAL_COMMAND (U-04) → `make deploy` через CI не работает. TRAP B8 D2 в AGENTS.md устарел | phases.py:256-257, setup-node.sh:97-119 |
| K2 | **`make audit` — no-op**: entrypoints/audit.sh → internal/audit/audit.sh: при вызове с аргументами печатает message + exit 0, системный аудит не выполняется | internal/audit/audit.sh:24-34 |
| K3 | **platform-gate-fast.yml**: `install-pre-commit: 'false'`, но `make gate MODE=fast` шаг 1 = pre-commit-run → command not found на cache-miss | platform-gate-fast.yml:50-60 |
| K4 | **CI в обход SoT**: 8 сырых `ssh -o ConnectTimeout=10` в 2 workflow (SoT=30); гейты ssh_opts_sole_path/timeout_literals CI не сканируют | core-deploy.yml:108+ |

## 3. Полный реестр задач (71)

### T1. Deploy-канал и bootstrap-консистентность — **бриф A**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 1 | CRIT | Унифицировать ci-deploy forced-command на `dispatch`; единственный писатель — Python users.py (φ2); setup-node.sh — только sudoers; обновить TRAP B8 D2 в AGENTS.md | phases.py:256 vs setup-node.sh:116 |
| 2 | HIGH | docker_registry_auth.py ×2 за init (φ3+φ6), каждый `systemctl restart docker` — убрать дубль, restart по guard | phases.py:315,518; docker_registry_auth.py:188 |
| 3 | MED | cert_orchestrator ×2 за init (φ7 + deploy_context) — skip при валидном сертификате | phases.py:680; context_deployer.py:691 |
| 4 | MED | issue-cert.sh: удалить мёртвый main() (110 LOC); executor — живой (TRAP) | issue-cert.sh:583-695 |
| 5 | MED | state_machine: мёртвый execute_grouped_phase; WARN-фазы маскируются под done; current_step всегда 0 | state_machine.py:214-221,543-548,798 |
| 6 | MED | preflight при каждом init даже при всех done-фазах | cli.py:188-189; node-lifecycle.sh:60-64 |
| 7 | LOW | bootstrap.sh:147 прямой `exec ssh` в обход lib/ssh.sh | bootstrap.sh:147 |
| 8 | LOW | Проглоченные ошибки: `2>/dev/null \|\| true` в bootstrap.sh:73, node-lifecycle.sh:53 | — |

### T2. Мёртвый код / no-op — **бриф B**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 9 | HIGH | `make audit` — перенацелить на реальный Python-аудит или удалить таргет (решение на брифе) | internal/audit/audit.sh:24-34 |
| 10 | HIGH | backup-cron: 3 cron-записи мёртвые (restore-test не в образе; disk-monitor/warm-images без docker CLI в контейнере) — host-cron+Python или удалить | crontab:32,41,50 |
| 11 | MED | rotate-spend-logs.sh (172) + pg-archive-cleanup.sh (168) — biz-logic не подключена никуда | litellm/scripts; postgres/config |
| 12 | MED | watchdog: platform-agent-watchdog.sh + .service/.timer — dead (не устанавливаются) | watchdog/:18 |
| 13 | MED | litellm init-multi-db.sh — не монтируется, не COPY | litellm/config/postgres-init |
| 14 | MED | platform-secrets healthcheck.sh мёртв (interface не зарегистрирован); nginx module.yaml `install` устарел | module-interface.sh:157-168 |
| 15 | LOW | yaml_query.py (267) жив через node-resolver.sh:216, хотя yaml_read.sh заявляет «replaced» | node-resolver.sh:216 |
| 16 | LOW | node-lifecycle.sh step_* функции мёртвые; + дожать верификацию ~100 функций shared/bootstrap/deploy на мёртвость | node-lifecycle.sh:44-48 |
| 17 | LOW | `build-local` в комментариях шаблонов (D3-политика) | templates/module.mk:30,37 |

### T3. Дубли логики → единый SoT — **бриф C**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 18 | HIGH | Две orphan-реконсиляции: docker_orchestrator.py:284 vs orphan_reconciler.py:305 | оба вызываются |
| 19 | HIGH | watchdog: AuditLogger + DockerManager дублируют shared/audit_logger.py + shared/docker_compose.py | agent_watchdog.py:267,636 |
| 20 | MED | secrets_validator.parse_modules_from_node_yaml (110 LOC) дублирует node_yaml.get_modules() | secrets_validator.py:360 |
| 21 | MED | Две openssl-валидации → shared/ssl_certs.py | s3_ssl_cache.py:125; cert_orchestrator.py:271 |
| 22 | MED | DeployResult ×4 одноимённых класса → shared/contracts.py | orchestrator.py:171 и др. |
| 23 | MED | platform-infra.yaml читается из SoT и generated-копии → единый loader | docker_orchestrator.py:159 |
| 24 | MED | key_provisioner shim discover_projects (hardcoded test-данные) → shared/project_registry.py | key_provisioner.py:62-105 |
| 25 | MED | hermes-agent healthcheck deps-режим дублирует postgres readiness | hermes/healthcheck.sh:64-77 |
| 26 | LOW | sha256 ×3; boto3-фабрика ×2 → shared/s3_client.py | upload.py:113 |

### T4. Реестры таймаутов/портов/env + гейты — **бриф D**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 27 | HIGH | 8 сырых ConnectTimeout=10 в CI → SSH_OPTS SoT | core-deploy.yml:108+ |
| 28 | HIGH | docker timeout 15 vs канон 10 (×6); гейт timeout_literals не покрывает 15 и core/modules | docker_orchestrator.py:322 |
| 29 | HIGH | Watchdog-таймауты (90/30/10/5/3) вне timeouts.py | agent_watchdog.py:175-188 |
| 30 | HIGH | NGINX_CERT_DIR default /etc/letsencrypt vs SoT ./dev-certs | nginx compose:73 |
| 31 | MED | AGENT_PORT vs HERMES_DASHBOARD_PORT (9119); STATUS_PAGE_PORT (8080) не в SoT | agent_watchdog.py:147 |
| 32 | MED | healthcheck_poll: 10/1 vs канон 60/3; interval 2; 100s | context_deployer.py:468 |
| 33 | MED | WATCHDOG_* не декларированы; CONTEXT_IMAGE sha vs tag; PLATFORM_DOMAIN localhost vs SoT | hermes compose:69 |
| 34 | MED | Три retry-политики → единый реестр | state_machine.py:233 |
| 35 | LOW | healthcheck_poller docstring vs факт (60 vs 180s) | healthcheck_poller.py:16 |
| 36 | LOW | Порт 8000/8080 хардкод | healthcheck_poller.py:170 |
| 68 | MED | Гейт timeout_literals: расширить набор (15) + скоуп core/modules + workflows | test_gate_timeout_literals.py:27 |
| 69 | MED | ssh_opts_sole_path: покрыть .github/workflows | — |
| 70 | MED | docker-sole-path: покрыть shell/make точки (module.mk COMPOSE_CMD, compose-wrapper) | templates/module.mk:76 |
| 71 | LOW | Двойной pre-commit-run (platform-test.yml:100 + gate fast) | platform-test.yml:100 |

### T5. Документация/манифесты/TRAP — **бриф E**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 37 | HIGH | AGENTS.md TRAP B8 D2 (receive→dispatch); инвариант «ensure_context_repo — единственный git на VPS» (факт: context_overlay.py) | AGENTS.md:24-28 |
| 38 | HIGH | Verb `platform-deliver` не существует (0 совпадений) — Triple Delivery таблица | AGENTS.md:19 |
| 39 | MED | Путь config_renderer.py: bootstrap/deploy → llm/ | AGENTS.md:260 |
| 40 | MED | «shared/healthcheck_poll» не существует → healthcheck_poller.py | AGENTS.md:39-42 |
| 41 | MED | entrypoint-manifest: bootstrap/node-update цепочки описывают до-B9 топологию | manifest:18-23 |
| 42 | MED | Навигация: 5 строк «Канонический» vs инвариант «3 канонических» | AGENTS.md §Навигация |
| 43 | MED | platform-test.yml: header «Full gate» vs ci-docker; inline openssl dev-certs дубль make dev-certs | platform-test.yml:224 |
| 44 | LOW | smoke_env_generated.py пути; Makefile «45 targets» vs 72; README gate MODE | Makefile:2 |
| 45 | LOW | secret-definitions.yaml ci_default email с доменом | secret-definitions.yaml:156 |

### T6. Test Honesty — **бриф F**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 46 | HIGH | R4: 14+ skips при недоступности сервиса; двойной стандарт require_docker_or_fail vs skipif | test_local_auth.py:90 |
| 47 | MED | 77× sys.path.insert — легитимизировать в политике или conftest-хук | test_status_page.py:260 |
| 48 | MED | R5: завершить парный negative-скан (~100 ссылок на баги) | — |
| 49 | LOW | LDD-выборка: IMP:9-трассы в unit/gates | — |
| 50 | LOW | test_inventory_changes.yaml синхронизировать | — |

### T7. Python-монолиты — **бриф G**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 51 | MED | node_yaml.py 1890: вынести CLI (420 LOC) + типизированные reader'ы | node_yaml.py:1470-1890 |
| 52 | MED | agent_watchdog.py 1088 → watchdog/: circuit_breaker, docker_ops, notifier, watchdog | agent_watchdog.py:300+ |
| 53 | MED | vhost_renderer.py 1189: вынести nginx-harness (~200) | :696-900 |
| 54 | MED | monitoring_config_renderer.py 938: split по генераторам | :257-810 |
| 55 | MED | status-page/app.py 1075: collectors/renderer/server | app.py:228-1053 |
| 56 | MED | generate_platform_env.py 863: порт-сканер отдельно | :188-321 |
| 57 | MED | sync_env_defaults.py 626: функция 450 LOC → секции | :85-538 |
| 58 | LOW | Точечно: project_scaffolder github_ops, s3_ssl_cache bulk_restore, secrets_manager htpasswd, cert_orchestrator cron_install, context_deployer LLM | — |

### T8. Shell → Python финальная волна — **бриф H**
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 59 | MED | bootstrap.sh (150): оркестрация-логика + exec ssh → Python/чистый фасад | bootstrap.sh:73-147 |
| 60 | MED | deploy.sh (161): legacy-диспетчер — решение по срокам жизни (B1 T7 transitional) | deploy.sh:65-158 |
| 61 | MED | scaffold.sh (128): CLI-нормализация → argparse | scaffold.sh:34-68 |
| 62 | LOW | Проверить живость node-resolver.sh/yaml_read.sh (потребители после Strangler) | — |
| 63 | LOW | check-file-lines.sh (74): логика гейта → Python | — |
| 64 | LOW | backup-postgres.sh (153) → Python (модуль уже Python-first) | backup-postgres.sh |
| 65 | LOW | on-project-deploy.sh: дожать inline python3 -m → Python-хук | postgres/hooks:47-50 |
| 66 | LOW | warm-images.sh / disk-monitor.sh / backup-restore-test.sh — судьба вместе с #10 | — |

### T9. CI-гейты — слепые зоны — **бриф D** (см. T4, #67-71)
| # | Sev | Задача | Evidence |
|---|-----|--------|----------|
| 67 | HIGH | platform-gate-fast.yml: install-pre-commit 'false' → починить | :50-60 |

## 4. Разбиение на брифы

| Бриф | Домен | Задачи | Размер | Порядок |
|------|-------|--------|--------|---------|
| A | Deploy-канал + bootstrap-консистентность (до ручного теста) | 1-8 | STANDARD | 1-й (CRITICAL K1) |
| B | Dead code sweep + no-op | 9-17 | STANDARD | 2-й |
| C | SoT-унификация дублей | 18-26 | LARGE | 3-й |
| D | Реестры таймаутов/портов/env + CI-гейты | 27-36, 67-71 | STANDARD | 3-й (параллельно C) |
| E | Docs/manifest/TRAP sync | 37-45 | SMALL | 4-й |
| F | Test Honesty | 46-50 | STANDARD | 4-й |
| G | Python-декомпозиция монолитов | 51-58 | LARGE | 5-й |
| H | Shell→Python финал | 59-66 | STANDARD | 6-й |

Порядок: **A обязателен до ручного bootstrap-тестирования** (K1 ломает CI-деплой). B/C/D снимают мёртвый код и дрейф до тестов — тестируем чистую систему. G/H — последние (чистка после заморозки, без нового функционала).

## 5. Критерии приёмки программы
- AC1: все 71 задача закрыта или отклонена с обоснованием.
- AC2: gate MODE=fast + check-manifests зелёные после каждой волны.
- AC3: bootstrap-node NODE=tronyx-vps без CRITICAL-ошибок.
- AC4: make deploy через CI (dispatch) работает на тестовом сервере.
- AC5: ноль новых глаголов/механизмов.
