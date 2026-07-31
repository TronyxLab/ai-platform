# DevPlan 093 — Validate & Checkpoint Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Удалить 2 нарушения языковой политики в `core/`: (1) PYOF heredoc `validate_with_python()` в `validate.sh` — вынести в Python; (2) 3 inline `python3 -c` блока в `checkpoint.sh` — обернуть в CLI-вызовы `core/internal/scripts/`. Параллельно перевести `validate.sh` на модель «thin facade → Python dispatch».
- **DESCRIPTION:** Wave 1: извлечь jsonschema-валидацию из `validate_with_python()` heredoc в CLI `core/internal/scripts/jsonschema_validate.py` (generic yaml+schema validator); `validate.sh` остаётся диспетчером <50 LOC. Wave 2: извлечь 3 inline блока `checkpoint.sh` в `core/internal/scripts/checkpoint_cli.py` (read/mark/force/reset), shell остаётся тонким фасадом с вызовом CLI. **Никакого удаления `checkpoint.sh` и никакой замены на `state_machine.py`** — бриф ошибается (см. §Diagnosis).
- **RATIONALE:** Бриф основан на неточной верификации. В §Diagnosis ниже документированы 4 расхождения брифа с кодом. Корректная цель: ликвидировать inline-Python (языковая политика Tier 1) **без** разрушения существующего API `checkpoint_step()` (который потребляет `secrets.sh` и `state_machine._decrypt_secrets`) и **без** утери схемы node/ai-platform (которые не покрываются существующим `validate_module_yaml.py`).
- **ACCEPTANCE_CRITERIA:**
  - AC1: `make validate` работает идентично на существующем наборе файлов (regression test).
  - AC2: `make validate-modules` не затронут (отдельный путь через `validate_module_yaml.py`).
  - AC3: `grep -rn "python3 -c\|python3 <<\|python3 - <<" core/internal/validate/ core/lib/checkpoint.sh` → 0.
  - AC4: `checkpoint_step/checkpoint_force/checkpoint_reset_all` API сохранён (shell-функции-фасады), все потребители (`secrets.sh`, `state_machine._decrypt_secrets`, `tests/test_unit_checkpoint_v2.py`) работают без изменений.
  - AC5: `state_machine.py` НЕ модифицируется по вайбу брифа (нет замены checkpoint на прямой state.json — `state_machine` сам зависит от `checkpoint.sh`).
  - AC6: `validate.sh` ≤ 250 LOC (фасад), вся jsonschema-логика в Python CLI.
  - AC7: `checkpoint.sh` ≤ 90 LOC (фасад), вся state.json логика в Python CLI.
  - AC8: Unit-тесты на новый Python CLI (jsonschema + checkpoint) с IMP:9 assertion.
  - AC9: `make gate MODE=fast` зелёный.
- **IMPLEMENTS:** Закрытие Tier-1 Strangler-триггера (AGENTS.md §Языковая политика) для `validate.sh` и `checkpoint.sh`. НЕ реализует мнимое «удаление дублирования» из брифа (его не существует — см. Diagnosis D3).
- **IMPACTS:**
  - `core/internal/validate/validate.sh` — MODIFY (удалить PYOF heredoc, делегировать в CLI)
  - `core/lib/checkpoint.sh` — MODIFY (удалить 3 inline python3, делегировать в CLI, сохранить API)
  - `core/internal/scripts/jsonschema_validate.py` — NEW (generic yaml↔schema validator CLI)
  - `core/internal/scripts/checkpoint_cli.py` — NEW (state.json R/W CLI)
  - `tests/unit/test_jsonschema_validate.py` — NEW
  - `tests/unit/test_checkpoint_cli.py` — NEW
  - `tests/test_unit_checkpoint_v2.py` — VERIFY (не сломать)
- **REQUIRES:**
  - **DevPlan 087 STABLE** — `state_machine.py` финализирован, `state.json` format frozen (14 phase keys). ✅ Подтверждено: `087/DevPlan.md` + `02-VerificationReport.md` существуют.
  - Блокировка: DevPlan 091 (stabilize-087-088-089) должен слиться ПЕРВЫМ. Wave 2 (checkpoint) не стартует, пока 091 в `in-progress`. Если 091 трогает `checkpoint.sh` — координировать merge-order.

---

## $START_DEVPLAN

## 0. Diagnosis — корректировка брифа

⚠️ Бриф `01-Brief.md` содержит 4 фактические ошибки. Реализация следует коду, а не брифу. Каждое расхождение задокументировано для audit-trail.

### D1: `validate_module_yaml.py` НЕ в `core/internal/validate/`
- **Бриф:** "расширение существующего `core/internal/validate/validate_module_yaml.py` (jsonschema уже есть)."
- **Реальность:** Файл находится в `core/internal/scripts/validate_module_yaml.py` (подтверждено `glob`). Это **module.yaml-specific** D5-валидатор (env_requires, restart-drift) — он НЕ generic и НЕ валидирует `node.yaml`/`ai-platform.yaml`.
- **Следствие:** PYOF heredoc из `validate.sh` валидирует **3 разных schema** (`node.schema.json`, `module.schema.json`, `ai-platform.schema.json`) generic-образом. Помещение этой логики в `validate_module_yaml.py` = нарушение SRP (Principle 8, AI-First Architecture). Решение: новый **generic** `jsonschema_validate.py` рядом в `core/internal/scripts/`.

### D2: `checkpoint.sh` содержит ДРУГИЕ функции, не `checkpoint_save/load/clear`
- **Бриф:** "функции `checkpoint_save()`, `checkpoint_load()`, `checkpoint_clear()` дублируют `state_machine.py` методы `save_state()`, `load_state()`, `clear_state()`."
- **Реальность:** `checkpoint.sh` содержит: `_checkpoint_is_done_json()`, `_checkpoint_mark_done_json()`, `checkpoint_step()` (resume/force/verify), `checkpoint_force()`, `checkpoint_reset_all()`. Функций `checkpoint_save/load/clear` **не существует**. `state_machine.py` имеет `_is_step_done(n)`, `save_state`, `_resume_phase()` — это другая абстракция (phase-based vs step-based).
- **Следствие:** Методология (B5): использую фактические имена функций из чтения кода.

### D3: `checkpoint.sh` — НЕ dead code, НЕ может быть удалён
- **Бриф:** "После 087 state.json пишется `state_machine.py` напрямую — `checkpoint.sh` становится **dead code**. Удалить."
- **Реальность (3 активных потребителя):**
  1. `core/lib/secrets.sh:117-120` — fallback-определения `step_start/step_done/step_skip` + комментарии "assumes step_start/done/skip/log_step are defined". `checkpoint.sh` (через `state_machine._decrypt_secrets:1966-1978`) — обязательная зависимость для sourced `secrets.sh`.
  2. `core/internal/bootstrap/lifecycle/state_machine.py:1630` — `path_map["verify_core"]` включает `lib/checkpoint.sh` для content-hash.
  3. `core/internal/bootstrap/lifecycle/state_machine.py:1960-1978` — `_decrypt_secrets()` явно `source`s `checkpoint.sh` перед `secrets.sh` (TRAP-аннотация P0 объясняет почему).
- **Следствие:** Удаление `checkpoint.sh` сломает bootstrap. Корректная цель: сохранить shell-API как тонкий фасад, вынести inline-Python в CLI.

### D4: Inline-блоков больше, чем утверждает бриф
- **Бриф:** "`checkpoint.sh`: 3 inline python3 (строки 52-69, 81-103, 172-188)."
- **Реальность:** 3 блока, но строки `_checkpoint_is_done_json:46-70` (25 LOC), `_checkpoint_mark_done_json:77-105` (29 LOC), `checkpoint_force:169-190` (22 LOC). Локализация точная, но объём ~76 LOC inline — больше, чем «15 строк» для validate. Плюс в `validate.sh` есть второй inline `python3 -m core.internal.shared.node_yaml` в `check_port_conflict()` — НЕ затрагивается (уже CLI, корректно), но отмечен для полноты.

---

## 1. Verification of Pre-conditions

| Pre-condition | Статус | Доказательство |
|---------------|--------|----------------|
| DevPlan 087 merged | ✅ | `.ai/plans/087-bootstrap-phase-consolidation/{DevPlan.md,01-VerificationReport.md,02-VerificationReport.md}` существуют |
| `state_machine.py` final | ✅ | 2355 LOC, `path_map["verify_core"]` ref на `checkpoint.sh` (стр. 1630) — stable |
| `checkpoint.sh` consumers active | ✅ | 3 точки (D3): `secrets.sh`, `state_machine:1630`, `state_machine:1966` |
| `validate_module_yaml.py` exists | ✅ | `core/internal/scripts/validate_module_yaml.py` (638 LOC, D5-specific) |
| DevPlan 091 status | ⚠️ check | `090/091/092` dirs exist — Wave 2 блокируется пока 091 in-progress |
| `make gate MODE=fast` baseline green | ⏳ | Замерить ДО старта (baseline для regression AC1) |

---

## 2. Design Decisions

### DD1: Почему новый `jsonschema_validate.py`, а не расширение `validate_module_yaml.py`?

**Q:** Бриф требует «расширить существующий». Почему новый файл?

**A:** `validate_module_yaml.py` — **semantically coupled** к module.yaml D5-контракту (env_requires normalization, secrets-manifest cross-check, restart-drift vs docker-compose.base.yml). Generic jsonschema-валидация `node.yaml`/`ai-platform.yaml` туда не лезет без искажения `@purpose`. Principle 8 (AI-First Architecture): один модуль = одна ответственность. Цена: +1 файл (~120 LOC). Выгода: SRP сохранён, оба CLI независимо тестируемы.

⚠️ TRAP[DECISION] · 2026-07-30 · MED · Не расширять `validate_module_yaml.py`
· Rejected: расширение (риск: SRP нарушение, смешение generic-schema и D5-specific logic)
· Reason: `validate_module_yaml.py` уже 638 LOC с 3 cross-check'ами. Добавление generic валидатора → 800+ LOC god-module.
· Rev: если `jsonschema_validate.py` начнёт включать module-specific checks → merge назад в `validate_module_yaml.py`.

### DD2: Почему `checkpoint.sh` остаётся фасадом, а не удаляется?

**Q:** Бриф требует удалить. Почему фасад?

**A:** `checkpoint.sh` экспортирует 5 shell-функций, потребляемых `secrets.sh` (sourced) и `state_machine` (source-before-secrets). Удаление требует:
1. Переписать `secrets.sh` под `state_machine` direct-call (out of scope, риск P0).
2. Изменить `state_machine._decrypt_secrets` bash-source chain (TRAP P0).
3. Переписать `tests/test_unit_checkpoint_v2.py`.

Это работа уровня отдельного DevPlan (предложить как 097 «checkpoint.sh full retirement»). Текущий план ограничивается Tier-1: **ликвидация inline-Python** без изменения внешнего API.

⚠️ TRAP[DECISION] · 2026-07-30 · HI · checkpoint.sh остаётся shell-facade (не удаление)
· Rejected: удаление (риск: P0 regression в bootstrap decrypt chain, 3 активных потребителя)
· Reason: YAGNI для целей 093. Tier-1 триггер требует только извлечь inline-Python. Full retirement = отдельный план после стабилизации secrets.sh.
· Constraint: Внешний API `checkpoint_step/force/reset_all` immutable в рамках 093.
· Rev: открыть DevPlan 097 если `secrets.sh` мигрируется на прямой `secrets_manager.py` import.

### DD3: Почему `checkpoint_cli.py` вместо `state_machine.py` CLI?

**Q:** Бриф предлагает "заменить вызовы на прямой импорт `state_machine.py`."

**A:** `state_machine.py` — 2355 LOC bootstrap-orchestrator с фазовой моделью. Его `BootstrapState` имеет side-effects (phase-graph, precondition-checks). `checkpoint.sh` needs только 4 примитивные операции над `state.json` (read-key/mark-done/force-pending/reset-all). Импорт state_machine ради этого = pull-in всего dependency-graph. `checkpoint_cli.py` = ~100 LOC standalone (json + os). Principle 6 (Small Simple Blocks).

---

## 3. Waves

### Wave 1: `validate.sh` — jsonschema extraction

| Task | Описание | Est (pts) |
|------|----------|-----------|
| **W1-T1** | Создать `core/internal/scripts/jsonschema_validate.py`: CLI `--yaml-file --schema-file`, generic Draft7Validator с `iter_errors`, формат error "path: message", exit 0/1. MODULE_CONTRACT + LDD [IMP:9]. | 2 |
| **W1-T2** | `tests/unit/test_jsonschema_validate.py`: valid node.yaml, missing-field (invalid), type-mismatch, multiple-errors aggregation, malformed-yaml, missing-schema. caplog + IMP:9 assert. | 2 |
| **W1-T3** | `validate.sh`: заменить тело `validate_with_python()` на `python3 -m core.internal.scripts.jsonschema_validate --yaml-file "$yaml_file" --schema-file "$schema_file"`. Удалить PYOF heredoc (стр. 94-119). Сохранить error-проброс через `$output`. | 1 |
| **W1-T4** | Smoke: `make validate` на реальном дереве node.yaml/module.yaml/ai-platform.yaml — output byte-identical (capture до/после, diff). Regression-test в `tests/test_validate.py` расширить интеграционным случаем (CLI subprocess на tmp fixtures). | 1 |
| **W1-T5** | Обновить `core/AGENTS.md` навигацию если нужно; обновить GREP_SUMMARY `validate.sh` (убрать "python-jsonschema" если теперь только delegator). | 1 |

**Wave 1 exit criteria:**
- AC1, AC3, AC6 met
- `make validate` exit code + stderr semantic identical to baseline (W1-T4 evidence)
- `tests/test_validate_module_yaml.py` не затронут (separate path)

---

### Wave 2: `checkpoint.sh` — state.json CLI extraction

⚠️ **Зависит от DevPlan 091 merge.** Не стартует пока 091 `in-progress`.

| Task | Описание | Est (pts) |
|------|----------|-----------|
| **W2-T1** | Создать `core/internal/scripts/checkpoint_cli.py`: subcommands `is-done --step <name>`, `mark-done --step <name> [--hash <h>]`, `force --step <name>`, `reset-all`. Exit codes: is-done → 0/1 (shell-совместимо), остальные 0. Поддержка `CHECKPOINT_STATE_FILE` env (default `/var/lib/platform/.bootstrap/state.json`). Atomic write (tmp + os.replace). Чтение supports both `data['steps'][name]` (old) и root-level phase keys (new, post-087). | 2 |
| **W2-T2** | `tests/unit/test_checkpoint_cli.py`: round-trip (mark → is-done), missing-state-file (is-done exit 1), force resets to pending, reset-all deletes file, old-format-key compat, new-phase-key compat, hash stored+read. caplog IMP:9. | 2 |
| **W2-T3** | `checkpoint.sh`: переписать `_checkpoint_is_done_json` → `python3 ... checkpoint_cli.py is-done --step "$1"`, `_checkpoint_mark_done_json` → `mark-done`, тело `checkpoint_force` → `force`, тело `checkpoint_reset_all` → `reset-all`. Сохранить `checkpoint_step()` orchestration (resume/force/verify) — она вызывает helper'ы. Inline python3 = 0. | 2 |
| **W2-T4** | `tests/test_unit_checkpoint_v2.py`: VERIFY не сломан. Запустить, добавить negative-test на phase-key формат если отсутствует (R5 anti-survivorship — bug 087 DRIFT-CHECKPOINT-004). | 1 |
| **W2-T5** | `state_machine._decrypt_secrets:1966` + `path_map["verify_core"]:1630`: VERIFY не сломаны (checkpoint.sh всё ещё source'ится с теми же именами функций). Доказать: `tests/unit/test_state_machine.py` green, dry-run bootstrap test green. | 1 |

**Wave 2 exit criteria:**
- AC3, AC4, AC5, AC7, AC8 met
- `grep -rn "python3 -c\|python3 <<" core/lib/checkpoint.sh` → 0
- Все 3 потребителя (D3) functional

---

## 4. File Manifest

| Файл | Действие | LOC (до → после) | Замечание |
|------|----------|------------------|-----------|
| `core/internal/scripts/jsonschema_validate.py` | NEW | 0 → ~120 | Generic Draft7Validator CLI |
| `core/internal/scripts/checkpoint_cli.py` | NEW | 0 → ~110 | state.json R/W CLI |
| `core/internal/validate/validate.sh` | MODIFY | 380 → ~250 | Удалить PYOF (94-119), `validate_with_python` делегирует |
| `core/lib/checkpoint.sh` | MODIFY | 203 → ~90 | 3 inline → CLI-вызовы, API сохранён |
| `tests/unit/test_jsonschema_validate.py` | NEW | 0 → ~150 | |
| `tests/unit/test_checkpoint_cli.py` | NEW | 0 → ~180 | |
| `tests/test_unit_checkpoint_v2.py` | VERIFY | 264 | Не сломать |
| `tests/test_validate.py` | EXTEND | — | + integration case CLI subprocess |
| `core/AGENTS.md` | VERIFY | — | Обновить навигацию/scripts-секцию если требуется generated |

---

## 5. Out of Scope

- ❌ Удаление `checkpoint.sh` (DD2) → DevPlan 097 (будущий)
- ❌ Миграция `secrets.sh` на прямой `secrets_manager.py` import
- ❌ Рефакторинг `validate_module_yaml.py` архитектуры (Anti-Loop Note брифа принят)
- ❌ Изменение `state_machine.py` фазовой модели
- ❌ Изменение schema-файлов (`core/schemas/*.schema.json`) — read-only consumers
- ❌ `check_fqdn_conflict()` / `check_port_conflict()` в `validate.sh` — не содержат inline python3 (используют `node_yaml` CLI), не трогать

---

## 6. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| `checkpoint_cli.py is-done` exit-code semantics отличается от shell `_checkpoint_is_done_json` (return 0/1) | HI | W2-T3: Python exit 0=done, 1=not-done — shell `&& return 0 \|\| return 1` сохраняет совместимость. Unit-test W2-T2 покрывает |
| `validate_with_python` output format меняется → CI/logs diff | MED | W1-T4 byte-comparison baseline. Format: `Error at 'path': msg` сохранён 1:1 |
| `state_machine._decrypt_secrets` source-chain ломается при изменении checkpoint.sh | HI P0 | W2-T5 explicit verify + `tests/unit/test_state_machine.py`. API checkpoint_step/force/reset_all immutable |
| DevPlan 091 параллельно правит `checkpoint.sh` | MED | Wave 2 ждёт 091 merge. Если 091 трогает те же строки — rebase-конфликт, resolve вручную, перезапустить W2-T3 |
| Inline python3 в `checkpoint_force` печатает в stdout (`print(...)`), а контракт checkpoint.sh говорит "MUST NOT write to stdout" (MODULE_CONTRACT L23) | MED | `checkpoint_cli.py force` пишет только в stderr (LDD). Existing bug исправляется попутно — отметить в VerificationReport |
| `jsonschema_validate.py` добавляет новый entrypoint, не зарегистрированный в manifest | LOW | `scripts-audit` gate (`make scripts-audit`) — не entrypoint (internal CLI), регистрация не требуется. Проверить gate |

---

## 7. Verification Plan

### Pre-merge (per wave)
```bash
make fix-gate && git add -u
make gate MODE=fast
# Wave 1:
grep -rn "python3 -c\|python3 <<\|python3 - -" core/internal/validate/  # → 0
make validate  # exit 0 на чистом дереве

# Wave 2:
grep -rn "python3 -c\|python3 <<" core/lib/checkpoint.sh  # → 0
python -m pytest tests/unit/test_checkpoint_cli.py tests/test_unit_checkpoint_v2.py -v
python -m pytest tests/unit/test_state_machine.py -v  # consumer не сломан
```

### Post-merge (gate)
- `make gate MODE=full` зелёный
- `tests/test_add_vhost.py` green (mock validate.sh consumer)
- `tests/integration/test_bootstrap_dry_run.py:144` green (lib/checkpoint.sh exists check)
- Regression evidence: capture `make validate` stderr ДО и ПОСЛЕ, diff в VerificationReport

---

## 8. VerificationReport Outline (генерируется после реализации)

1. **AC Matrix** — AC1-AC9 с evidence (команды + output)
2. **Diagnosis reconciliation** — D1-D4: подтверждение, что реализация следует коду, не брифу
3. **LDD trajectory** — пример IMP:9 из `test_checkpoint_cli.py` / `test_jsonschema_validate.py`
4. **Consumer impact** — table по 3 потребителям checkpoint.sh (secrets.sh, state_machine:1630, state_machine:1966): статус до/после
5. **Regression diff** — `make validate` output byte-comparison
6. **Inline-python audit** — grep по `core/` показывает 0 в touched files

---

## 9. Implementation Order (для Coder)

```
Wave 1 (параллельно с W2-T1 design):
  coder W1-T1 (jsonschema_validate.py) → W1-T2 (tests) → W1-T3 (validate.sh) → W1-T4 (regression) → W1-T5 (docs)

  QA Wave 1: tests/unit/test_jsonschema_validate.py + make validate regression

[gate: DevPlan 091 merged?]

Wave 2:
  coder W2-T1 (checkpoint_cli.py) → W2-T2 (tests) → W2-T3 (checkpoint.sh facade) → W2-T4 (v2 verify) → W2-T5 (consumer verify)

  QA Wave 2: tests/unit/test_checkpoint_cli.py + test_unit_checkpoint_v2.py + test_state_machine.py + make gate MODE=fast

VerificationReport (этот файл → 02-VerificationReport.md)
```

---

## 10. Notes for downstream plans

- **DevPlan 097 (предложить):** «checkpoint.sh full retirement» — после миграции `secrets.sh` на прямой Python import. Предпосылка: `core/internal/scripts/secrets_manager.py` должен покрывать `step_10_decrypt_secrets` без source-chain.
- **DevPlan 094 (template-engine-python):** независим, но может использовать `jsonschema_validate.py` если template-schemas нужны.
- **AGENTS.md языковая политика:** после 093 пересчитать «inline python3» метрику для Decision Gate (TRAP 2026-07-22).

$END_DEVPLAN
