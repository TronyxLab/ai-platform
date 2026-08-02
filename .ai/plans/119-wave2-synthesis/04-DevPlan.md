# 04-DevPlan — Бриф C: Dead Code, Backup Fix, Debt Registry

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Исправление критического бага backup-cron (Dockerfile не копирует зависимости → retention падает),
                  TRAP-маркировка watchdog-подсистемы (решение пользователя на 120), удаление мёртвого кода
                  (validate_not_verb, дубли тестов), пополнение debt-реестра.
DESCRIPTION:      8 задач (C1–C8). Единственный баг-фикс (C1) в волне 119. Остальные — чистка и документирование.
RATIONALE:        Backup-cron retention падает на импорте — off-site бэкапы не работают. Watchdog требует
                  решения пользователя → только TRAP+реестр. Дубли тестов и мёртвый код — чистка
                  перед миграциями.
ACCEPTANCE_CRITERIA:
  - AC-C-1: `make gate MODE=fast` зелёный
  - AC-C-2: Backup-cron Dockerfile копирует date_parser.py + s3_client.py
  - AC-C-3: Backup-cron образ проходит `docker build` (retention импорт не падает)
  - AC-C-4: 3 TRAP[DEBT] добавлены (watchdog ×3 + letsencrypt)
  - AC-C-5: validate_not_verb удалён, 0 references
  - AC-C-6: test_stub_detection.py и test_shared_ssh_command_parser.py удалены
IMPLEMENTS:       Бриф C из 01-Brief.md (волна 119) — Dead Code, Backup Fix, Debt.
IMPACTS:          core/modules/backup-cron/Dockerfile, core/modules/backup-cron/scripts/backup_config.py,
                  core/modules/backup-cron/scripts/backup-postgres.sh,
                  core/modules/hermes-agent/watchdog/agent_watchdog.py,
                  core/modules/hermes-agent/watchdog/circuit_breaker.py,
                  core/modules/hermes-agent/watchdog/docker_ops.py,
                  core/internal/shared/verbs.py,
                  tests/unit/test_shared_ssh_command_parser.py (удаление),
                  tests/test_stub_detection.py (удаление),
                  tests/gates/test_gate_project_context.py,
                  tests/gates/test_gate_project_env.py,
                  .ai/debt/ (реестр долгов).
REQUIRES:         Результаты аудита 3 (мёртвый код) и аудита 5 (тесты DUP-1, DUP-2).
-->

# DevPlan C — Dead Code, Backup Fix, Debt Registry

## $START_DEVPLAN

### Контекст

Волна 119, бриф C. Третья волна — чистка перед миграциями. Единственный баг-фикс в волне: backup-cron Dockerfile не копирует date_parser.py и s3_client.py, из-за чего retention.py падает на импорте, а upload-цепочка никогда не вызывается.

---

## $TASKS

### TASK-C1: Backup-cron fix — Dockerfile + upload activation

| Поле | Значение |
|------|----------|
| **ID** | C1 |
| **Sev** | HIGH |
| **Сложность** | 5/10 |
| **Файлы** | `backup-cron/Dockerfile`, `backup-cron/scripts/backup_config.py`, `backup-cron/scripts/backup-postgres.sh`, `backup-cron/scripts/retention.py` |
| **Зависимости** | нет |
| **Риск** | MED — изменение Dockerfile (пересборка образа) |

**Описание:**
Три проблемы в backup-cron:
1. Dockerfile COPY's `retention.py` но НЕ `date_parser.py` + `s3_client.py` (retention.py импортирует их) → `ImportError` при cron-запуске retention.
2. `backup_config.py:36` импортирует `core.internal.config` (LINT-EXEMPT), отсутствующий в образе → любой импорт backup_config падает.
3. `upload-s3.sh`/`upload.py` НЕ вызываются ни одной cron-записью — upload-цепочка мёртвая. Off-site бэкапы критичны.

**Шаги:**
1. Dockerfile: добавить `COPY scripts/date_parser.py /usr/local/bin/date_parser.py` и `COPY scripts/s3_client.py /usr/local/bin/s3_client.py`.
2. `backup_config.py`: заменить `from core.internal.config import ...` на чтение env vars или inline defaults. Убрать зависимость от core.internal.
3. `backup-postgres.sh`: после успешного `pg_dumpall` → вызов `upload-s3.sh` (с проверкой exit code, не блокировать при ошибке upload).
4. Проверить, что `retention.py` импортирует date_parser/s3_client из `/usr/local/bin/` (sys.path).
5. R5 negative-тест: `docker build` backup-cron → `docker run ... python3 -c "import retention"` → SUCCESS (было ImportError).

**Acceptance Criteria:**
- AC-C1.1: `grep "date_parser.py\|s3_client.py" backup-cron/Dockerfile` → 2 COPY строки
- AC-C1.2: `grep "core.internal.config" backup-cron/scripts/backup_config.py` → 0
- AC-C1.3: `grep "upload-s3.sh" backup-cron/scripts/backup-postgres.sh` → вызов после дампа
- AC-C1.4: R5 negative-тест: `docker build + import retention` → SUCCESS

---

### TASK-C2: Watchdog TRAP[DEBT] + debt registry

| Поле | Значение |
|------|----------|
| **ID** | C2 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `hermes-agent/watchdog/agent_watchdog.py`, `circuit_breaker.py`, `docker_ops.py`, `.ai/debt/` |
| **Зависимости** | нет |
| **Риск** | LOW — только комментарии и реестр |

**Описание:**
Watchdog-подсистема НЕ доставляется (0 в Dockerfile/compose/systemd/CI), потребители — только тесты и env_requires в module.yaml. Решение об удалении/доставке — на пользователя (волна 120). Минимально: TRAP[DEBT] + запись в debt-реестр.

**Шаги:**
1. Добавить TRAP[DEBT] в начало каждого из 3 файлов:
   ```
   # 📝 TRAP[DEBT] · 2026-08-02 · HI · Watchdog subsystem not delivered
   # · Observed: 0 references in Dockerfile/compose/systemd/CI
   # · Suspected: feature-flag awaiting activation or abandoned prototype
   # · Impact: dead code in repo, tests cover undelivered functionality
   # · When: 119 wave 2 audit — deferred, user decision required
   ```
2. Создать/обновить `.ai/debt/watchdog-undelivered.md` — запись в реестре с полями Status/Rev.
3. НЕ удалять код, НЕ трогать тесты.

**Acceptance Criteria:**
- AC-C2.1: TRAP[DEBT] в agent_watchdog.py, circuit_breaker.py, docker_ops.py
- AC-C2.2: `.ai/debt/watchdog-undelivered.md` создан
- AC-C2.3: `make gate MODE=fast` зелёный (watchdog тесты всё ещё проходят)

---

### TASK-C3: validate_not_verb removal

| Поле | Значение |
|------|----------|
| **ID** | C3 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `shared/verbs.py` |
| **Зависимости** | нет |
| **Риск** | LOW — 0 внешних вызовов |

**Описание:**
`validate_not_verb()` в `shared/verbs.py:69` — 0 внешних вызовов (только определение + region-маркеры). Удалить функцию и region-маркеры.

**Шаги:**
1. Удалить `validate_not_verb()` + region-маркеры FUNC_validate_not_verb.
2. R5 negative-тест: `test_validate_not_verb_removed` — verify что импорт validate_not_verb → ImportError.

**Acceptance Criteria:**
- AC-C3.1: `grep "validate_not_verb" core/internal/shared/verbs.py` → 0
- AC-C3.2: `grep -rn "validate_not_verb" core/ tests/` → 0
- AC-C3.3: R5 negative-тест: импорт удалённой функции → ImportError

---

### TASK-C4: test_stub_detection.py removal

| Поле | Значение |
|------|----------|
| **ID** | C4 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/test_stub_detection.py` (удаление), `tests/tools/sync_inventory.py` (changelog) |
| **Зависимости** | нет |
| **Риск** | LOW — тест тестирует сам себя |

**Описание:**
`test_stub_detection.py` (194 LOC) тестирует сам себя — содержит inline-bash копию `_is_stub`. Продакшн `stub_detection` удалён в 118. Тест не проверяет реальный код.

**Шаги:**
1. Удалить `tests/test_stub_detection.py`.
2. Обновить inventory changelog в `tests/tools/sync_inventory.py` — запись об удалении.
3. R5 negative-тест: `test_gate_stub_detection_imports` — verify что `is_stub_ai_platform_yaml` используется ТОЛЬКО из `shared/stub_detection` (не из теста).

**Acceptance Criteria:**
- AC-C4.1: `tests/test_stub_detection.py` удалён
- AC-C4.2: `pytest tests/ -m "not requires_node"` — 0 упоминаний удалённого файла
- AC-C4.3: R5 negative-тест: gate проверяет, что stub_detection импортируется из shared/

---

### TASK-C5: Duplicate test_shared_ssh_command_parser removal

| Поле | Значение |
|------|----------|
| **ID** | C5 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/unit/test_shared_ssh_command_parser.py` (удаление), `tests/test_ssh_command_parser.py` (канон) |
| **Зависимости** | нет |
| **Риск** | LOW — дубль после 118 D3 |

**Описание:**
`tests/unit/test_shared_ssh_command_parser.py` (299 LOC, 13 записей inventory) — дубль `tests/test_ssh_command_parser.py`. Модуль `ssh_command_parser` переехал в shared/ в 118 D3, старый тест остался.

**Шаги:**
1. Удалить `tests/unit/test_shared_ssh_command_parser.py`.
2. Обновить inventory changelog.
3. R5 negative-тест: `test_gate_ssh_command_parser_single_test` — verify что только ОДИН тестовый файл для ssh_command_parser.

**Acceptance Criteria:**
- AC-C5.1: `tests/unit/test_shared_ssh_command_parser.py` удалён
- AC-C5.2: `pytest tests/test_ssh_command_parser.py` проходит (канонический тест жив)
- AC-C5.3: R5 gate проходит

---

### TASK-C6: Letsencrypt DEBT registration

| Поле | Значение |
|------|----------|
| **ID** | C6 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `cert_orchestrator.py`, `ssl_certs.py`, `issue-cert.sh`, `.ai/debt/` |
| **Зависимости** | нет |
| **Риск** | LOW — только комментарии |

**Описание:**
`/etc/letsencrypt/live` usage в нескольких файлах не зарегистрирован в debt-реестре. Добавить TRAP[DEBT] + запись.

**Шаги:**
1. Добавить TRAP[DEBT] в cert_orchestrator.py (рядом с letsencrypt-путём).
2. Создать `.ai/debt/letsencrypt-path-hardcode.md`.

**Acceptance Criteria:**
- AC-C6.1: TRAP[DEBT] в cert_orchestrator.py
- AC-C6.2: `.ai/debt/letsencrypt-path-hardcode.md` создан

---

### TASK-C7: Doc-drift fixes

| Поле | Значение |
|------|----------|
| **ID** | C7 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | по касанию в других задачах |
| **Зависимости** | C1–C6 (делать вместе с ними) |
| **Риск** | LOW — косметика |

**Описание:**
Устаревшие комментарии C2/D2/D3/E4 из аудита 3. Поправить при касании файлов в задачах C1–C6.

**Acceptance Criteria:**
- AC-C7.1: Устаревшие комментарии поправлены в затронутых файлах

---

### TASK-C8: Always-skip tests → inversions

| Поле | Значение |
|------|----------|
| **ID** | C8 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/gates/test_gate_project_context.py`, `tests/gates/test_gate_project_env.py` |
| **Зависимости** | нет |
| **Риск** | LOW — изменение skip-логики |

**Описание:**
`test_gate_project_context.py:53` и `test_gate_project_env.py:98` — always-skip (условие `projects/ не существует` всегда истинно в CI/локально без проектов). По R4: отсутствие тестового окружения → FAIL, не skip.

**Шаги:**
1. Инвертировать skip → FAIL: если projects/ не существует — тест FAIL с сообщением «тестовое окружение не настроено».
2. ИЛИ: добавить фикстуру, создающую тестовый project/ с валидным ai-platform.yaml.
3. R5: тест реально запускается и проверяет контекст.

**Acceptance Criteria:**
- AC-C8.1: `test_gate_project_context.py` не skip (проходит или fail с понятной причиной)
- AC-C8.2: `test_gate_project_env.py` не skip
- AC-C8.3: `pytest tests/gates/ -k "project_context or project_env"` → не SKIP

---

## $PARALLEL_GROUPS

### Wave 1 (независимые)
```
coder Read .ai/plans/119-wave2-synthesis/04-DevPlan.md, implement Wave 1: C1, C2, C3, C4, C5, C6, C8
```

C7 (doc-drift) делается вместе с другими задачами при касании файлов.

**Файловые пересечения:**
- C1 затрагивает backup-cron/ — уникально
- C2 затрагивает watchdog/ — уникально
- C3 затрагивает shared/verbs.py — уникально
- C4 затрагивает tests/test_stub_detection.py — уникально
- C5 затрагивает tests/unit/test_shared_ssh_command_parser.py — уникально
- C6 затрагивает cert_orchestrator.py — уникально
- C8 затрагивает tests/gates/test_gate_project_*.py — уникально

Все задачи можно параллелить.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_backup_cron_dockerfile.py` | `test_retention_import_in_container_negative` | R5: docker build + import retention → SUCCESS | backup-cron Dockerfile |
| `tests/unit/test_backup_cron_dockerfile.py` | `test_upload_called_after_dump` | upload-s3.sh вызывается после pg_dumpall | backup-postgres.sh |
| `tests/unit/test_verbs.py` | `test_validate_not_verb_removed_negative` | R5: импорт validate_not_verb → ImportError | shared/verbs |
| `tests/gates/test_gate_stub_detection_sole.py` | `test_stub_detection_sole_import_negative` | R5: is_stub только из shared/stub_detection | stub_detection gate |
| `tests/gates/test_gate_ssh_command_parser_sole.py` | `test_single_test_file_negative` | R5: только один тестовый файл ssh_command_parser | test inventory gate |
| `tests/gates/test_gate_project_context.py` | `test_project_context_valid` | Валидный project context (не skip) | project context gate |
| `tests/gates/test_gate_project_env.py` | `test_project_env_valid` | Валидный project env (не skip) | project env gate |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-C-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-C-BACKUP | Backup-cron образ собирается, retention импорт работает |
| AC-C-DEBT | 4 TRAP[DEBT] добавлены (watchdog×3 + letsencrypt) |
| AC-C-REMOVED | validate_not_verb, test_stub_detection.py, test_shared_ssh_command_parser.py удалены |
| AC-C-R5 | Каждая задача удаления имеет R5 negative-тест |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/04-DevPlan.md, implement Wave 1: C1, C2, C3, C4, C5, C6, C8
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
