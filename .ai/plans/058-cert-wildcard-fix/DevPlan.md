# DevPlan 058 — Wildcard Certificate + S3 Cache Fix

$ARTIFACT_CONTRACT
PURPOSE: Исправить выпуск wildcard-сертификатов и процесс бекапа/восстановления через S3 при бутстрапе.
DESCRIPTION: ⚠️ REEVALUATED 2026-07-23: DNS-01 через webnames.ru API РАБОТАЕТ для add/delete TXT-записей. `zone_manager_unavailable` возвращается только для `domains_list` (listing), но управление записями функционально. Wildcard-сертификат `*.tronyx.ru` успешно выпущен через LE staging. Предыдущая неудача — LE rate-limit (expired Jul 23 23:19 UTC). S3 SSL cache имеет 3 бага (G1: age key, G2: chain.pem, G3: account path). HTTP-01 fallback добавлен как safety net для случаев реальной недоступности DNS API.
RATIONALE: DNS-01 работает — wildcard доступен. S3-cache fix + HTTP-01 fallback обеспечивают多层ную защиту: (1) S3 restore при rebootstrap, (2) DNS-01 wildcard при первой установке, (3) HTTP-01 fallback при отказе DNS API. Двойная установка acme.sh (legacy /root/.acme.sh vs managed /opt/acme.sh) требует консолидации.
ACCEPTANCE_CRITERIA:
  AC-1: s3-ssl-cache.sh upload НЕ требует chain.pem (G2 fix)
  AC-2: s3-ssl-cache.sh корректно архивирует/восстанавливает acme.sh account data из <domain>_ecc/ (G3 fix)
  AC-3: s3-ssl-cache.sh download валидирует issuer (LE check) перед восстановлением
  AC-4: issue-cert.sh имеет fallback DNS-01 → HTTP-01 (graceful degradation)
  AC-5: issue-cert.sh логирует причину отказа DNS-01 и переход на HTTP-01
  AC-6: cert_orchestrator.py обрабатывает частичный успех (S3 restored часть доменов, остальные — acme)
  AC-7: При невозможности wildcard — выпускаются individual domain certs (platform.tronyx.ru и все vhost-домены из node.yaml)
  AC-8: S3 upload после успешного HTTP-01 issue тоже работает (unit test)
  AC-9: make gate MODE=fast — зеленый после всех изменений
IMPLEMENTS: StatusReport 057 fixes (G2, G3), HTTP-01 fallback, cert resilience
IMPACTS:
  - core/internal/bootstrap/issue-cert.sh (+HTTP-01 fallback, ~100 LOC)
  - core/internal/bootstrap/s3-ssl-cache.sh (G2, G3 fixes, ~30 LOC)
  - core/internal/bootstrap/cert_orchestrator.py (resilience, ~20 LOC)
  - tests/unit/test_cert_orchestrator.py (новые тесты)
  - tests/test_nginx_acme.py (HTTP-01 тесты)
REQUIRES: webnames.ru API status check (external), S3 credentials availability (env)

---

## Problem Analysis

### DNS-01: ЛОЖНЫЙ ДИАГНОЗ (пересмотрено 2026-07-23)

```
webnames.ru API:
  domains_list         → {"result":"ERROR","details":"zone_manager_unavailable"} ← FALSE ALARM
  add TXT record       → {"result":"OK","details":1}                            ← WORKS
  delete TXT record    → {"result":"OK","details":1}                            ← WORKS
  get_config_acmesh    → returns dns_webnames.sh with API key                   ← WORKS
```

**Реальная причина прошлой неудачи:** Let's Encrypt rate-limit (50 сертификатов на домен в неделю). Лимит истёк 23 июля 2026 23:19 UTC.

**Доказательство:** Wildcard `*.tronyx.ru` успешно выпущен через LE staging 2026-07-23 08:52 MSK:
```
acme.sh --issue --dns dns_webnames --server letsencrypt_test -d "*.tronyx.ru" -d tronyx.ru
→ Cert success. CN=*.tronyx.ru
```

**Текущее состояние на VPS (предыдущая сессия):** HTTP-01 individual certs:
```
/etc/letsencrypt/live/
├── tronyx.ru/          ← LE cert: tronyx.ru + www.tronyx.ru (HTTP-01, legacy acme.sh)
├── platform.tronyx.ru/ ← LE cert: только platform.tronyx.ru (HTTP-01)
├── botanika.tronyx.ru/ ← LE cert: только botanika.tronyx.ru (HTTP-01)
└── sexydancerostov.ru/ ← LE cert: sexydancerostov.ru (HTTP-01)
```

**План:** Заменить HTTP-01 individual certs на DNS-01 wildcard `*.tronyx.ru` покрывающий все поддомены.

### S3 SSL Cache: 3 критических бага (StatusReport 057)

| ID | Severity | Баг | Файл | Строки |
|----|----------|-----|------|--------|
| **G2** | CRITICAL | `_s3_upload()` требует `chain.pem`, но acme.sh `--install-cert` выдаёт только `fullchain.pem` + `privkey.pem`. Upload всегда FAIL. | `s3-ssl-cache.sh` | 72-86 |
| **G3** | HIGH | Account data path: скрипт ищет `${ACME_HOME}/data/${domain}/`, acme.sh хранит в `${ACME_HOME}/${domain}_ecc/`. Upload account data — всегда SKIP. | `s3-ssl-cache.sh` | 107-122 |
| **G1** | CRITICAL | S3 credentials не расшифровываются (age key missing на некоторых нодах). S3 cache = dead code без правильной конфигурации. | infra | — |

### Двойная установка acme.sh

| Установка | Путь | Состояние | Сертификаты |
|-----------|------|-----------|-------------|
| Legacy (ручная) | `/root/.acme.sh/` | Работает, HTTP-01 | Все 4 домена выпущены здесь |
| Managed (код) | `/opt/acme.sh/` | Установлен install-acme.sh | Не использовался для выпуска |

---

## Design Decisions

### D1: DNS-01 primary, HTTP-01 fallback (GUIDED mode)

```
## APPROACH: DNS-01 → HTTP-01 graceful degradation

Primary path: acme.sh --dns dns_webnames (wildcard *.domain)
Fallback: acme.sh --standalone (HTTP-01, individual domain cert)

Rationale:
  - DNS-01 preferred: даёт wildcard, все поддомены покрыты
  - HTTP-01 fallback: работает всегда (нужен только открытый порт 80)
  - Let's Encrypt rate-limit: 50 certs/domain/week — HTTP-01 для ~4 доменов укладывается
  - _issue_project_certs() уже умеет выпускать individual certs для project domains

Also considered:
  A. Только DNS-01 (rejected: broken, certs не выпускаются)
  B. Только HTTP-01 (rejected: нет wildcard, каждый поддомен — новый cert)
  C. Manual DNS-01 через dns_manual (rejected: ручное вмешательство при каждом renew)
  D. Смена DNS-провайдера на Cloudflare (rejected: требует переноса домена, out of scope)

Proceeding with DNS-01 → HTTP-01 fallback unless overridden.
```

### D2: chain.pem — optional, not required

```
## APPROACH: chain.pem optional in S3 upload

acme.sh --install-cert outputs: --fullchain-file + --key-file
No separate chain.pem is generated.

Fix: _s3_upload() — remove chain.pem from required_files[].
     _s3_download() — chain.pem already optional.

Fullchain.pem = cert + chain (concatenated).
Nginx config: ssl_certificate fullchain.pem (includes chain).
Separate chain.pem is NOT needed for nginx operation.

Backward compat: if chain.pem exists on disk, upload it. But don't require it.
```

### D3: Account path fix — <domain>_ecc/

```
## APPROACH: Use acme.sh default directory structure

acme.sh default dir: ${ACME_HOME}/${domain}_ecc/  (e.g., /opt/acme.sh/tronyx.ru_ecc/)
Current code uses: ${ACME_HOME}/data/${domain}/   (doesn't exist → SKIP)

Fix: change account data path in _s3_upload() and _s3_download()
     from data/${domain}/ to ${domain}_ecc/
```

---

## Implementation Plan

### Wave 1: S3 Cache Fixes (Coder)
**Priority: CRITICAL** — эти баги делают S3 cache неработоспособным.
**Shared files: none** — можно параллельно с Wave 2.

#### TASK-1.1: s3-ssl-cache.sh — G2 fix (chain.pem optional)
- **File:** `core/internal/bootstrap/s3-ssl-cache.sh`
- **Change:** `required_files` — убрать `chain.pem` из обязательных
- **Change:** `_s3_upload()` — если chain.pem существует → upload (best-effort), иначе skip
- **Change:** `_s3_download()` — chain.pem уже optional (проверить логи)

#### TASK-1.2: s3-ssl-cache.sh — G3 fix (account path)
- **File:** `core/internal/bootstrap/s3-ssl-cache.sh`
- **Change:** `_s3_upload()` — `acme_domain_dir="${ACME_HOME}/${domain}_ecc"` вместо `data/${domain}`
- **Change:** `_s3_download()` — tar extract path → `${ACME_HOME}/` (извлекает `${domain}_ecc/`)

#### TASK-1.3: s3-ssl-cache.sh — download LE issuer validation
- **File:** `core/internal/bootstrap/s3-ssl-cache.sh`
- **Change:** `_s3_download()` — после openssl x509 валидации добавить проверку issuer (Let's Encrypt)
- **Rationale:** Предотвращает восстановление mkcert/self-signed сертификатов из S3

#### TASK-1.4: Unit tests for S3 cache fixes
- **File:** `tests/test_ssl_s3_cache.py` (существующий или новый)
- **Test 1:** upload без chain.pem → success
- **Test 2:** upload с account data из `<domain>_ecc/` → success
- **Test 3:** download с не-LE сертификатом → rejected
- **Test 4:** download с корректным LE cert → restored

### Wave 2: HTTP-01 Fallback (Coder)
**Priority: HIGH** — без этого сертификаты не выпускаются при сбое DNS-01.
**Depends on: Wave 1** (тесты S3 должны проходить).

#### TASK-2.1: issue-cert.sh — HTTP-01 fallback функция
- **File:** `core/internal/bootstrap/issue-cert.sh`
- **New function:** `_issue_http01_cert()` — выпуск через `acme.sh --issue --standalone`
  - `--standalone` mode: acme.sh запускает временный HTTP-сервер на порту 80
  - Требует: порт 80 свободен (nginx остановлен или ещё не запущен)
  - Выдаёт individual domain cert (НЕ wildcard)
  - Все параметры как у `_issue_acme_cert()` кроме `--dns`
- **Change:** `_issue_acme_cert()` → при FAIL DNS-01, если `wildcard=true`:
  1. Логирует WARN: "DNS-01 failed, falling back to HTTP-01 (no wildcard)"
  2. Вызывает `_issue_http01_cert()` для основного домена
  3. Вызывает `_issue_http01_cert()` для каждого поддомена из `PLATFORM_PROJECT_DOMAINS`
- **New parameter:** `ACME_CHALLENGE_MODE` env var:
  - `dns` (default) — только DNS-01, fail при ошибке
  - `http` — только HTTP-01
  - `auto` — DNS-01 first, HTTP-01 fallback

#### TASK-2.2: issue-cert.sh — graceful domain list
- **File:** `core/internal/bootstrap/issue-cert.sh`
- **Change:** При HTTP-01 fallback — собирать список доменов (platform domain + все vhost имена из node.yaml или PLATFORM_PROJECT_DOMAINS)
- **Change:** `_issue_project_certs()` — вызывается и для HTTP-01 доменов
- **New:** `_extract_vhost_domains()` — парсит node.yaml для получения всех vhost-доменов

#### TASK-2.3: cert_orchestrator.py — HTTP-01 awareness
- **File:** `core/internal/bootstrap/cert_orchestrator.py`
- **Change:** `_issue_cert()` — добавить `challenge_mode` параметр
- **Change:** `orchestrate_certs()` — передавать `ACME_CHALLENGE_MODE` env var в issue-cert.sh
- **Change:** Логировать DNS-01 vs HTTP-01 в DomainCertResult

#### TASK-2.4: Unit tests for HTTP-01 fallback
- **File:** `tests/test_nginx_acme.py`
- **Test 1:** DNS-01 success → wildcard cert
- **Test 2:** DNS-01 fail → HTTP-01 fallback → individual cert
- **Test 3:** ACME_CHALLENGE_MODE=http → только HTTP-01
- **Test 4:** ACME_CHALLENGE_MODE=auto → DNS-01 fail → HTTP-OK fallback

### Wave 3: Infrastructure — S3 credentials + DNS provider check (Sysadmin)
**Priority: MEDIUM** — операционные проверки, не код.

#### TASK-3.1: Проверка работоспособности webnames.ru API
- Проверить через curl/web: `domains_list` и `get_config_acmesh`
- Если API восстановился → проверить выпуск wildcard через `acme.sh --dns dns_webnames`
- Если нет → зафиксировать статус, рекомендовать HTTP-01 или смену провайдера

#### TASK-3.2: Проверка S3 credentials на VPS
- AGE-ключ для расшифровки секретов → должен быть на VPS
- S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET → должны быть в secrets.env
- Ручной тест: `s3-ssl-cache.sh upload tronyx.ru` (если есть cert на диске)
- Ручной тест: `s3-ssl-cache.sh check tronyx.ru` → проверить что cert в S3

#### TASK-3.3: Консолидация acme.sh
- Проверить статус legacy `/root/.acme.sh/`
- Если managed `/opt/acme.sh/` работает → мигрировать cron + account data
- Если нет → оставить legacy как primary, обновить install-acme.sh для использования `/root/.acme.sh/`

---

## Parallel Groups

```
Wave 1 (S3 fixes):
  [TASK-1.1] [TASK-1.2] [TASK-1.3] — параллельно (разные функции в одном файле)
  ↓
  [TASK-1.4] — тесты после code changes

Wave 2 (HTTP-01 fallback):
  [TASK-2.1] [TASK-2.2] — параллельно
  ↓
  [TASK-2.3] — зависит от TASK-2.1
  ↓
  [TASK-2.4] — тесты после всех code changes

Wave 3 (Infrastructure):
  [TASK-3.1] [TASK-3.2] [TASK-3.3] — параллельно, независимы
```

**Waves 1 and 2 can be done by Coder. Wave 3 by Sysadmin.**

---

## Test Spec

```
tests/test_ssl_s3_cache.py:
  test_upload_without_chain_pem_succeeds
  test_upload_with_account_ecc_path
  test_download_rejects_non_le_issuer
  test_download_accepts_le_cert
  test_check_valid_cert_in_s3
  test_check_expired_cert_rejected

tests/test_nginx_acme.py:
  test_dns01_success_wildcard
  test_dns01_fail_http01_fallback
  test_challenge_mode_http_only
  test_challenge_mode_auto_fallback
  test_http01_issues_individual_certs

tests/unit/test_cert_orchestrator.py:
  test_orchestrate_with_s3_restore_and_acme_fallback
  test_orchestrate_http01_fallback_in_result
```

---

## Verification

1. `make gate MODE=fast` — зеленый
2. `python -m pytest tests/test_ssl_s3_cache.py tests/test_nginx_acme.py tests/unit/test_cert_orchestrator.py -s -v`
3. Ручная проверка на VPS (если доступен):
   - `bash s3-ssl-cache.sh upload tronyx.ru` → success (если S3 credentials работают)
   - `bash s3-ssl-cache.sh check tronyx.ru` → success
   - `bash issue-cert.sh` → HTTP-01 выпускает сертификаты (если DNS-01 сломан)

---

## Risks

| Risk | Mitigation |
|------|------------|
| HTTP-01 требует порт 80 свободным | issue-cert.sh вызывается ДО docker compose up (nginx ещё не запущен) |
| LE rate-limit (50 certs/domain/week) | При ~4 доменах укладываемся. S3 cache снижает потребность в перевыпуске |
| webnames API может внезапно заработать | DNS-01 пробуется первым, если работает — HTTP-01 не используется |
| S3 credentials всё ещё не работают | G1 — инфраструктурная проблема. S3 cache gracefully degrade (WARN, не блокирует) |
