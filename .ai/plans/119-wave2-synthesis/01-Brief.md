# 01-Brief — Волна 119: синтез второй итерации анализа

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Синтез 6 аудитов кодовой базы (shell-бизнес-логика, Python-монолиты, мёртвый код,
                  SoT-дубли, тестовый набор, манифест) в единый план волны 119. Задачи сгруппированы
                  в 8 брифов (A–H) по приоритету: гейты → SoT → dead-code → shell→Python →
                  декомпозиция → тесты → манифест → NodeYaml.
DESCRIPTION:      6 параллельных аудиторов (2026-08-02) + grep-верификация ключевых находок →
                  56 задач в 8 брифи. LARGE-артефакт: 01-Brief.md + 02..09-DevPlan.md (по одному
                  на бриф). Каждый DevPlan содержит $TASKS, $PARALLEL_GROUPS, $TEST_SPEC, R5-требования.
RATIONALE:        Волна 117 (Shell→Python финал) и 118 (снижение дрейфа) завершены локально.
                  Накоплен значительный технический долг: дубли, дрейф SoT, мёртвый код, пропуски
                  тестов, зомби-гейты. Волна 119 системно закрывает находки второй итерации аудита.
ACCEPTANCE_CRITERIA:
  - AC-GLOBAL-1: `make gate MODE=fast && make check-manifests && ruff check .` зелёные после каждого брифа
  - AC-GLOBAL-2: 0 новых глаголов в entrypoint-manifest.yaml (инвариант, ни одна задача не добавляет)
  - AC-GLOBAL-3: R5 ANTI-SURVIVORSHIP — каждое удаление/упрощение покрыто negative-тестом с исходным входом
  - AC-GLOBAL-4: Языковая политика: новый код — Python; bash — тонкий фасад <150 LOC; 0 новых inline python3
  - AC-GLOBAL-5: ≤2 коммита на DevPlan: docs(119) + feat(119). Локальные, БЕЗ push.
  - AC-GLOBAL-6: НЕ трогать test-VPS, НЕ делать push/merge/rebase.
  - AC-GLOBAL-7: `pytest tests/ -m "not requires_node"` — 0 regressions.
IMPLEMENTS:       Решение пользователя 2026-08-02: 8 брифов A-H в волну 119; порядок: гейты → SoT → dead-code →
                  shell→Python → декомпозиция → тесты → манифест → NodeYaml (HIGH risk, отдельным брифом).
IMPACTS:          core/internal/shared/, core/internal/bootstrap/, core/internal/deploy/,
                  core/modules/hermes-agent/, core/modules/backup-cron/, core/lib/,
                  core/entrypoints/, tests/, tests/gates/, tests/_conftest/,
                  .github/workflows/, entrypoint-manifest.yaml, core/AGENTS.md.
REQUIRES:         Результаты 6 аудитов 2026-08-02 (ниже); grep-верификация подтверждена.
-->

---

## 1. Контекст

- **Волна 117** (Shell→Python финал + SoT-dedup + Test Honesty) завершена локально.
- **Волна 118** (финальное снижение дрейфа, 7 брифов) завершена локально. 51+ коммит не запушен.
- **test-VPS** пересоздан, проектов нет. Ручное тестирование — ПОСЛЕ волны 119.
- **Волна 119** — вторая итерация системного аудита, закрывает находки, пропущенные в 118 из-за приоритизации критических фиксов деплоя.

## 2. Методология аудита (вторая итерация)

6 параллельных субагентов по зонам:
1. **Shell-бизнес-логика** — все .sh: классификация тонкий-фасад/бизнес-логика, Python-аналоги, дубли с shared/
2. **Python-монолиты** — топ-20 .py: смешанные ответственности, слои, kitchen-sink shared/
3. **Мёртвый код** — подсистемы без доставки, orphan-модули, dangling-ссылки, doc-drift
4. **SoT-дубли** — таймауты, пути, парсеры, константы, retry-интервалы вне канона
5. **Тестовый набор** — skip-маркеры, дубли, дыры покрытия, хрупкие тесты, pass-тесты
6. **Манифест** — зомби-гейты вне tests/gates/, thin_wrapper allowlist, системные исключения

Все находки верифицированы grep-проверками. Ключевые подтверждены в коде при создании плана.

## 3. DECISION-пункты (решения архитектора)

### D-1: Watchdog-подсистема (A1) → DEBT, решение на 120
**Статус:** ТРЕБУЕТ ПОЛЬЗОВАТЕЛЯ.
**Описание:** `core/modules/hermes-agent/watchdog/*` (agent_watchdog/circuit_breaker/docker_ops) — подсистема НЕ доставляется (0 в Dockerfile/compose/systemd/CI), потребители — только тесты и env_requires в module.yaml.
**Решение архитектора:** Минимально — добавить TRAP[DEBT] на каждый файл + запись в debt-реестр (Status/Rev). Деструктивное удаление НЕ планировать в волне 119 без явного решения пользователя. Задача C2 — только TRAP+реестр, не удаление.
**Риск при удалении:** неизвестные планы на watchdog (возможно, feature flag).
**Перенос на 120:** если владелец решит «удалить» — полный sweep: код + тесты + module.yaml + env_requires.

### D-2: Backup-cron upload-цепочка (D1) → FIX: завести вызов из backup-postgres.sh
**Статус:** РЕШЕНИЕ АРХИТЕКТОРА.
**Проблема:**
1. Dockerfile НЕ копирует `date_parser.py` и `s3_client.py` — `retention.py` импортирует их → cron retention падает на импорте.
2. `backup_config.py:36` импортирует `core.internal.config` (LINT-EXEMPT), отсутствующий в образе → любой импорт backup_config падает.
3. `upload-s3.sh`/`upload.py` НЕ вызываются ни одной cron-записью — upload-цепочка мёртвая.
**Решение:**
1. COPY `date_parser.py` + `s3_client.py` в Dockerfile.
2. Убрать `core.internal.config` из `backup_config.py` → вынести конфиг в env или inline defaults.
3. Завести вызов `upload-s3.sh` из `backup-postgres.sh` (после успешного дампа) — off-site бэкапы критичны.
**Риск:** низкий — цепочка уже написана, просто не активирована. Откат: удалить вызов из cron-скрипта.
**Задача C1.**

### D-3: NodeYaml декомпозиция (M1, D2 debt) → бриф H, ЗАВИСИТ от стабильности B+E
**Статус:** ВКЛЮЧЕНО в волну 119, но отдельным последним брифом H.
**Условие:** декомпозиция node_yaml.py (1164 LOC) на миксины по поддоменам; ~21 прямой потребитель NodeYaml.get() — verify-then-delete. Зависит от стабильности унифицированных shared-модулей (B) и декомпозиций (E).

## 4. Defer-список (на волну 120+)

| # | Что | Причина | Rev-условие |
|---|-----|---------|-------------|
| DEFER-1 | Watchdog полный sweep (удаление) | Ждёт решения пользователя (D-1) | 120 |
| DEFER-2 | F3 (issue-cert.sh 719 LOC) — полная миграция | TRAP: Rev после стабилизации acme.sh ≥6 мес | 2027-02 |
| DEFER-3 | F10 (deploy.sh 172 LOC) — удаление | Rev-условие 117 H D60: production не верифицирован | После prod-верификации A |
| DEFER-4 | M8 (sync_env_defaults 897 LOC) — data-driven, оставить | При касании | — |
| DEFER-5 | D7 (generate_platform_env codegen f-string → jinja) | Опционально, низкий приоритет | При рефакторинге codegen |

## 5. Полный реестр задач (56 → 8 брифов)

Легенда: `[AUDIT-N]` — источник аудита. Sev: HIGH/MED/LOW.

---

### Бриф A — Gates & Protection (закрывает loopholes, дешёво)

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| A1 | HIGH | **Зомби-гейты:** новый анти-drift gate «ни один @pytest.mark.gate вне tests/gates/» + перенос/marker-split 26 тестов (test_cross_layer_imports 12, test_smoke_test_isolation 6, test_template_syntax_gate 3, test_bootstrap_no_duplicate_steps 4, test_no_backward_compat_markers 1) | AUDIT-6 F1 |
| A2 | HIGH | **Timeout blind spot:** расширить `_DOMAIN_FILES` гейта на `docker_registry_auth.py`, `reconciler_projects.py`, `circuit_breaker.py`; импортировать таймауты из shared/timeouts | AUDIT-4 T1 |
| A3 | HIGH | **Honesty fail mode:** переключить `REQUIRE_HONESTY_MODE` с `marker` (skip) на `fail` в CI workflow (platform-gate-fast.yml, platform-test.yml); локальная дев-машина — marker через .env | AUDIT-5 R4-1 |
| A4 | MED | **Networks parity gate:** создать новый гейт `test_gate_networks_sot.py` по образцу `test_gate_volumes_sot.py` — имена сетей в 10+ compose vs platform-infra.yaml | AUDIT-4 K3 |

---

### Бриф B — SoT Unification (фундамент для декомпозиций)

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| B1 | HIGH | **vhost_renderer parser:** расширить API `shared/project_yaml` (expose/needs/domain/target_node), мигрировать vhost_renderer/vhost_configurator/conflict_checks/monitoring (9 файлов с yaml.safe_load ai-platform.yaml) на единый reader | AUDIT-2 S3, AUDIT-4 D1 |
| B2 | MED | **`/opt/projects` literals:** 12+ мест → единый импорт `projects_base()`/`DEFAULT_PROJECTS_BASE` из `shared/deploy_paths` | AUDIT-4 T2 |
| B3 | MED | **`/opt/platform`, `/opt/node-configs`:** ~15 call sites → импорт из `shared/deploy_paths` | AUDIT-4 T3 |
| B4 | MED | **subprocess_io canonical:** мигрировать bootstrap-фазы (lifecycle/helpers/subprocess_io.py — второй канон run_subprocess, default timeout=120 литерал) на `shared/subprocess_io`; удалить копию | AUDIT-4 D2 |
| B5 | MED | **OpenSSL timeout:** `cert_orchestrator:452,474`, `nginx_harness:159` — timeout=30×3 → `DEFAULT_OPENSSL_TIMEOUT=10` из shared/timeouts | AUDIT-4 T4 |
| B6 | MED | **PROJECT_HEALTHCHECK_PORTS:** `[8080,8000]` не пересекается с реальными (4000/3000/9000) → расширить/генерировать из platform-infra.yaml | AUDIT-4 K2 |
| B7 | MED | **Converge/infra timeouts:** локальные `DOCKER_TIMEOUT=30`, `FILE_OP_TIMEOUT=15` → импорт из shared/timeouts | AUDIT-4 T5 |
| B8 | LOW | **vps_readiness SSH_TIMEOUT:** `SSH_TIMEOUT=30` → `SSH_CONNECT_TIMEOUT` из shared/ssh_opts | AUDIT-4 T6 |

---

### Бриф C — Dead Code, Backup Fix, Debt Registry

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| C1 | HIGH | **Backup-cron fix:** (1) COPY `date_parser.py`+`s3_client.py` в Dockerfile; (2) убрать `core.internal.config` из backup_config.py → env/inline defaults; (3) завести вызов `upload-s3.sh` из backup-postgres.sh после дампа. R5: тест импорта retention в контейнере. | AUDIT-3 D1 |
| C2 | MED | **Watchdog DEBT:** добавить TRAP[DEBT] на agent_watchdog/circuit_breaker/docker_ops + запись в debt-реестр (Status/Rev). НЕ удалять. | AUDIT-3 A1 |
| C3 | LOW | **validate_not_verb removal:** 0 внешних вызовов (только определение в verbs.py:69) → удалить функцию + region-маркеры. R5: test_validate_not_verb_removed. | AUDIT-3 A2 |
| C4 | MED | **test_stub_detection removal:** тест тестирует сам себя (inline-bash копия _is_stub, продакшн удалён в 118) → удалить файл. R5: test_gate_stub_detection_imports. | AUDIT-5 DUP-2 |
| C5 | MED | **Duplicate test_shared_ssh_command_parser removal:** дубль test_ssh_command_parser.py (модуль переехал в 118 D3, старый тест остался) → удалить + inventory changelog. R5: test_gate_ssh_command_parser_single_test. | AUDIT-5 DUP-1 |
| C6 | LOW | **Letsencrypt DEBT:** TRAP[DEBT] на `/etc/letsencrypt/live` usage в 3 файлах + запись в debt-реестр | AUDIT-4 T7 |
| C7 | LOW | **Doc-drift fixes:** C2/D2/D3/E4 — поправить устаревшие комментарии при касании файлов в других задачах | AUDIT-3 C2/D2/D3/E4 |
| C8 | MED | **Always-skip tests → inversions:** `test_gate_project_context.py:53`, `test_gate_project_env.py:98` — always-skip (projects/ не существует) → инвертировать в FAIL (отсутствие projects/ = ошибка конфигурации) или добавить фикстуры | AUDIT-5 DEAD-1 |

---

### Бриф D — Shell→Python Migration

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| D1 | HIGH | **issue-cert.sh cert-валидация:** `_is_le_cert()` (L64-72) и `_acme_verify_cert()` (L372-406) дублируют `ssl_certs.cert_is_le_issuer`/`cert_check_expiry`. Fix: CLI-фасад в ssl_certs (паттерн `ssh_opts --shell`) или передача результата из cert_orchestrator. R5: test_issue_cert_wrapper_consistency. | AUDIT-1 F1, F2 |
| D2 | MED | **install-tor-proxy.sh install_packages:** деградационная state-machine (webtunnel→obfs4 fallback, 52-116) → извлечь в Python `tor_setup.py` (расширить tor_transport). Test-first. R5: test_tor_package_fallback. | AUDIT-1 F4 |
| D3 | MED | **install-tor-proxy.sh write_privoxy_config:** идемпотентная мутация (grep-guard + sed, 172-213) → Python-мутатор `privoxy_config.py` ~40 LOC + тесты идемпотентности. R5: test_privoxy_config_idempotent. | AUDIT-1 F5 |
| D4 | MED | **module-interface.sh dual-SoT:** shell-библиотека (206 LOC) дублирует Python `shared/module_interface.py` (создан в 118 C5). Fix: CLI `python3 -m ... invoke` + lib → тонкий фасад (<30 LOC bash). R5: test_module_interface_shell_parity. | AUDIT-1 F6 |
| D5 | MED | **hermes-agent init.sh:** cont-init бизнес-логика (157 LOC) → `init.py` ~80 LOC в `build/scripts/`. R5: test_hermes_init_py_parity. | AUDIT-1 F7 |
| D6 | MED | **hermes-agent healthcheck deps-mode:** required/optional агрегация (48-112) → Python `healthcheck_deps.py` ~50 LOC ИЛИ декларативная таблица в module.yaml. R5: test_hermes_hc_deps_aggregation. | AUDIT-1 F8 |
| D7 | LOW | **deploy.sh TRAP-актуальность:** обновить TRAP-комментарий (Rev-условие 117 H D60 не выполнено — production не верифицирован) | AUDIT-1 F10 |
| D8 | LOW | **LOW/INFO shell exceptions:** задокументировать keep-решения для F9/F11-F16 в AGENTS.md (почему эти скрипты НЕ мигрируются сейчас) | AUDIT-1 F9-F16 |
| D9 | LOW | **F2 openssl x509 pipeline:** уходит вместе с D1 (удаление дублирующей shell-логики cert-валидации) | AUDIT-1 F2 |

---

### Бриф E — Monolith Decomposition

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| E1 | HIGH | **docker_orchestrator deploy_docker_module:** 195 LOC CC=25, 13 if-веток → разбить по фазам: `_phase_hermes`, `_observability`, `_rebuild` или dispatch-таблица. R5: test_deploy_docker_module_phases. | AUDIT-2 M7 |
| E2 | MED | **orchestrator deploy/receive:** `deploy()` 186 LOC CC=13, `receive()` 127 LOC CC=15 → вынести receive в `deploy/receive_flow.py`, deploy по шагам `_prepare`/`_apply`/`_verify`/`_rollback`. R5: test_orchestrator_receive_flow_parity. | AUDIT-2 M2 |
| E3 | MED | **phases domain split:** `phases.py` 1080 LOC → фазы по доменам: `phases/system.py`, `docker.py`, `secrets.py`, `certs.py` (паттерн `lifecycle/helpers`). `phase_registry_update` (CC=23) — кандидат №1. R5: test_phases_domain_imports. | AUDIT-2 M3 |
| E4 | MED | **deploy_engine preflight/first_deploy:** вынести `_preflight_checks`/`_handle_first_deploy` из 874 LOC монолита → `deploy/preflight.py`, `deploy/first_deploy.py`. R5: test_deploy_engine_preflight_parity. | AUDIT-2 M9 |
| E5 | MED | **atomic_writer canonical:** создать `shared/atomic_writer.py` (atomic_write(path, content, mode, validator) — tempfile + os.replace + optional validator). Мигрировать 12 генераторов с os.replace. Исключение: json_writer.py (Docker bind mount — НЕ мигрировать, TRAP задокументирован). R5: test_atomic_writer_idempotent. | AUDIT-2 S5 |
| E6 | MED | **deploy_orchestrator pure functions:** вынести severity/exit-code агрегацию, status-metrics JSON, hc-маркеры, llm-рендер → `deploy/orchestrator_metrics.py`. R5: test_orchestrator_metrics_pure. | AUDIT-2 M5 |
| E7 | MED | **context_deployer llm-layer:** `_render_and_provision_llm` → `llm/provision_flow.py`. `deploy_context` ветвления → dispatch-таблица. R5: test_context_deployer_llm_flow. | AUDIT-2 M6 |
| E8 | LOW | **upload.py split:** `_upload`/`_verify` — разбить 745 LOC на `upload/uploader.py` + `upload/verifier.py`. R5: test_upload_verify_split. | AUDIT-2 M10 |
| E9 | MED | **vhost_renderer parser → depends on B1:** после миграции на shared/project_yaml, удалить собственный read_project_yaml из vhost_renderer. Зависит от B1. | AUDIT-2 M4 |

---

### Бриф F — Test Cleanup & Coverage

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| F1 | HIGH | **Skip→fail (R4 compliance):** R4-2 (test_component_hermes.py requests import → fail), R4-3 (test_tls_wildcard.py acme.sh → require_script_or_fail), R4-4 (test_gate_make_contract.py docker → require_docker_or_fail), R4-5 (ldd.py timeout skip → fail after 1 retry), R4-6 (test_e2e_litellm.py traffic skip → generate traffic or FAIL), R4-7 (test_e2e_grafana_api.py 401 → fail). R5: verify tests actually fail without deps. | AUDIT-5 R4-2–7 |
| F2 | MED | **Duplicate provision tests:** `test_unit_provision_environment.py:141,153,159` — побайтовые копии `test_gate_platform_env_schema.py` → удалить 3 теста. R5: test_gate_platform_env_schema_coverage preserved. | AUDIT-5 DUP-3 |
| F3 | AMBER | **Delegate tests:** 1-строчные обёртки → свести к patch-проверке (что делегат вызывает правильный модуль) | AUDIT-5 DUP-4 |
| F4 | HIGH | **HOLE-1: nginx_reload() 0 tests:** unit-тест + тест `_step_nginx_reload` для `shared/docker_compose.py:694` (создан в 118 D6). R5: test_nginx_reload_failure_mode. | AUDIT-5 HOLE-1 |
| F5 | AMBER | **HOLE-2: build_channel uncovered:** 3 кейса каналов для `orchestrator_cli.build_channel()`. | AUDIT-5 HOLE-2 |
| F6 | HIGH | **FRAG-1: test_status_page env mutation:** `_setup_app_env` мутирует 5 env без restore, sys.path.insert → обернуть в фикстуру с monkeypatch. R5: test_status_page_env_isolation. | AUDIT-5 FRAG-1 |
| F7 | MED | **FRAG-2: test_smoke_platform bare subprocess:** голый subprocess docker без require_docker_or_fail на L95,215. | AUDIT-5 FRAG-2 |
| F8 | AMBER | **FRAG-3: ldd.py offline-skip schema:** задокументировать поведение (см. F1 R4-5 fix) | AUDIT-5 FRAG-3 |
| F9 | AMBER | **FRAG-4: test_deploy_e2e dead branch:** `if imp_level >= 9: pass` (L197-198) → убрать, добавить found_log+assert | AUDIT-5 FRAG-4 |
| F10 | AMBER | **R2 unfalsifiable asserts:** `len(entries) >= 0` (test_deploy_e2e.py:205), `len(errors) >= 0` (test_platform_export_metrics.py:1050) → ужесточить до `>= 1` или явной проверки структуры | AUDIT-5 R2-1/R2-2 |
| F11 | LOW | **LDD-1 IMP:9 coverage:** добавить IMP:9 логи в ключевые файлы без них (46 файлов, низкий приоритет — делать при касании в других задачах) | AUDIT-5 LDD-1 |

---

### Бриф G — Manifest & Low-Priority Cleanup

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| G1 | MED | **thin_wrapper allowlist update:** `lint.sh` (40 LOC), `check-doc-headers.sh` (17), `converge.sh` (100) → удалить из allowlist (под лимитом); `bootstrap.sh` (160, комментарий «T15») → обновить; `deploy.sh` (172) → обновить комментарий | AUDIT-6 F2 |
| G2 | LOW | **Системные исключения .PHONY:** `help`, `venv`, `pre-commit-install`, `pre-commit-run` → задокументировать комментарий в манифесте/AGENTS.md почему они вне глоссария | AUDIT-6 F3 |
| G3 | LOW | **Bootstrap→deploy import direction:** задокументировать в core/AGENTS.md (bootstrap импортирует deploy, не наоборот) | AUDIT-4 C1 |
| G4 | LOW | **sync_env_defaults fallback literals:** AGE/TELEGRAM дублируют ci_default → импорт из shared или ci_default | AUDIT-4 S1 |

---

### Бриф H — NodeYaml Decomposition (HIGH risk, D2 debt)

| ID | Sev | Задача | Источник |
|----|-----|--------|----------|
| H1 | HIGH | **node_yaml миксины:** декомпозиция 1164 LOC на миксины по поддоменам (domains/secrets/firewall/projects/contexts/certs). NodeYaml — тонкий агрегатор с сохранением API `.get()`. R5: test_node_yaml_mixin_parity — все 21 потребитель .get() проходят. | AUDIT-2 M1 |
| H2 | HIGH | **node_yaml write_back:** `_write_back` → интеграция с `shared/atomic_writer.py` (E5). R5: test_node_yaml_atomic_write. | AUDIT-2 M1 |
| H3 | MED | **node_yaml consumers verify-then-delete:** ~21 прямой потребитель NodeYaml.get() — верифицировать все вызовы, поправить импорты после декомпозиции. R5: полный регрессионный прогон. | AUDIT-2 M1 |

---

## 6. Порядок волн (последовательная оркестрация)

```
A (Gates) ──► B (SoT) ──► C (Dead Code) ──► D (Shell→Py) ──► E (Monoliths) ──► F (Tests) ──► G (Manifest) ──► H (NodeYaml)
```

| Волна | Бриф | Задач | Риск | Обоснование порядка |
|-------|------|-------|------|---------------------|
| 1 | **A** — Gates | 4 | LOW | Закрывает loopholes (зомби-гейты, timeout blind spot, honesty fail, networks parity). Дешёво, защищает последующие волны от регресса. |
| 2 | **B** — SoT Unification | 8 | MED | Фундамент: единый project_yaml reader, пути, subprocess, таймауты. Без этого декомпозиции (E) и shell→Python (D) будут плодить новые дубли. |
| 3 | **C** — Dead Code/Debt | 8 | MED | Чистка перед миграциями: backup-cron fix (критичный баг), удаление мёртвых тестов, debt-реестр. |
| 4 | **D** — Shell→Python | 9 | MED | Миграция shell-бизнес-логики. Зависит от B (shared/ssl_certs, shared/module_interface уже есть). |
| 5 | **E** — Monolith Decomp | 9 | HIGH | Декомпозиция крупных Python-файлов. Зависит от B (shared модули — atomic_writer, project_yaml). Самый высокий риск регресса. |
| 6 | **F** — Test Cleanup | 11 | MED | После миграций и декомпозиций: удаление дублей, заплатка дыр, fix хрупких тестов. |
| 7 | **G** — Manifest/Docs | 4 | LOW | Косметика: allowlist, документация. После всех изменений. |
| 8 | **H** — NodeYaml | 3 | HIGH | Самый высокий риск: ~21 потребитель .get(). Зависит от стабильности B (shared модули) и E (atomic_writer, миксин-паттерн). |

**Параллелизм:** Внутри каждого брифа DevPlan определяет $PARALLEL_GROUPS — задачи без общих файлов могут исполняться параллельно (но в рамках одной волны = один кодер).

---

## 7. Жёсткие ограничения (на всю волну)

1. **0 новых глаголов** в entrypoint-manifest.yaml — ни одна задача не добавляет глагол.
2. **R5 anti-survivorship:** на КАЖДОЕ удаление/упрощение — negative-тест с исходным входом.
3. **Языковая политика:** новый код — Python; bash — тонкий фасад <150 LOC; 0 новых inline python3.
4. **Коммиты ≤2 на DevPlan:** `docs(119): <N> DevPlan — <slug>` + `feat(119): <N> implementation — ...`.
5. **НЕ трогать** test-VPS, НЕ делать push/merge/rebase.
6. **Gate после каждого брифа:** `make gate MODE=fast && make check-manifests && ruff check .`.

---

## 8. Файлы артефактов

| Файл | Содержание |
|------|-----------|
| `01-Brief.md` | Этот файл — обзор, DECISION-пункты, реестр задач |
| `02-DevPlan.md` | Бриф A — Gates & Protection |
| `03-DevPlan.md` | Бриф B — SoT Unification |
| `04-DevPlan.md` | Бриф C — Dead Code, Backup Fix, Debt |
| `05-DevPlan.md` | Бриф D — Shell→Python Migration |
| `06-DevPlan.md` | Бриф E — Monolith Decomposition |
| `07-DevPlan.md` | Бриф F — Test Cleanup & Coverage |
| `08-DevPlan.md` | Бриф G — Manifest & Low-Priority Cleanup |
| `09-DevPlan.md` | Бриф H — NodeYaml Decomposition |

---

## Next Steps

### Wave 1 (Бриф A)
```
coder Read .ai/plans/119-wave2-synthesis/02-DevPlan.md, implement Wave 1: A1, A2, A3, A4
```

### После каждой волны
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```
