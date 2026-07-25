$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Унификация жизненного цикла SSL-сертификатов — единый restore-first entrypoint через cert_orchestrator, гарантированный S3 backup при любом сценарии (bootstrap, node-update, acme.sh cron renewal), устранение дублирующейся логики _ssl_provision().
DESCRIPTION:           Трёхфазная имплементация: (1) Fix S3 credential propagation в _ssl_provision(), (2) Унификация — замена _ssl_provision() на cert_orchestrator.orchestrate_certs() во всех точках вызова, (3) Гарантированный S3 upload при skip/existing cert + acme.sh --renew-hook для cron renewal.
RATIONALE:             Bug-репорт от 2026-07-25: сертификаты были выпущены и сайты работали, но при повторном bootstrap не восстановились из S3. Корневая причина — двойная: (а) _ssl_provision() не прокидывает S3_* креды в родительский процесс — s3-ssl-cache.sh upload/download молча падают; (б) cert_orchestrator в deploy_context пропускает platform domain (cert на диске) — upload в S3 не вызывается. Как следствие: platform domain cert НИКОГДА не попадает в S3 при нормальном потоке. Дополнительно: дублирование логики между _ssl_provision() и cert_orchestrator противоречит языковой политике (shell-логика в Python-модуле state_machine) и принципу AI-First Architecture (единый typed contract).
ACCEPTANCE_CRITERIA:   1. `make bootstrap-node` — platform domain cert восстанавливается из S3 (если есть) без нового LE-запроса. 2. `make bootstrap-node` — после успешного issue новый cert попадает в S3. 3. `make node-update` — при существующем cert на диске, S3 upload вызывается (кеш не устаревает). 4. acme.sh cron renewal — после renew сертификат автоматически синхронизируется в S3. 5. `make deploy-context` — поведение не меняется (project domain certs обрабатываются как прежде). 6. Все существующие тесты (test_cert_orchestrator.py, test_cert_backup_gap.py, test_ssl_s3_cache.py) проходят. 7. `make gate MODE=fast` — зелёный.
IMPLEMENTS:            Bug-report 2026-07-25 (tronyx-vps — certs not restored from S3), StatusReport 045 (cert orchestration gap), DevPlan 047 (deploy_context cert integration).
IMPACTS:               core/internal/bootstrap/lifecycle/state_machine.py (_ssl_provision → удаление/замена), core/internal/bootstrap/cert_orchestrator.py (upload-on-skip logic), core/internal/bootstrap/issue-cert.sh (добавление --renew-hook), core/internal/bootstrap/s3-ssl-cache.sh (без изменений), core/internal/bootstrap/lifecycle/steps.py (_step_deploy_context — без изменений), tests/unit/test_cert_orchestrator.py (новые тесты), tests/test_cert_backup_gap.py (обновление ассертов).
REQUIRES:              S3 credentials доступны в /run/platform/secrets.env на момент выполнения ssl_provision (уже обеспечивается шагом decrypt-secrets перед ssl_provision в pipeline).
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- PROBLEM: Детальный разбор бага с трассировкой pipeline → PROBLEM_ID
- ROOT_CAUSE: Две корневые причины + architectural debt → ROOT_CAUSE_ID
- SOLUTION: Трёхфазный план имплементации → SOLUTION_ID
- FILE_MANIFEST: Полный список файлов → FILES_ID
- TEST_PLAN: Что и как тестировать → TEST_ID
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

# 01-Brief: Унификация жизненного цикла SSL-сертификатов

**Severity:** CRITICAL (блокирует disaster recovery — при потере VPS сертификаты невосстановимы из S3)
**Created:** 2026-07-25
**Author:** Architect (Kilo)
**Status:** DRAFT — ожидает утверждения перед DevPlan

---

## 1. Проблема

### 1.1 Симптом

Пользователь выполнил деплой на tronyx-vps. Сайты работали, сертификаты были валидны. При следующем `make bootstrap-node` сертификаты должны были восстановиться из S3 (C3), но этого не произошло — потребовался повторный выпуск через Let's Encrypt.

### 1.2 Трассировка Pipeline (доказательство бага)

#### Сценарий: Fresh Bootstrap (init mode)

```
Шаг 12: decrypt-secrets → /run/platform/secrets.env создан
         ├── S3_ACCESS_KEY=<key>
         ├── S3_SECRET_KEY=<secret>
         ├── S3_BUCKET=<bucket>
         └── WEBNAMES_API_KEY=<key>

Шаг 19: node_update → ssl_provision → _ssl_provision()
         ├── source secrets.env в SUBSHELL (set -a; source ...; set +a)
         │   └── ❌ Subshell завершился → S3_* vars УМЕРЛИ
         ├── s3-ssl-cache.sh check → $S3_ACCESS_KEY пуст → FAIL (graceful)
         ├── s3-ssl-cache.sh download → $S3_ACCESS_KEY пуст → FAIL
         ├── issue-cert.sh → выпускает НОВЫЙ LE-сертификат
         │   └── s3-ssl-cache.sh upload → $S3_ACCESS_KEY пуст → FAIL
         └── ⚠️ Platform cert на диске, НО НЕ В S3

Шаг 23: deploy_context → cert_orchestrator.orchestrate_certs()
         ├── _source_secrets_env() → S3_* vars в os.environ ✅
         ├── Platform domain: cert на диске → _is_cert_valid() → SKIP
         │   └── ⚠️ Upload в S3 НЕ вызывается при SKIP
         ├── Project domains: cert нет на диске → S3 check (пусто) → issue → upload ✅
         └── Итог: platform cert ВЕЧНО отсутствует в S3
```

#### Сценарий: Node Update (update mode)

```
Шаг 3: ssl_provision → _ssl_provision()
        ├── source secrets.env в SUBSHELL → ❌ S3_* vars не прокинуты
        ├── s3-ssl-cache.sh check → FAIL (нет кредов)
        ├── cert на диске существует → _is_le_cert() → SKIP
        └── ⚠️ S3 upload не вызывается → кеш устаревает

Шаг 8: deploy_context → cert_orchestrator
        ├── Platform domain: cert на диске → SKIP (upload не вызывается)
        └── ⚠️ S3 НЕ обновляется при продлении через acme.sh cron
```

#### Сценарий: acme.sh Cron Renewal

```
crontab: acme.sh --cron → renew *.tronyx.ru
         ├── Новый сертификат установлен в /etc/letsencrypt/live/
         ├── --reloadcmd "systemctl reload nginx" → nginx перезагружен ✅
         └── ❌ S3 upload НЕ ВЫЗЫВАЕТСЯ → кеш устарел
```

**Следствие:** При повторном bootstrap (после wipe диска или на новой VPS) S3 кеш либо пуст, либо содержит устаревший сертификат. Восстановление невозможно → новый LE-запрос → риск rate-limit (50 certs/domain/week).

### 1.3 Почему это не было обнаружено раньше

- `_ssl_provision()` и `cert_orchestrator` имеют разную логику загрузки S3-кредов — баг маскируется тем, что cert_orchestrator работает корректно
- S3 check/download в `_ssl_provision()` падает **gracefully** (non_fatal=True) — нет видимых ошибок
- Тесты проверяют структурное наличие вызовов (`s3-ssl-cache.sh` должен вызываться), но не проверяют наличие S3-кредов в окружении subprocess
- Wipe-and-restore сценарий не тестировался в production

---

## 2. Корневые причины

### Причина #1 (CRITICAL): S3 credentials не прокидываются в `_ssl_provision()`

**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py:1778-1786`

```python
# ТЕКУЩИЙ КОД (сломан):
_subprocess_run(
    ["bash", "-c",
     f"set -a; source '{secrets_env}'; set +a; ..."],
    "source_secrets_env", non_fatal=True,
)
# ❌ subprocess.run создаёт НОВЫЙ процесс → source внутри него
# ❌ После return — все переменные УМЕРЛИ
# ❌ os.environ родительского процесса НЕ изменён
```

**Правильный паттерн** уже существует в `cert_orchestrator.py:443-459`:

```python
# ПРАВИЛЬНЫЙ ПАТТЕРН (cert_orchestrator._source_secrets_env):
result = subprocess.run(
    ["bash", "-c", f"set -a; source '{secrets_env_path}'; ... env"],
    capture_output=True, text=True
)
for line in result.stdout.splitlines():
    if '=' in line:
        key, _, value = line.partition('=')
        os.environ[key] = value  # ✅ ПЕРСИСТЕНТНО
```

### Причина #2 (HIGH): Platform cert never uploaded to S3

Даже если исправить причину #1, есть **два независимых path**, где cert не попадает в S3:

1. **Skip path:** `cert_orchestrator._process_single_domain()` — если cert существует на диске → `status="skipped"` → upload не вызывается. При последующих вызовах cert_orchestrator (deploy_context) этот domain всегда будет skipped.

2. **Cron renewal path:** `_acme_install_cron()` в `issue-cert.sh` устанавливает `acme.sh --cron` без `--renew-hook`. При renewal через cron новый cert заменяет старый на диске, но S3 не обновляется.

### Причина #3 (MEDIUM): Дублирование cert-логики

Два независимых code path для управления сертификатами:

|                       | `_ssl_provision()` (state_machine.py) | `cert_orchestrator.orchestrate_certs()` |
|-----------------------|---------------------------------------|----------------------------------------|
| Язык                  | Python (внутри state machine)         | Python (отдельный модуль)              |
| S3 creds loading      | ❌ Сломано (subshell)                 | ✅ Правильно (parse env output)        |
| Restore-first          | ⚠️ Структурно есть, но не работает     | ✅ Работает                            |
| Домены                 | Только platform domain                | Все домены (platform + projects)       |
| Idempotency           | _is_le_cert() в issue-cert.sh          | _is_cert_valid() + _is_le_issuer()     |
| S3 upload после issue | В issue-cert.sh (через subshell)      | В issue-cert.sh (через вызов)          |
| Тестируемость          | ❌ Сложно (внутри монолита)            | ✅ Unit-тесты (test_cert_orchestrator) |

Это прямое нарушение:
- **Языковой политики:** новая бизнес-логика должна быть в Python-модулях, а не встроена в оркестратор
- **AI-First Architecture:** дублирование ответственности, отсутствие единого typed contract

---

## 3. Решение (трёхфазное)

### Фаза 1: Fix S3 credential propagation (CRITICAL, ~30 мин)

**Цель:** Починить `_ssl_provision()` чтобы s3-ssl-cache.sh получал S3-креды.

**Изменения:**
1. Заменить блок `source secrets.env` (subshell) на вызов `_source_secrets_env()` — той же функции, что использует cert_orchestrator
2. Вынести `_source_secrets_env()` в общий модуль (например, `core/internal/bootstrap/secrets_loader.py`) чтобы избежать дублирования
3. Либо: добавить импорт `_source_secrets_env` из `cert_orchestrator` в state_machine

**Верификация:** После fix — `s3-ssl-cache.sh check` находит сертификат в S3, `download` восстанавливает на диск.

### Фаза 2: Унификация — cert_orchestrator как единый entrypoint (HIGH, ~3-4 часа)

**Цель:** Удалить `_ssl_provision()`, заменить все вызовы на `cert_orchestrator.orchestrate_certs()`.

**Изменения в `state_machine.py`:**
1. `UPDATE_STEPS`: заменить `"ssl_provision"` на вызов `cert_orchestrator.orchestrate_certs()` для всех доменов
2. `INIT_STEPS`: `deploy_context` (step 23) уже вызывает cert_orchestrator — дублирования не будет, так как ssl_provision (step 19 → update → ssl_provision) теперь тоже проходит через cert_orchestrator с restore-first
3. Удалить функцию `_ssl_provision()` полностью (~70 строк)
4. Удалить `"ssl_provision"` из `REQUIRED_FILES` маппинга или заменить на `cert_orchestrator.py`

**Изменения в cert_orchestrator.py:**
1. Добавить режим `process_all_domains` — принимать `node_yaml` path и извлекать все домены (сейчас это делает `steps._extract_domains_for_context`)
2. Либо: добавить вызов `_extract_domains_for_context` внутри cert_orchestrator

**Pipeline после изменений:**

```
INIT mode:
  ... → install_acme (step 18) → node_update (step 19)
    └─ update mode: ... → cert_orchestrator (restore-first, все домены)
       → deploy_modules → healthcheck → converge → deploy_context
         └─ cert_orchestrator (повторный вызов — все домены skipped если уже на диске)
         └─ context_deployer (проекты)

UPDATE mode:
  verify_core → provision → deliver_overlays
  → cert_orchestrator (restore-first, все домены)  ← БЫЛО: ssl_provision
  → deploy_modules → provision_llm_keys → healthcheck
  → converge → deploy_context
    └─ cert_orchestrator (повторный вызов, upload-on-skip)
    └─ context_deployer
```

### Фаза 3: Гарантированный S3 backup (MEDIUM, ~1-2 часа)

**3a. Upload-on-skip в cert_orchestrator:**

Модифицировать `_process_single_domain()`: если cert существует на диске и это валидный LE cert → **всегда вызывать `s3-ssl-cache.sh upload`** перед return `status="skipped"`.

```python
# В _process_single_domain(), шаг 1:
if os.path.isfile(cert_path) and _is_cert_valid(domain, cert_path):
    # Всегда синхронизируем с S3 — кеш мог устареть после cron renewal
    _upload_to_s3(domain, s3_cache_script)
    return DomainCertResult(domain=domain, status="skipped", source="disk_synced")
```

**3b. acme.sh --renew-hook для S3 синхронизации:**

Модифицировать `issue-cert.sh:221` и `:286` (оба `--install-cert` вызова):

```bash
# БЫЛО:
--reloadcmd "systemctl reload nginx"

# СТАЛО:
--reloadcmd "systemctl reload nginx && /opt/platform/core/internal/bootstrap/s3-ssl-cache.sh upload ${domain}"
```

Для cron renewal (где `--install-cert` уже был вызван при первоначальном issue), нужно также добавить `--renew-hook`:

```bash
# В _acme_install_cron(), после --install-cronjob:
"$acme_sh" --renew-hook "bash ${SCRIPT_DIR}/s3-ssl-cache.sh upload ${domain}" \
    -d "$domain" --home "$acme_home"
```

⚠️ **Важно:** renew-hook получает `Le_Domain` из окружения acme.sh, нужно использовать `$Le_Domain` вместо хардкода.

**3c. Синхронизация при deploy_context:**

`_step_deploy_context()` уже вызывает cert_orchestrator с upload-on-skip — дополнительных изменений не требуется.

---

## 4. File Manifest

| Действие | Файл | Описание |
|----------|------|----------|
| **MODIFY** | `core/internal/bootstrap/lifecycle/state_machine.py` | Удалить `_ssl_provision()` (~70 строк), добавить вызов cert_orchestrator в ssl_provision step, использовать `_source_secrets_env` |
| **MODIFY** | `core/internal/bootstrap/cert_orchestrator.py` | Добавить `_upload_to_s3()`, upload-on-skip логику, опционально — `orchestrate_all_domains(node_yaml)` |
| **MODIFY** | `core/internal/bootstrap/issue-cert.sh` | `--reloadcmd` → добавить S3 upload; `_acme_install_cron` → добавить `--renew-hook` |
| **REFACTOR** | `core/internal/bootstrap/lifecycle/steps.py` | Опционально: перенести `_extract_domains_for_context()` в cert_orchestrator для устранения cross-module зависимости |
| **NEW** | `tests/unit/test_cert_s3_upload_on_skip.py` | Unit-тест: cert существует → upload вызывается |
| **NEW** | `tests/unit/test_cert_renew_hook.py` | Unit-тест: проверка --renew-hook в acme.sh конфигурации |
| **MODIFY** | `tests/unit/test_cert_orchestrator.py` | Добавить тест-кейсы для upload-on-skip |
| **MODIFY** | `tests/test_cert_backup_gap.py` | Обновить ассерты (upload вызывается при skip) |
| **MODIFY** | `tests/test_node_lifecycle_static.py` | Обновить тесты `test_update_ssl_step_sources_secrets_env` — ожидать `_source_secrets_env` вместо inline source |
| **MODIFY** | `core/internal/bootstrap/AGENTS.md` | Обновить документацию pipeline: cert_orchestrator как единый entrypoint |
| **REFERENCE** | `.ai/plans/052-cert-lifecycle-unification/01-Brief.md` | Настоящий бриф |

---

## 5. План тестирования

### 5.1 Unit-тесты

| Тест | Что проверяет |
|------|--------------|
| `test_s3_creds_propagated_to_subprocess` | `_source_secrets_env()` устанавливает S3_* в os.environ |
| `test_upload_called_on_skip` | `_process_single_domain()` вызывает s3-ssl-cache.sh upload когда cert на диске |
| `test_renew_hook_in_acme_config` | `_acme_install_cron()` содержит --renew-hook с s3-ssl-cache.sh |
| `test_orchestrate_certs_restore_first` | Существующий тест — должен продолжать проходить |
| `test_orchestrate_certs_all_domains` | Новый: orchestrate_certs обрабатывает и platform, и project domains |

### 5.2 Интеграционные тесты

| Тест | Что проверяет |
|------|--------------|
| `test_full_bootstrap_s3_restore` | `make bootstrap-node` восстанавливает certs из S3 (мок) |
| `test_full_bootstrap_s3_upload_after_issue` | После успешного issue — cert в S3 |
| `test_node_update_upload_existing_cert` | `make node-update` синхронизирует существующий cert в S3 |

### 5.3 Ручная верификация

1. `make bootstrap-node NODE=tronyx-vps` — проверить что cert восстановлен из S3 (логи: `SSL certificate restored from S3 cache`)
2. `make node-update NODE=tronyx-vps` — проверить S3 upload при существующем cert
3. Проверить acme.sh cron — `crontab -l` содержит renew-hook

---

## 6. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|------------|---------|-----------|
| Изменение порядка шагов в pipeline ломает зависимости | Низкая | HIGH | cert_orchestrator вызывается ДО docker compose up — nginx ещё не запущен, HTTP-01 не блокирован |
| Двойной вызов cert_orchestrator (ssl_provision + deploy_context) вызывает дублирование | Низкая | LOW | Idempotency: второй вызов видит cert на диске → skip + upload |
| `--renew-hook` падает при недоступности S3 | Средняя | LOW | s3-ssl-cache.sh upload — graceful degradation, нефатально |
| cert_orchestrator теперь требует node_yaml для platform domain | Низкая | MEDIUM | node_yaml уже доступен во всех точках вызова |
| Регрессия существующих тестов | Средняя | MEDIUM | Прогнать `make test MARKER=static,unit,contract` перед merge |

---

## 7. Не входит в scope данного брифа

Следующие улучшения **осознанно исключены** из текущего scope для минимизации риска. Могут быть добавлены отдельным брифом при необходимости:

1. **Standalone `make cert-backup` / `make cert-restore`** — ручное управление кешем
2. **CI/CD cert handling при `make deploy`** — выпуск сертификатов для новых проектных доменов
3. **Cert preflight в preflight.py** — предварительная проверка S3 до bootstrap
4. **S3-vs-local cert drift monitoring** — алерты при расхождении
5. **AGE-шифрование сертификатов в S3** — текущие LE-сертификаты публичные (fullchain), шифрование privkey.pem

---

## 8. Approval Gate

- [ ] Architect review: подтверждение архитектурного решения
- [ ] Impact analysis: проверка всех точек вызова `_ssl_provision()` и `orchestrate_certs()`
- [ ] Test coverage plan утверждён
- [ ] Rollback plan: `git revert` + `make bootstrap-node` восстанавливает старый pipeline

$END_BRIEF
