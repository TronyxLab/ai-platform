# DevPlan 052 — S3 SSL Cache Bugfix: copy-paste data loss + HTTPS_PROXY leak

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Исправить два бага, обнаруженные при первом запуске s3-ssl-cache.sh: (1) CRITICAL — _s3_check() затирает валидные сертификаты в S3 пустыми файлами из-за copy-paste upload.py вызова, (2) MEDIUM — HTTPS_PROXY из secrets.env утекает в host-скрипты boto3, вызывая ProxyConnectionError на VPS.
  DESCRIPTION: Точечный багфикс без переработки архитектуры. Bug 1: удалить блок upload.py (строки 330-340) из _s3_check() — он скопирован из _s3_upload(), но для check нужен только download. Bug 2: добавить defence-in-depth unset прокси-переменных перед host-level boto3-вызовами в 5 точках (s3-ssl-cache.sh, node-lifecycle.sh, state_machine.py, steps.py, cert_orchestrator.py). Дополнить инлайн-boto3 в _s3_download_file() явным os.environ.pop('HTTPS_PROXY').
  RATIONALE: Bug 1 — классический copy-paste баг: _s3_upload() использует upload.py для загрузки, _s3_check() должен только скачивать и валидировать, но содержит идентичный вызов upload.py с пустым tmp-файлом в качестве source. upload.py вызывает boto3 client.upload_file(local_path, bucket, key), что перезаписывает валидный fullchain.pem нулями. Bug 2 — secrets.env содержит HTTPS_PROXY=http://host.docker.internal:8118 для Docker-контейнеров, но host-level boto3 (upload.py, inline python3) подхватывает эту переменную и пытается проксировать S3-запросы через несуществующий на VPS хост. Текущая защита (unset HTTP_PROXY HTTPS_PROXY в node-lifecycle.sh:81) неполная — не покрывает lowercase-варианты и не применяется во всех точках вызова boto3.
  ACCEPTANCE_CRITERIA:
    1. _s3_check() НЕ содержит вызов upload.py — верифицируется статическим тестом
    2. _s3_check() использует только _s3_download_file() + openssl-валидацию — существующие тесты test_s3_cache_script_* проходят
    3. При вызове s3-ssl-cache.sh с HTTPS_PROXY в окружении (secrets.env) — boto3 НЕ пытается использовать прокси
    4. node-lifecycle.sh update_step_3_ssl_provision использует unset_platform_proxy (все 6 вариантов), а не ручной unset двух uppercase
    5. state_machine.py _ssl_provision() unset-ит все 6 proxy-вариантов (было только 2 uppercase)
    6. _s3_download_file() inline boto3 явно удаляет HTTPS_PROXY/HTTP_PROXY из os.environ перед client creation
    7. Все существующие тесты (make gate MODE=fast) проходят без регрессии
    8. Новый статический тест: _s3_check() не содержит upload.py
    9. Новый unit-тест: secrets.env с HTTPS_PROXY не ломает boto3 S3Config
  IMPLEMENTS:
    - Статус-отчёт сессии 2026-07-22 (обнаружение двух багов при первом запуске s3-ssl-cache.sh)
    - TRAP[BUG] в s3-ssl-cache.sh:314-326 (уже задокументирован, ждёт исправления)
  IMPACTS:
    - core/internal/bootstrap/s3-ssl-cache.sh — удаление upload.py из _s3_check() (11 строк), +proxy-pop в _s3_download_file()
    - core/internal/bootstrap/node-lifecycle.sh — замена unset HTTP_PROXY HTTPS_PROXY на unset_platform_proxy (строка 81)
    - core/internal/bootstrap/lifecycle/state_machine.py — расширение unset до 6 proxy-вариантов (строка 1696)
    - core/internal/bootstrap/lifecycle/steps.py — добавление proxy unset (строка 726)
    - core/internal/bootstrap/cert_orchestrator.py — добавление proxy unset в source_secrets_env()
    - tests/test_ssl_s3_cache.py — +2 теста: запрет upload.py в _s3_check(), proxy-утечка
    - tests/unit/ — возможен новый test_s3_proxy_isolation.py
  REQUIRES:
    - Python 3.10+ (уже на VPS)
    - upload.py с --config-source ssl-cache (существует, проверен)
    - secrets.env c HTTPS_PROXY (для воспроизведения Bug 2)
-->
$START_DEVPLAN

## Overview

**Status:** Draft — pending architect review
**DevPlan:** 052
**Session:** 2026-07-22
**Priority:** CRITICAL — Bug 1 вызывает data loss (перезапись валидных S3-сертификатов нулями)

### Problem Statement

22 июля 2026 при первом запуске `s3-ssl-cache.sh upload` + `s3-ssl-cache.sh check` обнаружено два бага:

| # | Баг | Серьёзность | Симптом |
|---|-----|-------------|---------|
| 🔴 | `_s3_check()` вызывает `upload.py` с пустым tmp-файлом — затирает S3 | CRITICAL | Все 4 доменных сертификата в S3 стали 0-байтовыми после `check` |
| 🟡 | `HTTPS_PROXY` из `secrets.env` утекает в host-скрипты boto3 | MEDIUM | `ProxyConnectionError` при вызове upload.py/download из s3-ssl-cache.sh |

---

## Root Cause Analysis

### Bug 1: `_s3_check()` copy-paste upload → data loss

**Location:** `core/internal/bootstrap/s3-ssl-cache.sh:330-340`

**Механизм:**

1. `_s3_upload()` (строка 65) вызывает `upload.py <local_file> <s3_key>` — корректно: загружает локальный файл сертификата в S3
2. `_s3_check()` (строка 308) содержит идентичный вызов `upload.py "$tmp_cert" "${s3_base}/fullchain.pem"` (строка 332-336), скопированный из `_s3_upload()`
3. `$tmp_cert` — только что созданный `mktemp` (строка 328), **0 байт**
4. `upload.py` вызывает `boto3.client.upload_file(local_path, bucket, key)` — **записывает 0 байт в S3, перезаписывая валидный fullchain.pem**
5. Комментарий на строке 331 говорит "Downloading", но код выполняет UPLOAD
6. `_s3_download_file()` на строке 353 делает правильный download, но **после** того как S3 уже испорчен

**Почему не было обнаружено раньше:** `s3-ssl-cache.sh check` никогда не запускался до 22 июля — это был первый тестовый прогон полного цикла upload→check→download.

**Impact:** CRITICAL data loss. 4 доменных fullchain.pem в S3 перезаписаны нулями. Сертификаты перезагружены вручную. Production не затронут (S3 cache — disaster recovery, сертификаты на VPS не пострадали).

### Bug 2: HTTPS_PROXY leak from secrets.env → host boto3

**Location:** `core/internal/bootstrap/node-lifecycle.sh:80-84`, `core/internal/bootstrap/lifecycle/state_machine.py:1696`, `core/lib/secrets.sh:143-148`

**Механизм:**

1. `secrets.env` содержит `HTTPS_PROXY=http://host.docker.internal:8118` для Tor/Privoxy-прокси в Docker-контейнерах
2. `update_step_3_ssl_provision()` (node-lifecycle.sh:81) делает `set -a; source secrets.env; set +a` → HTTPS_PROXY попадает в окружение shell
3. После source делается `unset HTTP_PROXY HTTPS_PROXY` — но **только uppercase**, не lowercase (`http_proxy`, `https_proxy`), не `NO_PROXY`/`no_proxy`
4. boto3 (и `upload.py`, и inline `_s3_download_file()`) проверяет proxy-переменные и пытается слать запросы через `host.docker.internal:8118`
5. На VPS `host.docker.internal` не резолвится → `ProxyConnectionError`

**Где ещё есть риск:** `state_machine.py:1696` делает тот же `unset HTTP_PROXY HTTPS_PROXY` (только uppercase). `steps.py:726` grep-ит `HTTP_PROXY` но не unset-ит. `cert_orchestrator.py` source_secrets_env не unset-ит прокси вообще.

**Текущая защита в step_10_decrypt_secrets:** `unset_platform_proxy()` (secrets.sh:82) unset-ит все 6 вариантов + `sed -i.bak '/^HTTPS_PROXY=/d'` физически удаляет строку из файла при `TOR_ENABLED != true`. Но:
- При `TOR_ENABLED=true` sed-удаление пропускается → строка остаётся в файле
- `unset_platform_proxy` очищает текущий shell, но secrets.env может быть пере-source-нут позже (в update_step_3_ssl_provision, cert_orchestrator)

**Impact:** MEDIUM. Блокирует S3-операции на VPS с включённым Tor. Не влияет на production при TOR_ENABLED=false (sed удаляет строку из файла).

---

## Fix Plan

### Fix 1: Remove upload.py from `_s3_check()` (CRITICAL)

**Файл:** `core/internal/bootstrap/s3-ssl-cache.sh`

**Изменения:**

1. **Удалить строки 330-340** — блок вызова `upload.py`:
   ```bash
   # УДАЛИТЬ:
   # Try to download fullchain.pem from S3 (single retry, minimal wait)
   log_imp 8 "-" "Downloading fullchain.pem from S3 to check validity"
   if ! python3 "$UPLOAD_PY" \
       --config-source ssl-cache \
       --retries 1 \
       "$tmp_cert" \
       "${s3_base}/fullchain.pem" 2>&1; then
       log_step "check" "INFO" "No cert in S3 cache for ${domain} — cache miss (not an error)"
       rm -f "$tmp_cert"
       return 1
   fi
   ```

2. **Обновить TRAP[BUG] маркер** (строка 326): `FIX PENDING` → `FIXED in DevPlan 052`. Сохранить комментарий как историческую документацию.

**Что остаётся:** `tmp_cert` создание (строка 327-328), проверка `S3_BUCKET` (строки 345-350), вызов `_s3_download_file()` (строка 353), openssl-валидация (строки 366-394). Это корректный flow: download → validate → return.

**Риск:** нулевой. Удаляемый код — мёртвый (с data-loss side effect). `_s3_download_file()` полностью покрывает потребность check.

### Fix 2: Defence-in-depth HTTPS_PROXY isolation (MEDIUM)

**Стратегия:** Не полагаться только на `sed`-удаление из файла. Добавить явный proxy-unset в каждой точке, где host-level boto3 может выполняться.

**Точки исправления:**

#### 2a. `s3-ssl-cache.sh` — `_s3_download_file()` inline boto3

Добавить `os.environ.pop()` перед созданием boto3 client:

```python
# In _s3_download_file() inline python3 -c block, BEFORE boto3.client():
import os
for proxy_var in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'NO_PROXY', 'no_proxy'):
    os.environ.pop(proxy_var, None)
```

Это **последняя линия обороны** — даже если прокси-переменные просочились из secrets.env, inline boto3 их игнорирует.

#### 2b. `s3-ssl-cache.sh` — `main()` перед вызовом upload.py

Добавить `unset_platform_proxy` (или эквивалентный inline unset) в `main()` перед `case upload)` — чтобы `upload.py` тоже не подхватывал прокси.

Примечание: `unset_platform_proxy` определена в `core/lib/secrets.sh`, но s3-ssl-cache.sh не source-ит secrets.sh. Добавить либо source, либо локальный unset.

#### 2c. `node-lifecycle.sh:81` — `update_step_3_ssl_provision`

```bash
# Было:
[[ -f "$secrets_env" ]] && { set -a; source "$secrets_env"; set +a; unset HTTP_PROXY HTTPS_PROXY

# Стало:
[[ -f "$secrets_env" ]] && { set -a; source "$secrets_env"; set +a; unset_platform_proxy
```

`unset_platform_proxy` уже доступна (node-lifecycle.sh source-ит secrets.sh на строке 45).

#### 2d. `state_machine.py:1696` — `_ssl_provision()`

```python
# Было:
f"set -a; source '{secrets_env}'; set +a; unset HTTP_PROXY HTTPS_PROXY; echo WEBNAMES_API_KEY=..."

# Стало:
f"set -a; source '{secrets_env}'; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; echo WEBNAMES_API_KEY=..."
```

#### 2e. `steps.py:726` — `_ssl_provision_step()`

После source secrets.env добавить `unset` всех 6 proxy-переменных.

#### 2f. `cert_orchestrator.py` — `source_secrets_env()`

После `set -a; source secrets.env; set +a` добавить `os.environ.pop(var, None)` для всех 6 proxy-переменных.

---

## File Manifest

### Modified files

| Файл | Изменение | Строки |
|------|-----------|--------|
| `core/internal/bootstrap/s3-ssl-cache.sh` | Удалить upload.py из `_s3_check()` (строки 330-340) | −11 |
| `core/internal/bootstrap/s3-ssl-cache.sh` | Добавить `os.environ.pop` в inline boto3 `_s3_download_file()` | +6 |
| `core/internal/bootstrap/s3-ssl-cache.sh` | Добавить proxy unset в `main()` перед dispatch | +7 |
| `core/internal/bootstrap/s3-ssl-cache.sh` | Обновить TRAP[BUG] маркер: FIX PENDING → FIXED | ±1 |
| `core/internal/bootstrap/node-lifecycle.sh` | `unset HTTP_PROXY HTTPS_PROXY` → `unset_platform_proxy` | ±1 (строка 81) |
| `core/internal/bootstrap/lifecycle/state_machine.py` | Расширить unset до 6 вариантов | ±1 (строка 1696) |
| `core/internal/bootstrap/lifecycle/steps.py` | Добавить proxy unset после source secrets.env | +2 (строка 726) |
| `core/internal/bootstrap/cert_orchestrator.py` | Добавить proxy unset в source_secrets_env() | +3 |
| `tests/test_ssl_s3_cache.py` | +2 теста: запрет upload.py в check, proxy-изоляция | +50 |

### New files

| Файл | Назначение |
|------|-----------|
| (none) | Все изменения — в существующих файлах |

---

## Test Plan

### T1: Статический тест — `_s3_check()` не содержит upload.py

```python
@pytest.mark.static_audit
def test_s3_check_does_not_use_upload_py():
    """_s3_check() must NOT call upload.py — regression guard for TRAP[BUG] 2026-07-22."""
    script_path = "core/internal/bootstrap/s3-ssl-cache.sh"
    with open(script_path) as f:
        content = f.read()
    
    # Extract _s3_check() function body
    import re
    match = re.search(r'_s3_check\(\)\s*\{(.*?)\n\}', content, re.DOTALL)
    assert match, "_s3_check() function not found"
    check_body = match.group(1)
    
    # upload.py must NOT appear in _s3_check()
    assert "upload.py" not in check_body, (
        "CRITICAL: _s3_check() contains upload.py call — this overwrites S3 certs with empty files. "
        "See TRAP[BUG] 2026-07-22 in s3-ssl-cache.sh:314"
    )
    
    # But _s3_download_file must be present (correct download path)
    assert "_s3_download_file" in check_body, (
        "_s3_check() must use _s3_download_file() for download"
    )
```

### T2: Unit-тест — HTTPS_PROXY не ломает S3Config

```python
def test_s3_config_ignores_https_proxy(monkeypatch):
    """S3Config/S3 client must not use HTTPS_PROXY from secrets.env context."""
    from core.modules.backup_cron.scripts.upload import get_s3_config, _parse_args
    
    monkeypatch.setenv("HTTPS_PROXY", "http://host.docker.internal:8118")
    monkeypatch.setenv("HTTP_PROXY", "http://host.docker.internal:8118")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example.com")
    
    # get_s3_config should succeed (not throw ProxyConnectionError)
    config = get_s3_config()
    assert config["aws_access_key_id"] == "test-key"
    # Proxy env vars should be ignored by S3Config
```

### T3: Регрессия — существующие тесты

```bash
make gate MODE=fast  # Все существующие gate-тесты должны остаться зелёными
pytest tests/test_ssl_s3_cache.py -v  # Все SSL S3 cache тесты
pytest tests/test_cert_backup_gap.py -v  # Сертификатный gap-тест
pytest tests/unit/test_cert_orchestrator.py -v  # Unit-тесты cert_orchestrator
```

---

## Rollback Plan

| Сценарий | Действие |
|----------|----------|
| Bug 1 fix ломает _s3_check | `git revert` коммита — upload.py возвращается, баг остаётся. Ручной workaround: не вызывать `s3-ssl-cache.sh check` до исправления |
| Proxy fix ломает Tor-прокси | Контейнеры получают HTTPS_PROXY через docker-compose env, не через host shell. Host-level unset не влияет на контейнеры → риск нулевой |
| Регрессия в CI gate | `make gate MODE=fast` — зелёный до и после. При падении: проверить test_s3_check_does_not_use_upload_py на false positive |

---

## Verification Checklist

- [ ] `_s3_check()` function body does NOT contain string "upload.py"
- [ ] `_s3_check()` function body DOES contain "_s3_download_file"
- [ ] `_s3_download_file()` inline python3 pops HTTPS_PROXY before boto3.client()
- [ ] `main()` in s3-ssl-cache.sh unsets proxy vars before case dispatch
- [ ] `node-lifecycle.sh:81` uses `unset_platform_proxy` (not manual unset)
- [ ] `state_machine.py:1696` unsets all 6 proxy variants
- [ ] `steps.py:726` unsets proxy after source secrets.env
- [ ] `cert_orchestrator.py` source_secrets_env() unsets proxy vars
- [ ] `make gate MODE=fast` — all green
- [ ] `pytest tests/test_ssl_s3_cache.py -v` — all green
- [ ] `pytest tests/test_cert_backup_gap.py -v` — all green

$END_DEVPLAN
