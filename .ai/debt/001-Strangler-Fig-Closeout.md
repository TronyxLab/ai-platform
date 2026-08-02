# $ARTIFACT_CONTRACT
# GREP_SUMMARY: debt-registry, strangler-closeout, TRAP-inventory, shell-residual, P2-P3-backlog, ARCH-DECISIONS, TEST-DEBT
# STRUCTURE: ┌Full TRAP scan (BUG/DEBT/DECISION)┐ → ◇ SHELL-RESIDUAL (8) → ◇ P2-BACKLOG (5) → ◇ P3-BACKLOG (5) → ◇ TEST-DEBT (7) → ◇ ARCH-DECISIONS (7) → ◇ TRAP-INVENTORY → ◇ GITIGNORE/CHECK-FILE-LINES → ⎋ Appendix A/B
## @PURPOSE Единый реестр архитектурного долга после завершения Strangler-Fig (волны 099-109): полный TRAP-инвентарь кодовой базы, уточнённый список shell-скриптов >200 LOC, P2/P3-бэклоги, тестовый долг и решения с датами пересмотра
## @DESCRIPTION
##   Реестр создан по DevPlan 111 (`.ai/plans/111-debt-registry/02-DevPlan.md`) в рамках Wave 1 (TASK-A..D).
##   Источник данных — полный TRAP-скан кодовой базы от 2026-07-31 + grep-верификация в рамках TASK-A.
##   Ключевая корректировка относительно Brief 111: P2-BACKLOG ЗАКРЫТ волнами 106-109 (validate.sh 251→18,
##   scp-deliver.sh 251→59, check-dead-code.sh 86→14, lint.sh 40, check-doc-headers.sh 17 — все стали
##   thin-фасадами <100 LOC). Фактический P2 пересчитан; добавлен новый кандидат node-resolver.sh (271 LOC).
## @ACCEPTANCE_CRITERIA
##   AC1: Файл создан и force-added в git (git add -f)
##   AC2: Все секции заполнены: SHELL-RESIDUAL, P2-BACKLOG (пересчитанный), P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS
##   AC3: Каждая запись содержит: файл, LOC, обоснование исключения/отсрочки, rev-дату
##   AC4: Все TRAP[DECISION] из AGENTS.md с будущими rev-датами продублированы в ARCH-DECISIONS
##   AC5: make check-file-lines не блокирует .ai/debt/ (scope: core/ only)
##   AC6: .gitignore содержит .ai/* (строка 21) — покрывает .ai/debt/; файл коммитится с git add -f
##   AC7: grep-верификация: реестр существует, все обязательные секции, записи валидны
## @IMPLEMENTS Brief 111, DevPlan 111 (TASK-A/B/C/D)
## @IMPACTS
##   - .ai/debt/001-Strangler-Fig-Closeout.md (NEW — этот файл)
##   - .gitignore (проверено — .ai/* уже покрывает .ai/debt/, правок не требуется)
##   - core/entrypoints/check-file-lines.sh (проверено — scope core/ only, правок не требуется)
##   - tests/gates/test_gate_debt_registry.py (NEW — gate-тесты реестра)
## @REQUIRES
##   - Результаты миграционных волн 099-109 (актуальный P2-пересчёт)
##   - Полный TRAP-скан кодовой базы от 2026-07-31 (Шаг 1 DevPlan 111)
## @changes  CREATED: 2026-07-31 | DevPlan 111 Wave 1 | TASK-A (реестр) + TASK-B (.gitignore) + TASK-C (check-file-lines) + TASK-D (git add -f)
## @changes  UPDATED: 2026-08-01 | DevPlan 116 B11 T7 (U-82, D4) — формат записей: Status (OPEN/FIXED/SUPERSEDED) + Rev (дата ИЛИ условие); T1/P2-2 → FIXED (B10); AD8-AD12 (U-83..88 решения); гейт свежести test_gate_debt_registry.py
## @changes  UPDATED: 2026-08-02 | DevPlan 119 C2/C6 — +P3-6 (watchdog undelivered, решение на 120) +P3-7 (letsencrypt path hardcode); отдельные файлы .ai/debt/watchdog-undelivered.md, .ai/debt/letsencrypt-path-hardcode.md

---

# Strangler-Fig Closeout — Debt Registry

> Создан: 2026-07-31 | DevPlan 111 | Волны: 099-109 (Strangler-Fig завершён)
> Назначение: Единый реестр архитектурного долга после завершения миграции shell→Python
> Верификация: grep 2026-07-31 — 251 TRAP[BUG] / 33 TRAP[DEBT] / 248 TRAP[DECISION] (канонические числа DevPlan 111 §1.1)

---

## §SHELL-RESIDUAL — Скрипты >200 LOC, исключённые из миграции

| # | Файл | LOC | Обоснование исключения | Status | Rev |
|---|------|-----|------------------------|--------|-----|
| S1 | `core/internal/bootstrap/issue-cert.sh` | 704 | acme.sh executor (DNS-01/HTTP-01). Осознанно пропущен в Wave 5a — shell subprocess by design (U-85 justified). TRAP[DECISION] 2026-07-26. | OPEN | 2026-12-31 |
| S2 | `core/internal/bootstrap/install-tor-proxy.sh` | 422 | Одноразовый bootstrap. Не содержит бизнес-логики для извлечения в Python. TRAP[DECISION] webtunnel degradation. | OPEN | При росте >500 LOC |
| S3 | `core/lib/healthcheck.sh` | 388 | STABLE библиотека. Исключена политикой (AGENTS.md: языковая политика п.2 — lib-функции низкого уровня). | OPEN | Бессрочно (стабильное API) |
| S4 | `core/modules/platform-secrets/install.sh` | 223 | Bootstrap-установка systemd unit для SOPS/age. P3-кандидат. | OPEN | При росте >300 LOC |
| S5 | `core/internal/bootstrap/install-docker.sh` | 218 | Bootstrap-установка Docker. P3-кандидат. | OPEN | При росте >300 LOC |
| S6 | `core/internal/bootstrap/setup-node.sh` | 215 | Bootstrap-инициализация ноды (пользователи, директории). P3-кандидат. | OPEN | При росте >300 LOC |
| S7 | `core/lib/module-interface.sh` | 206 | STABLE библиотека. Исключена политикой (AGENTS.md: языковая политика п.2). | OPEN | Бессрочно (стабильное API) |
| S8 | `core/lib/node-resolver.sh` | 271 | Thin facade для NodeYaml Python CLI. 271 LOC > порога фасада (150 LOC). Inline python3 -c мигрирован (строки 214/254 — «Replaces», DevPlan 116 B11 T5 U-58). Включён в P2-BACKLOG. | OPEN | 2026-09-30 |

**Примечание (относительно Brief 111):** P2-кандидаты из Brief (validate.sh, scp-deliver.sh, check-dead-code.sh, lint.sh) ЗАКРЫТЫ волнами 106-109 — все стали thin-фасадами <100 LOC и не входят в SHELL-RESIDUAL. Новый кандидат — node-resolver.sh (S8), не был в Brief.

---

## §P2-BACKLOG — Задачи на следующую волну

| # | Задача | Файл | LOC/Scope | Обоснование | Status | Rev |
|---|--------|------|-----------|-------------|--------|-----|
| P2-1 | Strangler-Fig node-resolver.sh | `core/lib/node-resolver.sh` | 271 | >150 LOC facade; inline python3 -c мигрирован (214/254, U-58). Кандидат на декомпозицию: shell facade <100 LOC + Python-модуль (U-86 backlog подтверждён). | OPEN | 2026-09-30 |
| P2-2 | Починить test_add_vhost.py | `tests/test_add_vhost.py` | 7 тестов | FIXED волной B10 (116): test_add_vhost 7 passed (main() exit 1 устранён). См. TEST-DEBT T1. | FIXED | 2026-08-01 |
| P2-3 | Удалить мёртвый код state_machine | `core/internal/bootstrap/lifecycle/state_machine.py:213` | ~100 LOC | resume_phase()/execute_grouped_phase()/_grouped_phases — мёртвый код (TRAP[DEBT] MED 2026-07-31). Ни один тест не покрывает. | OPEN | 2026-08-31 |
| P2-4 | Починить manifest.mk G2/G4/G5 | `makefiles/manifest.mk:25` | ~20 LOC | generate-manifests не fully repairs stale manifests (TRAP[DEBT] MED 2026-07-31). | OPEN | 2026-08-31 |
| P2-5 | Docker operations → shared module | `core/internal/deploy/deploy_engine.py:76` | ~200 LOC | Дублирование docker-операций между deploy_engine, docker_orchestrator, docker.sh (TRAP[DEBT] MED 2026-07-26). | OPEN | 2026-09-30 |

---

## §P3-BACKLOG — Долгосрочные кандидаты

| # | Файл | Суть | Status | Rev |
|---|------|------|--------|-----|
| P3-1 | `core/internal/bootstrap/install-docker.sh` (218 LOC) | Bootstrap, кандидат при росте >300 LOC | OPEN | При росте >300 LOC |
| P3-2 | `core/internal/bootstrap/setup-node.sh` (215 LOC) | Bootstrap, кандидат при росте >300 LOC | OPEN | При росте >300 LOC |
| P3-3 | `core/modules/platform-secrets/install.sh` (223 LOC) | Bootstrap, кандидат при росте >300 LOC | OPEN | При росте >300 LOC |
| P3-4 | `core/modules/postgres/docker-compose.base.yml:50` | POSTGRES_PASSWORD rotation risk (TRAP[DEBT] MED) | OPEN | 2026-12-31 |
| P3-5 | `tests/_conftest/networks.py:90` | Parallel test teardown destroys shared external networks (TRAP[DEBT] MED) | OPEN | 2026-12-31 |
| P3-6 | `core/modules/hermes-agent/watchdog/*` (3 файла) | Watchdog-подсистема не доставляется (0 в Dockerfile/compose/systemd/CI) — решение пользователя на 120 (DevPlan 119 C2, D-1). TRAP[DEBT] HI на agent_watchdog/circuit_breaker/docker_ops. См. `.ai/debt/watchdog-undelivered.md`. | OPEN | 2026-08-31 (решение на 120) |
| P3-7 | `core/internal/scaffold/vhost_renderer.py`, `core/internal/scaffold/nginx_harness.py` | `/etc/letsencrypt/live` хардкод вне `letsencrypt_live()` (DevPlan 119 C6, AUDIT-4 T7). cert_orchestrator мигрирован (118 C7). TRAP[DEBT] на оба файла. См. `.ai/debt/letsencrypt-path-hardcode.md`. | OPEN | При касании vhost_renderer/nginx_harness |

---

## §TEST-DEBT — Зарегистрированные тестовые проблемы

| # | Файл | Суть | Severity | Status | Rev |
|---|------|------|----------|--------|-----|
| T1 | `tests/test_add_vhost.py` | FIXED волной B10 (116): все 7 тестов проходят (main() exit 1 после миграции add-vhost.sh → vhost_renderer.py устранён). | **HI** | FIXED | 2026-08-01 |
| T2 | `tests/test_smoke_litellm.py:72` | litellm first-start crash (httpx.ConnectError) — TRAP[DEBT] MED | MED | OPEN | 2026-09-30 |
| T3 | `tests/test_spool_dir.py:18` | 3 модуля без spool_volume: litellm, langfuse, infra-metrics — TRAP[DEBT] MED | MED | OPEN | 2026-09-30 |
| T4 | `tests/test_volume_spool_consistency.py:82` | Vacuous Check 3 — TRAP[DEBT] MED | MED | OPEN | 2026-09-30 |
| T5 | `tests/test_lib_node_resolver.py:258` | No cleanup of /opt/node-configs/ test files — TRAP[DEBT] LO | LO | OPEN | 2026-12-31 |
| T6 | `tests/_conftest/skip_gate.py:36` | _handle_e2e_error не используется uniformly — TRAP[DEBT] LO | LO | OPEN | 2026-12-31 |
| T7 | `tests/e2e/test_failure_scenarios.py:23` | Мёртвый код (resume_phase) — см. P2-3 | MED | OPEN | 2026-08-31 |

---

## §ARCH-DECISIONS — TRAP[DECISION] с датами пересмотра

| # | Источник | Дата | Sev | Решение | Status | Rev |
|---|----------|------|-----|---------|--------|-----|
| AD1 | AGENTS.md:25 | 2026-07-15 | HI | L1 pushed to ghcr.io as backup (disaster recovery, backup-канал не delivery-модель) | OPEN | При context-specific data в L1 |
| AD2 | AGENTS.md:29 | 2026-07-22 | MED | Strangler-Fig canonical pattern (декомпозиция, не big-bang rewrite) | OPEN | При shell >500 LOC с inline python3 |
| AD3 | AGENTS.md:33 | 2026-07-22 | HI | Bootstrap pipeline — deploy-context step 18 (эволюция state machine, не rewrite) | OPEN | При deploy-context >5min |
| AD4 | AGENTS.md:84 | 2026-07-15 | HI | Dual delivery model: core push-only SCP/rsync, context-overlay git-pull | OPEN | При секретах в context-overlay |
| AD5 | AGENTS.md:163 | 2026-07-21 | HI | Языковая политика — enforcement pre-commit, не CI gate (пересмотрен B11: гейты с allowlist — канон, TRAP 2026-07-31) | OPEN | **2026-10-21** |
| AD6 | AGENTS.md:168 | 2026-07-21 | HI | SSH staging-gate для lib/ssh.sh (single point of failure) | OPEN | При CI-deploy < 300s |
| AD7 | AGENTS.md:175 | 2026-07-22 | HI | Decision Gate: Python-First VALIDATED — continue Strangler-Fig | OPEN | **2026-10-22** |
| AD8 | AGENTS.md (root) | 2026-07-31 | HI | Enforcement-гейты с allowlist — канон (пересмотр TRAP 2026-07-21; D1 01-Brief §1 + волна B11: cross-layer allowlist, audit-format R2, glossary G4, debt-freshness) | OPEN | 2026-10-21 |
| AD9 | P3-наблюдение U-83 | 2026-08-01 | MED | Big-bang коммиты запрещены: процессный лимит ≤2 коммита на DevPlan (docs + feat) в .kilo/rules/_project.md | OPEN | Бессрочно (процессное правило) |
| AD10 | P3-наблюдение U-84 | 2026-08-01 | MED | DevPlans 085/110/111 — superseded-пометки (VR задним числом НЕ пишутся, D5) | OPEN | При ревизии 116-программы |
| AD11 | P3-наблюдение U-87 | 2026-08-01 | LO | CI-комментарии (platform-test debug-вывод) — cleanup: gh api удаление лишних комментариев (manual-шаг, при доступе) | OPEN | При появлении лишних CI-комментариев |
| AD12 | P3-наблюдение U-88 | 2026-08-01 | MED | cert ×3 — тройка по дизайну: cert_orchestrator.py (Python-оркестрация) + issue-cert.sh (S1 justified, acme.sh executor) + s3_ssl_cache.py (Python cache) | OPEN | При изменении контракта сертификатов |

---

## §TRAP-INVENTORY — Полный инвентарь TRAP-аннотаций

### TRAP[BUG] — 251 упоминание в 117 файлах

| Severity | Count | Top files |
|----------|-------|-----------|
| P0 | 31 | state_machine.py (7), remote_executor.py (3), cert_orchestrator.py (1), issue-cert.sh (4), ... |
| P1 | 62 | state_machine.py (9), paths.sh (1), build-ssh-cmd.sh (2), add-vhost.sh (2), ... |
| P2 | 24 | docker_orchestrator.py (6), node_yaml.py (5), dead_code_checker.py (1), ... |
| HI | 11 | infra-metrics (4), test files (3), provision-environment.sh (1), ... |
| MED | 8 | monitoring_config_renderer.py, test files, litellm, ... |
| Прочие | 115 | Документация FIXED-багов, исторические маркеры |

**Актуальные (не-FIXED) TRAP[BUG]** — 22 записи B1-B22, см. Приложение A §A.1.
**Полный список** — см. Приложение A (grep-вывод от 2026-07-31; DevPlan 111 — source of truth).

### TRAP[DEBT] — 33 упоминания в 24 файлах

Полный список D1-D24 — в §1.5 DevPlan 111; сводка в Приложении A §A.2.
Из них: 3 MED, 18 LO, 2 HI, 10 unspecified. HI-записи: D21 (test_add_vhost — см. TEST-DEBT T1).

### TRAP[DECISION] — 248 упоминаний в 132 файлах

| Severity | Count | Top files |
|----------|-------|-----------|
| HI | ~20 | deploy_engine.py, issue-cert.sh, docker-compose.base.yml, mirror.yml, ... |
| MED | ~20 с rev-датами ≥2026-08 | doc_header_validator.py, install-tor-proxy.sh, validate_orchestrator.py, ... |
| Прочие | ~208 | Исторические решения, FIXED, контекстные маркеры |

**Ключевые с rev-датами** — §ARCH-DECISIONS (AD1-AD7). Полный список — Приложение B (grep-вывод от 2026-07-31).

### AGENTS.md TRAP[DECISION] — 7 записей (root + core)

5 HI, 2 MED — все продублированы в §ARCH-DECISIONS (AD1-AD7).

---

## §GITIGNORE

`.gitignore` строка 21: `.ai/*` — покрывает `.ai/debt/`.
Строка 22: `!.ai/plans/` — исключение для планов, НЕ затрагивает `.ai/debt/`.
**Вывод (TASK-B):** `.ai/debt/` уже игнорируется git (верифицировано: `git check-ignore -v .ai/debt/` → `.gitignore:21:.ai/*`). Правки не требуются (D3). Файлы внутри требуют `git add -f`.

---

## §CHECK-FILE-LINES

`core/entrypoints/check-file-lines.sh` сканирует только `${PATHS_CORE_DIR}` (core/) — find-корень на строке 60.
`.ai/debt/` находится в корне репозитория, вне scope скрипта.
**Вывод (TASK-C):** `make check-file-lines` не блокирует `.ai/debt/` — AC5 satisfied автоматически (D4). Правки не требуются. Верифицировано запуском: `bash core/entrypoints/check-file-lines.sh` → exit 0 (38 файлов >500 LOC, все в core/).

---

## Приложение A: Полный TRAP[BUG] инвентарь

### §A.1 Актуальные TRAP[BUG] — кандидаты на исправление (из DevPlan 111 §1.4)

| # | Файл:строка | Дата | Sev | Суть | Статус |
|---|------------|------|-----|------|--------|
| B1 | `core/lib/paths.sh:36` | 2026-07-31 | P1 | PLATFORM_ROOT env silently dropped → Python resolver misses node.yaml | Актуален |
| B2 | `core/internal/bootstrap/build-ssh-cmd.sh:26` | 2026-07-31 | P1 | PLATFORM_ROOT не экспортировался на remote | Актуален |
| B3 | `core/internal/bootstrap/node-lifecycle.sh:51` | 2026-07-31 | P1 | set -e убивал bootstrap при tor.enabled=false | Актуален |
| B4 | `core/internal/bootstrap/lifecycle/state_machine.py:445` | 2026-07-31 | P1 | precondition искал core по CORE_DIR env | Актуален |
| B5 | `core/internal/bootstrap/lifecycle/state_machine.py:516` | 2026-07-31 | P1 | `command -v` через прямой exec НИКОГДА не работал | Актуален |
| B6 | `core/internal/bootstrap/lifecycle/state_machine.py:1158` | 2026-07-31 | P1 | setup_state сбрасывал ВСЕ фазы в pending | Актуален |
| B7 | `core/internal/bootstrap/lifecycle/state_machine.py:2013` | 2026-07-31 | P1 | Чистая нода без secrets не могла забутстрапиться | Актуален |
| B8 | `core/internal/bootstrap/lifecycle/secrets_manager.py:548` | 2026-07-31 | P1 | Random salt breaks idempotency | Актуален |
| B9 | `core/internal/deploy/deploy_history.py:280` | 2026-07-31 | P1 | prune удалял СВЕЖИЕ снапшоты DeployHistory | Актуален |
| B10 | `core/internal/shared/vps_readiness.py:30` | 2026-07-31 | P1 | Bash $first \|\| json_diag+="," executes false → broken JSON | Актуален |
| B11 | `core/internal/test_runner.py:173` | 2026-07-31 | P1 | Атрибуты считывались с \<testsuites\> wrapper | Актуален |
| B12 | `core/internal/provision-environment.sh:24` | 2026-07-31 | HI | Stale source deleted audit_logging.sh broke make provision | Актуален |
| B13 | `core/internal/scaffold/add-vhost.sh:28` | 2026-07-31 | P1 | python3 -m core.* fails outside repo root | Актуален |
| B14 | `core/internal/bootstrap/issue-cert.sh:56` | 2026-07-22 | P0 | mkcert certs survived bootstrap — no issuer check | Актуален |
| B15 | `core/internal/bootstrap/remote_executor.py:21` | 2026-07-23 | P0 | VPS self-SSH loop | Актуален |
| B16 | `core/internal/bootstrap/lifecycle/state_machine.py:1972` | 2026-07-23 | P0 | non_fatal=True swallowed decrypt failures | Актуален |
| B17 | `core/internal/bootstrap/lifecycle/state_machine.py:1983` | 2026-07-23 | P0 | source secrets.sh без зависимостей | Актуален |
| B18 | `core/internal/bootstrap/lifecycle/state_machine.py:2261` | 2026-07-24 | P0 | invoke_module_interface is a bash function, not executable | Актуален |
| B19 | `core/internal/bootstrap/overlay_deliverer.py:17` | 2026-07-24 | P0 | node-update не доставлял core/ на VPS | Актуален |
| B20 | `core/modules/infra-metrics/docker-compose.base.yml:74` | 2026-07-27 | HI | wget missing in scratch-based v0.55.1 → HEALTHCHECK fails | Актуален |
| B21 | `core/modules/infra-metrics/docker-compose.test.yml:30` | 2026-07-27 | HI | cadvisor-test inherited broken wget-based HEALTHCHECK | Актуален |
| B22 | `core/modules/infra-metrics/healthcheck.sh:37` | 2026-07-27 | HI | CONTAINERS used canonical names, test uses -test suffix | Актуален |

**Примечание:** большинство TRAP[BUG] в коде — исторические маркеры документирования УЖЕ исправленных багов (паттерн «FIXED», «Root: ... Fix: ...»). Выше — только записи без явного указания на исправление. Чистка stale TRAP-аннотаций — отдельная задача (Non-Goals DevPlan 111).

### §A.2 TRAP[DEBT] — все записи (из DevPlan 111 §1.5)

| # | Файл:строка | Дата | Sev | Суть |
|---|------------|------|-----|------|
| D1 | `core/internal/lint/doc_header_validator.py:52` | 2026-07-31 | LO | check_file_lines/check_shellcheck_directives в Brief не существуют |
| D2 | `core/internal/lint/doc_header_validator.py:479` | 2026-07-31 | LO | namespace_collision_names не реализуется |
| D3 | `core/internal/bootstrap/deploy/docker_orchestrator.py:37` | 2026-07-22 | P2 | 5 test-side failures в test_docker_orchestrator.py |
| D4 | `core/internal/bootstrap/lifecycle/state_machine.py:213` | 2026-07-31 | MED | resume_phase()/execute_grouped_phase() — мёртвый код |
| D5 | `core/internal/bootstrap/overlay_deliverer.py:21` | 2026-07-26 | LO | node-resolver.sh:306-316 inline python3 -c |
| D6 | `core/internal/deploy/deploy_engine.py:76` | 2026-07-26 | MED | Docker operations library — кандидат на shared модуль |
| D7 | `core/internal/hooks/check-no-new-inline-python3.sh:25-28` | 2026-07-26 | — | 4 whitelist-записи (yaml_read.sh, generate-catalog.sh, adopt-project.sh, add-vhost.sh) |
| D8 | `core/internal/scaffold/project_adopter.py:46` | 2026-07-26 | LO | gen_env_platform.py — CLI-first design prevents direct import |
| D9 | `core/internal/scaffold/project_adopter.py:52` | 2026-07-26 | LO | node.yaml path resolution duplicated across 4+ scripts |
| D10 | `core/modules/backup-cron/scripts/s3_client.py:64` | 2026-07-12 | LO | S3 timeout not wired to boto3 Config |
| D11 | `core/modules/postgres/docker-compose.base.yml:50` | 2026-07-17 | MED | POSTGRES_PASSWORD rotation risk |
| D12 | `core/modules/postgres/healthcheck.sh:15` | 2026-07-15 | LO | Container names hardcoded — непригоден для -test stack |
| D13 | `tests/gates/test_gate_compose_no_base_image.py:235` | 2026-07-14 | — | root compose include-based, hermes-agent image в base.yml |
| D14 | `tests/gates/test_gate_dead_code.py:650` | future | — | test_gate_stale_comments — будущая реализация |
| D15 | `tests/unit/test_spool_dir.py:18` | 2026-07-15 | MED | 3 модуля без spool_volume: litellm, langfuse, infra-metrics |
| D16 | `tests/test_lib_node_resolver.py:258` | 2026-07-08 | LO | No cleanup of /opt/node-configs/ test files on failure |
| D17 | `tests/_conftest/skip_gate.py:36` | 2026-07-08 | LO | _handle_e2e_error не используется uniformly |
| D18 | `tests/_conftest/networks.py:90` | 2026-07-15 | MED | Parallel test teardown destroys shared external networks |
| D19 | `tests/test_smoke_litellm.py:72` | 2026-07-18 | MED | litellm first-start crash (httpx.ConnectError) |
| D20 | `tests/test_volume_spool_consistency.py:82` | 2026-07-15 | MED | Vacuous Check 3 (spool coverage via Phase 2 grep) |
| D21 | `tests/test_add_vhost.py:29` | 2026-07-31 | **HI** | **Все 7 тестов падают (main() exit 1)** |
| D22 | `tests/e2e/test_failure_scenarios.py:23` | — | — | Мёртвый код resume_phase() (см. D4) |
| D23 | `makefiles/manifest.mk:25` | 2026-07-31 | MED | generate-manifests omits G2/G4/G5 — fix-gate не fully repairs |
| D24 | `.github/workflows/mirror.yml:209` | 2026-07-07 | LOW | Manual force-sync may be needed |

### §A.3 Распределение по файлам (grep-верификация 2026-07-31)

Top-25 файлов по числу TRAP[BUG]:

| Файл | Count |
|------|-------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | 10 |
| `core/internal/bootstrap/issue-cert.sh` | 10 |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | 9 |
| `tests/test_smoke_langfuse.py` | 8 |
| `tests/_conftest/smoke.py` | 8 |
| `tests/test_component_hermes.py` | 7 |
| `core/internal/shared/node_yaml.py` | 6 |
| `tests/test_smoke_infra_metrics.py` | 5 |
| `tests/gates/test_gate_test_infra_consistency.py` | 5 |
| `core/modules/infra-metrics/docker-compose.test.yml` | 5 |
| `core/internal/bootstrap/remote_executor.py` | 5 |
| `tests/test_unit_yaml_query.py` | 4 |
| `tests/test_hermes_l2_fallback.py` | 4 |
| `core/internal/bootstrap/s3_ssl_cache.py` | 4 |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | 4 |
| `core/internal/bootstrap/build-ssh-cmd.sh` | 4 |
| `tests/unit/test_test_runner.py` | 3 |
| `tests/test_smoke_monitoring.py` | 3 |
| `tests/test_smoke_hermes.py` | 3 |
| `tests/test_hermes_init.py` | 3 |
| `core/modules/nginx/config/hermes-dashboard.conf` | 3 |
| `core/internal/lint/doc_header_validator.py` | 3 |
| `core/internal/bootstrap/overlay_deliverer.py` | 3 |
| `core/internal/bootstrap/core_deliverer.py` | 3 |
| `.github/workflows/core-deploy.yml` | 3 |

Полный список 133 файлов с счётчиками — grep-вывод от 2026-07-31 (DevPlan 111, Шаг 1 — source of truth).

---

## Приложение B: Полный TRAP[DECISION] инвентарь

### §B.1 Ключевые решения с rev-датами (из DevPlan 111 §1.6 — см. §ARCH-DECISIONS)

AD1-AD7 — полная таблица в §ARCH-DECISIONS. Все 7 TRAP[DECISION] из AGENTS.md (root + core) продублированы с rev-датами.

### §B.2 Распределение по файлам (grep-верификация 2026-07-31)

Top-25 файлов по числу TRAP[DECISION]:

| Файл | Count |
|------|-------|
| `core/internal/deploy/deploy_engine.py` | 11 |
| `core/modules/infra-metrics/docker-compose.base.yml` | 8 |
| `tests/e2e/test_bootstrap_pipeline.py` | 7 |
| `core/modules/hermes-agent/docker-compose.base.yml` | 7 |
| `core/modules/litellm/docker-compose.base.yml` | 6 |
| `core/internal/bootstrap/issue-cert.sh` | 6 |
| `.github/workflows/mirror.yml` | 6 |
| `core/internal/lint/doc_header_validator.py` | 5 |
| `core/internal/bootstrap/install-tor-proxy.sh` | 5 |
| `core/internal/bootstrap/deploy/deploy_orchestrator.py` | 5 |
| `core/modules/nginx/config/platform-default.conf.template` | 4 |
| `core/modules/monitoring/docker-compose.base.yml` | 4 |
| `core/modules/logging/config/loki-config.yml` | 4 |
| `core/modules/AGENTS.md` | 4 |
| `core/internal/validate/validate_orchestrator.py` | 4 |
| `core/internal/llm/key_provisioner.py` | 4 |
| `core/internal/lint/dead_code_checker.py` | 4 |
| `tests/e2e/test_failure_scenarios.py` | 3 |
| `tests/_conftest/smoke.py` | 3 |
| `tests/_conftest/skip_gate.py` | 3 |
| `core/modules/redis/docker-compose.base.yml` | 3 |
| `core/modules/postgres/docker-compose.test.yml` | 3 |
| `core/modules/nginx/config/nginx.conf` | 3 |
| `core/lib/docker.sh` | 3 |
| `core/internal/shared/vps_readiness.py` | 3 |

Полный список 132 файлов (канонические числа DevPlan 111 §1.1; grep-верификация 2026-07-31: 169 файлов с учётом .conf/.template/.mk расширений).

---

## Next Steps

### Wave 1 (выполнено)
- TASK-A: реестр создан (этот файл)
- TASK-B: .gitignore верифицирован — правки не требуются (§GITIGNORE)
- TASK-C: check-file-lines.sh верифицирован — правки не требуются (§CHECK-FILE-LINES)
- TASK-D: `git add -f` выполнен (без коммита — по запросу)

### Wave 2 (QA)
- TASK-E: QA-верификация реестра против AC1-AC7 + VerificationReport

### После QA
- P2-1..P2-5: отдельные DevPlan'ы на основе P2-BACKLOG (Non-Goals: реестр НЕ создаёт DevPlan'ы)
- AD5 (2026-10-21) / AD7 (2026-10-22): пересмотр решений языковой политики и Python-First

### B11 (2026-08-01, DevPlan 116 T7/T8)
- Формат записей мигрирован: Status (OPEN/FIXED/SUPERSEDED) + Rev (дата ИЛИ условие) — гейт свежести test_gate_debt_registry.py (stale >90 дней → RED)
- T1 (test_add_vhost) → FIXED (B10: 7 passed); P2-2 → FIXED (тот же фикс)
- U-83..88 решения: AD8 (enforcement allowlist — канон), AD9 (≤2 коммита на DevPlan), AD10 (superseded 085/110/111), AD11 (CI-комментарии cleanup), AD12 (cert ×3 по дизайну)
