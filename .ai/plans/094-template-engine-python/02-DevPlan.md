$START_DEVPLAN
# DevPlan 094 — Template Engine Python Native

## $ARTIFACT_CONTRACT
- **PURPOSE:** Ликвидировать последний shell-компонент, вызываемый из Python через subprocess. Заменить `template-engine.sh` (238 LOC, thin bash-CLI wrapper) на прямые вызовы нативного Python-ядра `template_engine.py` во всех call sites.
- **DESCRIPTION:** Три волны: (1) Verify parity — убедиться, что `template_engine.py` API покрывает все use-cases shell-обёртки (render/render-dir/check), выровнять контракт; (2) Migrate call sites — заменить 3 subprocess-вызова на прямой import + функции; (3) Delete shell + update Makefile/manifest/AGENTS.md. Гарантия: `{{UPPER_SNAKE}}` strict regex сохранён дословно.
- **RATIONALE:** Бриф уточнён по факту аудита: `template-engine.sh` НЕ содержит бизнес-логики рендеринга — это тонкий arg-parsing wrapper (238 LOC: usage(), resolve_manifest(), case-dispatch) вокруг `template_engine.py`. Все 3 subprocess-вызова из Python→bash→python — это чистая инверсия зависимости (Infrastructure→Python через shell) без функциональной надобности. Удаление wrapper'а + прямой import = `-238 LOC shell`, `-3 subprocess.run`, `-1 indirection layer`, `-2 процесса` на каждый render. Соответствует языковой политике AGENTS.md и Strangler-Fig (Tier 1 — каждый wrapper удаляется, business-logic уже в Python).
- **ACCEPTANCE_CRITERIA:**
  - `grep -rn "template-engine.sh" core/ --include="*.py"` → 0 (0 subprocess-вызовов).
  - `core/internal/template-engine.sh` удалён (file not found).
  - `grep -rn "template-engine.sh" core/ makefiles/` → 0 (Makefile/manifest обновлены).
  - `{{UPPER_SNAKE}}` strict regex НЕ изменён: `re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")` дословно (байт-идентично).
  - Тест: `{{$labels.alertname}}` и `{{instance}}` проходят как literal (не матчатся).
  - `make templates-check` зелёный (делегирует в `template_engine.py check`).
  - `make templates-render` зелёный (делегирует в `template_engine.py render-all`).
  - `make gate MODE=fast` зелёный (regression на call sites: monitoring, sudoers, scaffold).
  - Unit-тесты `tests/test_template_engine.py` (20 шт.) проходят без изменений (контракт сохранён).
  - `make check-manifests` зелёный (entrypoint-manifest.yaml синхронизирован).
- **IMPLEMENTS:** Brief 094 GAP-2 (устранение shell-зависимости из Python-домена) + закрытие «template-engine.sh не покрыт планом» (3-я экспертиза). Зависимость DevPlan 092 (scaffold-python-completion) — SATISFIED (VerificationReport от 2026-07-30 присутствует, `project_scaffolder.py` мигрирован).
- **IMPACTS:**
  - `core/internal/template-engine.sh` — DELETE (238 LOC).
  - `core/internal/template_engine.py` — MODIFY (минимально: update `@scope` docstring, убрать упоминание `template-engine.sh`; код рендеринга НЕ трогается).
  - `core/internal/monitoring_config_renderer.py` — MODIFY (1 функция `_render_template`, ~30 строк: subprocess→import; удалить sed-fallback + `TEMPLATE_ENGINE_SCRIPT` const).
  - `core/internal/bootstrap/deploy/sudoers_generator.py` — MODIFY (функции `_resolve_template_engine` + `_render_template`, ~80 строк: subprocess→import; удалить temp-file dance).
  - `core/internal/scaffold/project_scaffolder.py` — MODIFY (функция `render_project_template`, ~30 строк: subprocess→import).
  - `makefiles/helpers.mk` — MODIFY (2 таргета: `template-engine.sh` → `python3 template_engine.py`).
  - `core/entrypoint-manifest.yaml` — REGENERATE (2 delegates_to: `.sh` → `.py`).
  - `AGENTS.md` (root) §Template Mechanisms — MODIFY (1 строка: отметить UPPER_SNAKE как Python-native, удалить упоминание shell wrapper).
  - `core/AGENTS.md` — REGENERATE (canon_table: 2 строки delegates_to).
  - `core/internal/bootstrap/converge/reconciler.py` — MODIFY (1 комментарий line 1668: `template-engine.sh render` → `template_engine.render_template`).
  - `core/templates/sudo-whitelist.template` — MODIFY (3 docstring-комментария: update ссылок).
- **REQUIRES:** DevPlan 092 (scaffold-python-completion) — SATISFIED. PyYAML установлен (уже required для `template_engine.py`).

---

## $DOCUMENT_PLAN

### Document Plan

**SECTION_GOALS:**
- GOAL Доказать, что `template_engine.py` покрывает все 4 операции shell-обёртки (render, render-all, render-dir, check) байт-идентично => GOAL_PARITY
- GOAL Заменить 3 subprocess.run вызова на прямой import в Python-модулях => GOAL_MIGRATE
- GOAL Удалить shell-обёртку и обновить все ссылки (Makefile, manifest, AGENTS.md) => GOAL_DELETE
- GOAL Доказать отсутствие регрессии через gate + unit-тесты => GOAL_VERIFY

**SECTION_USE_CASES:**
- USE_CASE Рендер Grafana dashboard шаблона из `monitoring_config_renderer.py` => SCENARIO_MONITORING
- USE_CASE Рендер sudo-whitelist.template из `sudoers_generator.py` => SCENARIO_SUDOERS
- USE_CASE Рендер project template директории из `project_scaffolder.py` => SCENARIO_SCAFFOLD
- USE_CASE `make templates-check` и `make templates-render` из CLI => SCENARIO_CLI

$END_DOCUMENT_PLAN

---

## 1. Audit Summary (verificated 2026-07-31)

### 1.1 Реальное состояние template-engine.sh

`template-engine.sh` (238 LOC) — **thin bash CLI wrapper**, НЕ содержит бизнес-логики:

| LOC | Компонент | Назначение |
|-----|-----------|------------|
| 1-33 | pre-flight | `python3` in PATH, `template_engine.py` exists |
| 35-59 | `usage()` | help text |
| 61-77 | `resolve_manifest()` | auto-detect `template-manifest.yaml` |
| 79-238 | `case "$COMMAND"` dispatch | 4 команды → `python3 template_engine.py <cmd> ...` |

**Бизнес-логика рендеринга (PLACEHOLDER_RE, render_template, atomic_write) полностью в `template_engine.py` (716 LOC).** Shell-обёртка только парсит args и пробрасывает их в Python — это лишний процесс + лишний indirection layer.

### 1.2 Корректировка Brief: «25 subprocess-вызовов» → 3

Бrief утверждает «25 grep-совпадений». Фактический аудит (2026-07-31):

| Категория | Count | Что это |
|-----------|-------|---------|
| **Активные subprocess.run к .sh** | **3** | Реальные call sites для миграции |
| Docstring/комментарии с упоминанием .sh | ~12 | Обновить текст (не функционально) |
| Grep-совпадения в самом .sh (self-reference) | ~5 | Удалятся вместе с файлом |
| entrypoint-manifest.yaml delegates_to | 2 | Regenerate |
| Makefile helpers.mk | 2 | Update recipe |
| AGENTS.md (root + core) | 2 | Regenerate core/, edit root |

**3 реальных call sites** (subprocess.run из Python к `bash template-engine.sh`):

1. `core/internal/monitoring_config_renderer.py:390` — `_render_template()` → `subprocess.run([engine_path, "render", ...])` + sed-fallback (lines 391-398)
2. `core/internal/bootstrap/deploy/sudoers_generator.py:140` — `_render_template()` → `subprocess.run(["bash", engine_path, "render", ...])` + temp-file dance (lines 122-171)
3. `core/internal/scaffold/project_scaffolder.py:241` — `render_project_template()` → `subprocess.run(["bash", engine_script, "render-dir", ...])` (lines 241-253)

### 1.3 Паритет API: .sh wrapper ↔ .py core

| .sh команда | .py API (уже существует) | Паритет |
|-------------|--------------------------|---------|
| `render <tmpl> [out] VAR=val` | `render_template(tmpl, output_path, vars, allow_missing, dry_run)` | ✅ Полный |
| `render-all [--manifest] [VAR=val]` | `render_all(manifest, extra_vars, dry_run)` | ✅ Полный |
| `render-dir <dir> [VAR=val]` | `render_directory_in_place(dir, vars)` | ✅ Полный |
| `check [--manifest] [--verbose]` | `check_all(manifest, extra_vars)` → `(ok, diagnostics)` | ✅ Полный |

**Вывод Wave 1:** `template_engine.py` уже покрывает 100% операций shell-обёртки. Никаких новых функций не требуется. Regex `PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")` идентичен в обоих (shell проксирует в Python).

### 1.4 Scoping note: cert_orchestrator and deploy-modules (из Brief)

Brief 094 перечисляет `cert_orchestrator` и `deploy modules` как call sites для миграции.
Фактический аудит (2026-07-31):
- `core/internal/bootstrap/deploy/cert_orchestrator.py` — НЕ вызывает template-engine.sh (0 grep-совпадений). Сертификаты рендерятся через acme.sh, не через template engine.
- `core/internal/bootstrap/deploy-modules.sh` — НЕ вызывает template-engine.sh. После Strangler-Fig (DevPlan 087) deploy-modules.sh сокращён до 91 LOC и не содержит template-рендеринга. Sudoers генерируются через `sudoers_generator.py` (учтён в Wave 2.B).
- `core/internal/scaffold/add-project.sh` — мигрирован в `project_scaffolder.py` (DevPlan 092, учтён в Wave 2.C).

**Вывод:** Никаких дополнительных call sites для миграции. Brief был написан до финального аудита.

---

## 2. Draft Code Graph (XML)

```xml
<knowledge_graph plan="094-template-engine-python">

  <!-- DELETE -->
  <entity id="core_internal_template_engine_sh" type="FILE"
          keywords="template-engine.sh, bash-wrapper, CLI"
          annotation="DELETE — thin bash CLI wrapper (238 LOC), proxying to template_engine.py">
    <CrossLink target="core_internal_template_engine_py" relation="REPLACED_BY"/>
    <CrossLink target="makefiles_helpers_mk" relation="CALLED_BY (templates-check/render)"/>
    <CrossLink target="core_entrypoint_manifest_yaml" relation="DELEGATED_TO_BY (2 entries)"/>
  </entity>

  <!-- MODIFY (core, no business logic change) -->
  <entity id="core_internal_template_engine_py" type="FILE"
          keywords="template_engine.py, render, render_all, check_all, render_directory_in_place, PLACEHOLDER_RE"
          annotation="KEEP — Python core, 716 LOC. Update @scope docstring only.">
    <CrossLink target="tests_test_template_engine_py" relation="TESTED_BY (20 unit tests)"/>
    <entity id="template_engine_PLACEHOLDER_RE" type="CONSTANT"
            keywords="PLACEHOLDER_RE, regex, UPPER_SNAKE, strict"
            annotation="INVARIANT — MUST stay byte-identical: re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')"/>
    <entity id="template_engine_render_template_FUNC" type="FUNC"
            keywords="render_template, render single file"
            annotation="KEEP — used by monitoring, sudoers (after migration via import)"/>
    <entity id="template_engine_render_all_FUNC" type="FUNC"
            keywords="render_all, manifest rendering"
            annotation="KEEP — used by make templates-render (via CLI)"/>
    <entity id="template_engine_render_directory_in_place_FUNC" type="FUNC"
            keywords="render_directory_in_place, render-dir, in-place directory"
            annotation="KEEP — used by project_scaffolder (after migration via import)"/>
    <entity id="template_engine_check_all_FUNC" type="FUNC"
            keywords="check_all, dry-run, diagnostics"
            annotation="KEEP — used by make templates-check (via CLI)"/>
    <entity id="template_engine_main_FUNC" type="FUNC"
            keywords="main, CLI entrypoint"
            annotation="KEEP — used by make templates-check/render (python3 template_engine.py ...)"/>
  </entity>

  <!-- MIGRATE: call site 1 -->
  <entity id="monitoring_config_renderer_render_template_FUNC" type="FUNC"
          file="core/internal/monitoring_config_renderer.py"
          keywords="_render_template, subprocess, sed-fallback, TEMPLATE_ENGINE_SCRIPT"
          annotation="MODIFY — replace subprocess.run + sed-fallback with import template_engine.render_template">
    <CrossLink target="template_engine_render_template_FUNC" relation="WILL_IMPORT"/>
    <entity id="monitoring_TEMPLATE_ENGINE_SCRIPT" type="CONSTANT"
            keywords="TEMPLATE_ENGINE_SCRIPT, path constant"
            annotation="DELETE — line 68, obsolete after migration"/>
  </entity>

  <!-- MIGRATE: call site 2 -->
  <entity id="sudoers_generator_resolve_template_engine_FUNC" type="FUNC"
          file="core/internal/bootstrap/deploy/sudoers_generator.py"
          keywords="_resolve_template_engine, path resolution"
          annotation="DELETE — obsolete after migration"/>
  <entity id="sudoers_generator_render_template_FUNC" type="FUNC"
          file="core/internal/bootstrap/deploy/sudoers_generator.py"
          keywords="_render_template, subprocess, temp-file"
          annotation="MODIFY — replace subprocess + temp-file dance with import template_engine.render_template (dry_run=True returns str)"/>
  <entity id="reconciler_reconcile_sudoers_FUNC" type="FUNC"
          file="core/internal/bootstrap/converge/reconciler.py"
          keywords="reconcile_sudoers, comment-only"
          annotation="MODIFY — 1 docstring comment line 1668 (template-engine.sh → template_engine)"/>

  <!-- MIGRATE: call site 3 -->
  <entity id="project_scaffolder_render_project_template_FUNC" type="FUNC"
          file="core/internal/scaffold/project_scaffolder.py"
          keywords="render_project_template, subprocess, render-dir"
          annotation="MODIFY — replace subprocess.run(bash engine render-dir) with import template_engine.render_directory_in_place"/>

  <!-- Makefile + manifest -->
  <entity id="makefiles_helpers_mk" type="FILE"
          keywords="helpers.mk, templates-check, templates-render"
          annotation="MODIFY — 2 recipes: .sh → python3 template_engine.py"/>
  <entity id="core_entrypoint_manifest_yaml" type="FILE"
          keywords="entrypoint-manifest.yaml, delegates_to, generated"
          annotation="REGENERATE — 2 delegates_to entries (.sh → .py) via make generate-entrypoint-manifest"/>

  <!-- Docs -->
  <entity id="AGENTS_md_root" type="FILE"
          keywords="AGENTS.md, Template Mechanisms, UPPER_SNAKE"
          annotation="MODIFY — §Template Mechanisms: mark UPPER_SNAKE as Python-native, remove shell wrapper mention"/>
  <entity id="core_AGENTS_md" type="FILE"
          keywords="core/AGENTS.md, canon_table, generated"
          annotation="REGENERATE — 2 canon_table rows (templates-check, templates-render delegates_to)"/>
  <entity id="sudo_whitelist_template" type="FILE"
          file="core/templates/sudo-whitelist.template"
          keywords="sudo-whitelist.template, docstring"
          annotation="MODIFY — 3 docstring comments (template-engine.sh → template_engine)"/>

  <!-- Tests -->
  <entity id="tests_test_template_engine_py" type="FILE"
          keywords="test_template_engine.py, 20 unit tests, IMP:9"
          annotation="KEEP — no changes needed (contract preserved). Re-run to confirm green."/>
  <entity id="tests_test_templates_py" type="FILE"
          keywords="test_templates.py, integration"
          annotation="KEEP — re-run to confirm green"/>
  <entity id="tests_test_template_syntax_gate_py" type="FILE"
          keywords="test_template_syntax_gate.py, gate, strict grammar"
          annotation="KEEP — re-run to confirm green (validates {{$labels}} non-matching)"/>

</knowledge_graph>
```

---

## 3. Step-by-Step Data Flow

### 3.1 Текущий поток (BEFORE — 2 процесса на каждый render)

```
monitoring_config_renderer._render_template()
  └─ subprocess.run([template-engine.sh, "render", tmpl, out, K=V...])
       └─ bash: case dispatch → python3 template_engine.py render tmpl out K=V...
            └─ render_template() → atomic_write() → exit 0
       └─ bash: log OK/FAILED → exit 0
  └─ (if .sh missing) sed-fallback: content.replace("${K}", V)

sudoers_generator._render_template()
  └─ tempfile.NamedTemporaryFile()
  └─ subprocess.run([bash, template-engine.sh, "render", tmpl, tmpfile, K=V...])
       └─ bash → python3 template_engine.py → render_template → write tmpfile → exit
  └─ open(tmpfile).read() → return str
  └─ finally: _safe_cleanup(tmpfile)

project_scaffolder.render_project_template()
  └─ subprocess.run([bash, template-engine.sh, "render-dir", dir, K=V...])
       └─ bash → python3 template_engine.py render-dir → render_directory_in_place → exit

make templates-check / templates-render
  └─ core/internal/template-engine.sh check/render-all
       └─ bash dispatch → python3 template_engine.py → exit
```

**Проблема:** Python → bash → Python = 2 лишних процесса + arg-marshalling overhead + temp-file dance в sudoers.

### 3.2 Целевой поток (AFTER — in-process import, 0 subprocess)

```
monitoring_config_renderer._render_template()
  └─ from core.internal.template_engine import render_template
  └─ render_template(tmpl, output_path=out, vars=variables, allow_missing=False)

sudoers_generator._render_template()
  └─ from core.internal.template_engine import render_template
  └─ render_template(tmpl, vars=variables, dry_run=True) → returns str (no temp file needed!)

project_scaffolder.render_project_template()
  └─ from core.internal.template_engine import render_directory_in_place
  └─ render_directory_in_place(project_dir, vars)

make templates-check
  └─ python3 core/internal/template_engine.py check --verbose

make templates-render
  └─ python3 core/internal/template_engine.py render-all
```

**Выгода:** 0 subprocess в Python-домене. CLI-таргеты вызывают Python напрямую (1 процесс вместо 2).

### 3.3 Упрощение sudoers (key insight)

Текущий `sudoers_generator._render_template()` пишет в temp file, затем читает обратно, чтобы вернуть `str`. Но `render_template(..., dry_run=True)` уже **возвращает str** напрямую. Миграция устраняет весь temp-file dance (lines 122-171 → ~5 строк).

---

## 4. Implementation Waves

### Wave 1: Verify Python Parity (LOW RISK — read-only verification)

**Цель:** Доказать, что `template_engine.py` покрывает все операции shell-обёртки байт-идентично.

| Step | Действие | Файл | Артефакт |
|------|----------|------|----------|
| 1.1 | Сравнить `PLACEHOLDER_RE` в .sh (проксирует) vs .py (line 33) | `template_engine.py:33` | Подтверждение: regex идентичен (shell не имеет своего regex) |
| 1.2 | Сверить сигнатуры: `render`, `render-all`, `render-dir`, `check` | `template_engine.py:626-716` (main) | Таблица паритета (см. §1.3) — 4/4 ✅ |
| 1.3 | Запустить `tests/test_template_engine.py` (20 тестов) — baseline green | — | `pytest tests/test_template_engine.py -v` → 20 passed |
| 1.4 | Запустить `tests/test_template_syntax_gate.py` — strict grammar green | — | Подтверждение: `{{$labels}}` не матчится |

**Gate Wave 1:** 20 unit-тестов + syntax gate green. Если FAIL — STOP, parity нарушен, переоценить.

### Wave 2: Migrate Call Sites (MEDIUM RISK — 3 файла)

**Цель:** Заменить 3 subprocess-вызова на прямой import. Каждый call site — независимая единица (можно мигрировать по одному).

#### Wave 2.A: `monitoring_config_renderer.py` (SCENARIO_MONITORING)

| Step | Действие | Локация |
|------|----------|---------|
| 2.A.1 | Удалить `TEMPLATE_ENGINE_SCRIPT` const (line 68) | `monitoring_config_renderer.py:68` |
| 2.A.2 | Добавить import вверху: `from template_engine import render_template` | header imports |
| 2.A.3 | Переписать `_render_template()` (lines 365-398): заменить subprocess + sed-fallback на `render_template(str(template_path), output_path=str(output_path), vars=variables, allow_missing=False)` | lines 365-398 |
| 2.A.4 | Удалить параметр `platform_root` из сигнатуры `_render_template()` (больше не нужен для resolve engine path) — проверить все callers | function signature + callers |
| 2.A.5 | Обновить docstring: убрать упоминание template-engine.sh и sed-fallback | lines 366-381 |
| 2.A.6 | TRAP-комментарий: `⚠️ TRAP[BUG] · 2026-07-31 · removed sed-fallback — strict {{UPPER_SNAKE}} is the only grammar now` | в функции |

**Контракт сохранён:** `render_template(allow_missing=False)` поднимает `TemplateError` на unresolved placeholder — эквивалент `subprocess.run(check=True)` с exit 1. Callers должны ловить `TemplateError` (проверить, что уже ловят или добавить catch).

#### Wave 2.B: `sudoers_generator.py` (SCENARIO_SUDOERS)

| Step | Действие | Локация |
|------|----------|---------|
| 2.B.1 | Добавить import: `sys.path.insert(0, os.environ.get('PLATFORM_ROOT', os.path.join(os.path.dirname(__file__), '../../..')))` + `from core.internal.template_engine import render_template` | header |
| 2.B.2 | Удалить `_resolve_template_engine()` (lines 73-92) — obsolete | lines 73-92 |
| 2.B.3 | Переписать `_render_template()` (lines 95-172): убрать temp-file dance + subprocess, заменить на `render_template(str(template_file), vars={...}, dry_run=True)` → return str | lines 95-172 → ~15 строк |
| 2.B.4 | Удалить `_safe_cleanup()` (lines 175-181) если больше не используется — проверить references | lines 175-181 |
| 2.B.5 | Обработать `TemplateError` вместо subprocess exit-code (возвращать None on error, как сейчас) | _render_template error path |
| 2.B.6 | Обновить docstrings (lines 6, 11, 21, 75-80, 101, 109, 191, 252) — заменить `template-engine.sh` → `template_engine` | 8 docstring locations |
| 2.B.7 | Обновить STRUCTURE line 3: `subprocess bash template-engine.sh` → `template_engine.render_template` | line 3 |

**Ключевое упрощение:** `render_template(dry_run=True)` возвращает `str` напрямую — temp-file dance полностью устраняется.

#### Wave 2.C: `project_scaffolder.py` (SCENARIO_SCAFFOLD)

| Step | Действие | Локация |
|------|----------|---------|
| 2.C.1 | Добавить import: `from core.internal.template_engine import render_directory_in_place` | header |
| 2.C.2 | Переписать `render_project_template()` (lines 212-261): убрать subprocess.run(bash engine render-dir), заменить на `render_directory_in_place(project_dir, vars={...})` | lines 212-261 → ~25 строк |
| 2.C.3 | Обработать возвращаемый int (0=OK, >0=errors): `return errors == 0` | return logic |
| 2.C.4 | Обновить docstring (lines 203, 220, 230) — `template-engine.sh` → `template_engine.render_directory_in_place` | docstrings |
| 2.C.5 | Обновить MODULE_CONTRACT line 24: `CALLS: ... template-engine.sh ...` → `template_engine` | line 24 |

**Gate Wave 2 (после всех 3):**
- `grep -rn "subprocess\.run.*template" core/ --include="*.py"` → 0
- `grep -rn "template-engine\.sh" core/ --include="*.py"` → только в комментариях (пока не удалены)
- Запустить unit-тесты для каждого модуля: `pytest tests/unit/test_sudoers_generator.py tests/test_template_engine.py -v`

### Wave 3: Delete Shell + Update References (LOW RISK — cleanup)

**Цель:** Удалить `template-engine.sh`, обновить Makefile, manifest, AGENTS.md, комментарии.

| Step | Действие | Файл |
|------|----------|------|
| 3.1 | Обновить `makefiles/helpers.mk` lines 25, 31: `template-engine.sh check` → `python3 template_engine.py check`; `template-engine.sh render-all` → `python3 template_engine.py render-all` | `makefiles/helpers.mk` |
| 3.2 | Обновить `entrypoint-manifest.yaml` lines 87, 94 (или regenerate): `delegates_to: core/internal/template-engine.sh` → `core/internal/template_engine.py` | `core/entrypoint-manifest.yaml` |
| 3.3 | Обновить `core/templates/sudo-whitelist.template` docstrings (lines 4-5, 19, 21, 27, 35): `template-engine.sh` → `template_engine` | `sudo-whitelist.template` |
| 3.4 | Обновить `reconciler.py` line 1668 комментарий: `template-engine.sh render` → `template_engine.render_template` | `reconciler.py:1668` |
| 3.5 | Обновить `template_engine.py` line 6 `@scope` docstring: убрать "Вызывается из bash-CLI (template-engine.sh)" → "Вызывается из CLI, CI-gates, тестов и напрямую через import" | `template_engine.py:6` |
| 3.6 | Верифицировать `AGENTS.md` §Template Mechanisms (уже ссылается на `template_engine.py`, shell wrapper не упоминается) — изменений не требуется | `AGENTS.md` §Template Mechanisms |
| 3.7 | **Удалить** `core/internal/template-engine.sh` | `template-engine.sh` — DELETE |
| 3.8 | Regenerate manifests: `make generate-entrypoint-manifest && make generate-agents-md` | manifest + core/AGENTS.md |
| 3.9 | `make fix-gate` — синхронизация executable bits, ruff, manifests | auto-fix |

**Gate Wave 3:**
- `grep -rn "template-engine\.sh" core/ makefiles/` → 0
- `ls core/internal/template-engine.sh` → file not found
- `make templates-check` → green
- `make templates-render` → green
- `make check-manifests` → green (manifests синхронны)

---

## 5. Acceptance Criteria (verifiable)

| ID | Критерий | Команда проверки | Ожидаемый результат |
|----|----------|------------------|---------------------|
| AC1 | 0 subprocess-вызовов к template-engine | `grep -rn "subprocess.*template-engine" core/ --include="*.py"` | 0 matches |
| AC2 | 0 упоминаний .sh в Python (functional) | `grep -rn "template-engine\.sh" core/ --include="*.py"` | 0 matches |
| AC3 | .sh файл удалён | `test ! -f core/internal/template-engine.sh && echo DELETED` | DELETED |
| AC4 | Makefile делегирует в .py | `grep "template_engine.py" makefiles/helpers.mk` | 2 matches (check + render-all) |
| AC5 | Manifest делегирует в .py | `grep "template_engine.py" core/entrypoint-manifest.yaml` | 2 matches |
| AC6 | Strict regex неизменён | `grep 'PLACEHOLDER_RE = re.compile' core/internal/template_engine.py` | `r"\{\{([A-Z][A-Z0-9_]*)\}\}"` (byte-identical) |
| AC7 | `make templates-check` green | `make templates-check` | exit 0 |
| AC8 | `make templates-render` green | `make templates-render` | exit 0 |
| AC9 | Unit-тесты green | `pytest tests/test_template_engine.py tests/test_template_syntax_gate.py tests/unit/test_sudoers_generator.py -v` | all passed |
| AC10 | `make gate MODE=fast` green | `make gate MODE=fast` | exit 0 |
| AC11 | Manifests синхронны | `make check-manifests` | exit 0 |
| AC12 | `make templates-check` после удаления .sh | `make templates-check` | green (дополнительное подтверждение, что .py самодостаточен — проверяется ОТДЕЛЬНО от AC7 после удаления .sh) |

---

## 6. File Manifest

| Действие | Файл | LOC delta | Risk |
|----------|------|-----------|------|
| DELETE | `core/internal/template-engine.sh` | -238 | LOW (после AC1-AC2) |
| MODIFY | `core/internal/template_engine.py` | ~1 (docstring) | NONE |
| MODIFY | `core/internal/monitoring_config_renderer.py` | -25 (sed-fallback + subprocess → import) | MEDIUM |
| MODIFY | `core/internal/bootstrap/deploy/sudoers_generator.py` | -60 (temp-file dance + resolve → import) | MEDIUM |
| MODIFY | `core/internal/scaffold/project_scaffolder.py` | -20 (subprocess → import) | MEDIUM |
| MODIFY | `makefiles/helpers.mk` | ~0 (2 строки recipe) | LOW |
| REGENERATE | `core/entrypoint-manifest.yaml` | 0 (2 delegates_to) | LOW (auto) |
| REGENERATE | `core/AGENTS.md` | 0 (2 canon rows) | LOW (auto) |
| MODIFY | `AGENTS.md` (root) §Template Mechanisms | ~1 | LOW |
| MODIFY | `core/templates/sudo-whitelist.template` | ~0 (comments) | NONE |
| MODIFY | `core/internal/bootstrap/converge/reconciler.py` | ~0 (1 comment) | NONE |
| **Итого** | — | **~-340 LOC** | — |

---

## 7. Risk Analysis

### 7.1 Высокий риск: ImportError при cross-module import

**Риск:** `from core.internal.template_engine import render_template` может не сработать,
если модуль вызывается как прямой скрипт (`python3 script.py`) без `PYTHONPATH`.

**Инвокация call sites:**

| Call site | Invocation | sys.path[0] | Импорт |
|-----------|-----------|-------------|--------|
| monitoring_config_renderer.py | `python3 "${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py"` | `core/internal/` | Same dir → `from template_engine import render_template` (прямой импорт, не через core.internal) |
| sudoers_generator.py | `python3 "${SCRIPT_DIR}/deploy/sudoers_generator.py"` | `core/internal/bootstrap/deploy/` | 3 уровня вверх → нужно `sys.path.insert(0, PLATFORM_ROOT)` ИЛИ `importlib` |
| project_scaffolder.py | `python3 -m core.internal.scaffold.project_scaffolder` | project root (`-m` добавляет cwd) | ✅ `from core.internal.template_engine import render_template` работает |

**Mitigation:**
- monitoring: использовать `from template_engine import render_template` (оба в `core/internal/`)
- sudoers: добавить `sys.path.insert(0, os.environ.get('PLATFORM_ROOT', ...))` перед импортом;
  sudoers_generator уже вызывается из `deploy-modules.sh` где `PLATFORM_ROOT` всегда определён
- scaffold: существующий `python3 -m` — import работает без изменений

**Проверка до миграции:**
```bash
# monitoring — прямой импорт (same dir)
python3 -c "import sys; sys.path.insert(0, 'core/internal'); from template_engine import render_template; print('OK')"

# sudoers — через sys.path
python3 -c "import sys; sys.path.insert(0, '.'); from core.internal.template_engine import render_template; print('OK')"
```

### 7.2 Средний риск: error-handling semantic shift

**Риск:** subprocess.run(check=True) поднимает `CalledProcessError`. Прямой import поднимает `TemplateError`, `FileNotFoundError`, `PermissionError`. Callers должны ловить правильные исключения.

**Mitigation:** Для каждого call site явно проверить/обновить except-блоки. `template_engine.render_template` уже объявляет raises в docstring (lines 124-128). `TemplateError` содержит `.template_path`, `.unresolved`, `.line_no` — богаче, чем subprocess exit code.

### 7.3 Низкий риск: sed-fallback removal в monitoring

**Риск:** Удаление sed-fallback (lines 391-398) убирает путь, когда `.sh` не найден. После миграции `.sh` больше не нужен — fallback устарел.

**Mitigation:** `template_engine.render_template` — единственный путь рендеринга. Если Python-импорт работает — fallback не нужен. Логировать `TemplateError` вместо silent sed-substitution (которая к тому же не соблюдала strict grammar — использовала `{{{K}}}` и `${K}`).

### 7.4 Низкий риск: regressión в `make templates-check/render`

**Риск:** Смена `.sh` → `python3 template_engine.py` в Makefile меняет process invocation.

**Mitigation:** `template_engine.py:main()` (lines 626-716) уже реализует CLI-парсинг, идентичный shell-wrapper'у. Shell просто проксировал в этот main(). Поэтому `make templates-check` = `python3 template_engine.py check --verbose` — семантически идентично.

### 7.5 Низкий риск: PYTHONPATH в разных окружениях (local, CI, VPS)

**Риск:** Прямой import требует, чтобы project root был на sys.path. Локально это работает
(pytest добавляет cwd, `python3 -m` добавляет cwd). На CI и VPS окружение может отличаться.

**Mitigation:**
- CI: `make templates-check/render` → `python3 template_engine.py` (CLI, не import) — не затронут
- VPS: monitoring_config_renderer вызывается через `python3 script.py` где PLATFORM_ROOT определён;
  прямой import из той же директории (`from template_engine import ...`) не требует PYTHONPATH
- VPS: sudoers_generator вызывается из deploy-modules.sh где PLATFORM_ROOT всегда определён;
  добавляем `sys.path.insert(0, PLATFORM_ROOT)` перед импортом

---

## 8. Testing Strategy

### 8.1 Существующие тесты (KEEP, no changes)

| Тест | Покрытие | Действие |
|------|----------|----------|
| `tests/test_template_engine.py` (20 tests) | render_template, parse_vars, check_all, strict grammar, atomic write | Re-run, confirm green (контракт сохранён) |
| `tests/test_templates.py` | Integration template rendering | Re-run |
| `tests/test_template_syntax_gate.py` | Gate: strict grammar, `{{$labels}}` non-matching | Re-run (валидирует AC6) |
| `tests/unit/test_sudoers_generator.py` | sudoers generation pipeline | Re-run после Wave 2.B |

### 8.2 Regression testing после миграции

| Сценарий | Команда | Ожидание |
|----------|---------|----------|
| Monitoring render | `make templates-render` (рендерит monitoring configs) | green, output идентичен |
| Sudoers render | `pytest tests/unit/test_sudoers_generator.py -v` | green |
| Scaffold render-dir | `pytest tests/ -k "new_project or scaffold" -v` (если нет — `python3 -m core.internal.scaffold.project_scaffolder ... --dry-run` в temp dir) | green |
| CLI check | `make templates-check` | green |
| Full gate | `make gate MODE=fast` | green |

### 8.3 LDD trajectory validation

Каждый мигрированный call site должен сохранять IMP:9 логи. После миграции:
- `_render_template()` в monitoring → `log.log(9, "[IMP:9][template] Render complete")` (из `template_engine.render_template` line 201)
- `_render_template()` в sudoers → аналогично через `render_template(dry_run=True)`

Если log-level делегируется в `template_engine.py` — IMP:9 логи сохраняются автоматически (в ядре уже есть `log.log(9, ...)`).

---

## 9. Anti-Loop Notes

1. **НЕ объединять UPPER_SNAKE regex с Jinja2.** Это 2 разных механизма по дизайну (AGENTS.md §rationale). План только нативизирует вызов, не меняет механизм.
2. **НЕ добавлять новые функции в `template_engine.py`.** API уже покрывает 100% use-cases. План только меняет call sites и удаляет wrapper.
3. **НЕ переписывать `template_engine.py` бизнес-логику.** Только docstring update (line 6). Regex, render_template, atomic_write — INVARIANT.
4. **НЕ оставлять sed-fallback в monitoring.** Fallback использовал `${K}` и `{{{K}}}` — несовместимо со strict grammar. После миграции только strict `{{UPPER_SNAKE}}`.
5. **Если import не работает** — НЕ fallback на subprocess. Исправить package/import setup. Subprocess к .sh = regress.

---

## 10. Out of Scope

- Jinja2 template engine (LiteLLM config, status-page) — отдельный механизм, не затрагивается.
- `${VAR}` compose templating — встроен в compose engine, не затрагивается.
- `envsubst` для systemd/nginx main config — не затрагивается.
- `template_engine.py` CLI refactor — `main()` остаётся как есть (используется в Makefile).
- Расширение unit-тестов — существующие 20 покрывают контракт. Новые тесты не требуются (контракт сохранён).

---

## 11. Verification Commands (для VerificationReport)

```bash
# AC1-AC2: 0 subprocess / 0 .sh в Python
grep -rn "subprocess.*template-engine" core/ --include="*.py"
grep -rn "template-engine\.sh" core/ --include="*.py"

# AC3: файл удалён
test ! -f core/internal/template-engine.sh && echo "DELETED ✓"

# AC4-AC5: Makefile + manifest делегируют в .py
grep "template_engine.py" makefiles/helpers.mk
grep "template_engine.py" core/entrypoint-manifest.yaml

# AC6: strict regex byte-identical
grep 'PLACEHOLDER_RE = re.compile' core/internal/template_engine.py

# AC7-AC8: CLI таргеты green
make templates-check
make templates-render

# AC9: unit-тесты green
pytest tests/test_template_engine.py tests/test_template_syntax_gate.py tests/unit/test_sudoers_generator.py -v

# AC10-AC11: gate + manifests green
make gate MODE=fast
make check-manifests
```

$END_DEVPLAN
