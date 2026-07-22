# DevPlan 048 — CI/CD Gap Closure: Unit Tests + Inline Python3 Fix

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть два пробела из StatusReport 046 §5 File Manifest: (1) отсутствуют 3 unit-теста для новых Python-модулей CICD-01 и (2) discover-modules/action.yml содержит inline python3, который будет заблокирован pre-commit hook'ом.
DESCRIPTION:           TASK-1: создать 3 unit-теста для module_discovery.py, validate_dora_dashboard.py, vps_status_check.py. TASK-2: добавить --count флаг в module_discovery.py, заменить inline в action.yml. Обе задачи независимы (Parallel Group 1). Объём: SMALL (~150 строк нового кода).
RATIONALE:             StatusReport 046 T2-T5 создал 3 Python-модуля для замены inline блоков в CI, но тесты к ним не были созданы (StatusReport §5.3). Без тестов модули беззащитны перед регрессиями. Дополнительно: сам CI-action discover-modules содержит inline `python3 -c "import json,sys..."` на строке 36, что иронично для модуля, созданного для устранения inline python3. Pre-commit hook (check-no-new-inline-python3.sh) расширен на .github/actions/*/action.yml и заблокирует этот файл при следующем коммите.
ACCEPTANCE_CRITERIA:
  AC-1: test_module_discovery.py → ≥4 тестов, покрывающих discover_docker_modules() + CLI main()
  AC-2: test_validate_dora_dashboard.py → ≥5 тестов, покрывающих validate() + CLI main()
  AC-3: test_vps_status_check.py → ≥5 тестов, покрывающих parse_status_json() + CLI main()
  AC-4: Все тесты используют @ldd_trajectory, содержат IMP:9 лог, проходят `python -m pytest tests/unit/<file> -v`
  AC-5: module_discovery.py получает флаг --count (печать только числа модулей), action.yml обновлён на `python3 core/internal/scripts/module_discovery.py --count`
  AC-6: check-no-new-inline-python3.sh НЕ блокирует обновлённый action.yml
  AC-7: `make test MARKER=static` проходит без новых failures
  AC-8: `make gate MODE=fast` проходит без регрессий
IMPLEMENTS:            StatusReport 046 §5.3 (File Manifest items 5, 6, 7) + §4 Finding F2 (action.yml inline python3)
IMPACTS:               tests/unit/test_module_discovery.py (NEW), tests/unit/test_validate_dora_dashboard.py (NEW), tests/unit/test_vps_status_check.py (NEW), core/internal/scripts/module_discovery.py (MODIFIED — добавить --count), .github/actions/discover-modules/action.yml (MODIFIED — заменить inline), .ai/plans/048-cicd-tests-inline-fix/ (NEW — настоящий артефакт)
REQUIRES:              Python ≥3.10, pytest, core/internal/scripts/{module_discovery,validate_dora_dashboard,vps_status_check}.py (уже существуют), tests/_conftest/ldd.py (shared decorator)
$END_ARTIFACT_CONTRACT

---

## $TASKS

### TASK-1: Unit-тесты для module_discovery.py
**Приоритет:** 🔴 HIGH
**Файл:** `tests/unit/test_module_discovery.py` (NEW)

#### Test Spec

| # | Тест | Что проверяет | Подход |
|---|------|---------------|--------|
| T1.1 | `test_discover_all_non_system_modules` | discover_docker_modules() возвращает только не-system модули с docker-compose.base.yml | tmp_path с module.yaml + docker-compose.base.yml |
| T1.2 | `test_exclude_system_modules` | Модули с `install_type: system` исключаются | tmp_path, module.yaml с marker'ом |
| T1.3 | `test_exclude_no_compose_file` | Модули без docker-compose.base.yml исключаются | tmp_path, только module.yaml |
| T1.4 | `test_empty_modules_dir` | Пустая директория → пустой список | tmp_path, пустая директория |
| T1.5 | `test_cli_json_output` | CLI --format json выдаёт валидный JSON массив | subprocess.run + assert JSON parse + len |
| T1.6 | `test_cli_lines_output` | CLI --format lines выдаёт по одному файлу на строку | subprocess.run + assert line count |

**Паттерн:** `sys.path.insert(0, ...)` + прямой импорт `discover_docker_modules` из `core/internal/scripts/module_discovery.py`. Для CLI — `subprocess.run([sys.executable, script, ...], capture_output=True)`.

**IMP:9 логи:**
- T1.1: `"[IMP:9][test] All non-system modules discovered — count=%d"`
- T1.2: `"[IMP:9][test] System modules correctly excluded"`
- T1.3: `"[IMP:9][test] No-compose modules correctly excluded"`
- T1.4: `"[IMP:9][test] Empty modules dir → empty result"`
- T1.5: `"[IMP:9][test] CLI JSON output valid"`
- T1.6: `"[IMP:9][test] CLI lines output valid"`

---

### TASK-2: Unit-тесты для validate_dora_dashboard.py
**Приоритет:** 🔴 HIGH
**Файл:** `tests/unit/test_validate_dora_dashboard.py` (NEW)

#### Test Spec

| # | Тест | Что проверяет | Подход |
|---|------|---------------|--------|
| T2.1 | `test_valid_dashboard_all_panels` | Валидный дашборд с uid + 4 панелями → True | tmp_path, валидный JSON |
| T2.2 | `test_missing_uid` | Неверный uid → False + IMP:10 diagnostic | tmp_path, JSON с uid: "wrong" |
| T2.3 | `test_missing_panel` | Отсутствие одной из 4 панелей → False | tmp_path, JSON с 3 панелями |
| T2.4 | `test_file_not_found` | Несуществующий файл → False | Path к несуществующему файлу |
| T2.5 | `test_invalid_json` | Битый JSON → False | tmp_path, не-JSON содержимое |
| T2.6 | `test_non_dict_root` | JSON root — не объект (массив) → False | tmp_path, JSON array |
| T2.7 | `test_cli_exit_code` | CLI exit 0 на валидном, exit 1 на невалидном | subprocess.run + assert returncode |

**Паттерн:** прямой импорт `validate` из `core/internal/scripts/validate_dora_dashboard.py`.

**IMP:9 логи:**
- T2.1–T2.7: уникальные IMP:9 сообщения с названием теста

---

### TASK-3: Unit-тесты для vps_status_check.py
**Приоритет:** 🔴 HIGH
**Файл:** `tests/unit/test_vps_status_check.py` (NEW)

#### Test Spec

| # | Тест | Что проверяет | Подход |
|---|------|---------------|--------|
| T3.1 | `test_parse_valid_status_found` | parse_status_json с `{"status": "found"}` → dict | прямой вызов функции |
| T3.2 | `test_parse_valid_status_stub` | parse_status_json с `{"status": "stub"}` → dict | прямой вызов функции |
| T3.3 | `test_parse_empty_stdin` | Пустая строка → EmptyStdinError | прямой вызов, assert raises |
| T3.4 | `test_parse_whitespace_stdin` | Строка только из пробелов → EmptyStdinError | прямой вызов, assert raises |
| T3.5 | `test_parse_malformed_json` | Не-JSON строка → json.JSONDecodeError | прямой вызов, assert raises |
| T3.6 | `test_cli_valid_status` | CLI с валидным stdin JSON → exit 0 | subprocess.run, stdin pipe |
| T3.7 | `test_cli_invalid_status` | CLI с невалидным status → exit 1 | subprocess.run, stdin pipe |
| T3.8 | `test_cli_empty_stdin` | CLI с пустым stdin → exit 3 | subprocess.run, stdin pipe |
| T3.9 | `test_cli_malformed_json` | CLI с битым JSON → exit 2 | subprocess.run, stdin pipe |
| T3.10 | `test_cli_output_status_only` | CLI --output-status-only → stdout содержит только статус, exit 0 | subprocess.run, stdin pipe |

**Паттерн:** прямой импорт `parse_status_json` из `core/internal/scripts/vps_status_check.py`. Для CLI — subprocess.run с stdin pipe.

**IMP:9 логи:** уникальные для каждого теста

---

### TASK-4: --count флаг + fix action.yml
**Приоритет:** 🟠 MED
**Файлы:** `core/internal/scripts/module_discovery.py` (MODIFIED), `.github/actions/discover-modules/action.yml` (MODIFIED)

#### Изменения

**module_discovery.py** — добавить в argparse:
```python
parser.add_argument(
    "--count",
    action="store_true",
    help="Print only the count of discovered modules (single integer). Incompatible with --format.",
)
```
В `main()`: если `args.count` — напечатать `len(modules)` и exit 0.

**action.yml** строка 36 — заменить:
```yaml
# Было:
COUNT=$(python3 core/internal/scripts/module_discovery.py --format json | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
# Стало:
COUNT=$(python3 core/internal/scripts/module_discovery.py --count)
```

**Валидация:** запустить check-no-new-inline-python3.sh до и после — убедиться, что после изменений hook не блокирует.

---

## $PARALLEL_GROUPS

```
Group 1 (независимые, нет shared files):
  ├── TASK-1: test_module_discovery.py     (NEW файл)
  ├── TASK-2: test_validate_dora_dashboard.py (NEW файл)
  ├── TASK-3: test_vps_status_check.py     (NEW файл)
  └── TASK-4: module_discovery.py + action.yml (MODIFY существующие, НЕ конфликтует с TASK-1)
```

Все 4 задачи могут выполняться параллельно — нет общих файлов между создаваемыми тестами и модифицируемыми модулями.

TASK-1 и TASK-4 затрагивают `module_discovery.py` (TASK-1 — тестирует существующий API, TASK-4 — добавляет `--count`), но тест TASK-1 не зависит от наличия `--count` флага. Порядок: TASK-4 (добавить флаг) → TASK-1 (тест может опционально проверить и `--count`). Однако для полной параллельности можно сделать TASK-1 без проверки `--count` и добавить проверку `--count` в TASK-4.

## $TEST_SPEC

### Pre-flight
```bash
# Проверить, что check-no-new-inline-python3.sh блокирует action.yml до фикса
python3 core/internal/hooks/check-no-new-inline-python3.sh  # ожидается FAIL из-за action.yml inline
```

### Post-implementation
```bash
# 1. Все новые тесты проходят
python -m pytest tests/unit/test_module_discovery.py -v
python -m pytest tests/unit/test_validate_dora_dashboard.py -v
python -m pytest tests/unit/test_vps_status_check.py -v

# 2. --count флаг работает
python3 core/internal/scripts/module_discovery.py --count  # ожидается целое число

# 3. Pre-commit hook больше не блокирует action.yml
git add .github/actions/discover-modules/action.yml
python3 core/internal/hooks/check-no-new-inline-python3.sh  # exit 0

# 4. Full gate
make gate MODE=fast

# 5. Static tests
make test MARKER=static
```

---

## Draft Code Graph (XML)

```xml
<CodeGraph task="048-cicd-tests-inline-fix">
  <Entity id="module_discovery_py" TYPE="Module" keywords="discover,docker,modules,CI,zero-deps">
    ├── <Function id="discover_docker_modules" TYPE="API" returns="list[Path]" />
    ├── <Function id="main" TYPE="CLI" returns="int" />
    └── <Add id="add_count_flag" TYPE="Feature" description="--count flag in argparse" />
  </Entity>
  <Entity id="validate_dora_dashboard_py" TYPE="Module" keywords="grafana,DORA,validation,JSON">
    ├── <Function id="validate" TYPE="API" returns="bool" />
    └── <Function id="main" TYPE="CLI" returns="int" />
  </Entity>
  <Entity id="vps_status_check_py" TYPE="Module" keywords="vps,project-status,stdin,JSON">
    ├── <Function id="parse_status_json" TYPE="API" returns="dict" />
    ├── <Class id="EmptyStdinError" TYPE="Exception" />
    └── <Function id="main" TYPE="CLI" returns="int" />
  </Entity>
  <Entity id="test_module_discovery_py" TYPE="TestModule" keywords="unit,discovery,tmp_path,CLI">
    ├── <Test id="T1.1" tests="discover_docker_modules-full" />
    ├── <Test id="T1.2" tests="discover_docker_modules-exclude-system" />
    ├── <Test id="T1.3" tests="discover_docker_modules-exclude-no-compose" />
    ├── <Test id="T1.4" tests="discover_docker_modules-empty" />
    ├── <Test id="T1.5" tests="cli-json" />
    └── <Test id="T1.6" tests="cli-lines" />
  </Entity>
  <Entity id="test_validate_dora_dashboard_py" TYPE="TestModule" keywords="unit,DORA,tmp_path,CLI">
    ├── <Test id="T2.1" tests="validate-valid" />
    ├── <Test id="T2.2" tests="validate-missing-uid" />
    ├── <Test id="T2.3" tests="validate-missing-panel" />
    ├── <Test id="T2.4" tests="validate-file-not-found" />
    ├── <Test id="T2.5" tests="validate-invalid-json" />
    ├── <Test id="T2.6" tests="validate-non-dict-root" />
    └── <Test id="T2.7" tests="cli-exit-code" />
  </Entity>
  <Entity id="test_vps_status_check_py" TYPE="TestModule" keywords="unit,vps,stdin,CLI,exceptions">
    ├── <Test id="T3.1" tests="parse-valid-found" />
    ├── <Test id="T3.2" tests="parse-valid-stub" />
    ├── <Test id="T3.3" tests="parse-empty-stdin" />
    ├── <Test id="T3.4" tests="parse-whitespace-stdin" />
    ├── <Test id="T3.5" tests="parse-malformed-json" />
    ├── <Test id="T3.6" tests="cli-valid-status" />
    ├── <Test id="T3.7" tests="cli-invalid-status" />
    ├── <Test id="T3.8" tests="cli-empty-stdin" />
    ├── <Test id="T3.9" tests="cli-malformed-json" />
    └── <Test id="T3.10" tests="cli-output-status-only" />
  </Entity>
  <Entity id="action_yml" TYPE="CI" keywords="discover-modules,composite,inline-fix">
    └── <Change id="replace_inline" description="python3 -c → python3 --count" />
  </Entity>
  <CrossLinks>
    <Link from="T1.1..T1.6" to="module_discovery_py" relation="TESTS" />
    <Link from="T2.1..T2.7" to="validate_dora_dashboard_py" relation="TESTS" />
    <Link from="T3.1..T3.10" to="vps_status_check_py" relation="TESTS" />
    <Link from="add_count_flag" to="action_yml" relation="ENABLES" />
  </CrossLinks>
</CodeGraph>
```

---

## Step-by-step Data Flow

```
Stage 0: INTAKE
  StatusReport 046 §5.3 + §4 F2 → 2 проблемы (тесты + inline)

Stage 1: ARCHITECT (design)
  ├── Классификация: SMALL (~150 LOC, 4 файла, нет зависимостей)
  └── → DevPlan 048 (настоящий документ)

Stage 2: CODER (реализация)
  Wave 1 (Parallel Group 1 — 4 задачи):
    ├── Coder-A: TASK-1 (test_module_discovery.py)
    ├── Coder-B: TASK-2 (test_validate_dora_dashboard.py)
    ├── Coder-C: TASK-3 (test_vps_status_check.py)
    └── Coder-D: TASK-4 (--count + action.yml fix)

Stage 3: QA (верификация)
  ├── pytest tests/unit/test_module_discovery.py -v
  ├── pytest tests/unit/test_validate_dora_dashboard.py -v
  ├── pytest tests/unit/test_vps_status_check.py -v
  ├── make gate MODE=fast
  └── VerificationReport → verdict

Stage 4: FIX (если нужно)
  └── max 3 цикла

Stage 5: REPORT
  ├── DevPlan 048 → .ai/plans/048-cicd-tests-inline-fix/DevPlan.md
  ├── VerificationReport → .ai/plans/048-cicd-tests-inline-fix/01-VerificationReport.md
  └── Final verdict + test summary
```

---

## $DOCUMENT_PLAN

| Section | Goal ID | Purpose |
|---------|---------|---------|
| $ARTIFACT_CONTRACT | GOAL-0 | Контрактная самодокументация |
| $TASKS | GOAL-1 | Декомпозиция на 4 независимые задачи |
| $PARALLEL_GROUPS | GOAL-2 | Определение параллельных групп Coder'ов |
| $TEST_SPEC | GOAL-3 | Спецификация pre/post команд для верификации |
| Draft Code Graph | GOAL-4 | Структурные якоря для Coder'а |
| Data Flow | GOAL-5 | Симуляция процесса для оркестратора |

$END_DEVPLAN
