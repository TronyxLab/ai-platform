# GREP_SUMMARY: DevPlan 062 CI drift diagnostic superposition retry hardcoded-paths checkout-order gate-coverage
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ superposition (6 bugs, 5 options each) → ⊕ unfixed-delta (10 findings) → ⚡ remediation plan → ⎋ verification

$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:               Диагностика CI-дрифта после рефакторинга 2026-07-23 — системный поиск аналогичных неисправленных багов + суперпозиция по каждому исправленному багу.
DESCRIPTION:           Двухфазный анализ: (1) ретроспективная суперпозиция по 6 исправленным багам (какой альтернативный подход мог быть выбран и почему выбранный — оптимален), (2) проактивный поиск 10 аналогичных неисправленных паттернов в кодовой базе (hardcoded paths вне tests/, inline python3 в CI actions, requests без retry, gate coverage gaps).
RATIONALE:             Предотвращение повторения однотипных багов через системный анализ. Gate-тесты G1/G2 предотвращают рецидив исправленных багов, но остаются 10 unfixed delta, не покрытых ни одним gate.
ACCEPTANCE_CRITERIA:   (1) 6 superposition analyses с ≥5 вариантами каждый, (2) 10 unfixed findings с severity classification, (3) remediation plan для P0/P1, (4) DevPlan проходит `make check-manifests`.
IMPLEMENTS:            Post-mortem CI incident 2026-07-23 (P0-1..P0-4, P1-5, P2-6)
IMPACTS:               tests/gates/*, tests/test_smoke_langfuse.py, tests/test_platform_endpoints.py, .github/actions/discover-modules/action.yml, core/internal/bootstrap/deploy/compose_preflight.py
REQUIRES:              git checkout main (базовый коммит до исправлений), доступ к CI-логам за 2026-07-23
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

**SECTION_GOALS:**
- GOAL 1: Полная суперпозиция по каждому из 6 исправленных багов — анализ альтернатив → GOAL_SP1
- GOAL 2: Системный поиск неисправленных аналогичных паттернов → GOAL_UF1
- GOAL 3: Classification unfixed findings по severity (P0/P1/P2) → GOAL_CL1
- GOAL 4: Remediation plan с конкретными файлами и изменениями → GOAL_RP1
- GOAL 5: Оценка gate coverage gap (что не покрыто существующими gate-тестами) → GOAL_GC1

**SECTION_USE_CASES:**
- USE_CASE 1: Разработчик добавляет новый тест → должен знать о gate G1 (hardcoded paths) и паттерне repo_root() → UC_NEW_TEST
- USE_CASE 2: Разработчик добавляет новый CI workflow → должен знать о gate G2 (checkout order) → UC_NEW_WF
- USE_CASE 3: Разработчик добавляет HTTP-запрос в тест → должен знать о необходимости retry-логики → UC_NEW_HTTP_TEST
- USE_CASE 4: DevOps engineer модифицирует composite action → должен знать о запрете inline python3 → UC_NEW_ACTION
$END_DOCUMENT_PLAN

---

## Часть 1: Ретроспективная суперпозиция исправленных багов

### P0-1: Hardcoded macOS path в test_component_hermes.py:66

**Symptom:** `'unable to prepare context: path "/Users/tronyx/projects/ai-platform" not found'` — все 7 hermes-тестов падают на Linux CI.

**Root:** `_PLATFORM_ROOT: str = os.environ.get("PLATFORM_ROOT", "/Users/tronyx/projects/ai-platform")` — macOS-specific fallback.

## SUPERPOSITION: Hardcoded path fix — 5 альтернатив

### Option A: Auto-detect через `__file__` [score: 9/10] ✅ ВЫБРАНО
**Approach:** `os.environ.get("PLATFORM_ROOT", str(repo_root()))` — `repo_root()` = `Path(__file__).resolve().parent.parent.parent` (2 уровня вверх: tests/helpers/ → tests/ → repo_root).
**Trade-offs:** +Zero-config, работает на любой OS, любой рабочей директории. −Зависимость от `tests.helpers.gate_helpers.repo_root` (добавляет import).
**Best when:** Тесты запускаются из клона репозитория (всегда так и есть).

### Option B: CI-переменная `PLATFORM_ROOT` на уровне workflow [score: 6/10]
**Approach:** `env: PLATFORM_ROOT: ${{ github.workspace }}` в platform-test.yml.
**Trade-offs:** +Просто, не меняет код. −Хрупкое: если другой CI (GitLab, локальный запуск) — переменная не установлена, fallback не поможет. −Не решает локальную разработку.

### Option C: `os.getcwd()` [score: 4/10]
**Approach:** `os.environ.get("PLATFORM_ROOT", os.getcwd())`.
**Trade-offs:** +Просто, без импортов. −Ломается если `pytest` запущен из поддиректории (`cd tests/ && pytest`). −Нестабильно.

### Option D: `pathlib.Path.home() / "projects" / "ai-platform"` [score: 2/10]
**Approach:** Жёсткая привязка к структуре `~/projects/ai-platform`.
**Trade-offs:** +Работает на macOS/Linux с одинаковой структурой. −Ломается на CI (runner home = `/home/runner/work/...`). −Не портабельно.

### Option E: Полное удаление `PLATFORM_ROOT` — build context через `context: ..` в compose [score: 5/10]
**Approach:** Изменить `docker-compose.base.yml` — `build.context: ../../..` вместо `${PLATFORM_ROOT}`.
**Trade-offs:** +Убирает переменную из Python-кода полностью. −Требует изменений в compose-файлах (шире скоуп). −Может сломать production deploy где PLATFORM_ROOT=/opt/platform.

**Collapse signal:** Выбран Option A. Gate G1 предотвращает рецидив.

---

### P0-2/P0-3/P0-4: Local composite action до checkout в трёх workflow

**Symptom:** `Can't find 'action.yml' under .../.github/actions/sha-resolve` — три workflow падают: core-deploy, build-platform, mirror.

**Root:** `uses: ./.github/actions/sha-resolve` выполнялся ДО `actions/checkout` — локальные composite actions требуют checkout'нутого репозитория.

## SUPERPOSITION: Checkout order fix — 5 альтернатив

### Option A: Переместить `actions/checkout` перед local actions [score: 9/10] ✅ ВЫБРАНО
**Approach:** Во всех трёх workflow: step 1 = checkout, step 2+ = local actions.
**Trade-offs:** +Минимальное изменение, очевидный порядок. −Требует ручной проверки каждого workflow.
**Best when:** Всегда правильное решение для локальных composite actions.

### Option B: `actions/checkout` как dependency в самом composite action [score: 3/10]
**Approach:** Каждый `.github/actions/*/action.yml` вызывает `actions/checkout` внутри себя.
**Trade-offs:** +Workflow не обязан помнить о checkout. −Множественный checkout (N composite actions = N checkout'ов). −Замедляет CI (дублирование checkout).
**Best when:** Composite actions используются в изоляции, без общего рабочего дерева.

### Option C: Использовать Docker container actions вместо composite [score: 4/10]
**Approach:** Переписать `sha-resolve` как `docker://...` action (не требует checkout).
**Trade-offs:** +Не зависит от checkout. −Требует сборки Docker-образа для action. −Overhead: образ в registry, медленнее запуск.
**Best when:** Action используется в десятках репозиториев.

### Option D: `actions/checkout` на уровне workflow defaults [score: 5/10]
**Approach:** `defaults.run.working-directory` или нестандартный `pre steps` (нет в GitHub Actions).
**Trade-offs:** +Централизованно. −GitHub Actions не поддерживает «pre steps» — только `jobs.<job_id>.steps`.
**Best when:** GitHub добавит `pre_steps` в API.

### Option E: Gate-only — не фиксить workflow, только предотвратить в будущем [score: 2/10]
**Approach:** Оставить баг в CI, полагаясь на то что gate G2 предотвратит новые.
**Trade-offs:** +Ноль изменений в production CI. −CI остаётся сломанным. −Gate не чинит существующие баги.
**Best when:** (Никогда — CI должен работать сейчас.)

**Collapse signal:** Выбран Option A. Gate G2 предотвращает рецидив во всех workflow.

---

### P0-5: LiteLLM crash-restart цикл — ConnectionResetError(104)

**Symptom:** `requests.get(http://localhost:14000/health/readiness)` → `ConnectionResetError(104)`. LiteLLM крашится на Application startup, `restart: unless-stopped` восстанавливает контейнер, первый запрос попадает в окно перезапуска.

## SUPERPOSITION: LiteLLM retry fix — 5 альтернатив

### Option A: Retry с exponential backoff (1s/2s) на уровне теста [score: 8/10] ✅ ВЫБРАНО
**Approach:** `for attempt in range(3): try/except RequestException: time.sleep(2**attempt); continue`.
**Trade-offs:** +Не меняет инфраструктуру. +Прозрачно для других тестов. −Маскирует root cause (DNS-alias коллизия, Dep 017).
**Best when:** Crash-restart — известный transient failure, root cause требует отдельного расследования.

### Option B: Увеличить `--wait-timeout` в docker compose [score: 4/10]
**Approach:** `docker compose up --wait --wait-timeout 180` (вместо 60).
**Trade-offs:** +Ждёт пока контейнер стабилизируется ДО теста. −Замедляет CI на 2 минуты. −LiteLLM может крашнуться ПОСЛЕ --wait (после healthcheck, но до стабилизации model_list).
**Best when:** Startup медленный, но стабильный.

### Option C: Docker healthcheck retry в compose-файле [score: 6/10]
**Approach:** `healthcheck: retries: 5, start_period: 30s` — compose сам ждёт полной стабилизации.
**Trade-offs:** +Решает проблему на уровне инфраструктуры. −Увеличивает startup-time для всех (не только CI). −start_period игнорируется без `start_interval`.
**Best when:** Контейнер всегда крашится на старте, а не иногда.

### Option D: `depends_on: postgres-healthy` + `condition: service_healthy` [score: 5/10]
**Approach:** LiteLLM ждёт postgres healthy перед стартом.
**Trade-offs:** +Предотвращает гонку startup'ов. −Уже есть `depends_on` — проблема не в postgres. −Не устраняет httpx.ConnectError к модели.
**Best when:** Проблема — гонка с зависимым сервисом.

### Option E: Tenacity library retry decorator [score: 7/10]
**Approach:** `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1))` на уровне функции.
**Trade-offs:** +Декларативно, стандартная библиотека. −Добавляет зависимость (tenacity не в requirements). −Избыточно для 1 теста.
**Best when:** Много тестов с retry-логикой (тогда оправдывает dependency).

**Collapse signal:** Выбран Option A — минимальное изменение, не маскирует root cause (логируется attempt count для мониторинга частоты).

---

### P2-6: Nginx default 404 вместо кастомной error-page

**Symptom:** `test_nginx_error_page` — `assert "Page Not Found" in r.text` падает. CI возвращает дефолтный nginx 404 вместо styled 404.html из `error-pages/`.

## SUPERPOSITION: Nginx 404 diagnostic fix — 5 альтернатив

### Option A: Diagnostic warning + сохранение assert [score: 8/10] ✅ ВЫБРАНО
**Approach:** Перед assert'ом — `logger.warning()` с полным телом ответа (первые 500 символов). Assert остаётся.
**Trade-offs:** +Диагностика без изменения поведения. +В CI видно ЧТО вернул nginx. −Не чинит root cause.
**Best when:** Root cause неизвестен — diagnostic помогает понять.

### Option B: Автоматическое копирование error-pages в volume [score: 6/10]
**Approach:** В фикстуре nginx_compose: `cp -r core/modules/nginx/error-pages/ /var/lib/platform/nginx/error-pages/`.
**Trade-offs:** +Гарантирует наличие файлов. −Хрупкое (пути). −Не решает проблему если конфиг nginx неправильный.
**Best when:** Проблема — отсутствие файлов в volume.

### Option C: Проверка конфига nginx на наличие `error_page` директивы [score: 7/10]
**Approach:** `docker exec nginx-test nginx -T | grep error_page` — проверяет что конфиг содержит директиву.
**Trade-offs:** +Диагностика на уровне конфига. −Не проверяет пути к файлам. −Требует docker exec.
**Best when:** Подозрение на неправильный конфиг.

### Option D: `pytest.skip` если default 404 [score: 2/10]
**Approach:** `if "Page Not Found" not in r.text: pytest.skip(...)`.
**Trade-offs:** +Тест не падает. −Маскирует баг. −Нарушает R4 (skip-as-fail).
**Best when:** (Никогда — R4 violation.)

### Option E: Использовать `curl` вместо `requests` для диагностики [score: 5/10]
**Approach:** `subprocess.run(["curl", "-v", "http://127.0.0.1:18080/404.html"])` — verbose вывод.
**Trade-offs:** +Полный HTTP-трейс (заголовки, редиректы). −Менее читаемый в pytest-выводе. −Зависимость от curl в контейнере/CI.
**Best when:** Нужно видеть HTTP-заголовки и редиректы.

**Collapse signal:** Выбран Option A — diagnostic-first approach. Root cause (nginx config после cert refactoring ee4d5f5) требует отдельного расследования.

---

### G1/G2: Prevention gates

Оба gate-теста — best practice prevention pattern. Альтернативы не требуют суперпозиции (стандартная практика: «нашли баг → написали тест который его ловит»).

---

## Часть 2: Unfixed Delta — 10 неисправленных аналогичных паттернов

### Findings Table

| # | Severity | File | Line | Finding | Root |
|---|----------|------|------|---------|------|
| **UF1** | **P1** | `.github/actions/discover-modules/action.yml` | 36 | Inline `python3 -c "import json,sys;..."` | Language policy violation (AGENTS.md §Языковая политика п.3) |
| **UF2** | **P2** | `core/internal/bootstrap/deploy/compose_preflight.py` | 45 | `_MANIFEST_DEFAULT = "/opt/platform/core/secrets-manifest.yaml"` без env-var fallback | Hardcoded server path без `os.environ.get("PLATFORM_ROOT", ...)` |
| **UF3** | **P1** ⚠️↑ | `tests/test_smoke_langfuse.py` | 62,77,96,124,126 | `requests.get/post` без retry И без exception handling — crash на любом transient error | Transient failure, хуже UF4-UF8: ни retry, ни try/except (VerificationReport F3) |
| **UF4** | **P1** ⚠️↑ | `tests/test_smoke_monitoring.py` | 348,385,423 | `requests.get` без retry, hard fail без recovery | Transient failure, хуже UF5-UF8: `except RequestException → pytest.fail()` без retry (VerificationReport F2) |
| **UF5** | **P2** | `tests/test_platform_endpoints.py` | 89,135,161,197,269 | `requests.get` без retry | Transient failure |
| **UF6** | **P2** | `tests/test_smoke_hermes.py` | 91,141,196 | `requests.get/post` без retry | Transient failure |
| **UF7** | **P2** | `tests/test_e2e_health.py` | 97+ | `requests.get` без retry | Transient failure |
| **UF8** | **P2** | `tests/test_e2e_langfuse.py` | ~50-100 | `requests.get` без retry | Transient failure |
| **UF9** | **P2** | `tests/gates/test_gate_no_hardcoded_local_paths.py` | 30-31 | Gate сканирует только `tests/` — не `core/` | Coverage gap: хардкод `/opt/platform` в core/ не детектится |
| **UF10** | **P1** | (новый gate) | — | Нет gate для `requests.*` без retry/timeout | Prevention gap: ничто не блокирует merge тестов без retry |

---

### Remediation Priority

#### Wave 1: P1 (блокирующие для CI стабильности) — 4 fixes

**UF1 — Inline python3 в discover-modules action**

Файл: `.github/actions/discover-modules/action.yml:36`
Текущий код:
```bash
COUNT=$(python3 core/internal/scripts/module_discovery.py --format json | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
```

Fix: Вынести `python3 -c` в отдельный Python-скрипт или использовать `--count` флаг в `module_discovery.py`:
```bash
COUNT=$(python3 core/internal/scripts/module_discovery.py --count)
```

`module_discovery.py` уже имеет `--format json` — добавить `--count` флаг (возвращает только число).

⚠️ VerificationReport F1: `action.yml:1` GREP_SUMMARY заявляет `zero-inline-python3`, но строка 36 содержит inline python3 — документационный дрифт. При фиксе обновить и GREP_SUMMARY.

**UF10 — Gate: no requests without retry**

Новый gate `tests/gates/test_gate_http_retry_policy.py`:
- Сканирует все `tests/test_*.py` на `requests.get(` / `requests.post(`
- Проверяет что вызов обёрнут в `for attempt in range(...)` ИЛИ в `try/except RequestException` с retry
- Allowlist: файлы которые используют `_handle_e2e_error` (уже имеет retry-логику)

**UF3 — langfuse smoke без retry (no exception handling)**

Файл: `tests/test_smoke_langfuse.py` (lines 62, 77, 96, 124, 126)
⚠️ VerificationReport F3: 5 вызовов `requests.get/post` **вообще без обработки исключений** — ни retry, ни try/except. Любой `ConnectionError` → необработанное исключение → crash.
Фикс: обернуть все HTTP-вызовы в retry-цикл (паттерн из F5 — см. ниже), добавить `_handle_e2e_error()` или отдельный `try/except RequestException`.

**UF4 — monitoring smoke без retry (hard fail)**

Файл: `tests/test_smoke_monitoring.py` (lines 348, 385, 423)
⚠️ VerificationReport F2: `except RequestException: pytest.fail()` — HARD FAIL без retry, хуже чем UF5-UF8 где `_handle_e2e_error` хотя бы делает skip для timeout.
Фикс: заменить `pytest.fail()` на retry-цикл (паттерн из F5 — см. ниже), сохранить `pytest.fail` только для последней попытки.

#### Wave 2: P2 (желательные, но не блокирующие)

**UF2 — compose_preflight.py hardcoded path**

```python
# Было:
_MANIFEST_DEFAULT = "/opt/platform/core/secrets-manifest.yaml"
# Стало:
_MANIFEST_DEFAULT = os.path.join(
    os.environ.get("PLATFORM_ROOT", "/opt/platform"),
    "core/secrets-manifest.yaml"
)
```

**UF5-UF8 — Retry для HTTP-запросов в тестах**

Целевые файлы: `test_platform_endpoints.py`, `test_smoke_hermes.py`, `test_e2e_health.py`, `test_e2e_langfuse.py`.

Паттерн (из F5):
```python
for attempt in range(3):
    try:
        r = requests.get(url, timeout=10)
        # assertions...
        break
    except requests.RequestException as exc:
        if attempt < 2:
            logger.warning("Attempt %d failed, retrying in %ds...", attempt + 1, 2**attempt)
            time.sleep(2**attempt)
        else:
            raise
```

**UF9 — Расширение gate coverage на core/**

Модифицировать `test_gate_no_hardcoded_local_paths.py`:
- Добавить паттерн для `/opt/platform` БЕЗ `os.environ.get("PLATFORM_ROOT", ...)` или `os.environ.get("CORE_DIR", ...)`
- Сканировать `core/**/*.py` а не только `tests/**/*.py`
- Allowlist: `core/lib/paths.sh` (canonical SoT), файлы с `os.environ.get("PLATFORM_ROOT", "/opt/platform")`

---

## Remediation Wave Plan

| Wave | Fixes | Files | Estimated LOC | Risk |
|------|-------|-------|---------------|------|
| **W1** | UF1, UF3, UF4, UF10 | 4 файла (+1 новый gate) | +125/-20 | LOW-MED — retry замедляет тесты, UF1/UF10 изолированы |
| **W2** | UF2, UF5-UF8, UF9 | 6 файлов | +55/-10 | LOW — изолированные изменения |
| **Итого** | 10 fixes | 10 файлов | +180/-30 | — |

---

## Verification

```bash
# Pre-flight
make fix-gate && git add -u && make gate MODE=fast

# Gate tests (должны быть зелёными — сейчас 202/202 pass)
python3 -m pytest tests/gates/ -m gate -q

# New gate verification
python3 -m pytest tests/gates/test_gate_http_retry_policy.py -v

# Smoke tests (должны проходить с retry)
python3 -m pytest tests/test_smoke_litellm.py tests/test_smoke_langfuse.py -v
```

---

## Current State (pre-remediation)

- Gate tests: **202 passed, 15 skipped** ✅
- QA pre-implementation gate: **VerificationReport** → STABLE, 10/10 UF confirmed (see `02-VerificationReport.md`)
- Severity adjustments: UF3→P1 (F3: no exception handling), UF4→P1 (F2: hard fail w/o retry) — applied 2026-07-23
- Staged changes: 11 files (+415/-85) — все исправления P0-P2
- New prevention gates: G1 (hardcoded paths), G2 (checkout order) — оба pass
- Unfixed delta: 10 findings (3 P1, 7 P2) — severity апгрейд UF3/UF4 по результатам VerificationReport F2/F3

$END_DEVPLAN
