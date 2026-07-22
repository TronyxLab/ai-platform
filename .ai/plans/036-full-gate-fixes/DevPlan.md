# 036-DevPlan: Full Gate Error Remediation (W4-E4 Regression Fix)

**Source:** `make gate MODE=full` — 7/10 steps RED, 7 unique test failures + pre-commit lint errors
**Verified against codebase:** 2026-07-22 (diagnostic session, superposition collapsed)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить ВСЕ ошибки полного гейта (7 уникальных test failures + pre-commit ruff) без изменения архитектуры, без отключения тестов. Системный подход: root-cause → fix → verify per bug group.
DESCRIPTION:           W4-E4 Makefile include-split (747→41 LOC) породил regression cascade: контрактный тест .PHONY устарел (не читает makefiles/*.mk), compose test overlay содержит !override (yaml.safe_load incompatible), pgbouncer charset test не учитывает ${VAR:?...} fail-fast синтаксис, pre-commit lint накопился.
RATIONALE:             Красный гейт блокирует CI/CD pipeline. Все ошибки — regression после architectural change (W4-E4 include-split, D5 fail-fast vars). Zero architectural change: фиксы точечные — адаптация тестов и парсеров к новой структуре.
ACCEPTANCE_CRITERIA:
  **B0 (pre-commit):**
     1. ruff-check passes (0 errors)
     2. ruff-format passes (0 files reformatted)
     3. `make gate MODE=full` Step 1 green
  **B1 (contract .PHONY):**
     4. `test_every_manifest_target_has_makefile_entry` PASSES
     5. `test_every_makefile_target_has_manifest_entry` PASSES
     6. `_parse_phony_targets()` reads root Makefile + all makefiles/*.mk
  **B2 (loki test overlay):**
     7. `test_docker_compose_test_overlay` PASSES
     8. Test handles `!override` YAML tags (custom SafeLoader or docker compose config)
     9. Test expects `restart: "no"` (correct per AGENTS.md core/modules docker-compose.test.yml contract)
     10. MODULE_CONTRACT @invariants in docker-compose.test.yml corrected from "unless-stopped" to "no"
  **B3 (pgbouncer charset):**
     11. `test_pgbouncer_password_charset_constraint` PASSES
     12. Test recognizes `${POSTGRES_PASSWORD:?...}` fail-fast syntax as valid direct usage
     13. `POSTGRES_PASSWORD_ENCODED` still rejected (unchanged invariant)
  **B4 (clickhouse smoke):**
     14. `test_platform_starts_all_containers` PASSES or skip with documented reason
     15. clickhouse-test container starts within timeout
  **B5 (skip enforcement gate):**
     16. `test_executed_tests_greater_than_zero` PASSES (JUnit XML shows 0 failures after all fixes)
  **B6 (hardcoded target sets — verify):**
     17. `test_no_hardcoded_target_sets_in_gates` PASSES (verify current clean state)
  **B7 (restart consistency — verify):**
     18. `test_root_makefile_restart_is_soft` PASSES (verify current clean state)
IMPLEMENTS:            Targeted test + minor source fixes. 7 files modified, 0 architectural changes, 0 test disables.
IMPACTS:               **Modified:**
                         - `tests/contracts/test_make_target_contracts.py` (B1: multi-file .PHONY parsing)
                         - `tests/test_logging_static.py` (B2: !override + restart expectation)
                         - `core/modules/logging/docker-compose.test.yml` (B2: MODULE_CONTRACT fix)
                         - `tests/test_pgbouncer_static.py` (B3: charset pattern update)
                         - `.ai/plans/033-wave3-contract-d5/_fix_compose_profiles.py` (B0: ruff fixes)
                         - `core/internal/scripts/validate_module_yaml.py` (B0: ruff fixes)
                         - `tests/gates/test_gate_module_yaml_contract_d5_negative.py` (B0: ruff fixes)
                         - `tests/test_validate_module_yaml.py` (B0: ruff fixes)
                         - `tests/gates/test_gate_ci_coverage.py` (B0: ruff fixes)
                         - `tests/gates/test_gate_compose_restart_consistency.py` (B0: ruff format)
                         - `tests/gates/test_gate_module_yaml_contract.py` (B0: ruff format)
                       **Diagnostic-only (B4):**
                         - Clickhouse test timeout/config investigation
REQUIRES:              Чистый working tree. Python 3.10+ in .venv. Docker daemon (for smoke tests). GNU Make.
TASK_SIZE:             MEDIUM (7 bug groups, ~150 LOC changes, diagnostic for B4)
CRITICALITY:           HIGH — gate blocks CI/CD
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL SUPERPOSITION: полный анализ каждого бага с 3-5 гипотезами, коллапс к рекомендованному fix => GOAL_SUPERPOSITION
- GOAL B0: ruff-check + ruff-format исправление (pre-commit gate) => GOAL_B0_PRECOMMIT
- GOAL B1: адаптация _parse_phony_targets к include-split (makefiles/*.mk) => GOAL_B1_CONTRACT
- GOAL B2: исправление loki test overlay на !override-compatible YAML + restart=no => GOAL_B2_LOKI
- GOAL B3: обновление pgbouncer charset теста под ${VAR:?...} синтаксис => GOAL_B3_PGBOUNCER
- GOAL B4: диагностика clickhouse smoke test (container not running) => GOAL_B4_CLICKHOUSE
- GOAL B5: верификация чистых regression (hardcoded sets, restart) => GOAL_B5_VERIFY
- GOAL ROOT_CAUSE: архитектурная root-cause analysis — почему W4-E4 породил эти regression => GOAL_ROOT_CAUSE
**SECTION_USE_CASES:**
- USE_CASE `make gate MODE=full` → ALL STEPS PASS => UC_GATE_GREEN
- USE_CASE `make gate MODE=fast` → ALL STEPS PASS => UC_FAST_GREEN
- USE_CASE CI deploy pipeline → gate green → deploy proceeds => UC_CI_GREEN
$END_DOCUMENT_PLAN
```

---

## 1. Superposition Analysis

### Архитектурный root-cause

W4-E4 Makefile include-split (commit `400f048`) переместил все `.PHONY:` декларации и target definitions из корневого Makefile (747→41 LOC) в `makefiles/*.mk`. Это — архитектурно корректное решение (разделение ответственности, navigability, merge conflict reduction). Однако 3 контрактных теста не были обновлены под новую структуру, и накопились lint-дефекты.

**Chain of causation:**
```
W4-E4 include-split
  ├→ Root Makefile: нет .PHONY: → B1 (contract test fails)
  ├→ restart target ушёл в modules.mk → extract_make_target адаптирован, PASSES ✓
  ├→ .PHONY в makefiles/ → hardcoded target сканер не затронут ✓
  └→ Pre-commit lint накопился между волнами → B0
Независимые regression:
  ├→ B2: DevPlan 033 F-7 rollout добавил !override в docker-compose.test.yml (YAML parse break)
  ├→ B3: DevPlan 033 W3-E3 добавил ${VAR:?...} fail-fast → substring mismatch
  └→ B4: clickhouse smoke test — env-specific (Docker Desktop macOS timing)
```

---

### B0: Pre-commit ruff failures

#### FULL Superposition: Как исправить 17 ruff ошибок + 7 неформатированных файлов?

**Option A: `ruff check --fix . && ruff format .` [score: 10/10]**
Approach: Автоматическое исправление всех auto-fixable ошибок.
Trade-offs: Fast, zero-risk, канонический подход (`_project.md`: "ruff format . && ruff check --fix .").
Best when: 14 из 17 ошибок помечены `[*]` (auto-fixable). Оставшиеся 3 — manual fix.

**Option B: Ручное исправление каждого файла [score: 6/10]**
Approach: Построчное редактирование каждого lint violation.
Trade-offs: Контроль над каждым изменением, но избыточно для auto-fixable ошибок.
Best when: Нужен audit каждого изменения отдельно.

**Option C: Исключить файлы из lint (per-file-ignores) [score: 2/10]**
Approach: Добавить исключения в pyproject.toml.
Trade-offs: Нарушает §TESTING Honesty Rules (скрытие проблем).
Best when: Never — явно запрещено (R1: NO pass-tests).

**Option D: Удалить старые план-файлы [score: 4/10]**
Approach: Удалить `.ai/plans/033-wave3-contract-d5/_fix_compose_profiles.py` (исторический fixup-скрипт).
Trade-offs: Чистота, но потеря audit trail.
Best when: Файл больше не нужен для воспроизводимости.

**Recommendation: Option A** — `ruff check --fix . && ruff format .` + manual fix 3 non-auto-fixable errors (PERF203, SIM102, PERF401).

**Collapse signal:** A — автофикс + 3 ручных правки. Файл `_fix_compose_profiles.py` оставить (исторический артефакт, не удалять).

---

### B1: Contract .PHONY test (test_every_manifest_target_has_makefile_entry)

#### FULL Superposition: Как адаптировать _parse_phony_targets?

**Option A: Расширить парсер на makefiles/*.mk [score: 9/10]**
Approach: `_parse_phony_targets()` читает root Makefile + все `makefiles/*.mk`. Использовать ту же логику, что уже в `_collect_all_phony_targets()` из `test_gate_makefile_targets.py`.
Trade-offs: Минимальное изменение (5-7 строк), reuse проверенного кода.
Best when: Инвариант "Makefile — единая точка входа" сохраняется.

**Option B: Добавить .PHONY обратно в root Makefile [score: 4/10]**
Approach: Сгенерировать единую строку `.PHONY:` со всеми target в корневом Makefile.
Trade-offs: Контринтуитивно — откат W4-E4 split, увеличивает root Makefile.
Best when: Никогда — нарушает W4-E4 архитектурное решение.

**Option C: Изменить тест на проверку make -n dry-run [score: 5/10]**
Approach: Вместо парсинга .PHONY запускать `make -n <target>` для каждого manifest target.
Trade-offs: Надёжнее (проверяет реальную работоспособность), но требует GNU Make и медленнее.
Best when: Нужна полная уверенность в resolvability.

**Option D: Символическая .PHONY в корневом Makefile [score: 3/10]**
Approach: Добавить `.PHONY: $(ALL_TARGETS)` с переменной из include.
Trade-offs: Make не поддерживает такую индирекцию для .PHONY.
Best when: Never — технически невозможно.

**Recommendation: Option A** — расширить `_parse_phony_targets()` на `makefiles/*.mk`, mirroring `_collect_all_phony_targets()`.

**Collapse signal:** A — минимальное, архитектурно-корректное расширение парсера.

---

### B2: Loki test overlay (test_docker_compose_test_overlay)

#### ADVERSARIAL Analysis: Две независимые проблемы в одном тесте

**Проблема 2a: YAML `!override` tag incompatibility**

| Case | Argument | Counter |
|------|----------|---------|
| **A: Custom SafeLoader** | Регистрировать `!override` конструктор в `yaml.SafeLoader` (игнорировать — return value as-is, compose-specific tag). Минимальное изменение в test_logging_static.py. | Добавляет технический долг — compose-specific YAML extension в тестовом коде. |
| **B: docker compose config** | Использовать `docker compose -f file config` для парсинга YAML (как делают другие тесты). Корректно обрабатывает compose extensions. | Требует Docker daemon, медленнее, не pure-static. |
| **C: Preprocess YAML** | Удалить `!override` теги regex'ом перед yaml.safe_load. | Хрупко, не обрабатывает вложенные !override. |
| **D: Убрать !override из YAML** | Вернуться к pre-F-7 синтаксису (без !override, полное переопределение). | Ломает test network isolation (DevPlan 017 W2.5). |

**Decision: Option A** — Custom SafeLoader. All other tests use `yaml.safe_load` too, and `!override` is harmless compose-extension (merge-override, not security-relevant). Minimal change: register constructor that returns the tagged value as-is.

**Проблема 2b: restart expectation mismatch (no vs unless-stopped)**

| Criterion | Option A: Fix test to expect "no" | Option B: Fix YAML to "unless-stopped" |
|-----------|-----------------------------------|---------------------------------------|
| AGENTS.md contract | ✅ Matches | ❌ Violates |
| Speed | ✅ 1-line change | ✅ 2-line change |
| Safety | ✅ Correct semantics | ❌ Test containers auto-restarting in CI |

**Decision: Option A** — Test expects `restart: "no"` (AGENTS.md compliant). Also fix MODULE_CONTRACT @invariants header in docker-compose.test.yml (says "unless-stopped" incorrectly).

---

### B3: Pgbouncer charset (test_pgbouncer_password_charset_constraint)

#### GUIDED Approach: Update substring check for ${VAR:?...} syntax

**Root cause:** Test checks `"${POSTGRES_PASSWORD}" in compose_text` but compose uses `${POSTGRES_PASSWORD:?PG_PASSWORD_REQUIRED}`. The closing `}` in `:?...}` is AFTER `PG_PASSWORD_REQUIRED`, not immediately after `PASSWORD`. Therefore `${POSTGRES_PASSWORD}` (with `}` right after `PASSWORD`) is NOT a substring of `${POSTGRES_PASSWORD:?...}`.

**Recommended fix:** Use regex `r'\$\{POSTGRES_PASSWORD[\}:]'` — matches `${POSTGRES_PASSWORD}` followed by either `}` (simple reference) or `:` (fail-fast/default variant). This recognizes all valid compose variable references to POSTGRES_PASSWORD.

**Also considered:** 
- Escape-curly approach `${POSTGRES_PASSWORD:?...}` — rejected (still fragile, hardcodes fallback message).
- Read from docker compose config — rejected (need Docker, overkill for static test).
- Check `"${POSTGRES_PASSWORD"` only — rejected (matches substrings like `${POSTGRES_PASSWORD_ENCODED}` if it existed, less precise). Wait — actually `"${POSTGRES_PASSWORD"` would ALSO match `${POSTGRES_PASSWORD_ENCODED}` which the test explicitly rejects. So regex is safer.

**Proceeding with:** `re.search(r'\$\{POSTGRES_PASSWORD[\}:]', compose_text)` — matches all valid POSTGRES_PASSWORD references.

---

### B4: Clickhouse smoke test (test_platform_starts_all_containers)

#### FULL Superposition: Почему clickhouse-test не запускается?

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| H1 | **Таймаут 50s недостаточен для clickhouse на macOS** | **70%** | Clickhouse cold-start на Docker Desktop macOS занимает >60s (JVM-like init). |
| H2 | Clickhouse healthcheck не проходит (container running но unhealthy) | 15% | Тест ждёт healthy status, clickhouse долго инициализирует storage. |
| H3 | Docker resource limits (memory/CPU) на macOS | 10% | Docker Desktop default 2GB — clickhouse в составе 13-модульного стека может не хватать памяти. |
| H4 | Compose profile или network issue | 3% | Clickhouse не включается в COMPOSE_PROFILES или network conflict. |
| H5 | Баг в docker-compose.test.yml clickhouse | 2% | Test overlay некорректен. |

**Diagnostic plan (перед фиксом):**
1. Проверить `docker compose ps -a | grep clickhouse` после smoke test
2. Проверить `docker logs clickhouse-test` для ошибок
3. Проверить resource usage: `docker stats --no-stream`
4. Проверить healthcheck: `docker inspect clickhouse-test`

**Варианты фикса в зависимости от диагностики:**

| Option | When | Change |
|--------|------|--------|
| **A: Увеличить таймаут** | H1 подтверждён | `timeout=50` → `timeout=120` в тесте |
| **B: Skip на macOS** | H1+H2 — платформенное ограничение | `pytest.skip("ClickHouse cold-start >60s on macOS Docker Desktop")` |
| **C: Pre-warm clickhouse** | H2 — healthcheck timeout | Добавить explicit wait/polling в тест |
| **D: Увеличить Docker resources** | H3 | Документировать требование (не код) |

**Recommendation:** Diagnostic-first. Запустить `make gate MODE=full` с мониторингом clickhouse, принять решение на основе логов.

---

### B5: Skip enforcement gate (cascading)

**Diagnosis:** `test_executed_tests_greater_than_zero` читает `tests/report.xml` (merged JUnit). Показывает 7 failures — сумма всех failures выше. После исправления B0-B4 этот тест автоматически станет зелёным.

**No fix needed** — cascading PASS after B0-B4 fixes.

---

## 2. Implementation Plan (Wave Sequence)

### Wave 1: Pre-commit cleanup (B0) — `make gate` Step 1

| Task | File | Change | Risk |
|------|------|--------|------|
| T1.1 | `.ai/plans/033-wave3-contract-d5/_fix_compose_profiles.py` | `ruff check --fix` + `ruff format` | NONE |
| T1.2 | `core/internal/scripts/validate_module_yaml.py` | `ruff check --fix` + manual fix PERF203 (try-in-loop), SIM102 (nested if) | LOW |
| T1.3 | `tests/gates/test_gate_ci_coverage.py` | `ruff check --fix` (PERF401) + `ruff format` | NONE |
| T1.4 | `tests/gates/test_gate_module_yaml_contract_d5_negative.py` | `ruff check --fix` (I001, F401, F541) + `ruff format` | NONE |
| T1.5 | `tests/test_validate_module_yaml.py` | `ruff check --fix` (I001, F541, UP032) + `ruff format` | NONE |
| T1.6 | `tests/gates/test_gate_compose_restart_consistency.py` | `ruff format` | NONE |
| T1.7 | `tests/gates/test_gate_module_yaml_contract.py` | `ruff format` | NONE |
| T1.8 | ALL | Verify: `ruff check . && ruff format --check .` → 0 errors | — |

### Wave 2: Contract .PHONY test (B1) — Step 6/7

| Task | File | Change | Risk |
|------|------|--------|------|
| T2.1 | `tests/contracts/test_make_target_contracts.py` | `_parse_phony_targets()`: after root Makefile, also scan `makefiles/*.mk` | LOW |

### Wave 3: Loki test overlay (B2) — Step 7

| Task | File | Change | Risk |
|------|------|--------|------|
| T3.1 | `tests/test_logging_static.py` | Add custom `!override` constructor to SafeLoader before `yaml.safe_load` | LOW |
| T3.2 | `tests/test_logging_static.py` | Fix restart expectation: `"unless-stopped"` → `"no"` | NONE |
| T3.3 | `core/modules/logging/docker-compose.test.yml` | Fix MODULE_CONTRACT @invariants: `unless-stopped` → `no` | NONE |

### Wave 4: Pgbouncer charset (B3) — Step 7

| Task | File | Change | Risk |
|------|------|--------|------|
| T4.1 | `tests/test_pgbouncer_static.py` | Replace `"${POSTGRES_PASSWORD}" in compose_text` with regex `re.search(r'\$\{POSTGRES_PASSWORD[\}:]', compose_text)` | LOW |

### Wave 5: Clickhouse smoke (B4) — Step 9

| Task | File | Change | Risk |
|------|------|--------|------|
| T5.1 | Diagnostic | Run smoke test, capture `docker logs clickhouse-test`, `docker stats` | — |
| T5.2 | `tests/test_smoke_platform.py` | Apply fix per diagnostic result (timeout increase OR skip for macOS) | MEDIUM |

### Wave 6: Verification (B5 + B6 + B7) — Final gate

| Task | Command | Expected |
|------|---------|----------|
| T6.1 | `make gate MODE=fast` | ALL STEPS PASS |
| T6.2 | `make gate MODE=full` | ALL STEPS PASS |
| T6.3 | `ruff check . && ruff format --check .` | 0 errors |

---

## 3. File Manifest

| File | Action | Wave | Risk |
|------|--------|------|------|
| `.ai/plans/033-wave3-contract-d5/_fix_compose_profiles.py` | ruff fix + format | W1 | NONE |
| `core/internal/scripts/validate_module_yaml.py` | ruff fix + manual | W1 | LOW |
| `tests/gates/test_gate_ci_coverage.py` | ruff fix + format | W1 | NONE |
| `tests/gates/test_gate_compose_restart_consistency.py` | ruff format | W1 | NONE |
| `tests/gates/test_gate_module_yaml_contract.py` | ruff format | W1 | NONE |
| `tests/gates/test_gate_module_yaml_contract_d5_negative.py` | ruff fix + format | W1 | NONE |
| `tests/test_validate_module_yaml.py` | ruff fix + format | W1 | NONE |
| `tests/contracts/test_make_target_contracts.py` | extend _parse_phony_targets | W2 | LOW |
| `tests/test_logging_static.py` | !override handler + restart fix | W3 | LOW |
| `core/modules/logging/docker-compose.test.yml` | MODULE_CONTRACT fix | W3 | NONE |
| `tests/test_pgbouncer_static.py` | regex update | W4 | LOW |
| `tests/test_smoke_platform.py` | clickhouse diagnostic → fix | W5 | MEDIUM |

---

## 4. Rollback Plan

Каждое изменение — точечное и обратимое:

| Wave | Rollback |
|------|----------|
| W1 | `git checkout -- <files>` + восстановление pyproject.toml |
| W2 | `git checkout -- tests/contracts/test_make_target_contracts.py` |
| W3 | `git checkout -- tests/test_logging_static.py core/modules/logging/docker-compose.test.yml` |
| W4 | `git checkout -- tests/test_pgbouncer_static.py` |
| W5 | `git checkout -- tests/test_smoke_platform.py` |

---

## 5. Open Questions (для collapsing перед реализацией)

1. **B0:** Удалить ли `_fix_compose_profiles.py` (исторический fixup-скрипт из DevPlan 033) или оставить для audit trail? → Предложение: оставить, просто ruff-fix.

2. **B4:** Перед фиксом нужна диагностика clickhouse. Предлагаю: сначала Waves 1-4 (все статические фиксы), потом `make gate MODE=full` с захватом полных логов clickhouse, затем решение по Wave 5.

3. **B0 PERF203 (try-in-loop):** В `validate_module_yaml.py:277` — `try/except ValueError` внутри цикла по `env_requires`. Это business-logic валидатор, а не performance-critical код. Предлагаю заглушить предупреждение `# noqa: PERF203` вместо рефакторинга — соответствует принципу Small Simple Blocks (не переусложнять ради lint).

4. **B0 SIM102 (nested if):** `if req_type == "secret": if not _env_var_in_secrets_manifest(...)` → объединить в `if req_type == "secret" and not _env_var_in_secrets_manifest(...)` — тривиально, предлагаю исправить.

$END_DEVPLAN
