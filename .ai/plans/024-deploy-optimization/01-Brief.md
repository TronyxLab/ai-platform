# 024-Brief: Deploy performance optimization — SSL cache, project scaffold, predeploy gate, L2 fallback

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Сократить время полного деплой-цикла с ~2 часов до ~15-20 минут через 4 взаимодополняющие оптимизации: SSL-кэширование на S3, авто-создание project scaffold при bootstrap, расширение predeploy gate для проектов, и fallback-механизм pre-built L2 образов hermes-agent.
DESCRIPTION:           На основе superposition-анализа длительности деплой-сессий (анализ 3014+ сессий, 3 StatusReport'ов, кода deploy-project.sh/deploy-modules.sh/node-lifecycle.sh) выявлены 4 ключевые оптимизации, одобренные оператором. Фокус: устранение fix-циклов и ручных мутаций VPS (60-70% времени сессии), а не микрооптимизация времени выполнения скриптов.
RATIONALE:             Сессии 019 (18.07) и 020 (20.07) заняли по ~2 часа каждая. 60-70% времени — не полезный деплой, а fix-циклы (push → CI красный → фикс → push, 5-8 мин каждый) и ручные мутации VPS (mkdir, chmod, docker network connect, /etc/hosts правки). Bootstrap занимает ~10 мин, из них 50% — docker pull образов (уже оптимизирован pre-pull фазой в DevPlan 020) и 40% — SSL provisioning. Цель: устранить первопричины fix-циклов, а не ускорять сами скрипты.
ACCEPTANCE_CRITERIA:   1. SSL-сертификаты кэшируются на S3 после успешного выпуска (fullchain + privkey + acme.sh account). При bootstrap: если кэш валиден (domain match + expiry > 30d) → восстановление, skip acme.sh issue.  2. При bootstrap (node-lifecycle.sh --mode init) для каждого проекта из node.yaml#projects создаётся /opt/projects/<name>/ с ai-platform.yaml (из context-overlay) и .env.platform.  3. Predeploy gate расширен: docker compose config dry-run для проектных compose-файлов, проверка портов на конфликты, проверка external networks, валидация ai-platform.yaml schema.  4. hermes-agent: при деплое сначала pull pre-built L2 из ghcr.io, при 404 — локальная сборка L1→L2 (fallback без дрейфа).  5. `make gate MODE=fast` — зелёный.  6. Полный цикл (bootstrap + deploy 2 проектов) ≤ 30 мин без ручных вмешательств.
IMPLEMENTS:            Инварианты 1 (Makefile-фасад), 3 (org = context), 6 (bootstrap-node идемпотентный), 9 (тестовый сервер может быть пересоздан). Суперпозиция из анализа сессий: Options A (кэш сертификатов), D (project scaffold), G (predeploy gate), B (pre-built L2 с fallback).
IMPACTS:               core/internal/bootstrap/issue-cert.sh (сохранение в S3 + восстановление), core/internal/bootstrap/node-lifecycle.sh (шаг projects-base расширен), core/internal/bootstrap/deploy-modules.sh (L2 fallback pull), tests/test_predeploy_gate.py (4 новых теста), core/internal/deploy/deploy-project.sh (predeploy валидация), node-configs/*/overlays/ (убрать /etc/hosts хак для proxy-net).
REQUIRES:              Ветка от origin/main, make gate MODE=fast зелёный до начала, working tree чистый. S3 credentials (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET) в secrets tronyx-vps.enc.yaml.
$END_ARTIFACT_CONTRACT

---

## 1. Контекст

### Статистика, обосновывающая необходимость

**Источники:** 3 StatusReport'а (001, 009, 014), 2 сессии оркестратора (019, 020), кодовая база (deploy-project.sh 1123 LOC, deploy-modules.sh 1327 LOC, node-lifecycle.sh 1250 LOC).

**Полный деплой-цикл:**
| Метрика | 001-wave1 | 009-bootstrap | 014-rebootstrap | 019-orchestrator | 020-orchestrator |
|---------|:---------:|:-------------:|:---------------:|:----------------:|:----------------:|
| Длительность | 37 мин | 10 мин (timeout) | 20 мин | ~2 часа | ~2 часа |
| Fix-циклов | 0 | 0 | 0 | 3 | 5 |
| Ручных мутаций VPS | 0 | 1 | 2 | 6 | 7 |

**Bootstrap breakdown (10 мин):**
| Фаза | Длительность | % |
|------|:-----------:|:---:|
| SCP, apt, docker, users, firewall, secrets | ~1.5 мин | 15% |
| **SSL provisioning** (2 домена DNS-01) | **~4 мин** | **40%** |
| **Docker module deploy** (pull + up + healthcheck) | **~5 мин** | **45%** |

**Docker module detail (830s sequential → 210s parallel x4):**
| Модуль | Время | Размер образа |
|--------|:-----:|:---:|
| monitoring (prometheus+grafana+exporters) | 180s | 380MB |
| langfuse (langfuse+redis) | 120s | 300MB |
| infra-metrics (cadvisor+node-exporter) | 120s | 100MB |
| clickhouse | 90s | 400MB |
| logging (loki+promtail) | 90s | 250MB |
| minio, postgres, litellm, hermes-agent | 60s each | 120-350MB |
| nginx, redis, backup-cron | 30s each | 15-70MB |

**Сессионные накладные расходы (60-70% от 2 часов):**
| Операция | Время | Частота |
|----------|:---:|:---:|
| Цикл «push → CI красный → fix → push» | 5-8 мин | ×2-6 |
| Ручные SSH-фиксы (права, mkdir, network connect) | 2-5 мин | ×4-10 |
| `gh run watch` ожидание | 5-12 мин | ×1-3 |
| Диагностика по логам CI | 3-10 мин | ×3-8 |

### Ключевые проблемы из сессий 019-020

| ID | Проблема | Причина | Где чинить |
|----|----------|--------|------------|
| M3 | `/opt/projects/<name>/` не существует при первом деплое | Bootstrap не создаёт project директории | node-lifecycle.sh step 6b |
| M4 | nginx → upstream timeout | Контейнеры проектов на изолированных default-сетях, не на proxy-net | project scaffold + templates |
| M5 | `/etc/hosts` захардкоженные IP контейнеров | nginx resolver привязан к IP, а не docker DNS | overlay nginx config |
| B3 | Org-level secrets не резолвятся в CI | GitHub Free ограничение | repo-level secrets через `gh secret set` |
| — | SSL 4 мин на каждый bootstrap | Каждый раз новый issue через DNS-01 | S3 cache |

---

## 2. Решения (4 волны)

### Волна 1: SSL certificate caching on S3

**Проблема:** SSL provisioning занимает 40% bootstrap (~4 мин на 2 домена). При активной разработке сервер регулярно сбрасывается до bare metal (инвариант 9), но физически это тот же сервер с теми же доменами. Сертификаты перевыпускаются заново каждый раз.

**Решение:**
1. После успешного выпуска сертификата (`issue-cert.sh`) — сохранять на S3:
   - `s3://<S3_BUCKET>/platform/ssl-certs/<domain>/fullchain.pem`
   - `s3://<S3_BUCKET>/platform/ssl-certs/<domain>/privkey.pem`
   - `s3://<S3_BUCKET>/platform/ssl-certs/<domain>/chain.pem`
   - `s3://<S3_BUCKET>/platform/ssl-certs/<domain>/account/` (acme.sh account data)
2. При bootstrap (перед issue-cert.sh) — проверять S3:
   - Скачать fullchain.pem → `openssl x509 -checkend 2592000 -noout` (валидность > 30 дней)
   - `openssl x509 -subject -noout` → проверить что CN/SAN содержит текущий domain из node.yaml
   - Если валиден → восстановить в `/etc/letsencrypt/live/<domain>/` + `/opt/acme.sh/ca/`
   - Skip `issue_tls_cert()`, перейти к установке cron и verify
3. S3-клиент: переиспользовать `upload.py` из backup-cron (boto3, уже протестирован).
4. Graceful degradation: если S3 недоступен или сертификат невалиден — fallback к полному issue через acme.sh DNS-01.

**Эффект:** Bootstrap: 10 мин → **~6 мин** (SSL из ~4 мин → ~10s на S3 download + verify).

**Файлы:** `issue-cert.sh` (upload после issue), `node-lifecycle.sh` (download перед issue), `upload.py` (переиспользование).

---

### Волна 2: Project scaffold auto-creation in bootstrap

**Проблема:** При первом деплое проекта CI падает с «FATAL: project directory not found» потому что bootstrap не создаёт `/opt/projects/<name>/`. Каждый проект требует 3-4 ручных мутации VPS (M3: mkdir, M4: network connect, M5: /etc/hosts, + права). Это 4-6 fix-циклов = **20-40 минут на сессию**.

**Решение:**
1. В `node-lifecycle.sh --mode init`, step 6b (`projects-base`):
   - Для каждого проекта из `node.yaml#projects[name]`:
     - Создать `/opt/projects/<name>/` (owner ci-deploy:ci-deploy)
     - Скопировать `ai-platform.yaml` из context-overlay (`/opt/<context>/platform/projects/<name>/ai-platform.yaml`) если есть, иначе сгенерировать минимальный stub из node.yaml (`name`, `domain`, `target_node`)
     - Сгенерировать `.env.platform` из `platform-env.yaml` через `gen-env-platform.sh`
     - `docker network connect proxy-net <project-container>` — НЕ делать! Вместо этого:
2. **Убрать `/etc/hosts` хак (M5):**
   - Проектные docker-compose шаблоны (`templates/template-*/`) обязывают `networks: [proxy-net (external: true)]`
   - Nginx overlay использует Docker DNS resolver: `resolver 127.0.0.11 valid=30s; set $backend "<project>:<port>"; proxy_pass http://$backend;`
   - Gate test: каждый проектный compose объявляет proxy-net как external
3. Идемпотентность: `mkdir -p`, `cp -n` (не перезаписывать существующие).

**Связанные фиксы (не в этом брифе, но необходимые):**
- Обновить TRAP[DECISION] в nginx overlay (старый TRAP описывает system nginx, а nginx давно в Docker)
- Почистить мёртвый `conf.d/tronyx.ru.conf`, deprecated `listen ... http2`

**Эффект:** First-deploy проекта: **1 push → CI зелёный** (без fix-циклов). Экономия 20-40 мин на сессию.

**Файлы:** `node-lifecycle.sh` (step 6b), `templates/template-*/` (proxy-net в compose), nginx overlay configs (resolver + убрать /etc/hosts).

---

### Волна 3: Predeploy gate extension

**Проблема:** Сейчас predeploy gate (`test_predeploy_gate.py`, 5 тестов, `@pytest.mark.predeploy`) проверяет только платформенные compose-файлы (`core/modules/*/`). Проектные compose-файлы (из `/opt/projects/<name>/`) не валидируются до отправки на VPS. CI падает на VPS через 2-3 минуты, вместо fail-fast за 10s.

**Текущий predeploy gate (5 тестов):**
| Тест | Что проверяет |
|------|---------------|
| `test_required_env_vars_present` | ${VAR} без default есть в os.environ или .env |
| `test_docker_image_tags_pinned` | Нет :latest тегов (с exceptions) |
| `test_all_compose_configs_valid` | yaml.safe_load() возвращает валидный dict |
| `test_docker_networks_precreated` | external:true сети существуют (docker network ls) |
| `test_no_hardcoded_credentials` | Нет password=/secret=/token= литералов |

**Расширение (4 новых теста):**
1. **`test_project_compose_configs_valid`**: Для каждого проекта из `node.yaml#projects` (или из `PROJECTS_DIR` env) выполнять `docker compose -f <dir>/docker-compose.yml config --dry-run` (требует Docker, skip если недоступен). Валидирует: compose syntax, env var resolution, extends/include валидность.
2. **`test_project_ports_no_conflict`**: Статический анализ: порты проекта (из `ports:` секции compose) не конфликтуют с платформенными портами (из `platform-env.yaml profiles`). Fail-fast на конфликтах.
3. **`test_project_external_networks_exist`**: Все `networks: <name> (external: true)` из проектного compose задекларированы в `platform-env.yaml networks[]`. Fail если проект ссылается на несуществующую сеть.
4. **`test_ai_platform_yaml_schema`**: YAML schema валидация `ai-platform.yaml` (name: required, domain: required, target_node: required). Fail на отсутствующих required полях.

**Интеграция с CI:** `deploy-project.yml` → шаг «Validate payload» перед deliver → `make gate MODE=fast PROJECT=<name>`.

**Эффект:** Ещё 1-2 fix-цикла устранены = **10-15 мин экономии**. CI падает за 10-30s вместо 2-3 мин.

**Файлы:** `tests/test_predeploy_gate.py`, `tests/_conftest/` (новые fixtures: project_compose_files), `.github/workflows/deploy-project.yml` (новый validate шаг).

---

### Волна 4: Hermes-agent L2 pre-built с fallback

**Проблема:** hermes-agent L2 собирается контекстно на VPS (~60s на сборку). При частых пересозданиях сервера — каждая сборка с нуля. Pre-built образ может быть недоступен (не собран в CI, новый контекст).

**Решение:**
1. `deploy-modules.sh` → `deploy_docker_module("hermes-agent")`:
   - Сначала: `docker pull ghcr.io/<context>/hermes-agent-context:latest`
   - Если pull успешен → `docker tag ... hermes-agent:latest` → compose up (no build)
   - Если pull 404/ошибка → `docker compose build` (L1→L2 локально) → compose up
2. Логика сборки остаётся в `hermes-agent/Makefile` и `docker-compose.base.yml` — **без дрейфа**: единственный Dockerfile, единственный build context. Разница только в том, ГДЕ происходит сборка (CI или VPS).
3. `make hermes-build-context CONTEXT=<org>` — CI собирает и пушит `ghcr.io/<org>/hermes-agent-context:latest`.

**Эффект:** hermes-agent deploy: 60s → **5-10s** (pull вместо build). Незначительно для общего времени, но устраняет лишнюю точку отказа.

**Файлы:** `deploy-modules.sh` (pull-or-build логика), CI workflow (уже есть `build-platform.yml`).

---

## 3. Приоритеты и оценки

| Приоритет | Волна | Эффект (экономия) | Усилие | Зависимости |
|:---------:|-------|:-----------------:|:------:|-------------|
| **P0** | Волна 2 (scaffold) | **20-40 мин** с сессии | Среднее | Нет |
| **P0** | Волна 1 (SSL cache) | **~4 мин** с bootstrap | Среднее | S3 credentials в secrets |
| **P1** | Волна 3 (predeploy gate) | **10-15 мин** с сессии | Низкое | Волна 2 (нужны проектные compose для тестов) |
| **P2** | Волна 4 (L2 fallback) | **~1 мин** | Низкое | CI build-platform workflow |

**Суммарный ожидаемый эффект:** полный деплой-цикл **~2 часа → ~15-20 мин**.

---

## 4. Definition of Done

1. `make bootstrap-node NODE=tronyx-vps` на голом сервере:
   - SSL сертификаты восстановлены из S3 (если валидны) или выпущены и сохранены в S3
   - `/opt/projects/<name>/` созданы для всех проектов из node.yaml
   - `docker network inspect proxy-net` показывает контейнеры проектов подключены
   - `/etc/hosts` НЕ содержит захардкоженных IP
2. `make deploy PROJECT=<name>` для каждого проекта:
   - CI зелёный с первой попытки (без ручных мутаций VPS)
   - Predeploy gate падает ДО отправки на VPS при невалидном compose/yaml
3. `make gate MODE=fast` — зелёный (включая новые predeploy тесты)
4. hermes-agent: pull pre-built при наличии, build локально при отсутствии — без дрейфа
5. Полный цикл (bootstrap + deploy 2 проектов) ≤ 30 мин

## 5. Не входит в этот бриф

- Registry mirror / warm-images (Option A) — отклонено оператором
- Async SSL provisioning (Option C original) — отклонено, заменено на S3 cache
- CI incremental gates (Option E) — отложено
- Parallel bootstrap steps (Option F) — отложено
- Исправление org-level secrets (B3, GitHub Free ограничение) — отложено, требует диагностики GitHub
- Исправление ERR trap в deploy-project.sh (B1) — исправлено в DevPlan 019, верифицировано в 020

$END_BRIEF
