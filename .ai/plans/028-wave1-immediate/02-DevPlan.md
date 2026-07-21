# 028-DevPlan: Wave 1 (Immediate) — Bugs, Tests, Policy Fixation, Inline python3 Consolidation

**Program:** 027-architecture-modernization-program
**Wave:** 1 of 5 (Immediate — нулевой риск)
**Source Brief:** `.ai/plans/027-architecture-modernization-program/01-Brief.md` §3 (Wave 1 эпики W1-E1…W1-E8)
**Verified against codebase:** 2026-07-21 (skip=84 occurrences, R4-pattern ≈18-25, inline `python3 -c`=105, `usage()`=12 файлов, `_load_yaml` дубли=6 файлов, `_negative` пар в target-тестах=0)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Реализовать Wave 1 (Immediate) программы архитектурной модернизации 027: закрыть trust-киллер (false-green через skip→fail), зафиксировать языковую политику в root AGENTS.md с двухуровневым Strangler-триггером, дать ощутимый профит в первой волне (−400 строк бойлерплейта через gate_helpers.py + lib/args.sh, консолидация inline python3 в yaml_query.py, baseline-метрики для следующих волн). Нулевой production-риск: все изменения — либо документация, либо тесты, либо новые файлы (не затрагивают production-пути).
DESCRIPTION:           8 эпиков (W1-E1…W1-E8), сгруппированных в 4 подволны по порядку выполнения: (A) Policy fixation — AGENTS.md «Языковая политика» с двухуровневым триггером; (B) Inline python3 consolidation — yaml_query.py + обновление yaml_read.sh + pre-commit hook + map 105 inline-вызовов; (C) Honesty-first — require_docker_or_fail() в tests/_conftest/honesty.py + замена skip→fail + 3 `_negative` пары для gate-тестов; (D) Boilerplate removal — gate_helpers.py (load_yaml, repo_root, assert_ldd_imp9) + lib/args.sh (parse_args + usage) + verify-domains log_imp dedup. Wave завершается замером baseline-метрик (W1-E8) для следующих волн. Применяется поэтапный переход skip→fail (R-RISK-2): marker → xfail(strict=False) → fail — для избежания временно красного CI на окружениях без Docker.
RATIONALE:             Анализ архитектуры (arch-forensics skill, `reports/architecture-analysis-2026-07-21.md`), верифицированный против кодовой базы 2026-07-21, показал: 105 inline `python3 -c` вызовов в 15+ файлах (не единичный heredoc — реальный масштаб проблемы в 5× больше первоначальной оценки); ~84 `pytest.skip()` в тестах, из которых ~18-25 — R4-нарушения (skip вместо fail при отсутствии сервиса); 0 `_negative` пар для 3 gate-тестов (test_gate_litellm_pg_enforcement, test_gate_module_schema_d4, test_gate_env_shared_consistency) → R5-нарушения; 6 копий `_load_yaml`, 57 объявлений `PROJECT_ROOT`, 12 файлов с собственным `usage()`. Оператор выбрал (бриф 027 §1.2, §3.3): (а) Honesty-First в первой волне — trust-киллер должен быть закрыт до любых архитектурных изменений; (б) enforcement через AGENTS.md (без CI gate на .sh) — опирается на code review; (в) inline python3 консолидация вместо heredoc extraction — реальный масштаб проблемы в 5× больше; (г) двухуровневый Strangler-триггер — баг-фикс не блокируется необходимостью переписывать весь скрипт.
ACCEPTANCE_CRITERIA:
  **A. AGENTS.md Policy Fixation (W1-E1):**
    1. Root `AGENTS.md` содержит раздел «Языковая политика» после §Глоссарий глаголов, перед §Правило. Текст соответствует брифу 027 §1.1 (главное правило + 5 принципов применения + двухуровневый Strangler-триггер).
    2. `core/AGENTS.md` содержит ссылку на раздел «Языковая политика» root AGENTS.md (one-line pointer).
    3. `rg "## Языковая политика" AGENTS.md` → 1 match.
  **B. Inline python3 Consolidation (W1-E7):**
    4. `core/internal/scripts/yaml_query.py` создан с typed Python API: `yaml_get(path, key, default=None)`, `yaml_query(path, jq_like_filter)`, `json_get(path, key)`. Unit-тесты в `tests/test_yaml_query.py` покрывают все 3 функции + edge-cases (missing file, missing key, malformed YAML).
    5. `core/lib/yaml_read.sh` переведён на вызов `python3 core/internal/scripts/yaml_query.py` вместо inline `python3 -c "import yaml,sys; ..."`. Локальные inline-блоки в yaml_read.sh удалены.
    6. Файл `reports/inline-python3-map-2026-07-21.csv` создан: 105 inline-вызовов с колонками `file, line, snippet, consolidation_wave` (W1 / W4 / W5).
    7. Pre-commit hook `no-new-inline-python3` в `.pre-commit-config.yaml`: блокирует добавление новых `python3 -c` / `<<PYEOF` в файлах под `core/` (кроме whitelist: `core/lib/yaml_read.sh`, `core/internal/scripts/`).
    8. `rg "python3 -c" core/ | wc -l` → ≤105 (не увеличилось; цель Wave 1 — консолидация yaml_read.sh + блокировка новых, не удаление всех 105).
  **C. Honesty-First (W1-E2, W1-E3):**
    9. `tests/_conftest/honesty.py` создан с `require_docker_or_fail(reason="...")` — вызывает `pytest.fail()` при отсутствии Docker (не skip). Поэтапный переход: `REQUIRE_HONESTY_MODE=marker|xfail|fail` env var (default: `marker` в Wave 1, `fail` — после стабилизации в Wave 2).
    10. Все ~18-25 R4-нарушений (skip с reason "Docker/script/env not available") заменены на `require_docker_or_fail()` (с учётом режима). `rg 'pytest\.skip\(.*not available' tests/` → 0 matches в режиме `fail`.
    11. Три новых файла `test_*_negative.py` для gate-тестов без `_negative` пар:
        - `tests/gates/test_gate_litellm_pg_enforcement_negative.py` — детектирует нарушение (SQLite вместо PG в LiteLLM config).
        - `tests/gates/test_gate_module_schema_d4_negative.py` — детектирует module.yaml без required D4 полей.
        - `tests/gates/test_gate_env_shared_consistency_negative.py` — детектирует рассинхрон env-переменных между модулями.
    12. Каждый `_negative`-тест действительно падает на конструируемом нарушении (Test Honesty R1: не `assert True`).
  **D. Boilerplate Removal (W1-E4, W1-E5, W1-E6):**
    13. `tests/helpers/gate_helpers.py` создан с `load_yaml(path)`, `repo_root()`, `module_yaml_paths()`, `assert_ldd_imp9(caplog, min_count=1)`. Файл `tests/helpers/__init__.py` создан.
    14. 6 файлов с локальным `_load_yaml` (test_redis_static.py, test_gate_workflow_consistency.py, test_gate_password_charset.py, test_gate_ci_env_vars.py, test_gate_gitleaks_version.py, test_gate_secrets_manifest.py) рефакторены: импорт `from tests.helpers.gate_helpers import load_yaml`. `rg "def _load_yaml" tests/` → 0 определений вне gate_helpers.py.
    15. Минимум 10 gate-тестов рефакторены на `repo_root()` из gate_helpers.py вместо локального `PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent`. Целевое уменьшение `rg "PROJECT_ROOT\s*=" tests/ | wc -l` с 57 → ≤30 (W1 scope; полное устранение — Wave 4 при декомпозиции).
    16. `core/lib/args.sh` создан: `parse_args()` (стандартизированная обработка `--help`, `--context`, `--mode`, `--dry-run` + custom specs), `usage()` (template-helper для единообразных usage-сообщений). Unit-покрытие через `tests/test_lib_args.bats` или эквивалент.
    17. Минимум 6 из 12 файлов с собственным `usage()` рефакторены на `source lib/args.sh` (приоритет: entrypoints/bootstrap.sh, entrypoints/node-update.sh, entrypoints/converge.sh, internal/scaffold/adopt-project.sh, internal/scaffold/add-project.sh, internal/scaffold/remove-project.sh). `rg "^usage\(\)" core/` → ≤6 определений (down from 12).
    18. `core/internal/verify/verify-domains.sh` использует `source lib/logging.sh` вместо локального `log_imp()`. Локальная функция `log_imp()` (строка 34) удалена. `rg "log_imp\(\)" core/internal/verify/verify-domains.sh` → 0 matches (definition + calls).
  **E. Baseline Measurement (W1-E8):**
    19. Файл `reports/baseline-metrics-2026-07.csv` создан с метриками ДО старта Wave 2: `make gate MODE=fast` time (3 повтора), `make gate MODE=full` time (3 повтора), SSH-вызовов без timeout (точный count из `rg "ssh" core/ | rg -v "ConnectTimeout|timeout"`), CI execution time per workflow (из GitHub Actions API или last 10 runs), inline python3 count (=105 или актуальный), shell LOC (cloc core/), Python LOC (cloc core/internal/scripts/ + core/modules/*/scripts/*.py).
  **Cross-cutting:**
    20. `make gate MODE=fast` — зелёный после всех изменений.
    21. `make gate MODE=full` — зелёный за исключением известных macOS-overlay failures (status-page-test, вынесен в отдельный трекинг).
    22. Все новые Python-файлы (yaml_query.py, gate_helpers.py, honesty.py, 3 _negative теста) проходят `ruff check` + `ruff format --check` без ошибок.
    23. Все новые gate-тесты зарегистрированы в `core/entrypoint-manifest.yaml` (секция gates).
    24. TRAP[DECISION] в root AGENTS.md (после раздела «Языковая политика») фиксирует выбор enforcement-механизма (AGENTS.md + pre-commit hook вместо CI gate на .sh).
IMPLEMENTS:            Brief 027 §3 (Wave 1 эпики W1-E1…W1-E8), §1.1 (текст «Языковая политика»), §1.2 (обоснование). AGENTS.md invariants 4 (канонические AGENTS.md), 8 (AI-First Architecture). Principles 6 (Small Simple Blocks через Strangler), 8 (модульные границы), 9 (Read before Act — отчёт прочитан, problem matrix верифицирован против кодовой базы 2026-07-21). Test Honesty Rules R1 (NO pass-tests), R4 (NO_SERVICE = FAIL), R5 (ANTI-SURVIVORSHIP). Skills: doc-protocols (этот DevPlan), arch-forensics (исходный анализ).
IMPACTS:               **AGENTS.md** (root) — НОВЫЙ раздел «Языковая политика» (~40 строк) + TRAP[DECISION] о enforcement-механизме. **core/AGENTS.md** — one-line pointer на языковую политику. **New Python:** `core/internal/scripts/yaml_query.py` (typed YAML/JSON API), `tests/helpers/gate_helpers.py` (load_yaml, repo_root, assert_ldd_imp9), `tests/helpers/__init__.py`, `tests/_conftest/honesty.py` (require_docker_or_fail), `tests/test_yaml_query.py` (unit-тесты), `tests/gates/test_gate_litellm_pg_enforcement_negative.py`, `tests/gates/test_gate_module_schema_d4_negative.py`, `tests/gates/test_gate_env_shared_consistency_negative.py` (3 R5-пары). **New lib:** `core/lib/args.sh` (parse_args + usage). **Modified tests:** ~18-25 файлов с skip→require_docker_or_fail, 6 файлов с `_load_yaml` dedup, 10+ файлов с `PROJECT_ROOT` dedup. **Modified shell:** `core/lib/yaml_read.sh` (перевод на yaml_query.py), `core/internal/verify/verify-domains.sh` (удаление локального log_imp), 6 entrypoints/scaffold (рефактор usage→lib/args.sh). **CI/config:** `.pre-commit-config.yaml` (+hook no-new-inline-python3), `core/entrypoint-manifest.yaml` (+3 gate-теста, +yaml_query.py если требуется регистрация). **Reports:** `reports/inline-python3-map-2026-07-21.csv` (105 вызовов), `reports/baseline-metrics-2026-07.csv` (baseline для Wave 2+).
REQUIRES:              Чистый working tree. Python 3.10+ (match/case допустимы). Зависимости: `pyyaml`, `jsonschema` (уже в deps). Bash >= 4.0 (для lib/args.sh с `declare -A`). Перед стартом: архитектор ОБЯЗАН прочитать `reports/architecture-analysis-2026-07-21.md` и Brief 027 §3. Поэтапный skip→fail переход (R-RISK-2): Wave 1 запускается в режиме `REQUIRE_HONESTY_MODE=marker` (мягкий), переключение на `fail` — после стабилизации в Wave 2 (оператор подтверждает). Wave 2 не стартует до завершения Wave 1 + production-релиза.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать подволны и порядок выполнения (A→B→C→D→E) => GOAL_ORDER
- GOAL Описать Draft Code Graph для новых Python-модулей и shell-lib => GOAL_GRAPH
- GOAL Описать step-by-step data flow для honesty-mode перехода => GOAL_HONESTY
- GOAL Описать inline python3 consolidation map (105 вызовов → W1/W4/W5) => GOAL_INLINE
- GOAL Зафиксировать File Manifest (все CREATE/MODIFY) => GOAL_MANIFEST
- GOAL Определить Acceptance Criteria + verifiable commands => GOAL_AC
- GOAL Зафиксировать risks (R-RISK-2, PGM-R1, R-RISK-9) и mitigation => GOAL_RISK
- GOAL Оценить effort по подволны => GOAL_EFFORT
**SECTION_USE_CASES:**
- USE_CASE Разработчик добавляет новый python3 -c → pre-commit блокирует, подсказывает yaml_query.py => UC_INLINE_BLOCK
- USE_CASE Тест запускается на CI без Docker → require_docker_or_fail → pytest.fail (режим fail) или marker-skip (режим marker) => UC_HONESTY_MODE
- USE_CASE Gate-тест_litellm_pg_enforcement детектит SQLite → PASSED; _negative-компаньон детектит нарушение → FAIL (конструирует SQLite config) => UC_NEGATIVE_PAIR
- USE_CASE Архитектор Wave 4 открывает inline-python3-map → видит, какие вызовы консолидировать в декомпозиции => UC_MAP_TRACKING
- USE_CASE Разработчик пишет новый entrypoint → source lib/args.sh → получает стандартизированный parse_args + usage => UC_ARGS_LIB
$END_DOCUMENT_PLAN
```

---

## Draft Code Graph (XML)

```xml
<graph>
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- A. POLICY FIXATION                                                   -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="agents_md_language_policy" type="DOC_SECTION" layer="AGENTS.md">
    <position>after §Глоссарий глаголов, before §Правило</position>
    <contains>
      Главное правило (Python-only new code, bash = thin wrapper),
      5 принципов применения,
      Двухуровневый Strangler-триггер (Tier 1 immediate, Tier 2 planned)
    </contains>
    <references>reports/architecture-analysis-2026-07-21.md §4.1 Option B</references>
  </entity>

  <entity id="trap_decision_enforcement" type="TRAP" layer="AGENTS.md">
    <annotation>⚠️ TRAP[DECISION] · 2026-07-21 · HI · Enforcement через AGENTS.md + pre-commit hook, не CI gate</annotation>
    <rejected>CI gate на создание .sh файлов (риск: блокирует легитимные lib-правки)</rejected>
    <reason>Code review + AGENTS.md + pre-commit hook на новые inline python3 = достаточная защита при текущем масштабе</reason>
    <rev>если через квартал нарушений >3 → поднять вопрос о CI gate (Whitelist .sh)</rev>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- B. INLINE PYTHON3 CONSOLIDATION                                      -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="yaml_query_py" type="PYTHON_MODULE" layer="core/internal/scripts/yaml_query.py">
    <purpose>Typed Python API для YAML/JSON-запросов — замена 40+ inline python3 -c однострочников</purpose>
    <public_api>
      yaml_get(path: Path, key: str, default: Any = None) -> Any
      yaml_query(path: Path, jq_filter: str) -> Any  # упрощённый jq-подобный синтаксис
      json_get(path: Path, key: str, default: Any = None) -> Any
    </public_api>
    <deps>pyyaml, json (stdlib), pathlib (stdlib), typing (stdlib)</deps>
    <cli>`python3 yaml_query.py --file <path> --get <key> [--default <val>]`</cli>
    <exit_codes>0=success, 1=key not found (no default), 2=file not found, 3=malformed YAML</exit_codes>
  </entity>

  <entity id="yaml_read_sh_migration" type="SHELL_LIB" layer="core/lib/yaml_read.sh">
    <before>6 inline `python3 -c "import yaml,sys; print(json.load(sys.stdin)...)"` блоков</before>
    <after>делегирует к `python3 "${CORE_DIR}/internal/scripts/yaml_query.py" --file ... --get ...`</after>
    <keeps>тот же public API (yaml_get, yaml_list, yaml_has_key) — backward compatible</keeps>
  </entity>

  <entity id="inline_python3_map_csv" type="REPORT" layer="reports/inline-python3-map-2026-07-21.csv">
    <columns>file, line, snippet, call_type (python3 -c | heredoc), consolidation_wave (W1|W4|W5)</columns>
    <rows>105 (верифицировано rg 'python3 -c' core/ 2026-07-21)</rows>
    <generated_by>scripts/collect-inline-python3.sh (одноразовый скрипт, не регистрируется в manifest)</generated_by>
  </entity>

  <entity id="precommit_no_new_inline_python3" type="HOOK" layer=".pre-commit-config.yaml">
    <id>no-new-inline-python3</id>
    <entry>bash core/internal/hooks/check-no-new-inline-python3.sh</entry>
    <files>^core/.*\.sh$</files>
    <whitelist>
      core/lib/yaml_read.sh (facade),
      core/internal/scripts/*.py (pure Python),
      core/internal/hooks/*.sh (self)
    </whitelist>
    <blocks>`python3 -c`, `python3 - <<PYEOF`, `python3 <<EOF`</blocks>
  </entity>

  <entity id="check_no_new_inline_python3_sh" type="INTERNAL_SCRIPT" layer="core/internal/hooks/check-no-new-inline-python3.sh">
    <logic>
      1. git diff --cached --name-only → filter .sh under core/
      2. for each file: git diff --cached → grep +python3 -c, +python3 - <<, +python3 <<EOF
      3. если found и file not in whitelist → exit 1 с diagnostic
    </logic>
    <exit>0=clean | 1=violation</exit>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- C. HONESTY-FIRST                                                     -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="honesty_py" type="CONFTEST_MODULE" layer="tests/_conftest/honesty.py">
    <purpose>Test Honesty enforcement: NO_SERVICE = FAIL, not skip (R4)</purpose>
    <public_api>
      require_docker_or_fail(reason: str = "Docker daemon required") -> None
      require_script_or_fail(script_path: Path, reason: str = "...") -> None
      require_env_or_fail(var: str, reason: str = "...") -> None
    </public_api>
    <mode_logic>
      REQUIRE_HONESTY_MODE env var:
        "marker" (default W1) → pytest.mark.skip(reason)  [мягкий переход]
        "xfail"               → pytest.xfail(reason, strict=False)
        "fail" (target W2)    → pytest.fail(reason)
    </mode_logic>
    <logs>[IMP:10][honesty] mode=<mode>, service=<missing>, action=<skip|xfail|fail></logs>
  </entity>

  <entity id="r4_skip_to_fail_migration" type="REFACTOR" layer="tests/ (18-25 файлов)">
    <pattern_before>pytest.skip("Docker daemon not available")</pattern_before>
    <pattern_after>from _conftest.honesty import require_docker_or_fail; require_docker_or_fail()</pattern_after>
    <scope>
      test_smoke_platform.py, test_smoke_postgres.py, test_component_clickhouse.py,
      test_component_hermes.py, test_component_pgbouncer.py, test_local_auth.py,
      test_integration_hermes_llm.py, test_hermes_init.py, test_hermes_l2_fallback.py,
      tests/_conftest/smoke.py, tests/_conftest/e2e.py, + другие по grep
    </scope>
  </entity>

  <entity id="negative_pair_litellm_pg" type="GATE_TEST" layer="tests/gates/test_gate_litellm_pg_enforcement_negative.py">
    <purpose>R5 anti-survivorship: если positive-тест детектит PG-enforcement,
             negative-тест детектирует отсутствие enforcement (SQLite config)</purpose>
    <constructs>temp LiteLLM config с sqlite:// URL (in tmp_path)</constructs>
    <asserts>gate function raises AssertionError / returns violations list non-empty</asserts>
    <marker>@pytest.mark.gate</marker>
  </entity>

  <entity id="negative_pair_module_schema_d4" type="GATE_TEST" layer="tests/gates/test_gate_module_schema_d4_negative.py">
    <constructs>temp module.yaml без required D4 полей (env_requires, restart)</constructs>
    <asserts>validation function reports missing fields</asserts>
  </entity>

  <entity id="negative_pair_env_shared_consistency" type="GATE_TEST" layer="tests/gates/test_gate_env_shared_consistency_negative.py">
    <constructs>two module.yaml with divergent env_shared declarations</constructs>
    <asserts>consistency check detects divergence</asserts>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- D. BOILERPLATE REMOVAL                                               -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="gate_helpers_py" type="TEST_HELPER" layer="tests/helpers/gate_helpers.py">
    <purpose>Единый source of truth для boilerplate в gate-тестах</purpose>
    <public_api>
      load_yaml(path: Path) -> dict
      repo_root() -> Path  # кешируется, вычисление через __file__
      module_yaml_paths() -> list[Path]  # glob core/modules/*/module.yaml
      assert_ldd_imp9(caplog, min_count: int = 1) -> None  # Test Honesty LDD enforcement
    </public_api>
    <replaces>
      _load_yaml (6 копий в test_redis_static, test_gate_workflow_consistency,
                  test_gate_password_charset, test_gate_ci_env_vars,
                  test_gate_gitleaks_version, test_gate_secrets_manifest),
      PROJECT_ROOT (57 объявлений → целевые 10+ в W1, rest в W4),
      ad-hoc assert_ldd_imp9 (10+ копий)
    </replaces>
  </entity>

  <entity id="helpers_init" type="PACKAGE_MARKER" layer="tests/helpers/__init__.py">
    <content>пустой + docstring</content>
    <purpose>делает tests/helpers/ импортируемым пакетом</purpose>
  </entity>

  <entity id="args_sh" type="SHELL_LIB" layer="core/lib/args.sh">
    <purpose>Стандартизированная обработка аргументов для entrypoints + scaffold</purpose>
    <public_api>
      parse_args(spec: assoc array, "$@") -> assoc array  # --help, --context, --mode, --dry-run + custom
      usage(script_name: str, description: str, options: array) -> no return (prints to stderr, exit 0)
    </public_api>
    <deps>bash >= 4.0 (declare -A), lib/logging.sh</deps>
    <replaces>
      12 файлов с собственным usage():
      entrypoints/{bootstrap,node-update,converge}.sh,
      internal/scaffold/{adopt-project,add-project,remove-project,project-list,add-vhost,gen-env-platform}.sh,
      internal/template-engine.sh, internal/bootstrap/converge.sh,
      modules/postgres/config/pg-archive-cleanup.sh
    </replaces>
    <w1_scope>рефактор 6 приоритетных (entrypoints + scaffold), остальное — Wave 4 при декомпозиции</w1_scope>
  </entity>

  <entity id="verify_domains_log_imp_dedup" type="REFACTOR" layer="core/internal/verify/verify-domains.sh">
    <before>локальная функция log_imp() (строка 34)</before>
    <after>source "${LIB_DIR}/logging.sh"; используется log_imp из lib</after>
    <assertion>rg "log_imp\(\)" core/internal/verify/verify-domains.sh → 0 matches</assertion>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- E. BASELINE MEASUREMENT                                              -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="baseline_metrics_csv" type="REPORT" layer="reports/baseline-metrics-2026-07.csv">
    <columns>metric, value, unit, measured_at, notes</columns>
    <metrics>
      gate_fast_time (3 повтора, median),
      gate_full_time (3 повтора, median),
      ssh_no_timeout_count,
      ci_execution_time_per_workflow (last 10 runs, median),
      inline_python3_count,
      shell_loc (cloc core/),
      python_loc,
      r4_violations_count,
      r5_negative_pairs_missing
    </metrics>
    <purpose>baseline для Wave 2-5 KPI tracking (Brief 027 §10.1)</purpose>
  </entity>
</graph>
```

---

## 1. Подволна A: AGENTS.md Policy Fixation (W1-E1)

### 1.1. Шаг A1: Внести раздел «Языковая политика» в root AGENTS.md

**Файл:** `AGENTS.md`

**Позиция:** после §Глоссарий глаголов (заканчивается ~на «Правило создания проекта»), перед §Правило.

**Вставить текст из брифа 027 §1.1** (главное правило + 5 принципов применения + двухуровневый Strangler-триггер) дословно — это canonical text, согласованный с оператором.

**Верификация:**
```bash
rg -c "## Языковая политика" AGENTS.md          # → 1
rg -c "Strangler-триггер" AGENTS.md             # → ≥1
rg -c "Tier 1.*немедленный" AGENTS.md           # → ≥1
```

### 1.2. Шаг A2: TRAP[DECISION] об enforcement-механизме

**Файл:** `AGENTS.md` (сразу после раздела «Языковая политика»)

```markdown
⚠️ TRAP[DECISION] · 2026-07-21 · HI · Enforcement языковой политики через AGENTS.md + pre-commit hook, не CI gate
· Rejected: CI gate на создание .sh файлов (риск: блокирует легитимные lib-правки, замедляет hotfix-cycle)
· Reason: Code review + AGENTS.md (настоящий раздел) + pre-commit hook на новые inline python3 = достаточная защита при текущем масштабе команды и velocity. CI gate добавит friction без пропорционального gains.
· Rev: если через квартал (2026-10-21) зафиксировано >3 нарушений языковой политики → поднять вопрос о CI gate (Whitelist .sh через core/entrypoint-manifest.yaml).
```

### 1.3. Шаг A3: Pointer в core/AGENTS.md

**Файл:** `core/AGENTS.md`

В секцию навигации / references добавить one-line pointer:
```markdown
- [Root AGENTS.md — Языковая политика](../AGENTS.md#языковая-политика) — Python-only new code, двухуровневый Strangler-триггер
```

### Файлы подволны A

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 1 | `AGENTS.md` | MODIFY | +раздел «Языковая политика» (~40 строк) + TRAP[DECISION] |
| 2 | `core/AGENTS.md` | MODIFY | +one-line pointer на языковую политику |

---

## 2. Подволна B: Inline python3 Consolidation (W1-E7)

### 2.1. Шаг B1: Создать `core/internal/scripts/yaml_query.py`

**Дизайн:** Typed Python API + CLI. Заменяет 40+ однострочников `python3 -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(...))))..."`. CLI-режим позволяет вызывать из bash без inline-Python.

**Файл:** `core/internal/scripts/yaml_query.py` (CREATE)

```python
# GREP_SUMMARY: yaml_query, yaml-api, json-api, python3-c-consolidation, typed-access
# STRUCTURE: ▶ yaml_get(path,key) → ◇ load_yaml → ⊕ key lookup (nested dotted) → ⎋ value | default | exit 1
#            ▶ yaml_query(path, jq_filter) → ◇ load_yaml → ⊕ simplified jq eval → ⎋ result
#            ▶ json_get(path, key) → ◇ load_json → ⊕ key lookup → ⎋ value
# region MODULE_CONTRACT
## @purpose  Typed Python API + CLI для YAML/JSON-запросов. Заменяет inline `python3 -c` однострочники.
## @scope    Чтение YAML/JSON файлов с типизированным доступом по dotted-key path.
##           Не выполняет запись, не валидирует schema (для этого — validate_module_yaml.py в Wave 3).
## @invariants
##   - yaml_get с nested key: "node.host" → data["node"]["host"]
##   - missing key без default → exit 1 (CLI) / raise KeyError (API)
##   - missing key с default → return default (CLI prints nothing, exit 0)
##   - malformed YAML → exit 3 / raise yaml.YAMLError
##   - file not found → exit 2 / raise FileNotFoundError
## @rationale 40+ inline `python3 -c "import yaml,sys; ..."` в shell-скриптах сигнализируют о Bash-ceiling.
##            Централизация в typed-модуль: тестируемость (unit-тесты), grep-ability, единая обработка ошибок,
##            consistent CLI exit codes. Wave 1 — консолидация yaml_read.sh; Wave 4 — остальные в ходе декомпозиции.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import yaml


# region PUBLIC_API


def yaml_get(path: pathlib.Path, key: str, default: Any = None) -> Any:
    """Get value from YAML file by dotted-key path.

    Examples:
        yaml_get(Path("node.yaml"), "node.host") → "127.0.0.1"
        yaml_get(Path("node.yaml"), "node.nonexistent", default="fallback") → "fallback"
    """
    data = _load_yaml(path)
    return _dotted_get(data, key, default)


def yaml_query(path: pathlib.Path, jq_filter: str) -> Any:
    """Simplified jq-like query against YAML data.

    Supported filters (subset of jq):
        ".key"              → data["key"]
        ".key.subkey"       → nested
        ".key[]"            → list iteration (returns JSON array)
        ".key[] | .subkey"  → map over list

    For complex queries — use Python API directly, not CLI.
    """
    data = _load_yaml(path)
    return _jq_eval(data, jq_filter)


def json_get(path: pathlib.Path, key: str, default: Any = None) -> Any:
    """Get value from JSON file by dotted-key path."""
    with open(path) as f:
        data = json.load(f)
    return _dotted_get(data, key, default)


# endregion PUBLIC_API


# region INTERNAL_HELPERS


def _load_yaml(path: pathlib.Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"[IMP:9][yaml_query] file not found: {path}")
    with open(path) as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"[IMP:9][yaml_query] malformed YAML in {path}: {e}") from e


def _dotted_get(data: Any, key: str, default: Any = None) -> Any:
    current = data
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            if default is not None:
                return default
            raise KeyError(f"[IMP:9][yaml_query] key not found: {key}")
    return current


def _jq_eval(data: Any, jq_filter: str) -> Any:
    # Simplified jq evaluator — supports dotted paths + list iteration + pipe
    # NOT full jq implementation; for complex queries use Python API
    current = data
    for segment in jq_filter.strip().lstrip(".").split("|"):
        segment = segment.strip()
        if segment.endswith("[]"):
            key = segment[:-2]
            current = _dotted_get(current, key) if key else current
            if not isinstance(current, list):
                raise TypeError(f"[IMP:9][yaml_query] expected list for {segment}, got {type(current).__name__}")
            current = list(current)
        else:
            current = _dotted_get(current, segment)
    return current


# endregion INTERNAL_HELPERS


# region CLI


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="yaml_query.py",
        description="Typed YAML/JSON query — replacement for inline python3 -c",
    )
    parser.add_argument("--file", required=True, type=pathlib.Path, help="YAML or JSON file path")
    parser.add_argument("--get", metavar="KEY", help="Dotted-key path (e.g. node.host)")
    parser.add_argument("--query", metavar="JQ_FILTER", help="Simplified jq-like filter")
    parser.add_argument("--default", default=None, help="Default value if key not found")
    parser.add_argument("--json-output", action="store_true", help="Output as JSON (default: raw)")
    args = parser.parse_args()

    try:
        if args.get:
            value = yaml_get(args.file, args.get, args.default)
        elif args.query:
            value = yaml_query(args.file, args.query)
        else:
            parser.error("either --get or --query required")
            return 1  # unreachable
    except FileNotFoundError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 1
    except yaml.YAMLError as e:
        print(f"[IMP:10][yaml_query] {e}", file=sys.stderr)
        return 3

    if value is None and args.default is None:
        print(f"[IMP:9][yaml_query] key not found, no default", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(value))
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())


# endregion CLI
```

### 2.2. Шаг B2: Unit-тесты `tests/test_yaml_query.py`

**Файл:** `tests/test_yaml_query.py` (CREATE)

```python
# GREP_SUMMARY: test, yaml_query, unit-test, edge-cases, error-handling
# STRUCTURE: ▶ test_yaml_get_nested → ◇ tmp_path fixture → ⊕ assert dotted-key → ⎋ PASSED
#            ▶ test_yaml_get_missing_key_with_default → ⊕ assert default returned
#            ▶ test_yaml_get_missing_key_no_default → ⊕ assert KeyError
#            ▶ test_malformed_yaml → ⊕ assert YAMLError
#            ▶ test_file_not_found → ⊕ assert FileNotFoundError
# region MODULE_CONTRACT
## @purpose  Unit-тесты для core/internal/scripts/yaml_query.py
## @scope    Все public API функции + CLI + edge cases
## @invariants
##   - Test Honesty R1: каждый тест имеет реальное assertion (не assert True)
##   - Test Honesty R5: negative tests для error-paths (missing key, malformed, not found)
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT

import pathlib
import pytest

from core.internal.scripts.yaml_query import (
    yaml_get, yaml_query, json_get,
    _dotted_get, _load_yaml,
)


@pytest.fixture
def sample_yaml(tmp_path):
    p = tmp_path / "sample.yaml"
    p.write_text(
        "node:\n"
        "  host: 127.0.0.1\n"
        "  port: 8080\n"
        "modules:\n"
        "  - name: postgres\n"
        "  - name: redis\n"
    )
    return p


# region POSITIVE_TESTS


def test_yaml_get_nested(sample_yaml):
    assert yaml_get(sample_yaml, "node.host") == "127.0.0.1"


def test_yaml_get_list_index(sample_yaml):
    assert yaml_get(sample_yaml, "modules.0.name") == "postgres"


def test_yaml_get_with_default(sample_yaml):
    assert yaml_get(sample_yaml, "node.nonexistent", default="fb") == "fb"


def test_yaml_query_dotted(sample_yaml):
    assert yaml_query(sample_yaml, ".node.host") == "127.0.0.1"


def test_yaml_query_list_iteration(sample_yaml):
    result = yaml_query(sample_yaml, ".modules[]")
    assert result == [{"name": "postgres"}, {"name": "redis"}]


def test_json_get(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"a": {"b": 42}}')
    assert json_get(p, "a.b") == 42


# endregion POSITIVE_TESTS


# region NEGATIVE_TESTS (Test Honesty R5)


def test_yaml_get_missing_key_no_default(sample_yaml):
    with pytest.raises(KeyError, match="key not found"):
        yaml_get(sample_yaml, "node.nonexistent")


def test_malformed_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("node:\n  host: [unclosed")
    with pytest.raises(Exception):  # yaml.YAMLError
        _load_yaml(p)


def test_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        _load_yaml(tmp_path / "nonexistent.yaml")


# endregion NEGATIVE_TESTS
```

### 2.3. Шаг B3: Перевести `core/lib/yaml_read.sh` на `yaml_query.py`

**Файл:** `core/lib/yaml_read.sh` (MODIFY)

**Принцип:** public API (yaml_get, yaml_list, yaml_has_key) остаётся неизменным — backward compatible для всех callers. Внутренности делегируют к `python3 core/internal/scripts/yaml_query.py`.

**До (пример, 1 из 6 inline-блоков):**
```bash
yaml_get() {
    local file="$1" key="$2"
    python3 -c "import yaml,sys; d=yaml.safe_load(open('$file')); k='$key'.split('.'); v=d; [v:=v.get(x) for x in k]; print(v)"
}
```

**После:**
```bash
yaml_get() {
    local file="$1" key="$2"
    python3 "${CORE_DIR}/internal/scripts/yaml_query.py" --file "$file" --get "$key"
}
```

**Верификация:**
- `rg -c "python3 -c" core/lib/yaml_read.sh` → 0 (было 6).
- Все callers yaml_get/yaml_list/yaml_has_key продолжают работать (smoke-test через `make test MARKER=static`).

### 2.4. Шаг B4: Сгенерировать `reports/inline-python3-map-2026-07-21.csv`

**Одноразовый скрипт** (не регистрируется в manifest, выполняется архитектором):

```bash
#!/usr/bin/env bash
# scripts/collect-inline-python3.sh (одноразовый, НЕ коммитить как canonical script)
set -euo pipefail
OUT="reports/inline-python3-map-2026-07-21.csv"
echo "file,line,snippet,call_type,consolidation_wave" > "$OUT"

while IFS=: read -r file line content; do
    content_trimmed="${content//\"/'}"  # escape quotes for CSV
    content_trimmed="${content_trimmed/,/;}"  # escape commas
    # Determine call type
    if echo "$content" | grep -q '<<.*PYEOF\|<<.*EOF'; then
        call_type="heredoc"
    else
        call_type="python3 -c"
    fi
    # Determine consolidation wave (heuristic)
    file_rel="${file#./}"
    case "$file_rel" in
        core/lib/yaml_read.sh) wave="W1" ;;  # консолидирован в этом DevPlan
        core/internal/bootstrap/deploy-modules.sh|core/internal/bootstrap/converge.sh|core/internal/bootstrap/node-lifecycle.sh) wave="W4" ;;
        *) wave="W5" ;;
    esac
    echo "$file_rel,$line,$content_trimmed,$call_type,$wave" >> "$OUT"
done < <(rg "python3 -c|python3 - <<|python3 <<EOF" core/ -n)
```

**Верификация:** `wc -l reports/inline-python3-map-2026-07-21.csv` → ~106 (header + 105 entries).

### 2.5. Шаг B5: Pre-commit hook `no-new-inline-python3`

**Файл:** `core/internal/hooks/check-no-new-inline-python3.sh` (CREATE)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: pre-commit, inline-python3, block-new, language-policy, enforcement
# region MODULE_CONTRACT
## @purpose  Pre-commit hook: блокирует добавление НОВЫХ `python3 -c` / heredoc в shell-файлах под core/.
##           Не трогает существующие (консолидация через Strangler-триггер).
## @scope    Только staged changes (+line prefix в git diff) в .sh файлах под core/.
## @invariants
##   - Проверяет только staged additions (git diff --cached, строки с '+')
##   - Whitelist: core/lib/yaml_read.sh, core/internal/scripts/*.py, core/internal/hooks/*.sh
##   - Exit 0 = clean | Exit 1 = violation detected
## @rationale Enforcement языковой политики (AGENTS.md «Языковая политика» Tier 1).
##            CI gate отклонён оператором (TRAP[DECISION] в AGENTS.md).
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT
set -euo pipefail

WHITELIST_REGEX="^core/lib/yaml_read\.sh$|^core/internal/scripts/.*\.py$|^core/internal/hooks/.*\.sh$"

# Получаем staged .sh files under core/
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' || true)

violations=0
for file in $staged_files; do
    # Whitelist check
    if echo "$file" | grep -qE "$WHITELIST_REGEX"; then
        continue
    fi

    # Проверяем только added lines
    while IFS= read -r line; do
        # Strip leading + from diff
        content="${line#+}"
        # Detect inline python3 patterns
        if echo "$content" | grep -qE 'python3 -c|python3 - <<|python3 <<EOF|python3 <<PYEOF'; then
            echo "[IMP:10][no-new-inline-python3] VIOLATION in $file:"
            echo "  $content"
            echo ""
            echo "Language policy violation: new inline python3 blocked."
            echo "  → Extract logic to core/internal/scripts/<module>.py"
            echo "  → Or use existing core/internal/scripts/yaml_query.py for YAML/JSON access"
            echo "  → See AGENTS.md §Языковая политика (Tier 1 trigger)"
            violations=$((violations + 1))
        fi
    done < <(git diff --cached -- "$file" | grep '^+')
done

if [[ $violations -gt 0 ]]; then
    exit 1
fi

exit 0
```

**Файл:** `.pre-commit-config.yaml` (MODIFY) — добавить hook:

```yaml
  - id: no-new-inline-python3
    name: Block new inline python3 in shell scripts
    entry: bash core/internal/hooks/check-no-new-inline-python3.sh
    language: system
    files: '^core/.*\.sh$'
    pass_filenames: false
    always_run: false
```

### Файлы подволны B

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 3 | `core/internal/scripts/yaml_query.py` | CREATE | Typed YAML/JSON API + CLI |
| 4 | `tests/test_yaml_query.py` | CREATE | Unit-тесты (positive + negative) |
| 5 | `core/lib/yaml_read.sh` | MODIFY | Перевод 6 inline-блоков на yaml_query.py |
| 6 | `core/internal/hooks/check-no-new-inline-python3.sh` | CREATE | Pre-commit enforcement |
| 7 | `.pre-commit-config.yaml` | MODIFY | +hook no-new-inline-python3 |
| 8 | `reports/inline-python3-map-2026-07-21.csv` | CREATE | 105 inline-вызовов с wave-tracking |

---

## 3. Подволна C: Honesty-First (W1-E2, W1-E3)

### 3.1. Шаг C1: Создать `tests/_conftest/honesty.py`

**Дизайн:** Поэтапный переход skip→fail через env var. Wave 1 запускается в режиме `marker` (мягкий — skip остаётся skip, но с явным marker'ом для future переключения). После стабилизации в Wave 2 оператор переключает на `fail`. Это митигация R-RISK-2.

**Файл:** `tests/_conftest/honesty.py` (CREATE)

```python
# GREP_SUMMARY: honesty, require-docker, require-script, require-env, R4-fix, mode-transition
# STRUCTURE: ▶ require_docker_or_fail → ◇ _check_service_available → ⊕ mode-dispatch(marker|xfail|fail) → ⎋ skip|xfail|fail
# region MODULE_CONTRACT
## @purpose  Test Honesty enforcement: NO_SERVICE = FAIL, not skip (Test Honesty Rule R4).
##           Поэтапный переход через REQUIRE_HONESTY_MODE env var.
## @scope    All tests requiring external service (Docker, scripts, env vars).
## @invariants
##   - REQUIRE_HONESTY_MODE env var controls behavior:
##     "marker" (default W1) → pytest.mark.skip (soft, but tagged [IMP:10][honesty])
##     "xfail"               → pytest.xfail(strict=False)
##     "fail" (target W2)    → pytest.fail (honest)
##   - Каждый вызов логирует [IMP:10][honesty] mode + missing service + action
##   - На CI без Docker: в режиме "marker" — skip; в режиме "fail" — fail (R4 compliant)
## @rationale R-RISK-2: прямой skip→fail переход временно ломает CI на staging (no Docker).
##            Поэтапность: marker → xfail → fail даёт команде время на настройку CI runners.
##            Wave 1 = marker (no behavior change, just tagging).
##            Wave 2 = переключение на fail (operator decision).
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E2)
# endregion MODULE_CONTRACT

import os
import shutil
import subprocess
import pathlib
from typing import Literal

import pytest


HonestyMode = Literal["marker", "xfail", "fail"]


def _honesty_mode() -> HonestyMode:
    """Read REQUIRE_HONESTY_MODE env var. Default: 'marker' (soft Wave 1)."""
    mode = os.environ.get("REQUIRE_HONESTY_MODE", "marker").lower().strip()
    if mode not in ("marker", "xfail", "fail"):
        raise ValueError(
            f"[IMP:10][honesty] invalid REQUIRE_HONESTY_MODE={mode!r}, "
            f"expected one of: marker, xfail, fail"
        )
    return mode  # type: ignore[return-value]


def _docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _dispatch(mode: HonestyMode, reason: str) -> None:
    """Dispatch action based on honesty mode."""
    print(f"[IMP:10][honesty] mode={mode} action={ {'marker':'skip','xfail':'xfail','fail':'fail'}[mode] } reason={reason}")
    if mode == "marker":
        pytest.skip(f"[honesty:marker] {reason}")
    elif mode == "xfail":
        pytest.xfail(f"[honesty:xfail] {reason}")
    elif mode == "fail":
        pytest.fail(f"[honesty:fail] {reason}", pytrace=False)


# region PUBLIC_API


def require_docker_or_fail(reason: str = "Docker daemon required") -> None:
    """R4 fix: skip/fail when Docker not available. Mode controlled by REQUIRE_HONESTY_MODE."""
    if _docker_available():
        print(f"[IMP:9][honesty] Docker available, proceeding")
        return
    _dispatch(_honesty_mode(), f"Docker daemon not available — {reason}")


def require_script_or_fail(script_path: pathlib.Path, reason: str = "") -> None:
    """R4 fix: skip/fail when required script not found."""
    if script_path.exists() and os.access(script_path, os.X_OK):
        return
    _dispatch(_honesty_mode(), f"Script not found or not executable: {script_path} — {reason}")


def require_env_or_fail(var: str, reason: str = "") -> None:
    """R4 fix: skip/fail when required env var not set."""
    if os.environ.get(var):
        return
    _dispatch(_honesty_mode(), f"Env var not set: {var} — {reason}")


# endregion PUBLIC_API
```

### 3.2. Шаг C2: Заменить R4-нарушения (skip → require_docker_or_fail)

**Scope:** ~18-25 файлов (точный список по `rg 'pytest\.skip\(.*not available' tests/`).

**Паттерн замены:**

**До:**
```python
import subprocess
try:
    subprocess.run(["docker", "info"], check=True, capture_output=True)
except (subprocess.CalledProcessError, FileNotFoundError):
    pytest.skip("Docker daemon not available")
```

**После:**
```python
from _conftest.honesty import require_docker_or_fail

# В начале теста или fixture:
require_docker_or_fail(reason="postgres smoke test requires Docker daemon")
```

**Приоритетные файлы (W1 scope, ~15 файлов):**
1. `tests/test_smoke_platform.py`
2. `tests/test_smoke_postgres.py`
3. `tests/test_component_clickhouse.py`
4. `tests/test_component_hermes.py`
5. `tests/test_component_pgbouncer.py`
6. `tests/test_local_auth.py`
7. `tests/test_integration_hermes_llm.py`
8. `tests/test_hermes_init.py`
9. `tests/test_hermes_l2_fallback.py`
10. `tests/_conftest/smoke.py`
11. `tests/_conftest/e2e.py`
12. `tests/gates/test_gate_vhost_nginx_t.py`
13. `tests/test_smoke_langfuse.py` (частично)
14. `tests/test_hermes_best_practices.py` (частично)
15. `tests/test_e2e_prometheus.py` (частично)

**Остальные (5-10 файлов с `script not available` / `env not available`):**
- `tests/test_contract_entrypoints.py` → `require_script_or_fail`
- `tests/gates/test_gate_project_context.py` → keep (legitimate skip: dev environment)
- `tests/gates/test_gate_project_env.py` → keep (legitimate skip: dev environment)
- `tests/gates/test_gate_ci_env_vars.py` → analyze (likely legitimate)
- `tests/gates/test_gate_template_drift.py` → `require_env_or_fail` или keep
- `tests/gates/test_gate_template_syntax.py` → `require_env_or_fail("PYAMLENV")` или keep
- `tests/gates/test_gate_env_hostname_drift.py` → analyze
- `tests/gates/test_gate_env_example_sync.py` → keep (legitimate skip: CI vs local)

**Верификация:**
```bash
# Wave 1 запускается в marker mode → skip остаётся skip, но логируется [IMP:10][honesty]
REQUIRE_HONESTY_MODE=marker python -m pytest tests/test_smoke_platform.py -s | rg "honesty"
# → хотя бы одна строка [IMP:10][honesty] mode=marker action=skip

# Wave 2 target: fail mode → skip превращается в fail
REQUIRE_HONESTY_MODE=fail python -m pytest tests/test_smoke_platform.py | rg "FAILED\|honesty"
```

### 3.3. Шаг C3: Создать 3 `_negative` пары (W1-E3)

**Принцип (Test Honesty R5):** для каждого gate-теста, детектирующего соблюдение инварианта, должен существовать `_negative`-компаньон, детектирующий нарушение.

#### C3a: `tests/gates/test_gate_litellm_pg_enforcement_negative.py`

**Файл:** `tests/gates/test_gate_litellm_pg_enforcement_negative.py` (CREATE)

```python
# GREP_SUMMARY: test, gate, litellm, pg-enforcement, negative, R5, sqlite-detection
# STRUCTURE: ▶ test_litellm_sqlite_config_detected → ◇ tmp_path construct bad config → ⊕ call gate fn → ⎋ assert violation reported
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_litellm_pg_enforcement.
##           Если positive-тест детектит PG-enforcement, negative-тест детектит SQLite config.
## @scope    Конструирует LiteLLM config с sqlite:// URL, вызывает gate function,
##           ожидает detection (violation reported).
## @invariants
##   - Test Honesty R1: реально падает на конструируемом нарушении (не assert True)
##   - Test Honesty R5: companion to test_gate_litellm_pg_enforcement
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib
import pytest

# Import the gate function under test
# Actual import path depends on existing test structure:
from tests.gates.test_gate_litellm_pg_enforcement import (
    check_litellm_pg_enforcement,  # adjust to actual function name
)


@pytest.mark.gate
def test_litellm_sqlite_config_detected(tmp_path):
    """R5: gate must DETECT SQLite config (LiteLLM invariant violation).

    Constructs a LiteLLM config with database_url=sqlite:///...,
    calls gate function, expects violation reported.
    """
    # Construct violating config
    litellm_config = tmp_path / "litellm_config.yaml"
    litellm_config.write_text(
        "model_list: []\n"
        "general_settings:\n"
        "  database_url: 'sqlite:///./test.db'  # VIOLATION: must be PostgreSQL\n"
    )

    violations = check_litellm_pg_enforcement(config_path=litellm_config)

    assert violations, (
        f"[IMP:9][gate][negative] gate FAILED to detect SQLite config — "
        f"violations={violations!r}"
    )
    assert any("sqlite" in v.lower() for v in violations), (
        f"[IMP:9][gate][negative] violations do not mention sqlite: {violations!r}"
    )
```

#### C3b: `tests/gates/test_gate_module_schema_d4_negative.py`

**Файл:** `tests/gates/test_gate_module_schema_d4_negative.py` (CREATE)

```python
# GREP_SUMMARY: test, gate, module-schema, D4, negative, R5, missing-fields
# STRUCTURE: ▶ test_module_yaml_missing_required_fields → ◇ tmp_path bad module.yaml → ⊕ validate → ⎋ assert errors
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_module_schema_d4.
##           Детектит module.yaml без required D4 полей.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib
import pytest
import jsonschema

from tests.gates.test_gate_module_schema_d4 import (
    MODULE_D4_SCHEMA,  # adjust to actual export
    validate_module_yaml_d4,
)


@pytest.mark.gate
def test_module_yaml_missing_required_fields(tmp_path):
    """R5: validator must REPORT missing required D4 fields."""
    # Construct module.yaml without env_requires (required by D4)
    bad_module = tmp_path / "module.yaml"
    bad_module.write_text(
        "name: test-module\n"
        "version: 1.0.0\n"
        "# missing: env_requires, restart, healthcheck\n"
    )

    errors = validate_module_yaml_d4(bad_module)

    assert errors, (
        f"[IMP:9][gate][negative] validator FAILED to report missing fields"
    )
    assert any("env_requires" in e.lower() for e in errors), (
        f"[IMP:9][gate][negative] errors do not mention env_requires: {errors!r}"
    )
```

#### C3c: `tests/gates/test_gate_env_shared_consistency_negative.py`

**Файл:** `tests/gates/test_gate_env_shared_consistency_negative.py` (CREATE)

```python
# GREP_SUMMARY: test, gate, env-shared, consistency, negative, R5, divergence
# STRUCTURE: ▶ test_env_shared_divergence_detected → ◇ tmp_path two module.yaml → ⊕ consistency check → ⎋ assert divergence
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_env_shared_consistency.
##           Детектит рассинхрон env_shared между модулями.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib
import pytest

from tests.gates.test_gate_env_shared_consistency import (
    check_env_shared_consistency,  # adjust to actual function name
)


@pytest.mark.gate
def test_env_shared_divergence_detected(tmp_path):
    """R5: consistency checker must DETECT divergent env_shared declarations."""
    # Module A declares SHARED_VAR with one value
    module_a = tmp_path / "moduleA.yaml"
    module_a.write_text(
        "name: moduleA\n"
        "env_shared:\n"
        "  SHARED_VAR: 'value-from-A'\n"
    )
    # Module B declares SHARED_VAR with different value (VIOLATION)
    module_b = tmp_path / "moduleB.yaml"
    module_b.write_text(
        "name: moduleB\n"
        "env_shared:\n"
        "  SHARED_VAR: 'value-from-B'  # DIVERGENT\n"
    )

    divergences = check_env_shared_consistency([module_a, module_b])

    assert divergences, (
        f"[IMP:9][gate][negative] checker FAILED to detect SHARED_VAR divergence"
    )
    assert any("SHARED_VAR" in d for d in divergences), (
        f"[IMP:9][gate][negative] divergences do not mention SHARED_VAR: {divergences!r}"
    )
```

**Важно:** actual function names (`check_litellm_pg_enforcement`, `validate_module_yaml_d4`, `check_env_shared_consistency`) должны быть уточнены при реализации через чтение исходных positive-тестов. Если functions не exported — добавить export (refactor positive-теста).

### 3.4. Шаг C4: Регистрация `_negative` gate-тестов в manifest

**Файл:** `core/entrypoint-manifest.yaml` (MODIFY) — секция gates:

```yaml
  - id: gate-litellm-pg-enforcement-negative
    description: "R5 companion: detect SQLite config in LiteLLM (anti-survivorship)"
    test_file: "test_gate_litellm_pg_enforcement_negative.py"
    issue: "028-wave1-immediate"

  - id: gate-module-schema-d4-negative
    description: "R5 companion: detect module.yaml missing required D4 fields"
    test_file: "test_gate_module_schema_d4_negative.py"
    issue: "028-wave1-immediate"

  - id: gate-env-shared-consistency-negative
    description: "R5 companion: detect divergent env_shared declarations between modules"
    test_file: "test_gate_env_shared_consistency_negative.py"
    issue: "028-wave1-immediate"
```

### Файлы подволны C

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 9 | `tests/_conftest/honesty.py` | CREATE | require_docker_or_fail + mode-transition |
| 10-24 | ~15 test files | MODIFY | skip → require_docker_or_fail (marker mode) |
| 25 | `tests/gates/test_gate_litellm_pg_enforcement_negative.py` | CREATE | R5 negative pair |
| 26 | `tests/gates/test_gate_module_schema_d4_negative.py` | CREATE | R5 negative pair |
| 27 | `tests/gates/test_gate_env_shared_consistency_negative.py` | CREATE | R5 negative pair |
| 28 | `core/entrypoint-manifest.yaml` | MODIFY | +3 gate registrations |

---

## 4. Подволна D: Boilerplate Removal (W1-E4, W1-E5, W1-E6)

### 4.1. Шаг D1: Создать `tests/helpers/gate_helpers.py`

**Файл:** `tests/helpers/__init__.py` (CREATE) — пустой + docstring.

**Файл:** `tests/helpers/gate_helpers.py` (CREATE)

```python
# GREP_SUMMARY: gate-helpers, load-yaml, repo-root, assert-ldd-imp9, boilerplate-dedup
# STRUCTURE: ▶ load_yaml(path) → ◇ yaml.safe_load → ⎋ dict
#            ▶ repo_root() → ◇ __file__ resolution (cached) → ⎋ Path
#            ▶ module_yaml_paths() → ◇ glob core/modules/ → ⎋ list[Path]
#            ▶ assert_ldd_imp9(caplog, min_count) → ◇ filter records → ⊕ assert count → ⎋ None
# region MODULE_CONTRACT
## @purpose  Единый source of truth для boilerplate в gate-тестах.
##           Устраняет 6 копий _load_yaml, 57 объявлений PROJECT_ROOT, 10+ копий assert_ldd_imp9.
## @scope    All tests under tests/gates/ и tests/ использующие YAML loading, project root, LDD assertions.
## @invariants
##   - repo_root() кешируется (module-level) — вычисление один раз за сессию
##   - load_yaml использует yaml.safe_load (не FullLoader) для security
##   - assert_ldd_imp9 fails test если нет ни одного [IMP:9]+ log (Test Honesty LDD)
## @rationale Brief 027 §3.1 W1-E4: −25-30% строк в gate-тестах, единый source of truth.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E4)
# endregion MODULE_CONTRACT

import functools
import pathlib
from typing import Any

import yaml


# region REPO_ROOT


@functools.lru_cache(maxsize=1)
def repo_root() -> pathlib.Path:
    """Cached project root. Resolves from this file: tests/helpers/ → tests/ → project root."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


# endregion REPO_ROOT


# region YAML_HELPERS


def load_yaml(path: pathlib.Path | str) -> Any:
    """Load YAML file. Uses yaml.safe_load for security."""
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[gate_helpers] YAML file not found: {p}")
    with open(p) as f:
        return yaml.safe_load(f)


def module_yaml_paths() -> list[pathlib.Path]:
    """Glob all module.yaml files under core/modules/."""
    root = repo_root()
    return sorted((root / "core" / "modules").glob("*/module.yaml"))


# endregion YAML_HELPERS


# region LDD_ASSERTIONS


def assert_ldd_imp9(caplog, min_count: int = 1) -> None:
    """Assert that at least min_count [IMP:9+] log records exist in caplog.

    Implements Test Honesty LDD enforcement (RULES.md §TESTING).
    """
    imp9_plus = [
        r for r in caplog.records
        if "[IMP:" in r.message
        and any(
            int(lvl) >= 9
            for lvl in [r.message.split("[IMP:")[1].split("]")[0]]
            if lvl.isdigit()
        )
    ]
    assert len(imp9_plus) >= min_count, (
        f"[gate_helpers] LDD assertion failed: expected >={min_count} [IMP:9+] logs, "
        f"got {len(imp9_plus)}. Records: {[r.message for r in caplog.records[:5]]}"
    )


# endregion LDD_ASSERTIONS
```

### 4.2. Шаг D2: Рефактор gate-тестов на gate_helpers

**6 файлов с локальным `_load_yaml` (полный dedup):**
1. `tests/test_redis_static.py`
2. `tests/gates/test_gate_workflow_consistency.py`
3. `tests/gates/test_gate_password_charset.py`
4. `tests/gates/test_gate_ci_env_vars.py`
5. `tests/gates/test_gate_gitleaks_version.py`
6. `tests/gates/test_gate_secrets_manifest.py`

**Паттерн:**

**До:**
```python
def _load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
```

**После:**
```python
from tests.helpers.gate_helpers import load_yaml, repo_root

# Remove _load_yaml and PROJECT_ROOT definitions
```

**Дополнительно: минимум 10 файлов на `repo_root()` dedup** (из 57 объявлений PROJECT_ROOT → целевые ≤30 после Wave 1; полное устранение — Wave 4).

**Верификация:**
```bash
rg "def _load_yaml" tests/ | wc -l     # → 0 (было 6)
rg "PROJECT_ROOT\s*=" tests/ | wc -l   # → ≤30 (было 57)
```

### 4.3. Шаг D3: Создать `core/lib/args.sh`

**Файл:** `core/lib/args.sh` (CREATE)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: args, parse-args, usage, standardization, boilerplate-dedup
# STRUCTURE: ▶ parse_args(spec, "$@") → ◇ loop args → ⊕ match spec → ⎋ assoc array | usage+exit
#            ▶ usage(script, desc, options) → ◇ format → ⎋ print stderr + exit 0
# region MODULE_CONTRACT
## @purpose  Стандартизированная обработка аргументов для entrypoints + scaffold.
##           Заменяет 12 локальных usage() и 8+ локальных parse_args.
## @scope    Sourced by entrypoints/*.sh, internal/scaffold/*.sh, internal/bootstrap/*.sh.
## @invariants
##   - Bash >= 4.0 (declare -A для assoc arrays)
##   - Поддерживает: --help, -h, --context <val>, --mode <val>, --dry-run, --verbose
##   - Custom options через spec array: ["--option"]="value_required|flag"
##   - На --help / -h → вызов usage() + exit 0
## @rationale Brief 027 §3.1 W1-E5: единый lib-layer, -400 строк boilerplate.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E5)
# endregion MODULE_CONTRACT

# Requires: lib/logging.sh (sourced by caller)

# region USAGE


usage() {
    local script_name="$1"
    local description="$2"
    shift 2
    local -a options=("$@")

    echo "Usage: ${script_name} [OPTIONS]"
    echo ""
    echo "  ${description}"
    echo ""
    echo "Options:"
    for opt in "${options[@]}"; do
        # Format: "--flag <value> | description"
        echo "  ${opt}"
    done
    echo ""
    echo "Common options:"
    echo "  --help, -h        Show this help and exit"
    echo "  --context <name>  Platform context (default: from path)"
    echo "  --mode <mode>     Operation mode"
    echo "  --dry-run         Show actions without executing"
    echo "  --verbose         Enable verbose logging"
    echo ""
    exit 0
}


# endregion USAGE


# region PARSE_ARGS


parse_args() {
    # Usage: parse_args <spec_assoc_array_name> -- "$@"
    # spec format: declare -A SPEC=( [--context]="value" [--dry-run]="flag" ... )
    # Returns: assoc array with parsed values, exit 0
    # On --help: calls usage (caller must set USAGE_SCRIPT/USAGE_DESC before)
    local -n _spec_ref="$1"
    local -n _result_ref="$2"
    shift 2
    # shift past "--"
    [[ "${1:-}" == "--" ]] && shift

    # Initialize result with defaults
    local opt
    for opt in "${!_spec_ref[@]}"; do
        _result_ref["$opt"]=""
    done

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                if [[ -n "${USAGE_SCRIPT:-}" ]]; then
                    usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}"
                fi
                exit 0
                ;;
            --*)
                opt="$1"
                if [[ -z "${_spec_ref[$opt]+x}" ]]; then
                    echo "[IMP:10][args] unknown option: $opt" >&2
                    return 1
                fi
                if [[ "${_spec_ref[$opt]}" == "flag" ]]; then
                    _result_ref["$opt"]="1"
                    shift
                else
                    # value required
                    if [[ $# -lt 2 ]]; then
                        echo "[IMP:10][args] option $opt requires value" >&2
                        return 1
                    fi
                    _result_ref["$opt"]="$2"
                    shift 2
                fi
                ;;
            *)
                echo "[IMP:10][args] unexpected positional arg: $1" >&2
                return 1
                ;;
        esac
    done

    return 0
}


# endregion PARSE_ARGS
```

### 4.4. Шаг D4: Рефактор 6 приоритетных entrypoints на lib/args.sh

**Приоритетные файлы (W1 scope):**
1. `core/entrypoints/bootstrap.sh`
2. `core/entrypoints/node-update.sh`
3. `core/entrypoints/converge.sh`
4. `core/internal/scaffold/adopt-project.sh`
5. `core/internal/scaffold/add-project.sh`
6. `core/internal/scaffold/remove-project.sh`

**Паттерн:**

**До:**
```bash
usage() {
    echo "Usage: bootstrap.sh [OPTIONS]"
    echo "Options:"
    echo "  --context <name>  Platform context"
    # ... 20+ lines
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --context) CONTEXT="$2"; shift 2 ;;
            # ...
        esac
    done
}
```

**После:**
```bash
source "${CORE_DIR}/lib/args.sh"

declare -A ARG_SPEC=(
    [--context]="value"
    [--mode]="value"
    [--dry-run]="flag"
    [--verbose]="flag"
)
declare -A ARG_RESULT

USAGE_SCRIPT="bootstrap.sh"
USAGE_DESC="Idempotent node bootstrap (LIFE CYCLE/INIT)"
USAGE_OPTIONS=(
    "--context <name>  Platform context (default: from path)"
    "--mode <mode>     bootstrap | verify (default: bootstrap)"
    "--dry-run         Show actions without executing"
    "--verbose         Enable verbose logging"
)

parse_args ARG_SPEC ARG_RESULT -- "$@"
CONTEXT="${ARG_RESULT[--context]:-}"
MODE="${ARG_RESULT[--mode]:-bootstrap}"
DRY_RUN="${ARG_RESULT[--dry-run]:-}"
VERBOSE="${ARG_RESULT[--verbose]:-}"
```

**Остальные 6 файлов** (`internal/scaffold/project-list.sh`, `internal/scaffold/add-vhost.sh`, `internal/scaffold/gen-env-platform.sh`, `internal/template-engine.sh`, `internal/bootstrap/converge.sh`, `modules/postgres/config/pg-archive-cleanup.sh`) — Wave 4 при декомпозиции.

**Верификация:**
```bash
rg "^usage\(\)" core/ | wc -l     # → ≤6 (было 12)
rg "^parse_args\(\)" core/ | wc -l # → ≤2 (было ~8)
```

### 4.5. Шаг D5: verify-domains log_imp dedup

**Файл:** `core/internal/verify/verify-domains.sh` (MODIFY)

**Удалить локальную функцию** (строка 34):
```bash
log_imp() {
    # ... локальная реализация
}
```

**Добавить source** (в header после других source-строк):
```bash
source "${CORE_DIR}/lib/logging.sh"
```

**Использование остаётся** `log_imp "..."` — теперь резолвится в lib-функцию.

**Верификация:**
```bash
rg "log_imp\(\)" core/internal/verify/verify-domains.sh   # → 0 matches (definition)
rg "log_imp " core/internal/verify/verify-domains.sh      # → calls остаются (теперь к lib)
```

### Файлы подволны D

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 29 | `tests/helpers/__init__.py` | CREATE | Package marker |
| 30 | `tests/helpers/gate_helpers.py` | CREATE | load_yaml, repo_root, assert_ldd_imp9 |
| 31-36 | 6 test files | MODIFY | _load_yaml dedup → gate_helpers.load_yaml |
| 37-46 | 10 test files | MODIFY | PROJECT_ROOT dedup → gate_helpers.repo_root |
| 47 | `core/lib/args.sh` | CREATE | parse_args + usage helpers |
| 48-53 | 6 entrypoints/scaffold | MODIFY | usage()/parse_args() → lib/args.sh |
| 54 | `core/internal/verify/verify-domains.sh` | MODIFY | удаление локального log_imp, source lib/logging.sh |

---

## 5. Подволна E: Baseline Measurement (W1-E8)

### 5.1. Шаг E1: Замерить baseline-метрики

**Команды** (выполняет архитектор, результаты в `reports/baseline-metrics-2026-07.csv`):

```bash
# 1. make gate MODE=fast time (3 повтора, median)
for i in 1 2 3; do
    /usr/bin/time -p make gate MODE=fast 2>&1 | rg "real"
done

# 2. make gate MODE=full time (3 повтора, median)
for i in 1 2 3; do
    SKIP_PRECOMMIT=1 /usr/bin/time -p make gate MODE=full 2>&1 | rg "real"
done

# 3. SSH-вызывов без timeout
rg "ssh " core/ -g "*.sh" | rg -v "ConnectTimeout|timeout|BatchMode" | wc -l

# 4. CI execution time per workflow (из GitHub Actions API или last 10 runs)
gh run list --limit 10 --json status,conclusion,createdAt,updatedAt,name

# 5. Inline python3 count
rg "python3 -c" core/ | wc -l

# 6. Shell LOC
cloc core/ --include-lang=Bash --quiet

# 7. Python LOC
cloc core/internal/scripts/ core/modules/ --include-lang=Python --quiet

# 8. R4 violations count
rg "pytest\.skip\(.*not available" tests/ | wc -l

# 9. R5 negative pairs missing
# (вычисляется вручную: gate-тесты без _negative компаньона)
```

### 5.2. Шаг E2: Записать в CSV

**Файл:** `reports/baseline-metrics-2026-07.csv` (CREATE)

```csv
metric,value,unit,measured_at,notes
gate_fast_time_run1,,s,2026-07-21,
gate_fast_time_run2,,s,2026-07-21,
gate_fast_time_run3,,s,2026-07-21,
gate_fast_time_median,,s,2026-07-21,
gate_full_time_run1,,s,2026-07-21,SKIP_PRECOMMIT=1
gate_full_time_run2,,s,2026-07-21,SKIP_PRECOMMIT=1
gate_full_time_run3,,s,2026-07-21,SKIP_PRECOMMIT=1
gate_full_time_median,,s,2026-07-21,
ssh_no_timeout_count,,calls,2026-07-21,rg "ssh " core/ -g "*.sh" | rg -v "ConnectTimeout|timeout|BatchMode" | wc -l
ci_execution_time_median,,s,2026-07-21,median of last 10 GitHub Actions runs
inline_python3_count,105,calls,2026-07-21,rg "python3 -c" core/ | wc -l
shell_loc,,LOC,2026-07-21,cloc core/ --include-lang=Bash
python_loc,,LOC,2026-07-21,cloc core/internal/scripts/ core/modules/ --include-lang=Python
r4_violations_count,,violations,2026-07-21,rg "pytest\.skip\(.*not available" tests/ | wc -l
r5_negative_pairs_missing,3,pairs,2026-07-21,litellm_pg_enforcement + module_schema_d4 + env_shared_consistency
```

Values заполняются архитектором после выполнения замеров.

### Файлы подволны E

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 55 | `reports/baseline-metrics-2026-07.csv` | CREATE | Baseline для Wave 2-5 KPI tracking |

---

## 6. Step-by-step Data Flow — Honesty-Mode Transition

```
┌─────────────────────────────────────────────────────────────────┐
│  Тест test_smoke_platform.py::test_docker_up                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Вызов require_docker_or_fail(reason="...")            │   │
│  └─────────────────────┬────────────────────────────────────┘   │
│                        │                                          │
│                        ▼                                          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 2. _docker_available()?                                   │   │
│  │    subprocess.run(["docker", "info"], timeout=5)         │   │
│  └─────────────────────┬────────────────────────────────────┘   │
│                        │                                          │
│           ┌────────────┴────────────┐                             │
│           ▼                         ▼                             │
│      available=true             available=false                  │
│           │                         │                             │
│           ▼                         ▼                             │
│  [IMP:9][honesty]              3. _honesty_mode()                │
│  proceeding                    reads REQUIRE_HONESTY_MODE env    │
│      │                                │                          │
│      ▼                  ┌─────────────┼─────────────┐            │
│   test runs             ▼             ▼             ▼            │
│                    "marker"       "xfail"        "fail"          │
│                   (W1 default)                                  │
│                         │             │             │            │
│                         ▼             ▼             ▼            │
│                   pytest.skip    pytest.xfail  pytest.fail       │
│                   [IMP:10]       [IMP:10]      [IMP:10]          │
│                                                                  │
│  Wave 1: REQUIRE_HONESTY_MODE=marker (default)                   │
│    → behaviorally identical to old skip, but tagged for switch   │
│                                                                  │
│  Wave 2: REQUIRE_HONESTY_MODE=fail (operator switch)             │
│    → CI без Docker становится RED (honest) → team fixes runners  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. File Manifest — полный список изменений

| # | Файл | Подволна | Действие | Описание |
|---|------|:--------:|----------|----------|
| 1 | `AGENTS.md` | A | MODIFY | +раздел «Языковая политика» + TRAP[DECISION] |
| 2 | `core/AGENTS.md` | A | MODIFY | +one-line pointer |
| 3 | `core/internal/scripts/yaml_query.py` | B | CREATE | Typed YAML/JSON API + CLI |
| 4 | `tests/test_yaml_query.py` | B | CREATE | Unit-тесты |
| 5 | `core/lib/yaml_read.sh` | B | MODIFY | Перевод 6 inline-блоков на yaml_query.py |
| 6 | `core/internal/hooks/check-no-new-inline-python3.sh` | B | CREATE | Pre-commit enforcement |
| 7 | `.pre-commit-config.yaml` | B | MODIFY | +hook no-new-inline-python3 |
| 8 | `reports/inline-python3-map-2026-07-21.csv` | B | CREATE | 105 inline-вызовов с wave-tracking |
| 9 | `tests/_conftest/honesty.py` | C | CREATE | require_docker_or_fail + mode-transition |
| 10-24 | ~15 test files | C | MODIFY | skip → require_docker_or_fail |
| 25 | `tests/gates/test_gate_litellm_pg_enforcement_negative.py` | C | CREATE | R5 negative pair |
| 26 | `tests/gates/test_gate_module_schema_d4_negative.py` | C | CREATE | R5 negative pair |
| 27 | `tests/gates/test_gate_env_shared_consistency_negative.py` | C | CREATE | R5 negative pair |
| 28 | `core/entrypoint-manifest.yaml` | C | MODIFY | +3 gate registrations |
| 29 | `tests/helpers/__init__.py` | D | CREATE | Package marker |
| 30 | `tests/helpers/gate_helpers.py` | D | CREATE | load_yaml, repo_root, assert_ldd_imp9 |
| 31-36 | 6 test files | D | MODIFY | _load_yaml dedup |
| 37-46 | 10 test files | D | MODIFY | PROJECT_ROOT dedup |
| 47 | `core/lib/args.sh` | D | CREATE | parse_args + usage helpers |
| 48-53 | 6 entrypoints/scaffold | D | MODIFY | usage()/parse_args() → lib/args.sh |
| 54 | `core/internal/verify/verify-domains.sh` | D | MODIFY | log_imp dedup → lib/logging.sh |
| 55 | `reports/baseline-metrics-2026-07.csv` | E | CREATE | Baseline для Wave 2-5 |

**Итого:** 11 CREATE + 30+ MODIFY + 2 reports = ~43 файла затронуто.

---

## 8. Порядок выполнения

```
Подволна A (AGENTS.md policy)
    │ даёт policy-рамку для всех остальных
    ▼
Подволна B (inline python3 consolidation)
    │ демонстрирует policy в действии, даёт yaml_query.py
    ▼
Подволна C (honesty-first)
    │ закрывает false-green, параллельно с D
    ├────────────────────┐
    ▼                    ▼
Подволна D (boilerplate)   Подволна E (baseline measurement)
    │                    │ параллельно, не блокирует
    ▼                    │
gate green ◄─────────────┘
    │
    ▼
Production-релиз Wave 1
(обновлённый AGENTS.md, честные тесты в marker mode, yaml_query.py, gate_helpers, lib/args.sh)
    │
    ▼
Переключение REQUIRE_HONESTY_MODE=fail (operator decision, ~1-2 нед после стабилизации)
    │
    ▼
Wave 2 старт (после подтверждения operator)
```

**Обоснование порядка:**

1. **A первой** — языковая политика даёт рамку для всех решений в B-D (например, «почему мы создаём yaml_query.py, а не правим inline?» — ответ из AGENTS.md).

2. **B после A** — yaml_query.py демонстрирует policy в действии. Без policy фикса создание yaml_query.py было бы «очередным helper'ом»; с policy — это реализация Tier 1 триггера.

3. **C параллельно с D** — honesty-first и boilerplate-removal независимы. C закрывает trust-киллер (R4/R5), D даёт −400 строк. Оба не блокируют друг друга.

4. **E параллельно** — baseline measurement не зависит от A-D, но желательно после B (для точного inline count после частичной консолидации).

5. **Production-релиз после всех подволны** — единый commit/PR на Wave 1, gate green, обновлённый AGENTS.md публикуется.

---

## 9. Risks и Mitigation

| ID | Risk | L | I | Mitigation | Подволна |
|----|------|---|---|------------|----------|
| **R-RISK-2** | R4-fix (skip→fail) временно ломает CI на staging (no Docker) | H | M | Поэтапный режим REQUIRE_HONESTY_MODE: marker (W1 default) → xfail → fail (W2). W1 не меняет behavior, только tagging. | C |
| **R-RISK-9** | Pre-commit hook no-new-inline-python3 даёт false-positives на легитимные однострочники | M | L | Whitelist: `core/lib/yaml_read.sh`, `core/internal/scripts/*.py`, `core/internal/hooks/*.sh`. Ignore-pattern для `python3 -c "import yaml; ..."` внутри одобренных lib-файлов. | B |
| **PGM-R1** | Языковая политика не соблюдается без CI gate | M | M | Code review checklist + AGENTS.md enforcement + pre-commit hook (W1-E7). TRAP[DECISION] фиксирует: если через квартал >3 нарушений → CI gate. | A |
| **NEW-R1** | `require_docker_or_fail` в marker mode не меняет behavior — operator не видит эффекта | M | L | Логирование `[IMP:10][honesty] mode=marker` делает переход видимым в test output. После стабилизации W1 operator переключает mode=fail — эффект мгновенный. | C |
| **NEW-R2** | lib/args.sh с `declare -A` (assoc arrays) требует Bash 4.0 — на macOS default Bash 3.2 | L | M | Dev-окружение использует brew bash 5.x (AGENTS.md инвариант 7). CI — Ubuntu с Bash 5.x. Если найдётся caller на Bash 3.2 — fallback к case-statement (задокументировать в lib/args.sh). | D |
| **NEW-R3** | `yaml_query.py` CLI медленнее inline python3 (spawn overhead) на частых вызовах | L | L | Inline python3 тоже spawn'ит процесс — разницы нет. Для hot paths (циклы) — batch через Python API, не CLI. yaml_read.sh остаётся facade для bash-callers. | B |
| **NEW-R4** | gate_helpers.repo_root() кеширование ломается при chdir в test | L | L | `@functools.lru_cache` + resolution через `__file__` (не CWD) — устойчив к chdir. | D |
| **NEW-R5** | Refactor _load_yaml → gate_helpers.load_yaml ломает тесты, ожидающие exception на невалидном YAML | L | M | gate_helpers.load_yaml использует yaml.safe_load → кидает yaml.YAMLError (как и оригинал). Если тесты ловят конкретный тип — проверить после рефактора. | D |
| **NEW-R6** | 3 `_negative` пары импортируют functions из positive-тестов, которые не exported | M | L | Рефактор positive-тестов: вынести gate function в `tests/gates/_checkers/` или просто `from tests.gates.test_X import check_fn`. Если function inlined в test body — extract. | C |

---

## 10. Acceptance Criteria — Verifiable Commands

### A. AGENTS.md Policy Fixation

```bash
# AC-1: раздел существует
rg -c "## Языковая политика" AGENTS.md                    # → 1

# AC-2: двухуровневый триггер описан
rg -c "Tier 1.*немедленный" AGENTS.md                     # → ≥1
rg -c "Tier 2.*плановый" AGENTS.md                        # → ≥1

# AC-3: TRAP[DECISION] об enforcement
rg -c "TRAP\[DECISION\].*Enforcement.*языковой" AGENTS.md # → 1

# AC-4: pointer в core/AGENTS.md
rg "языковая политика" core/AGENTS.md                     # → ≥1 match
```

### B. Inline python3 Consolidation

```bash
# AC-5: yaml_query.py создан и имеет CLI
python3 core/internal/scripts/yaml_query.py --help        # → exit 0, prints help

# AC-6: unit-тесты проходят
python -m pytest tests/test_yaml_query.py -v              # → all PASSED

# AC-7: yaml_read.sh не содержит inline python3
rg -c "python3 -c" core/lib/yaml_read.sh                  # → 0

# AC-8: inline-python3-map создан
test -f reports/inline-python3-map-2026-07-21.csv         # → exit 0
[[ $(wc -l < reports/inline-python3-map-2026-07-21.csv) -gt 100 ]]  # → exit 0

# AC-9: pre-commit hook существует и executable
test -x core/internal/hooks/check-no-new-inline-python3.sh  # → exit 0

# AC-10: inline count не увеличился
[[ $(rg "python3 -c" core/ | wc -l) -le 105 ]]            # → exit 0
```

### C. Honesty-First

```bash
# AC-11: honesty.py создан
python3 -c "from _conftest.honesty import require_docker_or_fail; print('ok')"
# (этот inline python3 — легитимный, в whitelist через tests/)

# AC-12: marker mode тегирует skip
REQUIRE_HONESTY_MODE=marker python -m pytest tests/test_smoke_platform.py -s 2>&1 | rg "honesty"
# → хотя бы одна [IMP:10][honesty] mode=marker

# AC-13: fail mode превращает skip в fail
REQUIRE_HONESTY_MODE=fail python -m pytest tests/test_smoke_platform.py 2>&1 | rg "FAILED\|honesty"
# → хотя бы одна FAILED с [honesty:fail]

# AC-14: 3 _negative файла существуют
test -f tests/gates/test_gate_litellm_pg_enforcement_negative.py
test -f tests/gates/test_gate_module_schema_d4_negative.py
test -f tests/gates/test_gate_env_shared_consistency_negative.py

# AC-15: _negative тесты реально проходят (детектят нарушение)
python -m pytest tests/gates/test_*_negative.py -v        # → all PASSED
```

### D. Boilerplate Removal

```bash
# AC-16: gate_helpers.py создан
python3 -c "from tests.helpers.gate_helpers import load_yaml, repo_root, assert_ldd_imp9; print('ok')"

# AC-17: _load_yaml dedup
[[ $(rg "def _load_yaml" tests/ | wc -l) -eq 0 ]]         # → exit 0

# AC-18: PROJECT_ROOT dedup (≥10 файлов отрефакторено)
[[ $(rg "PROJECT_ROOT\s*=" tests/ | wc -l) -le 30 ]]      # → exit 0

# AC-19: lib/args.sh создан
test -f core/lib/args.sh                                  # → exit 0

# AC-20: usage() dedup
[[ $(rg "^usage\(\)" core/ | wc -l) -le 6 ]]              # → exit 0

# AC-21: verify-domains log_imp удалён
[[ $(rg "log_imp\(\)" core/internal/verify/verify-domains.sh | wc -l) -eq 0 ]]  # → exit 0
```

### E. Baseline Measurement

```bash
# AC-22: baseline metrics CSV создан
test -f reports/baseline-metrics-2026-07.csv              # → exit 0
rg "gate_fast_time_median" reports/baseline-metrics-2026-07.csv  # → ≥1 match (with value)
```

### Cross-cutting

```bash
# AC-23: make gate MODE=fast green
make gate MODE=fast                                       # → exit 0

# AC-24: ruff clean
ruff check core/internal/scripts/yaml_query.py tests/helpers/gate_helpers.py tests/_conftest/honesty.py tests/test_yaml_query.py
# → 0 errors

ruff format --check core/internal/scripts/yaml_query.py tests/helpers/gate_helpers.py tests/_conftest/honesty.py
# → 0 files would be reformatted

# AC-25: 3 _negative теста зарегистрированы в manifest
rg "gate-litellm-pg-enforcement-negative\|gate-module-schema-d4-negative\|gate-env-shared-consistency-negative" core/entrypoint-manifest.yaml
# → 3 matches
```

---

## 11. Не входит в этот DevPlan (Wave 1)

| Исключено | Причина | Куда идёт |
|-----------|---------|-----------|
| **Полное устранение 105 inline python3** | W1 scope — только yaml_read.sh консолидация + блокировка новых | Wave 4 (декомпозиция топ-3 скриптов), Wave 5 (bootstrap) |
| **Переключение REQUIRE_HONESTY_MODE=fail** | Поведенческое изменение CI — требует стабилизации W1 | Operator decision через 1-2 нед после W1 release |
| **lib/ssh.sh с timeouts** | Production-risk, затрагивает remote-пути | Wave 2 (W2-E1) |
| **CI composite action** | Затрагивает все workflows | Wave 2 (W2-E2) |
| **audit-trail на 7 entrypoints** | Затрагивает production entrypoints | Wave 2 (W2-E3) |
| **D5-контракт validate_module_yaml.py** | Module.yaml strengthening — отдельная волна | Wave 3 (W3-E1) |
| **Strangler-декомпозиция deploy-modules/converge/node-lifecycle** | Архитектурное изменение, high-risk | Wave 4 |
| **Makefile include-split** | Архитектурное изменение | Wave 4 (W4-E4) |
| **Рефактор оставшихся 6 файлов с usage()** | Не приоритет (entrypoints/scaffold — main pain) | Wave 4 при декомпозиции |
| **Полное устранение PROJECT_ROOT (57→1)** | W1 scope — top 10 файлов | Wave 4 при декомпозиции |
| **Property-based testing** | Откладывается | Post-Wave 5 (Decision Gate) |
| **Big-bang rewrite всех shell-строк** | Отклонён (Option A §4.1, score 3/10) | Never |

---

## 12. Оценка усилия

| Подволна | Шаги | Время | Исполнитель |
|----------|------|:----:|-------------|
| A (AGENTS.md policy) | Раздел + TRAP + pointer | 30 мин | Architect |
| B (inline python3) | yaml_query.py + тесты + yaml_read.sh + hook + map | 3-4 часа | Coder |
| C (honesty-first) | honesty.py + 15 файлов refactor + 3 _negative | 4-5 часов | Coder |
| D (boilerplate) | gate_helpers.py + 16 files refactor + lib/args.sh + 6 entrypoints + verify-domains | 4-5 часов | Coder |
| E (baseline) | Замеры + CSV | 30 мин | Sysadmin |
| QA | Gate verification + regression | 1-2 часа | QA |
| **Итого** | | **~14-17 часов** (~2-3 рабочих дня) | |

---

## 13. Definition of Done

- [ ] Все AC из §10 выполняются (25 verifiable commands)
- [ ] `make gate MODE=fast` — зелёный
- [ ] `make gate MODE=full` — зелёный за исключением известных macOS-overlay failures
- [ ] Обновлённый AGENTS.md закоммичен с разделом «Языковая политика» + TRAP[DECISION]
- [ ] `reports/inline-python3-map-2026-07-21.csv` и `reports/baseline-metrics-2026-07.csv` созданы
- [ ] Production-релиз: git tag `wave1-immediate` (или merge в main)
- [ ] VerificationReport `03-VerificationReport.md` создан (в этой же директории)
- [ ] Ссылка на DevPlan и VerificationReport добавлена в Brief 027 §3 (progress tracking)
- [ ] Operator уведомлён о готовности переключения REQUIRE_HONESTY_MODE=fail (через 1-2 нед)

---

$END_DEVPLAN

---

## Заключение

Wave 1 (Immediate) — нулевой risk, ощутимый профит:

- **Trust-киллер закрыт**: false-green → real-fail (после operator switch в Wave 2). 3 `_negative` пары закрывают R5-нарушения.
- **−400 строк бойлерплейта**: gate_helpers.py (6 копий _load_yaml + 10+ PROJECT_ROOT), lib/args.sh (6+ usage/parse_args), verify-domains log_imp dedup.
- **Языковая политика зафиксирована**: AGENTS.md «Языковая политика» с двухуровневым Strangler-триггером + pre-commit enforcement.
- **Inline python3 консолидация**: yaml_query.py (typed API + CLI) заменяет 6 inline-блоков в yaml_read.sh; pre-commit hook блокирует новые; map 105 вызовов отслеживает консолидацию в Waves 4-5.
- **Baseline-метрики**: CSV с замерами ДО старта Wave 2-5 для KPI tracking.

**Готов к передаче в dev-pipeline (Coder → QA → Fix) по команде оператора.**

**Следующий шаг:** operator подтверждает старт Wave 1 → Coder реализует подволны A→B→C/D/E → QA верифицирует AC → production-релиз.
