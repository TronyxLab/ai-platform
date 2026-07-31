$START_DEVPLAN
# DevPlan 106 — lint.sh (238 LOC) + check-doc-headers.sh (236 LOC) → два Python-модуля (Strangler-Fig)

$ARTIFACT_CONTRACT
PURPOSE:               Консолидация двух entrypoint-скриптов с дублирующейся логикой валидации
                       документ-хедеров (lint.sh 238 LOC + check-doc-headers.sh 236 LOC = 474 LOC
                       суммарно) → два Python-модуля `core/internal/lint/grepsummary_validator.py`
                       и `core/internal/lint/doc_header_validator.py` + два тонких shell-фасада (≤40 LOC).
                       Устранение дублирования GREP_SUMMARY-валидации и проверки .sh-ссылок, заявленного
                       в entrypoint-manifest.yaml («replaces former grepsummary from lint.sh») но
                       фактически не выполненного — drift между манифестом и кодом.
DESCRIPTION:           (1) `grepsummary_validator.py` — единый валидатор GREP_SUMMARY keywords +
                       .sh-ссылок в .md (оба режима: scan-all по git ls-files из lint.sh и staged-file
                       режим из check-doc-headers.sh). (2) `doc_header_validator.py` — проверки
                       документ-хедеров staged-файлов (MODULE_CONTRACT region, STRUCTURE, #region/
                       #endregion balance, YAML @purpose, GREP_SUMMARY presence) + namelint
                       (make-target validation против entrypoint-manifest.yaml). (3) `lint.sh` →
                       фасад ≤40 LOC (grepsummary | namelint | --help). (4) `check-doc-headers.sh` →
                       фасад ≤40 LOC (doc-headers). (5) entrypoint-manifest.yaml: delegates_to
                       обновлён. Awk-парсинг YAML в namelint заменён на yaml.safe_load.
                       GNU/BSD grep-ветвление (grep -P vs -oE) заменено единым Python regex
                       (lookbehind доступен во всех средах, включая macOS). Все TRAP[BUG]/
                       TRAP[DECISION] из shell-исходников сохранены как комментарии-история.
RATIONALE:             474 LOC дублирования — крупнейший случай копипасты в entrypoints; манифест
                       уже заявляет о замене, код не соответствует (drift). После миграции оба
                       entrypoint < 50 LOC (политика: entrypoints — тонкие фасады), бизнес-логика
                       покрыта unit-тестами. Языковая политика (root AGENTS.md): новый код только
                       Python; awk-парсинг YAML — источник хрупкости (пользовательское требование:
                       namelint переезжает в Python).
                       Решения: namelint размещается в doc_header_validator.py (Brief §План, строка 28);
                       системные исключения namelint берутся из секции `name_linter` манифеста
                       (behavior-equivalent проверено); GNU-паттерн lookbehind — канонический
                       (строгое подмножество BSD-fallback → AC10 не нарушается).
ACCEPTANCE_CRITERIA:   AC1: `core/internal/lint/grepsummary_validator.py` — единый grepsummary +
                            md-sh-refs валидатор (оба режима: scan-all + staged)
                       AC2: `core/internal/lint/doc_header_validator.py` — module-contract + structure +
                            yaml-purpose + regions + namelint валидатор
                       AC3: `lint.sh` ≤ 40 LOC (color helpers + вызов Python)
                       AC4: `check-doc-headers.sh` ≤ 40 LOC (вызов Python)
                       AC5: grepsummary больше не дублируется — lint.sh делегирует в
                            grepsummary_validator.py (в lint.sh нет копий check_grepsummary/
                            check_sh_refs_in_md)
                       AC6: `make lint` проходит идентично (validate.sh --lint, схема-валидация
                            YAML — поток не меняется)
                       AC7: Pre-commit hooks работают идентично:
                            `pre-commit run check-doc-headers --all-files` и
                            `pre-commit run name-linter --all-files` → exit 0
                       AC8: `core/entrypoint-manifest.yaml` обновлён (delegates_to содержит
                            Python-модули)
                       AC9: `make gate MODE=fast` зелёный
                       AC10: Все существующие false-positive исключения сохранены (http-ссылки,
                            /opt/ пути, проза без слешей, `..`, абсолютные пути, lib/-префикс,
                            .venv/node_modules/__pycache__)
IMPLEMENTS:            Brief 106 (`.ai/plans/106-lint-headers-consolidation/01-Brief.md`)
IMPACTS:
                       - `core/internal/lint/__init__.py` (NEW)
                       - `core/internal/lint/grepsummary_validator.py` (NEW) — grepsummary + md-sh-refs
                       - `core/internal/lint/doc_header_validator.py` (NEW) — doc-headers + namelint
                       - `core/entrypoints/lint.sh` (MODIFY) — усечение до фасада ≤40 LOC
                       - `core/entrypoints/check-doc-headers.sh` (MODIFY) — усечение до фасада ≤40 LOC
                       - `tests/unit/test_grepsummary_validator.py` (NEW) — unit-тесты
                       - `tests/unit/test_doc_header_validator.py` (NEW) — unit-тесты
                       - `core/entrypoint-manifest.yaml` (MODIFY) — секция `lint:` delegates_to
                       - `tests/test_inventory.yaml` (MODIFY) — регенерация `make test-inventory-sync`
                         (если inventory gate активен)
REQUIRES:              Python ≥3.10 с PyYAML (yaml.safe_load — прецедент conflict_checks.py).
                       `core/lib/paths.sh` (PATHS_CORE_DIR) — фасады сохраняют source-паттерн для
                       вычисления PLATFORM_ROOT. Python-модули вычисляют repo-root самодостаточно
                       (`Path(__file__).resolve().parents[3]` — zero hardcoded paths).
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix

| # | Проблема | Текущее состояние (подтверждено чтением исходников) | Решается как |
|---|----------|----------------------------------------------------|--------------|
| P1 | Дублирование GREP_SUMMARY-валидации | `lint.sh:check_grepsummary()` (строки 29-51) и `check-doc-headers.sh:check_grep_summary()` (строки 43-77) — две независимые реализации с РАЗНЫМ парсингом keywords | Единый `grepsummary_validator.py` с двумя режимами (scan-all / staged), парсинг строго по-скриптово |
| P2 | Дублирование .sh-ссылок в .md | `lint.sh:check_sh_refs_in_md()` (строки 54-80) — все .sh-токены, GNU `-P`/BSD `-oE`; `check-doc-headers.sh:check_md_sh_refs()` (строки 121-171) — только backtick-ссылки, другое разрешение путей | Два режима экстракции + два режима разрешения в одном модуле; GNU-паттерн канонический (Python lookbehind портабелен) |
| P3 | awk-парсинг YAML в namelint — хрупкий | `lint.sh:check_namelint()` (строки 86-169) — 3 awk-парсера (allowed_verbs, module_lifecycle, forbidden_verbs) + hardcoded case-исключения | yaml.safe_load манифеста; исключения из секции `name_linter` манифеста (behavior-equivalent) |
| P4 | check_region_balance в lint.sh — НЕ СУЩЕСТВУЕТ | Brief заявляет, но фактически `check_regions_balanced()` только в check-doc-headers.sh (строки 30-40) | Drift-коррекция: regions → только doc_header_validator.py |
| P5 | check_file_lines / check_shellcheck_directives — НЕ СУЩЕСТВУЮТ в check-doc-headers.sh | `check_file_lines` живёт в отдельном `core/entrypoints/check-file-lines.sh` (вне скоупа); shellcheck-directives проверки в исходнике нет | НЕ реализуются — вне фактического кода. Зафиксировать в DevPlan (AC2 трактуется по факту) |
| P6 | GNU/BSD grep-ветвление (macOS vs Linux) | 2 места: lint.sh:59, check-doc-headers.sh:126 — capability probe `echo \| grep -P ''` | Единый Python regex — lookbehind доступен везде; BSD-fallback-ветка удаляется (подмножество → AC10) |
| P7 | Drift манифеста: «replaces former grepsummary from lint.sh», но lint.sh сохранил копии | check-doc-headers.sh:18 комментарий + manifest lint: секция | После миграции lint.sh делегирует в Python — манифест становится правдой (AC5/AC8) |

---

## 2. Анализ исходников (drift-коррекция Brief)

Прочитаны: `core/entrypoints/lint.sh` (238 LOC), `core/entrypoints/check-doc-headers.sh` (236 LOC),
`.pre-commit-config.yaml`, `core/entrypoint-manifest.yaml` (секции lint:/name_linter/allowed_verbs),
`core/internal/validate/conflict_checks.py` (прецедент Python-порта), `core/internal/scripts/generate_entrypoint_manifest.py`
(G3 cycle-break: allowed_verbs/gates генерируются, структурные секции сохраняются verbatim).

### 2.1 lint.sh — фактический инвентарь функций

| Функция | Строки | Семантика (обязательна к сохранению) |
|---------|--------|--------------------------------------|
| `red`/`green`/`yellow` | 24-26 | Color helpers |
| `check_grepsummary(file, line)` | 29-51 | Экстракция keywords: `sed 's/.*GREP_SUMMARY:\s*//' \| tr ',' ' '`; strip `#` → `-->` → `<!--`; skip пустых; skip `-*`/`--*` (flags); `grep -qiF "$kw" "$file"` (case-insensitive literal substring) |
| `check_sh_refs_in_md(file)` | 54-80 | GNU: `(?<!\S)([\w./-]+\.sh)(?!\S)`; BSD fallback: `[\w./-]+\.sh` + `grep -v '^http'`. Skips: `^http`, `^/opt/`, нет `/`, содержит `..`. Resolve: `$PLATFORM_ROOT/$ref` ИЛИ `$ref` |
| `check_namelint()` | 86-169 | awk-парсинг `allowed_verbs`/`module_lifecycle`/`forbidden_verbs`; `.PHONY:` из root Makefile + `makefiles/*.mk`; forbidden → FAIL; allowed → pass; module_lifecycle → pass; case-исключения `test-*|gate-*|pre-commit-*` + `help|venv`; иначе FAIL |
| main | 171-238 | `--help`, `grepsummary` (git ls-files → все файлы, find-fallback вне git), `namelint`; exit 0/1; IMP:7-9 логи в stderr |

### 2.2 check-doc-headers.sh — фактический инвентарь функций

| Функция | Строки | Семантика (обязательна к сохранению) |
|---------|--------|--------------------------------------|
| `check_regions_balanced(file)` | 30-40 | `grep -c '^[[:space:]]*# region'` vs `grep -c '^[[:space:]]*# endregion'`; неравенство → FAIL. Считает ВСЕ # region (включая FUNC_*) |
| `check_grep_summary(file)` | 43-77 | Presence: `# GREP_SUMMARY:` в первых 10 строках (`grep -cE \|\| true` — TRAP[BUG] pipefail race). Keywords: `sed 's/.*# GREP_SUMMARY:[[:space:]]*//' \| tr ',' ' '`, БЕЗ strip HTML-маркеров и БЕЗ skip flags; `grep -qiF -- "$kw" "$file"` (TRAP[BUG] SIGPIPE) |
| `check_module_contract(file)` | 80-91 | `# region MODULE_CONTRACT` и `# endregion MODULE_CONTRACT` присутствуют |
| `check_structure(file)` | 94-103 | `# STRUCTURE:` в первых 10 строках (`grep -cE \|\| true`) |
| `check_yaml_purpose(file)` | 106-113 | `## @purpose` присутствует (только .yaml/.yml) |
| `check_md_sh_refs(file)` | 121-171 | Backtick-only: `` `[^`]+\.sh` `` (GNU -P и BSD -oE ветки идентичны). Skips: абсолютные `/*`. Resolve: `$ref`, `core/entrypoints/$ref`, `core/lib/$ref`, `core/bootstrap/$ref`; `lib/X` → `core/lib/X` (TRAP[BUG-FIX] 2026-07-21); рекурсивный find `core/internal/` maxdepth 5 (TRAP[BUG] SIGPIPE, subshell +o pipefail). TRAP[DECISION] 2026-07-11: НЕ skip-list |
| main | 173-236 | Итерация `"$@"` (staged files); ext filter py\|sh\|md\|yaml\|yml; skip `.venv/*|node_modules/*|__pycache__/*`; НЕТ аргументов → exit 0; exit 1 при любом FAIL |

### 2.3 Ключевые различия двух скриптов (НЕ унифицировать слепо)

| Аспект | lint.sh grepsummary | check-doc-headers.sh |
|--------|--------------------|----------------------|
| Набор файлов | Все tracked (git ls-files) | Staged (args из pre-commit) |
| GREP_SUMMARY экстракция | `^# GREP_SUMMARY:\|^<!-- GREP_SUMMARY:` (много строк в файле) | `# GREP_SUMMARY:` в первых 10 строках (presence) |
| Keywords парсинг | HTML-маркеры strip + flags skip | Без strip, без flags skip |
| .sh-ссылки | Все токены `[\w./-]+\.sh` (lookbehind) | Только backtick `` `ref.sh` `` |
| Разрешение путей | `$PLATFORM_ROOT/$ref` или `$ref` | cwd + известные dirs + lib/-strip + find core/internal/ |
| Исключения | http, /opt/, нет `/`, `..` | абсолютные `/*` |

**Вывод:** общий модуль реализует ОБА поведения через параметризацию (mode scan-all / staged;
backtick_only: bool). Объединение — только на уровне экстракции/валидации, семантика каждого
режима байт-в-байт сохраняется.

### 2.4 Факты окружения (проверены)

- `core/internal/lint/` НЕ существует → создать с `__init__.py` (паттерн `core/internal/shared/`).
- `core/` и `core/internal/` без `__init__.py` — implicit namespace packages; `python3 -c "import core.internal.validate.conflict_checks"` работает (проверено).
- Python ≥3.10 (REQUIRES), PyYAML доступен.
- `.pre-commit-config.yaml`: `check-doc-headers` hook → `core/entrypoints/check-doc-headers.sh` (language: script, files `\.(py|yaml|yml|sh|md)$`, exclude `^\.kilo/|^\.ai/|^\.pytest_cache/|^tests/test_data/|^reports/`); `name-linter` hook → `bash core/entrypoints/lint.sh namelint` (language: system, pass_filenames: false, always_run: true).
- `lint.sh grepsummary` НЕ вызывается из pre-commit — только `namelint`. grepsummary-режим сохраняется для ручного/CI использования (AC5).
- `make lint` → `core/entrypoints/validate.sh --lint` → `core/internal/validate/validate.sh`, который пропускает `--*` флаги — схема-валидация YAML. Поток НЕ меняется (AC6).
- `entrypoint-manifest.yaml` — генерируемый; `allowed_verbs`/`gates` перегенерируются, структурные секции (включая `lint:`) сохраняются verbatim (load_structural_sections, G3 cycle-break). Правка `lint:` секции НЕ ломает `make check-manifests`.
- Секция `name_linter` манифеста: system_exceptions [help, venv, pre-commit-install, pre-commit-run], system_prefixes [test-, gate-, pre-commit-], namespace_collision_names [deploy]. Покрытие равно hardcoded case-исключениям shell (проверено: test-*/gate-*/pre-commit-* префиксы + help/venv литералы).
- Doxyfile существует на root — BUILD_DOXYGEN выполним.

---

## 3. Draft Code Graph (XML)

```xml
<!-- DevPlan 106: lint.sh + check-doc-headers.sh → core/internal/lint/ (Strangler) -->
<knowledge_graph>
  <!-- NEW: единый grepsummary + md-sh-refs валидатор (AC1) -->
  <entity name="core_internal_lint_grepsummary_validator_py"
          type="MODULE"
          keywords="grepsummary, md-sh-refs, GREP_SUMMARY, validator, scan-all, staged, strangler"
          annotation="Единый валидатор GREP_SUMMARY keywords + .sh-ссылок в .md. Два режима:
                      scan-all (git ls-files, lint.sh grepsummary) и staged (ключи по файлу).">
    <function name="extract_keywords_FUNC" keywords="keywords, grepsummary, strip, html-marker, flag-skip"
              annotation="Экстракция keywords из GREP_SUMMARY-строки. mode: scan (strip #/-->/&lt;!-- + skip flags) | staged (без strip, без skip)." />
    <function name="validate_keywords_present_FUNC" keywords="grep-qiF, case-insensitive, literal, substring"
              annotation="Для каждого keyword: case-insensitive literal substring в содержимом файла. Возвращает список ошибок [FAIL]-сообщений." />
    <function name="extract_sh_refs_FUNC" keywords="sh-refs, regex, lookbehind, backtick, GNU, BSD"
              annotation="Экстракция .sh-ссылок: plain (GNU-паттерн (?&lt;!\S)([\w./-]+\.sh)(?!\S) с re.ASCII) | backtick-only (`.sh`). Единый паттерн заменяет GNU/BSD grep-ветвление (TRAP[DECISION])." />
    <function name="resolve_sh_ref_scan_FUNC" keywords="resolve, PLATFORM_ROOT, relative, file-exists"
              annotation="Разрешение ссылки в scan-режиме: repo_root/ref ИЛИ ref (cwd). Skips: http, /opt/, нет /, содержит .." />
    <function name="collect_tracked_files_FUNC" keywords="git-ls-files, find-fallback, tracked"
              annotation="git ls-files (в git-репо) | find-фallback вне git (node_modules/.git/__pycache__/pyc исключены)." />
    <function name="scan_all_FUNC" keywords="scan-all, loop, md-sh-refs, errors, exit-code"
              annotation="Полный прогон по всем tracked файлам: GREP_SUMMARY lines (^# GREP_SUMMARY:|^&lt;!-- GREP_SUMMARY:) + .md → sh-refs. Возвращает (errors, file_count)." />
    <function name="build_parser_FUNC" keywords="argparse, cli" annotation="CLI: scan-all (субкоманда). --help." />
    <function name="main_FUNC" keywords="main, cli, exit-0, exit-1, ldd"
              annotation="Точка входа: парсинг, вызов scan_all, вывод [lint.sh] PASS/FAILED, exit 0/1, IMP:7-9 логи stderr." />
  </entity>

  <!-- NEW: документ-хедер валидатор + namelint (AC2) -->
  <entity name="core_internal_lint_doc_header_validator_py"
          type="MODULE"
          keywords="doc-headers, module-contract, structure, regions, yaml-purpose, namelint, make-target, manifest"
          annotation="Валидация документ-хедеров staged-файлов + namelint (make-target names vs manifest).">
    <function name="check_regions_balanced_FUNC" keywords="region, endregion, balance, count"
              annotation="Счёт строк ^[ \t]*# region vs ^[ \t]*# endregion (re.MULTILINE); неравенство → ошибка. Порт grep -c (check-doc-headers.sh:30-40)." />
    <function name="check_grep_summary_presence_FUNC" keywords="presence, first-10-lines, grep-summary"
              annotation="'# GREP_SUMMARY:' в первых 10 строках. Порт grep -cE || true (TRAP[BUG] pipefail race сохранён как комментарий-история)." />
    <function name="check_structure_FUNC" keywords="structure, first-10-lines"
              annotation="'# STRUCTURE:' в первых 10 строках (TRAP[BUG] pipefail race)." />
    <function name="check_module_contract_FUNC" keywords="module-contract, region, endregion"
              annotation="'# region MODULE_CONTRACT' + '# endregion MODULE_CONTRACT' присутствуют." />
    <function name="check_yaml_purpose_FUNC" keywords="yaml, purpose, @purpose"
              annotation="'## @purpose' присутствует (только yaml/yml)." />
    <function name="check_md_sh_refs_FUNC" keywords="md, backtick, sh-refs, lib-prefix, resolve, find"
              annotation="Backtick-ссылки в .md; skip абсолютных; resolve: cwd/core/entrypoints/core/lib/core/bootstrap + lib/-strip + find core/internal/ maxdepth 5. TRAP[DECISION] no-skip-list, TRAP[BUG-FIX] lib/." />
    <function name="validate_file_FUNC" keywords="validate, ext-filter, skip, venv, node_modules"
              annotation="Применяет ext-правила (py|sh|md|yaml|yml; skip .venv/node_modules/__pycache__). Возвращает список ошибок." />
    <function name="validate_files_FUNC" keywords="loop, files, errors, count"
              annotation="Итерация по файлам; агрегация ошибок; без аргументов → pass (exit 0)." />
    <function name="validate_make_target_names_FUNC" keywords="namelint, make-target, manifest, allowed-verbs, forbidden, system-exceptions"
              annotation="Порт check_namelint: yaml.safe_load манифеста (allowed_verbs/module_lifecycle/forbidden_verbs + name_linter системные исключения), .PHONY из Makefile + makefiles/*.mk, проверки + системные исключения. namespace_collision НЕ реализуется (TRAP[DEBT])." />
    <function name="build_parser_FUNC" keywords="argparse, cli" annotation="CLI: doc-headers <files...> | namelint. --help." />
    <function name="main_FUNC" keywords="main, cli, exit-0, exit-1, ldd"
              annotation="Точка входа: doc-headers/namelint, [PASS]/[FAIL] вывод, exit 0/1, IMP:7-9 логи." />
  </entity>

  <!-- MODIFY: thin facade -->
  <entity name="core_entrypoints_lint_sh" type="SCRIPT" keywords="lint, facade, grepsummary, namelint, python3-m"
          annotation="Фасад ≤40 LOC: --help | grepsummary → python3 -m core.internal.lint.grepsummary_validator scan-all | namelint → python3 -m core.internal.lint.doc_header_validator namelint. source paths.sh → cd PLATFORM_ROOT. Сохраняет GREP_SUMMARY/MODULE_CONTRACT/STRUCTURE/regions (self-hosted).">
    <crosslink from="core_entrypoints_lint_sh" to="core_internal_lint_grepsummary_validator_py" label="delegates-to (scan-all)" />
    <crosslink from="core_entrypoints_lint_sh" to="core_internal_lint_doc_header_validator_py" label="delegates-to (namelint)" />
  </entity>

  <!-- MODIFY: thin facade -->
  <entity name="core_entrypoints_check_doc_headers_sh" type="SCRIPT" keywords="check-doc-headers, facade, pre-commit, python3-m, staged"
          annotation="Фасад ≤40 LOC: source paths.sh → cd PLATFORM_ROOT → exec python3 -m core.internal.lint.doc_header_validator doc-headers &quot;$@&quot;. Сохраняет GREP_SUMMARY/MODULE_CONTRACT/STRUCTURE/regions (self-hosted).">
    <crosslink from="core_entrypoints_check_doc_headers_sh" to="core_internal_lint_doc_header_validator_py" label="delegates-to (doc-headers)" />
  </entity>

  <!-- NEW: unit tests (AC1/AC10) -->
  <entity name="tests_unit_test_grepsummary_validator_py" type="TEST"
          keywords="test, grepsummary, scan-all, md-sh-refs, tmp_path, caplog, ldd"
          annotation="Unit-тесты grepsummary_validator: keywords scan/staged, html-strip, flags-skip, sh-refs plain/backtick, исключения (http/opt/prose/..), resolve, scan_all e2e. LDD caplog IMP:9, TRAP[TEST] на каждой функции.">
    <crosslink from="tests_unit_test_grepsummary_validator_py" to="core_internal_lint_grepsummary_validator_py" label="unit-tests" />
  </entity>

  <!-- NEW: unit tests (AC2/AC10) -->
  <entity name="tests_unit_test_doc_header_validator_py" type="TEST"
          keywords="test, doc-headers, module-contract, structure, regions, yaml-purpose, namelint, tmp_path, caplog, ldd"
          annotation="Unit-тесты doc_header_validator: все 6 doc-проверок, ext-фильтр, .venv skip, namelint (allowed/forbidden/module_lifecycle/system exceptions/manifest-missing). LDD caplog IMP:9, TRAP[TEST] на каждой функции.">
    <crosslink from="tests_unit_test_doc_header_validator_py" to="core_internal_lint_doc_header_validator_py" label="unit-tests" />
  </entity>

  <!-- MODIFY: manifest registry (AC8) -->
  <entity name="core_entrypoint_manifest_yaml" type="CONFIG" keywords="manifest, lint, delegates-to, script-registry"
          annotation="Секция lint: (строки 150-163) — delegates_to обновлены на Python-модули. Структурная секция — генератор сохраняет verbatim (G3 cycle-break), check-manifests не ломается.">
    <crosslink from="core_entrypoint_manifest_yaml" to="core_internal_lint_grepsummary_validator_py" label="delegates-to (declared)" />
    <crosslink from="core_entrypoint_manifest_yaml" to="core_internal_lint_doc_header_validator_py" label="delegates-to (declared)" />
  </entity>
</knowledge_graph>
```

---

## 4. Step-by-Step Data Flow (процессная симуляция)

### 4.1 Режим `grepsummary` (AC5) — `core/entrypoints/lint.sh grepsummary`

```
▶ lint.sh grepsummary
  → source paths.sh → PLATFORM_ROOT = PATHS_CORE_DIR/..
  → cd $PLATFORM_ROOT
  → exec python3 -m core.internal.lint.grepsummary_validator scan-all
      → REPO_ROOT = Path(__file__).resolve().parents[3]           # core/internal/lint/xx.py
      → files = collect_tracked_files(REPO_ROOT)
            ◇ git rev-parse --git-dir ? → git ls-files
            └ else → find REPO_ROOT (исключая node_modules/.git/__pycache__/*.pyc)
      → for f in files:
            [ ! -f f ] → continue
            for line in findall(r'^# GREP_SUMMARY:|^<!-- GREP_SUMMARY:', f, re.M):
                kws = extract_keywords(line, mode="scan")         # strip #/-->/<!--, skip flags/empty
                errors += validate_keywords_present(f, kws)       # case-insensitive literal substring
            if f.endswith(".md"):
                refs = extract_sh_refs(text, backtick_only=False) # GNU-паттерн, re.ASCII
                errors += [resolve_sh_ref_scan(ref) for ref in refs
                           if not skip_scan(ref)]                 # skip: ^http, ^/opt/, нет /, ..
      → [lint.sh] FAILED — N error(s) / [lint.sh] PASS — ...
      → exit 1 | 0;  IMP:9 логи в stderr
```

### 4.2 Режим `doc-headers` (AC7) — `core/entrypoints/check-doc-headers.sh <files...>`

```
▶ pre-commit check-doc-headers hook → staged files (exclude .kilo/.ai/test_data/reports)
  → check-doc-headers.sh "$@" (language: script, CWD = repo root)
      → source paths.sh → cd PLATFORM_ROOT
      → exec python3 -m core.internal.lint.doc_header_validator doc-headers "$@"
          → for file in args:
                ext filter (py|sh|md|yaml|yml) + skip (.venv|node_modules|__pycache__) → continue
                errs = validate_file(file)
                    check_grep_summary_presence   → '# GREP_SUMMARY:' в первых 10 строках
                    + extract_keywords(mode="staged") + validate_keywords_present   # без strip/flags
                    check_structure               → '# STRUCTURE:' в первых 10 строках
                    if ext != md: check_module_contract  → region + endregion MODULE_CONTRACT
                    check_regions_balanced        → count(# region) == count(# endregion)
                    if yaml|yml: check_yaml_purpose → '## @purpose'
                    if md: check_md_sh_refs       → backtick refs; resolve cwd/dirs/lib/-strip/find
                [CHECK] file / [FAIL] ... на каждую ошибку
          → без аргументов → [PASS] exit 0
          → [FAIL] check-doc-headers: One or more files failed... / [PASS] ... exit 1 | 0
```

### 4.3 Режим `namelint` (AC7) — `core/entrypoints/lint.sh namelint`

```
▶ pre-commit name-linter hook → bash core/entrypoints/lint.sh namelint (CWD = repo root)
  → source paths.sh → cd PLATFORM_ROOT
  → exec python3 -m core.internal.lint.doc_header_validator namelint
      → manifest = yaml.safe_load(REPO_ROOT/core/entrypoint-manifest.yaml)
      → allowed = manifest['allowed_verbs']; lifecycle = manifest['module_lifecycle']
      → forbidden = manifest['forbidden_verbs']
      → exceptions = manifest['name_linter']  # system_exceptions + system_prefixes (behavior-equivalent)
      → targets = parse_phony(REPO_ROOT/Makefile) + parse_phony(REPO_ROOT/makefiles/*.mk)
            ◇ regex r'^\.PHONY:\s*(.+)$' per line → split whitespace
      → for t in targets:
            t in forbidden        → FAIL (FORBIDDEN)
            t in allowed          → pass
            t in lifecycle        → pass
            t == literal-exception or t.startswith(prefix) → pass
            else                  → FAIL (not in allowed_verbs and not a system exception)
      → [lint.sh] FAILED — N error(s) / [lint.sh] PASS — ... exit 1 | 0
```

### 4.4 Порядок миграции (этапы реализации)

```
1. __init__.py + grepsummary_validator.py   (базовый модуль, без зависимостей)
2. doc_header_validator.py                  (imports из grepsummary_validator — sequential, НЕ swarm)
3. lint.sh → фасад; check-doc-headers.sh → фасад
4. entrypoint-manifest.yaml: lint: секция delegates_to
5. unit-тесты (2 файла) + make test-inventory-sync
6. Верификация: pytest → pre-commit run → make lint → make gate MODE=fast
7. Doxygen (Step 7 BUILD_DOXYGEN)
```

---

## 5. Архитектура модулей (контракты для Coder)

### 5.1 `core/internal/lint/grepsummary_validator.py` (AC1)

- **Импортируемый API:** `extract_keywords(line, mode: Literal["scan","staged"]) -> list[str]`,
  `validate_keywords_present(file: Path, keywords) -> list[str]`,
  `extract_sh_refs(text, backtick_only: bool) -> list[str]`,
  `resolve_sh_ref_scan(repo_root: Path, ref: str) -> bool`,
  `collect_tracked_files(repo_root: Path) -> list[Path]`,
  `scan_all(repo_root: Path) -> tuple[list[str], int]`.
- **CLI:** `python3 -m core.internal.lint.grepsummary_validator scan-all` → exit 0/1.
- **Паттерны (порты grep):**
  - GREP_SUMMARY lines: `re.compile(r'^# GREP_SUMMARY:|^<!-- GREP_SUMMARY:', re.MULTILINE)`.
  - Плейн .sh-ссылки: `re.compile(r'(?<!\S)([\w./-]+\.sh)(?!\S)', re.ASCII)` — канонический
    GNU-паттерн; BSD-fallback НЕ нужен (Python lookbehind портабелен). TRAP[DECISION].
  - Backtick: `re.compile(r'`[^`]+\.sh`', re.ASCII)` + strip backticks + `sorted(set(...))`.
- **Порядок strip в scan-режиме (байт-в-байт):** `#` → `-->` → `<!--`; skip `""`, `-*`.
- **LDD:** `logger.info("[IMP:7-9][func][block] ...")`; CLI-логи в stderr c префиксами `[lint.sh]`.
- **TRAP-комментарии:** GNU/BSD unification (TRAP[DECISION] 2026-07-31); SIGPIPE-race история
  (TRAP[BUG] — в Python pipe отсутствует, комментарий-история).

### 5.2 `core/internal/lint/doc_header_validator.py` (AC2)

- **Imports:** `from core.internal.lint.grepsummary_validator import extract_keywords, validate_keywords_present, extract_sh_refs`.
- **Импортируемый API:** `validate_file(file: Path) -> list[str]`, `validate_files(files) -> tuple[list[str], int]`,
  `validate_make_target_names(repo_root: Path) -> list[str]`.
- **CLI:** `doc-headers <files...>` | `namelint`.
- **Паттерны:** regions `re.compile(r'^[ \t]*# region', re.MULTILINE)` /
  `^[ \t]*# endregion` (замена `grep -c '^[[:space:]]*# region'`); presence-проверки — первые 10 строк.
- **namelint:** `yaml.safe_load` манифеста; `parse_phony(makefile) -> set[str]`; исключения из
  `name_linter` секции (system_exceptions литералы + system_prefixes префиксы); namespace_collision
  НЕ реализуется → TRAP[DEBT].
- **LDD:** IMP:7-9 логи; префиксы `[FAIL]`/`[PASS]`/`[CHECK]` сохранены для пре-коммит-вывода.
- **TRAP-комментарии:** все 4 из check-doc-headers.sh (SIGPIPE ×2, lib/-prefix BUG-FIX, no-skip-list
  DECISION) — переносятся как история.

### 5.3 Self-hosting (новые файлы проходят собственные проверки)

Все новые .py/.sh обязаны иметь: GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT region + endregion,
сбалансированные # region/# endregion (count-check не сломается), `# region FUNC_X` на каждую функцию.
Фасады — те же требования (их проверяет pre-commit check-doc-headers при коммите).

### 5.4 Порядок зависимостей

`doc_header_validator.py` → `grepsummary_validator.py` (импорт). Swarm-параллелизация НЕ применяется
к самим модулям (зависимость), но тест-файлы могут писаться параллельно после фиксации API (§8.5).

---

## 6. Acceptance Criteria (AC1–AC10, mapped to Brief)

| # | Критерий (Brief) | Верификация (команда/тест) |
|---|------------------|----------------------------|
| AC1 | `core/internal/lint/grepsummary_validator.py` — единый grepsummary + md-sh-refs валидатор | Файл существует; `python3 -m core.internal.lint.grepsummary_validator scan-all` выполняется; `tests/unit/test_grepsummary_validator.py` зелёный |
| AC2 | `core/internal/lint/doc_header_validator.py` — module-contract + structure + yaml-purpose + regions + namelint | Файл существует; `python3 -m core.internal.lint.doc_header_validator --help`; `tests/unit/test_doc_header_validator.py` зелёный |
| AC3 | `lint.sh` ≤ 40 LOC (color helpers + вызов Python) | `wc -l core/entrypoints/lint.sh` ≤ 40; содержит `python3 -m` вызовы; НЕ содержит `check_grepsummary`/`check_sh_refs_in_md`/`check_namelint` тел |
| AC4 | `check-doc-headers.sh` ≤ 40 LOC | `wc -l core/entrypoints/check-doc-headers.sh` ≤ 40; `exec python3 -m core.internal.lint.doc_header_validator doc-headers "$@"` |
| AC5 | grepsumsummary не дублируется — lint.sh делегирует в grepsummary_validator.py | `grep -cE 'check_grepsummary|check_sh_refs_in_md' core/entrypoints/lint.sh` == 0; grep `grepsummary_validator` в lint.sh > 0 |
| AC6 | `make lint` проходит идентично | `make lint` → exit 0 (схема-валидация YAML; поток validate.sh --lint не тронут — проверка `git diff core/entrypoints/validate.sh core/internal/validate/validate.sh` пуст) |
| AC7 | Pre-commit hooks работают идентично | `pre-commit run check-doc-headers --all-files` → exit 0; `pre-commit run name-linter --all-files` → exit 0 |
| AC8 | `entrypoint-manifest.yaml` обновлён | Секция `lint:` → `delegates_to` содержит `core/internal/lint/grepsummary_validator.py` и `core/internal/lint/doc_header_validator.py`; `make check-manifests` → exit 0 |
| AC9 | `make gate MODE=fast` зелёный | `make gate MODE=fast` → exit 0 |
| AC10 | Все false-positive исключения сохранены | Unit-тесты на исключения: http-ссылка, /opt/ путь, проза без `/`, `..`, абсолютный `/*`, `lib/ssh.sh` (strip-префикс), `.venv/`, `node_modules/`, `__pycache__/`, HTML-маркеры в keywords — все pass без ошибок |

---

## 7. File Manifest

| Файл | Действие | Содержание |
|------|----------|-----------|
| `core/internal/lint/__init__.py` | NEW | Пустой (маркер пакета, паттерн `core/internal/shared/`) |
| `core/internal/lint/grepsummary_validator.py` | NEW | ~300 LOC: extract_keywords/validate_keywords_present/extract_sh_refs/resolve_sh_ref_scan/collect_tracked_files/scan_all + CLI (AC1) |
| `core/internal/lint/doc_header_validator.py` | NEW | ~380 LOC: 6 doc-проверок + validate_file/validate_files + validate_make_target_names (namelint) + CLI (AC2) |
| `core/entrypoints/lint.sh` | MODIFY | Фасад ≤40 LOC: --help/grepsummary/namelint; source paths.sh → cd PLATFORM_ROOT; exec python3 -m (AC3) |
| `core/entrypoints/check-doc-headers.sh` | MODIFY | Фасад ≤40 LOC: source paths.sh → cd PLATFORM_ROOT; exec python3 -m ... doc-headers "$@" (AC4) |
| `tests/unit/test_grepsummary_validator.py` | NEW | ~180 LOC: тесты grepsummary_validator (таблица §9) |
| `tests/unit/test_doc_header_validator.py` | NEW | ~220 LOC: тесты doc_header_validator + namelint (таблица §9) |
| `core/entrypoint-manifest.yaml` | MODIFY | Секция `lint:` (строки 150-163): delegates_to → Python-модули (AC8) |
| `tests/test_inventory.yaml` | MODIFY | Регенерация `make test-inventory-sync` после добавления тестов (если inventory gate активен) |

**НЕ трогать:** `core/entrypoints/validate.sh`, `core/internal/validate/validate.sh` (AC6),
`.pre-commit-config.yaml` (hook entry-строки остаются — фасады сохраняют пути и имена),
`core/entrypoints/check-file-lines.sh` (check_file_lines вне скоупа — P5).

---

## 8. Implementation Steps

**Step 1 — STUDY_PLAN.** Прочитать настоящий DevPlan полностью + `core/entrypoints/lint.sh` +
`core/entrypoints/check-doc-headers.sh` + секции `lint:`/`name_linter`/`allowed_verbs`/
`module_lifecycle`/`forbidden_verbs` манифеста. ВНИМАНИЕ: Brief.md не читать (Architect artifact).
§INVARIANT: DevPlan — единственный источник.

**Step 2 — DETECT_LEGACY.** Целевые файлы уже содержат `# GREP_SUMMARY:` — не мигрировать разметку.
Скопировать TRAP-комментарии (инвентарь §10) в Python-модули до написания кода.

**Step 3 — IMPLEMENT_MODULES (sequential: зависимость doc_header_validator → grepsummary_validator).**
1. `core/internal/lint/__init__.py` + `grepsummary_validator.py` (полный семантический markup:
   MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE, region-маркеры, LDD IMP:7-9, TRAP[DECISION] GNU/BSD).
   Байт-в-байт парсинг keywords per §2.3/§5.1.
2. `doc_header_validator.py` — импортирует из grepsummary_validator; 6 проверок + namelint per §5.2.
   `yaml.safe_load` (НЕ awk). Системные исключения из секции `name_linter` манифеста.
3. Проверить self-hosting: оба файла проходят `pre-commit run check-doc-headers --all-files`.

**Step 4 — FACADES.** Усечь `lint.sh` и `check-doc-headers.sh` до ≤40 LOC (AC3/AC4):
- `lint.sh`: сохранить shebang, GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT, `set -euo pipefail`,
  color helpers, source paths.sh → `PLATFORM_ROOT="$(cd "$PATHS_CORE_DIR/.." && pwd)"` → `cd` →
  `case ${1:-}`: `--help|-h` usage; `grepsummary` → `exec python3 -m core.internal.lint.grepsummary_validator scan-all`;
  `namelint` → `exec python3 -m core.internal.lint.doc_header_validator namelint`; `*` → usage exit 1.
- `check-doc-headers.sh`: сохранить shebang, doc-хедеры, `set -euo pipefail`, source paths.sh →
  cd PLATFORM_ROOT → `exec python3 -m core.internal.lint.doc_header_validator doc-headers "$@"`.
- НЕ использовать `python3 -c`/heredoc (hook no-new-inline-python3 блокирует) — только `python3 -m`.

**Step 5 — MANIFEST (AC8).** В `core/entrypoint-manifest.yaml` секция `lint:`:
- `lint.sh` → `delegates_to: core/entrypoints/lint.sh → core/internal/lint/grepsummary_validator.py (grepsummary) + core/internal/lint/doc_header_validator.py (namelint)`
- `check-doc-headers.sh` → `delegates_to: core/entrypoints/check-doc-headers.sh → core/internal/lint/doc_header_validator.py`
- `make check-manifests` → exit 0 (структурная секция сохраняется генератором verbatim).

**Step 6 — IMPLEMENT_TESTS ($TEST_SPEC §9).** Два тест-файла по таблице: native imports, tmp_path,
caplog LDD-траектория (паттерн test_conflict_checks.py), `# 🧪 TRAP[TEST]` на каждой функции.
`make test-inventory-sync` (если inventory gate активен).

**Step 7 — VERIFY_TESTS.** `pytest tests/unit/test_grepsummary_validator.py tests/unit/test_doc_header_validator.py -v`
→ все pass, в выводе IMP:9. Затем AC7: `pre-commit run check-doc-headers --all-files`,
`pre-commit run name-linter --all-files`. Затем AC6/AC9: `make lint`; `make gate MODE=fast`.

**Step 8 — FINAL_AUDIT.** Self-critique: region/endregion сбалансированы во всех изменённых файлах;
GREP_SUMMARY на каждом файле; TRAP[TEST] на каждом тесте; TRAP[DEBT] namespace_collision на месте;
`grep -cE 'check_grepsummary|check_sh_refs_in_md' core/entrypoints/lint.sh` == 0 (AC5).

**Step 9 — BUILD_DOXYGEN.** Doxyfile существует. `doxygen Doxyfile` (вывод в temp-файл) → zero
warnings по `## @-tags`; fix на источнике при ошибках.

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_grepsummary_validator.py` | `test_extract_keywords_scan_mode` | scan-режим: HTML-маркеры `#`/`-->`/`<!--` strip, flags `-x`/`--x` skip, пустые skip | `extract_keywords` |
| `tests/unit/test_grepsummary_validator.py` | `test_extract_keywords_staged_mode` | staged-режим: без strip (raw keywords, включая `-->` сохраняются) | `extract_keywords` |
| `tests/unit/test_grepsummary_validator.py` | `test_validate_keywords_present` | keyword найден (case-insensitive) / не найден → ошибка | `validate_keywords_present` |
| `tests/unit/test_grepsummary_validator.py` | `test_extract_sh_refs_plain_backtick` | plain-паттерн находит токены; backtick-only не находит не-обрамлённые | `extract_sh_refs` |
| `tests/unit/test_grepsummary_validator.py` | `test_sh_ref_scan_exceptions_preserved` | AC10: http-ссылка, `/opt/`, проза без `/`, `..` → skip без ошибок | `scan_all`/resolve |
| `tests/unit/test_grepsummary_validator.py` | `test_scan_all_full_pass_and_fail` | tmp_path: файл с валидным GREP_SUMMARY → (errors=0); с битым keyword → errors>0; .md с несуществующей .sh-ссылкой → errors>0 | `scan_all` |
| `tests/unit/test_doc_header_validator.py` | `test_regions_balanced` | равные/неравные счётчики # region/# endregion (включая FUNC_-регионы) | `check_regions_balanced` |
| `tests/unit/test_doc_header_validator.py` | `test_grep_summary_presence_first10` | presence в первых 10 строках; отсутствие → ошибка | `check_grep_summary_presence` |
| `tests/unit/test_doc_header_validator.py` | `test_structure_presence` | `# STRUCTURE:` в первых 10 строках | `check_structure` |
| `tests/unit/test_doc_header_validator.py` | `test_module_contract_presence` | region + endregion MODULE_CONTRACT | `check_module_contract` |
| `tests/unit/test_doc_header_validator.py` | `test_yaml_purpose_required` | yaml без `## @purpose` → ошибка; py/md не проверяется | `check_yaml_purpose` |
| `tests/unit/test_doc_header_validator.py` | `test_md_sh_refs_resolution` | AC10: backtick-ссылка реальная → pass; `lib/ssh.sh` strip → pass; битая → ошибка; абсолютный `/*` → skip | `check_md_sh_refs` |
| `tests/unit/test_doc_header_validator.py` | `test_validate_file_ext_filter` | .venv/node_modules/__pycache__ skip; ext вне списка skip; без аргументов → pass | `validate_file`/`validate_files` |
| `tests/unit/test_doc_header_validator.py` | `test_namelint_targets` | allowed → pass; forbidden → FAIL; module_lifecycle → pass; system-префиксы test-/gate-/pre-commit- → pass; help/venv → pass; неизвестный → FAIL | `validate_make_target_names` |
| `tests/unit/test_doc_header_validator.py` | `test_namelint_missing_manifest` | манифест отсутствует → FAIL "Manifest not found" (поведение lint.sh:90-94) | `validate_make_target_names` |

Конвенции: native imports (`from core.internal.lint.<mod> import ...`), `tmp_path` (zero hardcoded
paths), caplog LDD-траектория перед assert'ами (паттерн `_print_trajectory` из
test_conflict_checks.py), IMP:9 log в успешных сценариях, `# 🧪 TRAP[TEST]` на каждой функции.
Тесты в `tests/unit/` (без Docker, без subprocess).

---

## 10. Риски, TRAP-инвентарь и решения

### 10.1 Сохраняемые TRAP-комментарии (перенос в Python как история)

| TRAP | Источник | Куда |
|------|----------|------|
| `TRAP[BUG]` pipefail + head\|grep -qE race (presence-проверки) | check-doc-headers.sh:47-51, 96-97 | `check_grep_summary_presence`/`check_structure` — в Python race отсутствует (прямой файловый I/O), комментарий-история |
| `TRAP[BUG]` SIGPIPE (grep -q закрывает stdin) | check-doc-headers.sh:63-66 | `validate_keywords_present` — в Python pipes нет |
| `TRAP[BUG]` SIGPIPE subshell +o pipefail (find\|grep -q) | check-doc-headers.sh:159-161 | `check_md_sh_refs` — рекурсивный поиск через Path.walk с maxdepth |
| `TRAP[BUG-FIX]` `lib/<name>.sh` strip-префикс | check-doc-headers.sh:147-156 | `check_md_sh_refs` — путь к разрешению ссылок |
| `TRAP[DECISION]` deleted script refs — НЕ skip-list | check-doc-headers.sh:116-120 | `check_md_sh_refs` — отказ от skip-list документирован |

### 10.2 Новые TRAP-решения (добавить при реализации)

- `TRAP[DECISION] · 2026-07-31 · — · GNU/BSD grep-унификация: единый lookbehind-паттерн в Python`
  · Rejected: сохранение двух веток с capability probe (`grep -P ''`)
  · Reason: Python re имеет fixed-width lookbehind на всех платформах (включая macOS BSD);
  GNU-паттерн — строгое подмножество BSD-fallback → меньше потенциальных ложных срабатываний,
  AC10 не нарушается (все ранее проходившие файлы продолжают проходить).
  · Rev: если появится потребность в BSD-расширенном поведении (матч внутри слова) — вернуть
  параметр `loose_bsd_match: bool = False`.
- `TRAP[DECISION] · 2026-07-31 · — · namelint-исключения из секции name_linter манифеста`
  · Rejected: hardcoded case-исключения (текущий shell-код lint.sh:156-163)
  · Reason: манифест — Source of Truth (G3); проверено: system_exceptions [help, venv,
  pre-commit-install, pre-commit-run] + system_prefixes [test-, gate-, pre-commit-] покрывают
  ровно case-паттерны shell. Behavior-equivalent.
  · Rev: если name_linter секция в манифесте начнёт расходиться с политикой — обновлять манифест.
- `TRAP[DECISION] · 2026-07-31 · — · namelint размещён в doc_header_validator.py`
  · Rejected: третий модуль name_validator.py
  · Reason: Brief §План (строка 28) явно перечисляет namelint в doc_header_validator.py;
  файл-манифест Brief @IMPACTS содержит ровно 2 Python-модуля — третий модуль создал бы drift
  между DevPlan и Brief.
  · Rev: при росте namelint-логики >150 LOC — вынести в отдельный модуль с обновлением манифеста.
- `TRAP[DEBT] · 2026-07-31 · LO · namespace_collision_names: [deploy] в манифесте не реализуется`
  · Observed: секция name_linter манифеста содержит namespace_collision_names, shell-namelint
  его не проверяет
  · Suspected: запланированная, но не реализованная проверка коллизий имён
  · Impact: манифест заявляет больше, чем проверяется (минорный drift)
  · When: during DevPlan 106 namelint-порта — deferred, вне скоупа (AC6: поведение идентично)
- `TRAP[DEBT] · 2026-07-31 · LO · check_file_lines/check_shellcheck_directives в Brief не существуют в коде`
  · Observed: Brief упоминает функции, отсутствующие в исходниках (check_file_lines — в отдельном
  скрипте check-file-lines.sh; check_shellcheck_directives — нет нигде)
  · Suspected: устаревшее описание более ранней версии скриптов
  · Impact: следующий агент может искать несуществующие функции
  · When: during DevPlan 106 drift-аудита — зафиксировано в §2, НЕ реализуется

### 10.3 Риски

| Риск | Митигация |
|------|-----------|
| Семантический рассинхрон парсинга keywords между режимами | Таблицы §2.3 + отдельные тесты на каждый режим (scan vs staged) |
| Новые .py-файлы не проходят собственный check-doc-headers | Self-hosting требование §5.3 + ранний прогон `pre-commit run check-doc-headers` в Step 3 |
| `make check-manifests` ломается после правки манифеста | Генератор сохраняет структурные секции verbatim (G3); верификация `make check-manifests` в Step 5 |
| Ruff format новые файлы | `ruff format core/internal/lint/ tests/unit/test_*_validator.py` (make fix-gate) |
| Быстрый скрипт становится медленнее (Python startup ~100ms) | Некритично для pre-commit (прочие hooks дороже); single subprocess на прогон |

$QA_VERIFICATION
VERDICT:               PARTIAL — 1 фактологический дефект исправлен (Python 3.14.6 → ≥3.10),
                       1 непроверяемое утверждение (import workability — PEP 420 namespace packages
                       должны позволять, но Coder должен верифицировать при создании модуля).
                       Все остальные проверки пройдены: протокольная compliance (7 полей
                       $ARTIFACT_CONTRACT, $START/$END, Draft Code Graph, Data Flow, File Manifest,
                       Implementation Steps, $TEST_SPEC), Brief→DevPlan fidelity (все отклонения
                       документированы и обоснованы), TRAP-покрытие (5/5 из shell-исходников),
                       фактологическая точность (23/24 утверждений верны), AC mapping (10/10).
ISSUES_FOUND:          1. [MEDIUM] Python 3.14.6 — несуществующая версия. Исправлено на
                          "Python ≥3.10 (REQUIRES), PyYAML доступен" (line 136).
                       2. [LOW] Import workability для `core.internal.lint.*` не верифицирован
                          из-за tool restrictions. PEP 420 namespace packages должны позволять
                          импорт без __init__.py в родительских директориях. Coder должен
                          проверить `python3 -c "import core.internal.lint.grepsummary_validator"`
                          после создания модуля.
CORRECTIONS_APPLIED:   1. DevPlan:136: исправлена версия Python (3.14.6 → ≥3.10).
                       2. Настоящая $QA_VERIFICATION секция добавлена.
VERIFICATION_TIMESTAMP: 2026-07-31T18:19:13+03:00
VERIFIED_AGAINST_SHA:  fbe306d4284d9105193605378be28eb64b3c6795
QA_REPORT:             .ai/plans/106-lint-headers-consolidation/03-VerificationReport.md
$END_DEVPLAN
