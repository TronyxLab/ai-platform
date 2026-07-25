$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация жизненного цикла SSL-сертификатов — единый restore-first entrypoint через cert_orchestrator, гарантированный S3 backup при любом сценарии (bootstrap, node-update, acme.sh cron renewal), устранение дублирующейся логики _ssl_provision().
DESCRIPTION:           Трёхфазная имплементация с Python-миграцией shell-кода: (1) Порт s3-ssl-cache.sh (602 строки) в Python-модуль s3_ssl_cache.py — устранение корневой причины бага (subshell credential propagation), замена inline python3 heredoc на типизированные функции, (2) Унификация — замена _ssl_provision() на cert_orchestrator.orchestrate_certs() с прямым импортом s3_ssl_cache, (3) Гарантированный S3 backup: upload-on-skip в cert_orchestrator + acme.sh --renew-hook для cron renewal.
RATIONALE:             Bug-репорт от 2026-07-25: сертификаты были выпущены и сайты работали, но при повторном bootstrap не восстановились из S3. Корневая причина — двойная: (а) _ssl_provision() не прокидывает S3_* креды в родительский процесс — s3-ssl-cache.sh upload/download молча падают; (б) cert_orchestrator в deploy_context пропускает platform domain (cert на диске) — upload в S3 не вызывается. Как следствие: platform domain cert НИКОГДА не попадает в S3 при нормальном потоке. Дополнительно: дублирование логики между _ssl_provision() и cert_orchestrator противоречит языковой политике (shell-логика в Python-модуле state_machine) и принципу AI-First Architecture (единый typed contract).
ACCEPTANCE_CRITERIA:   1. `make bootstrap-node` — platform domain cert восстанавливается из S3 (если есть) без нового LE-запроса. 2. `make bootstrap-node` — после успешного issue новый cert попадает в S3. 3. `make node-update` — при существующем cert на диске, S3 upload вызывается (кеш не устаревает). 4. acme.sh cron renewal — после renew сертификат автоматически синхронизируется в S3. 5. `make deploy-context` — поведение не меняется (project domain certs обрабатываются как прежде). 6. Все существующие тесты (test_cert_orchestrator.py, test_cert_backup_gap.py, test_ssl_s3_cache.py) проходят. 7. `make gate MODE=fast` — зелёный.
IMPLEMENTS:            Bug-report 2026-07-25 (tronyx-vps — certs not restored from S3), StatusReport 045 (cert orchestration gap), DevPlan 047 (deploy_context cert integration).
IMPACTS:               core/internal/bootstrap/lifecycle/state_machine.py (_ssl_provision → удаление/замена), core/internal/bootstrap/cert_orchestrator.py (upload-on-skip + прямой импорт s3_ssl_cache), core/internal/bootstrap/s3_ssl_cache.py (NEW — Python-порт s3-ssl-cache.sh), core/internal/bootstrap/s3-ssl-cache.sh (редуцирован до CLI-фасада ~30 строк), core/internal/bootstrap/issue-cert.sh (--renew-hook для cron, --reloadcmd + S3 upload), core/internal/bootstrap/lifecycle/steps.py (без изменений), tests/unit/test_s3_ssl_cache.py (NEW), tests/unit/test_cert_upload_on_skip.py (NEW), tests/unit/test_cert_orchestrator.py (обновление), tests/test_cert_backup_gap.py (обновление ассертов), tests/test_node_lifecycle_static.py (обновление test_update_ssl_step), core/internal/bootstrap/AGENTS.md (обновление pipeline docs).
REQUIRES:              S3 credentials доступны в /run/platform/secrets.env на момент выполнения ssl_provision (уже обеспечивается шагом decrypt-secrets перед ssl_provision в pipeline). SECRETS_ENV_FILE env var (уже передаётся через node-lifecycle.sh).
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- DECISIONS: Ответы на 3 вопроса из брифа + архитектурное обоснование → DECISIONS_ID
- DRAFT_CODE_GRAPH: XML-граф изменяемых модулей → GRAPH_ID
- PHASE_1: Fix S3 credential propagation (CRITICAL, ~30 мин) → PHASE1_ID
- PHASE_2: Унификация entrypoint (HIGH, ~3-4 часа) → PHASE2_ID
- PHASE_3: Гарантированный S3 backup (MEDIUM, ~1-2 часа) → PHASE3_ID
- FILE_MANIFEST: Полный список файлов с diff-планом → FILES_ID
- TEST_PLAN: Что и как тестировать → TEST_ID
- PIPELINE_FLOW: Обновлённая диаграмма pipeline до/после → FLOW_ID
- RISKS: Что может пойти не так → RISK_ID
**SECTION_USE_CASES:**
- USE_CASE fresh bootstrap без S3 cache → FRESH_NO_S3
- USE_CASE fresh bootstrap с S3 cache → FRESH_WITH_S3
- USE_CASE node-update (cert на диске, не истёк) → UPDATE_SKIP
- USE_CASE node-update (cert на диске, был продлён cron) → UPDATE_RENEWED
- USE_CASE node-update после wipe /etc/letsencrypt → UPDATE_WIPED
- USE_CASE acme.sh cron renewal → CRON_RENEW
$END_DOCUMENT_PLAN

---

# 02-DevPlan: Унификация жизненного цикла SSL-сертификатов

**Severity:** CRITICAL (блокирует disaster recovery — при потере VPS сертификаты невосстановимы из S3)
**Created:** 2026-07-25
**Author:** Architect (Kilo)
**Parent:** `01-Brief.md`
**Status:** READY — ожидает имплементации

---

## 0. Архитектурные решения (ответы на вопросы из брифа)

### Q1: Выносить `_source_secrets_env()` в общий модуль или оставить дубликатом?

**Решение: НЕ создавать secrets_loader.py. Вместо этого — портировать `s3-ssl-cache.sh` в Python-модуль `s3_ssl_cache.py`.**

```
ПРИЧИНЫ:
1. Корневая причина бага — subshell в _ssl_provision() убивает S3_* переменные.
   secrets_loader.py патчит симптом (source), но не решает фундаментальную проблему:
   s3-ssl-cache.sh вызывается через subprocess и ВСЕГДА зависит от env родителя.

2. Python-модуль s3_ssl_cache.py работает в ТОМ ЖЕ процессе, что и cert_orchestrator.
   Читает os.environ напрямую — никакого subshell, никакой проблемы credential propagation.

3. s3-ssl-cache.sh уже нарушает языковую политику:
   - _s3_download_file() — inline python3 heredoc (37 строк boto3) — Tier-1 Strangler trigger
   - _s3_bulk_restore() — inline python3 для парсинга YAML — Tier-1 Strangler trigger
   Оба подлежат немедленному извлечению в Python-модуль.

4. Каскадный эффект:
   - secrets_loader.py НЕ НУЖЕН — s3_ssl_cache.py решает проблему на корню
   - cert_orchestrator.py импортирует s3_ssl_cache напрямую (без subprocess)
   - _source_secrets_env() в cert_orchestrator.py остаётся (нужна для WEBNAMES_API_KEY)

КОНТРАКТ s3_ssl_cache.py:
  def upload_cert(domain: str, cert_dir: str, s3_bucket: str, ...) -> bool
  def download_cert(domain: str, cert_dir: str, s3_bucket: str, ...) -> bool
  def check_cert(domain: str, s3_bucket: str, ...) -> bool
  def bulk_restore(node_yaml_path: str, ...) -> dict[str, str]

SHELL-ФАСАД s3-ssl-cache.sh (редуцирован до ~30 строк):
  - Парсинг аргументов (upload|download|check|bulk-restore)
  - Делегирование в python3 s3_ssl_cache.py
  - Только для обратной совместимости (issue-cert.sh --reloadcmd)
```

### Q2: `orchestrate_all_domains(node_yaml)` в cert_orchestrator или `_extract_domains_for_context()` в steps.py?

**Решение: Оставить `_extract_domains_for_context()` в `steps.py`. НЕ добавлять `orchestrate_all_domains()` в cert_orchestrator.**

```
ПРИЧИНЫ (отказоустойчивость — главный критерий):

1. Single Responsibility: cert_orchestrator.orchestrate_certs(domains, ...) имеет чистый контракт
   "список доменов → результат". Добавление парсинга node.yaml даёт вторую ответственность.

2. Fail-Fast на границе: если _extract_domains_for_context() упадёт (битый YAML),
   это произойдёт ДО вызова cert_orchestrator. Мы не теряем частичные результаты.
   Если extraction встроить в cert_orchestrator и YAML сломан — теряем ВСЕ домены.

3. Для вызова из ssl_provision (update mode) нужны ВСЕ домены (platform + все проекты).
   Решение: использовать _extract_domains_for_context(node_yaml, context="")
   с пустым контекстом → фильтрация не применяется → возвращаются все домены.

4. Тестируемость: cert_orchestrator остаётся pure-function от списка доменов.
   Не нужно мокать файловую систему для node.yaml.
```

### Q3: Оставлять `ssl_provision` как отдельный шаг или объединить с `deploy_context`?

**Решение: Оставить `ssl_provision` как отдельный шаг в UPDATE_STEPS, но заменить вызов `_ssl_provision()` на `cert_orchestrator.orchestrate_certs()`.**

```
ПРИЧИНЫ (отказоустойчивость — главный критерий):

1. Два шанса вместо одного:
   - ssl_provision (update step 3/4): cert_orchestrator → restore-from-S3 или issue → cert на диске.
   - deploy_context (update step 8/9): cert_orchestrator → upload-on-skip → S3 синхронизация.
   Если первый вызов упал (S3 timeout), второй — страховка.

2. Разные предусловия:
   - ssl_provision: ДО deploy_modules, nginx не запущен → HTTP-01 standalone возможен.
   - deploy_context: ПОСЛЕ deploy_modules, nginx работает → HTTP-01 standalone НЕВОЗМОЖЕН (port 80 занят).
   Объединение ломает HTTP-01 fallback для platform domain.

3. Минимальные изменения: замена 70 строк _ssl_provision() на ~15 строк вызова cert_orchestrator.
   Структура pipeline не меняется, индексы шагов не сдвигаются.

4. Idempotency: второй вызов cert_orchestrator в deploy_context видит cert на диске → skip + upload.
   Дублирования операций не происходит.
```

---

## 1. Draft Code Graph (XML)

```xml
<CodeGraph>
  <!-- NEW: Python-порт s3-ssl-cache.sh — устраняет root cause бага -->
  <entity id="s3_ssl_cache_py" TYPE="Module" keywords="s3,ssl,cache,upload,download,check,bulk-restore,boto3">
    <annotation>Порт 602-строчного s3-ssl-cache.sh в Python. Прямой доступ к os.environ (без subshell).
    Заменяет inline python3 heredoc в _s3_download_file() и _s3_bulk_restore().
    Shell s3-ssl-cache.sh редуцирован до CLI-фасада (~30 строк).</annotation>
    <CrossLinks>
      <link target="cert_orchestrator_py" relation="imported_by"/>
      <link target="issue_cert_sh" relation="called_by_cli"/>
    </CrossLinks>
  </entity>

  <!-- MODIFY: cert_orchestrator — прямой импорт s3_ssl_cache, upload-on-skip -->
  <entity id="cert_orchestrator_py" TYPE="Module" keywords="cert,ssl,s3,acme,orchestrator,upload-on-skip">
    <annotation>Добавлены: upload-on-skip в _process_single_domain(). Импорт s3_ssl_cache напрямую
    (без subprocess). _source_secrets_env() остаётся (нужна для WEBNAMES_API_KEY).
    _try_s3_restore() и _upload_to_s3() вызывают s3_ssl_cache напрямую.</annotation>
    <CrossLinks>
      <link target="s3_ssl_cache_py" relation="imports"/>
      <link target="issue_cert_sh" relation="calls"/>
    </CrossLinks>
  </entity>

  <!-- MODIFY: _ssl_provision() → удаление, замена на cert_orchestrator -->
  <entity id="state_machine_py" TYPE="Module" keywords="state-machine,lifecycle,ssl_provision,update-steps">
    <annotation>Удалена _ssl_provision() (~70 строк). ssl_provision step вызывает
    cert_orchestrator.orchestrate_certs() для всех доменов.</annotation>
    <CrossLinks>
      <link target="cert_orchestrator_py" relation="imports_dynamic"/>
    </CrossLinks>
  </entity>

  <!-- MODIFY: --renew-hook + --reloadcmd с вызовом s3_ssl_cache.py через CLI-фасад -->
  <entity id="issue_cert_sh" TYPE="ShellScript" keywords="acme,cert,issue,renew-hook,s3-upload">
    <annotation>Добавлен --renew-hook с python3 s3_ssl_cache.py upload в _acme_install_cron().
    --reloadcmd расширен: + python3 s3_ssl_cache.py upload.</annotation>
    <CrossLinks>
      <link target="s3_ssl_cache_py" relation="called_by_cli"/>
    </CrossLinks>
  </entity>

  <!-- REDUCE: Shell-фасад редуцирован с 602 до ~30 строк -->
  <entity id="s3_ssl_cache_sh" TYPE="ShellScript" keywords="s3,ssl,cache,cli,facade">
    <annotation>Тонкий CLI-фасад: парсинг аргументов + вызов python3 s3_ssl_cache.py.
    Вся бизнес-логика перенесена в s3_ssl_cache.py.</annotation>
    <CrossLinks>
      <link target="s3_ssl_cache_py" relation="delegates_to"/>
    </CrossLinks>
  </entity>

  <!-- NO CHANGE: steps.py — вызов остаётся прежним -->
  <entity id="steps_py" TYPE="Module" keywords="steps,deploy_context,domains,extract">
    <annotation>_step_deploy_context() не меняется. cert_orchestrator вызывается как прежде.</annotation>
    <CrossLinks>
      <link target="cert_orchestrator_py" relation="imports_dynamic"/>
    </CrossLinks>
  </entity>

  <!-- NEW + MODIFY: tests -->
  <entity id="tests" TYPE="TestSuite" keywords="test,cert,orchestrator,s3-cache,upload-on-skip,renew-hook">
    <annotation>Новые: test_s3_ssl_cache.py, test_cert_upload_on_skip.py.
    Обновлены: test_cert_orchestrator.py, test_cert_backup_gap.py, test_node_lifecycle_static.py.</annotation>
    <CrossLinks>
      <link target="s3_ssl_cache_py" relation="tests"/>
      <link target="cert_orchestrator_py" relation="tests"/>
      <link target="issue_cert_sh" relation="tests"/>
    </CrossLinks>
  </entity>
</CodeGraph>
```

---

## 2. Pipeline Flow — до и после

### До изменений (текущий, СЛОМАННЫЙ)

```
UPDATE mode:
  verify_core → provision → deliver_overlays
  → ssl_provision: _ssl_provision()
      ├── source secrets.env in SUBSHELL → ❌ S3_* vars УМЕРЛИ
      ├── s3-ssl-cache.sh check → FAIL (нет кредов)
      ├── cert на диске? → SKIP (upload не вызывается)
      └── ⚠️ Platform cert никогда не в S3
  → deploy_modules → provision_llm_keys → healthcheck → converge
  → deploy_context: cert_orchestrator.orchestrate_certs()
      ├── Platform domain: cert на диске → skip (upload НЕ вызывается)
      ├── Project domains: cert нет → S3 check → issue → upload ✅
      └── ⚠️ Platform cert ВЕЧНО отсутствует в S3

INIT mode:
  ... → node_update (→ UPDATE mode, см. выше) → deploy_context (см. выше)

CRON:
  acme.sh --cron → renew cert → reload nginx
  └── ❌ S3 upload НЕ вызывается → кеш устарел
```

### После изменений (целевой, Python-first)

```
UPDATE mode:
  verify_core → provision → deliver_overlays
  → ssl_provision: cert_orchestrator.orchestrate_certs(ALL domains)
      ├── s3_ssl_cache.check_cert(domain) → ✅ os.environ напрямую (без subshell!)
      ├── S3 hit → s3_ssl_cache.download_cert(domain) → cert на диске
      ├── S3 miss → issue-cert.sh → после успеха: s3_ssl_cache.upload_cert(domain)
      └── ✅ Все домены обработаны restore-first в Python-процессе
  → deploy_modules → provision_llm_keys → healthcheck → converge
  → deploy_context: cert_orchestrator.orchestrate_certs(ALL domains)
      ├── Platform domain: cert на диске → skip + s3_ssl_cache.upload_cert ✅
      ├── Project domains: cert на диске → skip + s3_ssl_cache.upload_cert ✅
      └── ✅ S3 синхронизирован для всех доменов

INIT mode:
  ... → node_update (→ UPDATE mode) → deploy_context (→ upload-on-skip)

CRON:
  acme.sh --cron → renew cert
  ├── --reloadcmd: reload nginx + python3 s3_ssl_cache.py upload ✅
  └── ✅ S3 синхронизирован после каждого cron renewal
```

---

## 3. Фаза 1: Python-миграция s3-ssl-cache.sh → s3_ssl_cache.py (CRITICAL, ~2 часа)

**Цель:** Портировать всю бизнес-логику 602-строчного `s3-ssl-cache.sh` в Python-модуль `s3_ssl_cache.py`. Это устраняет корневую причину бага (subshell credential propagation) и два Tier-1 нарушения языковой политики (inline python3 heredoc).

### 3.1 Создать `core/internal/bootstrap/s3_ssl_cache.py` (NEW, ~250 строк)

Модуль портирует 4 операции из shell-скрипта в типизированные Python-функции:

```python
# core/internal/bootstrap/s3_ssl_cache.py
# GREP_SUMMARY: s3-ssl-cache, boto3, cert-upload, cert-download, cert-check, bulk-restore
# STRUCTURE: ▶ upload_cert → download_cert → check_cert → bulk_restore → ⎋

# ── Ported from s3-ssl-cache.sh _s3_upload() ──
def upload_cert(domain: str, cert_dir: str, acme_home: str,
                s3_bucket: str, s3_prefix: str = "platform/ssl-certs") -> bool:
    """Upload cert files to S3: fullchain.pem, privkey.pem, chain.pem (opt), account.tar.gz.
    Returns True on success. Uses boto3 directly (reads S3_* from os.environ).
    """

# ── Ported from s3-ssl-cache.sh _s3_download_file() + _s3_download() ──
def download_cert(domain: str, cert_dir: str, acme_home: str,
                  s3_bucket: str, s3_prefix: str = "platform/ssl-certs") -> bool:
    """Download and validate cert from S3. Validates issuer (LE only), domain match,
    openssl integrity. Returns True if restored successfully.
    """

# ── Ported from s3-ssl-cache.sh _s3_check() ──
def check_cert(domain: str, s3_bucket: str,
               s3_prefix: str = "platform/ssl-certs") -> bool:
    """Check if valid cert exists in S3 (>30 days expiry, correct domain).
    Uses boto3 to download fullchain.pem to temp file, validates with openssl.
    """

# ── Ported from s3-ssl-cache.sh _s3_bulk_restore() ──
def bulk_restore(node_yaml_path: str, s3_bucket: str,
                 s3_prefix: str = "platform/ssl-certs") -> dict[str, str]:
    """Parse node.yaml → extract all domains → check + download each.
    Returns {domain: status} dict. Replaces inline python3 YAML parsing.
    """
```

**Ключевое преимущество:** `s3_ssl_cache.py` читает `os.environ['S3_ACCESS_KEY']`, `os.environ['S3_SECRET_KEY']`, `os.environ['S3_BUCKET']` напрямую — никакого subshell, никакой проблемы credential propagation.

**Устраняемые нарушения языковой политики:**
- `_s3_download_file()` → inline python3 heredoc (37 строк boto3) → Tier-1: извлечено в `download_cert()`
- `_s3_bulk_restore()` → inline python3 YAML-парсинг + json → Tier-1: извлечено в `bulk_restore()`

### 3.2 Редуцировать `s3-ssl-cache.sh` до CLI-фасада (~30 строк)

```bash
#!/usr/bin/env bash
# Thin CLI facade — delegates all business logic to s3_ssl_cache.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command="${1:-}"; shift || true
domain="${1:-}"
python3 "${SCRIPT_DIR}/s3_ssl_cache.py" "$command" "$domain" "$@"
```

Весь код `_s3_upload()`, `_s3_download()`, `_s3_check()`, `_s3_bulk_restore()`, `_s3_download_file()`, `main()` удаляется.

### 3.3 Обновить `cert_orchestrator.py` — прямой импорт s3_ssl_cache

- `_try_s3_restore()`: заменить `subprocess.run(["bash", s3_cache_script, "check", domain])` на `s3_ssl_cache.check_cert(domain, s3_bucket)`
- `_try_s3_restore()`: заменить `subprocess.run(["bash", s3_cache_script, "download", domain])` на `s3_ssl_cache.download_cert(domain, ...)`
- Добавить `_upload_to_s3()`: вызов `s3_ssl_cache.upload_cert(domain, cert_dir, ...)`
- `_source_secrets_env()` остаётся (нужна для WEBNAMES_API_KEY)

### 3.4 Верификация Фазы 1

```bash
make test MARKER=static,unit
```

---

## 4. Фаза 2: Унификация — cert_orchestrator как единый entrypoint (HIGH, ~2-3 часа)

### 4.1 `state_machine.py` — замена `_ssl_provision()` на cert_orchestrator

**Шаг 2a:** В `_execute_update_step()`, case `"ssl_provision"` (строка 1189-1190):

```python
# БЫЛО:
elif step_name == "ssl_provision":
    _ssl_provision(core_dir, node_yaml)

# СТАЛО:
elif step_name == "ssl_provision":
    _ssl_provision_via_orchestrator(core_dir, node_yaml)
```

**Шаг 2b:** Новая функция `_ssl_provision_via_orchestrator()`:

```python
def _ssl_provision_via_orchestrator(core_dir: str, node_yaml: str) -> None:
    """Provision SSL certs via cert_orchestrator (unified entrypoint).

    Replaces the old _ssl_provision() which had broken S3 credential propagation
    and only handled platform domain. Now delegates to cert_orchestrator for
    ALL domains (platform + projects).
    """
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # _source_secrets_env() is called inside cert_orchestrator.orchestrate_certs()
    # for WEBNAMES_API_KEY. S3 credentials are read directly by s3_ssl_cache.py
    # from os.environ — no subshell needed.

    # Extract ALL domains (platform + all projects, no context filter)
    context = ""  # empty = no filtering, all domains
    domains = _extract_domains(node_yaml, context)

    if not domains:
        logger.warning("[IMP:7][ssl_provision] No domains found in node.yaml — skipping")
        return

    issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # Dynamic import of cert_orchestrator
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cert_orchestrator",
        os.path.join(bootstrap_dir, "cert_orchestrator.py"),
    )
    if spec and spec.loader:
        cert_mod = importlib.util.module_from_spec(spec)
        sys.modules["cert_orchestrator"] = cert_mod
        spec.loader.exec_module(cert_mod)
        cert_result = cert_mod.orchestrate_certs(domains, issue_cert_script, secrets_env)
        logger.info("[IMP:9][ssl_provision] Cert orchestration complete: %s", cert_result.to_dict())
    else:
        logger.warning("[IMP:7][ssl_provision] Cannot load cert_orchestrator.py")
```

**Шаг 2c:** Удалить `_ssl_provision()` полностью (строки 1744-1812, ~70 строк).

**Шаг 2d:** Обновить `_compute_step_hash` (строка 1276):

```python
# БЫЛО:
"ssl_provision": [os.path.join(core_dir, "internal", "bootstrap", "issue-cert.sh")],

# СТАЛО:
"ssl_provision": [
    os.path.join(core_dir, "internal", "bootstrap", "cert_orchestrator.py"),
    os.path.join(core_dir, "internal", "bootstrap", "s3_ssl_cache.py"),
],
```

### 4.2 `cert_orchestrator.py` — обновить сигнатуру `orchestrate_certs()`

Убрать параметр `s3_cache_script` (больше не нужен — s3_ssl_cache.py импортируется напрямую):

```python
# БЫЛО:
def orchestrate_certs(domains: list[str], s3_cache_script: str,
                      issue_cert_script: str, secrets_env: str = "") -> CertResult:

# СТАЛО:
def orchestrate_certs(domains: list[str], issue_cert_script: str,
                      secrets_env: str = "") -> CertResult:
```

### 4.3 `cert_orchestrator.py` — переписать `_try_s3_restore()` на прямой импорт

```python
def _try_s3_restore(domain: str) -> DomainCertResult:
    """Try S3 check + download via s3_ssl_cache (direct import, no subprocess)."""
    s3_bucket = os.environ.get("S3_BUCKET", "")
    if not s3_bucket:
        logger.warning("[IMP:7][cert_orchestrator] S3_BUCKET not set — S3 restore unavailable")
        return DomainCertResult(domain=domain, status="pending", source="s3")
    try:
        if s3_ssl_cache.check_cert(domain, s3_bucket):
            if s3_ssl_cache.download_cert(domain, ..., s3_bucket):
                return DomainCertResult(domain=domain, status="restored", source="s3")
    except Exception as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 operation failed: %s", domain, e)
    return DomainCertResult(domain=domain, status="pending", source="s3")
```

### 4.4 `cert_orchestrator.py` — `_upload_to_s3()` через прямой импорт

```python
def _upload_to_s3(domain: str) -> bool:
    """Upload cert to S3 via s3_ssl_cache (direct import)."""
    s3_bucket = os.environ.get("S3_BUCKET", "")
    if not s3_bucket:
        return False
    try:
        return s3_ssl_cache.upload_cert(domain, CERT_VALIDITY_PATH, "/opt/acme.sh", s3_bucket)
    except Exception as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 upload failed: %s", domain, e)
        return False
```

### 4.5 `cert_orchestrator.py` — upload-on-skip в `_process_single_domain()`

Сигнатура `_process_single_domain()` упрощается (s3_cache_script больше не нужен):

```python
def _process_single_domain(domain: str, issue_cert_script: str) -> DomainCertResult:
    # Step 1: Check disk
    cert_path = os.path.join(CERT_VALIDITY_PATH, domain, "fullchain.pem")
    if os.path.isfile(cert_path) and _is_cert_valid(domain, cert_path):
        _upload_to_s3(domain)  # Always sync to S3
        return DomainCertResult(domain=domain, status="skipped", source="disk_synced")

    # Step 2: Try S3 restore (via direct import)
    s3_result = _try_s3_restore(domain)
    if s3_result.status == "restored":
        return s3_result

    # Step 3: Fall back to issue-cert.sh
    if os.path.isfile(issue_cert_script):
        result = _issue_cert(domain, issue_cert_script)
        if result.status == "issued":
            _upload_to_s3(domain)  # Upload after successful issue
        return result
    ...
```

### 4.6 Обновить `orchestrate_certs()` — убрать s3_cache_script

```python
def orchestrate_certs(domains: list[str], issue_cert_script: str,
                      secrets_env: str = "") -> CertResult:
    # Source secrets for WEBNAMES_API_KEY (DNS-01)
    if secrets_env and os.path.isfile(secrets_env):
        _source_secrets_env(secrets_env)

    results: dict[str, DomainCertResult] = {}
    for domain in domains:
        results[domain] = _process_single_domain(domain, issue_cert_script)
    ...
```

---

## 5. Фаза 3: Гарантированный S3 backup (MEDIUM, ~1 час)

### 5.1 `issue-cert.sh` — `--reloadcmd` + S3 upload (DNS-01, строка 221)

```bash
# БЫЛО:
--reloadcmd "systemctl reload nginx"

# СТАЛО:
--reloadcmd "systemctl reload nginx && python3 '${SCRIPT_DIR}/s3_ssl_cache.py' upload '${domain}'"
```

### 5.2 `issue-cert.sh` — `--reloadcmd` + S3 upload (HTTP-01, строка 286)

Аналогично 5.1.

### 5.3 `issue-cert.sh` — `--renew-hook` для cron renewal

В `_acme_install_cron()`:

```bash
local SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # NEW
# ... после --install-cronjob:
local renew_hook_script="${SCRIPT_DIR}/s3_ssl_cache.py"
if [[ -f "$renew_hook_script" ]]; then
    "$acme_sh" --renew-hook "python3 '${renew_hook_script}' upload \"\$Le_Domain\"" \
        --home "$acme_home" 2>&1 || log_step "acme-cron" "WARN" "renew-hook failed (non-fatal)"
fi
```

---

## 6. File Manifest

| Действие | Файл | Строки | Описание |
|----------|------|--------|----------|
| **NEW** | `core/internal/bootstrap/s3_ssl_cache.py` | ~250 | Python-порт s3-ssl-cache.sh: upload/download/check/bulk-restore через boto3 |
| **REDUCE** | `core/internal/bootstrap/s3-ssl-cache.sh` | 602→~30 | CLI-фасад: парсинг аргументов + вызов python3 s3_ssl_cache.py |
| **MODIFY** | `core/internal/bootstrap/cert_orchestrator.py` | -55/+60 | Прямой импорт s3_ssl_cache, upload-on-skip, убрать s3_cache_script из сигнатур |
| **MODIFY** | `core/internal/bootstrap/lifecycle/state_machine.py` | -70/+40 | Удалить `_ssl_provision()`, добавить `_ssl_provision_via_orchestrator()` |
| **MODIFY** | `core/internal/bootstrap/issue-cert.sh` | +12 | `--reloadcmd` + python3 s3_ssl_cache.py, `--renew-hook`, SCRIPT_DIR |
| **MODIFY** | `core/internal/bootstrap/lifecycle/steps.py` | -2 | Убрать `s3_cache_script` из вызова `orchestrate_certs()` (сигнатура изменилась в §4.2) |
| **MODIFY** | `core/internal/bootstrap/AGENTS.md` | ~20 | Обновить pipeline docs |
| **NEW** | `tests/unit/test_s3_ssl_cache.py` | ~120 | Unit-тесты для s3_ssl_cache.py |
| **NEW** | `tests/unit/test_cert_upload_on_skip.py` | ~60 | Unit-тест: upload вызывается при skip |
| **MODIFY** | `tests/unit/test_cert_orchestrator.py` | +40 | Тесты для upload-on-skip, обновить сигнатуры |
| **MODIFY** | `tests/test_cert_backup_gap.py` | ~15 | `source="disk_synced"` вместо `source="disk"` |
| **MODIFY** | `tests/test_node_lifecycle_static.py` | ~30 | Обновить ассерты: cert_orchestrator вместо inline source |

**Суммарно:** ~370 строк нового кода (s3_ssl_cache.py + тесты), ~572 строк удалено (s3-ssl-cache.sh shell-логика + _ssl_provision + s3_cache_script в steps.py). Net change: ~-202 строк.

---

## 7. План тестирования

### 7.1 Новые unit-тесты

| Тест | Файл | Что проверяет |
|------|------|--------------|
| `test_upload_cert_success` | `tests/unit/test_s3_ssl_cache.py` (NEW) | `upload_cert()` загружает файлы в S3 |
| `test_download_cert_success` | `tests/unit/test_s3_ssl_cache.py` (NEW) | `download_cert()` восстанавливает сертификат |
| `test_check_cert_hit` | `tests/unit/test_s3_ssl_cache.py` (NEW) | `check_cert()` находит валидный сертификат в S3 |
| `test_check_cert_miss` | `tests/unit/test_s3_ssl_cache.py` (NEW) | `check_cert()` возвращает False при отсутствии |
| `test_bulk_restore_parses_yaml` | `tests/unit/test_s3_ssl_cache.py` (NEW) | `bulk_restore()` парсит node.yaml и обрабатывает все домены |
| `test_upload_called_on_skip` | `tests/unit/test_cert_upload_on_skip.py` (NEW) | `_process_single_domain()` вызывает upload при skip |
| `test_upload_called_after_issue` | `tests/unit/test_cert_upload_on_skip.py` (NEW) | После успешного issue → upload вызывается |
| `test_skip_source_is_disk_synced` | `tests/unit/test_cert_orchestrator.py` | `source="disk_synced"` |

### 7.2 Обновление существующих тестов

| Тест | Изменение |
|------|-----------|
| `test_idempotent_skip_valid` | Ассерт: `source="disk_synced"` (было `"disk"`) |
| `test_bulk_restore_all_from_s3` | Без изменений (restore path не меняется) |
| `test_state_machine_full_bootstrap_restore_flow` | Обновить: ожидать `cert_orchestrator` вместо `_ssl_provision` |
| `test_update_ssl_step_sources_secrets_env` | Обновить: ожидать cert_orchestrator вызов вместо inline `set -a` |
| `test_issue_cert_saves_all_4_files_to_s3` | Добавить проверку `--renew-hook` в `_acme_install_cron` |

### 7.3 Верификация

```bash
make test MARKER=static,unit,contract
make gate MODE=fast
```

---

## 8. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|------------|---------|-----------|
| **R1:** `s3_ssl_cache.py` недоступен при импорте на VPS | Низкая | CRITICAL | Файл доставляется через SCP вместе с core/. Путь детерминированный. Добавить в content-hash для verify_core. |
| **R2:** `--renew-hook` с экранированным `$Le_Domain` не раскрывается корректно | Средняя | MEDIUM | Протестировать: `acme.sh --renew-hook 'echo "Domain: $Le_Domain"'`. Проверить что acme.sh подставляет переменную. |
| **R3:** Двойной вызов cert_orchestrator вызывает дублирование операций | Низкая | LOW | Idempotency: второй вызов → skip + upload (cert уже на диске). Upload идемпотентен (перезапись тех же файлов). |
| **R4:** `_extract_domains()` с пустым контекстом возвращает домены из других контекстов | Низкая | MEDIUM | При пустом контексте фильтр `proj_context != context` → `proj_context != ""` → ВСЕ проектные домены проходят. Это корректно для cert orchestration (один LE-сертификат на домен). |
| **R5:** Удаление `_ssl_provision()` ломает `test_state_machine_full_bootstrap_restore_flow` | Высокая | LOW | Тест статический (grep-based) — нужно обновить ассерты. Функция меняет имя, но restore-first логика сохраняется в cert_orchestrator. |
| **R6:** `issue-cert.sh` переменная `SCRIPT_DIR` не определена в `_acme_install_cron()` | Средняя | MEDIUM | Добавить `local SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` в начало функции. |
| **R7:** `boto3` не установлен в окружении cert_orchestrator | Низкая | MEDIUM | boto3 уже используется `upload.py` в backup-cron. Убедиться что s3_ssl_cache.py импортирует boto3 lazily или через try/except. |
| **R8:** ⚠️ TRAP[DEBT] · Дублирование `_extract_domains()` | Низкая | LOW | `_extract_domains()` (state_machine.py:2146) и `_extract_domains_for_context()` (steps.py:961) — почти идентичны. Новый `_ssl_provision_via_orchestrator()` использует state_machine.py версию, deploy_context — steps.py версию. Извлечение общей функции отложено до follow-up рефакторинга (DRIFT-4 из VerificationReport). Причина: логика контекстной фильтрации может разойтись в будущем. |

---

## 9. Rollback Plan

При обнаружении регрессии:

```bash
# 1. Откатить коммит
git revert <merge-commit>

# 2. Восстановить старый pipeline на VPS
make bootstrap-node NODE=<node>  # SCP доставит старую версию core/

# 3. Верифицировать
make node-update NODE=<node>
make healthcheck NODE=<node>
```

**Время восстановления:** ~5 минут (git revert + make bootstrap-node).

---

## 10. Порядок имплементации

```
Фаза 1 (Python-миграция, CRITICAL):
  1.1 Создать s3_ssl_cache.py (NEW, ~250 строк) — порт всей бизнес-логики
  1.2 Редуцировать s3-ssl-cache.sh → CLI-фасад (~30 строк)
  1.3 Обновить cert_orchestrator.py (прямой импорт s3_ssl_cache)
  1.4 Запустить make test MARKER=static,unit → зелёный
  1.5 Коммит: "refactor(ssl): port s3-ssl-cache.sh to Python (s3_ssl_cache.py)"

Фаза 2 (Унификация entrypoint, HIGH):
  2.1 Добавить _upload_to_s3() + upload-on-skip в cert_orchestrator.py
  2.2 Создать _ssl_provision_via_orchestrator() в state_machine.py
  2.3 Удалить _ssl_provision() из state_machine.py
  2.4 Обновить сигнатуру orchestrate_certs() (убрать s3_cache_script)
  2.4b Обновить steps.py — убрать s3_cache_script из вызова orchestrate_certs() (−2 строки)
  2.5 Обновить _compute_step_hash
  2.6 Запустить make test MARKER=static,unit → зелёный
  2.7 Коммит: "refactor(ssl): unify cert lifecycle — cert_orchestrator as single entrypoint"

Фаза 3 (S3 backup гарантия, MEDIUM):
  3.1 issue-cert.sh: --reloadcmd + python3 s3_ssl_cache.py upload
  3.2 issue-cert.sh: --renew-hook в _acme_install_cron()
  3.3 Создать tests/unit/test_s3_ssl_cache.py
  3.4 Создать tests/unit/test_cert_upload_on_skip.py
  3.5 Обновить существующие тесты
  3.6 Обновить core/internal/bootstrap/AGENTS.md
  3.7 Запустить make gate MODE=fast → зелёный
  3.8 Коммит: "feat(ssl): guaranteed S3 backup — upload-on-skip + acme.sh renew-hook"

Финальная верификация:
  make fix-gate && git add -u && make gate MODE=fast
```

---

## Appendix A: Полный контракт `s3_ssl_cache.upload_cert()`

```python
# core/internal/bootstrap/s3_ssl_cache.py
def upload_cert(domain: str, cert_dir: str = "/etc/letsencrypt/live",
                acme_home: str = "/opt/acme.sh", s3_bucket: str = "",
                s3_prefix: str = "platform/ssl-certs") -> bool:
    """Upload cert files to S3: fullchain.pem, privkey.pem, chain.pem (opt), account.tar.gz.

    ## @purpose — Port of s3-ssl-cache.sh _s3_upload(). Uses boto3 directly.
    ##            Reads S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL from os.environ.
    ##            No subshell needed — works in the same Python process as caller.
    ## @io — ⇥ domain, paths, bucket → ⎋ bool
    ## @complexity — O(N) where N = files to upload (~4)
    ## @invariants
    ##   - Non-fatal: returns False on failure, never raises
    ##   - Required files: fullchain.pem, privkey.pem (chain.pem optional)
    ##   - Account data: tar czf acme.sh domain dir → upload to S3
    ##   - Uses boto3 client with retries (max_attempts=3, mode='standard')
    ## @rationale Eliminates inline python3 heredoc in s3-ssl-cache.sh _s3_upload().
    ##           Direct os.environ access fixes credential propagation bug.
    """
```

## Appendix B: Полный контракт `s3_ssl_cache.check_cert()`

```python
def check_cert(domain: str, s3_bucket: str = "",
               s3_prefix: str = "platform/ssl-certs") -> bool:
    """Check if valid cert exists in S3 (>30 days expiry, correct domain, LE issuer).

    ## @purpose — Port of s3-ssl-cache.sh _s3_check(). Downloads fullchain.pem to temp,
    ##            validates with openssl (checkend 2592000s, issuer, domain match).
    ## @io — ⇥ domain, bucket → ⎋ bool
    ## @returns True if valid LE cert >30 days exists in S3
    """
```

$END_DEVPLAN
