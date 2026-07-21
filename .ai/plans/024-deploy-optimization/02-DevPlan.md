# 024-DevPlan: Deploy performance optimization — 5 волн, 14 оптимизаций

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Сократить время полного деплой-цикла с ~2 часов до ~15-20 минут через 5 волн оптимизаций: архитектурный долг (S1+S2+S10), SSL-кэширование на S3, project scaffold через converge, predeploy gate extension, hermes-agent L2 fallback, и 7 микрооптимизаций (S3-S9). Всего 14 атомарных изменений.
DESCRIPTION:           DevPlan расширяет Brief 024 результатами superposition-аудита кодовой базы (10 дополнительных оптимизаций S1-S10). Волна 0 устраняет 3 архитектурных долга, обнаруженных при аудите: тройной вызов provisioner, двойной полный запуск deploy-modules.sh, и дублирующее чтение module.yaml. Волны 1-4 — из Brief. Волна 5 — батчинг и дедупликация (S3-S9).
RATIONALE:             Аудит 1250+1327+1123+513+993+210+108+255+278+663 LOC выявил: (a) provisioner вызывается 3× за update-цикл, (b) deploy-modules.sh запускается дважды с полным повторением дорогих операций (docker_login, ghcr_login, ensure_context_repo, parse node.yaml), (c) 26 python3-спавнов для detect_install_type/get_module_severity, (d) последовательные healthcheck'и — bottleneck на 60-90s. Все оптимизации сохраняют архитектурные инварианты (Makefile-фасад, push-based core, idempotent bootstrap, no git for core).
ACCEPTANCE_CRITERIA:   1. `make gate MODE=fast` — зелёный до и после каждой волны.  2. `make bootstrap-node NODE=tronyx-vps` на голом сервере: SSL из S3 (≤10s), /opt/projects/<name>/ созданы (через converge), /etc/hosts без хардкодов.  3. `make deploy PROJECT=<name>`: CI зелёный с первой попытки.  4. hermes-agent: pull-or-build без дрейфа.  5. Полный цикл (bootstrap + deploy 2 проектов) ≤ 20 мин.  6. Все 14 оптимизаций имеют соответствующие тесты в test_predeploy_gate.py или test_deploy_modules.py.
IMPLEMENTS:            Brief 024 (01-Brief.md), superposition S1-S10 (02-DevPlan.md), инварианты 1 (Makefile), 3 (org=context), 6 (bootstrap idempotent), 9 (test server recreatable).
IMPACTS:               core/internal/bootstrap/node-lifecycle.sh (step_6b → converge R3, skip-provision флаг), core/internal/bootstrap/deploy-modules.sh (--skip-provision, --deploy-both, батчинг, parallel healthcheck, pull-or-build hermes), core/internal/bootstrap/issue-cert.sh (S3 save/restore), core/internal/bootstrap/_topo_sort.py (enriched output), core/internal/bootstrap/converge.sh (R3 — без изменений, вызывается раньше), core/lib/yaml_read.sh (domain config helper), core/modules/backup-cron/scripts/upload.py (shared S3 config refactor), tests/test_predeploy_gate.py (4 новых теста + тесты S1-S10), .github/workflows/deploy-project.yml (validate step + rsync consolidation), .github/workflows/core-deploy.yml (manifest-based rsync).
REQUIRES:              Ветка от origin/main, `make gate MODE=fast` зелёный, working tree чистый. S3 credentials (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET) в secrets tronyx-vps.enc.yaml. Python3+boto3 доступны на VPS (уже есть через backup-cron). Docker daemon на VPS.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Wave 0: устранить 3 архитектурных долга (S1, S2, S10) до начала основных волн => GOAL_W0
- GOAL Wave 1: SSL certificate caching on S3 — save после issue, restore перед issue => GOAL_W1
- GOAL Wave 2: project scaffold через converge — /opt/projects/<name>/ создаются при bootstrap => GOAL_W2
- GOAL Wave 3: predeploy gate extension — 4 теста для проектных compose файлов => GOAL_W3
- GOAL Wave 4: hermes-agent L2 pre-built с fallback — pull-or-build => GOAL_W4
- GOAL Wave 5: микрооптимизации S3-S9 — батчинг, дедупликация, parallel healthcheck => GOAL_W5
**SECTION_USE_CASES:**
- USE_CASE bootstrap bare VPS => UC_BOOTSTRAP
- USE_CASE deploy project first time => UC_FIRST_DEPLOY
- USE_CASE deploy project update => UC_UPDATE
- USE_CASE gate pre-push validation => UC_GATE
$END_DOCUMENT_PLAN
```

---

## 1. Волна 0 (P0): Архитектурный долг — S1, S2, S10

### Текущее состояние

```
node-lifecycle.sh --mode update
├── step 2: provision-environment.sh --scope networks --scope volumes    ← вызов #1
├── step 3: ssl-provision (issue-cert.sh)
├── step 4: deploy-modules.sh ──────────────────────────────────────────
│   ├── _validate_secret_charsets
│   ├── provisioner --scope networks                                      ← вызов #2
│   ├── provisioner --scope volumes                                       ← вызов #3
│   ├── ensure_spool_dirs
│   ├── docker_login + ghcr_login
│   ├── ensure_context_repo (git pull)
│   ├── parse_modules_from_node_yaml (python3)
│   ├── for each module:
│   │   ├── detect_install_type() → python3 -c "import yaml..."           ← 13× spawn
│   │   └── _get_module_severity() → python3 -c "import yaml..."          ← 13× spawn
│   ├── _topo_sort.py → читает все module.yaml
│   └── deploy_docker_group → deploy_docker_module × N
│       └── run_healthcheck (последовательный, 4 retries × 3s)
└── step 5: deploy-modules.sh --system ─────────────────────────────────
    ├── _validate_secret_charsets                                         ← повтор
    ├── provisioner × 2                                                    ← повтор (вызовы #4, #5!)
    ├── ensure_spool_dirs                                                  ← повтор
    ├── docker_login + ghcr_login                                         ← повтор
    ├── ensure_context_repo                                                ← повтор
    ├── parse_modules_from_node_yaml                                      ← повтор
    └── detect_install_type × N                                           ← повтор
```

**Проблемы:**
1. `provision-environment.sh` вызывается **5 раз** за один update-цикл (1× из step_2, 2× из deploy-modules.sh шаг 4, 2× из deploy-modules.sh шаг 5).
2. `deploy-modules.sh` запускается дважды с полным повторением дорогих setup-операций.
3. `_topo_sort.py` читает все `module.yaml` для построения DAG, а затем главный цикл снова читает их через `detect_install_type()` и `_get_module_severity()`.

### S1: `--skip-provision` флаг для deploy-modules.sh

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:**
1. Добавить парсинг флага `--skip-provision` в `main()`.
2. При наличии флага — пропустить вызов `provision-environment.sh` (строки 1048-1063).
3. В `node-lifecycle.sh` update-режиме: `update_step_4_deploy_docker()` и `update_step_5_deploy_system()` передают `--skip-provision`.

```bash
# deploy-modules.sh main() — новый парсинг
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-provision) SKIP_PROVISION=true; shift ;;
        --modules) ... ;;
        *) break ;;
    esac
done

# ... позже
if [[ "${SKIP_PROVISION:-false}" != "true" ]]; then
    # существующий блок provisioner
fi
```

```bash
# node-lifecycle.sh update_step_4_deploy_docker()
bash "${CORE_DIR}/internal/bootstrap/deploy-modules.sh" --skip-provision 2>&1
```

### S2: Единый вызов deploy-modules.sh с `--deploy-both`

**Файлы:** `core/internal/bootstrap/deploy-modules.sh`, `core/internal/bootstrap/node-lifecycle.sh`

**Изменение:**
1. В `node-lifecycle.sh` update-режиме: объединить step 4 и step 5 в один шаг:
   ```bash
   # Было: два вызова
   # update_step_4_deploy_docker → deploy-modules.sh
   # update_step_5_deploy_system → deploy-modules.sh --system

   # Стало: один вызов с объединённым флагом
   update_step_4_deploy_modules() {
       step_start "deploy-modules" "Deploying all modules (docker + system)"
       bash "${CORE_DIR}/internal/bootstrap/deploy-modules.sh" --skip-provision 2>&1
   }
   ```
2. `deploy-modules.sh` и так обрабатывает оба типа (system + docker) в одном проходе при отсутствии `--system` флага. Убрать разделение на step_4/step_5 — оба типа деплоятся в одном вызове.
3. Удалить `update_step_5_deploy_system()`.
4. Обновить checkpoint-хэш: один `deploy-modules` вместо `deploy-docker` + `deploy-system`.
5. Обновить `--dry-run` вывод.

**Эффект:** Экономия второго полного прохода `main()`: `_validate_secret_charsets`, `provisioner`, `ensure_spool_dirs`, `docker_login`, `ghcr_login`, `ensure_context_repo`, `parse_modules`.

### S10: _topo_sort.py enriched output — убрать повторные чтения module.yaml

**Файлы:** `core/internal/bootstrap/_topo_sort.py`, `core/internal/bootstrap/deploy-modules.sh`

**Текущий вывод _topo_sort.py:**
```json
{"groups": [["postgres", "redis"], ["langfuse", "minio"], ...]}
```

**Новый вывод:**
```json
{
  "groups": [["postgres", "redis"], ["langfuse", "minio"], ...],
  "modules": {
    "postgres":    {"install_type": "docker",  "severity": "critical"},
    "redis":       {"install_type": "docker",  "severity": "critical"},
    "nginx":       {"install_type": "system",  "severity": "critical"},
    "hermes-agent":{"install_type": "docker",  "severity": "warn"},
    ...
  }
}
```

**Изменение в deploy-modules.sh:**
1. Вместо цикла с `detect_install_type()` + `_get_module_severity()` для каждого модуля — читаем `_topo_result.modules`.
2. `detect_install_type()` остаётся для обратной совместимости (прямой вызов deploy-modules.sh без _topo_sort), но не вызывается из топо-сортированного пути.

### S1+S2+S10: совокупный эффект

| Метрика | До | После |
|---------|:--:|:-----:|
| Вызовов provision-environment.sh | 5 | 1 |
| Вызовов deploy-modules.sh main() | 2 | 1 |
| Python3 спавнов для module.yaml | 26 | 0 |
| docker_login вызовов | 2 | 1 |
| ghcr_login вызовов | 2 | 1 |
| ensure_context_repo вызовов | 2 | 1 |

---

## 2. Волна 1 (P0): SSL certificate caching on S3

### Design

```
┌─ Bootstrap ──────────────────────────────────────────────────────┐
│                                                                   │
│  node-lifecycle.sh --mode update                                  │
│  └── update_step_3_ssl_provision()                               │
│      └── s3_restore_cert "$PLATFORM_DOMAIN"                      │
│          ├── download fullchain.pem from S3                       │
│          ├── openssl x509 -checkend 2592000 -noout                │
│          ├── openssl x509 -subject | grep "CN = $domain"          │
│          ├── if valid → restore to /etc/letsencrypt/live/         │
│          │            → restore acme.sh account data              │
│          │            → SKIP acme.sh issue, go to cron + verify   │
│          └── if invalid/404 → fallback to full acme.sh issue      │
│                                                                   │
│  issue-cert.sh (after successful issue)                           │
│  └── s3_save_cert "$PLATFORM_DOMAIN"                              │
│      ├── upload fullchain.pem → s3://bucket/ssl-certs/domain/    │
│      ├── upload privkey.pem                                       │
│      ├── upload chain.pem                                         │
│      └── upload acme.sh account/ dir (tar czf | upload)           │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

### Стратегия переиспользования upload.py

`upload.py` зависит от `backup_config.py`:

```python
# backup_config.py — жёсткая привязка к backup-контейнеру
class BackupConfig(TypedDict):
    endpoint_url: str
    aws_access_key_id: str
    aws_secret_access_key: str
    bucket: str
    region: str
    prefix: str          # backup-specific префикс
```

**Решение (оператор выбрал «переиспользовать upload.py»):**
1. Рефакторить `backup_config.py` → добавить `S3Config` (базовый класс/функцию) для общих S3-операций.
2. `BackupConfig` остаётся специализацией `S3Config` (добавляет `prefix`).
3. `upload.py` принимает опциональный `--config-source` (default: `backup` для обратной совместимости, `ssl-cache` для issue-cert.sh).
4. Новый скрипт `core/internal/bootstrap/s3-ssl-cache.sh` — тонкая bash-обёртка:
   ```bash
   # s3-ssl-cache.sh upload <domain>
   # s3-ssl-cache.sh download <domain>
   # s3-ssl-cache.sh check <domain>  # returns 0 if valid cache exists
   ```
   Вызывает `upload.py` с `--config-source ssl-cache`.

**S3-ключи:**
```
s3://<S3_BUCKET>/platform/ssl-certs/<domain>/
├── fullchain.pem
├── privkey.pem
├── chain.pem
└── account.tar.gz          # acme.sh account data
```

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `core/modules/backup-cron/scripts/backup_config.py` | MODIFY | Рефакторинг: извлечь `S3Config`, `BackupConfig` = специализация |
| `core/modules/backup-cron/scripts/upload.py` | MODIFY | Добавить `--config-source ssl-cache` |
| `core/internal/bootstrap/s3-ssl-cache.sh` | CREATE | Bash-обёртка: upload/download/check для SSL-сертификатов |
| `core/internal/bootstrap/issue-cert.sh` | MODIFY | После успешного issue → вызов `s3-ssl-cache.sh upload` |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | `update_step_3_ssl_provision()`: перед issue → `s3-ssl-cache.sh check` → restore или fallback |
| `tests/test_ssl_s3_cache.py` | CREATE | Тесты: save/restore/check/invalid-cert/404-fallback |

### Graceful degradation

- S3 недоступен → WARN, fallback к полному acme.sh issue
- Сертификат в S3 есть, но невалиден (expiry < 30d, domain mismatch) → WARN, fallback к issue
- После успешного restore → `_acme_install_cron` + `_acme_verify_cert` (как при обычном issue)

---

## 3. Волна 2 (P0): Project scaffold через converge

### Design

**Решение (оператор выбрал «оставить в converge.sh»):**

```
node-lifecycle.sh --mode init
├── ...
├── step 6: user-ci-deploy
├── step 6b: projects-base        ← РАСШИРЕН
│   ├── mkdir -p /opt/projects + chown ci-deploy  (существующее)
│   └── converge.sh --units R3 --node "$NODE_NAME" (НОВОЕ)
│       └── Для каждого проекта из node.yaml#projects:
│           ├── mkdir -p /opt/projects/<name>/
│           ├── chown ci-deploy:ci-deploy
│           ├── stub ai-platform.yaml (если отсутствует)
│           └── .env.platform через gen-env-platform.sh
├── ...
└── step 15: converge              ← полный converge (все R-units, включая R3 повторно — идемпотентно)
```

**Ключевое изменение в node-lifecycle.sh step_6b:**
```bash
step_6b_create_projects_base() {
    step_start "projects-base" "Ensuring /opt/projects base + project stubs"
    # ... существующий mkdir + chown ...

    # Вызвать converge R3 для создания проектных директорий до первого деплоя
    local converge_script="${CORE_DIR}/internal/bootstrap/converge.sh"
    if [[ -f "$converge_script" ]]; then
        log_step "projects-base" "INFO" "Calling converge R3 for project scaffold"
        bash "$converge_script" --node "${NODE_NAME}" --units R3 2>&1 || \
            log_step "projects-base" "WARN" "Converge R3 had issues — projects may need manual creation"
    fi
    step_done "projects-base" "Project directories ensured via converge R3"
}
```

**Converge.sh R3 — улучшения для генерации .env.platform:**
- Текущий R3 создаёт пустой `.env.platform` (touch).
- Новый R3 вызывает `gen-env-platform.sh` для генерации реального `.env.platform` из `platform-env.yaml`.

### Связанные фиксы (не в этом брифе, но блокируют деплой)

Эти фиксы необходимы для успешного первого деплоя проектов:

1. **Nginx Docker DNS resolver**: vhost-конфиги уже используют `resolver 127.0.0.11 valid=30s` + variable-based `proxy_pass` (проверено в node-configs/tronyx-vps/overlays/nginx/*.conf). `/etc/hosts` хардкоды не требуются.
2. **Убрать мёртвый `conf.d/tronyx.ru.conf`**: deprecated listen-директивы.
3. **Обновить TRAP[DECISION] в nginx overlay**: старый TRAP описывает system nginx, а nginx давно в Docker.

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | `step_6b_create_projects_base`: добавить вызов converge --units R3 |
| `core/internal/bootstrap/converge.sh` | MODIFY | R3: заменить `touch .env.platform` на вызов `gen-env-platform.sh` |
| `core/internal/scaffold/gen-env-platform.sh` | MODIFY | Поддержка вызова из converge (принимать `--project-name`) |
| `node-configs/tronyx-vps/overlays/nginx/` | MODIFY | Почистить мёртвый conf.d/tronyx.ru.conf, обновить TRAP |
| `tests/test_project_scaffold.py` | CREATE | Тест: scaffold через converge R3, идемпотентность |

---

## 4. Волна 3 (P1): Predeploy gate extension

### Новые тесты

| # | Тест | Что проверяет | Требует Docker? |
|---|------|---------------|:---:|
| T1 | `test_project_compose_configs_valid` | `docker compose -f <dir>/docker-compose.yml config --dry-run` для проектов из node.yaml | Да (skip если нет) |
| T2 | `test_project_ports_no_conflict` | Порты проекта не конфликтуют с платформенными (из platform-env.yaml profiles) | Нет |
| T3 | `test_project_external_networks_exist` | Все `networks: <name> (external: true)` задекларированы в platform-env.yaml | Нет |
| T4 | `test_project_requires_proxy_net` | Каждый проектный compose подключается к `proxy-net (external: true)` | Нет |
| T5 | `test_ai_platform_yaml_schema` | YAML schema валидация `ai-platform.yaml` (name, domain, target_node — required) | Нет |

### Интеграция с CI

`deploy-project.yml`:
```yaml
- name: Validate project payload
  run: |
    make gate MODE=fast PROJECT=${{ inputs.project_name }}
```

Требует: `make gate MODE=fast` поддерживает `PROJECT=<name>` для фильтрации project-specific тестов.

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `tests/test_predeploy_gate.py` | MODIFY | Добавить T1-T5 |
| `tests/conftest.py` | MODIFY | Новые fixtures: `project_compose_files`, `node_yaml_projects` |
| `.github/workflows/deploy-project.yml` | MODIFY | Добавить validate step перед deliver |
| `Makefile` | MODIFY | `make gate MODE=fast PROJECT=<name>` — фильтрация project тестов |

---

## 5. Волна 4 (P2): Hermes-agent L2 pre-built с fallback

### Design

```
deploy_docker_module("hermes-agent")
├── resolve images from docker compose config --images
├── for each resolved hermes image:
│   ├── docker manifest inspect <image>    ← проверка в registry
│   ├── if found → docker pull <image>
│   └── if NOT found (404/manifest unknown):
│       └── docker compose build <service>   ← локальная сборка L1→L2
└── docker compose up -d
```

**Текущее поведение:** `_check_image_exists()` (строка 372) проверяет `docker manifest inspect`. Если не найдено — FAIL (строка 451-457). Нужно заменить FAIL на fallback-сборку.

**Изменение в deploy_docker_module():**
```bash
if [[ "$module_name" == "hermes-agent" ]]; then
    local -a hermes_images=()
    mapfile -t hermes_images < <(docker compose "${compose_args[@]}" --profile "$module_name" config --images 2>/dev/null || true)
    if [[ ${#hermes_images[@]} -eq 0 ]]; then
        log_step "docker:${module_name}" "FAIL" "No images resolved from compose config"
        return 1
    fi
    local _all_found=true
    for _img in "${hermes_images[@]}"; do
        if ! _check_image_exists "$_img"; then
            _all_found=false
            log_step "docker:${module_name}" "WARN" "Pre-built image not found: ${_img} — will build locally"
        fi
    done
    if ! $_all_found; then
        log_step "docker:${module_name}" "BUILD" "Building hermes-agent L1→L2 locally (fallback)"
        docker compose "${compose_args[@]}" --profile "$module_name" build 2>&1 || {
            log_step "docker:${module_name}" "FAIL" "Local build failed"
            return 1
        }
    fi
fi
```

### Файлы

| Файл | Действие | Описание |
|------|----------|----------|
| `core/internal/bootstrap/deploy-modules.sh` | MODIFY | `deploy_docker_module`: заменить FAIL на fallback build для hermes-agent |
| `tests/test_hermes_l2_fallback.py` | CREATE | Тест: pull success, pull 404→build, build fallback |

---

## 6. Волна 5 (P1): Микрооптимизации S3-S9

### S3: Python3 батчинг в deploy-modules.sh

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:** Заменить вызовы `detect_install_type()` (строка 1122) и `_get_module_severity()` (строка 1305) на один вызов python3, возвращающий `name:type:severity` для всех модулей.

```bash
# Новый хелпер — один вызов python3
_batch_module_metadata() {
    python3 -c "
import yaml
from pathlib import Path
modules_dir = Path('${PATHS_MODULES_DIR}')
for yf in sorted(modules_dir.glob('*/module.yaml')):
    with open(yf) as f:
        d = yaml.safe_load(f)
    name = d.get('name', yf.parent.name)
    itype = d.get('install_type', 'unknown')
    sev = d.get('severity', 'warn')
    print(f'{name}:{itype}:{sev}')
"
}
```

В главном цикле:
```bash
# Вместо:
# install_type="$(detect_install_type "$mod_name")"

# Стало — читаем из предзагруженного ассоциативного массива:
declare -A _MODULE_TYPES _MODULE_SEVERITIES
while IFS=: read -r _mname _mtype _msev; do
    _MODULE_TYPES["$_mname"]="$_mtype"
    _MODULE_SEVERITIES["$_mname"]="$_msev"
done < <(_batch_module_metadata)

# ... в цикле:
install_type="${_MODULE_TYPES[$mod_name]:-unknown}"
```

### S4: Параллельные healthcheck'и

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:** `deploy_docker_group()` — после деплоя всех модулей в группе, запустить healthcheck'и параллельно (паттерн из `_pre_pull_images`).

```bash
# После цикла ожидания всех PID'ов группы:
_pids=() _hc_names=()
for name in "${group_names[@]}"; do
    ( run_healthcheck "$name" "docker" && exit 0 || exit 1 ) &
    _pids+=($!)
    _hc_names+=("$name")
done
# Дождаться всех
for i in "${!_pids[@]}"; do
    wait "${_pids[$i]}" || log_step "health:${_hc_names[$i]}" "WARN" "Healthcheck failed"
done
```

### S5: CI rsync консолидация

**Файл:** `.github/workflows/core-deploy.yml`

**Изменение:** Объединить 3 rsync-шага в один:

```yaml
- name: Rsync core + config to VPS
  run: |
    rsync -avz --delete \
      --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
      ./core/ ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }}:/opt/platform/core/
    rsync -avz \
      ./platform-env.yaml ./Makefile \
      ${{ env.VPS_USER }}@${{ secrets.VPS_HOST }}:/opt/platform/
```

Note: сохраняем отдельные rsync для core/ (с `--delete`) и для одиночных файлов (без `--delete`).

### S6: sudoers батчинг

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:** `generate_module_sudoers()` вызывается не по одному модулю, а собирает правила для всех модулей в группе и рендерит один файл `/etc/sudoers.d/platform-modules`.

```bash
_batch_generate_sudoers() {
    local -a module_names=("$@")
    local tmp_sudoers="$(mktemp /tmp/platform-sudoers-all-XXXXXX)"
    # header
    cat > "$tmp_sudoers" <<'EOF'
# platform modules sudoers — ALL modules
# Generated by deploy-modules.sh
# DO NOT edit manually
EOF
    for mod_name in "${module_names[@]}"; do
        _render_sudoers_rules "$mod_name" >> "$tmp_sudoers"
    done
    visudo -c -f "$tmp_sudoers" && mv "$tmp_sudoers" /etc/sudoers.d/platform-modules
}
```

### S7: YAML domain extraction dedup

**Файлы:** `core/lib/yaml_read.sh`, `core/internal/bootstrap/node-lifecycle.sh`, `core/internal/bootstrap/issue-cert.sh`

**Изменение:** Извлечь общий python3-блок в функцию `yaml_read_domain_config()`:

```bash
# core/lib/yaml_read.sh
yaml_read_domain_config() {
    local node_yaml="$1"
    python3 - "$node_yaml" <<'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get('domain', '')
email = data.get('email', '')
acme_dns_plugin = data.get('acme_dns_plugin', '')
projects = data.get('projects', [])
project_domains = [p.get('domain', '') for p in projects if isinstance(p, dict) and p.get('domain')]
print(f"platform_domain:{domain}")
print(f"email:{email}")
print(f"acme_dns_plugin:{acme_dns_plugin}")
print(f"project_domains:{' '.join(project_domains)}")
PYEOF
}
```

Три места вызова заменяются на `source core/lib/yaml_read.sh && eval "$(yaml_read_domain_config "$NODE_YAML" | ...)"`.

### S8: Orphan reconciliation батчинг

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:** Вместо inline Python на каждый модуль — один вызов, который:
1. Собирает `docker compose config` для ВСЕХ docker-модулей (единый compose с includes).
2. Делает один `docker ps -a --format`.
3. Выдаёт список orphan-контейнеров для всех модулей сразу.
4. Stop + rm всех orphan одной пачкой.

### S9: Git pull кэширование

**Файл:** `core/internal/bootstrap/deploy-modules.sh`

**Изменение:** `ensure_context_repo()` — добавить проверку времени последнего pull:

```bash
ensure_context_repo() {
    # ... существующий код ...
    if [[ -d "$context_path" ]]; then
        local last_pull_file="/var/lib/platform/.context-pull-ts"
        local now=$(date +%s)
        local last_pull=0
        [[ -f "$last_pull_file" ]] && last_pull=$(cat "$last_pull_file")
        # Skip if pulled within last 5 minutes
        if [[ $((now - last_pull)) -lt 300 ]]; then
            log_step "context-repo" "SKIP" "Pulled recently (${last_pull}) — skipping"
            return 0
        fi
        git -C "$context_path" pull --ff-only 2>/dev/null || ...
        echo "$now" > "$last_pull_file"
        return 0
    fi
    # ...
}
```

---

## 7. File Manifest — ALL changes

| # | Файл | Волна | Действие |
|---|------|:-----:|----------|
| 1 | `core/internal/bootstrap/node-lifecycle.sh` | W0,W1,W2,W7 | MODIFY: --skip-provision, merge step 4+5, step_6b converge R3, s3-ssl-cache check, yaml_read_domain_config |
| 2 | `core/internal/bootstrap/deploy-modules.sh` | W0,W3,W4,W5,W6,W8,W9 | MODIFY: --skip-provision, pull-or-build, батчинг metadata, parallel healthcheck, sudoers batch, orphan batch, git cache |
| 3 | `core/internal/bootstrap/_topo_sort.py` | W0 | MODIFY: enriched output (install_type, severity per module) |
| 4 | `core/internal/bootstrap/converge.sh` | W2 | MODIFY: R3 — gen-env-platform.sh вместо touch |
| 5 | `core/internal/bootstrap/issue-cert.sh` | W1 | MODIFY: вызов s3-ssl-cache.sh upload после успешного issue |
| 6 | `core/internal/bootstrap/s3-ssl-cache.sh` | W1 | CREATE: Bash-обёртка upload/download/check |
| 7 | `core/modules/backup-cron/scripts/backup_config.py` | W1 | MODIFY: рефакторинг S3Config + BackupConfig |
| 8 | `core/modules/backup-cron/scripts/upload.py` | W1 | MODIFY: --config-source ssl-cache |
| 9 | `core/lib/yaml_read.sh` | W7 | MODIFY: yaml_read_domain_config() |
| 10 | `core/internal/scaffold/gen-env-platform.sh` | W2 | MODIFY: --project-name для вызова из converge |
| 11 | `.github/workflows/deploy-project.yml` | W3 | MODIFY: validate step (make gate MODE=fast PROJECT=) |
| 12 | `.github/workflows/core-deploy.yml` | W5 | MODIFY: rsync consolidation (3→1 шаг) |
| 13 | `Makefile` | W3 | MODIFY: gate MODE=fast PROJECT=<name> |
| 14 | `tests/test_predeploy_gate.py` | W3 | MODIFY: T1-T5 новые тесты |
| 15 | `tests/test_deploy_modules.py` | W0,W4,W5,W6,W8,W9 | CREATE/MODIFY: тесты S1-S10 |
| 16 | `tests/test_ssl_s3_cache.py` | W1 | CREATE: save/restore/check/invalid/404 |
| 17 | `tests/test_project_scaffold.py` | W2 | CREATE: converge R3 scaffold |
| 18 | `tests/test_hermes_l2_fallback.py` | W4 | CREATE: pull-or-build |
| 19 | `tests/conftest.py` | W3 | MODIFY: fixtures project_compose_files, node_yaml_projects |
| 20 | `node-configs/tronyx-vps/overlays/nginx/` | W2 | MODIFY: чистка мёртвых конфигов, обновление TRAP |

---

## 8. Порядок выполнения

```
Волна 0 (S1+S2+S10) → gate green
  └── Волна 2 (project scaffold) → gate green  (зависит от W0 — converge вызывается из step_6b)
      └── Волна 1 (SSL cache) → gate green     (независима от W2, но deploy-cycle тестируется после W2)
          └── Волна 3 (predeploy gate) → gate green
              └── Волна 4 (hermes L2) → gate green
                  └── Волна 5 (S3-S9) → gate green
```

**Обоснование порядка:** Волна 0 первой — устраняет архитектурный долг, на котором базируются остальные изменения. Волна 2 перед Волной 1 — потому что scaffold нужен для тестирования полного деплой-цикла. Волна 5 последней — микрооптимизации с минимальным риском регрессии.

---

## 9. Acceptance Criteria (на все волны)

- [ ] `make gate MODE=fast` — зелёный до и после каждой волны
- [ ] `make bootstrap-node NODE=tronyx-vps --dry-run` — все шаги объявлены, порядок корректен
- [ ] Bootstrap: SSL сертификаты восстановлены из S3 за ≤10s (или выпущены и сохранены)
- [ ] Bootstrap: `/opt/projects/<name>/` созданы для всех проектов из node.yaml#projects
- [ ] Bootstrap: provisioner вызван ровно 1 раз (не 5)
- [ ] Bootstrap: deploy-modules.sh вызван ровно 1 раз в update-режиме
- [ ] `make deploy PROJECT=tronyx-site`: CI зелёный с первой попытки
- [ ] Predeploy gate: T1-T5 падают ДО отправки на VPS при невалидном compose/yaml
- [ ] Hermes-agent: pull pre-built при наличии, build локально при 404 — без дрейфа
- [ ] Healthcheck'и: выполняются параллельно в пределах группы (логи показывают overlapping времена)
- [ ] S3-S9: соответствующие тесты подтверждают оптимизации (python3 вызовов ≤ 3, healthcheck parallel и т.д.)
- [ ] Полный цикл (bootstrap + deploy 2 проектов) ≤ 20 мин

$END_DEVPLAN
