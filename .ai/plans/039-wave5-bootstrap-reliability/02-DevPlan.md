# 039-DevPlan: Wave 5 — Bootstrap Reliability + Converge K8s-parity

**Program:** 027 Architecture Modernization Program (волна 5 из 5)
**Predecessors:** Wave 1 (028 ✅), Wave 2 (029 ✅), Wave 3 (033 ✅), Wave 4 (035 ✅)
**Brief reference:** `.ai/plans/027-architecture-modernization-program/01-Brief.md` §7 (Wave 5)
**Duration estimate:** ~10 недель (по брифу); реалистично с учётом готовой Python-базы Wave 4 — ~6-8 недель
**Branch:** `wave5-bootstrap-reliability` (feature-branch, explicit merge-commit per R-RISK-1/PGM-R2)
**Model:** dev-pipeline skill (Brief → Architect → Coder → QA → Fix)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть последние 2 проблемы Problem Matrix (P05 ERROR_HANDLING rollback, P13 ARCHITECTURE converge K8s-parity) и завершить Strangler-Fig программу 027. Поднять converge K8s-parity score с 4/10 (текущий — 6 R-units, но R5/R6 read-only) до 7/10 за счёт self-heal для docker-volumes/sudoers/runtime-state + перевод R5 (orphan containers) и image-age из detect-only в self-heal. Реализовать transactional atomic-success-or-rollback в `deploy_docker_group` (P05). Hardening state-machine (W5-E6) поверх готовой JSON-state из Wave 4: добавить retry-policy, формальные pre/post-conditions, диаграмму переходов.
DESCRIPTION:           6 эпиков (W5-E1..E6), каждый — независимый deliverable с unit-тестами и regression-проверкой. Базируется на Python-модулях Wave 4: `deploy/docker_orchestrator.py` (deploy_docker_group, fork-based parallelism), `converge/reconciler.py` (6 R-units, JSON report, --dry-run), `deploy/orphan_reconciler.py` (batch detect-only), `lifecycle/state_machine.py` (JSON-state, checkpoint-resume). Принцип Strangler-Fig сохраняется: shell-фасады остаются тонкими, вся новая логика — в Python-модулях с typed contracts.
RATIONALE:             Бриф 027 §7 Wave 5 + §9 Risk Register (R-RISK-6, R-RISK-7) + §13 Pre-commitment (Outcome A). Текущее состояние кодовой базы (verified 2026-07-22): (а) `deploy_docker_group` не имеет rollback — failures логируются, group продолжается (строки docker_orchestrator.py:745-836); (б) `reconciler.py` имеет 6 R-units, но R5 (hosts drift) — read-only detect, R6 (vhosts) — verify-only, нет docker-volumes/sudoers/runtime-state/self-heal; (в) `orphan_reconciler.py` — только `_batch_orphan_reconciliation` detect, нет `docker rm`/`docker rmi`; (г) `state_machine.py` (1599 LOC) уже имеет JSON-state, checkpoint-resume, StepState — W5-E6 сокращён до hardening (retry-policy + formal conditions + диаграмма). HARD STOP на 7/10 K8s-parity — continuous-watch явный non-goal (R-RISK-7).
ACCEPTANCE_CRITERIA:
  - **AC-1 (W5-E1 transactional rollback):** `deploy_docker_group` при failure ≥1 контейнера в группе выполняет atomic rollback: `docker compose down` на всех siblings в группе + rollback-state-record в audit.log. Параллельность внутри группы сохранена. Unit-тест симулирует failure 1 контейнера → asserts rollback всех в группе + audit-trail запись. TDD: тест ПЕРЕД реализацией.
  - **AC-2 (W5-E2 docker-volumes drift):** Новый R7-unit `reconcile_volumes` в reconciler.py. Detect: для каждого docker-модуля из node.yaml проверяет существование named volumes из compose config. ⚠️ ОТКРЫТЫЙ ВОПРОС (см. §3 W5-E2): self-heal (docker volume create) vs detect-only. Решение оператора требуется до реализации. Unit-тест покрывает оба сценария через monkeypatch. Named-volume metadata only — bind-mounts исключены (O7 invariant сохраняется для содержимого).
  - **AC-3 (W5-E3 sudoers drift):** Новый R8-unit `reconcile_sudoers` в reconciler.py. Detect + self-heal: сравнивает sudoers.d файлы с desired state (из template-engine.sh рендера), пересоздаёт при drift через atomic write (tmp+rename, visudo -c validation). Unit-тест: monkeypatch subprocess, симуляция drift → assert recreated. Интеграция с W4-E1 sudoers_generator.py (переиспользование render-логики).
  - **AC-4 (W5-E4 runtime-state reconciliation):** Новый R9-unit `reconcile_runtime_state` в reconciler.py. Для каждого docker-модуля из node.yaml: проверяет `docker inspect` → State.Status. Если expected=running но actual∈{exited, restarting, dead, unhealthy} → self-heal через `docker compose up -d <service>` (не `restart` — preserves container). Unit-тест: monkeypatch docker inspect/compose, assert self-heal invoked.
  - **AC-5 (W5-E5 R5/R6 self-heal):** `orphan_reconciler.py` расширен: detect-only → detect+self-heal. Orphan containers (R5-orphan): `docker rm -f <container>` после detect. Aged images (R6-image-age): `docker image prune --filter "until=<retention>"` — retention из node.yaml (default 30d). Feature-flag `--self-heal` (default: false для backward compat, true в converge context). Unit-тест: monkeypatch subprocess, assert `docker rm`/`docker image prune` invoked в self-heal режиме, НЕ invoked в detect-only режиме.
  - **AC-6 (W5-E6 state-machine hardening):** `lifecycle/state_machine.py` расширен: (а) retry-policy с exponential backoff для transient failures (network, docker daemon) — max 3 retries, backoff 2s/4s/8s; (б) формальные pre/post-conditions для каждого transition (asserted в коде, logged при violation); (в) state-machine диаграмма (Mermaid) добавлена в `core/internal/bootstrap/AGENTS.md` §lifecycle. Существующие unit-тесты test_state_machine.py остаются green. Новые тесты покрывают retry-policy + precondition violation.
  - **AC-7 (regression):** `make gate MODE=fast` green. Все существующие unit-тесты (test_reconciler.py, test_docker_orchestrator.py, test_orphan_reconciler.py, test_state_machine.py) остаются green. Новые тесты для W5-E1..E6 добавлены в tests/unit/. K8s-parity score измерен и зафиксирован в VerificationReport: 4/10 → 7/10.
  - **AC-8 (production-release):** Staging-тест на tronyx-vps: `make converge NODE=tronyx-vps` (R7/R8/R9 units) + `make bootstrap-node NODE=tronyx-vps` (transactional rollback path не триггерится на success, но code path exercised). Audit-trail фиксирует новые reconcile-actions. Branch `wave5-bootstrap-reliability` → explicit merge-commit → main.
IMPLEMENTS:            Brief 027 §7 (Wave 5 эпики W5-E1..E6), §9 Risk Register (R-RISK-6 transactional cold-start, R-RISK-7 K8s-parity overkill HARD STOP), §10 KPI (converge K8s-parity 4/10 → 7/10), §13 Pre-commitment (Outcome A). AGENTS.md invariant 8 (AI-First Architecture — модульные границы). Принцип 6 (Small Simple Blocks — расширение существующих Python-модулей, не дублирование). Принцип 9 (Read before Act — Wave 4 код прочитан и verified).
IMPACTS:               **Modified Python:** `core/internal/bootstrap/deploy/docker_orchestrator.py` (W5-E1 transactional rollback в deploy_docker_group), `core/internal/bootstrap/converge/reconciler.py` (W5-E2/E3/E4 — новые R7/R8/R9 units), `core/internal/bootstrap/deploy/orphan_reconciler.py` (W5-E5 self-heal), `core/internal/bootstrap/lifecycle/state_machine.py` (W5-E6 retry-policy + pre/post-conditions). **Modified docs:** `core/internal/bootstrap/AGENTS.md` (W5-E6 Mermaid диаграмма + R7/R8/R9 описание). **New tests:** `tests/unit/test_docker_orchestrator_rollback.py`, `tests/unit/test_reconciler_r7_volumes.py`, `tests/unit/test_reconciler_r8_sudoers.py`, `tests/unit/test_reconciler_r9_runtime.py`, `tests/unit/test_orphan_reconciler_selfheal.py`, расширение `tests/unit/test_state_machine.py` (retry-policy). **No Makefile changes** (S7 constraint — 0 новых таргетов, converge/bootstrap-node уже зарегистрированы). **No shell facade changes** (reconciler.py расширяется, converge.sh остаётся thin facade).
REQUIRES:              Чистый working tree (или координация с параллельным агентом — см. §Dependencies). Перед стартом: `make gate MODE=fast` green как baseline. Staging-нода tronyx-vps доступна для AC-8. Операторское решение по W5-E2 (volumes self-heal vs detect-only) — см. §3. Audit-trail infrastructure (W2-E3) работает для rollback-state-record. dev-pipeline skill для делегирования Coder/QA.
$END_ARTIFACT_CONTRACT

---

## Verification Report Feedback (03-VerificationReport.md, 2026-07-22)

**Verdict:** DEGRADED (WARNING), health score 63/100.
**Reason:** Test baseline degradation (26/109 failures, 76.1% pass). No CRITICAL drifts. All invariants HELD.

### VR Findings Incorporated into this DevPlan

| VR-ID | Severity | Finding | Action in DevPlan |
|-------|----------|---------|-------------------|
| **D-CODE-1** | 🟡 MEDIUM | `UPDATE_STEP_COUNT=6` vs `UPDATE_STEPS` list (7 elements) — структурный drift в `state_machine.py:56` vs `:83-91` | Добавлен в §1.2 gap table + W5-E6 prereq + §5 Risk Register (W5-R9) |
| **Failure baseline** | WARNING | 26/109 unit tests fail (76.1% pass), 16/32 в `test_reconciler.py` (50%) | Добавлен pre-remediation gate (§0) + W5-R10 в §5 Risk Register |
| **@complexity gaps** | WARNING | `reconciler.py` 8.7% coverage (2/23 functions) vs 89-100% в остальных модулях | Добавлен в §2 AC-2/AC-3/AC-4 — добавить `## @complexity` на новые R7/R8/R9 функции |
| **IMP:9 gaps** | INFO | `_cleanup_legacy_container` не логирует IMP:9 в success-path → 2 Anti-Illusion failures | Добавлен в §9 pre-remediation P4 |
| **O7 AT_RISK** | INFO | W5-E2 volumes self-heal граничит с O7 invariant | Уже acknowledged в §3. Без изменений. |

### Pre-Implementation Remediation Gate (§0)

Перед делегированием Coder необходимо:

| Priority | Action | Rationale | Tracked in |
|----------|--------|-----------|------------|
| **P1** | Исправить 16 failures в `test_reconciler.py` | 50% pass rate делает TDD для W5-E2/E3/E4 бессмысленным | §9 Pre-remediation |
| **P2** | Исправить D-CODE-1 (UPDATE_STEP_COUNT vs UPDATE_STEPS list) | Структурный drift. Retry-policy и pre/post-conditions (W5-E6) зависят от структуры шагов | §1.2 gap + W5-R9 |
| **P3** | Ответить на открытый вопрос W5-E2 (volumes self-heal vs detect-only) | Блокирует выбор реализации. Default: Вариант B (conservative) | §3 |
| **P4** | Добавить IMP:9 логи в `_cleanup_legacy_container` success-path | 2 теста падают с Anti-Illusion. Быстрый фикс (< 5 строк) | §9 Pre-remediation |

**Gate condition:** P1 и P2 выполнены до старта W5-E2 (reconciler.py changes). P4 — опционально, но рекомендуется.

---

## 1. Текущее состояние кодовой базы (Read before Act — Principle 9)

Verified 2026-07-22 против branch `wave2-dangerous` (head `048b436`):

### 1.1. Что уже реализовано (Wave 1-4)

| Компонент | Файл | LOC | Статус | Релевантность Wave 5 |
|-----------|------|-----|--------|----------------------|
| `deploy_docker_group` | `deploy/docker_orchestrator.py:745-836` | 92 | fork-based parallelism, slot-limiting, parallel healthcheck. **Нет rollback.** | W5-E1 — добавить atomic rollback |
| `reconciler.py` R1-R6 | `converge/reconciler.py` | 1367 | R1 perms, R2 audit_log, R3 projects, R4 networks, R5 hosts (read-only), R6 vhosts (verify-only) | W5-E2/E3/E4 — добавить R7/R8/R9 |
| `orphan_reconciler.py` | `deploy/orphan_reconciler.py` | 465 | `_batch_orphan_reconciliation` — detect-only (docker ps -a, no rm) | W5-E5 — добавить self-heal |
| `state_machine.py` | `lifecycle/state_machine.py` | 1599 | JSON-state, checkpoint-resume, StepState (status/started_at/hash/reason), atomic save (tmp+rename), 17 init steps (INIT_STEP_COUNT=17 ✅), 7 update steps в `UPDATE_STEPS` list but `UPDATE_STEP_COUNT=6` ⚠️ D-CODE-1 (см. §1.3) | W5-E6 — hardening (retry-policy, formal conditions, диаграмма). **Prereq:** исправить D-CODE-1 |
| `converge.sh` facade | `converge.sh` | 137 | Thin facade: arg-parse → flock → reconciler.py → --reconcile → exit {0,1,2} | Без изменений (R7/R8/R9 внутри reconciler.py) |
| Unit-тесты | `tests/unit/test_*.py` | 12 files | test_reconciler.py (R1-R6), test_docker_orchestrator.py, test_orphan_reconciler.py, test_state_machine.py | Расширение + 5 новых файлов |

### 1.2. Gap analysis vs Brief 027 §7

| Brief epic | Текущее состояние | Gap для Wave 5 |
|-----------|-------------------|----------------|
| W5-E1 transactional deploy_docker_group | Failures логируются, group продолжается (deploy_docker_group:802-833) | Atomic rollback: docker compose down на siblings + audit-state-record |
| W5-E2 docker-volumes drift | Не существует (R-units: R1-R6) | Новый R7-unit. ⚠️ Открытый вопрос self-heal vs detect-only |
| W5-E3 sudoers drift | Не существует. sudoers_generator.py (W4-E1) только render | Новый R8-unit: detect drift vs rendered desired state + self-heal |
| W5-E4 runtime-state reconciliation | Не существует. R4 networks только connectivity-check | Новый R9-unit: State.Status → self-heal через compose up -d |
| W5-E5 R5/R6 self-heal | orphan_reconciler.py detect-only (нет docker rm/rmi) | Расширение: --self-heal flag, docker rm -f + docker image prune |
| W5-E6 state-machine explicit | **Уже реализовано в Wave 4** (JSON-state, transitions, checkpoint-resume) | Hardening только: retry-policy + formal pre/post-conditions + Mermaid диаграмма. ⚠️ **Prereq:** D-CODE-1 (UPDATE_STEP_COUNT=6 vs 7 в UPDATE_STEPS list) — см. §1.3 |

### 1.3. D-CODE-1: UPDATE_STEP_COUNT structural drift (обнаружено VR Phase 2)

**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py`
**Строки:** 56 (`UPDATE_STEP_COUNT = 6`) vs 83-91 (`UPDATE_STEPS` list — 7 элементов: verify_core, deploy_systemd, deliver_overlays, converge_node, verify_services, etc.)
**Суть:** Константа говорит 6, список содержит 7. `deliver_overlays` (#2.5) присутствует в списке `UPDATE_STEPS` (строка 86), но не учтён в `UPDATE_STEP_COUNT`. AGENTS.md диаграмма показывает 6 шагов (без `deliver_overlays`).

**Влияние на Wave 5:**
- W5-E6 добавляет retry-policy с step-index-based логикой и формальные pre/post-conditions. Если step count неверен:
  - Pre-condition `step N-1 status ∈ {done, skipped}` может проверять не тот шаг.
  - Retry-loop может пропустить `deliver_overlays` или посчитать неверный total-steps.
  - Mermaid диаграмма (AC-6) должна отражать актуальную структуру шагов.
- **Рекомендация VR:** исправить до W5-E6. Либо `UPDATE_STEP_COUNT = 7` (если `deliver_overlays` — легитимный шаг в `--mode update`), либо удалить `deliver_overlays` из `UPDATE_STEPS` (если не используется).

**Решение:** P2 pre-remediation gate (§0). Coder W5-E6 использует скорректированную константу.

### 1.4. Инварианты и constraints (зафиксированы из AGENTS.md)

- **O7 (reconciler.py:23):** «Never modifies project data (volumes, DB, images — invariant O7)». W5-E2 требует интерпретации (см. §3).
- **S7 constraint:** 0 новых make-таргетов. converge, bootstrap-node, node-update уже зарегистрированы в entrypoint-manifest.yaml.
- **HARD STOP (R-RISK-7):** K8s-parity 7/10 — не реализуем continuous-watch (systemd-timer territory).
- **R-RISK-6:** Transactional deploy НЕ замедляет cold-start — parallel-within-group сохраняется, только atomic-rollback на failure.

---

## 2. Эпики Wave 5

### W5-E1: Transactional deploy_docker_group (atomic success-or-rollback)

**Проблема:** P05 (ERROR_HANDLING rollback), 🟠 HIGH
**Файл:** `core/internal/bootstrap/deploy/docker_orchestrator.py` (deploy_docker_group, строки 745-836)
**Скоуп:**
- При failure ≥1 контейнера в группе → atomic rollback: `docker compose down` на ВСЕХ siblings в группе (не только failed).
- rollback-state-record в audit.log (использовать audit_logging.sh wrapper из W2-E3 или прямой append в /var/log/platform/audit.log).
- Параллельность внутри группы (fork + slot-limiting) сохраняется — rollback только post-failure.
- Возврат: tuple `(deployed, failed, failed_names, rolled_back: list[str])` — расширение текущего tuple.

**TDD порядок:**
1. `tests/unit/test_docker_orchestrator_rollback.py` — тест симулирует failure 1 контейнера (monkeypatch deploy_docker_module → returns False for 1 entry), asserts: docker compose down invoked для всех entries в группе, audit-state-record содержит rollback marker.
2. Реализация rollback-логики в deploy_docker_group.
3. Regression: существующий test_docker_orchestrator.py остаётся green (success-path без rollback).

**Acceptance:** AC-1. Тест ПЕРЕД реализацией. audit-trail фиксирует rollback.

**Риски:** R-RISK-6 (cold-start замедление) — митигация: rollback только на failure, parallel сохранён.

---

### W5-E2: Converge docker-volumes drift (R7-unit)

**Проблема:** P13 (ARCHITECTURE converge K8s-parity), 🟡 MED
**Файл:** `core/internal/bootstrap/converge/reconciler.py` (новый R7-unit)
**Скоуп:**
- Для каждого docker-модуля из node.yaml: `docker compose config --format json` → извлечение named volumes (НЕ bind-mounts).
- Detect: для каждого named volume проверить `docker volume inspect <name>` — существует ли.
- ⚠️ **ОТКРЫТЫЙ ВОПРОС (требует решения оператора до реализации):**
  - **Вариант A (self-heal):** `docker volume create <name>` если не существует. Безопасно — содержимое не трогается, создаётся пустой named volume. O7 сохраняется буквально (не «modifies data», а «creates container for data»). Но semantically граничит с нарушением.
  - **Вариант B (detect-only):** Сообщать в JSON report, не создавать. Сохраняет O7 буквально. Self-heal переносится в будущий эпик.
- Интеграция: R7 добавляется в `_unit_enabled` фильтр, вызывается из main() после R6.

**Acceptance:** AC-2. Unit-тест покрывает оба варианта через monkeypatch (решение оператора фиксируется в коде через константу/flag). Все новые функции R7 получают `## @complexity` Doxygen-теги (VR S1/D-QUAL-1: довести reconciler.py с 8.7% до ≥85% coverage).

---

### W5-E3: Converge sudoers drift (R8-unit)

**Проблема:** P13, 🟡 MED
**Файл:** `core/internal/bootstrap/converge/reconciler.py` (новый R8-unit)
**Скоуп:**
- Detect: для каждого sudoers.d файла из desired state (рендер через sudoers_generator.py из W4-E1) сравнить с actual в `/etc/sudoers.d/`.
- Self-heal: при drift → пересоздать через atomic write (tmp + rename), `visudo -c` validation перед rename. Если validation fail → WARN, не трогать actual.
- Reuse: `sudoers_generator.py` render-логика (не дублировать — Principle 6 extension over duplication).

**Acceptance:** AC-3. Unit-тест: monkeypatch subprocess (visudo, stat), симуляция drift → assert recreated + visudo -c called. Все новые функции R8 получают `## @complexity` теги.

---

### W5-E4: Converge runtime-state reconciliation (R9-unit)

**Проблема:** P13, 🟡 MED
**Файл:** `core/internal/bootstrap/converge/reconciler.py` (новый R9-unit)
**Скоуп:**
- Для каждого docker-модуля из node.yaml: `docker inspect <container> --format '{{.State.Status}}'`.
- Expected: running (для модулей в node.yaml без `enabled: false`).
- Если actual ∈ {exited, restarting, dead, unhealthy} → self-heal через `docker compose up -d <service>` (НЕ `docker restart` — preserves container, re-applies compose).
- Logging: IMP:9 для self-heal action, IMP:8 для detection.

**Acceptance:** AC-4. Unit-тест: monkeypatch docker inspect/compose, assert `docker compose up -d` invoked при exited status. Все новые функции R9 получают `## @complexity` теги.

---

### W5-E5: R5/R6 self-heal (orphan containers + aged images)

**Проблема:** P13, 🟡 MED
**Файл:** `core/internal/bootstrap/deploy/orphan_reconciler.py` (расширение)
**Скоуп:**
- Feature-flag `--self-heal` (default: false — backward compat для direct calls; true когда вызывается из converge context).
- Orphan containers (R5-orphan): после detect → `docker rm -f <container>` (только контейнеры без project-label или с project-label несуществующего проекта).
- Aged images (R6-image-age): `docker image prune --filter "until=<retention>" --filter "label=com.docker.compose.project"` — retention из node.yaml `image_retention_days` (default 30d). НЕ dangling-only prune (risk: remove used images) — только labelled + aged.
- Audit: каждое rm/prune → audit-state-record.

**Acceptance:** AC-5. Unit-тест: monkeypatch subprocess, assert `docker rm -f` / `docker image prune` invoked в --self-heal режиме, НЕ invoked в default режиме.

---

### W5-E6: State-machine hardening (retry-policy + formal conditions + диаграмма)

**Проблема:** (§4.5 Option C отчёта) — частично выполнено в Wave 4
**Файл:** `core/internal/bootstrap/lifecycle/state_machine.py` (расширение) + `core/internal/bootstrap/AGENTS.md` (документация)
**Скоуп (hardening только — база уже есть):**
- **Retry-policy:** для transient failures (subprocess TimeoutExpired, FileNotFoundError docker binary, network SSH errors) — exponential backoff: 2s, 4s, 8s, max 3 retries. Non-transient (OSError, KeyError) — no retry, fail-fast.
- **Formal pre/post-conditions:** для каждого step transition (start_step/complete_step) — assertions в коде:
  - Pre: `step N-1 status ∈ {done, skipped}` (не running, не failed).
  - Post: `step N status == done` после complete_step, `state.current_step == N`.
  - Violation → IMP:10 log + raise StateTransitionError.
- **Mermaid диаграмма:** добавить в `core/internal/bootstrap/AGENTS.md` §lifecycle — визуализация 17 init + 7 update steps с transitions, retry-loops, skip-branches.

**Acceptance:** AC-6. Существующие test_state_machine.py green. Новые тесты: retry-policy (monkeypatch subprocess → TimeoutExpired ×3 → success), precondition violation (force step 5 before step 4 → StateTransitionError).

---

## 3. Открытые вопросы (требуют решения до/во время реализации)

### W5-E2: docker-volumes self-heal vs detect-only

**Контекст:** Инвариант O7 (reconciler.py:23): «Never modifies project data (volumes, DB, images)». W5-E2 требует reconcile docker-volumes drift.

**Вариант A (self-heal — `docker volume create`):**
- Плюсы: K8s-parity 7/10 достигается полностью. converge действительно self-healing.
- Минусы: semantic tension с O7. `docker volume create` не «modifies data» (создаёт контейнер для данных), но граничит.
- O7 интерпретация: «modifies» = изменяет содержимое существующего volume. Создание нового named volume — infrastructure provisioning, не data modification. Bind-mounts исключены.

**Вариант B (detect-only):**
- Плюсы: O7 сохраняется буквально. Меньше risk.
- Минусы: K8s-parity остаётся 6.5/10 (R7 detect-only не считается full self-heal). converge не fully self-healing для volumes.

**Решение:** Требуется от оператора. В DevPlan зафиксировано как AC-2 с обоими сценариями в unit-тесте. Coder реализует выбранный вариант после ответа. Если ответ не получен к старту W5-E2 — по умолчанию Вариант B (conservative), self-heal переносится в Debt-tracker.

---

## 4. Порядок выполнения (dependency graph)

```
W5-E1 (transactional rollback)  ──► независим, НО staging-test (AC-8) требует стабильного deploy
                                    Параллелен с W5-E2/E3/E4 (разные файлы)

W5-E2 (R7 volumes)     ─┐
W5-E3 (R8 sudoers)     ─┼─► все в reconciler.py — СЕКВЕНЦИАЛЬНО (один файл, конфликт merge)
W5-E4 (R9 runtime)     ─┘     Порядок: E2 → E3 → E4 (по возрастанию risk: volumes → sudoers → runtime)

W5-E5 (orphan self-heal)      ──► независим (orphan_reconciler.py), параллелен с E2-E4

W5-E6 (state-machine hardening) ──► независим (state_machine.py), параллелен со всеми

Финал: AC-7 regression (make gate) → AC-8 staging-test → merge
```

**Рекомендация:** Делегировать через dev-pipeline 3 параллельных Coder-subagent'а:
- Track A: W5-E1 (docker_orchestrator.py)
- Track B: W5-E2 → W5-E3 → W5-E4 (reconciler.py, последовательно)
- Track C: W5-E5 (orphan_reconciler.py) + W5-E6 (state_machine.py)

---

## 5. Risk Register (Wave 5 специфичный)

| ID | Risk | L | I | Mitigation |
|----|------|---|---|------------|
| **W5-R1** | Transactional rollback (W5-E1) ломает success-path deploy | M | H | TDD: тест success-path ПЕРЕД реализацией rollback. Regression test_docker_orchestrator.py остаётся green. Staging-test AC-8. |
| **W5-R2** | R7/R8/R9 units замедляют converge (было ~seconds, стало minutes) | M | M | Каждый R-unit timeout 30s (DOCKER_TIMEOUT). Benchmark: converge time до/после в VerificationReport. |
| **W5-R3** | Sudoers self-heal (W5-E3) ломает sudo на production-ноде | L | **H** | visudo -c validation перед atomic write. Если validation fail → WARN, не трогать actual. Staging-test на tronyx-vps обязателен. Feature-flag `--dry-run` для preview. |
| **W5-R4** | Runtime-state self-heal (W5-E4) вызывает `docker compose up -d` в loop (flapping container) | M | M | Cooldown: если тот же container self-healed в последних 3 converge-runs → WARN + skip (не loop). Запись в state.json для cooldown-tracking. |
| **W5-R5** | Orphan self-heal (W5-E5) удаляет легитимный container (false-positive orphan detection) | M | H | Conservative orphan criteria: только контейнеры с project-label несуществующего проекта (verified через project-list). `docker rm -f` только в --self-heal режиме (default false). Audit-trail для каждого rm. |
| **W5-R6** | Image prune (W5-E5) удаляет used image | L | H | Filter: `--filter "label=com.docker.compose.project"` + `until=<retention>`. НЕ dangling-only prune. Dry-run mode: `docker image prune --dry-run` (если поддерживается) или --filter-only без prune для preview. |
| **W5-R7** | State-machine retry-policy маскирует real bugs (retry вместо fix) | M | M | Retry только для transient exceptions (TimeoutExpired, FileNotFoundError, network). Non-transient — fail-fast. Логирование каждой retry-попытки IMP:8. Max 3 retries — не бесконечно. |
| **W5-R8** | Параллельный агент (tests/) конфликтует с новыми test-файлами Wave 5 | M | L | Координация: новые test-файлы в tests/unit/ с уникальными именами (test_*_rollback.py, test_reconciler_r7_*.py и т.д.). Проверка git status перед commit. См. §Dependencies. |
| **W5-R9** | D-CODE-1: UPDATE_STEP_COUNT drift ломает W5-E6 retry-policy/pre-conditions | M | M | Pre-remediation P2: исправить константу до W5-E6. VR Phase 2 подтвердил drift. W5-E6 Coder стартует только после фикса. |
| **W5-R10** | Test baseline degradation (26 failures, 16 в test_reconciler.py) маскирует regression от W5-E2/E3/E4 | M | H | Pre-remediation P1: исправить test_reconciler.py failures до W5-E2. 50% pass rate делает TDD бессмысленным — Coder не отличит pre-existing от своих багов. VR §4.3-4.4. |

---

## 6. Метрики успеха (замер в VerificationReport)

| Метрика | Baseline (2026-07-22) | Цель (конец Wave 5) |
|---------|----------------------|---------------------|
| converge K8s-parity score | 4/10 (R1-R6, R5/R6 read-only) | 7/10 (R7/R8/R9 + self-heal R5/R6) |
| deploy_docker_group rollback | нет (failures логируются) | atomic rollback + audit-state-record |
| R-units в reconciler.py | 6 (R1-R6) | 9 (R1-R9) |
| orphan reconciler mode | detect-only | detect + self-heal (--self-heal flag) |
| state-machine retry-policy | нет | exponential backoff (2s/4s/8s, max 3) |
| Unit-тестов bootstrap | 12 файлов | 17 файлов (+5 новых) |
| Unit-test pass rate (baseline) | 83/109 (76.1%) — ⚠️ 26 failures pre-existing | 100% (all green) — цель AC-7 |
| Inline `python3 -c` в bootstrap/ | 0 (Wave 4 закрыл) | 0 (сохраняется) |
| @complexity coverage (reconciler.py) | 2/23 functions (8.7%) | ≥20/23 functions (≥85%) — новые R7/R8/R9 + существующие |
| IMP:9 Anti-Illusion pass rate | 107/109 (98.2%) | 109/109 (100%) — P4 добавляет IMP:9 в _cleanup_legacy_container |

---

## 7. Dependencies и координация

### 7.1. Параллельный агент (tests/ директория)

**Контекст:** Оператор сообщил, что параллельный агент фиксит тесты в `tests/` директории и «почти закончил».

**Координация:**
- Wave 5 добавляет **новые** test-файлы в `tests/unit/` с уникальными именами (test_docker_orchestrator_rollback.py, test_reconciler_r7_volumes.py, test_reconciler_r8_sudoers.py, test_reconciler_r9_runtime.py, test_orphan_reconciler_selfheal.py). Конфликт имён минимален.
- Расширение существующих test_state_machine.py — потенциальное пересечение. Митигация: Coder добавляет новые test-функции в конец файла, не модифицирует существующие.
- **Перед стартом Coder:** `git status` + `git log --oneline -5` для проверки состояния параллельного агента. Если параллельный агент ещё активен — координировать через оператора или отложить merge до его завершения.
- **Перед commit/merge:** повторный `git status` + разрешение конфликтов (если есть) вручную.

### 7.2. Staging-нода tronyx-vps

- AC-8 staging-test требует SSH root доступ к tronyx-vps.
- Тест пересоздаваемый (invariant 9) — backward compat не требуется.
- Используется для: converge R7/R8/R9 validation, transactional rollback path (success-case), audit-trail verification.

### 7.3. Audit-trail infrastructure (W2-E3 dependency)

- W5-E1 rollback-state-record и W5-E5 orphan self-heal audit используют audit_logging.sh wrapper (W2-E3, уже реализован).
- Проверка перед стартом: `tail -f /var/log/platform/audit.log` на staging-нode — записи от W2-E3 entrypoints присутствуют.

---

## 8. Anti-goals (что мы НЕ делаем в Wave 5)

- ❌ **Continuous-watch** в converge (R-RISK-7, §4.5 Option D) — systemd-timer territory. HARD STOP на 7/10.
- ❌ **ACID-транзакции** для bootstrap (§4.5 Option B) — overkill для bare-metal. Transactional rollback только на failure, не full 2PC.
- ❌ **Декомпозиция shell-фасадов** converge.sh/bootstrap.sh — они уже thin facades (W4-E3), не трогаем.
- ❌ **Новые make-таргеты** (S7 constraint) — converge, bootstrap-node, node-update зарегистрированы.
- ❌ **Миграция на K8s** — K8s-parity это conceptual score, не фактическая миграция.
- ❌ **Property-based testing** (Brief §12) — откладывается до post-Wave 5 Decision Gate.

---

## 9. Production-release strategy

1. **Feature-branch** `wave5-bootstrap-reliability` от `origin/main` (не от локального main — CI Pre-flight Rules).
2. **Поэтапный commit** по эпикам (W5-E1, W5-E2/E3/E4, W5-E5, W5-E6) — explicit merge-commit pattern per R-RISK-1/PGM-R2.
3. **Local gate:** `make gate MODE=fast` green перед каждым push.
4. **Staging-test** (AC-8) на tronyx-vps перед merge.
5. **Explicit merge-commit** в main (не squash — audit-trail для revert-path).
6. **Revert-path:** `git revert <merge-commit>` + `make bootstrap-node NODE=<prod>` (SCP/rsync доставит старую версию).
7. **Post-merge:** обновить Brief 027 ссылкой на DevPlan 039 + VerificationReport. Зафиксировать KPI замеры (K8s-parity 7/10).

---

## 10. Делегирование в dev-pipeline

```
Phase 0: PRE-REMEDIATION (перед делегированием Coder)
    ├─► P1: Fix test_reconciler.py (16 failures → 0, target ≥90% pass)
    ├─► P2: Fix D-CODE-1 (UPDATE_STEP_COUNT 6→7 или удалить deliver_overlays из list)
    ├─► P3: Answer W5-E2 open question (volumes self-heal vs detect-only)
    └─► P4: Add IMP:9 logs in _cleanup_legacy_container success-path
    ═══ GATE: test_reconciler.py ≥90% pass + D-CODE-1 resolved ═══

Phase 1: IMPLEMENTATION
039-DevPlan.md (этот файл)
    │
    ├─► Track A: W5-E1 (Coder: docker_orchestrator.py rollback → QA: test_rollback.py)
    ├─► Track B: W5-E2 → W5-E3 → W5-E4 (Coder: reconciler.py R7/R8/R9 → QA: test_r7/r8/r9.py)
    │             ⚠️ Track B стартует только после Phase 0 Gate (test_reconciler.py green)
    ├─► Track C: W5-E5 (Coder: orphan_reconciler.py self-heal → QA: test_selfheal.py)
    ├─► Track C: W5-E6 (Coder: state_machine.py retry-policy → QA: test_state_machine.py extension)
    │             ⚠️ Track C W5-E6 стартует только после P2 (D-CODE-1 resolved)
    │
    └─► Финал: AC-7 regression (make gate) → AC-8 staging (tronyx-vps) → merge → VerificationReport
```

**Принципы делегирования:**
1. TDD: тест ПЕРЕД реализацией для каждого эпика (Principle — Fail-Fast).
2. Один эпик = один Coder-subagent = один QA-cycle.
3. После каждого эпика: `make gate MODE=fast` green как checkpoint.
4. Финальный QA: full VerificationReport по протоколу (Phase 1-6).
5. Pre-remediation gate (Phase 0): P1+P2 обязательны до Track B и W5-E6 соответственно. P3 — решение оператора до W5-E2. P4 — опционально.
6. Все новые функции получают `## @complexity` теги (VR D-QUAL-1/D-QUAL-2). Цель: reconciler.py ≥85% coverage (сейчас 8.7%).

---

$END_DEVPLAN

---

## Заключение

Wave 5 завершает Strangler-Fig программу 027. База Wave 4 (Python-модули: docker_orchestrator, reconciler, orphan_reconciler, state_machine) позволяет реализовать 6 эпиков через **расширение существующих модулей** (Principle 6 — extension over duplication), не создавая новую архитектуру. Основной risk — sudoers/runtime self-heal на production-ноде (W5-R3, W5-R5) — митигируется visudo validation, conservative orphan criteria, feature-flags, staging-test.

**Pre-implementation QA (03-VerificationReport.md, 2026-07-22):** Verdict DEGRADED (WARNING), health score 63/100. 0 CRITICAL drifts, 1 MEDIUM (D-CODE-1 — UPDATE_STEP_COUNT structural drift), 26 baseline test failures. Test baseline degradation — основной blocker для TDD-based реализации W5-E2/E3/E4. Pre-remediation gate (§0) обязателен перед делегированием Coder. После устранения P1-P4 — DevPlan готов к реализации.

**Открытый вопрос** (W5-E2 volumes self-heal vs detect-only) требует решения оператора до реализации соответствующего эпика. По умолчанию (если решение не получено) — Вариант B (detect-only, conservative).

**После завершения Wave 5** — Decision Gate (Brief 027 §8): аналитический артефакт (~2 дня), сбор метрик за период программы, TRAP[DECISION] в root AGENTS.md с recommendation на 2027+.

**Готов к делегированию в dev-pipeline после Phase 0 pre-remediation gate (P1+P2).**
