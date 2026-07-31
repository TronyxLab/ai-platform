$START_VERIFICATION_REPORT

# VerificationReport 095 — E2E Bootstrap Pipeline Test (Plan Audit)

$ARTIFACT_CONTRACT
PURPOSE:               QA аудит DevPlan 095 перед реализацией — проверка $ARTIFACT_CONTRACT полноты, структурной целостности DevPlan-протокола, консистентности с кодбазой, кросс-референсной валидации с DP-091/089/092, feasibility анализа и AC→Task traceability.
DESCRIPTION:           Plan-only audit (до имплементации). Фазы 1, 2 (ограниченно — проверка консистентности с существующими файлами), AC→Task трассировка. Phase 5 (runtime) не применим — код не написан. Проверено 13 конкретных assertions из задания пользователя (a–i + кросс-референсы + feasibility).
RATIONALE:             DevPlan 095 закрывает критический gap (0 E2E тестов на реальном окружении после Strangler-Fig 087-094). Цена ошибки в плане высока: неверный маркер/фильтр поломает CI gate, а переделывать E2E тесты дорого (требуют test-VPS). План должен быть верифицирован до первого coder implement.
ACCEPTANCE_CRITERIA:   Все 13 assertions из задания пользователя проверены. AC→Task coverage = 13/13. Findings классифицированы по severity.
IMPLEMENTS:            User QA task — проверка плана 095 перед реализацией
IMPACTS:               CREATE: VerificationReport.md. Не меняет codebase.
REQUIRES:              DevPlan 095 (02-DevPlan.md), Brief 095 (01-Brief.md), pyproject.toml, makefiles/ci.mk, core/entrypoint-manifest.yaml, tests/AGENTS.md, root AGENTS.md, VR 091 (03-VerificationReport.md), планы 089/092.
$END_ARTIFACT_CONTRACT

---

🔒 **Verified against SHA** `c8c6add369144f1593eab9fdf3c74d5356e3e16d`
⚠️ **WARNING:** `git diff --name-only` → `Doxyfile` (dirty). НЕ связано с планом 095.

---

## §0. Scope & Methodology

**Task size:** STANDARD (14 файлов: 10 CREATE + 4 MODIFY, затрагивает CI filters, entrypoint-manifest, pyproject.toml — config files в скоупе).

**Audit type:** Plan audit (pre-implementation). Phase 5 (runtime) пропущен — код не существует. Phase 2 (drift) — проверена консистентность плана с существующим кодом.

**Verified assertions (из задания пользователя):**
| # | Assertion | Section |
|---|-----------|---------|
| 3a | pyproject.toml markers — `requires_node` vs `e2e` ортогональность | §1.3a |
| 3b | ci.mk filters — `not e2e → not e2e and not requires_node` на L32,39,140 | §1.3b |
| 3c | entrypoint-manifest.yaml allowed_verbs — `test-node` отсутствие/добавление | §1.3c |
| 3d | tests/AGENTS.md taxonomy — `tests/e2e/` отсутствие | §1.3d |
| 3e | Language policy — Python-враппер над shell lib/ssh.sh | §1.3e |
| 3f | Invariant 9 — test_vps_fresh согласованность | §1.3f |
| 3g | Brief 095 существование + IMPLEMENTS консистентность | §1.3g |
| 3h | Anti-Loop Protocol — PYTEST_NO_ESCALATION=1 | §1.3h |
| 3i | Test Honesty R4 — pytest.fail vs pytest.skip | §1.3i |
| 4 | Cross-ref: DP-091/089/092 + VR 091 AC-B3 + state_migration | §2 |
| 5a | node-configs/ существование | §3.5a |
| 5b | Session-scoped autouse timeout | §3.5b |
| 5f | requires_node fixture vs marker confusion | §3.5f |
| 6 | AC→Task traceability | §4 |

---

## §1. Phase 1 — Artifact Integrity & Static Audit

### 1.1 $ARTIFACT_CONTRACT Completeness

| Field | Present | Non-empty | Meaningful | Evidence |
|-------|:---:|:---:|:---:|-----------|
| PURPOSE | ✅ | ✅ | ✅ | "Создать автоматизированный E2E тест полного bootstrap-pipeline" — чёткая цель |
| DESCRIPTION | ✅ | ✅ | ✅ | Детально: маркер, таргет, 11 сценариев, fixtures, test-VPS |
| RATIONALE | ✅ | ✅ | ✅ | 271+ тестов — 0% покрытия реального окружения, Strangler-Fig gap, 3 экспертизы |
| ACCEPTANCE_CRITERIA | ✅ | ✅ | ✅ | 13 AC с grep-верифицируемыми критериями |
| IMPLEMENTS | ✅ | ✅ | ✅ | "Brief 095 §Required Actions (Waves 1-4), закрытие GAP-4, AC10 (089) + AC12 (087), VR 091 AC-B3" |
| IMPACTS | ✅ | ✅ | ⚠️ | 9 файлов CREATE (пропущен `__init__.py` — см. Finding F1) |
| REQUIRES | ✅ | ✅ | ✅ | DP-091 STABLE, DP-092 желательно, test-VPS, AGE_SECRET_KEY, SSH |

**Boundary markers:** `$START_DEVPLAN` (L1) + `$END_DEVPLAN` (L478) — оба на месте, правильно спарены.

### 1.2 DevPlan Protocol Structure

| Required Element | Present | Location |
|-----------------|:---:|----------|
| Draft Code Graph (XML/structural) | ✅ | §3 (L149-192) — ASCII tree с зависимостями |
| Step-by-step Data Flow / process simulation | ✅ | §4 Wave Structure + §4.1 Fixtures Architecture — 4 волны, 20 задач, session/function scope |
| Acceptance Criteria (verifiable) | ✅ | §6 (L325-337) — 13 AC с grep/pytest-верифицируемыми проверками |
| File Manifest | ✅ | §5 (L294-317) — 10 CREATE + 4 MODIFY + 0 DELETE |
| §1 Current State | ✅ | §1 (L37-76) |
| §2 Target State | ✅ | §2 (L80-145) |
| §3 Draft Code Graph | ✅ | §3 (L148-192) |
| §4 Wave Structure | ✅ | §4 (L196-291) |
| §5 File Manifest | ✅ | §5 (L294-317) |
| §6 Acceptance Criteria | ✅ | §6 (L323-337) |
| §7 Design Decisions | ✅ | §7 (L341-386) — 6 DD с rationale |
| §8 Implementation Commands | ✅ | §8 (L389-438) |
| §9 Test-VPS Preparation Checklist | ✅ | §9 (L442-461) |
| §10 Risks & Mitigations | ✅ | §10 (L465-474) |

**Verdict:** Структура DevPlan-протокола выполнена полностью. Все 10 секций присутствуют.

### 1.3 Специфические проверки консистентности

#### 1.3a — pyproject.toml markers (✅ PASS)
- Файл: `pyproject.toml:54-70` — маркер `requires_node` отсутствует.
- Существующий `e2e` (L60): `"e2e: manual end-to-end tests against external *.tronyx.ru (no Docker, dev-only)"`.
- Добавление `requires_node` ортогонально: `e2e` = HTTP-проверки внешних доменов, `requires_node` = SSH/Docker pipeline на test-VPS.
- DD1 rationale подтверждается анализом кода: разные зависимости (SSH vs HTTP), разные таргеты (test-node vs e2e), разные конфигурации (_conftest/node.py vs _conftest/e2e.py).
- `--strict-markers` в `addopts:53` требует регистрации маркера — T1 обязателен для прохождения gate.

#### 1.3b — ci.mk filters (✅ PASS)
- **Line 32-33** (`MARKER=all` / `MARKER=static`):
  ```
  -m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)"
  ```
- **Line 38-39** (`MARKER=static_audit`):
  ```
  -m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)"
  ```
- **Line 139-140** (`make gate MODE=fast` Step 5):
  ```
  -m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)"
  ```
- Все три строки содержат идентичный фильтр с `not e2e`. Замена `not e2e` → `not e2e and not requires_node` синтаксически корректна (pytest marker expression — Python-like boolean).
- `.PHONY` на L12: `test-node` нужно добавить к существующему списку.

#### 1.3c — entrypoint-manifest.yaml allowed_verbs (✅ PASS)
- `allowed_verbs` начинается с L623. `test` присутствует (L678). `test-node` **отсутствует**.
- Gate `test_all_makefile_targets_in_allowed_verbs` зареджит, если `test-node` есть в Makefile `.PHONY` но не в allowed_verbs. T2 корректно добавляет регистрацию.
- Полный список allowed_verbs: 45 глаголов (L624-684). `test-node` станет 46-м.

#### 1.3d — tests/AGENTS.md taxonomy (✅ PASS)
- Directory Taxonomy (L59-69): 7 записей. `tests/e2e/` **отсутствует**.
- T19 добавляет row: `tests/e2e/` — "E2E pipeline tests against test-VPS (requires_node marker, make test-node)".
- Структура таблицы поддерживает добавление строки без изменения существующих.

#### 1.3e — Language policy compliance (✅ PASS)
- `tests/_conftest/node.py` → `NodeSSHClient` оборачивает `core/lib/ssh.sh` через subprocess.
- AGENTS.md языковая политика: "Bash остаётся для: ... lib-функций низкого уровня (lib/logging.sh, lib/paths.sh, lib/ssh.sh). Существующие стабильные shell-библиотеки ... НЕ мигрируются на Python."
- `lib/ssh.sh` — явно указан как стабильная shell-библиотека, НЕ подлежащая миграции. Python-враппер через subprocess — это канонический паттерн согласно политике (Python-first для нового кода, shell как внешняя зависимость).
- `NodeSSHClient` не содержит inline `python3 -c` или heredoc — чистая оркестрация subprocess.

#### 1.3f — Invariant 9 (✅ PASS)
- Инвариант: "Тестовый сервер может быть пересоздан заново — обратная совместимость не требуется."
- `test_vps_fresh` (session-scoped autouse): `bootstrap-node --force` → `[IMP:9][main] --force: Clearing state` → чистый cold start.
- Соответствует: не нужно тестировать миграцию state.json, только cold-start.
- User constraint из Brief: "❌ НЕ тестировать миграцию state.json со старого формата" — соблюдён.

#### 1.3g — Brief 095 существование (✅ PASS — пользователь ошибся)
- **Факт:** `01-Brief.md` существует (`.ai/plans/095-e2e-bootstrap-pipeline-test/01-Brief.md`, 59 строк, $START_BRIEF/$END_BRIEF).
- IMPLEMENTS в DevPlan: "Brief 095 §Required Actions (Waves 1-4)" — Brief действительно содержит Waves 1-4 (L30-49).
- Пользовательский glob `.ai/plans/095*/**` мог не найти файлы из-за особенностей разрешения `**` в glob-паттернах. Bash `ls` показывает оба файла.
- ⚠️ **Однако:** Brief §Wave 1 говорит `@pytest.mark.e2e` и `make test-e2e`, а DevPlan использует `requires_node` и `make test-node`. Это divergence между Brief и DevPlan (см. Finding F3).

#### 1.3h — Anti-Loop Protocol (✅ PASS)
- `PYTEST_NO_ESCALATION=1` используется в `make test-node` target (L129) и во всех ci.mk test target'ах (L32, L38, L131, L133, L139, L144).
- `.kilo/rules/testing.md` Anti-Loop Protocol описывает escalation через попытки (1-2: checklist, 3: external help, 4: reflection, 5+: escalation). `PYTEST_NO_ESCALATION=1` семантически означает «не эскалировать».
- Для E2E тестов это корректно: E2E тесты медленные (~30min), эскалация при каждом fail'е сделает их непригодными. Отказ E2E = либо тест-VPS недоступна (R4 fail), либо реальный баг — оба варианта не решаются эскалацией.
- Проверка реализации: `conftest.py` должен проверять `PYTEST_NO_ESCALATION` и пропускать escalation logic. Это ответственность Coder'а, план корректно задаёт переменную.

#### 1.3i — Test Honesty Rules R4 (✅ PASS)
- R4: "NO_SERVICE = FAIL, not skip. Environmental absence is a configuration error — surface it, don't hide it."
- `requires_node` fixture (L90-101): `pytest.fail(..., pytrace=False)` при отсутствии `NODE` env.
- `pytrace=False` — хорошая практика, подавляет полный трейсбек для ожидаемой ошибки конфигурации.
- Сообщение об ошибке информативно: указывает `make test-node NODE=test-e2e` как решение.

---

## §2. Cross-Reference Validation

### 2.1 Prerequisite Plans Status

| Plan | Directory | Artifacts | Status |
|------|-----------|-----------|--------|
| DP-091 | `.ai/plans/091-stabilize-087-088-089/` | 01-Brief.md, 02-DevPlan.md, 03-VerificationReport.md | ✅ **STABLE** (WARNING: AC-B3 NOT_VERIFIABLE, AC-G1/G2 not verified) |
| DP-089 | `.ai/plans/089-deploy-orchestrator-unification/` | DevPlan.md, 02-VerificationReport.md, 03-VerificationReport.md | ✅ Имплементирован (2 VR) |
| DP-092 | `.ai/plans/092-scaffold-python-completion/` | 01-Brief.md, DevPlan.md, 02-VerificationReport.md | ✅ VR существует (WARNING: AC4 — отсутствуют тесты) |

**Вывод:** Все prerequisite планы существуют. DP-091 имеет вердикт STABLE. DP-092 имеет VR с задокументированными проблемами. DevPlan 095 корректно указывает DP-092 как "желательно" (не блокирующее), т.к. T9 (deploy test-project) использует scaffold.

### 2.2 VR 091 AC-B3 Verification

- VR 091:157 — AC-B3: `⚠️ NOT_VERIFIABLE` — "Требуется тестовая нода. Cold-start path проверен статически: setup_state(mode=INIT) → BootstrapPhase.INIT_PHASE_ORDER → 9 фаз"
- DevPlan 095 §1.2.8 + T6: `test_cold_start_bootstrap_9_phases` делает AC-B3 verifiable.
- AC13 в DevPlan 095: "VR 091 AC-B3 теперь verifiable: test_cold_start_bootstrap_9_phases → 9 INIT фаз done на test-VPS."
- **Согласованность:** ✅ DevPlan правильно идентифицирует gap и предлагает его закрытие.

### 2.3 state_migration.py Deletion

- VR 091:155 — AC-B1: `state_migration.py удалён + state_machine.py чист` → PASS.
- DevPlan 095 §1.3: "state_migration.py удалён (Wave B 091) — test только cold-start + fresh-failure resume"
- **Согласованность:** ✅ План 095 корректно учитывает, что state_migration удалён, и тестирует только cold-start.

---

## §3. Feasibility & Edge Cases

### 3.5a — node-configs/ Directory (✅ CORRECTLY IDENTIFIED)
- `node-configs/` **не существует** в репозитории (glob: 0 files).
- DevPlan §1.2.7 явно это отмечает: "node-configs/ не существует в репозитории — конфигурации нод хранятся в /opt/node-configs/ на VPS."
- §7 DD6 обосновывает: `node-configs/test-e2e/node.yaml` — infra-config, не test-data. Convention: `/opt/node-configs/<name>/node.yaml` на VPS, `node-configs/<name>/node.yaml` в репозитории для тестовых нод.
- T5 создаёт директорию. План корректно идентифицирует необходимость создания.

### 3.5b — Session-scoped Autouse Timeout (✅ ACCEPTABLE)
- `test_vps_fresh`: `node_ssh.ssh_exec("make bootstrap-node NODE=... --force", timeout=600)`.
- 600s (10 минут) для полного cold-start bootstrap — разумно. Bootstrap включает: Docker install, firewall, acme.sh, deploy-modules, cert orchestration. AGENTS.md TRAP lib/ssh.sh CI-deploy notes: "если CI-deploy стабильно < 300s → снизить deploy-default timeout с 600s до 400s."
- TRAP[DECISION] в §4.1 документирует trade-off: session-scoped (= 1 cold start ~10min + 11 тестов ~55min) vs function-scoped (= 11 cold starts ~2 часа).
- **Риск:** если тест-VPS медленная или bootstrap-node растянется >600s — тест упадёт по таймауту. Mitigation: параметризовать timeout через env var (не предложено в плане).

### 3.5f — requires_node: Fixture vs Marker (✅ VALID PATTERN, requires DOC clarification)

**Детальный анализ:**

План использует `requires_node` в двух ролях:
1. **MARKER** (pyproject.toml + `@pytest.mark.requires_node` на тестах): для `-m "requires_node"` селекции в `make test-node`.
2. **FIXTURE** (conftest.py `def requires_node() -> str`): для инъекции NODE имени в тестовые функции и FAIL при отсутствии NODE env.

**Это валидный pytest-паттерн.** Маркеры и фикстуры живут в разных namespace'ах pytest. Тест может одновременно:
```python
@pytest.mark.requires_node       # ← marker: selection via -m
def test_something(requires_node):  # ← fixture: injection + env validation
    ...
```

**Почему это не баг:**
- `--strict-markers` (pyproject.toml:53) требует, чтобы ВСЕ маркеры были зарегистрированы в `markers` списке. T1 регистрирует `requires_node` как маркер → strict-markers не упадёт.
- `-m "requires_node"` селектирует тесты, декорированные `@pytest.mark.requires_node` → работает корректно.
- `requires_node` как параметр функции разрешается pytest'ом в фикстуру из conftest.py → работает корректно.
- При `NODE` env отсутствует: фикстура делает `pytest.fail` → тест FAIL'ит (R4: NO_SERVICE = FAIL).

**Потенциальная ловушка (для Coder'а):**
Если Coder напишет тест с `@pytest.mark.requires_node` но БЕЗ параметра `requires_node` в сигнатуре — тест будет селектирован `-m`, но фикстура не вызовется, проверка `NODE` env не выполнится, и тест может упасть с неочевидной ошибкой (нет node name для SSH).

**Рекомендация:** Добавить в DevPlan явное примечание для Coder'а: "Каждый тест ДОЛЖЕН иметь ОБА: `@pytest.mark.requires_node` (для `-m` селекции) И `requires_node` параметр (для fixture injection)." Альтернатива: добавить `pytest_collection_modifyitems` в conftest.py для авто-добавления маркера всем тестам в `tests/e2e/`, что устранит риск расхождения.

**Verdict:** ✅ План архитектурно корректен. Требуется DOC-уточнение для предотвращения implementation error. Не блокирует реализацию.

### 3.5c — Additional Edge Case: `__init__.py` Omission

- `tests/e2e/__init__.py` перечислен в таблице §5 как CREATE, но НЕ в списке IMPACTS ($ARTIFACT_CONTRACT), и не назначен ни одной задаче явно.
- `__init__.py` необходим, чтобы `tests/e2e/` был Python-пакетом и `conftest.py` внутри него был обнаружен pytest'ом.
- **Рекомендация:** Явно добавить `__init__.py` создание в T4 (conftest + fixtures) или выделить микро-задачу. Без `__init__.py` фикстуры из `tests/e2e/conftest.py` не загрузятся.

---

## §4. Acceptance Criteria → Task Traceability

| AC | Описание | Покрывающая задача | Статус |
|----|----------|-------------------|--------|
| AC1 | `requires_node` маркер в pyproject.toml, gate зелёный | T1 | ✅ |
| AC2 | `test-node` target + allowed_verbs + NODE required check | T2 | ✅ |
| AC3 | `pytest.fail` в conftest.py, R4 compliance | T4 | ✅ |
| AC4 | 8 happy-path тестов PASS | T6, T7, T8, T9, T10, T11, T12, T13 | ✅ |
| AC5 | 3 failure-сценария PASS | T14, T15, T16 | ✅ |
| AC6 | 0 parametrize (детерминированный) | Кросс-каттинг — верифицируется grep в T20 | ✅ |
| AC7 | LDD trajectory + TRAP[TEST] в каждом тесте | T3 (assert_ldd_imp9_e2e) + T6-T16 (использование) | ✅ |
| AC8 | `tests/e2e/README.md` с секциями | T17 | ✅ |
| AC9 | `make gate MODE=fast` зелёный | T20 | ✅ |
| AC10 | Существующий `e2e` маркер не изменён | T1 (только добавление) + T20 (grep-проверка) | ✅ |
| AC11 | 3 fixture-файла test-project существуют | T18 | ✅ |
| AC12 | `make test MARKER=all` исключает requires_node | T2 (фильтр обновлён) + T20 (верификация) | ✅ |
| AC13 | VR 091 AC-B3 verifiable через test_cold_start_bootstrap | T6 | ✅ |

**AC coverage: 13/13 (100%).** Нет orphan AC. Все AC имеют хотя бы одну покрывающую задачу.

**Task coverage completeness:**
| Task | AC покрытие |
|------|------------|
| T1 | AC1, AC10 |
| T2 | AC2, AC12 |
| T3 | AC7 (helpers) |
| T4 | AC3 |
| T5 | (infra, не AC-specific) |
| T6 | AC4, AC13 |
| T7-T13 | AC4 |
| T14-T16 | AC5 |
| T17 | AC8 |
| T18 | AC11 |
| T19 | (taxonomy, не AC-specific) |
| T20 | AC6, AC9, AC10, AC12 (верификация) |

Все 20 задач покрывают AC или создают необходимую инфраструктуру. Нет orphan-задач.

---

## §5. Findings Register

### CRITICAL (0)
Нет критических блокирующих находок.

### HIGH (2)

**[HIGH] F1 — File Count Inconsistency + Missing __init__.py Assignment**
- **Где:** §5 заголовок "CREATE (9)" vs таблица с 10 файлами. `$ARTIFACT_CONTRACT.IMPACTS` перечисляет 9 CREATE (пропущен `tests/e2e/__init__.py`).
- **Проблема:** `__init__.py` необходим для обнаружения `conftest.py` pytest'ом. Без него фикстуры не загрузятся — E2E тесты не смогут найти `requires_node`, `node_ssh`, `node_state`.
- **Fix:** (1) Исправить заголовок §5 на "CREATE (10)". (2) Обновить IMPACTS: добавить `tests/e2e/__init__.py`. (3) Явно назначить создание `__init__.py` задаче T4 (вместе с conftest.py).
- **Severity rationale:** Без `__init__.py` весь план неработоспособен. Это implementation-blocking oversight, но не архитектурная ошибка — тривиальный фикс.

**[HIGH] F2 — Brief-DevPlan Divergence: маркер и target name**
- **Где:** Brief §Wave 1 (L32): `@pytest.mark.e2e` и `make test-e2e`. DevPlan: `requires_node` и `make test-node`.
- **Проблема:** Brief и DevPlan расходятся в naming. DevPlan DD1+DD2 аргументированно обосновывают diverging от Brief, но сам Brief не обновлён. При будущем аудите (чтение Brief → ожидание `e2e` маркера → код использует `requires_node`) это вызовет путаницу.
- **Fix:** Добавить в Brief примечание "⚠️ UPDATED by DevPlan 095: маркер изменён с `e2e` на `requires_node`, target с `test-e2e` на `test-node`. Rationale: DD1, DD2."
- **Severity rationale:** Не блокирует реализацию (DevPlan — authoritative artifact по R1), но создаёт technical debt для будущих агентов.

### MEDIUM (3)

**[MEDIUM] F3 — 14 фаз vs 9 INIT фаз: несогласованность**
- **Где:** DevPlan §1 Current State + Brief L5/L36 говорят "14 фаз", но T6 и VR 091 AC-B3 говорят "9 INIT фаз".
- **Проблема:** 14 фаз = 9 INIT + 5 UPDATE. Для cold-start bootstrap используются 9 INIT фаз. План не объясняет discrepancy явно.
- **Fix:** Добавить в §1 примечание: "14 фаз всего (9 INIT от φ1 до φ8.5 + 5 UPDATE от φ9 до φ13). E2E тесты покрывают INIT (T6) и UPDATE (T7) раздельно."
- **Severity rationale:** Может ввести в заблуждение Coder'а при имплементации T6 (сколько фаз проверять — 9 или 14?).

**[MEDIUM] F4 — requires_node fixture+marker: отсутствует явная инструкция для Coder'а**
- **Где:** §2.1 (fixture definition) + §2.2 (marker-based -m selection) + §2.3 (taxonomy — "uses requires_node marker").
- **Проблема:** План не содержит явного утверждения: "Каждый тест ДОЛЖЕН иметь `@pytest.mark.requires_node` И `requires_node` параметр". Coder может написать тест с маркером но без параметра — тест селектируется `-m`, но фикстура не вызовется, проверка NODE env не выполнится.
- **Fix:** Добавить в §2.3 или §5 примечание: "⚠️ IMPLEMENTATION NOTE: Each test MUST have both `@pytest.mark.requires_node` (for -m selection) AND `requires_node` as a function parameter (for fixture injection and env validation)."
- **Severity rationale:** Без этой инструкции Coder с вероятностью ~30% допустит ошибку, которая проявится только при запуске без NODE env.

**[MEDIUM] F5 — timeout 600s не параметризован**
- **Где:** §4.1 test_vps_fresh: `timeout=600`.
- **Проблема:** На медленной test-VPS или при высокой нагрузке bootstrap-node может занять >600s. Таймаут хардкожен — нет механизма переопределения.
- **Fix:** Добавить env var `E2E_BOOTSTRAP_TIMEOUT` с default 600. Или добавить в TRAP примечание о сценарии превышения.
- **Severity rationale:** False negative на медленной VPS. Не блокирует, но снижает надёжность.

### LOW (2)

**[LOW] F6 — $ARTIFACT_CONTRACT.IMPACTS: количество CREATE не совпадает**
- **Где:** IMPACTS: "CREATE: tests/e2e/conftest.py, tests/e2e/test_bootstrap_pipeline.py, tests/e2e/test_failure_scenarios.py, tests/e2e/fixtures/test-project/ (3 файла), tests/e2e/README.md, tests/_conftest/node.py, node-configs/test-e2e/node.yaml" = 9 файлов. Реально 10 (пропущен __init__.py).
- **Fix:** См. F1.

**[LOW] F7 — Not all tasks explicitly listed in IMPACTS MODIFY section**
- **Где:** IMPACTS.MODIFY перечисляет pyproject.toml, makefiles/ci.mk, core/entrypoint-manifest.yaml, tests/AGENTS.md — 4 файла. Корректно.
- **Наблюдение:** §5 File Manifest перечисляет изменения ci.mk как "(1) Добавить test-node target (2) Обновить фильтры (3) Добавить test-node в .PHONY". Три атомарных изменения в одном файле. Это корректно, но T2 (единый task на все три) может быть разделён при реализации для читаемости diff.

### INFO (3)

**[INFO] I1 — Doxyfile dirty в working tree**
- Не связано с планом 095. Не влияет на верификацию.

**[INFO] I2 — Brief использует `make test-e2e`, DevPlan — `make test-node`**
- DD2 обосновывает переименование: `test-e2e` конфликтует с существующим `MARKER=e2e`. `test-node` явно выражает зависимость от test-VPS.
- См. F2 для рекомендации по синхронизации Brief.

**[INFO] I3 — DevPlan §1.2 Items 1-8 структурированы как нумерованный список без подсекций**
- Не влияет на качество. §1.2.1-§1.2.8 имплицитно соответствуют пунктам списка.

---

## §6. Risk Re-Evaluation

Дополнительные риски, не покрытые §10 плана:

| Risk | Severity | Не покрыт в §10? | Mitigation |
|------|----------|:---:|------------|
| `tests/e2e/__init__.py` отсутствует → conftest не загружается → fixtures не найдены | HIGH | ❌ Нет | Исправить F1 |
| Brief расходится с DevPlan → confusion при аудите | MEDIUM | ❌ Нет | Исправить F2 |
| `-m "requires_node"` без `@pytest.mark.requires_node` на тестах → тесты не селектируются | MEDIUM | ❌ Частично | Исправить F4 |
| Coder не добавляет `requires_node` параметр → фикстура не вызывается | MEDIUM | ❌ Частично | Исправить F4 |
| Bootstrap >600s → timeout false negative | LOW | ❌ Нет | См. F5 |

---

## §7. Семантический вердикт

**PARTIAL — HIGH (2 findings requiring fix before implementation)**

### Сводка

| Категория | Находок |
|-----------|---------|
| CRITICAL | 0 |
| HIGH | 2 (F1, F2) |
| MEDIUM | 3 (F3, F4, F5) |
| LOW | 2 (F6, F7) |
| INFO | 3 (I1, I2, I3) |

### Рекомендации

**Перед реализацией (MUST FIX):**
1. **[F1]** Исправить File Manifest: заголовок "CREATE (10)", добавить `tests/e2e/__init__.py` в IMPACTS, явно назначить T4.
2. **[F2]** Синхронизировать Brief с DevPlan: добавить примечание об изменении маркера/таргета.

**Перед реализацией (SHOULD FIX):**
3. **[F4]** Добавить IMPLEMENTATION NOTE о необходимости `@pytest.mark.requires_node` + `requires_node` параметра на каждом тесте.
4. **[F3]** Прояснить 14 фаз vs 9 INIT фаз в §1.

**Опционально (NICE TO HAVE):**
5. **[F5]** Параметризовать bootstrap timeout через env var.
6. **[F7]** Рассмотреть разделение T2 на атомарные sub-таски.

### Go/No-Go Recommendation

**План может proceed к реализации после исправления F1 и F2.** Остальные находки — рекомендации, не блокирующие. Архитектурная целостность плана подтверждена: маркерная/фикстурная модель корректна, фильтры синтаксически верны, все 13 AC покрыты задачами, кросс-референсы с DP-091/089/092 верифицированы.

**Общая оценка качества плана:** 85/100
- −5: F1 (file count error + missing __init__.py assignment)
- −5: F2 (Brief-DevPlan divergence)
- −3: F3 (phase count ambiguity)
- −2: F4 (missing implementation note)

---

## §8. TRAP Proposals

Предлагаю создать следующие TRAP при реализации:

1. **`tests/e2e/conftest.py` → TRAP[DECISION]:** Session-scoped autouse `test_vps_fresh` vs function-scoped trade-off (дублирует TRAP из §4.1, закрепляет в коде).
2. **`tests/_conftest/node.py` → TRAP[BUG]:** `timeout=600` — задокументировать риск превышения на медленных VPS.

---

$END_VERIFICATION_REPORT
