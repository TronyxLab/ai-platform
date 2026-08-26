$START_DEVPLAN

# DevPlan 015 — Post-launch Fix Round (F-08 S3-кеш + 5 P2-регрессий сессии 014)

<!-- GREP_SUMMARY: DevPlan 015 post-launch fixes F-08 s3-ssl-cache boto3 lazy-import F-09 status-page healthz F-11 project-lister scan-root F-06 pyright-orphan F-02 pyright-timeout F-07 pyc-invalidation F-10 vhost-domain-test -->
<!-- STRUCTURE: ▶ вердикт-вход 014 → ⊕ 6 находок (1 P1 + 5 P2) → ⚡ Draft Code Graph → ◇ Data Flow → ⎋ $TASKS/$PARALLEL_GROUPS → ⚡ AC/File Manifest/Next Steps -->

## $ARTIFACT_CONTRACT
```yaml
PURPOSE: Закрыть блокирующую регрессию F-08 (S3-кеш сертификатов мёртв на свежей ноде — boto3
         не доставлен/не импортируется) и 5 P2-находок сессии 014, после чего разблокировать
         фазы C–H launch-validation (release-checklist) на tronyx-vps.
DESCRIPTION: F-08 (P1): (a) верифицировать доставку boto3 в φ1 python_deps и починить gap,
         (b) сделать boto3/botocore-импорты lazy в s3_ssl_cache.py + shared/s3_client.py, чтобы
         модуль s3_ssl_cache загружался даже без boto3 и деградировал точным диагнозом вместо
         «module not loaded». F-11: scan-root project_lister → NODE_CONFIGS_DIR. F-09: диагностика
         status-page /healthz 503. F-06/F-02: reaper орфанов basedpyright + changed-files scope.
         F-07-followup: инвалидация .pyc при core-deliver. F-10-test: unit-тест platform_domain.
RATIONALE: Сессия 014 (launch-validation) — вердикт PARTIAL: полный цикл «голое железо → bootstrap →
         converge» ЗЕЛЁНЫЙ после 5 фиксов (коммит fde3fe8), но фазы C–H не достигнуты: C2 (cache
         drill) блокирован F-08 (S3-кеш), D5/G5 — операторские гейты. Пять P2 — деградация
         dev-эргономики (project-list/status), локальной диагностики (pyright-орфан/таймаут),
         честности инкрементальной доставки (.pyc) и покрытия уже-закоммиченного фикса (F-10).
ACCEPTANCE_CRITERIA:
  - AC1 (F-08): `from core.internal.bootstrap import s3_ssl_cache` успешен без boto3 (lazy-import);
         при наличии boto3 — S3 restore-first работает; при отсутствии — точный WARN «boto3 missing»,
         а не «module not loaded». Rerun C2 cache drill на tronyx-vps → restore-first из S3.
  - AC2 (F-11): `make project-list` на dev находит node.yaml (не 0); `make project-status NAME=…`
         резолвит проект offline; remote (NODE=…) не регрессирует.
  - AC3 (F-09): status-page на ноде healthy (healthz 200), причина 503 идентифицирована и устранена.
  - AC4 (F-06/F-02): после timeout pyright-шага в системе 0 орфанов basedpyright; changed-files
         scope укладывается в 120s на dev.
  - AC5 (F-07-followup): core-deliver после rsync инвалидирует stale .pyc на ноде (доставка →
         перекомпиляция с новым кодом).
  - AC6 (F-10-test): unit-тест покрывает цепочку CLI arg > env > node.yaml#domain > None.
  - AC7: `make check` локально зелёный; `make agent-check` exit 0; pre-commit green.
IMPLEMENTS: 02-VerificationReport.md плана 014 (черновик DevPlan, строки 75-86) + 01-Findings.md
         (F-06/F-07-followup/F-08/F-09/F-10/F-11).
IMPACTS: core/internal/bootstrap/{python_deps.py, s3_ssl_cache.py, cert_orchestrator.py},
         core/internal/shared/s3_client.py, core/internal/scaffold/project_lister.py,
         core/internal/check_suite/runner.py (+check-suite.yaml), core/internal/bootstrap/core_deliverer.py,
         core/modules/status-page/*, tests/unit/*.
REQUIRES: Забутстрапленная tronyx-vps (для Wave 3 C2 drill); операторские гейты D5 (GitHub Billing
         org TronyxLab) и G5/H1 (test-VPS) — BLOCKED вне кода; evidence-логи /tmp/bootstrap_b2b.log.
```

## 1. Requirements Analysis

**Вход (evidence, сессия 014, коммит `fde3fe8`):**

| # | Severity | Находка | Блокирует |
|---|----------|---------|-----------|
| F-08 | P1 | `s3_ssl_cache module not loaded — S3 restore unavailable` для всех 4 доменов; boto3 не импортируется в интерпретаторе cert_orchestrator; F-019 из 011 НЕ закрыт для свежей ноды | C2 (cache drill), D/F (бэкап-cert) |
| F-11 | P2 | `make project-list`/`project-status` на dev — «Found 0 node.yaml file(s)»: scan-root резолвится в repo-root, не NODE_CONFIGS_DIR | B5 dev-эргономика |
| F-09 | P2 | status-page `Up (unhealthy)` — healthz 503 (единственный нездоровый из 22) | healthcheck-чистота ноды |
| F-06 | P2 | Утёкший basedpyright-орфан (209 мин CPU) при timeout check-suite 120s | локальная деградация машины |
| F-02 | P2 | pyright full-repo >120s на dev (известный TRAP[DEBT] + TRAP[DECISION] «keep 120s») | `make check` систематически красный |
| F-07-followup | P2 | Стейл .pyc на живой ноде при инкрементальной доставке (rsync -t) | честность core-deliver |
| F-10-test | P2 | platform_domain-резолв (fix F-10 в `fde3fe8`) не покрыт unit-тестом | регрессионная защита |

**Ключевые критерии успеха:**
1. **F-08 — это не только «доставить boto3», но и «не ронять весь кеш при его отсутствии».** Сейчас
   `s3_ssl_cache.py` имеет ДВА top-level импорта boto3: прямой (`from boto3.exceptions import
   S3UploadFailedError` + `from botocore.exceptions import ClientError`, строки 44-45) и косвенный
   (`from core.internal.shared.s3_client import get_s3_client` → `s3_client.py:31 import boto3` +
   `:32 from botocore.config import Config`). Любой из них роняет `from core.internal.bootstrap import
   s3_ssl_cache` (cert_orchestrator.py:86) → `s3_ssl_cache=None` → весь S3-кеш молча выключен.
2. **F-11** — `project_lister._DEFAULT_PROJECTS_ROOT` (строка 48-51) фолбэчит на repo-root, а glob
   `*/node-configs/*/node.yaml` предполагает layout `<context>/node-configs/<node>/`, тогда как
   канонический dev-layout — `node-configs/<node>/node.yaml` прямо в корне репо (NODE_CONFIGS_DIR из .env).
3. **F-06/F-02** — `check_suite/runner.py` уже использует `run_subprocess_streaming` (killpg на
   таймауте, DevPlan 006 W2), но орфан basedpyright выживает → нужна верификация, достигает ли killpg
   node-воркеров basedpyright, и/или changed-files scope как основное решение.

## 2. SUPERPOSITION (F-08 — ключевое решение)

### Option A: «Только доставка» [score: 6/10]
Починить python_deps так, чтобы boto3 гарантированно ставился в φ1, и всё.
Trade-offs: устраняет симптом, но не защищает от повторения (любой временный сбой boto3 в
интерпретаторе снова молча выключит кеш — тот же класс, что F-019).

### Option B: «Lazy-import + доставка» [score: 9/10] ← RECOMMENDED
Сделать boto3/botocore-импорты lazy в `s3_ssl_cache.py` (и в `shared/s3_client.py`, куда они
просачиваются через `get_s3_client`), чтобы модуль всегда загружался; реальные S3-вызовы
(`check_cert`/`download_cert`/`upload_cert`) деградируют с точным «boto3 missing» WARN и
`return False` (non-fatal — контракт s3_ssl_cache). Плюс верификация/фикс доставки в φ1.
Trade-offs: чуть больше правок (2 файла), но устраняет класс багов «модуль не загрузился →
кеш выключен» раз и навсегда.

### Option C: «Убрать boto3 из s3_ssl_cache целиком» [score: 4/10]
Переписать S3-кеш на HTTP-API MinIO/S3 (SigV4 без boto3, как loadtest/s3.py).
Trade-offs: устраняет зависимость, но огромный churn (переписать check/download/upload с
multi-part, retries, ошибками) — несоразмерно для фикса.

**Collapse:** Option B (автономный коллапс; переопределить можно до старта Wave 1).

## 3. Architecture Overview — Draft Code Graph

```
core/internal/bootstrap/
├── s3_ssl_cache.py            ← EDIT: lazy-import boto3/botocore (строки 44-45 → внутрь функций);
│                                тип S3UploadFailedError/ClientError через локальные import-блоки
├── python_deps.py             ← VERIFY+EDIT: доставка boto3 в φ1 (path-fix/маркер уже от 012 T2);
│                                если gap — импорт-probe/переустановка при marker-match без boto3
├── cert_orchestrator.py       ← EDIT (малый): точный диагноз «boto3 missing» vs «module not loaded»
│                                (опционально: probe import boto3 перед предупреждением)
└── core_deliverer.py          ← EDIT (F-07): post-rsync инвалидация __pycache__ на ноде

core/internal/shared/
└── s3_client.py               ← EDIT: lazy import boto3 + botocore.config (строки 31-32 → внутрь get_s3_client)

core/internal/scaffold/
└── project_lister.py          ← EDIT (F-11): find_node_yaml_files scan-root → NODE_CONFIGS_DIR
                                  (pattern `*/node.yaml` + backward-compat `*/node-configs/*/node.yaml`)

core/internal/check_suite/
├── runner.py                  ← EDIT (F-06): верификация killpg / process-tree reaper для basedpyright
└── check-suite.yaml           ← EDIT (F-02): pyright шаг → changed-files scope (если решено)

core/modules/status-page/      ← DIAGNOSE (F-09): /healthz 503 — reason из readiness_check
tests/unit/
├── test_s3_ssl_cache.py       ← + lazy-import тест (импорт без boto3 → graceful)
├── test_project_lister.py     ← + scan-root NODE_CONFIGS_DIR тест
├── test_vhost_cli.py (или test_vhost_renderer.py) ← + platform_domain fallback тест (F-10-test)
└── test_core_deliverer.py     ← + .pyc invalidation тест (если применимо)
```

**Инвариант потока:** ни одна правка не трогает канонический `s3_ssl_cache`-контракт
(non-fatal, return bool, never raise); F-11/F-09/F-06/F-02/F-07/F-10 — независимые файлы,
без общих модулей (параллелизация без конфликтов).

## 4. Data Flow

### F-08 (S3-кеш после фикса)
```
▶ φ1 python_deps ensure (boto3 в /usr/local/bin/python3 3.14) → ⚡ φ7 cert_orchestrator:
  from core.internal.bootstrap import s3_ssl_cache → s3_ssl_cache ЗАГРУЖЕН всегда (lazy boto3)
→ ◇ try_s3_restore: _get_s3_client() → shared get_s3_client → lazy import boto3
  → ⚡ boto3 есть? → S3 check_cert/download_cert (restore-first) │ нет? → WARN «boto3 missing» + return False
→ ◇ fallback issue_cert → ⊕ upload_cert (тот же lazy-путь) → ⎋ DomainCertResult
```

### F-11 (scan-root)
```
▶ main() → projects_root = NODE_CONFIGS_DIR (env) │ fallback <repo>/node-configs
→ ⚡ find_node_yaml_files: glob `*/node.yaml` (dev/bare-NODE) ∪ `*/node-configs/*/node.yaml` (multi-context)
→ ◇ NodeYaml.get_projects → ⊕ table|json → ⎋ список проектов (не 0)
```

### F-06/F-02 (pyright)
```
▶ run_cmd pyright (timeout 120) → ◇ timeout → killpg (run_subprocess_streaming)
→ ⚡ REAPER: если basedpyright-воркеры выживают → process-tree kill (psutil/recursive killpg)
→ ◇ F-02: pyright = changed-files scope (быстрый) + full-repo опционально → ⎋ 0 орфанов, <120s
```

## 5. $TASKS

| ID | Артефакт | Владелец | Зависимости | Complexity |
|----|----------|----------|-------------|------------|
| TASK-1 | F-08: lazy-import boto3 + верификация доставки φ1 | Coder | — | 5 |
| TASK-2 | F-11: project_lister scan-root → NODE_CONFIGS_DIR | Coder | — | 3 |
| TASK-3 | F-09: диагностика status-page /healthz 503 + фикс | Coder | — | 3 |
| TASK-4 | F-06/F-02: pyright reaper + changed-files scope | Coder | — | 4 |
| TASK-5 | F-07-followup: .pyc инвалидация core-deliver | Coder | — | 2 |
| TASK-6 | F-10-test: unit-тест platform_domain fallback | Coder | — | 2 |

Critical path: TASK-1 (P1-блокер C2). TASK-2..6 независимы (нет общих файлов).

**TASK-1 — F-08 (P1), lazy-import boto3 + доставка**
- VERIFY перед реализацией: прочитать evidence `/tmp/bootstrap_b2b.log` (строки 133/164/181/197) и
  определить, boto3 отсутствует в интерпретаторе ИЛИ импортируется под другим python. Проверить
  `/opt/platform/core` на ноде: `python3 -c "import boto3; print(boto3.__version__)"` через `make
  healthcheck`/SSH. Сравнить `_resolve_python_bin` (python_deps) и интерпретатор, которым запускается
  cert_orchestrator (deploy-modules.sh/issue_cert.py).
- Правка A (robustness, основной фикс): `s3_ssl_cache.py` — перенести `from boto3.exceptions import
  S3UploadFailedError` (строка 44) и `from botocore.exceptions import ClientError` (строка 45) внутрь
  функций, где они используются (catch-ветки download/check/upload); заменить на локальные
  `import` внутри try/except-блоков или на `except Exception` с проверкой класса-имени (сохранить
  non-fatal контракт). `shared/s3_client.py` — перенести `import boto3` (строка 31) и
  `from botocore.config import Config` (строка 32) внутрь `get_s3_client` (ленивый импорт);
  аннотация `-> boto3.client` под `from __future__ import annotations` остаётся строкой (не вычисляется).
- Правка B (доставка): если VERIFY показал, что boto3 не ставится в φ1 — починить `python_deps.py`
  (path-fix/marker/import-probe). НЕ менять канон requirements.txt (GENERATED).
- Правка C (диагноз): в `cert_orchestrator.py` — если `s3_ssl_cache is None`, попытаться
  `import boto3` и выдать точный WARN «boto3 missing — install via python_deps ensure» вместо
  «module not loaded». Контракт non-fatal сохранить.
- Acceptance: `python3 -c "from core.internal.bootstrap import s3_ssl_cache"` в окружении БЕЗ boto3
  (venv/env -i) → модуль грузится; `check_cert(...)` → WARN + `return False` (не raise). `make check
  TEST_FILE=tests/unit/test_s3_ssl_cache.py` green. `make agent-check` exit 0.

**TASK-2 — F-11 (P2), scan-root**
- `project_lister.py::find_node_yaml_files` (строки 90-106): резолвить scan-root из `NODE_CONFIGS_DIR`
  (env) → fallback `<repo>/node-configs` → fallback текущий `projects_root`. Паттерны:
  `*/node.yaml` (прямые дети scan-root) ∪ backward-compat `*/node-configs/*/node.yaml` (multi-context).
  `_DEFAULT_PROJECTS_ROOT` оставить как fallback для PROJECTS_BASE-режима.
- `find_project_node` (строки 217-278) использует тот же `find_node_yaml_files` — регрессия не нужна.
- Acceptance: `make project-list` на dev выводит ≥1 project; `make project-status NAME=<proj>` (offline)
  резолвит; unit-тест `test_project_lister.py` с tmp_path (NODE_CONFIGS_DIR-layout) green.

**TASK-3 — F-09 (P2), status-page healthz**
- Диагностика первым шагом: на ноде `docker logs status-page --tail 200` + `docker exec status-page
  wget -q -O - http://127.0.0.1:8080/healthz` → определить reason из readiness_check
  (`metrics_file_missing` | `metrics_file_unreadable` | `metrics_schema_stale` | `stale_data`).
- Гипотеза: `status-metrics.json` отсутствует/протух на mount-пути `STATUS_METRICS_JSON`
  (cron `/etc/cron.d/platform-metrics` ещё не отработал на свежей ноде, или путь в base.yml не
  совпадает с фактическим — F-009 план 011, комментарий base.yml:60).
- Fix: если `metrics_file_missing` — обеспечить генерацию файла (запуск metrics-крон/`make dev-metrics`
  эквивалент на ноде) ИЛИ поправить mount-путь; если `stale_data` — проверить свежесть генератора.
- Acceptance: `docker inspect status-page` → healthy; `/healthz` → 200 PASS; `make healthcheck NODE=…`
  зелёный (22/22 healthy).

**TASK-4 — F-06/F-02 (P2), pyright reaper + scope**
- F-06: верифицировать, что `run_subprocess_streaming` (shared/subprocess_io) killpg достигает
  node-воркеров basedpyright; если орфаны выживают — добавить process-tree reaper (psutil
  рекурсивный kill / `pkill -P` по цепочке) в runner.py run_cmd при timeout, либо переключить
  pyright-шаг на `timeout --kill-after=KILL --foreground`-семантику.
- F-02: перевести pyright-шаг check-suite на changed-files scope (pyright только изменённые файлы,
  как уже делается в agent-check/check-diff), full-repo — отдельный опциональный шаг/режим.
  Обновить TRAP[DECISION] (Rev-условие выполнено: full-repo >120s подтверждено на dev).
- Acceptance: после timeout pyright `ps aux | grep basedpyright` → 0 орфанов; `make check` pyright-шаг
  укладывается в 120s на dev; unit-тест reaper (если добавлен) green.

**TASK-5 — F-07-followup (P2), .pyc invalidation**
- `core_deliverer.py`: после Phase 1/4 (deliver_core rsync) добавить post-rsync шаг — remote
  `find <base>/core -type d -name __pycache__ -exec rm -rf {} +` (или `find -name '*.pyc' -delete`)
  через существующий ssh-хелпер (build_ssh_cmd/remote_executor), чтобы инкрементальная доставка
  всегда перекомпилировала. Guard: только при non-dry-run; сухо-режим печатает команду.
- Acceptance: unit-тест (runner-DI) — вызов deliver_core добавляет invalidate-шаг; на ноде после
  core-deliver `.pyc` пересоздаются с новым mtime (проверка Wave 3).

**TASK-6 — F-10-test (P2), unit-тест platform_domain**
- `vhost_cli.py::main` (строки 177-194): покрыть цепочку резолва CLI arg > env `PLATFORM_DOMAIN` >
  node.yaml#domain (top-level `get("domain")`) > None. Тест через tmp_path с фикстурным node.yaml.
- Acceptance: `make check TEST_FILE=tests/unit/test_vhost_cli.py` (или подходящий существующий файл)
  green; 3 кейса резолва + fallback None.

## 6. $PARALLEL_GROUPS

### Wave 1 (P1-блокер)
- Tasks: TASK-1
- Command: `coder Read .ai/plans/015-post-launch-fixes/01-DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (независимые P2, без общих файлов)
- Tasks: TASK-2, TASK-3, TASK-4, TASK-5, TASK-6
- Command: `coder Read .ai/plans/015-post-launch-fixes/01-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5, TASK-6`

### Wave 3 (runtime verification, операторское окно)
- Не код: rerun C2 cache drill (F-08 AC1) + status-page healthcheck (F-09) + core-deliver .pyc
  (F-07) на tronyx-vps, затем фазы C–H по release-checklist (с операторскими гейтами D5/G5).

## 7. Design Decisions

## @rationale Q: Почему F-08 — lazy-import, а не только «доставить boto3»? A: Оба top-level импорта
boto3 (прямой + косвенный через shared/s3_client) превращают отсутствие одной зависимости в молчаливое
выключение ВСЕГО S3-кеша — тот же класс, что F-019 (marker-match при отсутствующем boto3). Lazy-import
делает модуль грузимым всегда, а S3-вызовы деградируют точным non-fatal диагнозом. Это устраняет класс
«модуль не загрузился → кеш выключен», а не только текущий симптом.

## @rationale Q: Почему scan-root → NODE_CONFIGS_DIR, а не PROJECTS_BASE? A: node.yaml канонически
живёт в `node-configs/<node>/node.yaml` (локально — корень репо, remote — /opt/node-configs). Текущий
glob `*/node-configs/*/node.yaml` кодирует НЕ-каноничный layout `<context>/node-configs/`. Резолв от
NODE_CONFIGS_DIR (env, уже задан в .env) + backward-compat паттерн покрывает и dev/bare-NODE, и
multi-context без регрессии.

## @rationale Q: Почему F-02 — changed-files scope, а не подъём таймаута? A: TRAP[DECISION] (check-suite
:137-142) уже зафиксировал «keep 120s» с Rev-условием «если full-repo стабильно >120s И на CI — поднять
до 180 ИЛИ changed-files». F-02-verify подтвердил full-repo >120s в изоляции. Changed-files scope дешевле
(совпадает с agent-check/check-diff), не маскирует медленный full-scan, а full-repo остаётся опциональным.

## @rationale Q: Почему F-06 — reaper, а не только доверие killpg? A: runner.py уже использует
run_subprocess_streaming (start_new_session + killpg), но basedpyright (node) порождает воркеры, которые
могут уйти из process-group. Орфан на 209 мин CPU — прямое доказательство, что killpg недостаточен.
Reaper/process-tree kill закрывает дыру, не отменяя killpg.

## 8. Debt Intake

| Источник | Классификация | Решение |
|----------|---------------|---------|
| D5 (GitHub Billing org TronyxLab) | BLOCKED (владелец) | Вне кода; после оплаты — rerun D (deploy-context, 5 проектов awaiting CI payload) |
| G5/H1 (test-VPS недоступна) | BLOCKED (владелец) | Rerun `make test-node` после восстановления test-VPS |
| G2 (chaos FULL, ночное окно) | DEFER | Отдельное ночное окно (техдолг из 011) |
| B1 NOTE: injection-ветка ci_default без source-фильтра (симметричный хвост F-05) | DEFER | Безвредно (GHCR_PUSH_TOKEN CI-ключ, не потребляется на ноде); при следующем касании decrypt_secrets — source=sops фильтр для injection |
| F-10 (cert coverage botanika/roadmap «NO cert coverage») | DEFER → Wave 3 | Проверка live/-каталога + coverage-детектор на ноде (не код-фикс) |

## 9. Change Impact (cascade)

- **boto3 lazy-import:** `s3_ssl_cache.py` + `shared/s3_client.py` — потребители: cert_orchestrator,
  preflight (probe_s3_connectivity). Контракт non-fatal/return-bool не меняется; каскада на
  backup-cron (upload/retention — отдельный домен, не трогается) нет.
- **project_lister scan-root:** только `core/internal/scaffold/project_lister.py`; фасад
  `project-list.sh` не меняется. Make-глаголы/entrypoint-manifest не затрагиваются.
- **pyright scope/timeout:** `check-suite.yaml` (SoT) + `runner.py`; TRAP[DECISION] обновить.
- **.pyc invalidation:** `core_deliverer.py` (Core-канал); parity с CI core-deploy не нарушается
  (инвалидация — добавка после rsync, не замена).
- **status-page:** диагностика → при фиксе только status-page файлы; платформенный канон не меняется.
- Гейт-контур: F-10-test — новый unit-тест (без requires_node); requires_node не добавляется.

## 10. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_s3_ssl_cache.py | test_module_imports_without_boto3 | `import s3_ssl_cache` без boto3 → грузится; check_cert → WARN + False (не raise) | core/internal/bootstrap/s3_ssl_cache.py |
| tests/unit/test_s3_ssl_cache.py | test_get_s3_client_lazy_boto3 | `get_s3_client` без boto3 → non-fatal False/диагноз, не ImportError на импорте | core/internal/shared/s3_client.py |
| tests/unit/test_project_lister.py | test_find_node_yaml_files_node_configs_dir | scan-root NODE_CONFIGS_DIR-layout → ≥1 node.yaml | core/internal/scaffold/project_lister.py |
| tests/unit/test_vhost_cli.py | test_platform_domain_resolution_fallback | CLI arg > env > node.yaml#domain > None | core/internal/scaffold/vhost_cli.py |
| tests/unit/test_core_deliverer.py | test_deliver_core_invalidates_pyc | deliver_core (runner-DI) → invalidate-шаг в команде | core/internal/bootstrap/core_deliverer.py |
| tests/unit/test_check_suite_runner.py | test_pyright_timeout_no_orphans | timeout pyright-шага → reaper/process-tree kill | core/internal/check_suite/runner.py |

F-09 (status-page) — диагностика на ноде; unit-тест readiness-коллектора уже существует
(`readiness_check` покрыт) — нового unit-слоя не требуется, если причина runtime (cron/mount).

## 11. Acceptance Criteria (summary)

| AC | Проверка |
|----|----------|
| AC1 | `from core.internal.bootstrap import s3_ssl_cache` успешен без boto3; C2 cache drill restore-first PASS |
| AC2 | `make project-list` на dev выводит проекты (не 0); `make project-status NAME=…` резолвит |
| AC3 | status-page healthy (healthz 200) на ноде; `make healthcheck` 22/22 |
| AC4 | 0 орфанов basedpyright после timeout; pyright-шаг <120s на dev |
| AC5 | core-deliver инвалидирует .pyc на ноде |
| AC6 | unit-тест platform_domain (CLI>env>node.yaml>None) green |
| AC7 | `make check` rc=0; `make agent-check` exit 0; pre-commit green |

## 12. File Manifest

| Файл | Операция |
|------|----------|
| core/internal/bootstrap/s3_ssl_cache.py | edit — lazy boto3/botocore импорты |
| core/internal/shared/s3_client.py | edit — lazy boto3 + botocore.config |
| core/internal/bootstrap/cert_orchestrator.py | edit (малый) — точный «boto3 missing» диагноз |
| core/internal/bootstrap/python_deps.py | edit (если gap) — доставка boto3 в φ1 |
| core/internal/scaffold/project_lister.py | edit — scan-root NODE_CONFIGS_DIR |
| core/internal/check_suite/runner.py | edit — reaper орфанов basedpyright |
| core/check-suite.yaml | edit — pyright changed-files scope + TRAP-комментарий |
| core/internal/bootstrap/core_deliverer.py | edit — post-rsync .pyc invalidation |
| core/modules/status-page/* | edit (по итогу диагностики F-09) |
| tests/unit/test_s3_ssl_cache.py | edit — +lazy-import тест |
| tests/unit/test_project_lister.py | edit — +scan-root тест |
| tests/unit/test_vhost_cli.py (или существующий vhost-файл) | edit — +platform_domain тест |
| tests/unit/test_core_deliverer.py | edit — +.pyc invalidation тест |
| tests/unit/test_check_suite_runner.py | edit — +reaper тест |

## Next Steps

### Wave 1
Use coder role and read `.ai/plans/015-post-launch-fixes/01-DevPlan.md`, implement Wave 1: TASK-1.
Перед реализацией: прочитать evidence `/tmp/bootstrap_b2b.log` и `core/internal/bootstrap/s3_ssl_cache.py`
(строки 44-45) + `core/internal/shared/s3_client.py` (строки 31-32). Шаги: `make check TEST_FILE=tests/unit/test_s3_ssl_cache.py` → `make agent-check`.

### Wave 2
Use coder role and read `.ai/plans/015-post-launch-fixes/01-DevPlan.md`, implement Wave 2: TASK-2..TASK-6
(параллельно, нет общих файлов). Шаги: per-task `make check TEST_FILE=...` → финальный `make check` (батч) → `make agent-check`.

### Wave 3 (операторское окно, tronyx-vps)
```
# C2 cache drill (F-08 AC1) + status-page (F-09) + core-deliver .pyc (F-07):
make secrets-unlock NODE=tronyx-vps
make core-deliver NODE=tronyx-vps            # проверка .pyc invalidation
make converge NODE=tronyx-vps                # status-page healthz + healthcheck 22/22
# затем фазы C–H по release-checklist (с гейтами D5/G5):
make e2e-verify NODE=tronyx-vps              # HTTP+TLS sweep
make healthcheck NODE=tronyx-vps
```
Операторские блокеры (D5 GitHub Billing, G5/H1 test-VPS, G2 chaos) — вне кода, фиксируются как
оставшиеся в следующей сессии.

$END_DEVPLAN
