$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Системный аудит дрейфа ai-platform — обнаружение всех параллельных реализаций одной бизнес-логики, дублирующегося кода, конфликтующих default-значений и архитектурных противоречий. БРИФ описывает проблемы, а не решения.
DESCRIPTION:           Полный скан 287 коммитов за 15 дней (2026-07-10 – 2026-07-25) + файловый аудит 6 доменов: секреты/токены, bootstrap pipeline, сертификаты/SSL, deploy pipeline, конфигурация/env, healthcheck. Выявлено 40+ точек дрейфа, сгруппированных в 5 корневых архитектурных проблем.
RATIONALE:             Пользователь зафиксировал повторный круг проблем (секреты/бутстрап/токены-доступа) после предыдущих исправлений. Симптом указывает на множественные параллельные ветки бизнес-логики, которые дрейфуют независимо. Без системного аудита исправления будут точечными и временными — дрейф продолжится.
ACCEPTANCE_CRITERIA:   1. Каждая точка дрейфа имеет конкретный файл/строку/дифф. 2. Определены корневые причины (не следствия). 3. Проблемы сгруппированы по доменам с указанием severity. 4. БРИФ пригоден для создания независимых DevPlan'ов.
IMPLEMENTS:            Диагностический мета-БРИФ → серия DevPlan'ов (по одному на корневую причину)
IMPACTS:               70+ файлов (полный список в секции File Inventory)
REQUIRES:              Доступ к репозиторию, git history с 2026-07-10
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- INVENTORY: Полный каталог конфликтующих файлов по доменам → INVENTORY_ID
- ROOT_CAUSES: 5 корневых архитектурных проблем → ROOT_CAUSES_ID
- SECRETS: Дрейф в управлении секретами и токенами → SECRETS_ID
- BOOTSTRAP: Дрейф в bootstrap pipeline → BOOTSTRAP_ID
- CERTIFICATES: Дрейф в управлении SSL-сертификатами → CERTS_ID
- DEPLOY: Дрейф в deploy pipeline → DEPLOY_ID
- CONFIG: Дрейф в конфигурации и env-переменных → CONFIG_ID
- HEALTHCHECK: Дрейф в healthcheck-системе → HEALTHCHECK_ID
- WAVE_MAP: Маппинг проблем на будущие DevPlan-волны → WAVE_MAP_ID
**SECTION_USE_CASES:**
- USE_CASE Разработчик фиксит баг → натыкается на дублирующую логику, не знает какую править
- USE_CASE CI проходит, но production падает → разные default-значения между окружениями
- USE_CASE После bootstrap секреты не прокидываются → двойная бухгалтерия shell/Python
- USE_CASE Сертификат выпущен, но не попал в S3 → параллельные cert-пути с разной логикой
$END_DOCUMENT_PLAN

---

# 01-Brief: Системный аудит дрейфа ai-platform

**Severity:** CRITICAL (системный дрейф затрагивает все домены платформы)
**Created:** 2026-07-25
**Author:** Kilo (architect agent)
**Source:** Анализ 287 коммитов за 15 дней + файловый аудит 6 доменов
**Status:** DRAFT — БРИФ проблем (не решений). DevPlan'ы будут созданы отдельно.

---

## Резюме: что произошло

Платформа достигла состояния, когда **одна и та же бизнес-логика реализована в 2-5 разных местах**:

- **AGE key detection** — 5 копий одной функции в разных файлах
- **SSL provisioning** — 3 параллельных пути выдачи сертификатов (shell, Python-шаги, оркестратор)
- **Content hash** — 3 реализации с разными алгоритмами и source-файлами
- **Healthcheck** — 9 механизмов проверки здоровья, 8 паттернов port-check
- **Docker compose ops** — 2 независимых реализации pull/build/up с разными retry/rollback
- **POSTGRES_PASSWORD** — 6 разных default-значений в разных файлах

Причина: **Strangler-Fig на середине пути**. Python-модули реализуют каноническую логику, но shell-обёртки продолжают дублировать бизнес-логику вместо чистого делегирования. Мёртвый код не удаляется. Default-значения не синхронизированы между 4 слоями (secret-definitions, .env.example, compose, shell).

**Результат:** точечный фикс в одном месте не решает проблему — другие копии логики продолжают работать по-старому, создавая новый круг багов через 2-3 дня.

---

## Глава 1: 5 корневых архитектурных проблем

Эти 5 проблем — **причины**, а не следствия. Каждая из них порождает множественные точки дрейфа в разных доменах.

### RC-1: Двойная бухгалтерия shell/Python (Strangler-Fig на середине)

**Суть:** Python-модули (state_machine.py, cert_orchestrator.py, docker_orchestrator.py) реализуют каноническую бизнес-логику. Но shell-скрипты (node-lifecycle.sh, steps.py, bootstrap.sh) продолжают дублировать ту же логику вместо чистого делегирования через `python3 module.py`.

**Масштаб:** 287 коммитов за 15 дней, из них ~60% — фиксы багов, вызванных рассинхронизацией shell и Python реализаций.

**Примеры:**
- Шаги 1-13 bootstrap проходят через **оба** механизма одновременно: shell `checkpoint_step` (.done-файлы) И Python `state_machine._run_steps()` (state.json). Разные хеши, разные файлы для idempotency.
- `steps.py._ssl_cert_provision()` (DEPRECATED, вызов shell s3-ssl-cache.sh) И `cert_orchestrator.orchestrate_certs()` (CANONICAL, прямой импорт s3_ssl_cache.py). Оба живы в коде.
- `content-hash.sh` (shell, хеширует node-lifecycle.sh) И `state_machine._step_hash()` (Python, хеширует state_machine.py). Разный набор файлов → разный хеш → непредсказуемое поведение idempotency.

### RC-2: Отсутствие единой Python shared library

**Суть:** Python-модули в `core/internal/bootstrap/` не используют общие библиотеки для одинаковых операций. Каждый модуль реализует docker pull/up/healthcheck, YAML-парсинг, content hash с нуля.

**Масштаб:** 4+ реализации YAML-key extraction, 3 реализации content hash, 2 реализации docker compose orchestration, 2 реализации healthcheck polling в Python.

**Примеры:**
- `steps.py._extract_context_from_node_yaml()` и `context_deployer.extract_context_from_node_yaml()` — 100% копипаста (чтение node.yaml → contexts[0].name).
- `docker_orchestrator.py` (платформенные модули) и `context_deployer.py` (проекты) реализуют pull/build/up/healthcheck-poll независимо, с разными таймаутами и retry-стратегиями.
- `cert_orchestrator.py` и `issue-cert.sh` оба умеют вызывать acme.sh для выдачи сертификатов, но с разными fallback-стратегиями и разной обработкой ошибок.

### RC-3: Фрагментированные default-значения (4 слоя без синхронизации)

**Суть:** Одна и та же переменная (POSTGRES_PASSWORD, S3_ENDPOINT_URL, NEXTAUTH_SECRET, etc.) имеет разные default-значения в 4 слоях:
1. `secret-definitions.yaml` → `ci_default` (формальный SoT для CI)
2. `.env.example` → значение для разработчика
3. `docker-compose.base.yml` → `${VAR:-default}` (Docker Compose fallback)
4. Shell-скрипты → `${VAR:-default}` (bash fallback)

Нет механизма синхронизации между слоями. Изменение в одном слое не отражается в других.

**Масштаб:** 12+ переменных с конфликтующими default-значениями.

**Примеры:**
- **POSTGRES_PASSWORD:** `testpass` (.env) vs `test-pg-pwd` (.env.example + secret-definitions) vs `test-postgres-password` (hermes-agent/.env). 6 разных значений.
- **S3_ENDPOINT_URL:** `https://s3.timeweb.cloud` (compose) vs `https://s3.twcstorage.ru` (upload-s3.sh). Плюс взаимный циклический fallback S3_ENDPOINT_URL↔S3_ENDPOINT в compose.
- **PLATFORM_DOMAIN:** `ai-platform.local` (везде) vs `tronyx.ru` (gen-env-platform.sh default). При `make project-sync-env` без явного домена генерируется tronyx.ru.

### RC-4: Мёртвый код не удаляется

**Суть:** Файлы, маркированные DEPRECATED, остаются на диске. Агенты (AI и человек) читают их как source of truth и создают новые ответвления на основе устаревшей логики.

**Масштаб:** ~1500 LOC мёртвого кода.

**Примеры:**
- **`nginx/install.sh`** (1107 LOC) — маркирован DEPRECATED. Содержит полные дубликаты `_issue_acme_cert()`, `_acme_install_cron()`, `issue_tls_cert()` из `issue-cert.sh`. Устанавливает cron для renewal **без** `--renew-hook` для S3 — отличается от канонической версии. Если cron был установлен этой версией, S3 backup не работает.
- **`ssl-provision.sh`** (40 LOC) — backward-compat wrapper. Все callers мигрированы, но файл существует.
- **`LITELLM_METRICS_TOKEN`** — определён в `.env.example` как пустая строка. Не используется ни одним модулем (Prometheus использует LITELLM_MASTER_KEY). Присутствует в secret-definitions.yaml.
- **Shell checkpoint для init-шагов** — `.done`-файлы и `checkpoint_step()` для шагов 1-13. Python state_machine дублирует эту же логику через state.json.

### RC-5: Разные стандарты обработки ошибок (нет единого контракта)

**Суть:** Разные deploy-пути имеют радикально разный уровень зрелости обработки ошибок. Один путь имеет rollback+retry+snapshot+audit, другой — ничего.

**Масштаб:** 7 deploy-путей, из которых только 1 имеет полный механизм обработки ошибок.

**Примеры:**
- **`deploy-project.sh`** (bash, 1179 LOC): полный rollback (ERR trap → snapshot restore → compose up старого), 3 retry pull (5/10/20s backoff), health-gate через poll_until_healthy, audit_log, snapshot, deploy hooks, image pruning.
- **`context_deployer.py`** (Python, 769 LOC): **ноль** rollback, **ноль** retry при pull, свой healthcheck (docker ps, не Docker HEALTHCHECK), **нет** audit_log, **нет** snapshot.
- **`deploy-modules.sh`** (bash, 233 LOC): нет rollback, severity-based exit (0/2), через invoke_module_interface.
- **`docker_orchestrator.py`**: свой retry (10×10s), нет rollback.
- **Healthcheck**: разные критерии здоров/не здоров между Docker HEALTHCHECK, healthcheck.sh deep mode, модульным healthcheck, Python polling.

---

## Глава 2: Домен SECRETS & TOKENS — детальный каталог проблем

Всего файлов: **25** (секреты + токены + пароли)

### 2.1 Инвентарь участников секретов

| Файл | Роль | Проблема |
|------|------|----------|
| `core/secret-definitions.yaml` | Authoritative SoT — 31 секрет | OK |
| `core/secrets-manifest.yaml` | GENERATED — definitions + consumers | OK |
| `platform-env.yaml` | GENERATED — CI defaults | OK |
| `core/lib/secrets.sh` | Decrypt + ensure secrets | Дублирует SOPS_AGE_KEY fallback |
| `core/entrypoints/secrets.sh` | Entrypoint `make secrets-unlock` | OK (тонкий фасад) |
| `core/entrypoints/bootstrap.sh` | AGE key detection | Дублирует detect_age_key() |
| `core/entrypoints/node-update.sh` | AGE key detection | Дублирует detect_age_key() |
| `core/internal/bootstrap/node-lifecycle.sh` | SOPS_AGE_KEY fallback | Дублирует SOPS_AGE_KEY fallback |
| `core/internal/secrets/decrypt-secrets.sh` | SOPS decrypt | Основной decryptor. SOPS_AGE_KEY fallback на строках 76-84 |
| `core/internal/bootstrap/secrets-init.sh` | PLATFORM_MASTER_PASSWORD → service passwords | Третья реализация htpasswd-генерации |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | Autogen secrets (7 шт.) | Хардкодный `_FALLBACK_SECRETS` не синхронизирован с definitions |
| `core/internal/bootstrap/deploy/secrets_validator.py` | Env validation + charset check | OK |
| `core/internal/scripts/generate_secrets_manifest.py` | Генератор secrets-manifest.yaml | OK |
| `core/internal/bootstrap/docker_registry_auth.py` | Docker Hub login | **Токен в командной строке** через `bash -c "echo '{token}' \| docker login"` — виден в `/proc/pid/cmdline` |
| `core/internal/llm/key_provisioner.py` | LiteLLM virtual keys | OK |
| `core/internal/llm/admin_client.py` | LITELLM Admin HTTP client | OK |

### 2.2 DRIFT-S1: detect_age_key() — 5 копий

| Файл | Строки | Отличия |
|------|--------|---------|
| `core/entrypoints/bootstrap.sh` | 53-76 | Каноническая — env → SOPS_AGE_KEY → file |
| `core/entrypoints/node-update.sh` | 47-66 | Идентичная логика, другой log prefix `[node-update]` |
| `core/lib/secrets.sh` | 134-138 | Только SOPS_AGE_KEY fallback (не полная detect_age_key) |
| `core/internal/secrets/decrypt-secrets.sh` | 76-84 | Только AGE_SECRET_KEY → SOPS_AGE_KEY fallback |
| `core/internal/bootstrap/node-lifecycle.sh` | 44 | Однострочник: `[[ -z "${AGE_SECRET_KEY:-}" && -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY"` |

**Последствия:** При изменении логики детекции (например, новый источник ключа) нужно править 5 файлов. Один пропущен — баг.

### 2.3 DRIFT-S2: htpasswd-генерация — 3 реализации с разной идемпотентностью

1. **Shell:** `core/lib/secrets.sh` → `_ensure_htpasswd_generated()` (строки 194-248). Использует `openssl passwd -apr1 -salt "$salt" "$password"` — идемпотентность через фиксированный salt.
2. **Python:** `core/internal/bootstrap/lifecycle/secrets_manager.py` → `_ensure_htpasswd()` (строки 375-450). Использует `subprocess.run(["openssl", "passwd", "-apr1", password])` — без явного salt.
3. **Shell другой:** `core/internal/bootstrap/secrets-init.sh` — инициализирует пароли, но не htpasswd напрямую.

**Последствия:** Разная идемпотентность: shell-версия с фиксированным salt гарантирует одинаковый хеш при повторном вызове, Python-версия — нет. При миграции между версиями возможен regression.

### 2.4 DRIFT-S3: _FALLBACK_SECRETS не синхронизирован с definitions

`secrets_manager.py` строки 34-42 содержит хардкодный список из 7 секретов:

```python
_FALLBACK_SECRETS = [
    {"name": "LITELLM_MASTER_KEY", "gen_command": 'echo "sk-$(openssl rand -hex 32)"'},
    {"name": "LANGFUSE_INIT_ORG_ID", ...},
    ...
]
```

Это копия `secret-definitions.yaml` (tier: generated). При добавлении/изменении generated-секрета в definitions, `_FALLBACK_SECRETS` рассинхронизируется. **Нет теста**, проверяющего `_FALLBACK_SECRETS ≡ definitions`.

### 2.5 DRIFT-S4: Docker registry token в командной строке

`docker_registry_auth.py` строка 159:

```python
result = subprocess.run(
    ["bash", "-c", f"echo '{token}' | docker login -u '{username}' --password-stdin"],
    ...
)
```

**Токен интерполируется в аргумент `-c`**. Хотя `docker login` читает из stdin (безопасно), сам токен виден в `/proc/<pid>/cmdline` для любого процесса на хосте.

### 2.6 DRIFT-S5: Конфликтующие имена секретов

| Конфликт | Описание |
|----------|----------|
| **OPENAI_API_KEY vs LITELLM_MASTER_KEY** | OPENAI_API_KEY удалён из definitions (DevPlan 049), но тест `test_secrets_validation.py` всё ещё проверяет `OPENAI_API_KEY == LITELLM_MASTER_KEY` |
| **LITELLM_METRICS_TOKEN vs LITELLM_MASTER_KEY** | Metrics token существует в .env.example (пустой), но Prometheus реально использует LITELLM_MASTER_KEY для /metrics auth. Мёртвая переменная. |
| **SSH_KEY vs CI_DEPLOY_KEY** | Документированы как один ключ с разными ролями (rsync vs forced-command), но это два разных GitHub Secret |
| **GHCR_PULL_TOKEN vs GHCR_PUSH_TOKEN** | PULL_TOKEN в definitions, PUSH_TOKEN — только в .env.example |
| **S3_ACCESS_KEY, S3_SECRET_KEY** | `consumers: []` в secrets-manifest.yaml — ни один module.yaml не требует их через env_requires. backup-cron использует через compose override, не через формальный контракт. |

### 2.7 DRIFT-S6: POSTGRES_PASSWORD — 6 разных default-значений

| Файл | Значение |
|------|----------|
| `.env` | `testpass` |
| `.env.example` | `test-pg-pwd` |
| `secret-definitions.yaml` (ci_default) | `test-pg-pwd` |
| `platform-env.yaml` (env_defaults) | `test-pg-pwd` |
| `hermes-agent/.env` | `test-postgres-password` |
| `docker-compose.test.yml` (hardcoded fallback) | `test-pg-pwd` |

**Последствия:** Разные разработчики получают разное поведение при копировании `.env` из `.env.example`. CI тесты используют `test-pg-pwd`, local dev может использовать `testpass`. DB init при разных паролях даёт silent mismatch.

### 2.8 DRIFT-S7: NEXTAUTH_SECRET — 4 разных test-значения

| Файл | Значение |
|------|----------|
| `.env` | `sk-test-nextauth-secret` |
| `.env.example` | `ci-test-nextauth-secret-32-chars-min!!` |
| `secret-definitions.yaml` | `ci-test-nextauth-secret-32-chars-min!!` |
| `hermes-agent/.env` | `test-nextauth-secret-value` |

---

## Глава 3: Домен BOOTSTRAP PIPELINE — детальный каталог проблем

Всего файлов: **30+** (entrypoints + internal + lifecycle + deploy)

### 3.1 DRIFT-B1: Двойная state machine (shell .done + Python state.json)

**Shell checkpoint** (`node-lifecycle.sh` + `lib/checkpoint.sh`):
- Шаги 1-13: `checkpoint_step "ssh-access" step_1_ssh_access`
- Состояние: `.done`-файлы в `/var/lib/platform/.bootstrap-checkpoints/`
- Content hash: через `content-hash.sh` (хеширует `node-lifecycle.sh` + extra)

**Python state machine** (`state_machine.py`):
- INIT_STEPS = 23 шага
- Состояние: `state.json` в `/var/lib/platform/.bootstrap/`
- Content hash: через `hashlib.sha256` (хеширует `state_machine.py` + extra)

**Проблема:** Шаги 1-13 проходят через **оба** механизма одновременно. Разные хеши (разные файлы) → shell checkpoint может пропустить шаг, а Python — выполнить (или наоборот). Двойная проверка idempotency без преимущества.

### 3.2 DRIFT-B2: SSL provisioning — 4 реализации

| Реализация | Файл | Статус |
|-----------|------|--------|
| `_ssl_cert_provision()` | `lifecycle/steps.py:705` | DEPRECATED — subprocess к s3-ssl-cache.sh (shell) |
| `_ssl_provision_via_orchestrator()` | `state_machine.py:1773` | CANONICAL — importlib cert_orchestrator |
| `update_step_3_ssl_provision()` | `node-lifecycle.sh:82` | Shell wrapper → `python3 state_machine.py --run-step 4` |
| `orchestrate_certs()` | `cert_orchestrator.py:134` | CANONICAL — Python-native boto3 S3 |

**Дублирование:** `steps.py._ssl_cert_provision()` и `cert_orchestrator.orchestrate_certs()` — оба проверяют S3 cache, оба вызывают `issue-cert.sh`, оба логируют результат. Разница: первый через subprocess к shell-кэшу, второй через прямой импорт `s3_ssl_cache.py`.

### 3.3 DRIFT-B3: 4 entrypoint'а для deploy context

| # | Entry | Механизм |
|---|-------|----------|
| 1 | `state_machine.py` step 23 (init) / step 8 (update) | inline вызов `_steps._step_deploy_context()` |
| 2 | `steps._step_deploy_context()` | importlib загрузка `cert_orchestrator` + `context_deployer` |
| 3 | `make deploy-context` → `entrypoints/deploy-context.sh` | subprocess к `context_deployer.py` |
| 4 | `context_deployer.main()` | standalone CLI |

Все 4 делают одно и то же: cert orchestration + project deploy + vhost render + verify. #3 и #4 — standalone (не требуют state machine), #1 и #2 — встроены в bootstrap lifecycle.

### 3.4 DRIFT-B4: Content hash — 3 разные реализации

| Файл | Алгоритм | Файлы для хеширования |
|------|----------|----------------------|
| `content-hash.sh` | `cat paths \| sha256sum` | `node-lifecycle.sh` + extra |
| `state_machine._step_hash()` | `hashlib.sha256` | `state_machine.py` + extra |
| `add-vhost.sh:_compute_vhost_hash()` | `sha256sum file \| cut -d' ' -f1` | vhost-файлы |

**Проблема:** Shell и Python хешируют разные файлы для одного шага. При изменении `state_machine.py` shell checkpoint этого не заметит и пропустит шаг. **Потенциальный баг рассинхронизации.**

### 3.5 DRIFT-B5: YAML-key extraction — 4+ копий

| Файл | Извлекает |
|------|-----------|
| `bootstrap.sh` | `python3 yaml_helpers.py "key"` |
| `preflight.py:459` | `_extract_domain_from_node_yaml()` — domain |
| `steps.py:925` | `_extract_context_from_node_yaml()` — context |
| `context_deployer.py:214` | `extract_context_from_node_yaml()` — context (100% копипаста steps.py) |

### 3.6 DRIFT-B6: Docker compose orchestration — 2 пути с разной зрелостью

| Аспект | `docker_orchestrator.py` (платформа) | `context_deployer.py` (проекты) |
|--------|--------------------------------------|-------------------------------|
| pull | `_pull_module_images()` | `_docker_compose_pull()` |
| build | `deploy_docker_module()` inline | `_docker_compose_build()` |
| up | `deploy_docker_module()` inline | `_docker_compose_up()` |
| healthcheck | `_wait_for_readiness()` (invoke_module_interface) | `_wait_until_healthy()` (docker ps --filter) |
| image check | `_check_image_exists()` (manifest inspect) | Нет |
| retry pull | Нет | Нет |
| rollback | Нет | Нет |

---

## Глава 4: Домен CERTIFICATES & SSL — детальный каталог проблем

Всего файлов: **40** (production issuance, lifecycle integration, dev certs, nginx configs, templates, tests, deprecated)

### 4.1 DRIFT-C1: `nginx/install.sh` — 1107 LOC мёртвого кода с дубликатами

Маркирован DEPRECATED (`install_type: docker`). Содержит точные дубликаты функций из `issue-cert.sh`:
- `_issue_acme_cert()` — дубликат
- `_acme_install_cron()` — **отличается**: НЕ устанавливает `--renew-hook` для S3
- `_acme_verify_cert()` — дубликат
- `issue_tls_cert()` — дубликат

**Последствия:** Если cron был установлен через `install.sh` на старой ноде, renewal не будет пушить сертификаты в S3. Агенты могут прочитать этот файл и использовать устаревшую логику.

### 4.2 DRIFT-C2: Shadow cert path `/etc/nginx/ssl/`

Файл `templates/platform-default.conf.template` (Docker variant) ссылается на `/etc/nginx/ssl/<domain>.crt` + `.key` + `.ca.crt` — путь, который **никто не создаёт**. Нет скрипта, который кладёт сертификаты в эту директорию.

Одновременно `config/platform-default.conf.template` (основной) ссылается на стандартный LE-путь `/etc/letsencrypt/live/<domain>/`.

**Две системы шаблонов с разными cert-путями.**

### 4.3 DRIFT-C3: `cert_orchestrator.py` vs `issue-cert.sh` — пересекающаяся функциональность

Оба умеют вызывать `acme.sh --issue`, но:
- `cert_orchestrator`: S3 restore-first + self-signed fallback. **Не знает** про HTTP-01 fallback.
- `issue-cert.sh`: DNS-01 (primary) + HTTP-01 (fallback) + cron + project certs. **Не знает** про self-signed fallback.

Нет единого «вызови cert issuance, получи лучший доступный результат».

### 4.4 DRIFT-C4: Три параллельных пути renewal

| Механизм | Где установлен | S3 sync после renewal? |
|----------|----------------|----------------------|
| `acme.sh --cron` daily | `issue-cert.sh:_acme_install_cron()` | ✅ `--renew-hook` с upload |
| `acme.sh --cron` daily | `nginx/install.sh:_acme_install_cron()` (DEPRECATED) | ❌ Нет S3 upload |
| `acme.sh --install-cronjob` | Оба скрипта | Зависит от того, кто установил последним |

### 4.5 DRIFT-C5: Двойной `--reloadcmd` — последний перезаписывает

- `issue-cert.sh` строка 221: `systemctl reload nginx && python3 s3_ssl_cache.py upload <domain>`
- `nginx/install.sh` строка 228: `systemctl reload nginx` (без S3)

Если оба вызывались последовательно, последний `--install-cert` перезаписывает reloadcmd. Порядок вызовов определяет поведение.

### 4.6 DRIFT-C6: Два dev cert filename в одной директории

- `generate-dev-certs.sh` → создаёт `_local.pem` и `_local-key.pem`
- `add-vhost.sh` harness → создаёт `fullchain.pem` и `privkey.pem`

Оба в `/etc/nginx/dev-certs/`. Разные имена для одной цели.

### 4.7 DRIFT-C7: platform-vhost vs остальные vhost — разные cert-пути

- `platform-vhost.conf.template` → отдельный cert `platform.${PLATFORM_DOMAIN}`
- grafana/loki/prometheus/langfuse/hermes vhost → `include ssl-params.conf` → wildcard `${PLATFORM_DOMAIN}`

Если wildcard-сертификат не покрывает `platform.` поддомен (например, HTTP-01 fallback), то 5 vhost'ов используют рабочий wildcard, а platform-vhost — сломан.

### 4.8 DRIFT-C8: Кросс-шаблонный синтаксический clash

- `config/*.conf.template` → `${PLATFORM_DOMAIN}` → envsubst/sed
- `templates/*.conf.template` → `{{PLATFORM_DOMAIN}}` → template_engine.py

Оба используют суффикс `.template`. Агент, не читающий разметку, легко перепутает.

---

## Глава 5: Домен DEPLOY PIPELINE — детальный каталог проблем

Всего файлов: **20+** (entrypoints, internal, CI workflows)

### 5.1 DRIFT-D1: Семь различных путей доставки кода на VPS

| # | Путь | Механизм | Триггер |
|---|------|----------|---------|
| 1 | CI → platform-deliver + deploy.sh | tar stdin via SSH forced-command | `git push` → CI |
| 2 | Direct deploy-project (bypass CI) | tar + SSH, тот же platform-deliver | `make deploy-project` |
| 3 | Context deployer (Python) | ghcr.io pull (+ build fallback) | `make deploy-context` / bootstrap step 23 |
| 4 | deploy-modules.sh | docker compose up (system: install.sh) | node-lifecycle init/update |
| 5 | Core SCP/rsync | SCP/rsync push | CI workflow core-deploy |
| 6 | Context-overlay git | `git clone/pull` | `ensure_context_repo()` |
| 7 | Bootstrap compose stub | Генерация minimal nginx:alpine | context_deployer |

### 5.2 DRIFT-D2: Content hash — три реализации (см. также DRIFT-B4)

| Файл | Алгоритм | Применение |
|------|----------|------------|
| `content-hash.sh` | `cat paths \| sha256sum` | Per-step checkpoint в bootstrap |
| `checkpoint.sh` | Импортирует content-hash.sh, fallback по VERSION | Оркестрация node-lifecycle |
| `add-vhost.sh` | `sha256sum` + macOS fallback `shasum` + свой fallback | GENERATED-маркер в vhost |
| `upload.py` | `hashlib.sha256()` chunked read | S3-метадата backup-cron |

### 5.3 DRIFT-D3: Docker операции — три caller'а с разными retry/rollback

| Где | Retry pull | Таймаут | Rollback |
|-----|-----------|---------|----------|
| `deploy-project.sh` (bash) | 3 попытки (5/10/20s) | ~60s | ✅ Полный: ERR trap → snapshot restore |
| `context_deployer.py` (Python) | Нет | 120s | ❌ Отсутствует |
| `docker_orchestrator.py` (Python) | Через pre-pull | Переменный | ❌ Отсутствует |

### 5.4 DRIFT-D4: Два парсера SSH_ORIGINAL_COMMAND

- `deploy.sh` entrypoint: парсит `ping`/`exit`/`remove`/`status`/`verify`/`deploy`
- `deploy-project.sh` internal: парсит `platform-deliver`/`platform-deploy`/`verify`/deploy

Оба выполняют stripping префиксов пути, очистку env-переменных. Хрупкая цепочка: `deploy.sh exec` → `deploy-project.sh`, `SSH_ORIGINAL_COMMAND` сохраняется через exec. Документированные TRAP'ы (BUG 2026-07-20, BUG 2026-07-22).

### 5.5 DRIFT-D5: platform-deliver собирается в трёх местах

- `entrypoints/deploy-project.sh` (строка 230-236): строит deliver verb
- `internal/deploy-project.sh` (строка 456-481): парсит и исполняет
- `reconcile-projects.sh` (строка 192): генерирует verb при конвергенции

### 5.6 DRIFT-D6: Разные форматы audit-логов

- Bash: `audit_log tag STATUS msg` (через lib)
- Python: `[ts] tag status=...` (прямой file.append)

---

## Глава 6: Домен CONFIGURATION & ENV — детальный каталог проблем

Всего файлов: **30+** (YAML-конфиги, env-файлы, generated Python, JSON-схемы)

### 6.1 DRIFT-E1: POSTGRES_PASSWORD — 6 разных default-значений

См. DRIFT-S6 в Главе 2.

### 6.2 DRIFT-E2: S3_ENDPOINT_URL — циклический fallback + 3 разных дефолта

```yaml
# backup-cron/docker-compose.base.yml (строки 66-67):
S3_ENDPOINT_URL: "${S3_ENDPOINT_URL:-${S3_ENDPOINT:-https://s3.timeweb.cloud}}"
S3_ENDPOINT:     "${S3_ENDPOINT:-${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}}"
# ВЗАИМНЫЙ FALLBACK — если обе не заданы, циклическая ссылка
```

```bash
# backup-cron/scripts/upload-s3.sh (строка 40):
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-${S3_ENDPOINT:-https://s3.twcstorage.ru}}"
# ДРУГОЙ ДЕФОЛТ: s3.twcstorage.ru vs s3.timeweb.cloud
```

```yaml
# langfuse/docker-compose.base.yml (строка 86):
LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: "${S3_ENDPOINT_URL:-}"
# ПУСТОЙ дефолт
```

**Три разных хоста, два имени переменной (URL vs без URL), взаимный циклический fallback.**

### 6.3 DRIFT-E3: NEXTAUTH_SECRET — 4 разных test-значения

См. DRIFT-S7 в Главе 2.

### 6.4 DRIFT-E4: Три Jinja2-подобных механизма

| Механизм | Файл | Грамматика |
|----------|------|-----------|
| `template_engine.py` | `core/internal/template_engine.py` | `{{UPPER_SNAKE}}` — strict regex |
| Jinja2 (status page) | `core/modules/status-page/app.py` | `{{ var_name }}` — full Jinja2 |
| Jinja2 (LLM config) | `core/internal/llm/config_renderer.py` | `{{ var_name }}` — full Jinja2 |
| Docker Compose | Все `docker-compose.base.yml` | `${VAR}` / `${VAR:-default}` |
| envsubst (nginx) | `config/*.conf.template` | `${VAR}` |

### 6.5 DRIFT-E5: Variable naming inconsistencies

| Концепт | Имена | Где |
|---------|-------|-----|
| S3 endpoint | `S3_ENDPOINT_URL`, `S3_ENDPOINT` | backup-cron, langfuse, .env |
| Master email | `PLATFORM_MASTER_EMAIL`, `HERMES_DASHBOARD_USERNAME`, `GF_SECURITY_ADMIN_USER`, `LANGFUSE_INIT_USER_EMAIL` | 4 имени для одной сущности |
| Master password | `PLATFORM_MASTER_PASSWORD`, `HERMES_DASHBOARD_PASSWORD`, `GF_SECURITY_ADMIN_PASSWORD`, `LANGFUSE_INIT_USER_PASSWORD` | 4 имени |
| Node name | `NODE_NAME`, `NODE` | deploy scripts, Makefile |
| CI deploy key | `CI_DEPLOY_KEY`, `PLATFORM_CI_DEPLOY_KEY_FILE`, `SSH_KEY` | workflows + scripts |
| Context | `CONTEXT`, `PLATFORM_CONTEXT` | .env, deploy scripts |

### 6.6 DRIFT-E6: PLATFORM_DOMAIN default divergence

```bash
# gen-env-platform.sh строка 92:
default "tronyx.ru"

# .env / .env.example / compose:
"ai-platform.local"
```

При `make project-sync-env` без явного домена генерируется `tronyx.ru`, но все compose-дефолты ожидают `ai-platform.local`.

### 6.7 DRIFT-E7: NO_PROXY — 3 разных списка

| Источник | Сервисы |
|----------|---------|
| `platform-infra.yaml` (SoT — `no_proxy_internal`) | `localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse` |
| `.env.example` | `...+litellm,langfuse,minio,grafana,prometheus` (расширенный) |
| `hermes-agent compose` (default) | `...+litellm,langfuse,postgres,pgbouncer,redis,clickhouse,minio,grafana,prometheus` |

SoT содержит только core-сервисы, фактический список включает все сервисы.

### 6.8 DRIFT-E8: GF_SECURITY_ADMIN_USER — chain fallback с двумя explicit значениями

```yaml
# compose:
GF_SECURITY_ADMIN_USER: "${GF_SECURITY_ADMIN_USER:-${HERMES_DASHBOARD_USERNAME:-admin@${PLATFORM_DOMAIN:-ai-platform.local}}}"
```

`.env.example` задаёт оба явно (`admin@ai-platform.local`), `.env` задаёт оба явно (`admin@tronyx.ru`). Взаимозаменяемость через chain fallback маскирует проблему — при удалении одного поведение меняется неожиданно.

---

## Глава 7: Домен HEALTHCHECK — детальный каталог проблем

Всего файлов: **20+** (lib, entrypoint, orchestrator, 14 модулей, Python, тесты)

### 7.1 DRIFT-H1: 9 различных механизмов проверки здоровья

| # | Механизм | Используется |
|---|----------|-------------|
| 1 | `docker inspect --format='{{.State.Health.Status}}'` | Все module healthcheck.sh (liveness), modules-healthcheck.sh |
| 2 | `docker inspect --format='{{.State.Running}}'` | postgres/clickhouse/redis/nginx/backup-cron/hermes-agent (deep) |
| 3 | `check_http()` (curl) | monitoring/minio/langfuse/logging/status-page/hermes-agent (deep) |
| 4 | `poll_until_healthy()` (generic bash loop) | lib/healthcheck.sh |
| 5 | `poll_docker_health()` (convenience wrapper) | lib/healthcheck.sh |
| 6 | `check_docker_health()` (docker inspect status) | lib/healthcheck.sh |
| 7 | Service-specific tools: `pg_isready`, `redis-cli PING`, `pgrep cron`, `nginx -t` | Docker HEALTHCHECK или deep mode |
| 8 | Systemd: `systemctl is-active platform-secrets` | platform-secrets healthcheck.sh |
| 9 | Python: `subprocess.run()` + custom retry 10×10s | `docker_orchestrator.py` |

### 7.2 DRIFT-H2: 8 разных паттернов port/protocol check

| Стиль | Пример | Где |
|-------|--------|-----|
| `nc -z localhost <port>` | `nc -z localhost 80` | nginx compose |
| `bash -c 'exec 3<>/dev/tcp/...'` | `exec 3<>/dev/tcp/127.0.0.1/9000` | minio compose |
| `wget --spider /ping` | `wget --spider http://.../ping` | clickhouse compose + deep |
| `wget -q -O- /health` | `wget -q -O- http://127.0.0.1:9090/-/healthy` | prometheus, grafana, exporters |
| `curl` (docker exec vs host) | `curl -sf --max-time 5 http://localhost:80/` | nginx deep, hermes-agent deep |
| `python3 -c "urllib.request..."` | `urllib.request.urlopen('http://localhost:8080/healthz')` | status-page, litellm compose |
| `redis-cli ping` | `redis-cli -h 127.0.0.1 ping` | redis compose |
| `pg_isready` | `pg_isready -U postgres -h 127.0.0.1 -t 5` | postgres, pgbouncer compose |

### 7.3 DRIFT-H3: 7 разных start_period значений в Docker HEALTHCHECK

postgres=15s, pgbouncer=15s, redis=10s, clickhouse=20s, minio=30s, nginx=5s, loki=15s, prometheus=15s, grafana=15s, status-page=5s, **litellm=120s**, langfuse=40s, backup-cron=15s, exporters=15s.

Шаблоны (template-backend/frontend/fullstack): 5-10s.

**7 различных значений** (5s, 10s, 15s, 20s, 30s, 40s, 120s) без документированного обоснования для большинства.

### 7.4 DRIFT-H4: docker exec pattern — копипаста в 5 модулях

clickhouse, redis, nginx, backup-cron, hermes-agent deep mode — одинаковый паттерн:

```bash
if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
    if docker exec "$CONTAINER" <tool-specific-command>; then
        log_imp 8 "deep" "<service> OK (docker exec)"
    else
        log_imp 9 "deep" "<service> FAIL"
        exit 1
    fi
fi
```

Нет общей `_docker_exec_check()` функции в `lib/healthcheck.sh`.

### 7.5 DRIFT-H5: Два оркестратора для одной задачи

- `modules-healthcheck.sh` (bash) — single pass, без retry
- `docker_orchestrator.py` (Python) — retry 10×10s = 100s

Оба итерируют module.yaml, оба вызывают healthcheck.sh или docker inspect. Разная retry-логика, **нет координации** между ними.

### 7.6 DRIFT-H6: Deep check ≠ Docker HEALTHCHECK — разные критерии для одного сервиса

| Сервис | Docker HEALTHCHECK проверяет | healthcheck.sh deep проверяет |
|--------|------------------------------|------------------------------|
| postgres | `pg_isready` (готовность БД) | `docker inspect State.Running` (процесс жив) |
| nginx | `nc -z localhost 80` (TCP) | `curl http://localhost:80/` (HTTP) |
| minio | `bash /dev/tcp` (TCP) | `check_http /minio/health/live` (HTTP) |
| loki | `/usr/bin/loki -version` (процесс) | `check_http :3100/ready` (HTTP readiness) |
| status-page | `/healthz` (лёгкий) | `/health` (полный) |

**Только LiteLLM** правильно делегирует deep → Docker HEALTHCHECK через `check_docker_health`. Остальные модули проверяют разное.

### 7.7 DRIFT-H7: modules-healthcheck.sh дублирует docker inspect

`modules-healthcheck.sh` строки 77-81 выполняет прямой `docker inspect --format`, вместо вызова `check_docker_health()` из `lib/healthcheck.sh`. Дублирование библиотечной функции в оркестраторе.

---

## Глава 8: Маппинг проблем на будущие DevPlan-волны

Проблемы сгруппированы по корневым причинам для создания независимых DevPlan'ов:

### Wave A: Унификация секретов и токенов (RC-1, RC-3)
- DRIFT-S1: detect_age_key() → единая lib
- DRIFT-S2: htpasswd → единая Python-реализация
- DRIFT-S3: _FALLBACK_SECRETS → синхронизация с definitions + тест
- DRIFT-S4: docker_registry_auth → безопасный Popen stdin
- DRIFT-S5: конфликтующие имена → инвентаризация и удаление мёртвых
- DRIFT-S6, S7: POSTGRES_PASSWORD, NEXTAUTH_SECRET defaults → единый SoT

### Wave B: Унификация bootstrap pipeline (RC-1, RC-2)
- DRIFT-B1: двойная state machine → удаление shell checkpoint для init-шагов
- DRIFT-B2: 4 SSL provisioning → единый cert_orchestrator
- DRIFT-B3: 4 entrypoint'а deploy context → унификация
- DRIFT-B4: 3 content hash → единая Python-реализация
- DRIFT-B5: YAML-key extraction → shared Python module
- DRIFT-B6: docker compose ops → shared Python library

### Wave C: Унификация сертификатов и SSL (RC-1, RC-4)
- DRIFT-C1: nginx/install.sh → удаление
- DRIFT-C2: shadow cert path → удаление или реализация
- DRIFT-C3: cert_orchestrator vs issue-cert → унификация
- DRIFT-C4: 3 renewal пути → единый механизм
- DRIFT-C5: двойной reloadcmd → единый хук
- DRIFT-C6: dev cert filenames → гармонизация
- DRIFT-C7: platform-vhost cert path → консистентность
- DRIFT-C8: template syntax clash → документирование/gate

### Wave D: Унификация deploy pipeline (RC-2, RC-5)
- DRIFT-D1: 7 путей → документировать канонические
- DRIFT-D2: content hash → единая Python-реализация (общая с Wave B)
- DRIFT-D3: docker ops retry/rollback → единый контракт
- DRIFT-D4: SSH_ORIGINAL_COMMAND → единый парсер
- DRIFT-D5: platform-deliver → единое место сборки
- DRIFT-D6: audit-логи → единый формат

### Wave E: Унификация конфигурации и env (RC-3)
- DRIFT-E1-E3: конфликтующие defaults → единый механизм разрешения
- DRIFT-E4: три template engine → консолидация или документирование
- DRIFT-E5: naming → единый стандарт имён
- DRIFT-E6: PLATFORM_DOMAIN default → единый SoT
- DRIFT-E7: NO_PROXY → синхронизация
- DRIFT-E8: GF_SECURITY_ADMIN_USER chain → упрощение

### Wave F: Унификация healthcheck (RC-2, RC-5)
- DRIFT-H1: 9 механизмов → консолидация
- DRIFT-H2: 8 port-check паттернов → общий `check_tcp()`
- DRIFT-H3: 7 start_period → стандартизация
- DRIFT-H4: docker exec копипаста → `_docker_exec_check()`
- DRIFT-H5: два оркестратора → единый механизм
- DRIFT-H6: deep ≠ HEALTHCHECK → консистентность
- DRIFT-H7: modules-healthcheck дублирование → вызов lib

### Wave G: Мёртвый код (RC-4) — может быть частью других волн
- `nginx/install.sh` (1107 LOC)
- `ssl-provision.sh` (40 LOC)
- `LITELLM_METRICS_TOKEN` (в .env.example + definitions)
- Shell checkpoint .done-файлы для init-шагов

---

## File Inventory (все затронутые файлы)

### Секреты/токены (25 файлов)
`core/secret-definitions.yaml`, `core/secrets-manifest.yaml`, `platform-env.yaml`, `.env`, `.env.example`, `core/lib/secrets.sh`, `core/entrypoints/secrets.sh`, `core/entrypoints/bootstrap.sh`, `core/entrypoints/node-update.sh`, `core/internal/bootstrap/node-lifecycle.sh`, `core/internal/secrets/decrypt-secrets.sh`, `core/internal/bootstrap/secrets-init.sh`, `core/internal/bootstrap/lifecycle/secrets_manager.py`, `core/internal/bootstrap/deploy/secrets_validator.py`, `core/internal/scripts/generate_secrets_manifest.py`, `core/internal/bootstrap/docker_registry_auth.py`, `core/internal/llm/key_provisioner.py`, `core/internal/llm/admin_client.py`, `core/modules/hermes-agent/.env`, `core/modules/hermes-agent/.env.example`, `core/modules/platform-secrets/install.sh`, `core/modules/platform-secrets/platform-secrets.service`, `core/modules/litellm/docker-compose.base.yml`, `core/modules/monitoring/docker-compose.base.yml`, `tests/test_secrets_validation.py`

### Bootstrap pipeline (30 файлов)
`core/entrypoints/bootstrap.sh`, `core/entrypoints/deploy-context.sh`, `core/entrypoints/converge.sh`, `core/entrypoints/node-update.sh`, `core/internal/bootstrap/node-lifecycle.sh`, `core/internal/bootstrap/scp-deliver.sh`, `core/internal/bootstrap/remote-cmd.sh`, `core/internal/bootstrap/content-hash.sh`, `core/internal/bootstrap/preflight.py`, `core/internal/bootstrap/lifecycle/state_machine.py`, `core/internal/bootstrap/lifecycle/steps.py`, `core/internal/bootstrap/deploy/context_deployer.py`, `core/internal/bootstrap/deploy/docker_orchestrator.py`, `core/internal/bootstrap/deploy/orphan_reconciler.py`, `core/internal/bootstrap/deploy/compose_preflight.py`, `core/internal/bootstrap/deploy/secrets_validator.py`, `core/internal/bootstrap/deploy/spool_validator.py`, `core/internal/bootstrap/deploy/content_hash.py`, `core/internal/bootstrap/deploy/sudoers_generator.py`, `core/internal/bootstrap/converge.sh`, `core/internal/bootstrap/converge/reconciler.py`, `core/lib/checkpoint.sh`, `core/lib/secrets.sh`, `core/internal/bootstrap/issue-cert.sh`, `core/internal/bootstrap/s3-ssl-cache.sh`, `core/internal/bootstrap/s3_ssl_cache.py`, `core/internal/bootstrap/cert_orchestrator.py`, `core/internal/bootstrap/ssl-provision.sh`, `core/internal/bootstrap/install-acme.sh`

### Сертификаты/SSL (40 файлов)
`core/internal/bootstrap/cert_orchestrator.py`, `core/internal/bootstrap/issue-cert.sh`, `core/internal/bootstrap/install-acme.sh`, `core/internal/bootstrap/ssl-provision.sh`, `core/internal/bootstrap/s3_ssl_cache.py`, `core/internal/bootstrap/s3-ssl-cache.sh`, `core/internal/bootstrap/lifecycle/steps.py`, `core/internal/bootstrap/lifecycle/state_machine.py`, `core/internal/bootstrap/preflight.py`, `nginx/install.sh` (DEPRECATED), `core/modules/nginx/generate-dev-certs.sh`, `nginx/config/platform-default.conf.template`, `nginx/config/platform-vhost.conf.template`, `nginx/config/ssl-params.conf.template`, `nginx/config/grafana-vhost.conf`, `nginx/config/loki-vhost.conf`, `nginx/config/prometheus-vhost.conf`, `nginx/config/langfuse-vhost.conf`, `nginx/config/hermes-dashboard.conf`, `nginx/dev-config/ssl-dev.conf`, `nginx/templates/platform-default.conf.template`, `core/internal/scaffold/add-vhost.sh`, `core/internal/healthcheck/metrics/cert_collector.py`, `tests/test_cert_backup_gap.py`, `tests/test_nginx_dev_certs.py`, `tests/test_cert_collector.py`, `tests/test_nginx_acme.py`, `tests/test_ssl_s3_cache.py`, `tests/test_tls_wildcard.py`, `tests/unit/test_cert_upload_on_skip.py`, `tests/unit/test_cert_orchestrator.py`, `tests/unit/test_s3_ssl_cache.py`

### Deploy pipeline (20 файлов)
`core/entrypoints/deploy.sh`, `core/entrypoints/deploy-project.sh`, `core/internal/deploy/deploy-project.sh`, `core/internal/deploy/reconcile-projects.sh`, `core/internal/bootstrap/deploy-modules.sh`, `core/internal/bootstrap/deploy/context_deployer.py`, `core/internal/bootstrap/scp-deliver.sh`, `core/internal/bootstrap/deploy/docker_orchestrator.py`, `core/internal/bootstrap/deploy/context_overlay.py`, `core/internal/bootstrap/deploy/secrets_validator.py`, `core/internal/bootstrap/deploy/orphan_reconciler.py`, `core/internal/bootstrap/deploy/sudoers_generator.py`, `core/internal/bootstrap/deploy/content_hash.py`, `core/internal/bootstrap/deploy/compose_preflight.py`, `core/internal/bootstrap/deploy/spool_validator.py`, `.github/workflows/deploy-project.yml`, `.github/workflows/platform-deploy.yml`, `.github/workflows/stage-deploy.yml`, `core/internal/bootstrap/content-hash.sh`, `core/lib/checkpoint.sh`

### Конфигурация/env (30+ файлов)
`platform-env.yaml`, `core/platform-infra.yaml`, `core/secret-definitions.yaml`, `core/secrets-manifest.yaml`, `core/entrypoint-manifest.yaml`, `core/templates/template-manifest.yaml`, `.env`, `.env.example`, `core/modules/*/module.yaml` (×13), `core/modules/*/docker-compose.base.yml` (×12), `core/modules/*/docker-compose.test.yml` (×12), `core/modules/hermes-agent/.env`, `core/modules/hermes-agent/.env.example`, `templates/template-*/.env.platform` (×3), `core/internal/template_engine.py`, `tests/_conftest/smoke_env_generated.py`, `tests/helpers/env_defaults_generated.py`, `core/internal/scripts/generate_platform_env.py`, `core/internal/llm/config_renderer.py`, `core/modules/status-page/app.py`

### Healthcheck (20+ файлов)
`core/lib/healthcheck.sh`, `core/entrypoints/healthcheck.sh`, `core/internal/healthcheck/modules-healthcheck.sh`, `core/internal/healthcheck/tor-proxy-healthcheck.sh`, `core/modules/*/healthcheck.sh` (×14), `core/modules/*/docker-compose.base.yml` (×12, HEALTHCHECK), `core/internal/bootstrap/deploy/docker_orchestrator.py`, `tests/test_lib_healthcheck.py`, `tests/test_healthcheck_static.py`, `tests/test_healthcheck_contract.py`, `tests/gates/test_gate_healthcheck_contract.py`

$END_BRIEF
