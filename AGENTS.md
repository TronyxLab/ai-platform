<!-- GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture -->

<!-- ai-instructions:0.6.3 -->
# GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture
# STRUCTURE: ┌make targets┐ → ◇ invariants (11 rules) → ◇ deploy-model (local→CI→context) → ⊕ verb glossary → ⎋ navigation
# region MODULE_CONTRACT
## @purpose  Root architecture documentation for ai-platform — defines invariants, deploy model, verb glossary
## @scope    Project-wide architectural rules, deployment model, canonical make targets, navigation
## @invariants
##   1. Makefile — единый фасад. Все операции через `make <target>`. entrypoints — internal-обёртки.
##   2. Модель деплоя: git push → CI. Для проекта: `make deploy` (git push → CI → forced-command). Для платформы: `make context-promote` (копирование в контекстную org → CI → деплой). Core-код доставляется CI-воркфлоу (rsync/scp с аудит-трейлом).
##   3. org = context. tronyx161 — исходный репозиторий. Каждый контекст — отдельная GitHub-организация.
##      context определяется из физического пути projects/<context>/<project>/, поле context в ai-platform.yaml УДАЛЕНО (DevPlan 020).
##   4. AGENTS.md — 3 канонических файла (root, core/, core/modules/) + вспомогательные, перечисленные в §Навигация; файлы в templates/template-*/ — payload шаблонов new-project/new-context, вне скоупа инварианта.
##   5. core/entrypoint-manifest.yaml — YAML-реестр канонических операций для CI-gate'ов.
##   6. make bootstrap-node — строго идемпотентный. Второй вызов = no-op (INIT, не DEPLOY).
##   7. Полный локальный стек через `docker compose up` на macOS разработчика.
##   8. LiteLLM — PostgreSQL во всех окружениях (никакого SQLite).
##   9. Тестовый сервер может быть пересоздан заново — обратная совместимость не требуется.
##   10. Сборка образов hermes: `make hermes-build-platform` (L1, локально + push в ghcr.io как backup), `make hermes-push-l1` (L1 push в ghcr.io как disaster recovery + дистрибутивная база, public package, DevPlan 116 B3 D1) и `make hermes-build-context CONTEXT=<context>` (L1→L2, контекстная разработка и production).
##   11. Manifest Generation Contract — authoritative sources (module.yaml, secret-definitions.yaml, platform-infra.yaml, Makefile .PHONY, @pytest.mark.gate) порождают generated files (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py, entrypoint-manifest.yaml#allowed_verbs/gates, core/AGENTS.md generated-секции). Generated files коммитятся, но НЕ редактируются вручную. CI gate `make check-manifests` блокирует divergence.
## @rationale Single source of truth for platform architecture consumed by autonomous agents and developers
## @rationale (D2) Invariant 4 обновлён по результатам drift-аудита: 3 канонических + 2 вспомогательных (core/internal/bootstrap/, tests/gates/) в §Навигация root AGENTS.md; templates/template-*/ — payload `make new-project`/`make new-context`, вне скоупа инварианта (не являются архитектурной документацией платформы)
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · Bootstrap forced-command → orchestrator_cli dispatch (DevPlan 116 B8 D2, волна 117 D1)
## · Rejected: оставить forced-command на удалённый legacy deploy-скрипт (риск: новые ноды получают сломанные authorized_keys)
## · Reason: единственный писатель ci-deploy ключа — Python lifecycle φ2 (helpers/users.py add_ssh_key,
##   phases/system.py): `command="cd {base} && PYTHONPATH={base} python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict`
##   (SSH_ORIGINAL_COMMAND-диспетчер verbs, DevPlan 116 B1; base = platform_remote_base() канон, DevPlan 125 T3). setup-node.sh — ТОЛЬКО generate_sudoers
##   (visudo -c + atomic mv); create_user/add_owner_key/add_ci_deploy_command удалены (дубли φ2, волна 117 D1).
## · Rev: — (B1 реализован; dispatch — канонический канал. Rev-условие снято волной 117 D1.)
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · Строгий гейт фантомов — 0 упоминаний 4 удалённых имён (DevPlan 116 B8 D3)
## · Rejected: мягкий allowlist-режим (риск: allowlist-дрейф как в dead-code gate; фантомные имена живут в docstring/TRAP)
## · Reason: решение пользователя 2026-08-01 (D3) — история удаляется вместе с именами;
##   перечень имён — tests/gates/test_gate_phantom_refs.py::_PHANTOM_NAMES, allowlist гейта пуст.
## · Rev: 2026-10-21 — пересмотр, если гейт начнёт блокировать легитимную историческую документацию.
## @changes  Plan 082 — added Template Mechanisms section with decision table
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · SSH_OPTS — Python SoT shared/ssh_opts.py (D1 B5) — триггер vps_readiness:37-42 сработал
## · Rejected: 5 Python-копий «SSH_OPTS» + shell lib/ssh.sh (каждая правка ConnectTimeout = 6 правок; ConnectTimeout=10 outlier в context_promoter)
## · Reason: «extract when consumers > 3» (5 потребителей) + решение пользователя 2026-08-01 (D1) — Python SoT, lib/ssh.sh — тонкий фасад через python3 -m ... --shell (уменьшение bash-поверхности). Гейт ssh_opts_sole_path enforce-ит.
## · Rev: если появится второй shell-потребитель флагов — пересмотреть фасад.
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · Единый канон healthcheck-критерия (D5 B5) — inspect State.Health, running-без-healthcheck = здоров
## · Rejected: 5 расходящихся реализаций (ps-filter 60/3, wrapper 10/1, inspect 60/2, lib/healthcheck.sh, poller 30) с разными семантиками ""/starting
## · Reason: U-14 — расхождение уже началось. Канон: контейнер running AND (healthy|""|none) = здоров; "unhealthy" → ждать (стартовые гонки). Python-реализация — ТОЛЬКО deploy/healthcheck_poller.py (гейт docker_sole_path); lib/healthcheck.sh — shell-фасад с тем же критерием (D5).
## · Rev: если у контейнера появится состояние, требующее иного трактования — менять канон в одном месте.
## ⚠️ TRAP[DECISION] · 2026-07-15 · HI · L1 pushed to ghcr.io as backup, never used directly by contexts
## · Rejected: local-only L1 (risk: loss of build machine → rebuild from scratch)
## · Reason: L1 contains no secrets (only Python dependencies). Push = disaster recovery, not delivery model change.
## · 2026-08-01 (B3 D1, DevPlan 116 T7): L1 ПУБЛИКУЕТСЯ (public package hermes-agent-base на tronyx161,
## ·   org/repo остаются приватными) — distribution base для контекстных L2-сборок и L1-только тестов;
## ·   контексты НЕ используют L1 как runtime (runtime = L2), L1 — build-base + smoke-цель.
## · Rev: if L1 starts carrying context-specific data → revert to local-only.
## ⚠️ TRAP[DECISION] · 2026-07-22 · MED · Strangler-Fig decomposition — canonical pattern для shell→Python миграции
## · Rejected: Big-bang rewrite (risk: 4114 LOC top-3 монолиты → один PR — слишком высокий риск регрессии)
## · Reason: Strangler-Fig (пошаговая декомпозиция бизнес-логики в Python + unit-тесты + shell-фасад) выбран как canonical pattern на основе Wave 4 (035-wave4-strangler-top3). Каждый из топ-3 скриптов (deploy-modules, converge, node-lifecycle) разбит независимо: Python-модуль получает бизнес-логику, shell остаётся тонким фасадом (<100-200 LOC). Результат: 4114→392 LOC shell (90% сокращение), 0 inline python3 блоков, 7 новых unit-тестов.
## · Rev: если новый shell-скрипт достигает >500 LOC с inline python3 → применять Strangler-Fig немедленно, без утверждения архитектором.
## ⚠️ TRAP[DECISION] · 2026-07-22 · HI · Bootstrap pipeline redesign — deploy-context as step 18 (index 23)
## · Rejected: Option A (full rewrite of state machine, risk: regression after Decision Gate HARD STOP)
## · Reason: Option B (evolutionary extension) preserves invariants, adds 4 components: preflight.py gate, docker_registry_auth.py step (index 5), cert_orchestrator.py, context_deployer.py step (index 23). Solves StatusReport 045 problems (projects not deployed, certs missing, Docker Hub rate-limit).
## · Constraint: 1 node = 1 context (CONTEXT extracted from node.yaml context field). No multi-context ambiguity.
## · ⚠️ Shell facade step functions renumbered (indices 5→6 through 21→22) — see TRAP[INDEX] in DevPlan 047.
## · Rev: if deploy-context step adds >5min to bootstrap → make it async (background job + telegram notify)
# endregion MODULE_CONTRACT

# AGENTS.md — ai-platform

---

## Triple Delivery Model

```
                    ┌─ Core (SCP/rsync, push-based, NO git)
                    │   make bootstrap-node → SCP core/ + node-configs/
                    │   CI core-deploy → rsync core/ → /opt/platform/core/
                    ▼
┌─ Локальная разработка ─────────────────────────────┐
│  make test/lint/gate → docker compose up (healthy)   │
└─────────────────────────────────────────────────────┘
                           ↓ git push
┌─ Source CI (tronyx161/ai-platform) ────────────────┐
│  Все gate'ы → валидация → код готов к промоуту      │
└─────────────────────────────────────────────────────┘
                           ↓ make context-promote CONTEXT=<context>
┌─ Context CI (<org>/ai-platform) ───────────────────┐
│  Сборка L1→L2 → push ghcr.io → авто-деплой на VPS  │
└─────────────────────────────────────────────────────┘
                    ▲
                    │ ┌─ Context-overlay (git, pull-based)
                    │   context_overlay.py → ensure_context_repo()
                    │   git clone/pull → /opt/<context>/platform/
```

### Три канала доставки кода на VPS

| Канал | Механизм | Направление | Применение |
|-------|----------|-------------|------------|
| **Core** | SCP/rsync | Push (с машины оператора/CI) | `core/`, `node-configs/`, `secrets/` — инфраструктурный код платформы |
| **Context-overlay** | git clone/pull | Pull (с VPS в репозиторий) | Контекстные overlay, модульные конфигурации, кастомизации |
| **Project payload** | tar по SSH forced-command (`receive`) | Push (CI) | docker-compose.yml, ai-platform.yaml, .env.platform |

### Инварианты

1. **Core-код NEVER доставляется через git** на VPS — только SCP/rsync. Никаких git-токенов, deploy-keys или repo-URL для core на сервере.
2. **Context-overlay использует git** для клонирования/пула контекстного репозитория — это overlay-кастомизация поверх core.
3. **`ensure_context_repo()`** в `context_overlay.py` (Python-модуль, вызывается из deploy_orchestrator.py) — единственное место, где git выполняется на VPS.
4. **AGE-ключи, secrets, SSH-keys** никогда не передаются через git — только через SCP/age-encrypted файлы.

⚠️ TRAP[DECISION] · 2026-07-15 · HI · Dual delivery — не ослабление безопасности
· Риск: контринтуитивно — «NO git» в core, но git в context-overlay
· Решение: инвариант уточнён, не ослаблен. Core остаётся push-only SCP/rsync (zero git surface).
· Context-overlay — это пользовательский код, который ВЫБИРАЕТ храниться в git.
· Rev: если context-overlay начнёт нести критичные секреты — пересмотреть модель.

---

## Глоссарий глаголов

| Статус | Глагол | Операция |
|--------|--------|----------|
<!-- GENERATED:START:glossary -->
| ✅ | `_get_all_profiles` | Вывод COMPOSE_PROFILES |
| ✅ | `adopt-project` | Адаптация существующего проекта |
| ✅ | `backup` | Резервное копирование |
| ✅ | `bootstrap-node` | Идемпотентный bootstrap ноды |
| ✅ | `check` | Диагностика — все проверки из core/check-suite.yaml (экс-preflight) |
| ✅ | `check-dead-code` | Проверка мёртвого кода |
| ✅ | `check-diff` | Узкая диагностика по изменённым файлам |
| ✅ | `check-domain-parity` | Parity-гейт PLATFORM_DOMAIN (единое определение, 0 legacy-доменов) |
| ✅ | `check-env-defaults` | Проверка актуальности .env.example |
| ✅ | `check-exception-patterns` | Проверка паттернов исключений |
| ✅ | `check-file-lines` | Проверка длины файлов |
| ✅ | `check-manifests` | Проверка актуальности сгенерированных манифестов |
| ✅ | `check-profiles-parity` | Parity-гейт COMPOSE_PROFILES (единый SoT platform-infra.yaml) |
| ✅ | `check-requirements` | Проверка актуальности requirements.txt |
| ✅ | `check-security` | Проверка security-постурa ноды |
| ✅ | `compose-safe-up` | Deprecated alias for up-safe |
| ✅ | `context-promote` | Промоут платформы в контекст |
| ✅ | `converge` | Реконсиляция ноды |
| ✅ | `deploy` | Деплой проекта |
| ✅ | `deploy-context` | Деплой проектов контекста на ноде |
| ✅ | `deploy-project` | Прямой деплой минуя CI (DeployOrchestrator deliver) |
| ✅ | `dev-certs` | Генерация dev SSL-сертификатов |
| ✅ | `discover-modules` | Авто-обнаружение модулей |
| ✅ | `down` | Остановка compose-стека |
| ✅ | `down-volumes` | Остановка compose-стека и удаление volumes |
| ✅ | `doxygen-check` | Doxygen zero-warnings проверка |
| ✅ | `fix-executable-bit` | Исправление executable bit на .sh файлах |
| ✅ | `fix-gate` | Композитное исправление gate-ошибок |
| ✅ | `fix-ruff` | Форматирование Python файлов через ruff |
| ✅ | `gate` | Production gate |
| ✅ | `generate-agents-md` | Генерация core/AGENTS.md |
| ✅ | `generate-entrypoint-manifest` | Генерация entrypoint-manifest.yaml |
| ✅ | `generate-env-example` | Генерация .env.example |
| ✅ | `generate-litellm-config` | Генерация litellm-config.yml |
| ✅ | `generate-manifests` | Генерация всех манифестов |
| ✅ | `generate-platform-env` | Генерация platform-env.yaml + Python env files |
| ✅ | `generate-requirements` | Генерация requirements.txt из pyproject.toml |
| ✅ | `generate-secrets-manifest` | Генерация secrets-manifest.yaml |
| ✅ | `healthcheck` | Проверка здоровья |
| ✅ | `hermes-build-context` | Сборка L1→L2 образа |
| ✅ | `hermes-build-platform` | Сборка L1 образа |
| ✅ | `hermes-push-l1` | Push L1 в ghcr.io |
| ✅ | `hermes-push-l2` | Push L2 в ghcr.io |
| ✅ | `lint` | Линтинг |
| ✅ | `new-context` | Создание контекста деплоя |
| ✅ | `new-project` | Создание проекта из шаблона |
| ✅ | `node-update` | Обновление provisioned ноды |
| ✅ | `preflight` | Deprecated-алиас — диагностика через make check |
| ✅ | `project-list` | Список проектов |
| ✅ | `project-status` | Статус проекта |
| ✅ | `project-sync-env` | Синхронизация .env.platform и AI-PLATFORM.md |
| ✅ | `provision` | Provision окружения |
| ✅ | `provision-llm` | Provision LiteLLM virtual keys |
| ✅ | `remove-project` | Удаление проекта из lifecycle |
| ✅ | `render-monitoring` | Рендер конфигурации мониторинга после деплоя проекта |
| ✅ | `render-vhosts` | Генерация vhost конфигов |
| ✅ | `restart` | Мягкий перезапуск compose-стека |
| ✅ | `restore` | Восстановление из бэкапа |
| ✅ | `scripts-audit` | Аудит регистрации скриптов |
| ✅ | `secrets-unlock` | Расшифровка секретов |
| ✅ | `status` | Статус compose-стека |
| ✅ | `sync-env-defaults` | Генерация .env.example из SoT |
| ✅ | `templates-check` | Проверка покрытия и разрешимости шаблонов |
| ✅ | `templates-render` | Рендер шаблонов |
| ✅ | `test` | Запуск тестов |
| ✅ | `test-inventory-sync` | Синхронизация test inventory |
| ✅ | `test-node` | E2E pipeline тесты на test-VPS |
| ✅ | `test-summary` | Запуск тестов (агент-ориентированная обёртка) |
| ✅ | `up` | Запуск compose-стека |
| ✅ | `up-safe` | Безопасный compose up |
| ✅ | `validate` | Schema-валидация |
| ✅ | `validate-modules` | Валидация module.yaml |
| ✅ | `verify` | HTTPS-верификация |
<!-- GENERATED:END:glossary -->
| ❌ | `push-core`, `deploy-node`, `build-local`, `bootstrap-core`, `hermes-deploy-vps` | Запрещены — не из словаря |

**Правило:** одно имя таргета не может означать разное в разных Makefile. Все таргеты регистрируются в `core/entrypoint-manifest.yaml`.

**Правило создания проекта:** `make new-project` — единственный способ создания проекта. Ручное создание проектной директории не регистрирует проект в lifecycle и требует `make project-sync-env` для синхронизации .env.platform.

**Двухуровневая семантика:** root-глагол = оркестрация стека, module-глагол = операция одного модуля. Глаголы `up`, `down`, `restart`, `backup`, `restore` имеют разную реализацию на уровне root Makefile (весь стек) и в module.mk (один модуль).

---

## Языковая политика

**Главное правило:** новый код платформы пишется только на Python. Bash допустим исключительно как тонкая обёртка над Python-модулями и системными утилитами (`rsync`, `scp`, `ssh`, `docker`, `systemctl`).

**Принципы применения:**

1. **Новый код = Python.** Любая новая подсистема, бизнес-логика, валидатор, парсер, state-machine — пишется на Python (предпочтительно в `core/internal/scripts/` или `core/internal/<domain>/`). Shell-обёртка в `core/entrypoints/` только вызывает `python3 script.py` и пробрасывает exit code.

2. **Bash остаётся для:** entrypoints (тонкие фасады, вызываемые из Makefile), чистой оркестрации (последовательность subprocess-вызовов без логики), lib-функций низкого уровня (`lib/logging.sh`, `lib/paths.sh`, `lib/ssh.sh`). Существующие стабильные shell-библиотеки (`logging.sh`, `paths.sh`, `healthcheck.sh`, `module-interface.sh`) НЕ мигрируются на Python — их API стабилен и замена не даст прироста.

3. **Inline Python и heredoc — сигнал к извлечению.** Любой `python3 -c "..."` или `python3 - <<PYEOF ... PYEOF` в bash-скрипте — это сигнал: логику нужно вынести в отдельный `.py` файл и вызывать через `python3 script.py`. Никаких исключений для нового кода. Для существующего кода: миграция через двухуровневый Strangler-триггер (см. п.4).

4. **Strangler-триггер (двухуровневый порог переписывания на Python):**
   - **Tier 1 (немедленный — при ЛЮБОМ изменении скрипта):**
     - Добавление нового `python3 -c "..."` или heredoc-блока → вынести эту конкретную логику в отдельный `.py` модуль. Не переписывать всю подсистему.
     - Добавление >3 новых `if`-веток с бизнес-логикой (не оркестрацией) → вынести business-logic в Python-модуль.
   - **Tier 2 (плановый — при накоплении):**
     - В одном shell-файле накоплено ≥3 Tier-1 экстракций → плановая Strangler-декомпозиция всей подсистемы в Python. Shell остаётся фасадом <150 строк.
     - Баг-фикс затрагивает >2 ответственностей одного скрипта → регистрируется в debt-tracker, включается в план следующей волны.

   **Важно:** Tier 1 НЕ требует переписывать весь скрипт — только извлечь новую/изменяемую логику в Python-модуль. Это устраняет извращённый стимул «не чини баг, потому что придётся переписывать всё».

5. **Несовпадение с hybrid-без-дисциплины:** это не откладывание решения, а Strangler-Fig discipline — каждая переписанная подсистема становится тестируемой, типизированной и grep-able. Двухуровневый триггер обеспечивает непрерывное движение к Python без блокировки баг-фиксов.

⚠️ TRAP[DECISION] · 2026-07-21 · HI · Enforcement языковой политики через AGENTS.md + pre-commit hook, не CI gate
· Rejected: CI gate на создание .sh файлов (риск: блокирует легитимные lib-правки, замедляет hotfix-cycle)
· Reason: Code review + AGENTS.md (настоящий раздел) + pre-commit hook на новые inline python3 = достаточная защита при текущем масштабе команды и velocity. CI gate добавит friction без пропорционального gains.
· Rev: если через квартал (2026-10-21) зафиксировано >3 нарушений языковой политики → поднять вопрос о CI gate (Whitelist .sh через core/entrypoint-manifest.yaml).

⚠️ TRAP[DECISION] · 2026-07-31 · HI · Enforcement-гейты с allowlist ПРИНЯТЫ (DevPlan 116 T9) — пересмотр TRAP 2026-07-21
· Rejected: pre-commit/pre-push hook как единственный enforcement (риск: «gate зелёный, система врёт» — хардкод-копии живут без CI-детекции)
· Reason: решения пользователя 2026-07-31 (D1, 01-Brief §1) — parity-гейты как pytest-гейты (trinity: файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest с repair-полями L1) + тонкие make-обёртки (check-profiles-parity, check-domain-parity). Гейты с allowlist: хардкод значений разрешён ТОЛЬКО в SoT (platform-infra.yaml) и generated-файлах (platform-env.yaml, .env.example); всё остальное — RED. Охват: COMPOSE_PROFILES (profiles_parity), PLATFORM_DOMAIN/test.local (domain_parity), *.template покрытие (template_manifest_coverage), копий-нет по бывшим callsites (compose_profiles_consistency).
· 2026-08-01 (B11, DevPlan 116): волна расширяет канон — cross-layer allowlist (dotted-импорты + python3 -m, 9 задокументированных записей — расширен до 9 (117: D19/D29/T52; 118: B8 добавил on_project_deploy), НЕ растёт), audit-format R2 (единый shared writer), glossary G4 (генерируемый глоссарий root AGENTS.md), debt-freshness (реестр долга: Status+Rev, stale >90 дней → RED).
· Rev: если parity-гейты начнут ложно-блокировать легитимные правки (friction > gain) → сузить allowlist или пересмотреть формат; пересмотр 2026-10-21 вместе с TRAP языковой политики.

⚠️ TRAP[DECISION] · 2026-07-21 · HI · SSH staging-gate для lib/ssh.sh — single point of failure для всех remote-операций
· Rejected: Прямой merge в main без staging-test (риск: единая точка отказа для deploy/bootstrap/healthcheck/node-update/converge/project-list/remove-project/verify)
· Reason: `core/lib/ssh.sh` (Wave 2 W2-E1) — единственный source of truth для всех remote-команд платформы. Любая бага в `ssh_exec/ssh_read` блокирует все remote-операции одновременно. Staging-test обязателен перед merge: `make converge NODE=<test> && make project-list NODE=<test> && make project-status NAME=<p> NODE=<test>` — все 3 проходят без hang. Feature-branch с explicit merge-commit для audit-trail.
· Revert-path: `git revert <merge-commit>` + `make bootstrap-node NODE=<prod>` (SCP/rsync доставит старую версию lib/).
· DRIFT-note: macOS dev-machine не имеет GNU `timeout` — локальная проверка `ssh_read` падает с `command not found`. CI/runner/VPS = Linux, где `timeout` доступен. Если нужно локальное тестирование — `brew install coreutils && gtimeout` alias.
· Rev: если CI-deploy стабильно < 300s → снизить deploy-default timeout с 600s до 400s (TRAP в lib/ssh.sh).

⚠️ TRAP[DECISION] · 2026-07-22 · HI · Decision Gate: Python-First strategy VALIDATED — continue Strangler-Fig
· Metrics: 4114→395 shell LOC (−90%), 0 inline python3 в топ-3, 7/10 K8s-parity converge, 204/210 tests pass
· Verdict: Все 15 Problem Matrix закрыты. Change-cost снижен >80%. Incident rate = 0.
· Recommendation: продолжить Strangler-Fig на legacy-скрипты, завершить test adaptation (042), docker optimization (040)
· Rev: 2026-10-22 — переоценка метрик после ≥2 недель на production-ноде

### Shell-исключения (keep-решения, DevPlan 119 D8 / AUDIT-1 F9, F11-F16)

Документированные исключения из миграции shell→Python (LOW/INFO классификация аудита 1).
Каждое имеет явный keep-аргумент и Rev-условие. Новый код — Python; перечисленное — НЕ мигрируется сейчас.

| Скрипт | LOC | Keep-причина | Rev-условие |
|--------|-----|--------------|-------------|
| `core/internal/bootstrap/issue-cert.sh` | 700 | acme.sh CLI interaction executor (DNS-01/HTTP-01, API-key shred протокол) — battle-tested shell; оркестрация/валидация уже в Python (cert_orchestrator + ssl_certs CLI, D1). DEFER-2 | после стабилизации acme.sh API ≥6 мес (2027-02) |
| `core/entrypoints/deploy.sh` | 175 | Переходный SSH forced-command entrypoint — канонический канал уже orchestrator_cli dispatch; удаление ломает legacy-ноды (authorized_keys). D7 | после верификации brief A на production |
| `core/lib/healthcheck.sh`, `logging.sh`, `paths.sh` | 251 / 116 / 46 | Стабильные shell-библиотеки — API стабилен, замена не даст прироста (языковая политика, явное исключение) | при смене API |
| `core/lib/ssh.sh` | 188 | Тонкий фасад над `python3 -m core.internal.shared.ssh_opts --shell` (116 B5 D1) | при втором shell-потребителе флагов |
| `core/lib/audit.sh` | 83 | Тонкий фасад над `shared.audit_logger` (единственный writer, 116 B11 T2) | при изменении схемы аудита |
| `core/lib/module-interface.sh` | 26 | Тонкий фасад над `python3 -m core.internal.shared.module_interface invoke` (119 D4) | при новом канале dispatch |
| `core/lib/yaml_read.sh` | 100 | Исторический YAML-reader; вытеснен `yaml_query.py` CLI — сохранён для обратной совместимости (0 активных source-потребителей после 119 D4) | при 0 ссылок в течение 90 дней → удалить |
| `core/lib/vps-readiness.sh` | 23 | Тонкий фасад над `python3 -m core.internal.shared.vps_readiness` (105) | — |

**Мигрированы (DevPlan 127, W1/W2 — больше НЕ исключения):**
- `core/internal/bootstrap/install-tor-proxy.sh` (было 321 LOC) → фасад 25 LOC + `core/internal/bootstrap/install_tor_proxy.py` (оркестрация, LDD IMP:9, идемпотентность).
- `core/lib/node-resolver.sh` (было 215 LOC) → фасад 99 LOC + `core/internal/shared/node_resolver.py` (резолв, CLI exit 0/1).

---

## Правило

**Не изобретай новый скрипт** — открой issue и предложи `make`-таргет. Если канонический таргет уже существует — используй его. Shell-скрипты в `core/entrypoints/` вызываются только через Makefile.

---

## Template Mechanisms

Платформа использует 3 механизма шаблонизации для разных доменов:

| Домен | Механизм | Модуль | Причина |
|-------|----------|--------|---------|
| nginx vhost конфиги | `{{UPPER_SNAKE}}` strict regex | `core/internal/template_engine.py` | Go/Prometheus-шаблоны (`{{$labels.x}}`, `{{instance}}`) НЕ должны подменяться. Strict regex чувствителен к регистру — только UPPER_SNAKE переменные. |
| LiteLLM config (model_list, fallbacks) | Jinja2 | `core/internal/llm/config_renderer.py` | Сложные структуры данных требуют циклов, условий и фильтров Jinja2. |
| Status-page HTML | Jinja2 | `core/modules/status-page/app.py` | Autoescape + template inheritance для XSS-защиты HTML. |
| Docker Compose | `${VAR:-default}` | compose engine | Встроенная функция compose. Не требует шаблонизации — compose резолвит env vars при запуске. |
| `envsubst` | `${VAR}` | systemd units, nginx main config | POSIX-совместимая подстановка env vars для файлов конфигурации. |

**Правило:** шаблонные файлы в одной директории ДОЛЖНЫ использовать один механизм консистентно. Смешивание Jinja2 и `{{UPPER_SNAKE}}` в одной директории — красный гейт.

## @rationale (3 механизма)
**Q:** Зачем 3 разных механизма шаблонизации?
**A:** Каждый механизм обслуживает фундаментально разный домен. Консолидация в один механизм либо потеряет функциональность (strict regex не может делать циклы), либо создаст ложные совпадения (Jinja2 матчит Go/Prometheus шаблоны). Цена ложных совпадений (silent config corruption) превышает цену поддержки 3 механизмов.

---

## Навигация

| Файл | Назначение | Статус |
|------|-----------|--------|
| [`AGENTS.md`](AGENTS.md) | Root architecture, invariants, deploy model, glossary | Канонический |
| [`core/AGENTS.md`](core/AGENTS.md) | Каталог операций, слои, forbidden-списки | Канонический |
| [`core/modules/AGENTS.md`](core/modules/AGENTS.md) | Шаблон модуля, healthcheck/Makefile-контракты | Канонический |
| [`core/internal/template_engine.py`](core/internal/template_engine.py) | Python-ядро template engine | Вспомогательный |
| [`core/templates/template-manifest.yaml`](core/templates/template-manifest.yaml) | Единый манифест шаблонов | Вспомогательный |
| [`core/internal/bootstrap/AGENTS.md`](core/internal/bootstrap/AGENTS.md) | Bootstrap pipeline, node lifecycle | Вспомогательный |
| [`docs/platform-project-contract.md`](docs/platform-project-contract.md) | Канон окружения проекта (AI-PLATFORM.md, DevPlan 133 D1) | Вспомогательный |
| [`docs/projects-root-AGENTS.md`](docs/projects-root-AGENTS.md) | Корневой контракт ~/projects/ (walk-up, symlink) | Вспомогательный |
| [`tests/gates/AGENTS.md`](tests/gates/AGENTS.md) | Gate test conventions, invariant testing | Вспомогательный |
| [`templates/template-backend/AGENTS.md`](templates/template-backend/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
| [`templates/template-frontend/AGENTS.md`](templates/template-frontend/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
| [`templates/template-fullstack/AGENTS.md`](templates/template-fullstack/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
