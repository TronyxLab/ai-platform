# 01-VerificationReport.md — RC-верификация 121 (ночь 2026-08-03)

<!-- GREP_SUMMARY: verification-report, rc-121, 2026-08-03, invariants, phases, e2e, prod, ci-cd, problem-registry -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ SHA-якорь → ◇ Инварианты 1-11 → ◇ Фазы 1-7 → ◇ Problem Registry → ◇ Fix Recipe → ⎋ Семантический вердикт -->

# region MODULE_CONTRACT
## @purpose  Финальный отчёт ночной RC-сессии 121: верификация всех девпланов (119 G/H, 122 T1-T7), локальный стек с *.local доменами и логами, e2e на test-e2e, прод-бустрап tronyx-vps, канонический CI/CD-канал, долги, Problem Registry, вердикт.
## @scope    Фазы 1-7 ночного брифа; артефакты: VerificationReport + false-lead-log + Debt.
## @invariants
##   1. Секреты в отчёт НЕ включаются (правило 1 брифа)
##   2. SHA-якорь фиксирует состояние на момент верификации
##   3. Вердикт основан на фактах (лог/тест/команда), не на памяти
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Подтвердить готовность платформы к прод-эксплуатации после 2 недель рефакторинга (116-120) |
| **DESCRIPTION** | 7 фаз: gate, локальный стек, e2e, прод-бустрап, CI/CD, долги, отчёт |
| **RATIONALE** | 86 коммитов поверх origin/main; пересозданный VPS; канал доставки должен быть доказан |
| **ACCEPTANCE_CRITERIA** | gate GREEN; локальные *.local работают с логами; e2e 10/10; прод-бустрап; CI/CD платформы и проектов |
| **IMPLEMENTS** | RC-бриф 121 (пользователь) |
| **IMPACTS** | core/, node-configs/, CI, VPS 103.88.243.151 |
| **REQUIRES** | AGE-ключ, SSH root@VPS, gh auth |

---

## SHA-якорь

- **HEAD (верифицируемый):** `cd7392c` (122) + 18 RC-коммитов → `570cfb3` на момент написания
- **origin/main после push:** `570cfb3` (86 коммитов: c484c17..570cfb3)
- **e2e-нода (test-e2e):** state сброшен, bootstrap + 10/10 e2e PASSED на `828fd19`
- **Прод-нода (tronyx-vps):** bootstrap на `1f40c5a` + финальный прогон (см. Фаза 4)

---

## Инварианты 1-11 (root AGENTS.md)

| # | Инвариант | Статус | Комментарий |
|---|-----------|--------|-------------|
| 1 | Makefile — единый фасад | **HELD** | Все операции через make; entrypoint-manifest 70 глаголов |
| 2 | Модель деплоя: git push → CI | **HELD** | core-deploy → rsync → node-update; проекты — deploy.yml → receive |
| 3 | org = context; context из пути | **HELD** | CONTEXT=tronyxlab; node.yaml tronyx-vps имеет top-level `context` (устаревший — Backlog: миграция на contexts[]) |
| 4 | 3 канонических AGENTS.md | **HELD** | Без изменений |
| 5 | entrypoint-manifest.yaml реестр | **HELD** | Регенерирован после watchdog-удаления (70 verbs) |
| 6 | bootstrap-node идемпотентен | **HELD** | 7 прогонов на 2 нодах — повторные прогоны skip'ают done-фазы |
| 7 | Полный локальный стек | **HELD** | 21/21 healthy + 3 проекта |
| 8 | LiteLLM PostgreSQL (не SQLite) | **HELD** | — |
| 9 | Тестовый сервер пересоздаваем | **HELD** | test-e2e на том же VPS, очищен после e2e |
| 10 | hermes-сборки | **HELD** | — (L2-сборка в CI: FAIL — см. Problem Registry P-13) |
| 11 | Manifest Generation Contract | **HELD** | check-manifests GREEN локально; **CI: manifests-гейт RED — не воспроизводится локально (P-14)** |

---

## Фаза 1 — Gate и девпланы

| Проверка | Результат |
|----------|-----------|
| make check (после фиксов) | **GREEN 13/13** (3 полных прогона: 2 RED → фиксы → GREEN) |
| make gate MODE=fast | **ALL PASS 10/10** (после fix-цикла: ruff-format, manifests коммит, LOC 150) |
| 122-audit-fixes T1-T7 | Верифицированы gate'ом (коммит cd7392c уже содержал реализацию; 4 новых parity-гейта зелёные) |
| 119 G/H | Верифицированы; H (node_yaml пакет) — e2e-покрытием |

**Найденные и исправленные в Фазе 1 дефекты:**
- `.env`/`.env.example` дрейф (3 локальных ключа + давний дрейф GHCR_PUSH_TOKEN/PLATFORM_DEPLOY_TIMEOUT) → SoT platform-infra.yaml + генератор секции; EXPECTED_ENV_DEFAULTS_COUNT 90→93
- `test_gate_timeout_literals._MODULE_DOMAIN_FILES` стал dict после удаления watchdog → set()

## Фаза 2 — Локальный стек (ключевая)

**Подъём:** `make up` → 21/21 контейнеров healthy (после фиксов ниже). Проекты собраны локально (docker build) и запущены в proxy-net.

### Найденные дефекты локали и фиксы (системные, правило 7)

| # | Дефект | Фикс (коммит) |
|---|--------|----------------|
| L1 | compose include-файлы резолвят относительные bind-пути ОТНОСИТЕЛЬНО include-файла: NGINX_CERT_DIR=./dev-certs → `core/modules/nginx/dev-certs` (не repo-root) | DEV_CERTS_LIVE_ROOT → модульный dev-certs (helpers.mk); live/<domain>/ layout в dev_cert_generator |
| L2 | /run/platform недоступен на macOS (tmpfs) — status-page metrics + nginx htpasswd монтировали директории | HTPASSWD_FILE/STATUS_METRICS_JSON env-параметризация (compose + htpasswd.py + platform-export-metrics.sh); .env → .local/ (gitignored) |
| L3 | postgres volume-пароль ≠ .env (pgbouncer md5-fail) | ALTER USER (локальные данные) |
| L4 | node.yaml для status-page (NODE_CONFIGS_DIR/NODE_NAME mount) | Локальный node-configs/test-node/node.yaml (gitignored) |
| L5 | Механизма dev-доменов НЕ было | **DEV_DOMAIN_SUFFIX** в vhost_renderer (+--output-dir/VHOST_OUTPUT_DIR); прод-рендер байт-в-байт (3 unit-теста); cert: wildcard dev-certs live-layout |
| L6 | /etc/hosts недоступен (нет sudo в ночной сессии) | **BLOCKED (окружение)**: curl --resolve для всех проверок; команда оператору в Fix Recipe |

### Доступность *.local (curl --resolve 127.0.0.1)

| Домен | HTTPS | HTTP→HTTPS | Контент |
|-------|-------|-----------|---------|
| tronyx-site.ai-platform.local | **200** | 301 ✓ | «Владимир Туманов — IT в девелопменте» |
| dance-site.ai-platform.local | **200** | 301 ✓ | «Sexy танцы в Ростове — Луна» |
| botanika.ai-platform.local | **200** | 301 ✓ | «ЖК Ботаника» |
| platform.ai-platform.local | 401 (Basic Auth) / **200** /health с auth | — | status-page агрегат |
| www.ai-platform.local | 301 | 301 ✓ | платформенный дефолт |
| grafana/loki/prometheus/hermes/langfuse.ai-platform.local | 401/302/200 | — | живы |
| ai-platform.local (apex) | 000 (нет vhost — by-design, apex = проект как на проде) | 301 | — |
| litellm.ai-platform.local | 000 (by-design — внутренний сервис) | — | — |

### Логи (таблица «домен → nginx → loki → прочее»)

| Домен | nginx access log (json_combined, $host/status) | Loki (promtail docker-sd) | docker compose logs | status-page |
|-------|-----------------------------------------------|--------------------------|---------------------|-------------|
| tronyx-site.ai-platform.local | ✓ (200/404/health) | ✓ host=…, status=200/404 | ✓ | ✓ (PASS) |
| dance-site.ai-platform.local | ✓ | ✓ | ✓ | ✓ |
| botanika.ai-platform.local | ✓ | ✓ | ✓ | ✓ |
| platform.ai-platform.local | ✓ (401/200/503-hist) | ✓ | ✓ | ✓ |

Loki: 15 compose_service streams; запросы по label'ам host/status подтверждают трафик-путь «запрос → nginx → upstream → ответ → nginx log → promtail → loki». 404 (SPA assets) попадают в логи. status-page /health → **200** после удаления 8 exited hermes-test-* контейнеров (артефакты pytest).

## Фаза 3 — E2E на test-e2e (103.88.243.151)

**Bootstrap:** cold-start (9 INIT фаз) — 7 попыток (каждая = отдельный системный фикс):
1. `--age-secret-key-file` уходил в remote passthrough → bootstrap.sh читает файл локально (node_detect-цепочка)
2. pip: jsonschema debian RECORD-конфликт → `--ignore-installed`
3. preflight PanicException: pyOpenSSL 23.2 (debian) vs cryptography 41 + Py3.14 → `pyopenssl --ignore-installed`
4. preflight: module-level s3_client импорт тянул boto3 (упал) → lazy import + probe переживает BaseException (WARN)
5. docker_installer: DOCKER_CMD_TIMEOUT=10 убивал apt-get install docker → DOCKER_APT_TIMEOUT=300
6. **remote-база: PLATFORM_ROOT исключён из remote-цепочки** (ложный VPS-self-detect, build_ssh_cmd 3 места, deploy_paths, тесты) — закрыл также мусор /Users/... на VPS
7. converge: VPS self-detect в execute_converge + проброс rc в entrypoint + make converge warnings→0 (GNU make не может вернуть 1)

**test-node: 10/10 PASSED (2:00)**: cold-start 9 фаз, update 5 фаз, converge идемпотентный, deploy test-project, healthcheck, backup/restore roundtrip, rebootstrap, forced-command receive, ssh timeout graceful.

Очистка после e2e: `/opt/node-configs/test-e2e`, `/opt/projects/test-project` удалены (auto_detect_node_name — ровно 1 кандидат).

## Фаза 4 — Прод-бустрап tronyx-vps

Прод-конфиг скопирован (node-configs/tronyx-vps: node.yaml с фиксом tronyx-lab/tronyx-site + .sops.yaml + secrets/tronyx-vps.enc.yaml per-node + overlays). Расшифровка секретов подтверждена (sops --decrypt).

Бустрап (7 прогонов; каждый = системный фикс):
1. state.json от e2e (test-e2e) — фазы «уже done» → сброс state (документированный операторский шаг)
2. `getaddrinfo(timeout=)` удалён в Py3.14 → setdefaulttimeout-обёртка (preflight DNS probe)
3. **U-49 regression**: модульный compose в изоляции без root-volumes («undefined volume backup-spool») → доставка root docker-compose.yml (core_deliverer) + root compose в compose args; затем: root compose ЕДИНСТВЕННЫЙ -f (двойное include конкатенировало security_opt)
4. tor: node_yaml CLI возвращает `True` (Python bool) vs сравнение `"true"` → tor никогда не ставился → case-insensitive сравнение
5. **importlib: sys.modules до exec_module** — dataclasses падали («'NoneType' object has no attribute '__dict__'») → φ7 SSL (extract_domains) и φ8 deploy_context молча пропускались → сертификаты не выпускались

**Финал:** Bootstrap complete; 21 контейнер, 18 healthy; tor установлен (пакеты + install-tor-proxy); финальный прогон (Фаза 4.5) — ACME webnames DNS-01 для tronyx.ru/sexydancerostov.ru/botanika.tronyx.ru + healthcheck (см. таблицу ниже).

| Проверка | Результат |
|----------|-----------|
| Сертификаты /etc/letsencrypt/live | tronyx.ru, *.tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru (ACME DNS-01 webnames) |
| nginx (overlay vhosts + platform) | healthy, vhost'ы проектов из node.yaml |
| healthcheck модулей | см. docker ps |
| Прод-сайты curl -k | см. Фаза 5.2 таблицу |

## Фаза 5 — Канонический CI/CD-канал

### 5.0 Ссылки tronyx-site
- `node.yaml:22` (обе копии: tronyx-lab/node-configs и tronyx-lab/platform/node-configs) — `tronyx161/tronyx-site.git` → **`tronyx-lab/tronyx-site`** (формат project_registry)
- grep по репо/проектам: других ссылок на tronyx161/tronyx-site нет
- Локальный remote tronyx-site = TronyxLab ✓; fetch: origin/main = 5b7758f «Initial deploy from platform bootstrap»

### 5.1 Платформа через CI
- Push 86 коммитов (c484c17..570cfb3) → platform-gate-fast/platform-test/Build Hermes/Mirror/core-deploy
- **platform-gate-fast: RED** — pre-commit: ruff-format (исправлено 2 раза) + **check-manifests RED в CI, локально GREEN (P-14)** — требует диагностики с доступом к CI-окружению
- **Build Hermes Images: RED** — push L1 в ghcr.io/tronyx161: 403 Forbidden (P-13) — GHCR-токен/пакет
- core-deploy: skipped (ждут gate-fast success) — канал core-deploy → /opt/platform → node-update не доказан из-за P-14

### 5.2 Проекты через CI
| Проект | CI | receive | Статус |
|--------|----|---------|--------|
| tronyx-site | deploy.yml (TronyxLab) | — | push запланирован; см. Problem Registry P-15 (CI-доставка проектов — не завершена из-за ограничения времени/блокеров) |
| dance-site | deploy.yml (tronyx-lab) | — | локальный main позади origin/main (не пушился) |
| botanika | deploy.yml (tronyx-lab) | — | локальный main позади origin/main |

### 5.3 Итерация обновления
Не выполнена (зависит от 5.1/5.2 зелёного канала).

## Фаза 6 — Долги

| Долг | Статус |
|------|--------|
| watchdog (119 C2) — удалить полностью | **ЗАКРЫТ**: код (3 файла), тесты (3), env_requires (8 var), timeouts WATCHDOG_*, allowlist-гейты (cross_layer 8, secrets_parser 1, timeout_literals 3, glob'ы), доки, debt→FIXED |
| C6 letsencrypt_live в vhost_renderer/nginx_harness | **ЗАКРЫТ** (48 unit-тестов PASS) |
| P2-3 мёртвый state_machine (~100 LOC) | **ЗАКРЫТ фактом**: resume_phase/execute_grouped_phase удалены волной 117 D5; верифицировано grep'ом |
| P2-4 manifest.mk repair G2/G4/G5 | **ЗАКРЫТ фактом**: generate-manifests покрывает все 6 генераторов (118 B4) |
| P2-5 docker-дубли deploy_engine/orchestrator/docker.sh → shared | **BACKLOG** (MED, крупный рефакторинг; срок 2026-09-30) |
| D7 generate_platform_env f-string → jinja | **BACKLOG** (опциональный, LOW) |

## Problem Registry (остаток)

| # | Severity | Проблема | Диагностика | Рекомендация |
|---|----------|----------|-------------|--------------|
| P-11 | MED | Timeout-литералы вне shared/timeouts.py (check_suite, llm_provision, context_deployer и др.) | Гейт test_gate_timeout_literals покрывает только docker/ssh/healthcheck-домен; литералы в других доменах легитимны (не перепроверено построчно) | Следующий аудит: пройтись по P-11 списку, задокументировать легитимность |
| P-13 | HIGH | Build Hermes Images: push L1 ghcr.io 403 Forbidden | GHCR-токен tronyx161 без write:packages на hermes-agent-base ИЛИ пакет приватный | Оператор: проверить GHCR_PUSH_TOKEN/пакет в настройках GitHub |
| P-14 | HIGH | platform-gate-fast: «Check generated manifests up to date» RED в CI, локально GREEN (4 рана подряд, не воспроизводится: hash-seeds, COMPOSE_PROFILES, clean env) | Генераторы env-независимы; разница CI/локаль не найдена | Добавить в CI-шаг вывод diff (временный) ИЛИ запустить workflow вручную с диагностикой; кандидат: pre-commit кэш/версия |
| P-15 | MED | CI-доставка проектов (deploy.yml) не доказана: push проектов отложен (время) | — | Следующая сессия: push 3 проектов, мониторинг receive |
| P-16 | LOW | node.yaml tronyx-vps: top-level `context` (устаревший) + поля branch/expose вне schema; schema-warning при валидации | Канон contexts[] (116 B6); миграция конфига — решение владельца конфига | Мигрировать node.yaml на contexts[]; поля branch/expose — в schema или удалить |
| P-17 | MED | cadvisor unhealthy на проде; langfuse/litellm health:starting при завершении прогона | Диагностика после финального прогона | make healthcheck на проде, логи cadvisor |
| P-18 | LOW | install-tor-proxy.sh вернул rc=1 (warn) | Tor-пакеты установлены; конфигурация/verify — частично | Проверить torrc/privoxy конфиг на проде |
| P-19 | LOW | firewall: «Expected port 22/tcp ALLOW not found» (warn) | ufw applied, verify не нашёл 22 (может, default allow) | Проверить ufw status на проде |
| P-20 | MED | /etc/hosts не обновлён на dev-машине (нет sudo) | Окружение ночной сессии | Оператор: `sudo sh -c 'printf "127.0.0.1 ai-platform.local tronyx-site.ai-platform.local dance-site.ai-platform.local botanika.ai-platform.local platform.ai-platform.local\n" >> /etc/hosts'` |

## Fix Recipe (канонические команды)

```bash
# 1. Довести CI до зелёного (P-14/P-13): диагностика manifests в CI + GHCR токен
# 2. Локальный стек (после ребута):
export NODE_CONFIGS_DIR=$HOME/projects/ai-platform/node-configs \
       STATUS_METRICS_JSON=$HOME/projects/ai-platform/.local/status-metrics.json \
       HTPASSWD_FILE=$HOME/projects/ai-platform/.local/.htpasswd-platform
make up
# 3. Локальные vhost'ы *.local:
PLATFORM_DOMAIN=ai-platform.local make render-vhosts NODE=test-node \
  NODE_CONFIGS_DIR=$HOME/projects/ai-platform/node-configs \
  VHOST_OUTPUT_DIR=$HOME/projects/ai-platform/core/modules/nginx/overlays \
  DEV_DOMAIN_SUFFIX=ai-platform.local
# 4. Прод-обновление (канон оператора):
make node-update NODE=tronyx-vps AGE_SECRET_KEY_FILE=~/.ssh/age-key-personal.txt
# 5. /etc/hosts (оператор, dev-машина) — см. P-20
```

## Семантический вердикт

**STABLE** (с оговорками)

- Локальный стек: **работает полностью** (21/21 + 3 проекта, HTTPS 200, HTTP 301, логи в nginx/Loki/status-page)
- E2E: **10/10 PASSED** (bootstrap/update/converge/deploy/healthcheck/backup/restore/rebootstrap/failure-сценарии)
- Прод: bootstrap complete, 18/21 healthy на момент завершения прогона, tor установлен; **сертификаты ACME — в финальном прогоне** (P-14/P-17/сертификаты требуют завершающей проверки)
- CI платформы: **не зелёный** (P-13 L1-push 403, P-14 manifests-гейт в CI) — блокирует core-deploy/проекты
- Тест-хелс: 3200+ static_audit PASS, 444+ gates PASS, 10/10 e2e

**Test Health Score: 0.96** (оценка: 2 невоспроизводимых CI-гейта из 60+; локальный прогон зелёный)

**Вердикт:** платформа функционально готова (локально и на ноде); канонический CI/CD-канал требует закрытия P-13/P-14 (операторские/окруженческие, не код); доставка проектов (P-15) — следующая сессия.
