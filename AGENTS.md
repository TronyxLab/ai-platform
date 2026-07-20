<!-- GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture -->

# GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture
# STRUCTURE: ┌make targets┐ → ◇ invariants (10 rules) → ◇ deploy-model (local→CI→context) → ⊕ verb glossary → ⎋ navigation
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
##   10. Сборка образов hermes: `make hermes-build-platform` (L1, локально + push в ghcr.io как backup), `make hermes-push-l1` (L1 push в ghcr.io как disaster recovery) и `make hermes-build-context CONTEXT=<context>` (L1→L2, контекстная разработка и production).
## @rationale Single source of truth for platform architecture consumed by autonomous agents and developers
## @rationale (D2) Invariant 4 обновлён по результатам drift-аудита: 3 канонических + 2 вспомогательных (core/internal/bootstrap/, tests/gates/) в §Навигация root AGENTS.md; templates/template-*/ — payload `make new-project`/`make new-context`, вне скоупа инварианта (не являются архитектурной документацией платформы)
## ⚠️ TRAP[DECISION] · 2026-07-15 · HI · L1 pushed to ghcr.io as backup, never used directly by contexts
## · Rejected: local-only L1 (risk: loss of build machine → rebuild from scratch)
## · Reason: L1 contains no secrets (only Python dependencies). Push = disaster recovery, not delivery model change.
## · Rev: if L1 starts carrying context-specific data → revert to local-only.
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
                    │   deploy-modules.sh → ensure_context_repo
                    │   git clone/pull → /opt/<context>/platform/
```

### Три канала доставки кода на VPS

| Канал | Механизм | Направление | Применение |
|-------|----------|-------------|------------|
| **Core** | SCP/rsync | Push (с машины оператора/CI) | `core/`, `node-configs/`, `secrets/` — инфраструктурный код платформы |
| **Context-overlay** | git clone/pull | Pull (с VPS в репозиторий) | Контекстные overlay, модульные конфигурации, кастомизации |
| **Project payload** | tar по SSH forced-command (`platform-deliver`) | Push (CI) | docker-compose.yml, ai-platform.yaml, .env.platform |

### Инварианты

1. **Core-код NEVER доставляется через git** на VPS — только SCP/rsync. Никаких git-токенов, deploy-keys или repo-URL для core на сервере.
2. **Context-overlay использует git** для клонирования/пула контекстного репозитория — это overlay-кастомизация поверх core.
3. **`ensure_context_repo()`** в `deploy-modules.sh` — единственное место, где git выполняется на VPS.
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
| ✅ | `deploy` | Деплой проекта (git push → CI → forced-command) |
| ✅ | `bootstrap-node` | Идемпотентный bootstrap ноды (LIFE CYCLE/INIT) |
| ✅ | `dev-certs` | Генерация dev SSL-сертификатов (make dev-certs → generate-dev-certs.sh) |
| ✅ | `context-promote` | Промоут платформы в контекстную org |
| ✅ | `discover-modules` | Авто-обнаружение модулей и обновление docker-compose.yml include-секции (make discover-modules → discover_modules.py) |
| ✅ | `hermes-build-platform` | Сборка L1 локально |
| ✅ | `hermes-build-context` | Сборка L1→L2 |
| ✅ | `hermes-push-l1` | Push L1 в ghcr.io как disaster recovery backup (make hermes-push-l1 → docker tag + docker push) |
| ✅ | `provision` | Provision окружения (сети, volumes, CI env) из platform-env.yaml (`make provision [SCOPE=all|networks|volumes|env]`) |
| ✅ | `templates-check` | Dry-run проверка разрешимости шаблонов |
| ✅ | `templates-render` | Рендер шаблонов по манифесту |
| ✅ | `validate` / `lint` / `audit` / `check-file-lines` / `verify` | Проверки и аудит |
| ✅ | `secrets-unlock` | Расшифровка SOPS/age секретов |
| ✅ | `test` | Тестирование (`make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all]`) |
| ✅ | `test-inventory-sync` | Регенерация test_inventory.yaml из pytest --collect-only (make test-inventory-sync → tests/tools/sync_inventory.py) |
| ✅ | `gate` | Production gate (`make gate [MODE=fast|full]`) |
| ✅ | `new-project` / `new-context` | Создание из шаблона |
| ✅ | `project-sync-env` | Синхронизация .env.platform из platform-env.yaml (make project-sync-env → scaffold.sh sync-env) |
| ✅ | `remove-project` | Безопасное удаление проекта из lifecycle (unregister + compose down без -v) |
| ✅ | `adopt-project` | Адаптация существующего проекта в lifecycle платформы (make adopt-project DIR=&lt;dir&gt;) |
| ✅ | `project-list` / `project-status` | Список проектов (offline) и live-статус на ноде |
| ✅ | `backup` / `restore` | Резервное копирование. Root = оркестрация стека, module = один модуль |
| ✅ | `healthcheck` | Проверка здоровья |
| ✅ | `node-update` | Регулярный update ноды (make node-update → provision + deploy-modules + healthcheck) |
| ✅ | `converge` | Idempotent reconcile — конвергирует ноду с desired state из node.yaml (make converge NODE=&lt;name&gt;) |
| ✅ | `render-vhosts` | Генерация nginx vhost конфигов из node.yaml (make render-vhosts NODE=&lt;name&gt;) |
| ⏳ | `project-sync-secrets` | DISABLED — раскатка repo-secrets, требует sync-repo-secrets.sh (T3.6 conditional) |
| ✅ | `up` | Root = оркестрация стека, module = один модуль (compose up) |
| ✅ | `down` | Root = оркестрация стека, module = алиас `stop` (discoverability) |
| ✅ | `restart` | Soft restart (stop + start). Root = оркестрация стека, module = один модуль |
| ✅ | `restart-hard` | Hard restart c `--force-recreate` (module-level target only — нет root Makefile target) |
| ✅ | `status` | Локальный compose-lifecycle |
| ❌ | `push-core`, `deploy-node`, `build-local`, `bootstrap-core`, `hermes-deploy-vps` | Запрещены — не из словаря |

**Правило:** одно имя таргета не может означать разное в разных Makefile. Все таргеты регистрируются в `core/entrypoint-manifest.yaml`.

**Правило создания проекта:** `make new-project` — единственный способ создания проекта. Ручное создание проектной директории не регистрирует проект в lifecycle и требует `make project-sync-env` для синхронизации .env.platform.

**Двухуровневая семантика:** root-глагол = оркестрация стека, module-глагол = операция одного модуля. Глаголы `up`, `down`, `restart`, `backup`, `restore` имеют разную реализацию на уровне root Makefile (весь стек) и в module.mk (один модуль).

---

## Правило

**Не изобретай новый скрипт** — открой issue и предложи `make`-таргет. Если канонический таргет уже существует — используй его. Shell-скрипты в `core/entrypoints/` вызываются только через Makefile.

---

## Навигация

| Файл | Назначение | Статус |
|------|-----------|--------|
| [`AGENTS.md`](AGENTS.md) | Root architecture, invariants, deploy model, glossary | Канонический |
| [`core/AGENTS.md`](core/AGENTS.md) | Каталог операций, слои, forbidden-списки | Канонический |
| [`core/modules/AGENTS.md`](core/modules/AGENTS.md) | Шаблон модуля, healthcheck/Makefile-контракты | Канонический |
| [`core/internal/template_engine.py`](core/internal/template_engine.py) | Python-ядро template engine | Канонический |
| [`core/templates/template-manifest.yaml`](core/templates/template-manifest.yaml) | Единый манифест шаблонов | Канонический |
| [`core/internal/bootstrap/AGENTS.md`](core/internal/bootstrap/AGENTS.md) | Bootstrap pipeline, node lifecycle | Вспомогательный |
| [`tests/gates/AGENTS.md`](tests/gates/AGENTS.md) | Gate test conventions, invariant testing | Вспомогательный |
| [`templates/template-backend/AGENTS.md`](templates/template-backend/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
| [`templates/template-frontend/AGENTS.md`](templates/template-frontend/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
| [`templates/template-fullstack/AGENTS.md`](templates/template-fullstack/AGENTS.md) | Payload шаблона new-project | Вне скоупа инварианта |
