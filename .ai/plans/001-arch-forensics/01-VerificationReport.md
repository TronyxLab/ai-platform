<!-- GREP_SUMMARY: VerificationReport, arch-forensics, boundaries, coupling, violations, fragility, risk-map, superposition-collapse -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Полный отчёт архитектурной криминалистики ai-platform по протоколу skill arch-forensics (7 задач + S7–S15 + collapse detection)
- **DESCRIPTION:** Объективная модель системы: компоненты, границы, связанность, нарушения, карта хрупкости/риска, коллапсы суперпозиции. Без исправлений — только модель.
- **RATIONALE:** Обнаружен CRITICAL-коллапс (INVARIANT COLLAPSE: заявленное cross-layer правило нарушено де-факто, а гейт слеп к нарушениям) — по протоколу skill требуется артефакт VerificationReport.md
- **ACCEPTANCE_CRITERIA:** Все 7 задач выполнены; ≥3 режимов суперпозиции проверено (проверено 9); каждое утверждение имеет evidence file:line; 6 сигналов коллапса проверены
- **IMPLEMENTS:** skill arch-forensics (Staff Software Architect Pattern)
- **IMPACTS:** core/AGENTS.md (cross-layer таблица), tests/test_cross_layer_imports.py (Gate #8), core/internal/bootstrap/*, core/modules/{backup-cron,monitoring,platform-secrets}
- **REQUIRES:** —

$START_VERIFICATION_REPORT

# Architecture Forensics Report: ai-platform

## Executive Summary

- **Components analyzed:** 24 (Makefile-фасад, 15 entrypoints, 20 internal-скриптов, 9 lib, 13 модулей, 3 compose-слоя, 8 CI-воркфлоу, 6 composite actions, тестовая инфраструктура ~822 test node IDs, 36 gate-файлов)
- **Boundaries found:** 6 (valid: 3, porous: 2, fractured: 1)
- **Violations:** 14 (circular: 1 self-ref, cross-layer: 9, SRP: 1, hidden deps: 3+)
- **Fragile points:** 5
- **Risk distribution:** CRITICAL: 1 · HIGH: 4 · MEDIUM: 6 · LOW: 13
- **Superposition collapses:** 2 (INVARIANT — CRITICAL, BOUNDARY — HIGH)
- **Verdict:** **NEEDS_ATTENTION**

Система в целом дисциплинирована выше среднего: единый Makefile-фасад с машиночитаемым реестром, 36 anti-drift гейтов, полный паритет manifest↔Makefile↔AGENTS.md, pinned-by-digest образы, DAG зависимостей модулей без циклов. Однако центральное cross-layer правило (`internal ↛ modules`) противоречиво задокументировано, систематически нарушается в рантайме и **невидимо для гейта, который обязан его защищать** — это INVARIANT COLLAPSE.

---

## 1. System Architecture (задача 1)

### Components

| Component | Type | Entry Points | Dependencies | Data Owned |
|-----------|------|-------------|--------------|------------|
| Makefile (root) | facade | `make <target>` ×30 | core/entrypoints/, docker compose | — |
| core/entrypoints/ ×15 | thin wrappers | Makefile, git hooks | internal/, lib/ | — |
| core/internal/bootstrap/ | orchestrator | bootstrap.sh, node-update.sh | internal/, lib/, **modules/** (runtime) | /var/lib/platform/.bootstrap-checkpoints/ |
| core/internal/deploy/ | deploy engine | CI forced-command | lib/, **modules/hooks** (runtime) | /var/log/platform/audit.log |
| core/lib/ ×9 | shared libs | source-only | — | PLATFORM_ROOT contract |
| core/modules/ ×13 | docker/system services | compose include, module Makefile | lib/, templates/ | volumes, spool dirs |
| docker-compose.yml | composition root | make up | 12 module base.yml (include:) | 6 networks, 11 volumes |
| platform-env.yaml | env SoT | provision-environment.sh | — | networks, volumes, ports, proxy.no_proxy_internal |
| entrypoint-manifest.yaml | ops registry | CI gates | — | allowed_verbs, gates, forbidden lists |
| .github/workflows/ ×8 | CI | push/PR/schedule/workflow_run | make targets, composite actions | — |
| tests/ (~822 node IDs) | quality gates | make test/gate | _conftest/, Docker | test_inventory.yaml baseline |

### Data Flow (dual delivery)

```
dev macOS ──make up──▶ docker compose (12 модулей, include-only)
   │ git push
   ▼
platform-test.yml (pre-commit + gate MODE=fast/full + integration)
   │ workflow_run(main)                        │ workflow_run(main)
   ▼                                           ▼
core-deploy.yml ──rsync core/──▶ VPS      build-platform.yml ──L1──▶ ghcr.io (backup)
   │ ssh: make node-update
   ▼
node-lifecycle.sh --mode update ──▶ provision → deploy-modules → healthcheck-all
                                        │
                                        └─ ensure_context_repo(): git clone/pull
                                           deploy-modules.sh:217,233 — ЕДИНСТВЕННЫЙ git на VPS ✅
```

### Lifecycle

- **node-lifecycle.sh --mode init:** 17 шагов (ssh-access → apt → tor → docker → users → firewall → verify-core → secrets → sudoers → **self-invoke --mode update** (строка 501) → audit → telegram). Шаг 15 отсутствует в нумерации (14 → 16).
- **--mode update:** verify-core → provision → deploy-docker → deploy-system → healthcheck-all (итерация `modules/<name>/healthcheck.sh`, node-lifecycle.sh:698-702).

---

## 2. Component Inventory (задача 3, ключевые)

### Component: core/internal/bootstrap/node-lifecycle.sh
- Purpose: единый оркестратор жизненного цикла ноды (init/update, mode dispatch)
- Data owned: checkpoint-маркеры /var/lib/platform/.bootstrap-checkpoints/
- Consumers: bootstrap.sh, node-update.sh, core-deploy.yml (через make node-update), сам себя (:501)
- Dependencies: 7 lib-файлов, 6 internal-скриптов, **modules/\*/healthcheck.sh** (:698)
- Invariants: идемпотентность (checkpoint-driven), NO git для core

### Component: core/internal/bootstrap/deploy-modules.sh
- Purpose: деплой docker/system-модулей по топологии
- Data owned: /opt/<context>/platform/ (context-overlay через git)
- Consumers: node-lifecycle.sh (:631, :646)
- Dependencies: lib/paths, lib/docker, `_topo_sort.py` (:848), **modules/\*/install.sh** (:325), **modules/\*/healthcheck.sh** (:419, :451), provision-environment.sh (:695, :699)
- Invariants: `ensure_context_repo()` — единственный git на VPS (:217, :233) — **HELD**

### Component: core/internal/provision-environment.sh
- Purpose: идемпотентный provision (сети/volumes/CI env) из platform-env.yaml
- Consumers: **5 разных вызывающих**: Makefile provision (:59), Makefile up (:74 — минуя make provision), provisioner-call action (:44), core-deploy.yml (:183-186), deploy-modules.sh (:695, :699)
- Invariants: единственный читатель-исполнитель platform-env.yaml networks/volumes

### Component: platform-env.yaml
- Purpose: SoT окружения (8 сетей, 16 volumes, 15 портов, proxy-контракт)
- Consumers: provision-environment.sh, tests/_conftest/infra.py:36-102, CI ×4, гейт T8.5
- Invariants: no_proxy_internal — канонический список (:76), .env NO_PROXY ⊇ его — **HELD** (гейт env-shared-consistency)

### Component: tests/gates/ (36 файлов, ~145 тестов)
- Purpose: anti-drift защита триады Makefile↔manifest↔AGENTS.md + контрактов модулей
- Invariants: триединая регистрация (файл+маркер+manifest) — **HELD** (test_gate_test_inventory.py)

Полный инвентарь 13 модулей: контрактная матрица — все 12 docker-модулей имеют module.yaml + base.yml + healthcheck.sh + Makefile + .dockerignore-symlink (проверено `ls -la`, симлинки на ../../templates/.dockerignore корректны). `platform-secrets` — system-модуль без base.yml, легально исключён из include.

---

## 3. Architectural Boundaries (задача 2, S7)

```
🧱 BOUNDARY: Makefile-фасад (все операции через make)
├─ Type: layer
├─ Declared at: AGENTS.md @invariants #1, Makefile:9
├─ Permeability: POROUS
├─ Violations: 3
│   1. .github/actions/provisioner-call/action.yml:44 — `bash core/internal/provision-environment.sh` в обход make (×4 воркфлоу)
│   2. core-deploy.yml:183-186 — прямой ssh bash provision-environment.sh (задокументировано TRAP[DECISION], deferred)
│   3. Makefile:74-75 — таргет `up` сам вызывает provision-environment.sh напрямую вместо `$(MAKE) provision`
└─ Verdict: WEAK — фасад держится для людей/агентов, но CI и сам Makefile его обходят в одной и той же точке (provision)

🧱 BOUNDARY: entrypoints → internal → lib (слоистость core/)
├─ Type: layer
├─ Declared at: core/AGENTS.md §Cross-layer import rules; enforcement: tests/test_cross_layer_imports.py:50-54
├─ Permeability: ENFORCED (для направления entrypoints→*)
├─ Violations: 0 — все 15 entrypoints вызывают только internal/ и lib/ (полный граф построен)
└─ Verdict: VALID

🧱 BOUNDARY: internal ↛ modules
├─ Type: layer
├─ Declared at: core/AGENTS.md таблица (`internal/` → только `internal/, lib/`); _IMPORT_RULES: test_cross_layer_imports.py:52
├─ Permeability: FRACTURED
├─ Violations: 6 рантайм-вызовов
│   - node-lifecycle.sh:698-702 → modules/<name>/healthcheck.sh
│   - deploy-modules.sh:325 → modules/<name>/install.sh
│   - deploy-modules.sh:419,451 → modules/<name>/healthcheck.sh
│   - deploy-project.sh:509 → modules/<name>/hooks/on-project-deploy.sh
│   - modules-healthcheck.sh:58,101 → modules/<name>/healthcheck.sh
├─ Контрадикция: core/entrypoints/healthcheck.sh:12-13 утверждает «internal/ → modules is permitted» — прямо противоречит core/AGENTS.md и линтеру
└─ Verdict: BROKEN — правило фиктивно; см. INVARIANT COLLAPSE

🧱 BOUNDARY: modules ↛ internal
├─ Type: layer
├─ Declared at: core/modules/AGENTS.md §Запрет #2
├─ Permeability: POROUS (нарушения вне видимости import-графа)
├─ Violations: 3 рантайм
│   - core/modules/backup-cron/scripts/crontab:44 → /opt/core/internal/healthcheck/docker-healthcheck.sh (cron)
│   - core/modules/monitoring/hooks/on-project-deploy.sh:321 → core/internal/catalog/generate-catalog.sh
│   - core/modules/platform-secrets/platform-secrets.service:13 → ExecStart=/opt/platform/core/internal/secrets/decrypt-secrets.sh (systemd)
└─ Verdict: WEAK — все 3 нарушителя невидимы линтеру (crontab/systemd-unit — не .sh импорты)

🧱 BOUNDARY: Core (SCP/rsync, NO git) vs Context-overlay (git)
├─ Type: deployment
├─ Declared at: root AGENTS.md §Инварианты dual delivery
├─ Permeability: ENFORCED
├─ Violations: 0 — git на VPS только в ensure_context_repo (deploy-modules.sh:217, :233)
└─ Verdict: VALID

🧱 BOUNDARY: Модульная изоляция (module contract)
├─ Type: domain
├─ Declared at: core/modules/AGENTS.md MODULE_CONTRACT
├─ Permeability: ENFORCED
├─ Violations: 0 контрактных (profiles ✅, x-logging ✅ ×12, ${VAR:?} = 0, symlinks ✅)
└─ Verdict: VALID
```

---

## 4. Violations (задача 4)

### Violation: Cross-layer (internal → modules) — де-факто конвенция против де-юре запрета
- Component: node-lifecycle.sh, deploy-modules.sh, deploy-project.sh, modules-healthcheck.sh
- Evidence: node-lifecycle.sh:698 → `${CORE_DIR}/modules/${mod_name}/healthcheck.sh`; deploy-modules.sh:325, :419, :451; deploy-project.sh:509; modules-healthcheck.sh:58, :101
- Impact: правило в core/AGENTS.md и _IMPORT_RULES (test_cross_layer_imports.py:52) описывают несуществующую систему; агенты, читающие AGENTS.md, будут принимать неверные решения
- Severity: **CRITICAL** (в связке с слепотой гейта — см. коллапс)

### Violation: Гейт Gate #8 слеп к рантайм-вызовам через переменные
- Component: tests/test_cross_layer_imports.py
- Evidence: вызовы вида `bash "$hc_script"` (node-lifecycle.sh:701) не распознаются — `_looks_like_path("$hc_script")` (test_cross_layer_imports.py:118-126) требует `/` в строке, а путь собран в промежуточную переменную (:698). Аналогично `"$hook_script"` (deploy-project.sh:509), `"$install_script"` (deploy-modules.sh:325)
- Impact: Gate #8 репортит «0 violations» (test_gate_cross_layer.py:60) при 6 фактических нарушениях — ложная гарантия
- Severity: **CRITICAL**

### Violation: Контрадикция документации cross-layer
- Component: core/entrypoints/healthcheck.sh vs core/AGENTS.md
- Evidence: healthcheck.sh:12-13 «internal/ → modules is permitted» ↔ core/AGENTS.md таблица «internal/ → internal/, lib/ | Всё остальное запрещено»
- Impact: два авторитетных источника дают противоположные ответы
- Severity: HIGH

### Violation: modules → internal через невидимые каналы (cron/systemd)
- Component: backup-cron, monitoring, platform-secrets
- Evidence: crontab:44; on-project-deploy.sh:321; platform-secrets.service:13
- Impact: изменение путей internal/ ломает cron и systemd на VPS без сигнала от гейтов
- Severity: HIGH

### Violation: Циклическая сэндвич-цепочка internal → modules → internal
- Component: deploy-project.sh → monitoring hook → generate-catalog.sh
- Evidence: deploy-project.sh:509 → modules/monitoring/hooks/on-project-deploy.sh → :321 `${PLATFORM_ROOT}/core/internal/catalog/generate-catalog.sh`
- Impact: граница пересекается дважды за один деплой; generate-catalog.sh при этом числится сиротой в статическом графе (его MODULE_CONTRACT ссылается на несуществующий вызов из deploy-project.sh)
- Severity: MEDIUM

### Violation: Self-reference node-lifecycle.sh
- Component: node-lifecycle.sh
- Evidence: :501 `bash node-lifecycle.sh --mode update` (step 14 init-режима)
- Impact: intentional (задокументировано), но это единственный цикл в графе; рекурсия без глубинного ограничителя
- Severity: LOW

### Violation: SRP — тройная роль provision-environment.sh
- Component: core/internal/provision-environment.sh
- Evidence: 5 вызывающих из 3 контекстов (Makefile:59, Makefile:74, action.yml:44, core-deploy.yml:183, deploy-modules.sh:695)
- Impact: один скрипт — контракт для make, CI и деплой-оркестратора одновременно; изменение сигнатуры → 3 зоны поражения
- Severity: MEDIUM

### Violation: Сироты / фиктивные контракты
- Evidence: internal/catalog/generate-catalog.sh — MODULE_CONTRACT утверждает вызов из deploy-project.sh, вызова нет (реальный вызыватель — monitoring hook); check-commit-msg.sh, check-doc-headers.sh, pre-push-gate.sh — живут только в git hooks, зарегистрированы в манифесте как `script:` (легально)
- Severity: LOW

### Violation: Устаревший TRAP[DEBT] в Makefile
- Evidence: Makefile:282-287 — TRAP[DEBT] о «gate MODE=fast проглатывает падения», но текущий код (Makefile:295-309) уже имеет `|| { echo FAIL; exit 1; }` на каждом шаге
- Impact: TRAP описывает исправленную проблему → кандидат на TRAP[ARCHIVED]
- Severity: LOW

### Дополнительно (S12/конфиг):
- `staging-proxy-net`, `staging-shared-db-net` объявлены только в platform-env.yaml — мёртвые объявления (0 потребителей) — LOW
- `.env.example`: PLATFORM_CONTEXT, OPENROUTER_API_KEY, ANTHROPIC_API_KEY, GLM_API_KEY, API_SERVER_* — объявлены, активных потребителей не найдено — LOW
- push-gate.yml: двойной запуск pre-commit-run (:66 отдельно + внутри `make gate MODE=fast` :69) — LOW
- 22 строки идентичного cleanup-кода в platform-test.yml:321-342 и nightly-gate.yml:104-126 — LOW

---

## 5. Fragility Map (задача 5, S14)

```
📡 CHANGE IMPACT: core/lib/paths.sh
├─ Direct dependents (1-hop): ВСЕ 15 entrypoints + ~10 internal-скриптов
├─ Configuration propagation: PLATFORM_ROOT (default /opt/platform) — контракт для VPS, CI, тестов
└─ ⚡ FRAGILE: изменение семантики PLATFORM_ROOT → каскад по ~25 файлам + прод-пути cron/systemd

📡 CHANGE IMPACT: module.yaml схема (D4)
├─ Direct: 13 module.yaml
├─ Test propagation: test_gate_module_schema_d4, test_gate_module_yaml_contract, test_gate_topology, test_gate_container_name_consistency, _conftest/audit.py
├─ Runtime: deploy-modules.sh (yaml_get_field), _topo_sort.py
└─ ⚡ FRAGILE: новое поле → 5+ гейтов + deploy-оркестратор

📡 CHANGE IMPACT: entrypoint-manifest.yaml
├─ Direct: 36 gate-файлов (триединая регистрация), lint.sh namelint, Makefile .PHONY
├─ Logical coupling: любой новый make-таргет = 3 синхронных правки (Makefile + manifest + core/AGENTS.md)
└─ ⚡ FRAGILE by design — хрупкость намеренная (anti-drift), но co-change 100%

📡 CHANGE IMPACT: core/internal/provision-environment.sh
├─ Direct: Makefile (×2 точки), provisioner-call action (×4 воркфлоу), core-deploy.yml, deploy-modules.sh
└─ ⚡ FRAGILE: смена CLI-флагов → одновременно локальная разработка, CI и прод-деплой

📡 CHANGE IMPACT: пути core/internal/* на VPS
├─ Hidden dependents: crontab:44, platform-secrets.service:13, watchdog units
└─ ⚡ FRAGILE: переименование/перемещение internal-скрипта → тихая поломка cron/systemd на всех нодах (гейты не видят)
```

Пары высоковероятного co-change: (Makefile ↔ entrypoint-manifest.yaml ↔ core/AGENTS.md) — enforced triad; (.env.example ↔ .env) — enforced гейтом env-example-sync; (module base.yml ↔ module.yaml resources) — не enforced (гейта на resources-паритет нет).

---

## 6. Risk Map (задача 6, S10, S11)

```
🎲 RISK: node-lifecycle.sh + deploy-modules.sh (деплой-оркестратор)
├─ Likelihood: 4 — сложнейшие файлы проекта (866/850+ строк), частые изменения
├─ Impact: 5 — отказ = нода не поднимается/не обновляется; blast radius = все 13 модулей
├─ Detectability: 3 — contract-тесты bash-функций есть, но полный путь проверяется только на живом VPS
├─ Risk Score: 4×5÷3 = 6.7
└─ Tier: HIGH

🎲 RISK: cross-layer инвариант + Gate #8
├─ Likelihood: 4 — каждый новый internal-скрипт может добавить невидимое нарушение
├─ Impact: 3 — эрозия архитектурной модели, неверные решения агентов
├─ Detectability: 1 — гейт слеп к вызовам через переменные, cron, systemd
├─ Risk Score: 4×3÷1 = 12
└─ Tier: HIGH (граница CRITICAL)

🎲 RISK: platform-secrets (systemd, /run/platform/secrets.env)
├─ Likelihood: 2 — редкие изменения
├─ Impact: 5 — secrets.env читают deploy-modules, docker-healthcheck, notify-hook, checkpoint; отказ = деплой и алертинг мертвы
├─ Detectability: 2 — install.sh + healthcheck.sh, но systemd-путь захардкожен (platform-secrets.service:13)
├─ Risk Score: 2×5÷2 = 5
└─ Tier: MEDIUM

🎲 RISK: provision-environment.sh
├─ Likelihood: 3 · Impact: 4 (5 вызывающих) · Detectability: 3 (гейты provisioner usage есть)
├─ Risk Score: 4
└─ Tier: MEDIUM

🎲 RISK: hermes-agent (L1/L2, watchdog, proxy-канал)
├─ Likelihood: 4 — самый активный модуль (build/, context/, overlays/, watchdog/)
├─ Impact: 3 — изолирован в hermes-agent-net; proxy opt-in контракт гейтится (T8.5)
├─ Detectability: 4 — component/integration тесты + watchdog
├─ Risk Score: 3
└─ Tier: MEDIUM

🎲 RISK: Makefile gate/test диспетчеры
├─ Likelihood: 3 · Impact: 4 · Detectability: 4 (CI выполняет их же; TRAP[DEBT] о fast-режиме уже устарел — код исправлен)
├─ Risk Score: 3
└─ Tier: MEDIUM

💥 FAILURE (S10, выборочно): postgres — direct dependents: litellm, langfuse, backup-cron, hermes-agent (module.yaml depends_on); blast radius 4+ модулей, graceful degradation NO (hard failure LLM-стека). nginx — dependents: hermes-agent, infra-metrics, monitoring; blast radius = весь публичный вход. platform-secrets — recovery dependency для деплоя и алертинга (см. выше).
```

LOW-tier (13): отдельные модули с pinned-digest образами, полными контрактами и healthcheck'ами (redis, clickhouse, minio, logging, monitoring, infra-metrics, langfuse, litellm, nginx, postgres, backup-cron), templates/, schemas/.

### S9: Ownership (ключевые конфликты)

```
👑 OWNERSHIP: redis image digest
├─ Creator: core/modules/redis/docker-compose.base.yml:36
├─ Duplicated: core/modules/langfuse/docker-compose.base.yml:121 (langfuse-redis, тот же digest)
└─ ⚠️ Задокументировано TRAP[DECISION] (langfuse base.yml:117) — принятый дубль, ручная синхронизация digest'ов

👑 OWNERSHIP: NO_PROXY список
├─ SoT: platform-env.yaml:76 (no_proxy_internal)
├─ Readers: .env.example:178 (⊇-контракт), hermes-agent module.yaml:39
└─ Гейт T8.5 enforced — конфликт снят ✅

👑 OWNERSHIP: provision (сети/volumes)
├─ Updaters: make provision, make up (напрямую), CI provisioner-call ×4, core-deploy.yml, deploy-modules.sh
└─ ⚠️ CONFLICT: 5 инициаторов одной мутации; идемпотентность скрипта — единственная защита
```

### S13: Dependency (модульный DAG)

`postgres, redis, nginx, clickhouse, minio, logging` — корни; `litellm→postgres`; `langfuse→postgres,clickhouse`; `backup-cron→postgres`; `monitoring→nginx`; `infra-metrics→nginx`; `hermes-agent→nginx,postgres,redis,litellm`. Циклов нет ✅ (подтверждено test_gate_topology.py). Несуществующих depends_on нет.

### S15: Hidden Dependencies

```
🕶️ HIDDEN: crontab → internal/healthcheck/docker-healthcheck.sh
├─ Type: ENVIRONMENT · Evidence: core/modules/backup-cron/scripts/crontab:44
├─ Break scenario: переименование internal/healthcheck/ → минутный cron тихо умирает
└─ Detectability: только grep

🕶️ HIDDEN: systemd → internal/secrets/decrypt-secrets.sh
├─ Type: ENVIRONMENT · Evidence: platform-secrets.service:13 (абсолютный путь /opt/platform/...)
└─ Break scenario: смена PLATFORM_ROOT или пути скрипта → секреты не расшифровываются при буте

🕶️ HIDDEN: /run/platform/secrets.env — общий ресурс 5 потребителей
├─ Type: RESOURCE · Evidence: decrypt-secrets.sh (writer); deploy-modules.sh, docker-healthcheck.sh, tor-proxy-healthcheck.sh, notify-hook.sh, checkpoint.sh (readers)
└─ Break scenario: изменение формата/пути → каскад из 5 читателей без контракта

🕶️ HIDDEN: make dev-certs читает .env через grep (KNOWLEDGE/CONVENTION)
├─ Evidence: Makefile:48 — recipe-level `grep PLATFORM_DOMAIN= .env` (TRAP[BUG] 2026-07-16 задокументирован)
└─ Detectability: contract-проверка добавлена, но паттерн «make не читает .env» остаётся системным риском
```

---

## 7. Superposition Collapses

```
⚡ INVARIANT COLLAPSE — CRITICAL
├─ S12: инвариант «internal ↛ modules» (core/AGENTS.md, _IMPORT_RULES test_cross_layer_imports.py:52) нарушен в 6 точках (node-lifecycle.sh:698; deploy-modules.sh:325,419,451; deploy-project.sh:509; modules-healthcheck.sh:58,101)
├─ S15: все нарушения проходят через промежуточные переменные ($hc_script, $hook_script, $install_script) — _looks_like_path() (:118-126) их не видит; Gate #8 репортит «0 violations»
├─ S7: третий источник (healthcheck.sh:12-13) декларирует противоположное правило
└─ Verdict: заявленная граница фиктивна, защищающий её гейт даёт ложную гарантию, документация противоречит сама себе. Система работает — но её архитектурная модель недостоверна в этой точке.

⚡ BOUNDARY COLLAPSE — HIGH
├─ S7: граница modules ↛ internal POROUS (3 рантайм-нарушителя: crontab:44, monitoring hook:321, platform-secrets.service:13)
├─ S8: environmental coupling HIGH — cron/systemd связывают слои через абсолютные прод-пути
├─ S15: все 3 канала (cron, systemd, hook-цепочка) невидимы для import-графа и гейтов
└─ Verdict: изоляция modules/internal де-факто односторонне монолитна на VPS; сэндвич internal→modules→internal (deploy-project.sh:509 → monitoring hook → generate-catalog.sh) пересекает границу дважды.
```

**Проверены и НЕ сработали:** OWNERSHIP COLLAPSE (мульти-writer provision защищён идемпотентностью + гейтами), RISK COLLAPSE (оркестратор HIGH, но покрыт contract-тестами и checkpoint'ами), FRAGILITY COLLAPSE (триада Makefile↔manifest↔AGENTS.md — намеренная enforced-хрупкость), CIRCULAR COLLAPSE (единственный цикл — задокументированный self-invoke :501, домен не пересекает).

---

## Сильные стороны (для объективности модели)

1. Dual-delivery инвариант (git только в ensure_context_repo) — **HELD**, evidence deploy-modules.sh:217,233.
2. Все образы pinned by digest (`@sha256:`) — 0 «плавающих» тегов кроме локальных build-артефактов (backup-cron:latest, hermes-agent-base:latest в dev-override).
3. Manifest↔Makefile↔AGENTS.md — полный двунаправленный паритет (27 allowed_verbs = 27 таргетов, 4 system_exceptions учтены).
4. Skip-rate 0.97% (8 skipif из 822), все условно-интеграционные; skip-enforcement gate активен.
5. Инварианты 1, 2, 4, 5, 7, 8 root AGENTS.md — прямое тестовое покрытие; 3, 6, 9, 10 — косвенное/отсутствует (gap для test-план).

$END_VERIFICATION_REPORT
