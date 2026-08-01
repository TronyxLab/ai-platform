# 03-DevPlan — Бриф B: Dead code sweep + no-op ликвидация

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 9–17 программного брифа 117 — удаление мёртвого кода, no-op таргетов, broken cron-записей, неиспользуемых скриптов и умерших healthcheck/install-интерфейсов.
- DESCRIPTION: 9 задач: (9) `make audit` — перенацелить или удалить, (10) backup-cron: 3 broken cron-записи, (11) rotate-spend-logs.sh + pg-archive-cleanup.sh — dead, (12) watchdog shell-лаунчер — dead, (13) litellm init-multi-db.sh — dead, (14) platform-secrets healthcheck + nginx stale install, (15) yaml_query.py — КОРРЕКЦИЯ (жив, не удалять), (16) node-lifecycle.sh step_* dead definitions, (17) build-local в шаблонах — already absent.
- RATIONALE: Мёртвый код создаёт ложную сложность, замедляет grep-навигацию и порождает неверные ожидания у агентов. Вторая волна после критического брифа A — разгребаем то, что гарантированно не работает, перед ручным тестированием tronyx-vps.
- ACCEPTANCE_CRITERIA:
  - AC-B1: `make audit` либо удалён из всех реестров (entrypoint-manifest, AGENTS.md glossary, canon table), либо перенацелен на Python-аудит (решение D9).
  - AC-B2: backup-cron crontab — 0 broken записей; remove-скрипты удалены; warm-images либо удалён, либо исправлен (docker.sock + CLI).
  - AC-B3: rotate-spend-logs.sh и pg-archive-cleanup.sh удалены (0 references → 0 references).
  - AC-B4: platform-agent-watchdog.sh удалён; .service/.timer — проверен install-механизм, решение по удалению зафиксировано.
  - AC-B5: init-multi-db.sh удалён; docker-compose.test.yml — mount очищен если был.
  - AC-B6: platform-secrets healthcheck.sh: удалён ИЛИ зарегистрирован healthcheck interface; nginx module.yaml: install удалён из interfaces.
  - AC-B7: yaml_query.py НЕ тронут (жив); yaml_read.sh claim «replaced» исправлен.
  - AC-B8: node-lifecycle.sh step_{start,done,skip,warn} удалены (мёртвые определения).
  - AC-B9: `make gate MODE=fast`, `make check-manifests`, dead-code gate зелёные.
- IMPLEMENTS: 117 01-Brief задачи 9–17.
- IMPACTS: core/entrypoints/audit.sh, core/internal/audit/audit.sh, core/lib/audit.sh (НЕ трогать), core/modules/backup-cron/ (crontab + scripts), core/modules/litellm/scripts/, core/modules/postgres/config/, core/modules/hermes-agent/watchdog/, core/modules/platform-secrets/, core/modules/nginx/module.yaml, core/lib/yaml_read.sh, core/internal/bootstrap/node-lifecycle.sh, core/entrypoint-manifest.yaml, AGENTS.md (root + core), core/AGENTS.md.
- REQUIRES: 117 01-Brief (реестр), результаты аудита dead-code от 2026-08-01, зелёный gate после брифа A.

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 15 (LOW) | yaml_query.py — мёртв, «заменён yaml_read.sh» | **ЖИВ**: Makefile:32, helpers.mk:43/99, node-resolver.sh:216. yaml_read.sh — фасад над NodeYaml CLI, а не замена. | **НЕ удалять.** Исправить вводящий в заблуждение claim «replaced by yaml_read.sh» в yaml_read.sh:12. |
| 17 (LOW) | «build-local в комментариях шаблонов» | **0 совпадений** в templates/. Уже вычищен (D3-политика, phantom gate B8). | Задача закрыта без действий — подтверждено отсутствие. |

---

## 1. Технический анализ и решения

### Задача 9 (HIGH) — `make audit` no-op → удалить таргет

**Факты (верифицированы):**
- `core/entrypoints/audit.sh` (17 строк) — тонкий фасад, делегирует `core/internal/audit/audit.sh "$@"`.
- `core/internal/audit/audit.sh` (23 строки) — **backward-compat shim**, no-op при вызове с аргументами. Весь «системный аудит» = загрузка логгер-фасада + информационный echo.
- `core/lib/audit.sh` (83 строки) — **живой audit-логгер**: тонкий фасад над `core.internal.shared.audit_logger` (`write_audit_entry`, JSON-lines, syslog). Используется bootstrap/модулями. Зарегистрирован в manifest (lib секция, 4 потребителя). **НЕ удалять.**
- Реального системного аудита (проверка состояния ноды, df, docker ps, аналитика) **не существует** нигде в кодовой базе.

**Решение D9:** удалить `make audit` полностью из трёх реестров:
1. `core/entrypoints/audit.sh` — удалить файл.
2. `core/internal/audit/audit.sh` — удалить файл.
3. `core/entrypoint-manifest.yaml` — удалить `audit` из `allowed_verbs`.
4. `AGENTS.md` (root) — удалить строку `| ✅ | audit | ...` из глоссария (секция `<!-- GENERATED:START:glossary -->` — после ручного удаления запустить `make generate-agents-md` для перегенерации).
5. `core/AGENTS.md` — удалить строку `| make audit | ...` из canon-таблицы (секция `<!-- GENERATED:START:canon_table -->` — перегенерация через `make generate-agents-md`).
6. `core/lib/audit.sh` — **сохранить**, НЕ трогать (живой логгер).

**Альтернатива (если пользователь решит иначе):** перенацелить `make audit` на реальный Python-аудит (проверка docker ps, df, systemctl, версии пакетов) через `core/internal/shared/audit_logger.py`. Это требует ~100-200 LOC нового кода. **По умолчанию — удаление** (AC5 программы: «ноль новых глаголов/механизмов»).

**Файлы:** audit.sh ×2 (удалить), entrypoint-manifest.yaml, AGENTS.md (root + core), core/AGENTS.md.

**Риск:** LOW. Таргет уже no-op — никто не может на него полагаться.

---

### Задача 10 (HIGH) — backup-cron: 3 broken cron-записи

**Факты (верифицированы):**
- `core/modules/backup-cron/scripts/crontab` (50 строк) — устанавливается в образ через `Dockerfile L74`: `COPY scripts/crontab /etc/cron.d/platform-backup`. Cron работает **внутри контейнера** (CMD `cron -f`).
- Контейнер **не имеет docker CLI и docker.sock не монтируется**:
  - Dockerfile ставит только: `cron, postgresql-client-16, procps, python3, python3-pip, awscli` (L36-57).
  - `docker-compose.base.yml` L75-78: volumes только `backup-spool` и `backup-logs` — **нет** `/var/run/docker.sock`.

**Broken записи:**

| Строка | Время | Команда | Дефект |
|--------|-------|---------|--------|
| L32 | 03:45 | `/usr/local/bin/warm-images.sh` | `docker compose pull` без docker CLI/socket → **silent fail daily** |
| L41 | 05:00 Вс | `/scripts/backup-restore-test.sh` | Скрипт не скопирован в образ (нет COPY), путь `/scripts/` не существует в контейнере → **silent fail weekly** |
| L50 | каждый час | `/opt/platform/core/modules/backup-cron/scripts/disk-monitor.sh` | Host-путь (в контейнере нет `/opt/platform/`), скрипт не скопирован в образ, docker CLI отсутствует → **silent fail hourly** |

**Рабочие записи (5 из 8):** backup-postgres.sh (L26), backup-app-data.sh (L29), backup-cleanup.sh (L35), retention.py (L38) — все скопированы в образ и работают.

**Решение D10:**
- **L41 (restore-test):** удалить строку из crontab + удалить `scripts/backup-restore-test.sh` (110 LOC). Restore-test — функциональность, которая требует отдельного контейнера с доступом к prod-DB; реализация через docker exec в cron-контейнере без docker.sock невозможна.
- **L50 (disk-monitor):** удалить строку из crontab + удалить `scripts/disk-monitor.sh` (69 LOC). Disk monitor по определению — host-level операция; если нужна — вынести в host-cron (вне образа, не в скоупе волны).
- **L32 (warm-images):** удалить строку из crontab + удалить `scripts/warm-images.sh` (94 LOC). Warm-images требует docker.sock + CLI, которых нет в контейнере. Pre-warming образов при деплое уже делается через `docker_compose.py pull` — дублирования не требуется.

**Файлы:** crontab (3 строки), warm-images.sh, backup-restore-test.sh, disk-monitor.sh (удалить).

**Риск:** LOW. Все три записи сегодня молча падают — удаление не меняет наблюдаемое поведение.

---

### Задача 11 (MED) — rotate-spend-logs.sh + pg-archive-cleanup.sh

**Факты (верифицированы):**
- `core/modules/litellm/scripts/rotate-spend-logs.sh` (172 строки) — **0 внешних ссылок**: только self-reference (L2, L21/23/25) и 01-Brief.md:54. Не упоминается в Makefile, entrypoint-manifest, compose-файлах, Dockerfile, crontab, workflow. Контракт «Should be scheduled via crontab» — но нигде не зашедулен.
- `core/modules/postgres/config/pg-archive-cleanup.sh` (168 строк) — **0 внешних ссылок**: только self-reference (L2, L41/42, L158/164) и 01-Brief.md:54. Комментарий «Cron schedule: 0 2 * * *» — но cron-запись не существует ни в backup-cron crontab, ни где-либо ещё.
- Оба скрипта не сломаны (синтаксически верны), но **не подключены к системе** → пользы 0.

**Решение D11:** удалить оба файла. Если функциональность (ротация spend_logs LiteLLM, WAL-archiving PostgreSQL) реально нужна — это отдельная задача с полноценным подключением (новый функционал, нарушает AC5). Пока — мёртвый груз.

**Файлы:** `core/modules/litellm/scripts/rotate-spend-logs.sh`, `core/modules/postgres/config/pg-archive-cleanup.sh` (удалить).

**Риск:** LOW. 0 потребителей = 0 регрессий.

---

### Задача 12 (MED) — watchdog shell-лаунчер

**Факты (верифицированы):**
- `core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh` — 18 LOC shell-лаунчер. **Dead**: DevPlan 075 VR L263: «Shell launcher is NOT deployed to /usr/local/bin/ — the systemd unit now calls Python directly».
- `platform-agent-watchdog.service` L24: `ExecStart=/usr/bin/python3 /opt/platform/core/modules/hermes-agent/watchdog/agent_watchdog.py` — вызывает Python напрямую, без shell-лаунчера.
- `platform-agent-watchdog.timer` — 31 LOC, активен.
- `agent_watchdog.py` — 1048 LOC, **живой** (покрыт тестами `test_agent_watchdog.py`).
- **НЕ проверено (лимит шагов аудита):** устанавливает ли кто-то `.service`/`.timer` на ноду через `deploy-modules.sh` или `module.yaml` hermes-agent. Требуется grep `watchdog` в `core/internal/bootstrap/deploy-modules.sh` и `core/modules/hermes-agent/module.yaml`.

**Решение D12:**
- Удалить `platform-agent-watchdog.sh` — не деплоится, не используется.
- Проверить deploy-механизм `.service`/`.timer`: если устанавливаются через deploy-modules → сохранить; если не устанавливаются → удалить.
- `agent_watchdog.py` — **НЕ трогать** (живой).

**Файлы:** `platform-agent-watchdog.sh` (удалить); `.service`/`.timer` — решение после проверки install-механизма.

**Риск:** LOW для .sh (уже не деплоится). MEDIUM для .service/.timer — зависит от непроверенного install-механизма.

---

### Задача 13 (MED) — litellm init-multi-db.sh

**Факты (верифицированы):**
- `core/modules/litellm/config/postgres-init/init-multi-db.sh` (21 строка) — создаёт БД `litellm` + `langfuse` через psql `\gexec`.
- **0 внешних ссылок**: единственное вхождение вне self-reference — 01-Brief.md:56. Нет COPY в Dockerfile litellm, нет volume-mount в compose-файлах, нет ссылок в Makefile/workflow.
- **НЕ проверено:** `core/modules/litellm/docker-compose.test.yml` — может монтировать `postgres-init/` директорию. Нужно проверить перед удалением.

**Решение D13:** удалить `init-multi-db.sh`. Если `docker-compose.test.yml` монтирует директорию `postgres-init/` — удалить и mount (тестовый compose может использовать стандартный postgres-init механизм).

**Файлы:** `init-multi-db.sh` (удалить), возможно `docker-compose.test.yml` (очистить mount).

**Риск:** LOW (0 production references). Проверить test-compose перед удалением.

---

### Задача 14 (MED) — platform-secrets healthcheck.sh + nginx stale install

**Факты (верифицированы):**

**platform-secrets:**
- `core/modules/platform-secrets/healthcheck.sh` — **существует**, но `module.yaml` interfaces: `[install]` (L34-35) — `healthcheck` не зарегистрирован.
- `install_type: system` (L28) — system-модуль.
- Оркестратор `modules-healthcheck.sh` вызывает `invoke_module_interface <mod> healthcheck` → module-interface.sh:67-71: незарегистрированный интерфейс = **тихий SKIP**.
- Нарушение контракта `core/modules/AGENTS.md`: «System-модули НЕ содержат healthcheck.sh» (т.к. system-модули управляются через install.sh, а не docker healthcheck).

**nginx:**
- `core/modules/nginx/module.yaml:22` — `interfaces: [install]`, но:
  - nginx — `install_type: docker` (L20), а `invoke_module_interface ... install` вызывается **только для system-модулей** (deploy_orchestrator.py:627-628).
  - `nginx/install.sh` **удалён** (dead-code gate enforce-ит).
  - Запись `install` в interfaces — латентный stale (dispatch был бы graceful-skip, но никогда не приходит).
- `core/internal/bootstrap/AGENTS.md:119`: «Примеры: nginx (системная установка)» — **противоречит** `install_type: docker` в module.yaml.

**Решение D14:**
- **platform-secrets healthcheck.sh:** удалить (system-модуль не должен иметь healthcheck.sh). Альтернатива: зарегистрировать `healthcheck` в interfaces, но это изменение контракта — предпочтительно удаление.
- **nginx install interface:** удалить `install` из `interfaces` в module.yaml — docker-модуль никогда не получает install-dispatch.
- **nginx в AGENTS.md bootstrap:** исправить пример «nginx (системная установка)» → «nginx (docker-модуль)».

**Файлы:** `platform-secrets/healthcheck.sh` (удалить), `nginx/module.yaml` (interfaces), `core/internal/bootstrap/AGENTS.md` (пример).

**Риск:** LOW. Оба — мёртвые/недостижимые кодовые пути.

---

### Задача 15 (LOW) — yaml_query.py: КОРРЕКЦИЯ (НЕ удалять)

**Факты (верифицированы — исходный бриф ошибался):**
- `core/internal/scripts/yaml_query.py` (244 строки) — **ЖИВ**. Активно используется:
  - `Makefile:32` — COMPOSE_PROFILES runtime SoT
  - `makefiles/helpers.mk:43` — PLATFORM_DOMAIN fallback
  - `makefiles/helpers.mk:99` — `_get_all_profiles`
  - `core/lib/node-resolver.sh:216` — `--stdin --get`, JSON host map
- `core/lib/yaml_read.sh` — фасад над **NodeYaml CLI** (`python3 -m core.internal.shared.node_yaml`), а НЕ замена yaml_query. Упоминание «old yaml_query.py» в шапке (yaml_read.sh:12) — историческая справка, а не claim о замене.
- yaml_query.py и yaml_read.sh обслуживают **разные домены**: yaml_query = dotted-ключи произвольного YAML/JSON; yaml_read = node.yaml-домен через NodeYaml.

**Решение D15:** **НЕ удалять yaml_query.py.** Исправить только документацию:
- `yaml_read.sh:12` — заменить вводящий в заблуждение текст «old yaml_query.py used `--file ... --get ...` pattern» на «Separate tool: yaml_query.py handles arbitrary YAML dotted-keys; this facade targets node.yaml domain via NodeYaml CLI».

**Файлы:** `core/lib/yaml_read.sh:12` (docfix). Без удалений.

**Риск:** NONE (только комментарий).

---

### Задача 16 (LOW) — node-lifecycle.sh step_* dead definitions

**Факты (верифицированы):**
- `core/internal/bootstrap/node-lifecycle.sh:45-48` — определяет `step_start()`, `step_done()`, `step_skip()`, `step_warn()`.
- **Ни один вызов этих функций не существует** в текущем коде node-lifecycle.sh: main() (L57-77) только делегирует в `lifecycle/cli.py` через `_delegate()`.
- `core/lib/secrets.sh` (L26-29) имеет **собственные stub-определения** step_start/done/skip с guard `declare -f step_start` — secrets.sh работает автономно, не зависит от node-lifecycle.sh.
- STEP=0, STEP_ERRORS=() (L44) — тоже мёртвые (используются только step_* функциями).
- Шапка файла (L5) правильно документирует: «Thin shell facade (<80 LOC) delegating phase execution to lifecycle/cli.py».

**Решение D16:** удалить строки 44-48 (STEP, STEP_ERRORS, step_start, step_done, step_skip, step_warn). Это ~6 строк мёртвого кода. Файл остаётся <80 LOC (чистый фасад).

**Файлы:** `node-lifecycle.sh:44-48` (удалить 5 строк).

**Риск:** LOW. Функции не вызываются ни в одном актуальном code path.

---

### Задача 17 (LOW) — build-local в шаблонах: уже absent

**Факты (верифицированы):**
- grep `build-local` в `templates/` → **0 совпадений**. Строка уже вычищена (DevPlan 116 B8 D3 — phantom gate, D3-политика запрещённых глаголов).
- `build-local` присутствует в `core/entrypoint-manifest.yaml` как запрещённый глагол (секция `forbidden_verbs`).

**Решение D17:** задача закрыта без действий. В DevPlan зафиксировать подтверждение отсутствия.

**Файлы:** нет изменений.

**Риск:** NONE.

---

## 2. Порядок реализации

Фаза 1 — быстрые удаления (нет зависимостей):
1. **D15** (docfix yaml_read.sh) — 1 строка.
2. **D16** (node-lifecycle.sh dead defs) — 5 строк.
3. **D13** (init-multi-db.sh) — после проверки docker-compose.test.yml.

Фаза 2 — удаление файлов:
4. **D11** (rotate-spend-logs.sh + pg-archive-cleanup.sh) — 2 файла.
5. **D12** (watchdog .sh) — после проверки .service/.timer install.
6. **D10** (backup-cron: 3 скрипта + 3 cron-строки).

Фаза 3 — изменения с реестрами:
7. **D9** (`make audit` — удаление из 5 реестров + 2 файла).
8. **D14** (platform-secrets healthcheck + nginx interfaces).

Фаза 4 — верификация:
9. `make generate-manifests && make generate-agents-md` — перегенерация манифестов и AGENTS.md.
10. `make check-dead-code` — должен остаться зелёным (удалённые файлы не в allowlist).
11. `make gate MODE=fast` + `make check-manifests` — зелёные.

---

## 3. Критерии приёмки (повтор из контракта)

- AC-B1: `make audit` удалён из всех реестров ИЛИ перенацелен (решение D9).
- AC-B2: backup-cron crontab — 0 broken записей.
- AC-B3: rotate-spend-logs.sh, pg-archive-cleanup.sh удалены.
- AC-B4: platform-agent-watchdog.sh удалён.
- AC-B5: init-multi-db.sh удалён.
- AC-B6: platform-secrets healthcheck.sh удалён ИЛИ зарегистрирован; nginx install удалён.
- AC-B7: yaml_query.py жив; docfix применён.
- AC-B8: node-lifecycle.sh step_* удалены.
- AC-B9: gate + check-manifests зелёные.

Дополнительно:
- `rg "rotate-spend-logs\|pg-archive-cleanup\|init-multi-db\|platform-agent-watchdog\.sh" core/` — 0 совпадений (кроме документации).
- `make check-dead-code` — зелёный (удалённые файлы не вызывают false-positive: они не в allowlist OR они были в allowlist и удалены из него).

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| D12: .service/.timer watchdog устанавливаются deploy-modules → удаление сломает watchdog на ноде | Проверить grep перед удалением; если живы — удалить только .sh |
| D13: docker-compose.test.yml монтирует postgres-init/ → удаление скрипта сломает тестовый compose | Проверить перед удалением; если монтирует — удалить mount вместе со скриптом |
| D9: удаление `make audit` из glossary/manifest рассинхронизирует generated-секции | Запустить `make generate-agents-md` + `make generate-entrypoint-manifest` после правок |
| D10: удаление warm-images — потеря pre-warming при деплое | Pre-warming уже делается через `docker_compose.py pull` в deploy_orchestrator — дублирования нет |
| Dead-code gate завалится на удалённых файлах (если они в allowlist) | Проверить `tests/gates/test_gate_dead_code.py` allowlist; удалить записи о Removing-файлах |

---

## 5. Оценка

- Изменяемые файлы: ~12 (6 удалений + 6 правок).
- Удаляемые файлы: 7 (audit.sh ×2, warm-images.sh, backup-restore-test.sh, disk-monitor.sh, rotate-spend-logs.sh, pg-archive-cleanup.sh, init-multi-db.sh, platform-agent-watchdog.sh, platform-secrets/healthcheck.sh — итого 9, из них 8 скриптов + 1 healthcheck).
- Строк кода: ~600 строк удалено (суммарно по скриптам), ~10 строк правок.
- Трудозатраты: ~0.25-0.5 дня агент-времени. Размер: STANDARD (9-20 файлов, бизнес-логика отсутствует — только удаление) → только DevPlan.

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 15 | yaml_query.py НЕ удалять | Живой (4 потребителя: Makefile, helpers.mk, node-resolver.sh). Бриф ошибался — claim «replaced by yaml_read.sh» инвертирован. |
| 17 | build-local — без действий | Уже вычищен (phantom gate B8). 0 совпадений в templates/. |
