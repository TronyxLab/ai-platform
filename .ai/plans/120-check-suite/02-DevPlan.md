# 02-DevPlan — Волна 120: единая проверочная система check-suite (пересмотр по замерам)

<!-- GREP_SUMMARY: check-suite, check, check-diff, SoT-манифест, xdist, fingerprint-кэш, static_audit, gate-executor, нейминг, baseline, preflight -->
# STRUCTURE: ┌решения пользователя┐ → ◇ baseline-замеры → ◇ дизайн (манифест → executor → xdist → fingerprint → diff → gate-портал) → ◇ волны 0-4 (шаги+тесты) → ⊕ AC/файлы/риски/выигрыш → ⎋ глоссарий
<!-- ai-instructions:0.6.3 -->

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Волна 120 — устранить потерю времени на верификацию через: (1) ускорение самих проверок (xdist для static_audit/contract/predeploy — корень 80% времени), (2) единый SoT-манифест набора проверок core/check-suite.yaml (фикс дрейфа 3 hardcoded-списков), (3) глобальный fingerprint-кэш (повторный прогон на неизменённом дереве = replay отчёта <10s), (4) узкий check-diff (pre-commit/ruff/pytest по diff-файлам), (5) нейминг check-*/gate. Количество прогоняемых тестов НЕ снижается — ускоряется их прохождение.
DESCRIPTION:           Решения пользователя 2026-08-02 (суперпозиция по замерам): **B** — SoT-манифест + xdist везде + глобальный fingerprint-кэш вместо per-check watch-scope кэша брифа (watch-скоупы [tests/, core/] инвалидируются любой правкой кода — обещанный брифом «фикс → 10-60s» не работал бы для доминирующего сценария); **b** — pytest-чеки последовательно друг за другом с -n auto, static-чеки параллельно в потоках (без переподписки 36+ воркеров на 12 ядер); **a** — check-diff = узкий diff-таргет (pre-commit --files + ruff по diff + pytest изменённых тест-файлов), без кэша; **gate + CI** — xdist добавляется и в канонический gate (порядок шагов, fail-fast и семантика неизменны — ускорение, не пропуск). Отличие от брифа: per-check watch-scope кэш (Wave 2 брифа) ЗАМЕНЁН на fingerprint-кэш целого дерева; check-diff сужен с changed-only по манифесту до честного diff-таргета; добавлены baseline-замеры и волна валидации xdist-безопасности static_audit (3106 тестов, общее состояние .test_counter.json).
RATIONALE:             Замеры сессии 2026-08-02 (см. §1): preflight 254s+ — это serial static_audit (3106 тестов, catch-all marker), а НЕ оркестрация (gates 436 тестов с xdist = 29.4s). Документация «20-40s» была права по духу — test_runner просто не применяет xdist. Один фикс (-n auto в test_runner) даёт 5 мин → ~1 мин без потери тестов. Fingerprint-кэш целого дерева безопасен конструктивно (replay только при байт-идентичном дереве), per-check кэш — нет (внешние входы: venv, pyproject, окружение). Gate-портал на манифесте устраняет дрейф конструктивно (инвариант «два executor'а одного манифеста»).
IMPLEMENTS:            U-120-1 (время верификации: preflight 254s+ → check ~90s, gate fast ~2.5 мин), U-120-2 (дрейф покрытия: 3 hardcoded-списка → SoT-манифест + consistency-гейт), U-120-3 (нейминг check-*/gate). Связано: DevPlan 098 (test_runner), DevPlan 060 (Repair Contract), DevPlan 097 (doxygen), инвариант 11 root AGENTS.md (Manifest Generation Contract), TRAP[DECISION] 2026-07-31 (parity-гейты), TRAP[DECISION] 2026-08-01 (D5 healthcheck-канон — единый критерий в одном месте).
IMPACTS:               core/check-suite.yaml (NEW), core/internal/check_suite.py (NEW), core/internal/preflight.py (MOD — тонкий фасад), core/internal/test_runner.py (MOD — xdist), makefiles/repair.mk (check/check-diff), makefiles/ci.mk (gate через executor), core/entrypoint-manifest.yaml, core/AGENTS.md, tests/gates/AGENTS.md, tests/AGENTS.md, .kilo/rules/_project.md, root AGENTS.md (глоссарий G4), README.md (при упоминании preflight). БЕЗ ИЗМЕНЕНИЙ: .github/workflows/* (CI получает xdist через executor автоматически), core/entrypoints/pre-push-gate.sh. ПРИМЕЧАНИЕ: .kilo/agents/code.md и .kilo/rules/testing.md — framework-generated файлы (ai-instructions), НЕ модифицируются; вся проектная информация о цикле верификации — в _project.md.
REQUIRES:              Baseline-замеры §1 (выполнены 2026-08-02); решения пользователя §2 (зафиксированы); валидация xdist-безопасности static_audit в Wave 1 (общее состояние conftest); стабильный Docker (подтверждён) для ci-docker-режимов Wave 2; DevPlan после ревью (текущий документ).
$END_ARTIFACT_CONTRACT

---

## 1. Baseline-замеры (2026-08-02, сессия, до изменений)

Последовательные замеры (без CPU-контеншна), 12 ядер, xdist 3.8.0, pytest 9.1.1, дерево с незакоммиченными правками:

| Чек | Команда | Тестов | Serial | xdist | Вывод |
|-----|---------|--------|--------|-------|-------|
| **static_audit** | test_runner --marker static_audit | **3106** | **254.3s** | не применён | **БОТТЛНЕК: ~80% времени preflight** |
| gates-static | pytest tests/gates/ -m "gate and not requires_docker" | 436 | 61.3s | **29.4s** | xdist уже включён в preflight |
| contract | test_runner --marker contract | 279 | 13.2s | не применён | быстрый, но тоже serial |
| predeploy | pytest -m "predeploy and not requires_docker" | 37 | 3.0s | не применён | — |
| gates-docker | pytest -m "gate and requires_docker" | **0** | 0.8s rc=5 | — | чек ПУСТ (0 тестов) |
| pre-commit --all-files | 25 хуков | — | 9.9s | — | не главный |
| fix-gate | make fix-gate | — | 2.8s | — | фаза 1 |
| check-dead-code | git-log скан | — | 11.8s | — | — |
| doxygen-check | doxygen Doxyfile | — | 12.9s | — | — |
| check-manifests | G1-G6 byte-сверка | — | 2.4s | — | ДЫРА: вне preflight/gate |
| ruff check . | — | — | 0.1s | — | ДЫРА: вне preflight/gate |
| validate | validate.sh | — | 0.1s | — | — |

**Факты:**
1. `preflight` wall ≈ max(static_audit 254s, остальные ~30s) ≈ **4-5 мин** — совпадает с болью пользователя. Корень — serial static_audit в test_runner (catch-all marker: 3106 из 3198 тестов).
2. Документация tests/gates/AGENTS.md «фаза 3 ~20-40s» неверна не «по духу»: xdist доведён только до gates-static; test_runner (contract/static_audit/predeploy) — без xdist.
3. gates-docker пуст (rc=5, 0 тестов) — включение в диагностику требует `allow_no_tests` (exit 5 → PASS).
4. check-manifests уже частично живёт в pre-commit (conditional hooks `files:`), но в preflight/gate его нет — дыра AC-2 формулируется точнее.
5. Противоречие брифа: таблица §2.3 «gate fast 5-15s из кэша» vs AC-4 «gate без кэша». В настоящем DevPlan: gate БЕЗ кэша всегда (AC-4), таблица исправлена.
6. CI (platform-test.yml) вызывает только `make gate MODE=fast/ci-docker SKIP_PRECOMMIT=1` — прямого pytest в workflows нет: xdist в executor автоматически ускоряет CI (ubuntu 4 vCPU → static_audit ~70-90s).

---

## 2. Решения пользователя (2026-08-02, суперпозиция по замерам)

| Вопрос | Решение | Следствие |
|--------|---------|-----------|
| Архитектура ускорения | **B**: манифест + xdist + fingerprint | per-check watch-scope кэш брифа ОТКЛОНЁН (не работает для фикс-цикла по коду); вместо него — fingerprint целого дерева |
| Параллелизм pytest-чеков | **b**: pytest последовательно + static в потоках | детерминированные тайминги, без переподписки (12 ядер, 1 pytest с -n auto за раз) |
| check-diff | **a**: узкий diff-таргет | pre-commit --files + ruff по diff + pytest изменённых тест-файлов; без кэша |
| xdist в каноническом gate | **Да, и в CI** | порядок шагов/семантика неизменны; CI ускоряется через executor без правки workflows |

---

## 3. Целевой дизайн

### 3.1 SoT-манифест `core/check-suite.yaml` (схема v1)

```yaml
version: 1
checks:
  - id: pre-commit
    cmd: make pre-commit-run
    tier: fix                      # автофикс-фаза: последовательно, до проверок
    timeout: 120
    gate_modes: [fast, full]       # шаг 1 обоих режимов gate
  - id: validate
    cmd: bash core/entrypoints/validate.sh
    tier: static
    timeout: 60
    gate_modes: [fast, full]
  - id: check-dead-code
    cmd: make check-dead-code
    tier: static
    timeout: 60
    gate_modes: [fast, full]
  - id: check-exception-patterns
    cmd: make check-exception-patterns
    tier: static
    timeout: 30
    gate_modes: [fast]             # в full его НЕТ (паритет с текущим ci.mk)
  - id: lint
    cmd: bash core/entrypoints/validate.sh --lint
    tier: static
    timeout: 120
    gate_modes: [full]             # только full
  - id: doxygen-check
    cmd: make doxygen-check
    tier: static
    timeout: 30
    gate_modes: [fast, full]
  - id: check-file-lines
    cmd: make check-file-lines
    tier: static
    timeout: 60
    gate_modes: [full]
    non_blocking: true             # || true, как сейчас в ci.mk full
  - id: check-manifests            # ← ДЫРА закрыта (диагностика + pre-commit hook; в gate не добавляется — паритет)
    cmd: make check-manifests
    tier: static
    timeout: 60
  - id: ruff-check                 # ← ДЫРА закрыта (диагностика; в gate — через pre-commit, паритет)
    cmd: .venv/bin/ruff check .
    tier: static
    timeout: 60
  - id: gates
    tier: pytest
    timeout: 180
    gate_modes: [fast, full]
    xdist: true
    cmds:                          # per-mode: выражения РАЗНЫЕ (fast vs full)
      fast: pytest tests/gates/ -m "gate and not requires_docker"
      full: pytest tests/gates/ -m "gate and not skip_enforcement"
  - id: gates-docker
    tier: pytest
    timeout: 180
    gate_modes: [fast, full]
    allow_no_tests: true           # сегодня 0 тестов (rc=5 → PASS); оживает при добавлении docker-гейтов
    docker: true
    cmds:
      fast: pytest tests/gates/ -m "gate and requires_docker"
      full: pytest tests/gates/ -m "gate and requires_docker"
  - id: contract
    cmd: make test MARKER=contract    # = test_runner --marker contract (xdist в test_runner, Wave 1)
    tier: pytest
    timeout: 180
    gate_modes: [fast, full]         # fast (ci.mk:162) и full (ci.mk:193) — одна команда
    xdist: true
    junit: tests/report-contract.xml
  - id: static_audit
    tier: pytest
    timeout: 300
    gate_modes: [fast, full]
    xdist: true                      # ← главный выигрыш: 254s → ~40-60s (12 ядер)
    junit: tests/report-static.xml
    cmds:                            # fast (ci.mk:164) и full (ci.mk:195) — РАЗНЫЕ команды
      fast: python3 -m core.internal.test_runner --marker static_audit --junit-output tests/report-static.xml
      full: make test MARKER=static_audit
  - id: predeploy
    tier: pytest
    timeout: 180
    gate_modes: [fast, full]
    xdist: true
    junit: tests/report-predeploy.xml
    project_filter: true             # PROJECT=<name> → -k
    cmds:                            # fast (ci.mk:166) — прямой pytest; full (ci.mk:197) — test_runner
      fast: pytest tests/ -m "predeploy and not requires_docker" --tb=short -rs --junitxml=tests/report-predeploy.xml
      full: make test MARKER=predeploy
  - id: predeploy-docker
    cmd: pytest tests/ -m "predeploy and requires_docker" --tb=short -rs --junitxml=tests/report-predeploy.xml
    tier: pytest
    timeout: 600
    gate_modes: [ci-docker]
    xdist: false                     # docker-зависимые — последовательно
    allow_no_tests: true
    junit: tests/report-predeploy.xml
  - id: smoke
    cmd: make test MARKER=smoke      # full (ci.mk:199) и ci-docker (ci.mk:222) — одна команда
    tier: pytest
    timeout: 600
    gate_modes: [full, ci-docker]
    xdist: true
    junit: tests/report-smoke.xml
  - id: component
    cmd: make test MARKER=component  # full (ci.mk:201) и ci-docker (ci.mk:224) — одна команда
    tier: pytest
    timeout: 600
    gate_modes: [full, ci-docker]
    xdist: true
    junit: tests/report-component.xml
```

**Поля:** `id` (kebab-case), `cmd` (один вариант) ИЛИ `cmds` (per gate-mode), `tier` (fix|static|pytest), `timeout`, `gate_modes` (⊆ {fast, full, ci-docker}; отсутствует = только диагностика), `diagnostic` (bool, default: true для tier fix/static/pytest — lint/check-file-lines/smoke/component/predeploy-docker: false), `xdist` (bool, default: true для tier pytest), `allow_no_tests` (exit 5 → PASS), `non_blocking` (провал не роняет gate), `junit` (путь отчёта для merge), `project_filter`, `docker`, опционально `repair` (DevPlan 060).

**Порядок записей = канонический порядок шагов gate** (супермножество fast ∪ full ∪ ci-docker). Executor фильтрует по `gate_modes`:
- fast: pre-commit → validate → check-dead-code → check-exception-patterns → doxygen-check → gates → gates-docker → contract → static_audit → predeploy — **совпадает с текущим ci.mk fast (паритет)**.
- full: pre-commit → validate → check-dead-code → lint → doxygen-check → check-file-lines → gates → contract → static_audit → predeploy → smoke → component — **совпадает с текущим ci.mk full**.

### 3.2 Два executor'а из одного манифеста

| Executor | Таргет | Поведение |
|----------|--------|-----------|
| Диагностический | `make check` (+ `make check-diff`) | fix-фаза (tier=fix, CHECK_FIX=0 отключает) → fingerprint (кэш) → pytest-чеки ПОСЛЕДОВАТЕЛЬНО с -n auto, static-чеки параллельно в потоках поверх → единый отчёт |
| Канонический | `make gate` MODE=fast\|full\|ci-docker | шаги из манифеста по `gate_modes`, fail-fast (fast) / accumulate + junit-merge (full, ci-docker), без кэша |

Инвариант «preflight НЕ заменяет gate» переформулируется системно: *два executor'а одного манифеста; диагностический — параллельный акселератор, канонический — арбитр; дрейф невозможен конструктивно*. Инвариант усиливается, не ослабляется (как в брифе).

### 3.3 xdist в test_runner (корень ускорения)

`core/internal/test_runner.py`: в pytest-инвокацию (строка ~352) добавляется `-n auto` перед `-m` (если xdist доступен — `_has_xdist` уже есть в preflight.py, переносится в shared). Флаг `TEST_NO_XDIST=1` для отключения (слабые машины, диагностика гонок). Потребители: `make test`, `make test-summary`, `make gate` (contract/static_audit/predeploy/smoke/component). Меняется ТОЛЬКО способ исполнения, не набор тестов.

**Валидация xdist-безопасности static_audit (3106 тестов, Wave 1):**
- Общее состояние: `tests/.test_counter.json` (anti-loop session hooks в tests/conftest.py) — при xdist session-хуки выполняются в каждом worker'е → конкурентные чтения/записи JSON. Митигация: файловая блокировка (fcntl/flock) в conftest или per-worker ключ.
- Отчёты junit (`tests/report-*.xml`) пишутся единственным раннером — безопасно.
- Проверка: полный прогон `pytest tests/ -m <static_audit expr> -n auto` → 3106 тестов зелёные, 0 гонок; при гонках — точечные фиксы conftest, `TEST_NO_XDIST` как fallback для конкретного чека.

### 3.4 Fingerprint-кэш (замена per-check watch-scope кэша брифа)

- Файл: `$(git rev-parse --git-dir)/check-cache.json` (не коммитится).
- **Fingerprint = sha256(манифест + .pre-commit-config.yaml + pyproject.toml + содержимое ВСЕХ файлов дерева)**. Реализация: `git ls-files -c -o --exclude-standard -z | xargs -0 sha256sum` (один subprocess, включая untracked; исключения: .git/, .venv/, __pycache__/, .pytest_cache/, tests/report*.xml, .test_counter.json) + sha256-конкатенация в Python. Оценка ~1-2s на ~3000 файлов.
- **Правила:**
  1. Кэш применяется ТОЛЬКО к диагностическому executor'у (`make check`). `make gate`, CI, pre-push — без кэша (канонический прогон всегда).
  2. Replay только при совпадении fingerprint И последний прогон был зелёным. Упавший прогон никогда не реплеится (перезапускается полностью).
  3. Fingerprint вычисляется ПОСЛЕ fix-фазы (fix-gate/pre-commit мутируют файлы — иначе автоправка ломала бы replay).
  4. `CHECK_CACHE=0` — полный прогон без чтения/записи кэша.
  5. Инвалидация автоматическая: любая правка любого файла дерева, манифеста или bump `version:` → miss.
  6. Ограничение (документируется): изменения вне дерева (pip install, системные пакеты) fingerprint не видит → при сомнениях CHECK_CACHE=0; в каноническом прогоне кэш не участвует в принципе.
- **Почему безопасно:** replay возможен только при байт-идентичном дереве — результат предыдущего зелёного прогона с этого же дерева гарантированно валиден. Ложный зелёный невозможен конструктивно (в отличие от per-check watch-скоупов, где внешние входы чеков не покрывались).

Ожидаемый цикл кодера (baseline → after):

| Шаг | Сейчас | После 120 |
|-----|--------|-----------|
| 1-й `check` | ~5 мин | ~90s (xdist static_audit) |
| повторный `check` без изменений | ~5 мин | <10s (fingerprint replay; с fix-фазой ~25s) |
| фикс → 2-й `check` (изменён код) | ~5 мин | ~90s (честный полный прогон) |
| финальный `make gate MODE=fast` | ~5-6 мин | ~2-2.5 мин (xdist в шагах gate) |
| Баг-цикл (2 итерации) | 4×5 мин ≈ 20 мин | 90s + 2×90s + 150s ≈ 7 мин |

### 3.5 check-diff (узкий diff-таргет, без кэша)

`make check-diff` — diff-файлы: `git diff --name-only HEAD` + `git ls-files -o --exclude-standard` (untracked). Запускает ТОЛЬКО:
1. `pre-commit run --files <изменённые>` (вместо --all-files: 9.9s → ~2s),
2. `ruff check <изменённые .py>` (вместо .: 0.1s → ~0.05s),
3. `pytest <изменённые test-файлы>` (только paths matching `tests/**/test_*.py`).

Нет изменений → exit 0 «nothing to diff». Семантика: `check` = «проверь проект (всё)», `check-diff` = «проверь то, что в git diff»; полнота гарантируется каноническим `make gate MODE=fast` перед push (pre-push hook — существует). Per-task узкие прогоны по-прежнему через `make test-summary TEST_FILE=...`.

### 3.6 Gate-портал (ci.mk → executor)

`makefiles/ci.mk` target `gate`: `$(PYTHON) -m core.internal.check_suite run --gate-mode $(MODE) [--project $(PROJECT)] [--skip-precommit]`. Executor воспроизводит текущую семантику ci.mk:
- Порядок шагов из манифеста (3.1) — паритет с текущим ci.mk (проверяется golden-тестом); команды `make test MARKER=X` проходят через test_runner (junit + компактный отчёт + xdist) — ровно как сейчас, только с xdist;
- `SKIP_PRECOMMIT=1` → пропуск pre-commit шага;
- fail-fast для fast (первый провал → exit 1), accumulate для full/ci-docker (все шаги, `GATE_FAILED`, junit-merge через существующий `tests/merge_junit.py`, exit 1 при любом провале);
- `non_blocking` (check-file-lines) и `allow_no_tests` (gates-docker, predeploy-docker) не роняют gate;
- env `PYTEST_NO_ESCALATION=1` на pytest-шагах;
- удаление старых `tests/report*.xml` перед прогоном (как сейчас).

### 3.7 Consistency-гейт (анти-дрейф, по образцу parity-гейтов 116 T9)

`tests/gates/test_gate_check_suite_consistency.py`:
1. **0 hardcoded проверок вне манифеста**: AST/текстовый парсинг makefiles/ci.mk + makefiles/repair.mk — pytest-маркерные выражения и список чеков НЕ захардкожены (только вызовы `check_suite run`); hardcoded-команды = RED.
2. **Каждый check манифеста валиден**: id kebab-case, tier ∈ {fix, static, pytest}, timeout > 0, gate_modes ⊆ {fast, full, ci-docker}, cmd ИЛИ cmds (для gate_modes-режимов) присутствует, junit-пути уникальны.
3. **Покрытие дыр** (регресс AC-2): ids check-manifests, ruff-check, gates-docker присутствуют в манифесте.
4. **Паритет gate-шагов**: `check_suite list --gate-mode fast|full` == golden-списки (захардкоженные в тесте, снятые с текущего ci.mk ДО порта — Wave 1 фиксирует golden, Wave 2 сверяет).
5. **Регистрация**: id чеков, являющихся make-таргетами (pre-commit, validate, check-dead-code, …), присутствуют в allowed_verbs entrypoint-manifest.yaml; `check`/`check-diff` зарегистрированы, `preflight` — deprecated.

---

## 4. Волны внедрения

### Wave 0 — Baseline (ВЫПОЛНЕНО, §1)
Замеры сессии 2026-08-02 зафиксированы в §1. Дальнейшие волны сравниваются с этой таблицей.

### Wave 1 — Манифест + диагностический executor + xdist (ядро ускорения)
**Шаги:**
1. `core/check-suite.yaml` (чек-лист §3.1).
2. `core/internal/check_suite.py` (NEW): загрузка/валидация манифеста, `run --mode diagnostic [--no-fix] [--json] [--workers N] [--no-cache]`, `list`, `fingerprint` (заглушка кэша — запись без replay, Wave 3), отчёт (переиспользовать формат preflight).
3. `core/internal/preflight.py` → тонкий фасад на check_suite (прецедент compose-safe-up): CLI-флаги старые, вызов новый. `test_preflight.py` (212 LOC, тестирует bootstrap-preflight — НЕ трогает core/internal/preflight.py) не затрагивается.
4. `core/internal/test_runner.py`: xdist (`-n auto` при наличии) + `TEST_NO_XDIST=1`; перенос `_has_xdist` в shared (или локальный дубль).
5. `makefiles/repair.mk`: `check` (экс-preflight) + deprecated-алиас `preflight`; `check-diff` — заглушка до Wave 4 (или сразу, малый объём).
6. `core/entrypoint-manifest.yaml`: allowed_verbs += check, check-diff; preflight → deprecated. Глоссарий root AGENTS.md (G4) — регенерация.
7. `tests/gates/test_gate_check_suite_consistency.py` (пункты 1-3, 5; golden-списки фиксируются СЕЙЧАС с текущего ci.mk).
8. Валидация xdist-безопасности static_audit: полный прогон `pytest tests/ -m <expr> -n auto` → 3106 зелёных; фиксы гонок в tests/conftest.py (lock/per-worker ключ для .test_counter.json).

**Тесты волны:**
- `tests/unit/test_check_suite.py` (NEW): валидация манифеста (невалидный tier/timeout/cmds-покрытие → ошибка), список чеков diagnostic (smoke/component/lint НЕ входят; check-manifests/ruff/gates-docker входят), отчёт, фасад preflight.py (старые флаги работают).
- `tests/unit/test_test_runner.py` (MOD): ожидаемые pytest-args содержат `-n auto` (и не содержат при TEST_NO_XDIST=1).
- `tests/gates/test_gate_check_suite_consistency.py` (NEW): пункты выше.
- Интеграция: `make check` зелёный, wall < 120s (замер в отчёте), `make gate MODE=fast` зелёный (уже ускорен xdist через test_runner).

**Выходные метрики:** `make check` (без кэша) < 120s на 12 ядрах; static_audit xdist ~40-60s.

### Wave 2 — Gate-портал (ci.mk → манифест) + xdist в каноническом прогоне
**Шаги:**
1. `check_suite.py run --gate-mode fast|full|ci-docker`: fail-fast/accumulate, SKIP_PRECOMMIT, PROJECT, junit-merge, non_blocking, allow_no_tests, PYTEST_NO_ESCALATION, очистка report*.xml.
2. `makefiles/ci.mk` gate → executor (семантика и MODE неизменны; MODE=ci-docker тоже через executor).
3. Golden-тест паритета (consistency-гейт п.4): списки шагов fast/full == снятые в Wave 1.
4. CI-валидация: workflows БЕЗ изменений; запуск `make gate MODE=fast SKIP_PRECOMMIT=1` локально — зелёный; `MODE=full` и `MODE=ci-docker` на dev-машине (Docker стабилен) — зелёный.

**Тесты волны:**
- `tests/unit/test_check_suite.py` (MOD): gate-режимы — состав шагов по gate_modes, non_blocking не роняет, allow_no_tests rc=5 → PASS, junit-merge вызывается для full/ci-docker, PROJECT → `-k`.
- Интеграция: `make gate MODE=fast` == зелёный до/после порта; `make gate MODE=ci-docker` на dev-машине.

**Выходные метрики:** gate fast < 3 мин локально (было ~5-6).

### Wave 3 — Fingerprint-кэш (replay)
**Шаги:**
1. Реализация fingerprint (§3.4): вычисление, cache JSON в git-dir, replay зелёного прогона, CHECK_CACHE=0, инвалидация по манифесту/bump version.
2. Кэш применяется ТОЛЬКО к `make check`; gate/CI/pre-push не читают кэш (защита в executor: `--no-cache` при --gate-mode).

**Тесты волны:**
- `tests/unit/test_check_suite.py` (MOD): fingerprint стабилен на неизменённом дереве (tmp_path-repo-фикстура), меняется при правке файла/манифеста; replay зелёного прогона; упавший прогон НЕ реплеится; CHECK_CACHE=0 не читает/не пишет; excluded-пути (report*.xml, .test_counter.json) не влияют на fingerprint.
- Метрика AC-3: `make check CHECK_FIX=0` на чистом дереве < 10s; с fix-фазой < 25s.

### Wave 4 — check-diff + инструкции + нейминг-финализация
**Шаги:**
1. `check-diff` (§3.5): diff-файлы (tracked+untracked), pre-commit --files, ruff по diff, pytest по изменённым test-файлам.
2. Инструкции: `.kilo/rules/_project.md` (раздел «Верификация реализации»: per-task `make test-summary TEST_FILE=...` / `make check-diff` → фикс-цикл `make check` → финал `make gate MODE=fast`; п.4 ручной check-manifests/ruff УДАЛЯЕТСЯ — дыры закрыты манифестом). `.kilo/agents/code.md` и `.kilo/rules/testing.md` — framework-generated (ai-instructions), не модифицируются. Также: tests/gates/AGENTS.md (раздел Preflight → Check + новые тайминги), tests/AGENTS.md, core/AGENTS.md (canon-таблица — регенерация G-механизмом), README.md (при упоминании preflight).
3. Нейминг-миграция: phantom-refs гейт (0 упоминаний `make preflight` в .kilo/* и AGENTS.md — по образцу test_gate_phantom_refs.py); preflight остаётся deprecated-алиасом (compose-safe-up прецедент); `make gate` НЕ входит в запрет.

**Тесты волны:**
- `tests/unit/test_check_suite.py` (MOD): diff-скоуп — изменённый .py → ruff+pre-commit; изменённый test-файл → pytest; только README → pre-commit; пустой diff → exit 0.
- `tests/gates/test_gate_phantom_refs.py` (MOD): preflight в запрет.

---

## 5. Приёмка (AC)

- **AC-1**: ci.mk gate и repair.mk check/check-diff НЕ содержат hardcoded списков/маркерных выражений проверок — оба вызывают `check_suite`; гейт test_gate_check_suite_consistency блокирует хардкод (RED).
- **AC-2**: дыры закрыты — check-manifests, ruff check . и gates-docker (allow_no_tests) входят в диагностический `make check`.
- **AC-3**: повторный `make check` на чистом дереве (CHECK_FIX=0) < 10s (fingerprint replay); полный `make check` < 120s на 12 ядрах (baseline: static_audit 254s); упавший чек/прогон никогда не реплеится как зелёный.
- **AC-4**: `make gate` (MODE=fast|full|ci-docker), CI и pre-push — БЕЗ кэша; семантика gate неизменна (порядок шагов из манифеста == прежний порядок ci.mk, fail-fast fast / accumulate full+ci-docker, junit-merge, PROJECT, SKIP_PRECOMMIT); изменяется только способ исполнения pytest-шагов (xdist). Время gate fast < 3 мин.
- **AC-5**: нейминг — 0 упоминаний `make preflight` в .kilo/* и AGENTS.md (гейт phantom-refs); deprecated-алиас preflight → check работает; `make gate` остаётся каноническим именем.
- **AC-6**: `make gate MODE=fast && make check-manifests && ruff check .` зелёные (аналог AC-GLOBAL-1 волны 119).
- **AC-7**: инструкции кодера (_project.md, code.md) переписаны под цикл: per-task test-summary/check-diff → фикс-цикл check → финальная верификация gate MODE=fast; ручные check-manifests/ruff из инструкции удалены.
- **AC-8**: CI-workflows и pre-push-gate.sh БЕЗ ИЗМЕНЕНИЙ (gate не переименовывается); CI-время fast-gate сокращается за счёт xdist через executor (ubuntu 4 vCPU).
- **AC-9 (новое, честность)**: количество прогоняемых тестов НЕ снижено — static_audit 3106 + gates 436 + contract 279 + predeploy 37 присутствуют в прогоне `check` и gate (проверяется отчётом/счётчиками; xdist — ускорение, не пропуск).

## 6. Файл-манифест

| Файл | Действие |
|------|----------|
| `core/check-suite.yaml` | NEW — SoT набора проверок |
| `core/internal/check_suite.py` | NEW — executor (diagnostic/diff/gate/fingerprint/list) |
| `core/internal/preflight.py` | MOD — тонкий фасад на check_suite (compose-safe-up прецедент) |
| `core/internal/test_runner.py` | MOD — xdist (-n auto) + TEST_NO_XDIST |
| `tests/conftest.py` | MOD — lock/per-worker ключ .test_counter.json (xdist-безопасность) |
| `makefiles/repair.mk` | MOD — check, check-diff; preflight deprecated-алиас |
| `makefiles/ci.mk` | MOD (Wave 2) — gate через executor |
| `core/entrypoint-manifest.yaml` | MOD — allowed_verbs: check/check-diff; preflight deprecated |
| `tests/gates/test_gate_check_suite_consistency.py` | NEW — анти-дрейф гейт |
| `tests/gates/test_gate_phantom_refs.py` | MOD — preflight в запрет |
| `tests/unit/test_check_suite.py` | NEW — unit (манифест/fingerprint/diff/gate) |
| `tests/unit/test_test_runner.py` | MOD — xdist-args |
| `core/AGENTS.md`, `tests/gates/AGENTS.md`, `tests/AGENTS.md` | MOD — документация |
| `.kilo/rules/_project.md` | MOD — новый цикл верификации (framework-файлы .kilo/agents/code.md и .kilo/rules/testing.md не модифицируются) |
| root `AGENTS.md` (глоссарий) | regenerated (G4) |
| `README.md` | MOD — при упоминании preflight |
| `.github/workflows/*` | БЕЗ ИЗМЕНЕНИЙ |
| `core/entrypoints/pre-push-gate.sh` | БЕЗ ИЗМЕНЕНИЙ (make gate MODE=fast) |

## 7. Риски и защиты

| Риск | Защита |
|------|--------|
| xdist-гонки в static_audit (3106 тестов, общее состояние .test_counter.json, порядок-зависимые тесты) | Wave 1: полный прогон с xdist; точечный фикс conftest (flock/per-worker ключ); TEST_NO_XDIST=1 как fallback; гонки фиксируются до мержа |
| Регрессия канонического gate при порте (ci.mk → executor) | Golden-списки шагов (сняты ДО порта, Wave 1); AC-6; откат = git revert makefiles/ci.mk; CI-workflows не трогаются |
| Fingerprint не видит изменения вне дерева (pip, системные пакеты) | Replay только при байт-идентичном дереве; CHECK_CACHE=0; gate/CI/pre-push без кэша в принципе |
| Переподписка CPU при xdist везде | Решение b: pytest-чеки строго последовательно (1 pytest с -n auto за раз); static-чеки в потоках; CHECK_WORKERS для слабых машин |
| Медленный чек всё ещё доминирует (static_audit ~40-60s) | Дальнейшее ускорение — точечное (профиль самых тяжёлых unit-тестов), вне scope 120; check-diff для мелких правок |
| Переименование preflight ломает ссылки | Deprecated-алиас (compose-safe-up прецедент); phantom-refs гейт; Wave 4 — последняя, на зелёной базе |
| gates-docker пуст (0 тестов) | allow_no_tests: rc=5 → PASS; чек оживает автоматически при добавлении docker-гейтов |

## 8. Итоговая оценка выигрыша

| Сценарий | Сейчас | После 120 |
|----------|--------|-----------|
| Полный `check` (экс-preflight) | ~5 мин (static_audit serial 254s) | ~90s (xdist) |
| Повторный `check` без изменений | ~5 мин | <10s (fingerprint replay) |
| `make gate MODE=fast` (локально/CI) | ~5-6 мин | ~2-2.5 мин (xdist в шагах) |
| Баг, 2 фикс-итерации | ~20 мин (4 полных прогона) | ~7 мин (1 полный + 2 полных быстрых + gate) |
| CI-цикл push→fail→fix→push | ~12 мин × 2 | ~5 мин × 2 (xdist на 4 vCPU) + реже падает (дыры закрыты) |
| Кол-во прогоняемых тестов | 3858 | 3858 (0 снижение) |

$END_DEVPLAN
