# GREP_SUMMARY: devplan wave2-followups node-yaml-derivation dry-run-contract add-vhost-http2 spool-dir-none ops-secrets
$START_DEVPLAN

# DevPlan — Wave 2 VPS-report followups (не покрытое DevPlan 001)

## $ARTIFACT_CONTRACT
- **PURPOSE:** Закрыть находки отчёта Wave 2 (.ai/plans/003-wave2-node-update/01-StatusReport.md), не покрытые DevPlan 001-bootstrap-systemic-fixes: портирование FIX-001 в репо, контракт --dry-run, устаревший http2-синтаксис в генераторе vhost, WARN-шум spool-dirs, ops-пробелы секретов.
- **DESCRIPTION:** 4 кодовые задачи (T1 NODE_YAML derivation в update-mode; T2 --dry-run в node-lifecycle.sh; T3 add-vhost.sh http2-модернизация; T4 spool_dir: none декларация) + 1 ops-задача (T5 SOPS/node.yaml дополнения, сцепленная с 001-T7).
- **RATIONALE:** Q: почему это отдельный план, а не расширение 001? A: 001 уже реализован в working tree (uncommitted) и закрывает класс «пустые секреты/restart loop»; данный план закрывает независимый класс «контракт entrypoint↔internal + генераторы конфигов». Q: почему FIX-001 обязателен в репо? A: хот-фикс применён только на VPS; следующий rsync core-deploy затрёт его → регресс `make node-update` (инвариант №9 root AGENTS.md: сервер пересоздаваем, сходимость только через код репо).
- **ACCEPTANCE_CRITERIA:** См. §5 — 6 измеримых критериев.
- **IMPLEMENTS:** Запрос владельца «возьми в работу всё, что не покрыто девпланом 001» (2026-07-17) по отчёту Wave 2.
- **IMPACTS:** core/internal/bootstrap/node-lifecycle.sh, core/internal/scaffold/add-vhost.sh, core/internal/bootstrap/deploy-modules.sh, core/schemas/module.schema.json, core/modules/{nginx,redis,platform-secrets}/module.yaml, tests/*.
- **REQUIRES:** Только локальный репозиторий. VPS не трогаем (сходимость через следующий node-update). SOPS — ручной ops-шаг T5.

---

## 1. Requirements Analysis — карта покрытия отчёта Wave 2

| Находка отчёта | Покрытие 001 | Действие |
|----------------|--------------|----------|
| langfuse/ClickHouse auth, hermes image, MINIO secrets, healthcheck not-found/restart-loop | ✅ T1-T5/T7 плана 001 (реализовано в working tree) | нет |
| **FIX-001**: node-update не передавал NODE_YAML | ❌ (в репо только фикс `--node`→`--node-name`) | **T1** |
| **S1 BLOCKED**: `--dry-run` не реализован в node-lifecycle.sh | ❌ | **T2** |
| **WARN nginx**: `listen ... http2` deprecated + `protocol options redefined` | ❌ (репо-конфиги уже `http2 on;`, но add-vhost.sh:178-179 генерирует старый синтаксис) | **T3** |
| **WARN spool-dirs** для nginx/platform-secrets/redis | ❌ | **T4** |
| **WARN secrets**: LITELLM_MASTER_KEY, AWS_ACCESS_KEY_ID/SECRET | ❌ (001-T7 только MINIO+CLICKHOUSE) | **T5 (ops)** |
| **WARN context-repo**: нет repos.platform в node.yaml | ❌ | **T5 (ops)** |
| **S5 PARTIAL**: docker kill не триггерит restart | — | out of scope (§7) |
| conflicting server name «_» на :80 | — | out of scope (§7) |

**Success criteria (ключевые):**
1. `make node-update NODE=x` работает из чистого репо без хот-фиксов на VPS (NODE_YAML выводится автоматически).
2. `make node-update NODE=x DRY_RUN=1` печатает план шагов и завершается exit 0 без мутаций.
3. Генерируемые vhost'ы не содержат deprecated `listen ... http2`.
4. Stateless-модули декларируют `spool_dir: none` → нет WARN-шума; отсутствие декларации по-прежнему WARN (drift-детекция сохранена).

## 2. Design Decisions

### D1 — NODE_YAML derivation: в node-lifecycle.sh update-mode, не в entrypoint
## @rationale Q: почему не повторить VPS-хот-фикс в node-update.sh? A: entrypoint — thin-wrapper (контракт: ≤4 функций, только paths.sh, без логики); в lib/node-resolver.sh уже есть резолвер с candidate-путём `/opt/node-configs/<node>/node.yaml` (VPS fallback) — DRY (Step 1.6). Деривация в update-mode main покрывает ОБОИХ вызывателей (entrypoint и step_14 init-режима) и любой прямой вызов. Fail-fast: неразрешимый NODE_YAML → exit 1 ДО шагов (сейчас деплой падает на шаге 4, а healthcheck молча SKIP'ается). Rejected: (a) derivation в node-update.sh — дублирует резолвер, не покрывает прямые вызовы; (b) оставить как есть + документация — регресс FIX-001 при следующем core-deploy.

### D2 — dry-run: печать плана шагов, exit 0 до мутаций
## @rationale Q: какой объём dry-run достаточен? A: контракт-разрыв (S1 BLOCKED) чинится минимальной честной реализацией: парсер принимает `--dry-run`; после резолва NODE_NAME/NODE_YAML печатается список шагов режима с резолвнутой конфигурацией и exit 0 — ДО mkdir CHECKPOINT_DIR и любых мутаций. Rejected: (a) убрать --dry-run из Makefile/node-update.sh — теряем заявленную в usage/докам возможность, ломаем обратную совместимость CLI; (b) полный «what-if» с диффом состояния — избыточно для CI-сценария.

### D3 — spool_dir: none — явная декларация stateless
## @rationale Q: почему литерал `none`, а не подавление WARN по списку? A: знание «модуль stateless» должно жить в module.yaml (single source of truth метаданных, D4-контракт), а не в hardcoded-списке внутри deploy-modules.sh (это был бы новый дубль знания — Step 1.11). Отсутствие декларации остаётся WARN — детекция дрифта для новых модулей сохраняется. Схема module.schema.json расширяется literal'ом "none". Rejected: (a) hardcoded skip-список в ensure_spool_dirs; (b) снять WARN совсем — теряем drift-детекцию.

### D4 — add-vhost.sh: `listen 443 ssl;` + `http2 on;`
## @rationale Q: почему это код-фикс, а не ops? A: WARN'ы на VPS (`deprecated listen ... http2`, `protocol options redefined for 0.0.0.0:443`) порождены vhost'ами, сгенерированными шаблоном add-vhost.sh; репо-конфиги модуля nginx уже переведены на `http2 on;` (TRAP[DECISION] 2026-07-15 в platform-default.conf). Смешение старого listen-флага и нового директива на одном сокете и даёт «protocol options redefined». Фикс шаблона + регенерация vhost'ов на VPS (T5) закрывает оба WARN.

### Configuration DRY / Knowledge dedup
- Резолв node.yaml: единственный источник — lib/node-resolver.sh (T1 переиспользует, не дублирует).
- Признак stateless: единственный источник — module.yaml `spool_dir: none` (T4).
- http2-синтаксис: единый современный вариант `http2 on;` во всех генераторах и конфигах (T3).

## 3. Data Flow (после фиксов)

```
make node-update NODE=tronyx-vps [DRY_RUN=1]
  └─ node-update.sh --node-name → node-lifecycle.sh --mode update [--dry-run]
       ├─ NODE_NAME обязателен (fail-fast, exit 1)                        (T1)
       ├─ NODE_YAML пуст/нет файла → source lib/node-resolver.sh →
       │    резолв по candidate-путям (вкл. /opt/node-configs/<node>/) →
       │    неразрешимо → [IMP:10] + exit 1                               (T1)
       ├─ --dry-run → печать 6 шагов + резолвнутый конфиг → exit 0        (T2)
       └─ steps 1-6 (verify-core → provision → ssl → docker → system → hc)
            └─ deploy-modules.sh ensure_spool_dirs:
                 spool_dir: none → INFO "stateless (declared)"            (T4)
                 нет декларации → WARN (как раньше)

make new-project → add-vhost.sh → vhost c `listen 443 ssl;` + `http2 on;` (T3)
```

## 4. $TASKS

| ID | Задача | Файлы | Acceptance | Deps | Cx |
|----|--------|-------|-----------|------|----|
| T1 | NODE_YAML derivation + validate в update-mode: в main() ветке `update` (до mkdir CHECKPOINT_DIR): (a) NODE_NAME пуст → [IMP:10] FATAL + exit 1; (b) NODE_YAML пуст или файл отсутствует → source `${CORE_DIR}/lib/node-resolver.sh`, резолв по NODE_NAME (использовать существующую функцию резолвера — прочитать её реальную сигнатуру, Step 1.5), export NODE_YAML; (c) неразрешимо → [IMP:10] FATAL со списком candidate-путей + exit 1. Обновить MODULE_CONTRACT @invariants. TRAP[BUG] у места фикса (Symptom: `make node-update` падал `NODE_YAML not set`; Root: entrypoint не передавал --node-yaml, update-mode не резолвил; Fix: derivation через node-resolver; Prevention: contract-тест флагов). | core/internal/bootstrap/node-lifecycle.sh, tests/test_node_lifecycle_static.py (new) | static: update-ветка содержит вызов node-resolver + fail-fast; `python -m pytest tests/test_node_lifecycle_static.py -s -v` PASS; shellcheck чист | — | 4 |
| T2 | --dry-run в node-lifecycle.sh: (a) case `--dry-run) DRY_RUN_MODE=true; shift ;;` в парсер; (b) в main() обоих режимов: после резолва NODE_NAME/NODE_YAML (для update — после T1-блока), при DRY_RUN_MODE=true — печать `[IMP:9]` плана шагов режима (имена шагов + NODE_NAME + NODE_YAML) и exit 0 ДО mkdir CHECKPOINT_DIR/мутаций; (c) contract-тест: каждый флаг, который node-update.sh форвардит в node-lifecycle.sh, принимается его парсером (парсинг case-веток обоих скриптов). | core/internal/bootstrap/node-lifecycle.sh, tests/test_node_lifecycle_static.py | static-тесты PASS: парсер содержит --dry-run; contract-тест флагов зелёный; dry-run печать стоит до mkdir | T1 (тот же файл) | 3 |
| T3 | add-vhost.sh http2-модернизация: в heredoc-шаблоне `listen 443 ssl http2;`/`listen [::]:443 ssl http2;` → `listen 443 ssl;`/`listen [::]:443 ssl;` + строка `http2 on;`. Расширить tests/test_add_vhost.py: сгенерированный vhost содержит `http2 on;` и НЕ содержит `listen .* http2`. Repo-wide static: `rg 'listen .*ssl http2'` по core/ пуст (вне TRAP-комментариев). | core/internal/scaffold/add-vhost.sh, tests/test_add_vhost.py | `python -m pytest tests/test_add_vhost.py -s -v` PASS; grep deprecated-синтаксиса пуст | — | 2 |
| T4 | spool_dir: none — stateless-декларация: (a) module.schema.json: spool_dir допускает литерал "none" (не ломая path-вариант); (b) deploy-modules.sh ensure_spool_dirs: `spool_path == "none"` → log INFO/SKIP "stateless module (declared spool_dir: none)", без WARN; (c) nginx/module.yaml: закомментированную строку заменить на `spool_dir: none` (rationale-комментарий сохранить); redis/module.yaml и platform-secrets/module.yaml: добавить `spool_dir: none`; (d) расширить tests/unit/test_spool_dir.py: none → нет WARN; отсутствие декларации → WARN сохранён; (e) прогнать gates: test_gate_module_yaml_contract.py, test_volume_spool_consistency.py — при конфликте схемы согласовать. | core/internal/bootstrap/deploy-modules.sh, core/schemas/module.schema.json, core/modules/nginx/module.yaml, core/modules/redis/module.yaml, core/modules/platform-secrets/module.yaml, tests/unit/test_spool_dir.py | `python -m pytest tests/unit/test_spool_dir.py tests/gates/ -s -v` PASS; `make validate` PASS | — | 4 |
| T5 | **OPS (ручной, оператор, сцеплен с 001-T7):** (a) SOPS node-configs/secrets/tronyx-vps.enc.yaml: добавить `LITELLM_MASTER_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (backup-cron) — вместе с MINIO из 001-T7; (b) node.yaml tronyx-vps: добавить `repos.platform` (URL контекстного репо — решение оператора) для закрытия context-repo WARN; (c) на VPS после `make node-update`: регенерировать project-vhost'ы (пересоздать через add-vhost или удалить стейл) → `nginx -t` без deprecated/protocol-options WARN. | node-configs (вне репо), VPS | node-update: 0 WARN про LITELLM/AWS/context-repo; `nginx -t` без http2-deprecation | T1-T4 задеплоены | 2 |

Merge-rule: T1+T2 — один файл, одна кодер-сессия; концептуально различны — оставлены раздельными задачами (@keep_separate).

## 5. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|---------|
| A1 | update-mode без NODE_YAML в env резолвит его из NODE_NAME через node-resolver; неразрешимо → exit 1 до шагов | static-тест + shellcheck |
| A2 | Каждый флаг, форвардимый node-update.sh, принят парсером node-lifecycle.sh (нет «Unknown argument») | contract-тест |
| A3 | `--dry-run` в update-mode: план шагов, exit 0, ни одной мутации (до mkdir) | static-тест |
| A4 | add-vhost.sh генерирует `http2 on;`, deprecated `listen ... http2` отсутствует в core/ | pytest + grep |
| A5 | `spool_dir: none` → INFO, не WARN; модуль без декларации → WARN сохранён | unit-тест |
| A6 | `make validate` и `python -m pytest tests/ -m static_audit -s -v` зелёные после всех правок | локальный прогон |

## 6. File Manifest

| Файл | Действие |
|------|----------|
| core/internal/bootstrap/node-lifecycle.sh | edit (T1 derivation+validate, T2 dry-run) |
| core/internal/scaffold/add-vhost.sh | edit (T3 http2) |
| core/internal/bootstrap/deploy-modules.sh | edit (T4 ensure_spool_dirs) |
| core/schemas/module.schema.json | edit (T4 "none") |
| core/modules/nginx/module.yaml | edit (T4) |
| core/modules/redis/module.yaml | edit (T4) |
| core/modules/platform-secrets/module.yaml | edit (T4) |
| tests/test_node_lifecycle_static.py | new (T1, T2) |
| tests/test_add_vhost.py | extend (T3) |
| tests/unit/test_spool_dir.py | extend (T4) |

## 7. Constraints / Out of scope

- **VPS не трогаем** — сходимость через следующий `make node-update` (T5, после 001-T7).
- **S5 (docker kill не триггерит restart)** — штатная семантика Docker: ручной kill/stop отключает restart-policy `unless-stopped`. Кода нет; поведение задокументировано в StatusReport 003. Действий не требуется.
- **conflicting server name «_» на :80** — by design: platform-http.conf (pre-TLS fallback) + platform-default.conf монтируются одновременно, задокументировано TRAP[BUG] 2026-07-10 в platform-http.conf. Косметический nginx-WARN, принят.
- **`protocol options redefined`** — уходит после T3 + регенерации vhost'ов на VPS (T5c); отдельного кода не требует.
- deploy-modules.sh уже содержит uncommitted-правки DevPlan 001 (T3/T4) — T4 данного плана правит ТОЛЬКО ensure_spool_dirs, не касаясь env_requires gate / image check.
- При добавлении новых тестов при необходимости выполнить `make test-inventory-sync`.

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_node_lifecycle_static.py | test_update_mode_resolves_node_yaml | update-ветка содержит fail-fast NODE_NAME + резолв NODE_YAML через lib/node-resolver.sh до шагов | node-lifecycle |
| tests/test_node_lifecycle_static.py | test_dry_run_flag_accepted | Парсер node-lifecycle.sh содержит case --dry-run; dry-run печать/exit 0 стоит до mkdir CHECKPOINT_DIR | node-lifecycle |
| tests/test_node_lifecycle_static.py | test_entrypoint_flags_contract | Все флаги, форвардимые node-update.sh, присутствуют в case-парсере node-lifecycle.sh | entrypoint↔internal contract |
| tests/test_add_vhost.py | test_vhost_template_http2_directive | Шаблон генерирует `http2 on;`, нет `listen .* http2` | add-vhost |
| tests/unit/test_spool_dir.py | test_spool_dir_none_no_warn | module.yaml c `spool_dir: none` → INFO/SKIP, не WARN | ensure_spool_dirs |
| tests/unit/test_spool_dir.py | test_spool_dir_missing_still_warns | module.yaml без spool_dir/spool_volume → WARN сохранён | ensure_spool_dirs |

Все тесты — маркер `static_audit`, с LDD-телеметрией (IMP:7-10, caplog) по §TESTING.

## 9. $PARALLEL_GROUPS

### Wave 1 (одна кодер-сессия: T1/T2 — общий файл; T3, T4 — независимые, объединены для экономии)
- Tasks: T1, T2, T3, T4
- Command: `coder Read .ai/plans/004-wave2-followups/01-DevPlan.md, implement Wave 1: T1, T2, T3, T4`

### Wave 2 (ручной ops-шаг, оператор — вместе с 001-T7)
- Tasks: T5 — SOPS (LITELLM_MASTER_KEY, AWS creds, MINIO) + repos.platform + `make node-update NODE=tronyx-vps` + регенерация vhost'ов

## Next Steps
### Wave 1
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/004-wave2-followups/01-DevPlan.md, implement Wave 1: T1, T2, T3, T4
### Wave 2 (manual)
Operator: SOPS update (LITELLM_MASTER_KEY, AWS_*, MINIO из 001-T7) → repos.platform в node.yaml → `make node-update NODE=tronyx-vps` → регенерация vhost'ов → `nginx -t` без WARN

$END_DEVPLAN
