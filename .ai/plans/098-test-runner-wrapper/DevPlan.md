$START_DEVPLAN
# DevPlan 098 — Test Runner Wrapper (Level A)

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 2-5 лишних итераций bash-tool при каждом запуске тестов агентом.
                       Создать тонкую Python-обёртку над pytest, которая гарантирует получение
                       компактного machine-readable вывода за 1 вызов bash-tool.
DESCRIPTION:           Python-модуль `core/internal/test_runner.py` (~200 LOC) запускает pytest
                       с `--junitxml`, парсит XML через stdlib `xml.etree.ElementTree`, печатает
                       компактный вывод (<100 строк): PASS/FAIL/SKIP/ERROR counts + failed test
                       list с traceback. Новый make target `test-summary`.
RATIONALE:             Bash-tool агента имеет дефолтный таймаут 120s и лимит вывода 2000 строк /
                       51200 bytes. `pytest -v --tb=short` на 100+ тестах превышает оба лимита,
                       что приводит к:
                       - Потере вывода при таймауте → перезапуск с большим таймаутом
                       - Усечению вывода → grep-гадание, redirect в файл
                       - Отсутствию count'ов → отдельный `--collect-only` run
                       - JUnit XML обнаруживается только на 4-6 итерации
                       Обёртка гарантирует: 1 вызов → полный структурированный результат.
ACCEPTANCE_CRITERIA:   AC1: `make test-summary MARKER=static_audit` — вывод <100 строк,
                            содержит PASS/FAIL/SKIP/ERROR counts + failed list, exit code = pytest exit code
                        AC2: `make test-summary MARKER=all` — то же для полного suites
                        AC3: Вывод не превышает 2000 строк даже при 100+ failures
                        AC4: JUnit XML temporary file автоочищается после парсинга
                        AC5: `make test-summary` без MARKER → static_audit (безопасный default)
                        AC6: `make test-summary MARKER=smoke` — работает с Docker-зависимыми тестами
                        AC7: `make test-summary MARKER=static` — эквивалент `make test MARKER=static`
                            (validate.sh + lint + pytest), не только pytest-часть
                        AC8: subprocess timeout configurable (--timeout, default 1800s) — wrapper
                            не висит бесконечно при зависшем Docker healthcheck
                        AC9: `allowed_verbs` обновляется через `make generate-manifests`, НЕ ручной
                            правкой YAML (Invariant 11: Manifest Generation Contract)
                        AC10: `PYTEST_NO_ESCALATION=1` проксируется в subprocess — anti-loop
                            контракт из .kilo/rules/testing.md сохранён
                        AC11: Unit-тесты в tests/unit/test_test_runner.py покрывают parse_junit_xml
                            и format_summary (Test Honesty R1/R2)
IMPLEMENTS:            Уровень A из superposition-анализа проблем тестирования (см. Brief.md §Superposition)
IMPACTS:               `core/internal/test_runner.py` (NEW), `makefiles/ci.mk` (+test-summary target
                        + .PHONY registration), `core/entrypoint-manifest.yaml` (REGENERATED через
                        make generate-manifests, НЕ ручная правка), `tests/unit/test_test_runner.py` (NEW)
REQUIRES:              Python 3.10+, xml.etree.ElementTree (stdlib). PYTEST_NO_ESCALATION=1 env var.
                        Namespace packages (core/ и core/internal/ БЕЗ __init__.py — Python 3.3+ PEP 420)
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix (6 categories → Уровень A решает 4)

⚠️ NOTE: Формальный superposition-анализ (Brief.md §Superposition) должен быть создан перед
implementation. Текущая матрица — предварительная категоризация из DevPlan.

| # | Категория | Уровень A | Как решается |
|---|-----------|:---------:|-------------|
| C1 | Bash-tool timeout 120s → потеря вывода | ✅ | Вывод <100 строк → не превышает buffer. JUnit XML <100KB → не усекается. |
| C2 | Усечение вывода 2000 строк → grep-гадание | ✅ | Парсинг JUnit XML, компактный summary. Нет raw pytest -v вывода. |
| C3 | Нет pre-knowledge test count → лишний --collect-only | ✅ | Counts в первой же строке вывода: `PASS: 203 | FAIL: 12 | SKIP: 0 | ERROR: 1 | TOTAL: 216` |
| C4 | CWD-poisoning → каскадные shell-init ошибки | ❌ | Не в скоупе Уровня A. Будет в Уровне B (preflight gate). |
| C5 | Concurrent modification → collection errors | ❌ | Не в скоупе Уровня A. |
| C6 | Нет единого формата → перебор флагов | ✅ | Единый формат вывода для всех MARKER'ов. |

---

## 2. Draft Code Graph

```xml
<code_graph>
  <entity id="test_runner_py" type="MODULE" keywords="pytest wrapper junit xml compact output">
    <annotation>Python module: core/internal/test_runner.py — ~200 LOC</annotation>
    <crossLinks>
      <link target="make_test_summary" relation="called_by"/>
      <link target="junit_xml_parser" relation="uses"/>
    </crossLinks>
  </entity>

  <entity id="make_test_summary" type="MAKE_TARGET" keywords="test summary compact agent output">
    <annotation>Makefile target in makefiles/ci.mk — delegates to test_runner.py</annotation>
    <crossLinks>
      <link target="test_runner_py" relation="calls"/>
      <link target="entrypoint_manifest" relation="registered_in"/>
    </crossLinks>
  </entity>

  <entity id="junit_xml_parser_FUNC" type="FUNCTION" keywords="xml etree junit testsuite testcase parse">
    <annotation>parse_junit_xml(path) → TestSummary dataclass</annotation>
    <crossLinks>
      <link target="test_summary_CLASS" relation="returns"/>
    </crossLinks>
  </entity>

  <entity id="test_summary_CLASS" type="DATACLASS" keywords="pass fail skip error count tests">
    <annotation>Immutable result: pass_count, fail_count, skip_count, error_count, failed_tests list</annotation>
    <crossLinks>
      <link target="format_summary_FUNC" relation="consumed_by"/>
    </crossLinks>
  </entity>

  <entity id="format_summary_FUNC" type="FUNCTION" keywords="format compact output print summary">
    <annotation>format_summary(TestSummary) → str — compact <100 line output</annotation>
    <crossLinks>
      <link target="test_summary_CLASS" relation="consumes"/>
    </crossLinks>
  </entity>
</code_graph>
```

---

## 3. File Manifest

| # | Файл | Действие | LOC | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/test_runner.py` | CREATE | ~200 | Python-обёртка: pytest → JUnit XML → парсинг → компактный вывод |
| F2 | `makefiles/ci.mk` | MODIFY | +20 | Новый target `test-summary` + регистрация в `.PHONY` (строка 12) |
| F3 | `core/entrypoint-manifest.yaml` | REGENERATE | (auto) | `allowed_verbs` обновляется через `make generate-manifests` (НЕ ручная правка — Invariant 11) |
| F4 | `tests/unit/test_test_runner.py` | CREATE | ~120 | Unit-тесты parse_junit_xml, format_summary, _build_pytest_args (Test Honesty R1/R2) |
| F5 | `Brief.md` (опционально) | CREATE | — | Формальный superposition-анализ (5 options × decision matrix) перед implementation |

---

## 4. Step-by-Step Data Flow

```
make test-summary MARKER=static_audit
│
├─► makefiles/ci.mk: test-summary target
│   └─► .venv/bin/python -m core.internal.test_runner --marker static_audit
│
├─► test_runner.py: main()
│   ├─► argparse: --marker, --node, --project, --timeout (default 1800s)
│   ├─► _build_pytest_args(marker) → list[str]
│   │   └─► Резолвит marker в pytest -m выражение
│   │       Для MARKER=static: сначала validate.sh + lint (как ci.mk строки 26-35),
│   │       затем pytest (ПОЛНАЯ эквивалентность, а не только pytest-часть)
│   ├─► tempfile.mkdtemp() → junit_path = tmpdir/report.xml
│   ├─► subprocess.run(pytest_args + ["--junitxml", junit_path],
│   │       env={**os.environ, "PYTEST_NO_ESCALATION": "1"},  # anti-loop контракт
│   │       timeout=args.timeout, capture_output=True)
│   │   └─► timeout=1800s default (configurable). При timeout → TimeoutExpired handler:
│   │       печатает "TIMEOUT after {N}s", exit code 124
│   ├─► parse_junit_xml(junit_path) → TestSummary
│   │   ├─► ET.parse() → root. Использует root.iter("testsuite") для атрибутов
│   │   │   (TRAP из tests/merge_junit.py:38 — атрибуты на <testsuite>, НЕ на <testsuites>)
│   │   ├─► Итерация по <testsuite> → агрегация counts
│   │   ├─► Итерация по <testcase> → если <failure>/<error> → в failed_tests
│   │   └─► Для каждого failed: имя теста, message, text (traceback) — НО не <system-out>/<system-err>
│   ├─► Печать format_summary(TestSummary) → stdout
│   ├─► shutil.rmtree(tmpdir) — очистка в finally-блоке
│   └─► sys.exit(pytest_result.returncode)
│
└─► stdout: компактный вывод (<100 строк)
    └─► exit code = 0 (all pass) | 1 (failures) | 2 (error/interrupt)
```

---

## 5. Acceptance Criteria (детально)

### AC1: Compact output for static_audit
```bash
$ make test-summary MARKER=static_audit
=== TEST SUMMARY (marker=static_audit, 2.3s) ===
PASS:  203
FAIL:  12
SKIP:  0
ERROR: 1
TOTAL: 216

--- FAILED TESTS ---
FAIL tests/test_audit_step.py::test_audit_step_exists
     AssertionError: audit_logging.sh not found in provision-environment.sh

FAIL tests/test_scaffold_env_platform.py::test_gen_env_platform_exists
     FileNotFoundError: gen-env-platform.sh does not exist

--- ERRORS ---
ERROR tests/test_platform_config.py::test_config_load
       IsADirectoryError: [Errno 21] Is a directory: '/tmp/pytest-xxx'
```
**Проверка:** `wc -l` < 100 строк.

### AC2: All markers supported
```bash
make test-summary MARKER=static_audit   # ✅
make test-summary MARKER=contract       # ✅
make test-summary MARKER=smoke          # ✅ (Docker needed)
make test-summary MARKER=component      # ✅ (Docker needed)
make test-summary MARKER=predeploy      # ✅ (Docker needed)
make test-summary MARKER=all            # ✅ (full suite)
```

### AC3: Output never exceeds 2000 lines
Даже при 112 failures, вывод остаётся < 200 строк (по 2-3 строки на failure: имя + traceback first line).

### AC4: Cleanup
JUnit XML временный файл удаляется после парсинга (в finally-блоке).

### AC5: Default marker
`make test-summary` без MARKER → static_audit (безопасный default, не требует Docker).

### AC6: Exit code passthrough
```bash
make test-summary MARKER=static_audit; echo $?
# 0 = all pass
# 1 = failures
# 2 = error/interrupt
```

---

## 6. Marker Mapping (test_runner.py → pytest expression)

⚠️ TRAP[DESIGN] · 2026-07-31 · MED · Дублирование MARKER_MAP с ci.mk — сознательный компромисс
· Проблема: mapping marker→pytest expression существует в двух местах (ci.mk строки 24-105
  и test_runner.py MARKER_MAP). Это нарушение DRY.
· Альтернатива A (полное исключение): test_runner.py парсит ci.mk Makefile-синтаксис для
  извлечения mapping → хрупко, Makefile shell-условия не парсятся надёжно.
· Альтернатива B (вынести mapping в YAML SoT, оба потребляют): правильно, но выходит за
  рамки Уровня A. Зарегистрировать как debt для Уровня B/C.
· Выбрано: B (YAML SoT) — но в Уровне A допустима временная копия с явным debt-маркером.
· Rev: при добавлении 4-го маркера или первом расхождении — рефакторинг в YAML SoT.

Mapping (включает `static` — отсутствовал в первоначальном draft, см. AC7):

```python
MARKER_MAP = {
    # static: validate.sh + lint + pytest (ПОЛНАЯ эквивалентность ci.mk строки 26-35)
    "static": _run_static_full,  # special handler: validate.sh → lint → pytest
    "static_audit": [
        "-m", "static_audit or (not e2e and not component and not smoke "
              "and not integration and not local_auth and not requires_docker)",
    ],
    "smoke":        ["-m", "smoke", "-rs"],
    "component":    ["-m", "component", "-rs"],
    "integration":  ["-m", "integration", "-rs"],
    "predeploy":    ["-m", "predeploy", "-rs"],
    "contract":     ["-m", "contract"],
    "e2e":          ["-m", "e2e", "-rs"],
    "local_auth":   ["-m", "local_auth"],
}
```

Для `MARKER=all`: запускает каждый suite последовательно (как в ci.mk строках 72-101),
агрегирует результаты через reuses `tests/merge_junit.py` (НЕ новая логика агрегации).

---

## 7. Implementation Plan

### Wave 0: Superposition (опционально, если Brief.md ещё не создан)
0. Создать `Brief.md` с §Superposition: 5 options (A: Python wrapper, B: pytest plugin,
   C: shell-only jq-parser, D: pytest.ini hook, E: external tool) × decision matrix.
   Документировать почему выбран Уровень A.

### Wave 1: Core wrapper (F1)
1. Создать `core/internal/test_runner.py`
   - Dataclass `TestSummary` (pass/fail/skip/error counts + failed list)
   - `parse_junit_xml(path) → TestSummary` — использует `root.iter("testsuite")`
     (TRAP из tests/merge_junit.py:38, НЕ `root.get()`)
   - `format_summary(TestSummary, marker, duration) → str`
   - `_build_pytest_args(marker) → list[str]` — включает `static` special handler
   - `_run_static_full()` — validate.sh + lint + pytest (AC7)
   - `main()` — argparse, subprocess (env с PYTEST_NO_ESCALATION=1, timeout=1800s),
     parse, print, cleanup в finally
2. LDD markup: GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT, [IMP:7-9] логи
3. Ручной тест: `PYTEST_NO_ESCALATION=1 python -m core.internal.test_runner --marker static_audit`

### Wave 2: Make target (F2)
4. Добавить `test-summary` target в `makefiles/ci.mk`
5. Добавить `test-summary` в `.PHONY` строку 12 (ОБЯЗАТЕЛЬНО — иначе G3 не подхватит)
6. Флаг `PYTEST_NO_ESCALATION=1` передаётся через target или внутри test_runner.py

### Wave 3: Manifest regeneration (F3) — НЕ ручная правка!
7. `make generate-manifests` — генератор G3 извлечёт `test-summary` из .PHONY и добавит
   в `allowed_verbs` автоматически (Invariant 11: Manifest Generation Contract)
8. `make check-manifests` — проверить byte-level идентичность
9. Gate `test_gate_generate_entrypoint_manifest_no_self_read.py` должен остаться зелёным

### Wave 4: Unit tests (F4)
10. Создать `tests/unit/test_test_runner.py`:
    - `test_parse_junit_xml_pass` — fixture XML с 3 pass testcase
    - `test_parse_junit_xml_failure` — fixture с <failure>, проверка failed_tests list
    - `test_parse_junit_xml_error` — fixture с <error>
    - `test_parse_junit_xml_testsuites_wrapper` — TRAP regression (атрибуты на <testsuite>)
    - `test_format_summary_compact` — вывод <100 строк при 50 failures
    - `test_build_pytest_args_static` — AC7: static → validate+lint+pytest
    - `test_build_pytest_args_unknown_marker` — exit 1 с понятным сообщением
11. Тесты используют tmp_path (Zero Hardcode Rule), caplog для LDD IMP:9 проверки

### Wave 5: Verification
12. `make test-summary MARKER=static_audit` — локальный прогон, `wc -l` < 100
13. `make test-summary MARKER=static` — AC7: validate+lint+pytest эквивалентность
14. `make gate MODE=fast` — проверить что не сломали существующие gates
15. `make check-manifests` — подтверждение regeneration корректна

---

## 8. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| JUnit XML не создаётся при ошибке pytest (например collection error) | MEDIUM | Fallback: печатаем stderr pytest'а + exit code. TimeoutExpired handler (exit 124). |
| Pytest memory consumption при 1000+ тестах | LOW | `--junitxml` не добавляет значительного overhead |
| Дублирование marker-логики с ci.mk | MEDIUM | TRAP[DESIGN] §6: временное решение, debt-маркер. При 4-м маркере → YAML SoT рефакторинг. |
| `make test-summary MARKER=all` слишком долгий | HIGH | `all` не рекомендуется для bash-tool. Для CI используем `make test MARKER=all`. |
| **B1: Ручная правка allowed_verbs** | ~~HIGH~~→ eliminated | AC9: только `make generate-manifests`. Gate `make check-manifests` блокирует divergence. |
| **B4: PYTEST_NO_ESCALATION утерян** | HIGH | AC10: subprocess env наследует + явно устанавливает `PYTEST_NO_ESCALATION=1`. Без этого anti-loop escalation в `_conftest/session.py:255` ломает прозрачность wrapper'а. |
| **B5: subprocess timeout=None → бесконечный hang** | HIGH | AC8: `--timeout 1800s` default. TimeoutExpired handler печатает понятное сообщение + exit 124. Docker-dependent тесты с зависшим healthcheck — известная проблема (`_conftest/smoke.py:614`). |
| **B2: MARKER_MAP drift от ci.mk** | MEDIUM | TRAP[DESIGN] §6. Документирован как debt. Unit-тест `test_build_pytest_args_static` (Wave 4) ловит расхождение `static`-обработки. |
| **M3: parse_junit_xml не учитывает уроки merge_junit.py** | LOW | Reuses TRAP[BUG] из `tests/merge_junit.py:38` — `root.iter("testsuite")` вместо `root.get()`. Unit-тест `test_parse_junit_xml_testsuites_wrapper` — regression guard. |

---

## 9. Non-Goals (для Уровня B/C)

- ❌ Preflight CWD-poisoning check
- ❌ Concurrent modification detection
- ❌ Test isolation contract enforcement
- ❌ `os.chdir()` без `contextlib.chdir` CI gate
- ❌ Ожидаемая длительность тестов в контрактах

---

## 10. Migration Path

- **Существующие target'ы НЕ удаляются:** `make test`, `make gate` продолжают работать
- **Новый target — дополнительный:** `make test-summary` для агентов
- **CI не меняется:** CI продолжает использовать `make test` / `make gate`
- **Агенты мигрируют:** при запуске тестов используют `make test-summary` вместо `python -m pytest ... -v`

---

## 11. Critical Review Audit Trail (2026-07-31)

DevPlan пересмотрен против фактического состояния кодовой базы. Найдены и исправлены 5 блокирующих
и 3 средних проблемы. Audit trail для следующих агентов:

### Блокирующие проблемы (исправлены)

**B1 — Нарушение Invariant 11 (Manifest Generation Contract).**
Оригинал Wave 3 шаг 6: "Добавить `test-summary` в `core/entrypoint-manifest.yaml` → allowed_verbs".
Факт: `allowed_verbs` — GENERATED-секция, порождаемая G3 `generate_entrypoint_manifest.py`
из Makefile `.PHONY` таргетов. Доказательства:
- Gate `test_gate_generate_entrypoint_manifest_no_self_read.py` проверяет, что G3 НЕ читает
  `allowed_verbs` из существующего манифеста, а заменяет их полностью из extraction.
- AGENTS.md root Invariant 11: "Generated files коммитятся, но НЕ редактируются вручную."
Исправление: AC9 + Wave 3 переписан — regeneration через `make generate-manifests`.

**B2 — Дублирование MARKER_MAP с ci.mk без антипаттерна-защиты.**
Оригинал: `MARKER_MAP` — молчаливая копия ci.mk строк 24-105. Risk R3 mitigation: "comment".
Факт: два source-of-truth для marker→pytest mapping неизбежно разойдутся. Уже сейчас в ci.mk
есть `static`-маркер (validate+lint+pytest), отсутствующий в оригинальном MARKER_MAP.
Исправление: TRAP[DESIGN] §6 с явным debt-маркером и rev-trigger (4-й маркер → YAML SoT).

**B3 — Утеря поведения `static`-маркера.**
Оригинал: MARKER_MAP (§6) не содержит `static`. AC5 ставит default `static_audit`.
Факт: ci.mk различает `static` (validate.sh + lint + pytest) и `static_audit` (только pytest).
Исправление: AC7 + `_run_static_full()` handler + unit-тест `test_build_pytest_args_static`.

**B4 — Игнорирование `PYTEST_NO_ESCALATION=1`.**
Оригинал: data flow §4 и `_build_pytest_args` не упоминают env var.
Факт: все pytest-вызовы в ci.mk обёрнуты `PYTEST_NO_ESCALATION=1` (anti-loop протокол,
`.kilo/rules/testing.md`). Без неё `_conftest/session.py:255` триггерит escalation logic.
Исправление: AC10 + subprocess env в data flow §4.

**B5 — `subprocess.run(timeout=None)` — unbounded hang.**
Оригинал §4: "timeout=None (нет таймаута — pytest сам завершится)".
Факт: Docker-dependent тесты с зависшим healthcheck — известная проблема
(`_conftest/smoke.py:614`). Wrapper решает bash-tool timeout, но создаёт новый: бесконечный hang.
Исправление: AC8 + `--timeout 1800s` default + TimeoutExpired handler (exit 124).

### Средние проблемы (исправлены)

**M1 — Несуществующий superposition-анализ как sole обоснование.**
Оригинал IMPLEMENTS: "Уровень A из superposition-анализа".
Факт: `.ai/plans/098-test-runner-wrapper/` содержит только DevPlan.md, без Brief.md/Superposition.
Исправление: Wave 0 (опционально) + примечание в §1.

**M2 — Отсутствие unit-тестов для test_runner.py.**
Оригинал: Wave 1-4 не содержит шага создания тестов. File Manifest без F4.
Факт: `tests/AGENTS.md` + `.kilo/rules/testing.md` требуют unit-тесты для нового Python-модуля.
Исправление: Wave 4 + F4 `tests/unit/test_test_runner.py` (7 тестов).

**M3 — parse_junit_xml не использует уроки tests/merge_junit.py.**
Оригинал §4: "ET.parse() → root <testsuites>" (подразумевает root.get()).
Факт: `tests/merge_junit.py:38` содержит TRAP[BUG] — атрибуты на `<testsuite>`, НЕ на `<testsuites>`.
Исправление: data flow §4 явно ссылается на TRAP + unit-тест regression guard.

### Подтверждённые факты (не требовали исправления)

- Python namespace packages: `core/` и `core/internal/` БЕЗ `__init__.py` — PEP 420 работает.
  `python -m core.internal.deploy.orchestrator_cli` уже используется в `deploy.mk:55,79`.
- pytest markers (pyproject.toml): `static_audit, smoke, component, predeploy, contract, e2e,
  gate, skip_enforcement, requires_docker, local_auth, backup, requires_fresh_state, wave, unit,
  benchmark` — все 15 валидны, `--strict-markers` не блокирует.
- `tests/merge_junit.py` существует и переиспользуется для `MARKER=all` агрегации (§6).

$END_DEVPLAN
