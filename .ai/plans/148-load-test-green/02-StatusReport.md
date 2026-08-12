$START_STATUS_REPORT
# 02-StatusReport — 148: Load-test green — Wave 2 (Sysadmin)

$ARTIFACT_CONTRACT
PURPOSE:               Инфраструктурная подготовка тестовой VPS 103.88.243.151 (tronyx-vps) к прогонам нагрузочных тестов: LANGFUSE-ключи (S1), mock-litellm контейнер (S2), проверка Prometheus SSH-туннеля (S3).
DESCRIPTION:           Выполнен Wave 2 DevPlan 148 (§4): (S1) генерация `pk-lf_*`/`sk-lf_*` ключей langfuse, обновление secrets.enc.yaml + secrets.env, очистка БД langfuse, перезапуск контейнера, верификация API; (S2) создание изолированного docker-контейнера `lt-mock-litellm` на порту 127.0.0.1:14000 с mock-echo сервером; (S3) проверка SSH-туннеля Prometheus 19090→9090.
RATIONALE:             Без S1 langfuse_ingest-сценарий падает с 403; без S2 llm/llm_stream-сценарии невыполнимы (нет mock-модели); без S3 saturation-секция не соберёт Prometheus-метрики.
ACCEPTANCE_CRITERIA:   S1: curl https://langfuse.tronyx.ru/api/public/traces → НЕ 403; S2: curl -X POST http://127.0.0.1:14000/chat/completions → 200; S3: Prometheus отвечает через туннель.
IMPLEMENTS:            DevPlan 148 §4 Wave 2 (S1, S2, S3)
IMPACTS:               /opt/node-configs/secrets/tronyx-vps.enc.yaml (VPS), /var/lib/platform/run/secrets.env (VPS), контейнер lt-mock-litellm (VPS), БД langfuse (PostgreSQL, VPS)
REQUIRES:              SSH-доступ к 103.88.243.151; AGE-ключ на dev-машине (~/.config/sops/age/keys.txt); SOPS на dev-машине
$END_ARTIFACT_CONTRACT

---

## 1. Diagnostic Summary

| Параметр | Значение |
|----------|----------|
| **Target host** | 103.88.243.151 (tronyx-vps) |
| **OS** | Ubuntu 24.04.4 LTS, Linux 6.8.0-137-generic, x86_64 |
| **User** | root |
| **SOPS** | v3.9.4 (на ноде; без AGE-ключа — расшифровка только с dev-машины) |
| **Docker** | Все 29 контейнеров healthy, включая langfuse и litellm |
| **Prometheus** | :9090 (127.0.0.1), healthy (HTTP 200 на /-/healthy) |
| **AGE public key** | age1n3gnefwr6ln87rpquc6wwe6duhmvcrlevefhns8yt0gfc8a3ls2s7qhe98 |

### Issues identified (pre-mutation)

| # | Severity | Service | Issue |
|---|----------|---------|-------|
| 1 | **CRITICAL** | langfuse | LANGFUSE_PUBLIC_KEY/SECRET_KEY — placeholder values (`pk-lf-placeholder-update-after-first-deploy`) → 403 на /api/public/traces |
| 2 | **CRITICAL** | load-test (llm) | Нет mock-модели для llm/llm_stream сценариев на ноде |
| 3 | LOW | Prometheus | Туннель требует проверки перед прогонами saturation |

---

## 2. Actions Taken

### S1 — LANGFUSE-ключи

**Preflight:**
- Проверен docker inspect langfuse: `LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-placeholder-...`, `LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-placeholder-...`
- Проверен langfuse API: `curl -H 'Authorization: Bearer pk-lf-placeholder-...' https://langfuse.tronyx.ru/api/public/traces` → HTTP 403

**Mutations applied:**

1. **Генерация ключей** на ноде: `pk-lf_$(openssl rand -hex 16)`, `sk-lf_$(openssl rand -hex 16)`
2. **Расшифровка** secrets.enc.yaml с dev-машины через `sops -d` (AGE-ключ `~/.config/sops/age/keys.txt`)
3. **Обновление** `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY` в расшифрованном YAML
4. **Перешифровка** через `sops -e --age age1n3...` и загрузка на VPS (`/opt/node-configs/secrets/tronyx-vps.enc.yaml`)
5. **Генерация** secrets.env (shell-формат, 54 переменных) и загрузка в `/var/lib/platform/run/secrets.env`
6. **Очистка БД langfuse**: terminate connections → `DROP DATABASE langfuse` → `CREATE DATABASE langfuse OWNER platform`
7. **Force-recreate** langfuse + langfuse-redis через `docker compose -f /opt/platform/docker-compose.yml --profile langfuse up -d --force-recreate` с `NGINX_OVERLAY_DIR=/opt/node-configs/tronyx-vps/overlays/nginx`

**Snapshot diff:** `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY` в secrets.enc.yaml: `pk-lf-placeholder-...` → `pk-lf_<hex32>`, `sk-lf-placeholder-...` → `sk-lf_<hex32>`. Остальные 52 переменных без изменений.

**Verification:**
- `curl -u 'pk-lf_...:sk-lf_...' https://langfuse.tronyx.ru/api/public/traces` → **HTTP 200**, `{"data":[],"meta":{"page":1,"limit":50,"totalItems":0,"totalPages":0}}` ✅
- `curl -X POST -H 'Authorization: Bearer pk-lf_...' -d '{"batch":[...]}' https://langfuse.tronyx.ru/api/public/ingestion` → **HTTP 207** (валидный ответ, ключ принят) ✅
- Docker: `langfuse Up ... (healthy)`, `langfuse-redis Up ... (healthy)` ✅
- БД: `organizations` — 1 row (`org_ai-platform`), `projects` — 1 row (`proj_default`), `api_keys` — 1 row с новым public_key ✅

### S2 — mock-litellm контейнер

**Preflight:**
- Проверено: прод-litelmm (порт 4000) — healthy, используется DeepSeek-модели
- OpenAI API geo-blocked с данной VPS → `openai/echo` через litellm-образ недоступен

**Mutations applied:**

1. **Создание mock-echo сервера** (`/tmp/mock_echo_server.py`): Python http.server, OpenAI-совместимый формат `/chat/completions`, возвращает `"Echo: {user_content}"`, детерминированная латентность ~1ms
2. **Запуск контейнера**: `docker run -d --name lt-mock-litellm --network shared-db-net -p 127.0.0.1:14000:4000 -v /tmp/mock_echo_server.py:/app/server.py:ro python:3.12-slim python3 /app/server.py 4000`

**⚠️ TRAP[DECISION] · 2026-08-12 · — · Python mock-echo вместо litellm-образа для load-test mock**
**· Rejected: `ghcr.io/berriai/litellm:v1.91.2` с `openai/echo` (как в DevPlan 148 §4 S2)**
**· Reason: `openai/echo` — реальный OpenAI API endpoint, geo-blocked с VPS (РФ). Ошибка: «Country, region, or territory not supported». Litellm-образ без прокси не может достичь api.openai.com.**
**· Rev: при появлении OpenAI-доступа (прокси/VPN на ноде) — заменить на litellm-образ с `openai/echo`**

**Verification:**
- `curl -X POST http://127.0.0.1:14000/chat/completions -H 'Content-Type: application/json' -d '{"model":"echo","messages":[{"role":"user","content":"hi"}]}'` → **HTTP 200**, `{"choices":[{"message":{"content":"Echo: hi"}}]}` ✅
- `curl -X POST http://127.0.0.1:14000/chat/completions -H 'Authorization: Bearer <master_key>' ...` → **HTTP 200** ✅
- Контейнер: `lt-mock-litellm Up`, порт `127.0.0.1:14000->4000/tcp` ✅
- Прод-litellm (4000) не затронут ✅

### S3 — Prometheus SSH-туннель

**Preflight:**
- VPS: `curl http://localhost:9090/-/healthy` → HTTP 200 ✅
- VPS: Prometheus слушает на `127.0.0.1:9090` (docker-proxy) ✅

**Verification:**
- Dev-машина: `ssh -N -L 19090:localhost:9090 root@103.88.243.151` (процесс PID 86144, persistent) ✅
- Dev-машина: `curl http://localhost:19090/-/healthy` → HTTP 200 ✅
- Dev-машина: `curl 'http://localhost:19090/api/v1/query?query=up'` → `"status":"success"`, 8 targets ✅
- Валидность для прогонов: `LOAD_PROMETHEUS_HOST=localhost LOAD_PROMETHEUS_PORT=19090` ✅

---

## 3. Audit Trail

| Timestamp (UTC+3) | Action | Rationale | Result |
|-------------------|--------|-----------|--------|
| 01:45 | SSH fingerprint VPS | Preflight: verify connectivity, OS, Docker state | OK — Ubuntu 24.04, 29 containers healthy |
| 01:48 | Read langfuse module.yaml, docker-compose.base.yml | Understand secrets flow and LANGFUSE_INIT vars | Flow: secrets.enc.yaml → sops decrypt → secrets.env → docker compose env |
| 01:50 | Read secrets.enc.yaml (decrypted via SOPS on dev-machine) | Check current LANGFUSE keys | Placeholder values confirmed |
| 01:52 | Generate pk-lf_*/sk-lf_* keys | S1 step 1 | Keys: hex32 format |
| 01:53 | Update secrets.enc.yaml, re-encrypt, upload to VPS | S1 step 2-5 | Encrypted file at /opt/node-configs/secrets/tronyx-vps.enc.yaml |
| 01:54 | Generate secrets.env (54 vars), upload to /var/lib/platform/run/secrets.env | S1 step 6 | Env file with new LANGFUSE keys |
| 01:55 | Drop and recreate langfuse DB (terminate sessions + DROP + CREATE) | S1 step 7: force headless init | DB empty (0 relations) |
| 01:56 | Docker compose --profile langfuse up -d --force-recreate | S1 step 8: restart with new env | langfuse + langfuse-redis recreated, healthy |
| 01:58 | Verify langfuse API with new keys | S1 verification | HTTP 200 on /api/public/traces (Basic Auth), HTTP 207 on ingestion |
| 02:00 | Prepare mock-litellm config, pull ghcr.io/berriai/litellm:v1.91.2 | S2 prep | Image downloaded, mock config created |
| 02:02 | Attempt litellm image with openai/echo | S2: original approach | Failed — OpenAI geo-blocked (HTTP 403: "Country not supported") |
| 02:04 | Create Python mock-echo server, docker run python:3.12-slim | S2: fallback approach | Container lt-mock-litellm started, port 14000 |
| 02:04 | Verify mock endpoint | S2 verification | HTTP 200, valid chat completion response |
| 02:05 | Check Prometheus SSH tunnel on dev machine | S3 verification | Tunnel active (PID 86144), port 19090, HTTP 200, 8 targets |

---

## 4. Legalization Tasks

| # | What | Where | Deadline | Status |
|---|------|-------|----------|--------|
| L1 | Python mock-echo сервер (`/tmp/mock_echo_server.py`) вместо litellm-образа — TRAP[DECISION] | Контейнер lt-mock-litellm на VPS | — | DOCUMENTED (см. TRAP выше). Замена на litellm-образ при появлении OpenAI-доступа. |

---

## 5. Overall Verdict

**VERDICT: SUCCESS**

Все три задачи Wave 2 выполнены:

| Задача | Статус | Подтверждение |
|--------|--------|---------------|
| **S1** LANGFUSE-ключи | ✅ PASS | HTTP 200 на /api/public/traces, ingestion работает |
| **S2** mock-litellm | ✅ PASS (с TRAP[DECISION]) | HTTP 200 на :14000/chat/completions, echo-ответы |
| **S3** Prometheus-туннель | ✅ PASS | HTTP 200 на localhost:19090, 8 targets через PromQL |

**Состояние ноды:** 31 контейнер (29 production + langfuse-redis + lt-mock-litellm), все healthy.

---

## 6. Next-Step Suggestions

### Wave 3 (QA/оператор) — прогоны нагрузочных тестов:

**Pre-flight (dev-машина):**
```bash
pytest tests/unit/test_loadtest_pgwire.py tests/unit/test_loadtest_report.py \
  tests/unit/test_loadtest_config.py tests/unit/test_loadtest_runner_remote.py -q
make check
```

**Прогоны (3 волны × 3 режима):**

```bash
# Env-общие:
# LOAD_PROMETHEUS_HOST=localhost LOAD_PROMETHEUS_PORT=19090

# llm/llm_stream (mock-litellm на 14000):
LOAD_ENDPOINT_LLM=http://127.0.0.1:14000 make load-test SCENARIO=llm NODE=tronyx-vps MODE=smoke
LOAD_ENDPOINT_LLM=http://127.0.0.1:14000 make load-test SCENARIO=llm NODE=tronyx-vps MODE=regression
LOAD_ENDPOINT_LLM=http://127.0.0.1:14000 make load-test SCENARIO=llm NODE=test-e2e MODE=capacity

# langfuse_ingest (новые ключи в secrets.env):
make load-test SCENARIO=langfuse_ingest NODE=tronyx-vps MODE=smoke
make load-test SCENARIO=langfuse_ingest NODE=tronyx-vps MODE=regression

# web (уже работает):
make load-test SCENARIO=web NODE=tronyx-vps MODE=smoke
make load-test SCENARIO=web NODE=tronyx-vps MODE=regression
make load-test SCENARIO=web NODE=test-e2e MODE=capacity
```

**Сводная статистика:** см. DevPlan 148 §7, таблица SC_STATS.

$END_STATUS_REPORT
