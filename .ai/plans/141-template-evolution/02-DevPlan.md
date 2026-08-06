$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Эволюция шаблонов проектов (template-backend, template-frontend) и scaffold-механики — устранение дрейфа GENERATED-дублей (фаза A), наполнение реальными контент-паттернами (фаза B), закладка фундамента под композируемые слои (фаза C — отложена).
DESCRIPTION:           DevPlan синтезирует решения по 13 спорным моментам из брифа, организует работу в 4 волны: Wave 1 — чистка шаблонов + обогащение генераторов (A1–A8), Wave 2 — backend контент-паттерны (B1–B3 + B6: config.py, metrics, db.py, dev compose), Wave 3 — frontend SPA toolkit (B4: Vite+React+TS), Wave 4 — документация + gate-тесты + финальная верификация (B5 + гейты). Фаза C (композируемые слои + packs) — по триггеру «3-й шаблон».
RATIONALE:             Бриф выявил 7 из ~15 файлов в шаблонах как GENERATED-дубли, затираемые при scaffold — мёртвый груз + риск дрейфа от канона. Фаза A устраняет дубли, делает генераторы единственным источником. Фаза B доставляет «переиспользование придуманных решений»: config.py, db.py, /metrics, Vite+React, README-гайд. A+B одним заходом — A без B не даёт пользовательской ценности (разработчик не заметит разницы). Соблюдены инварианты 11 (generated files), 4 (AGENTS.md), 9 (test server recreateable), 1 (Makefile — единый фасад).
ACCEPTANCE_CRITERIA:   1) `make new-project NAME=test-foo TEMPLATE=backend --dry-run` успешен с новым генератором; 2) `make new-project NAME=test-bar TEMPLATE=frontend --dry-run` успешен; 3) `make templates-check` зелёный; 4) `make gate MODE=fast` зелёный (все существующие гейты); 5) Новый gate test_gate_env_example_template сверяет .env.example с platform-env.yaml#provides; 6) Проект из обновлённого backend-шаблона: /health + /metrics рабочие, config.py читает PLATFORM_* переменные; 7) Проект из обновлённого frontend-шаблона: npm ci && npm run build успешен, Dockerfile собирается.
IMPLEMENTS:            Brief 141 — 01-Brief.md (синтез двух отчетов суперповерхности)
IMPACTS:               templates/template-backend/ (+7 новых, −9 удалённых файлов), templates/template-frontend/ (+9 новых, −12 удалённых файлов), core/internal/scaffold/scaffold_helpers.py (обогащение gen_project_makefile, gen_ai_platform_yaml metrics_port, template.yaml reader), core/internal/scaffold/project_scaffolder.py (force=True для makefile/agents), core/internal/practices/generators.py (удаление TRAP[DEBT]:368 — tsconfig/eslintrc становятся статичным payload), core/templates/template-manifest.yaml (актуализация), tests/gates/test_gate_env_example_template.py (новый gate)
REQUIRES:              Коллапсированные решения по 13 спорным моментам (выполнено в настоящем DevPlan); доступ к ghcr.io для L1-сборки (smoke-верификация); Node.js 22+ для frontend-сборки.
$END_ARTIFACT_CONTRACT

---

## 0. Решения по спорным моментам (коллапс)

| # | Суть | Решение | Обоснование |
|---|------|---------|-------------|
| 1 | Удаление `tests/` из шаблонов | **A1-a: удалить полностью** | `sync_practices(force=True)` перетирает шаблонные копии; gate-тесты НЕ ссылаются на template-backend/tests (grep 0 результатов); tests/ создаёт генератор — шаблон не дублирует |
| 2 | `.env.example` — статика или генерация? | **A2-a: статичный payload + gate** | Статика проще, gate-тест сверяет с `platform-env.yaml#provides` — дрейф детектится на CI |
| 3 | `platform_domain` в генераторе ai-platform.yaml? | **НЕ добавлять** | `platform_domain` — legacy-поле, не читается scaffold-логикой (`project_scaffolder.py`, `project_adopter.py`, `project_yaml.py`); шаблонный `{{PLATFORM_DOMAIN}}` удаляется вместе с `ai-platform.yaml` |
| 4 | `force=True` для генераторов в scaffolder vs adopter | **Разделить: force=True для scaffolder, force=False для adopter** | Новый проект получает канонический Makefile/AGENTS.md (генератор — SoT); adopt-project сохраняет пользовательские правки |
| 5 | `.env.platform` в `.gitignore`? | **Да** | После `make sync-env` с credentials `.env.platform` содержит `PLATFORM_POSTGRES_DSN` с реальным паролем — коммитить нельзя; CI не читает `.env.platform` из git (генерируется `gen_env_platform` на VPS при receive); `.env.example` остаётся в git как reference |
| 6 | `template.yaml` — формат и потребитель | **A7-a: файл + минимальный reader** | `template.yaml` (name, version, requires_practices_version); reader в `scaffold_helpers.py` валидирует version vs practices_manifest.version — немедленная ценность (дрейф-детект) + фундамент для фазы C |
| 7 | `templates-check` после удаления файлов | **Прогнать в Wave 1 step 8** | Манифест регистрирует директории `type: directory, recursive: true` — `{{UPPER_SNAKE}}`-плейсхолдеры в удалённых файлах больше не проверяются |
| 8 | `pydantic-settings` vs `python-dotenv` | **B1-b: pydantic-settings (BaseSettings)** | Типобезопасный конфиг, валидация, default-значения; best-practice для FastAPI; добавляется в `requirements.txt` как payload |
| 9 | `/metrics` на том же порту или отдельном? | **B2-a: тот же порт (8000)** | Упрощает compose (один healthcheck-порт); `metrics_port` в генераторе → 8000 (было 8080); `monitoring_config_renderer.py` default=3000 — изменение 8080→8000 не ломает рендерер |
| 10 | Пример БД — asyncpg vs psycopg vs SQLAlchemy | **B3-a: asyncpg + pool в `src/db.py` + закомментированный endpoint** | Async-native, быстрый, минимальный; не навязываем ORM; закомментированный пример endpoint в `main.py` — разработчик раскомментирует при необходимости |
| 11 | **Frontend: Vite+React+TS vs static?** | **B4-a: Vite + React 19 + TypeScript 5** | Платформа создана для SPA-проектов (hermes-dashboard, status-page, langfuse); конкретный набор: React 19.1, Vite 6, TypeScript 5.8 |
| 12 | `tsconfig.json`/`.eslintrc` — генерировать или статика? | **Статичный payload** в шаблоне | eslint/tsconfig конфиги проектно-специфичны; нет смысла генерировать из канона; старые GENERATED-stub'ы удаляются; TRAP[DEBT] `generators.py:368` удаляется |
| 13 | Фаза C — когда? | **C-later: по триггеру «3-й шаблон»** | Фундамент (template.yaml + manifest) закладывается в Wave 1; merge-логика `project_scaffolder.py` не реализуется сейчас |

---

## 1. Draft Code Graph (XML)

```xml
<code_graph>
  <!-- Wave 1: Cleanup -->
  <entity id="templates_template_backend" type="directory" status="modified">
    <remove>.env.platform ai-platform.yaml pyproject.toml .pre-commit-config.yaml tests/conftest.py tests/test_health.py practices.lock Makefile AGENTS.md</remove>
    <add>.gitignore .env.example template.yaml</add>
    <modify>README.md</modify>
  </entity>
  <entity id="templates_template_frontend" type="directory" status="modified">
    <remove>.env.platform ai-platform.yaml .pre-commit-config.yaml tests/conftest.py tests/test_health.py practices.lock Makefile AGENTS.md src/index.html tsconfig.json .eslintrc</remove>
    <add>.gitignore .env.example template.yaml</add>
    <modify>README.md</modify>
  </entity>
  <entity id="scaffold_helpers_gen_project_makefile" type="function" file="core/internal/scaffold/scaffold_helpers.py" status="modified">
    <add_targets>project-check project-fix project-sync-practices project-set-practices</add_targets>
  </entity>
  <entity id="scaffold_helpers_gen_ai_platform_yaml" type="function" file="core/internal/scaffold/scaffold_helpers.py" status="modified">
    <change>metrics_port: 8080 → 8000 for backend</change>
  </entity>
  <entity id="scaffold_helpers_read_template_yaml" type="function" file="core/internal/scaffold/scaffold_helpers.py" status="new">
    <purpose>validate template.yaml version vs practices_manifest.version</purpose>
  </entity>
  <entity id="project_scaffolder_main" type="function" file="core/internal/scaffold/project_scaffolder.py" status="modified">
    <change>gen_project_makefile force=False → force=True</change>
    <change>gen_project_agents force=False → force=True</change>
  </entity>
  <entity id="practices_generators" type="function" file="core/internal/practices/generators.py" status="modified">
    <remove_trap>TRAP[DEBT]:368 — tsconfig/eslintrc больше не GENERATED</remove_trap>
  </entity>

  <!-- Wave 2: Backend -->
  <entity id="template_backend_src_config" type="file" status="new">
    <path>templates/template-backend/src/config.py</path>
    <purpose>pydantic-settings BaseSettings — чтение PLATFORM_* из .env.platform</purpose>
  </entity>
  <entity id="template_backend_src_db" type="file" status="new">
    <path>templates/template-backend/src/db.py</path>
    <purpose>asyncpg pool-инициализация из PLATFORM_POSTGRES_DSN</purpose>
  </entity>
  <entity id="template_backend_src_main" type="file" status="modified">
    <path>templates/template-backend/src/main.py</path>
    <change>/metrics через prometheus_client + закомментированный db-endpoint</change>
  </entity>
  <entity id="template_backend_src_requirements" type="file" status="modified">
    <path>templates/template-backend/src/requirements.txt</path>
    <add>pydantic-settings prometheus-client asyncpg</add>
  </entity>
  <entity id="template_backend_dev_compose" type="file" status="new">
    <path>templates/template-backend/docker-compose.dev.yml</path>
    <purpose>postgres + redis для локальной разработки</purpose>
  </entity>

  <!-- Wave 3: Frontend -->
  <entity id="template_frontend_package" type="file" status="new">
    <path>templates/template-frontend/package.json</path>
    <purpose>React 19.1 + Vite 6 + TypeScript 5.8</purpose>
  </entity>
  <entity id="template_frontend_vite_config" type="file" status="new">
    <path>templates/template-frontend/vite.config.ts</path>
  </entity>
  <entity id="template_frontend_index_html" type="file" status="new">
    <path>templates/template-frontend/index.html</path>
    <purpose>Vite entry point</purpose>
  </entity>
  <entity id="template_frontend_src_main_tsx" type="file" status="new">
    <path>templates/template-frontend/src/main.tsx</path>
  </entity>
  <entity id="template_frontend_src_app_tsx" type="file" status="new">
    <path>templates/template-frontend/src/App.tsx</path>
    <purpose>/health + /ready + platform services display</purpose>
  </entity>
  <entity id="template_frontend_tsconfig" type="file" status="new">
    <path>templates/template-frontend/tsconfig.json</path>
    <purpose>статичный payload (НЕ GENERATED)</purpose>
  </entity>
  <entity id="template_frontend_eslintrc" type="file" status="new">
    <path>templates/template-frontend/.eslintrc</path>
    <purpose>статичный payload (НЕ GENERATED)</purpose>
  </entity>
  <entity id="template_frontend_dockerfile" type="file" status="modified">
    <path>templates/template-frontend/Dockerfile</path>
    <change>убрать условный npm ci/build — теперь package.json есть всегда</change>
  </entity>

  <!-- Wave 4: Gates + Docs -->
  <entity id="gate_env_example_template" type="test" file="tests/gates/test_gate_env_example_template.py" status="new">
    <purpose>сверка .env.example с platform-env.yaml#provides</purpose>
  </entity>
  <entity id="template_backend_readme" type="file" status="modified">
    <path>templates/template-backend/README.md</path>
    <purpose>таблица PLATFORM_* ↔ сервис ↔ пример кода</purpose>
  </entity>
  <entity id="template_frontend_readme" type="file" status="modified">
    <path>templates/template-frontend/README.md</path>
    <purpose>таблица PLATFORM_* ↔ сервис + Vite-команды</purpose>
  </entity>
  <entity id="template_manifest" type="file" status="modified">
    <path>core/templates/template-manifest.yaml</path>
    <change>актуализация: удалить ссылки на несуществующие файлы</change>
  </entity>
</code_graph>
```

---

## 2. Пошаговая имплементация

### Wave 1: Cleanup (A1–A8)

#### Step 1.1 — A1: Удалить GENERATED-копии практик из шаблонов

**Файлы к удалению из `templates/template-backend/`:**
- `pyproject.toml`
- `.pre-commit-config.yaml`
- `tests/conftest.py`
- `tests/test_health.py`
- `practices.lock`

**Файлы к удалению из `templates/template-frontend/`:**
- `pyproject.toml` (нет во frontend — пропускаем)
- `.pre-commit-config.yaml`
- `tests/conftest.py`
- `tests/test_health.py`
- `practices.lock`

**Проверка gate-зависимостей:** `rg "template-backend/tests|template-frontend/tests" tests/gates/` → 0 результатов. Безопасно.

**После удаления:** директории `tests/` в обоих шаблонах станут пустыми → удалить сами директории `tests/`.

#### Step 1.2 — A2: Удалить `.env.platform`-заглушки, добавить `.env.example`

**Удалить:**
- `templates/template-backend/.env.platform`
- `templates/template-frontend/.env.platform`

**Создать `templates/template-backend/.env.example`:**
```bash
# ── Platform Services (provided by ai-platform) ──
# Все переменные ниже доступны через .env.platform после `make sync-env`.
# Этот файл — reference; для локальной разработки используйте `docker compose -f docker-compose.dev.yml up -d`.

# PostgreSQL (shared-db-net)
PLATFORM_POSTGRES_HOST=postgres-shared
PLATFORM_POSTGRES_PORT=5432
PLATFORM_POSTGRES_DSN=postgresql://user:pass@postgres-shared:5432/db

# Redis (shared-cache-net)
PLATFORM_REDIS_URL=redis://redis-shared:6379

# LiteLLM (proxy-net)
PLATFORM_LITELLM_URL=http://litellm:4000

# Langfuse (proxy-net)
PLATFORM_LANGFUSE_URL=http://langfuse:3000

# MinIO (proxy-net)
PLATFORM_MINIO_URL=http://minio:9000

# ClickHouse (shared-db-net)
PLATFORM_CLICKHOUSE_HOST=clickhouse
PLATFORM_CLICKHOUSE_PORT=8123
PLATFORM_CLICKHOUSE_DSN=clickhouse://clickhouse:8123

# Domain
PLATFORM_DOMAIN=test.local

# Docker networks (external)
PLATFORM_PROXY_NET=proxy-net
PLATFORM_SHARED_DB_NET=shared-db-net

# No-proxy (internal services)
PLATFORM_NO_PROXY=localhost,127.0.0.1,.local
```

**Создать `templates/template-frontend/.env.example`:**
```bash
# ── Platform Services (provided by ai-platform) ──
# Все переменные ниже доступны через .env.platform после `make sync-env`.

PLATFORM_DOMAIN=test.local
PLATFORM_LITELLM_URL=http://litellm:4000
PLATFORM_LANGFUSE_URL=http://langfuse:3000
PLATFORM_PROXY_NET=proxy-net
PLATFORM_NO_PROXY=localhost,127.0.0.1,.local
```

**Gate-тест (Wave 4):** `tests/gates/test_gate_env_example_template.py` — сверяет перечень `PLATFORM_*` переменных в `.env.example` каждого шаблона с `platform-env.yaml#provides`. Разрешённые расхождения: `.env.example` может содержать ПОДМНОЖЕСТВО (не все сервисы нужны frontend), но не содержит переменных, отсутствующих в provides.

#### Step 1.3 — A3: Удалить `ai-platform.yaml` из шаблонов

**Удалить:**
- `templates/template-backend/ai-platform.yaml`
- `templates/template-frontend/ai-platform.yaml`

**Проверка:** Генератор `gen_ai_platform_yaml` вызывается безусловно на шаге 2 scaffold (`project_scaffolder.py:646-661`) и содержит все поля (name, type, target_node, needs, monitoring, quality). Шаблонный файл — строгое подмножество. Поле `platform_domain: {{PLATFORM_DOMAIN}}` из шаблона НЕ добавляется в генератор (legacy, не читается scaffold-логикой).

**`template-manifest.yaml`:** запись для template-backend/frontend — `type: directory, recursive: true`. Удаление `ai-platform.yaml` из директории не требует изменения manifest (recursive-запись покрывает любые файлы внутри).

#### Step 1.4 — A4: Обогатить генераторы, переключить force=True для scaffolder

##### 1.4a: Обогатить `gen_project_makefile` (`scaffold_helpers.py:229-285`)

Добавить таргеты `project-check`, `project-fix`, `project-sync-practices`, `project-set-practices` (из шаблонного Makefile) и делегацию через `$(CURDIR)`:

```python
makefile_content = f"""# GENERATED by ai-platform — DO NOT EDIT manually
# Project: {name}
# ai-platform project Makefile (K3 contract) — facade for platform operations

PLATFORM_DIR ?= $(HOME)/projects/ai-platform

sync-env: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-env PROJECT=$(CURDIR)
status:   ; @$(MAKE) -C $(PLATFORM_DIR) project-status PROJECT=$(CURDIR)
project-check: ; @$(MAKE) -C $(PLATFORM_DIR) project-check PROJECT=$(CURDIR)
project-fix:   ; @$(MAKE) -C $(PLATFORM_DIR) project-fix PROJECT=$(CURDIR)
project-sync-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-practices PROJECT=$(CURDIR)
project-set-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-set-practices PROJECT=$(CURDIR) LEVEL=$(LEVEL)
help:
	@echo "sync-env  — Regenerate .env.platform from platform config"
	@echo "status   — Check deployment status on node"
	@echo "project-check — Run practices checks (K1)"
	@echo "project-fix — Auto-fix practices checks"
	@echo "project-sync-practices — Regenerate GENERATED practices files"
	@echo "project-set-practices — Set practices level (LEVEL=baseline|full|auto)"
```

##### 1.4b: Обогатить `gen_project_agents` (`scaffold_helpers.py:302-366`)

Добавить в контент:
- `project-*` команды (из обновлённого Makefile)
- Ссылку на `.env.example` для списка платформенных переменных
- Node и domain информацию (уже есть, проверить)

##### 1.4c: Изменить `force` в `project_scaffolder.py` main()

```python
# Step 5: Generate Makefile + AGENTS.md + AI-PLATFORM.md
gen_project_makefile(
    name=args.name,
    domain=domain,
    output_path=os.path.join(project_dir, "Makefile"),
    force=True,   # было False
)
gen_project_agents(
    name=args.name,
    org=org,
    template=args.template,
    node=node,
    domain=domain,
    output_path=os.path.join(project_dir, "AGENTS.md"),
    force=True,   # было False
)
```

**Проверка adopter:** `project_adopter.py:304-313` вызывает `gen_project_makefile`/`gen_project_agents` с `force=False` (по умолчанию) — поведение НЕ меняется.

##### 1.4d: Удалить Makefile и AGENTS.md из шаблонов

- `templates/template-backend/Makefile`
- `templates/template-backend/AGENTS.md`
- `templates/template-frontend/Makefile`
- `templates/template-frontend/AGENTS.md`

#### Step 1.5 — A5: Добавить `.gitignore`

**`templates/template-backend/.gitignore`:**
```
.ruff_cache/
__pycache__/
*.pyc
.env.platform
dist/
build/
*.egg-info/
```

**`templates/template-frontend/.gitignore`:**
```
.ruff_cache/
__pycache__/
*.pyc
node_modules/
.env.platform
dist/
build/
.vite/
```

**Также удалить** `.ruff_cache/` из `templates/template-backend/` (закоммиченный кэш ruff — реальный мусор, подтверждённый аудитом).

#### Step 1.6 — A6: Убрать `{{DATABASE}}` из README backend

В `templates/template-backend/README.md` удалить строку:
```
| `{{DATABASE}}` | Имя базы данных | опционально |
```

`{{DATABASE}}` не входит в vars рендера (`project_scaffolder.py:242-251`: `PROJECT_NAME`, `ORG_NAME`, `DOMAIN`, `NODE_NAME`, `PLATFORM_DOMAIN`) — остаётся незаменённым, формируя битый плейсхолдер в сгенерированном README.

#### Step 1.7 — A7: Добавить `template.yaml` + reader

**`templates/template-backend/template.yaml`:**
```yaml
# template.yaml — scaffold metadata
name: template-backend
version: 1
requires_practices_version: 1
description: "Backend project template (Python/FastAPI)"
```

**`templates/template-frontend/template.yaml`:**
```yaml
# template.yaml — scaffold metadata
name: template-frontend
version: 1
requires_practices_version: 1
description: "Frontend project template (Vite/React/TypeScript)"
```

**Reader в `scaffold_helpers.py`:**

```python
# region FUNC_read_template_yaml
def read_template_yaml(template_dir: str | Path) -> dict[str, Any]:
    """Read and validate template.yaml. Returns parsed dict.

    ## @purpose  Validate template version compatibility with practices_manifest.
    ## @io        ⇥ template_dir → ⎋ dict with keys: name, version, requires_practices_version
    ##            ⚡ raises ConfigValidationError if version incompatible
    """
    import yaml
    from core.internal.shared.exceptions import ConfigValidationError

    template_yaml = Path(template_dir) / "template.yaml"
    if not template_yaml.exists():
        logger.info("[IMP:8][template_yaml] template.yaml not found: %s", template_yaml)
        return {}

    with open(template_yaml) as f:
        data = yaml.safe_load(f) or {}

    # Validate requires_practices_version
    from core.internal.practices.manifest import PracticesManifest
    practices = PracticesManifest()
    required = data.get("requires_practices_version", 0)
    if required > practices.version:
        raise ConfigValidationError(
            f"Template {data.get('name', 'unknown')} requires practices_manifest v{required}, "
            f"but current is v{practices.version}. Update the platform first."
        )

    logger.info(
        "[IMP:9][template_yaml] template.yaml validated: %s v%d (requires practices v%d, current v%d)",
        data.get("name"), data.get("version", 0), required, practices.version,
    )
    return data
# endregion FUNC_read_template_yaml
```

**Примечание:** `read_template_yaml` вызывается опционально — при scaffold для информационного логирования и validation. Не блокирует scaffold при отсутствии файла (graceful degradation для обратной совместимости).

#### Step 1.8 — A8: Актуализировать `template-manifest.yaml` + пройти `templates-check`

**`core/templates/template-manifest.yaml`:** записи для `template-backend` и `template-frontend` — `type: directory, recursive: true`. Удаление файлов из директории не требует изменения manifest. Новые файлы (`.env.example`, `.gitignore`, `template.yaml`) автоматически попадают под coverage-check.

**Выполнить:** `make templates-check` после всех изменений Wave 1.

---

### Wave 2: Backend Content Patterns (B1–B3, B6)

#### Step 2.1 — B1: `src/config.py` через pydantic-settings

**`templates/template-backend/src/config.py`:**
```python
"""Application configuration via pydantic-settings — reads from .env.platform."""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Platform services (from .env.platform, generated by `make sync-env`)
    postgres_dsn: str = ""
    redis_url: str = ""
    litellm_url: str = ""
    langfuse_url: str = ""
    minio_url: str = ""
    clickhouse_dsn: str = ""
    platform_domain: str = ""

    # Application
    port: int = 8000
    debug: bool = False

    model_config = {
        "env_prefix": "PLATFORM_",
        "env_file": ".env.platform",
        "extra": "ignore",
    }

settings = Settings()
```

#### Step 2.2 — B2: Честный `/metrics` через prometheus_client

**Изменить `templates/template-backend/src/main.py`:**

Текущий `/metrics` (строка 50-53):
```python
@app.get("/metrics")
async def metrics() -> dict[str, str]:
    return {"status": "OK", "metrics": "exposed"}
```

Заменить на:
```python
from prometheus_client import make_asgi_app

# Mount Prometheus metrics on the same port (8000)
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

**В `scaffold_helpers.py:gen_ai_platform_yaml`**, строка 176: `"metrics_port": 8080` → `"metrics_port": 8000`.

**Проверка `monitoring_config_renderer.py`:** default `metrics_port=3000` (строка 123). Изменение 8080→8000 в генераторе не ломает рендерер — он читает значение из `ai-platform.yaml`. Prometheus scrape config будет ссылаться на `:8000/metrics` — что корректно для backend-проектов.

#### Step 2.3 — B3: `src/db.py` + пример подключения

**`templates/template-backend/src/db.py`:**
```python
"""Database connection pool via asyncpg — reads PLATFORM_POSTGRES_DSN from .env.platform."""
import logging
import asyncpg
from config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    """Return or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        if not settings.postgres_dsn:
            raise RuntimeError(
                "PLATFORM_POSTGRES_DSN not set. Run `make sync-env` or set manually."
            )
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
        )
        logger.info("Database pool created")
    return _pool

async def close_pool() -> None:
    """Close the connection pool (call on shutdown)."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")
```

**Добавить закомментированный endpoint в `templates/template-backend/src/main.py`:**
```python
# ── Example: database endpoint (uncomment when needed) ──
# from db import get_pool
#
# @app.get("/items")
# async def list_items():
#     """Example: query database via PLATFORM_POSTGRES_DSN."""
#     pool = await get_pool()
#     async with pool.acquire() as conn:
#         rows = await conn.fetch("SELECT 1 AS one")
#     return {"items": [dict(r) for r in rows]}
```

#### Step 2.4 — Обновить `requirements.txt`

Добавить в `templates/template-backend/src/requirements.txt`:
```
pydantic-settings>=2.0
prometheus-client>=0.21
asyncpg>=0.30
```

(Плюс существующие: `fastapi`, `uvicorn[standard]`, `python-dotenv`)

#### Step 2.5 — B6: `docker-compose.dev.yml`

**`templates/template-backend/docker-compose.dev.yml`:**
```yaml
# docker-compose.dev.yml — local development dependencies
# Usage: docker compose -f docker-compose.dev.yml up -d
#   or:  make dev  (from project Makefile)

services:
  postgres:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: dev
      POSTGRES_PASSWORD: dev
      POSTGRES_DB: dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dev"]
      interval: 5s
      timeout: 3s
      retries: 3

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3

volumes:
  pgdata:
```

**Добавить `dev` таргет в `gen_project_makefile`:**
```makefile
dev:
	@echo "Starting local dev services (postgres + redis)..."
	docker compose -f docker-compose.dev.yml up -d
	@echo "Dev services ready. Use 'make sync-env' to update .env.platform."
```

---

### Wave 3: Frontend SPA Toolkit (B4)

#### Step 3.1 — Удалить старые frontend-файлы

Уже удалены в Wave 1: `src/index.html`, `tsconfig.json`, `.eslintrc`.

#### Step 3.2 — Создать `package.json`

**`templates/template-frontend/package.json`:**
```json
{
  "name": "{{PROJECT_NAME}}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint ."
  },
  "dependencies": {
    "react": "^19.1.0",
    "react-dom": "^19.1.0"
  },
  "devDependencies": {
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.4.0",
    "eslint": "^9.24.0",
    "typescript": "~5.8.0",
    "vite": "^6.3.0"
  }
}
```

⚠️ `{{PROJECT_NAME}}` — валидный плейсхолдер (UPPER_SNAKE, резолвится template_engine при scaffold). `package.json` должен быть в списке файлов, которые template-engine рендерит (UPPER_SNAKE плейсхолдеры).

#### Step 3.3 — Создать `vite.config.ts`

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 80,
    proxy: {
      '/health': 'http://localhost:80',
      '/ready': 'http://localhost:80',
    },
  },
  build: {
    outDir: 'dist',
  },
})
```

#### Step 3.4 — Создать `index.html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{PROJECT_NAME}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

#### Step 3.5 — Создать `src/main.tsx`

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

#### Step 3.6 — Создать `src/App.tsx`

```tsx
import { useEffect, useState } from 'react'

interface HealthStatus {
  status: string
  service: string
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/health')
      .then(res => res.json())
      .then(setHealth)
      .catch(err => setError(err.message))
  }, [])

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem' }}>
      <h1>{{PROJECT_NAME}}</h1>
      <p>Frontend project — powered by ai-platform</p>
      <hr />
      <h2>Health</h2>
      {error && <p style={{ color: 'red' }}>Error: {error}</p>}
      {health && (
        <pre>{JSON.stringify(health, null, 2)}</pre>
      )}
      <hr />
      <h2>Platform Services</h2>
      <p>Available via <code>.env.platform</code> — run <code>make sync-env</code></p>
      <ul>
        <li>LiteLLM: <code>PLATFORM_LITELLM_URL</code></li>
        <li>Langfuse: <code>PLATFORM_LANGFUSE_URL</code></li>
      </ul>
    </div>
  )
}

export default App
```

#### Step 3.7 — Создать `tsconfig.json` (статичный payload)

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"]
}
```

#### Step 3.8 — Создать `.eslintrc` (статичный payload)

```json
{
  "root": true,
  "env": {
    "browser": true,
    "es2022": true
  },
  "extends": [
    "eslint:recommended"
  ],
  "parserOptions": {
    "ecmaVersion": "latest",
    "sourceType": "module"
  },
  "rules": {
    "no-unused-vars": "warn",
    "no-console": "off"
  }
}
```

#### Step 3.9 — Создать `src/vite-env.d.ts`

```typescript
/// <reference types="vite/client" />
```

#### Step 3.10 — Обновить frontend Dockerfile

Текущий Dockerfile использует условный `if [ -f package.json ]` — но теперь `package.json` есть всегда. Упростить:

```dockerfile
# ---- Build Stage ----
FROM node:22.23.0-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --prefer-offline --no-audit
COPY . .
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm run build

# ---- Run Stage ----
FROM nginx:1.28-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost:80/health || exit 1
```

**Важно:** nginx healthcheck ожидает `/health` — и nginx его отдаёт (строка 25-28 в `nginx/default.conf`). В production-сборке `/health` обслуживается nginx, не Vite dev-server. Работает.

#### Step 3.11 — Удалить GENERATED-stub'ы tsconfig/eslintrc из фронтенда

Уже удалены в Wave 1. Дополнительно: **удалить TRAP[DEBT] из `generators.py:368`**.

Текущий код (строки 368-376):
```python
# 📝 TRAP[DEBT] · 2026-08-05 · LO · frontend .eslintrc/tsconfig.json — template-stubs без рендера
# ...
```

Заменить на комментарий:
```python
# tsconfig.json/.eslintrc — static payload в template-frontend/, НЕ GENERATED.
# Не требуют рендера из practices_manifest — проектно-специфичные конфиги.
```

---

### Wave 4: Documentation + Gates (B5, Final Verification)

#### Step 4.1 — B5: Переработать README

**`templates/template-backend/README.md`** — полная переработка:

```markdown
# {{PROJECT_NAME}}

> Backend проект, создан из шаблона `template-backend` (ai-platform).

## Быстрый старт

```bash
# 1. Локальные зависимости (postgres + redis)
make dev

# 2. Синхронизировать платформенное окружение
make sync-env

# 3. Запустить приложение
pip install -r src/requirements.txt
PLATFORM_POSTGRES_DSN=postgresql://dev:dev@localhost:5432/dev python src/main.py
```

## Платформенные сервисы

| Переменная | Сервис | Пример использования |
|-----------|--------|---------------------|
| `PLATFORM_POSTGRES_DSN` | PostgreSQL (shared) | `src/db.py` — asyncpg pool |
| `PLATFORM_REDIS_URL` | Redis (shared) | `redis.from_url(settings.redis_url)` |
| `PLATFORM_LITELLM_URL` | LiteLLM proxy | `openai.AsyncOpenAI(base_url=settings.litellm_url)` |
| `PLATFORM_LANGFUSE_URL` | Langfuse tracing | `langfuse.Langfuse(host=settings.langfuse_url)` |
| `PLATFORM_MINIO_URL` | MinIO S3 | `boto3.client('s3', endpoint_url=settings.minio_url)` |
| `PLATFORM_CLICKHOUSE_DSN` | ClickHouse | `clickhouse_connect.get_client(dsn=settings.clickhouse_dsn)` |

Полный список: `grep PLATFORM_ .env.example`

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта (генерируется при scaffold) |
| `Dockerfile` | Python 3.12-slim + FastAPI |
| `docker-compose.yml` | Production сервис + platform networks |
| `docker-compose.dev.yml` | Локальные postgres + redis |
| `src/main.py` | HTTP-сервер: /health, /ready, /metrics |
| `src/config.py` | Конфигурация через pydantic-settings |
| `src/db.py` | Подключение к PostgreSQL через asyncpg |
| `src/requirements.txt` | Зависимости Python |
| `.env.platform` | Платформенное окружение (генерируется `make sync-env`, НЕ редактировать) |
| `Makefile` | Команды платформы |
| `AGENTS.md` | Контекст для AI-агента |

## Команды

```bash
make sync-env              # Обновить .env.platform
make status                # Проверить статус деплоя
make project-check         # Проверить практики проекта
make project-fix           # Автофикс практик
make project-sync-practices # Перегенерировать GENERATED-файлы
```

## Деплой

```bash
git push  # CI/CD деплоит автоматически (deploy-project.yml)
```
```

**`templates/template-frontend/README.md`** — аналогичная переработка с frontend-спецификой (Vite-команды, `npm run dev`, `npm run build`).

#### Step 4.2 — Gate-тест `.env.example`

**`tests/gates/test_gate_env_example_template.py`:**

```python
# GREP_SUMMARY: gate env-example-template .env.example vs platform-env.yaml provides
# STRUCTURE: ▶ load provides from platform-env.yaml → scan .env.example files → assert subset

@pytest.mark.gate
@ldd_trajectory
def test_env_example_subset_of_provides(caplog) -> None:
    """Каждая PLATFORM_* в .env.example шаблонов должна быть в platform-env.yaml#provides."""
    import yaml
    import re

    platform_env = ROOT / "platform-env.yaml"
    with open(platform_env) as f:
        env_data = yaml.safe_load(f)
    provides_vars = set(env_data.get("provides", {}).keys())

    VAR_RE = re.compile(r"^(PLATFORM_[A-Z_]+)=")
    violations = []

    for template_name in ("template-backend", "template-frontend"):
        example_file = ROOT / "templates" / template_name / ".env.example"
        if not example_file.exists():
            continue
        for line in example_file.read_text().splitlines():
            m = VAR_RE.match(line.strip())
            if m and m.group(1) not in provides_vars:
                violations.append(f"{template_name}: {m.group(1)}")

    if violations:
        pytest.fail(
            f".env.example содержит переменные, отсутствующие в platform-env.yaml#provides:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nДобавьте переменную в platform-env.yaml#provides или удалите из .env.example."
        )

    logger.info("[IMP:9][env_example] PASS: все PLATFORM_* в .env.example ∈ provides (%d vars)", len(provides_vars))
```

#### Step 4.3 — Актуализация template-manifest.yaml

Проверить, что все новые файлы (`.gitignore`, `.env.example`, `template.yaml`, `package.json`, `vite.config.ts`, `tsconfig.json`, `.eslintrc`, `index.html`, `src/main.tsx`, `src/App.tsx`, `src/vite-env.d.ts`, `src/config.py`, `src/db.py`, `docker-compose.dev.yml`) корректно покрываются `type: directory, recursive: true` записью. Файлы с `{{UPPER_SNAKE}}` плейсхолдерами: `package.json` (`{{PROJECT_NAME}}`), `index.html` (`{{PROJECT_NAME}}`), `src/App.tsx` (`{{PROJECT_NAME}}`) — должны резолвиться через существующие vars (`PROJECT_NAME` есть в manifest).

**Потенциальная проблема:** `{{PROJECT_NAME}}` внутри `package.json` (JSON) — template_engine использует strict regex `{{[A-Z][A-Z0-9_]*}}`. В JSON пробелов нет, uppercase внутри строки — OK. Проверить: `template_engine.render_template` должен корректно обрабатывать JSON-файлы.

Если нет — добавить `package.json` и `index.html` в allowlist для template engine (файлы, которые НЕ должны проходить рендер, если их содержимое нарушает strict grammar). **Но:** `PROJECT_NAME` — валидный UPPER_SNAKE, проблем быть не должно.

#### Step 4.4 — Финальная верификация

```bash
# 1. Генерация манифестов (если затронуты generators/AGENTS.md)
make generate-manifests

# 2. Проверка шаблонов
make templates-check

# 3. Проверка проекта (dry-run scaffold)
python3 -m core.internal.scaffold.project_scaffolder --name test-evolution --template backend --dry-run
python3 -m core.internal.scaffold.project_scaffolder --name test-evolution-fe --template frontend --dry-run

# 4. Pre-commit + format
make fix-gate && git add -u

# 5. Check (все проверки из check-suite.yaml)
make check
```

При пуше pre-push hook автоматически выполнит `make gate MODE=fast`.

---

## 3. File Manifest

### Удаляемые файлы

| # | Файл | Волна |
|---|------|-------|
| 1 | `templates/template-backend/.env.platform` | W1 |
| 2 | `templates/template-backend/ai-platform.yaml` | W1 |
| 3 | `templates/template-backend/pyproject.toml` | W1 |
| 4 | `templates/template-backend/.pre-commit-config.yaml` | W1 |
| 5 | `templates/template-backend/tests/conftest.py` | W1 |
| 6 | `templates/template-backend/tests/test_health.py` | W1 |
| 7 | `templates/template-backend/practices.lock` | W1 |
| 8 | `templates/template-backend/Makefile` | W1 |
| 9 | `templates/template-backend/AGENTS.md` | W1 |
| 10 | `templates/template-backend/.ruff_cache/` (директория) | W1 |
| 11 | `templates/template-backend/tests/` (пустая директория) | W1 |
| 12 | `templates/template-frontend/.env.platform` | W1 |
| 13 | `templates/template-frontend/ai-platform.yaml` | W1 |
| 14 | `templates/template-frontend/.pre-commit-config.yaml` | W1 |
| 15 | `templates/template-frontend/tests/conftest.py` | W1 |
| 16 | `templates/template-frontend/tests/test_health.py` | W1 |
| 17 | `templates/template-frontend/practices.lock` | W1 |
| 18 | `templates/template-frontend/Makefile` | W1 |
| 19 | `templates/template-frontend/AGENTS.md` | W1 |
| 20 | `templates/template-frontend/tests/` (пустая директория) | W1 |
| 21 | `templates/template-frontend/src/index.html` | W1 |
| 22 | `templates/template-frontend/tsconfig.json` (GENERATED-stub) | W1 |
| 23 | `templates/template-frontend/.eslintrc` (GENERATED-stub) | W1 |

### Создаваемые файлы

| # | Файл | Волна |
|---|------|-------|
| 1 | `templates/template-backend/.gitignore` | W1 |
| 2 | `templates/template-backend/.env.example` | W1 |
| 3 | `templates/template-backend/template.yaml` | W1 |
| 4 | `templates/template-frontend/.gitignore` | W1 |
| 5 | `templates/template-frontend/.env.example` | W1 |
| 6 | `templates/template-frontend/template.yaml` | W1 |
| 7 | `templates/template-backend/src/config.py` | W2 |
| 8 | `templates/template-backend/src/db.py` | W2 |
| 9 | `templates/template-backend/docker-compose.dev.yml` | W2 |
| 10 | `templates/template-frontend/package.json` | W3 |
| 11 | `templates/template-frontend/vite.config.ts` | W3 |
| 12 | `templates/template-frontend/index.html` | W3 |
| 13 | `templates/template-frontend/src/main.tsx` | W3 |
| 14 | `templates/template-frontend/src/App.tsx` | W3 |
| 15 | `templates/template-frontend/src/vite-env.d.ts` | W3 |
| 16 | `templates/template-frontend/tsconfig.json` (статичный payload) | W3 |
| 17 | `templates/template-frontend/.eslintrc` (статичный payload) | W3 |
| 18 | `tests/gates/test_gate_env_example_template.py` | W4 |

### Модифицируемые файлы

| # | Файл | Изменение | Волна |
|---|------|-----------|-------|
| 1 | `core/internal/scaffold/scaffold_helpers.py` | Обогатить `gen_project_makefile` (+4 таргета), `gen_ai_platform_yaml` (metrics_port 8080→8000), добавить `read_template_yaml()` | W1 |
| 2 | `core/internal/scaffold/project_scaffolder.py` | `force=True` для makefile/agents (строки 677-701) | W1 |
| 3 | `core/internal/practices/generators.py` | Удалить TRAP[DEBT]:368 (tsconfig/eslintrc — больше не GENERATED) | W1 |
| 4 | `templates/template-backend/README.md` | Убрать `{{DATABASE}}` (W1) + полная переработка (W4) | W1+W4 |
| 5 | `templates/template-backend/src/main.py` | `/metrics` через prometheus_client, закомментированный db-endpoint | W2 |
| 6 | `templates/template-backend/src/requirements.txt` | +pydantic-settings, prometheus-client, asyncpg | W2 |
| 7 | `templates/template-frontend/Dockerfile` | Упростить (убрать условный npm ci/build) | W3 |
| 8 | `templates/template-frontend/README.md` | Полная переработка (Vite-команды + платформенные сервисы) | W4 |
| 9 | `core/templates/template-manifest.yaml` | Актуализация (при необходимости) | W4 |

---

## 4. Acceptance Criteria

| # | Критерий | Проверка | Волна |
|---|----------|----------|-------|
| AC1 | `make new-project NAME=test-foo TEMPLATE=backend --dry-run` успешен | Без ошибок, план показывает корректный проект | W1 |
| AC2 | `make new-project NAME=test-bar TEMPLATE=frontend --dry-run` успешен | Без ошибок, план показывает корректный проект | W1 |
| AC3 | `make templates-check` зелёный | Все шаблоны зарегистрированы, плейсхолдеры резолвятся | W4 |
| AC4 | `make gate MODE=fast` зелёный | Все существующие гейты проходят | W4 |
| AC5 | Новый gate `test_gate_env_example_template` сверяет `.env.example` с provides | PASS | W4 |
| AC6 | Backend-проект из шаблона: `src/config.py` читает `PLATFORM_POSTGRES_DSN` | `settings.postgres_dsn` непустой после `make sync-env` | W2 |
| AC7 | Backend-проект: `/metrics` отдаёт prometheus-метрики | `curl /metrics` → `# HELP python_info ...` | W2 |
| AC8 | Backend-проект: `docker compose -f docker-compose.dev.yml up -d` поднимает postgres+redis | `docker compose ps` → оба healthy | W2 |
| AC9 | Frontend-проект: `npm ci && npm run build` успешен | Build без ошибок, `dist/` создан | W3 |
| AC10 | Frontend-проект: Dockerfile собирается | `docker build .` → образ собирается, nginx стартует | W3 |
| AC11 | `gen_project_makefile` содержит `project-check`, `project-fix`, `project-sync-practices`, `project-set-practices` | Сгенерированный Makefile включает все 4 таргета | W1 |
| AC12 | `gen_project_agents` содержит ссылку на `.env.example` и `project-*` команды | Сгенерированный AGENTS.md информативен | W1 |
| AC13 | `adopt-project` НЕ перезаписывает существующий Makefile/AGENTS.md | `force=False` (по умолчанию), поведение сохранено | W1 |
| AC14 | `.env.platform` НЕ коммитится в новый проект | `.gitignore` содержит `.env.platform` | W1 |
| AC15 | `template.yaml` валидируется при scaffold | `read_template_yaml()` проверяет `requires_practices_version` | W1 |

---

## 5. Риски и митигации

| Риск | Вероятность | Митигация |
|------|------------|-----------|
| `{{PROJECT_NAME}}` в package.json ломает JSON-парсинг template engine | Низкая | Плейсхолдер валиден (UPPER_SNAKE), JSON-строки допускают `{{...}}`; проверить двойным рендером |
| Удалённые `tests/` директории ломают gate | Низкая | `rg "template-backend/tests\|template-frontend/tests" tests/gates/` → 0 результатов |
| `.env.example` дрейфует относительно `platform-env.yaml` | Средняя | Gate `test_gate_env_example_template` детектит на CI |
| `npm ci` в Dockerfile без pre-installed node_modules падает на CI | Низкая | `--mount=type=cache` для npm; тестовая сборка в Wave 4 |
| `gen_project_makefile` с `$(CURDIR)` ломает adopter | Низкая | Adopter использует `force=False` — существующий Makefile сохраняется |
| `metrics_port` 8080→8000 ломает мониторинг | Низкая | `monitoring_config_renderer.py` default=3000 — читает значение из ai-platform.yaml динамически |
| Frontend `docker-compose.yml` требует изменений для Vite | Низкая | Vite dev-server порт 80 совместим с текущим docker-compose (healthcheck `wget :80/health`) |

---

## 6. Что НЕ входит в этот DevPlan (explicit scope boundaries)

1. **Фаза C (композируемые слои + packs).** Отложена до триггера «3-й шаблон». Фундамент заложен: `template.yaml` + reader.
2. **Snippets library (отчёт 1, Option E).** Переиспользование интеграционных паттернов — ортогонально packs, отложено.
3. **`template-context/`.** Не входит в скоуп брифа (context-init не затрагивается).
4. **Автоматическая генерация `.env.example`.** Выбрана статика + gate (A2-a). Генерация из `platform-env.yaml` отложена до появления второго `.env.example`-файла (YAGNI).
5. **Рендер `tsconfig.json`/`.eslintrc` из practices_manifest.** Выбран статичный payload (спорный момент 12). TRAP[DEBT] `generators.py:368` удаляется.
6. **`make dev` таргет в корневом Makefile платформы.** Только в проектном Makefile (через `gen_project_makefile`).

$END_DEVPLAN
