# 01-Brief — Волна 120: единая проверочная система check-suite

<!-- GREP_SUMMARY: check-suite, check-all, check-fast, SoT-манифест, кэш-результатов, нейминг, preflight, gate, drift-покрытия, бенчмарк -->
# STRUCTURE: ┌проблема┐ → ◇ замеры → ◇ целевой дизайн (манифест → executor'ы → кэш → changed-only) → ◇ нейминг check-* → ⊕ волны 0-4 → ⎋ AC/риски/импорты

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Устранить потерю времени агента и разработчика на верификацию (30 мин на бриф в лучшем случае, часы при баге) через единую проверочную систему: один SoT-манифест набора проверок (check-suite.yaml), два executor'а (диагностический параллельный + канонический последовательный), кэш результатов с инвалидацией по watch-скоупам, инкрементальный режим по затронутым файлам и единый нейминг линейки `check-*`.
DESCRIPTION:           Волна 120 — системная замена трёх расходящихся hardcoded списков проверок (makefiles/ci.mk gate, core/internal/preflight.py фаза 3, CI-workflows + ручные команды в инструкциях) на единый манифест `core/check-suite.yaml`. Из манифеста работают: `make check-all` (экс-preflight, параллельная диагностика + автофикс), `make check` (быстрый инкрементальный, кэш + changed-only), `make check-fast/check-full/check-docker` (экс-gate MODE=fast/full/ci-docker, каноническая последовательная верификация без кэша). Кэш результатов по sha256 watch-скоупов превращает повторные прогоны фикс-цикла из ~5 мин в 10-60s. Нейминг приводится к единому стилю `check-*` с deprecated-алиасами (прецедент: compose-safe-up).
RATIONALE:             (1) Замер: `make preflight SKIP_FIX=1` на чистом рабочем дереве > 4 минут, фаза 3 не завершилась (остановлен принудительно) — документация обещает 20-40s; параллелизм даёт не скорость, а сбор всех ошибок за один проход. (2) Дрейф: 3 независимых hardcoded списка проверок (ci.mk, preflight.py, workflows+инструкции) — check-manifests и ruff check . не входят ни в preflight, ни в gate fast; gates requires_docker — только в gate. (3) Цикл кодера = 2+ полных прогона (preflight → gate fast) + ручная фаза (check-manifests + ruff) по инструкции _project.md п.2-4, плюс двойной pre-commit (preflight фаза 2 + gate fast шаг 1). (4) Боль пользователя: 30 мин на бриф в лучшем случае, часы при баге — доминирует верификация, а не фиксы. (5) Docker на dev-машине стабилен — можно включить requires_docker гейты в локальную диагностику. (6) Единый стиль: глагол `verify` занят (HTTPS-верификация доменов, `make verify NODE=<node>`) — коллизия исключена выбором линейки `check-*`.
ACCEPTANCE_CRITERIA:   AC-1: preflight.py и ci.mk gate НЕ содержат hardcoded списков проверок — оба читают core/check-suite.yaml; гейт test_gate_check_suite_consistency блокирует расхождение. AC-2: дыры закрыты — check-manifests, ruff check . и gates requires_docker входят в диагностический прогон check-all. AC-3: повторный check-all без изменений рабочего дерева < 30s; повторные прогоны фикс-цикла < 60s (кэш Wave 2); упавшие чеки никогда не берутся из кэша. AC-4: каноническая верификация (check-fast/CI/pre-push hook) — без кэша, семантика gate не изменена. AC-5: нейминг-миграция — 0 упоминаний `make gate`/`make preflight` в .kilo/* и AGENTS.md (гейт phantom-refs); deprecated-алиасы работают. AC-6: `make check-fast && make check-manifests && ruff check .` зелёные (аналог AC-GLOBAL-1 волны 119). AC-7: инструкция кодера (_project.md) переписана: per-task `make check` (changed-only), фикс-цикл `make check-all`, финальная верификация `make check-fast`. AC-8: оба CI-workflow (platform-gate-fast, platform-test) и pre-push-gate.sh используют новые имена.
IMPLEMENTS:            U-новые (не зарегистрированы ранее): U-120-1 (время верификации), U-120-2 (дрейф покрытия), U-120-3 (нейминг). Связано: DevPlan 098 (test_runner), DevPlan 060 (Repair Contract), DevPlan 097 (doxygen), инвариант 11 AGENTS.md (Manifest Generation Contract), TRAP[DECISION] 2026-07-31 (parity-гейты).
IMPACTS:               Makefile (makefiles/ci.mk, makefiles/repair.mk, makefiles/manifest.mk), core/internal/preflight.py, core/check-suite.yaml (новый), core/entrypoint-manifest.yaml (allowed_verbs/gates), core/AGENTS.md (каноническая таблица), tests/gates/AGENTS.md (раздел Preflight), tests/AGENTS.md, .kilo/rules/_project.md, .kilo/agents/code.md, .github/workflows/platform-gate-fast.yml, .github/workflows/platform-test.yml, core/entrypoints/pre-push-gate.sh, README.md, root AGENTS.md (глоссарий).
REQUIRES:              Wave 0 (замеры per-check таймингов) до проектирования кэша; подтверждённый стабильный локальный Docker; решение пользователя по неймингу зафиксировано в §5 (check-*); DevPlan 120 после ревью брифа.
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и проблема

### 1.1 Боль пользователя

- Бриф без багов: ~30 минут, из них доминирует верификация (preflight ~5 мин + gate fast ~5 мин + ручные check-manifests/ruff + per-task прогоны).
- Баг: часы — каждый фикс-цикл = полный повторный прогон всего набора (~10 мин × N итераций), потому что preflight перезапускает ВСЁ при каждом изменении.
- Время теряется и локально (фикс-цикл), и в CI (push → fail → fix → push): ответ пользователя 2026-08-02 — «оба примерно поровну».

### 1.2 Факты замера (2026-08-02, эта сессия)

| Замер | Результат |
|-------|-----------|
| `make preflight SKIP_FIX=1 WORKERS=6` на чистом дереве | Фаза 3 (8 параллельных проверок) не завершилась за >4 мин — остановлен принудительно |
| Документация (tests/gates/AGENTS.md) | Заявляет «~20-40s» на фазу 3 — расхождение с фактом |
| Вывод | Параллелизм (ThreadPoolExecutor) экономит циклы, не секунды: время = max(самые медленные чеки: static_audit timeout 300s, contract/predeploy/gates timeout 180s) |

### 1.3 Дрейф покрытия (корневая причина)

Три независимых hardcoded списка «что такое быстрая проверка»:

| Источник | Список | Пробелы |
|----------|--------|---------|
| makefiles/ci.mk gate fast | pre-commit, validate, check-dead-code, check-exception-patterns, doxygen-check, gates static+docker, contract, static_audit, predeploy | check-manifests, ruff check . |
| core/internal/preflight.py фаза 3 | validate, check-dead-code, check-exception-patterns, doxygen-check, gates static (без docker), contract, static_audit, predeploy | check-manifests, ruff check ., gates requires_docker |
| workflows + .kilo/rules/_project.md | gate fast + check-manifests (CI); preflight → gate fast → check-manifests → ruff check . (агент) | ручная синхронизация |

Каждая новая проверка должна попасть в 4 места вручную. Следствие: doxygen-check уже в трёх (DevPlan 097), check-manifests — ни в одно.

### 1.4 Проблемы инструкции кодера (_project.md, не тестирована, закоммичена ed41a25)

1. Цикл = 2+ полных прогона (preflight → gate fast) + ручная фаза после gate (п.4: check-manifests + ruff check .).
2. Двойной pre-commit: preflight фаза 2 + gate fast шаг 1 — каждый прогон (~10-20s). В CI решено SKIP_PRECOMMIT=1, локально — нет.
3. SKIP_FIX=1 существует, но не упоминается: каждый повторный preflight заново гоняет fix-gate + pre-commit (~20s).
4. «Быстрые статические проверки» п.3 частично избыточны: doxygen-check и LOC-гейт уже в preflight фаза 3.

---

## 2. Целевой дизайн

### 2.1 SoT-манифест `core/check-suite.yaml`

```yaml
checks:
  - id: pre-commit              # автофикс гигиены + verify
    cmd: make pre-commit-run
    tier: fix                   # фаза автофикса (последовательно)
    timeout: 120
  - id: validate
    cmd: bash core/entrypoints/validate.sh
    tier: static
    timeout: 60
  - id: check-dead-code
    cmd: make check-dead-code
    tier: static
    timeout: 60
  - id: check-exception-patterns
    cmd: make check-exception-patterns
    tier: static
    timeout: 30
  - id: doxygen-check
    cmd: make doxygen-check
    tier: static
    timeout: 30
  - id: check-manifests          # ← дыра закрыта
    cmd: make check-manifests
    tier: static
    timeout: 60
  - id: ruff-check               # ← дыра закрыта
    cmd: .venv/bin/ruff check .
    tier: static
    timeout: 60
  - id: gates-static
    cmd: pytest tests/gates/ -m "gate and not requires_docker" -n auto
    tier: pytest
    watch: [tests/, core/, Makefile]
  - id: gates-docker             # ← теперь входит (Docker стабилен)
    cmd: pytest tests/gates/ -m "gate and requires_docker"
    tier: pytest
    watch: [tests/, core/]
  - id: contract
    cmd: test_runner --marker contract
    tier: pytest
    watch: [tests/, core/]
  - id: static_audit
    cmd: test_runner --marker static_audit
    tier: pytest
    watch: [tests/, core/]
  - id: predeploy
    cmd: pytest tests/ -m "predeploy and not requires_docker"
    tier: pytest
    watch: [tests/, core/, node-configs/]
```

Поля: `id` (kebab-case), `cmd`, `tier` (fix|static|pytest), `timeout`, `watch` (паттерны путей для кэша и changed-only), опционально `docker: true`, `repair` (из Repair Contract DevPlan 060).

### 2.2 Два executor'а из одного манифеста

| Executor | Таргет | Поведение |
|----------|--------|-----------|
| Диагностический (параллельный, автофикс, кэш) | `make check-all` (экс-preflight) | tier=fix последовательно → tier=static/pytest параллельно (ThreadPoolExecutor) → единый отчёт; кэш включён |
| Канонический (последовательный, fail-fast, без кэша) | `make check-fast` / `check-full` / `check-docker` (экс-gate MODE=...) | Шаги в каноническом порядке из манифеста; CI и pre-push hook вызывают только его |

Инвариант «preflight НЕ заменяет gate» переформулируется системно: *два executor'а одного манифеста; диагностический — параллельный акселератор, канонический — арбитр; дрейф невозможен конструктивно*. Инвариант усиливается, не ослабляется.

### 2.3 Кэш результатов (главный выигрыш по времени)

- Файл кэша: `$(git rev-parse --git-dir)/check-cache.json` (не коммитится, не мусорит в дереве).
- Ключ: `sha256(id + watch-скоуп)`, где watch-скоуп = содержимое файлов под паттернами `watch` + git diff.
- Правила:
  1. Кэш применяется ТОЛЬКО к чекам, прошедшим на идентичном хеше. Упавший чек всегда перезапускается.
  2. `make check-fast` (канонический), CI и pre-push hook — без кэша, всегда полный прогон.
  3. `--no-cache` / `CACHE=0` для сомнений.
  4. Инвалидация всего кэша при изменении манифеста.

Ожидаемый эффект на цикл кодера:
| Шаг | Сейчас | После |
|-----|--------|-------|
| 1-й check-all | ~5 мин | ~5 мин (полный) |
| фикс → 2-й check-all | ~5 мин | 10-60s (только затронутое) |
| фикс → 3-й check-all | ~5 мин | 10-60s |
| зелёный check-all → check-fast | ~5 мин | ~5-15s (весь набор в кэше; pre-commit кэшируется — двойной прогон исчезает) |
| Баг-цикл (2 итерации) | 4×5 мин = 20 мин | 5 мин + 2×1 мин ≈ 7 мин |

### 2.4 Инкрементальный режим changed-only

`make check` (новый): запускает только чеки, чей watch-скоуп пересекается с текущим diff. Для задачи «изменил один .py» — ruff + gates-static + static_audit ≈ 1-2 мин вместо 5. Полнота гарантируется каноническим прогоном перед push (pre-push hook — уже существует).

---

## 3. Нейминг: единая линейка `check-*`

### 3.1 Обоснование

- Доминирующий стиль платформы: `[глагол]-[объект]` kebab-case (check-manifests, check-dead-code, fix-gate, generate-manifests, deploy-project).
- `verify` НЕЛЬЗЯ: занят HTTPS-верификацией доменов (`make verify NODE=<node>`, core/AGENTS.md) — коллизия.
- `preflight` — вне стиля и коллизирует с bootstrap-preflight (core/internal/bootstrap/preflight.py).
- Прецедент deprecated-алиаса в платформе: `compose-safe-up` → `up-safe`.

### 3.2 Целевая схема (рекомендуемая)

| Старое | Новое | Роль | Миграция |
|--------|-------|------|----------|
| `make preflight` | `make check-all` | Полная параллельная диагностика + автофикс (экс-фазы 1-3) | alias preflight → check-all, deprecated |
| `make preflight SKIP_FIX=1` | `make check` | Быстрый инкрементальный read-only (кэш + changed-only) | — (новый таргет) |
| `make gate MODE=fast` | `make check-fast` | Каноническая верификация без Docker | alias gate → check-full, deprecated |
| `make gate MODE=full` | `make check-full` | Полная каноническая (smoke+component) | см. выше |
| `make gate MODE=ci-docker` | `make check-docker` | Docker-режим | см. выше |
| единичные check-* | без изменений | check-manifests, check-dead-code, check-exception-patterns, check-profiles-parity, check-domain-parity, check-env-defaults, check-file-lines | — |

Не трогаем: `make verify` (коллизия), линейку `test-*` (test, test-node, test-inventory-sync — отдельная семантика; конвергенция test-summary — отдельное решение), линейку `fix-*` (автофиксы — отдельная семантика).

### 3.3 Миграция

1. Makefile: новые таргеты + deprecated-алиасы (как compose-safe-up).
2. entrypoint-manifest.yaml: allowed_verbs пополняется новыми именами, старые помечаются deprecated.
3. Глоссарий root AGENTS.md — регенерируется (G4).
4. pre-push-gate.sh, оба workflow, .kilo/rules/_project.md, .kilo/agents/code.md — новые имена.
5. Гейт phantom-refs: 0 упоминаний `make gate`/`make preflight` в .kilo/* и AGENTS.md (по образцу test_gate_phantom_refs.py).

---

## 4. Волны внедрения

### Wave 0 — Замеры и baseline (диагностика, ~1 сессия)
- Полный прогон `make preflight` (без SKIP_FIX) до завершения; сбор duration_ms по каждому из 8 чеков из отчёта (поле уже есть).
- Замер `make gate MODE=fast` по шагам (по [IMP:7] логам).
- Выявление «медленных» чеков (static_audit/contract/predeploy/gates) и кандидатов на ускорение (xdist внутри test_runner).
- Фиксация baseline в DevPlan 120 (цифры до/после).

### Wave 1 — Манифест + два executor'а (ядро)
- Новый `core/check-suite.yaml` (чек-лист из §2.1).
- preflight.py: читает манифест вместо hardcoded списка; дыры закрыты (check-manifests, ruff check ., gates-docker в диагностику).
- ci.mk gate fast/full/ci-docker: шаги из манифеста (порядок канонический).
- Гейт `test_gate_check_suite_consistency`: AST/парсинг preflight.py и ci.mk — 0 hardcoded команд вне манифеста (по образцу parity-гейтов 116 T9).
- Регистрация в entrypoint-manifest.yaml, обновление core/AGENTS.md, tests/gates/AGENTS.md.
- Без кэша: уже убирает ручные check-manifests/ruff из инструкции (дыры закрыты).

### Wave 2 — Кэш результатов
- Реализация кэша (§2.3): watch-скоупы, инвалидация, `--no-cache`, JSON в git-dir.
- Тесты: кэш-хит при неизменном хеше; кэш-мисс при изменении; упавший чек не кэшируется; инвалидация по манифесту.
- Метрика AC-3: повторный прогон без изменений < 30s.

### Wave 3 — Инкрементальный режим + инструкции
- `make check` (changed-only по watch-скоупам).
- Переписывание .kilo/rules/_project.md (раздел «Верификация реализации») под новый цикл: per-task `make check` → фикс-цикл `make check-all` (с SKIP_FIX/CACHE) → финально `make check-fast`.
- .kilo/agents/code.md: batched verification → check-all.

### Wave 4 — Нейминг-миграция check-*
- Таргеты §3.2, deprecated-алиасы, allowed_verbs, глоссарий.
- pre-push-gate.sh, platform-gate-fast.yml, platform-test.yml.
- Гейт phantom-refs на старые имена в инструкциях.

---

## 5. Риски и защиты

| Риск | Защита |
|------|--------|
| Кэш скрывает регрессию (ложный зелёный) | Кэш только для passed-чеков на идентичном хеше; канонический прогон/CI/pre-push — всегда полные; `--no-cache` |
| Медленный чек (static_audit 300s) доминирует в check-all | Wave 0 замер → точечное ускорение (xdist); параллелизм уже есть |
| Переименование ломает CI/hook/манифест | Deprecated-алиасы (прецедент compose-safe-up); гейты phantom-refs; Wave 4 — последняя, на зелёной базе |
| gates-docker в локальной диагностике капризничает | Docker стабилен (подтверждено); в диагностике best-effort (FAIL — только в каноническом) |
| Дрейф манифеста vs реальные команды | Гейт консистентности (Wave 1) + check-manifests-подобная сверка |
| Изменение канонического gate (риск регрессии) | Канонический executor без кэша, тот же порядок шагов, CI-семантика не меняется |

---

## 6. Файл-манифест (предварительный)

| Файл | Действие |
|------|----------|
| `core/check-suite.yaml` | NEW — SoT набора проверок |
| `core/internal/preflight.py` | MOD — чтение манифеста, кэш, changed-only |
| `makefiles/repair.mk` | MOD — check/check-all таргеты + алиасы |
| `makefiles/ci.mk` | MOD — gate шаги из манифеста, check-fast/full/docker |
| `core/internal/scripts/generate_check_suite.py` (или ручная валидация) | NEW — генератор/валидатор манифеста (опционально, в стиле G1-G6) |
| `tests/gates/test_gate_check_suite_consistency.py` | NEW — гейт консистентности |
| `tests/gates/test_gate_phantom_refs.py` | MOD — старые имена в запрет |
| `core/entrypoint-manifest.yaml` | MOD — новые глаголы + deprecated |
| `core/AGENTS.md`, `tests/gates/AGENTS.md`, `tests/AGENTS.md` | MOD — документация |
| `.kilo/rules/_project.md`, `.kilo/agents/code.md` | MOD — новый цикл верификации |
| `.github/workflows/platform-gate-fast.yml`, `platform-test.yml` | MOD — новые имена |
| `core/entrypoints/pre-push-gate.sh` | MOD — check-fast |
| `README.md`, root `AGENTS.md` (глоссарий) | MOD |

---

## 7. Итоговая оценка выигрыша

| Сценарий | Сейчас | После 120 |
|----------|--------|-----------|
| Чистый бриф (реализация + верификация) | ~30 мин (5 preflight + 5 gate + 5 ручных) | ~8-12 мин (5 check-all + 0.2 check-fast из кэша) |
| Баг, 2 фикс-итерации | 60+ мин (4 полных прогона) | ~20-25 мин (1 полный + 2 инкрементальных) |
| CI-цикл push→fail→fix→push | ~12 мин × 2 (gate + check-manifests) | те же (канонический без кэша), но реже падает — дыры закрыты; setup-кэш (pre-commit/.venv) в Wave 4 |

$END_BRIEF
