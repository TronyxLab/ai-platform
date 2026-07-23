# GREP_SUMMARY: DevPlan 063 CI test deduplication gate pipeline fast ci-docker makefile workflow optimization build-cache
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ superposition-collapse (S1+S2) → ⊕ file-manifest → ⚡ step-plan → ⚠ TRAP[INDEX] → ⎋ verification

$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:               Устранить дублирование тестов между fast gate и ci-docker gate (S1) и починить кеширование сборки hermes-agent-base (S2). Сократить среднее время CI с 947 с до ~620 с (-35%).
DESCRIPTION:           Два изменения в двух файлах: (1) ci.mk — перенос contract-тестов в MODE=fast, удаление дублирующих static/predeploy из MODE=ci-docker, уточнение маркера predeploy; (2) platform-test.yml — pre-pull базового образа hermes-agent, обновление комментариев и job summary.
RATIONALE:             Статистика CI за 23.07.2026: 53% времени (506 с) тратится на ci-docker gate, из которых ~110 с — повторный прогон 1585 static + 33 predeploy тестов, уже прошедших в fast gate. Contract-тесты (267 шт., 0 Docker) живут только в ci-docker — перенос в fast gate даёт fail-fast и срезает 30 с из Docker-фазы. Build hermes-agent-base (69-102 с) не использует pre-pull базового образа — добавление `docker pull` в pre-pull фазу сокращает сборку на 50-70 с.
ACCEPTANCE_CRITERIA:   (1) `make gate MODE=fast` проходит зелёным с contract-тестами в пайплайне, (2) `make gate MODE=ci-docker` проходит зелёным без contract/static/predeploy, (3) smoke + component тесты не затронуты (38+10 тестов), (4) `make check-manifests` проходит, (5) CI platform-test завершается <700 с против текущих 947 с
IMPLEMENTS:            Superposition collapse S1+S2 от 2026-07-23 — анализ CI-статистики 10 прогонов platform-test
IMPACTS:               makefiles/ci.mk (строки 117-212), .github/workflows/platform-test.yml (строки 163-251), .github/workflows/push-gate.yml (строка 72)
REQUIRES:              Доступ к GitHub Actions для верификации изменённого workflow (push в ветку), локальный `make gate MODE=fast` и `make gate MODE=ci-docker`
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

**SECTION_GOALS:**
- GOAL S1: Устранить дублирование тестов → GOAL_DEDUP
- GOAL S2: Починить кеширование сборки hermes-agent-base → GOAL_CACHE
- GOAL V: Верификация — оба gate-режима проходят локально и в CI → GOAL_VERIFY

**SECTION_USE_CASES:**
- USE_CASE 1: Разработчик пушит PR → platform-test: fast gate (contract включён) + ci-docker (только smoke + component) → UC_PR
- USE_CASE 2: Разработчик пушит в feature-ветку → push-gate: fast gate (contract включён) → UC_PUSH
- USE_CASE 3: Ночной прогон → nightly-gate: MODE=full (канонический порядок, не меняется) → UC_NIGHTLY
$END_DOCUMENT_PLAN

---

## Часть 1: S1 — Устранение дублирования тестов

### Контекст: что происходит сейчас

```
MODE=fast (6 steps):
  pre-commit → validate → lint → gates → static(1585) → predeploy(33)

MODE=ci-docker (6 steps):
  contract(267) → static(1585)🔴 → predeploy(33)🔴 → smoke(38) → component(10) → merge
```

🔴 = полный или частичный дубликат шага из fast gate.

Из 1648 тестов только 48 требуют Docker (38 smoke + 10 component). Contract (267) — pure static (entrypoint-скрипты). Static (1585) — `static_audit or (not ... requires_docker)`. Predeploy (33) — 32 static, 1 Docker.

### Целевое состояние

```
MODE=fast (7 steps):
  pre-commit → validate → lint → gates → contract(267)🆕 → static(1585) → predeploy(32*)
  * исключая requires_docker

MODE=ci-docker (3 steps):
  predeploy-docker(1) → smoke(38) → component(10) → merge
```

### Изменения в ci.mk

#### 1. MODE=fast: добавить contract (новый шаг 5/7, сдвиг нумерации)

**Было (строка 118):**
```makefile
echo "[IMP:7][make][gate] MODE=fast — 6 steps: pre-commit, validate, lint, gates, static, predeploy...";
```

**Стало:**
```makefile
echo "[IMP:7][make][gate] MODE=fast — 7 steps: pre-commit, validate, lint, gates, contract, static, predeploy...";
```

**Было (строки 131-140, после step 4 gates):**
```makefile
echo "[IMP:7][make][gate] Step 5/6: static tests (no Docker)..."; \
```

**Стало (вставка contract + перенумерация static и predeploy):**
```makefile
echo "[IMP:7][make][gate] Step 5/7: contract tests..."; \
$(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; exit 1; }; \
echo "[IMP:7][make][gate] Step 6/7: static tests (no Docker)..."; \
```

И predeploy становится Step 7/7 вместо 6/6.

#### 2. MODE=fast: уточнить predeploy-маркер

**Было (строка 138):**
```makefile
PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy" -v --tb=short -rs \
```

**Стало:**
```makefile
PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy and not requires_docker" -v --tb=short -rs \
```

Причина: 1 из 33 predeploy-тестов (`test_project_compose_configs_valid`) требует Docker. В fast gate Docker недоступен — тест должен быть исключён. Он будет выполнен в ci-docker.

#### 3. MODE=ci-docker: убрать contract, static, predeploy → оставить predeploy-docker + smoke + component

**Было (строки 182-207):**
```makefile
elif [ "$(MODE)" = "ci-docker" ]; then \
    echo "[IMP:7][make][gate] MODE=ci-docker — running Docker-dependent gate pipeline (no pre-commit/validate/lint)..."; \
    GATE_FAILED=0; \
    rm -f tests/report.xml tests/report*.xml; \
    echo "[IMP:7][make][gate] Step 1/6: contract tests..."; \
    $(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 2/6: static tests..."; \
    $(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][gate] FAIL: static"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 3/6: predeploy tests..."; \
    $(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][gate] FAIL: predeploy"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 4/6: smoke tests..."; \
    $(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 5/6: component tests..."; \
    $(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
    $(PYTHON) tests/merge_junit.py \
        tests/report-contract.xml \
        tests/report-static.xml \
        tests/report-predeploy.xml \
        tests/report-smoke.xml \
        tests/report-component.xml \
        -o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
    if [ $$GATE_FAILED -ne 0 ]; then \
        echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=ci-docker) — see individual FAIL messages above"; \
        exit 1; \
    fi; \
```

**Стало:**
```makefile
elif [ "$(MODE)" = "ci-docker" ]; then \
    echo "[IMP:7][make][gate] MODE=ci-docker — running Docker-dependent gate pipeline (smoke + component, no static duplication)..."; \
    GATE_FAILED=0; \
    rm -f tests/report.xml tests/report*.xml; \
    echo "[IMP:7][make][gate] Step 1/3: predeploy tests (Docker-dependent only)..."; \
    PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy and requires_docker" -v --tb=short -rs \
        --junitxml=tests/report-predeploy.xml || { echo "[IMP:9][make][gate] FAIL: predeploy-docker"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 2/3: smoke tests..."; \
    $(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Step 3/3: component tests..."; \
    $(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
    echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
    $(PYTHON) tests/merge_junit.py \
        tests/report-predeploy.xml \
        tests/report-smoke.xml \
        tests/report-component.xml \
        -o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
    if [ $$GATE_FAILED -ne 0 ]; then \
        echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=ci-docker) — see individual FAIL messages above"; \
        exit 1; \
    fi; \
```

⚠️ **TRAP[INDEX]:** MODE=ci-docker шаги перенумерованы с 1-6 на 1-3. Комментарии в ci.mk и platform-test.yml, ссылающиеся на номера шагов ci-docker, должны быть обновлены.

### Изменения в platform-test.yml

**Было (строка 240, комментарий к full gate step):**
```yaml
- name: Run full gate (contract + static + predeploy + smoke + component — Docker-dependent, no pre-commit/validate/lint)
```

**Стало:**
```yaml
- name: Run full gate (smoke + component + predeploy-docker — no static duplication, contract/static/predeploy in fast gate)
```

**Было (строка 324-337, job summary):**
```
echo "| Full Gate         | (see step timing in UI) |"
```

Без изменений в тексте — ссылка на UI остаётся корректной. Но описание gate strategy в summary ниже (строки 339-342) нужно обновить:

**Было:**
```
echo "### Gate Strategy"
echo "- Fast gate: static_audit + gates + predeploy (no Docker, <3 min)"
echo "- Full gate: contract + static + predeploy + smoke + component (Docker-dependent only, SKIP_PRECOMMIT=1 in CI)"
```

**Стало:**
```
echo "### Gate Strategy"
echo "- Fast gate: gates + contract + static + predeploy (no Docker, <3 min)"
echo "- Full gate: smoke + component + predeploy-docker (Docker stack, no static duplication)"
```

### Изменения в push-gate.yml

**Было (строка 72):**
```yaml
- name: Run fast gate (syntax/JSON check + gates + static + predeploy)
```

**Стало:**
```yaml
- name: Run fast gate (syntax/JSON check + gates + contract + static + predeploy)
```

---

## Часть 2: S2 — Кеширование сборки hermes-agent-base

### Контекст

Сборка `hermes-agent-base` (69-102 с) выполняет multi-stage Docker build:
1. `FROM alpine:3.21 AS validate` — apk install shellcheck/yq/bash
2. `FROM nousresearch/hermes-agent:v2026.7.7.2` — базовый образ ~1-2 ГБ
3. COPY из validate

Registry cache (ghcr.io) кеширует слои, но базовый образ `nousresearch/hermes-agent:v2026.7.7.2` **не pre-pull'ится** — он тянется во время сборки из Docker Hub (30-50 с).

Pre-pull фаза (`docker compose pull --ignore-buildable`) тянет образы из compose-файлов, но не сборочный базовый образ.

### Изменение в platform-test.yml

**Добавить после pre-pull шага (строка 176, после `wait` и `postgres:16-alpine`):**

```yaml
# ⚡ TRAP[PERF] · 2026-07-23 · >0 · Pre-pull hermes-agent base image for build cache
# · Root: hermes-agent-base Dockerfile FROM nousresearch/hermes-agent:v2026.7.7.2
# ·   pulls ~1-2GB on every CI run. Pre-pulling moves this I/O into the parallel
# ·   pre-pull phase, saving 30-50s from the build step.
# · Rev: when hermes-agent upstream version changes, update this pull command.
# · TRAP[DRIFT] · 2026-07-23 · LOW · Version tag hardcoded in 2 places
# ·   Dockerfile:50 + platform-test.yml (this step). Consider dynamic extraction:
# ·   HERMES_BASE=$(grep -oP 'FROM nousresearch/hermes-agent:\K\S+' core/modules/hermes-agent/build/Dockerfile)
# ·   docker pull "nousresearch/hermes-agent:${HERMES_BASE}" || echo "[warn] ..."
- name: Pre-pull hermes-agent base image (for build cache)
  run: |
    echo "[IMP:8][pre-pull] Pulling nousresearch/hermes-agent:v2026.7.7.2 for build cache..."
    docker pull nousresearch/hermes-agent:v2026.7.7.2 2>&1 | tail -5 || echo "[warn] hermes-agent base image pre-pull failed — continuing (may affect build time)"
    echo "[IMP:9][pre-pull] Base image pulled — build step will reuse"
```

**Добавить диагностику cache-hit в build шаг (строка 186, после `make hermes-push-l1` не относится, это в docker-build-cache action):**

В composite action `.github/actions/docker-build-cache/action.yml` добавить вывод `cache-hit` (строки 62-72):

В шаге "Build Docker image" `docker/build-push-action@v7` уже возвращает outputs. Достаточно добавить шаг после build:

```yaml
- name: Report cache status (${{ inputs.cache-scope }})
  shell: bash
  run: |
    echo "[IMP:8][cache] ${{ inputs.cache-scope }} — cache backend: ${{ inputs.cache-backend }}"
```

Но проще — добавить диагностику прямо в platform-test.yml после build-шагов:

```yaml
- name: Diagnostic — verify built images
  run: |
    echo "=== Built images ==="
    docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep -E "(hermes-agent-base|backup-cron)"
    echo ""
    echo "=== Hermes-agent-base layers ==="
    docker history hermes-agent-base:latest --no-trunc --format "table {{.Size}}\t{{.CreatedBy}}" 2>/dev/null | head -15 || echo "(image not found)"
```

---

## Часть 3: Что НЕ меняется

| Компонент | Статус | Причина |
|-----------|--------|---------|
| MODE=full (10 steps) | Без изменений | Канонический порядок для nightly-gate, включает все шаги |
| Smoke тесты (38 шт.) | Без изменений | Ядро Docker-тестирования |
| Component тесты (10 шт.) | Без изменений | API-тесты hermes-agent/Grafana |
| Gate тесты (261 шт.) | Без изменений | Анти-drift инварианты |
| pre-commit хуки | Без изменений | Security gate |
| deploy-project.yml (MODE=fast PROJECT=...) | Без изменений в workflow, семантика MODE=fast поменяется | Contract-тесты Docker-free и project-agnostic — `PROJECT` применяется только к predeploy шагу. Функционально безопасно: contract-тесты пройдут независимо от PROJECT фильтра |
| predeploy тесты (32 static) | Выполняются в fast gate, а не в ci-docker | Смысловой перенос, не удаление |
| test_project_compose_configs_valid (1 Docker predeploy) | Выполняется в ci-docker, а не в fast gate | Перенесён к Docker-тестам |

---

## Файловый манифест

| Файл | Действие | Строк |
|------|----------|-------|
| `makefiles/ci.mk` | MODIFY: строки 117-212 (fast + ci-docker gate blocks) | ~30 строк изменений |
| `.github/workflows/platform-test.yml` | MODIFY: строки 163-176 (pre-pull), 240 (full gate name), 324-342 (summary) | ~15 строк |
| `.github/workflows/push-gate.yml` | MODIFY: строка 72 (fast gate description) | 1 строка |
| `.github/actions/docker-build-cache/action.yml` | MODIFY: добавить cache-report шаг (строки 62-72) | ~5 строк |

**Всего: 4 файла, ~50 строк изменений.**

---

## Пошаговый план реализации

### Step 1: Изменить ci.mk
- [ ] MODE=fast: добавить contract как step 5/7, перенумеровать static→6/7, predeploy→7/7
- [ ] MODE=fast: уточнить predeploy-маркер на `"predeploy and not requires_docker"`
- [ ] MODE=ci-docker: заменить 6 шагов на 3 (predeploy-docker + smoke + component)
- [ ] MODE=ci-docker: обновить merge_junit — убрать report-contract.xml и report-static.xml
- [ ] **Обновить header comments на строках 110-112** (описания MODE=fast и MODE=ci-docker):
  ```
  ##   MODE=fast — validate → lint → gates → contract → static → predeploy (no Docker)
  ##   MODE=ci-docker — predeploy-docker → smoke → component (Docker stack, no static duplication)
  ```

### Step 2: Изменить platform-test.yml
- [ ] Pre-pull шаг: добавить `docker pull nousresearch/hermes-agent:v2026.7.7.2`
- [ ] Full gate step: обновить name и комментарий
- [ ] Job summary: обновить Gate Strategy описание

### Step 3: Изменить push-gate.yml
- [ ] Fast gate step: обновить name — добавить "contract"

### Step 4: Диагностика cache (docker-build-cache/action.yml)
- [ ] Добавить шаг вывода cache-status после build

### Step 5: Локальная верификация
- [ ] `make gate MODE=fast` — должен пройти с 7 шагами (включая contract)
- [ ] `make gate MODE=ci-docker` — должен пройти с 3 шагами (smoke + component)
- [ ] `make check-manifests` — должен пройти

### Step 6: CI верификация
- [ ] Push в feature-ветку → push-gate зелёный с contract в fast gate
- [ ] PR в main → platform-test зелёный, время <700 с

---

## Верификация

```bash
# 1. Fast gate с contract
make gate MODE=fast
# Ожидание: 7 steps, Step 5/7 contract tests → PASS

# 2. ci-docker без дублирования
make gate MODE=ci-docker SKIP_PRECOMMIT=1
# Ожидание: 3 steps (predeploy-docker, smoke, component) → PASS

# 3. Smoke тесты не затронуты
make test MARKER=smoke
# Ожидание: 38 tests collected, PASS

# 4. Component тесты не затронуты
make test MARKER=component
# Ожидание: 10 tests collected, PASS

# 5. Манифесты актуальны
make check-manifests
# Ожидание: PASS (нет изменений в generated files)
```

---

## Откат

```bash
git revert <merge-commit>
```

Изменения изолированы в 4 файлах, не затрагивают тесты, production-код или инфраструктуру. Откат тривиален и безопасен.

$END_DEVPLAN
