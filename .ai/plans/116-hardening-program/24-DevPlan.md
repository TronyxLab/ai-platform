# 24-DevPlan — B11: Enforcement-гейты и процесс (U-09/10/45/51/57/58/79/80/82-88)

<!-- GREP_SUMMARY: enforcement-gates cross-layer dotted-imports python3-m audit-jsonl glossary-generator workflow-trigger debt-freshness ghost-refs test-inventory rename-detection superseded -->
<!-- STRUCTURE: ┌решения D1-D5┐ → ◇ T1 cross-layer gate → ◇ T2 audit-консолидация → ◇ T3 глоссарий-G4 → ◇ T4 workflow-триггеры → ◇ T5 гигиена/ghost → ◇ T6 inventory → ◇ T7 реестр долга → ◇ T8 процесс/TRAP → ◇ T9 манифесты → ⊕ T10 самоверификация → ⎋ риски/AC -->
# region MODULE_CONTRACT
## @purpose  Волна B11 программы хардненинга (116): закрыть enforcement-инфраструктуру и процессные дыры — дрейф не возвращается после программы (U-09/10/45/51/57/58/79/80/82-88).
## @scope    tests/test_cross_layer_imports.py, shared/audit_logger.py, deploy/audit_logger.py (удаляется), lifecycle/helpers/reporting.py, core/internal/scripts/generate_agents_md.py (G4-расширение), AGENTS.md (root глоссарий + TRAP), .github/workflows/{platform-gate-fast.yml (новый), core-deploy, build-platform, mirror, platform-test}, .ai/debt/001-Strangler-Fig-Closeout.md, tests/gates/{test_gate_audit_format.py (новый), test_gate_debt_registry.py}, tests/test_inventory.yaml, .kilo/rules/_project.md.
## @invariants
##   1. Enforcement: гейты с allowlist (канон 2026-07-31, D-решение); allowlist сжимается до нуля и не растёт; НОВЫЕ нарушения → RED.
##   2. Комментарии-инварианты и ручные списки заменяются генерацией (глоссарий из allowed_verbs — 0 ручных правок).
##   3. Реестр долга — живой SoT: каждая запись имеет status + rev-date (дата | условие); stale-пункты (конкретная дата > 90 дней) невозможны (гейт свежести).
##   4. Audit: единый writer (shared/audit_logger), единый JSONL-файл, единая схема (D1).
##   5. Downstream-деплой не зависит от full-gate platform-test (D2) — отдельный лёгкий gate-workflow.
## @rationale Бриф 12-Brief фиксирует цели (U-09..U-88); DevPlan фиксирует решения пользователя (D1-D5, 2026-08-01) и исполнительные шаги с точными файлами. Подтверждённые факты: cross-layer гейт слеп (36 passed при 5-6 реальных нарушениях — dotted-импорты и python3 -m не детектируются: _looks_like_path не видит dotted-names, scan_sh_file без паттерна python3 -m); audit-раскол: 3 writer'а (shared → audit.jsonl JSON ts/tag/status/msg; deploy/audit_logger.py → audit.log JSON operation/...; reporting.py::write_audit_log → audit.log free-text pipe); глоссарий: 37 из 68 allowed_verbs; 3 workflow на workflow_run platform-test (core-deploy.yml:31, build-platform.yml:35, mirror.yml:105); ghost-ссылки: overlay_deliverer.py:23 TRAP[DEBT] → node-resolver.sh:306-316 (файл 273 строки — строки не существуют); shared/AGENTS.md:31 потребители audit_logger; test_node_lifecycle_static.py:521 grep-контракт на state_machine; артефакты: reports/ ×8 в git, .pre-commit-config.yaml.bak на диске (НЕ в git), .deploy-snapshots/deploy-result.json в git; реестр долга: T1 фактически FIXED (B10 — test_add_vhost 7 passed), записи P3-1..3/AD1..4 без конкретных дат («При росте»/«Бессрочно»); backup-restore-test.sh — файл НЕ существует (5-й аудит-writer не подтверждён — проверить при T2).
## @changes 2026-08-01 · Решения пользователя (question 2026-08-01): (D1) U-10 — полная консолидация audit: единый shared/audit_logger (расширенная схема), единый audit.jsonl, deploy/audit_logger.py удаляется; (D2) U-57 — новый лёгкий workflow platform-gate-fast.yml (fast-gate), 3 downstream переключаются; (D3) U-45 — расширение G4 (generate_agents_md.py), НЕ новый генератор; (D4) U-82 — формат записей реестра: status (OPEN/FIXED/SUPERSEDED) + rev-date (конкретная дата ИЛИ условие-триггер); stale (>90 дней, конкретные даты) → RED; (D5) U-84 — DevPlans 085/110/111 пометить superseded, VR задним числом НЕ писать; процессный лимит ≤2 коммитов на DevPlan; (D6) старт — пользователь коммитит рабочее дерево до начала работ.
# endregion MODULE_CONTRACT

$START_DEVPLAN
$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B11 — enforcement-инфраструктура против возврата дрейфа: cross-layer гейт (dotted-импорты + python3 -m), единый audit-формат (JSONL, один writer), генерируемый глоссарий (G4-расширение), развязка downstream workflow от full-gate, гигиена артефактов и ghost-ссылок, стабилизация test_inventory (rename-детекция), реестр долга с гейтом свежести, процессные решения U-80/U-83..88 + TRAP[DECISION] 2026-07-31.
  DESCRIPTION: Расширение _looks_like_path/resolve_import/scan_sh_file/scan_py_file в test_cross_layer_imports.py (dotted-name regex + python3 -m паттерн) + фиксация ~5-6 реальных нарушений allowlist'ом с LINT-EXEMPT-обоснованием и негатив-тестами (R5); полная консолидация audit в shared/audit_logger (расширенная схема ts/tag/status/msg/operation/project/channel/result/duration, единый audit.jsonl; deploy/audit_logger.py удалён; reporting.py pipe → shared; DeployOrchestrator → shared) + новый гейт test_gate_audit_format (JSONL-валидация, 0 прямых f.write вне shared); генерация глоссария root AGENTS.md из allowed_verbs + operation_ru (расширение generate_agents_md.py --target root, GENERATED-маркеры, check-manifests сверка); новый workflow platform-gate-fast.yml (gitleaks + fast-gate на push main) и переключение core-deploy/build-platform/mirror; удаление reports/ ×8 (git rm), .pre-commit-config.yaml.bak, .deploy-snapshots/deploy-result.json (+ .gitignore); исправление ghost-ссылок (overlay_deliverer:23, shared/AGENTS.md:31, test_node_lifecycle_static:521); test_inventory — единая регенерация + rename-детекция в гейте; реестр долга — миграция формата (status + rev-date) + гейт свежести (>90 дней → RED) + T1→FIXED; TRAP[DECISION] 2026-07-31 (enforcement с allowlist — канон) + процессный лимит ≤2 коммитов + superseded 085/110/111 + решения U-83..88 в реестре.
  RATIONALE: Оба аудита (116): инварианты декларируются, но не enforce-ятся; гейты либо слепы (cross-layer не видит dotted-импортов — 36 passed при нарушениях), либо отсутствуют (audit format, глоссарий-паритет, свежесть долга). U-10: 3 формата аудита ломают observability. U-45: ручная таблица глоссария дрейфует (37/68). U-57: downstream ждут 20-минутный full-gate + integration-степ — хрупкая цепочка. U-82: реестр без гейта свежести — stale-пункты живут годами. Волна 116 закрывает функциональные проблемы; B11 делает их возврат невозможным.
  ACCEPTANCE_CRITERIA: (1) cross-layer гейт ловит dotted-импорты и python3 -m; ~5-6 нарушений закрыты allowlist'ом (LINT-EXEMPT задокументирован), allowlist не растёт, негатив-тесты RED; (2) audit: единый writer shared/audit_logger, единый audit.jsonl, расширенная схема; deploy/audit_logger.py удалён; reporting.py pipe мигрирован; гейт R2 (test_gate_audit_format) валидирует JSONL; (3) root AGENTS.md глоссарий генерируется из allowed_verbs (G4-расширение, GENERATED-маркеры) — 68 строк, 0 ручных правок, check-manifests сверяет; (4) core-deploy/build-platform/mirror не зависят от platform-test (только platform-gate-fast); (5) артефакты (.bak, reports/, deploy-result.json) удалены/исключены; ghost-ссылки (overlay_deliverer:23, shared/AGENTS.md:31, node_lifecycle_static:521) исправлены; (6) test_inventory: единая регенерация, rename-детекция (rename без changelog → PASS + warning, удаление без changelog → RED); (7) реестр долга: все записи со status + rev-date (дата|условие); гейт свежести (stale > 90 дней → RED, отсутствие полей → RED); T1 → FIXED; (8) новый TRAP[DECISION] (2026-07-31) в root AGENTS.md фиксирует пересмотр TRAP 2026-07-21: CI-гейты с allowlist — канон; (9) P3-наблюдения U-83..88 переведены в реестр с решениями (issue-cert justified S1; node-resolver P2-1 backlog; big-bang — лимит ≤2 коммитов на DevPlan в .kilo/rules/_project.md; DevPlans 085/110/111 — superseded; CI-комментарии — cleanup; cert ×3 — документировано).
  IMPLEMENTS: U-09, U-10, U-45, U-51, U-57, U-58, U-79, U-80, U-82, U-83..U-88
  IMPACTS: tests/test_cross_layer_imports.py, core/internal/shared/audit_logger.py, core/internal/deploy/audit_logger.py (удалён), core/internal/deploy/orchestrator.py, core/internal/bootstrap/lifecycle/helpers/reporting.py, core/internal/scripts/generate_agents_md.py, AGENTS.md (root), makefiles/{manifest.mk, helpers.mk}, .github/workflows/{platform-gate-fast.yml (новый), core-deploy.yml, build-platform.yml, mirror.yml}, tests/{test_inventory.yaml, test_inventory_changes.yaml}, tests/gates/{test_gate_audit_format.py (новый), test_gate_debt_registry.py, test_gate_test_inventory.py}, .ai/debt/001-Strangler-Fig-Closeout.md, .kilo/rules/_project.md, core/lib/audit.sh (verify), core/internal/shared/AGENTS.md, core/modules/{hermes-agent/watchdog/agent_watchdog.py, backup-cron/scripts/{backup_config.py, disk-monitor.sh}, postgres/hooks/on-project-deploy.sh} (LINT-EXEMPT)
  REQUIRES: Все предыдущие волны 116 (B10 — T1 FIXED в реестре долга; B1-B9 — гейты фиксируют их итоги); решения пользователя 2026-08-01 (D1-D6); чистое рабочее дерево на старте (пользователь коммитит перед началом)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| D | Вопрос | Решение |
|---|--------|---------|
| D1 | U-10: глубина audit-консолидации | **Полная консолидация.** Единый writer — shared/audit_logger.py с расширенной схемой (ts/tag/status/msg + optional operation/project/channel/result/duration_s/snapshot_id через **extra). Единый файл /var/log/platform/audit.jsonl. deploy/audit_logger.py УДАЛЯЕТСЯ — DeployOrchestrator переходит на shared. reporting.py::write_audit_log (free-text pipe) → write_audit_entry. context_deployer уже на shared (без изменений). Новый гейт R2 (test_gate_audit_format): 0 прямых open(audit)/f.write вне shared + JSONL-валидация вывода (json.loads по строкам — jq-эквивалент). |
| D2 | U-57: развязка downstream workflow | **Новый лёгкий workflow platform-gate-fast.yml** (push main: gitleaks + `make gate MODE=fast`, ~2-3 мин). core-deploy.yml/build-platform.yml/mirror.yml переключаются на workflow_run ["platform-gate-fast"]. platform-test остаётся для PR-валидации + full-gate. Downstream не зависят от integration/docker-степов (flaky-изоляция). |
| D3 | U-45: генератор глоссария | **Расширение G4** (generate_agents_md.py): параметр --target {core,root}; root AGENTS.md получает GENERATED-секцию glossary из allowed_verbs + operation_ru/description секций манифеста (join по имени таргета). Новый генератор G7 НЕ создаётся. check-manifests уже покрывает G4 — сверка глоссария бесплатна. |
| D4 | U-82: формат реестра долга + свежесть | **status + rev-date (дата ИЛИ условие).** Каждая запись секций SHELL-RESIDUAL/P2/P3/TEST-DEBT/ARCH-DECISIONS получает status (OPEN/FIXED/SUPERSEDED) и rev-date: конкретная YYYY-MM-DD ИЛИ условие-триггер («При росте >300 LOC», «Бессрочно», «При …»). Гейт: (а) запись без status/rev-date → RED; (б) конкретная дата в прошлом > 90 дней → RED (stale); (в) условие-триггер → не stale (проверка синтаксиса); (г) FIXED/SUPERSEDED не stale. T1 → FIXED (B10), P2-1 остаётся OPEN. |
| D5 | U-83..88: DevPlans 085/110/111 без VR | **Superseded-пометки, VR задним числом НЕ писать.** issue-cert → justified (S1, остаётся в SHELL-RESIDUAL, rev 2026-12-31). node-resolver → P2-1 backlog (OPEN, rev 2026-09-30). big-bang → процессный лимит ≤2 коммитов на DevPlan (docs + feat) в .kilo/rules/_project.md. DevPlans 085/110/111 → пометка superseded в шапках. CI-комментарии — cleanup (gh api, manual). cert ×3 — решение документируется в реестре. |
| D6 | Старт реализации | **Да.** Пользователь коммитит текущее рабочее дерево перед началом работ кодером. |

## 2. Текущее состояние worktree (подтверждённые факты)

- **Cross-layer гейт слеп (U-09):** `python3 -m pytest tests/test_cross_layer_imports.py` → 36 passed при 6 реальных нарушениях: `agent_watchdog.py:42-44` (3× `from core.internal.{config,shared.*} import`), `backup_config.py:36` (`from core.internal.config import platform_config`), `disk-monitor.sh:44` (`python3 -m core.internal.shared.telegram_notifier send`), `postgres/hooks/on-project-deploy.sh:46` (`python3 -m core.internal.shared.node_yaml`). Причины слепоты: `_looks_like_path` (:263) не распознаёт dotted-names (нет `/`); `scan_sh_file` (:452) не имеет паттерна `python3 -m`; `scan_py_file` (:567) ловит `from core.internal...`, но `resolve_import` (:373) отбрасывает (нет `/`). LINT-EXEMPT (:442) сейчас НЕ подавляет нарушения — только warning (:886-892).
- **Audit-раскол (U-10):** 3 writer'а: (1) `shared/audit_logger.py` → `/var/log/platform/audit.jsonl`, JSON ts/tag/status/msg; (2) `deploy/audit_logger.py` (259 LOC) → `/var/log/platform/audit.log`, JSON operation/project/channel/result/duration_s/snapshot_id (AuditLogger.log), потребитель — DeployOrchestrator; (3) `lifecycle/helpers/reporting.py::write_audit_log` (:140-161) → `/var/log/platform/audit.log`, free-text pipe `[ts] bootstrap:<mode> DONE | node=... | warnings=N | errors=N`. `context_deployer.py:480-501` уже на shared (write_audit_entry). `backup-restore-test.sh:104` — файл НЕ существует (проверить при T2; 5-й writer не подтверждён). `lib/audit.sh` — уже тонкий фасад над shared (83 LOC, без правок). `deploy/audit_logger.py` использует shared только для read (:244).
- **Глоссарий (U-45):** root AGENTS.md «Глоссарий глаголов» — 37 ✅-строк; `entrypoint-manifest.yaml allowed_verbs` — 68 имён. `generate_agents_md.py` (G4) генерит только core/AGENTS.md (canon_table + forbidden-списки, marker-механизм `<!-- GENERATED:START:canon_table -->`); root AGENTS.md глоссарий — ручная таблица. G-цепочка: Chain A (G1→G2→G5), Chain B (G3→G4), Chain C (G6) — `makefiles/manifest.mk`.
- **Workflow (U-57):** `core-deploy.yml:31`, `build-platform.yml:35`, `mirror.yml:105` — `workflow_run: workflows: ["platform-test"]` + push-filter + SHA-verification шаг («Resolve SHA + verify platform-test»). platform-test = единый job: checkout→gitleaks→pre-commit→dora→fast-gate→docker-setup→provision→build→full-gate→integration→cleanup (STRUCTURE-комментарий :2). Downstream ждут весь job (~20 мин + integration).
- **Артефакты (U-51):** `reports/` ×8 в git (RC3-*, baseline/wave4 csv, architecture-analysis, inline-python3-map); `.pre-commit-config.yaml.bak` на диске (13.5 KB, НЕ в git); `.deploy-snapshots/deploy-result.json` в git (runtime-артефакт).
- **Ghost-ссылки (U-58):** `overlay_deliverer.py:23` — TRAP[DEBT] ссылается на `node-resolver.sh:306-316` (файл = 273 строки; inline python3 -c уже мигрирован — строки 214/254 «Replaces inline python3 -c»); `shared/AGENTS.md:31` — audit_logger consumers «context_deployer, deploy, lib/audit.sh» (после D1 пересчитать: context_deployer, deploy_orchestrator, lifecycle/helpers/reporting, lib/audit.sh); `test_node_lifecycle_static.py:~:521` — grep-контракт на state_machine (execute_phase/CERTIFICATES — проверить валидность после B9-декомпозиции).
- **Реестр долга (U-82):** `001-Strangler-Fig-Closeout.md` — T1 (test_add_vhost) фактически FIXED (B10: 7 passed), но в реестре числится HI-активным; P2-1 node-resolver — OPEN rev 2026-09-30; P3-1..3 — rev «При росте»; AD1/AD4 — «При …»; AD5/AD7 — 2026-10-21/22; S1 — 2026-12-31. Гейт `test_gate_debt_registry.py` (405 LOC) сейчас проверяет только P2-1-присутствие (:298-311) — свежести нет.
- **test_inventory (U-79):** `tests/test_inventory.yaml` (generated, sync_inventory.py, единственный вызов — `helpers.mk:79`), `tests/test_inventory_changes.yaml` (manual changelog удалений), гейт `test_gate_test_inventory.py` (bi-directional collect-only, анти-tamper дубль парсера — намеренный, НЕ менять). Rename-детекции нет: rename = удаление + добавление → требует changelog.
- **Fix-churn (U-80):** `entrypoint-manifest.yaml` тронут в 8 из последних 15 коммитов (все feat/fix 116-волн) — симптом «чини гейт, а не процесс».
- **P3-наблюдения (U-83..88):** 085/110/111 без VR; issue-cert 704 LOC justified (S1); node-resolver 271 LOC (P2-1); big-bang коммиты (история 116 — wave-коммиты); CI-комментарии (platform-test debug-вывод); cert ×3 (cert_orchestrator.py + issue-cert.sh + s3_ssl_cache.py — тройка по дизайну).

## 3. Задачи

### T1 — U-09: cross-layer gate — dotted-импорты + python3 -m [CRITICAL]

**Файлы:** `tests/test_cross_layer_imports.py` (_looks_like_path :263, resolve_import :373, scan_sh_file :452, scan_py_file :567, layer_of_target :430), `core/modules/hermes-agent/watchdog/agent_watchdog.py` (:42-44), `core/modules/backup-cron/scripts/backup_config.py` (:36), `core/modules/backup-cron/scripts/disk-monitor.sh` (:44), `core/modules/postgres/hooks/on-project-deploy.sh` (:46)

**Шаги:**

1. **`_looks_like_path`**: добавить dotted-name детекцию — regex `^[a-z_][\w]*(\.[a-z_][\w]*)+$` (не начинается с `$`, не содержит `/`); dotted-name ≠ флаг (не в `_NON_IMPORT_ARGS`).
2. **`resolve_import`**: dotted-name → путь: `core.internal.shared.telegram_notifier` → `<CORE_DIR>/internal/shared/telegram_notifier` (замена `.` → `/`, prefix `core.internal.` → `core/internal/`); `layer_of_target` для dotted-целей (источник — modules → target internal = violation).
3. **`scan_sh_file`**: новый паттерн `python3 -m <module>` (и `python3 -m core.internal.*` — аргумент после `-m`); dotted-аргументы прогонять через `_looks_like_path` + `resolve_import`.
4. **`scan_py_file`**: уже собирает `from core.internal...` — фикс в `resolve_import` (п.2) делает их резолвимыми.
5. **Allowlist (канон B8 D3, строгий режим):** ~5-6 нарушений фиксируются в явном allowlist гейта `(path, lineno, reason)` — модули контейнеризированы и импортируют shared по дизайну (backup-cron/hermes-agent/postgres-hook). Нарушения получают `# LINT-EXEMPT: <причина>`-комментарии (документируется: LINT-EXEMPT снова легитимен ТОЛЬКО с allowlist-записью). Allowlist НЕ растёт: любое новое dotted-нарушение вне allowlist → RED.
6. **Негатив-тесты (R5, anti-survivorship):** inline-фикстура: (а) `from core.internal.shared.telegram_notifier import ...` в modules-фикстуре → RED; (б) `python3 -m core.internal.shared.node_yaml` в sh-фикстуре → RED.
7. **Документация:** обновить STRUCTURE/docstring гейта (U-09, dotted + python3 -m, allowlist-механизм).

**Критерий:** `rg "from core\.internal" core/modules/ --glob '*.py'` = только allowlist-записи (3 строки agent_watchdog + 1 backup_config); `rg "python3 -m core\.internal" core/modules/ --glob '*.sh'` = только allowlist-записи (disk-monitor:44, postgres-hook:46); 36 существующих тестов + 2 новых негативных зелёные; allowlist сжат до задокументированного минимума.

### T2 — U-10: audit — полная консолидация (D1) [CRITICAL]

**Файлы:** `core/internal/shared/audit_logger.py` (расширение схемы), `core/internal/deploy/audit_logger.py` (удаляется), `core/internal/deploy/orchestrator.py` (потребитель AuditLogger.log), `core/internal/bootstrap/lifecycle/helpers/reporting.py` (write_audit_log → shared), `tests/unit/test_shared_audit_logger.py` (расширение), `tests/gates/test_gate_audit_format.py` (новый), `core/internal/shared/AGENTS.md` (инвентарь — потребители), `core/entrypoint-manifest.yaml` (trinity — авто-discover)

**Шаги:**

1. **shared/audit_logger.py — расширенная схема:** `write_audit_entry(tag, status, message, log_file, **extra)` — extra-поля (operation, project, channel, result, duration_s, snapshot_id, …) сериализуются в ту же JSON-строку; обратная совместимость (существующие вызовы без extra); DEFAULT_LOG_FILE остаётся `/var/log/platform/audit.jsonl`.
2. **deploy/audit_logger.py удаляется:** DeployOrchestrator переходит на `write_audit_entry(tag="deploy:<operation>", status=result, message=..., operation=..., project=..., channel=..., duration_s=..., snapshot_id=...)`; пермишены (chmod 640/chown :adm — перенести в shared, если критично) — консолидировать в shared.
3. **reporting.py::write_audit_log:** free-text pipe → `write_audit_entry(tag="bootstrap:<mode>", status="DONE"|"ERROR", message=сводка)`; warnings/errors — отдельные записи status=WARN/ERROR.
4. **Единый файл:** все записи → `audit.jsonl` (deploy-записи больше не пишут в `audit.log`); `PLATFORM_AUDIT_LOG` compat в lib/audit.sh проверить (оставить как есть — фасад над shared).
5. **Новый гейт R2 `test_gate_audit_format.py`** (@pytest.mark.gate + trinity):
   - код-скан: 0 `open(..., "a")`/`f.write` на audit-файлы вне shared/audit_logger.py (паттерны: `audit.log`, `audit.jsonl`, `AUDIT_LOG` константы);
   - 0 free-text pipe-записей (нет `f.write(f"[{ts}]` в reporting/state_machine/steps);
   - формат: write → файл построчно парсится `json.loads` (jq-эквивалент); расширенная схема содержит ts/tag/status/msg + extra при передаче;
   - allowlist пуст (строгий).
6. **Учёт потребителей:** `rg "audit_logger" core/internal core/lib` — обновить shared/AGENTS.md inventory (строку audit_logger; потребители: context_deployer, deploy_orchestrator, lifecycle/helpers/reporting, lib/audit.sh); упоминания deploy/audit_logger в комментариях/AGENTS.md — актуализировать.
7. **Backup-restore-test.sh :104** — файл не существует; проверить при реализации: если 5-й writer где-то остался (tests/e2e/*.sh) — перевести на `python3 -m core.internal.shared.audit_logger write`.

**Критерий:** `rg "deploy.audit_logger|audit_logger.py" core/internal/deploy/` = 0; `rg -n 'open\(.*audit|f\.write' core/ --glob '*.py'` — только shared/audit_logger.py; reporting.py без pipe-формата; гейт R2 зелёный (+ негатив: inline-фикстура с f.write в audit → RED); unit-тесты shared расширены (extra-поля + backward-compat).

### T3 — U-45: глоссарий из allowed_verbs (D3) [FUNDAMENT]

**Файлы:** `core/internal/scripts/generate_agents_md.py` (G4-расширение), `AGENTS.md` (root — GENERATED-секция glossary), `makefiles/manifest.mk` (G4-цепочка — добавить --target root), `core/entrypoint-manifest.yaml` (SoT)

**Шаги:**

1. **G4-расширение:** `generate_agents_md.py` получает `--target {core,root}` (или `--root-agents <path>`): target=core → текущее поведение (canon_table + forbidden в core/AGENTS.md); target=root → генерация секции glossary в root AGENTS.md.
2. **Генерация глоссария:** таблица из `allowed_verbs` + join по имени таргета с секциями манифеста (operation_ru/description/signature); verbs без описания — строка с `—` (не RED); полный список 68; маркеры `<!-- GENERATED:START:glossary -->` / `<!-- GENERATED:END:glossary -->`.
3. **0 ручных правок:** весь глоссарий в маркерах; вне маркеров остаются только ручные элементы (❌-глаголы, «Правило», двухуровневая семантика) — сохранить.
4. **makefiles/manifest.mk:** G4-вызов дополняется вторым запуском (--target root); check-manifests: G4 --check для обоих target (байт-сравнение) — сверка глоссария автоматически.
5. **Регенерация:** `make generate-manifests` → diff = добавленные ~31 verb-строки (+ правки форматирования); ручные строки не тронуты.

**Критерий:** таблица глоссария = 68 строк (все allowed_verbs); `rg "GENERATED:START:glossary" AGENTS.md` присутствует; `make check-manifests` зелёный; повторный `make generate-manifests` → 0 diff (идемпотентность).

### T4 — U-57: развязка downstream workflow (D2) [CRITICAL]

**Файлы:** `.github/workflows/platform-gate-fast.yml` (новый), `.github/workflows/core-deploy.yml` (:31, :66-72 verification-шаг), `.github/workflows/build-platform.yml` (:35, :69), `.github/workflows/mirror.yml` (:105, :140-144), `.github/workflows/platform-test.yml` (read-only — источник шагов)

**Шаги:**

1. **Новый `platform-gate-fast.yml`:** on push main (и workflow_dispatch); steps: checkout → gitleaks (переиспользовать композитный шаг из platform-test) → `make gate MODE=fast` (~2-3 мин); имя workflow — `platform-gate-fast`.
2. **core-deploy.yml / build-platform.yml / mirror.yml:** `workflow_run.workflows: ["platform-test"]` → `["platform-gate-fast"]`; шаг «Resolve SHA + verify platform-test» → «verify platform-gate-fast» (имя/композитный action, если параметризован по имени workflow); push-filter и SHA-логика (head_sha) сохраняются.
3. **flaky-изоляция:** downstream больше не зависят от full-gate + integration + docker-степов platform-test (было: один job целиком).
4. **Проверка остальных зависимостей:** `rg "platform-test" .github/workflows/` — после правок только platform-test.yml (self) + комментарии; push-gate.yml — проверить отсутствие зависимости.
5. **Документация:** STRUCTURE/@changes-комментарии workflow (Plan 2 → Plan 3: trigger = platform-gate-fast; rationale D2).

**Критерий:** `rg 'workflows: \["platform-test"\]' .github/workflows/` = 0; `rg 'platform-gate-fast' .github/workflows/` = 3 (core-deploy, build-platform, mirror) + 1 (новый файл); yaml-валидация workflow-файлов (python -c yaml.safe_load); проверка: имя platform-gate-fast в списке workflow_run — без орфографических ошибок.

### T5 — U-51/U-58: гигиена артефактов + ghost-ссылки [FUNDAMENT]

**Файлы:** `reports/` ×8 (git rm), `.pre-commit-config.yaml.bak` (rm — не в git), `.deploy-snapshots/deploy-result.json` (git rm + .gitignore), `core/internal/bootstrap/overlay_deliverer.py` (:23), `core/internal/shared/AGENTS.md` (:31), `tests/test_node_lifecycle_static.py` (~:521)

**Шаги:**

1. **Артефакты (U-51):** `git rm reports/*` (8 файлов); `rm .pre-commit-config.yaml.bak` (не в git — проверить `git status`); `git rm .deploy-snapshots/deploy-result.json` + `.gitignore` += `.deploy-snapshots/` (runtime-артефакты не коммитятся).
2. **Ghost TRAP (overlay_deliverer.py:23):** TRAP[DEBT] ссылается на `node-resolver.sh:306-316` — строки не существуют (файл 273); inline python3 -c мигрирован (строки 214/254 — «Replaces»); актуализировать TRAP (указать реальные строки 214/254 + статус «мигрировано» или закрыть запись как stale — решение по месту, паттерн B8 D3).
3. **shared/AGENTS.md:31:** потребители audit_logger актуализируются после T2: context_deployer, deploy_orchestrator, lifecycle/helpers/reporting, lib/audit.sh.
4. **test_node_lifecycle_static.py:~:521:** проверить grep-контракт на state_machine (execute_phase/CERTIFICATES) против текущего кода после B9-декомпозиции; невалидные assert'ы — исправить или удалить (ложный контракт, U-58).
5. **Phantom-проверка (паттерн B8 D3):** `rg "reports/|deploy-result|\.bak"` в git-файлах = 0 (кроме .gitignore-строки).

**Критерий:** `git ls-files | rg 'reports/|deploy-result|\.bak'` = 0; `.gitignore` содержит `.deploy-snapshots/`; ghost-ссылки исправлены (rg по несуществующим строкам = 0); тесты зелёные.

### T6 — U-79: test_inventory — единая регенерация + rename-детекция [FUNDAMENT]

**Файлы:** `tests/tools/sync_inventory.py`, `tests/gates/test_gate_test_inventory.py`, `tests/test_inventory.yaml` (generated), `tests/test_inventory_changes.yaml`, `makefiles/helpers.mk` (:79)

**Шаги:**

1. **Диагностика «двойной регенерации»:** единственный вызов sync_inventory — `helpers.mk:79` (test-inventory-sync); гейт делает свой `--collect-only` (намеренный anti-tamper дубль T18 — НЕ менять); проверить CI-цепочку (`push-gate.yml`/`platform-test.yml` — inventory-sync не вызывается; `make fix-gate` — generate-manifests, не inventory): если фактического двойного вызова нет — зафиксировать single-source (комментарий + тест на отсутствие второго вызова); при наличии — консолидировать.
2. **Rename-детекция в гейте:** diff-анализ: nodeid удалён + новый nodeid с той же тест-функцией в том же изменении (файл/функция совпадают по нормализованному имени) → rename: changelog не обязателен, warning в лог; удаление без rename-пары → требование changelog сохраняется (RED).
3. **Документация гейта:** STRUCTURE/docstring обновить (rename-семантика).

**Критерий:** единая точка регенерации подтверждена/консолидирована; rename (удаление+добавление пары) → PASS + warning; удаление без changelog → RED (негатив-тест сохранён); `make test-inventory-sync` идемпотентен (2-й запуск = 0 diff).

### T7 — U-82: реестр долга — формат + гейт свежести (D4) [CRITICAL]

**Файлы:** `.ai/debt/001-Strangler-Fig-Closeout.md` (миграция формата), `tests/gates/test_gate_debt_registry.py` (расширение), `core/entrypoint-manifest.yaml` (trinity — авто-discover)

**Шаги:**

1. **Миграция формата:** каждая запись секций SHELL-RESIDUAL / P2-BACKLOG / P3-BACKLOG / TEST-DEBT / ARCH-DECISIONS получает: `status` (OPEN/FIXED/SUPERSEDED) + `rev` (дата YYYY-MM-DD ИЛИ условие: «При росте >300 LOC», «Бессрочно», «При …»). Формат: вторая строка таблицы или inline-поля — выбрать единый при миграции (рекомендуется: колонки таблиц дополняются `Status` и `Rev` — Rev уже есть; добавляется Status).
2. **Содержательная миграция:** T1 → FIXED (B10: test_add_vhost 7 passed — фактическое состояние); P2-1 → OPEN (rev 2026-09-30 — не stale); P3-1..3 → OPEN + условие («При росте >300/300/>300 LOC»); P3-4/5, T2-T7 → OPEN + существующие rev; S1 → OPEN (2026-12-31); AD1-AD4 → OPEN + условие; AD5 (2026-10-21) / AD7 (2026-10-22) → OPEN — не stale (≤90 дней от 2026-08-01); AD6 → OPEN + условие.
3. **Гейт свежести (расширение test_gate_debt_registry.py):**
   - каждая запись секций: status ∈ {OPEN, FIXED, SUPERSEDED} + rev непустой → иначе RED;
   - stale: rev = конкретная дата и (today - rev) > 90 дней → RED (сегодня 2026-08-01 → любая rev ≤ 2026-05-03 → RED);
   - условие-триггер (rev начинается с «При»/«Бессрочно»/«При росте») → не stale (синтаксическая проверка);
   - FIXED/SUPERSEDED → не stale;
   - существующая проверка P2-1-присутствия сохраняется;
   - негатив-тесты (R5): запись без rev → RED; запись с прошедшей датой (параметр today в функции-проверке) → RED.
4. **Интеграция:** trinity (файл + @pytest.mark.gate + manifest авто-discover); repair-поля не нужны (L3 — ручная правка реестра).

**Критерий:** 100% записей реестра имеют status + rev; гейт зелёный; негатив-тесты доказывают детект stale/отсутствия полей; T1 → FIXED (rg "FIXED" в TEST-DEBT-секции).

### T8 — U-80/U-83..88: процессные решения + TRAP[DECISION] [FUNDAMENT]

**Файлы:** `AGENTS.md` (root — новый TRAP[DECISION]), `.kilo/rules/_project.md` (процессный лимит), `.ai/debt/001-Strangler-Fig-Closeout.md` (новые записи), `.ai/plans/116-hardening-program/{15-DevPlan.md (085-эквивалент — проверить), 21-DevPlan.md, 22-DevPlan.md}` — superseded-пометки

**Шаги:**

1. **TRAP[DECISION] 2026-07-31 (AC8):** в root AGENTS.md MODULE_CONTRACT: «Enforcement-гейты с allowlist — канон (пересмотр TRAP 2026-07-21)»: Rejected — pre-commit как единственный enforcement (риск: «gate зелёный, система врёт»); Reason — решения пользователя 2026-07-31 (D1, 01-Brief §1) + волна B11: cross-layer allowlist, audit-format R2, glossary G4, debt-freshness; Rev — 2026-10-21 (синхронно с пересмотром языковой политики).
2. **Процессный лимит коммитов (U-83):** `.kilo/rules/_project.md` CI Pre-flight Rules += «≤2 коммита на DevPlan: docs (DevPlan) + feat (реализация); раздельные commit'ы по волнам — норма; big-bang (один коммит на N волн) — запрещён».
3. **U-84 (085/110/111 без VR):** superseded-пометки в шапках соответствующих DevPlan-файлов (`## @changes` строка: «SUPERSEDED 2026-08-01 — закрыт волнами 116; VR не требуется (D5)») + запись в реестре долга (если уместна).
4. **U-85 issue-cert justified:** запись в реестре подтверждается (S1, OPEN, rev 2026-12-31, обоснование acme.sh executor by design).
5. **U-86 node-resolver:** P2-1 подтверждается (OPEN, rev 2026-09-30, обоснование — декомпозиция в backlog).
6. **U-87 CI-комментарии:** cleanup-шаг: `gh api` удаление лишних комментариев (manual; при отсутствии доступа — задокументировать как manual-шаг в отчёте).
7. **U-88 cert ×3:** решение фиксируется в реестре: cert_orchestrator.py (Python-оркестрация) + issue-cert.sh (S1 justified, acme.sh executor) + s3_ssl_cache.py (Python cache) — тройка по дизайну; OPEN, rev при изменении контракта.

**Критерий:** TRAP[DECISION] 2026-07-31 в root AGENTS.md (rg "Enforcement-гейты с allowlist" AGENTS.md); `.kilo/rules/_project.md` содержит лимит ≤2 коммитов; 085/110/111 — superseded-пометки; реестр долга содержит записи U-83..88 с решениями.

### T9 — манифесты: регенерация + регистрация гейтов [FUNDAMENT]

**Файлы:** `core/entrypoint-manifest.yaml`, `tests/test_inventory.yaml`, `tests/AGENTS.md`, `core/AGENTS.md` (verify)

**Шаги:**

1. `make generate-manifests` (G1-G6 + расширенный G4 root) — зафиксировать diff (глоссарий +68 строк).
2. `make test-inventory-sync` — актуализация после T5/T6 (если удалялись тест-файлы — нет; verify 0 diff).
3. Новые/расширенные гейты (trinity): `test_gate_audit_format` (T2), `test_gate_debt_registry` (T7), `test_cross_layer_imports` (T1 — уже авто-discover) — проверить регистрацию в entrypoint-manifest gates.
4. `tests/AGENTS.md` — инвентарь новых гейтов (если требуется).
5. Проверка: `make check-manifests` зелёный до/после.

**Критерий:** check-manifests 0 diff; новые гейты в манифесте; inventory актуальна.

### T10 — самоверификация волны [CRITICAL]

**Шаги:**

1. **Целевые прогоны:** `python3 -m pytest tests/test_cross_layer_imports.py tests/gates/test_gate_audit_format.py tests/gates/test_gate_debt_registry.py tests/gates/test_gate_test_inventory.py tests/unit/test_shared_audit_logger.py` — зелёные (включая негатив-тесты).
2. **Полный gate:** `make gate MODE=full` (или `make preflight` при ограничении времени) — зелёный; `make check-manifests` — 0 diff.
3. **Consumer-scans финальные:** `rg "from core\.internal" core/modules/ --glob '*.py'` — allowlist-только; `rg 'workflows: \["platform-test"\]' .github/workflows/` = 0; `rg "deploy.audit_logger" core/` = 0; `rg -n 'open\(.*audit|f\.write' core/ --glob '*.py'` — только shared; `git ls-files | rg 'reports/|deploy-result|\.bak'` = 0; глоссарий 68 строк; реестр: 100% записей со status+rev.
4. **Fix-gate:** `make fix-gate DRY_RUN=1` → «would fix» пусто (все drift-состояния уже исправлены); `make fix-gate && git add -u` перед коммитом (CI pre-flight).
5. **U-83..88 сводка решений** — в @changes DevPlan.

**Критерий:** все шаги зелёные; allowlist cross-layer — только задокументированные записи; audit единый; downstream на platform-gate-fast; реестр свежий; TRAP 2026-07-31 зафиксирован.

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| D1: удаление deploy/audit_logger.py ломает DeployOrchestrator / observability-потребителей audit.log | Единственный потребитель — DeployOrchestrator (переводится в T2); консолидация в единый audit.jsonl документируется в shared/AGENTS.md; гейт R2 ловит возврат f.write; unit-тесты shared покрывают extra-схему. |
| D2: имя workflow в workflow_run — опечатка = тихий отказ триггеров | После правок — `rg`-проверка имени (T4 критерий); yaml-парсинг всех workflow; SHA-verification-шаг обновлён синхронно. |
| D3: генерация глоссария затирает ручные строки (❌-глаголы, «Правило») | Генерируется ТОЛЬКО секция между маркерами; ручные элементы вне маркеров; diff-ревью при регенерации; идемпотентность проверяется вторым прогоном (0 diff). |
| D4: миграция реестра теряет семантику «При росте» | Формат допускает условие-триггер — семантика сохраняется; гейт проверяет синтаксис условия; сегодня 2026-08-01 — записи ≤ 2026-05-03 попали бы в RED (при миграции проверить каждую дату). |
| Cross-layer allowlist «сжимается до 0» не в этой волне | Цель волны: зафиксировать 5-6 существующих нарушений с обоснованием (модули контейнеризированы, импорт shared — by design); НОВЫЕ нарушения → RED; сжатие — отдельный backlog (rev в allowlist-комментарии). |
| Ghost-фикс test_node_lifecycle_static:521 — grep-контракт может быть единственной защитой | Проверить фактическое поведение (B9-декомпозиция); при невалидности — заменить на контрактный assert по текущей структуре (native, не grep), паттерн B10 T2. |
| platform-gate-fast запускается дважды (push + workflow_dispatch) | concurrency-группа по SHA (паттерн platform-test); push-filter в downstream сохраняется. |

## 5. Критерии завершения волны (AC брифа 12-Brief)

- [ ] (1) Cross-layer гейт ловит dotted-импорты и `python3 -m`; 5-6 нарушений закрыты allowlist'ом (LINT-EXEMPT задокументирован), сжимается до 0, негатив-тесты RED (T1).
- [ ] (2) audit: единый writer (shared/audit_logger, JSONL, расширенная схема ts/tag/status/operation/result); deploy/audit_logger.py удалён; state_machine/reporting pipe-формат мигрирован; backup-restore-test → python-вызов (если существует); гейт R2 валидирует JSONL (T2).
- [ ] (3) root AGENTS.md глоссарий генерируется из allowed_verbs (G4-расширение) — 68 строк, 0 ручных правок; check-manifests включает сверку (T3).
- [ ] (4) core-deploy/build-platform/mirror не зависят от platform-test — только platform-gate-fast (T4).
- [ ] (5) артефакты (.bak, reports/ ×8, deploy-result.json) удалены/исключены; ghost-ссылки (overlay_deliverer:23, shared/AGENTS.md:31, node_lifecycle_static:521) исправлены (T5).
- [ ] (6) test_inventory: единая регенерация, rename-детекция (T6).
- [ ] (7) реестр долга: гейт свежести (stale > 90 дней → RED), ghost-строки устранены, T1 → FIXED (T7).
- [ ] (8) новый TRAP[DECISION] (2026-07-31) фиксирует пересмотр TRAP 2026-07-21: CI-гейты с allowlist — канон (T8).
- [ ] (9) P3-наблюдения U-83..88 переведены в реестр долга с решениями (issue-cert justified, node-resolver — backlog, big-bang — лимит ≤2 коммитов, 085/110/111 — superseded, CI-комментарии — cleanup, cert ×3 — дизайн) (T8).
- [ ] Все гейты программы зелёные: `make gate MODE=full` + `make check-manifests` + новые гейты (cross-layer, audit-format, glossary-G4, debt-freshness) (T10).
- [ ] `make fix-gate` чинит все drift-состояния; `make fix-gate && git add -u` выполнен перед коммитом (T10, CI pre-flight).

$END_DEVPLAN
