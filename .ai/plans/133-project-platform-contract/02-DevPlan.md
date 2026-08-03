# 133-project-platform-contract — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Реализовать файл-контракт AI-PLATFORM.md для всех проектов tronyx-lab (гибрид: статичный указатель на канон платформы + генерируемая per-node секция) и починить фактическое предоставление шаред-доступа к БД по needs.database (роли + pgbouncer wildcard-маршрутизация + credentials-канал).
DESCRIPTION:           4 волны. W1 — канон docs/platform-project-contract.md + генератор gen_project_platform_md.py + scaffold-интеграция + unit-тесты. W2 — фикс БД: pgbouncer wildcard (docker-compose.base.yml), расширение хука on_project_deploy (роль/пароль/GRANT/credentials-файл), password-injection в gen_env_platform, unit + e2e-тесты на локальном стеке (negative-тест найденного бага, R5). W3 — генерация и коммит AI-PLATFORM.md в botanika/dance-site/tronyx-site + обновление docs/projects-root-AGENTS.md и entrypoint-manifest (описание project-sync-env). W4 — верификация: make check, make gate MODE=fast, локальный e2e-сценарий (модуль toggle + DB-провижининг через pgbouncer), 03-VerificationReport.md.
RATIONALE:             Эмпирика 2026-08-03: модули вкл/выкл работают (профили compose), шаред-доступ к БД сломан в 2 точках (pgbouncer жёсткий список; роль _user не создаётся). Коллапс суперпозиции пользователя: C (гибридный файл) + B (фикс БД в плане) + имя AI-PLATFORM.md. Wildcard-маршрутизация — нативная возможность edoburu/pgbouncer (`${DB_NAME:-*} = host=... auth_user=...`, делегация auth в postgres через auth_query, auth_user=postgres имеет доступ к pg_shadow) — устраняет регенерацию pgbouncer.ini при каждой новой БД. Generated-секции — канонический паттерн (инвариант 11). Языковая политика: новый код — Python.
ACCEPTANCE_CRITERIA:   (1) AI-PLATFORM.md во всех 3 проектах tronyx-lab, закоммичен, содержит URL канона + актуальную GENERATED-секцию (enabled-модули ноды tronyx-vps, DSN/URL проекта). (2) docs/platform-project-contract.md — канонический документ, навигация root AGENTS.md обновлена. (3) Локальный e2e: needs.database → БД + роль + GRANT созданы; psql через pgbouncer:6432 с ролью проекта работает; подключение несуществующей ролью даёт auth failure, а НЕ «no such database». (4) .env.platform (пере)генерация на ноде подставляет реальный пароль роли в DSN при наличии .platform-db.env. (5) make check + make gate MODE=fast зелёные; unit-тесты: генератор (рендер, per-node данные, маркеры), хук (роль/гранты/идемпотентность/negative), gen_env_platform (password-injection); e2e на локальном стеке. (6) Регрессия существующих тестов (on_project_deploy, gen_env_platform, converge, scaffold) — зелёные.
IMPLEMENTS:            01-Brief.md (133-project-platform-contract); решения коллапса суперпозиции 2026-08-03 (C + B + AI-PLATFORM.md).
IMPACTS:               core/modules/postgres/docker-compose.base.yml; core/modules/postgres/hooks/on_project_deploy.py; core/internal/scaffold/gen_env_platform.py; core/internal/scaffold/scaffold_helpers.py; core/internal/scaffold/project_scaffolder.py; core/internal/scaffold/project_adopter.py; core/internal/bootstrap/converge/projects.py; docs/platform-project-contract.md (новый); docs/projects-root-AGENTS.md; core/entrypoint-manifest.yaml; AGENTS.md (root, навигация); репо tronyx-lab/botanika, tronyx-lab/dance-site, tronyx-lab/tronyx-site (AI-PLATFORM.md); tests/unit/, tests/e2e/.
REQUIRES:              Локальный docker-стек (здоров, проверен); ~/projects/tronyx-lab/ с 3 проектами; решения пользователя по деталям D1-D6 (приняты по умолчанию в GUIDED-режиме, переопределяются по запросу); доступ на запись в репо проектов (локальные коммиты).
$END_ARTIFACT_CONTRACT

## Решения дизайна (GUIDED-режим, приняты по умолчанию)

| ID | Решение | Альтернативы (отклонены) |
|----|---------|--------------------------|
| D1 | Канон платформы: **docs/platform-project-contract.md** (рядом с projects-root-AGENTS.md) | core/AGENTS.md (отклонён: каталог операций, не инструкция окружения) |
| D2 | Проектный файл **AI-PLATFORM.md**: статичная часть (рамки, ссылки на канон по URL + локальному пути, DO NOT, приоритет инструкций) + секция `<!-- GENERATED:START -->...<!-- GENERATED:END -->` | Полный снапшот (устаревает); только статика (не per-node) |
| D3 | Генератор **core/internal/scaffold/gen_project_platform_md.py** (модуль + CLI, паттерн gen_env_platform.py); вызывается из: new-project (после gen_env_platform), adopt-project, Makefile project-sync-env, converge R3 (if-missing) | Новый make-таргет (не нужен — расширяем project-sync-env) |
| D4 | Канал пароля: хук пишет **/opt/projects/<project>/.platform-db.env** (0600, ci-deploy) с PLATFORM_POSTGRES_DB/USER/PASSWORD; при первом создании роли хук перегенерирует .env.platform проекта на ноде (password-injection). Файл НЕ в payload whitelist (создаётся рантаймом на ноде) | Хранение в AGE-secrets (оператор-действие, не автоматика); расширение whitelist (ломает модель доставки) |
| D5 | pgbouncer: **DATABASE_URLS = одна URL без имени БД** → wildcard `* = host=postgres port=5432 auth_user=postgres`; auth_type scram-sha-256, auth_query по умолчанию (pg_shadow, postgres — суперпользователь). Роли проектов подхватываются без рестарта pgbouncer | Динамическая регенерация DATABASE_URLS при каждом деплое (рестарт пулера, гонки) |
| D6 | GRANT-скоуп роли: `GRANT CONNECT ON DATABASE` + `GRANT CREATE, USAGE ON SCHEMA public` (PG15: pg_database_owner у postgres; проекту нужен CREATE на public) | Владелец БД = роль (ломает идемпотентность и общий lifecycle) |

## 0. Draft Code Graph (XML)

```xml
<graph>
  <!-- W1: канон + генератор -->
  <entity name="docs_platform_project_contract_md" TYPE="DOC"
    keywords="platform-contract,environment,services,node,boundaries,instructions"
    annotation="Канонический документ-инструкция платформы для агентов проектов: полное окружение (provides-сервисы, сети, каналы доставки, лимиты), границы DO NOT, команды, приоритет инструкций, указатели на AGENTS.md/root/canon/environment. Ссылается из AI-PLATFORM.md проектов (URL + локальный путь)."
    CrossLinks="AGENTS.md; core/AGENTS.md; core/platform-infra.yaml; docs/projects-root-AGENTS.md"/>
  <entity name="core_internal_scaffold_gen_project_platform_md_py" TYPE="MODULE"
    keywords="generator,AI-PLATFORM.md,provides,node-yaml,enabled-modules,DSN,GENERATED-markers"
    annotation="Python-генератор AI-PLATFORM.md: читает node.yaml (enabled-модули, проекты/домены) + platform-env.yaml (provides, profiles) + ai-platform.yaml (needs, type); рендерит статичную часть (шаблон f-string) + GENERATED-секцию; CLI (--project-dir --node --node-yaml --platform-env --force) + library. Идемпотентен, атомарная запись (shared/atomic_writer)."
    CrossLinks="core/internal/scaffold/gen_env_platform.py; core/internal/shared/node_yaml; core/internal/shared/project_yaml; core/internal/shared/atomic_writer.py; core/internal/scaffold/scaffold_helpers.py"/>
  <entity name="core_internal_scaffold_scaffold_helpers_py" TYPE="MODULE"
    keywords="gen_project_platform_md,integration,sync-env"
    annotation="Добавляется gen_project_platform_md(project_dir, node, ...) — единая точка вызова генератора (аналог gen_project_agents/gen_project_makefile); вызов из project_scaffolder (new-project Step 5), project_adopter (adopt Step 5), Makefile project-sync-env."
    CrossLinks="core/internal/scaffold/gen_project_platform_md.py; core/internal/scaffold/project_scaffolder.py; core/internal/scaffold/project_adopter.py"/>
  <!-- W2: фикс БД -->
  <entity name="core_modules_postgres_docker_compose_base_yml" TYPE="CONFIG"
    keywords="pgbouncer,wildcard,DATABASE_URLS,shared-db-net,auth-delegation"
    annotation="DATABASE_URLS: три URL → одна 'postgresql://user:pw@postgres:5432/' без имени БД → entrypoint генерирует '* = host=postgres port=5432 auth_user=postgres'. Роли проектов резолвятся auth_query'ем из postgres. userlist: postgres (auth_user) остаётся через DB_USER/DB_PASSWORD."
    CrossLinks="core/modules/postgres/hooks/on_project_deploy.py"/>
  <entity name="core_modules_postgres_hooks_on_project_deploy_py" TYPE="MODULE"
    keywords="auto_create_db,role,password,GRANT,credentials-file,idempotent"
    annotation="Расширение: после CREATE DATABASE — idempotent CREATE ROLE ${project}_user LOGIN PASSWORD (secrets.token_urlsafe(24), skip если роль существует) + GRANT CONNECT ON DATABASE + GRANT CREATE,USAGE ON SCHEMA public; запись .platform-db.env (0600); перегенерация .env.platform проекта на ноде (password-injection); non-fatal семантика сохранена."
    CrossLinks="core/internal/scaffold/gen_env_platform.py; core/modules/postgres/docker-compose.base.yml"/>
  <entity name="core_internal_scaffold_gen_env_platform_py" TYPE="MODULE"
    keywords="password-injection,credentials,platform-db.env,DSN"
    annotation="generate(): при наличии project_dir/.platform-db.env — подстановка реального пароля роли в DSN (замена '***'); иначе DSN остаётся с ***. Библиотека+CLI: новые опции --project-dir/--credentials."
    CrossLinks="core/modules/postgres/hooks/on_project_deploy.py; core/internal/bootstrap/converge/projects.py"/>
  <entity name="core_internal_bootstrap_converge_projects_py" TYPE="MODULE"
    keywords="R3,AI-PLATFORM.md,if-missing,reconcile"
    annotation="R3: добавлена генерация AI-PLATFORM.md if-missing (рядом с .env.platform); существующий файл не трогается."
    CrossLinks="core/internal/scaffold/gen_project_platform_md.py"/>
  <!-- W2: тесты -->
  <entity name="tests_unit_test_on_project_deploy_py" TYPE="MODULE"
    keywords="role,GRANT,idempotent,credentials,negative,already-exists"
    annotation="Расширение: role creation (создание/существующая/повтор), GRANT-команды, credentials-файл (content/perms/owner), negative: invalid db_name, psql fail; R5-negative по найденному багу."
    CrossLinks="core/modules/postgres/hooks/on_project_deploy.py"/>
  <entity name="tests_unit_test_gen_project_platform_md_py" TYPE="MODULE"
    keywords="render,generated-markers,enabled-modules,dsn,per-node"
    annotation="Unit: статичная часть, GENERATED-секция (enabled-модули, сервисы с DSN/URL, домены, сети), идемпотентность (повторный вызов = no-op), атомарность, отсутствие файла node.yaml/platform-env.yaml → graceful."
    CrossLinks="core/internal/scaffold/gen_project_platform_md.py"/>
  <entity name="tests_unit_test_gen_env_platform_py" TYPE="MODULE"
    keywords="password-injection,credentials,dsn,placeholder"
    annotation="Unit: DSN с *** без credentials; с credentials — реальный пароль; отсутствие credentials-файла — без изменений."
    CrossLinks="core/internal/scaffold/gen_env_platform.py"/>
  <entity name="tests_e2e_test_shared_db_access_py" TYPE="MODULE"
    keywords="e2e,pgbouncer,wildcard,needs.database,role-connect,local-stack"
    annotation="E2E на локальном стеке (docker): hook → БД+роль; psql через pgbouncer:6432 с ролью — SELECT 1 ок; несуществующая роль — auth failure (negative, R5: баг 'no such database' закрыт); cleanup (DROP DATABASE/DROP ROLE). Маркер: integration/requires_local_docker."
    CrossLinks="core/modules/postgres/hooks/on_project_deploy.py; core/modules/postgres/docker-compose.base.yml"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── docs/platform-project-contract.md (канон) ─► gen_project_platform_md.py (модуль+CLI) ─►
     scaffold_helpers.gen_project_platform_md ─► project_scaffolder (new-project Step 5) ─►
     project_adopter (adopt Step 5) ─► Makefile project-sync-env (CLI) ─► converge R3 (if-missing) ─►
     unit-тесты (рендер/markers/per-node) ─► локальная генерация в 3 проекта tronyx-lab (W3)
W2 ── docker-compose.base.yml: DATABASE_URLS → wildcard URL ─► recreate pgbouncer (локальный стек) ─►
     проверка: '*' маршрутизация + auth через pg_shadow ─► on_project_deploy.py: роли+GRANT+credentials ─►
     gen_env_platform.py: password-injection (по .platform-db.env) ─► unit-тесты ─►
     e2e test_shared_db_access (локальный стек, negative-сценарий бага) ─► cleanup
W3 ── make project-sync-env / генератор → AI-PLATFORM.md в botanika/dance-site/tronyx-site ─►
     1 коммит на проект (feat: add AI-PLATFORM.md platform contract) ─►
     docs/projects-root-AGENTS.md + entrypoint-manifest (описание project-sync-env) ─►
     root AGENTS.md навигация (docs/platform-project-contract.md)
W4 ── make check (до чистоты) ─► make gate MODE=fast (1 раз) ─► локальный e2e-сценарий:
     модуль toggle (status-page) + DB-провижининг (роль через pgbouncer) ─► 03-VerificationReport.md
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/plans/133-project-platform-contract/01-Brief.md` | создан | W0 |
| `.ai/plans/133-project-platform-contract/02-DevPlan.md` | создан | W0 |
| `docs/platform-project-contract.md` | создать (канон инструкций) | W1 |
| `core/internal/scaffold/gen_project_platform_md.py` | создать (генератор + CLI) | W1 |
| `core/internal/scaffold/scaffold_helpers.py` | модифицировать (gen_project_platform_md wrapper) | W1 |
| `core/internal/scaffold/project_scaffolder.py` | модифицировать (Step 5: генерация после env) | W1 |
| `core/internal/scaffold/project_adopter.py` | модифицировать (Step 5: генерация) | W1 |
| `core/internal/bootstrap/converge/projects.py` | модифицировать (R3: AI-PLATFORM.md if-missing) | W1 |
| `tests/unit/test_gen_project_platform_md.py` | создать | W1 |
| `core/modules/postgres/docker-compose.base.yml` | модифицировать (DATABASE_URLS → wildcard) | W2 |
| `core/modules/postgres/hooks/on_project_deploy.py` | модифицировать (роль+пароль+GRANT+credentials) | W2 |
| `core/internal/scaffold/gen_env_platform.py` | модифицировать (password-injection, --project-dir) | W2 |
| `tests/unit/test_on_project_deploy.py` | модифицировать (роль/гранты/идемпотентность/negative) | W2 |
| `tests/unit/test_gen_env_platform.py` | модифицировать (password-injection) | W2 |
| `tests/e2e/test_shared_db_access.py` | создать (e2e локальный стек + negative R5) | W2 |
| `docs/projects-root-AGENTS.md` | модифицировать (упоминание AI-PLATFORM.md) | W3 |
| `core/entrypoint-manifest.yaml` | модифицировать (описание project-sync-env) | W3 |
| `AGENTS.md` (root) | модифицировать (навигация: docs/platform-project-contract.md) | W3 |
| `~/projects/tronyx-lab/botanika/AI-PLATFORM.md` | создать (генерация + коммит) | W3 |
| `~/projects/tronyx-lab/dance-site/AI-PLATFORM.md` | создать (генерация + коммит) | W3 |
| `~/projects/tronyx-lab/tronyx-site/AI-PLATFORM.md` | создать (генерация + коммит) | W3 |
| `.ai/plans/133-project-platform-contract/03-VerificationReport.md` | создать | W4 |

## 3. Волны

### W0 — Зафиксировано
Коллапс суперпозиции: C + B + AI-PLATFORM.md. Решения D1-D6 приняты по умолчанию (GUIDED). Эмпирика в 01-Brief.md §1.1.

### W1 — Канон + генератор AI-PLATFORM.md

1. **docs/platform-project-contract.md** — канонический документ-инструкция платформы:
   - $ARTIFACT_CONTRACT-стиль (MODULE_CONTRACT-регион: @purpose/@scope/@invariants/@rationale);
   - полное окружение платформы: provides-сервисы (postgres/pgbouncer, redis, nginx, litellm, langfuse, minio, clickhouse — из platform-env.yaml), сети (proxy-net, shared-db-net, shared-cache-net, hermes-agent-net, observability-net), каналы доставки (core SCP/rsync, context-overlay git, project payload tar), команды (`make sync-env/status` в проекте; `make new-project/deploy/...` в платформе), границы DO NOT (не поднимать свои БД/редис/прокси/TLS, не публиковать порты, не редактировать .env.platform), приоритет инструкций (AGENTS.md проекта → этот документ → ai-platform/AGENTS.md), указатель на .env.platform как машиночитаемую фактуру.
2. **gen_project_platform_md.py** (Python, LDD [IMP:7-10], атомарная запись через shared/atomic_writer):
   - входы: `--project-dir --node-yaml --platform-env --project-yaml [--force]` (+library `generate(project_dir, node_name, ...) -> str`);
   - статичная часть (f-string шаблон): «Что это», ссылка на канон (URL GitHub `<org>/ai-platform/blob/main/docs/platform-project-contract.md` + локальный путь), DO NOT, команды, приоритет инструкций;
   - GENERATED-секция: node/context/domain (из node.yaml), enabled-модули (из node.yaml modules, `enabled == "true"`), сервисы проекта (из platform-env.yaml provides с подстановкой ${NAME} в DSN/URL — как gen_env_platform), сети проекта, needs-статус проекта (из ai-platform.yaml: database/cache/storage/llm/expose);
   - маркеры `<!-- GENERATED:START:platform_md -->` / `<!-- GENERATED:END:platform_md -->`; при повторной генерации — замена только секции;
   - graceful degradation: нет node.yaml/platform-env.yaml → секция с явным warning-текстом (не краш).
3. **scaffold-интеграция:** scaffold_helpers.gen_project_platform_md (wrapper, не перезаписывает без --force/генерации секции), project_scaffolder (Step 5 после gen_env_platform), project_adopter (Step 5), converge R3 (if-missing, череs прямой импорт как generate_env_platform).
4. **Unit-тесты test_gen_project_platform_md.py:** рендер статики+секции, per-node enabled-модули, DSN-подстановка ${NAME}, маркеры GENERATED, повторная генерация = обновление секции без дублей, missing files → graceful, caplog IMP:9.
5. Верификация волны: `make test-summary TEST_FILE=tests/unit/test_gen_project_platform_md.py`, `make check-diff`.

### W2 — Фикс шаред-доступа к БД

1. **pgbouncer wildcard** (`docker-compose.base.yml`): `DATABASE_URLS: "postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:?...}@postgres:5432/"` (URL без имени БД → entrypoint: `* = host=postgres port=5432 auth_user=postgres`). userlist остаётся: postgres (DB_USER/DB_PASSWORD). `AUTH_TYPE: scram-sha-256` + auth_query по умолчанию (pg_shadow, postgres-суперпользователь). Проверка на локальном стеке: `docker compose --profile postgres up -d --force-recreate pgbouncer`; `docker exec pgbouncer cat /etc/pgbouncer/pgbouncer.ini` — `*` entry; подключение к platform/litellm/langfuse — регрессия зелёная.
2. **Хук on_project_deploy.auto_create_db** — расширение (Python, LDD):
   - после CREATE DATABASE: если роль `${project}_user` отсутствует (SELECT 1 FROM pg_roles) → `CREATE ROLE ... LOGIN PASSWORD <secrets.token_urlsafe(24)>`; `GRANT CONNECT ON DATABASE <db> TO <role>`; `GRANT CREATE, USAGE ON SCHEMA public TO <role>`;
   - credentials-файл `project_dir/.platform-db.env` (0600, owner ci-deploy): PLATFORM_POSTGRES_DB/USER/PASSWORD — запись/обновление атомарно;
   - перегенерация `.env.platform` проекта на ноде при первом создании роли (password-injection, см. п.3) — через generate_env_platform(project_name=..., credentials=...);
   - non-fatal семантика сохранена (ошибки роли/грантов — log, не блокируют деплой; invalid db_name — как сейчас);
   - idempotent: повторный деплой — роль существует → skip создания, пароль НЕ меняется (иначе ломается уже выданный credentials).
3. **gen_env_platform password-injection:** `generate()` принимает опциональный credentials-контекст (dict из .platform-db.env); при наличии пароля — подстановка в DSN (замена `***`); CLI: опции `--project-dir`/`--credentials-file`. Без credentials — поведение не меняется (обратная совместимость: tronyx-site .env.platform).
4. **Unit-тесты:** test_on_project_deploy (роль создана/существует/повтор; GRANT-команды; credentials-файл: содержимое/perms; negative: invalid db_name, psql fail; R5-negative: существовавший баг «pgbouncer no such database» — тест assert'ит, что маршрутизация больше не зависит от pgbouncer.ini-списка), test_gen_env_platform (DSN с *** / с паролем / без файла).
5. **E2E test_shared_db_access.py** (локальный стек, маркер `integration`): temp-проект needs.database → запуск хука → assert БД+роль; `docker exec pgbouncer psql -h pgbouncer -p 6432 -U <role> -d <db>` — SELECT 1; несуществующая роль → auth failure (не «no such database»); cleanup (DROP DATABASE, DROP ROLE). Run: `make test-summary MARKER=integration`.
6. Верификация волны: локальный прогон e2e + `make test-summary TEST_FILE=tests/unit/test_on_project_deploy.py tests/unit/test_gen_env_platform.py`.

### W3 — Правки tronyx-lab

1. Генерация AI-PLATFORM.md в botanika, dance-site, tronyx-site (генератор с node.yaml `~/projects/tronyx-lab/node-configs/tronyx-vps/node.yaml`, platform-env.yaml платформы, ai-platform.yaml проекта).
2. 1 коммит на проект: `feat: add AI-PLATFORM.md platform contract reference` (без изменения payload-файлов, деплой-триггер от коммита не ломается: AI-PLATFORM.md вне payload whitelist).
3. docs/projects-root-AGENTS.md: упоминание AI-PLATFORM.md (контракт для агента в репо проекта; walk-up канон остаётся для ~/projects).
4. core/entrypoint-manifest.yaml: описание `project-sync-env` → «Синхронизация .env.platform и AI-PLATFORM.md»; root AGENTS.md навигация: строка docs/platform-project-contract.md (после `make generate-manifests`/`generate-agents-md` перегенерация глоссария).

### W4 — Верификация

1. `make check` (до чистоты) → `make gate MODE=fast` (один раз).
2. Локальный e2e-сценарий: (а) модуль toggle status-page (down/up, healthy); (б) DB-провижининг полный цикл: needs.database → хук → роль через pgbouncer → cleanup.
3. 03-VerificationReport.md (вердикт + доказательства: выводы тестов, gate-статусы).

## 4. Риски и митигации

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| wildcard `*` в pgbouncer открывает доступ к platform/litellm/langfuse БД | LOW (только по паролю роли; роли проектов не имеют прав на чужие БД — GRANT только на свою) | Документировать в module.yaml postgres; negative-тест: роль проекта не может подключиться к platform |
| Пароль роли в .platform-db.env на ноде — секрет на диске | MED | 0600, owner ci-deploy; не в payload; не в git; ротация — вне скоупа (существующий TRAP[DEBT] 2026-07-17) |
| edoburu/pgbouncer entrypoint: pgbouncer.ini уже существует → wildcard не применится без recreate | LOW | `up --force-recreate` в проверке; одноразовая миграция стека |
| PG15: GRANT CREATE ON SCHEMA public не применится (владелец — pg_database_owner) | LOW | Проверка на локальном стеке до фикса; D6: явный GRANT |
| converge R3 не должен трогать существующий AI-PLATFORM.md | LOW | if-missing семантика (как .env.platform), unit-тест |

$END_DEVPLAN
