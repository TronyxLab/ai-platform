$START_DEVPLAN

# DevPlan 093 — Validate & Checkpoint Python Migration (Rev 2)

$ARTIFACT_CONTRACT
PURPOSE:               Ликвидировать 2 класса нарушений: (1) PYOF heredoc `validate_with_python()` в `validate.sh` (380 LOC, стр. 94-119) — вынести в Python CLI (Tier-1 Strangler триггер); (2) stale references на удалённый `core/lib/checkpoint.sh` в `state_machine.py` (2 точки: content-hash path_map L1588, _decrypt_secrets bash source chain L1916-1936) — cleanup после удаления файла в DevPlan 091.
DESCRIPTION:           Rev 2 плана (Rev 1 в git HEAD устарел — написан ДО удаления checkpoint.sh). Wave 1: извлечь generic jsonschema-валидацию из PYOF heredoc в CLI `core/internal/scripts/jsonschema_validate.py`; `validate.sh` остаётся диспетчером. Wave 2: удалить 2 stale references на `lib/checkpoint.sh` в `state_machine.py` (cleanup после 091), обновить комментарии. Никакого восстановления checkpoint.sh, никакой миграции state_machine на прямой state.json — 091 уже завершил backward-compat removal.
RATIONALE:             Бриф `01-Brief.md` основан на неточной верификации ДО удаления checkpoint.sh. В §Diagnosis ниже документированы 4 расхождения брифа с кодом. Корректная цель: (W1) закрыть Tier-1 Strangler-триггер для validate.sh, (W2) зачистить stale references, оставленные scaffold-коммитом 091 (8be2843).
ACCEPTANCE_CRITERIA:
  AC1: `make validate` работает идентично на существующем наборе файлов (regression: stderr byte-identical, exit 0) — W1-T4 evidence.
  AC2: `make validate-modules` не затронут (отдельный путь через `validate_module_yaml.py`).
  AC3: `grep -rn 'python3.*<<\|python3 -c' core/internal/validate/validate.sh` → 0 (ловит PYOF heredoc и inline -c).
  AC4: `grep -rn 'checkpoint\.sh\|lib/checkpoint' core/internal/bootstrap/lifecycle/state_machine.py` → 0 в executable code (L1588 path_map + L1924 source chain).
  AC5: `state_machine.py` functional integrity сохранена — `tests/unit/test_state_machine.py` green, `tests/integration/test_bootstrap_dry_run.py` green.
  AC6: `validate.sh` ≤ 365 LOC (фасад; было 380, расчёт: 380 − 26 heredoc + ~6 CLI-dispatch = ~360 — цель ≤ 350 недостижима при этом расчёте), вся jsonschema-логика в Python CLI.
  AC7a: `tests/unit/test_jsonschema_validate.py` — IMP:9 assertion (valid node.yaml → exit 0; invalid missing-field → exit 1).
  AC7b: `tests/unit/test_jsonschema_validate.py` — negative tests: type-mismatch, multiple-errors aggregation, malformed-yaml, invalid-json-schema (битый schema-файл → JSONDecodeError → exit 2; real risk: merge conflict в schema-файле), missing-schema (R5 anti-survivorship).
  AC8: `make gate MODE=fast` зелёный.
IMPLEMENTS:            Закрытие Tier-1 Strangler-триггера (AGENTS.md §Языковая политика) для `validate.sh` + cleanup stale references после DevPlan 091 (8be2843). НЕ реализует мнимое «удаление дублирования checkpoint↔state_machine» из брифа (его не существует — см. Diagnosis D3).
IMPACTS:
  - `core/internal/validate/validate.sh` — MODIFY (удалить PYOF heredoc L94-119, делегировать в CLI)
  - `core/internal/scripts/jsonschema_validate.py` — NEW (generic yaml↔schema validator CLI)
  - `core/internal/bootstrap/lifecycle/state_machine.py` — MODIFY (удалить 2 stale checkpoint.sh references, обновить комментарии)
  - `core/internal/bootstrap/content-hash.sh` — VERIFY (обновить комментарий L15, упоминающий checkpoint.sh)
  - `tests/unit/test_jsonschema_validate.py` — NEW
  - `tests/unit/test_validate_cli.py` — NEW (интеграционный subprocess regression-test, W1-T4)
  - `tests/test_unit_checkpoint_v2.py` — DELETE (QA audit 2026-07-31: 3/4 тестов тестируют удалённый checkpoint.sh — мёртвая логика, FileNotFoundError)
  - `tests/integration/test_bootstrap_dry_run.py` — MODIFY (убрать mock checkpoint.sh L161, обновить @invariants L122)
  - `tests/test_node_lifecycle_static.py` — MODIFY (docstring/@invariants L507-517 — ложное утверждение о source checkpoint.sh)
  - `tests/test_inventory.yaml` — MODIFY (re-sync через make test-inventory-sync)
REQUIRES:
  - DevPlan 087 STABLE — `state_machine.py` 14-phase модель финализирована, `state.json` format frozen. ✅ Подтверждено: `087/DevPlan.md` + `01-VerificationReport.md` + `02-VerificationReport.md` существуют.
  - DevPlan 091 merged — `core/lib/checkpoint.sh` удалён (commit 8be2843: "Remove legacy core/lib/checkpoint.sh"). ✅ Подтверждено: `git ls-tree HEAD` не содержит checkpoint.sh; commit 8be2843 в `git log`.
  - `make gate MODE=fast` baseline green. ✅ Подтверждено: `make validate` exit 0 (2026-07-31).
$END_ARTIFACT_CONTRACT

---

## §0. Audit 2026-07-31 — фактическое состояние vs Brief

Бриф 093 написан на основе аудита от 2026-07-30 (ДО merge DevPlan 091). После merge 091 (commit 8be2843, 2026-07-31 08:01) состояние кода радикально изменилось: `checkpoint.sh` удалён. DevPlan 093 Rev 2 отражает РЕАЛЬНЫЙ остаток.

### Что УЖЕ сделано (НЕ дублировать)

| Источник | Claim брифа | Фактический статус | Доказательство |
|----------|-------------|--------------------|----------------|
| 091 (8be2843) | `checkpoint.sh` (203 LOC) — удалить | ✅ DONE | `git ls-tree HEAD \| grep checkpoint.sh` = пусто; commit msg: "Remove legacy core/lib/checkpoint.sh; update tests, inventory, deploy.mk" |
| 091 (8be2843) | `state_migration.py` (198 LOC) — удалить | ✅ DONE | `git ls-files` не содержит state_migration.py |
| 091 (8be2843) | `INIT_STEPS`/`UPDATE_STEPS` dead constants — удалить | ✅ DONE | `grep "INIT_STEPS\|UPDATE_STEPS" state_machine.py` = пусто |
| 087 | 14-phase dispatch, dependency graph | ✅ DONE | `BootstrapPhase.INIT_PHASE_ORDER` в state_machine.py |

### Что ОСТАЛОСЬ (scope 093 Rev 2)

| Wave | Задача | Severity | Файл |
|------|--------|----------|------|
| W1 | PYOF heredoc `validate_with_python()` L94-119 (26 LOC) — извлечь в Python CLI | HI (Tier-1 языковая политика) | `core/internal/validate/validate.sh` |
| W2a | Stale reference: `path_map["verify_core"]` L1588 — `lib/checkpoint.sh` (удалён) | MED (content-hash включает несуществующий путь) | `state_machine.py` |
| W2b | Stale reference: `_decrypt_secrets` bash source chain L1916-1936 — `source checkpoint.sh` (удалён) | HI (bootstrap decrypt chain ломается на clean install) | `state_machine.py` |
| W2c | Comment drift: `content-hash.sh` L15 "All callers (checkpoint.sh, node-lifecycle.sh)" | LOW | `content-hash.sh` |

---

## §1. Diagnosis — корректировка брифа (4 расхождения)

⚠️ Бриф `01-Brief.md` содержит 4 фактические ошибки. Реализация следует коду, а не брифу. Каждое расхождение задокументировано для audit-trail. (D1-D2 актуальны из Rev 1; D3-D4 обновлены под post-091 состояние.)

### D1: `validate_module_yaml.py` НЕ в `core/internal/validate/` и НЕ generic
- **Бриф:** "расширение существующего `core/internal/validate/validate_module_yaml.py` (jsonschema уже есть)."
- **Реальность:** Файл находится в `core/internal/scripts/validate_module_yaml.py` (подтверждено `glob`, 638 LOC). Это **module.yaml-specific** D5-валидатор (env_requires normalization, secrets-manifest cross-check, restart-drift vs docker-compose.base.yml) — он НЕ generic и НЕ валидирует `node.yaml`/`ai-platform.yaml`.
- **Следствие:** PYOF heredoc из `validate.sh` валидирует **3 разных schema** (`node.schema.json`, `module.schema.json`, `ai-platform.schema.json`) generic-образом. Помещение этой логики в `validate_module_yaml.py` = нарушение SRP (Principle 8, AI-First Architecture). Решение: новый **generic** `jsonschema_validate.py` в `core/internal/scripts/`.

### D2: Функций `checkpoint_save/load/clear` никогда не существовало
- **Бриф:** "функции `checkpoint_save()`, `checkpoint_load()`, `checkpoint_clear()` дублируют `state_machine.py` методы `save_state()`, `load_state()`, `clear_state()`."
- **Реальность:** Исторический `checkpoint.sh` содержал: `_checkpoint_is_done_json()`, `_checkpoint_mark_done_json()`, `checkpoint_step()`, `checkpoint_force()`, `checkpoint_reset_all()`. Функций `checkpoint_save/load/clear` **не существовало** ни в одной ревизии. `state_machine.py` имеет `_is_step_done(n)`, `save_state`, `_resume_phase()` — другая абстракция (phase-based vs step-based).
- **Следствие:** «дублирование функциональности» из брифа — фикция. Никакого дублирования не было: две системы решали разные задачи на разных уровнях абстракции.

### D3: `checkpoint.sh` УЖЕ удалён (091 merged) — бриф устарел
- **Бриф:** "`checkpoint.sh` — 3 inline python3 блока для state.json R/W. `state_machine.py` (из DevPlan 087) уже управляет state.json. Это дублирование = dead code. Удалить."
- **Реальность (post-091, 2026-07-31):** `core/lib/checkpoint.sh` **удалён** в commit 8be2843 (DevPlan 091 Wave B). `git ls-tree HEAD` не содержит файла. Wave 1 брифа ("Удалить core/lib/checkpoint.sh") уже выполнен.
- **Следствие:** Wave 1 брифа = no-op. Единственный остаток — 2 stale references в `state_machine.py` (D4 ниже), которые 091 не успел зачистить (scaffold commit).

### D4: Stale references в `state_machine.py` (незакрытый долг 091)
- **Бриф:** Не упоминает (бриф предшествует удалению).
- **Реальность:** После удаления `checkpoint.sh` в 091 остались 2 stale references в `state_machine.py`:
  1. **L1587-1590** — `path_map["verify_core"]` включает `os.path.join(core_dir, "lib", "checkpoint.sh")` для content-hash вычисления. Файл удалён → hash вычисляется по несуществующему пути (no-op или warning в `_step_hash`).
  2. **L1916-1936** — `_decrypt_secrets()` собирает bash source chain: `source logging.sh && source checkpoint.sh && source secrets.sh && step_10_decrypt_secrets`. `source` несуществующего файла → bash error → `RuntimeError` в bootstrap.
- **Следствие:** Wave 2 = cleanup этих 2 stale references + обновление комментариев. Это завершение работы 091, не новая миграция.

---

## §2. Draft Code Graph (XML)

```xml
<graph version="093-rev2">
  <!-- Wave 1: validate.sh jsonschema extraction -->
  <entity id="validate_sh" type="FILE"
    keywords="validate, yaml, json-schema, ajv, python-jsonschema, PYOF-heredoc"
    annotation="380 LOC. PYOF heredoc в validate_with_python() L94-119 (26 LOC inline python3). Tier-1 violation. W1: делегировать в jsonschema_validate.py CLI."/>
  <entity id="validate_FUNC_validate_with_python" type="METHOD"
    keywords="PYOF, heredoc, jsonschema, Draft7Validator, iter_errors"
    annotation="L89-127. Заменить тело на python3 -m core.internal.scripts.jsonschema_validate. Сохранить error-проброс через $output + vlog_fail."/>
  <entity id="validate_FUNC_check_port_conflict" type="ANCHOR"
    keywords="node_yaml-cli, NOT-PYOF, no-change"
    annotation="L273: python3 -m core.internal.shared.node_yaml — уже CLI, НЕ inline. Не трогать (W1 scope)."/>
  <entity id="validate_module_yaml_py" type="FILE"
    keywords="D5-validator, module-specific, NOT-generic, separate-path"
    annotation="core/internal/scripts/validate_module_yaml.py (638 LOC). make validate-modules path. НЕ затрагивается (D1: separate responsibility)."/>
  <entity id="jsonschema_validate_py" type="FILE"
    keywords="NEW, generic, Draft7Validator, CLI, iter-errors"
    annotation="core/internal/scripts/jsonschema_validate.py (NEW ~120 LOC). Generic yaml+schema validator. Exit 0/1. Format: Error at 'path': message."/>

  <!-- Wave 2: state_machine.py stale references cleanup -->
  <entity id="state_machine_py" type="FILE"
    keywords="bootstrap, lifecycle, 14-phases, state-json, content-hash"
    annotation="2313 LOC. 14-phase model (DevPlan 087/091). 2 stale checkpoint.sh references после 091 удаления."/>
  <entity id="state_machine_L1588_path_map" type="ANCHOR"
    keywords="verify_core, content-hash, stale-ref"
    annotation="L1587-1590: path_map['verify_core'] = [lib/checkpoint.sh, content_hash.py]. checkpoint.sh удалён. W2a: удалить запись."/>
  <entity id="state_machine_FUNC_decrypt_secrets" type="METHOD"
    keywords="decrypt, bash-source-chain, stale-ref, P0"
    annotation="L1916-1936: bash -c 'source logging.sh && source checkpoint.sh && source secrets.sh'. checkpoint.sh удалён → source fails. W2b: убрать checkpoint.sh из source chain, обновить TRAP-комментарий."/>
  <entity id="content_hash_sh" type="FILE"
    keywords="comment-drift, checkpoint-callers"
    annotation="L15 comment: 'All callers (checkpoint.sh, node-lifecycle.sh)'. checkpoint.sh удалён. W2c: обновить комментарий."/>

  <!-- Wave 2: test files cleanup (QA audit 2026-07-31) -->
  <entity id="test_unit_checkpoint_v2_py" type="FILE"
    keywords="dead-tests, checkpoint-v2, DELETE, FileNotFoundError"
    annotation="tests/test_unit_checkpoint_v2.py (264 LOC). 3/4 тестов тестируют _checkpoint_version_check() из удалённого checkpoint.sh → _extract_func (L89-90) бросает FileNotFoundError. W2-T3.1: DELETE целиком (QA recommendation). Единственный живой тест test_rotate_checkpoints_removed покрывается test_node_lifecycle_static.py."/>
  <entity id="test_bootstrap_dry_run_py" type="FILE"
    keywords="mock-cleanup, checkpoint-mock, stale-fixture"
    annotation="tests/integration/test_bootstrap_dry_run.py. mock_fs L161 создаёт mock checkpoint.sh — мёртвый код после W2-T2 (checkpoint.sh не source'ится в _decrypt_secrets). W2-T5: убрать из lib_script + обновить @invariants L122."/>
  <entity id="test_node_lifecycle_static_py" type="FILE"
    keywords="docstring-drift, phase-based-checkpoint, state_machine"
    annotation="tests/test_node_lifecycle_static.py. L507-517 @purpose/@invariants утверждают 'node-lifecycle.sh sources checkpoint.sh' — ложь после 091. W2-T5.1: обновить docstring. Тело теста (L532-570) корректно (state_machine.py/BootstrapPhase/_step_hash) — не менять."/>
  <entity id="test_inventory_yaml" type="FILE"
    keywords="test-inventory, generated, re-sync, sync_inventory"
    annotation="tests/test_inventory.yaml (L1233-1236: 4 записи test_unit_checkpoint_v2). Generated — W2-T6: make test-inventory-sync после изменений тестов."/>

  <!-- CrossLinks -->
  <link from="validate_FUNC_validate_with_python" to="jsonschema_validate_py" rel="DELEGATE-TO"/>
  <link from="validate_FUNC_validate_with_python" to="validate_module_yaml_py" rel="DO-NOT-MERGE (SRP)"/>
  <link from="state_machine_L1588_path_map" to="state_machine_FUNC_decrypt_secrets" rel="SAME-ROOT-CAUSE (091 cleanup)"/>
  <link from="state_machine_FUNC_decrypt_secrets" to="content_hash_sh" rel="COMMENT-DRIFT"/>
  <link from="state_machine_FUNC_decrypt_secrets" to="test_bootstrap_dry_run_py" rel="FIXTURE-DRIFT (mock checkpoint.sh)"/>
  <link from="test_unit_checkpoint_v2_py" to="test_node_lifecycle_static_py" rel="COVERED-BY (test_rotate_checkpoints_removed)"/>
  <link from="test_unit_checkpoint_v2_py" to="test_inventory_yaml" rel="INVENTORY-DRIFT (L1233-1236)"/>
</graph>
```

---

## §3. Step-by-Step Data Flow

### Wave 1: `validate.sh` — jsonschema extraction (закрывает Tier-1 violation)

```
W1-T1. Создать core/internal/scripts/jsonschema_validate.py:
    CLI: --yaml-file <path> --schema-file <path>
    Logic: yaml.safe_load(instance) + json.load(schema) + Draft7Validator.iter_errors
    Error format: "Error at '<path>': <message>" (byte-identical существующему PYOF output — AC1)
    Exit: 0 = valid, 1 = validation errors, 2 = usage/file error
    MODULE_CONTRACT + GREP_SUMMARY + STRUCTURE + #region/#endregion paired + LDD [IMP:9]
    → verify: python3 -m core.internal.scripts.jsonschema_validate --help (no crash)

W1-T2. tests/unit/test_jsonschema_validate.py (NEW):
    Fixtures: tmp_path, valid node.yaml fixture, invalid (missing required field), invalid (type mismatch),
              malformed-yaml, missing-schema-file
    Tests: valid → exit 0; missing-field → exit 1 + error mentions field; type-mismatch → exit 1;
            multiple-errors aggregation (≥2 errors reported); malformed-yaml → exit 2;
            missing-schema → exit 2
    caplog + IMP:9 assert (ldd_trajectory decorator или assert_ldd_imp9)
    R5 anti-survivorship: negative test для каждого error-path
    → verify: pytest tests/unit/test_jsonschema_validate.py -v PASS

W1-T3. core/internal/validate/validate.sh — MODIFY:
    ЗАМЕНИТЬ тело validate_with_python() [L89-127]:
        ДО: if ! output="$(python3 - "$yaml_file" "$schema_file" <<'PYEOF' ... PYEOF)"; then
        ПОСЛЕ: if ! output="$(python3 -m core.internal.scripts.jsonschema_validate \
                --yaml-file "$yaml_file" --schema-file "$schema_file" 2>&1)"; then
    УДАЛИТЬ PYOF heredoc (L94-119, 26 LOC)
    СОХРАНИТЬ: vlog_fail "python" "${yaml_file}:\n${output}" + return 1 + vlog_ok "python" "${yaml_file}"
    ПРОВЕРИТЬ: detect_validator() L51 — require_python_module jsonschema остаётся (CLI зависит от jsonschema)
    → verify: grep -n "PYEOF\|python3 -.*<<" validate.sh = пусто
    → verify: validate.sh LOC ≤ 365 (было 380, −26 heredoc +~6 dispatch = ~360)

W1-T4. Regression smoke + integration test:
    BASELINE: capture stderr `make validate 2>&1 > /tmp/validate_baseline_before.txt` (УЖЕ сделано: exit 0)
    POST-CHANGE: capture stderr `make validate 2>&1 > /tmp/validate_baseline_after.txt`
    DIFF: `diff /tmp/validate_baseline_before.txt /tmp/validate_baseline_after.txt` → empty (AC1 byte-identical)
    tests/unit/test_validate_cli.py (NEW ~80 LOC): subprocess CLI на tmp fixtures,
        byte-comparison error output с golden baseline
    → verify: pytest tests/unit/test_validate_cli.py -v PASS
    → verify: diff baseline = empty

W1-T5. Docs:
    validate.sh GREP_SUMMARY: оставить "python-jsonschema" (всё ещё используется через CLI)
    validate.sh MODULE_CONTRACT: без изменений (внешний API immutable)
    → verify: ruff check + gate не падает на markup
```

### Wave 2: `state_machine.py` — stale checkpoint.sh references cleanup

⚠️ Зависимость: Wave 1 независим, Wave 2 можно делать параллельно (нет общих файлов). Но рекомендуется последовательно для чистого git history.

```
W2-T1. state_machine.py path_map["verify_core"] [L1587-1590]:
    ДО:
        "verify_core": [
            os.path.join(core_dir, "lib", "checkpoint.sh"),
            os.path.join(core_dir, "internal", "shared", "content_hash.py"),
        ],
    ПОСЛЕ:
        "verify_core": [
            os.path.join(core_dir, "internal", "shared", "content_hash.py"),
        ],
    ОБОСНОВАНИЕ: checkpoint.sh удалён в 091. content-hash для verify_core теперь зависит только от
        content_hash.py (который и выполняет реальную hash-логику). checkpoint.sh был в списке как
        "source of truth для checkpoint API" — больше не релевантно.
    → verify: rg "checkpoint\.sh" state_machine.py в path_map = пусто

W2-T2. state_machine.py _decrypt_secrets() [L1916-1936]:
    ДО (L1922-1932):
        # · Fix: export CORE_DIR, source logging.sh + checkpoint.sh перед secrets.sh
        logging_lib = os.path.join(core_dir, "lib", "logging.sh")
        checkpoint_lib = os.path.join(core_dir, "lib", "checkpoint.sh")
        _subprocess_run([
            "bash", "-c",
            f"export CORE_DIR={shlex.quote(core_dir)}"
            f" && source {shlex.quote(logging_lib)}"
            f" && source {shlex.quote(checkpoint_lib)}"
            f" && source {shlex.quote(secrets_lib)}"
            f" && step_10_decrypt_secrets",
        ], "decrypt_secrets")
    ПОСЛЕ:
        # · Fix: export CORE_DIR, source logging.sh перед secrets.sh
        # · (checkpoint.sh removed in DevPlan 091 — secrets.sh no longer needs step_start/done/skip
        #    from checkpoint lib; ПРЕДВЕРИФИЦИРОВАНО W2-T3: secrets.sh определяет их inline через
        #    declare -f stub-guard L117-120 — dependency на checkpoint lib отсутствует)
        logging_lib = os.path.join(core_dir, "lib", "logging.sh")
        _subprocess_run([
            "bash", "-c",
            f"export CORE_DIR={shlex.quote(core_dir)}"
            f" && source {shlex.quote(logging_lib)}"
            f" && source {shlex.quote(secrets_lib)}"
            f" && step_10_decrypt_secrets",
        ], "decrypt_secrets")
    → verify: rg "checkpoint" state_machine.py в _decrypt_secrets = пусто

W2-T3. VERIFY (ПРЕДВЕРИФИЦИРОВАНО 2026-07-31 — ответ УЖЕ известен): does secrets.sh still need
       step_start/step_done/step_skip from checkpoint.sh?
    → НЕТ, не нужно. secrets.sh содержит self-contained stub'ы с declare -f guard (L117-120):
        if ! declare -f step_start >/dev/null 2>&1; then
            step_start() { log_step "$1" "START" "${2:-}"; }
            step_done()  { log_step "$1" "DONE"  "${2:-}"; }
            step_skip()  { log_step "$1" "SKIP"  "${2:-}"; }
        fi
    → Guard определяет stub'ы ТОЛЬКО если consumer не предоставил step_* — secrets.sh source-safe
      standalone. W2-T2 (удаление checkpoint.sh из source chain) НЕ создаёт dangling refs.
    → Никаких stub'ов в logging.sh, никакого Debt 097. Открытый вопрос закрыт — реализация не должна
      его переоткрывать.
    → verify: grep -n "step_start\|declare -f" core/lib/secrets.sh → stub-guard L117-120 (зафиксировать в VR)

W2-T3.1. tests/test_unit_checkpoint_v2.py — DELETE (264 LOC):
    РЕШЕНИЕ (рекомендация QA 2026-07-31): удалить файл целиком.
    ОБОСНОВАНИЕ: 3 из 4 тестов (test_version_mismatch_invalidates_all,
        test_version_match_preserves_checkpoints, test_no_version_file_treats_as_mismatch)
        тестируют _checkpoint_version_check() из удалённого core/lib/checkpoint.sh — логика мертва,
        _extract_func (L89-90) бросает FileNotFoundError.
    test_rotate_checkpoints_removed — единственный живой тест, но его инвариант
        (rotate_checkpoints() отсутствует в node-lifecycle.sh) уже покрывается
        test_node_lifecycle_static.py::test_checkpoint_step_uses_content_hash и DEAD-code аудитом 091.
        Переписывать файл бессмысленно — тестируемая логика не существует.
    → verify: git rm tests/test_unit_checkpoint_v2.py
    → verify: python -m pytest tests/ --collect-only -q | grep test_unit_checkpoint_v2 = пусто

W2-T4. content-hash.sh comment [L15]:
    ДО: ##   - All callers (checkpoint.sh, node-lifecycle.sh) work without changes
    ПОСЛЕ: ##   - All callers (node-lifecycle.sh, state_machine.py) work without changes
    → verify: rg "checkpoint\.sh" content-hash.sh = пусто (только в comment, не в logic — logic уже content_hash.py)

W2-T5. Consumer verification + test_bootstrap_dry_run.py mock cleanup:
    tests/integration/test_bootstrap_dry_run.py — MODIFY:
        - L161: убрать "checkpoint.sh" из lib_script ["secrets.sh", "logging.sh", "checkpoint.sh"]
          → ["secrets.sh", "logging.sh"] (mock мёртв после W2-T2 — checkpoint.sh не source'ится
          в _decrypt_secrets)
        - L122: обновить @invariants "lib/ scripts (secrets.sh, logging.sh, checkpoint.sh)" →
          "(secrets.sh, logging.sh)"
    pytest tests/unit/test_state_machine.py -v → green (AC5)
    pytest tests/integration/test_bootstrap_dry_run.py -v → green (AC5)
    → verify: оба тест-набора PASS, нет regression
    → verify: rg "checkpoint" tests/integration/test_bootstrap_dry_run.py = пусто

W2-T5.1. tests/test_node_lifecycle_static.py — MODIFY (docstring-only):
    L507-517: @purpose + @invariants утверждают "node-lifecycle.sh sources lib/checkpoint.sh" —
    ложь после 091. Обновить на phase-based checkpoint:
        @purpose: W4-E5 edge-case: verify node-lifecycle.sh delegates checkpoint-resume to
                  state_machine.py (phase-based, content-hash idempotency).
        @invariants: заменить "sources lib/checkpoint.sh" → "delegates to state_machine.py";
                     оставить "sources content-hash.sh" (актуально).
    Тело теста (L532-570) НЕ менять — уже корректно проверяет state_machine.py, BootstrapPhase,
    _step_hash, phases.py (14 phases).
    → verify: rg "checkpoint\.sh" tests/test_node_lifecycle_static.py = пусто

W2-T6. tests/test_inventory.yaml — re-sync:
    make test-inventory-sync  (tests/tools/sync_inventory.py)
    → inventory отражает актуальный набор тестов после W2-T3.1 (DELETE) + W2-T5/T5.1 (MODIFY):
      исчезнут L1233-1236 (4 записи test_unit_checkpoint_v2.py)
    → verify: git diff tests/test_inventory.yaml = только ожидаемые изменения
```

---

## §4. Architecture & Decisions

### DD1: Почему новый `jsonschema_validate.py`, а не расширение `validate_module_yaml.py`?

**Q:** Бриф требует «расширить существующий». Почему новый файл?

**A:** `validate_module_yaml.py` — **semantically coupled** к module.yaml D5-контракту (env_requires normalization, secrets-manifest cross-check, restart-drift vs docker-compose.base.yml, 638 LOC с 3 cross-check'ами). Generic jsonschema-валидация `node.yaml`/`ai-platform.yaml` туда не лезет без искажения `@purpose`. Principle 8 (AI-First Architecture): один модуль = одна ответственность. Цена: +1 файл (~120 LOC). Выгода: SRP сохранён, оба CLI независимо тестируемы.

⚠️ TRAP[DECISION] · 2026-07-31 · MED · Не расширять `validate_module_yaml.py`
· Rejected: расширение (риск: SRP нарушение, смешение generic-schema и D5-specific logic, god-module 800+ LOC)
· Reason: `validate_module_yaml.py` уже 638 LOC с 3 cross-check'ами. Добавление generic валидатора → god-module.
· Rev: если `jsonschema_validate.py` начнёт включать module-specific checks → merge назад в `validate_module_yaml.py`.

### DD2: Почему Wave 2 = cleanup, а не «восстановить checkpoint.sh»?

**Q:** checkpoint.sh удалён в 091, но state_machine.py всё ещё его source'ит. Может, нужно восстановить checkpoint.sh?

**A:** Нет. 091 удалил checkpoint.sh намеренно (commit msg explicit: "Remove legacy core/lib/checkpoint.sh"). Stale references в state_machine.py — это незакрытый долг scaffold-коммита 091 (scaffold = частичная реализация). Корректное действие: убрать stale references, довести 091 до конца. Восстановление checkpoint.sh откатило бы 091 и вернуло dead code.

⚠️ TRAP[DECISION] · 2026-07-31 · HI · Cleanup stale refs, НЕ восстановление checkpoint.sh
· Rejected: восстановить checkpoint.sh (риск: откат 091, возврат dead code, нарушение Decision Gate 087)
· Reason: 091 User Constraint (тестовая фаза, backward-compat удаляется полностью). checkpoint.sh = vestigial API поверх state_machine.py.
· Constraint: Восстановление checkpoint.sh требует explicit user instruction + revert 091.
· Rev: если bootstrap падает на clean install из-за отсутствия step_* functions → secrets.sh migration в отдельный DevPlan 097.

### DD3: Stale references severity — почему L1924 = HI, L1588 = MED?

**L1924 (`_decrypt_secrets` source chain):** `source checkpoint.sh` на несуществующем файле → bash error exit ≠ 0 → `_subprocess_run` raises → `RuntimeError` → bootstrap FATAL на clean install (φ4 secrets-provision блокирует φ6/φ8). Это **HI P0** для любого свежего bootstrap.

**L1588 (`path_map["verify_core"]`):** `os.path.join(...)` строит строку пути. `content_hash.py` / `_step_hash` проверяет существование файлов перед хешированием (или тихо пропускает несуществующие). Hash вычисляется по `content_hash.py` alone — функционально не ломается, но reference вводит в заблуждение future maintainers. **MED** (cosmetic + потенциальный warning в логах).

---

## §5. File Manifest

| Файл | Действие | LOC (до → после) | AC | Wave |
|------|----------|------------------|----|------|
| `core/internal/scripts/jsonschema_validate.py` | NEW | 0 → ~120 | AC3, AC6, AC7a, AC7b | W1 |
| `core/internal/validate/validate.sh` | MODIFY | 380 → ~360 | AC1, AC3, AC6 | W1 |
| `tests/unit/test_jsonschema_validate.py` | NEW | 0 → ~150 | AC7a, AC7b | W1 |
| `tests/unit/test_validate_cli.py` | NEW | 0 → ~80 | AC1 | W1 |
| `core/internal/bootstrap/lifecycle/state_machine.py` | MODIFY | 2313 → ~2305 | AC4, AC5 | W2 |
| `core/internal/bootstrap/content-hash.sh` | VERIFY/MINOR | ~0 | AC4 | W2 |
| `tests/test_unit_checkpoint_v2.py` | DELETE | 264 → 0 | — | W2 |
| `tests/integration/test_bootstrap_dry_run.py` | MODIFY | 1029 → ~1027 | AC5 | W2 |
| `tests/test_node_lifecycle_static.py` | MODIFY | 774 → 774 (docstring-only) | — | W2 |
| `tests/test_inventory.yaml` | MODIFY (re-sync) | generated | AC8 | W2 |

---

## §6. Out of Scope

- ❌ Восстановление `core/lib/checkpoint.sh` (DD2) — откат 091
- ❌ Миграция `secrets.sh` на прямой `secrets_manager.py` import (→ DevPlan 097 если W2-T3 найдёт dangling step_* refs)
- ❌ Рефакторинг `validate_module_yaml.py` архитектуры (Anti-Loop Note брифа принят)
- ❌ Изменение `state_machine.py` фазовой модели (14 phases stable)
- ❌ Изменение schema-файлов (`core/schemas/*.schema.json`) — read-only consumers
- ❌ `check_fqdn_conflict()` / `check_port_conflict()` в `validate.sh` — не содержат inline python3 (используют `node_yaml` CLI), не трогать
- ❌ Расширение `validate_module_yaml.py` generic-логикой (DD1)

---

## §7. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| `validate_with_python` output format меняется → CI/logs diff | MED | W1-T4 byte-comparison baseline. Format `Error at 'path': msg` сохранён 1:1 в jsonschema_validate.py. AC1 = diff empty |
| `jsonschema_validate.py` exit-code semantics отличается (2 vs 1 для file errors) | LOW | W1-T1: exit 0=valid, 1=validation, 2=usage/file. validate.sh ловит любой ≠0 через `if !`. Документировано в CLI --help |
| `secrets.sh` имеет dangling `step_start/step_done/step_skip` после удаления checkpoint.sh из source chain | HI P0 | ЗАКРЫТ предверификацией (W2-T3): secrets.sh содержит declare -f stub-guard L117-120 — self-contained, source-safe standalone. Risk не материализуется; Debt 097 не требуется |
| `_step_hash` поведение меняется при удалении checkpoint.sh из path_map | MED | W2-T1: content_hash.py выполняет реальную hash-логику; checkpoint.sh был informational. Unit-test test_state_machine.py покрывает hash (AC5) |
| `jsonschema_validate.py` не зарегистрирован в entrypoint-manifest | LOW | `scripts-audit` gate: jsonschema_validate.py — internal CLI (не entrypoint), регистрация не требуется. Проверить gate |
| Ruff/bandit/markup gate падает на новом Python файле | LOW | `make fix-gate` (ruff format + check --fix) перед commit. MODULE_CONTRACT markup на новом файле |

---

## §8. Verification Plan

### Baseline capture (before implementation)
```bash
make validate 2>/tmp/validate_baseline_before.txt   # exit 0 (УЖЕ подтверждено 2026-07-31)
make gate MODE=fast 2>&1 | tee /tmp/gate_baseline_before.log
```

### Pre-merge (per wave)
```bash
# Wave 1:
grep -rn 'python3.*<<\|python3 -c' core/internal/validate/validate.sh  # → 0 (AC3)
make validate 2>/tmp/validate_baseline_after.txt
diff /tmp/validate_baseline_before.txt /tmp/validate_baseline_after.txt  # → empty (AC1)
pytest tests/unit/test_jsonschema_validate.py tests/unit/test_validate_cli.py -v  # → green

# Wave 2:
grep -rn 'checkpoint\.sh' core/internal/bootstrap/lifecycle/state_machine.py  # → 0 в executable (AC4)
rg -n 'checkpoint\.sh' tests/  # → 0 (после W2-T3.1/T5/T5.1 — residual-pattern check по всем тестам)
pytest tests/unit/test_state_machine.py -v  # → green (AC5)
pytest tests/integration/test_bootstrap_dry_run.py -v  # → green (AC5)
make test-inventory-sync  # → test_inventory.yaml re-sync после изменений тестов (W2-T6)
git diff --stat tests/test_inventory.yaml  # → только ожидаемые изменения (исчезли L1233-1236)
```

### Post-merge (gate)
```bash
make fix-gate && git add -u
make gate MODE=fast  # → green (AC8)
```

### VerificationReport outline (генерируется после реализации)
1. **AC Matrix** — AC1-AC8 с evidence (команды + output)
2. **Diagnosis reconciliation** — D1-D4: подтверждение, что реализация следует коду, не брифу
3. **LDD trajectory** — пример IMP:9 из `test_jsonschema_validate.py`
4. **W2-T3 finding** — предверифицирован: declare -f stub-guard L117-120 в secrets.sh, dangling refs отсутствуют
5. **Regression diff** — `make validate` output byte-comparison (AC1)
6. **Inline-python audit** — grep по `core/internal/validate/` показывает 0 (AC3)
7. **Stale-ref audit** — grep `checkpoint.sh` по `state_machine.py` показывает 0 (AC4)
8. **Test cleanup audit** — test_unit_checkpoint_v2.py удалён (W2-T3.1), mock checkpoint.sh убран из test_bootstrap_dry_run.py (W2-T5), docstring test_node_lifecycle_static.py обновлён (W2-T5.1), inventory re-synced (W2-T6)

---

## §9. Implementation Order (для Coder)

```
[baseline capture]
  make validate > /tmp/validate_baseline_before.txt 2>&1

Wave 1 (validate.sh jsonschema extraction):
  coder W1-T1 (jsonschema_validate.py)
    → W1-T2 (tests/unit/test_jsonschema_validate.py)
    → W1-T3 (validate.sh: replace PYOF heredoc)
    → W1-T4 (regression smoke + tests/unit/test_validate_cli.py)
    → W1-T5 (docs/grep_summary verify)

  QA Wave 1: pytest tests/unit/test_jsonschema_validate.py tests/unit/test_validate_cli.py -v
             + make validate diff baseline = empty

Wave 2 (state_machine.py stale refs cleanup):
  coder W2-T1 (path_map["verify_core"] remove checkpoint.sh entry)
    → W2-T2 (_decrypt_secrets source chain remove checkpoint.sh)
    → W2-T3 (VERIFY secrets.sh dangling refs — ПРЕДВЕРИФИЦИРОВАНО: declare -f stub-guard L117-120)
    → W2-T3.1 (DELETE tests/test_unit_checkpoint_v2.py)
    → W2-T4 (content-hash.sh comment update)
    → W2-T5 (consumer verification + test_bootstrap_dry_run.py mock cleanup)
    → W2-T5.1 (docstring update tests/test_node_lifecycle_static.py)
    → W2-T6 (make test-inventory-sync)

  QA Wave 2: pytest tests/unit/test_state_machine.py tests/integration/test_bootstrap_dry_run.py -v
             + grep checkpoint.sh state_machine.py = 0

[post-merge gate]
  make fix-gate && git add -u && make gate MODE=fast

VerificationReport → 02-VerificationReport.md
```

---

## §10. Notes for downstream plans

- **DevPlan 097 (НЕ требуется — W2-T3 предверифицирован):** «secrets.sh migration to secrets_manager.py» — изначально планировался если W2-T3 найдёт dangling step_* refs. Факт: secrets.sh содержит declare -f stub-guard L117-120 (self-contained, source-safe). DevPlan 097 отменяется до появления реального step_* dependency. Предпосылка остаётся: `core/internal/scripts/secrets_manager.py` должен покрывать decrypt workflow без source-chain.
- **AGENTS.md языковая политика:** после 093 пересчитать «inline python3» метрику для Decision Gate (TRAP 2026-07-22). validate.sh был последним PYOF в core/internal/validate/.
- **DevPlan 091 завершение:** W2 этого плана формально закрывает незакрытый долг 091 (stale references после scaffold commit 8be2843). Рекомендуется упомянуть в финальном VR 091.

$END_DEVPLAN
