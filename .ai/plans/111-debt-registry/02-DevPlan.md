# $ARTIFACT_CONTRACT
# GREP_SUMMARY: devplan-111, debt-registry, strangler-closeout, TRAP-inventory, shell-residual, P2-P3-backlog, ARCH-DECISIONS
# STRUCTURE: ┌Full TRAP scan (BUG/DEBT/DECISION)┐ → ◇ Problem Matrix → ◇ Draft Code Graph → ◇ $TASKS → ◇ $PARALLEL_GROUPS → ◇ $TEST_SPEC → ⎋ Delegation
## @PURPOSE Создание единого debt-реестра `.ai/debt/001-Strangler-Fig-Closeout.md` с полным TRAP-инвентарём кодовой базы после завершения Strangler-Fig (волны 099-109)
## @DESCRIPTION
Девплан на основе полного сканирования кодовой базы (Шаг 1 — research, без правок кода).
Содержит результаты TRAP-инвентаризации, Problem Matrix актуальных проблем, уточнённый
список shell-скриптов >200 LOC, $TASKS для создания реестра, $PARALLEL_GROUPS, $TEST_SPEC.

**Ключевая корректировка Brief:** P2-BACKLOG, описанный в Brief, фактически ЗАКРЫТ
волнами 106-109. validate.sh (18 LOC), scp-deliver.sh (59 LOC), check-dead-code.sh (14 LOC),
lint.sh (40 LOC), check-doc-headers.sh (17 LOC) — все стали thin-фасадами <100 LOC.
Фактический P2-бэклог пересчитан ниже по результатам скана.

**Новый кандидат >200 LOC:** `core/lib/node-resolver.sh` (271 LOC) — не был в Brief.
Заявлен как «thin facade для NodeYaml Python CLI», но 271 LOC превышает порог
фасада (<150 LOC по языковой политике). Требует анализа в реестре.

## @RATIONALE
- Отсутствие debt-реестра — риск потери контекста между волнами после завершения Strangler-Fig
- AGENTS.md ссылается на `.ai/debt/` как на source of truth для долгов, но директория не существует
- DevPlan 096 (debt-registry-and-vr-sync) был создан, но файлы не были написаны (пустая директория)
- Полное TRAP-сканирование (BUG/DEBT/DECISION) необходимо для инвентаризации ВСЕХ известных проблем
- 532 TRAP-аннотации в 117+24+132 уникальных файлах — без реестра невозможно приоритизировать

## @ACCEPTANCE_CRITERIA
- AC1: `.ai/debt/001-Strangler-Fig-Closeout.md` создан и force-added в git (`git add -f`)
- AC2: Все секции заполнены: SHELL-RESIDUAL, P2-BACKLOG (пересчитанный), P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS
- AC3: Каждая запись содержит: файл, LOC, обоснование исключения/отсрочки, rev-дату
- AC4: Все TRAP[DECISION] из AGENTS.md с будущими rev-датами (2026-08+) продублированы в реестре
- AC5: `make check-file-lines` не блокирует `.ai/debt/` (автоматически — скрипт сканирует только `core/`)
- AC6: `.gitignore` содержит `.ai/*` (уже покрывает `.ai/debt/`); файл закоммичен с `git add -f`
- AC7: `grep`-верификация: реестр существует, содержит все обязательные секции, записи валидны

## @IMPLEMENTS Brief 111
## @IMPACTS
- .ai/debt/001-Strangler-Fig-Closeout.md (NEW — создаётся)
- .gitignore (проверка — УЖЕ содержит `.ai/*`, изменений не требуется)
- make check-file-lines (НЕ требует изменений — сканирует только `core/`)
## @REQUIRES
- Результаты миграционных волн 099-109 для актуального P2-пересчёта
- Полный TRAP-скан кодовой базы (выполнен в Шаге 1)

---

## Шаг 1 — Результаты полного TRAP-сканирования кодовой базы

### 1.1 Сводка TRAP-аннотаций

| Тип | Всего упоминаний | Уникальных файлов | P0 | P1 | P2 | MED/HI/LO |
|-----|-----------------|-------------------|----|----|----|------------|
| **TRAP[BUG]** | 251 | 117 | 31 | 62 | 24 | ~88 прочих |
| **TRAP[DEBT]** | 33 | 24 | — | — | — | 3 MED, 18 LO, 2 HI, 10 unspecified |
| **TRAP[DECISION]** | 248 | 132 | — | — | — | ~20 с rev-датами ≥2026-08 |
| **AGENTS.md TRAP[DECISION]** | 7 | 2 (root + core) | — | — | — | 5 HI, 2 MED |
| **Итого** | **539** | **~200** | 31 | 62 | 24 | — |

### 1.2 Debt-файлы в `.ai/plans/*/*-Debt.md`

**Результат: 0 файлов.** Во всём `.ai/plans/` нет ни одного файла, соответствующего
артефакт-грамматике `{NN}-Debt.md`. DevPlan 096 (debt-registry-and-vr-sync) создал
директорию, но не написал ни одного артефакта.

### 1.3 `.ai/debt/` — статус

Директория **не существует**. `.gitignore` содержит `.ai/*` (строка 21), что уже
покрывает `.ai/debt/`. Исключение `!.ai/plans/` (строка 22) не затрагивает `.ai/debt/`.

### 1.4 Актуальные TRAP[BUG] — кандидаты на включение в реестр

Ниже — наиболее значимые TRAP[BUG], которые **не являются чисто историческими**
(т.е. не «FIXED» и не просто документируют уже исправленное):

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

**Примечание:** Большинство TRAP[BUG] в коде — исторические маркеры, документирующие
УЖЕ исправленные баги (паттерн: «FIXED», «Root: ... Fix: ...»). Выше перечислены только
те, где нет явного указания на исправление. Полный инвентарь — в реестре.

### 1.5 Актуальные TRAP[DEBT] — все записи

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

### 1.6 TRAP[DECISION] из AGENTS.md с будущими rev-датами (для ARCH-DECISIONS)

| # | Файл | Дата | Sev | Решение | Rev-дата |
|---|------|------|-----|---------|----------|
| AD1 | AGENTS.md:25 | 2026-07-15 | HI | L1 pushed to ghcr.io as backup | «if L1 starts carrying context-specific data» |
| AD2 | AGENTS.md:29 | 2026-07-22 | MED | Strangler-Fig canonical pattern | «если shell >500 LOC с inline python3» |
| AD3 | AGENTS.md:33 | 2026-07-22 | HI | Bootstrap pipeline — deploy-context step 18 | «если deploy-context >5min → async» |
| AD4 | AGENTS.md:84 | 2026-07-15 | HI | Dual delivery: core push-only, context git-pull | «если context-overlay начнёт нести секреты» |
| AD5 | AGENTS.md:163 | 2026-07-21 | HI | Языковая политика — enforcement через AGENTS.md + pre-commit | **2026-10-21** (через квартал) |
| AD6 | AGENTS.md:168 | 2026-07-21 | HI | SSH staging-gate для lib/ssh.sh | «если CI-deploy стабильно < 300s → снизить timeout» |
| AD7 | AGENTS.md:175 | 2026-07-22 | HI | Decision Gate: Python-First VALIDATED | **2026-10-22** (переоценка через 2 недели на prod) |

### 1.7 Shell-скрипты >200 LOC — уточнённый список

| # | Файл | LOC | Секция | Обоснование |
|---|------|-----|--------|-------------|
| S1 | `core/internal/bootstrap/issue-cert.sh` | 704 | SHELL-RESIDUAL | acme.sh executor, осознанно пропущен (TRAP 080: shell subprocess by design) |
| S2 | `core/internal/bootstrap/install-tor-proxy.sh` | 422 | SHELL-RESIDUAL | Одноразовый bootstrap, не содержит бизнес-логики для извлечения |
| S3 | `core/lib/healthcheck.sh` | 388 | SHELL-RESIDUAL | STABLE библиотека, исключена политикой (AGENTS.md: языковая политика п.2) |
| S4 | **`core/lib/node-resolver.sh`** | **271** | **P2-BACKLOG (NEW)** | **Не был в Brief. Заявлен как thin facade, но 271 LOC > порога 150 LOC. 306-316: inline python3 -c (Tier 1 триггер). Требует анализа.** |
| S5 | `core/modules/platform-secrets/install.sh` | 223 | P3-BACKLOG | Bootstrap-установка systemd unit. Кандидат при росте. |
| S6 | `core/internal/bootstrap/install-docker.sh` | 218 | P3-BACKLOG | Bootstrap-установка Docker. Кандидат при росте. |
| S7 | `core/internal/bootstrap/setup-node.sh` | 215 | P3-BACKLOG | Bootstrap-инициализация ноды. Кандидат при росте. |
| S8 | `core/lib/module-interface.sh` | 206 | SHELL-RESIDUAL | STABLE библиотека, исключена политикой (AGENTS.md: языковая политика п.2) |

**Изменения относительно Brief:**
- P2-BACKLOG из Brief (validate.sh 251, scp-deliver.sh 251, check-dead-code.sh 86, lint.sh) — **ЗАКРЫТ**: все стали thin-фасадами <100 LOC
- **Добавлен:** `node-resolver.sh` (271 LOC) — новый кандидат, не был в Brief
- P3-BACKLOG без изменений (install-docker.sh, setup-node.sh, platform-secrets/install.sh)

### 1.8 Пересчитанный P2-BACKLOG (фактический)

| # | Задача | Файл | LOC | Обоснование |
|---|--------|------|-----|-------------|
| P2-1 | Анализ node-resolver.sh | `core/lib/node-resolver.sh` | 271 | >150 LOC facade, inline python3 -c на L306-316, кандидат на Strangler-Fig |
| P2-2 | Тесты test_add_vhost.py | `tests/test_add_vhost.py` | — | Все 7 тестов падают (HI, TRAP[DEBT] 2026-07-31) |
| P2-3 | Мёртвый код state_machine | `core/internal/bootstrap/lifecycle/state_machine.py:213` | — | resume_phase()/execute_grouped_phase()/_grouped_phases — мёртвый код |
| P2-4 | manifest.mk G2/G4/G5 | `makefiles/manifest.mk:25` | — | generate-manifests не fully repairs stale manifests |
| P2-5 | Docker operations shared lib | `core/internal/deploy/deploy_engine.py:76` | — | Кандидат на shared модуль (TRAP[DEBT]) |

### 1.9 P3-BACKLOG (без изменений относительно Brief)

| # | Файл | LOC | Обоснование |
|---|------|-----|-------------|
| P3-1 | `core/internal/bootstrap/install-docker.sh` | 218 | Bootstrap, кандидат при росте |
| P3-2 | `core/internal/bootstrap/setup-node.sh` | 215 | Bootstrap, кандидат при росте |
| P3-3 | `core/modules/platform-secrets/install.sh` | 223 | Bootstrap, кандидат при росте |
| P3-4 | `core/modules/postgres/docker-compose.base.yml:50` | — | POSTGRES_PASSWORD rotation risk (TRAP[DEBT] MED) |
| P3-5 | `tests/_conftest/networks.py:90` | — | Parallel test teardown destroys shared networks (TRAP[DEBT] MED) |

---

## Problem Matrix

| # | Problem | Severity | Files | AC | Resolution |
|---|---------|----------|-------|----|------------|
| PM1 | Debt-реестр отсутствует — 539 TRAP-аннотаций не инвентаризированы | **HI** | .ai/debt/ (NEW) | AC1-AC7 | Создать реестр (TASK-A) |
| PM2 | P2-BACKLOG из Brief устарел — все кандидаты уже мигрированы | MED | Brief 111 §P2-BACKLOG | AC2 | Пересчитать P2 по факту (TASK-A §P2-BACKLOG) |
| PM3 | node-resolver.sh (271 LOC) — не был в Brief, превышает порог фасада | MED | core/lib/node-resolver.sh | AC3 | Включить в P2-BACKLOG с анализом |
| PM4 | test_add_vhost.py — все 7 тестов падают | **HI** | tests/test_add_vhost.py | AC2 (TEST-DEBT) | Зарегистрировать в TEST-DEBT |
| PM5 | .ai/debt/ в .gitignore — требует `git add -f` | LOW | .gitignore | AC6 | Проверить покрытие, задокументировать |
| PM6 | check-file-lines не сканирует .ai/debt/ (scope: core/ only) | NONE | core/entrypoints/check-file-lines.sh | AC5 | Не требуется действий — автоматически |
| PM7 | 0 Debt-файлов в .ai/plans/ — нет истории долговых артефактов | MED | .ai/plans/*/ | — | DevPlan 111 — первый реестр |
| PM8 | TRAP[DECISION] с rev-датами 2026-10 — risk пропуска дедлайнов | MED | AGENTS.md | AC4 | Продублировать в ARCH-DECISIONS |

---

## Architecture Overview

### Draft Code Graph

```
.ai/debt/001-Strangler-Fig-Closeout.md  ← TASK-A (создание)
  ├── §SHELL-RESIDUAL (8 записей: S1-S8)
  ├── §P2-BACKLOG (5 записей: P2-1..P2-5)
  ├── §P3-BACKLOG (5 записей: P3-1..P3-5)
  ├── §TEST-DEBT (минимум D21 + сводка)
  ├── §ARCH-DECISIONS (AD1-AD7 из AGENTS.md)
  └── §TRAP-INVENTORY (полная таблица BUG/DEBT/DECISION)

.gitignore  ← TASK-C (верификация, без правок)
  └── .ai/* (строка 21) — уже покрывает .ai/debt/

make check-file-lines  ← TASK-D (верификация, без правок)
  └── scope: ${PATHS_CORE_DIR} → core/ — .ai/debt/ вне scope
```

### Step-by-Step Data Flow

```
1. TASK-A: Coder создаёт .ai/debt/001-Strangler-Fig-Closeout.md
   └── Данные из §1.4-1.9 настоящего DevPlan
   └── Формат: Markdown с таблицами, каждая запись: файл, LOC, обоснование, rev-дата

2. TASK-B: Coder проверяет .gitignore покрытие
   └── .ai/* на строке 21 — уже покрывает .ai/debt/
   └── Документирует в реестре §GITIGNORE

3. TASK-C: Coder проверяет check-file-lines.sh
   └── Скрипт сканирует только ${PATHS_CORE_DIR} (core/)
   └── .ai/debt/ вне scope — AC5 satisfied автоматически

4. TASK-D: Coder выполняет git add -f + commit
   └── git add -f .ai/debt/001-Strangler-Fig-Closeout.md
   └── git commit с сообщением о создании debt-реестра

5. TASK-E: QA верифицирует реестр
   └── grep-тесты: все секции присутствуют
   └── make check-file-lines проходит
   └── Все AC1-AC7 подтверждены
```

---

## $TASKS

| ID | Task | Owner | Output | Dependencies | Complexity | AC Mapping |
|----|------|-------|--------|-------------|------------|------------|
| **TASK-A** | Создать `.ai/debt/001-Strangler-Fig-Closeout.md` | Coder | Файл реестра | None | 6 | AC1, AC2, AC3, AC4 |
| **TASK-B** | Верифицировать `.gitignore` покрытие `.ai/debt/` | Coder | Документирование в реестре §GITIGNORE | TASK-A | 1 | AC6 |
| **TASK-C** | Верифицировать `check-file-lines.sh` scope | Coder | Документирование в реестре §CHECK-FILE-LINES | TASK-A | 1 | AC5 |
| **TASK-D** | Git add -f + commit реестра | Coder | Коммит в репозитории | TASK-A, TASK-B, TASK-C | 1 | AC1, AC6 |
| **TASK-E** | QA-верификация реестра | QA | VerificationReport (grep-тесты) | TASK-D | 3 | AC1-AC7 |

### Merge Rule check:
- TASK-B (1 file, ~5 строк) — merge в TASK-A (parent)
- TASK-C (1 file, ~5 строк) — merge в TASK-A (parent)
- TASK-D (git add -f) — merge в TASK-A (финальный шаг)

**После merge:** 2 задачи — TASK-A (создание + git) и TASK-E (верификация).

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **TASK-A** — создание `.ai/debt/001-Strangler-Fig-Closeout.md` (включает TASK-B, TASK-C, TASK-D)
- Command: `coder Read DevPlan.md, implement Wave 1: TASK-A`

### Wave 2 (depends on Wave 1)
- **TASK-E** — QA-верификация
- Command: `qa Read DevPlan.md, verify TASK-A output against AC1-AC7`

---

## Содержание TASK-A — точная структура реестра

### Файл: `.ai/debt/001-Strangler-Fig-Closeout.md`

```markdown
# Strangler-Fig Closeout — Debt Registry

> Создан: 2026-07-31 | DevPlan 111 | Волны: 099-109 (Strangler-Fig завершён)
> Назначение: Единый реестр архитектурного долга после завершения миграции shell→Python

## §SHELL-RESIDUAL — Скрипты >200 LOC, исключённые из миграции

| # | Файл | LOC | Обоснование исключения | Rev-дата |
|---|------|-----|------------------------|----------|
| S1 | `core/internal/bootstrap/issue-cert.sh` | 704 | acme.sh executor (DNS-01/HTTP-01). Осознанно пропущен в Wave 5a — shell subprocess by design. TRAP[DECISION] 2026-07-26. | 2026-12-31 |
| S2 | `core/internal/bootstrap/install-tor-proxy.sh` | 422 | Одноразовый bootstrap. Не содержит бизнес-логики для извлечения в Python. TRAP[DECISION] webtunnel degradation. | При росте >500 LOC |
| S3 | `core/lib/healthcheck.sh` | 388 | STABLE библиотека. Исключена политикой (AGENTS.md: языковая политика п.2 — lib-функции низкого уровня). | Бессрочно (стабильное API) |
| S4 | `core/modules/platform-secrets/install.sh` | 223 | Bootstrap-установка systemd unit для SOPS/age. P3-кандидат. | При росте >300 LOC |
| S5 | `core/internal/bootstrap/install-docker.sh` | 218 | Bootstrap-установка Docker. P3-кандидат. | При росте >300 LOC |
| S6 | `core/internal/bootstrap/setup-node.sh` | 215 | Bootstrap-инициализация ноды (пользователи, директории). P3-кандидат. | При росте >300 LOC |
| S7 | `core/lib/module-interface.sh` | 206 | STABLE библиотека. Исключена политикой (AGENTS.md: языковая политика п.2). | Бессрочно (стабильное API) |
| S8 | `core/lib/node-resolver.sh` | 271 | Thin facade для NodeYaml Python CLI. 271 LOC > порога фасада (150 LOC). Inline python3 -c на L306-316 (Tier 1 триггер). Включён в P2-BACKLOG. | 2026-09-30 |

## §P2-BACKLOG — Задачи на следующую волну

| # | Задача | Файл | LOC/Scope | Обоснование | Rev-дата |
|---|--------|------|-----------|-------------|----------|
| P2-1 | Strangler-Fig node-resolver.sh | `core/lib/node-resolver.sh` | 271 | >150 LOC facade, inline python3 -c L306-316 (TRAP[DEBT] overlay_deliverer.py:21). Кандидат на декомпозицию: shell facade <100 LOC + Python-модуль. | 2026-09-30 |
| P2-2 | Починить test_add_vhost.py | `tests/test_add_vhost.py` | 7 тестов | Все 7 тестов падают — main() exit 1 (TRAP[DEBT] HI 2026-07-31). Корневая причина: add-vhost.sh мигрирован в vhost_renderer.py, тесты не обновлены. | 2026-08-31 |
| P2-3 | Удалить мёртвый код state_machine | `core/internal/bootstrap/lifecycle/state_machine.py:213` | ~100 LOC | resume_phase()/execute_grouped_phase()/_grouped_phases — мёртвый код (TRAP[DEBT] MED 2026-07-31). Ни один тест не покрывает. | 2026-08-31 |
| P2-4 | Починить manifest.mk G2/G4/G5 | `makefiles/manifest.mk:25` | ~20 LOC | generate-manifests не fully repairs stale manifests (TRAP[DEBT] MED 2026-07-31). | 2026-08-31 |
| P2-5 | Docker operations → shared module | `core/internal/deploy/deploy_engine.py:76` | ~200 LOC | Дублирование docker-операций между deploy_engine, docker_orchestrator, docker.sh (TRAP[DEBT] MED 2026-07-26). | 2026-09-30 |

## §P3-BACKLOG — Долгосрочные кандидаты

| # | Файл | Суть | Rev-дата |
|---|------|------|----------|
| P3-1 | `core/internal/bootstrap/install-docker.sh` (218 LOC) | Bootstrap, кандидат при росте >300 LOC | При росте |
| P3-2 | `core/internal/bootstrap/setup-node.sh` (215 LOC) | Bootstrap, кандидат при росте >300 LOC | При росте |
| P3-3 | `core/modules/platform-secrets/install.sh` (223 LOC) | Bootstrap, кандидат при росте >300 LOC | При росте |
| P3-4 | `core/modules/postgres/docker-compose.base.yml:50` | POSTGRES_PASSWORD rotation risk (TRAP[DEBT] MED) | 2026-12-31 |
| P3-5 | `tests/_conftest/networks.py:90` | Parallel test teardown destroys shared external networks (TRAP[DEBT] MED) | 2026-12-31 |

## §TEST-DEBT — Зарегистрированные тестовые проблемы

| # | Файл | Суть | Severity | Rev-дата |
|---|------|------|----------|----------|
| T1 | `tests/test_add_vhost.py` | Все 7 тестов падают (main() exit 1 после миграции add-vhost.sh → vhost_renderer.py) | **HI** | 2026-08-31 |
| T2 | `tests/test_smoke_litellm.py:72` | litellm first-start crash (httpx.ConnectError) — TRAP[DEBT] MED | MED | 2026-09-30 |
| T3 | `tests/test_spool_dir.py:18` | 3 модуля без spool_volume: litellm, langfuse, infra-metrics — TRAP[DEBT] MED | MED | 2026-09-30 |
| T4 | `tests/test_volume_spool_consistency.py:82` | Vacuous Check 3 — TRAP[DEBT] MED | MED | 2026-09-30 |
| T5 | `tests/test_lib_node_resolver.py:258` | No cleanup of /opt/node-configs/ test files — TRAP[DEBT] LO | LO | 2026-12-31 |
| T6 | `tests/_conftest/skip_gate.py:36` | _handle_e2e_error не используется uniformly — TRAP[DEBT] LO | LO | 2026-12-31 |
| T7 | `tests/e2e/test_failure_scenarios.py:23` | Мёртвый код (resume_phase) — см. P2-3 | MED | 2026-08-31 |

## §ARCH-DECISIONS — TRAP[DECISION] с датами пересмотра

| # | Источник | Дата | Sev | Решение | Rev-дата |
|---|----------|------|-----|---------|----------|
| AD1 | AGENTS.md:25 | 2026-07-15 | HI | L1 pushed to ghcr.io as backup | При context-specific data в L1 |
| AD2 | AGENTS.md:29 | 2026-07-22 | MED | Strangler-Fig canonical pattern | При shell >500 LOC с inline python3 |
| AD3 | AGENTS.md:33 | 2026-07-22 | HI | Bootstrap pipeline — deploy-context step 18 | При deploy-context >5min |
| AD4 | AGENTS.md:84 | 2026-07-15 | HI | Dual delivery model | При секретах в context-overlay |
| AD5 | AGENTS.md:163 | 2026-07-21 | HI | Языковая политика — enforcement pre-commit, не CI gate | **2026-10-21** |
| AD6 | AGENTS.md:168 | 2026-07-21 | HI | SSH staging-gate для lib/ssh.sh | При CI-deploy < 300s |
| AD7 | AGENTS.md:175 | 2026-07-22 | HI | Decision Gate: Python-First VALIDATED | **2026-10-22** |

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

**Полный список** — см. Приложение A (внешняя ссылка: grep results от 2026-07-31).

### TRAP[DEBT] — 33 упоминания в 24 файлах

Полный список в §1.5 настоящего DevPlan.

### TRAP[DECISION] — 248 упоминаний в 132 файлах

Ключевые с rev-датами — в §ARCH-DECISIONS выше. Полный список — в Приложении B.

## §GITIGNORE

`.gitignore` строка 21: `.ai/*` — покрывает `.ai/debt/`.
Строка 22: `!.ai/plans/` — исключение для планов, НЕ затрагивает `.ai/debt/`.
**Вывод:** `.ai/debt/` уже игнорируется git. Файлы внутри требуют `git add -f`.

## §CHECK-FILE-LINES

`core/entrypoints/check-file-lines.sh` сканирует только `${PATHS_CORE_DIR}` (core/).
`.ai/debt/` находится в корне репозитория, вне scope скрипта.
**Вывод:** `make check-file-lines` не блокирует `.ai/debt/` — AC5 satisfied автоматически.

---

## Приложение A: Полный TRAP[BUG] инвентарь

(Детальная таблица всех 251 TRAP[BUG] с файл:строка, дата, severity, суть, статус —
 генерируется из grep-вывода 2026-07-31. В реестре — сводная статистика + ссылка на DevPlan 111.)

## Приложение B: Полный TRAP[DECISION] инвентарь

(Детальная таблица всех 248 TRAP[DECISION] — генерируется из grep-вывода 2026-07-31.
 В реестре — ключевые с rev-датами в §ARCH-DECISIONS + ссылка на DevPlan 111.)
```

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_exists` | Файл `.ai/debt/001-Strangler-Fig-Closeout.md` существует и не пуст | `.ai/debt/` |
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_all_sections` | Все обязательные секции присутствуют: SHELL-RESIDUAL, P2-BACKLOG, P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS, TRAP-INVENTORY | `.ai/debt/` |
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_shell_residual_entries` | SHELL-RESIDUAL содержит 8 записей с файл/LOC/обоснование/rev-дата | `.ai/debt/` |
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_arch_decisions_rev_dates` | ARCH-DECISIONS содержит AD5 (2026-10-21) и AD7 (2026-10-22) | `.ai/debt/` |
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_no_trivial_entries` | Все записи SHELL-RESIDUAL имеют LOC > 200 | `.ai/debt/` |
| `tests/gates/test_gate_debt_registry.py` | `test_check_file_lines_ignores_debt` | `make check-file-lines` exit 0 (не сканирует .ai/debt/) | `core/entrypoints/check-file-lines.sh` |
| `tests/gates/test_gate_debt_registry.py` | `test_gitignore_covers_debt` | `.gitignore` содержит паттерн, покрывающий `.ai/debt/` | `.gitignore` |
| `tests/gates/test_gate_debt_registry.py` | `test_debt_registry_node_resolver_in_p2` | `node-resolver.sh` присутствует в §P2-BACKLOG (не был в Brief) | `.ai/debt/` |

**Примечание:** Тесты TASK-E (QA) — grep-верификация. Gate-тесты создаются Coder'ом в TASK-A
как часть поставки (файл `tests/gates/test_gate_debt_registry.py`).

---

## Design Decisions

### D1: node-resolver.sh в P2-BACKLOG, а не SHELL-RESIDUAL
## @rationale
**Q:** Почему node-resolver.sh (271 LOC) в P2, а не в SHELL-RESIDUAL как healthcheck.sh (388 LOC)?
**A:** healthcheck.sh и module-interface.sh — STABLE библиотеки с неизменным API, исключены
языковой политикой (AGENTS.md п.2). node-resolver.sh заявлен как «thin facade», но 271 LOC
превышает порог фасада (150 LOC) и содержит inline python3 -c (Tier 1 триггер на L306-316).
Это кандидат на Strangler-Fig, а не стабильная библиотека.

### D2: P2-BACKLOG из Brief ЗАКРЫТ — пересчёт по факту
## @rationale
**Q:** Почему P2-BACKLOG в реестре отличается от Brief?
**A:** Brief 111 написан до завершения волн 106-109. На момент скана (2026-07-31):
- validate.sh: 251 → 18 LOC (Brief 107 — мигрирован в validate_orchestrator.py)
- scp-deliver.sh: 251 → 59 LOC (Brief 108 — мигрирован в core_deliverer.py)
- check-dead-code.sh: 86 → 14 LOC (Brief 109 — мигрирован в dead_code_checker.py)
- lint.sh + check-doc-headers.sh: консолидированы (Brief 106 — doc_header_validator.py)

Фактический P2-бэклог пересчитан по результатам полного скана кодовой базы.

### D3: .gitignore НЕ требует правок
## @rationale
**Q:** Нужно ли добавлять `.ai/debt/` в `.gitignore`?
**A:** `.gitignore` уже содержит `.ai/*` (строка 21), что покрывает `.ai/debt/`.
Исключение `!.ai/plans/` (строка 22) не затрагивает `.ai/debt/`. Добавление явной
строки `.ai/debt/` было бы избыточным. Достаточно документировать в реестре §GITIGNORE.

### D4: check-file-lines.sh НЕ требует правок
## @rationale
**Q:** Нужно ли модифицировать check-file-lines.sh для исключения `.ai/debt/`?
**A:** Скрипт сканирует только `${PATHS_CORE_DIR}` (core/). `.ai/debt/` находится
в корне репозитория и не попадает в scope. AC5 satisfied без изменений кода.

### D5: Полный TRAP-инвентарь — в Приложениях, не в теле реестра
## @rationale
**Q:** Почему полные таблицы 251 TRAP[BUG] и 248 TRAP[DECISION] в приложениях?
**A:** Реестр должен быть читаемым для человека. 500+ строк таблиц сделают файл
непригодным для быстрой навигации. Сводная статистика — в теле реестра, полные
таблицы — в приложениях со ссылкой на DevPlan 111 как source of truth для grep-вывода.

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| `git add -f` отклонён pre-commit hook | LOW | LOW | `.ai/debt/` не содержит исполняемого кода — hooks не должны блокировать |
| Реестр устареет через месяц | HIGH | MED | Rev-даты в каждой записи; §ARCH-DECISIONS с конкретными датами пересмотра |
| node-resolver.sh анализ отложен | MED | LOW | P2-BACKLOG с rev-датой 2026-09-30 |
| QA-тесты (TASK-E) не покрывают все AC | LOW | MED | $TEST_SPEC — 8 тестов, каждый мапится на AC |
| merge conflict в .gitignore | LOW | LOW | .gitignore НЕ меняется — conflict исключён |

---

## Non-Goals

- **НЕ править код** — это RESEARCH + PLANNING. Все правки кода — в отдельных DevPlan'ах на основе P2-BACKLOG.
- **НЕ создавать DevPlan'ы для P2-задач** — только зарегистрировать в реестре. Конкретные DevPlan'ы создаются отдельно.
- **НЕ мигрировать node-resolver.sh** — только зарегистрировать в P2-BACKLOG с обоснованием.
- **НЕ править TRAP-аннотации в коде** — даже если найдены stale/неактуальные TRAP. Чистка TRAP — отдельная задача.
- **НЕ запускать `make test` или `make gate`** — это planning-артефакт, не верификационный прогон.

---

## Next Steps

### Wave 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/111-debt-registry/02-DevPlan.md, implement Wave 1: TASK-A
```

TASK-A включает:
1. Создание `.ai/debt/001-Strangler-Fig-Closeout.md` по спецификации из §Содержание TASK-A
2. Верификацию `.gitignore` покрытия (TASK-B)
3. Верификацию `check-file-lines.sh` scope (TASK-C)
4. `git add -f .ai/debt/001-Strangler-Fig-Closeout.md && git commit` (TASK-D)
5. Создание `tests/gates/test_gate_debt_registry.py` с 8 тестами из $TEST_SPEC

### Wave 2
```
qa Read /Users/tronyx/projects/ai-platform/.ai/plans/111-debt-registry/02-DevPlan.md, verify TASK-A output against AC1-AC7
```

TASK-E включает:
1. `grep`-верификацию: реестр существует, все секции присутствуют
2. `make check-file-lines` — exit 0
3. Проверку `.gitignore` покрытия
4. Проверку всех AC1-AC7
5. Создание VerificationReport
