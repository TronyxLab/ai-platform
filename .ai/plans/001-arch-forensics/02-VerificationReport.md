<!-- GREP_SUMMARY: VerificationReport, arch-forensics, full re-run, boundaries, coupling, invariants, fragility, risk, superposition-collapse, observability-collapse, path-prefix-split -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Полный повторный отчёт архитектурной криминалистики ai-platform по протоколу skill `arch-forensics` (7 задач + S7–S15 + collapse detection). Full re-run на 2026-07-18.
- **DESCRIPTION:** Объективная модель системы на дату прогона: компоненты, границы, связанность, нарушения, карта хрупкости/риска, коллапсы суперпозиции. Без исправлений — только модель. Включает delta-секцию относительно `01-VerificationReport.md` того же плана.
- **RATIONALE:** Подтверждён CRITICAL-коллапс (INVARIANT COLLAPSE: правило `internal ↛ modules` по-прежнему фиктивно, гейт слеп, документация противоречива) + обнаружен новый HIGH-уровневый PATH-PREFIX COLLAPSE (`/opt/core/` vs `/opt/platform/core/` приводит к silent runtime failure в backup-cron) + новый кандидат OBSERVABILITY COLLAPSE (postgres severity=critical без метрик). По протоколу skill требуется артефакт.
- **ACCEPTANCE_CRITERIA:** Все 7 задач выполнены; ≥3 режимов суперпозиции проверено (фактически все 9: S7–S15); каждое утверждение имеет evidence `file:line`; проверены все 6 сигналов коллапса + 1 новый кандидат; сверка с `01-VerificationReport.md` выполнена.
- **IMPLEMENTS:** skill `arch-forensics` (Staff Software Architect Pattern), протокол §5 (Report) + §Запрещается (no fixes).
- **IMPACTS:** (только модель — никаких правок) `core/AGENTS.md` (cross-layer правило), `core/entrypoints/healthcheck.sh:12-13` (контрадикция), `core/modules/backup-cron/scripts/crontab:44,48` (битые пути), `core/templates/sudo-whitelist.template:12,36-41` (битые пути), `core/modules/monitoring/config/prometheus.yml` (отсутствие postgres/hermes scrape), `tests/test_cross_layer_imports.py:121` (слепой линтер).
- **REQUIRES:** `01-VerificationReport.md` того же плана (исходный baseline).

$START_VERIFICATION_REPORT

# Architecture Forensics Report: ai-platform (re-run 2026-07-18)

## Executive Summary

- **Components analyzed:** 24 (Makefile-фасад, 16 entrypoints, ~20 internal-скриптов, 9 lib, 14 модулей, 3 compose-слоя, 9 CI-воркфлоу + 5 composite actions, тестовая инфраструктура 874 node IDs, 42 gate-файла)
- **Boundaries found:** 6 (valid: 3, porous: 2, fractured: 1)
- **Violations:** 16 (cross-layer internal→modules: 6 рантайм-точек; modules→internal скрытые: 3; SRP: 1; doc contradictions: 2; **path-prefix: 5 точек (NEW)**; obsolete traps: 2)
- **Fragile points:** 6 (добавлен `core/lib/paths.sh` PLATFORM_ROOT контракт в связке с путём `/opt/core`)
- **Risk distribution:** CRITICAL: 1 · HIGH: 5 · MEDIUM: 7 · LOW: 11
- **Superposition collapses:** 2 подтверждённых (INVARIANT — CRITICAL, BOUNDARY — HIGH) + **2 кандидата (PATH-PREFIX — HIGH, OBSERVABILITY — HIGH)** — все 4 имеют ≥3 сходящихся измерения
- **Verdict:** **NEEDS_ATTENTION**

Система сохранила свою основную дисциплину: единый Makefile-фасад с машиночитаемым реестром (`entrypoint-manifest.yaml`, 34 allowed verbs, 41 gate), digest-pinned образы (0 плавающих тегов в production), acyclic module DAG, held dual-delivery инвариант. Однако центральная архитектурная модель недостоверна в трёх ортогональных точках, и одна из них (PATH-PREFIX) имеет **подтверждённый silent runtime-эффект**: backup-cron каждую минуту пытается запустить `/opt/core/internal/healthcheck/docker-healthcheck.sh`, которого не существует ни в контейнере (нет mount'а), ни на хосте (rsync идёт в `/opt/platform/core/`).

---

## 0. Delta vs `01-VerificationReport.md`

| Категория | Что изменилось с момента 01 |
|-----------|----------------------------|
| **Структура entrypoints** | 15 → 16 entrypoints; добавлен `core/entrypoints/check-doc-headers.sh` (pre-commit hook, registered as `script:`). |
| **Структура modules** | 13 docker + 1 system = 14 (без изменений). |
| **CI** | 8 → 9 workflows (в `01` указано 8; фактически `.github/workflows/` содержит 9 файлов). |
| **Manifest gates** | 41 gate id (без изменений); файлов в `tests/gates/` теперь 42 — **расхождение 1 остаётся** (см. §4, не подтверждено какой файл orphan). |
| **INVARIANT COLLAPSE** | **PERSISTS** — `core/entrypoints/healthcheck.sh:12-13` по-прежнему декларирует `internal/ → modules is permitted`, что противоречит `core/AGENTS.md`. Gate #8 по-прежнему слеп к вызовам через промежуточные переменные (`_looks_like_path` at `tests/test_cross_layer_imports.py:121`). |
| **BOUNDARY COLLAPSE** | **PERSISTS** — `backup-cron/scripts/crontab`, `monitoring/hooks/on-project-deploy.sh:321`, `platform-secrets.service:13` по-прежнему пробивают границу modules→internal через cron/systemd/hook (невидимо для линтера). |
| **NEW: PATH-PREFIX COLLAPSE** | **NEW — HIGH.** Обнаружено два конкурирующих prod-префикса: `/opt/platform/core/` (canonical, `core/lib/paths.sh:33`, `core-deploy.yml:130`, `platform-secrets.service:13`) vs `/opt/core/` (5 точек в `backup-cron/scripts/crontab:44,48`, `core/templates/sudo-whitelist.template:12,36-41`, `core/bootstrap/systemd/README.md:189,192`). Backup-cron crontab строки 44 и 48 **молча падают каждую минуту/час** — в контейнере нет mount'а `/opt/core`. |
| **NEW: OBSERVABILITY COLLAPSE candidate** | **NEW — HIGH.** `core/modules/postgres/module.yaml:24` declares `severity: critical`, но в `core/modules/monitoring/config/prometheus.yml` нет ни одного postgres/pgbouncer scrape job, ни postgres-exporter контейнера в `infra-metrics`. Аналогично hermes-agent (`:9119` dashboard) не имеет metrics-scrape. Отказы в этих компонентах невидимы до хард-аутейта. |
| **Раньше MEDIUM, теперь подтверждённый runtime-bug** | Sandwich-цикл `internal → modules → internal` (`deploy-project.sh` → monitoring hook → `generate-catalog.sh`) — действующие номера строк обновлены: hook trigger now at `deploy-project.sh:729,1016` (deploy) и `:757,848` (remove), `monitoring/hooks/on-project-deploy.sh:321`. |
| **Исправленное утверждение 01** | 01 назвал `restart` drift'ом — **ошибка**: `restart` определён в `core/Makefile.common:14` (`restart: stop start`), который подключается через `core/templates/module.mk:60`. Не drift. |
| **Forbidden verbs** | По-прежнему 0 нарушений — все 5 forbidden verbs (`push-core`, `deploy-node`, `build-local`, `bootstrap-core`, `hermes-deploy-vps`) присутствуют только в определениях/тестах. |
| **Doc drift (без изменений)** | `verify` отсутствует в ✅-таблице root `AGENTS.md:80-109`, но зарегистрирован везде; `static` vs `static_audit` marker (`AGENTS.md:93` ↔ `pyproject.toml:52`); template `sync-env` vs canonical `project-sync-env`. |

---

## 1. System Architecture (задача 1)

### Components

| Component | Type | Entry Points | Dependencies | Data Owned |
|-----------|------|-------------|--------------|------------|
| Makefile (root) | facade | `make <target>` ×36 | `core/entrypoints/`, docker compose | — |
| core/entrypoints/ ×16 | thin wrappers | Makefile, git hooks, pre-commit | internal/, lib/ | — |
| core/internal/bootstrap/ | orchestrator | `bootstrap.sh`, `node-update.sh` | internal/, lib/, **modules/ (runtime)** | `/var/lib/platform/.bootstrap/` checkpoints |
| core/internal/deploy/ | deploy engine | CI forced-command | lib/, **modules/hooks (runtime)** | `/var/log/platform/audit.log` |
| core/internal/provision-environment.sh | provisioner | Makefile ×2, CI ×4, deploy-modules.sh | platform-env.yaml | networks, volumes |
| core/lib/ ×9 | shared libs | source-only | — | PLATFORM_ROOT contract (`paths.sh:33`) |
| core/modules/ ×14 (13 docker + 1 system) | services | compose include, module Makefile | lib/, templates/, **internal/ (hidden via cron/systemd)** | volumes, spool dirs |
| docker-compose.yml | composition root | `make up` | 12 module base.yml via `include:` | 6 networks, 10 volumes |
| platform-env.yaml | env SoT | provision-environment.sh | — | networks, volumes, ports, no_proxy |
| entrypoint-manifest.yaml | ops registry | CI gates | — | allowed_verbs (34), gates (41), forbidden (5+7) |
| .github/workflows/ ×9 | CI | push/PR/schedule/workflow_run | make targets, composite actions | — |
| tests/ (874 node IDs) | quality gates | `make test`/`gate` | `_conftest/`, Docker | `test_inventory.yaml` baseline |

### Data Flow (Triple Delivery + CI fan-in)

```
dev macOS ──make up──▶ docker compose (12 модулей, include-only)
   │ git push
   ▼
platform-test.yml (pre-commit + gate MODE=fast/full + integration)  ← gate of record
   │ workflow_run(main)                  │ workflow_run(main)              │ workflow_run(main)
   ▼                                     ▼                                 ▼
core-deploy.yml ──rsync core/──▶ VPS    build-platform.yml ──L1──▶ ghcr   mirror.yml ──push──▶ TronyxLab
   │ ssh: make node-update                                                    (DR mirror)
   ▼
node-lifecycle.sh --mode update ──▶ provision → deploy-modules → healthcheck-all
                                        │
                                        └─ ensure_context_repo(): git clone/pull  ← ЕДИНСТВЕННЫЙ git на VPS ✅
                                           deploy-modules.sh:214 (D2 invariant)

Project repo (any org) ──workflow_call──▶ deploy-project.yml / platform-deploy.yml / stage-deploy.yml
                                            │ tar | ssh forced-command
                                            ▼
                                   ci-deploy@vps → deploy-project.sh (SSH_ORIGINAL_COMMAND)
                                            │ verb: platform-deliver | platform-deploy
                                            ▼
                                   /opt/projects/<project> (atomic mv, audit logged)
```

### Lifecycle

- **node-lifecycle.sh `--mode init`** (17 шагов, idempotent): ssh-access → apt → tor (conditional) → docker → user-platform → user-ci-deploy → projects-base → firewall → verify-core (content-hash) → verify-node-configs → decrypt-secrets → ensure-secrets → read-node-yaml → ghcr-auth → sudoers → install-acme → **self-invoke `--mode update`** (`node-lifecycle.sh:517,559`) → audit-summary → telegram.
- **`--mode update`** (6 шагов): verify-core → provision (networks+volumes) → ssl-provision → deploy-docker (via `_topo_sort.py`) → deploy-system → healthcheck-all.
- **Idempotency:** per-step content-hash checkpointing (`core/internal/bootstrap/content-hash.sh`); `.done` markers in `/var/lib/platform/.bootstrap/`; explicit no-op skip messages (e.g. `node-lifecycle.sh:314`). Re-running `make bootstrap-node` is a no-op by design (root AGENTS.md invariant #6).

---

## 2. Architectural Boundaries (задача 2, S7)

```
🧱 BOUNDARY: Makefile-фасад (все операции через make)
├─ Type: layer
├─ Declared at: AGENTS.md @invariants #1, Makefile:34 (.PHONY)
├─ Permeability: POROUS
├─ Violations: 3 (без изменений с 01)
│   1. .github/actions/provisioner-call/action.yml:44 — bash provision-environment.sh в обход make (×4 воркфлоу)
│   2. core-deploy.yml:183-186 — прямой ssh bash provision-environment.sh (TRAP[DECISION], deferred)
│   3. Makefile:74-75 — таргет `up` сам вызывает provision-environment.sh напрямую вместо `$(MAKE) provision`
└─ Verdict: WEAK — для людей/агентов держится, но CI и сам Makefile обходят в одной точке (provision)

🧱 BOUNDARY: entrypoints → internal → lib (слоистость core/)
├─ Type: layer
├─ Declared at: core/AGENTS.md §Cross-layer import rules; enforcement: tests/test_cross_layer_imports.py:51-54
├─ Permeability: ENFORCED (для направления entrypoints→*)
├─ Violations: 0 — все 16 entrypoints вызывают только internal/ и lib/
└─ Verdict: VALID

🧱 BOUNDARY: internal ↛ modules
├─ Type: layer
├─ Declared at: core/AGENTS.md (таблица: internal/ → только internal/, lib/); _IMPORT_RULES: tests/test_cross_layer_imports.py:51-54
├─ Permeability: FRACTURED
├─ Violations: 6 рантайм-вызовов (через промежуточные переменные — слепая зона линтера)
│   - node-lifecycle.sh:842  → modules/<name>/healthcheck.sh  (var: $hc_script, bash at :846)
│   - deploy-modules.sh:333  → modules/<name>/install.sh      (var: $install_script, bash at :341)
│   - deploy-modules.sh:538  → modules/<name>/healthcheck.sh  (var: $healthcheck_script) — readiness poll
│   - deploy-modules.sh:571  → modules/<name>/healthcheck.sh  (var: $healthcheck_script) — health check
│   - deploy-project.sh:729,1016 → modules/<name>/hooks/<hook> (var: $hook_script, bash at :734) — deploy hooks
│   - deploy-project.sh:757,848  → modules/<name>/hooks/<hook> (var: $hook_script, bash at :763) — remove hooks
├─ Контрадикция: core/entrypoints/healthcheck.sh:12-13 — "internal/ → modules is permitted" — прямо противоречит core/AGENTS.md
└─ Verdict: BROKEN — правило фиктивно; см. INVARIANT COLLAPSE в §7

🧱 BOUNDARY: modules ↛ internal
├─ Type: layer
├─ Declared at: core/modules/AGENTS.md §Запрет #2
├─ Permeability: POROUS (нарушения вне видимости import-графа)
├─ Violations: 3 рантайм
│   - core/modules/backup-cron/scripts/crontab:44 → /opt/core/internal/healthcheck/docker-healthcheck.sh (cron ВНУТРИ контейнера)
│   - core/modules/monitoring/hooks/on-project-deploy.sh:321 → core/internal/catalog/generate-catalog.sh
│   - core/modules/platform-secrets/platform-secrets.service:13 → /opt/platform/core/internal/secrets/decrypt-secrets.sh (systemd)
└─ Verdict: WEAK — все 3 нарушителя невидимы линтеру (crontab/systemd-unit — не .sh импорты)

🧱 BOUNDARY: Core (SCP/rsync, NO git) vs Context-overlay (git)
├─ Type: deployment
├─ Declared at: root AGENTS.md §Triple Delivery Model invariants 1-4
├─ Permeability: ENFORCED
├─ Violations: 0 — git на VPS только в ensure_context_repo (deploy-modules.sh:214)
├─ Test: tests/gates/test_gate_context_overlay_git.py (D1/D2/D3)
└─ Verdict: VALID

🧱 BOUNDARY: Модульная изоляция (module contract)
├─ Type: domain
├─ Declared at: core/modules/AGENTS.md MODULE_CONTRACT
├─ Permeability: ENFORCED
├─ Violations: 0 контрактных (profiles ✅, x-logging ✅ ×12, digest-pinned ✅, symlinks ✅)
└─ Verdict: VALID
```

---

## 3. Component Inventory (задача 3, ключевые)

### Component: core/internal/bootstrap/node-lifecycle.sh
- **Purpose:** единый оркестратор жизненного цикла ноды (init/update, mode dispatch).
- **Data owned:** checkpoint-маркеры `/var/lib/platform/.bootstrap/`.
- **Consumers:** `bootstrap.sh`, `node-update.sh`, `core-deploy.yml` (через `make node-update`), сам себя (`:517,559`).
- **Dependencies:** 7 lib-файлов, 6 internal-скриптов, **modules/*/healthcheck.sh** (`:842`).
- **Invariants:** идемпотентность (checkpoint-driven), NO git для core.

### Component: core/internal/bootstrap/deploy-modules.sh
- **Purpose:** деплой docker/system-модулей по топологии.
- **Data owned:** `/opt/<context>/platform/` (context-overlay через git).
- **Consumers:** `node-lifecycle.sh` (update-mode steps).
- **Dependencies:** `lib/paths`, `lib/docker`, `_topo_sort.py`, **modules/*/install.sh** (`:341`), **modules/*/healthcheck.sh** (`:538,571`), `provision-environment.sh`.
- **Invariants:** `ensure_context_repo()` (`:214`) — единственный git на VPS — **HELD**.

### Component: core/internal/provision-environment.sh
- **Purpose:** идемпотентный provision (сети/volumes/CI env) из `platform-env.yaml`.
- **Consumers:** **5 разных вызывающих из 3 контекстов**: `Makefile:61` (provision), `Makefile:80` (up — напрямую, в обход `make provision`), `.github/actions/provisioner-call/action.yml:44` (×4 воркфлоу), `core-deploy.yml:183-186`, `deploy-modules.sh` (update-mode provision step).
- **Invariants:** единственный читатель-исполнитель `platform-env.yaml` networks/volumes.

### Component: core/lib/paths.sh (load-bearing)
- **Purpose:** single source of truth для PLATFORM_ROOT (`paths.sh:33` → `/opt/platform`), PATHS_CORE_DIR, PATHS_MODULES_DIR, PATHS_INTERNAL_DIR.
- **Consumers:** ВСЕ 16 entrypoints + ~10 internal-скриптов + tests.
- **Invariants:** PLATFORM_ROOT — контракт для VPS, CI, тестов.
- **⚠ Hidden coupling:** prod-пути в crontab/systemd захардкожены и **расходятся** с PLATFORM_ROOT — см. §7 PATH-PREFIX COLLAPSE.

### Component: platform-env.yaml
- **Purpose:** SoT окружения (8 сетей в дескрипторе, 16 volumes, 15 портов, proxy-контракт).
- **Consumers:** provision-environment.sh, tests/_conftest, CI ×4, gate T8.5.
- **Invariants:** `no_proxy_internal` — канонический список — **HELD** (гейт env-shared-consistency).

### Module Inventory (14 модулей, контрактная матрица)

Все 13 docker-модулей имеют `module.yaml` + `docker-compose.base.yml` + `healthcheck.sh` + `Makefile` + `.dockerignore`-symlink. `platform-secrets` — system-модуль (без base.yml, легально исключён из include).

| Module | install_type | severity | depends_on (module.yaml) | networks |
|--------|-------------|----------|--------------------------|----------|
| postgres | docker | **critical** (`module.yaml:24`) | — | shared-db-net |
| redis | docker | — | — | shared-cache-net |
| nginx | docker | — | — | proxy-net, observability-net |
| clickhouse | docker | — | — | observability-net, shared-db-net |
| minio | docker | — | — | backup-net, shared-db-net |
| logging | docker | — | — | observability-net |
| litellm | docker | — | postgres | shared-db-net, hermes-agent-net, observability-net |
| langfuse | docker | — | postgres, clickhouse | shared-db-net, observability-net |
| backup-cron | docker | — | postgres | backup-net, shared-db-net |
| monitoring | docker | — | nginx | proxy-net, observability-net |
| infra-metrics | docker | — | nginx | observability-net, shared-cache-net |
| hermes-agent | docker | — | nginx, postgres, redis, litellm | proxy-net, hermes-agent-net, observability-net |
| platform-secrets | **system** | — | — | (systemd, no network) |

DAG acyclic ✅ (подтверждено `tests/gates/test_gate_topology.py`). Несуществующих depends_on нет.

---

## 4. Violations (задача 4)

### Violation: Cross-layer (internal → modules) — де-факто конвенция против де-юре запрета
- **Component:** node-lifecycle.sh, deploy-modules.sh, deploy-project.sh
- **Evidence:** `node-lifecycle.sh:842-846`; `deploy-modules.sh:333-341,538,571`; `deploy-project.sh:729-734,757-763,1016,848`.
- **Impact:** правило в `core/AGENTS.md` и `_IMPORT_RULES` (`test_cross_layer_imports.py:51-54`) описывают систему, которой код не соответствует; агенты, читающие AGENTS.md, принимают неверные решения.
- **Severity:** **CRITICAL** (в связке со слепотой гейта — см. коллапс в §7).

### Violation: Gate #8 слеп к рантайм-вызовам через переменные
- **Component:** `tests/test_cross_layer_imports.py`
- **Evidence:** `_looks_like_path()` at `:121-129` требует `/`, `${...}/`, `../` или абсолютного пути в **самой строке литерала**. Вызовы через промежуточные переменные (`bash "$hc_script"` at `node-lifecycle.sh:846`, `bash "$install_script"` at `deploy-modules.sh:341`, `bash "$hook_script"` at `deploy-project.sh:734,763`) литерал не содержат → классифицируются как non-path → не проверяются.
- **Impact:** Gate репортит "0 violations" при 6 фактических — ложная гарантия.
- **Severity:** **CRITICAL**.

### Violation: Контрадикция документации cross-layer
- **Component:** `core/entrypoints/healthcheck.sh` ↔ `core/AGENTS.md`
- **Evidence:** `healthcheck.sh:12-13` "internal/ → modules is permitted" ↔ `core/AGENTS.md` (таблица: internal/ → только internal/, lib/ | Всё остальное запрещено).
- **Impact:** два авторитетных источника дают противоположные ответы на один архитектурный вопрос.
- **Severity:** HIGH.

### Violation: modules → internal через невидимые каналы (cron/systemd/hook)
- **Component:** backup-cron, monitoring, platform-secrets
- **Evidence:** `backup-cron/scripts/crontab:44`; `monitoring/hooks/on-project-deploy.sh:321`; `platform-secrets/platform-secrets.service:13`.
- **Impact:** изменение путей `internal/` ломает cron, systemd и deploy-хуки на VPS без сигнала от гейтов.
- **Severity:** HIGH.

### Violation: PATH-PREFIX SPLIT — `/opt/core/` vs `/opt/platform/core/` (NEW)
- **Component:** backup-cron (контейнер), sudo-whitelist.template, systemd/README.md
- **Evidence (canonical = `/opt/platform/core/`):**
  - `core/lib/paths.sh:33` — `PLATFORM_ROOT="/opt/platform"` (SoT)
  - `core-deploy.yml:130` — rsync dest `/opt/platform/core/`
  - `core-deploy.yml:14` — "Destination: /opt/platform/core/ on VPS"
  - `core/internal/bootstrap/scp-deliver.sh:88` — `PLATFORM_REMOTE_BASE default: /opt/platform`
  - `core/modules/platform-secrets/platform-secrets.service:13` — `/opt/platform/core/internal/secrets/decrypt-secrets.sh` ✅ CORRECT
  - `core/internal/bootstrap/install-tor-proxy.sh:340` — комментарий "Раньше было ${PLATFORM_ROOT}/core/ — не работало после rsync в /opt/core/" (намекает на исторический переход)
- **Evidence (stale/wrong = `/opt/core/`):**
  - `core/modules/backup-cron/scripts/crontab:44` — `/opt/core/internal/healthcheck/docker-healthcheck.sh`
  - `core/modules/backup-cron/scripts/crontab:48` — `/opt/core/modules/backup-cron/scripts/disk-monitor.sh`
  - `core/templates/sudo-whitelist.template:12,36-41` — `/opt/core/modules/{{MODULE_NAME}}/Makefile` (×6 строк)
  - `core/bootstrap/systemd/README.md:189,192` — `/opt/core/internal/healthcheck/`
- **Runtime-эффект (подтверждено):** backup-cron контейнер (`docker-compose.base.yml`) не имеет mount'а `/opt/core` — только `backup-spool` и `backup-logs`. Cron внутри контейнера (Dockerfile:74 `COPY scripts/crontab /etc/cron.d/platform-backup`) **каждую минуту пытается запустить `/opt/core/internal/healthcheck/docker-healthcheck.sh` и каждую минуту падает** (файл не существует). Аналогично `/opt/core/modules/backup-cron/scripts/disk-monitor.sh` — ежечасно. `/var/log/platform/backup/docker-healthcheck.log` (redirect-таргет в `crontab:44`) существует, но туда пишется stderr несуществующего файла.
- **Impact:** silent runtime failure в backup-cron every minute; диск-monitorинг backup-модуля не работает. Логирование ошибки идёт в redirect, но никто его не читает (нет алерта на содержимое `/var/log/platform/backup/docker-healthcheck.log`). Liveness-healthcheck контейнера (`pgrep cron`) проходит — cron-демон жив, cron-задачи падают.
- **Severity:** **HIGH** (runtime-affecting, но изолировано внутри backup-модуля; backup-данные не теряются — основные backup-скрипты используют `/usr/local/bin/...` после `COPY` в Dockerfile, не `/opt/core`).

### Violation: Циклическая сэндвич-цепочка internal → modules → internal
- **Component:** deploy-project.sh → monitoring hook → generate-catalog.sh
- **Evidence:** `deploy-project.sh:729,1016` (_trigger_deploy_hooks) → `monitoring/hooks/on-project-deploy.sh:321` (`${PLATFORM_ROOT}/core/internal/catalog/generate-catalog.sh`).
- **Impact:** граница internal/modules пересекается дважды за один deploy; generate-catalog.sh в статическом графе числится «сиротой» (MODULE_CONTRACT ссылается на несуществующий прямой вызов из deploy-project.sh — реальный вызыватель monitoring hook).
- **Severity:** MEDIUM.

### Violation: Self-reference node-lifecycle.sh
- **Component:** node-lifecycle.sh
- **Evidence:** `:517,559` — self-invoke `--mode update` (step init-режима).
- **Impact:** intentional (задокументировано), но это единственный цикл в графе; рекурсия без глубинного ограничителя (нарушение принципа, но не баг).
- **Severity:** LOW.

### Violation: SRP — тройная роль provision-environment.sh
- **Component:** core/internal/provision-environment.sh
- **Evidence:** 5 вызывающих из 3 контекстов (`Makefile:61,80`, `action.yml:44`, `core-deploy.yml:183`, `deploy-modules.sh`).
- **Impact:** один скрипт — контракт для make, CI и deploy-оркестратора одновременно; изменение сигнатуры → 3 зоны поражения.
- **Severity:** MEDIUM.

### Violation: Doc drift (3 точки)
- **Evidence:**
  1. `verify` отсутствует в ✅-таблице root `AGENTS.md:80-109`, но зарегистрирован в `entrypoint-manifest.yaml:177,460` и `core/AGENTS.md:49`.
  2. `AGENTS.md:93` и `entrypoint-manifest.yaml:88` ссылаются на marker `static`; `pyproject.toml:52` регистрирует `static_audit`. **0 тестов** используют `@pytest.mark.static`.
  3. Все 3 template AGENTS.md инструктируют `make sync-env` (templates/template-*/AGENTS.md:14); канонический глагол — `project-sync-env` (`AGENTS.md:97`). Inv#4 явно исключает templates — drift tolerated.
- **Severity:** LOW.

### Violation: Сироты / фиктивные контракты
- **Evidence:** `internal/catalog/generate-catalog.sh` — MODULE_CONTRACT утверждает вызов из deploy-project.sh, прямого вызова нет (реальный — monitoring hook). `check-commit-msg.sh`, `check-doc-headers.sh`, `pre-push-gate.sh` — живут в git hooks, зарегистрированы как `script:` (легально).
- **Severity:** LOW.

### Violation: Устаревший TRAP[DEBT] в Makefile
- **Evidence:** `Makefile:282-287` — TRAP[DEBT] о "gate MODE=fast проглатывает падения", но текущий код уже имеет `|| { echo FAIL; exit 1; }`.
- **Impact:** TRAP описывает исправленную проблему → кандидат на TRAP[ARCHIVED].
- **Severity:** LOW.

### Violation (unconfirmed): Gate-count off-by-one
- **Evidence:** `ls tests/gates/test_*.py` = 42 файла; `grep -c "^  - id:" core/entrypoint-manifest.yaml` = 41. Проверка двух подозреваемых (`test_p20_container_coupling`, `test_restart_consistency`) — **оба присутствуют** в манифесте (count=1 каждый).
- **Impact:** либо 1 файл реально не зарегистрирован (молча не запускается per trinity-протокол), либо подсчёт артефактный.
- **Severity:** LOW (требует запуска `make test MARKER=gate` или `test_gate_manifest_integrity` для подтверждения).

---

## 5. Fragility Map (задача 5, S14)

```
📡 CHANGE IMPACT: core/lib/paths.sh (PLATFORM_ROOT контракт)
├─ Direct dependents (1-hop): ВСЕ 16 entrypoints + ~10 internal-скриптов
├─ Hidden dependents: prod-пути в crontab, systemd units, sudo-whitelist.template (см. §4 PATH-PREFIX)
├─ Configuration propagation: PLATFORM_ROOT (default /opt/platform) — контракт для VPS, CI, тестов
└─ ⚡ FRAGILE: изменение семантики PLATFORM_ROOT → каскад по ~25 файлам + prod-пути cron/systemd, причём последние РАСХОДЯТСЯ с SoT уже сейчас

📡 CHANGE IMPACT: module.yaml схема (D4)
├─ Direct: 14 module.yaml
├─ Test propagation: test_gate_module_schema_d4, test_gate_module_yaml_contract, test_gate_topology,
│                    test_gate_container_name_consistency, _conftest/audit.py
├─ Runtime: deploy-modules.sh (yaml_get_field), _topo_sort.py
└─ ⚡ FRAGILE: новое поле → 5+ гейтов + deploy-оркестратор

📡 CHANGE IMPACT: entrypoint-manifest.yaml
├─ Direct: 42 gate-файла (триединая регистрация), lint.sh namelint, Makefile .PHONY
├─ Logical coupling: любой новый make-таргет = 3 синхронных правки (Makefile + manifest + core/AGENTS.md)
└─ ⚡ FRAGILE by design — хрупкость намеренная (anti-drift), co-change ~100%

📡 CHANGE IMPACT: core/internal/provision-environment.sh
├─ Direct: Makefile (×2 точки), provisioner-call action (×4 воркфлоу), core-deploy.yml, deploy-modules.sh
└─ ⚡ FRAGILE: смена CLI-флагов → одновременно локальная разработка, CI и prod-деплой

📡 CHANGE IMPACT: пути core/internal/* на VPS
├─ Hidden dependents: crontab:44 (невидимый пробой), platform-secrets.service:13, watchdog units
└─ ⚡ FRAGILE: переименование/перемещение internal-скрипта → тихая поломка cron/systemd на всех нодах

📡 CHANGE IMPACT: Prometheus scrape config (monitoring/config/prometheus.yml)
├─ Direct: prometheus (single reader); Grafana dashboards (8 dashboards via datasource)
├─ Coverage gap: postgres, pgbouncer, hermes-agent, langfuse, loki, minio, backup-cron, langfuse-redis — НЕ скрейпятся
└─ ⚡ FRAGILE: observability — отказ в критичном компоненте невидим до хард-аутейта (см. §7 OBSERVABILITY COLLAPSE)
```

Пары высоковероятного co-change: (Makefile ↔ entrypoint-manifest.yaml ↔ core/AGENTS.md) — enforced triad; (.env.example ↔ .env) — enforced гейтом; (module base.yml ↔ module.yaml resources) — НЕ enforced (гейта на resources-паритет нет).

---

## 6. Risk Map (задача 6, S10, S11)

```
🎲 RISK: node-lifecycle.sh + deploy-modules.sh (deploy-orchestrator)
├─ Likelihood: 4 — сложнейшие файлы проекта (>850 строк), частые изменения
├─ Impact: 5 — отказ = нода не поднимается/не обновляется; blast radius = все 14 модулей
├─ Detectability: 3 — contract-тесты bash-функций есть, полный путь проверяется только на живом VPS
├─ Risk Score: 4×5÷3 = 6.7
└─ Tier: HIGH

🎲 RISK: cross-layer инвариант + Gate #8 (INVARIANT COLLAPSE)
├─ Likelihood: 4 — каждый новый internal-скрипт может добавить невидимое нарушение
├─ Impact: 3 — эрозия архитектурной модели, неверные решения агентов
├─ Detectability: 1 — гейт слеп к вызовам через переменные, cron, systemd
├─ Risk Score: 4×3÷1 = 12
└─ Tier: HIGH (граница CRITICAL)

🎲 RISK: postgres (severity=critical, multi-writer, NO observability)
├─ Likelihood: 2 — редкие изменения, stable upstream
├─ Impact: 5 — severity=critical; writers: litellm, langfuse, backup-cron, hermes-readiness; blast radius 4+ модулей
├─ Detectability: 2 — pg_isready healthcheck + contract-тесты, но **0 Prometheus scrape, 0 exporter**
├─ Risk Score: 2×5÷2 = 5
└─ Tier: MEDIUM (но в связке с observability-gap → OBSERVABILITY COLLAPSE в §7)

🎲 RISK: platform-secrets (systemd, /run/platform/secrets.env)
├─ Likelihood: 2 — редкие изменения
├─ Impact: 5 — secrets.env читают deploy-modules, docker-healthcheck, notify-hook, checkpoint; отказ = деплой и алертинг мертвы
├─ Detectability: 2 — install.sh + healthcheck.sh, но systemd-путь захардкожен (`platform-secrets.service:13`)
├─ Risk Score: 2×5÷2 = 5
└─ Tier: MEDIUM

🎲 RISK: backup-cron (path-prefix silent failure)
├─ Likelihood: 5 — уже происходит (cron падает каждую минуту)
├─ Impact: 2 — основные backup-скрипты используют /usr/local/bin (COPY'd), не страдают; страдает только disk-monitor и docker-healthcheck логгирование
├─ Detectability: 1 — liveness pgrep cron проходит, никто не читает stderr в /var/log/platform/backup/docker-healthcheck.log
├─ Risk Score: 5×2÷1 = 10
└─ Tier: HIGH (chronic silent failure, но ограниченный blast radius)

🎲 RISK: provision-environment.sh
├─ Likelihood: 3 · Impact: 4 (5 вызывающих) · Detectability: 3 (гейты provisioner usage есть)
├─ Risk Score: 4
└─ Tier: MEDIUM

🎲 RISK: hermes-agent (L1/L2, watchdog, без metrics-scrape)
├─ Likelihood: 4 — самый активный модуль (build/, context/, overlays/, watchdog/)
├─ Impact: 3 — изолирован в hermes-agent-net; proxy opt-in контракт гейтится (T8.5)
├─ Detectability: 3 — component/integration тесты + watchdog, но **0 Prometheus scrape**
├─ Risk Score: 4×3÷3 = 4
└─ Tier: MEDIUM (в observability-gap — см. §7)

🎲 RISK: Makefile gate/test диспетчеры
├─ Likelihood: 3 · Impact: 4 · Detectability: 4 (CI выполняет их же)
├─ Risk Score: 3
└─ Tier: MEDIUM
```

**LOW-tier (11):** redis, clickhouse, minio, logging, monitoring, infra-metrics, langfuse, litellm, nginx, templates/, schemas/ — digest-pinned, контрактные, healthcheck'd.

### 💥 Blast Radius (S10, выборочно)

```
💥 FAILURE: postgres
├─ Failure mode: CORRUPTION / CRASH
├─ Direct dependents: litellm (DB litellm), langfuse (DB langfuse), backup-cron (pg_dump), hermes-agent (readiness poll)
├─ Cascading failures: LLM-стек (litellm→hermes), tracing (langfuse), backup pipeline
├─ Recovery dependency: platform-secrets (secrets.env) must be healthy; backup-cron needs S3 for restore
├─ Circuit breaker: ABSENT (no degradation path)
├─ Graceful degradation: NO — hard failure всего LLM-стека
├─ Observability: ⚠ PARTIAL — pg_isready healthcheck, НО 0 Prometheus scrape → деградация (slow queries, pool exhaustion) невидима
└─ Blast radius: 4+ модулей, весь LLM-стек

💥 FAILURE: nginx
├─ Failure mode: CRASH
├─ Direct dependents: hermes-agent (ingress), monitoring (grafana), весь публичный вход
├─ Blast radius: весь публичный ingress, N user-facing функций
└─ Observability: ✅ nginx-exporter скрейпится (prometheus.yml)

💥 FAILURE: platform-secrets
├─ Failure mode: CRASH (systemd)
├─ Recovery dependency: boot-order — RequiredBy=docker.service
├─ Blast radius: deploy + alerting + checkpoint — каскадный отказ деплоя
└─ Observability: только systemd unit status

💥 FAILURE: hermes-agent
├─ Failure mode: TIMEOUT / CRASH
├─ Blast radius: AI-operations (agent недоступен), telegram-нотификации
└─ Observability: ⚠ только watchdog + healthcheck endpoint — НО 0 Prometheus scrape
```

### S9: Ownership (ключевые)

```
👑 OWNERSHIP: redis image digest
├─ Creator: core/modules/redis/docker-compose.base.yml (image: redis:7.4-alpine@sha256:6ab0b6...)
├─ Duplicated: core/modules/langfuse/docker-compose.base.yml (langfuse-redis, тот же digest)
└─ ⚠ Задокументировано TRAP[DECISION] (langfuse base.yml) — принятый дубль, ручная синхронизация

👑 OWNERSHIP: NO_PROXY список
├─ SoT: platform-env.yaml (no_proxy_internal)
├─ Readers: .env.example (⊇-контракт), hermes-agent module.yaml
└─ Гейт T8.5 enforced — конфликт снят ✅

👑 OWNERSHIP: provision (сети/volumes)
├─ Updaters: make provision, make up (напрямую), CI provisioner-call ×4, core-deploy.yml, deploy-modules.sh
└─ ⚠ CONFLICT: 5 инициаторов одной мутации; идемпотентность скрипта — единственная защита

👑 OWNERSHIP: PLATFORM_ROOT path prefix
├─ SoT: core/lib/paths.sh:33 (/opt/platform)
├─ Correct consumers: platform-secrets.service:13, core-deploy.yml:130, scp-deliver.sh:88
├─ ⚠ STALE consumers: crontab:44,48; sudo-whitelist.template:12,36-41; systemd/README.md:189,192 (все /opt/core)
└─ ⚠ CONFLICT: 2 конкурирующих префикса; один из них ведёт к silent runtime failure
```

### S13: Dependency (модульный DAG)

`postgres, redis, nginx, clickhouse, minio, logging` — корни; `litellm→postgres`; `langfuse→postgres,clickhouse`; `backup-cron→postgres`; `monitoring→nginx`; `infra-metrics→nginx`; `hermes-agent→nginx,postgres,redis,litellm`. **Циклов нет ✅** (подтверждено `tests/gates/test_gate_topology.py`).

Внутримодульные depends_on: postgres←pgbouncer (`service_healthy`), grafana←prometheus (`service_healthy`), prometheus←prometheus-config-init (`service_completed_successfully`), promtail←loki (`service_healthy`), minio-createbuckets←minio (`service_healthy`).

### S15: Hidden Dependencies

```
🕶️ HIDDEN: crontab (в контейнере backup-cron) → /opt/core/internal/healthcheck/docker-healthcheck.sh
├─ Type: ENVIRONMENT + RESOURCE
├─ Evidence: core/modules/backup-cron/scripts/crontab:44
├─ Why hidden: путь захардкожен, mount'а нет, линтер не видит crontab-строки
├─ Break scenario: УЖЕ сломано — файл не существует в контейнере, cron падает каждую минуту
└─ Detectability: только runtime grep в /var/log/platform/backup/docker-healthcheck.log

🕶️ HIDDEN: systemd → /opt/platform/core/internal/secrets/decrypt-secrets.sh
├─ Type: ENVIRONMENT · Evidence: platform-secrets.service:13 (абсолютный путь)
├─ Break scenario: смена PLATFORM_ROOT или пути скрипта → секреты не расшифровываются при буте
└─ Detectability: только systemd unit status (fail = boot-degraded)

🕶️ HIDDEN: /run/platform/secrets.env — общий ресурс 5 потребителей
├─ Type: RESOURCE · Evidence: decrypt-secrets.sh (writer); deploy-modules.sh, docker-healthcheck.sh,
│         tor-proxy-healthcheck.sh, notify-hook.sh, checkpoint.sh (readers)
├─ Break scenario: изменение формата/пути → каскад из 5 читателей без контракта
└─ Detectability: только runtime

🕶️ HIDDEN: make dev-certs читает .env через grep (KNOWLEDGE/CONVENTION)
├─ Evidence: Makefile:48 — recipe-level grep PLATFORM_DOMAIN= .env (TRAP[BUG] 2026-07-16 задокументирован)
├─ Break scenario: переименование PLATFORM_DOMAIN → тихая поломка dev-certs
└─ Detectability: contract-проверка добавлена, но паттерн "make не читает .env" остаётся риском

🕶️ HIDDEN: nginx vhost → hermes-dashboard proxy_pass с Docker DNS
├─ Type: RESOURCE · Evidence: nginx/config/hermes-dashboard.conf:1-22 (resolver 127.0.0.11, deferred $upstream)
├─ Break scenario: backend не стартанул → nginx выживет за счёт deferred proxy_pass, но vhost отдаёт 502
└─ Detectability: nginx-exporter видит 502-rate (есть scrape)
```

---

## 7. Superposition Collapses

### ⚡ INVARIANT COLLAPSE — CRITICAL (PERSISTS с 01)
```
⚡ INVARIANT COLLAPSE
├─ S12: инвариант "internal ↛ modules" (core/AGENTS.md, _IMPORT_RULES test_cross_layer_imports.py:51-54) НАРУШЕН в 6 точках
│         (node-lifecycle.sh:842-846; deploy-modules.sh:333-341,538,571; deploy-project.sh:729-734,757-763,1016,848)
├─ S15: все нарушения проходят через промежуточные переменные ($hc_script, $hook_script, $install_script, $healthcheck_script)
│         — _looks_like_path() (test_cross_layer_imports.py:121-129) их не видит → Gate репортит "0 violations"
├─ S7: третий источник (core/entrypoints/healthcheck.sh:12-13) декларирует ПРОТИВОПОЛОЖНОЕ правило
│         ("internal/ → modules is permitted")
└─ Verdict: заявленная граница фиктивна, защищающий её гейт даёт ложную гарантию, документация противоречит сама себе.
            Система работает — но её архитектурная модель недостоверна в этой точке.
```

### ⚡ BOUNDARY COLLAPSE — HIGH (PERSISTS с 01)
```
⚡ BOUNDARY COLLAPSE
├─ S7: граница modules ↛ internal POROUS (3 рантайм-нарушителя: crontab:44, monitoring hook:321, platform-secrets.service:13)
├─ S8: environmental coupling HIGH — cron/systemd связывают слои через абсолютные прод-пути
├─ S15: все 3 канала (cron, systemd, hook-цепочка) невидимы для import-графа и гейтов
└─ Verdict: изоляция modules/internal де-факто односторонне монолитна на VPS;
            сэндвич internal→modules→internal (deploy-project.sh:729 → monitoring hook:321 → generate-catalog.sh) пересекает границу дважды.
```

### ⚡ PATH-PREFIX COLLAPSE — HIGH (NEW, runtime-affecting)
```
⚡ PATH-PREFIX COLLAPSE
├─ S9 (Ownership): PLATFORM_ROOT SoT = /opt/platform (paths.sh:33); но 5 consumer-точек используют /opt/core
│                   → 2 конкурирующих owner-утверждения о том, где лежит core на VPS
├─ S14 (Change Impact): изменение PLATFORM_ROOT → каскад по cron/systemd/sudo-whitelist, который никто не синхронизирует
├─ S15 (Hidden Dep): crontab:44,48 скрыто зависят от пути, которого нет ни в контейнере (no mount), ни на хосте (rsync → /opt/platform/core/)
├─ S10 (Failure): backup-cron cron-задача docker-healthcheck.sh падает каждую минуту (silent, liveness проходит)
└─ Verdict: SoT существует и корректен, но контракта "все prod-пути черпаются из PLATFORM_ROOT" НЕТ.
           backup-cron — подтверждённая жертва; sudo-whitelist.template — потенциальная (применяется к sudoers на хосте,
            где /opt/core тоже не существует после rsync в /opt/platform/core/).
```

### ⚡ OBSERVABILITY COLLAPSE — HIGH (NEW, candidate)
```
⚡ OBSERVABILITY COLLAPSE
├─ S11 (Risk): postgres severity=critical + blast radius 4+ модулей, НО Detectability=2 (нет metrics)
├─ S7 (Boundary): monitoring module скрейпит 7 целей (prometheus.yml), но postgres/pgbouncer/hermes/langfuse/minio/loki — ВНЕ зоны
├─ S14 (Change Impact): добавление сервиса без scrape job = тихо невидимый (нет гейта "каждый severity>=high модуль должен скрейпиться")
├─ S15 (Hidden Dep): docker-healthcheck liveness ≠ metrics observability — cron-pgrep/curl-/health проходит при деградации
└─ Verdict: граница observability де-факто односторонняя — "observability-net" объединяет 13 контейнеров в сеть,
            но scrape-coverage < 50% критичных компонентов. Отказ postgres (CORRUPTION, slow-query death) или hermes
            (TIMEOUT) невидим до хард-аутейта.
```

### NON-fires (проверены, не сработали)

| Сигнал | Обоснование non-fire |
|--------|----------------------|
| **OWNERSHIP COLLAPSE** | Мульти-writer provision защищён идемпотентностью + гейтами; redis-digest дубль задокументирован TRAP; NO_PROXY защищён T8.5. Конфликты есть, но каждый имеет enforced-механизм защиты. |
| **RISK COLLAPSE** | Deploy-оркестратор HIGH, но покрыт contract-тестами и checkpoint'ами; ни один компонент не имеет одновременно HIGH risk + blast radius + zero protection. |
| **FRAGILITY COLLAPSE** | Триада Makefile↔manifest↔AGENTS.md — намеренная enforced-хрупкость (anti-drift by design), не дефект. |
| **CIRCULAR COLLAPSE** | Единственный цикл — задокументированный self-invoke `node-lifecycle.sh:517,559`; домен не пересекает. Сэндвич internal→modules→internal — через hook, не через import-цикл. |

---

## Сильные стороны (для объективности модели)

1. **Dual-delivery инвариант** (git только в `ensure_context_repo`) — **HELD**, evidence `deploy-modules.sh:214`; gate `test_gate_context_overlay_git.py` активен.
2. **Все образы pinned by digest** (`@sha256:`) — 0 плавающих тегов в production; `hermes-agent-base` имеет LABEL `org.tronyxlab.do-not-deploy="true"`.
3. **Manifest↔Makefile↔AGENTS.md триада** — полный двунаправленный паритет (34 allowed_verbs ↔ таргеты ↔ AGENTS.md).
4. **0 forbidden-verb violations** — все 5 (`push-core`, `deploy-node`, `build-local`, `bootstrap-core`, `hermes-deploy-vps`) присутствуют только в определениях/тестах-фикстурах.
5. **Acyclic module DAG** — без циклов в `module.yaml depends_on` (подтверждено gate).
6. **Idempotent bootstrap** — per-step content-hash + `.done` markers; повторный `make bootstrap-node` = no-op.
7. **Invariants coverage** в тестах: 1, 2, 4, 5, 7, 8 root AGENTS.md — прямое тестовое покрытие; 3, 6, 9, 10 — косвенное/отсутствует (gap для test-плана).
8. **Trinity gate protocol** — каждый gate имеет файл + `@pytest.mark.gate` + manifest entry (`tests/gates/AGENTS.md:8-13`).
9. **Audit trail** — каждый deploy-project transition логируется (`deploy-project.sh` audit_log); `/var/log/platform/audit.log`.

---

## Сводка для архитектора (без рекомендаций — только наблюдения)

- **2 коллапса персистируют** с прошлого прогона (INVARIANT, BOUNDARY) — модель в этих точках остаётся недостоверной.
- **2 новых коллапса** обнаружены (PATH-PREFIX с подтверждённым runtime-эффектом, OBSERVABILITY с evidence-gap).
- **Один подтверждённый silent runtime failure** (backup-cron crontab:44,48) — это самое конкретное, машиноверифицируемое наблюдение во всём отчёте.
- **3 документационных drift'а** (verify, static/static_audit, sync-env) — низкий приоритет, но кумулятивно подрывают принцип "AGENTS.md = single source of truth".
- Сильные стороны системы (digest-pinning, manifest-triad, acyclic DAG, idempotent bootstrap, audit trail) **не компенсируют** недостоверность архитектурной модели в точках коллапса — они лишь снижают вероятность того, что недостоверность приведёт к аварии.

$END_VERIFICATION_REPORT
