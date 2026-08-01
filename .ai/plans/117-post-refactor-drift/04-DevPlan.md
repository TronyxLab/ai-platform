# 04-DevPlan — Бриф C: SoT-унификация дублей логики

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 18–26 программного брифа 117 — устранение дублирования логики в пользу единого Source of Truth (shared/), без добавления нового функционала.
- DESCRIPTION: 9 задач: (18) две orphan-реконсиляции → orphan_reconciler как канон, (19) watchdog AuditLogger + DockerManager → shared/audit_logger + shared/docker_compose, (20) secrets_validator wrapper → NodeYaml.get_modules(), (21) две openssl-валидации → новый shared/ssl_certs.py, (22) DeployResult ×4 → переименование по доменам, (23) platform-infra.yaml → единый loader, (24) key_provisioner shim → project_registry, (25) hermes-agent deps pg_isready → здоровый паттерн (без изменений), (26) sha256 stdlib + boto3-фабрики — низкая ценность унификации.
- RATIONALE: 7 из 9 задач брифа подтверждены после верификации в коде. Две задачи (25, 26) содержат завышенные оценки дублирования — переквалифицированы. Все решения минимальны и обратимы, без нового функционала (AC5 программы).
- ACCEPTANCE_CRITERIA:
  - AC-C1: docker_orchestrator._reconcile_orphan_containers удалён; orphan_reconciler.batch_orphan_reconciliation — единственный канон.
  - AC-C2: watchdog DockerManager использует shared/docker_compose.py для compose-операций; AuditLogger заменён на logging + опциональный audit_logger.
  - AC-C3: secrets_validator.parse_modules_from_node_yaml использует NodeYaml.get_modules() для чтения; нормализация остаётся тонкой обёрткой.
  - AC-C4: новый shared/ssl_certs.py с извлечёнными openssl-примитивами; s3_ssl_cache + cert_orchestrator импортируют его.
  - AC-C5: DeployResult классы переименованы по доменам (ModuleDeployResult, ServiceDeployResult, OrchestratorDeployResult, ContextDeployResult).
  - AC-C6: platform-infra.yaml читается через единый loader (platform_config или новый); сырой yaml.safe_load устранён.
  - AC-C7: key_provisioner.discover_projects использует project_registry (реальный discovery вместо хардкода).
  - AC-C8: `make gate MODE=fast`, `make check-manifests` зелёные; существующие гейты (parity, dead-code, docker_sole_path, ssh_opts_sole_path) не затронуты.
- IMPLEMENTS: 117 01-Brief задачи 18–26.
- IMPACTS: core/internal/bootstrap/deploy/docker_orchestrator.py, core/internal/bootstrap/deploy/orphan_reconciler.py, core/modules/hermes-agent/watchdog/agent_watchdog.py, core/internal/bootstrap/deploy/secrets_validator.py, core/internal/shared/node_yaml.py, core/internal/bootstrap/s3_ssl_cache.py, core/internal/bootstrap/cert_orchestrator.py, core/internal/bootstrap/deploy/deploy_orchestrator.py, core/internal/deploy/deploy_engine.py, core/internal/deploy/orchestrator.py, core/internal/bootstrap/deploy/context_deployer.py, core/internal/config/platform_config.py, core/internal/llm/key_provisioner.py, core/internal/shared/project_registry.py, +новый core/internal/shared/ssl_certs.py.
- REQUIRES: 117 01-Brief (реестр), результаты верификации кода 2026-08-01.

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 18 (HIGH) | docker_orchestrator.py:284 vs orphan_reconciler.py:305 | **Пути некорректны**: файл — `core/internal/bootstrap/deploy/docker_orchestrator.py` (не `core/internal/deploy/`). Обе функции живы, НО имеют разную семантику: `_reconcile_orphan_containers` — per-module в sequential-пути (L585), `batch_orphan_reconciliation` — batch в parallel/orchestrator-пути (deploy_orchestrator.py:746). | Унифицировать: docker_orchestrator → делегировать в orphan_reconciler. |
| 19 (HIGH) | AuditLogger + DockerManager дублируют shared/audit_logger.py + shared/docker_compose.py | **Частично**: AuditLogger — простой ts+print+file.write (НЕ JSON-lines схема shared); DockerManager — реальный дубль: compose_down/up/pull через subprocess в обход docker_sole_path (shared/docker_compose.py). | AuditLogger → стандартный logging; DockerManager → shared/docker_compose.py. |
| 20 (MED) | parse_modules_from_node_yaml (110 LOC) дублирует node_yaml.get_modules() | **Частично**: функция УЖЕ импортирует NodeYaml (L371), но вызывает `node.get("modules", default={})` вместо `node.get_modules()`. Нормализация (dict/list→tuple) — легитимная надстройка. | Исправить вызов на NodeYaml.get_modules(); нормализацию оставить. |
| 21 (MED) | Две openssl-валидации → shared/ssl_certs.py | **shared/ssl_certs.py НЕ СУЩЕСТВУЕТ**. Дублирование подтверждено: s3_ssl_cache._validate_cert (parseability+LE+domain+expiry) и cert_orchestrator._is_cert_valid+_is_le_issuer (expiry+LE). Разный scope, но общие openssl-примитивы. | **Создать** shared/ssl_certs.py с извлечёнными примитивами. |
| 22 (MED) | DeployResult ×4 → shared/contracts.py | **4 разных класса**: deploy_orchestrator (deployed/failed/crit_count/exit_code), deploy_engine (success/project/ref/service/rollback), orchestrator (status/channel/duration/healthcheck), context_deployer (results/deployed/skipped/failed). ПОЛЯ РАЗНЫЕ — слияние в один класс с 20+ optional-полями хуже. | Переименовать по доменам (не сливать). |
| 23 (MED) | platform-infra.yaml читается из SoT и generated-копии | **Подтверждено**: docker_orchestrator (L159) — сырой yaml.safe_load platform-infra.yaml; platform_config.py — читает platform-env.yaml (generated). Единого loader'а нет. | Создать единый loader в platform_config или новом модуле. |
| 24 (MED) | key_provisioner shim → shared/project_registry.py | **Подтверждено**: discover_projects — shim с хардкодом (L62-105). Динамический импорт пытается найти несуществующие модули. project_registry.py СУЩЕСТВУЕТ, но без LLM-специфичного discovery. | Добавить llm_projects() в project_registry; shim → делегировать. |
| 25 (MED) | hermes-agent healthcheck deps дублирует postgres readiness | **НЕ подтверждено**: pg_isready — стандартный инструмент, а не дубликат. Специфичной «postgres readiness»-функции в кодовой базе нет. Hermes-agent проверяет ДОСТУПНОСТЬ postgres для себя (deps-режим), а не healthcheck postgres-контейнера. | Задача закрыта с обоснованием. |
| 26 (LOW) | sha256 ×3; boto3-фабрика ×2 → shared/s3_client.py | **Завышено**: sha256 — stdlib hashlib (5+ файлов, не дублирование). boto3-фабрики (s3_ssl_cache, upload, retention, preflight) — 4 разных конфигурации (retries, timeouts, proxy-stripping, endpoint-резолвинг). shared/s3_client.py НЕ СУЩЕСТВУЕТ. | sha256: без изменений. boto3: унифицировать s3_ssl_cache + preflight (близкие конфиги); upload + retention (модуль backup-cron) — вне скоупа (отдельный домен). |

---

## 1. Технический анализ и решения

### Задача 18 (HIGH) — унификация orphan-реконсиляции

**Факты (верифицированы):**
- `docker_orchestrator.py:284` — `_reconcile_orphan_containers(module_name, compose_args)` — per-module orphan cleanup: docker compose config → docker ps → docker inspect → docker stop/rm. Вызывается в sequential-пути `deploy_docker_module()` (L585) для каждого модуля.
- `orphan_reconciler.py:305` — `batch_orphan_reconciliation(module_entries, modules_dir)` — batch-подход: один docker ps -a для всех модулей → проверка project label → возврат списка orphan-словарей. Вызывается `deploy_orchestrator.py:746` в parallel/orchestrator-пути.
- Обе функции живы и вызываются из разных путей деплоя (sequential vs parallel).
- `orphan_reconciler.py:main()` (L535-575) — CLI-обёртка; `batch_orphan_reconciliation` — публичная функция, импортируется через `__init__.py`.
- `docker_orchestrator._reconcile_orphan_containers` — приватная, НЕ экспортируется.

**Решение D18:** сделать `orphan_reconciler` каноническим модулем orphan-реконсиляции:
1. `docker_orchestrator._reconcile_orphan_containers` → заменить на вызов `orphan_reconciler.batch_orphan_reconciliation([module_name], modules_dir)` + вызов `orphan_reconciler.remove_orphans()` (уже существует, строка 395-).
2. После замены — удалить приватную функцию `_reconcile_orphan_containers` из docker_orchestrator (L272-376).
3. Преимущество: batch-подход эффективнее (один docker ps -a), per-module вызов с одним модулем также работает через batch.

**Файлы:** `docker_orchestrator.py:272-376,585` (удаление + замена вызова), `orphan_reconciler.py` (без изменений — уже канон).

**Риск:** LOW. Функционально эквивалентно — orphan_reconciler уже используется в production-пути (deploy_orchestrator). Тесты `test_orphan_reconciler.py` покрывают batch-подход.

---

### Задача 19 (HIGH) — watchdog: AuditLogger + DockerManager → shared

**Факты (верифицированы):**

**AuditLogger** (agent_watchdog.py:267-289):
- Простой класс: `_timestamp()` + `log(message)` — print + file append в `/var/log/platform/watchdog.log`.
- Не использует JSON-lines формат, не имеет `tag/status/extra` полей.
- Используется watchdog'ом для собственного логирования (19 вызовов `self.audit.log(...)`).
- `shared/audit_logger.py` — канонический JSON-lines логгер с расширенной схемой (tag/status/msg/**extra), chmod 640/chown :adm.

**DockerManager** (agent_watchdog.py:636-739):
- `_run_docker(args, timeout)` — subprocess.run(["sudo", "docker", ...]).
- `compose_down(service)`, `compose_pull()`, `compose_up(service)` — docker compose операции через subprocess.
- `cleanup_old_images(keep)` — docker image ls + docker rmi.
- **НЕ использует** `shared/docker_compose.py` (нарушение docker_sole_path).
- Использует `sudo docker` (watchdog работает от root? проверить).

**Решение D19:**
- **AuditLogger:** заменить на стандартный `logging.getLogger(__name__)` с форматтером `%(asctime)s %(message)s`. Это убирает самописный класс без потери функциональности. Опционально: писать критические события (rollback) в audit_logger.write_audit_entry() — но это новый функционал, вне скоупа.
- **DockerManager.compose_*:** заменить на вызовы `shared/docker_compose.py`:
  - `compose_down(service)` → `docker_compose_down(compose_dir, service=service, compose_args=["-f", compose_file, "--project-name", project_name])`
  - `compose_pull()` → `docker_compose_pull(compose_dir, compose_args=[...])`
  - `compose_up(service)` → `docker_compose_up(compose_dir, service=service, compose_args=[...])`
- **DockerManager.cleanup_old_images:** оставить (raw docker image ls — не compose-операция).
- **DockerManager._run_docker:** оставить как privаte helper для raw docker-команд (docker image ls, docker stop — не покрываются shared/docker_compose.py).
- Проверить, что watchdog НЕ требует `sudo docker` (если работает из docker-контейнера с docker.sock — sudo не нужен).
- ⚠️ **Важно:** watchdog — отдельный Python-процесс (systemd timer → agent_watchdog.py). Импорт shared/docker_compose.py должен работать в этом контексте (проверить PYTHONPATH/ sys.path на VPS).

**Файлы:** `agent_watchdog.py:267-289` (AuditLogger → удалить класс, заменить на logging), `agent_watchdog.py:636-739` (DockerManager compose_* → shared/docker_compose), импорты.

**Риск:** MEDIUM. Watchdog — критичный компонент (rollback). Требуется проверка:
- Доступность shared/docker_compose.py в контексте watchdog (PYTHONPATH).
- Совместимость API: проверить сигнатуры docker_compose_down/up/pull.
- Тесты `test_agent_watchdog.py` должны пройти.

---

### Задача 20 (MED) — secrets_validator.parse_modules_from_node_yaml

**Факты (верифицированы):**
- `secrets_validator.py:360-430` — `parse_modules_from_node_yaml(node_yaml_path)`.
- УЖЕ импортирует NodeYaml (L371): `from core.internal.shared.node_yaml import NodeYaml`.
- Но вызывает `node.get("modules", default={})` (L374) вместо `node.get_modules()`.
- Нормализация (L377-430): обрабатывает dict-формат `{name: {enabled, config_overlay}}` и list-формат `[{name, enabled, config_overlay}]` → `list[tuple[str, str, str]]`.
- `NodeYaml.get_modules()` (L583-601) возвращает `list[dict]` — сырой список модулей.

**Решение D20:**
1. Заменить `node.get("modules", default={})` → `node.get_modules()` (типизированный доступ).
2. Нормализацию (dict→list, enabled/overlay извлечение) оставить в secrets_validator как тонкую надстройку. Альтернатива: добавить `get_normalized_modules()` в NodeYaml — отклонено (добавление нового метода в NodeYaml для одного потребителя нарушает критерий «≥2 потребителей» для shared/).
3. Документировать в docstring: «normalization layer over NodeYaml.get_modules() — handles legacy dict format».

**Файлы:** `secrets_validator.py:374` (1 строка — замена вызова).

**Риск:** LOW. NodeYaml.get_modules() уже используется другими потребителями; семантика идентична.

---

### Задача 21 (MED) — OpenSSL-валидации → shared/ssl_certs.py

**Факты (верифицированы):**

`s3_ssl_cache._validate_cert(cert_path, domain, check_expiry)` (L125-221):
- 4 шага: parseability (`-noout`), LE issuer (`-issuer`), domain match (`-subject`), expiry (`-checkend`).
- Использует `OPENSSL_TIMEOUT`, `CHECKEND_THRESHOLD` константы.
- Не-fatal: возвращает False при любой ошибке.

`cert_orchestrator._is_cert_valid(domain, cert_path)` (L271-297) + `_is_le_issuer(cert_path)` (L311-320):
- 2 шага: expiry (`-checkend 2592000`), LE issuer (`-issuer`).
- Хардкодит `2592000` (не константа).
- Нет проверки domain match (не нужно для целей оркестрации).
- TRAP[BUG] 2026-07-22: добавлена LE-проверка после инцидента с mkcert.

**Общие openssl-примитивы (дублируются):**
1. `openssl x509 -in <cert> -checkend <seconds> -noout` — проверка expiry.
2. `openssl x509 -in <cert> -issuer -noout` — извлечение issuer.
3. Оба модуля делают subprocess.run с capture_output, timeout, обработкой ошибок.

**Решение D21:**
1. **Создать** `core/internal/shared/ssl_certs.py` с примитивами:
   - `cert_is_parseable(cert_path: str, timeout: int = 10) -> bool`
   - `cert_check_expiry(cert_path: str, threshold_seconds: int, timeout: int = 10) -> bool`
   - `cert_get_issuer(cert_path: str, timeout: int = 10) -> str | None`
   - `cert_is_le_issuer(cert_path: str, timeout: int = 10) -> bool` — надстройка над cert_get_issuer
   - ⚠️ Константы `DEFAULT_OPENSSL_TIMEOUT = 10`, `DEFAULT_EXPIRY_THRESHOLD = 2592000` — в этом модуле.
2. `s3_ssl_cache._validate_cert` → использовать примитивы из shared/ssl_certs (parseability, expiry, LE issuer). Domain match оставить в s3_ssl_cache (специфично для S3-кеша).
3. `cert_orchestrator._is_cert_valid` → использовать `cert_check_expiry` + `cert_is_le_issuer` из shared.
4. `cert_orchestrator._is_le_issuer` → удалить, заменить на `ssl_certs.cert_is_le_issuer`.
5. Константу `2592000` в cert_orchestrator заменить на `ssl_certs.DEFAULT_EXPIRY_THRESHOLD`.

**Файлы:** новый `core/internal/shared/ssl_certs.py` (~80 LOC), `s3_ssl_cache.py:125-221` (рефакторинг), `cert_orchestrator.py:271-320` (рефакторинг).

**Риск:** LOW. Примитивы — чистые функции без состояния. Существующие тесты test_s3_ssl_cache.py и test_cert_orchestrator.py верифицируют поведение.

---

### Задача 22 (MED) — DeployResult ×4 → переименование

**Факты (верифицированы):**

| Класс | Файл:строка | Поля | Семантика |
|-------|------------|------|-----------|
| `DeployResult` | `deploy_orchestrator.py:120` | deployed, failed, crit_count, warn_count, exit_code | Результат оркестрации модулей |
| `DeployResult` | `deploy_engine.py:133` | success, project, ref, service, previous_image, rollback_performed, first_deploy_failed, error_message | Результат деплоя одного сервиса |
| `DeployResult` | `orchestrator.py:171` | status, project, channel, error_info, duration_s, healthcheck_status, snapshot_id, deploy_time, stdout, stderr, version | Результат DeployOrchestrator.receive() |
| `DeployResult` | `context_deployer.py:137` | results, deployed, skipped, failed | Агрегация результатов деплоя проектов контекста |

**Анализ:** поля принципиально разные. Слияние в один класс с 20+ optional-полями создаст:
- Неясный контракт («какие поля заполнены для этого code path?»)
- Раздувание тестов (каждый потребитель должен проверять свои поля)
- Ложные срабатывания линтеров на «unused field»

**Решение D22:** переименовать классы по доменам, сохранив обратную совместимость через алиасы:
- `deploy_orchestrator.DeployResult` → `ModuleDeployResult`
- `deploy_engine.DeployResult` → `ServiceDeployResult`
- `orchestrator.DeployResult` → `OrchestratorDeployResult` (сохранить, основной контракт)
- `context_deployer.DeployResult` → `ContextDeployResult`
- Имена импортов обновить во всех потребителях.
- `OrchestratorDeployResult` оставить как `DeployResult` с DeprecationWarning-алиасом на 1 релиз? **Нет** — без нового функционала (AC5). Просто переименовать.

**Файлы:** `deploy_orchestrator.py:120`, `deploy_engine.py:133`, `orchestrator.py:171`, `context_deployer.py:137`, + все импорты (~8-10 файлов).

**Риск:** LOW. Чистое переименование — IDE/линтер найдут все использования.

---

### Задача 23 (MED) — platform-infra.yaml: единый loader

**Факты (верифицированы):**
- `docker_orchestrator.py:159-164` — `_resolve_compose_profiles_from_infra()`: прямой `yaml.safe_load()` platform-infra.yaml для COMPOSE_PROFILES.
- `platform_config.py:59-97` — `_load_defaults()`: читает **platform-env.yaml** (generated файл, не SoT). Возвращает `_defaults` dict.
- `generate_platform_env.py` — **генерирует** platform-env.yaml из platform-infra.yaml.
- `sync_env_defaults.py` — также читает platform-infra.yaml для синхронизации .env.example.
- Проблема: docker_orchestrator читает platform-infra.yaml напрямую (сырой YAML), а не через platform_config (который читает generated копию). При расхождении SoT и generated — docker_orchestrator видит актуальные данные, platform_config — устаревшие.

**Решение D23:**
1. `_resolve_compose_profiles_from_infra()` → удалить; заменить на вызов `platform_config.get_default("COMPOSE_PROFILES")`.
2. `platform_config._load_defaults()` — переключить на чтение **platform-infra.yaml** (SoT) вместо platform-env.yaml (generated). Путь резолвинга: `repo_root / "core" / "platform-infra.yaml"`.
3. Обновить docstring и @invariants platform_config.py: теперь читает SoT, а не generated копию.
4. Проверить всех потребителей platform_config (backup_config.py, s3_ssl_cache.py, cert_orchestrator.py, preflight.py, context_deployer.py, agent_watchdog.py) — они не должны сломаться (читают конкретные ключи, структура env_defaults идентична).

**Файлы:** `docker_orchestrator.py:150-165` (удаление функции + замена вызова), `platform_config.py:59-97` (переключение на platform-infra.yaml + обновление docstring).

**Риск:** MEDIUM. platform_config переключается с generated файла на SoT. Нужно проверить:
- Структура YAML идентична на уровне env_defaults (platform-env.yaml — надмножество platform-infra.yaml).
- PLATFORM_ROOT env резолвинг (платформенный путь) остаётся рабочим.
- Тесты platform_config должны пройти.

---

### Задача 24 (MED) — key_provisioner shim → project_registry

**Факты (верифицированы):**
- `key_provisioner.py:62-105` — `discover_projects()`: пытается динамически импортировать `core.internal.deploy.project_discovery.discover_projects` и 2 других несуществующих модуля. При неудаче → возвращает хардкод `[{"name": "test-project", "llm": {"enabled": true}}]` (L106-120).
- `project_registry.py` — СУЩЕСТВУЕТ: `list_projects()` возвращает список проектов из NodeYaml, `validate_project_name()` — валидация. Но НЕТ функции для обнаружения LLM-проектов.
- NodeYaml.get_projects() возвращает `list[ProjectEntry]` — типизированные записи проектов.

**Решение D24:**
1. Добавить в `project_registry.py` функцию `discover_llm_projects(node_yaml_path: str | None = None) -> list[dict[str, Any]]`:
   - Использует NodeYaml для чтения projects из node.yaml.
   - Фильтрует проекты, у которых в ai-platform.yaml есть `llm.enabled: true`.
   - Возвращает список `[{"name": ..., "llm": {...}}]`.
2. `key_provisioner.discover_projects()` → заменить тело на вызов `project_registry.discover_llm_projects()`.
3. Удалить динамические импорты и хардкод-шим из key_provisioner (L83-120).
4. TRAP[DECISION] в key_provisioner:27-30, 78-81 — снять (рев-условие выполнено).

**Файлы:** `project_registry.py` (+40 LOC — новая функция), `key_provisioner.py:62-120` (замена shim на реальный вызов).

**Риск:** MEDIUM. LLM-проекты должны иметь `ai-platform.yaml` с `llm.enabled: true`. Если таких проектов нет — `discover_llm_projects()` возвращает `[]`, provision-llm становится no-op (корректное поведение).

---

### Задача 25 (MED) — hermes-agent deps vs postgres readiness

**Факты (верифицированы):**
- `hermes-agent/healthcheck.sh:63-77` — deps-режим проверяет PostgreSQL через `pg_isready -h $PG_HOST -p $PG_PORT` (стандартная утилита).
- Отдельной «postgres readiness»-функции в кодовой базе **нет**:
  - `core/lib/healthcheck.sh` содержит `check_docker_health` (docker inspect) — не pg_isready.
  - postgres-модуль имеет свой `healthcheck.sh` — вызывает `check_docker_health postgres` (docker inspect), не pg_isready.
  - `core/internal/healthcheck/` — метрики, не проверка доступности.
- `pg_isready` — стандартная утилита PostgreSQL, используется по прямому назначению.

**Решение D25:** задача закрыта без изменений.
- Hermes-agent deps проверяет доступность PG **для себя** (сетевая доступность) — это валидный use-case, отличный от healthcheck postgres-контейнера (docker inspect).
- `pg_isready` — стандартный инструмент, не дубликат платформенной логики.
- Если в будущем появится ≥3 потребителей pg_isready с идентичным паттерном — извлечь в shared/postgres_check.py. Пока — 1 потребитель, критерий не выполнен.

**Файлы:** без изменений.

**Риск:** NONE.

---

### Задача 26 (LOW) — sha256 + boto3-фабрики

**Факты (верифицированы):**

**sha256 (5+ использований):**
- `shared/content_hash.py` — `hashlib.sha256()` для идемпотентности bootstrap.
- `bootstrap/deploy/content_hash.py` — `hashlib.sha256()` для build-skip (другой модуль! дубль имени).
- `scaffold/vhost_renderer.py:490` — `hashlib.sha256()` для body_hash vhost.
- `bootstrap/python_deps.py:73` — `hashlib.sha256()` для хеша requirements.txt.
- `healthcheck/metrics/cert_collector.py:93` — `hashlib.sha256()` для cert_id.
- Это **стандартная библиотека** — не дублирование кода. Каждый вызов использует `hashlib.sha256()` по прямому назначению. Извлечение в обёртку создаст лишний слой абстракции без снижения дублирования.

**boto3-фабрики (4 реализации):**

| Файл | Функция | Особенности |
|------|---------|-------------|
| `s3_ssl_cache.py:79` | `_get_s3_client()` | Proxy-stripping (defence-in-depth), DEFAULT_S3_ENDPOINT_URL, max_attempts=3 |
| `upload.py:113` | `create_s3_client(config)` | Принимает config-словарь, BotoConfig с connect_timeout=30/read_timeout=60 |
| `retention.py:414` | инлайн (не функция) | Аналогичен upload.py, BotoConfig с connect_timeout=30/read_timeout=60 |
| `preflight.py:216` | инлайн (не функция) | max_attempts=1 (быстрый probe), без proxy-stripping |

**Анализ:**
- `s3_ssl_cache` + `preflight` — платформенный домен (bootstrap), близкие конфигурации.
- `upload` + `retention` — домен backup-cron (периодические задачи), свои таймауты.
- Создание единой фабрики на все 4 случая → параметры для proxy-stripping, retries, timeouts, endpoint-резолвинга → сложная сигнатура с 8+ параметрами.

**Решение D26:**
- **sha256:** без изменений. Стандартная библиотека, не дублирование.
- **boto3:** унифицировать ТОЛЬКО s3_ssl_cache._get_s3_client() + preflight (один домен):
  - Извлечь `get_s3_client(endpoint=None, access_key=None, secret_key=None, max_attempts=3, region=None)` в `shared/s3_client.py` (новый модуль).
  - s3_ssl_cache → `s3_client.get_s3_client(max_attempts=3)` (сохранить proxy-stripping как внешний вызов перед созданием клиента).
  - preflight → `s3_client.get_s3_client(access_key=..., secret_key=..., max_attempts=1)`.
- **upload + retention** (backup-cron) — НЕ трогать: отдельный домен с другими зависимостями (config-словарь, BotoConfig с таймаутами). Унификация потребует изменения интерфейса backup-cron скриптов (нарушение границ домена).

**Файлы:** новый `shared/s3_client.py` (~50 LOC), `s3_ssl_cache.py:79-101` (→ вызов s3_client), `preflight.py:210-222` (→ вызов s3_client).

**Риск:** LOW. Новая shared-функция — тонкая обёртка над boto3.client.

---

## 2. Порядок реализации

Фаза 1 — быстрые, независимые:
1. **D20** (secrets_validator → NodeYaml.get_modules) — 1 строка.
2. **D22** (DeployResult → переименование) — 4 класса + импорты.
3. **D25** (закрыта без действий).

Фаза 2 — требующие верификации контрактов:
4. **D21** (новый shared/ssl_certs.py) — создать модуль + рефакторинг потребителей.
5. **D26** (новый shared/s3_client.py) — создать модуль + рефакторинг потребителей.
6. **D24** (key_provisioner shim → project_registry) — новый метод + замена shim.

Фаза 3 — изменения с cascading-эффектом:
7. **D18** (унификация orphan-реконсиляции) — удаление функции + замена вызова.
8. **D23** (platform-infra.yaml → единый loader) — переключение platform_config на SoT + удаление сырого yaml.safe_load.

Фаза 4 — watchdog (наиболее рискованный):
9. **D19** (watchdog → shared/docker_compose + logging) — рефакторинг с проверкой PYTHONPATH и тестов.

Фаза 5 — финальная верификация:
10. `make gate MODE=fast` + `make check-manifests` — зелёные.
11. `pytest tests/unit/test_agent_watchdog.py tests/unit/test_orphan_reconciler.py tests/unit/test_docker_orchestrator.py tests/unit/test_s3_ssl_cache.py tests/unit/test_cert_orchestrator.py` — все зелёные.
12. `make check-dead-code` — зелёный (удалена только `_reconcile_orphan_containers`, не в allowlist).
13. `rg "DeployResult" core/` — только переименованные классы (кроме orchestrator.py — каноническое имя).

---

## 3. Критерии приёмки (повтор из контракта)

- AC-C1: docker_orchestrator._reconcile_orphan_containers удалён; orphan_reconciler — канон.
- AC-C2: watchdog DockerManager использует shared/docker_compose; AuditLogger → logging.
- AC-C3: secrets_validator.parse_modules_from_node_yaml → NodeYaml.get_modules().
- AC-C4: shared/ssl_certs.py создан; s3_ssl_cache + cert_orchestrator импортируют его.
- AC-C5: DeployResult переименованы по доменам (4 уникальных имени).
- AC-C6: platform_config читает platform-infra.yaml (SoT); сырой yaml.safe_load устранён.
- AC-C7: key_provisioner.discover_projects использует project_registry.discover_llm_projects (без хардкода).
- AC-C8: gate MODE=fast + check-manifests зелёные.

Дополнительно:
- `rg "class DeployResult" core/internal/` — 0 совпадений (все переименованы).
- `rg "_reconcile_orphan_containers" core/internal/` — 0 совпадений (удалена).
- `rg "def discover_projects" core/internal/llm/` — вызывает project_registry, без хардкода.
- DockerManager.compose_down/up/pull → вызывают shared/docker_compose.py (проверить grep'ом).
- 0 новых dead-code записей (удаляемые функции не были в allowlist).
- 0 новых нарушений docker_sole_path (DockerManager больше не вызывает docker compose напрямую).

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| **D19**: Watchdog-рефакторинг сломает PYTHONPATH на VPS (shared/ импорт недоступен) | Проверить sys.path в контексте systemd timer; watchdog УЖЕ импортирует из core.internal (telegram_notifier) — shared должен быть доступен. Перед коммитом: ручной прогон watchdog в CI-окружении. |
| **D19**: Удаление AuditLogger изменит формат логов → сломает парсеры | AuditLogger пишет в `/var/log/platform/watchdog.log` — проверить, читает ли кто-то этот файл (grep). Если нет — замена на стандартный logging безопасна. |
| **D23**: Переключение platform_config на platform-infra.yaml сломает контракт (другие ключи) | Структура `env_defaults` идентична в обоих файлах (platform-env.yaml генерируется из platform-infra.yaml). Проверить diff перед коммитом. |
| **D21**: shared/ssl_certs.py дублирует существующие проверки без реальной дедупликации | Примитивы — чистые openssl-обёртки. Если cert_orchestrator/s3_ssl_cache используют разные таймауты — параметризовать. |
| **D24**: discover_llm_projects требует чтения ai-platform.yaml каждого проекта → I/O на каждый проект | Количество проектов ≤10 на ноде — O(projects) приемлемо. Кеширование — вне скоупа (без нового функционала). |
| **D22**: Переименование DeployResult сломает test_shell_facade_contract.py (L287: grep `class DeployResult:`) | Обновить тест — искать новые имена классов. |
| **D26**: s3_client.get_s3_client с 5 параметрами — over-engineering для 2 потребителей | Соответствует критерию shared/: ≥2 потребителей + дедупликация. Если preflight будет единственным вторым потребителем после рефакторинга — OK. |

---

## 5. Оценка

- **Изменяемые файлы:** ~16 (4 переименования + 6 рефакторингов + 2 новых модуля + 4 импорта).
- **Новые файлы:** 2 (`shared/ssl_certs.py` ~80 LOC, `shared/s3_client.py` ~50 LOC).
- **Удаляемый код:** docker_orchestrator._reconcile_orphan_containers (~105 LOC), AuditLogger class (~25 LOC), DockerManager compose_* (~60 LOC), key_provisioner shim (~60 LOC), _resolve_compose_profiles_from_infra (~15 LOC).
- **Строк кода:** ~350 LOC новых/изменённых, ~260 LOC удалено. Чистое изменение: +90 LOC.
- **Трудозатраты:** ~0.5-1 день агент-времени. Размер: **LARGE** (>20 файлов, новый shared-модуль — architectural change) → требуется Brief + DevPlan. Бриф уже существует (01-Brief.md), настоящий DevPlan — реализация.
- **Фактический размер после коррекций:** 16 изменяемых файлов + 2 новых = 18 файлов. Граница STANDARD/LARGE (20 файлов) не пересечена, но создание нового shared-модуля (ssl_certs.py) — architectural change → LARGE по критерию «arch/schema/contract changes».

---

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 18 | Пути исправлены: `core/internal/deploy/` → `core/internal/bootstrap/deploy/` | Файлы существуют по другим путям (аудит брифа указывал несуществующие пути). |
| 19 | AuditLogger — не прямой дубль audit_logger (разная схема); фокус на DockerManager → shared/docker_compose | AuditLogger — простой ts+print, audit_logger — JSON-lines с тегами. Замена на logging. |
| 21 | shared/ssl_certs.py НЕ существует — **создать** | Бриф предполагал существующий модуль. Факт: модуля нет, дублирование openssl-примитивов реально. |
| 22 | DeployResult НЕ сливать в один класс (поля разные) → переименовать по доменам | Слияние 4 разных контрактов в 1 класс с 20+ optional-полями — антипаттерн. |
| 25 | **Задача закрыта без изменений** | pg_isready — стандартный инструмент, не дубликат платформенной логики. 1 потребитель, критерий извлечения в shared не выполнен. |
| 26 | sha256 — без изменений (stdlib); boto3 — только s3_ssl_cache+preflight, НЕ backup-cron | sha256 — stdlib hashlib, извлечение в обёртку — over-engineering. boto3 upload/retention — отдельный домен backup-cron. |
