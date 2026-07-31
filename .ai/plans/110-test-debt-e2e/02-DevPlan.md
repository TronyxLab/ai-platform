# $ARTIFACT_CONTRACT
# GREP_SUMMARY: devplan-110, test-debt, e2e-bootstrap-095, python-deps, forensics, strangler-regressions, PRE-FAIL-038
# STRUCTURE: ┌$ARTIFACT_CONTRACT┐ → ◇ Forensics P0.1/P0.2/P0.3 → ◇ Phase 1 code change → ◇ Phase 2 verification runs → ◇ Phase 3 closeout → ⊕ Risk Assessment → ⎋ Delegation
## @PURPOSE План закрытия тестового долга (VR 038 §PRE-FAIL) + верификация E2E-теста 095 + sanctioned exception для python_deps.sh
## @DESCRIPTION
Девплан трёх подзадач Brief 110. Каждая подзадача исследована на фактическое состояние
(см. Phase 0 — Forensics ниже); план отражает РЕАЛЬНОЕ состояние, а не буквальное прочтение Brief.

**Важная корректировка Brief:** фактическое состояние отличается от описанного в Brief:

- **Подзадача 1 (PRE-FAIL-1/-2)** — ФАКТИЧЕСКИ ЗАКРЫТА DevPlan 091:
  - `tests/unit/test_deploy_snapshot.py` удалён в 089 (deploy-project.sh → DeployOrchestrator)
  - 7 удалённых nodeid зарегистрированы в `tests/test_inventory_changes.yaml` (issue 091)
  - `tests/test_inventory.yaml` призраков НЕ содержит (синхронизирован)
  - `test_gate_compose_profiles_consistency.py` обновлён 2026-07-30: deploy-project.sh callsite
    удалён, COMPOSE_PROFILES теперь распространяется через os.environ
  - `test_env_sync_order` (PRE-FAIL-3) — тест не найден в дереве, вероятно переименован/удалён
  → Подзадача 1 сводится к **верификации** (прогон + инвентарьный чек), а не к правкам кода
- **Подзадача 2 (E2E 095)** — ФАКТИЧЕСКИ РЕАЛИЗОВАНА: `tests/e2e/test_bootstrap_pipeline.py`
  (394 LOC, 8 сценариев T6-T13), маркер `requires_node` зарегистрирован в pyproject.toml:61,
  nodeid присутствуют в test_inventory.yaml. → Сводится к **верификации сбора** (collect-only),
  т.к. для прогона нужен test-VPS (AC2-soft: presence + collect; AC2-hard: live run = optional)
- **Подзадача 3 (python_deps.sh)** — единственная правка кода: добавить `@rationale` о
  sanctioned exception (Tier 1 триггер не применим — это библиотечная функция availability-check)

## @RATIONALE
- Brief 110 написан до закрытия долга 091; фактический объём работ < заявленного.
  Девплан фиксирует реальное состояние, чтобы QA верифицировал ACTUAL, а не ghost-долг.
- E2E-тест требует test-VPS (SSH, AGE_SECRET_KEY, Docker) — недоступен в dev/CI окружении.
  Верификация = collect + presence + marker, а не live run (см. tests/AGENTS.md: requires_node
  НЕ входит в `make test MARKER=all` и `make gate`, запускается только `make test-node NODE=`).
- python_deps.sh:22 — `python3 -c "import ${module}"` это availability-check библиотечной
  функции, не бизнес-логика. Tier 1 триггер (языковая политика) к availability-check не применим:
  замена на `find_spec` создаёт ложное чувство «sanctioned», не меняя сути (всё ещё python3 -c).
  Решение: явная `@rationale`-аннотация = sanctioned exception (как install-docker.sh:116 в VR 038).

## @ACCEPTANCE_CRITERIA
- AC1: `make test MARKER=all` — 0 failures (Docker-зависимые маркеры могут быть пропущены при
  отсутствии Docker на dev-машине, фиксируется в VR; gate+contract+static_audit ДОЛЖНЫ быть зелёными)
- AC2: `tests/e2e/test_bootstrap_pipeline.py` — 8 тестов, collect-only проходит без ошибок,
  каждый тест имеет `@pytest.mark.requires_node` + LDD IMP:9 assertion. nodeid присутствуют в
  test_inventory.yaml. Live-run на test-VPS — OPTIONAL (недоступен в dev-окружении).
- AC3: `core/lib/python_deps.sh` — добавлена `@rationale`-аннотация о sanctioned exception
  (availability-check библиотечной функции, Tier 1 не применим). Выбран вариант (б) из Brief AC3:
  вариант (а) — замена `import` на `importlib.util.find_spec` — отклонён, т.к. не устраняет
  `python3 -c` (остаётся subprocess-вызов) и не меняет сути (см. @RATIONALE)
- AC4: `make test-inventory-sync` — инвентарь актуален (no drift после прогона)
- AC5: `make gate MODE=fast` — зелёный (для статической части; Docker-зависимые шаги
  документируются как SKIPPED/BLOCKED в VR если Docker недоступен)
- AC6: VR 038 §PRE-FAIL-1/-2/-3 — все три закрыты (1/-2 в 091, -3 не воспроизводится)

## @IMPLEMENTS Brief 110
## @IMPACTS
- core/lib/python_deps.sh (правка: @rationale)
- tests/test_inventory.yaml (regenerate via make test-inventory-sync — без ручных правок)
- .ai/plans/038-arch-unification-node-yaml-errors-loggers/06-VerificationReport.md (UPDATE note
  о закрытии PRE-FAIL делегацией 091 — OPTIONAL, по решению Sysadmin)
## @REQUIRES
- Результаты миграционных брифов 099-109 для прогона `make test MARKER=all`
- Docker для smoke/component/integration/predeploy маркеров (AC1/AC5 — soft при отсутствии)
- test-VPS для live E2E прогона (AC2-hard — OPTIONAL)

---

## Phase 0 — Forensics: ACTUAL state vs Brief (research-only, no code changes)

### P0.1 Подзадача 1: Strangler-тест-регрессии (VR 038 §PRE-FAIL)

| PRE-FAIL | Brief claim | ACTUAL state | Evidence |
|----------|-------------|--------------|----------|
| -1 `test_deploy_snapshot.py` | "функции удалены, тесты остались" | ФАЙЛ УДАЛЁН в 089, 7 nodeid в changelog 091 | glob tests/unit/test_deploy_*.py → нет deploy_snapshot; test_inventory_changes.yaml:717-741 (issue 091) |
| -2 `test_gate_compose_profiles_consistency` | "затронут adopt-project миграцией" | ОБНОВЛЁН 2026-07-30: deploy-project.sh callsite удалён, комментарий в @changes | tests/gates/test_gate_compose_profiles_consistency.py:13-15 (комментарий «Deploy-project.sh callsite removed 2026-07-30»), @changes :28-30 (исторический) |
| -3 `test_env_sync_order` | (VR 038 §120) ".env 88 vs .env.example 87 keys" | ТЕСТ НЕ НАЙДЕН в дереве tests/ | grep test_env_sync_order → 0 matches в *.py |

**Вывод P0.1:** все три PRE-FAIL фактически закрыты (091 или ранее). AC1 сводится к верифицирующему
прогону `make test MARKER=all` + проверке отсутствия drift в инвентаре.

### P0.2 Подзадача 2: E2E-тест 095

| Артефакт | ACTUAL state | Evidence |
|----------|--------------|----------|
| Файл `tests/e2e/test_bootstrap_pipeline.py` | СУЩЕСТВУЕТ, 394 LOC, 8 сценариев | glob → 394 lines, T6-T13 |
| Маркер `requires_node` | ЗАРЕГИСТРИРОВАН | pyproject.toml:61 |
| nodeid в inventory | ПРИСУТСТВУЮТ (8 шт.) | test_inventory.yaml:26-33 |
| conftest fixtures | СУЩЕСТВУЮТ (requires_node, node_ssh, node_state, test_project_fixture) | tests/e2e/conftest.py |
| LDD IMP:9 assertion | КАЖДЫЙ тест (`assert_ldd_imp9_e2e(caplog)`) | test_bootstrap_pipeline.py:126,155,199,249,281,312,354,391 |
| `make test MARKER=all` includes requires_node | НЕТ (orthogonal) | ci.mk:66-86 (all = validate→lint→gates→contract→static→predeploy→smoke→component→integration) |
| `make test MARKER=e2e` | `-m "e2e"` (HTTP *.tronyx.ru, НЕ requires_node) | ci.mk:61-63 |
| `make test-node NODE=` | `-m "requires_node"` (единственный путь) | ci.mk:113 |

**Вывод P0.2:** реализация завершена. AC2 = presence + collect + marker + inventory (soft),
live-run на test-VPS — OPTIONAL (недоступен в dev-окружении, требует SSH/AGE/Docker на VPS).

### P0.3 Подзадача 3: python_deps.sh inline fix

| Артефакт | ACTUAL state | Evidence |
|----------|--------------|----------|
| `core/lib/python_deps.sh:22` | `python3 -c "import ${module}"` — единственный inline в файле | read → line 22 |
| Назначение | Библиотечная функция `require_python_module()` для availability-check | MODULE_CONTRACT @purpose |
| VR 038 §149 | CLASSIFIED как "LEGITIMATE — module availability check" | 06-VR:149 |

**Вывод P0.3:** Tier 1 триггер языковой политики (извлечение бизнес-логики в .py) неприменим к
availability-check — здесь нет бизнес-логики для извлечения. Замена `import` на `find_spec` не
устраняет `python3 -c` (это всё равно subprocess-вызов). Решение: явная `@rationale`-аннотация =
sanctioned exception, аналогично install-docker.sh:116 (VR 038 §150).

---

## Phase 1 — Code change: python_deps.sh @rationale (AC3)

### T1.1 Добавить @rationale в MODULE_CONTRACT и inline comment

**Файл:** `core/lib/python_deps.sh`

**Изменение:** расширить существующий блок `@rationale` в MODULE_CONTRACT (строка 11) и добавить
inline `# ⚠️ TRAP[DECISION]`-комментарий над строкой 22.

**@rationale (расширение строки 11):**
```
## @rationale Language policy: inline python3 -c "import X" → require_python_module (this function).
##            This file itself uses `python3 -c "import ${module}"` (line 22) as a SANCTIONED
##            EXCEPTION — availability-check of a library function, not business logic. Tier 1
##            Strangler-trigger does not apply: there is no logic to extract to a .py module.
##            Replacing `import` with `importlib.util.find_spec` would still be a `python3 -c`
##            subprocess call and adds no value. Classification: LEGITIMATE (VR 038 §149).
```

**Inline комментарий над строкой 22:**
```bash
    # ⚠️ TRAP[DECISION] · 2026-07-31 · LOW · python3 -c "import" — sanctioned availability-check
    # · Rejected: extract to .py module (risk: no business logic to extract — Tier 1 N/A)
    # · Reason: require_python_module() IS the sanctioned replacement for inline python3 -c in
    #   callers; its own internal import-check is the irreducible primitive. VR 038 §149: LEGITIMATE.
    # · Rev: если require_python_module() начнёт содержать >1 логическую ветку → Strangler-Fig.
    if python3 -c "import ${module}" 2>/dev/null; then
```

**Верификация T1.1:**
- `core/lib/python_deps.sh` — read confirms @rationale расширение + TRAP[DECISION] присутствуют
- `make check-no-new-inline-python3` (pre-commit hook) — НЕ блокирует (python_deps.sh в whitelist)
- Функциональность неизменна (только комментарии, 0 исполняемых строк изменено)

---

## Phase 2 — Verification runs (AC1, AC2, AC4, AC5)

### T2.1 Инвентарьный чек (AC4, AC2-soft)

**Команды:**
```bash
# 1. Сбор E2E тестов (без прогона) — AC2-soft
python3 -m pytest tests/e2e/test_bootstrap_pipeline.py --collect-only -q

# 2. Синхронизация инвентаря — AC4
make test-inventory-sync

# 3. Diff инвентаря (должен быть пустой после sync)
git diff --stat tests/test_inventory.yaml
```

**Ожидаемый результат:**
- 8 тестов собраны без ошибок collection
- `make test-inventory-sync` exit 0
- git diff пустой (инвентарь уже актуален) ИЛИ содержит только stub-правки (документируются в VR)

### T2.2 Gate fast (AC5, статическая часть)

**Команда:**
```bash
make gate MODE=fast
```

**Ожидаемый результат:** EXIT 0 для статической части. Docker-зависимые шаги (predeploy/smoke/
component/integration) — SKIPPED/BLOCKED при отсутствии Docker на dev-машине, фиксируются в VR.

**Fallback (если make gate падает на Docker-зависимом шаге):**
```bash
# Изолированный прогон статических gate'ов
python3 -m pytest tests/gates/ -m "gate and not requires_docker" -v
make test MARKER=contract
make test MARKER=static_audit
```

### T2.3 Полный прогон тестов (AC1)

**Команда:**
```bash
make test MARKER=all
```

**Ожидаемый результат:** EXIT 0 если Docker доступен. При отсутствии Docker — статические маркеры
(gate, contract, static_audit) ДОЛЖНЫ быть зелёными, Docker-зависимые (predeploy/smoke/component/
integration) — SKIPPED с reason "requires docker" (НЕ FAILED — Rule R4: NO_SERVICE = FAIL, но
docker-absence = infrastructure config error, документируется в VR как environmental BLOCK).

**Верификация AC1:**
```bash
# Явная проверка: нет FAILED, только PASSED + SKIPPED
python3 -m pytest tests/ -m "gate and not requires_docker" --tb=short -q
python3 -m pytest tests/unit/ -q --tb=short
```

### T2.4 (OPTIONAL) Live E2E run на test-VPS (AC2-hard)

**Условие:** доступна test-VPS (node-configs/test-e2e/node.yaml), SSH-ключ, AGE_SECRET_KEY_FILE.

**Команда:**
```bash
make test-node NODE=test-e2e
```

**Ожидаемый результат:** 8/8 PASSED. Pipeline: cold-start 9 INIT фаз → node-update 5 фаз →
converge (idempotent) → deploy test-project → healthcheck → backup snapshot → restore roundtrip →
rebootstrap idempotent.

**При отсутствии test-VPS:** AC2-hard помечается DEFERRED в VR (не блокирует close 110, т.к.
реализация + collect + marker = достаточные evidence для AC2-soft).

---

## Phase 3 — Closeout (AC6, опционально)

### T3.1 (OPTIONAL) Update note в VR 038

**Файл:** `.ai/plans/038-arch-unification-node-yaml-errors-loggers/06-VerificationReport.md`

**Изменение:** добавить closeout-заметку в §Findings Summary (строки 308-310) о закрытии
PRE-FAIL-1/-2/-3 через DevPlan 091 + Brief 110.

**Решение:** OPTIONAL — по усмотрению Sysadmin (VR — immutable artifact, предпочтительно
документировать close в VR 110, а не править VR 038). Default: SKIP (T3.1 отменяется, close
фиксируется в 03-VerificationReport.md задачи 110).

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| `make test MARKER=all` падает на Docker-зависимом маркере (нет Docker на dev) | HIGH | LOW | T2.3 fallback: изолированный прогон статических маркеров; Docker-absence = environmental BLOCK (Constitution §7) |
| `make test-inventory-sync` создаёт drift (новые nodeid после 099-109 миграций) | MEDIUM | LOW | T2.1 git diff после sync; если drift — это легитимные новые тесты, фиксируются в VR |
| `make gate MODE=fast` падает на manifest drift (generated files) | MEDIUM | LOW | `make fix-gate && git add -u` (pre-flight rule, .kilo/rules/_project.md) |
| E2E live-run зависает (SSH timeout, нет GNU timeout на macOS — TRAP в AGENTS.md) | MEDIUM | MEDIUM | AC2-hard OPTIONAL; при прогоне — gtimeout/coreutils (TRAP[DECISION] ssh.sh) |
| VR 038 правка нарушает immutability artifact'а | LOW | LOW | T3.1 OPTIONAL; default SKIP, close в VR 110 |

---

## Delegation

**Coder:** T1.1 (единственная правка кода — python_deps.sh @rationale). ~10 мин.

**Sysadmin:** T2.1-T2.4 (верифицирующие прогоны). ~30-60 мин (зависит от Docker/VPS доступности).

**QA:** Cross-cutting verification — проверить, что:
1. AC1-AC6 evidences собраны из прогонов Sysadmin
2. AC3 (python_deps.sh) — правка Coder не нарушила функциональность
3. AC2 — collect-only чистый, 8 тестов, маркеры присутствуют
4. Все SKIP/BLOCKED (Docker/VPS) задокументированы как environmental, не как masqueraded fail
5. VR 110 закрывает все 6 AC с explicit evidence (команды + exit codes + IMP:9 logs)

Делегирование QA — на flash-модели (cost-effective для verification-pattern работы).
