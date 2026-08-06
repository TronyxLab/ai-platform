$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Эволюция шаблонов проектов (template-backend, template-frontend) и scaffold-механики платформы для максимальной скорости старта и поддержки проектов. Мета-вариант, синтезированный из двух отчетов суперповерхности.
DESCRIPTION:           Бриф для интерактивного DevPlan-сессии. Содержит: (1) аудит текущего состояния шаблонов vs возможностей платформы; (2) мета-вариант «A+B сейчас, C потом» с пересмотром каждой фазы через суперповерхность; (3) спорные моменты, требующие решения до DevPlan. НЕ является DevPlan — только декомпозиция и опции для коллапса.
RATIONALE:             Два отчета суперповерхности дают пересекающиеся, но не идентичные рекомендации. Бриф их синтезирует, подсвечивает конфликтные точки и готовит почву для поэтапного коллапса (завтра). Мета-вариант проверен на совместимость с инвариантами платформы (1, 4, 9, 11).
ACCEPTANCE_CRITERIA:   Завтрашний DevPlan строится на основе коллапсированных решений по каждому спорному пункту; бриф содержит достаточно контекста, чтобы DevPlan-сессия шла без повторного чтения файлов.
IMPLEMENTS:            Пользовательский запрос на суперповерхность + мета-вариант из двух отчетов.
IMPACTS:               templates/template-backend/, templates/template-frontend/, templates/template-base/ (новое), core/internal/scaffold/, core/internal/practices/generators.py, core/templates/template-manifest.yaml, core/internal/scaffold/project_scaffolder.py
REQUIRES:              Интерактивный коллапс по спорным моментам (завтра); согласие на поэтапный подход (A+B сейчас, C потом).
$END_ARTIFACT_CONTRACT

---

## 1. Аудит: текущее состояние шаблонов

### 1.1 Что платформа уже предоставляет проектам (наследуется, НЕ в шаблоне)

| Канал | Что предоставляется | Механика | Источник |
|-------|---------------------|----------|----------|
| `.env.platform` | `PLATFORM_POSTGRES_HOST/PORT/DSN`, `PLATFORM_REDIS_URL`, `PLATFORM_LITELLM_URL`, `PLATFORM_LANGFUSE_URL`, `PLATFORM_MINIO_URL`, `PLATFORM_CLICKHOUSE_URL/DSN`, `PLATFORM_DOMAIN`, `PLATFORM_PROVIDES`, `PLATFORM_SHARED_DB_NET`, `PLATFORM_PROXY_NET`, `PLATFORM_NO_PROXY` | `gen_env_platform.py` из `platform-env.yaml#provides` (7 сервисов) | `gen_env_platform.py:239-310` |
| practices (GENERATED) | `pyproject.toml`, `.pre-commit-config.yaml`, `tests/conftest.py`, `tests/test_health.py`, `practices.lock` | `generators.py` — рендерятся при `make new-project` (шаг 11) и `make project-sync-practices` | `generators.py:354-386`, `project_scaffolder.py:323-353` |
| CI/CD | Reusable workflow `{{ORG_NAME}}/ai-platform/.github/workflows/deploy-project.yml@main` | Шаблонный `.github/workflows/deploy.yml` (22 строки, чистая делегация) | `templates/*/.github/workflows/deploy.yml` |
| Makefile-фасад | `sync-env`, `status`, `project-check`, `project-fix`, `project-sync-practices`, `project-set-practices` | Генератор `gen_project_makefile` (force=False) | `scaffold_helpers.py:229-285` |
| AGENTS.md | DD13-контракт (≤60 строк), platform services, DO NOT rules | Генератор `gen_project_agents` (force=False) | `scaffold_helpers.py:302-366` |
| AI-PLATFORM.md | Контракт проекта с платформой | `gen_project_platform_md` | `scaffold_helpers.py:390-429` |
| docker networks | `proxy-net`, `shared-db-net`, `shared-cache-net`, `hermes-agent-net`, `observability-net` — все external | `platform-env.yaml#networks` | `platform-env.yaml:19-45` |
| ai-platform.yaml | Полный манифест (name, type, target_node, needs, monitoring, quality.level) | `gen_ai_platform_yaml` (scaffolder, шаг 2) | `scaffold_helpers.py:103-216` |
| nginx vhost | Авто-генерация TLS + proxy_pass + resolver | `add-vhost.sh` (шаг 9) | `project_scaffolder.py:510-562` |

### 1.2 Что шаблоны содержат как payload (не GENERATED)

| Файл | backend | frontend | Статус |
|------|---------|----------|--------|
| `ai-platform.yaml` | ✅ | ✅ | ⚠️ ДУБЛЬ — генератор `gen_ai_platform_yaml` вызывается безусловно (шаг 2, `project_scaffolder.py:646-661`), перезаписывает шаблонный файл |
| `Makefile` | ✅ | ✅ | ⚠️ ДУБЛЬ — `gen_project_makefile` вызывается с force=False, но контент байт-идентичен между backend/frontend |
| `AGENTS.md` | ✅ | ✅ | ⚠️ ДУБЛЬ — `gen_project_agents` (force=False) проигрывает шаблону; но шаблонный stub (19 строк) УСТУПАЕТ генераторному (богаче) |
| `.env.platform` | ✅ stub | ✅ stub | ⚠️ ДУБЛЬ — генерируется на шаге 4 |
| `pyproject.toml` | ✅ | — | ⚠️ ДУБЛЬ — генерируется `gen_project_practices` (force=True, шаг 11) |
| `.pre-commit-config.yaml` | ✅ | ✅ | ⚠️ ДУБЛЬ — генерируется шагом 11 |
| `tests/conftest.py` | ✅ | ✅ | ⚠️ ДУБЛЬ — генерируется шагом 11 |
| `tests/test_health.py` | ✅ | ✅ | ⚠️ ДУБЛЬ — генерируется шагом 11 |
| `practices.lock` | ✅ | ✅ | ⚠️ ДУБЛЬ — генерируется шагом 11 |
| `docker-compose.yml` | ✅ | ✅ | PAYLOAD (уникален) |
| `Dockerfile` | ✅ | ✅ | PAYLOAD (уникален) |
| `src/main.py` | ✅ | — | PAYLOAD (уникален) |
| `src/requirements.txt` | ✅ | — | PAYLOAD (уникален) |
| `src/index.html` | — | ✅ static | PAYLOAD (уникален) |
| `nginx/default.conf` | — | ✅ | PAYLOAD (уникален) |
| `tsconfig.json` | — | ✅ stub | ⚠️ TRAP[DEBT] (`generators.py:368`) — не рендерится, вне practices.lock, drift не детектится |
| `.eslintrc` | — | ✅ stub | ⚠️ TRAP[DEBT] (`generators.py:368`) — не рендерится, вне practices.lock |
| `README.md` | ✅ | ✅ | PAYLOAD (уникален), но `{{DATABASE}}` не в vars рендера → незаменённый плейсхолдер |
| `.github/workflows/deploy.yml` | ✅ | ✅ | PAYLOAD (байт-идентичен между backend/frontend) |

### 1.3 Пробелы (что не использует наследуемые возможности)

1. **Backend: ноль демонстрации подключения к сервисам платформы.** Шаблон знает про `PLATFORM_POSTGRES_DSN` / `PLATFORM_REDIS_URL` / `PLATFORM_LITELLM_URL` только из комментария в `.env.platform` stub. Нет примера подключения.
2. **Frontend: нет реального SPA-toolchain.** `src/index.html` — голый HTML. Нет `package.json`, нет Vite/React/Vue. Dockerfile пытается `npm ci && npm run build`, но package.json отсутствует → build-stage молча no-op (`if [ -f package.json ]`).
3. **Нет примера бизнес-логики.** Оба шаблона содержат только /health+/ready.
4. **AGENTS.md (шаблонный stub, 19 строк) уступает генераторному.** Но генератор не вызывается с force=True → шаблонный stub побеждает.
5. **.env.platform stub — комментарий, а не рабочий пример.** Нет `.env.example` с реальными переменными.
6. **Отсутствуют типовые шаблоны для расширения.** Платформа предоставляет litellm, langfuse, redis, minio, clickhouse — ни один шаблон не показывает переиспользование.
7. **Frontend: tsconfig.json / .eslintrc — вне practices.lock** (TRAP[DEBT] `generators.py:368`).
8. **Нет docker-compose для локального dev с зависимостями.**
9. **`{{DATABASE}}` в README backend не резолвится** — не входит в vars рендера (`project_scaffolder.py:242-251`).

---

## 2. Мета-вариант: A+B сейчас, C потом (пересмотр через суперповерхность)

Оба отчета независимо сошлись на трёхфазную стратегию. Ниже — каждая фаза пересмотрена через суперповерхность с подсветкой спорных моментов.

### Фаза A — чистка дублей и фиксы

#### A1. Удалить GENERATED-копии практик из шаблонов

**Действие:** Удалить из обоих шаблонов: `pyproject.toml`, `.pre-commit-config.yaml`, `tests/conftest.py`, `tests/test_health.py`, `practices.lock`.

**Обоснование:** `gen_project_practices` (`project_scaffolder.py:323-353`) вызывает `sync_practices(force=True)` на шаге 11 — шаблонные копии затираются при каждом scaffold. Это мёртвый груз + риск дрейфа от канона.

**SUPERPOSITION: A1 — куда деть тесты шаблона?**
- **Option A1-a [score: 8/10]:** Удалить `tests/` из шаблонов полностью — `gen_project_practices` создаёт `tests/conftest.py` + `tests/test_health.py` в проекте. Шаблон не несёт тестов. ✅ Соответствует инварианту 11.
- **Option A1-b [score: 6/10]:** Оставить `tests/` в шаблонах как «reference-тесты» для разработчика (не GENERATED), удалить GENERATED-шапку. ❌ Нарушает инвариант 11 — файл будет перетёрт `sync_practices(force=True)`, разработчик потеряет свои тесты при первом `project-sync-practices`.
- **Option A1-c [score: 5/10]:** Оставить `tests/` в шаблонах, но переключить `gen_project_practices` на `force=False`. ❌ Ломает AC1 из DevPlan 137 («project-check зелёный на новом проекте») — без force синхронизация может пропустить файлы.

**Рекомендация:** A1-a (удалить полностью). `tests/` в проекте создаёт генератор; шаблон не дублирует.

**⚠️ СПОРНЫЙ МОМЕНТ 1:** Удаление `tests/` из шаблонов означает, что `tests-check` (если он проверяет наличие `tests/` в шаблоне) может упасть. Нужно проверить gate-тесты на зависимость от `templates/template-*/tests/`. Решение: проверить `tests/gates/` на grep `template-backend/tests|template-frontend/tests` перед удалением.

#### A2. Удалить `.env.platform`-заглушку

**Действие:** Удалить из обоих шаблонов.

**Обоснование:** Генерируется `gen_env_platform` на шаге 4 scaffold (`project_scaffolder.py:264-316`). Stub — мёртвый груз.

**SUPERPOSITION: A2 — чем заменить stub?**
- **Option A2-a [score: 9/10]:** Удалить stub, добавить `.env.example` (не GENERATED) со всеми `PLATFORM_*` переменными из `platform-env.yaml#provides`. Разработчик видит, какие переменные доступны, до запуска `make sync-env`. `.env.example` — reference-файл, не перетирается.
- **Option A2-b [score: 6/10]:** Удалить stub без замены. Разработчик узнаёт переменные только после `make sync-env`. ❌ Снижает discoverability.
- **Option A2-c [score: 4/10]:** Генерировать `.env.example` при scaffold (новый генератор). ❌ Избыточно — `.env.example` статичен относительно `platform-env.yaml`, можно коммитить как payload.

**Рекомендация:** A2-a (удалить stub + добавить `.env.example`).

**⚠️ СПОРНЫЙ МОМЕНТ 2:** Должен ли `.env.example` генерироваться из `platform-env.yaml` (как `.env.example` платформы через `sync_env_defaults.py`) или быть статичным payload-файлом в `template-base/`? Генерация = всегда актуален, но добавляет шаг scaffold; статика = проще, но риск дрейфа при изменении `platform-env.yaml`. Решение: статичный payload в `template-base/`, gate-тест сверяет с `platform-env.yaml#provides`.

#### A3. Удалить `ai-platform.yaml` из шаблонов, перенести контент в `gen_ai_platform_yaml`

**Действие:** Удалить `ai-platform.yaml` из шаблонов; убедиться, что `gen_ai_platform_yaml` несёт весь контент (включая `monitoring`, `quality.level`, `needs`).

**Обоснование:** Генератор вызывается безусловно (шаг 2, `project_scaffolder.py:646-661`) — шаблонный файл копируется (шаг 1) и сразу перезаписывается (шаг 2). Источником становится генератор.

**Аудит генератора (`scaffold_helpers.py:103-216`):** Генератор уже содержит все поля: `name`, `type`, `description`, `target_node`, `needs` (domain, expose, database), `monitoring` (per-type: metrics, metrics_port, logs_retention, alerting, dashboard), `quality.level`. Шаблонный `ai-platform.yaml` — строгое подмножество генераторного.

**Рекомендация:** Удалить из шаблонов; генератор уже авторитетен.

**⚠️ СПОРНЫЙ МОМЕНТ 3:** Шаблонный `ai-platform.yaml` содержит `platform_domain: {{PLATFORM_DOMAIN}}`, которого нет в генераторе. Нужно ли добавлять `platform_domain` в генератор? Проверка: `project_scaffolder.py` и `project_adopter.py` не читают `platform_domain` из `ai-platform.yaml` (домен резолвится из пути/`--domain`). Решение: НЕ добавлять — `platform_domain` — legacy-поле, не используемое scaffold-логикой.

#### A4. Свести Makefile/AGENTS.md к одному источнику — генераторы

**Действие:** Привести генераторы к контенту шаблонов (или наоборот), переключить на `force=True`, удалить файлы из шаблонов.

**Аудит:**
- `gen_project_makefile` (`scaffold_helpers.py:229-285`) — генерирует `sync-env`, `status`, `help`. Шаблонный Makefile — `sync-env`, `status`, `project-check`, `project-fix`, `project-sync-practices`, `project-set-practices`, `help`. **Генератор УСТУПАЕТ шаблону** — не содержит `project-*` таргетов.
- `gen_project_agents` (`scaffold_helpers.py:302-366`) — генерирует DD13-контракт (platform services, DO NOT, commands). Шаблонный AGENTS.md — 19-строчный stub. **Генератор БОГАЧЕ шаблона.**

**SUPERPOSITION: A4 — как разрешить расхождение Makefile?**
- **Option A4-a [score: 9/10]:** Обогатить `gen_project_makefile` до контента шаблонного (добавить `project-check`, `project-fix`, `project-sync-practices`, `project-set-practices`), переключить на `force=True`, удалить Makefile из шаблонов. ✅ Единый источник.
- **Option A4-b [score: 5/10]:** Оставить Makefile в шаблонах, удалить генератор. ❌ Нарушает «single source of truth» — adopt-project тоже вызывает генератор.
- **Option A4-c [score: 7/10]:** Оставить и то, и другое, но сделать генератор авторитетным (force=True), а шаблонный — «fallback для dry-run». ❌ Два источника = дрейф.

**Рекомендация:** A4-a (обогатить генератор + force=True + удалить из шаблонов). Проверить, что adopt-project (`project_adopter.py:304-313`) не сломается — он уже делегирует в `gen_project_makefile`.

**⚠️ СПОРНЫЙ МОМЕНТ 4:** `gen_project_agents` генерирует `<not set>` для домена, если он пуст. Шаблонный stub использует `{{DOMAIN}}`. При `force=True` для нового проекта без домена — `<not set>` корректно, но для adopt-project с существующим AGENTS.md — `force=True` перезапишет пользовательские правки. Решение: `force=True` только для scaffolder (новый проект), `force=False` для adopter (существующий проект). Разделить параметр.

#### A5. Добавить `.gitignore`

**Действие:** Добавить в оба шаблона `.gitignore` с: `.ruff_cache/`, `__pycache__/`, `*.pyc`, `node_modules/`, `.env.platform` (генерируется), `dist/`, `build/`.

**Обоснование:** `.ruff_cache/` уже в `template-backend/` (зафиксирован в glob). Без `.gitignore` `git add -A` коммитит кэши.

**Рекомендация:** Добавить. Для frontend — расширенный (node_modules, dist, .vite).

**⚠️ СПОРНЫЙ МОМЕНТ 5:** Должен ли `.env.platform` быть в `.gitignore`? Сейчас он коммитится (шаг scaffold генерирует + git init). Но инвариант: `.env.platform` содержит `PLATFORM_POSTGRES_DSN` с реальным паролем (после `make sync-env` с credentials). Решение: `.env.platform` в `.gitignore` (секреты), `.env.example` в git (reference). Это меняет текущую модель — проверить, не сломает ли deploy (CI не читает `.env.platform` из git, генерирует на VPS).

#### A6. Убрать `{{DATABASE}}` из README backend

**Действие:** Удалить строку с `{{DATABASE}}` из `README.md` (backend).

**Обоснование:** `{{DATABASE}}` не входит в vars рендера (`project_scaffolder.py:242-251`: `PROJECT_NAME`, `ORG_NAME`, `DOMAIN`, `NODE_NAME`, `PLATFORM_DOMAIN`). Остается незаменённым.

**Рекомендация:** Удалить или добавить `{{DATABASE}}` в vars. Удалить проще — `database` опционален.

#### A7. Добавить `template.yaml` (name/version/requires_practices_version)

**Действие:** Добавить в оба шаблона `template.yaml` с метаданными версии.

**SUPERPOSITION: A7 — нужен ли template.yaml сейчас?**
- **Option A7-a [score: 8/10]:** Добавить `template.yaml` сейчас (name, version, requires_practices_version) — фундамент для фазы C (upgrade-канал) и дрейф-детекта шаблон↔канон.
- **Option A7-b [score: 5/10]:** Отложить до фазы C — сейчас нет потребителя. ❌ Фундамент под upgrade-канал закладывается в A.
- **Option A7-c [score: 6/10]:** Вместо `template.yaml` расширить `template-manifest.yaml` (добавить version-поле в каждую template-запись). ✅ Единый реестр, но `template-manifest.yaml` — про template-engine (рендер `{{UPPER_SNAKE}}`), а не про scaffold-версионирование.

**Рекомендация:** A7-a (отдельный `template.yaml` в каждом шаблоне). Семантически чище — `template-manifest.yaml` для template-engine, `template.yaml` для scaffold-версионирования.

**⚠️ СПОРНЫЙ МОМЕНТ 6:** Формат `template.yaml` и его потребитель. Сейчас нет кода, читающего `template.yaml`. Если добавить файл без потребителя — это мёртвый код. Решение: добавить файл + минимальный reader в `scaffold_helpers.py` (validation: version совместима с practices_manifest.version). Это даёт немедленную ценность (дрейф-детект) и фундамент для C.

#### A8. Зарегистрировать изменения в `template-manifest.yaml` + `make templates-check`

**Действие:** После A1-A7 обновить `core/templates/template-manifest.yaml` (удалить ссылки на удалённые файлы) и прогнать `make templates-check`.

**Обоснование:** Инвариант: каждый шаблон регистрируется; гейт покрытия не пропустит рассогласование.

**⚠️ СПОРНЫЙ МОМЕНТ 7:** `template-manifest.yaml` регистрирует шаблонные директории как `type: directory, recursive: true` (`template-manifest.yaml:192-224`). Удаление файлов из шаблона не ломает регистрацию, но если `templates-check` валидирует покрытие `{{UPPER_SNAKE}}`-плейсхолдеров — нужно убедиться, что удалённые файлы не были единственными с определённым плейсхолдером. Решение: прогнать `make templates-check` после A1-A7.

---

### Фаза B — контент-паттерны (сразу после A)

#### B1. Конфиг через `PLATFORM_*` (backend)

**Действие:** Добавить `src/config.py` — чтение DSN/URL из `.env.platform` через `python-dotenv` (уже в requirements).

**SUPERPOSITION: B1 — pydantic-settings vs python-dotenv?**
- **Option B1-a [score: 7/10]:** `python-dotenv` (уже в requirements) + ручной парсинг `os.environ.get`. Минимум зависимостей, но нет валидации типов.
- **Option B1-b [score: 9/10]:** `pydantic-settings` (BaseSettings) — типобезопасный конфиг, валидация, default-значения. Добавляет зависимость `pydantic-settings`, но это best-practice для FastAPI-проектов.
- **Option B1-c [score: 5/10]:** `dynaconf` — многофункциональный, но избыточен для шаблона.

**Рекомендация:** B1-b (pydantic-settings). Это opinionated choice — платформа навязывает best-practice. Добавить в `requirements.txt`.

**⚠️ СПОРНЫЙ МОМЕНТ 8:** Добавление `pydantic-settings` в `requirements.txt` шаблона — но `requirements.txt` не GENERATED (в отличие от `pyproject.toml`). Разработчик может его редактировать. Решение: оставить `requirements.txt` как payload, добавить `pydantic-settings` + `asyncpg` (для B3) + `prometheus_client` (для B2).

#### B2. Честный `/metrics` (backend)

**Действие:** Реализовать `/metrics` через `prometheus_client` на `metrics_port`, согласованный с `monitoring.metrics_port` в генераторе.

**Аудит:** Шаблонный `main.py:50-53` — `/metrics` возвращает `{"status": "OK", "metrics": "exposed"}`. Генератор `ai-platform.yaml` ставит `metrics_port: 8080` для backend. Healthcheck в compose стучится на `:8000/health`. Несогласованность: `metrics_port` (8080) ≠ порт приложения (8000).

**SUPERPOSITION: B2 — /metrics на том же порту или отдельном?**
- **Option B2-a [score: 8/10]:** `/metrics` на том же порту, что и приложение (8000), через `prometheus_client.make_asgi_app()`. Упрощает compose (один healthcheck-порт).
- **Option B2-b [score: 6/10]:** Отдельный metrics-порт (8080), как декларирует `metrics_port`. Усложняет compose (два порта), но изолирует metrics от business-трафика.
- **Option B2-c [score: 5/10]:** Убрать `/metrics` из шаблона, оставить только `/health`/`/ready`. ❌ Снижает ценность шаблона.

**Рекомендация:** B2-a (тот же порт). Уточнить в генераторе: `metrics_port` = порт приложения (8000 для backend, 80 для frontend). Поправить `gen_ai_platform_yaml` (`scaffold_helpers.py:174-180`): `metrics_port: 8000` для backend.

**⚠️ СПОРНЫЙ МОМЕНТ 9:** Изменение `metrics_port` в генераторе влияет на monitoring-config-renderer (`render-monitoring` verb) — Prometheus scrape config может ссылаться на 8080. Решение: проверить `monitoring_config_renderer.py` и Prometheus-таргеты перед изменением.

#### B3. Пример подключения к сервису (backend)

**Действие:** Добавить рабочий паттерн подключения к postgres через `PLATFORM_POSTGRES_DSN`.

**SUPERPOSITION: B3 — asyncpg vs psycopg vs SQLAlchemy?**
- **Option B3-a [score: 8/10]:** `asyncpg` — async-native, быстрый, идеален для FastAPI. Минимум абстракций.
- **Option B3-b [score: 7/10]:** `psycopg[binary]` (v3) — sync/async, официальный PostgreSQL-драйвер. Поддерживает sync-фоллбэк.
- **Option B3-c [score: 6/10]:** `SQLAlchemy` (async) — ORM, миграции (Alembic). Богаче, но тяжелее для шаблона.
- **Option B3-d [score: 4/10]:** Не добавлять подключение — только /health. ❌ Не решает боль «как подключиться».

**Рекомендация:** B3-a (asyncpg) как базовый паттерн в `src/db.py`. Документировать в README, что для ORM — добавить SQLAlchemy поверх. Это opinionated-minimal: показываем паттерн, не навязываем стек.

**⚠️ СПОРНЫЙ МОМЕНТ 10:** Должен ли пример подключения быть «рабочим endpoint'ом» (например, `GET /items` с запросом к БД) или просто `db.py` с pool-инициализацией? Рабочий endpoint = больше ценности, но риск, что разработчик не удалит пример и получит «мусорный» endpoint в проде. Решение: `db.py` (pool-инициализация) + закомментированный пример endpoint в `main.py` с комментарием «uncomment to use».

#### B4. Честный frontend-стек

**Действие:** Решить дилемму: static-site или Vite+React+TS.

**Аудит:** Dockerfile пытается `npm ci && npm run build`, но `package.json` отсутствует → build-stage no-op. `tsconfig.json` и `.eslintrc` — GENERATED-stub'ы без реального TypeScript-кода. `src/index.html` — голый HTML без build-step. Это «франкенштейн»: TypeScript-конфиги без TypeScript, npm-build без package.json.

**SUPERPOSITION: B4 — static-site vs SPA vs выбор?**
- **Option B4-a [score: 9/10]:** Vite + React + TypeScript — реальный SPA-toolchain. `package.json`, `vite.config.ts`, `src/main.tsx`, `src/App.tsx`, `tsconfig.json`, `.eslintrc` — всё рабочее. Dockerfile `npm ci && npm run build` — осмысленный. Practices `build`/`eslint`-проверки — исполняются. ✅ Соответствует `practices_manifest.yaml` (build: languages [typescript, react]).
- **Option B4-b [score: 6/10]:** Static-site — удалить `tsconfig.json`, `.eslintrc`, `package.json`-стабы. Dockerfile = простой `nginx:alpine` + `COPY src/ /usr/share/nginx/html`. Practices: отключить `build`/`eslint` для frontend (language: sh/static). ❌ Снижает ценность — нет реального SPA.
- **Option B4-c [score: 7/10]:** Два frontend-шаблона: `template-frontend-static` (static-site) и `template-frontend-spa` (Vite+React). ✅ Гибкость, но усложняет scaffold (3 шаблона вместо 2).

**Рекомендация:** B4-a (Vite + React + TS). Платформа явно создана для проектов с frontend-стеком (hermes-dashboard, status-page, langfuse — все React/SPA). Static-site — слишком тривиален для шаблона. Если позже появится потребность в static-site — это фаза C (новый шаблон).

**⚠️ СПОРНЫЙ МОМЕНТ 11 (КРИТИЧЕСКИЙ):** Vite+React добавляет значительный payload в шаблон (`package.json` с зависимостями, `node_modules` при установке, `vite.config.ts`, `src/main.tsx`, `src/App.tsx`, `index.html` в корне). Это меняет структуру `template-frontend/` фундаментально. Решение: принять B4-a как направление; конкретный набор зависимостей (React 19? Vue? Svelte?) — отдельный коллапс завтра.

**⚠️ СПОРНЫЙ МОМЕНТ 12:** `tsconfig.json` и `.eslintrc` сейчас GENERATED-stub'ы, но `generators.py` их НЕ рендерит (TRAP[DEBT] `generators.py:368`). При B4-a: либо реализовать `render_eslintrc`/`render_tsconfig` в `generators.py` (закрыть TRAP), либо сделать их статичным payload (не GENERATED). Решение: статичный payload — eslint/tsconfig конфиги проектно-специфичны, нет смысла генерировать из канона (в отличие от ruff/pytest). Удалить GENERATED-шапку, сделать обычными файлами шаблона.

#### B5. README = гайд «как подключиться к сервисам платформы»

**Действие:** Переработать README обоих шаблонов: таблица `PLATFORM_*` ↔ сервис ↔ пример кода.

**Рекомендация:** Принять. README — главный onboarding-документ; текущий — минимальный.

---

### Фаза C — композируемые слои + feature-паки (когда начнёте расширять набор)

**Действие:** Выделить базовый слой (`template-base/`), языковые шаблоны (`template-{backend,frontend,bot,worker}/`), `templates/packs/` (`db-postgres`, `llm-litellm`, `cache-redis`, `storage-minio`, `auth`, `queue`, `observability`). `new-project --template backend --pack db,llm` — сборка слоёв в `project_scaffolder.py`.

**SUPERPOSITION: C — когда и как включать?**
- **Option C-now [score: 5/10]:** Делать C сразу вместе с A+B. ❌ Переусложняет первый заход; A+B уже даёт ценность.
- **Option C-later [score: 9/10]:** A+B сейчас, C — когда появится 3-й шаблон (bot/worker/llm-service). Фундамент под C (A7: `template.yaml`, A8: manifest) закладывается в A.
- **Option C-never [score: 3/10]:** Не делать C, плодить независимые шаблоны. ❌ Дублирование растёт линейно.

**Рекомендация:** C-later. A+B — немедленно; C — по триггеру «3-й шаблон».

**⚠️ СПОРНЫЙ МОМЕНТ 13:** Фаза C требует доработки `project_scaffolder.py` (merge base + overlay + packs). Это нетривиально: merge-политика (конфликты файлов — ошибка сборки), порядок слоёв, обработка `{{UPPER_SNAKE}}` в каждом слое. Решение: отложить детальный дизайн C до триггера; в A7 заложить только `template.yaml`-версионирование (минимальный фундамент).

---

## 3. Спорные моменты — сводная таблица для коллапса (завтра)

| # | Спорный момент | Опции | Рекомендация | Impact |
|---|----------------|-------|--------------|--------|
| 1 | Удаление `tests/` из шаблонов — проверить gate-зависимости | A1-a (удалить) / A1-b (оставить reference) / A1-c (force=False) | A1-a | Проверить `tests/gates/` на grep |
| 2 | `.env.example` — генерировать или статичный payload? | A2-a (статичный + gate) / A2-b (без замены) / A2-c (генерировать) | A2-a | Новый gate-тест сверки с `platform-env.yaml` |
| 3 | `platform_domain` в генераторе ai-platform.yaml? | Добавить / НЕ добавлять (legacy) | НЕ добавлять | Проверить потребителей `platform_domain` |
| 4 | `gen_project_agents` force=True для scaffolder, force=False для adopter | Разделить параметр / всегда force=False / всегда force=True | Разделить | `project_scaffolder.py` vs `project_adopter.py` |
| 5 | `.env.platform` в `.gitignore`? | Да (секреты) / Нет (коммитится) | Да | Проверить deploy-канал (CI не читает из git) |
| 6 | `template.yaml` — формат и потребитель | A7-a (файл + reader) / A7-b (отложить) / A7-c (в manifest) | A7-a | Новый reader в `scaffold_helpers.py` |
| 7 | `templates-check` после удаления файлов | Прогнать / не требуется | Прогнать | `make templates-check` |
| 8 | `pydantic-settings` vs `python-dotenv` для config | B1-a (dotenv) / B1-b (pydantic) / B1-c (dynaconf) | B1-b | +зависимость в requirements.txt |
| 9 | `/metrics` на том же порту или отдельном? | B2-a (тот же) / B2-b (отдельный) / B2-c (убрать) | B2-a | Поправить `gen_ai_platform_yaml` + проверить monitoring |
| 10 | Пример БД — рабочий endpoint или только pool? | B3-a (asyncpg) / B3-b (psycopg) / B3-c (SQLAlchemy) / B3-d (нет) | B3-a + закомментированный endpoint | `src/db.py` + комментарий в `main.py` |
| 11 | **Frontend: Vite+React+TS vs static-site?** | B4-a (Vite+React) / B4-b (static) / B4-c (два шаблона) | B4-a | **Фундаментальное изменение template-frontend/** |
| 12 | `tsconfig.json`/`.eslintrc` — генерировать или статичный payload? | Генерировать (закрыть TRAP) / Статичный payload | Статичный payload | Удалить GENERATED-шапку |
| 13 | Фаза C — когда включать? | C-now / C-later / C-never | C-later (по триггеру 3-й шаблон) | Отложить дизайн merge-логики |

---

## 4. Мета-замечания (пересмотр мета-варианта)

### 4.1 Сильные стороны мета-варианта «A+B сейчас, C потом»

1. **A устраняет существующий дрейф** — 7 из ~15 файлов в шаблонах — GENERATED-дубли, затираемые при scaffold. Удаление = мёртвый груз убирается, канон (генераторы) становится единственным источником.
2. **B доставляет «переиспользование придуманных решений»** — config.py, db.py, metrics, README-гайд. Каждый новый проект стартует с готовых паттернов.
3. **C включается по триггеру** — не переусложняет первый заход, фундамент (template.yaml, manifest) закладывается в A.
4. **Соблюдает инварианты** — 11 (generated files не редактируются), 4 (3 канонических AGENTS.md + templates вне скоупа), 9 (тестовый сервер пересоздаваем).

### 4.2 Слабые места / риски мета-варианта

1. **A4 (Makefile генератор) — расхождение контента.** Генератор беднее шаблона. Обогащение генератора = рефакторинг `scaffold_helpers.py`. Риск: adopt-project (`project_adopter.py:304-313`) тоже вызывает генератор — нужно проверить совместимость.
2. **B4 (frontend Vite+React) — большой payload.** Меняет структуру `template-frontend/` фундаментально. Риск: `template-manifest.yaml` регистрирует `type: directory, recursive: true` — новые файлы автоматически попадут в coverage-check.
3. **Фаза A не добавляет пользовательской ценности.** A — чистка; B — ценность. Если делать A без B — разработчик не заметит разницы (шаблоны станут чище, но не богаче). Рекомендация: A+B одним заходом, не разделять.
4. **`template.yaml` (A7) без потребителя — мёртвый код.** Решение: минимальный reader в `scaffold_helpers.py` (validation version vs practices_manifest.version) — даёт немедленную ценность.
5. **Спорный момент 5 (`.env.platform` в `.gitignore`)** может сломать текущий deploy-flow, если CI/CD читает `.env.platform` из git. Нужна проверка deploy-пайплайна.

### 4.3 Что мета-вариант НЕ покрывает (явные пробелы)

1. **`docker-compose.dev.yml` для локальной разработки** — упомянут в отчёте 1 (Option B), но отсутствует в мета-варианте. Разработчику нужен postgres/redis локально для разработки backend. Решение: добавить в фазу B (B6: `docker-compose.dev.yml` с postgres+redis, `make dev` таргет).
2. **Snippets library** (отчёт 1, Option E) — переиспользование интеграционных паттернов (litellm-client, langfuse-tracing). Мета-вариант упоминает packs в фазе C, но snippets — ортогональны packs. Решение: snippets как часть `template-base/` (копируются в проект как reference, разработчик удаляет лишнее).
3. **Backend: `src/requirements.txt` vs `pyproject.toml`** — сейчас `requirements.txt` — payload, `pyproject.toml` — GENERATED (ruff/pytest только). Нет `[project].dependencies`. Решение: оставить как есть (requirements.txt — авторский, pyproject.toml — GENERATED для tooling).

---

## 5. Предлагаемая последовательность коллапса (завтра)

1. **Спорные моменты 1-7 (фаза A)** — быстрые решения, мало альтернатив.
2. **Спорный момент 11 (frontend Vite+React)** — КРИТИЧЕСКИЙ, требует обсуждения набора зависимостей.
3. **Спорные моменты 8-10, 12 (фаза B backend)** — opinionated choices, быстрые.
4. **Спорный момент 13 (фаза C)** — подтверждение «позже», без детализации.
5. **Дополнения:** B6 (docker-compose.dev.yml), snippets (размер/состав).

$END_BRIEF
