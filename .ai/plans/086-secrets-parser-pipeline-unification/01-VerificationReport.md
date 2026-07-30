$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               QA audit DevPlan 086 — семантическая верификация на self-consistency, completeness и реализуемость
DESCRIPTION:           Проверка $ARTIFACT_CONTRACT полноты (7 полей), File Manifest consistency (CREATE/MODIFY/DELETE counts vs tasks), волнового порядка (зависимости), Acceptance Criteria измеримости, Rollback Plan реалистичности, Migration Gate полноты, edge case покрытия, а также существования всех заявленных файлов на диске.
RATIONALE:             DP-086 — крупный архитектурный DevPlan (29+ файлов, 26 задач, 4 волны). До передачи Coder'у необходимо убедиться, что план не содержит path drift, dependency inversion и underspecified scope — иначе исполнение приведёт к созданию файлов по неправильным путям или runtime failures.
ACCEPTANCE_CRITERIA:   Все 10 чеков проведены, каждый finding tagged severity BLOCKER/MAJOR/MINOR, даны конкретные строки и рекомендации. Итоговый вердикт: BROKEN (BLOCKER severity — 7 path drifts + dependency inversion).
IMPLEMENTS:            QA Phase 1-2 (static audit + cross-file drift detection) per QA workflow §BEHAVIOR
IMPACTS:               Создаёт 01-VerificationReport.md в папке плана. Не модифицирует codebase.
REQUIRES:              DevPlan.md (существующий), git rev-parse HEAD, filesystem access
$END_ARTIFACT_CONTRACT

---

# VerificationReport: DP-086 Secrets Parser & Pipeline Unification

🔒 **Verified against SHA:** `5a31ef2bafd10b6bbe59345d35625e3b1c108953`
⚠️  **Warning:** Working tree has uncommitted changes — 8 files dirty (unrelated to DP-086 scope):
```
core/modules/infra-metrics/docker-compose.base.yml
core/modules/infra-metrics/docker-compose.test.yml
core/modules/infra-metrics/healthcheck.sh
tests/_conftest/smoke.py
tests/test_char_deploy_parse_save.py
tests/test_hermes_init.py
tests/test_smoke_infra_metrics.py
.deploy-snapshots/deploy-result.json
```

**Verdict:** 🔴 **BROKEN** — 5 BLOCKER, 6 MAJOR, 5 MINOR

---

## §1. Findings Register

### BLOCKER (5) — Plan is not executable till resolved

#### [BLOCKER] F1 — Path drift: 7 MODIFY files at wrong paths
- **DevPlan File Manifest:** `core/internal/bootstrap/state_machine.py`
  - **Actual:** `core/internal/bootstrap/lifecycle/state_machine.py`
- **DevPlan File Manifest:** `core/internal/bootstrap/steps.py`
  - **Actual:** `core/internal/bootstrap/lifecycle/steps.py`
- **DevPlan File Manifest:** `core/internal/bootstrap/deploy/cert_orchestrator.py`
  - **Actual:** `core/internal/bootstrap/cert_orchestrator.py`
- **DevPlan File Manifest:** `core/entrypoints/node-lifecycle.sh`
  - **Actual:** `core/internal/bootstrap/node-lifecycle.sh`
- **DevPlan File Manifest:** `core/internal/bootstrap/checkpoint_migration.py`
  - **Actual:** `core/internal/checkpoint_migration.py`
- **DevPlan File Manifest:** `core/internal/bootstrap/sync_env_defaults.py`
  - **Actual:** `core/internal/scripts/sync_env_defaults.py`
- **DevPlan File Manifest:** `core/internal/bootstrap/generate_secrets_manifest.py`
  - **Actual:** `core/internal/scripts/generate_secrets_manifest.py`

**Impact:** Coder, следуя DevPlan буквально, создаст дубликаты файлов по неверным путям (или будет редактировать несуществующие файлы). Migration Gates G2.2, G2.3, G4.4 и Implementation Commands §7 (lines 424-426, 461) также ссылаются на неправильные пути.

**Affected lines:** File Manifest lines 198-205, Implementation Commands lines 424-426, Gate G2.2 line 367, Gate G2.3 line 368, Gate G4.4 line 389.

**Fix:** Заменить все 7 путей на фактические.

---

#### [BLOCKER] F2 — File Manifest CREATE undercount: declared 10, actual ≥12
- **T2** (line 119) создаёт `tests/unit/test_telegram_notifier.py` — файл **отсутствует** в CREATE (10) таблице §4
- **T3** (line 120) создаёт `tests/unit/test_docker_auth.py` — файл **отсутствует** в CREATE (10) таблице §4
- **T15** (line 156) создаёт 2 CREATE — один (`test_gate_no_inline_secrets_parsing.py`) в таблице, второй (gate test `test_secrets_env_parser`) — **отсутствует** в таблице

**Заявлено:** 10 CREATE (line 19, line 171)
**Фактически:** ≥12 (T1:2 + T2:2 + T3:2 + T10:2 + T12:2 + T15:2 + T23:1 + T24:1 = 14, из них 2 не задокументированы: T2 test + T3 test + T15 gate)

Расхождение: ≥20% — превышает критерий «File Manifest не совпадает с задачами на >20%».

**Affected lines:** §4 File Manifest (lines 171-183), $ARTIFACT_CONTRACT IMPACTS (line 19).

**Fix:** Добавить в CREATE таблицу: `tests/unit/test_telegram_notifier.py`, `tests/unit/test_docker_auth.py`, `tests/gates/test_gate_secrets_env_parser.py` (или уточнить имя файла для T15 gate test).

---

#### [BLOCKER] F3 — File Manifest MODIFY undercount: missing 7 files
Файлы, для которых T4/T14/T25 выполняют MODIFY, но которые **отсутствуют** в MODIFY (18) таблице:

| Task | Missing from MODIFY table | Actual path |
|------|--------------------------|-------------|
| T4 | `core/internal/shared/age_key.py` | EXISTS |
| T14 | `core/modules/backup-cron/scripts/disk-monitor.sh` | EXISTS |
| T14 | `core/internal/healthcheck/tor-proxy-healthcheck.sh` | EXISTS |
| T25 | `core/entrypoint-manifest.yaml` | EXISTS |
| T25 | `core/secret-definitions.yaml` | EXISTS |

Также T12 заявляет «2 CREATE, 1 MODIFY» (line 146), но из MODIFY таблицы видно только `generate-catalog.sh`. Второй MODIFY файл для T12 неидентифицирован — возможно это тестовый файл.

**Резюме:** MODIFY таблица заявляет 18 файлов, фактически задач с MODIFY операциями ≥23 (18 в таблице + 5 отсутствующих + ещё неучтённые из T12).

**Affected lines:** §4 File Manifest (lines 185-205).

**Fix:** Добавить пропущенные файлы в MODIFY таблицу, пересчитать итоговое число в IMPACTS.

---

#### [BLOCKER] F4 — 3 referenced files do NOT exist on disk
- **`deploy-notify.sh`** — referenced in problem table (line 52: «deploy-notify.sh») как один из 8 Telegram-нотификаторов. Glob `**/deploy-notify.sh` → **EMPTY.**
- **`healthcheck-notify.sh`** (или `healthcheck-notify.py`) — referenced в той же строке 52. Glob `**/healthcheck-notify*` → **EMPTY.**
- **`setup-mirror.sh`** — referenced в AC6 (line 13: «setup-mirror.sh») как одна из 6 точек Docker auth. Glob `**/setup*mirror*` → **EMPTY.**

Задачи T13 (Docker auth, line 154) и T14 (Telegram, line 155) не упоминают эти файлы в своих списках, но problem statement §1 утверждает необходимость их миграции. Если эти файлы существовали ранее и были удалены — problem statement должен быть скорректирован. Если они должны быть созданы — они должны быть в CREATE таблице.

**Affected lines:** Problem table (lines 52-53), AC6 (line 13).

**Fix:** Верифицировать, существовали ли эти файлы и были ли удалены. Если удалены — убрать из problem statement. Если должны быть созданы — добавить в CREATE с соответствующими задачами.

---

#### [BLOCKER] F5 — Dependency inversion: T9 (Wave 2) требует T10 (Wave 3)
- **T9** (Wave 2, line 133): «lib/secrets.sh: step_10_decrypt_secrets() — заменить inline bash-парсинг на `python3 decrypt_secrets.py`»
- **T10** (Wave 3, line 144): создаёт `core/internal/secrets/decrypt_secrets.py`

T9 модифицирует shell-код для вызова Python-модуля, который будет создан только в Wave 3. После завершения Wave 2, но до Wave 3, `lib/secrets.sh` будет ссылаться на несуществующий `decrypt_secrets.py`.

**Все тесты и интеграционные проверки, запущенные между Wave 2 и Wave 3, упадут при попытке выполнить `step_10_decrypt_secrets()`.**

**Affected lines:** Wave 2 T9 (line 133), Wave 3 T10 (line 144).

**Fix:** Варианты:
- A) Переместить T10 (создание decrypt_secrets.py) в Wave 1 или Wave 2
- B) Переместить T9 в Wave 3 (после T10)
- C) Разделить T9: в Wave 2 только подготовить shell к вызову Python (закомментированный код / feature flag), в Wave 3 активировать вызов

---

### MAJOR (6) — Architectural/consistency issues

#### [MAJOR] F6 — $ARTIFACT_CONTRACT ACCEPTANCE_CRITERIA numbering mismatch
- **Contract (lines 7-17):** AC1-AC10 (10 критериев)
- **§5 Detailed (lines 218-230):** AC1-AC13 (13 критериев)

Соответствие нарушено:
| Contract | §5 Detailed | Описание |
|----------|------------|----------|
| AC8 | AC8 + AC9 | Contract AC8 разделён на 2 критерия в §5 |
| AC9 | AC10 | Gate green |
| AC10 | AC11 | Pytest pass |
| — | AC12 | Performance benchmark (отсутствует в contract) |
| — | AC13 | Integration test (отсутствует в contract) |

**Affected lines:** $ARTIFACT_CONTRACT (lines 7-17), §5 (lines 218-230).

**Fix:** Привести $ARTIFACT_CONTRACT к 13 критериям или явно указать «AC1-AC10 summary, AC11-AC13 detailed only».

---

#### [MAJOR] F7 — T1 «prefix_filter» test case has no API counterpart
- **T1** (line 118): перечисляет 12 test cases, включая `prefix_filter`
- **§2 Draft Code Graph (lines 62-68):** `secrets_env_parser.parse(path) → dict[str, str]` — нет параметра `prefix_filter`

API не документирует функциональность фильтрации по префиксу. Либо `prefix_filter` должен быть добавлен в сигнатуру `parse()`, либо это отдельная функция. Coder не будет знать, что тестировать.

**Affected lines:** T1 (line 118), §2 Draft Code Graph (lines 62-68).

**Fix:** Добавить `prefix_filter` параметр в `parse()` или документировать как отдельную функцию в Code Graph.

---

#### [MAJOR] F8 — T11 «3 MODIFY» underspecified
- **T11** (line 145): «1 DELETE, 3 MODIFY | 1»
  - DELETE: `secrets-init.sh` ✓
  - MODIFY: `state_machine.py._step_secrets_init()` и `steps.py._step_secrets_init()` — это 2 MODIFY
  - 3-й MODIFY: **не указан**

Coder не будет знать, какой третий файл модифицировать. Возможно, это `secrets_manager.py` (связанная логика), но это уже T5. Или это может быть файл, регистрирующий шаги в pipeline.

**Affected lines:** T11 (line 145).

**Fix:** Указать третий модифицируемый файл явно или скорректировать count на «2 MODIFY».

---

#### [MAJOR] F9 — T12 «2 CREATE» underspecified
- **T12** (line 146): «2 CREATE, 1 MODIFY | 2»
  - CREATE #1: `generate_catalog.py` ✓ (в таблице)
  - CREATE #2: **не указан** (тестовый файл? вспомогательный модуль?)
  - MODIFY: `generate-catalog.sh` ✓ (в таблице)

**Affected lines:** T12 (line 146).

**Fix:** Указать второй CREATE файл. Если это `tests/unit/test_generate_catalog.py` — добавить в CREATE таблицу.

---

#### [MAJOR] F10 — Gate G1.5 verification tool does not exist
- **G1.5** (line 359): «AGE-key формат консистентен во всех 3 точках | `make check-age-key-format` (есть или создать)»

Make-таргет `check-age-key-format` не существует в проекте. Формулировка «(есть или создать)» перекладывает решение на Coder'а без спецификации. T4 (line 121) документирует формат в docstring, но не создаёт проверочный инструмент.

**Affected lines:** Gate G1.5 (line 359).

**Fix:** Либо добавить задачу на создание `make check-age-key-format`, либо заменить критерий G1.5 на grep-based проверку (как сделано для остальных gate'ов).

---

#### [MAJOR] F11 — Rollback Plan Wave 1 inaccurate statement
- **§Rollback Plan Wave 1** (line 301): «Восстанавливает: 5 старых парсеров в consumers, inline shell telegram, inline docker auth»

Wave 1 **только создаёт** shared модули (T1-T4), не модифицирует consumers. Утверждение о восстановлении «5 старых парсеров» неверно — в Wave 1 consumers ещё не мигрированы, откатывать нечего. Корректное описание: «Удаляет 4 новых shared модуля (CREATE-only)».

**Affected lines:** Rollback Plan Wave 1 (lines 299-303).

**Fix:** Исправить описание отката Wave 1 на соответствующее действительности.

---

### MINOR (5) — Improvements, not blockers

#### [MINOR] F12 — Non-sequential task numbering
Задачи пронумерованы нелинейно: T1-T9 (W1-W2), T17-T18 (W2 cont.), T10-T12 (W3), T13-T16 (W4), T19-T26 (W4 cont.). Это указывает на реструктуризацию плана после первоначальной нумерации. Coder, читающий задачи последовательно, может запутаться.

**Fix:** Пере-пронумеровать задачи T1-T26 последовательно (T1-T4 W1, T5-T11 W2, T12-T14 W3, T15-T26 W4).

---

#### [MINOR] F13 — $ARTIFACT_CONTRACT IMPACTS count inaccurate
Line 19: «29+ файлов (10 CREATE, 18 MODIFY, 1 DELETE + 2 функции)»

С учётом F2 (missing CREATE files) и F3 (missing MODIFY files), актуальные числа:
- CREATE: ≥12 (было 10)
- MODIFY: ≥23 (было 18)
- DELETE: 1 файл + 2 функции
- Итого: ≥36 (было «29+»)

**Fix:** Обновить IMPACTS после исправления File Manifest.

---

#### [MINOR] F14 — AC6 Docker auth список включает несуществующий файл
Line 13: «6 точек (lib/docker.sh, docker_registry_auth.py, state_machine.py._ghcr_auth, steps.py._ghcr_docker_login, deploy-context.sh, **setup-mirror.sh**)»

`setup-mirror.sh` не существует на диске. **deploy-context.sh** существует (`core/entrypoints/deploy-context.sh`) — но путь в MODIFY таблице не указан. Это divergence между problem statement и реальностью.

**Affected lines:** AC6 (line 13).

**Fix:** Убрать setup-mirror.sh или найти его реальный путь; добавить deploy-context.sh в MODIFY таблицу.

---

#### [MINOR] F15 — Problem table Telegram-нотификаторы: 2 несуществующих файла
Line 52: перечисляет 8 Telegram-нотификаторов, включая `deploy-notify.sh` и `healthcheck-notify.sh`

Оба файла не найдены на диске. Связано с F4.

**Affected lines:** Problem table (line 52).

**Fix:** Верифицировать список; убрать несуществующие или добавить в CREATE с задачами.

---

#### [MINOR] F16 — Wave 1 acceptance говорит о 15 unit-тестах, но T4 не создаёт тест
Line 123: «Wave 1 acceptance: 4 новых Python-модуля, 15 unit-тестов (12 для парсера + 1 telegram + 1 docker + 1 AGE-key)»

T4 (AGE-key standardization, line 121) — это «1 MODIFY» (документирование docstring), **не создаёт тест.** Упомянутый «1 AGE-key» тест не привязан ни к какой задаче Wave 1. T4 заявляет effort 1 и «1 MODIFY» — unit-тест требует CREATE файла, который не указан.

**Affected lines:** Wave 1 acceptance (line 123).

**Fix:** Либо добавить создание теста в T4 (и в CREATE таблицу), либо скорректировать acceptance на «14 unit-тестов (12 parser + 1 telegram + 1 docker)».

---

## §2. Compliance Matrix

| Check | Status | Key Findings |
|-------|--------|-------------|
| 1. $ARTIFACT_CONTRACT 7 fields | ⚠️ MAJOR | Все 7 полей присутствуют, но ACCEPTANCE_CRITERIA содержит 10 вместо 13 (F6) |
| 2. Self-consistency counts | 🔴 BLOCKER | CREATE 10→≥12 (F2), MODIFY 18→≥23 (F3), 7 path drifts (F1) |
| 3. Tasks T1-T26 completeness | ⚠️ MAJOR | Все задачи имеют description/file/effort, но T11 3-й MODIFY и T12 2-й CREATE не специфицированы (F8, F9) |
| 4. Wave ordering | 🔴 BLOCKER | T9 (W2) зависит от T10 (W3) — dependency inversion (F5) |
| 5. AC measurability | ⚠️ MAJOR | Все 13 AC измеримы, но numbering mismatch между contract и §5 (F6) |
| 6. Rollback Plan | ⚠️ MAJOR | Присутствует, но Wave 1 описание неточное (F11) |
| 7. Migration Gate | ⚠️ MAJOR | 4 gate'а с критериями; G1.5 ссылается на несуществующий make target (F10); G2.2/G2.3/G4.4 используют неправильные пути (F1) |
| 8. T1 edge cases | ⚠️ MAJOR | 12 cases валидны, но prefix_filter не имеет API (F7) |
| 9. File existence | 🔴 BLOCKER | 7 MODIFY путей неверны (F1), 3 файла не существуют (F4) |
| 10. $END_DEVPLAN | ✅ PASS | Присутствует на line 465 |

---

## §3. Positive Findings

1. **§Rollback Plan** реалистичен: 3 уровня отката (модуль/волна/весь DP), pre-rollback freeze через git tag, post-rollback верификация.
2. **§Migration Gate** структурирован: 4 gate'а (G1-G4) с измеримыми критериями и конкретными bash-командами для проверки.
3. **Line references точны:** `cert_orchestrator.py` L719-764 и `node-lifecycle.sh` L84 подтверждены на фактических файлах.
4. **Design Decisions (DD1-DD6)** аргументированы и объясняют архитектурные компромиссы.
5. **Draft Code Graph (§2)** детально описывает API всех новых модулей.
6. **Все 26 задач** имеют description, файлы и effort — нет «пустых» задач.

---

## §4. Recommendations

### Must-fix before Coder handoff (BLOCKER):
1. **F1:** Исправить 7 путей в File Manifest + Implementation Commands + Migration Gates
2. **F2:** Добавить недостающие CREATE файлы в таблицу + пересчитать count
3. **F3:** Добавить недостающие MODIFY файлы в таблицу + пересчитать count
4. **F4:** Верифицировать/убрать/добавить `deploy-notify.sh`, `healthcheck-notify.sh`, `setup-mirror.sh`
5. **F5:** Разрешить dependency inversion T9↔T10 (переместить задачу или модуль)

### Should-fix before Coder handoff (MAJOR):
6. **F6:** Синхронизировать ACCEPTANCE_CRITERIA между contract (10) и §5 (13)
7. **F7:** Добавить `prefix_filter` в API code graph или убрать из T1 test cases
8. **F8:** Указать 3-й MODIFY файл в T11 или скорректировать count
9. **F9:** Указать 2-й CREATE файл в T12
10. **F10:** Создать `make check-age-key-format` или заменить критерий G1.5
11. **F11:** Исправить описание Wave 1 Rollback

### Nice-to-fix (MINOR):
12. **F12:** Пере-пронумеровать задачи последовательно
13. **F13:** Обновить IMPACTS count
14. **F14-F15:** Почистить references на несуществующие файлы
15. **F16:** Согласовать Wave 1 acceptance (15 vs 14 unit-тестов)

---

**Делегирование:** Рекомендуется делегировать исправление BLOCKER-ов (F1-F5) Architect'у для обновления DevPlan.md. После исправления — повторная QA верификация.

**Рекомендуемый порядок исправления:** F1 (paths) → F5 (dependency) → F2-F3 (counts) → F4 (missing files) → MAJOR → MINOR.

$END_VERIFICATION_REPORT
