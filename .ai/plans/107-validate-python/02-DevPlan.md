$START_DEVPLAN

# DevPlan 107 — Validate Python Migration (orchestrator extraction)

$ARTIFACT_CONTRACT
PURPOSE:               Мигрировать `core/internal/validate/validate.sh` (251 LOC) → Python-модуль `validate_orchestrator.py` + тонкий shell-фасад (≤50 LOC). Завершает Strangler-декомпозицию области validate: jsonschema уже в Python (DevPlan 093 W1), conflict checks уже в Python (Strangler 2026-07-31), остаётся чистая оркестрация — file discovery, schema resolution, validator selection, lint routing, error aggregation.
DESCRIPTION:           Все «тяжёлые» операции validate.sh уже делегированы в Python-CLI: `jsonschema_validate.py` (093 W1, `core.internal.scripts`), `conflict_checks.py` (check-fqdn/check-ports). В shell осталась ОРКЕСТРАЦИЯ: auto-discovery (find), schema routing (case node/module/ai-platform), detect_validator (ajv|python), lint-mode routing, счётчик ошибок, exit-code. План: перенести оркестрацию в `core/internal/validate/validate_orchestrator.py` (namespace-пакет, invocation `python3 -m core.internal.validate.validate_orchestrator`), свести `validate.sh` к фасаду: arg parsing → вызов Python → exit code. Сохранить байт-идентичный формат stderr-сообщений `[IMP:N][validate][block] msg`, schema-routing 1:1, спец-флаги `--check-fqdn`/`--check-ports`, exit-коды (0=ok, 1=errors). Python-порт ИСПРАВЛЯЕТ латентный баг auto-discovery (BSD `sort -z` → один NUL-record + trailing `\n` corruption — см. §0 D3), не реплицирует его.
RATIONALE:             251 LOC — последний «толстый» internal-скрипт валидации; jsonschema и conflict-checks уже в Python (093, Strangler 2026-07-31) — естественное завершение миграции (Brief AC-контекст). После миграции все validate-скрипты >200 LOC будут в Python. Языковая политика (AGENTS.md §Языковая политика): новый код только Python, bash = тонкий фасад. Фасад сохраняет контракты `core/entrypoints/validate.sh` (18 LOC, НЕ трогаем), `make validate`, `make lint`, manifest `delegates_to` chain.
ACCEPTANCE_CRITERIA:
  AC1: `core/internal/validate/validate_orchestrator.py` — file discovery + schema resolution + validation orchestration (существование + unit-тесты).
  AC2: Shell-фасад `core/internal/validate/validate.sh` ≤ 50 LOC (было 251).
  AC3: `make validate` проходит идентично (exit 0; stderr-diff internal-части = пусто ИЛИ только задокументированное удаление blank-line артефакта бага discovery — §0 D3).
  AC4: `make validate FILES=...` работает идентично (FILES НЕ пробрасывается Makefile-таргетом — §0 D4; реальный путь: `bash core/entrypoints/validate.sh <files>` — файлы валидируются identically).
  AC5: `make lint` (validate --lint) работает идентично (flag-only args → auto-discovery SKIPPED → 0 файлов → exit 0 — воспроизводится точно, §0 D2).
  AC6: Авто-обнаружение схем идентично для существующих routing: node.yaml→node.schema.json, module.yaml→module.schema.json, ai-platform.yaml→ai-platform.schema.json (llm-policy.yaml → skip как non-declaration). BRIEF-DIVERGENCE: «platform-env.schema.json» в Brief НЕ существует в core/schemas/ (§0 D1) — routing воспроизводит ТОЛЬКО фактический case из validate.sh.
  AC7: Все ошибки валидации выводятся с теми же путями и сообщениями (байт-идентичный формат `[IMP:N][validate][block] msg`, error format `FAIL: <file>:\n<output>` от jsonschema_validate и `FAIL: <file>: <output>` от ajv).
  AC8: `make gate MODE=fast` зелёный (включая manifest contract: test_make_target_contracts.py, test_contract_entrypoints.py, test_inventory.yaml re-sync).
  AC9: DevPlan 093 AC3 не регрессирует: `grep -rn 'python3.*<<\|python3 -c' core/internal/validate/validate.sh` → 0.
IMPLEMENTS:            Brief 107 — миграция validate.sh оркестрации в Python. Закрывает Tier-2 Strangler-порог для `core/internal/validate/` (все ответственные скрипты области >200 LOC теперь Python).
IMPACTS:
  - `core/internal/validate/validate.sh` — MODIFY (251 → ≤50 LOC, фасад)
  - `core/internal/validate/validate_orchestrator.py` — NEW (~280 LOC)
  - `core/entrypoint-manifest.yaml` — MODIFY (structural секция delegates_to для validate/lint, ручное редактирование — генератор её сохраняет verbatim; §7 T6)
  - `core/AGENTS.md` — MODIFY (generated canonical table, через `make generate-manifests`)
  - `tests/unit/test_validate_orchestrator.py` — NEW (~300 LOC)
  - `tests/test_inventory.yaml` — MODIFY (re-sync через `make test-inventory-sync`)
REQUIRES:
  - DevPlan 093 STABLE — `jsonschema_validate.py` (core/internal/scripts/, exit 0/1/2, error format `  Error at '<path>': <message>`, stderr-only). ✅ Подтверждено чтением файла (211 LOC, полный MODULE_CONTRACT).
  - Strangler 2026-07-31 STABLE — `conflict_checks.py` (core/internal/validate/, CLI check-fqdn/check-ports, exit 0/1). ✅ Подтверждено чтением файла (177 LOC).
  - `core/internal/validate/` — namespace-пакет БЕЗ `__init__.py` (подтверждено glob: только conflict_checks.py, validate.sh, __pycache__). ✅ `python3 -m core.internal.validate.conflict_checks` работает.
  - `core/schemas/` содержит: node.schema.json, module.schema.json, ai-platform.schema.json, llm-policy.schema.json. ✅ (platform-env.schema.json ОТСУТСТВУЕТ — D1)
  - `make validate` / `make lint` baseline green (2026-07-31, эмпирически подтверждено).
$END_ARTIFACT_CONTRACT

---

## §0. Diagnosis — фактическое состояние кода vs Brief

Аудит 2026-07-31 (чтение исходников + эмпирические прогоны). Brief 107 содержит 4 неточности (D1-D4), влияющие на AC-трактовку. Реализация следует КОДУ, не брифу (прецедент: DevPlan 093 Rev 2).

### D1: «platform-env.schema.json» не существует — фактический schema-routing ровно 3 схемы

- **Brief AC6:** "Авто-обнаружение схем идентично (module.schema.json, platform-env.schema.json, etc.)".
- **Реальность:** `core/schemas/` содержит 4 файла: `node.schema.json`, `module.schema.json`, `ai-platform.schema.json`, `llm-policy.schema.json` (подтверждено glob). `validate.sh` case-выражение (L223-238) маршрутизирует ТОЛЬКО: `node.yaml|node.yml → node.schema.json`, `module.yaml|module.yml → module.schema.json`, `ai-platform.yaml|ai-platform.yml → ai-platform.schema.json`, всё остальное (включая llm/policy.yaml!) → skip "Skipping non-declaration file".
- **Следствие:** AC6 реализуется как «routing 1:1 по фактическому case», llm-policy.schema.json НЕ подключается (вне текущего контракта). Добавление routing для llm-policy.yaml — OUT OF SCOPE (изменение контракта, требует отдельного решения).

### D2: Краткое описание «Lint mode vs validate mode routing» — фактически lint-режима НЕТ

- **Brief:** "Lint mode vs validate mode routing".
- **Реальность:** В `main()` validate.sh НЕТ обработки `--lint` как отдельного режима. `--lint` — обычный arg: попадает в `targets`, НЕ матчит `--check-fqdn`/`--check-ports`, в цикле отфильтровывается `[[ "$file" == --* ]] && continue`. Никакого lint-поведения. Entrypoint-комментарий ("--lint flag triggers lint mode") — устаревший.
- **Следствие:** `make lint` = `validate.sh --lint` → targets=("--lint") непусто → auto-discovery ПРОПУЩЕН (условие `${#targets[@]} -eq 0`) → 0 файлов → `[IMP:8][validate][result] OK: All files valid` → exit 0. Эмпирически подтверждено (2026-07-31). Оркестратор воспроизводит ТОЧНО: args непустые → discovery не запускается, `--*` аргументы скипаются в цикле.

### D3: Auto-discovery `find | sort -z | read -d ''` — латентный баг (macOS/BSD sort)

- **Brief:** "File discovery (find command with null-delimited output)".
- **Реальность (эмпирически подтверждено):** BSD `sort -z` ожидает NUL-разделённые записи, `find` выдаёт `\n`-разделённые. Результат: ВЕСЬ вывод find становится ОДНИМ record'ом (без NUL внутри), `sort -z` дописывает один NUL в конец, `read -r -d ''` читает весь blob как ОДИН target, включая trailing `\n`. При 1 файле: `TARGET=[core/internal/foo/node.yaml$'\n']`, `basename` = "node.yaml" (NUL-record обрезает \n только в командной подстановке), но `[[ -f "$f" ]]` → `FILE-EXISTS: NO` — trailing `\n` в имени файла → ложный "File not found". При N>1 файлов: blob из всех путей → basename = последний компонент последнего пути → chaos.
- **Следствие:** текущий auto-discovery в shell фактически НЕ может валидировать auto-найденные declaration-файлы (только explicit args работают корректно). На текущем репо дереве (единственный yaml — core/internal/llm/policy.yaml, non-declaration) баг маскируется: policy.yaml → skip → exit 0.
- **Решение (TRAP[DECISION]):** Python-порт реализует КОРРЕКТНЫЙ discovery (`os.walk` + sorted, без NUL-артефактов). На текущем дереве результат байт-идентичен (policy.yaml → skip → exit 0), diff `make validate` = пусто. При наличии declaration-файлов Python их ВАЛИДИРУЕТ (исправление бага), shell — ложно "File not found". Это осознанное исправление, а не регрессия — задокументировано в TRAP.

### D4: `make validate FILES=...` — Makefile НЕ пробрасывает FILES

- **Brief AC4:** "make validate FILES=... работает идентично".
- **Реальность:** Makefile-таргет `validate:` (makefiles/ci.mk L244-247): `@bash $(_platform_root)/core/entrypoints/validate.sh` — БЕЗ `$(FILES)`. Переменная `FILES` нигде не используется для validate (grep по makefiles/*.mk = 0). Manifest `signature: make validate [FILES=...]` — устаревшая сигнатура (документирует намерение, не поведение).
- **Следствие:** AC4 проверяется через РЕАЛЬНЫЙ путь передачи файлов: `bash core/entrypoints/validate.sh <file...>` (проходит args через entrypoint → internal фасад → orchestrator). Оркестратор принимает позиционные аргументы-файлы и валидирует их identically (эмпирически: explicit file arg работает в текущем shell). Изменять Makefile — OUT OF SCOPE (поведение make validate FILES=... остаётся «FILES игнорируется» — идентично).

### D5: Порядок операций в ai-platform.yml ветке — extension check НЕ short-circuit'ит

- **validate.sh L230-234:** для `ai-platform.yaml|ai-platform.yml`: `check_project_extension "$file"` (при .yml → `vlog_fail "extension" ...` + ERRORS++, возврат 1 ИГНОРИРУЕТСЯ), затем `vlog_info "migration" ...`, затем `validate_file` (валидация ВСЁ РАВНО выполняется).
- **Следствие:** ai-platform.yml получает И extension-error, И schema-валидацию. Оркестратор воспроизводит оба шага (extension-проверка не прерывает выполнение ветки).

### D6: Модульный путь jsonschema_validate — подтверждён, НЕ перемещать

- **Brief:** "module path `core.internal.scripts.jsonschema_validate` vs `core.internal.validate.jsonschema_validate` needs checking".
- **Реальность:** файл в `core/internal/scripts/jsonschema_validate.py`, вызывается `python3 -m core.internal.scripts.jsonschema_validate` (093 закрепил путь; test_validate_cli.py и test_jsonschema_validate.py зависят от него). `core/internal/validate/` — namespace-пакет, НЕ содержит jsonschema_validate.
- **Решение:** путь НЕ менять. Оркестратор вызывает subprocess `python3 -m core.internal.scripts.jsonschema_validate --yaml-file X --schema-file Y` (cwd=repo root) — байт-идентично текущему shell. Перенос файла = регрессия 093 тестов — OUT OF SCOPE.

---

## §1. Draft Code Graph (XML)

```xml
<graph version="107-r1">
  <!-- Архитектурная цель: shell-фасад → Python orchestrator → существующие Python-CLI -->

  <!-- === Контракты, остающиеся в shell (не трогаем) === -->
  <entity id="entrypoint_validate_sh" type="FILE"
    keywords="entrypoint, make-validate, make-lint, thin-facade, 18-LOC"
    annotation="core/entrypoints/validate.sh (18 LOC). НЕ МЕНЯЕТСЯ. exec internal/validate/validate.sh с передачей всех args."/>

  <!-- === Целевой оркестратор (NEW) === -->
  <entity id="validate_orchestrator_py" type="FILE"
    keywords="NEW, orchestrator, discovery, schema-resolution, validator-selection, lint-routing, error-aggregation"
    annotation="core/internal/validate/validate_orchestrator.py (~280 LOC, namespace-пакет без __init__.py). Python-порт main()/validate_file()/check_project_extension()/detect_validator()/validate_with_ajv()/validate_with_python()/check_fqdn_conflict()/check_port_conflict() из validate.sh. Сохраняет байт-идентичный stderr-формат [IMP:N][validate][block] msg. Исправляет D3 (корректный discovery). CLI: python3 -m core.internal.validate.validate_orchestrator [--check-fqdn DIR|--check-ports [BASE]|FILES...|--lint]."/>

  <entity id="orch_FUNC_main" type="METHOD"
    keywords="cli-entry, special-flags, args, exit-code"
    annotation="Арг-парсинг: --check-fqdn (arg required, иначе [IMP:10][validate][fqdn] ERROR + exit 1), --check-ports (arg→PROJECTS_BASE→core/projects fallback), иначе targets. targets пусто → auto-discovery (os.walk root=core/internal, *.yaml+*.yml, sorted). Фильтр --* args в цикле. Итог: ERRORS>0 → [IMP:9][validate][result] FAIL: N validation error(s) found + exit 1; иначе [IMP:8][validate][result] OK: All files valid + exit 0."/>

  <entity id="orch_FUNC_detect_validator" type="METHOD"
    keywords="ajv, python, shutil.which, find_spec"
    annotation="shutil.which('ajv') → 'ajv'; elif importlib.util.find_spec('jsonschema') → 'python'; else [IMP:10][validate][detect] ERROR: No validator found. Install: npm install -g ajv-cli ajv-formats  OR  pip3 install jsonschema pyyaml + exit 1 (байт-идентично L54-56)."/>

  <entity id="orch_FUNC_discover_targets" type="METHOD"
    keywords="os.walk, yaml, yml, sorted, core-internal"
    annotation="root = Path(__file__).resolve().parents[2]/internal? НЕТ: root = Path(__file__).resolve().parent.parent = core/internal (эквивалент SCRIPT_DIR/.. из shell). os.walk, сбор *.yaml+*.yml, sorted() — детерминированно. ИСПРАВЛЯЕТ D3: корректные пути без trailing \n."/>

  <entity id="orch_FUNC_resolve_schema" type="METHOD"
    keywords="basename, node, module, ai-platform, case-routing"
    annotation="Роутинг 1:1 по case из validate.sh L223-238: node.yaml|node.yml → node.schema.json; module.yaml|module.yml → module.schema.json; ai-platform.yaml|ai-platform.yml → ai-platform.schema.json; иначе None (skip). Schemas dir = Path(__file__).resolve().parents[2]/schemas = core/schemas."/>

  <entity id="orch_FUNC_validate_file" type="METHOD"
    keywords="file-exists, schema-exists, ajv, python, routing"
    annotation="Проверки [[ -f ]] → vlog_fail file/schema. vlog_info 'Validating: <file> against <schema_basename>'. Диспетчеризация по validator: ajv → validate_with_ajv, python → validate_with_python. Воспроизводит D5: ai-platform ветка вызывает check_project_extension (fail → ERRORS++, НЕ прерывает) → migration INFO → validate_file."/>

  <entity id="orch_FUNC_validate_with_python" type="METHOD"
    keywords="subprocess, jsonschema_validate, exit-code, FAIL-format"
    annotation="subprocess.run([sys.executable, '-m', 'core.internal.scripts.jsonschema_validate', '--yaml-file', f, '--schema-file', s], cwd=REPO_ROOT, capture). rc!=0 → vlog_fail 'python' f'{file}:\n{output}' (байт-идентично L99-100); иначе vlog_ok 'python' file. REPO_ROOT = Path(__file__).resolve().parents[3]."/>

  <entity id="orch_FUNC_validate_with_ajv" type="METHOD"
    keywords="subprocess, node_yaml, json-output, ajv-cli, tempfile"
    annotation="mktemp-эквивалент (tempfile.NamedTemporaryFile/delete=False + finally cleanup). subprocess [sys.executable, '-m', 'core.internal.shared.node_yaml', '--file', f, '--json-output'] (cwd=REPO_ROOT) → tmp json; fail → vlog_fail 'ajv' 'Failed to parse YAML: <file>'. Затем [ajv_bin, 'validate', '-s', schema, '-d', tmp, '--errors=text', '--all-errors']; fail → vlog_fail 'ajv' f'{file}: {output}'; ok → vlog_ok 'ajv' file. Байт-идентично L62-85."/>

  <entity id="orch_FUNC_check_fqdn_port" type="METHOD"
    keywords="subprocess, conflict_checks, check-fqdn, check-ports, exit-passthrough"
    annotation="subprocess [sys.executable, '-m', 'core.internal.validate.conflict_checks', 'check-fqdn'|'check-ports', arg] (cwd=REPO_ROOT), return child rc. Для check-ports default base: arg → PROJECTS_BASE env → core/projects (если dir существует) → ''. Байт-идентично L174-194."/>

  <entity id="orch_FUNC_emit" type="METHOD"
    keywords="stderr, IMP-format, byte-identical, logging"
    annotation="Внутренний эмиттер: пишет '[IMP:{imp}][validate][{block}] {msg}' в stderr И дублирует в logger.info для caplog (LDD telemetry в тестах). Формат байт-идентичен log_imp() из core/lib/logging.sh с prefix=validate."/>

  <!-- === Существующие Python-CLI (НЕ трогаем) === -->
  <entity id="jsonschema_validate_py" type="FILE"
    keywords="EXISTING, 093, generic, Draft7Validator, exit-0-1-2, stderr-only"
    annotation="core/internal/scripts/jsonschema_validate.py (211 LOC, DevPlan 093 W1). Вызывается orchestrator'ом subprocess'ом. Path НЕ меняется (D6)."/>

  <entity id="conflict_checks_py" type="FILE"
    keywords="EXISTING, strangler, fqdn, ports, CLI, exit-0-1"
    annotation="core/internal/validate/conflict_checks.py (177 LOC, Strangler 2026-07-31). Вызывается orchestrator'ом subprocess'ом. CLI check-fqdn <dir> | check-ports [base]."/>

  <entity id="node_yaml_py" type="FILE"
    keywords="EXISTING, shared, yaml-json, --json-output, facade"
    annotation="core/internal/shared/node_yaml.py — CLI --file X --json-output (L1422). Используется ajv-путём для YAML→JSON конвертации."/>

  <!-- === Shell-фасад (MODIFY) === -->
  <entity id="validate_sh_facade" type="FILE"
    keywords="MODIFY, facade, le-50-LOC, exec-python"
    annotation="core/internal/validate/validate.sh 251→≤50 LOC. Сохраняет GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT/TRAPs, set -euo pipefail, SCRIPT_DIR, exec python3 -m core.internal.validate.validate_orchestrator \"$@\". НЕ source'ит logging.sh/python_deps.sh (вся логика в Python). НЕ содержит inline python3 heredoc (AC9)."/>

  <!-- === Manifest + generated === -->
  <entity id="entrypoint_manifest_yaml" type="FILE"
    keywords="MODIFY, delegates_to, structural-section, validate, lint"
    annotation="core/entrypoint-manifest.yaml: delegates_to для validate (L107) и lint (L113) → добавить ' → core/internal/validate/validate_orchestrator.py'. Structural секция — генератор сохраняет verbatim (подтверждено чтением generate_entrypoint_manifest.py: 'loads STRUCTURAL sections from existing manifest')."/>

  <entity id="core_agents_md" type="FILE"
    keywords="generated, canonical-table, G5"
    annotation="core/AGENTS.md canonical table (make validate/make lint rows) — регенерируется через make generate-manifests после правки manifest."/>

  <!-- === Tests === -->
  <entity id="test_validate_orchestrator_py" type="FILE"
    keywords="NEW, unit, discovery, routing, exit-codes, caplog, tmp_path"
    annotation="tests/unit/test_validate_orchestrator.py (~300 LOC). Native imports (core.internal.validate.validate_orchestrator), tmp_path, caplog + LDD IMP:9, TRAP[TEST] на каждую функцию. Тесты: discovery, schema-routing, extension-check, detect_validator (monkeypatch shutil.which/find_spec), error aggregation (exit 1), --* filtering (lint-поведение D2), --check-fqdn/--check-ports delegation (monkeypatch subprocess.run)."/>

  <entity id="test_inventory_yaml" type="FILE"
    keywords="generated, re-sync, sync_inventory"
    annotation="tests/test_inventory.yaml — make test-inventory-sync после добавления тестового файла."/>

  <!-- CrossLinks -->
  <link from="entrypoint_validate_sh" to="validate_sh_facade" rel="EXEC-UNCHANGED"/>
  <link from="validate_sh_facade" to="validate_orchestrator_py" rel="EXEC (python3 -m)"/>
  <link from="orch_FUNC_validate_with_python" to="jsonschema_validate_py" rel="DELEGATE-TO (subprocess, path UNCHANGED)"/>
  <link from="orch_FUNC_validate_with_ajv" to="node_yaml_py" rel="DELEGATE-TO (subprocess, --json-output)"/>
  <link from="orch_FUNC_check_fqdn_port" to="conflict_checks_py" rel="DELEGATE-TO (subprocess, exit passthrough)"/>
  <link from="orch_FUNC_resolve_schema" to="validate_orchestrator_py" rel="SCHEMA-ROUTING (core/schemas)"/>
  <link from="validate_orchestrator_py" to="test_validate_orchestrator_py" rel="UNDER-TEST"/>
  <link from="entrypoint_manifest_yaml" to="core_agents_md" rel="GENERATES (canonical table)"/>
  <link from="test_validate_orchestrator_py" to="test_inventory_yaml" rel="INVENTORY (re-sync)"/>
</graph>
```

---

## §2. Step-by-Step Data Flow (процессная симуляция миграции)

### Поток 1: `make validate` (без аргументов) — post-migration

```
make validate
  └─ makefiles/ci.mk validate: → bash core/entrypoints/validate.sh          [БЕЗ FILES — D4]
       └─ entrypoint: source lib/paths.sh → exec internal/validate/validate.sh "$@"
            └─ ФАСАД (≤50 LOC): set -euo pipefail
                 └─ exec python3 -m core.internal.validate.validate_orchestrator "$@"
                      ├─ main():
                      │   ├─ argv пусто → НЕ special flags
                      │   ├─ vlog(6, "start", "Detecting schema validator")
                      │   ├─ validator = detect_validator() → "python" (ajv не установлен; find_spec("jsonschema") ok)
                      │   │    └─ [IMP:6][validate][start] Using validator: python
                      │   ├─ targets = argv → пусто → discover_targets()
                      │   │    └─ os.walk(core/internal) → [core/internal/llm/policy.yaml] (sorted)
                      │   └─ цикл по targets:
                      │        └─ basename=policy.yaml → resolve_schema → None
                      │             └─ vlog(6, "skip", "Skipping non-declaration file: <abs path>")   [D3: БЕЗ trailing \n — корректный путь]
                      ├─ ERRORS=0 → vlog(8, "result", "OK: All files valid") → exit 0
```

### Поток 2: `make lint` (validate --lint) — post-migration (воспроизводит D2)

```
make lint
  └─ ci.mk lint: → bash core/entrypoints/validate.sh --lint
       └─ ... → exec python3 -m core.internal.validate.validate_orchestrator --lint
            ├─ argv=("--lint") → НЕ --check-fqdn/--check-ports
            ├─ vlog(6,"start","Detecting schema validator") → validator="python" → vlog(6,"start","Using validator: python")
            ├─ targets=("--lint") → НЕПУСТО → auto-discovery ПРОПУЩЕН (D2 semantics)
            ├─ цикл: "--lint" матчит startswith("--") → continue
            ├─ ERRORS=0 → vlog(8,"result","OK: All files valid") → exit 0
```

### Поток 3: explicit file (AC4 реальный путь)

```
bash core/entrypoints/validate.sh /path/to/node.yaml
  └─ ... → python3 -m core.internal.validate.validate_orchestrator /path/to/node.yaml
       ├─ targets=("/path/to/node.yaml") → непусто → discovery SKIPPED
       ├─ basename=node.yaml → resolve_schema → node.schema.json
       ├─ validate_file:
       │    ├─ [[ -f ]] ok, [[ -f schema ]] ok
       │    ├─ vlog(6,"validate","Validating: /path/to/node.yaml against node.schema.json")
       │    └─ validator=python → validate_with_python:
       │         └─ subprocess python3 -m core.internal.scripts.jsonschema_validate --yaml-file ... --schema-file ... (cwd=REPO_ROOT)
       │              ├─ rc=0 → vlog(7,"python","OK: /path/to/node.yaml")
       │              └─ rc≠0 → vlog(9,"python", f"{file}:\n{stderr_output}")  [байт-идентично]
       ├─ ERRORS>0 → vlog(9,"result","FAIL: N validation error(s) found") → exit 1
       └─ иначе → exit 0
```

### Поток 4: --check-fqdn / --check-ports (special flags)

```
bash core/entrypoints/validate.sh --check-fqdn /projects/myapp
  └─ ... → python3 -m ... validate_orchestrator --check-fqdn /projects/myapp
       ├─ argv[0]=="--check-fqdn" → arg присутствует → subprocess conflict_checks check-fqdn /projects/myapp (cwd=REPO_ROOT)
       ├─ child stderr passthrough ([IMP:9][conflict_checks][result] OK|FAIL: msg)
       └─ exit child_rc

bash core/entrypoints/validate.sh --check-ports
  └─ ... → python3 -m ... validate_orchestrator --check-ports
       ├─ base = argv[2] если есть; иначе PROJECTS_BASE env; иначе core/projects если существует; иначе ""
       └─ subprocess conflict_checks check-ports <base> → exit child_rc
```

### Поток 5: ai-platform.yml (D5 — extension error НЕ прерывает)

```
validate_orchestrator ai-platform.yml-file
  └─ basename=ai-platform.yml → resolve_schema → ai-platform.schema.json
       ├─ check_project_extension: basename=="ai-platform.yml" → vlog(9,"extension","FAIL: REJECT: '<file>' uses .yml extension — platform requires .yaml for ai-platform declarations") + ERRORS++ (rc ИГНОРИРУЕТСЯ)
       ├─ vlog(6,"migration","INFO: '<file>' — единый формат манифеста (AD-2)")
       └─ validate_file → schema-валидация ВЫПОЛНЯЕТСЯ (оба исхода агрегируются в ERRORS)
```

### Верификация байт-идентичности (AC3/AC7)

```
ДО:  make validate 2>/tmp/validate_baseline_before.txt   # exit 0, [IMP:6/8] строки
ПОСЛЕ: make validate 2>/tmp/validate_baseline_after.txt  # exit 0
diff /tmp/validate_baseline_before.txt /tmp/validate_baseline_after.txt
  → пусто (на текущем дереве: единственный yaml = policy.yaml → skip; D3-фикс не меняет вывод, т.к. policy.yaml не declaration и его путь теперь корректен — строка skip идентична по контенту)
  → ДОПУСТИМОЕ отличие: отсутствие blank-line артефакта (trailing \n из D3) — задокументировано в TRAP[DECISION]
```

---

## §3. Architecture & Decisions

### DD1: Почему discovery в Python (os.walk), а не репликация shell-pipeline?

**Q:** AC6 требует «авто-обнаружение идентично». Реплицировать ли `find | sort -z | read -d ''` 1:1?

**A:** Нет. D3 доказал (эмпирически): BSD `sort -z` + `find` (\n-вывод) → один NUL-record, trailing `\n` corruption → `[[ -f ]]` ложный fail, N>1 файлов → blob-chaos. Текущий shell auto-discovery ФАКТИЧЕСКИ не может валидировать auto-найденные declaration-файлы. Репликация = консервация бага. Python `os.walk` + `sorted()` даёт: корректные пути, детерминированный порядок, все файлы. На текущем репо-дереве (1 non-declaration yaml) вывод байт-идентичен → AC3 diff пусто. При появлении node.yaml в core/internal — Python валидирует (правильно), shell бы ложно падал. Это исправление, не регрессия.

⚠️ TRAP[DECISION] · 2026-07-31 · MED · Auto-discovery: os.walk+sorted вместо репликации find|sort -z|read -d ''
· Rejected: репликация shell-pipeline 1:1 (риск: консервация trailing-\n corruption → ложные "File not found" для declaration-файлов)
· Reason: D3 — BSD sort -z сливает весь find-вывод в один NUL-record; репликация сделала бы Python-порт столь же сломанным. os.walk даёт байт-идентичный вывод на текущем дереве + корректную валидацию в будущем.
· Rev: если окажется, что какой-то consumer полагается на ложный "File not found" для auto-discovered declaration-файлов → пересмотреть.

### DD2: subprocess vs native import для jsonschema_validate / conflict_checks

**Q:** Оркестратор — Python. Импортировать `validate_yaml_against_schema()` / `check_fqdn_conflict()` нативно вместо subprocess?

**A:** Subprocess. Причины:
1. **Байт-идентичность stderr:** shell-контракт `vlog_fail "python" "${yaml_file}:\n${output}"` опирается на output CLI (строки `  Error at '<path>': <message>`). CLI уже покрыт golden-тестами (test_validate_cli.py byte-comparison). Native import потребовал бы репликации exit-code semantics (2 = usage/file) и error-format — дрейф-риск.
2. **SRP:** orchestrator = оркестрация, НЕ валидационная логика. subprocess = та же граница, что у shell-фасада.
3. **exit-code passthrough:** `--check-fqdn`/`--check-ports` должны вернуть exit 0/1 дочернего CLI — subprocess.run().returncode — прямое соответствие `exit $?`.
4. Native import (альтернатива) — rejected: требует пере-эмуляции CLI stderr-формата в orchestrator'е (лишний слой, дрейф-риск).

⚠️ TRAP[DECISION] · 2026-07-31 · MED · Оркестратор делегирует Python-CLI через subprocess, не native import
· Rejected: native import validate_yaml_against_schema/check_fqdn_conflict (риск: репликация exit-code и stderr-контрактов CLI, дрейф от golden-тестов 093)
· Reason: subprocess = та же граница, что у shell-фасада; stderr и exit-code сохраняются дословно; CLI уже протестированы.
· Rev: если orchestrator начнёт нуждаться в бизнес-логике валидации (не оркестрации) → выделить в shared-модуль с собственными тестами.

### DD3: REPO_ROOT и cwd для subprocess-вызовов

**Q:** `python3 -m core.internal.scripts.jsonschema_validate` требует repo root в sys.path (namespace-пакеты без __init__.py). Как гарантировать резолвинг?

**A:** Оркестратор вычисляет `REPO_ROOT = Path(__file__).resolve().parents[3]` (validate_orchestrator.py → parents[0]=validate, [1]=internal, [2]=core, [3]=repo root) и передаёт `cwd=REPO_ROOT` ВСЕМ subprocess-вызовам (`-m`-модулям). Это устраняет зависимость от cwd вызывающего процесса (сейчас shell полагается на cwd=repo root при `make` — хрупко). Фасад вызывает `python3 -m core.internal.validate.validate_orchestrator` — для резолвинга самого orchestrator'а: если cwd ≠ repo root, фасад делает `cd "$(git rev-parse --show-toplevel)"` ИЛИ запускает по абсолютному пути скрипта. Решение: фасад `exec python3 "${SCRIPT_DIR}/validate_orchestrator.py" "$@"` (прямой путь, без -m) — устойчив к любому cwd; внутри orchestrator'а subprocess-вызовы получают cwd=REPO_ROOT.

⚠️ TRAP[DECISION] · 2026-07-31 · LO · Фасад запускает orchestrator по абсолютному пути (не -m), subprocess'ы с cwd=REPO_ROOT
· Rejected: полагаться на cwd=repo root у вызывающего (как текущий shell — хрупко при запуске из pre-commit hook или произвольной директории)
· Reason: namespace-пакеты (нет __init__.py) резолвятся через cwd/PYTHONPATH; явный REPO_ROOT делает порт независимым от точки запуска.
· Rev: если core/ получит __init__.py и установится как пакет (pip install -e) → можно вернуть чистый -m.

### DD4: Как сохранить `--check-fqdn`/`--check-ports` байт-идентично

**Q:** Спец-флаги (Brief constraint). Где их обрабатывать — в фасаде или orchestrator'е?

**A:** В orchestrator'е (main()). Фасад ≤50 LOC = только exec. Оркестратор воспроизводит L174-194: `--check-fqdn` без аргумента → `[IMP:10][validate][fqdn] ERROR: --check-fqdn requires a project directory argument` + exit 1; `--check-ports` default base resolution (arg → PROJECTS_BASE → core/projects fallback → ""). Вызовы делегируются в conflict_checks CLI (DD2).

---

## §4. File Manifest

| Файл | Действие | LOC (до → после) | AC | Примечание |
|------|----------|------------------|----|------------|
| `core/internal/validate/validate_orchestrator.py` | NEW | 0 → ~280 | AC1, AC3-AC7 | Python-порт оркестрации; namespace-пакет |
| `core/internal/validate/validate.sh` | MODIFY | 251 → ≤50 | AC2, AC3, AC5, AC9 | Фасад: exec python3 script (прямой путь, DD3) |
| `core/entrypoint-manifest.yaml` | MODIFY | — | AC8 | delegates_to validate/lint → добавить orchestrator (structural, ручная правка) |
| `core/AGENTS.md` | MODIFY (generated) | — | AC8 | `make generate-manifests` → canonical table |
| `tests/unit/test_validate_orchestrator.py` | NEW | 0 → ~300 | AC1, AC3-AC7 | Unit-тесты оркестратора |
| `tests/test_inventory.yaml` | MODIFY (generated) | — | AC8 | `make test-inventory-sync` |
| `core/internal/scripts/jsonschema_validate.py` | VERIFY (НЕ менять) | 211 | AC7, AC9 | Путь core.internal.scripts подтверждён (D6) |
| `core/internal/validate/conflict_checks.py` | VERIFY (НЕ менять) | 177 | AC7 | Уже Python, вызывается subprocess'ом |
| `core/entrypoints/validate.sh` | VERIFY (НЕ менять) | 18 | AC3-AC5 | Уже тонкий entrypoint |
| `core/lib/logging.sh`, `core/lib/python_deps.sh` | VERIFY | — | — | Фасад их НЕ source'ит (логика в Python) |

---

## §5. Acceptance Criteria → Verification Mapping

| AC | Критерий (Brief) | Как верифицируется | Evidence |
|----|------------------|--------------------|----------|
| AC1 | `validate_orchestrator.py` с discovery + schema resolution + orchestration | Файл существует; `python3 -m core.internal.validate.validate_orchestrator --help`-эквивалент (usage) не падает; unit-тесты discovery/routing/aggregation | `pytest tests/unit/test_validate_orchestrator.py -v` PASS |
| AC2 | Shell-фасад ≤ 50 LOC | `wc -l core/internal/validate/validate.sh` ≤ 50 | wc output |
| AC3 | `make validate` проходит идентично | `make validate 2>/tmp/before.txt` (ДО) vs `2>/tmp/after.txt` (ПОСЛЕ); diff = пусто (допустимо: только blank-line артефакт D3, задокументирован) | diff exit 0; оба exit 0 |
| AC4 | `make validate FILES=...` идентично | Реальный путь: `bash core/entrypoints/validate.sh <fixture node.yaml>` → exit 0 + `[IMP:7][validate][python] OK:` (D4: Makefile FILES не пробрасывает — поведение сохранено) | subprocess test: fixture валидируется, exit 0 |
| AC5 | `make lint` (validate --lint) идентично | `bash core/entrypoints/validate.sh --lint` → ровно те же 3 строки (`start`×2 + `OK: All files valid`), exit 0 (D2: discovery skipped) | stderr compare + exit 0 |
| AC6 | Авто-обнаружение схем идентично | unit: resolve_schema mapping node/module/ai-platform + skip прочих; эмпирически `make validate` на текущем дереве → policy.yaml skip (D3-фикс) | unit asserts + AC3 diff |
| AC7 | Ошибки с теми же путями/сообщениями | unit: vlog_fail format `[IMP:9][validate][python] FAIL: <file>:\n<output>`; golden stderr compare (invalid fixture) | assert stderr == golden |
| AC8 | `make gate MODE=fast` зелёный | `make gate MODE=fast` (включая contract: test_make_target_contracts, test_contract_entrypoints, inventory, manifest gates) | gate exit 0 |
| AC9 | DevPlan 093 AC3 не регрессирует | `grep -rn 'python3.*<<\|python3 -c' core/internal/validate/validate.sh` → 0 | grep empty |

---

## §6. $TEST_SPEC

НЕ NONE. Тесты создаются ТОЛЬКО по этой таблице (плюс conftest-хелперы при необходимости).

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_validate_orchestrator.py | test_discover_targets_finds_yaml_and_yml | tmp_path tree с a.yaml/b.yml/прочими → discover возвращает оба, sorted, без trailing \n (D3 regression) | validate_orchestrator.discover_targets |
| tests/unit/test_validate_orchestrator.py | test_discover_targets_empty_dir | пустая tmp_path → [] | discover_targets |
| tests/unit/test_validate_orchestrator.py | test_resolve_schema_node_module_aiplatform | basename mapping: node.yaml→node.schema.json, module.yml→module.schema.json, ai-platform.yaml→ai-platform.schema.json | resolve_schema |
| tests/unit/test_validate_orchestrator.py | test_resolve_schema_unknown_returns_none | policy.yaml/README.md → None (skip, AC6) | resolve_schema |
| tests/unit/test_validate_orchestrator.py | test_check_project_extension_rejects_yml | ai-platform.yml → False + ERRORS++ (D5: продолжает выполнение) | check_project_extension |
| tests/unit/test_validate_orchestrator.py | test_check_project_extension_allows_yaml | ai-platform.yaml/прочие .yml → True | check_project_extension |
| tests/unit/test_validate_orchestrator.py | test_detect_validator_ajv_preferred | monkeypatch shutil.which→"ajv" → "ajv" (приоритет перед python) | detect_validator |
| tests/unit/test_validate_orchestrator.py | test_detect_validator_python_fallback | which→None, find_spec("jsonschema") ok → "python" | detect_validator |
| tests/unit/test_validate_orchestrator.py | test_detect_validator_none_exits_1 | both unavailable → stderr `[IMP:10][validate][detect] ERROR: No validator found...`, exit 1 | detect_validator |
| tests/unit/test_validate_orchestrator.py | test_validate_with_python_ok | monkeypatch subprocess.run rc=0 → vlog_ok + no error increment | validate_with_python |
| tests/unit/test_validate_orchestrator.py | test_validate_with_python_fail_format | monkeypatch rc=1, stderr="  Error at '(root)': 'modules' is a required property" → `[IMP:9][validate][python] FAIL: <f>:\n<err>`, ERRORS++ (AC7 golden) | validate_with_python |
| tests/unit/test_validate_orchestrator.py | test_validate_file_missing_file_schema | несуществующий yaml/schema → `[IMP:9][validate][file] FAIL: File not found:` / `[schema]` | validate_file |
| tests/unit/test_validate_orchestrator.py | test_main_flag_only_skips_discovery | main(["--lint"]) → discovery НЕ вызван (mock), exit 0, "OK: All files valid" (D2 AC5) | main |
| tests/unit/test_validate_orchestrator.py | test_main_explicit_file_validates | main([node.yaml fixture]) → exit 0, OK line (AC4) | main |
| tests/unit/test_validate_orchestrator.py | test_main_error_aggregation_exit_1 | main([invalid fixture]) → `[IMP:9][validate][result] FAIL: 1 validation error(s) found`, exit 1 | main |
| tests/unit/test_validate_orchestrator.py | test_main_check_fqdn_delegates | monkeypatch subprocess.run rc=1 → main(["--check-fqdn", dir]) → exit 1, вызов conflict_checks check-fqdn | main |
| tests/unit/test_validate_orchestrator.py | test_main_check_fqdn_missing_arg | main(["--check-fqdn"]) → stderr `[IMP:10][validate][fqdn] ERROR: --check-fqdn requires a project directory argument`, exit 1 | main |
| tests/unit/test_validate_orchestrator.py | test_main_check_ports_default_base | monkeypatch env PROJECTS_BASE → subprocess вызван с этим base; без env → fallback core/projects|"" | main |
| tests/unit/test_validate_orchestrator.py | test_aiplatform_yml_extension_error_plus_validation | ai-platform.yml fixture → extension REJECT + migration INFO + validate всё равно выполнен (D5) | main/validate_file |
| tests/unit/test_validate_orchestrator.py | test_emit_format | emit(9,"python","FAIL: x") → stderr строка `[IMP:9][validate][python] FAIL: x` (byte-identical, AC7) | emit |

Все тесты: native imports (запрещён subprocess для бизнес-логики — тестирование оркестратора через monkeypatch subprocess.run), `tmp_path` (zero hardcode), caplog + LDD (IMP:9 assertion через ldd_trajectory из tests/_conftest/ldd.py или assert_ldd_imp9), `# 🧪 TRAP[TEST]` на каждой функции, R5 anti-survivorship для error-path тестов (negative тесты с точным trigger-инпутом).

---

## §7. Implementation Steps (для Coder)

```
T1. [baseline capture] ДО любых изменений:
      make validate 2>/tmp/validate_baseline_before.txt; echo "EXIT: $?"
      make lint 2>/tmp/lint_baseline_before.txt; echo "EXIT: $?"
      make gate MODE=fast 2>&1 | tee /tmp/gate_baseline_before.log   # подтвердить green

T2. Создать core/internal/validate/validate_orchestrator.py (NEW ~280 LOC):
      - MODULE_CONTRACT (## @purpose/@scope/@invariants/@rationale/@changes) + GREP_SUMMARY + STRUCTURE
      - Функции (каждая с # region FUNC_... / # endregion + Doxygen ## @-tags):
        * emit(imp, block, msg) — stderr + logger.info (caplog-видимость)
        * detect_validator() -> str  (DD: shutil.which ajv → find_spec jsonschema → ERROR+exit 1)
        * discover_targets(root: Path) -> list[Path]  (os.walk, *.yaml+*.yml, sorted — D3-фикс)
        * resolve_schema(basename: str) -> str | None  (1:1 case routing, D1)
        * check_project_extension(path: Path) -> bool  (ai-platform.yml → False + emit FAIL; НЕ прерывает)
        * validate_with_ajv(yaml_file, schema_file) -> bool  (subprocess node_yaml --json-output → tempfile → ajv CLI)
        * validate_with_python(yaml_file, schema_file) -> bool  (subprocess jsonschema_validate, cwd=REPO_ROOT)
        * validate_file(yaml_file, schema_file, validator) -> bool  (file/schema exists → routing)
        * check_fqdn_conflict(dir), check_port_conflict(base)  (subprocess conflict_checks, exit passthrough)
        * main(argv: list[str] | None = None) -> int  (спец-флаги → targets → discovery-if-empty → цикл → агрегация)
      - LDD logs [IMP:N][validate][block] в каждом не-тривиальном блоке
      - CONSTITUTION: fail-fast валидация входов, все error paths видимы (нет bare except)
      - REPO_ROOT = Path(__file__).resolve().parents[3]; SCHEMAS_DIR = parents[2]/schemas
      → verify: python3 core/internal/validate/validate_orchestrator.py --check-fqdn (usage/error без crash)

T3. Редуцировать core/internal/validate/validate.sh (MODIFY 251 → ≤50 LOC):
      - СОХРАНИТЬ: shebang, GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT (обновить @purpose/@changes),
        TRAP[DECISION] комментарии (перенести в orchestrator MODULE_CONTRACT, в фасаде оставить ссылку)
      - УДАЛИТЬ: detect_validator, validate_with_ajv, validate_with_python, check_project_extension,
        validate_file, check_fqdn_conflict, check_port_conflict, main-логику, source logging.sh/python_deps.sh
      - ЗАМЕНИТЬ: main "$@" → exec python3 "${SCRIPT_DIR}/validate_orchestrator.py" "$@"   (DD3: прямой путь)
      - НЕ ОСТАВЛЯТЬ: inline python3 (heredoc/-c) — AC9
      → verify: wc -l ≤ 50; grep -rn 'python3.*<<\|python3 -c' → 0 (AC9)

T4. Создать tests/unit/test_validate_orchestrator.py (NEW ~300 LOC) по §6 $TEST_SPEC:
      - Каждая тест-функция: # 🧪 TRAP[TEST] (Regression/Scenario/Last fail/Remove if)
      - caplog.set_level(INFO) + LDD trajectory print (IMP:7-10) ПЕРЕД asserts + assert IMP:9 (ldd_trajectory)
      - monkeypatch subprocess.run для CLI-делегаций (DD2 — граница subprocess не тестируется через subprocess)
      → verify: pytest tests/unit/test_validate_orchestrator.py -v PASS

T5. Regression smoke (AC3/AC4/AC5/AC7):
      make validate 2>/tmp/validate_baseline_after.txt; echo "EXIT: $?"
      diff /tmp/validate_baseline_before.txt /tmp/validate_baseline_after.txt
        → пусто (допустимо: только blank-line артефакт D3 — задокументировать в выводе diff)
      bash core/entrypoints/validate.sh --lint 2>&1 → 3 строки + exit 0 (AC5)
      fixture invalid node.yaml → байт-идентичный FAIL (AC7 golden)

T6. Manifest + generated (AC8):
      - core/entrypoint-manifest.yaml: delegates_to validate (L107) и lint (L113)
        → "core/entrypoints/validate.sh → core/internal/validate/validate.sh → core/internal/validate/validate_orchestrator.py"
        (structural секция — ручная правка, генератор сохраняет verbatim)
      - make generate-manifests (регенерирует core/AGENTS.md canonical table)
      - make test-inventory-sync (регенерирует tests/test_inventory.yaml с новым тестовым файлом)
      → verify: git diff — только ожидаемые изменения

T7. Gate:
      make fix-gate && git add -u && make gate MODE=fast   # AC8 green
      → verify: test_make_target_contracts (delegates_to файлы существуют), test_contract_entrypoints
        (core_internal_validate_validate entrypoint checks), inventory gate — все PASS

T8. [пост-мерж] Финальный аудит: region-баланс (# region/#endregion парные), GREP_SUMMARY на обоих файлах,
      TRAP[TEST] на всех тестах, Doxygen (make doxygen-check) при наличии Doxyfile.
```

---

## §8. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| stderr-формат меняется → CI/logs diff (AC3/AC7) | HI | T5 diff baseline; emit() байт-идентичен log_imp (prefix=validate); golden asserts в тестах (AC7) |
| auto-discovery D3-фикс меняет поведение для declaration-файлов | MED | На текущем дереве diff пуст (policy.yaml → skip). TRAP[DECISION] фиксирует намеренность. R5-негативный тест (trailing \n regression) |
| cwd-зависимость `python3 -m` при запуске вне repo root | MED | DD3: фасад exec по абсолютному пути, subprocess'ы cwd=REPO_ROOT. Тест: запуск из другой cwd |
| `--check-fqdn`/`--check-ports` выходной контракт сломан | HI | subprocess passthrough (DD2), exit=child rc; тесты main_check_fqdn_delegates/check_ports_default_base |
| manifest contract gate (AC8) | MED | T6: delegates_to → оба файла существуют (фасад + orchestrator); generate-manifests + test-inventory-sync |
| entrypoint contract tests (core_internal_validate_validate) | MED | Фасад сохраняет имя/путь/shebang/executable-bit (make fix-executable-bit при необходимости) |
| Ruff/bandit/markup gate на новом Python | LOW | `make fix-gate` перед commit; MODULE_CONTRACT + region-маркеры на новом файле |
| Ложное «No YAML files found» при пустом discovery | LOW | Воспроизводится: targets пусто ПОСЛЕ discovery → vlog(6,"main","No YAML files found to validate") + exit 0 (L212-215) — включить в T2/T4 |

---

## §9. Verification Plan (итоговый чеклист)

```bash
# AC1: orchestrator существует и исполняется
python3 core/internal/validate/validate_orchestrator.py --check-fqdn 2>&1 | grep -q "requires a project directory" && echo OK

# AC2: фасад ≤50 LOC
[ "$(wc -l < core/internal/validate/validate.sh)" -le 50 ] && echo OK

# AC3: make validate байт-идентичен (diff пусто; допустимо: blank-line артефакт D3)
make validate 2>/tmp/after.txt; diff /tmp/validate_baseline_before.txt /tmp/after.txt

# AC4: explicit файлы валидируются
bash core/entrypoints/validate.sh tests/test_data/valid-node.yaml 2>&1 | grep -q "OK:"

# AC5: make lint идентичен
bash core/entrypoints/validate.sh --lint 2>&1; echo "EXIT: $?"   # 3 строки, exit 0

# AC7: golden error format
bash core/entrypoints/validate.sh tests/test_data/invalid-node.yaml 2>&1 | grep -q "FAIL: .*:\n  Error at"

# AC8: gate
make gate MODE=fast   # exit 0

# AC9: 093 AC3
grep -rn 'python3.*<<\|python3 -c' core/internal/validate/validate.sh  # → 0

# Тесты
pytest tests/unit/test_validate_orchestrator.py tests/unit/test_jsonschema_validate.py tests/unit/test_validate_cli.py -v
```

---

## §10. Out of Scope

- ❌ Изменение `jsonschema_validate.py` / его пути (D6 — 093 закрепил core.internal.scripts)
- ❌ Изменение `conflict_checks.py` (уже Python, Strangler 2026-07-31)
- ❌ Изменение `core/entrypoints/validate.sh` (уже тонкий, 18 LOC)
- ❌ Routing для llm-policy.schema.json / новых схем (D1 — вне текущего контракта)
- ❌ Исправление Makefile `FILES=` проброса (D4 — поведение сохранено «FILES игнорируется»)
- ❌ Добавление `__init__.py` в core/internal/validate/ (namespace-пакет — рабочий механизм, ломка = регрессия)
- ❌ Рефакторинг `validate_module_yaml.py` (D5-валидатор, отдельный путь make validate-modules)
- ❌ Добавление настоящего lint-режима (D2 — текущий контракт: --lint = no-op pass)

---

## §11. Implementation Order (для Coder)

```
[baseline capture]
  make validate 2>/tmp/validate_baseline_before.txt
  make gate MODE=fast 2>&1 | tee /tmp/gate_baseline_before.log

  coder T2 (validate_orchestrator.py — NEW)
    → T3 (validate.sh facade — MODIFY)
    → T4 (tests/unit/test_validate_orchestrator.py — NEW)
    → T5 (regression smoke: AC3/AC4/AC5/AC7 diffs)
    → T6 (manifest delegates_to + generate-manifests + test-inventory-sync)
    → T7 (make gate MODE=fast)
    → T8 (финальный аудит markup/regions/TRAPs)

  QA: pytest tests/unit/test_validate_orchestrator.py -v
      + make validate diff baseline = пусто
      + make gate MODE=fast green
      + VerificationReport → 02-VerificationReport.md (в папке плана)
```

---

## $QA_VERIFICATION

**Verdict:** STABLE (с замечаниями)

**Verification timestamp:** 2026-07-31T18:19 UTC+3

**SHA anchor:** 🔒 `fbe306d4284d9105193605378be28eb64b3c6795` (working tree dirty — 8 uncommitted files: .ai/debt/011-Debt.md, core/entrypoint-manifest.yaml, core/internal/bootstrap/lifecycle/secrets_manager.py, core/internal/bootstrap/lifecycle/state_machine.py, core/internal/deploy/deploy_history.py, core/modules/nginx/generate-dev-certs.sh, makefiles/helpers.mk, tests/e2e/test_failure_scenarios.py)

### Protocol Compliance

| Check | Status | Evidence |
|-------|--------|----------|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✅ | Line 1 / Line 533 |
| `$ARTIFACT_CONTRACT` (7 fields) | ✅ | PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES — все 7 присутствуют (L5-33) |
| Draft Code Graph (XML) | ✅ | §1, 96 lines of structured XML with entities + crosslinks |
| Data Flow (процессная симуляция) | ✅ | §2, 5 потоков: make validate, make lint, explicit file, --check-fqdn/--check-ports, ai-platform.yml |
| File Manifest | ✅ | §4, 10 файлов с действиями и AC-привязкой |
| AC → Verification Mapping | ✅ | §5, все 9 AC с evidence |
| $TEST_SPEC | ✅ | §6, 20 тестовых функций с scenario/module under test |
| Implementation Steps | ✅ | §7, 8 шагов T1-T8 |
| Risks & Mitigations | ✅ | §8, 7 рисков с severity и mitigation |
| Out of Scope | ✅ | §10, 8 пунктов |
| Implementation Order | ✅ | §11 |

### Brief → DevPlan Cross-Check

| Brief AC | DevPlan AC | Match | Deviations |
|----------|-----------|-------|------------|
| AC1 | AC1 + unit-тесты | ✅ | — |
| AC2 | AC2 | ✅ | — |
| AC3 | AC3 + D3 exception | ✅ | D3 auto-discovery fix документирован, допустимое отличие |
| AC4 | AC4 + D4 clarification | ✅ | D4: Makefile не пробрасывает FILES — поведение сохранено, реальный путь через entrypoint |
| AC5 | AC5 + D2 behavior | ✅ | D2: lint-режима фактически нет, --lint = no-op pass — поведение сохранено |
| AC6 | AC6 + D1 correction | ✅ | D1: platform-env.schema.json не существует — routing только 3 схемы, deviation обоснован |
| AC7 | AC7 + byte-identical format | ✅ | — |
| AC8 | AC8 + manifest/inventory | ✅ | — |
| AC9 | AC9 | ✅ | — |

**Все 6 divergence (D1-D6) документированы и обоснованы в §0.** Реализация следует коду, не брифу — прецедент DevPlan 093 Rev 2.

### Architectural Decisions Audit

| Decision | Rationale | Verdict |
|----------|-----------|---------|
| DD1: os.walk вместо репликации find\|sort | D3 bug fix — BSD sort -z corruption | ✅ Обосновано |
| DD2: subprocess вместо native import | Byte-identical stderr, SRP, exit-code passthrough | ✅ Обосновано |
| DD3: REPO_ROOT = parents[3], фасад exec по абсолютному пути | Устойчивость к cwd ≠ repo root | ✅ Проверено вычисление: core/internal/validate/orchestrator.py → parents[3] = repo root |
| DD4: Спец-флаги в orchestrator, не фасаде | Фасад ≤50 LOC = только exec | ✅ Обосновано |

### TRAP Annotations Coverage

validate.sh содержит 3 TRAP:
- L13-18: TRAP[DECISION] · Single manifest format → **переносится** в orchestrator MODULE_CONTRACT, в фасаде — ссылка (T3) ✅
- L20-25: TRAP[DECISION] · --check-ports → **переносится** в orchestrator MODULE_CONTRACT, в фасаде — ссылка (T3) ✅
- L151: TRAP[BUG] reference в @rationale check_fqdn_conflict → уже в conflict_checks.py (Strangler), orchestrator не дублирует ✅

Новые TRAP в DevPlan:
- DD1 TRAP[DECISION]: os.walk вместо shell-pipeline ✅
- DD2 TRAP[DECISION]: subprocess вместо native import ✅
- DD3 TRAP[DECISION]: фасад по абсолютному пути ✅

### Фактические ошибки, исправленные в DevPlan

| # | Проблема | Где | Исправлено |
|---|----------|-----|------------|
| F1 | AC5 ссылается на §0 D5 (ai-platform.yml extension), должно быть §0 D2 (lint-mode) | L15 (было) | ✅ Исправлено на §0 D2 |

### Замечания (не блокируют, задокументированы)

**MEDIUM — G1: `_extract_delegate_paths` в gate-тесте — только `.sh` пути**
- Файл: `tests/gates/test_gate_manifest_integrity.py:96-111`
- Функция `_extract_delegate_paths()` проверяет `if ".sh" in chunk` и извлекает только `.sh` файлы из `delegates_to` цепочек. Добавление `validate_orchestrator.py` в цепочку **не будет валидироваться** этим gate-тестом.
- **Не блокирует:** контрактный тест `test_manifest_delegate_scripts_exist` (`test_make_target_contracts.py:112-192`) обрабатывает и `.py` файлы (L180: `words[0].endswith(".sh") or words[0].endswith(".py")`), так что AC8 gate pass достижим через contract-слой.
- **Рекомендация:** добавить `.py` поддержку в `_extract_delegate_paths()` в будущем (out of scope для данного плана).

**LOW — G2: AC5 «3 строки» — неточность**
- DevPlan §5 AC5: «3 строки (start×2 + OK: All files valid)» для `bash core/entrypoints/validate.sh --lint`.
- Реальность: entrypoint добавляет 2 строки (`Starting validate entrypoint` + `Delegating to validate.sh`), итого 5 строк в stderr. Data Flow §2 корректно показывает полную цепочку.
- Влияние: минимальное — сравнение должно быть против полного stderr (включая entrypoint), что и так подразумевается diff-подходом в T5.

**LOW — G3: Manifest signature `make validate [FILES=...]` — известный drift**
- Запись: `core/entrypoint-manifest.yaml:108` → `signature: make validate [FILES=...]`
- Реальность: Makefile `validate:` таргет не пробрасывает `$(FILES)` (D4).
- DevPlan корректно документирует это как D4 и не меняет Makefile (out of scope).
- **Не исправляется** в этом плане — задокументировано как известный drift.

**LOW — G4: Нет точного YAML diff для T6 manifest-правки**
- T6 описывает изменение текстом: `"core/entrypoints/validate.sh → core/internal/validate/validate.sh → core/internal/validate/validate_orchestrator.py"`
- Coder должен самостоятельно найти точные строки L107 и L113 в `core/entrypoint-manifest.yaml` и заменить `delegates_to`.
- **Не блокирует:** строки однозначно идентифицируются (L107: validate delegates_to, L113: lint delegates_to).

**INFO — G5: Dead code test references исторический `lint.sh`**
- `tests/gates/test_gate_dead_code.py:625` — TRAP[TEST] LAST_FAIL mentions `core/internal/validate/lint.sh`
- Файл `lint.sh` не существует (проверено). Это историческая запись, не влияет на текущий план.
- Удаление логики validate.sh **не должно** создать новых dead code detection hits (фасад остаётся исполняемым через entrypoint).

**INFO — G6: Working tree dirty при верификации**
- 8 uncommitted файлов. Верификация проводилась против HEAD `fbe306d`, но часть исходников могла быть изменена.
- Ни один из изменённых файлов не затрагивает validate-область → влияние на верификацию минимальное.

### Статус Acceptance Criteria (pre-implementation)

| AC | Статус | Комментарий |
|----|--------|-------------|
| AC1 | ⏳ PENDING | `validate_orchestrator.py` будет создан в T2 |
| AC2 | ⏳ PENDING | Фасад будет редуцирован в T3 |
| AC3 | ⏳ PENDING | Baseline capture + diff в T1/T5 |
| AC4 | ⏳ PENDING | Explicit file validation в T5 |
| AC5 | ⏳ PENDING | Lint-режим в T5 |
| AC6 | ⏳ PENDING | Schema routing в unit-тестах T4 |
| AC7 | ⏳ PENDING | Golden error format в unit-тестах T4 + smoke T5 |
| AC8 | ⏳ PENDING | Manifest + inventory + gate в T6-T7 |
| AC9 | ⏳ PENDING | grep проверка в T3 |

### Итог

DevPlan 107 корректно реализует Brief 107 с 6 документированными и обоснованными отклонениями (D1-D6). Архитектурные решения (DD1-DD4) хорошо мотивированы и проверены против кодовой базы. Implementation steps (T1-T8) полны и выполнимы Coder'ом. Одна фактическая ошибка исправлена (F1: D5→D2). Замечания G1-G6 не блокируют имплементацию.

$END_DEVPLAN
