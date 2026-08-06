$START_METADEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Критический аудит и коррекция 02-DevPlan.md (составлен другой моделью). Фиксация фактических дефектов (неверные пути, пропущенный gate-блокер, несовместимость eslint 9 + .eslintrc) и свёрнутые пользователем решения. Заменяет спорные части 02-DevPlan.md; непротиворечащие секции 02 остаются в силе.
DESCRIPTION:           Мета-девплан. Структура: (1) каталог дефектов 02-DevPlan.md с вердиктами FAIL/WARN, разбитый на критические (RED-блокеры) и средние; (2) свёрнутые решения по 6 опросным вопросам (все ответы пользователя зафиксированы); (3) дельта code graph — только изменившиеся относительно 02 сущности; (4) обновлённые File Manifest и Acceptance Criteria; (5) явные указания Code-агенту «что в 02 — оставить, что — заменить». НЕ дублирует весь 02-DevPlan: читается совместно с ним.
RATIONALE:             02-DevPlan.md содержит 4 RED-блокера и 3 WARN, выявленных сверкой с реальным кодом (проверено: tests/gates/test_gate_templates_practices.py зарегистрирован в entrypoint-manifest.yaml:2220-2229 и требует файлы, которые 02 удаляет; core/internal/monitoring_config_renderer.py лежит НЕ в deploy/; eslint 9 несовместим с .eslintrc JSON). Прямое исполнение 02 = сломанный `make gate` + несобирающийся frontend. Мета-девплан даёт минимальный набор прицельных правок без переписывания 02.
ACCEPTANCE_CRITERIA:   1) `make gate MODE=fast` зелёный после всех правок (включая переработанный test_gate_templates_practices.py); 2) `make check` зелёный; 3) `make templates-check` зелёный; 4) dry-run scaffold обоих шаблонов успешен; 5) frontend `npm ci && npm run build` успешен с eslint.config.js (НЕ .eslintrc); 6) dev-compose подключается к external networks (shared-db-net/shared-cache-net), не поднимает локальные postgres/redis; 7) backend template: asyncpg/prometheus-client закомментированы в requirements.txt, db.py вынесен в snippets/.
IMPLEMENTS:            Решения пользователя по 6 опросным вопросам + аудит 02-DevPlan.md.
IMPACTS:               tests/gates/test_gate_templates_practices.py (переработка под runtime-модель), core/internal/scaffold/scaffold_helpers.py (gen_ai_platform_yaml: metrics_port frontend 3000→80, backend 8080→8000), core/entrypoint-manifest.yaml (gates секция — обновить описание переработанного гейта), templates/template-frontend/eslint.config.js (новый, flat config, вместо .eslintrc), templates/template-backend/docker-compose.dev.yml (external networks вместо локальных сервисов), templates/template-backend/src/requirements.txt (asyncpg/prometheus-client закомментированы), templates/template-backend/snippets/db.py (новое расположение).
REQUIRES:              02-DevPlan.md как базовый план; согласие с вердиктами аудита; решения по 6 вопросам приняты (см. §2).
$END_ARTIFACT_CONTRACT

---

## 0. Как читать этот документ

**Приоритет:** при расхождении 03-MetaDevPlan.md > 02-DevPlan.md. Все непротиворечащие секции 02 остаются авторитетными.

**Для Code-агента:** раздел §1 — список правок к 02 (строка → замена). Раздел §3 — изменившиеся сущности code graph. Разделы §4–§5 — обновлённые манифесты. Раздел §6 — explicit-указания «оставить/заменить».

**Вердикты:**
- 🔴 **RED-BLOCKER** — исполнение 02 без этой правки ломает `make gate`/`make check`/сборку. Обязательно.
- 🟡 **WARN** — логическая ошибка или риск регрессии, не блокирующая немедленно, но требующая исправления.
- 🟢 **OK** — секция 02 подтверждена аудитом, оставить как есть.

---

## 1. Каталог дефектов 02-DevPlan.md

### 1.1 🔴 RED-BLOCKER #1: Пропущен gate `test_gate_templates_practices.py`

**Утверждение 02 (Step 1.1, строка 162):** «Проверка gate-зависимостей: `rg "template-backend/tests|template-frontend/tests" tests/gates/` → 0 результатов. Безопасно.»

**Реальность:** grep `template-*/tests` действительно = 0, НО это половина правды. Существует `tests/gates/test_gate_templates_practices.py` (DevPlan 137 W5), зарегистрированный в `core/entrypoint-manifest.yaml:2220-2229` (4 gate-id), который проверяет:

| Тест | Требование | Что удаляет 02 |
|------|-----------|----------------|
| `test_gate_templates_contain_practices_files` | `.pre-commit-config.yaml` + `practices.lock` в обоих шаблонах; `pyproject.toml` в backend | Step 1.1 удаляет ВСЕ эти файлы |
| `test_gate_templates_practices_have_generated_header` | GENERATED-шапка в `.pre-commit-config.yaml` + `practices.lock` | Файлы удалены → шапку проверять не на чем |
| `test_gate_templates_precommit_upstream_only` | `.pre-commit-config.yaml` с upstream-only репозиториями + `project-push-check` | Файл удалён |
| `test_gate_templates_ai_platform_yaml_quality` | `ai-platform.yaml` с `quality.level=auto` в обоих шаблонах | Step 1.3 удаляет `ai-platform.yaml` |

**Итог:** после исполнения Wave 1 из 02 — минимум 4 gate-теста падают. `make gate MODE=fast` = RED. `make check` = RED (static_gate phase). DevPlan **нигде** (ни в одном из 4 wave, ни в File Manifest, ни в §5 Риски) не упоминает этот гейт.

**Доп. контекст:** `sync_practices` (`core/internal/practices/manifest.py:50-57`) через `LANGUAGE_FOR_TYPE` генерирует разный набор файлов по типам: `backend → python` (5 файлов: pyproject, .pre-commit, conftest, test_health, practices.lock), `frontend → typescript, react` (4 файла: .pre-commit, conftest, test_health, practices.lock — без pyproject). Это означает, что гейт, проверяющий одинаковый набор файлов в обоих шаблонах, концептуально расходится с моделью генерации.

**Решение (свёрнуто пользователем — Q1 = «Переработать гейт под новую модель»):** гейт `test_gate_templates_practices.py` перерабатывается из статической проверки наличия файлов в шаблонах → runtime-валидацию: `sync_practices` на шаблоне в `tmp_path` генерирует ожидаемый набор, гейт сверяет фактический output с эталоном. См. §2.1.

---

### 1.2 🔴 RED-BLOCKER #2: eslint 9 несовместим с `.eslintrc` (JSON)

**Утверждение 02 (Step 3.8, строки 737-758 + Step 3.2, package.json строка 605):** `package.json` фиксирует `"eslint": "^9.24.0"`, а `.eslintrc` (Step 3.8) — JSON-файл в старом формате eslintrc (deprecated с eslint 8.57, удалён в eslint 9).

**Реальность:** eslint 9.x требует `eslint.config.js` (flat config). `.eslintrc.*` файлы игнорируются с eslint 9 (флаг `ESLINT_USE_FLAT_CONFIG=false` deprecated). Запуск `npm run lint` (`eslint .`) из package.json упадёт с:
```
Oops! Something went wrong! ESLint: 9.x
Could not find config file ".eslintrc" (this is expected in flat config mode)
```

**Итог:** AC9 (`npm ci && npm run build` успешен) пройдёт (build не запускает lint), НО `npm run lint` сломан, pre-commit hook (если добавит eslint) упадёт, разработчик получит неработающий lint в шаблоне.

**Решение (свёрнуто пользователем — Q5 = «Свежие версии + flat config»):** оставить версии React 19/Vite 6/TS 5.8/eslint 9, НО заменить `.eslintrc` → `eslint.config.js` (flat config). См. §2.5.

---

### 1.3 🟡 WARN #1: Неверный путь `monitoring_config_renderer.py`

**Утверждение 02 (строка 460):** «Проверка `monitoring_config_renderer.py`: default `metrics_port=3000` (строка 123)» — подразумевает путь `core/internal/deploy/monitoring_config_renderer.py`.

**Реальность:** файл лежит в `core/internal/monitoring_config_renderer.py` (НЕ в `deploy/`). Проверено: `find core/ -name "monitoring_config_renderer.py"` → `core/internal/monitoring_config_renderer.py`. Default `metrics_port=3000` (строка 123) — правда.

**Итог:** некритично (DevPlan не редактирует этот файл), но цитирование несуществующего пути — артефакт галлюцинации. Code-агент, пытающийся «проверить» по указанному пути, не найдёт файл.

**Решение:** в §2.3 указан правильный путь; больше ничего не требуется.

---

### 1.4 🟡 WARN #2: Frontend `metrics_port=3000` — мёртвое значение

**Утверждение 02:** меняет только backend `metrics_port` 8080→8000 (Step 2.2), frontend остаётся 3000 (`gen_ai_platform_yaml` строка 168). При этом nginx (frontend) слушает порт 80 (`templates/template-frontend/nginx/default.conf:24`).

**Реальность:** для frontend `metrics=false` (строка 165), поэтому `metrics_port=3000` не используется рендерером (`monitoring_config_renderer.py:338` — `metrics_port` берётся из `merged.get(...)`, но если `metrics=false`, scrape-конфиг не генерируется). Технически работает, но «мёртвое» значение 3000 при реальном порту 80 — conceptual drift.

**Решение (свёрнуто пользователем — Q2 = «Синхронизировать frontend→80»):** `gen_ai_platform_yaml` для frontend: `metrics_port: 3000 → 80`. Привести к реальному порту сервиса даже при `metrics=false` — меньше путаницы. См. §2.3.

---

### 1.5 🟡 WARN #3: `docker-compose.dev.yml` поднимает локальные postgres/redis

**Утверждение 02 (Step 2.5, строки 527-564):** dev-compose поднимает `postgres:17-alpine` + `redis:7-alpine` локально (новые контейнеры).

**Реальность:** платформа уже предоставляет `shared-db-net` (postgres-shared) и `shared-cache-net` (redis-shared) для production-стека. Локальный dev-compose дублирует сервисы, которые в prod — external networks. Это создаёт:
- Дрейф версий: dev postgres 17-alpine vs prod `pgbouncer` (из `platform-env.yaml:138-139`).
- Несовпадение DSN: `.env.platform` указывает на `postgres-shared`, а dev-compose поднимает `postgres` на localhost — разработчик должен руками менять DSN.

**Решение (свёрнуто пользователем — Q3 = «External networks к платформе»):** dev-compose подключается к платформенному стеку через external networks. См. §2.6.

---

### 1.6 🟡 WARN #4: asyncpg — обязательная зависимость всех backend-проектов

**Утверждение 02 (Step 2.4 + B3):** добавляет `asyncpg` в `requirements.txt` template-backend + `src/db.py` + закомментированный endpoint в `main.py`.

**Реальность:** не все backend-проекты используют PostgreSQL (worker, ClickHouse-only, Redis-only). `asyncpg` как обязательная зависимость в шаблоне — opinionated. Плюс `prometheus-client`: `/metrics` добавляется всегда (Step 2.2), но не всем нужен prometheus.

**Решение (свёрнуто пользователем — Q4 = «Закомментировать в requirements»):** asyncpg + prometheus-client закомментированы в `requirements.txt` с пометкой «раскомментируйте при необходимости». `db.py` → `snippets/db.py` (не в `src/`). См. §2.4.

---

### 1.7 🟢 OK: Проверенные утверждения 02

Подтверждены аудитом (оставить как есть):
- `gen_project_practices` вызывает `sync_practices(force=True)` — подтверждено (`project_scaffolder.py:343`).
- `gen_project_makefile`/`gen_project_agents` вызываются с `force=False` в scaffolder (`project_scaffolder.py:681, 690`) — подтверждено.
- `{{PROJECT_NAME}}` валиден в strict regex (`template_engine.py:34`, UPPER_SNAKE) — подтверждено.
- `platform_domain` не читается scaffold-логикой из `ai-platform.yaml` (только из env/CLI) — подтверждено (`vhost_renderer.py` читает из `os.environ.get("PLATFORM_DOMAIN")`).
- `.ruff_cache/` закоммичен в backend — подтверждено (`templates/template-backend/.ruff_cache/0.16.1/`).
- `{{DATABASE}}` в README backend не резолвится (не в vars) — подтверждено (`project_scaffolder.py:242-251`).
- `test_health.py` и `conftest.py` идентичны между backend и frontend (`diff` = 0) — подтверждено.

---

## 2. Свёрнутые решения (ответы пользователя на опрос)

### 2.1 [Q1] Переработка gate `test_gate_templates_practices.py`

**Было (статическая проверка):** гейт проверяет наличие файлов `.pre-commit-config.yaml`, `practices.lock`, `pyproject.toml`, `ai-platform.yaml` в директориях шаблонов + GENERATED-шапки + `quality.level=auto`.

**Стало (runtime-валидация):** гейт проверяет, что `sync_practices` на шаблоне в `tmp_path` генерирует ожидаемый набор файлов с правильным содержимым.

**Новый гейт (заменяет 4 теста в `test_gate_templates_practices.py`):**

```python
# GREP_SUMMARY: gate templates-practices runtime sync_practices expected-files LANGUAGE_FOR_TYPE
# STRUCTURE: ▶ ┌tmp_path scaffold┐ → ◇ sync_practices → ◇ expected set by LANGUAGE_FOR_TYPE → ⎋ assert

@pytest.mark.gate
def test_gate_templates_practices_sync_generates_expected_files(tmp_path) -> None:
    """sync_practices на свежем проекте из шаблона генерирует ожидаемый набор файлов.

    Новая модель (DevPlan 141): шаблоны НЕ хранят практики-файлы как образцы —
    sync_practices — единственный источник. Гейт валидирует, что генератор
    покрывает ожидаемый набор по LANGUAGE_FOR_TYPE.
    """
    from core.internal.practices.manifest import LANGUAGE_FOR_TYPE
    from core.internal.practices.sync_practices import sync_practices

    EXPECTED_BY_LANG = {
        "python": {"pyproject.toml", ".pre-commit-config.yaml",
                   "tests/conftest.py", "tests/test_health.py", "practices.lock"},
        "typescript,react": {".pre-commit-config.yaml",
                             "tests/conftest.py", "tests/test_health.py", "practices.lock"},
    }

    for ptype, langs in LANGUAGE_FOR_TYPE.items():
        if ptype not in ("backend", "frontend"):
            continue
        lang_key = ",".join(langs)
        expected = EXPECTED_BY_LANG.get(lang_key)
        if not expected:
            continue

        # Создаём минимальный проект в tmp_path
        project_dir = tmp_path / f"test-{ptype}"
        project_dir.mkdir()
        (project_dir / "ai-platform.yaml").write_text(
            f"name: test-{ptype}\ntype: {ptype}\ntarget_node: test\n"
            "quality:\n  level: auto\n"
        )

        report = sync_practices(project_dir, force=True)

        actual = set()
        for p in project_dir.rglob("*"):
            if p.is_file():
                actual.add(str(p.relative_to(project_dir)))

        missing = expected - actual
        extra = actual - expected

        assert not missing, f"{ptype}: sync_practices не сгенерировал: {missing}"
        assert not extra, f"{ptype}: sync_practices сгенерировал лишнее: {extra}"

        # Проверка GENERATED-шапки во всех сгенерированных файлах (кроме practices.lock)
        for rel in expected - {"practices.lock"}:
            content = (project_dir / rel).read_text(encoding="utf-8")
            assert GENERATED_HEADER in content[:200], f"{ptype}/{rel}: нет GENERATED-шапки"
```

**Удалить из гейта (устаревают):**
- `test_gate_templates_contain_practices_files` — статическая проверка наличия.
- `test_gate_templates_practices_have_generated_header` — статическая проверка шапки (переезжает в runtime-гейт).
- `test_gate_templates_precommit_upstream_only` — переезжает в runtime: проверять что `.pre-commit-config.yaml` из `sync_practices` не содержит `core/entrypoints`/`hooks/hygiene.sh`/`hooks/commit_msg.sh` + содержит `pre-commit-hooks` + `project-push-check`.
- `test_gate_templates_ai_platform_yaml_quality` — становится бессмысленным (шаблон не имеет `ai-platform.yaml`, генератор создаёт). **Удалить полностью** — генератор `gen_ai_platform_yaml` уже проверяется своим набором тестов.

**Inventory-процедура (tests/AGENTS.md §Удаление тестов):** перед удалением тестов — запись в `tests/test_inventory_changes.yaml` секция `removed:` (3-4 nodeid, reason: «DevPlan 141: переработан под runtime-модель»), затем `make test-inventory-sync`.

**`entrypoint-manifest.yaml`:** обновить `description` для 4 gate-id (строки 2219-2229): указать новую runtime-семантику. Имена gate-id оставить (стабильные).

---

### 2.2 [Q5] Frontend: свежие версии + flat config

**Версии (подтверждены, оставить из 02):**
- React 19.1.0, react-dom 19.1.0
- Vite 6.3.0
- TypeScript 5.8.0
- eslint 9.24.0
- @vitejs/plugin-react 4.4.0

**Заменить (`.eslintrc` → `eslint.config.js`):**

Удалить `templates/template-frontend/.eslintrc` (создавался в Step 3.8 из 02).

Создать `templates/template-frontend/eslint.config.js`:
```javascript
// eslint.config.js — flat config (eslint 9+)
// Static payload (NOT GENERATED) — project-specific, copy as-is
import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default tseslint.config(
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': 'warn',
      'no-console': 'off',
    },
  },
)
```

**Обновить `package.json` (доп. devDependencies для flat config):**
```json
{
  "devDependencies": {
    "@eslint/js": "^9.24.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.4.0",
    "eslint": "^9.24.0",
    "eslint-plugin-react-hooks": "^5.2.0",
    "eslint-plugin-react-refresh": "^0.4.0",
    "globals": "^16.0.0",
    "typescript": "~5.8.0",
    "typescript-eslint": "^8.30.0",
    "vite": "^6.3.0"
  }
}
```

**Обновить File Manifest 02:** `.eslintrc` (создаваемый) → удалить из списка; `eslint.config.js` → добавить в создаваемые (W3).

**TRAP[DEBT] `generators.py:368`:** удалить (как в 02 Step 3.11) — подтверждено, tsconfig/eslint теперь статичный payload.

---

### 2.3 [Q2] metrics_port синхронизация frontend→80

**`scaffold_helpers.py:gen_ai_platform_yaml` (строки 163-180 02 → реальный файл `core/internal/scaffold/scaffold_helpers.py`):**

Было:
```python
if ptype == "frontend":
    mon_config = {"metrics": False, "metrics_port": 3000, ...}
else:  # backend
    mon_config = {"metrics": True, "metrics_port": 8080, ...}
```

Стало:
```python
if ptype == "frontend":
    mon_config = {"metrics": False, "metrics_port": 80, ...}  # nginx реальный порт
else:  # backend
    mon_config = {"metrics": True, "metrics_port": 8000, ...}  # FastAPI реальный порт
```

**Проверка:** `monitoring_config_renderer.py:338` (`core/internal/monitoring_config_renderer.py`, НЕ `deploy/`) — `metrics_port=int(merged.get("metrics_port", 3000))`. Для frontend `metrics=false` → scrape-конфиг не генерируется → изменение 3000→80 безопасно. Для backend `metrics=true` → Prometheus будет скрейпить `:8000/metrics` (было `:8080`, но 8080 не соответствовало реальному порту FastAPI 8000 — это был отдельный баг, теперь исправлен).

---

### 2.4 [Q4] asyncpg/prometheus-client — закомментированы в requirements

**`templates/template-backend/src/requirements.txt` (заменить Step 2.4 из 02):**

```text
# Core (обязательно)
fastapi>=0.115
uvicorn[standard]>=0.34
pydantic-settings>=2.0
python-dotenv>=1.0

# Опционально — раскомментируйте при необходимости:
# ── PostgreSQL (PLATFORM_POSTGRES_DSN) — см. snippets/db.py ──
# asyncpg>=0.30

# ── Prometheus metrics (/metrics endpoint) — см. snippets в main.py ──
# prometheus-client>=0.21

# ── Redis (PLATFORM_REDIS_URL) ──
# redis[hiredis]>=5.0
```

**`templates/template-backend/src/main.py` (заменить Step 2.2 из 02):**

`/metrics` оставить как есть (заглушка `{"status": "OK"}`), НО добавить комментарий со ссылкой на snippets:
```python
@app.get("/metrics")
async def metrics() -> dict[str, str]:
    """Metrics endpoint. Для Prometheus — см. snippets/metrics_prometheus.py."""
    return {"status": "OK", "metrics": "see snippets/metrics_prometheus.py"}
```

**Удалить из 02:**
- Step 2.1 (`src/config.py` через pydantic-settings) — **ОСТАВИТЬ** (config.py полезен, pydantic-settings в обязательных зависимостях).
- Step 2.2 (`/metrics` через prometheus_client) — **ЗАМЕНИТЬ** (см. выше, prometheus закомментирован).
- Step 2.3 (`src/db.py` + endpoint) — **ПЕРЕНЕСТИ** в `snippets/db.py`.
- Step 2.4 (обновить requirements) — **ЗАМЕНИТЬ** на версию выше.

**Создать `templates/template-backend/snippets/`:**
- `snippets/db.py` — содержимое Step 2.3 из 02 (asyncpg pool).
- `snippets/metrics_prometheus.py` — `make_asgi_app()` mount + пример counter.
- `snippets/README.md` — таблица «файл → сервис → как подключить».

---

### 2.5 [Q3] docker-compose.dev.yml — external networks к платформе

**Заменить Step 2.5 из 02 (строки 527-564).**

**`templates/template-backend/docker-compose.dev.yml`:**
```yaml
# docker-compose.dev.yml — локальная разработка против платформенного стека
# Usage:
#   1. В ai-platform: make up (поднимает postgres-shared, redis-shared)
#   2. Здесь: docker compose -f docker-compose.dev.yml up -d
# Подключается к external networks платформы (shared-db-net, shared-cache-net).
# В .env.platform PLATFORM_POSTGRES_HOST=postgres-shared, PLATFORM_REDIS_HOST=redis-shared.

services:
  # Development app — запускает uvicorn с reload, подключается к платформенным сервисам
  app:
    build: .
    image: {{PROJECT_NAME}}:dev
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    env_file: .env.platform
    volumes:
      - ./src:/app/src
    ports:
      - "8000:8000"
    networks:
      - shared-db-net
      - shared-cache-net
    depends_on: []  # платформенные сервисы уже запущены

networks:
  shared-db-net:
    external: true
  shared-cache-net:
    external: true
```

**`make dev` таргет в `gen_project_makefile` (Step 1.4a 02):** оставить, но обновить echo-сообщение:
```makefile
dev:
	@echo "Starting dev app against platform services (requires 'make up' in ai-platform)..."
	docker compose -f docker-compose.dev.yml up -d
	@echo "Dev app ready on http://localhost:8000. Ensure .env.platform is synced (make sync-env)."
```

**Важно:** внешние сети `shared-db-net`, `shared-cache-net` создаются платформенным `docker compose up` (`platform-env.yaml:networks`). Без запущенного стека платформы `docker compose -f docker-compose.dev.yml up` упадёт с «network not found». Это документировано в комментариях + `make dev` echo.

---

### 2.6 Сводная таблица свёрнутых решений

| Q | Решение | Влияние на 02 |
|---|---------|---------------|
| Q1 | Переработать gate в runtime-модель | Step 1.1 дополняется; новый гейт-файл; inventory-процедура |
| Q2 | frontend metrics_port 3000→80 | Step 2.2 расширяется (2 правки в gen_ai_platform_yaml) |
| Q3 | dev-compose external networks | Step 2.5 полностью заменяется |
| Q4 | asyncpg/prometheus закомментированы | Step 2.2, 2.3, 2.4 заменяются; добавляется snippets/ |
| Q5 | eslint.config.js (flat config) | Step 3.8 заменяется; package.json обновляется |
| Q6 | Полный мета-девплан | Настоящий документ |

---

## 3. Дельта Code Graph (изменившиеся сущности)

Только сущности, изменившиеся относительно §1 Draft Code Graph из 02. Неперечисленные — остаются как в 02.

```xml
<code_graph>
  <!-- ИЗМЕНИЛОСЬ: gate templates_practices — runtime вместо static -->
  <entity id="gate_templates_practices" type="test" file="tests/gates/test_gate_templates_practices.py" status="modified">
    <change>4 static-теста → 1 runtime-тест (sync_practices на tmp_path)</change>
    <remove_tests>
      test_gate_templates_contain_practices_files
      test_gate_templates_practices_have_generated_header
      test_gate_templates_precommit_upstream_only
      test_gate_templates_ai_platform_yaml_quality
    </remove_tests>
    <add_tests>
      test_gate_templates_practices_sync_generates_expected_files (runtime, R5 negative)
    </add_tests>
  </entity>

  <!-- ИЗМЕНИЛОСЬ: gen_ai_platform_yaml — обе правки metrics_port -->
  <entity id="scaffold_helpers_gen_ai_platform_yaml" type="function" file="core/internal/scaffold/scaffold_helpers.py" status="modified">
    <change>frontend metrics_port: 3000 → 80 (nginx реальный порт)</change>
    <change>backend metrics_port: 8080 → 8000 (FastAPI реальный порт)</change>
  </entity>

  <!-- НОВОЕ: eslint.config.js (flat config, заменяет .eslintrc) -->
  <entity id="template_frontend_eslint_config" type="file" status="new">
    <path>templates/template-frontend/eslint.config.js</path>
    <purpose>eslint 9 flat config (НЕ .eslintrc — несовместим с eslint 9)</purpose>
  </entity>

  <!-- УДАЛЕНО из 02: .eslintrc -->
  <entity id="template_frontend_eslintrc_REMOVED" type="file" status="removed">
    <note>02 Step 3.8 создавал .eslintrc — УДАЛЕНО, заменено на eslint.config.js</note>
  </entity>

  <!-- ИЗМЕНИЛОСЬ: requirements.txt — asyncpg/prometheus закомментированы -->
  <entity id="template_backend_requirements" type="file" status="modified">
    <path>templates/template-backend/src/requirements.txt</path>
    <change>pydantic-settings — обязательно; asyncpg, prometheus-client, redis — закомментированы</change>
  </entity>

  <!-- ПЕРЕНЕСЕНО: db.py из src/ в snippets/ -->
  <entity id="template_backend_snippets_db" type="file" status="new">
    <path>templates/template-backend/snippets/db.py</path>
    <purpose>asyncpg pool — reference, копируется разработчиком при необходимости (бывший src/db.py из 02)</purpose>
  </entity>
  <entity id="template_backend_snippets_metrics" type="file" status="new">
    <path>templates/template-backend/snippets/metrics_prometheus.py</path>
    <purpose>prometheus_client make_asgi_app — reference</purpose>
  </entity>
  <entity id="template_backend_snippets_readme" type="file" status="new">
    <path>templates/template-backend/snippets/README.md</path>
    <purpose>таблица файлов snippets ↔ сервисов</purpose>
  </entity>
  <!-- ВНИМАНИЕ: src/db.py из 02 (entity template_backend_src_db) — УДАЛЁН из создаваемых -->

  <!-- ИЗМЕНИЛОСЬ: docker-compose.dev.yml — external networks -->
  <entity id="template_backend_dev_compose" type="file" status="modified_vs_02">
    <path>templates/template-backend/docker-compose.dev.yml</path>
    <purpose>app-service против платформенных external networks (shared-db-net, shared-cache-net), НЕ локальные postgres/redis</purpose>
  </entity>

  <!-- ИЗМЕНИЛОСЬ: package.json — доп. devDeps для flat config -->
  <entity id="template_frontend_package" type="file" status="modified_vs_02">
    <path>templates/template-frontend/package.json</path>
    <add_devdeps>@eslint/js, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals, typescript-eslint</add_devdeps>
  </entity>

  <!-- ИЗМЕНИЛОСЬ: main.py — /metrics остаётся заглушкой + ссылка на snippets -->
  <entity id="template_backend_src_main" type="file" status="modified">
    <path>templates/template-backend/src/main.py</path>
    <change>/metrics — заглушка (НЕ prometheus_client mount); комментарий → snippets/metrics_prometheus.py</change>
    <note>02 Step 2.2 монтировал prometheus_client — УБРАНО (prometheus в закомментированных deps)</note>
  </entity>

  <!-- ИЗМЕНИЛОСЬ: entrypoint-manifest gates — обновить description переработанного гейта -->
  <entity id="entrypoint_manifest_gates_descriptions" type="manifest" file="core/entrypoint-manifest.yaml" status="modified">
    <lines>2219-2229</lines>
    <change>description для 4 gate-id templates_* — указать runtime-семантику (auto-discover имён сохраняется)</change>
  </entity>
</code_graph>
```

---

## 4. Обновлённый File Manifest (дельта относительно 02 §3)

### Удаляемые файлы — дополнения к 02

| # | Файл | Волна | Причина |
|---|------|-------|---------|
| 24 | `templates/template-frontend/.eslintrc` (если существует) | W3 | RED-BLOCKER #2: eslint 9 несовместим с .eslintrc |

### Создаваемые файлы — замены/дополнения к 02

| # | Файл | Волна | Изменение относительно 02 |
|---|------|-------|---------------------------|
| A | `templates/template-frontend/eslint.config.js` | W3 | **ЗАМЕНА** `.eslintrc` (flat config для eslint 9) |
| B | `templates/template-backend/snippets/db.py` | W2 | **ПЕРЕНОС** из `src/db.py` (бывшая сущность 02 #8) |
| C | `templates/template-backend/snippets/metrics_prometheus.py` | W2 | **НОВОЕ** (reference для /metrics) |
| D | `templates/template-backend/snippets/README.md` | W2 | **НОВОЕ** (карта snippets) |

### Создаваемые файлы — УДАЛЕНЫ из списка 02

| # | Файл | Причина удаления из создаваемых |
|---|------|---------------------------------|
| ~~8~~ | ~~`templates/template-backend/src/db.py`~~ | Перенесён в `snippets/db.py` (Q4) |

### Модифицируемые файлы — коррекции к 02

| # | Файл | Изменение | Коррекция 02 |
|---|------|-----------|--------------|
| 2 | `core/internal/scaffold/scaffold_helpers.py` | `gen_ai_platform_yaml`: metrics_port frontend 3000→80 **И** backend 8080→8000 | 02 указывал только backend |
| 6 | `templates/template-backend/src/requirements.txt` | pydantic-settings обязательно; asyncpg, prometheus-client, redis — **закомментированы** | 02 добавлял как обязательные |
| 7 | `templates/template-backend/src/main.py` | `/metrics` остаётся заглушкой + ссылка на snippets | 02 монтировал prometheus_client |
| 8 | `templates/template-backend/docker-compose.dev.yml` | **external networks** (shared-db-net, shared-cache-net), НЕ локальные сервисы | 02 поднимал postgres+redis локально |
| NEW | `tests/gates/test_gate_templates_practices.py` | **Переработка**: 4 static-теста → 1 runtime-тест | 02 не упоминал |
| NEW | `tests/test_inventory_changes.yaml` | Запись в `removed:` 4 nodeid (inventory-процедура) | 02 не упоминал |
| NEW | `core/entrypoint-manifest.yaml` | `description` для 4 gate-id (строки 2219-2229) | 02 не упоминал |
| NEW | `templates/template-frontend/package.json` | доп. devDeps: @eslint/js, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals, typescript-eslint | 02 не включал flat-config deps |

---

## 5. Обновлённые Acceptance Criteria (дельта)

| AC | Критерий | Проверка | Коррекция 02 |
|----|----------|----------|--------------|
| AC5 (изм.) | Переработанный gate `test_gate_templates_practices` (runtime) проходит | `pytest tests/gates/test_gate_templates_practices.py` PASS | 02: проверял .env.example; теперь: runtime sync_practices |
| AC7 (изм.) | Backend `/metrics` отдаёт заглушку + ссылку на snippets | `curl /metrics` → `{"status":"OK","metrics":"see snippets/..."}` | 02: ожидал prometheus-метрики |
| AC8 (изм.) | `docker compose -f docker-compose.dev.yml up` подключается к external networks | Предварительно `make up` в ai-platform; dev-app видит postgres-shared | 02: поднимал локальные postgres+redis |
| AC9 (изм.) | Frontend `npm ci && npm run build && npm run lint` успешен | Все 3 команды без ошибок; eslint flat config работает | 02: только build (lint сломан с .eslintrc) |
| NEW AC16 | asyncpg НЕ устанавливается при `pip install -r src/requirements.txt` (закомментирован) | `pip install -r src/requirements.txt` не тянет asyncpg | — |
| NEW AC17 | `snippets/db.py` содержит asyncpg pool-reference | Файл существует, документирован | — |
| NEW AC18 | `eslint.config.js` существует, `.eslintrc` отсутствует | `ls templates/template-frontend/eslint.config.js` OK; `.eslintrc` нет | — |
| NEW AC19 | `test_inventory_changes.yaml` содержит 4 removed-записи | `grep -c "test_gate_templates" tests/test_inventory_changes.yaml` ≥ 4 | — |
| NEW AC20 | `gen_ai_platform_yaml` frontend: metrics_port=80 | Сгенерированный yaml содержит `metrics_port: 80` для frontend | — |

---

## 6. Указания Code-агенту: «оставить / заменить»

### Оставить из 02 без изменений (🟢 OK)

- **Wave 1 Step 1.1** (удаление `.pre-commit-config.yaml`, `practices.lock`, `pyproject.toml`, `tests/` из шаблонов) — ПОДТВЕРЖДЕНО. **НО** добавить шаг: переработать гейт (см. §2.1) — иначе RED.
- **Wave 1 Step 1.2** (`.env.platform` удаление + `.env.example` создание) — ПОДТВЕРЖДЕНО.
- **Wave 1 Step 1.3** (удаление `ai-platform.yaml` из шаблонов) — ПОДТВЕРЖДЕНО. `platform_domain` НЕ добавлять (legacy).
- **Wave 1 Step 1.4** (обогащение `gen_project_makefile`/`gen_project_agents`, force=True scaffolder, force=False adopter) — ПОДТВЕРЖДЕНО.
- **Wave 1 Step 1.5** (`.gitignore`) — ПОДТВЕРЖДЕНО. Включая `.env.platform` в `.gitignore`.
- **Wave 1 Step 1.6** (удаление `{{DATABASE}}` из README backend) — ПОДТВЕРЖДЕНО.
- **Wave 1 Step 1.7** (`template.yaml` + reader) — ПОДТВЕРЖДЕНО.
- **Wave 1 Step 1.8** (`template-manifest.yaml` актуализация + `make templates-check`) — ПОДТВЕРЖДЕНО.
- **Wave 2 Step 2.1** (`src/config.py` через pydantic-settings) — ПОДТВЕРЖДЕНО. pydantic-settings в обязательных deps.
- **Wave 3 Steps 3.1–3.7, 3.9–3.11** (Vite+React+TS, кроме eslint-файла) — ПОДТВЕРЖДЕНО. Конкретные версии React 19/Vite 6/TS 5.8.
- **Wave 4 Step 4.1** (README переработка) — ПОДТВЕРЖДЕНО. Адаптировать под snippets/ вместо src/db.py.
- **Wave 4 Step 4.2** (`test_gate_env_example_template.py`) — ПОДТВЕРЖДЕНО.
- **Wave 4 Step 4.3** (`template-manifest.yaml` `{{PROJECT_NAME}}` в JSON) — ПОДТВЕРЖДЕНО. Валиден.
- **Wave 4 Step 4.4** (финальная верификация) — ПОДТВЕРЖДЕНО.

### Заменить (🔴🟡)

| Шаг 02 | Что заменить | Чем (ссылка на 03) |
|--------|--------------|---------------------|
| Wave 1 Step 1.1 | Добавить переработку gate | §2.1 (новый runtime-гейт + inventory-процедура) |
| Wave 2 Step 2.2 | `/metrics` через prometheus_client | Заглушка + ссылка на snippets (§2.4) |
| Wave 2 Step 2.3 | `src/db.py` + закомментированный endpoint | Перенос в `snippets/db.py` (§2.4) |
| Wave 2 Step 2.4 | requirements с обязательным asyncpg | Закомментированные опциональные deps (§2.4) |
| Wave 2 Step 2.5 | Локальные postgres/redis в dev-compose | External networks к платформе (§2.5) |
| Wave 3 Step 3.2 | package.json без flat-config deps | Доп. devDeps для eslint flat config (§2.2) |
| Wave 3 Step 3.8 | `.eslintrc` (JSON) | `eslint.config.js` (flat config) (§2.2) |
| — (новое) | `gen_ai_platform_yaml` frontend metrics_port | 3000→80 (§2.3) |

### Выполнить дополнительно (не в 02)

1. **Inventory-процедура** перед удалением 4 тестов из `test_gate_templates_practices.py`: запись в `tests/test_inventory_changes.yaml`, `make test-inventory-sync`.
2. **`entrypoint-manifest.yaml`**: обновить `description` для 4 gate-id (строки 2219-2229) — `make generate-entrypoint-manifest` пересоберёт, но описание лучше руками.
3. **R5 negative-тест** для нового runtime-гейта: проверить, что при сломанном `LANGUAGE_FOR_TYPE` (например, убрать тип) гейт падает (Test Honesty R5).

---

## 7. Риски и митигации (дополнение к 02 §5)

| Риск | Вероятность | Митигация |
|------|------------|-----------|
| 🔴 Переработанный гейт `test_gate_templates_practices` теряет защиту от копи-паста шаблонов | Средняя | Runtime-тест покрывает генерацию; дополнительный статический гейт на `sync_practices` output (GENERATED-шапка) сохраняет drift-детект |
| 🔴 `eslint.config.js` flat config имеет конфликты с typescript-eslint 8.x | Низкая | Версии из §2.2 проверены совместимостью; `npm run lint` в AC9 — защита |
| 🟡 External networks dev-compose требует запущенного `make up` платформы | Средняя | Документировано в комментариях + `make dev` echo; fallback: разработчик может поменять на локальные сервисы |
| 🟡 Inventory-процедура забыта — гейт `test_gate_test_inventory` падает | Высокая | §6 п.1 — обязательный шаг; AC19 проверяет наличие записей |
| 🟡 `prometheus-client` закомментирован, но `/metrics` отдаёт JSON-заглушку — несоответствие с `metrics=true` в ai-platform.yaml | Низкая | `monitoring_config_renderer` скрейпит `/metrics`; JSON вместо prometheus-format = метрики не парсятся. Документировать: для production `/metrics` нужно реализовать через snippets/metrics_prometheus.py. ИЛИ: для backend оставить prometheus-client обязательным, а опциональным сделать только asyncpg/redis |

⚠️ **TRAP[RISK-5]** · Последний риск (🟡 metrics заглушка vs `metrics=true`) требует решения: либо prometheus-client возвращается в обязательные deps для backend (тогда §2.4 корректируется — prometheus обязателен, asyncpg/redis опциональны), либо `gen_ai_platform_yaml` ставит `metrics=false` по умолчанию для backend-template, а `/metrics` через prometheus — snippets. **Рекомендация:** prometheus-client вернуть в обязательные deps (метрики — часть платформенного контракта monitoring). Оставляю финальное решение за исполнителем — это ортогональная корректировка к Q4.

---

## 8. Что НЕ входит в этот мета-девплан (явные границы)

1. **Переписывание 02-DevPlan.md целиком.** 03 — патч-оверлей; 02 остаётся базой (непротиворечащие секции).
2. **Фаза C** (композируемые слои). Как в 02 — отложена по триггеру «3-й шаблон».
3. **Snippets library как формальный packs-механизм.** `snippets/` в template-backend — неформальный reference, копируется руками. Формализация — фаза C.
4. **Решение TRAP[RISK-5]** (prometheus обяз vs опц). Зафиксирован как риск; исполнитель принимает финальное решение.

$END_METADEVPLAN
