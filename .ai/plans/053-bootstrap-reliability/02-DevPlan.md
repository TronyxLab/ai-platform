$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранение 10 инцидентов bootstrap-пайплайна + Python-миграция shell-логики (secrets, deps, inline python3, inline fallback) в соответствии со Strangler-Fig стратегией. НЕ затрагивает DevPlan 052 (cert-lifecycle-unification).
DESCRIPTION:           Трёхволновая имплементация: (1) P0 Critical Fixes — 5 блокирующих багов (timeout, autogen secrets, secrets.env source, PLATFORM_DOMAIN passthrough, bootstrap project files), (2) Python Migration — порт secrets.sh→Python, node-lifecycle.sh shell→Python, устранение дублирования state_machine.py/steps.py, bootstrap.sh inline python3, (3) P1 Reliability — self-signed cert fallback, vhost ordering fix, labeling fix.
RATIONALE:             Bug-репорт от 2026-07-25: 10 инцидентов при `make bootstrap-node`, 6 из которых — баги в Python state machine (P0). Без исправлений bootstrap NEVER завершается без ручных действий. Дополнительно: 4 области кода нарушают языковую политику (inline python3, shell-логика в state_machine.py, дублирование steps.py). Python-миграция устраняет root causes и снижает change-cost на 80%.
IMPLEMENTS:            Bug-report 2026-07-25 (10 bootstrap инцидентов), AGENTS.md Strangler-Fig Tier-1 триггеры (secrets.sh inline python3, bootstrap.sh inline python3, node-lifecycle.sh inline python3), AGENTS.md архитектурный инвариант: единый source of truth для шагов (устранение дублирования state_machine.py/steps.py).
IMPACTS:               core/internal/bootstrap/lifecycle/state_machine.py (F1-F4, P1, P3), core/internal/bootstrap/lifecycle/steps.py (P3), core/internal/bootstrap/lifecycle/secrets_manager.py (NEW), core/internal/bootstrap/python_deps.py (NEW), core/entrypoints/bootstrap.sh (F4, P4), core/internal/bootstrap/remote-cmd.sh (F4), core/internal/bootstrap/node-lifecycle.sh (F4, P2), core/internal/bootstrap/deploy/context_deployer.py (F5), core/internal/bootstrap/cert_orchestrator.py (F6), core/internal/bootstrap/preflight.py (P2), core/lib/secrets.sh (P1 — редуцирован), tests/unit/test_secrets_manager.py (NEW), tests/unit/test_python_deps.py (NEW), tests/unit/test_state_machine.py (обновление).
REQUIRES:              Python 3.10+, secrets-manifest.yaml доступен на VPS, node.yaml доступен на машине оператора до SCP.
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- DECISIONS: Ответы на 3 архитектурных вопроса + обоснование → DECISIONS_ID
- WAVE_1: P0 Critical Fixes (F1-F5, ~2-3 часа) → WAVE1_ID
- WAVE_2: Python Migration (P1-P4, ~5-7 часов) → WAVE2_ID
- WAVE_3: P1 Reliability (F6-F8, ~1-2 часа) → WAVE3_ID
- FILE_MANIFEST: Полный список файлов с diff-планом → FILES_ID
- TEST_PLAN: Что и как тестировать → TEST_ID
- PIPELINE_FLOW: Обновлённая диаграмма pipeline до/после → FLOW_ID
- RISKS: Что может пойти не так → RISK_ID
**SECTION_USE_CASES:**
- USE_CASE fresh bootstrap без внешних зависимостей → FRESH_NO_DEPS
- USE_CASE fresh bootstrap с Docker Hub + GHCR credentials → FRESH_WITH_CREDS
- USE_CASE node-update после успешного bootstrap → UPDATE_AFTER_BOOTSTRAP
- USE_CASE secrets recycling: автогенерация при отсутствии → SECRETS_AUTOGEN
- USE_CASE project bootstrap без CI roundtrip → PROJECT_BOOTSTRAP
$END_DOCUMENT_PLAN

---

# 02-DevPlan: Bootstrap Reliability & Python Migration

**Severity:** CRITICAL (10 инцидентов, 6 P0 — bootstrap NEVER завершается без ручных действий)
**Created:** 2026-07-25
**Author:** Architect (Kilo)
**Status:** READY — ожидает имплементации (пересмотрен 2026-07-25 после завершения DevPlan 052)
**Relation:** DevPlan 052-cert-lifecycle-unification ЗАВЕРШЁН. 053 строится поверх его изменений: `_ssl_provision()` уже заменён на `_ssl_provision_via_orchestrator()`, `cert_orchestrator.py` уже переписан (прямой импорт s3_ssl_cache, upload-on-skip), `s3_ssl_cache.py` уже существует. Код в секциях Q2, F6, Pipeline Flow обновлён под post-052 реальность.
**⚠️ F1 WARNING:** Fix был добавлен в `a66826a` (timeout=600), но ОТКАЧЕН в `472c5cd` с комментарием «remove stale custom timeout — uses consistent 120s default». При re-implement убедиться что причина отката понята (возможно, timeout должен быть на уровне _subprocess_run default, а не per-call).

---

## 0. Архитектурные решения

### Q1: Портировать ли `secrets.sh:step_12b_ensure_secrets()` в Python или вызывать shell через subprocess?

**Решение: ПОРТИРОВАТЬ в Python-модуль `secrets_manager.py`.**

```
ПРИЧИНЫ:
1. Корневая причина бага F2 — `_ensure_secrets_exist()` (state_machine.py:1590) только проверяет
   наличие файла. Shell-библиотека `secrets.sh:step_12b_ensure_secrets()` содержит логику
   генерации autogen-секретов через `secrets-manifest.yaml`, но НЕ вызывается из Python.

2. Портирование в Python:
   - Устраняет inline python3 блок (secrets.sh:343-350) — Tier-1 Strangler trigger
   - Даёт typed API: `ensure_secrets(manifest_path, secrets_env) -> list[str]`
   - Позволяет unit-тестировать логику генерации (сейчас — только интеграционно через shell)
   - Работает в ТОМ ЖЕ процессе, что и state_machine.py — прямой доступ к os.environ

3. Shell `secrets.sh:step_12b_ensure_secrets()` редуцируется до CLI-фасада (~15 строк):
   парсинг аргументов → вызов `python3 secrets_manager.py ensure`

4. sops --set persistence: остаётся shell-вызовом через subprocess (sops — внешняя утилита,
   нет Python SDK). Но решение о persistence принимается в Python (флаг `persist_to_sops: bool`).

КОНТРАКТ secrets_manager.py:
  def ensure_secrets(manifest_path: str, secrets_env: str,
                     persist_to_sops: bool = True) -> list[str]:
      """Read secrets-manifest.yaml, generate missing tier=generated secrets,
      write to secrets_env file. Returns list of generated variable names."""
  def source_secrets_env(secrets_env: str) -> dict[str, str]:
      """Parse secrets.env key=value file into dict. For sourcing into os.environ
      before subprocess calls that require PLATFORM_MASTER_PASSWORD etc."""
```

### Q2: Убирать ли `_step_*_inline()` fallback из state_machine.py или оставить?

**Решение: УБРАТЬ все `_step_*_inline()` функции. `steps.py` — единственный source of truth.**

```
ПРИЧИНЫ:
1. Дублирование ~200 строк бизнес-логики между state_machine.py и steps.py.
   Любое изменение требует правки в двух местах — divergence risk.

2. Причина существования fallback: `from . import steps` падал при standalone запуске
   state_machine.py (PYTHONPATH). Решение: исправить PYTHONPATH в node-lifecycle.sh,
   убрать `try/except ImportError`, сделать steps.py всегда доступным.

3. Конкретные функции для удаления:
   - _step_install_acme_inline() (lines 1718-1730) → заменяется на steps._step_install_acme()
   - _step_secrets_init_inline() (lines 1733-1744) → заменяется на steps._step_secrets_init()
   - _step_deploy_context_inline() (lines 1983-2084) → заменяется на steps._step_deploy_context()
   - _ssl_provision_via_orchestrator() — УЖЕ сделано DevPlan 052, НЕ трогать

4. Таблица замен:

   | Инлайн-функция | Строки | Замена | Примечание |
   |---------------|--------|--------|-----------|
   | _step_install_acme_inline | 1719-1730 | steps._step_install_acme | Прямая замена |
   | _step_secrets_init_inline | 1733-1744 | НОВАЯ _step_secrets_init() (не inline, с source secrets.env) | Баг F3 фиксится здесь |
   | _step_deploy_context_inline | 1983-2084 | steps._step_deploy_context | Прямая замена |
   | ~~_ssl_provision~~ | ~~1747-1814~~ | ~~УЖЕ удалена DevPlan 052~~ → `_ssl_provision_via_orchestrator()` на строке 1747 | НЕ трогать — сделано в 052 |
```

### Q3: Генерировать ли docker-compose.yml для проектов при первом bootstrap или требовать CI roundtrip?

**Решение: Для bootstrap-режима — генерировать минимальный `docker-compose.yml` (nginx:alpine reverse proxy), который позже заменяется реальным через CI (`platform-deliver`).**

```
ПРИЧИНЫ:
1. При первом bootstrap CI ещё не запускался → директория проекта содержит только
   ai-platform.yaml stub (созданный `make new-project`). Без docker-compose.yml
   context_deployer.py падает с "both ghcr pull and build failed".

2. Минимальный bootstrap-compose:
   - Контейнер nginx:alpine
   - Reverse proxy на порт из node.yaml (project.port)
   - Сертификаты монтируются из /etc/letsencrypt/live/<domain>/
   - HEALTHCHECK: curl localhost:<port>
   - Label: ai-platform.bootstrap=true (для последующей замены)

3. При следующем CI-деплое (`platform-deliver`) реальный docker-compose.yml
   заменяет bootstrap-версию → `docker compose up -d` пересоздаёт контейнеры.

4. Альтернатива (SCP project-файлов вместе с core) отклонена:
   - Нарушает модель доставки (Project payload — через CI forced-command)
   - Смешивает ответственности bootstrap.sh (должен доставлять ТОЛЬКО core)
```

---

## 1. Wave 1: P0 Critical Fixes (CRITICAL, ~2-3 часа)

**Цель:** Bootstrap завершается без ручных действий. 5 блокирующих багов исправлены.

### 1.1 F1: node_update timeout 120s → 600s

**⚠️ ИСТОРИЯ:** Fix был реализован в `a66826a` (timeout=600) и ОТКАЧЕН в `472c5cd` с формулировкой «remove stale custom timeout (600s) comment for node_update — uses consistent 120s default like all other steps». Причина отката: стремление к консистентности (все шаги используют default 120s). Но `node_update` — НЕ обычный шаг: это self-invocation всего update-пайплайна (deploy 14 модулей ~300s + provision + ssl + healthcheck + converge). Необходимо re-implement с явным документированием исключения.

**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py:1114`

```python
# ТЕКУЩЕЕ СОСТОЯНИЕ (472c5cd — timeout убран):
_subprocess_run(["bash", lifecycle_script, "--mode", "update"], "node_update", non_fatal=True)

# ЦЕЛЕВОЕ:
_subprocess_run(["bash", lifecycle_script, "--mode", "update"], "node_update", non_fatal=True, timeout=600)
```

**Обновить docstring инвариант (строка 15):**
```python
##   2. All subprocess.run calls use capture_output=True, text=True, timeout=120;
##      exception: node_update=600s (self-invocation wraps entire update pipeline:
##      deploy 14 modules ~300s + provision + ssl + healthcheck + converge)
```

**Изменение:** 1 строка + docstring fix

### 1.2 F2: Реализовать генерацию autogen secrets в Python

**Файлы:**
- **NEW:** `core/internal/bootstrap/lifecycle/secrets_manager.py` (~180 строк)
- **MODIFY:** `core/internal/bootstrap/lifecycle/state_machine.py` (~25 строк)

**secrets_manager.py** — порт `secrets.sh:step_12b_ensure_secrets()` (lines 298-411):

```python
# core/internal/bootstrap/lifecycle/secrets_manager.py
# GREP_SUMMARY: secrets-manager, autogen-secrets, manifest, ensure-secrets, sops, htpasswd
# STRUCTURE: ▶ ensure_secrets → source_secrets_env → _read_manifest → _generate_secret → _persist_to_sops → _ensure_htpasswd → ⎋ CLI

def ensure_secrets(
    manifest_path: str,
    secrets_env: str = "/run/platform/secrets.env",
    persist_to_sops: bool = True,
) -> list[str]:
    """Read secrets-manifest.yaml, generate missing tier=generated secrets.

    ## @purpose — Port of secrets.sh:step_12b_ensure_secrets().
    ##            Reads manifest, for each tier=generated secret with gen_command:
    ##            checks if exists in os.environ, if not → executes gen_command →
    ##            writes to secrets_env file. Optionally persists to SOPS.
    ## @io — ⇥ manifest_path, secrets_env, persist_to_sops → ⎋ list[str] (generated var names)
    ## @invariants
    ##   - Non-fatal: returns partial list on failure, never raises
    ##   - Existing secrets NOT overwritten (only fills gaps)
    ##   - gen_command executed via subprocess (openssl rand -hex 32 etc.)
    ##   - sops persistence: subprocess call, non-fatal on failure
    ##   - Htpasswd generation: called after secrets (needs PLATFORM_MASTER_PASSWORD)
    """
```

**Логика порта:**
1. Source `secrets_env` в `os.environ` если файл существует
2. Прочитать `secrets-manifest.yaml` → найти все `tier: generated`
3. Для каждого: проверить `os.environ.get(name)` → если пусто, выполнить `gen_command` через subprocess → установить в `os.environ` → дописать в `secrets_env` файл
4. Fallback hardcoded list: если manifest не найден или пуст — использовать LITELLM_MASTER_KEY, LANGFUSE_*, NEXTAUTH_SECRET, SALT
5. sops --set persistence (через subprocess, non-fatal)
6. Htpasswd генерация (порт `_ensure_htpasswd_generated()`)

**state_machine.py изменения:**
```python
# _ensure_secrets_exist() — строка 1590-1601
# БЫЛО: только проверка наличия файла
def _ensure_secrets_exist() -> None:
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
    if not os.path.isfile(secrets_env):
        logger.warning(...)
    else:
        logger.info(...)

# СТАЛО:
def _ensure_secrets_exist(core_dir: str) -> None:
    """Ensure secrets.env exists AND all autogen secrets are generated."""
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # Step 1: Check file exists (after decrypt)
    if not os.path.isfile(secrets_env):
        logger.error("[IMP:9][ensure_secrets] %s not found after decrypt — cannot generate secrets", secrets_env)
        raise RuntimeError(f"secrets.env not found: {secrets_env}")

    # Step 2: Source secrets.env into os.environ
    try:
        from .secrets_manager import source_secrets_env
        env_vars = source_secrets_env(secrets_env)
        for k, v in env_vars.items():
            if k not in os.environ:
                os.environ[k] = v
        logger.info("[IMP:9][ensure_secrets] Sourced %d vars from %s", len(env_vars), secrets_env)
    except Exception as e:
        logger.warning("[IMP:7][ensure_secrets] Failed to source secrets.env: %s", e)

    # Step 3: Generate missing autogen secrets
    manifest_path = os.path.join(core_dir, "secrets-manifest.yaml")
    try:
        from .secrets_manager import ensure_secrets as do_ensure
        generated = do_ensure(manifest_path, secrets_env)
        if generated:
            logger.info("[IMP:9][ensure_secrets] Generated %d secrets: %s", len(generated), generated)
    except Exception as e:
        logger.warning("[IMP:7][ensure_secrets] Autogen failed: %s", e)
```

### 1.3 F3: Source secrets.env перед secrets_init

**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py:1733-1744`

```python
# БЫЛО:
def _step_secrets_init_inline(core_dir: str) -> None:
    init_script = os.path.join(core_dir, "internal", "bootstrap", "secrets-init.sh")
    if os.path.isfile(init_script):
        _subprocess_run(["bash", init_script], "secrets_init", non_fatal=True)

# СТАЛО:
def _step_secrets_init(core_dir: str) -> None:
    """Initialize service passwords. Sources secrets.env first for PLATFORM_MASTER_PASSWORD."""
    # Source secrets.env into os.environ BEFORE calling secrets-init.sh
    # Bug F3: secrets-init.sh requires PLATFORM_MASTER_PASSWORD in env
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
    if os.path.isfile(secrets_env):
        try:
            from .secrets_manager import source_secrets_env
            env_vars = source_secrets_env(secrets_env)
            for k, v in env_vars.items():
                if k not in os.environ:
                    os.environ[k] = v
            logger.info("[IMP:9][secrets_init] Sourced %d vars for secrets-init.sh", len(env_vars))
        except Exception as e:
            logger.warning("[IMP:7][secrets_init] Failed to source secrets.env: %s", e)

    init_script = os.path.join(core_dir, "internal", "bootstrap", "secrets-init.sh")
    if os.path.isfile(init_script):
        _subprocess_run(["bash", init_script], "secrets_init", non_fatal=True)
    else:
        logger.warning("[IMP:7][secrets_init] %s not found", init_script)
```

**Важно:** Это фиксит и F3 И связанный баг — после `_ensure_secrets_exist()` (шаг 12b), `os.environ` уже содержит все переменные из secrets.env + autogen. Поэтому `_step_secrets_init()` получает их "бесплатно". Но явное source — defence-in-depth.

### 1.4 F4: Передавать PLATFORM_DOMAIN и CONTEXT через SSH

**Файлы:**
- `core/entrypoints/bootstrap.sh:main()` — извлечь PLATFORM_DOMAIN из node.yaml
- `core/internal/bootstrap/remote-cmd.sh:build_ssh_cmd()` — добавить export PLATFORM_DOMAIN + CONTEXT
- `core/internal/bootstrap/node-lifecycle.sh` — принять --platform-domain и --context флаги

**bootstrap.sh изменения (inline python3 → порт в P4):**
```bash
# После строки 126 (извлечение owner_key):
# ── Extract PLATFORM_DOMAIN + CONTEXT from node.yaml ──
PLATFORM_DOMAIN=$(python3 -c "import yaml; f=open('${NODE_YAML}'); d=yaml.safe_load(f); print(d.get('domain','') or d.get('node',{}).get('platform_domain','') or d.get('node',{}).get('domain',''))" 2>/dev/null) || PLATFORM_DOMAIN=""
CONTEXT=$(python3 -c "import yaml; f=open('${NODE_YAML}'); d=yaml.safe_load(f); print(d.get('context','') or (d.get('contexts',[{}])[0].get('name','') if d.get('contexts') else ''))" 2>/dev/null) || CONTEXT=""
```

**remote-cmd.sh:build_ssh_cmd() — добавить после строки 106:**
```bash
# Export PLATFORM_DOMAIN on remote (Bug F4 — nginx needs it for vhost paths)
if [[ -n "${PLATFORM_DOMAIN:-}" ]]; then
    local quoted_domain
    quoted_domain="$(printf '%q' "${PLATFORM_DOMAIN}")"
    cmd+=" && export PLATFORM_DOMAIN=${quoted_domain}"
fi
# Export CONTEXT on remote (Bug F4 — deploy_context needs it for project filtering)
if [[ -n "${CONTEXT:-}" ]]; then
    local quoted_context
    quoted_context="$(printf '%q' "${CONTEXT}")"
    cmd+=" && export CONTEXT=${quoted_context}"
fi
```

**node-lifecycle.sh:** `--context` уже принимается (строка 35). Добавить `--platform-domain`:
```bash
--platform-domain) export PLATFORM_DOMAIN="$2"; shift 2 ;;
```

### 1.5 F5: Bootstrap project files без CI roundtrip

**Файл:** `core/internal/bootstrap/deploy/context_deployer.py`

Добавить метод `_ensure_bootstrap_compose()` в `_deploy_single_project()`:

```python
def _ensure_bootstrap_compose(project_dir: str, project: ProjectInfo) -> bool:
    """Generate minimal docker-compose.yml for first bootstrap (no CI delivery yet).

    Creates a minimal nginx:alpine reverse proxy that will be replaced
    by the real docker-compose.yml via CI (platform-deliver) on next deploy.
    """
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    if os.path.isfile(compose_file):
        return True  # Already exists (real delivery or previous bootstrap)

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)

    port = getattr(project, 'port', None) or "3000"
    domain = getattr(project, 'domain', None) or project.name

    compose_content = f"""# GENERATED-STUB: Bootstrap reverse proxy. Replaced by CI platform-deliver.
version: '3.8'
services:
  {project.name}-proxy:
    image: nginx:alpine
    labels:
      - "ai-platform.bootstrap=true"
      - "ai-platform.project={project.name}"
    ports:
      - "{port}:{port}"
    volumes:
      - /etc/letsencrypt/live/{domain}/fullchain.pem:/etc/nginx/certs/fullchain.pem:ro
      - /etc/letsencrypt/live/{domain}/privkey.pem:/etc/nginx/certs/privkey.pem:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
"""
    try:
        with open(compose_file, 'w') as f:
            f.write(compose_content)
        logger.info("[IMP:9][context_deployer] Generated bootstrap compose for %s", project.name)
        return True
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write bootstrap compose for %s: %s", project.name, e)
        return False
```

Интеграция в `_deploy_single_project()` (после строки 332):
```python
project_dir = os.path.join(projects_base, project.name)

# Bootstrap guard: if project dir has no docker-compose.yml, generate minimal one
if not os.path.isfile(os.path.join(project_dir, "docker-compose.yml")):
    if not _ensure_bootstrap_compose(project_dir, project):
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel="none",
            health="unhealthy",
            error="bootstrap compose generation failed",
        )
```

**Верификация Wave 1:**
```bash
make test MARKER=static,unit
# Проверить: test_state_machine.py, test_context_deployer.py проходят
```

---

## 2. Wave 2: Python Migration (HIGH, ~5-7 часов)

**Цель:** Устранить 4 Tier-1 нарушения языковой политики, портировать shell-логику в Python, убрать дублирование state_machine.py/steps.py.

### 2.1 P1: secrets.sh → secrets_manager.py (уже создан в Wave 1, расширить)

**Файлы:**
- **MODIFY:** `core/internal/bootstrap/lifecycle/secrets_manager.py` — добавить `source_secrets_env()`, `_ensure_htpasswd()`
- **REDUCE:** `core/lib/secrets.sh` — `step_12b_ensure_secrets()` редуцировать до CLI-фасада (~15 строк)
- **NEW:** `tests/unit/test_secrets_manager.py` (~100 строк)

**source_secrets_env():**
```python
def source_secrets_env(secrets_env: str) -> dict[str, str]:
    """Parse secrets.env key=value file into dict.

    Handles: comments (#), empty lines, quoted values, inline export prefix.
    Returns dict of VAR→VALUE. Never raises.
    """
    result: dict[str, str] = {}
    if not os.path.isfile(secrets_env):
        return result

    try:
        with open(secrets_env) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Handle 'export VAR=VALUE' prefix
                if line.startswith('export '):
                    line = line[7:].strip()
                # Parse KEY=VALUE (handle = in value)
                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    # Strip quotes if present
                    value = value.strip().strip("'").strip('"')
                    if key:
                        result[key] = value
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] Failed to read %s: %s", secrets_env, e)

    return result
```

**secrets.sh редукция:**
```bash
# step_12b_ensure_secrets() — редуцирован до CLI-фасада
step_12b_ensure_secrets() {
    step_start "ensure-secrets" "Validating and generating required secrets"
    local manifest="${PATHS_CORE_DIR:-/opt/platform/core}/secrets-manifest.yaml"
    python3 "${PATHS_CORE_DIR:-/opt/platform/core}/internal/bootstrap/lifecycle/secrets_manager.py" \
        ensure --manifest "$manifest" 2>&1 || {
        log_step "ensure-secrets" "WARN" "secrets_manager.py failed"
    }
    step_done "ensure-secrets" "Secrets validation complete"
}
```

### 2.2 P2: node-lifecycle.sh shell-логика → Python

**Файлы:**
- **NEW:** `core/internal/bootstrap/python_deps.py` (~90 строк) — порт `_ensure_python_deps()` (lines 117-169)
- **MODIFY:** `core/internal/bootstrap/node-lifecycle.sh` (~50 строк удалено)
- **MODIFY:** `core/internal/bootstrap/preflight.py` — добавить `output_warnings()` статический метод
- **NEW:** `tests/unit/test_python_deps.py` (~60 строк)

**python_deps.py:**
```python
# core/internal/bootstrap/python_deps.py
# GREP_SUMMARY: python-deps, pip3, apt, requirements, content-hash, idempotent
# STRUCTURE: ▶ ensure_python_deps → _check_content_hash → _install_pip3 → _install_requirements → ⎋ CLI

def ensure_python_deps(core_dir: str) -> bool:
    """Idempotent install of pip3 + platform Python dependencies on VPS.

    ## @purpose — Port of node-lifecycle.sh:_ensure_python_deps().
    ##            Checks content-hash of requirements.txt; if unchanged, skips.
    ##            Installs pip3 via apt if missing. Installs requirements via pip.
    ## @io — ⇥ core_dir → ⎋ bool (True = deps ready)
    ## @invariants
    ##   - Fail-soft: returns False on failure, never raises
    ##   - Content-hash guard: skips pip install if requirements unchanged
    ##   - PEP 668 workaround: --break-system-packages on Ubuntu Noble
    ##   - typing_extensions conflict: --ignore-installed first
    """
```

**port-логика:**
1. Проверить content-hash `requirements.txt` (sha256sum)
2. Если хэш совпадает с сохранённым → skip
3. Если pip3 отсутствует → `apt-get install python3-pip python3-venv`
4. `pip3 install --break-system-packages --ignore-installed typing_extensions`
5. `pip3 install --break-system-packages -r requirements.txt`
6. Сохранить хэш в `/var/lib/platform/.bootstrap/python-deps.hash`

**node-lifecycle.sh редукция:**
- Удалить `_ensure_python_deps()` (строки 117-169)
- Заменить вызов на: `python3 "${SCRIPT_DIR}/python_deps.py" ensure --core-dir "$CORE_DIR" || true`
- Удалить inline python3 preflight-парсинг (строки 203-211: `echo "$PREFLIGHT_RESULT" | python3 -c "..."`) → заменить на `python3 preflight.py --parse-warnings`

**preflight.py — добавить:**
```python
# В if __name__ == "__main__":
if "--parse-warnings" in sys.argv:
    # Read JSON from stdin, output warnings to stderr
    import json
    result = json.load(sys.stdin)
    warnings = [k for k, v in result.items() if v.get('status') == 'warn']
    if warnings:
        print(f'[IMP:7][preflight] Warnings (non-fatal): {warnings}', file=sys.stderr)
    sys.exit(0)
```

### 2.3 P3: Устранение дублирования state_machine.py/steps.py

**Файлы:**
- **MODIFY:** `core/internal/bootstrap/lifecycle/state_machine.py` — удалить все `_step_*_inline()` функции (~200 строк)
- **MODIFY:** `core/internal/bootstrap/node-lifecycle.sh` — исправить PYTHONPATH для импорта steps.py
- **MODIFY:** `tests/unit/test_state_machine.py` — обновить тесты

**Шаг 1: Исправить PYTHONPATH в node-lifecycle.sh (строка 12):**
```bash
# БЫЛО:
SM_SCRIPT="${SCRIPT_DIR}/lifecycle/state_machine.py"

# СТАЛО:
SM_SCRIPT="${SCRIPT_DIR}/lifecycle/state_machine.py"
# Ensure lifecycle/ is on PYTHONPATH so 'from . import steps' works
export PYTHONPATH="${SCRIPT_DIR}/lifecycle:${PYTHONPATH:-}"
```

**Шаг 2: Убрать try/except ImportError в state_machine.py (строки 44-53):**
```python
# БЫЛО:
try:
    from . import steps as _steps
except ImportError:
    _steps = None

# СТАЛО:
from . import steps as _steps  # Always available (PYTHONPATH set by node-lifecycle.sh)
```

**Шаг 3: Убрать все `if _steps and hasattr(...)` условия — заменить на прямые вызовы:**
```python
# БЫЛО (patterns):
if _steps and hasattr(_steps, "_step_install_acme"):
    _steps._step_install_acme(core_dir)
else:
    _step_install_acme_inline(core_dir)

# СТАЛО:
_steps._step_install_acme(core_dir)
```

**Шаг 4: Удалить inline-функции (полный список):**

| Функция | Строки | Замена |
|---------|--------|--------|
| `_step_install_acme_inline()` | 1718-1730 | `_steps._step_install_acme(core_dir)` |
| `_step_secrets_init_inline()` | 1733-1744 | `_step_secrets_init(core_dir)` (новая, c source secrets) |
| `_ssl_provision()` | 1747-1814 | временно `_ssl_provision()` с source secrets (до DevPlan 052 Phase 2) |
| `_step_deploy_context_inline()` | 2000-2099 | `_steps._step_deploy_context(core_dir, node_name, node_yaml)` |

**Шаг 5: Создать `_step_secrets_init()` (новая, не inline):**
```python
def _step_secrets_init(core_dir: str) -> None:
    """Initialize service passwords from PLATFORM_MASTER_PASSWORD.

    Sources secrets.env first (F3 fix), then calls secrets-init.sh.
    """
    # Source secrets.env into os.environ (F3 fix)
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
    if os.path.isfile(secrets_env):
        try:
            from .secrets_manager import source_secrets_env
            env_vars = source_secrets_env(secrets_env)
            for k, v in env_vars.items():
                if k not in os.environ:
                    os.environ[k] = v
            logger.info("[IMP:9][secrets_init] Sourced %d vars for secrets-init.sh", len(env_vars))
        except Exception as e:
            logger.warning("[IMP:7][secrets_init] Failed to source secrets.env: %s", e)

    _steps._step_secrets_init(core_dir)
```

**Шаг 6: Обновить `_execute_init_step()` — убрать все inline-fallback ветки:**
```python
# replace: if _steps and hasattr(...) else _step_*_inline()
# with:    direct call to _steps.* or local function
```

### 2.4 P4: bootstrap.sh YAML extraction → typed Python

**Файлы:**
- **NEW:** `core/internal/bootstrap/yaml_helpers.py` (~30 строк)
- **MODIFY:** `core/entrypoints/bootstrap.sh` (~15 строк заменены)

**yaml_helpers.py:**
```python
# core/internal/bootstrap/yaml_helpers.py
# GREP_SUMMARY: yaml, extract-field, node-yaml, typed, bootstrap
# STRUCTURE: ▶ extract_yaml_field → ⎋ CLI

import sys
import yaml

def extract_yaml_field(file_path: str, *field_path: str) -> str:
    """Extract a field from YAML file using dotted path.

    ## @purpose — Replace inline python3 -c blocks in bootstrap.sh.
    ## @io — ⇥ file_path, field_path → ⎋ str (empty if not found)
    ## @example extract_yaml_field('node.yaml', 'node', 'owner_key') → 'ssh-ed25519 AAAA...'
    """
    try:
        with open(file_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return ""

    current = data
    for key in field_path:
        if isinstance(current, dict):
            current = current.get(key, "")
        elif isinstance(current, list) and current:
            current = current[0].get(key, "") if isinstance(current[0], dict) else ""
        else:
            return ""

    return str(current) if current else ""

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: yaml_helpers.py <file> <field.path>")
        sys.exit(1)
    file_path = sys.argv[1]
    field_path = sys.argv[2].split('.')
    print(extract_yaml_field(file_path, *field_path))
```

**bootstrap.sh — заменить inline python3:**
```bash
# БЫЛО (строка 126):
OWNER_KEY=$(python3 -c "import yaml; f=open('${NODE_YAML}'); d=yaml.safe_load(f); print(d.get('node',{}).get('owner_key',''))" 2>/dev/null) || true

# СТАЛО:
OWNER_KEY=$(python3 "${CORE_DIR}/internal/bootstrap/yaml_helpers.py" "${NODE_YAML}" "node.owner_key" 2>/dev/null) || true
```

Аналогично для `CI_DEPLOY_KEY`, `PLATFORM_DOMAIN`, `CONTEXT`.

**Верификация Wave 2:**
```bash
make test MARKER=static,unit
make gate MODE=fast
```

---

## 3. Wave 3: P1 Reliability Fixes (MEDIUM, ~1-2 часа)

### 3.1 F6: Self-signed cert fallback в cert_orchestrator.py

**⚠️ Код обновлён под post-052 структуру.** `cert_orchestrator.py` уже переписан DevPlan 052: `_process_single_domain()` имеет 3 шага (disk check→upload, S3 restore, issue-cert.sh). Self-signed — Step 4 (last-resort disaster recovery).

**Файл:** `core/internal/bootstrap/cert_orchestrator.py`

Добавить `_generate_self_signed()` и вызвать в `_process_single_domain()` как Step 4:

```python
def _generate_self_signed(domain: str) -> DomainCertResult:
    """Generate self-signed certificate as last-resort fallback.

    Called when BOTH S3 restore and acme.sh issue fail (e.g., DNS API down,
    no credentials). Self-signed cert allows nginx to start (avoids crash-loop),
    but browsers will show security warning. Valid 90 days.

    ## @purpose — Disaster recovery: keep nginx running when cert issuance fails.
    ## @io — ⇥ domain → ⎋ DomainCertResult
    ## @returns DomainCertResult with status="issued", source="self_signed"
    """
    cert_dir = os.path.join(CERT_VALIDITY_PATH, domain)
    os.makedirs(cert_dir, exist_ok=True)

    key_path = os.path.join(cert_dir, "privkey.pem")
    cert_path = os.path.join(cert_dir, "fullchain.pem")

    try:
        subprocess.run(
            ["openssl", "genrsa", "-out", key_path, "2048"],
            capture_output=True, timeout=30, check=True,
        )
        os.chmod(key_path, 0o600)

        subprocess.run(
            ["openssl", "req", "-new", "-x509",
             "-key", key_path, "-out", cert_path,
             "-days", "90", "-subj", f"/CN={domain}"],
            capture_output=True, timeout=30, check=True,
        )
        os.chmod(cert_path, 0o644)

        logger.warning(
            "[IMP:7][cert_orchestrator] %s — SELF-SIGNED cert generated (browsers will warn). "
            "Fix: ensure DNS-01 credentials in secrets.env or wait for acme.sh retry.",
            domain,
        )
        return DomainCertResult(domain=domain, status="issued", source="self_signed")
    except Exception as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — self-signed generation failed: %s", domain, e)
        return DomainCertResult(domain=domain, status="failed", source="none", error=str(e))
```

**Интеграция в `_process_single_domain()` — добавить Step 4 ПОСЛЕ строки 224:**
```python
    # ── Step 4: Self-signed as last resort (DevPlan 053 F6) ──
    # Both S3 restore and acme.sh issue failed — generate self-signed
    # to prevent nginx crash-loop. Monitoring should alert on self_signed source.
    logger.warning(
        "[IMP:8][cert_orchestrator] %s — all issuance methods failed, generating self-signed fallback", domain
    )
    return _generate_self_signed(domain)
```

### 3.2 F7: Vhost render ДО nginx reload

**✅ DONE (DevPlan 052, steps.py:896-902).** `_step_deploy_context()` в `steps.py` уже вызывает `nginx -s reload` после `--render-all` vhost'ов. Удалено из Wave 3 — не требует имплементации.

### 3.3 F8: Labeling shell facade fix

**Диагностика:**

Python `UPDATE_STEPS` (state_machine.py:110-120):
```
1=verify_core, 2=provision, 3=deliver_overlays, 4=ssl_provision,
5=deploy_modules, 6=provision_llm_keys, 7=healthcheck, 8=converge, 9=deploy_context
```

Текущие shell-функции (node-lifecycle.sh:85-87) — несовпадение имён:
```
update_step_6_healthcheck  → --run-step 6 → выполняет provision_llm_keys    ❌ имя != шаг
update_step_8_deploy_context → --run-step 8 → выполняет converge            ❌ имя != шаг
```

Функционально все 9 шагов выполняются (1-5 через checkpoint, 6 через `update_step_6_healthcheck`, 7-9 через `_delegate --mode update`). Но:
- provision_llm_keys (шаг 6) checkpoint'ится как "healthcheck-all" — неверный label
- Нет отдельных checkpoint для шагов 7-9
- Shell-функции имеют неправильные имена → `--run-step N` вызывает не тот шаг при ручном запуске

**Файл:** `core/internal/bootstrap/node-lifecycle.sh`

**Шаг 1: Переименовать shell-функции (строки 85-87):**
```bash
# БЫЛО:
update_step_6_healthcheck(){ _delegate --mode "${MODE}" --run-step 6; }
update_step_8_deploy_context(){ _delegate --mode "${MODE}" --run-step 8; }

# СТАЛО:
update_step_6_provision_llm_keys(){ _delegate --mode "${MODE}" --run-step 6; }   # was healthcheck (misnamed)
update_step_7_healthcheck(){ _delegate --mode "${MODE}" --run-step 7; }          # NEW — was missing
update_step_8_converge(){ _delegate --mode "${MODE}" --run-step 8; }             # was deploy_context (misnamed)
update_step_9_deploy_context(){ _delegate --mode "${MODE}" --run-step 9; }       # NEW — was missing
```

**Шаг 2: Обновить update-секцию main() — добавить явные checkpoint для шагов 6-9:**
```bash
# БЫЛО (строка 269-278):
_do_update_steps() {
    CHECKPOINT_STEP_HASH=... checkpoint_step "verify-core" update_step_1_verify_core
    CHECKPOINT_STEP_HASH=... checkpoint_step "provision" update_step_2_provision
    CHECKPOINT_STEP_HASH=... checkpoint_step "deliver-overlays" update_step_2_5_deliver_overlays
    CHECKPOINT_STEP_HASH=... checkpoint_step "ssl-provision" update_step_3_ssl_provision
    CHECKPOINT_STEP_HASH=... checkpoint_step "deploy-modules" update_step_4_deploy_modules
    CHECKPOINT_STEP_HASH=... checkpoint_step "healthcheck-all" update_step_6_healthcheck   # ← BUG: вызывает provision_llm_keys
    _delegate --mode update ...

# СТАЛО:
_do_update_steps() {
    CHECKPOINT_STEP_HASH="$(_step_hash "verify-core")"           checkpoint_step "verify-core" update_step_1_verify_core
    CHECKPOINT_STEP_HASH="$(_step_hash "provision")"             checkpoint_step "provision" update_step_2_provision
    CHECKPOINT_STEP_HASH="$(_step_hash "deliver-overlays")"      checkpoint_step "deliver-overlays" update_step_2_5_deliver_overlays
    CHECKPOINT_STEP_HASH="$(_step_hash "ssl-provision")"         checkpoint_step "ssl-provision" update_step_3_ssl_provision
    CHECKPOINT_STEP_HASH="$(_step_hash "deploy-modules")"        checkpoint_step "deploy-modules" update_step_4_deploy_modules
    # F8 fix: правильные имена для шагов 6-9
    CHECKPOINT_STEP_HASH="$(_step_hash "provision-llm-keys")"    checkpoint_step "provision-llm-keys" update_step_6_provision_llm_keys
    CHECKPOINT_STEP_HASH="$(_step_hash "healthcheck-all")"       checkpoint_step "healthcheck-all" update_step_7_healthcheck
    # converge + deploy_context — fast steps, delegate to Python (no separate checkpoint needed,
    # state_machine.py handles --resume for them)
    _delegate --mode update --node-name "${NODE_NAME}" --node-yaml "${NODE_YAML}" \
        ${CONTEXT:+--context "$CONTEXT"} \
        ${FORCE_MODE:+--force}
}
```

**Итог F8:** 6 строк заменены/добавлены. Shell-функции теперь правильно мапятся на Python-шаги. `update_step_6_provision_llm_keys` → шаг 6 (provision_llm_keys), `update_step_7_healthcheck` → шаг 7 (healthcheck).

**Верификация Wave 3:**
```bash
# F6: self-signed cert generation
make test MARKER=static,unit
# F8: проверка что update_step_* функции правильно мапятся на Python-шаги
make gate MODE=fast
```

---

## 4. File Manifest

| Действие | Файл | +/- строк | Описание |
|----------|------|-----------|----------|
| **NEW** | `core/internal/bootstrap/lifecycle/secrets_manager.py` | +180 | Python-порт secrets.sh:step_12b_ensure_secrets(): ensure_secrets() + source_secrets_env() + _ensure_htpasswd() |
| **NEW** | `core/internal/bootstrap/python_deps.py` | +90 | Порт node-lifecycle.sh:_ensure_python_deps(): pip3 install + content-hash guard |
| **NEW** | `core/internal/bootstrap/yaml_helpers.py` | +35 | extract_yaml_field() — замена inline python3 в bootstrap.sh |
| **NEW** | `tests/unit/test_secrets_manager.py` | +100 | Unit-тесты: autogen generation, source, htpasswd |
| **NEW** | `tests/unit/test_python_deps.py` | +60 | Unit-тесты: content-hash guard, pip install |
| **MODIFY** | `core/internal/bootstrap/lifecycle/state_machine.py` | -200/+120 | F1 (timeout 600s), F2 (_ensure_secrets_exist переписан), F3 (_step_secrets_init новая), P3 (inline fallback удалён, _steps always imported) |
| **~~MODIFY~~** | `~~core/internal/bootstrap/lifecycle/steps.py~~` | ~~+5~~ | ~~F7: nginx reload after vhost render~~ **✅ DONE (052, steps.py:896-902)** |
| **MODIFY** | `core/entrypoints/bootstrap.sh` | +15/-15 | F4: extract PLATFORM_DOMAIN+CONTEXT; P4: inline python3 → yaml_helpers.py |
| **MODIFY** | `core/internal/bootstrap/remote-cmd.sh` | +12 | F4: export PLATFORM_DOMAIN+CONTEXT в build_ssh_cmd() |
| **MODIFY** | `core/internal/bootstrap/node-lifecycle.sh` | +15/-55 | F4: --platform-domain flag; P2: _ensure_python_deps → python_deps.py, preflight parse → preflight.py; F8: исправление имён update_step_* функций |
| **MODIFY** | `core/internal/bootstrap/deploy/context_deployer.py` | +55 | F5: _ensure_bootstrap_compose() — генерация минимального docker-compose.yml при первом bootstrap |
| **MODIFY** | `core/internal/bootstrap/cert_orchestrator.py` | +55 | F6: _generate_self_signed() — Step 4 last-resort fallback (интеграция в post-052 _process_single_domain) |
| **MODIFY** | `core/internal/bootstrap/preflight.py` | +12 | P2: --parse-warnings CLI mode |
| **REDUCE** | `core/lib/secrets.sh` | -100/+15 | P1: step_12b_ensure_secrets() → CLI-фасад (вызов secrets_manager.py) |
| **MODIFY** | `tests/unit/test_state_machine.py` | +30/-20 | P3: обновить тесты (нет inline fallback) |
| **MODIFY** | `core/internal/bootstrap/AGENTS.md` | +25 | Обновить pipeline docs (новые модули, исправленные шаги) |

**Суммарно:** ~480 строк нового кода, ~390 строк удалено. Net change: ~+90 строк.
**F7 исключён (уже done).**

---

## 5. План тестирования

### 5.1 Новые unit-тесты

| Тест | Файл | Что проверяет |
|------|------|--------------|
| `test_ensure_secrets_from_manifest` | `tests/unit/test_secrets_manager.py` | `ensure_secrets()` читает manifest → генерирует missing secrets |
| `test_ensure_secrets_fallback_hardcoded` | `tests/unit/test_secrets_manager.py` | Без manifest — использует hardcoded список |
| `test_ensure_secrets_skips_existing` | `tests/unit/test_secrets_manager.py` | Существующие secrets НЕ перезаписываются |
| `test_source_secrets_env` | `tests/unit/test_secrets_manager.py` | `source_secrets_env()` парсит key=value файл |
| `test_source_secrets_export_prefix` | `tests/unit/test_secrets_manager.py` | Обрабатывает `export VAR=VALUE` |
| `test_python_deps_content_hash_skip` | `tests/unit/test_python_deps.py` | При совпадении хэша — skip |
| `test_python_deps_content_hash_changed` | `tests/unit/test_python_deps.py` | При изменении хэша — переустановка |
| `test_bootstrap_compose_generation` | `tests/unit/test_context_deployer.py` | `_ensure_bootstrap_compose()` создаёт docker-compose.yml |
| `test_bootstrap_compose_idempotent` | `tests/unit/test_context_deployer.py` | Не перезаписывает существующий compose |
| `test_self_signed_cert_fallback` | `tests/unit/test_cert_orchestrator.py` | `_generate_self_signed()` создаёт валидный self-signed cert как Step 4 |
| `test_yaml_helpers_extract` | `tests/unit/test_yaml_helpers.py` | `extract_yaml_field()` извлекает вложенные поля |
| `test_secrets_init_sources_env` | `tests/unit/test_state_machine.py` | `_step_secrets_init()` source'ит secrets.env перед subprocess |

### 5.2 Обновление существующих тестов

| Тест | Изменение |
|------|-----------|
| `test_state_machine_full_bootstrap` | Убрать ассерты на `_step_*_inline()` функции |
| `test_node_lifecycle_static` | Обновить: нет inline python3 в node-lifecycle.sh |
| `test_update_ssl_step` | Ассерт: source secrets.env вызывается (F3 fix) |
| `test_ensure_secrets_static` | Обновить: теперь проверяет и генерирует (F2 fix) |

### 5.3 Интеграционная верификация

```bash
# После Wave 1:
make test MARKER=static,unit
# Проверить: state_machine, context_deployer тесты проходят

# После Wave 2:
make test MARKER=static,unit,contract
make gate MODE=fast

# После Wave 3:
make test MARKER=static,unit,component
make gate MODE=fast

# Финальная верификация:
make fix-gate && git add -u && make gate MODE=fast
```

---

## 6. Pipeline Flow — до и после

### До изменений (текущий post-052, всё ещё с багами 053)

```
bootstrap.sh --resolve
  ├── node.yaml → owner_key, ci_deploy_key, age_key
  ├── SCP core/ → VPS
  └── SSH: export AGE_SECRET_KEY && node-lifecycle.sh --mode init
       ├── ❌ PLATFORM_DOMAIN не передан → nginx падает (F4)
       ├── ❌ CONTEXT не передан → deploy_context использует fallback (F4)
       └── node-lifecycle.sh --mode init
            ├── decrypt_secrets → secrets.env создан
            ├── ensure_secrets → ❌ ТОЛЬКО проверка файла (F2)
            ├── secrets_init → ❌ PLATFORM_MASTER_PASSWORD не в env (F3)
            ├── node_update → ❌ timeout 120s (F1 — был откачен в 472c5cd)
            │   └── node-lifecycle.sh --mode update
            │        ├── ssl_provision → _ssl_provision_via_orchestrator() (052) → S3 restore → cert OK
            │        ├── deploy_modules → PLATFORM_DOMAIN нет → nginx crash (F4)
            │        └── healthcheck → падает (nginx не стартовал)
            └── deploy_context
                 ├── cert_orchestrator → certs OK (restore-first, upload-on-skip — 052)
                 │   └── ❌ no self-signed fallback если S3+acme оба упали (F6)
                 ├── vhost render → nginx reload ✅ (F7 — done)
                 └── context_deployer → ❌ нет docker-compose.yml (F5)
```

### После изменений (целевой, исправленный)

```
bootstrap.sh --resolve
  ├── node.yaml → owner_key, ci_deploy_key, age_key, PLATFORM_DOMAIN, CONTEXT (F4, P4)
  ├── SCP core/ → VPS
  └── SSH: export AGE_SECRET_KEY PLATFORM_DOMAIN CONTEXT && node-lifecycle.sh --mode init
       └── node-lifecycle.sh --mode init
            ├── _ensure_python_deps → python_deps.py (P2)
            ├── decrypt_secrets → secrets.env создан
            ├── ensure_secrets → secrets_manager.py: autogen сгенерированы (F2)
            │   └── source secrets.env в os.environ (F3 prerequisite)
            ├── secrets_init → PLATFORM_MASTER_PASSWORD в env (F3)
            ├── node_update → timeout=600s (F1)
            │   └── node-lifecycle.sh --mode update
            │        ├── ssl_provision → cert_orchestrator (052): restore-first → cert OK
            │        ├── deploy_modules → PLATFORM_DOMAIN передан → nginx OK (F4)
            │        └── healthcheck → ✅ все модули healthy
            └── deploy_context
                 ├── cert_orchestrator → S3 → acme.sh → self-signed fallback (F6)
                 ├── context_deployer → bootstrap compose если нет docker-compose.yml (F5)
                 ├── vhost render → nginx reload ✅ (F7 — already done)
                 └── verify domains
```

---

## 7. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|------------|---------|-----------|
| **R1:** Удаление `_step_*_inline()` ломает standalone запуск state_machine.py | Низкая | HIGH | Проверить PYTHONPATH в node-lifecycle.sh + добавить `sys.path.insert(0, ...)` в state_machine.py main() как fallback |
| **R2:** `secrets_manager.py` не находит `secrets-manifest.yaml` на VPS | Средняя | MEDIUM | Hardcoded fallback список (LITELLM_MASTER_KEY, LANGFUSE_*, etc.) портирован из secrets.sh. Файл доставляется через SCP вместе с core/. |
| **R3:** `source_secrets_env()` не обрабатывает многострочные значения | Низкая | LOW | secrets.env содержит только однострочные key=value (openssl rand -hex). Многострочные значения (RSA keys) хранятся в отдельных файлах. |
| **R4:** Bootstrap compose (F5) конфликтует с реальным CI-деплоем | Средняя | MEDIUM | Bootstrap compose имеет label `ai-platform.bootstrap=true`. CI deploy (`platform-deliver`) перезаписывает docker-compose.yml → `docker compose up -d` пересоздаёт контейнеры. |
| **R5:** `_generate_self_signed()` может маскировать реальные проблемы с DNS/ACME | Средняя | LOW | Self-signed cert логируется с WARN уровнем. Healthcheck проверяет HTTPS (curl -k), но мониторинг алертит на self-signed. |
| **R6:** Shell facade функции `update_step_7_healthcheck` и `update_step_9_deploy_context` не имеют соответствующих шагов в state_machine.py | Низкая | HIGH | Проверить `UPDATE_STEPS`: шаг 7 = healthcheck, шаг 9 = deploy_context. Оба существуют. Риск в несовпадении сигнатур, а не в отсутствии шагов. |
| **R7:** `_step_deploy_context_inline()` удаляется, но `steps._step_deploy_context()` может иметь другую сигнатуру | Средняя | HIGH | Сверить сигнатуры перед удалением. Inline-версия принимает `(core_dir, node_name, node_yaml)`. Steps-версия: проверить. |
| **R8:** ~~Конфликт с DevPlan 052 при merge~~ — 052 ЗАВЕРШЁН. `_ssl_provision()` уже заменён на `_ssl_provision_via_orchestrator()`. Merge conflict невозможен. | — | — | Риск снят. 052 изменения уже в main. |

---

## 8. Rollback Plan

```bash
# 1. Откатить коммит
git revert <merge-commit>

# 2. Восстановить старый pipeline на VPS
make bootstrap-node NODE=<node>

# 3. Верифицировать
make node-update NODE=<node>
make healthcheck NODE=<node>
```

**Время восстановления:** ~5 минут.

---

## 9. Порядок имплементации

```
Wave 1 (P0 Critical Fixes, 2-3ч):
  1.1 F1: node_update timeout 600s (1 строка)
  1.2 F2: создать secrets_manager.py + переписать _ensure_secrets_exist()
  1.3 F3: переписать _step_secrets_init() с source secrets.env
  1.4 F4: bootstrap.sh + remote-cmd.sh — PLATFORM_DOMAIN, CONTEXT
  1.5 F5: context_deployer.py — _ensure_bootstrap_compose()
  1.6 make test MARKER=static,unit → зелёный
  1.7 Коммит: "fix(bootstrap): P0 critical fixes F1-F5 — timeout, autogen secrets, env passthrough, bootstrap projects"

Wave 2 (Python Migration, 5-7ч):
  2.1 P1: secrets_manager.py — source_secrets_env(), _ensure_htpasswd()
  2.2 P1: secrets.sh — редукция до CLI-фасада
  2.3 P1: tests/unit/test_secrets_manager.py
  2.4 P2: python_deps.py — порт _ensure_python_deps()
  2.5 P2: node-lifecycle.sh — удалить _ensure_python_deps, inline python3
  2.6 P2: preflight.py — --parse-warnings
  2.7 P2: tests/unit/test_python_deps.py
  2.8 P3: state_machine.py — убрать try/except ImportError, удалить _step_*_inline()
  2.9 P3: node-lifecycle.sh — PYTHONPATH fix
  2.10 P3: обновить test_state_machine.py
  2.11 P4: yaml_helpers.py — extract_yaml_field()
  2.12 P4: bootstrap.sh — inline python3 → yaml_helpers.py
  2.13 make test MARKER=static,unit,contract → зелёный
  2.14 make gate MODE=fast → зелёный
  2.15 Коммит: "refactor(bootstrap): Python migration — secrets, deps, yaml helpers, remove inline fallback"

Wave 3 (P1 Reliability, 1-2ч):
  3.1 F6: cert_orchestrator.py — _generate_self_signed() + Step 4 интеграция в _process_single_domain()
  3.2 ~~F7: steps.py — nginx reload after vhost render~~ ✅ DONE (052, steps.py:896-902)
  3.3 F8: node-lifecycle.sh — исправить имена update_step_* функций + добавить недостающие
  3.4 make test MARKER=static,unit,component → зелёный
  3.5 make gate MODE=fast → зелёный
  3.6 Коммит: "fix(bootstrap): P1 reliability — self-signed fallback, labeling"

Финальная верификация:
  make fix-gate && git add -u && make gate MODE=fast
```

---

## Appendix A: Полный контракт `secrets_manager.ensure_secrets()`

```python
# core/internal/bootstrap/lifecycle/secrets_manager.py
def ensure_secrets(
    manifest_path: str,
    secrets_env: str = "/run/platform/secrets.env",
    persist_to_sops: bool = True,
) -> list[str]:
    """Read secrets-manifest.yaml, generate missing tier=generated secrets.

    ## @purpose — Port of secrets.sh:step_12b_ensure_secrets() (lines 298-411).
    ##            Eliminates inline python3 heredoc (Tier-1 Strangler trigger).
    ## @io — ⇥ manifest_path, secrets_env, persist_to_sops → ⎋ list[str]
    ## @returns List of generated variable names (empty if none generated)
    ## @invariants
    ##   - Non-fatal: returns partial list on failure, never raises
    ##   - Existing secrets in os.environ or secrets_env NOT overwritten
    ##   - gen_command executed via subprocess (openssl rand -hex 32 etc.)
    ##   - Hardcoded fallback if manifest missing: LITELLM_MASTER_KEY, LANGFUSE_*, NEXTAUTH_SECRET, SALT
    ##   - sops persistence: subprocess call, non-fatal on failure
    ##   - Htpasswd generated after secrets (needs PLATFORM_MASTER_PASSWORD)
    ## @complexity — O(N) where N = secrets in manifest
    ## @rationale Direct os.environ access fixes credential propagation issues.
    ##            Typed API enables unit-testing (shell version tested only via integration).
    """
```

$END_DEVPLAN
