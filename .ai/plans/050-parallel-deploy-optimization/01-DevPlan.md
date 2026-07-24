$START_DEVPLAN

# DevPlan 050 — Parallel Deploy Optimization + 048 Absorption

$ARTIFACT_CONTRACT
PURPOSE:               Сократить время node-update с 200-485s до 60-120s (3-8x ускорение) через подключение уже реализованных но не используемых механизмов параллельного деплоя, pre-pull, content-hash skip и батчинга Python subprocess. Попутно поглотить незакрытые фиксы DevPlan 048.
DESCRIPTION:           6-волновая реализация. Wave 0 — поглощение 048 (P1/P2/P3). Wave 1 — подключение _topo_sort.py к shell-фасаду (группировка модулей по depends_on). Wave 2 — параллельный деплой групп через deploy_docker_group(). Wave 3 — content-hash skip для hermes-agent и status-page (пропуск docker build при неизменных исходниках). Wave 4 — батчинг Python subprocess (batch-metadata + batch check-env). Wave 5 — параллельный healthcheck + feature flag DEPLOY_PARALLEL. Wave 6 — верификация на staging-ноде.
RATIONALE:             _topo_sort.py (295 LOC), deploy_docker_group() (1327 LOC docker_orchestrator.py), _pre_pull_images(), batch-metadata — уже написаны, протестированы (test_deploy_modules.py, 1236 LOC), но НЕ подключены к shell-фасаду deploy-modules.sh. AGENTS.md декларирует пайплайн _topo_sort → pull → up, но код делает последовательный for-цикл. Это документационно-кодовый дрифт. Подключение — минимальные изменения в shell-фасаде (~30 строк), вся бизнес-логика уже в Python.
ACCEPTANCE_CRITERIA:   AC1: node-update на production-ноде ≤ 150s (холодный кэш), ≤ 90s (тёплый). AC2: hermes-agent rebuild пропускается при неизменных исходниках (повторный node-update без изменений → docker build не вызывается). AC3: 14/14 healthcheck PASS после parallel deploy. AC4: feature flag DEPLOY_PARALLEL=false сохраняет последовательное поведение (обратная совместимость). AC5: все существующие тесты (test_deploy_modules.py, test_docker_orchestrator.py) остаются зелёными. AC6: 048.P1 (FORCE_MODE double-run), 048.P2 (healthcheck retry), 048.P3 (PLATFORM_DOMAIN fallback) исправлены.
IMPLEMENTS:            Анализ времени node-update (2026-07-24), DevPlan 048 (P1/P2/P3 absorption), W4-E1 Strangler-Fig shell-фасад deploy-modules.sh (107 LOC)
IMPACTS:               core/internal/bootstrap/deploy-modules.sh, core/internal/bootstrap/deploy/docker_orchestrator.py, core/internal/bootstrap/_topo_sort.py, core/internal/bootstrap/deploy/secrets_validator.py, core/internal/bootstrap/lifecycle/state_machine.py, core/lib/healthcheck.sh, core/modules/hermes-agent/, core/modules/status-page/, tests/test_deploy_modules.py, core/AGENTS.md
REQUIRES:              Доступ к staging VPS для верификации Wave 6, Docker daemon на worker-машине для unit-тестов
$END_ARTIFACT_CONTRACT

---

## Draft Code Graph (XML)

```xml
<knowledge_graph>
  <Node id="deploy_modules_sh" type="SHELL_FACADE" keywords="orchestrator, thin-facade, hybrid">
    <annotation>107 LOC shell — вызывает _topo_sort.py, итерирует группы, вызывает docker_orchestrator.py</annotation>
  </Node>
  <Node id="topo_sort_py" type="PYTHON_MODULE" keywords="kahn, dag, depends-on, enriched">
    <annotation>295 LOC — вычисляет группы независимых модулей, возвращает {groups, modules} с install_type/severity</annotation>
  </Node>
  <Node id="docker_orchestrator_py" type="PYTHON_MODULE" keywords="deploy, pre-pull, parallel, fork, rollback">
    <annotation>1327 LOC — deploy_docker_module, deploy_docker_group (fork-based parallel), _pre_pull_images, _handle_hermes_agent</annotation>
  </Node>
  <Node id="secrets_validator_py" type="PYTHON_MODULE" keywords="batch, metadata, check-env, charset">
    <annotation>595 LOC — batch-metadata (name:install_type:severity), batch check-env, expand-deps</annotation>
  </Node>
  <Node id="content_hash_py" type="NEW_PYTHON_MODULE" keywords="hash, skip, build, cache, hermes, status-page">
    <annotation>NEW ~80 LOC — хеширует исходники build-модулей, сравнивает с сохранённым хешем, решает skip/rebuild</annotation>
  </Node>
  <Node id="state_machine_py" type="PYTHON_MODULE" keywords="lifecycle, steps, deploy_modules">
    <annotation>Вызывает deploy-modules.sh с --skip-provision. Поглощает 048.P1 (FORCE_MODE fix), 048.P3 (PLATFORM_DOMAIN)</annotation>
  </Node>
  <Node id="test_deploy_modules_py" type="TEST_FILE" keywords="parallel, topo-sort, enriched, edge-cases">
    <annotation>1236 LOC — существующие тесты для topo_sort, parallel deploy, batch metadata</annotation>
  </Node>
  <CrossLinks>
    <Edge from="deploy_modules_sh" to="topo_sort_py" label="call --modules-dir --filter-names → groups JSON"/>
    <Edge from="deploy_modules_sh" to="docker_orchestrator_py" label="call --action deploy-group --module-entries per group"/>
    <Edge from="deploy_modules_sh" to="secrets_validator_py" label="call --action batch-check-env + batch-metadata"/>
    <Edge from="docker_orchestrator_py" to="content_hash_py" label="call for build skip decision"/>
    <Edge from="state_machine_py" to="deploy_modules_sh" label="invoke with --skip-provision"/>
    <Edge from="test_deploy_modules_py" to="topo_sort_py" label="native import, tmp_path fixtures"/>
    <Edge from="test_deploy_modules_py" to="docker_orchestrator_py" label="static audit of deploy_docker_group"/>
  </CrossLinks>
</knowledge_graph>
```

---

## Step-by-step Data Flow

### Before (status quo — sequential)

```
deploy-modules.sh (107 LOC shell)
  ├── python3 secrets_validator.py --action parse-node-yaml → ALL_NAMES, ENABLED_NAMES
  ├── python3 secrets_validator.py --action validate-charsets
  └── for module in ENABLED_NAMES:                    ← SEQUENTIAL BOTTLENECK
        ├── python3 secrets_validator.py --action check-env --module-name X
        ├── python3 secrets_validator.py --action detect-type --module-name X
        ├── [if docker] python3 docker_orchestrator.py --action deploy --module-name X
        │     ├── _handle_hermes_agent()              ← BUILD EVERY TIME
        │     ├── _reconcile_orphan_containers()
        │     └── docker compose up -d                ← wait for pull + start
        └── [if system] invoke_module_interface X install
            → invoke_module_interface X healthcheck liveness
  ├── python3 sudoers_generator.py --action batch-generate
  ├── python3 orphan_reconciler.py
  └── severity-based exit (per-module python3 secrets_validator.py --action module-metadata)

Time: 200s (warm) / 485s (cold)
Subprocess spawns: ~56 (4 per docker-module × 14)
```

### After (target — parallel with groups)

```
deploy-modules.sh (130 LOC shell)                      ← +23 lines
  ├── python3 secrets_validator.py --action parse-node-yaml → ALL_NAMES, ENABLED_NAMES
  ├── python3 secrets_validator.py --action validate-charsets
  ├── python3 secrets_validator.py --action batch-metadata → ALL metadata (1 call)
  ├── python3 secrets_validator.py --action batch-check-env → ALL env validation (1 call)
  ├── [if DEPLOY_PARALLEL=true]
  │     ├── python3 _topo_sort.py --modules-dir ... --filter-names ENABLED_NAMES
  │     │     → {"groups": [["postgres","redis"],["nginx","clickhouse"],["litellm","langfuse"],["hermes-agent","status-page","backup-cron"]], "modules": {...}}
  │     ├── python3 docker_orchestrator.py --action pre-pull --module-entries ... --parallel-limit 4
  │     │     → fork-based параллельный pull всех образов
  │     └── for group in groups:                       ← SEQUENTIAL BETWEEN GROUPS
  │           python3 docker_orchestrator.py --action deploy-group --module-entries group
  │           │   ├── for module in group (os.fork, slot_limit=4):  ← PARALLEL WITHIN GROUP
  │           │   │     ├── content_hash.check_build_needed()       ← SKIP if unchanged
  │           │   │     ├── [if build needed] docker compose build
  │           │   │     └── docker compose up -d
  │           │   └── atomic rollback if any module fails
  │           └── for module in group (os.fork):                   ← PARALLEL HC
  │                 run_healthcheck(module)
  ├── [else] current sequential for-loop (unchanged)   ← BACKWARD COMPAT
  ├── python3 sudoers_generator.py --action batch-generate
  ├── python3 orphan_reconciler.py
  └── severity-based exit (from batch-metadata output, no per-module calls)

Time: 60s (warm) / 120s (cold)
Subprocess spawns: ~8 (batch calls + 1 per group + sudoers + orphan)
```

---

## Wave Structure

### Wave 0 — Absorption of DevPlan 048 (Preliminary Fixes)

**Scope:** Закрыть незавершённые фиксы из DevPlan 048 перед основной работой.

| Task | ID | Что | Файлы | Сложность |
|------|----|-----|-------|-----------|
| T0.1 | 048.P1 | FORCE_MODE double-run: заменить `FORCE_MODE="false"` → `FORCE_MODE=""` в state_machine.py | `core/internal/bootstrap/lifecycle/state_machine.py` | 1 строка |
| T0.2 | 048.P2 | Healthcheck retry: увеличить `hc_max_retries` 4→10, `hc_retry_interval` 3→10 в `run_healthcheck()` и `wait_for_readiness()` | `core/internal/bootstrap/deploy/docker_orchestrator.py` (DEFAULT константы), `core/internal/bootstrap/node-lifecycle.sh` (если там дублируются параметры) | ~4 строки |
| T0.3 | 048.P3 | PLATFORM_DOMAIN fallback: если `PLATFORM_DOMAIN` env пуст — читать `domain` из `node.yaml` в `issue-cert.sh` или `cert_orchestrator.py` | `core/internal/bootstrap/deploy/cert_orchestrator.py` | ~5 строк |

**P0 из 048 уже закрыт в DevPlan 045 (commit `2ea8be5`) — НЕ включаем.**

### Wave 1 — Connect topo_sort + pre_pull to Shell Facade

**Scope:** Минимальные изменения в `deploy-modules.sh` для вызова `_topo_sort.py` и `--action pre-pull`.

| Task | Что | Детали | Файлы |
|------|-----|--------|-------|
| T1.1 | Добавить вызов `_topo_sort.py` | После `parse-node-yaml` вызывать `_topo_sort.py --modules-dir ... --filter-names $ENABLED_NAMES`, парсить JSON, извлекать `groups` и `modules` dict | `deploy-modules.sh` |
| T1.2 | Добавить pre-pull фазу | Вызвать `docker_orchestrator.py --action pre-pull --module-entries $ENABLED_NAMES --parallel-limit 4` перед циклом деплоя | `deploy-modules.sh` |
| T1.3 | Сохранить `modules` dict для severity exit | Вместо per-module `--action module-metadata` использовать данные из enriched output `_topo_sort.py` | `deploy-modules.sh` |

**Инвариант:** При `DEPLOY_PARALLEL=false` код T1.1-T1.3 НЕ выполняется — старый for-цикл остаётся нетронутым.

### Wave 2 — Parallel Deploy via deploy_docker_group

**Scope:** Заменить последовательный for-цикл на вызов `deploy_docker_group()` для каждой topo-группы.

| Task | Что | Детали | Файлы |
|------|-----|--------|-------|
| T2.1 | Добавить `--action deploy-group` в CLI `docker_orchestrator.py` | Уже существует `deploy_docker_group()` но CLI-диспатч не имеет case для него. Добавить `"deploy-group"` в `choices` и вызов `deploy_docker_group()` | `docker_orchestrator.py` (argparse + main) |
| T2.2 | Интегрировать `deploy_docker_group` в shell | В shell: for group in groups → `python3 docker_orchestrator.py --action deploy-group --module-entries "mod1:overlay1,mod2:overlay2"` | `deploy-modules.sh` |
| T2.3 | System-модули: оставить последовательными | System-модули (install_type != docker) НЕ входят в topo_sort (фильтруются). Для них — старый последовательный цикл до/после docker-групп | `deploy-modules.sh` |
| T2.4 | Интегрировать content_hash в deploy_docker_module | Перед `_handle_hermes_agent()` и блоком `build:` вызвать `content_hash.check_build_needed(module_dir)` → skip если хеш совпадает (см. Wave 3) | `docker_orchestrator.py` |

**Семантика rollback:** Atomic per-group (выбор пользователя). Если любой модуль в группе падает — `docker compose down` для всех модулей группы. Группы изолированы.

### Wave 3 — Build Optimization (Hermes-agent + Build-Modules)

**Scope:** Трёхуровневая стратегия ускорения сборок:

1. **BuildKit cache** в `hermes-images.sh` — мгновенные пересборки L1/L2 локально (меняются только изменённые слои)
2. **CI auto-push в GHCR** при изменениях в `core/modules/hermes-agent/` — образ всегда свежий в registry, VPS делает `pull` вместо `build`
3. **Content-hash skip** для не-hermes build-модулей (status-page, backup-cron) — пропуск `docker compose build` при неизменных исходниках

| Task | Что | Детали | Файлы |
|------|-----|--------|-------|
| T3.1 | BuildKit cache в `hermes-images.sh` | Добавить `--cache-from type=local,src=/tmp/.hermes-build-cache` и `--cache-to type=local,dest=/tmp/.hermes-build-cache,mode=max` в `build_L1()` и `build_L2()`. Кэш-директория `/tmp/.hermes-build-cache` создаётся при первом прогоне. Эффект: повторная сборка без изменений — <5s вместо 120-185s; сборка с мелкими изменениями — пересобираются только изменённые слои | `core/internal/build/hermes-images.sh` |
| T3.2 | CI workflow для auto-build + push в GHCR | Создать `.github/workflows/build-hermes.yml` в `tronyx161/ai-platform`. Триггер: `push` в `core/modules/hermes-agent/**`. Использует `--cache-from type=gha` + `--cache-to type=gha,mode=max` для CI-кэша. Шаги: (1) build L1 `hermes-agent-base` → push в `ghcr.io/tronyx161/hermes-agent-base:latest`, (2) build L2 для дефолтного контекста → push в `ghcr.io/tronyx161/hermes-agent-context:latest`. Результат: `_handle_hermes_agent()` в `docker_orchestrator.py` находит образы в GHCR → делает `pull` (~10-20s) вместо `build` (120-185s) | NEW: `.github/workflows/build-hermes.yml` |
| T3.3 | Content-hash skip для status-page и backup-cron | Создать `deploy/content_hash.py`. Хешировать Dockerfile + build context (`.dockerignore` учитывается). Хранить хеш в `/var/lib/platform/.build-cache/<module>.hash`. Функции: `compute_source_hash(module_dir) → str`, `check_build_needed(module_dir) → bool`, `save_build_hash(module_dir, hash)`. Интегрировать в `deploy_docker_module()`: блок `if "build:" in compose_content` → проверить `check_build_needed()` → skip если хеш совпал | NEW: `core/internal/bootstrap/deploy/content_hash.py` (~80 LOC), mod: `docker_orchestrator.py` |
| T3.4 | Unit-тесты | `test_content_hash.py`: хеш одинаковых исходников → совпадает; изменение Dockerfile → разный хеш; `.dockerignore` исключает файлы; отсутствие кэша → build needed. `test_build_hermes_ci.py`: static audit `.github/workflows/build-hermes.yml` — проверка триггера, cache-from/cache-to флагов, push в GHCR | NEW: `tests/unit/test_content_hash.py`, NEW: `tests/gates/test_gate_build_hermes_ci.py` |

**Стратегия для hermes-agent (итоговая):**

```
node-update на VPS
  └── _handle_hermes_agent()
        ├── docker manifest inspect ghcr.io/tronyx161/hermes-agent-context:latest
        │     ├── FOUND (CI уже собрал и запушил) → docker pull (~10-20s) ✅
        │     └── NOT FOUND → fallback to local build
        │           └── docker compose build (L1→L2)
        │                 └── BuildKit cache (/tmp/.hermes-build-cache)
        │                       ├── Nothing changed → <5s ⚡
        │                       └── Small change → changed layers only (~30-60s)
        └── docker compose up -d
```

**Стратегия для status-page/backup-cron:**
```
deploy_docker_module()
  ├── content_hash.check_build_needed(module_dir)
  │     ├── Hash matches → SKIP docker compose build ✅
  │     └── Hash differs → docker compose build → save new hash
  └── docker compose up -d
```

### Wave 4 — Batch Python Subprocess Optimization

**Scope:** Уменьшить количество Python-subprocess spawn'ов с ~56 до ~8.

| Task | Что | Детали | Файлы |
|------|-----|--------|-------|
| T4.1 | Использовать `batch-metadata` вместо per-module `detect-type` | Один вызов `secrets_validator.py --action batch-metadata` возвращает `name:install_type:severity` для всех модулей. Замена per-module цикла | `deploy-modules.sh` |
| T4.2 | Добавить `batch-check-env` в `secrets_validator.py` | Новый action: валидирует secrets для всех модулей за 1 вызов. Возвращает `name:status` lines (status = ok/warn/error). Существующий `check-env` логику переиспользовать, обернув в цикл по модулям | `secrets_validator.py` |
| T4.3 | Использовать enriched `modules` из `_topo_sort.py` | `modules` dict уже содержит `install_type` и `severity` → не нужно вызывать `detect-type` и `module-metadata` per-module при параллельном режиме | `deploy-modules.sh` |
| T4.4 | Обновить severity-based exit | Использовать `modules` dict из `_topo_sort.py` enriched output вместо per-module вызовов `--action module-metadata` | `deploy-modules.sh` |

### Wave 5 — Parallel Healthcheck + Feature Flag

**Scope:** Параллельный healthcheck внутри группы + `DEPLOY_PARALLEL` feature flag.

| Task | Что | Детали | Файлы |
|------|-----|--------|-------|
| T5.1 | Healthcheck сразу после каждого модуля в группе | Модифицировать `deploy_docker_group()`: после drain-forks добавить fork-per-module healthcheck (уже частично реализовано в существующей функции) | `docker_orchestrator.py` |
| T5.2 | Feature flag `DEPLOY_PARALLEL` | Добавить проверку `DEPLOY_PARALLEL=true` в `deploy-modules.sh`. Если не установлен или `false` → старый последовательный for-цикл. Это обеспечивает обратную совместимость и возможность отката | `deploy-modules.sh` |
| T5.3 | Пропустить отдельный healthcheck-шаг в node-lifecycle | Если `deploy_docker_group()` уже делает healthcheck внутри группы, шаг `healthcheck` в `node-lifecycle.sh --mode update` должен это учитывать (не дублировать) | `node-lifecycle.sh`, `state_machine.py` |

### Wave 6 — Verification

**Scope:** Тесты + staging-нода + документация.

| Task | Что | Детали |
|------|-----|--------|
| T6.1 | Unit-тесты для новых функций | `test_content_hash.py` (T3.4), `test_gate_build_hermes_ci.py` (T3.4), обновить `test_docker_orchestrator.py` для `deploy-group` CLI-диспатча, обновить `test_deploy_modules.py` для проверки feature flag |
| T6.2 | Staging VPS: последовательный прогон | `DEPLOY_PARALLEL=false make node-update NODE=<staging>` — убедиться что обратная совместимость не нарушена |
| T6.3 | Staging VPS: параллельный прогон | `DEPLOY_PARALLEL=true make node-update NODE=<staging>` — замерить время, проверить healthcheck 14/14 |
| T6.4 | Staging VPS: проверка hermes-agent pull | Убедиться что `_handle_hermes_agent()` находит образ в GHCR → `docker pull` вместо `docker build` (если CI workflow отработал). Если образ отсутствует → fallback build с BuildKit cache |
| T6.5 | Staging VPS: content-hash skip для status-page | Два последовательных `node-update` без изменений → второй прогон пропускает build для status-page |
| T6.6 | Обновить AGENTS.md | Исправить документационный дрифт: `core/internal/bootstrap/AGENTS.md` строка 98 — актуализировать пайплайн docker-модулей (добавить feature flag, parallel path) |
| T6.7 | Production gate | `make gate MODE=fast` — все существующие тесты зелёные |

---

## File Manifest

### Новые файлы (создать)

| # | Файл | Назначение | LOC (оценка) |
|---|------|-----------|-------------|
| 1 | `core/internal/bootstrap/deploy/content_hash.py` | Content-hash для status-page, backup-cron build skip | ~80 |
| 2 | `tests/unit/test_content_hash.py` | Unit-тесты content_hash | ~60 |
| 3 | `.github/workflows/build-hermes.yml` | CI workflow: auto-build L1+L2 hermes-agent → push GHCR | ~50 |
| 4 | `tests/gates/test_gate_build_hermes_ci.py` | Static audit CI workflow (триггер, cache flags, push target) | ~30 |
| 5 | `.ai/plans/050-parallel-deploy-optimization/01-DevPlan.md` | Этот DevPlan | — |

### Модифицируемые файлы

| # | Файл | Что меняется | LOC Δ |
|---|------|-------------|-------|
| 6 | `core/internal/bootstrap/deploy-modules.sh` | + вызов `_topo_sort.py`, + pre-pull, + `deploy-group` цикл, + batch-metadata, + feature flag | +30/-5 |
| 7 | `core/internal/bootstrap/deploy/docker_orchestrator.py` | + `deploy-group` в argparse/main, + content_hash интеграция в `deploy_docker_module()`, + T0.2 healthcheck retry | +25/-5 |
| 8 | `core/internal/bootstrap/deploy/secrets_validator.py` | + `batch-check-env` action | +30 |
| 9 | `core/internal/build/hermes-images.sh` | + `--cache-from type=local` + `--cache-to type=local,mode=max` для L1 и L2 | +4/-0 |
| 10 | `core/internal/bootstrap/lifecycle/state_machine.py` | T0.1: FORCE_MODE fix, T0.3: PLATFORM_DOMAIN fallback | +6/-1 |
| 11 | `core/internal/bootstrap/deploy/cert_orchestrator.py` | T0.3: PLATFORM_DOMAIN fallback | +5 |
| 12 | `core/internal/bootstrap/AGENTS.md` | Актуализация пайплайна docker-модулей (документационный дрифт) | +10/-3 |
| 13 | `tests/test_deploy_modules.py` | + тест для `deploy-group` CLI, + тест для feature flag | +30 |
| 14 | `tests/unit/test_docker_orchestrator.py` | + тест `deploy-group` CLI dispatch | +15 |

### НЕ модифицируются (только используются)

| Файл | Роль |
|------|------|
| `core/internal/bootstrap/_topo_sort.py` | Без изменений — уже выдаёт enriched JSON |
| `core/internal/bootstrap/node-lifecycle.sh` | Без изменений — вызывает `deploy-modules.sh` с `--skip-provision` |
| `core/lib/healthcheck.sh` | Без изменений |
| `core/lib/module-interface.sh` | Без изменений |

---

## Acceptance Criteria — Verification Matrix

| AC | Описание | Как проверить | Wave |
|----|----------|--------------|------|
| AC1 | node-update ≤ 150s холодный / ≤ 90s тёплый | `time make node-update NODE=<staging>` с `DEPLOY_PARALLEL=true` | W6 |
| AC2a | Hermes-agent: pull из GHCR вместо build | CI workflow отработал → образ в GHCR. `_handle_hermes_agent()` находит образ → `docker pull` вместо `docker build`. Лог: `[IMP:9][_handle_hermes_agent][all_found] All hermes-agent images found in registry` | W3 |
| AC2b | Hermes-agent: BuildKit cache при локальной сборке | Два последовательных `hermes-images.sh build-context` без изменений → второй прогон <5s (все слои в кэше) | W3 |
| AC2c | Status-page/backup-cron: build skip при неизменных исходниках | Два последовательных `node-update` без изменений → второй прогон: `[IMP:9][content_hash][skip] Build skipped — source unchanged` для status-page и backup-cron | W3 |
| AC3 | 14/14 healthcheck PASS после parallel deploy | `make healthcheck NODE=<staging>` после parallel deploy → все healthy | W6 |
| AC4 | `DEPLOY_PARALLEL=false` сохраняет последовательное поведение | `DEPLOY_PARALLEL=false make node-update NODE=<staging>` → логи показывают старый for-цикл, тайминг ~200-485s | W6 |
| AC5 | Все существующие тесты зелёные | `make gate MODE=fast` → 100% PASS | W6 |
| AC6 | 048.P1: FORCE_MODE не вызывает double-run | `make converge NODE=<staging> FORCE_MODE=""` → state machine выполняется 1 раз | W0 |
| AC7 | 048.P2: Healthcheck retry 10×10s | Модуль с медленным стартом → healthcheck повторяется до 10 раз с интервалом 10s | W0 |
| AC8 | 048.P3: PLATFORM_DOMAIN fallback | `PLATFORM_DOMAIN="" make node-update NODE=<staging>` → domain читается из node.yaml, сертификат выпускается | W0 |
| AC9 | Atomic per-group rollback | Падение модуля в группе → compose down для всех модулей группы, следующая группа не затрагивается | W2 |
| AC10 | Pre-pull не блокирует деплой при ошибке | Ошибка pull одного образа → warning в логах, деплой продолжается (compose up дотянет сам) | W1 |

---

## Dependency Graph (между волнами)

```
Wave 0 (048 absorption) ──┐
                           ├──► Wave 1 (topo_sort + pre_pull) ──► Wave 2 (parallel deploy) ──┐
                           │       │                                                         ├──► Wave 5 (HC + flag) ──► Wave 6 (verify)
                           │       └──► Wave 4 (batch subprocess) ───────────────────────────┘
                           └──► Wave 3 (build optimization)
                                  ├── T3.1: BuildKit cache (hermes-images.sh) ── независимо
                                  ├── T3.2: CI workflow (build-hermes.yml) ── независимо
                                  └── T3.3: Content-hash skip (status-page) ── зависит от W2 (docker_orchestrator.py)
```

Waves 0, 1, 3 (T3.1, T3.2), 4 — независимы, можно параллелить.
Wave 2 зависит от Wave 1 (нужны группы из topo_sort).
Wave 3 (T3.3) зависит от Wave 2 (content_hash интегрируется в `deploy_docker_module()`).
Wave 5 зависит от Wave 2 (нужен работающий parallel deploy).
Wave 6 зависит от всех.

**Критический путь:** W0 → W1 → W2 → W5 → W6.

---

## Риски

| # | Риск | Вероятность | Mitigation |
|---|------|------------|------------|
| R1 | os.fork() гонки с логгерами/файловыми дескрипторами | Низкая | Уже работает в `_pre_pull_images()` и `deploy_docker_group()`. Дочерние процессы делают только subprocess.run + os._exit |
| R2 | Docker daemon concurrency limit (слишком много параллельных pull/build) | Низкая | `parallel_limit=4` — консервативный лимит. Docker daemon рассчитан на десятки параллельных операций |
| R3 | Atomic rollback ломает работающие сервисы | Средняя | Feature flag позволяет откатиться на последовательный режим. Rollback только внутри группы — зависимые сервисы в одной группе и так должны стартовать вместе |
| R4 | Content-hash коллизия (разные исходники → одинаковый хеш) | Очень низкая | SHA256. Вероятность коллизии пренебрежимо мала |
| R5 | `jq` отсутствует на VPS для парсинга JSON | Низкая | Python парсит JSON в shell через `python3 -c "import json,sys; ..."` — jq не требуется |
| R6 | 048.P2 (10 retries × 10s = 100s) делает healthcheck слишком медленным для быстрых модулей | Средняя | Использовать разные retry для быстрых/медленных модулей: postgres/redis → 4×3s, litellm/langfuse → 10×10s. Либо использовать `wait_for_readiness()` (polling) вместо фиксированных retry |
| R7 | GHCR rate limit для CI workflow (100 pulls/6h для анонимных) | Низкая | CI использует `github.token` для аутентификации → 1000 pulls/6h. VPS уже имеет `ghcr_login()` в deploy-modules.sh |
| R8 | `/tmp/.hermes-build-cache` забивает диск на VPS при частых сборках | Низкая | `/tmp` очищается при перезагрузке. `mode=max` кэширует все слои — ~2-5 GB для L1+L2. Добавить `docker builder prune --keep-storage=10GB` в cron при необходимости |
| R9 | CI workflow build-hermes.yml триггерится на КАЖДЫЙ push в `core/modules/hermes-agent/**` — избыточные сборки при частых коммитах | Низкая | `paths` фильтр уже ограничивает триггер. Добавить `concurrency: cancel-in-progress` для очереди сборок |

$END_DEVPLAN
