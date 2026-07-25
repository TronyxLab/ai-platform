$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить 2 ошибки, выявленные при node-update: P1 (pip3/pydantic), P2 (verify-domains.sh wrong URL)
DESCRIPTION:           P1: Установка pip3 + pydantic на VPS через bootstrap-шаг install_python_deps.
                       P2: Исправление verify-domains.sh — status-page health check на platform.${PLATFORM_DOMAIN}/health.
RATIONALE:             P1 блокирует provisioning LLM-ключей при каждом node-update. P2 даёт ложный FAIL в verify.
                       Минимальные хирургические фиксы без рефакторинга.
IMPLEMENTS:            Brief: .ai/plans/051-node-update-hotfix/01-Brief.md
IMPACTS:               core/internal/verify/verify-domains.sh, core/internal/bootstrap/node-lifecycle.sh, новый файл core/requirements.txt
REQUIRES:              SSH-доступ к VPS (103.88.243.151), права root
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Определить точный набор изменений для P1 и P2 => GOAL_CODE_PLAN
- GOAL Описать шаги реализации => GOAL_IMPLEMENTATION
- GOAL Определить acceptance criteria => GOAL_AC
**SECTION_USE_CASES:**
- USE_CASE Разработчик запускает make node-update после деплоя core → SCENARIO_NODE_UPDATE
- USE_CASE CI core-deploy вызывает make node-update → SCENARIO_CI
$END_DOCUMENT_PLAN

# DevPlan: Hotfix ошибок `make node-update` — P1 (pip3/pydantic) + P2 (verify-domains.sh)

## XML Knowledge Graph

```xml
<entities>
  <entity id="requirements.txt_py" TYPE="file" keywords="pip, pydantic, pyyaml, boto3, jinja2, cryptography, requirements" annotation="Platform-level runtime Python dependencies for VPS" CrossLinks="node-lifecycle.sh:install_python_deps_step, pyproject.toml:deps_source"/>

  <entity id="node_lifecycle_install_deps_FUNC" TYPE="step" keywords="bootstrap, init, pip, install, python, deps, idempotent" annotation="New step install_python_deps in --mode init pipeline: apt-get install python3-pip + pip3 install -r requirements.txt" CrossLinks="requirements.txt_py, state_machine.py:step_registry"/>

  <entity id="verify_domains_platform_url_FIX" TYPE="fix" keywords="verify, status-page, health, platform, subdomain, url" annotation="Change status-page /health URL from ${main_domain} to platform.${main_domain} in verify-domains.sh" CrossLinks="platform-vhost.conf:nginx_config"/>
</entities>
```

## Step-by-step Data Flow

### P1: pip3/pydantic fix

```
make node-update NODE=tronyx-vps
→ node-lifecycle.sh --mode init (bootstrap) или --mode update (update)
→ [NEW] step install_python_deps (index: между setup-node и docker-install)
  → apt-get update && apt-get install -y python3-pip python3-venv  (idempotent)
  → pip3 install --no-cache-dir -r /opt/platform/core/requirements.txt  (idempotent)
→ state_machine checkpoint сохраняется (content-hash, skip при неизменном requirements.txt)
→ последующие Python-скрипты (config_renderer.py, key_provisioner.py) находят pydantic
```

**Важно:**
- `apt-get install python3-pip` должен быть **идемпотентным** (проверять, установлен ли уже)
- `pip3 install -r requirements.txt` должен быть **идемпотентным** (проверять content-hash requirements.txt)
- Шаг добавляется **только в `--mode init`** (bootstrap новой ноды). Для `--mode update` на существующей ноде — pip3/pydantic устанавливаются **один раз** при первом прогоне после деплоя этого фикса, через ad-hoc вызов в `node-lifecycle.sh --mode update` перед step provision_llm_keys.

**Обходной путь для --mode update (существующая нода):**
- Не добавлять новый шаг в state machine для update (изменение индексов — рискованно)
- Вместо этого: в `node-lifecycle.sh --mode update`, перед вызовом `run_step provision_llm_keys`, выполнить идемпотентную установку pip3 + pydantic однократно (guard: проверить `pip3 --version`)
- Это гарантирует, что существующая нода получит исправление при первом же node-update после деплоя

### P2: verify-domains.sh fix

```
make node-update NODE=tronyx-vps (или make verify NODE=tronyx-vps)
→ verify-domains.sh
→ _verify_status_page()
  → [FIX] curl -u email:pass https://platform.${PLATFORM_DOMAIN}/health
  → status-page:8080 → _handle_health() → 200 PASS
→ [IMP:7] Status-page health check PASSED ✓
```

## Реализация

### T1: Создать `core/requirements.txt`

Файл: `core/requirements.txt` (новый)

```txt
# Platform runtime Python dependencies for VPS
# Source of truth: pyproject.toml [project] dependencies
# Must be kept in sync manually (or via generate-manifests in future)
pydantic>=2.0.0
pyyaml>=6.0
jinja2>=3.1.0
requests>=2.31.0
python-dotenv>=1.0.0
boto3>=1.28.0
cryptography>=41.0.0
```

### T2: Добавить idempotent установку pip3 + pydantic в bootstrap (--mode init)

Файл: `core/internal/bootstrap/node-lifecycle.sh`

**Вариант A (предпочтительный — для --mode update без изменения индексов):**

Добавить идемпотентную проверку/установку перед `provision_llm_keys` в секции `--mode update`:

```bash
# Идемпотентная установка Python-зависимостей (однократно при первом node-update после деплоя)
_ensure_python_deps() {
    # Guard: если pip3 уже установлен и pydantic доступен — пропускаем
    if command -v pip3 &>/dev/null && python3 -c "import pydantic" 2>/dev/null; then
        log_imp 8 "python-deps" "pip3 + pydantic already installed — skipping"
        return 0
    fi

    log_imp 9 "python-deps" "Installing pip3 + Python dependencies..."

    # Установка pip3 (только если отсутствует)
    if ! command -v pip3 &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq python3-pip python3-venv || {
            log_imp 8 "python-deps" "WARN: apt-get install python3-pip failed (no internet?); continuing without LLM key provisioning"
            return 0  # fail-soft: не блокируем node-update
        }
    fi

    # Установка зависимостей из requirements.txt
    if [[ -f "${PLATFORM_ROOT}/core/requirements.txt" ]]; then
        pip3 install --no-cache-dir -r "${PLATFORM_ROOT}/core/requirements.txt" || {
            log_imp 8 "python-deps" "WARN: pip install failed; continuing without LLM key provisioning"
            return 0  # fail-soft
        }
    fi

    log_imp 9 "python-deps" "Python dependencies installed successfully"
}
```

Вызов: в `--mode update` перед вызовом `provision_llm_keys`, и в `--mode init` между `setup-node` и `install-docker`.

### T3: Исправить verify-domains.sh — URL для status-page /health

Файл: `core/internal/verify/verify-domains.sh` (строки ~195-206)

**Было:**
```bash
log_imp 7 "status-page" "Checking status-page /health on https://${main_domain}/health"
...
curl -sS -o /dev/null -w '%{http_code}' \
    --max-time 30 \
    -u "${master_email}:${master_password}" \
    "https://${main_domain}/health" 2>/dev/null
```

**Стало:**
```bash
local status_page_url="https://platform.${main_domain}/health"
log_imp 7 "status-page" "Checking status-page /health on ${status_page_url}"
...
curl -sS -o /dev/null -w '%{http_code}' \
    --max-time 30 \
    -u "${master_email}:${master_password}" \
    "${status_page_url}" 2>/dev/null
```

⚠️ TRAP[BUG] · 2026-07-24 · P2 · verify-domains.sh проверяла status-page по неправильному URL
· Symptom: curl https://tronyx.ru/health → nginx overlay proxied to tronyx-site project → 500
· Root: status-page живёт на platform.tronyx.ru (platform-vhost.conf), не на apex tronyx.ru
· Fix: изменить URL на platform.${PLATFORM_DOMAIN}/health

## Acceptance Criteria

| ID | Критерий | Как проверить |
|----|----------|---------------|
| AC1 | pip3 установлен на VPS после первого node-update с фиксом | `ssh VPS "pip3 --version"` — показывает версию |
| AC2 | pydantic доступен на VPS | `ssh VPS "python3 -c 'import pydantic; print(pydantic.__version__)'"` — успех |
| AC3 | `make node-update NODE=tronyx-vps` — step provision_llm_keys не падает | Лог: `[IMP:9][subprocess][render_litellm_config] Command succeeded (exit=0)` |
| AC4 | `make node-update NODE=tronyx-vps` — verify-domains.sh PASS | Лог: `[IMP:7][verify][status-page] Status-page health check PASSED` |
| AC5 | Повторный node-update (после фикса) — идемпотентен | Лог: `[IMP:8][python-deps] pip3 + pydantic already installed — skipping` |
| AC6 | bootstrap-node (новая нода) получает pip3 + pydantic | Лог при --mode init: `[IMP:9][python-deps] Python dependencies installed successfully` |
| AC7 | Отсутствие интернета на VPS не блокирует node-update | `apt-get install python3-pip` падает → `[IMP:8] WARN ... continuing without LLM key provisioning` → node-update продолжается |

## File Manifest

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 1 | `core/requirements.txt` | **Создать** | Runtime Python-зависимости для VPS |
| 2 | `core/internal/bootstrap/node-lifecycle.sh` | **Изменить** | Добавить `_ensure_python_deps()` и вызовы в --mode init и --mode update |
| 3 | `core/internal/verify/verify-domains.sh` | **Изменить** | Исправить URL status-page health check на platform.${PLATFORM_DOMAIN}/health |

$END_DEVPLAN
