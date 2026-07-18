$START_DEVPLAN
# $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Единая модель работы с подключёнными проектами: подключение (connection-модель из 04-rev) + полный жизненный цикл (create → adopt → list/status → remove) + контекст для AI-агентов в каждом проекте. Superсedes 02-DevPlan.md и 04-DevPlan-rev.md. |
| **DESCRIPTION** | Ядро — connection-модель 04-rev (org-variable `NODE_HOST_MAP`, reusable workflow, `.env.platform`, авто-домены), наложенная на реальную архитектуру и дополненная по Architecture Forensics: lifecycle завершён операцией `remove-project` (безопасной), добавлены `project-adopt` (миграция существующих проектов, включая личный домен dance-site), `project-list`/`project-status`, проектные `Makefile` + `AGENTS.md`. 21 задача, 6 волн, ~44 файла. |
| **RATIONALE** | Forensics-вердикт NEEDS_ATTENTION: lifecycle реализован на 2/3 (CREATE+DEPLOY без REMOVE), node.yaml append-only, контракт хуков `on_project_remove` проверяется gate-тестом, но не вызывается в runtime. Работа с подключёнными проектами — 80% рабочего времени владельца: операции должны запускаться из папки проекта, а AI-агент должен получать контекст платформы без объяснений. |
| **ACCEPTANCE_CRITERIA** | AC1–AC13 (см. §Acceptance Criteria): one-command create/adopt/remove, zero project secrets, `.env.platform` ≥8 переменных с pgbouncer:6432, авто-/личные домены, `make sync-env`/`status` из папки проекта, проектный AGENTS.md, все gate-тесты зелёные. |
| **IMPLEMENTS** | 01-Brief.md C1–C3, D1–D4 (приоритет — ответы владельца); DD1–DD8 из 04-rev; Architecture Forensics: V1–V6, hidden deps (append-only node.yaml, on_project_remove, VPS cleanup) |
| **IMPACTS** | `.github/workflows/deploy-project.yml` (NEW), `.github/actions/resolve-node/` (DELETE), `platform-env.yaml`, `core/internal/scaffold/{gen-env-platform,add-project,add-vhost,remove-project,adopt-project,project-list}.sh`, `core/entrypoints/{scaffold,deploy}.sh`, `core/internal/deploy/deploy-project.sh`, `core/lib/node-resolver.sh`, `Makefile`, `core/entrypoint-manifest.yaml`, `core/schemas/ai-platform.schema.json`, `core/AGENTS.md`, root `AGENTS.md`, `templates/template-{frontend,backend,fullstack}/` (×8 файлов каждый), `tests/` ×3 NEW |
| **REQUIRES** | В КАЖДОЙ контекстной org: `NODE_HOST_MAP` (Actions variable, JSON `{"tronyx-vps":"<host>"}`) + `CI_DEPLOY_KEY` (org secret). `PLATFORM_DOMAIN`, `PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE` в `.env`. Зеркало `<org>/ai-platform` создаётся `make context-promote`. |

$END_ARTIFACT_CONTRACT

---

## Requirements Analysis — ключевые критерии успеха

1. **Один вход — одна команда.** create / adopt / remove / list / sync-env — каждая операция = один make-таргет, без ручного YAML-редактирования.
2. **Zero project secrets.** Проект использует только авто-`GITHUB_TOKEN` + унаследованные org-level `NODE_HOST_MAP`/`CI_DEPLOY_KEY`.
3. **Lifecycle полон.** CREATE → REGISTER → DEPLOY → **REMOVE** — каждая фаза канонична (Makefile → entrypoint → internal), node.yaml перестаёт быть append-only.
4. **AI-агент самодостаточен в папке проекта.** `AGENTS.md` + `grep PLATFORM_ .env.platform` дают полный контекст без объяснений при переходе между проектами.
5. **Обновление платформы не трогает проекты.** Reusable workflow `@main` + регенерация `.env.platform` = единственные точки синхронизации.

### Решения владельца (приоритет, не пересматривать)

| # | Решение | Источник |
|---|---------|----------|
| O1 | Zero-secret: org-variable `NODE_HOST_MAP`, никаких PAT | Brief C1 + 04-rev DD4 |
| O2 | Домены: личный / third-level / авто `<name>.<PLATFORM_DOMAIN>` | Brief C3, D4 |
| O3 | `.env.platform` — контракт окружения, генерируется из `platform-env.yaml` | Brief D3 |
| O4 | Reusable workflow — одна точка обновления CI | Brief D2 |
| O5 | Postgres façade: `pgbouncer:6432`, не `postgres:6432` | 04-rev DD2/F3 |
| O6 | `provides:` keys ⊆ `profiles` | 04-rev DD8 |
| O7 | **remove-project ТОЛЬКО безопасный** — данные (volumes, БД, GitHub-репо, локальная папка) никогда не удаляются автоматически; никакого `--purge` | Сессия 2026-07-17 |
| O8 | `uses: __ORG_NAME__/ai-platform/...` — org = context, workflow из зеркала контекста | Сессия 2026-07-17 |
| O9 | Мини-Makefile в проекте — ежедневные команды из папки проекта | Сессия 2026-07-17 |
| O10 | Scope: connection-модель + remove-project + list/status + adopt + проектный AGENTS.md; `delete-context` и `project-doctor` — отложены (TRAP[DEBT]) | Сессия 2026-07-17 |
| O11 | Существующий проект dance-site с личным доменом `sexydancerostov.ru` — adopt обязан поддержать личный домен | Сессия 2026-07-17 |

---

## Architecture Overview

### Полный жизненный цикл (закрывает forensics-вердикт)

```
 CREATE                REGISTER              DEPLOY                 OBSERVE              REMOVE (NEW)
────────────────────────────────────────────────────────────────────────────────────────────────────
 make new-project ──→ node.yaml:projects[] ─→ git push → CI →      make project-list    make remove-project
 make project-adopt    append (существует)    reusable workflow →   make project-status  ├─ unregister из node.yaml
 (NEW, для            unregister (NEW)       SSH forced-command    (NEW)                ├─ SSH: compose down (БЕЗ -v)
  существующих)                              deploy-project.sh                          ├─ снятие vhost
                                                                                        ├─ on_project_remove хуки
                                                                                        └─ volumes/БД/репо — НЕ ТРОГАЮТСЯ (O7)
```

### CI: org-agnostic reusable workflow (O1 + O8)

```
Проект <org>/myapp: .github/workflows/deploy.yml (≤40 строк, ≤15 non-comment)
  jobs.deploy:
    uses: __ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml@main
    with: { project_name: __PROJECT_NAME__ }
    secrets: inherit
         │  __ORG_NAME__ подставляется при scaffold → org проекта = org контекста.
         │  Приватный reusable workflow виден только внутри своей org →
         │  каждый контекст получает workflow из СВОЕГО зеркала (context-promote).
         ▼
Зеркало <org>/ai-platform: deploy-project.yml (reusable, org-agnostic — без хардкода org)
  resolve-node:  checkout проекта → target_node из ai-platform.yaml →
                 ssh_host = fromJson(vars.NODE_HOST_MAP)[target_node]   ← org variable, ноль секретов
  build-image:   docker login ghcr.io (github.token) → build-push :sha
  deploy:        ssh ci-deploy@ssh_host, key: secrets.CI_DEPLOY_KEY (org, inherited)
                 forced-command → deploy-project.sh <name> <sha> <env>
```

### `.env.platform` — контракт окружения (O3, O5)

Генератор `gen-env-platform.sh`: `platform-env.yaml provides:` + `ai-platform.yaml` → `.env.platform`:

```
# GENERATED by ai-platform — DO NOT EDIT
PLATFORM_DOMAIN=tronyx.ru
PLATFORM_PROVIDES=postgres,redis,litellm,langfuse,minio,clickhouse,nginx
PLATFORM_POSTGRES_HOST=pgbouncer        ← façade, НЕ имя контейнера (O5)
PLATFORM_POSTGRES_PORT=6432
PLATFORM_POSTGRES_DSN=postgresql://myapp_user:***@pgbouncer:6432/myapp_db
PLATFORM_REDIS_URL=redis://redis:6379
... (по одному HOST/PORT + DSN|URL на сервис)
PLATFORM_PROXY_NET=proxy-net
PLATFORM_SHARED_DB_NET=shared-db-net
PLATFORM_NO_PROXY=localhost,127.0.0.1,.local,pgbouncer,redis,clickhouse
```

Секция `provides:` в `platform-env.yaml` — по 04-rev (ключ = имя профиля, `host`/`port`/`dsn_template|url_template`/`networks`; postgres → `host: pgbouncer, port: 6432`; nginx → ключ `nginx`, host `nginx-proxy`).

### Проектный слой для человека и AI (O9 + D-опция)

```
Проект (9 платформенных файлов):
  ai-platform.yaml   docker-compose.yml   Dockerfile   nginx/default.conf
  .env.platform      .github/workflows/deploy.yml      README.md
  Makefile  (NEW)  ← мини-фасад: sync-env / status / help → делегирует в платформу
  AGENTS.md (NEW)  ← контекст для AI-агента: что даёт платформа, чего НЕ делать, команды
```

---

## Contracts (формализация ДО реализации)

### K1: SSH forced-command verb contract (расширение, обратная совместимость)

`SSH_ORIGINAL_COMMAND`, парсится в `core/entrypoints/deploy.sh`:

| Форма | Действие |
|-------|----------|
| `<project> <sha> <environment>` | Deploy (текущее поведение, БЕЗ изменений — первый токен не из списка глаголов) |
| `remove <project>` | `deploy-project.sh --remove <project>`: `compose down` (БЕЗ `-v`), `_trigger_remove_hooks()`, деактивация vhost на ноде, запись в audit.log. Volumes, images, данные — не трогаются (O7) |
| `status <project>` | JSON в stdout: `docker compose ps --format json` + последний `deploy-result.json` |

### K2: `_trigger_remove_hooks()` — симметрия контракта хуков (forensics V3)

Зеркало `_trigger_deploy_hooks()`: итерация `module.yaml → hooks.on_project_remove`. Контракт хука: **идемпотентный и неразрушающий** (backup/уведомление — да; `DROP DATABASE` — запрещено, O7). Модули не обязаны определять хук (0 модулей сегодня — норм); задача — починить runtime-вызов, который gate-тест уже проверяет.

### K3: Мини-Makefile проекта ↔ платформа

```make
# Проектный Makefile (шаблон, ≤15 строк):
PLATFORM_DIR ?= $(HOME)/projects/ai-platform
sync-env: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-env PROJECT=$(CURDIR)
status:   ; @$(MAKE) -C $(PLATFORM_DIR) project-status PROJECT=$(CURDIR)
help:     ; ...
```
Платформенные таргеты принимают `PROJECT=<path>` и извлекают имя из `ai-platform.yaml`. Деплой = `git push` (не make-таргет).

### K4: Reusable workflow

`on.workflow_call` → inputs: `project_name` (required); vars: `NODE_HOST_MAP`; secrets: `CI_DEPLOY_KEY` (через `secrets: inherit`). Файл org-agnostic — никаких упоминаний конкретной org внутри.

### K5: Обязательства контекстной org (REQUIRES)

Каждая org-контекст: variable `NODE_HOST_MAP` + secret `CI_DEPLOY_KEY`. Отсутствие → CI падает на resolve-node с явной ошибкой `"NODE_HOST_MAP org variable not configured"`. Проверка выполняется в CI (fail-fast), `project-doctor` отложен (O10).

---

## Design Decisions

### Унаследованные из 04-rev (компактно, полные @rationale — в 04-DevPlan-rev.md)
- **DD1** `uses: .../deploy-project.yml@main` + `secrets: inherit` — авто-апдейт CI всех проектов.
- **DD2** DSN из `provides:` с явными host/port — `pgbouncer:6432` (иначе non-routable).
- **DD3** Авто-домен `${NAME}.${PLATFORM_DOMAIN}` — wildcard-cert покрывает без действий.
- **DD4** Node resolve через `vars.NODE_HOST_MAP`, не checkout — zero-secret, без mirror-конфликта.
- **DD5** `env_file: .env.platform` (единственный) — `environment:` проекта всегда переопределяет.
- **DD6** Глагол `project-sync-env` закреплён.
- **DD7** ORG/NODE defaults из `.env` (`PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE`).
- **DD8** `provides:` keys ⊆ `profiles` — enforced генератором и тестом.

### Новые

**DD9: `__ORG_NAME__/ai-platform` вместо хардкода TronyxLab (O8)**
**@rationale** Q: Почему плейсхолдер? A: Инвариант «org = context» + приватные reusable workflows доступны только внутри своей org. Хардкод сломал бы все контексты кроме одного. `context-promote` уже создаёт зеркало `<org>/ai-platform` — workflow приезжает туда автоматически. Rejected: хардкод TronyxLab — работает ровно для одной org.

**DD10: remove-project — только безопасный, без `--purge` (O7)**
**@rationale** Q: Почему нет режима полного удаления? A: Явное решение владельца: данные никогда не удаляются автоматически. `remove` = отключение (unregister + compose down + vhost off). Очистка volumes/БД/репо — всегда ручная, осознанная операция. Rejected: `--purge`-флаг — даже за флагом автоматическое удаление данных неприемлемо.
→ Coder: добавить в `remove-project.sh` `# 💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически · Source: owner · Risk: авто-очистка = невосстановимая потеря БД проекта`

**DD11: Мини-Makefile в проекте (O9)**
**@rationale** Q: Почему файл-делегат, а не команды из репо платформы? A: 80% времени владелец в папке проекта; `make sync-env` там — комфорт. Вся логика в платформе, делегат ~10 строк со стабильным контрактом → дрейф-риск минимален. Rejected: только из платформы — постоянные `cd`/`-C` пути; rejected: полный Makefile в проекте — дублирование логики = дрейф.

**DD12: remove/status через существующий deploy-канал (anti-dual-mechanism)**
**@rationale** Q: Почему не отдельный SSH-пользователь/скрипт для remove? A: Второй канал = dual mechanism = ускоритель дрейфа. Один forced-command entrypoint (`deploy.sh`) с verb-контрактом K1 сохраняет единую поверхность безопасности и аудита. Rejected: новый forced-command юзер — вторая поверхность атаки и второй audit-путь.

**DD13: Проектный AGENTS.md генерируется из шаблона**
**@rationale** Q: Почему файл, а не ссылка на репо платформы? A: Zero-Context Survival: агент видит только папку проекта; сетевые/кросс-репо ссылки недоступны или дороги. Короткий (≤60 строк) генерируемый файл с плейсхолдерами не дрейфует: изменчивые данные (сервисы) живут в `.env.platform`, AGENTS.md описывает только стабильный контракт и команды.

### Configuration DRY
Новые глаголы каскадируются в 5 файлов (Makefile → scaffold.sh → entrypoint-manifest.yaml → core/AGENTS.md → root AGENTS.md) — каскад полный, см. §Change Impact. Единственный источник host/port — `platform-env.yaml provides:`; список платформенных команд проекта — только в шаблоне Makefile/AGENTS.md.

---

## $TASKS (21 задача)

| ID | Task | Role | Output | Deps | Cx | Acceptance Criteria |
|----|------|------|--------|------|----|---------------------|
| T1 | Reusable workflow `deploy-project.yml` (org-agnostic, K4) + удаление мёртвого `.github/actions/resolve-node/` | Coder | `.github/workflows/deploy-project.yml` NEW; `.github/actions/resolve-node/` DELETE | — | 7 | `vars.NODE_HOST_MAP` через `fromJson()`; без cross-repo checkout; валидная `on.workflow_call` схема; в workflow нет упоминаний конкретной org; `grep -r "resolve-node" .github/ core/ Makefile` → 0 ссылок на удалённый action |
| T2 | `provides:` в `platform-env.yaml` | Coder | `platform-env.yaml` UPD | — | 4 | YAML парсится; ≥7 сервисов host/port + dsn/url_template; postgres = `pgbouncer:6432`; все ключи ∈ profiles |
| T3 | `add-vhost.sh`: third-level → wildcard-cert; личный домен → собственный cert path | Coder | `add-vhost.sh` UPD | — | 3 | `*.${PLATFORM_DOMAIN}` → wildcard path; личный домен (кейс O11) → отдельный cert path; удаление vhost поддержано (для T10) |
| T4 | `node-resolver.sh`: CI-контекст из `NODE_HOST_MAP` env (JSON), fallback на файловый резолв | Coder | `node-resolver.sh` UPD | — | 3 | Обе ветки работают; ошибки явные (K5) |
| T5 | `ai-platform.schema.json`: `platform_domain` в root + needs | Coder | schema UPD | — | 3 | Схема валидирует шаблоны с новым полем; существующие тесты зелёные |
| T6 | Verb-контракт K1 в `deploy.sh` + `deploy-project.sh`: `--remove` (compose down БЕЗ `-v`, vhost off на ноде, audit) + `--status` (JSON) + `_trigger_remove_hooks()` (K2) | Coder | `core/entrypoints/deploy.sh` UPD, `core/internal/deploy/deploy-project.sh` UPD | — | 8 | Легаси-форма `<proj> <sha> <env>` работает без изменений; `remove` не содержит `down -v`/`volume rm`/`image rm`; `_trigger_remove_hooks` итерирует `hooks.on_project_remove`; TRAP[BUSINESS] из DD10 добавлен |
| T7 | Генератор `gen-env-platform.sh` | Coder | `core/internal/scaffold/gen-env-platform.sh` NEW | T2 | 6 | Подстановка `${NAME}/${HOST}/${PORT}`; `pgbouncer:6432` в DSN; `grep -c "^PLATFORM_"` ≥ 8; header `# GENERATED`; provides ⊆ profiles (fail-fast); идемпотентен |
| T8 | `scaffold.sh`: подкоманды `project-sync-env`, `remove-project`, `adopt-project`, `project-list`, `project-status`; positional→named мост для `new-project` (ORG/NODE из env, DD7) | Coder | `core/entrypoints/scaffold.sh` UPD | T7 | 5 | Каждая подкоманда делегирует в свой internal-скрипт; `new-project` передаёт `--name/--template/--org/--node/--domain` |
| T9 | `add-project.sh`: авто-домен, вызов gen-env-platform, копирование Makefile+AGENTS.md, НЕ копировать platform-deploy.yml | Coder | `add-project.sh` UPD | T7, T8 | 7 | Без `--domain` → `<name>.${PLATFORM_DOMAIN}`; `.env.platform`, `Makefile`, `AGENTS.md` в проекте; `create_github_repo()` не тронут (debt F12) |
| T10 | `remove-project.sh` NEW: unregister из `node.yaml:projects[]` (`yq del`), локальное снятие vhost-overlay, SSH `remove <project>` на ноду, итоговый отчёт «что НЕ удалено» | Coder | `core/internal/scaffold/remove-project.sh` NEW | T3, T6, T8 | 8 | Запись удалена, остальные записи и структура YAML целы; идемпотентен (повторный вызов → SKIP); при недоступности VPS — unregister выполняется, SSH-шаг помечается SKIPPED с инструкцией; в конце печатает список нетронутого (volumes, БД, репо, папка) |
| T11 | `adopt-project.sh` NEW: существующий проект → новый контракт (упростить deploy.yml по шаблону, удалить platform-deploy.yml, сгенерить `.env.platform`, добавить Makefile/AGENTS.md если нет, регистрация idempotent, vhost c личным доменом — O11) | Coder | `core/internal/scaffold/adopt-project.sh` NEW | T3, T7, T8 | 8 | `src/`, `Dockerfile`, прикладной код НЕ тронуты; повторный запуск → no-op; `--domain sexydancerostov.ru`-кейс проходит (личный cert path); отчёт diff-ом что изменено |
| T12 | `project-list.sh` NEW: `list` — таблица из node.yaml (имя, нода, домен, template); `status` — SSH `status <project>` → человекочитаемый вывод | Coder | `core/internal/scaffold/project-list.sh` NEW | T6, T8 | 5 | `list` работает офлайн (только локальный node.yaml); `status` при недоступности ноды — явная ошибка, не зависание (timeout) |
| T13 | template-frontend: deploy.yml ≤40/≤15 строк с `uses: __ORG_NAME__/ai-platform/...`; DELETE platform-deploy.yml; compose `env_file: .env.platform`; ai-platform.yaml `platform_domain`; `.env.platform` placeholder; `Makefile` NEW (K3); `AGENTS.md` NEW (DD13, ≤60 строк: «Что даёт платформа / Чего НЕ делать / Команды / Домен»); README | Coder | 8 файлов в `templates/template-frontend/` | T9 | 5 | Все критерии в описании; плейсхолдеры `__PROJECT_NAME__/__ORG_NAME__/__DOMAIN__` консистентны |
| T14 | template-backend: тот же набор | Coder | 8 файлов | T9 | 4 | Критерии T13, backend-специфика |
| T15 | template-fullstack: тот же набор | Coder | 8 файлов | T9 | 4 | Критерии T13, fullstack (2 сервиса) |
| T16 | `Makefile`: таргеты `project-sync-env`, `remove-project`, `project-adopt`, `project-list`, `project-status`; обновлённый `new-project` (ORG/NODE из .env) | Coder | `Makefile` UPD | T8–T12 | 4 | Каждый таргет: валидация параметров → делегация в scaffold.sh → IMP-логи; `make help` показывает новые таргеты |
| T17 | Регистрация и документация: `entrypoint-manifest.yaml` (5 новых глаголов), `core/AGENTS.md` (таблица операций), root `AGENTS.md` (глоссарий глаголов), `docs/projects-root-AGENTS.md` (удалить строку «Статус: контракт плана…» — команды стали реальными) | Coder | 4 файла UPD | T16 | 3 | `no-unregistered-entrypoint` gate зелёный; глоссарий содержит все 5 глаголов; в projects-root-AGENTS.md нет строки «Статус» |
| T18 | `tests/test_scaffold_env_platform.py` | Coder | NEW | T7 | 6 | 9 тестов по $TEST_SPEC; LDD-трейс IMP:7-10 в выводе |
| T19 | `tests/test_project_ci_contract.py` | Coder | NEW | T1, T13–T15 | 6 | 7 тестов по $TEST_SPEC |
| T20 | `tests/test_project_lifecycle.py` | Coder | NEW | T10–T12 | 7 | 9 тестов по $TEST_SPEC; фикстуры node.yaml через tmp_path; домены — из фикстур, не хардкод |
| T21 | Gate: `python -m pytest tests/ -s -v` + `make gate MODE=fast` | QA | Отчёт | T1–T20 | 3 | Все тесты и gate зелёные |

### Critical Path
`T2 → T7 → T8 → T9 → T13/T14/T15 → T16 → T17 → T21` (8 звеньев). Lifecycle-ветка `T6 → T10/T12 → T20` параллельна ядру до T16.

---

## $PARALLEL_GROUPS

### Wave 1 — независимый фундамент (нет общих файлов)
T1, T2, T3, T4, T5, T6
```
coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 1: T1, T2, T3, T4, T5, T6"
```

### Wave 2 — генератор + entrypoint
T7, T8
```
coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 2: T7, T8"
```

### Wave 3 — internal-скрипты (независимые файлы)
T9, T10, T11, T12
```
coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 3: T9, T10, T11, T12"
```

### Wave 4 — шаблоны (параллельно по шаблонам)
T13, T14, T15
```
coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 4: T13, T14, T15"
```

### Wave 5 — фасад + документация + тесты
Group A: T16, T17 · Group B: T18, T19, T20
```
coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 5: T16, T17, T18, T19, T20"
```

### Wave 6 — валидация
```
qa "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 6: T21 — run python -m pytest tests/ -s -v and make gate MODE=fast, report results"
```

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| test_scaffold_env_platform.py | test_gen_env_platform_has_header | Вывод начинается с `# GENERATED by ai-platform — DO NOT EDIT` | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_min_vars | ≥8 строк `PLATFORM_*` | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_provides_list | `PLATFORM_PROVIDES` = ключи provides из фикстуры | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_dsn_format | DSN = `scheme://user:***@host:port/db` | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_dsn_host_is_pgbouncer | Postgres DSN host=pgbouncer, port=6432 (регресс F3) | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_no_proxy_internal | `PLATFORM_NO_PROXY` содержит pgbouncer,redis | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_idempotent | Повторный запуск → идентичный вывод | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_missing_yaml | Явная ошибка при отсутствии platform-env.yaml | gen-env-platform.sh |
| test_scaffold_env_platform.py | test_gen_env_platform_provides_in_profiles | provides ⊄ profiles → fail-fast (регресс F8) | gen-env-platform.sh |
| test_project_ci_contract.py | test_deploy_yml_calls_reusable_workflow | Шаблонный deploy.yml ≤40 строк всего, ≤15 non-comment; содержит `uses: __ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml` | templates deploy.yml |
| test_project_ci_contract.py | test_deploy_yml_no_resolve_node_action | Нет ссылок на `./.github/actions/resolve-node` (регресс F6) | templates deploy.yml |
| test_project_ci_contract.py | test_reusable_workflow_schema | Валидный `on.workflow_call`, required input `project_name` | deploy-project.yml |
| test_project_ci_contract.py | test_reusable_workflow_no_node_configs_token | Нет `NODE_CONFIGS_TOKEN` нигде в workflow и шаблонах | deploy-project.yml + templates |
| test_project_ci_contract.py | test_reusable_workflow_uses_org_variable | Использует `vars.NODE_HOST_MAP`; нет хардкода org внутри workflow (DD9) | deploy-project.yml |
| test_project_ci_contract.py | test_platform_deploy_yml_deleted_from_templates | `templates/*/.github/workflows/platform-deploy.yml` не существует | templates FS |
| test_project_ci_contract.py | test_template_has_env_platform_makefile_agents | В каждом шаблоне есть `.env.platform`, `Makefile`, `AGENTS.md`; AGENTS.md ≤60 строк | templates FS |
| test_project_lifecycle.py | test_unregister_removes_project_entry | Фикстура node.yaml (3 проекта) → remove → 1 запись удалена, 2 целы, YAML валиден | remove-project.sh |
| test_project_lifecycle.py | test_unregister_idempotent | Повторный remove того же проекта → SKIP, exit 0 | remove-project.sh |
| test_project_lifecycle.py | test_remove_is_safe_no_data_deletion | Скрипты не содержат `down -v`, `volume rm`, `image rm`, `gh repo delete` (контракт O7/DD10) | remove-project.sh + deploy-project.sh |
| test_project_lifecycle.py | test_remove_hooks_triggered_in_runtime | `deploy-project.sh` содержит `_trigger_remove_hooks` и читает `hooks.on_project_remove` (закрывает V3 — симметрия с gate-тестом) | deploy-project.sh |
| test_project_lifecycle.py | test_deploy_verb_contract_backward_compat | Парсер K1: `<proj> <sha> <env>` → deploy; `remove <proj>` → remove; `status <proj>` → status | deploy.sh |
| test_project_lifecycle.py | test_adopt_preserves_project_files | adopt на фикстурном «старом» проекте: src/ и Dockerfile не изменены | adopt-project.sh |
| test_project_lifecycle.py | test_adopt_idempotent | Второй adopt → no-op | adopt-project.sh |
| test_project_lifecycle.py | test_adopt_personal_domain_cert_path | Личный домен (из фикстуры) → non-wildcard cert path в vhost (O11) | adopt-project.sh + add-vhost.sh |
| test_project_lifecycle.py | test_project_list_offline | list читает фикстурный node.yaml без сети → таблица с 3 проектами | project-list.sh |

---

## Acceptance Criteria

| AC | Критерий | Проверка |
|----|----------|----------|
| AC1 | `make new-project NAME=test TEMPLATE=frontend` → deployable проект одной командой | Ручное: scaffold → git push → CI green |
| AC2 | ≤9 платформенных файлов в проекте (7 из 04-rev + Makefile + AGENTS.md) | `find` по списку из §Architecture |
| AC3 | CI без project-секретов: только `vars.NODE_HOST_MAP` + org `CI_DEPLOY_KEY` | CI log; тест no_node_configs_token |
| AC4 | `.env.platform`: ≥8 `PLATFORM_*`, postgres = pgbouncer:6432 | `grep -c "^PLATFORM_"`; тесты T18 |
| AC5 | Авто-домен `<name>.${PLATFORM_DOMAIN}` без `--domain` | ai-platform.yaml после scaffold |
| AC6 | Личный домен работает (`DOMAIN=myapp.com`) | vhost + cert path; тест adopt_personal_domain |
| AC7 | Обновление платформенного CI распространяется без правок проектов | Изменение reusable workflow → следующий push проектов |
| AC8 | `make remove-project PROJECT=x NODE=y`: запись из node.yaml удалена, контейнеры остановлены, vhost снят; volumes/БД/репо/папка НЕ тронуты | Тесты T20 + ручная проверка на VPS |
| AC9 | `make project-adopt PROJECT_DIR=<dir> [DOMAIN=...]` приводит существующий проект (кейс dance-site) к контракту одной командой | Тесты adopt_* + ручной прогон на dance-site |
| AC10 | `make project-list` — таблица проектов офлайн; `make project-status PROJECT=x` — live-статус | Тест list_offline + ручное |
| AC11 | Из папки проекта работают `make sync-env` и `make status` | Ручное в созданном проекте |
| AC12 | Проектный AGENTS.md ≤60 строк, самодостаточен для AI-агента | Тест template_has_...; ревью владельцем |
| AC13 | `python -m pytest tests/ -s -v` и `make gate MODE=fast` зелёные | T21 |

---

## File Manifest (44 записи)

| # | Файл | Действие | Task |
|---|------|----------|------|
| 1 | `.github/workflows/deploy-project.yml` | NEW | T1 |
| 2 | `.github/actions/resolve-node/` | DELETE | T1 |
| 3 | `platform-env.yaml` | UPDATE | T2 |
| 4 | `core/internal/scaffold/add-vhost.sh` | UPDATE | T3 |
| 5 | `core/lib/node-resolver.sh` | UPDATE | T4 |
| 6 | `core/schemas/ai-platform.schema.json` | UPDATE | T5 |
| 7 | `core/entrypoints/deploy.sh` | UPDATE | T6 |
| 8 | `core/internal/deploy/deploy-project.sh` | UPDATE | T6 |
| 9 | `core/internal/scaffold/gen-env-platform.sh` | NEW | T7 |
| 10 | `core/entrypoints/scaffold.sh` | UPDATE | T8 |
| 11 | `core/internal/scaffold/add-project.sh` | UPDATE | T9 |
| 12 | `core/internal/scaffold/remove-project.sh` | NEW | T10 |
| 13 | `core/internal/scaffold/adopt-project.sh` | NEW | T11 |
| 14 | `core/internal/scaffold/project-list.sh` | NEW | T12 |
| 15–22 | `templates/template-frontend/`: deploy.yml (SIMPLIFY), platform-deploy.yml (DELETE), docker-compose.yml, ai-platform.yaml, README.md (UPDATE), `.env.platform`, `Makefile`, `AGENTS.md` (NEW) | см. действия | T13 |
| 23–30 | `templates/template-backend/`: тот же набор ×8 | — | T14 |
| 31–38 | `templates/template-fullstack/`: тот же набор ×8 | — | T15 |
| 39 | `Makefile` | UPDATE | T16 |
| 40 | `core/entrypoint-manifest.yaml` | UPDATE | T17 |
| 41 | `core/AGENTS.md` | UPDATE | T17 |
| 42 | `AGENTS.md` (root) | UPDATE | T17 |
| 42a | `docs/projects-root-AGENTS.md` (канон; symlink `~/projects/AGENTS.md` → него, создан 2026-07-17) | UPDATE | T17 |
| 43 | `tests/test_scaffold_env_platform.py` | NEW | T18 |
| 44 | `tests/test_project_ci_contract.py` | NEW | T19 |
| 45 | `tests/test_project_lifecycle.py` | NEW | T20 |

---

## Change Impact

### Каскад новых глаголов (CASCADE_CHECK — 5 файлов на глагол)
`project-sync-env`, `remove-project`, `project-adopt`, `project-list`, `project-status` → Makefile (T16) → scaffold.sh (T8) → entrypoint-manifest.yaml (T17) → core/AGENTS.md (T17) → root AGENTS.md (T17). Все цели включены в задачи — каскад закрыт.

### Конфигурационный каскад
| Значение | Источник | Потребители |
|----------|----------|-------------|
| host/port сервисов | `platform-env.yaml provides:` | gen-env-platform.sh → `.env.platform` |
| `NODE_HOST_MAP` | Org variable (каждый контекст) | reusable workflow, node-resolver.sh (CI-ветка) |
| `CI_DEPLOY_KEY` | Org secret (каждый контекст) | reusable workflow (`secrets: inherit`) |
| `PLATFORM_DOMAIN/ORG/DEFAULT_NODE` | `.env` платформы | Makefile → scaffold → add-project/adopt |
| `projects[]` | `node.yaml` (теперь add+remove) | deploy-modules.sh, project-list.sh, remove-project.sh |

### Dual Mechanism — конвергенция
- Node resolution: composite action + inline-блоки шаблонов → **один** шаг в reusable workflow (`vars.NODE_HOST_MAP`). Action удалён (T1).
- VPS-канал: remove/status **переиспользуют** deploy forced-command (DD12) — второй канал не создаётся.
- Хуки: `on_project_deploy`/`on_project_remove` теперь симметричны в runtime (T6) — иллюзия gate-тестов устранена (forensics V3).

---

## Edge Cases

| Сценарий | Поведение |
|----------|-----------|
| `PLATFORM_DOMAIN` не задан | Авто-домен пропущен, warning; `--domain` работает |
| provides-ключ ∉ profiles | gen-env-platform.sh fail-fast с именем ключа |
| `NODE_HOST_MAP` не задан в org / нет ключа ноды | CI fail на resolve-node с явным сообщением (K5) |
| `target_node` отсутствует в ai-platform.yaml | Default `tronyx-vps` (обратная совместимость) |
| remove: VPS недоступен | unregister + vhost выполняются, SSH-шаг → SKIPPED + инструкция для ручного повтора; exit 0 с warning |
| remove: проект не найден в node.yaml | SKIP, exit 0 (идемпотентность) |
| remove: контейнеры уже остановлены | `compose down` идемпотентен → OK |
| adopt: у проекта уже есть `.env.platform`/Makefile/AGENTS.md | Перегенерация `.env.platform`; Makefile/AGENTS.md не перезаписываются без `--force` |
| adopt: нет `ai-platform.yaml` | Генерируется интерактивно-минимальный (name из папки, node default) с предупреждением |
| status: нода не отвечает | Timeout (≤10s), явная ошибка, ненулевой exit |
| Проект имеет свой `.env` | `env_file: .env.platform` единственный в compose; `environment:` проекта всегда приоритетнее (F14) |
| Легаси SSH-команда деплоя | Работает без изменений (K1 backward compat) |

---

## TRAP[DEBT] (отложено по O10 + перенос из 04-rev)

1. **MED · `delete-context` отсутствует** — lifecycle контекстов 2/3 (forensics V2). Отложено: операция редкая. При реализации: unregister из `contexts[]` + архивация репо (не удаление, по духу O7).
2. **LO · `project-doctor` отсутствует** — диагностика подключения (org-vars, vhost, свежесть .env.platform). Częściowo покрыто fail-fast в CI (K5).
3. **MED · `add-project.sh` contract drift — `create_github_repo()`** (перенос F12): MODULE_CONTRACT заявляет «Never auto-creates GitHub repos», код создаёт. Вне scope.
4. **LO · Плейсхолдеры шаблонов** `$X` vs `__X__` — конвенция не унифицирована (перенос из 04-rev).
5. **LO · Свежесть `.env.platform` не отслеживается** — изменение `provides:` не сигнализирует проектам о необходимости `make sync-env`. Кандидат: hash-стемп в header + проверка в CI.

---

## Next Steps

```
Wave 1: coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 1: T1, T2, T3, T4, T5, T6"
Wave 2: coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 2: T7, T8"
Wave 3: coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 3: T9, T10, T11, T12"
Wave 4: coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 4: T13, T14, T15"
Wave 5: coder "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 5: T16, T17, T18, T19, T20"
Wave 6: qa    "Read .ai/plans/001-project-connection-model/05-DevPlan-connect-lifecycle.md, implement Wave 6: T21"
```

Пост-реализация (ручные шаги владельца, вне кода):
1. Установить `NODE_HOST_MAP` (variable) и `CI_DEPLOY_KEY` (secret) на org-уровне каждого контекста.
2. `make context-promote CONTEXT=<ctx>` — доставить reusable workflow в зеркала.
3. `make project-adopt PROJECT_DIR=~/projects/<org>/dance-site DOMAIN=sexydancerostov.ru` — первый adopt.

**Plan status:** READY FOR IMPLEMENTATION. Superсedes 02-DevPlan.md и 04-DevPlan-rev.md (R1: авторитетный DevPlan = наивысший NN).

$END_DEVPLAN
