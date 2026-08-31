<!-- GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture -->

<!-- ai-instructions:0.7.0 -->
# GREP_SUMMARY: AGENTS.md, ai-platform, invariants, deploy-model, verb-glossary, architecture
# STRUCTURE: ┌make targets┐ → ◇ invariants (12 rules) → ◇ deploy-model (local→CI→context) → ⊕ verb glossary → ⎋ navigation
# region MODULE_CONTRACT
## @purpose  Root architecture documentation for ai-platform — defines invariants, deploy model, verb glossary
## @scope    Project-wide architectural rules, deployment model, canonical make targets, navigation
## @invariants
##   1. Makefile — единый фасад. Все операции через `make <target>`. entrypoints — internal-обёртки.
##   2. Модель деплоя: git push → CI. Для проекта: `make deploy` (git push → CI → forced-command). Для платформы: `make context-promote` (копирование в контекстную org → CI → деплой). Core-код доставляется CI-воркфлоу (rsync/scp с аудит-трейлом).
##   3. org = context. tronyx161 — исходный репозиторий. Каждый контекст — отдельная GitHub-организация.
##      context определяется из физического пути projects/<context>/<project>/; поле context в ai-platform.yaml не существует (org = context из физического пути).
##   4. AGENTS.md — 3 канонических файла (root, core/, core/modules/) + вспомогательные, перечисленные в §Навигация; файлы в templates/template-*/ — payload шаблонов new-project/new-context, вне скоупа инварианта.
##   5. core/entrypoint-manifest.yaml — YAML-реестр канонических операций для CI-gate'ов.
##   6. make bootstrap-node — строго идемпотентный. Второй вызов = no-op (INIT, не DEPLOY).
##   7. Полный локальный стек через `docker compose up` на macOS разработчика.
##   8. LiteLLM — PostgreSQL во всех окружениях (никакого SQLite).
##   9. Тестовый сервер может быть пересоздан заново — обратная совместимость не требуется.
##   10. Сборка образов hermes: единый L2-образ `hermes-agent-context` из multi-stage Dockerfile (`make hermes-build-context CONTEXT=<context>` — локально/CI; `make hermes-push-l2` — push в org контекста). L1-distribution base (hermes-agent-base), её версии/digest и `hermes-build-platform`/`hermes-push-l1` удалены (DevPlan 002: L1 схлопнут в L2, base-стадия единого Dockerfile).
##   11. Manifest Generation Contract — authoritative sources (module.yaml, secret-definitions.yaml, platform-infra.yaml, Makefile .PHONY, @pytest.mark.gate) порождают generated files (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py, entrypoint-manifest.yaml#allowed_verbs/gates, core/AGENTS.md generated-секции). Generated files коммитятся, но НЕ редактируются вручную. CI gate `make check MARKER=check-manifests` блокирует divergence.
##   12. docs-in-code — вся операционная документация только в коде: AGENTS.md-файлы
##       (канонические + вспомогательные), module.yaml-контракты, docstrings, GREP_SUMMARY/STRUCTURE.
##       Каталог docs/ запрещён. Исключения: .ai/plans/* (артефакты процессов), templates/template-*/README.md
##       (payload шаблонов; AGENTS.md проекта генерируется при scaffold). Enforcement: tests/gates/test_gate_docs_dir_forbidden.py (gate-тринити).
## @rationale Single source of truth for platform architecture consumed by autonomous agents and developers
## ⚠️ TRAP[DECISION] · — · SSH-флаги — единый Python SoT `core/internal/shared/ssh_opts.py`; lib/ssh.sh — тонкий фасад через `--shell`; гейт ssh_opts_sole_path enforce-ит · Rev: если появится второй shell-потребитель флагов — пересмотреть фасад
## ⚠️ TRAP[DECISION] · — · Единый канон healthcheck-критерия: контейнер running AND (healthy|""|none) = здоров, "unhealthy" → ждать (стартовые гонки); Python-реализация — только deploy/healthcheck_poller.py (static-детектор docker_sole_path), lib/healthcheck.sh — shell-фасад с тем же критерием · Rev: если появится состояние контейнера, требующее иного трактования — менять канон в одном месте
## ⚠️ TRAP[DECISION] · — · L1 (бывш. hermes-agent-base) схлопнут в L2 (DevPlan 002): единый multi-stage Dockerfile (base-стадия = бывш. L1 + final = context overlay + CONTEXT guard + USER 10000); L1-distribution не публикуется, контекстные org собирают L2 из source (gha-cache переиспользует base-слои) · Rev: если появится >1 org, тянущих L1 анонимно (цена анонимного pull станет критичной) — вернуть отдельную base-стадию с публикацией
## ⚠️ TRAP[DECISION] · — · Shell→Python миграция — только Strangler-Fig: бизнес-логика извлекается в Python-модуль с unit-тестами, shell остаётся тонким фасадом (<100-200 LOC), 0 inline python3 · Rev: если новый shell-скрипт достигает >500 LOC с inline python3 → применять Strangler-Fig немедленно
## ⚠️ TRAP[DECISION] · — · Bootstrap pipeline: deploy-context — канонический шаг (state machine φ8/φ12 → bootstrap/deploy/context_deployer.py); 1 нода = 1 контекст (CONTEXT из node.yaml contexts[].name или CLI --context) · Rev: если deploy-context шаг добавит >5 мин к bootstrap → сделать async (background job + telegram notify)
## ⚠️ TRAP[DECISION] · — · Проект контекста живёт ТОЛЬКО в ~/projects/<context>/<project>/ (путь резолвит context); имя kebab-case, глобально уникальное, БЕЗ префикса org; реестр platform/projects/*.yaml — L2-оверрайды мониторинга (реестр без папки проекта — ошибка, папка без реестра — норма) · Rev: при появлении multi-component проектов — рассмотреть суффиксы -web/-api/-bot
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
│  Сборка единого L2-образа → push ghcr.io → деплой   │
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

⚠️ TRAP[DECISION] · — · Dual delivery — не ослабление безопасности: core остаётся push-only SCP/rsync (zero git surface); git в context-overlay — это пользовательский код, который ВЫБИРАЕТ храниться в git · Rev: если context-overlay начнёт нести критичные секреты — пересмотреть модель

---

## Глоссарий глаголов

| Статус | Глагол | Операция |
|--------|--------|----------|
<!-- GENERATED:START:glossary -->
| ✅ | `adopt-project` | Адаптация существующего проекта |
| ✅ | `age-key-backup` | Off-node encrypted backup AGE мастер-ключа (DR, секция «DR мастер-ключа AGE» core/AGENTS.md) |
| ✅ | `agent-check` | L1-статический сигнал агента (DevPlan 163 W-E) |
| ✅ | `ai-instructions-sync` | Пересборка инструкций (канон + проектные дополнения) |
| ✅ | `backup` | Резервное копирование |
| ✅ | `bootstrap-node` | Идемпотентный bootstrap ноды |
| ✅ | `check` | Диагностика — все проверки из core/check-suite.yaml |
| ✅ | `check-diff` | Узкая диагностика по изменённым файлам |
| ✅ | `check-security` | Проверка security-постурa ноды |
| ✅ | `context-promote` | Промоут платформы в контекст |
| ✅ | `converge` | Реконсиляция ноды |
| ✅ | `core-deliver` | DR-канал локального оператора (тот же core_deliverer.py) |
| ✅ | `deploy` | Деплой проекта |
| ✅ | `deploy-context` | Деплой проектов контекста на ноде |
| ✅ | `deploy-project` | Прямой деплой минуя CI (DeployOrchestrator deliver) |
| ✅ | `dev-certs` | Генерация dev SSL-сертификатов |
| ✅ | `dev-hosts` | Управление /etc/hosts dev-блоком |
| ✅ | `dev-metrics` | Генерация dev status-metrics.json + htpasswd |
| ⚙️ | `discover-modules` (internal) | Авто-обнаружение модулей |
| ✅ | `down` | Остановка compose-стека |
| ✅ | `down-volumes` | Остановка compose-стека и удаление volumes |
| ✅ | `e2e-verify` | HTTP+TLS sweep-верификация всех endpoints ноды |
| ⚙️ | `fix-executable-bit` (internal) | Исправление executable bit на .sh файлах |
| ✅ | `fix-gate` | Композитное исправление gate-ошибок |
| ✅ | `fix-pycache` | Очистка __pycache__ рабочего дерева |
| ⚙️ | `fix-ruff` (internal) | Форматирование Python файлов через ruff |
| ✅ | `gate` | Production gate |
| ⚙️ | `generate-agents-md` (internal) | Генерация core/AGENTS.md |
| ⚙️ | `generate-entrypoint-manifest` (internal) | Генерация entrypoint-manifest.yaml |
| ⚙️ | `generate-env-example` (internal) | Генерация .env.example |
| ⚙️ | `generate-litellm-config` (internal) | Генерация litellm-config.yml |
| ✅ | `generate-manifests` | Генерация всех манифестов |
| ⚙️ | `generate-platform-env` (internal) | Генерация platform-env.yaml + Python env files |
| ⚙️ | `generate-requirements` (internal) | Генерация requirements.txt из pyproject.toml |
| ⚙️ | `generate-secrets-manifest` (internal) | Генерация secrets-manifest.yaml |
| ✅ | `healthcheck` | Проверка здоровья |
| ✅ | `hermes-build-context` | Сборка L2 образа |
| ✅ | `hermes-push-l2` | Push L2 в ghcr.io |
| ✅ | `load-test` | Запуск нагрузочного теста (locust-генератор + PromQL-отчёт + baseline) |
| ✅ | `new-context` | Создание контекста деплоя |
| ✅ | `new-project` | Создание проекта из шаблона |
| ✅ | `node-update` | Обновление provisioned ноды |
| ✅ | `parity-db` | Create/drop temporary parity database via privileged path (Plan 019 TASK-6, AC5) |
| ✅ | `project-check` | Проверка практик проекта (K1) |
| ✅ | `project-list` | Список проектов |
| ✅ | `project-set-practices` | Установка уровня практик (baseline|full|auto) |
| ✅ | `project-status` | Статус проекта |
| ✅ | `project-sync-env` | Синхронизация .env.platform и AI-PLATFORM.md |
| ✅ | `project-sync-practices` | Перегенерация GENERATED-файлов практик до канона |
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
| ⚙️ | `templates-check` (internal) | Проверка покрытия и разрешимости шаблонов |
| ⚙️ | `templates-render` (internal) | Рендер шаблонов |
| ✅ | `test-node` | E2E pipeline тесты на test-VPS |
| ✅ | `up` | Запуск compose-стека |
| ⚙️ | `validate-modules` (internal) | Валидация module.yaml |
| ✅ | `verify-domains` | HTTPS-верификация доменов |
<!-- GENERATED:END:glossary -->

**Правило:** таргет вне глоссария = запрещён (namelint блокирует любой незарегистрированный таргет; перечень запрещённых имён упразднён, правило категорийное). Одно имя таргета не может означать разное в разных Makefile. Все таргеты регистрируются в `core/entrypoint-manifest.yaml`.

**Правило создания проекта:** `make new-project` — единственный способ создания проекта. Ручное создание проектной директории не регистрирует проект в lifecycle и требует `make project-sync-env` для синхронизации .env.platform.

**Двухуровневая семантика:** root-глагол = оркестрация стека, module-глагол = операция одного модуля. Глаголы `up`, `down`, `restart`, `backup`, `restore` имеют разную реализацию на уровне root Makefile (весь стек) и в module.mk (один модуль).

## requires_node: ручной запуск

⚠️ TRAP[DECISION] · — · requires_node (E2E pipeline) остаётся РУЧНЫМ запуском `make test-node NODE=<name>` — авто-гейт создаст false-blocking (инфраструктурная недоступность ноды ≠ регрессия кода); ручной прогон — обязательный шаг release-checklist · Rev: при росте числа requires_node-тестов >40 ИЛИ первом production-инциденте этого класса → поднять requires_node до blocking-гейта (CI-workflow на test-VPS)

---

## Наследование практик

**Принцип: проект наследует ПОВЕДЕНИЕ (проверки исполняются платформенными каналами), а не код.**
В репозитории проекта — только тонкие GENERATED-файлы (рендер из канона
`core/internal/practices/practices_manifest.yaml`, DO NOT EDIT, repair: `make project-sync-practices`):
`pyproject.toml` (ruff/pytest-конфиг), `.pre-commit-config.yaml` (upstream-хуки + pre-push K5),
`tests/conftest.py` + `tests/test_health.py` (.env.platform + health-фикстура),
`practices.lock` (снапшот канона), `ai-platform.yaml#quality.level` (baseline|full|auto, default auto).

**Каналы исполнения:** K1 `make project-check/fix/sync-practices/set-practices` →
`core.internal.practices.*`; K2 inline quality-шаги deploy-project.yml (org-agnostic,
0 inline python3); K3 verify verb → `verify_contracts.py` на VPS (L1 всегда, L2/L3 по state
из practices.lock); K4 эскалатор зрелости `maturity.py`/`escalator.py` (baseline → proposed →
active-full, БЕЗ автопромоута); K5 pre-push хук `project-push-check` → `make project-check`.

**Классы L1/L2/L3:** L1 — безопасность платформы (секреты/порты/healthcheck/external-сети/
env-контракт/labels — блок при ЛЮБОМ уровне); L2 — контракт качества (compose config/build/
drift-lock; warning в baseline/proposed, блок в active-full); L3 — код-стандарты (ruff check/
pyright/eslint/LDD/grep-summary; warning, блок в active-full).

**Варнинги** единым форматом: `[PRACTICES:PROPOSE]` (предложение full, non-blocking),
`[PRACTICES:UNMANAGED]` (lock отсутствует — L1 блокирует деплой), `[PRACTICES:BLOCK]`,
`[PRACTICES:DRIFT-VERSION]`.

**Язык-ветвление** по `type` проекта: python → ruff/pytest, typescript/react → build/tsc/eslint,
sh → shellcheck, общий слой → gitleaks/hygiene/compose. Проверки исполняются платформенным
Python (`core/internal/practices/`), НЕ копируются в проект.

---

## Контракт окружения проекта

Канон для агентов/разработчиков в репозитории подключённого проекта (на него ссылается
`AI-PLATFORM.md` каждого проекта; GENERATED-секция `AI-PLATFORM.md` — актуальная фактура ноды).
Изменчивые данные (hosts/ports/DSN) — ТОЛЬКО в `.env.platform` проекта (GENERATED,
регенерация: `make sync-env`) и GENERATED-секции `AI-PLATFORM.md`. Канон: правки только
через PR в ai-platform, не в проектах.

### Что предоставляет платформа (provides из platform-env.yaml)

| Сервис | Фасад | Назначение |
|--------|-------|------------|
| `postgres` | `pgbouncer:6432` (`PLATFORM_POSTGRES_DSN`) | Единый PostgreSQL 18.4 + пулер соединений. Проект подключается своей ролью `${project}_user` к своей БД `needs.database` (роль/БД/GRANT создаются хук-ом postgres при деплое) |
| `redis` | `redis:6379` (`PLATFORM_REDIS_URL`) | Общий кэш/очереди |
| `nginx` | `nginx:443` (`PLATFORM_NGINX_URL`) | Ingress + TLS (единственная точка публикации) |
| `litellm` | `litellm:4000` (`PLATFORM_LITELLM_URL`) | LLM-прокси (единый ключ через платформу) |
| `langfuse` | `langfuse:3000` (`PLATFORM_LANGFUSE_URL`; host-facade 3001) | Трейсинг/наблюдаемость LLM |
| `minio` | `minio:9000` (`PLATFORM_MINIO_URL`) | S3-совместимое хранилище |
| `clickhouse` | `clickhouse:8123` (`PLATFORM_CLICKHOUSE_URL/DSN`) | Аналитика/логи |

Сети платформы: `proxy-net` (внешний ingress nginx ↔ проект), `shared-db-net`
(postgres/pgbouncer ↔ проекты), `shared-cache-net` (redis ↔ проекты), `hermes-agent-net`
(hermes-agent, litellm, langfuse, minio, clickhouse), `observability-net` (мониторинг/логирование).
Проект подключается к нужным сетям через свой `docker-compose.yml` (external сети).

Каналы доставки кода на ноду: **Core** — SCP/rsync (push: `core/`, `node-configs/`, `secrets/`);
**Context-overlay** — git clone/pull; **Project payload** — tar по SSH forced-command (`receive`):
docker-compose.yml, ai-platform.yaml, .env.platform, practices.lock — после `git push` проекта (CI).

### Команды

**Из папки проекта:** `make sync-env` (перегенерировать `.env.platform` + GENERATED-секцию
`AI-PLATFORM.md`) · `make project-check` (практики, K1, [PRACTICES:...] отчёт) ·
`make project-check --fix` (автофикс) · `make project-sync-practices` (repair дрейфа GENERATED) ·
`make project-set-practices LEVEL=<baseline|full|auto>` (full — ТОЛЬКО по явному согласию) ·
`make ai-sync` (пересборка `.kilo/` проекта из живого канона инструкций, DevPlan 001 T5.3) ·
`make status` (live-статус проекта на целевой ноде) · `make help` (все команды проекта).
Деплой = `git push` (main → production, staging → staging). Секреты в проекте настраивать не нужно.

**Из платформы (`ai-platform/`):** `make new-project` · `make adopt-project` ·
`make remove-project` · `make project-list` · `make project-status` · `make new-context` ·
`make context-promote` · `make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой, emergency) ·
`make ai-instructions-sync [PROJECT=<dir>] [TEMPLATE=all|backend|frontend] [CANON_PATH=<dir>]`
(пересборка инструкций платформы или проекта, SoT: `core/internal/ai-instructions/ai-instructions-pins.yaml`) ·
`make converge NODE=<node>`.

### Канонические env-имена (SoT: core/secret-definitions.yaml, platform-infra.yaml)

| Имя | SoT | Ловушка |
|-----|-----|---------|
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` | platform-infra.yaml (endpoint) / secret-definitions.yaml (keys) | НЕ `S3_ENDPOINT` |
| `AGE_SECRET_KEY` vs `AGE_SECRET_KEY_FILE` | core_deliverer.py | env ПЕРЕКРЫВАЕТ файл; принудительный файл — `unset AGE_SECRET_KEY` |
| `PLATFORM_MASTER_EMAIL/PASSWORD` | secret-definitions.yaml (autogen-конвенция) | tier=required/source=sops; override через SOPS |

Правило: имя env — из манифестов, не из памяти; неизвестное имя — `grep` по core/ перед использованием.

### Practices: уровни, эскалатор, practices.lock

Детали — в секции «Наследование практик» выше (каналы K1–K5, классы L1/L2/L3, варнинги
[PRACTICES:...], уровни BASELINE/FULL, эскалатор baseline→proposed→active-full БЕЗ
автопромоута). Ключевое дополнение: **practices.lock** — GENERATED-снапшот (version/level/state/
maturity/generator_hash/language + sha256 по GENERATED-файлам), коммитится в git проекта и
доставляется на VPS payload'ом receive (deploy-project.yml: `FILES += practices.lock`) — без него
K3 не имеет носителя state для L2/L3-блокировки. На VPS применяется готовый `state` из lock
(evaluate() не вызывается). Repair дрейфа: `make project-sync-practices`.

### DO NOT

1. **НЕ поднимай собственные** postgres/redis/прокси/TLS в проекте — это сервисы платформы.
2. **НЕ публикуй порты** в docker-compose проекта — ingress и TLS делает nginx-модуль платформы (сеть `proxy-net`, external).
3. **НЕ редактируй `.env.platform`** вручную — файл GENERATED; устарел → `make sync-env`.
4. **НЕ храни секреты/токены/ключи** в файлах проекта (в т.ч. в `.env`, коммитимом в git).
   Пароль роли БД проекта — только в `.platform-db.env` на ноде (0600, вне payload/git).
5. **НЕ удаляй** `AI-PLATFORM.md`, `Makefile`, `AGENTS.md` — контракты проекта с платформой.
6. **НЕ меняй** GENERATED-секцию `AI-PLATFORM.md` вручную — перезапишется при `make sync-env`.
7. **НЕ создавай** свои БД вне `needs.database` — роль/БД/GRANT выдаются хук-ом postgres.

### Приоритет инструкций (контракт окружения)

При конфликте (убывание приоритета): `AGENTS.md` проекта → `AI-PLATFORM.md` проекта →
настоящий канон (эта секция) → `ai-platform/AGENTS.md` (root) → `ai-platform/core/AGENTS.md`.

### Лимиты и границы ноды

- Память/CPU модулей платформы ограничены в `docker-compose.base.yml` каждого модуля
  (deploy.resources) — проект не может превысить лимиты общего стека.
- Проектный контейнер работает в сетях платформы (external) — без host-портов.
- Шаред-доступ к БД: роль `${project}_user` имеет `CONNECT` на свою БД и `CREATE, USAGE`
  на её схему `public` — и НИЧЕГО больше (изоляция от чужих БД).
- `.env.platform` проекта — единственный машиночитаемый источник hosts/ports/DSN/URL
  (`PLATFORM_*` переменные, GENERATED); GENERATED-секция `AI-PLATFORM.md` — человекочитаемая
  сводка той же фактуры (enabled-модули ноды, сервисы, сети, needs-статус).

---

## Multi-node размещение модулей (DevPlan 010)

Топология контекста описывается **только** в `node-configs/<context>/placement.yaml`.
Placement авторитетен: при наличии файла `node.yaml#modules` для деплоя не читается;
дрейф node.yaml ↔ placement — lint-WARNING с repair-подсказкой (не RED). Отсутствие
placement.yaml = single-node канон, поведение байт-идентично легаси.

**Закрытый словарь форм размещения:** singleton `{node: <name>}` · all-nodes
`{mode: all-nodes}` · nodes-list `{nodes: [a, b]}` (v1 — только nginx, multi-ingress) ·
off `{mode: "off"}` (⚠️ YAML-ловушка: bare `off` парсится как boolean false — кавычки
обязательны). Полнота записей обязательна: каждый модуль инвентаря имеет запись (включая off).

**Словарь терминов (Brief-009 T8):** «размещение/placement», «шаримый модуль (singleton)»,
«per-node модуль (all-nodes)», «нода-пир», «критичная нода / канарейка».

**Порядок бутстрапа контекста:** data → agent → apps/obs (данные раньше потребителей).
Повторный bootstrap идемпотентен; проверка готовности — per-node `make status NODE=<n>`
(глагол context-status отложен до >4 нод). Распределённый оркестратор не строится (v1).

**VPN-prerequisite + аттестация:** `nodes[].host` — только приватные адреса (RFC1918,
100.64/10); публичный IP → ConfigValidationError. Multi-node контекст обязан нести
`vpn_enforced: true` — оператор подтверждает шифрованный канал (RFC1918 ≠ крипто; платформа
VPN не строит: sslmode=disable, redis/minio без TLS — шифрует VPN-канал).

**Security-префикс:** ни один кросс-нодовый порт (6432 pgbouncer, 6379 redis, 9000 minio,
8123 clickhouse HTTP, 19000 CH native peer, 3100 loki push, 9100+8080 node-metrics,
9187/9121 service-exporters, 9113 nginx-exporter [модуль nginx — DR-H2 fix: exporter co-located
со скрейпимым nginx], фасады LLM-стека 4000 litellm / 3001 langfuse / 9119 hermes-dashboard) не
публикуется без выполненного префикса (redis requirepass, Loki tenant X-Scope-OrgID,
pg_hba scram-sha-256 — реализовано, T2.0.*). ufw peer-ALLOW — только
для IP нод-пиров (`allow from <peer>`, вставка ДО module-deny); Anywhere на этих портах = FAIL;
прямой 5432 НЕ публикуется (потребители переезжают на data-ноду вместе с postgres).

**Multi-ingress + DNS-steering prerequisite:** nginx `{nodes:[...]}` — экземпляр на каждой
ноде; exposed-проект обязан иметь `target_node` из списка; каждому FQDN — своя A-запись на IP
своей ноды (платформа DNS не управляет — шаг оператора перед первым exposed-проектом на
второй ноде). TLS wildcard DNS-01 выдаётся на любой ноде независимо (S3-cache restore).

**Граница бэкапа честная:** backup-cron покрывает ТОЛЬКО postgres (pg_dumpall+WAL → внешний
S3); project/minio/clickhouse/loki volumes не бэкапятся и в single-node. Multi-node экспозицию
не ухудшает — фиксирует существующую (Rev: первый stateful-проект → phase-07 app-data).

**SPOF-honesty (DR-L2 fix):** multi-node ≠ HA. Размещение модулей по нодам устраняет
ресурсную конкуренцию и даёт blast-radius изоляцию (ingress/data/agent), НО каждая нода
остаётся единой точкой отказа своего стека: failover отсутствует, автоматического
переноса ролей нет — восстановление = re-bootstrap ноды + redeploy (RTO часы). RPO не
улучшается multi-node'ом: бэкапы по-прежнему nightly-дампы postgres (RPO 24ч).
Метрики/RTO/RPO и fix-forward политика — core/AGENTS.md §«Безопасность данных».

Полный план: `.ai/plans/010-multi-node-module-placement/01-DevPlan.md`; сценарии S2 «данные
отдельно» / S2b «критичные-канарейки» / S3 «data-agent-apps» — §8 плана.

---

## Корневой контракт ~/projects/

Общий контракт платформы для агентов, работающих в `~/projects/`: агент находит контракт
walk-up по дереву каталогов (до `~/projects/AGENTS.md` — symlink на канон, автообновление
git pull; первым при подъёме находится AGENTS.md ближайшего уровня — специфика проекта
перекрывает общий контракт). Изменчивые данные (сервисы/hosts/порты) НЕ дублируются —
они в `.env.platform` каждого проекта.

| Путь | Что это |
|------|---------|
| `ai-platform/` | Платформа (source-репозиторий). Полные правила: `ai-platform/AGENTS.md` |
| `<context>/<project>/` | Подключённый проект. **org = контекст** (отдельная GitHub-организация) |
| `<context>/` | Служебная папка контекста (node-configs, hermes-agent) — создаётся `make new-context` |

**Размещение и имена проектов (строгий канон):** проект живёт ТОЛЬКО в
`~/projects/<context>/<project>/` — путь определяет контекст (инвариант 3); имя kebab-case,
глобально уникальное, без префикса org; корневые клоны `~/projects/<project>/` запрещены;
номера волн/задач и суффиксы `-be/-fe` в именах запрещены; реестр `platform/projects/*.yaml` —
L2-оверрайды мониторинга (реестр без папки — ошибка, папка без реестра — норма);
домен — авто `<project>.tronyx.ru` (wildcard) или личный (`ai-platform.yaml#needs.domain`);
`make new-project` — единственный канал подключения. Multi-component (future): суффиксы
`-web`/`-api`/`-bot`. Исключения (вне lifecycle `~/projects/`, реестр не ведётся): `ai-instructions`
и `ai-project` — отдельные инструменты платформы.

### Контракт для работы в папке проекта

1. **Платформа уже предоставляет сервисы** — узнай списком: `grep PLATFORM_ .env.platform`.
   Postgres — через `PLATFORM_POSTGRES_DSN` (façade `pgbouncer:6432`), redis/litellm/
   langfuse/minio/clickhouse — через `PLATFORM_*_URL`.
2. **`AI-PLATFORM.md` в корне проекта** — контракт проекта с платформой: статичные рамки +
   GENERATED-секция окружения ноды (enabled-модули, сервисы, сети, needs). Регенерация
   `make sync-env`; ручные правки — только вне GENERATED-секции.
3. **НЕ устанавливай** postgres, redis, прокси или свой TLS в проект — это сервисы платформы.
4. **НЕ публикуй порты** в docker-compose — ingress и TLS делает nginx-модуль платформы.
5. **`.env.platform` — GENERATED, не редактировать.** Устарел → `make sync-env` из папки проекта.

### Команды

**Из папки проекта:** `make sync-env` · `make status` (live-статус на ноде).
Деплой = `git push` (main → production, staging → staging). Секреты настраивать не нужно.

**Из `ai-platform/`:** `make new-project NAME=<n> TEMPLATE=<t> [DOMAIN=<d>]` ·
`make adopt-project PROJECT_DIR=<dir> [DOMAIN=<d>]` · `make remove-project PROJECT=<n> NODE=<node>`
(данные/volumes/репо не удаляются) · `make project-list` · `make project-status PROJECT=<n>` ·
`make new-context NODE=<n>` · `make context-promote CONTEXT=<ctx>` ·
`make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой, emergency).

### Deploy-модель (кратко)

```
git push → CI проекта (≤15 строк) → reusable workflow из <org>/ai-platform@main
        → build ghcr.io → SSH forced-command → атомарный деплой на VPS + healthcheck rollback

make deploy-project → tar+ssh (orchestrator_cli deliver → forced-command receive) → VPS
        (прямой путь, emergency, аудит DEPLOY-DIRECT)
```

Обновление платформенного CI не требует правок проектов (workflow подтягивается `@main`;
в org — зеркало `<org>/ai-platform`, обновляется `make context-promote`).

**Fix-forward политика rollback (C5, security hardening):** healthcheck-rollback откатывает
только образ; миграции БД нового кода НЕ откатываются — старый код против новой схемы
недопустим. Rollback = fix-forward (новый коммит), страховка — nightly-дампы (RPO 24ч)
и pre-restore снэпшот. Детали RTO/RPO — `core/AGENTS.md` §«Безопасность данных».

### Приоритет инструкций (~/projects/)

При конфликте: `AGENTS.md` проекта → этот контракт (~/projects/) → `ai-platform/AGENTS.md`.

---

## Release checklist

ОБЯЗАТЕЛЬНО перед деплоем в production (`make deploy` / `make context-promote`).
`requires_node` — ручной прогон (последняя защита идемпотентности/restore между CI-гейтами
и production; Rev: >40 requires_node-тестов ИЛИ первый production-инцидент → blocking CI-гейт).

1. **E2E на test-VPS:** `make test-node NODE=<test>` зелёный (0 failed; skip — только при
   документированной инфраструктурной недоступности) → нода согласована (`make check NODE=<test>`).
2. **Resilience drills:** fast (`-m "chaos and not night"`, ≤30 мин) + night (`-m night`, отдельное окно ~25 мин) — после bootstrap.
3. **CI-гейты:** `make check` локально зелёный; CI на целевой ветке (platform-test + security-scan
   включая gitleaks) зелёный; `make check MARKER=check-manifests` чистый.
4. **Деплой:** `make deploy` (проект) / `make context-promote CONTEXT=<context>` (платформа);
   пост-деплой: `make e2e-verify NODE=<prod>` или `make healthcheck NODE=<prod>`.
5. **Off-site DR активен:** `AGE_RECIPIENT` непуст в env backup-cron на prod (sops-матрица
   ноды; QA C6, DevPlan 14 T1.5); последние nightly uploads без `BackupUploadFailure`
   (пусто → fail-closed SKIP = RPO 24ч фиктивен).
6. **После:** мониторинг без новых ошибок; release-заметка при необходимости.

---

## DevOps-политика (канон supply-chain/updates)

| Аспект | Канон |
|--------|-------|
| Digest-pin | Все образы — tag@sha256 (compose base.yml + Dockerfile FROM + env_defaults). Гейты: tests/test_compose_contract.py + tests/gates/test_gate_image_tag_form.py. Обновление digest'а — `docker buildx imagetools inspect <ref>` → правка SoT. |
| SBOM + provenance | docker-build-cache action: `release: true` → push с provenance:true + sbom:true (CycloneDX + SLSA); обычные сборки — load:true без attestations. |
| CVE-scan | security-scan.yml: trivy fs + pip-audit на push/PR в main; HIGH/CRITICAL → блок. Супрессия — ТОЛЬКО через `.trivyignore` (файл создаётся с первой записью; запись = CVE-ID + причина + Rev-дата). |
| Автообновление зависимостей | `.github/dependabot.yml` (weekly: GHA-actions + pip через core/requirements.txt, лимит 5 открытых PR); renovate.json (docker pinDigests) — для контекстных org-ов. dependabot-ветки НЕ удалять — merge или close с причиной. |
| Таймауты/пути/порты | SoT: shared/timeouts.py, shared/deploy_paths.py, shared/platform_ports.py + parity-гейты. Литералы вне SoT → RED. |
| Versioned-теги образов | tag-policy U-60: релизные образы публикуются :latest + :v\<pyproject-version\> (hermes-push-l2; контекстные образы — при каждом контекстном релизе). |

---

## Языковая политика

**Главное правило:** новый код платформы — только Python. Bash — исключительно тонкая
обёртка над Python-модулями и системными утилитами (`rsync`, `scp`, `ssh`, `docker`, `systemctl`).

1. **Новый код = Python.** Подсистема, бизнес-логика, валидатор, парсер — в
   `core/internal/scripts/` или `core/internal/<domain>/`; shell-обёртка в `core/entrypoints/`
   только вызывает `python3 script.py` и пробрасывает exit code.
2. **Bash остаётся для:** entrypoints (тонкие фасады), чистой оркестрации (subprocess-цепочки
   без логики), стабильных lib-библиотек (`logging.sh`, `paths.sh`, `ssh.sh`).
3. **Inline Python/heredoc в shell — сигнал к извлечению** в отдельный `.py` (никаких исключений
   для нового кода).
4. **Strangler-триггер:** Tier 1 (немедленно, при ЛЮБОМ изменении скрипта) — новый `python3 -c`
   /heredoc или >3 новых `if`-веток бизнес-логики → извлечь эту логику в Python. Tier 2 (при
   накоплении) — ≥3 Tier-1 экстракций в одном файле или баг-фикс >2 ответственностей → плановая
   декомпозиция подсистемы (shell-фасад <150 LOC); баг-фикс >2 ответственностей → Debt-артефакт
   (`{NN}-Debt.md` в папке плана — канон артефактов §ARTIFACT_REGISTRY). Tier 1 НЕ требует
   переписывать весь скрипт.

⚠️ TRAP[DECISION] · — · Enforcement-гейты с allowlist: хардкод значений — ТОЛЬКО в SoT (module.yaml, secret-definitions.yaml, platform-infra.yaml, Makefile .PHONY, @pytest.mark.gate) и generated-файлах; parity-гейты — pytest-тринити (tests/gates/ + @pytest.mark.gate + entrypoint-manifest с repair-полями L1) + тонкие make-обёртки; всё остальное — RED · Rev: если parity-гейты начнут ложно-блокировать легитимные правки (friction > gain) → сузить allowlist или пересмотреть формат

⚠️ TRAP[DECISION] · — · lib/ssh.sh — единственный source of truth для всех remote-команд платформы; merge в main требует staging-test на test-VPS (`make converge` + `make project-list` + `make project-status` — все 3 без hang) · Rev: если CI-deploy стабильно < 300s → снизить deploy-default timeout с 600s до 400s

### Shell-исключения (мета-правило)

Перечень keep-решений фасадов не ведётся — правило категорийное: shell допустим ТОЛЬКО
как тонкий фасад (<150 LOC, 0 inline python3) над Python-модулем; стабильные shell-библиотеки
(`lib/logging.sh`, `lib/paths.sh`, `lib/healthcheck.sh`, `lib/module-interface.sh`, `lib/args.sh`,
`lib/secrets.sh`) НЕ мигрируются — API стабилен. Фактические фасады: `lib/docker.sh` → docker_ops
`--shell`, `lib/ssh.sh` → ssh_opts `--shell`, `lib/audit.sh` → audit_logger, `lib/node-resolver.sh`
→ node_resolver.py, `core/internal/bootstrap/build-ssh-cmd.sh`/`remote-cmd.sh` →
ssh_cmd_builder/remote_executor, `core/internal/healthcheck/platform-export-metrics.sh` →
platform_export_metrics.py, entrypoints/*.sh → `python3 -m core.internal.*` (dispatch-резолв в Python).
Мигрированы (прецеденты): install-tor-proxy.sh, node-resolver.sh, hermes-images.sh, validate.sh,
decrypt-secrets.sh, modules-healthcheck.sh, verify-domains.sh; entrypoints
deploy-context/check-file-lines/add-vhost/check-security — логика → Python CLI.

**Keep-решения:** bootstrap.sh (SCP/SSH-оркестрация + age-chain — легитимная shell-оркестрация;
Rev: остаточный парсинг вне bootstrap_resolver), pre-push-gate.sh (git-hook glue), scaffold.sh
(subcommand-роутер), core-deliver.sh (эталон тонкого фасада), node-lifecycle.sh
(remote-диспетчер), модульные healthcheck.sh в core/modules/*/ (Docker-контракт,
source lib/healthcheck.sh), enforcement-хуки (check-no-new-inline-python3.sh).
Гейты: test_entrypoint_no_direct_binary_calls, docker_sole_path, test_gate_thin_wrapper.

---

## Правило

**Не изобретай новый скрипт** — открой issue и предложи `make`-таргет. Если канонический таргет уже существует — используй его. Shell-скрипты в `core/entrypoints/` вызываются только через Makefile.

## Канон агента

Единственная тестовая команда агента — **`make check`** (все проверки из `core/check-suite.yaml`):
диагностика, фикс-цикл и верификация — только через неё (`make check TEST_FILE=...` — один файл,
`make check MARKER=<suite>` — один сьют, `make check-diff` — diff-скоуп). Фикс gate-блокирующих
ошибок — `make fix-gate` + `make check`. Публичные глаголы — через `make help` (сценарии) /
`make help-all` (полный реестр с internal-пометками), НЕ по памяти. Полный `make gate MODE=fast`
агент НЕ запускает — арбитр pre-push hook (quick check) + CI push-gate.yml (OOM-политика 0.8).

## Обязательный шаг агента: `make agent-check`

Перед объявлением готовности (завершение задачи/волны) агент ОБЯЗАН прогнать
`make agent-check` — L1-статический сигнал <5 s на типовом изменении (ruff + advisory
SLF/FBT/ARG/C90 + basedpyright + `static check --changed` + bespoke doc-headers).
Чистота = exit 0; нарушения = exit 1 + JSON `{rule, file, line, message, fixable}`.
FP-журнал: `core/internal/agent_check/fp_registry.yaml` (правило с >10 FP/нед не включается
в select — еженедельное ревью, связка с `files/ruff_policy.md`).

---

## Template Mechanisms

Три механизма шаблонизации по доменам (смешивание в одной директории = красный гейт):

| Домен | Механизм | Модуль | Причина |
|-------|----------|--------|---------|
| nginx vhost конфиги | `{{UPPER_SNAKE}}` strict regex | `core/internal/template_engine.py` | Go/Prometheus-шаблоны (`{{$labels.x}}`) НЕ должны подменяться — regex чувствителен к регистру |
| LiteLLM config | Jinja2 | `core/internal/llm/config_renderer.py` | Циклы/условия/фильтры |
| Status-page HTML | Jinja2 | `core/modules/status-page/app.py` | Autoescape + inheritance для XSS-защиты |
| Docker Compose | `${VAR:-default}` | compose engine | Встроенная функция compose |
| `envsubst` | `${VAR}` | systemd units, nginx main config | POSIX-совместимая подстановка |

**@rationale:** консолидация в один механизм потеряет функциональность (strict regex не может
делать циклы) или создаст ложные совпадения (Jinja2 матчит Go/Prometheus шаблоны); цена ложных
совпадений (silent config corruption) превышает цену поддержки 3 механизмов.

---

## Навигация

| Файл | Назначение | Статус |
|------|-----------|--------|
| [`AGENTS.md`](AGENTS.md) | Root architecture, invariants, deploy model, glossary | Канонический |
| [`core/AGENTS.md`](core/AGENTS.md) | Каталог операций, слои | Канонический |
| [`core/modules/AGENTS.md`](core/modules/AGENTS.md) | Шаблон модуля, healthcheck/Makefile-контракты | Канонический |
| [`core/internal/template_engine.py`](core/internal/template_engine.py) | Python-ядро template engine | Вспомогательный |
| [`core/templates/template-manifest.yaml`](core/templates/template-manifest.yaml) | Единый манифест шаблонов | Вспомогательный |
| [`core/internal/bootstrap/AGENTS.md`](core/internal/bootstrap/AGENTS.md) | Bootstrap pipeline, node lifecycle | Вспомогательный |
| [`core/internal/shared/AGENTS.md`](core/internal/shared/AGENTS.md) | Инвентарь shared-модулей, критерии размещения | Вспомогательный |
| [`core/modules/nginx/AGENTS.md`](core/modules/nginx/AGENTS.md) | Контракт nginx-модуля | Вспомогательный |
| [`core/internal/ai-instructions/ai-instructions-pins.yaml`](core/internal/ai-instructions/ai-instructions-pins.yaml) | SoT-пин канона ai-instructions (tag/digest, hermes-профиль) — DevPlan 001 R11 | Вспомогательный |
| [`ai-instructions.lock`](ai-instructions.lock) | Lock-манифест сгенерированных инструкций (drift-детект, `ai-instructions check`) | Generated |
| `.ai/rules/`, `.ai/roles/` | Проектные источники инструкций платформы (компилируются в `.kilo/`) | Канонический |
| `AGENTS.md` → «Контракт окружения проекта» | Канон окружения проекта (AI-PLATFORM.md) | Вспомогательный |
| `AGENTS.md` → «Корневой контракт ~/projects/» | Корневой контракт ~/projects/ (walk-up, symlink) | Вспомогательный |
| [`tests/gates/AGENTS.md`](tests/gates/AGENTS.md) | Gate test conventions, invariant testing | Вспомогательный |
| [`templates/template-backend/README.md`](templates/template-backend/README.md) | Payload шаблона new-project (backend) | Вне скоупа инварианта |
| [`templates/template-frontend/README.md`](templates/template-frontend/README.md) | Payload шаблона new-project (frontend) | Вне скоупа инварианта |
