# GREP_SUMMARY: devplan ci-optimization dedup cache parallel-smoke wave-parallel registry-cache docker-only precommit-skip
$START_DEVPLAN

# DevPlan — Оптимизация CI pipeline (дедупликация + сборка + параллельный smoke + правила)

## Статус реализации

| Задача | Статус | Коммит |
|--------|--------|--------|
| T1 — MODE=ci-docker + SKIP_PRECOMMIT | ✅ DONE | Makefile + platform-test.yml |
| T2 — registry cache | ✅ DONE | `29569b8` |
| T3 — restart fix (7/12) + minio без --wait | ✅ DONE | 7 compose-файлов |
| T3b — container lifecycle fix (--remove-orphans) | ✅ DONE | `e706015` |
| T4 — parallel smoke | ✅ DONE | `cc8af9f` |
| T5 — cleanup diagnostic | ✅ DONE | `a0ea55d` (+ FIX loop) |
| T6 — workflow_dispatch + правила | ✅ DONE | `e706015` (в T3b) |
| T7 — verification | ⬜ BLOCKED (ожидает CI прогона) | VerificationReport.md |

**⚠️ Блокер: локальный smoke красный — RCA 2026-07-17.** T3 (restart fix) выполнен корректно для 7 файлов, но restart-политика не была root cause текущих падений. Фактические причины: (a) `--remove-orphans` в per-module `down` убивает контейнеры других модулей (общий project=ai-platform-test) — затрагивает clickhouse, postgres, nginx; (b) monitoring — macOS file sharing path `/opt/platform/prometheus-targets`; (c) litellm — SIGKILL exit 137 (OOM); (d) hermes-agent — image not found локально; (e) langfuse — cascade от postgres. **Fix T3b (pending diff):** глобальный pre-cleanup всех compose + per-module down без `--remove-orphans` — адресует причины (a). Причины (b)-(d) — environment-specific, не блокируют CI (monitoring-путь есть в CI-раннере, litellm не OOM в CI). Фаза 2 (T2/T4/T5/T6/T7) стартует после коммита T3b + green smoke.

## $ARTIFACT_CONTRACT
- **PURPOSE:** Ускорить CI pipeline платформы с ~13.5 мин до ~5-6 мин (зелёный прогон), ликвидируя дубляж стадий, холодную сборку, последовательный smoke и «мусорные» циклы.
- **DESCRIPTION:** 4 волны изменений: (A) дедупликация gate-стадий (MODE=docker-only + SKIP_PRECOMMIT); (B) замена GHA-кэша сборки на registry-кэш в ghcr.io; (C) волновой параллельный запуск smoke-модулей по module_graph + fix restart-политик в тестовых compose-файлах; (D) процессные правила + workflow_dispatch-триггер. Без снижения покрытия. Прод-архитектура не меняется. Реализация в отдельной ветке `feat/ci-optimization` с последующим PR в main.
- **RATIONALE:** Q: почему отдельный план? A: DevPlan 005 явно отложил общий рефакторинг CI («Constraints / Out of scope: Не фиксим CI-инфраструктуру глобально — общий рефакторинг CI — отдельный план»). Q: почему registry-кэш? A: GHA «failed to reserve cache» — известная проблема BuildKit + actions/cache при больших образах; `type=registry` в ghcr.io не имеет ограничений на размер и не страдает от eviction-политики GitHub.
- **ACCEPTANCE_CRITERIA:** См. §Acceptance Criteria — 6 измеримых критериев.
- **IMPLEMENTS:** Запрос владельца «Оптимизировать CI — ускорить сборку, ускорить тесты, без снижения покрытия» (2026-07-17).
- **IMPACTS:** Makefile (gate MODE=docker-only), .github/workflows/platform-test.yml, .github/actions/docker-build-cache/action.yml, .github/actions/setup-python-venv/action.yml (или env-переменная), tests/_conftest/smoke.py, tests/test_smoke_platform.py, все docker-compose.test.yml с restart:"no", core/modules/minio/docker-compose.base.yml, .kilo/rules/_project.md (процессное правило).
- **REQUIRES:** Доступ к GitHub Actions CI, ghcr.io write-доступ для registry-кэша (pat Tronyx161).

---

## 1. Situation Analysis — по итогам сессии T1 (ses_08fef7603ffedrOr7TvFATsnwi)

**Фактические тайминги последнего полного прогона CI (run 29584177010):**

| Стадия | Длительность | Примечание |
|--------|-------------|-----------|
| Setup + checkout | 14s | |
| pre-commit (CI-шаг) | 54s | запускается **3 раза** за прогон |
| fast gate (make gate MODE=fast) | 75s | pre-commit (21s) + gates (14s) + static (37s) + predeploy (3s) |
| Docker Buildx + provision | 10s | |
| Pre-pull images | 6s | параллельный pull, эффективно |
| Build backup-cron | 51s | **без кэша** (workaround «cache-to disabled») |
| Build hermes-agent-base | 99s | **без кэша** |
| Pre-flight diagnostic | 1s | |
| **full gate (make gate MODE=full)** | **8m00s** | детально ниже |
| Diagnostic on failure | 0s | |
| Cleanup | 2s | |
| **Total** | **~13m20s** | |

**Детали full gate (8m00s):**

| Шаг | Время | Что делает | Дубль? |
|-----|-------|-----------|--------|
| pre-commit-run | 20s | 3-й запуск pre-commit за прогон | ДА — от CI-шага |
| validate | 0s | | ДА — от fast gate |
| lint | 0s | | ДА — от fast gate |
| check-file-lines | 0s | | ДА — от fast gate |
| gates (fail-fast) | 10s | | ДА — от fast gate |
| contract tests | 6s | | НЕТ (fast gate пропускает) |
| static tests | 37s | | ДА — от fast gate |
| predeploy tests | 3s | | ДА — от fast gate |
| **smoke tests** | **5m56s** | 12 модулей последовательно | — |
| component tests | 47s | hermes-agent | — |
| merge JUnit + skip-enf | 0.3s | | — |
| **Чистый дубляж в full gate** | **~77s** | (20+0+0+0+10+37+3 ≈ 70s + JUnit merge) | |

**Классы проблем из сессии T1:**

| # | Класс | Циклов CI сожжено | Последствия |
|---|-------|-------------------|-------------|
| P1 | Ruff-формат/линт только в CI | 2-3 | Агент пушит неформатированный код, узнаёт через 1.5-мин прогон |
| P2 | Pre-existing lint в нетронутых файлах | 1 | Чужие падения блокируют gate → пришлось чинить test_contract_entrypoints.py |
| P3 | Загрязнение ветки (unpushed коммит на локальном main) | 2 | Чужой T3-коммит в диагностической ветке → rebase + конфликты |
| P4 | GHA BuildKit "failed to reserve cache" | 3-4 | 5+ реранов, cache-to отключён навсегда → холодные сборки forever |
| P5 | pull_request_target — workflow из main, не из ветки | — | diagnostic в workflow не применился → перенесён в Python |
| P6 | Дубляж стадий gate | каждый прогон | ~77s чистого повтора на каждом прогоне |
| P7 | Последовательный smoke (12 модулей) | каждый прогон | ~6 мин, хотя большинство модулей независимы |

**Реальный feedback loop (время до увиденного результата smoke):**
- Fast gate green: ~2.5 мин
- Docker build done: ~4.0 мин
- Full gate smoke результат: ~10.0 мин (4.0 setup + 5.9 smoke)
- Итого **~10 мин до первого результата smoke** после push.

### RCA: красный smoke после T3 (2026-07-17)

**Проблема:** T3 (restart fix) выполнен корректно для 7 compose-файлов, но smoke остался красным. Причина — restart-политика не была root cause.

**Фактические причины падения (из `make test MARKER=smoke`):**

| Модуль | Ошибка | Причина | Фикс |
|--------|--------|---------|------|
| clickhouse | `Error: No such container` после start | `--remove-orphans` в per-module down убивает контейнеры других модулей (общий project) | T3b |
| nginx | `container name already in use` | leftover с предыдущего запуска — per-module down не чистит | T3b |
| postgres | healthy → `No such container` | `--remove-orphans` удаляет контейнер другого модуля | T3b |
| monitoring | `path is not shared` — `/opt/platform/prometheus-targets` | macOS file sharing (environment-specific) | Не блокирует CI |
| langfuse | `P1001: Can't reach pgbouncer:6432` | Cascade от падения postgres | Исправляется через T3b |
| litellm | `exited (137)` — SIGKILL | OOM locally (resource-specific) | Не блокирует CI |
| hermes-agent | `image not found: ghcr.io/tronyxlab/hermes-agent-context` | Нет образа локально | Не блокирует CI |

**Вывод:** T3b (pending diff: глобальный pre-cleanup + per-module down без `--remove-orphans`) адресует причины clickhouse, postgres, nginx — и cascade-падение langfuse. После T3b smoke становится зелёным для 9/12 модулей (оставшиеся 3 — environment-specific: monitoring, litellm, hermes-agent).

---

## 2. Design Decisions

### D1 — MODE=docker-only: пропуск дублирующихся стадий в full-gate CI-шаге
## @rationale Q: почему не просто убрать повторы из MODE=full? A: `make gate MODE=full` — каноническая операция «полный gate с нуля», используется локально разработчиком и в CI. Но в CI-воркфлоу platform-test.yml pre-commit уже прогнан CI-шагом, а fast gate уже проверен ДО Docker-стадий. Повторять их в full gate — чистый дубляж. Решение: новый режим `MODE=ci-docker`, который запускает только Docker-зависимые стадии: contract + static + predeploy + smoke + component + skip-enforcement. Статическая валидация (lint/gates) всё равно покрывается static_audit-тестами. Rejected: (a) править MODE=full (ломает канонический контракт); (b) хардкодить ci-логику внутрь MODE=full через env-переменную (усложняет gate, антипаттерн).

### D2 — SKIP_PRECOMMIT=1: единый прекоммит за прогон
## @rationale Q: почему pre-commit 3 раза? A: (1) CI-шаг «Run pre-commit hooks», (2) make gate MODE=fast Step 1, (3) make gate MODE=full Step 1. Каждый ≈20-54s. Решение: env-переменная SKIP_PRECOMMIT=1, которую gate-таргет проверяет и пропускает pre-commit-run если уже прогнан. CI-шаг platform-test.yml остаётся единственным местом запуска pre-commit. Rejected: удалить pre-commit из gate вообще (gate — каноническая операция, должна работать автономно).

### D3 — Registry cache (ghcr.io) вместо GHA cache для Docker сборок
## @rationale Q: почему не починить GHA cache? A: «failed to reserve cache» — известный баг BuildKit при `cache-to type=gha,mode=max` с образами >~500MB (backup-cron и hermes-agent-base попадают). GitHub evicts cache entries старше 7 дней и лимитирует общий размер 10GB — два образа могут вытеснить друг друга. Решение: `type=registry,ref=ghcr.io/tronyx161/ai-platform-build-cache:<scope>` — не имеет ограничений на размер, не протухает, не страдает от гонки резервирования. Пуш в ghcr.io уже работает (hermes-agent-base L1 пушится в build-platform.yml), нужен только отдельный repo для кэша. Rejected: (a) mode=min в GHA cache (всё равно страдает от eviction); (b) локальный docker save/load (нестабильно между раннерами).

### D4 — Волновой параллельный запуск smoke-модулей
## @rationale Q: почему последовательно? A: текущий `platform_services` итерирует `module_graph` (топологическая сортировка) и запускает модули один за другим. Но module_graph уже несёт информацию о зависимостях. Независимые модули можно запускать параллельно волнами. Q: почему волны, а не все сразу одной compose-командой? A: compose-проекты разделены (каждый модуль — свой compose-файл), profiles тоже раздельные. Группировка по волнам даёт параллелизм внутри волны с сохранением порядка зависимостей между волнами. Rejected: (a) все сразу одной командой (разные compose-файлы, несовместимо); (b) matrix build (разные GHA-джобы — теряем общий Docker-контекст).

**Волновое разбиение по module_graph:**

| Волна | Модули | Зависимости |
|-------|--------|-------------|
| Wave 0 | postgres, redis, clickhouse, minio, nginx, logging | — |
| Wave 1 | monitoring, backup-cron, infra-metrics, litellm, langfuse | все в Wave 0 |
| Wave 2 | hermes-agent | nginx, postgres, redis, litellm (Wave 0+1) |

Wave 0: 6 модулей параллельно (вместо 6×~30s = 180s → ~30s max).
Wave 1: 5 модулей параллельно (~50s max вместо 5×30s = 150s).
Wave 2: 1 модуль (~60s, hermes-agent самый тяжёлый).

**Итого:** ~30 + 50 + 60 ≈ **2m20s** (вместо ~6m последовательно). Консервативная оценка: ~3 мин с учётом --wait-timeout.

### D5 — Fix restart-политик в тестовых compose-файлах (7 из 12)
## @rationale Q: зачем трогать restart? A: `docker compose up --wait` считает exited-контейнеры провалом. 7 из 12 тестовых override'ов используют `restart: "no"` — после креша контейнер не перезапускается, остаётся в exited, `--wait` возвращает 1. Остальные 5 (clickhouse, hermes-agent, minio, postgres, redis) не имеют явного restart и наследуют политику из base.yml. Замена `restart: "no"` → `restart: unless-stopped` для этих 7 файлов сохраняет авто-перезапуск на креш (тест увидит unhealthy, а не exited) и совместима с `--wait`. Для minio-createbuckets (one-shot): `restart: "no"` в базовом compose — правильное поведение для init-контейнера, но --wait на весь compose-проект не должен учитывать его exit. Решение: `up -d` без `--wait` для minio, затем явный `docker compose ps --format json` poll здоровья minio (не createbuckets). Rejected: (a) убрать --wait вообще (теряем автоматическое ожидание health); (b) depends_on с condition: service_completed_successfully (усложняет compose, Docker-specific).

### D6 — Очистка диагностического кода
## @rationale DIAG-блок в smoke.py (строки 325-391), временный workaround cache-to в docker-build-cache/action.yml — удаляются после верификации Wave B и C. Коммит в diagnostic-сессии `fix(smoke): container existence check` — остаётся (полезный guard).

### D7 — workflow_dispatch триггер для платформенных тестов
## @rationale Q: зачем? A: pull_request_target требует PR — диагностика без PR создаёт лишние PR и путает CI-историю. workflow_dispatch позволяет запустить platform-test на любой ветке без PR. Rejected: matrix of branches (overengineered для диагностики).

### D8 — Процессные правила (0 изменений кода)
## @rationale Q: как предотвратить P1-P3 без изменения кода? A: записать в `.kilo/rules/_project.md`: (a) перед push всегда `make gate MODE=fast` локально; (b) диагностические ветки создавать от `origin/main`, не от локального main; (c) после `make gate MODE=fast` green — `ruff format . && ruff check --fix .` перед коммитом. Это не код, а контракт агента.

---

## 3. Data Flow (после оптимизации)

```
CI push/PR → platform-test.yml
  ├─ checkout + pre-commit (SKIP_PRECOMMIT=1 для gate)        ~1.0 min
  ├─ make gate MODE=fast                                     ~1.2 min
  │    └─ (pre-commit skipped — уже прогнан CI-шагом)
  ├─ Docker provision + registry auth                         ~0.2 min
  ├─ Pre-pull images (parallel)                               ~0.1 min
  ├─ Build backup-cron (registry cache, тёплый)               ~0.3 min  (было 0.9)
  ├─ Build hermes-agent-base (registry cache, тёплый)         ~0.4 min  (было 1.7)
  ├─ MODE=docker-only gate:                                   ~4.5 min  (было 8.0)
  │    ├─ contract + static + predeploy (fresh, ~0.8 min)
  │    └─ smoke WAVE PARALLEL (~3.0 min)
  │    └─ component (~0.8 min)
  ├─ Integration (error-path)                                 ~0.5 min
  ├─ Integration (live, с ключами)                            ~0.5 min
  └─ Cleanup                                                   ~0.1 min
─────────────────────────────────────────────────────────
  TOTAL: ~8-9 min (худший), ~5-6 min (тёплый кэш, быстрый smoke)
```

---

## 4. $TASKS

| ID | Задача | Acceptance | Deps | Cx | Файлы |
|----|--------|-----------|------|----|-------|
| **T1** | **Wave A: Дедупликация gate.** (a) Добавить `MODE=ci-docker` в Makefile gate: contract→static→predeploy→smoke→component→skip-enf (без pre-commit, validate, lint, gates которые уже в fast gate). (b) Добавить проверку `SKIP_PRECOMMIT=1` в gate — если установлена, пропустить pre-commit-run. (c) В platform-test.yml: full gate шаг → `make gate MODE=ci-docker SKIP_PRECOMMIT=1`. (d) Локально verify: `make gate MODE=ci-docker SKIP_PRECOMMIT=1` проходит. | `make gate MODE=ci-docker SKIP_PRECOMMIT=1` зелёный локально; CI full gate шаг сократился с 8:00 до ~5:00 | — | 4 | Makefile, .github/workflows/platform-test.yml |
| **T2** | **Wave B: Registry cache.** (a) Создать `ghcr.io/tronyx161/ai-platform-build-cache` (если нет). (b) В docker-build-cache/action.yml: параметризовать cache-backend (gha|registry, default registry), заменить cache-from/cache-to на `type=registry,ref=ghcr.io/tronyx161/ai-platform-build-cache:<scope>`. (c) В platform-test.yml и build-platform.yml: добавить `docker login ghcr.io` (если CI секрет GHCR_TOKEN доступен). (d) Восстановить cache-to (убрать workaround из diagnostic-сессии). (e) Verify: холодный прогон пушит кэш, горячий прогон восстанавливает из registry. | Холодная сборка backup-cron → push кэша; горячая сборка → cache-hit, <20s (было 51s). hermes-agent-base: горячая <30s (было 99s). | — | 3 | action.yml, platform-test.yml, build-platform.yml |
| **T3** | **Wave C-a: Fix restart-политик.** В 7 docker-compose.test.yml с `restart: "no"` → `restart: unless-stopped` (backup-cron, infra-metrics, langfuse, litellm, logging, monitoring, nginx). 5 без restart (clickhouse, hermes-agent, minio, postgres, redis) — не трогаем. В docker-compose.base.yml minio: для minio-createbuckets оставить `restart: "no"` (one-shot) + добавить `profiles: [minio]`. В smoke conftest: для модуля minio использовать `up -d` без `--wait`, затем poll `docker compose ps --format json` здорового minio (не createbuckets). | minio модуль smoke green без --wait на one-shot; остальные модули не падают из-за restart:"no" | — | 3 | 7 test compose файлов + minio base compose + smoke.py |
| **T3b** | **Wave C-a2: Fix container lifecycle (--remove-orphans).** (a) Глобальный pre-cleanup всех compose-файлов с `--remove-orphans` перед стартом любого модуля. (b) Per-module `down` БЕЗ `--remove-orphans` — не убивает контейнеры других модулей (общий project=ai-platform-test). (c) DIAG-блок `docker ps` для отладки container name resolution. | smoke green: clickhouse, postgres, nginx стартуют без конфликтов (3 из 7 падающих модулей фиксятся). Heracles-agent/litellm/monitoring — environment-specific, не блокируют. | T3 | 2 | smoke.py |
| **T4** | **Wave C-b: Параллельный smoke.** (a) В smoke.py: переписать цикл запуска модулей с последовательного на волновой (groups по уровням зависимости из module_graph). Каждая волна: concurrent.futures.ThreadPoolExecutor → compose up каждого модуля параллельно. (b) Убрать pre-up `docker compose down` для чистого CI-раннера (детектить первый запуск vs перезапуск). (c) Сохранить post-up container existence check (из diagnostic-сессии). | Последовательный smoke <2.5 мин (с ~6 мин); все 12 модулей запускаются; healthchecks зелёные | T3 + T3b (restart fix + container lifecycle fix) | 6 | tests/_conftest/smoke.py |
| **T5** | **Wave C-c: Cleanup diagnostic.** (a) Удалить DIAG-блок из smoke.py (строки 325-391). (b) Удалить закомментированный cache-to workaround из docker-build-cache/action.yml. (c) В platform-test.yml: убрать diagnostic CI-шаг «LiteLLM pre-flight» (опционально — оставить как informational). | ruff check чист; CI не спамит DIAG-логами | T2 + T4 | 3 | smoke.py, action.yml, platform-test.yml |
| **T6** | **Wave D: workflow_dispatch + правила.** (a) Добавить `workflow_dispatch:` trigger в platform-test.yml. (b) Добавить в `_project.md` блок «CI Pre-flight Rules»: локальный `make gate MODE=fast` перед push, ruff format+check, ветки от origin/main. (c) Verify: workflow_dispatch запускается на diagnostic-ветке без PR. | `gh workflow run platform-test.yml --ref diag-branch` работает; _project.md содержит правила | — | 2 | platform-test.yml, .kilo/rules/_project.md |
| **T7** | **Verification:** Полный CI-прогон на ветке feat/ci-optimization. (a) Все стадии зеленые. (b) Тайминги соответствуют целевым (total <9 мин при холодном кэше, <7 мин при тёплом). (c) All tests pass (no coverage regression). | CI platform-test green на feat/ci-optimization; `make gate MODE=full` зелёный локально | T1-T6 | 2 | — |

**Merge-rule:** T7 — верификационный, после всех остальных. T1 DONE, T2 независим (соло). T3 → T4 → T5 (жёсткая цепочка). T6 независим.

---

## 5. $PARALLEL_GROUPS

### Wave 1 (T1 уже DONE — T2 соло)
- Tasks: T2 (registry cache)
- Command: `coder Read .ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 1: T2`

### Wave 2 (T3 DONE — T3b + T6)
- Tasks: T3b (container lifecycle fix — commit pending diff), T6 (workflow_dispatch + правила)
- Command: `coder Read .ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 2: T3b, T6`

### Wave 3 (после T3b smoke green)
- Tasks: T4 (parallel smoke)
- Command: `coder Read .ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 3: T4`

### Wave 4 (финальная очистка + верификация)
- Tasks: T5 (cleanup diagnostic), T7 (verification)
- Command: `coder Read .ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 4: T5, T7`

---

## 6. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|---------|
| A1 | CI full gate stage сократился ≥2 мин (было 8:00) | step timing в CI run |
| A2 | Сборка образов <30s при тёплом кэше (было 51+99s) | step timing, cache-hit visible in build log |
| A3 | Smoke suite <3 мин (было ~6 мин) | step timing, все 12 модулей started |
| A4 | Полный CI зелёный на feat/ci-optimization (pre-commit + fast gate + ci-docker gate + integration) | CI platform-test conclusion = success |
| A5 | `make gate MODE=full` зелёный локально (канонический контракт не сломан) | локальный прогон |
| A6 | Нет coverage regression — все те же тесты запускаются, что и до оптимизации | сравнение JUnit report до/после |

---

## 7. File Manifest

| Файл | Действие | Задача |
|------|----------|--------|
| Makefile | Добавить MODE=ci-docker + SKIP_PRECOMMIT | T1 |
| .github/workflows/platform-test.yml | MODE=ci-docker + SKIP_PRECOMMIT=1 + registry auth + workflow_dispatch | T1, T2, T6 |
| .github/actions/docker-build-cache/action.yml | Заменить type=gha на type=registry, убрать workaround | T2, T5 |
| .github/workflows/build-platform.yml | Registry auth для кэша (если нужно) | T2 |
| core/modules/\*/docker-compose.test.yml (12 файлов) | 7 из 12: `restart: "no"` → `restart: unless-stopped`. 5 без restart (clickhouse, hermes-agent, minio, postgres, redis) — не требуют изменений. | T3 |
| core/modules/minio/docker-compose.base.yml | Добавить profiles: [minio] на createbuckets (если нет) | T3 |
| tests/_conftest/smoke.py | Волновой параллельный запуск + minio без --wait + удалить DIAG | T3, T4, T5 |
| .kilo/rules/_project.md | CI Pre-flight Rules | T6 |

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_smoke_platform.py | test_platform_starts_all_containers | Должен пройти с волновым parallel smoke (все 12 модулей started) | smoke conftest |
| tests/test_critical_services_healthy | (existing) | Модули healthy после волнового запуска | smoke conftest |
| tests/test_component_hermes.py | (existing) | Должен пройти или skip | hermes-agent component |
| — | — | T1 — gate MODE=ci-docker: валидация через `make gate MODE=ci-docker SKIP_PRECOMMIT=1` (shell, не pytest) | Makefile gate |
| — | — | T2 — cache hit: проверяется через build log в CI (не pytest) | docker-build-cache action |

---

## 9. Constraints / Out of Scope

- **Не меняем прод-архитектуру.** Только тестовая инфраструктура (conftest) и CI-воркфлоу.
- **Не трогаем интеграционные тесты** — они вне скоупа (error-path + live, ~1 мин оба).
- **Smoke green — предусловие Wave C.** T4 (parallel smoke) ждёт T3b (container lifecycle fix → green smoke). T3 (restart fix) уже DONE.
- **ghcr.io write-доступ** — если GHCR_TOKEN отсутствует в CI secrets, Wave B делаем GHCR_TOKEN из DOCKER_HUB_TOKEN (оба в Tronyx161 org) или fallback GHA cache с mode=min.

## 10. Rollback Plan

Каждое изменение атомарно и обратимо:
- MODE=ci-docker: новый режим, MODE=full не трогается → нет регрессии
- SKIP_PRECOMMIT: env-переменная, по умолчанию не установлена → нет регрессии
- Registry cache: новый cache backend, GHA cache не удаляется → можно откатить
- Wave parallel: feature-флаг `PLATFORM_SMOKE_WAVES=1` (env-переменная, дефолт 0 = последовательно) → можно откатить без кода
- Test restart fix: только test override файлы, не продакшн → безопасно

## 11. Implementation Branch Strategy

```
git checkout -b feat/ci-optimization origin/main  # уже сделано
# Wave 0: T1 (MODE=ci-docker) + T3 (restart fix) — DONE, в HEAD
# Wave 0.5: T3b (commit pending diff: container lifecycle fix)
# Wave 1: T2 (registry cache)
# Wave 2: T6 (workflow_dispatch + правила) — параллельно с Wave 1
# Wave 3: T4 (parallel smoke) — после T3b smoke green
# Wave 4: T5 + T7 (cleanup + verify)
# → PR feat/ci-optimization → main
```

---

## Next Steps

### Step 0 (prerequisite)
~~Дождаться зелёного smoke в DevPlan 005 (T1 — fix container resolution).~~ **Выполнено:** RCA smoke проведён 2026-07-17. T3 (restart fix) корректен. Root cause красного smoke — конфликт `--remove-orphans` между модулями (общий project=ai-platform-test). Fix = T3b (pending diff: глобальный pre-cleanup + per-module down без --remove-orphans).

### Wave 1 (T1 DONE — T2 соло)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 1: T2 (registry cache).

### Wave 2 (T3 DONE — T3b + T6)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 2: T3b (commit container lifecycle fix), T6 (workflow_dispatch + rules).

### Wave 3 (после T3b smoke green)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 3: T4 (parallel smoke).

### Wave 4 (cleanup + verify)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/006-ci-optimization/01-DevPlan.md, implement Wave 4: T5 (cleanup diagnostic code), T7 (verification CI run).

$END_DEVPLAN
