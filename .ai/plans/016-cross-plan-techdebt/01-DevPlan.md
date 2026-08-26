$START_DEVPLAN

# DevPlan 016 — Cross-Plan Techdebt Consolidation (010/011/012/013/016/017 residuals)

<!-- GREP_SUMMARY: DevPlan 016 cross-plan techdebt consolidation partial-tasks postgres16 sync_env_defaults AGENTS-inventory json_writer-ARG001 provides-networks restore-clean loadtest-promql worktree-parity debt-archive -->
<!-- STRUCTURE: ▶ аудит 6 девпланов (вердикты) → ⊕ debt-регистр (10 задач + 2 регистра) → ⚡ Draft Code Graph → ◇ Data Flow → ⎋ $TASKS/$PARALLEL_GROUPS → ⚡ AC/Manifest/Next Steps -->

## $ARTIFACT_CONTRACT
```yaml
PURPOSE: Закрыть остаточный техдолг, накопленный за 2026-08-25..26 в девпланах 010/011/012/013/
         016/017: недоделанные хвосты (PARTIAL-задачи плана 17, DR-M4 плана 010, D-013b плана 013,
         F-032/F-036 плана 011) + архив уже-закрытого долга.
DESCRIPTION: Аудит 6 девпланов показал: 010 — 14/15 (DR-M4 недоделан), 016 — 13/13 (чисто),
         017 — 8 волн с 7 PARTIAL-хвостами, 012 — 19/19 (чисто), 013 — 3/3 код (Wave 3 runtime
         = операторское окно) + D-013a/b, 011 — READY_WITH_FIXES с открытыми F-032/F-036.
         Этот план собирает ВСЕ недоделанные пункты в 10 атомарных задач (3 волны) + 2 регистра
         (deferred-бэклог, operator-only). Параллельная сессия 014 уже закрыла свои F-06..F-11
         отдельным планом 015-post-launch-fixes — пересечения исключены (см. Debt Intake).
RATIONALE: Каждый из 6 девпланов оставил недоделанный хвост: кто-то PARTIAL (план 17 — 7 задач
         не доведены до literal-AC), кто-то заявил полноту, но пропустил пункт (010 DR-M4 в
         заголовке коммита, но не в теле), кто-то зафиксировал долг Debt.md'ом (013), а
         launch-validation оставила P1-хвосты на решение владельца (F-032/F-036). Без единого
         плана эти хвосты распылены по 6 папкам и теряются между сессиями.
ACCEPTANCE_CRITERIA:
  - AC1: 10 задач закрыты; `make check` rc=0; `make agent-check` exit 0; pre-commit green.
  - AC2: literal-AC плана 17 (T2.1/T3.2/T4.2/T6.2/T6.3/T7.3/T8.2) проверяемы grep-ом — каждый
         хвост даёт пустой grep по своему паттерну (см. $TASKS).
  - AC3: DR-M4 — provides.networks в platform-infra.yaml приведён к фактическому multi-network
         attach; parity-гейт test_gate_provides_networks_parity.py зелёный.
  - AC4: F-032/F-036 — зафиксирована owner-стратегия (--clean dump / saturation-pull), код
         реализует выбранный путь; unit-тест покрывает сценарий.
  - AC5: D-013a помечен TRAP[ARCHIVED]; D-013b получил policy-документ «worktree parity».
IMPLEMENTS: 02-VerificationReport плана 011 (F-032/F-036); 02-Debt плана 013 (D-013a/b);
          §Debt Intake планов 16 (BP-1..BP-12) и 17 (AI-0057); PARTIAL-таблицы аудита 017.
IMPACTS: core/modules/postgres/init/01-create-databases.sql, core/internal/scripts/sync_env_defaults.py,
          core/internal/shared/AGENTS.md, core/internal/healthcheck/metrics/json_writer.py,
          core/internal/scaffold/scaffold_helpers.py, tests/unit/test_deploy_orchestrator.py,
          core/platform-infra.yaml, core/modules/postgres/Makefile, core/internal/loadtest/*,
          tests/e2e/README.md (или core/internal/shared/AGENTS.md), .ai/plans/013-*/02-Debt.md,
          + тестовые фикстуры (test_orphan_reconciler*, test_security_posture, test_status_page).
REQUIRES: решение владельца по F-032 (--clean dump) и F-036 (sshd-порт vs node-side pull) для Wave 2;
          операторские гейты (D5/G5/G2) вне кода — регистрируются, не реализуются.
```

## 1. Requirements Analysis — аудит 6 девпланов (2026-08-25..26)

Проверка «все ли пункты выполнены» — через 5 параллельных субагентов (git log -S + чтение $TASKS):

| Девплан | Задач | Вердикт | Недоделанное |
|---------|-------|---------|--------------|
| 010 multi-node audit-fixes (DR-C1..DR-L6) | 15 | **14/15** | DR-M4 (provides.networks SoT) — нет в теле коммита 42679a0; функционально смягчён 404cad0 |
| 016 post-audit (P0-1..P0-7 + P1-хвост) | 13 | **13/13** | чисто; BP-1..BP-12 → DEFER-бэклог (§Backlog) |
| 017 ai-code-fixes (8 волн) | ~50 | **7 PARTIAL** | T2.1, T3.2, T4.2, T6.2, T6.3, T7.3, T8.2 (см. §2) |
| 012 fast-bootstrap-deploy (T1-T19) | 19 | **19/19** | чисто; AC1/AC5/AC6 — ручные runtime-проверки (release-checklist) |
| 013 resilience-drills-rework | 3 код | **3/3 код** | Wave 3 (runtime на ноде) — операторское окно; D-013a (уже закрыт на HEAD — надо архивировать), D-013b (открыт) |
| 011 launch-validation (F-001..F-037) | — | **READY_WITH_FIXES** | F-032 (P1), F-036 (P1), F-037-residual (ручной drill), операторские D5/G5/G2 |

**Ключевые критерии успеха:**
1. **План 17 оставил 7 literal-хвостов** — задачи, где AC требовал «grep пуст», но остались
   единичные literals/строки. Это самый дешёвый сигнал (каждая — 1 файл, ≤5 строк), но каждая —
   нарушение заявленного AC. Закрыть их = довести план 17 до фактической полноты.
2. **DR-M4** — единственный пункт, заявленный в заголовке коммита (`DR-C1..DR-L6`), но отсутствующий
   в теле: `provides.networks` в platform-infra.yaml остаётся подмножеством фактического multi-network
   attach (minio/clickhouse/langfuse). Parity-гейт уже существует — нужна системная выверка SoT.
3. **F-032/F-036** — два P1 из launch-validation, которые упираются в РЕШЕНИЕ ВЛАДЕЛЬЦА по стратегии,
   а не в чистый код. План фиксирует опции и деградирует до owner-gate, не блокируя Wave 1/3.

## 2. Debt Register (собранный техдолг за 2 дня)

### A. PARTIAL-хвосты плана 17 (код, LO/MED) — Wave 1
| ID | Sev | Хвост | Файл:строка | literal-AC проверка |
|----|-----|-------|-------------|---------------------|
| T2.1 | MED | `postgres:16` в init-SQL + 9 тест-фикстурах (канон 18.4) | init/01-create-databases.sql:9; test_orphan_reconciler.py:138; test_orphan_reconciler_selfheal.py:427; test_security_posture.py:592/597/642/647/655/698/703; test_status_page.py:140/243 | `grep -rn "postgres:16" core/ tests/` → пусто |
| T3.2 | LO | Стейл TRAP[DOCKER-BIND-MOUNT] противоречит новому os.replace (T3.2) | healthcheck/metrics/json_writer.py:47-50 | TRAP заархивирован/удалён, нет «Rejected: atomic rename» рядом с os.replace |
| T4.2 | LO | fallback `"600"` вместо канона 900 | scripts/sync_env_defaults.py:805 | `grep -n '"600"' scripts/sync_env_defaults.py` → пусто |
| T6.2 | LO | inventory строка `requires_compose_project()` (функция удалена) | shared/AGENTS.md:29 | `grep "requires_compose_project" shared/AGENTS.md` → пусто |
| T6.3 | LO | inventory строка `get_canonical_paths()` (удалён) | shared/AGENTS.md:33 | `grep "get_canonical_paths" shared/AGENTS.md` → пусто |
| T7.3 | LO | `# ruff: ignore[ARG001]` остался на `name` | scaffold/scaffold_helpers.py:479 | `grep -n "ARG001" scaffold_helpers.py` → пусто |
| T8.2 | LO | тест пинит приватные `_deploy_sequential`/`_deploy_parallel` | tests/unit/test_deploy_orchestrator.py:209 | assert на публичные observable, патчи приватных заменены |

### B. Пункты, заявленные но недоделанные (MED/P1) — Wave 2
| ID | Sev | Пункт | Девплан | Блокер |
|----|-----|-------|---------|--------|
| DR-M4 | MED | provides.networks системная выверка ↔ compose-attach | 010 | нет (parity-гейт есть) |
| F-032 | P1 | pg_dumpall restore конфликт с init-инициализацией (role/db/type exists) | 011/012 | owner-стратегия: --clean dump vs empty-volume |
| F-036 | P1 | load-test PromQL saturation-pull блокирован AllowTcpForwarding=no | 011 | owner: node-side pull vs sshd-исключение |

### C. Debt-реестр плана 013 (policy/doc) — Wave 3
| ID | Sev | Состояние на HEAD 6fff904 |
|----|-----|---------------------------|
| D-013a | MED | **УЖЕ ЗАКРЫТ** — grep `\ref` и `/Users/tronyx/projects/ai-platform` пуст; relative markdown-links. Требуется только ARCHIVE в 02-Debt.md. |
| D-013b | LO | **ОТКРЫТ** — 6 тестов падают в чистом worktree (untracked `.env`, `core/modules/hermes-agent/.env`); policy «worktree parity» не создан. |

### D. Deferred-бэклог (регистр, не задачи)
- **AI-0057** (план 17) — subprocess_io adoption gap ~99 raw `subprocess.run` файлов; отдельный triage-проход.
- **BP-1..BP-12** (план 16 §Backlog) — P2-хвост/coverage-gaps/010-claims; DEFER с return-условиями.
- **DEPLOY_PARALLEL default=true** (план 12) — отдельный пост-012 план по решению владельца.

### E. Operator-only (регистр — вне кода)
D5 (GitHub Billing org TronyxLab) · G5/H1 (test-VPS недоступна) · G2 (chaos-night окно) ·
core-deploy CI secret `AGE_SECRET_KEY` (step_10_decrypt_secrets, repo Tronyx161/AI-platform) ·
F-04 (SSH host key сменился — подтвердить SC2) · F-037-residual (ручной reboot-drill + снять drop-in).

## 3. Architecture Overview — Draft Code Graph

```
Wave 1 — PARTIAL-хвосты плана 17 (независимые файлы, параллельно):
├── core/modules/postgres/init/01-create-databases.sql  ← T1: postgres:16 → 18.4
├── tests/unit/{test_orphan_reconciler,test_orphan_reconciler_selfheal,test_security_posture,test_status_page}.py ← T1: фикстуры 16→18.4
├── core/internal/scripts/sync_env_defaults.py           ← T2: fallback "600"→"900"
├── core/internal/shared/AGENTS.md                       ← T3: снять 2 стейл-строки inventory
├── core/internal/healthcheck/metrics/json_writer.py     ← T4: TRAP[DOCKER-BIND-MOUNT]→ARCHIVED
├── core/internal/scaffold/scaffold_helpers.py           ← T5: снять ruff:ignore[ARG001]
└── tests/unit/test_deploy_orchestrator.py               ← T6: публичные observable вместо приватных патчей

Wave 2 — MED/P1 (анализ/owner-гейт):
├── core/platform-infra.yaml                             ← T7: provides.networks ↔ фактический attach
├── core/modules/postgres/Makefile (+backup config)      ← T8: restore --clean / пустой volume
└── core/internal/loadtest/runner_cli.py (+ssh path)     ← T9: saturation-pull / sshd-исключение

Wave 3 — policy/doc:
├── tests/e2e/README.md (или shared/AGENTS.md)           ← T10a: policy «worktree parity»
└── .ai/plans/013-resilience-drills-rework/02-Debt.md    ← T10b: D-013a → TRAP[ARCHIVED]
```

**Инвариант параллелизации:** файлы волн не пересекаются (Wave 1 — 7 файловых доменов, Wave 2 —
3 домена, Wave 3 — 2 документа); каждая волна внутренне параллельна.

## 4. Data Flow

### Wave 1 (literal-гигиена)
```
▶ grep <pattern> по core/+tests/ → ⚡ найти оставшийся literal → edit (1 файл, ≤5 строк)
→ ◇ per-task make check TEST_FILE=... → ⊕ финальный make check (батч) → ⎋ 7 хвостов закрыты
```

### Wave 2 T7 (DR-M4)
```
▶ собрать фактический multi-network attach из docker-compose.base.yml всех модулей
→ ⚡ сравнить с provides.networks в platform-infra.yaml → ◇ дополнить/свести к SoT
→ ⊕ parity-гейт test_gate_provides_networks_parity.py → ⎋ subset-инвариант честен
```

### Wave 2 T8/T9 (owner-гейт → код)
```
▶ question владельцу (стратегия) → ⚡ по выбранному пути → edit + unit-тест → ⎋ AC4
```

## 5. $TASKS

| ID | Артефакт | Владелец | Зависимости | Complexity |
|----|----------|----------|-------------|------------|
| TASK-1 | postgres:16 → 18.4 (init-SQL + 9 фикстур) | Coder | — | 2 |
| TASK-2 | sync_env_defaults fallback 600→900 | Coder | — | 1 |
| TASK-3 | shared/AGENTS.md снять 2 стейл-строки | Coder | — | 1 |
| TASK-4 | json_writer TRAP[DOCKER-BIND-MOUNT]→ARCHIVED | Coder | — | 1 |
| TASK-5 | scaffold_helpers снять ruff:ignore[ARG001] | Coder | — | 2 |
| TASK-6 | test_deploy_orchestrator публичные observable | Coder | — | 3 |
| TASK-7 | DR-M4 provides.networks SoT-выверка | Coder | — | 4 |
| TASK-8 | F-032 restore init-conflict (owner-стратегия) | Coder | owner-gate | 4 |
| TASK-9 | F-036 load-test saturation-pull (owner-стратегия) | Coder | owner-gate | 4 |
| TASK-10 | 013-debt: D-013a ARCHIVE + D-013b policy | Coder | — | 2 |

Critical path: Wave 1 (TASK-1..6) → Wave 2 (TASK-7..9, где TASK-8/9 ждут owner-gate) → Wave 3.
TASK-7 независим от owner-гейта и может идти параллельно Wave 1 (выделен в Wave 2 по меди-анализу).

**TASK-1 — postgres:16 → 18.4 (T2.1 literal-хвост)**
- `core/modules/postgres/init/01-create-databases.sql:9` — комментарий `Created by postgres:16 entrypoint`.
- 9 тест-фикстур: `tests/unit/test_orphan_reconciler.py:138`, `test_orphan_reconciler_selfheal.py:427`,
  `test_security_posture.py:592/597/642/647/655/698/703`, `test_status_page.py:140/243`.
- Acceptance: `grep -rn "postgres:16" core/ tests/` → пусто (кроме легитимного архив-комментария);
  `make check TEST_FILE=tests/unit/test_status_page.py` green (первый затронутый файл, затем батч).

**TASK-2 — sync_env_defaults fallback (T4.2)**
- `core/internal/scripts/sync_env_defaults.py:805` — `_get_env_val(..., "600")` → `"900"` (канон
  `DEPLOY_TIMEOUT=900`, SoT `shared/timeouts.py:151`).
- Acceptance: `grep -n '"600"' core/internal/scripts/sync_env_defaults.py` → пусто;
  `make check TEST_FILE=tests/unit/test_sync_env_defaults.py` green.

**TASK-3 — shared/AGENTS.md inventory (T6.2+T6.3)**
- Строки 29 (`requires_compose_project()`) и 33 (`get_canonical_paths()`) — функции удалены планом 17
  (T6.2/T6.3), inventory не синхронизирован.
- Acceptance: `grep "requires_compose_project\|get_canonical_paths" core/internal/shared/AGENTS.md` → пусто.

**TASK-4 — json_writer стейл-TRAP (T3.2)**
- `core/internal/healthcheck/metrics/json_writer.py:47-50` — TRAP[DOCKER-BIND-MOUNT] утверждает
  «Rejected: atomic rename via os.replace», но T3.2 перевёл код на os.replace (строки 121-122,
  TRAP[DECISION] AI-0009). Стейл-TRAP заархивировать (TRAP[ARCHIVED] с датой) или удалить.
- Acceptance: `grep -n "DOCKER-BIND-MOUNT" core/internal/healthcheck/metrics/json_writer.py` → только
  архив-строка (или пусто); противоречие с os.replace устранено.

**TASK-5 — scaffold_helpers ARG001 (T7.3)**
- `core/internal/scaffold/scaffold_helpers.py:479` — `name: str,  # ruff: ignore[ARG001]`. Снять
  ignore и поправить тело (переименовать в `_name` или удалить параметр, если keyword-контракт
  допускает). Проверить ВСЕ callers `gen_project_platform_md(...)`.
- Acceptance: `grep -n "ARG001" core/internal/scaffold/scaffold_helpers.py` → пусто;
  `ruff check core/internal/scaffold/scaffold_helpers.py` green; callers не сломаны.

**TASK-6 — test_deploy_orchestrator публичные observable (T8.2)**
- `tests/unit/test_deploy_orchestrator.py:209` — `patch.object(orch, "_deploy_sequential"/"_deploy_parallel")`
  + `assert_called_once_with(["postgres","redis"],{},…)` пинит приватную декомпозицию. Заменить на
  публичные observable (deployed/failed/modules_info — уже добавлены T8.2), патчи приватных снять.
- Acceptance: тест проходит без патчей приватных методов; observable-ассерты (deployed/failed) остаются.

**TASK-7 — DR-M4 provides.networks (010)**
- Собрать фактический multi-network attach из `docker-compose.base.yml` модулей (minio/clickhouse/
  langfuse — hermes-agent-net + shared-*). Выверить `core/platform-infra.yaml` §provides.networks.
- Acceptance: `test_gate_provides_networks_parity.py` (4 теста) green; provides.networks отражает
  фактический subset; системная выверка задокументирована (не точечный патч).

**TASK-8 — F-032 restore init-conflict (011/012)**
- `core/modules/postgres/Makefile:139-147` фиксирует конфликт (role/db/type exists при restore поверх
  init-кластера). Owner-гейт: стратегия (a) `--clean` dumps в backup-канале, (b) пустой postgres-data
  volume для restore. Реализовать выбранный путь + unit-тест.
- Acceptance: `make check TEST_FILE=tests/unit/test_postgres_restore.py` green; restore сценарий
  покрывает чистый restore без ручного вмешательства.

**TASK-9 — F-036 load-test PromQL (011)**
- `core/internal/loadtest/runner_cli.py` — post-run PromQL pull с локальной машины на :9090 блокирован
  `AllowTcpForwarding=no`. Owner-гейт: (a) node-side saturation-pull через ssh_read, (b) sshd-исключение
  на конкретный порт. Реализовать + unit-тест.
- Acceptance: `make check TEST_FILE=tests/unit/test_loadtest_runner.py` green; PromQL-pull путь не
  требует TCP-forwarding.

**TASK-10 — 013-debt register (D-013a ARCHIVE + D-013b policy)**
- D-013a: пометить `TRAP[ARCHIVED]` в `.ai/plans/013-resilience-drills-rework/02-Debt.md` (grep `\ref`/
  absolute-path уже пуст на HEAD — долг закрыт).
- D-013b: создать policy «worktree parity» (минимальный набор symlink'ов: .venv, node-configs, .env,
  core/modules/hermes-agent/.env) в `tests/e2e/README.md` ИЛИ `core/internal/shared/AGENTS.md`;
  альтернатива — R4-стиль skip→FAIL с сообщением «отсутствует <артефакт> — операторская среда».
- Acceptance: D-013a заархивирован; policy-документ существует и ссылается на 6 падающих тестов.

## 6. $PARALLEL_GROUPS

### Wave 1 (независимые literal-хвосты, 7 файловых доменов без пересечений)
- Tasks: TASK-1, TASK-2, TASK-3, TASK-4, TASK-5, TASK-6
- Command: `coder Read .ai/plans/016-cross-plan-techdebt/01-DevPlan.md, implement Wave 1: TASK-1..TASK-6`

### Wave 2 (MED/P1, анализ; TASK-8/9 ждут owner-gate)
- Tasks: TASK-7 (без гейта), TASK-8 (owner), TASK-9 (owner)
- Command: `coder Read .ai/plans/016-cross-plan-techdebt/01-DevPlan.md, implement Wave 2: TASK-7 (и TASK-8/TASK-9 после решения владельца)`

### Wave 3 (policy/doc)
- Tasks: TASK-10
- Command: `coder Read .ai/plans/016-cross-plan-techdebt/01-DevPlan.md, implement Wave 3: TASK-10`

## 7. Design Decisions

## @rationale Q: Почему Wave 1 — «дожать literal-AC плана 17», а не новый скоуп? A: Каждый из 7
хвостов — заявленный AC плана 17, который остался частично выполненным (grep не пуст). Это дешёвый,
измеримый сигнал (каждый — 1 файл ≤5 строк), и его закрытие доводит план 17 до фактической полноты,
что и запрошено («все ли пункты выполнены»). Новый скоуп не нужен — только доводка.

## @rationale Q: Почему DR-M4 вынесен в Wave 2, а не Wave 1? A: DR-M4 требует системной выверки
(сбор фактического multi-network attach из всех docker-compose.base.yml и сверка с provides.networks),
а не точечного патча. Parity-гейт уже существует — задача в честности SoT, что требует меди-анализа
(сравнение subset-инварианта для minio/clickhouse/langfuse), не быстрой правки.

## @rationale Q: Почему F-032/F-036 — owner-gate, а не авто-выбор? A: Оба P1 упираются в бизнес-решение
владельца (стратегия backup dump'ов и исключение в sshd-политике — security-sensitive). Поиск работы
«разумный дефолт» здесь неприемлем: sshd-исключение расширяет атаку-поверхность, а --clean dump меняет
семантику restore. План фиксирует опции и деградирует до явного решения, не блокируя Wave 1/3.

## @rationale Q: Почему D-013a только ARCHIVE, а не фикс? A: Аудит на HEAD 6fff904 показал, что `\ref` с
абсолютным путём уже устранён (grep пуст; relative markdown-links). Долг закрыт конвергенцией 013+main
(b889551), осталась только запись в реестре. Архивация предотвращает повторную трату времени агента на
уже-решённый дефект.

## 8. Debt Intake (пересечения и решения)

| Источник | Классификация | Решение |
|----------|---------------|---------|
| F-02 pyright full-repo timeout | IN_SCOPE плана 015 (сессия 014) | НЕ дублировать — уже TASK-4 в 015-post-launch-fixes |
| F-06..F-11 (сессия 014) | IN_SCOPE плана 015 | НЕ дублировать — покрыты 015 (коммит fde3fe8 + TASK-1..6) |
| AI-0057 subprocess_io (~99 файлов) | DEFER | Отдельный triage-проход; return-условие: после Wave 1 |
| BP-1..BP-12 (план 16) | DEFER | Уже в §Backlog плана 16 с return-условиями |
| DEPLOY_PARALLEL default=true (план 12) | DEFER | Отдельный пост-012 план по решению владельца |
| D5/G5/G2/core-deploy-secret/F-04/F-037-residual | BLOCKED (владелец) | Регистр §2E; вне кода |
| G2 chaos-night (план 13 Wave 3) | DEFER | Операторское окно; 013 реструктурировал в 9 fast + 3 night |

## 9. Change Impact (cascade)

- **postgres:16 → 18.4:** 1 init-SQL + 9 тест-фикстур. Без кода продуктива (только комментарий/фикстуры).
- **sync_env_defaults:** 1 файл; генерация env_defaults_generated.py при следующем `make generate-manifests`.
- **shared/AGENTS.md:** inventory-строки; не влияет на гейты (строки уже стейл).
- **json_writer:** TRAP-комментарий; поведение атомарной записи не меняется.
- **scaffold_helpers ARG001:** сигнатура `gen_project_platform_md`; каскад на callers — проверить grep.
- **test_deploy_orchestrator:** тест; продакшн-код не трогается.
- **DR-M4 provides.networks:** platform-infra.yaml (SoT); каскад — parity-гейт + docs.
- **F-032 restore:** postgres/Makefile + backup-канал; SEC-0018 (plaintext pre_restore) не нарушается.
- **F-036 load-test:** runner_cli.py + ssh-путь; REF-0016 (AllowTcpForwarding=no) сохраняется.
- Гейт-контур: requires_node не добавляется; F-032/F-036 unit-тесты — без ноды.

## 10. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_status_page.py (обновить фикстуру) | (существующие) | postgres:18.4 фикстура вместо :16 | status-page |
| tests/unit/test_sync_env_defaults.py | test_deploy_timeout_default_is_900 | fallback = "900" (не "600") | core/internal/scripts/sync_env_defaults.py |
| — (T3/T4 doc) | — | NONE — doc/комментарий; проверка grep-ом | shared/AGENTS.md, json_writer.py |
| tests/unit/test_scaffold_helpers.py | test_gen_project_platform_md_no_unused_name | ARG001 снят; callers работают | core/internal/scaffold/scaffold_helpers.py |
| tests/unit/test_deploy_orchestrator.py | test_deploy_uses_public_observable | observable (deployed/failed) вместо приватных патчей | deploy_orchestrator |
| tests/gates/test_gate_provides_networks_parity.py | (существующие 4) | subset-инвариант зелёный | platform-infra.yaml |
| tests/unit/test_postgres_restore.py | test_restore_clean_strategy | restore без init-конфликта (выбранная стратегия) | postgres restore |
| tests/unit/test_loadtest_runner.py | test_promql_pull_without_tcp_forward | PromQL-pull не требует TCP-forwarding | loadtest runner_cli |

TASK-10 — policy/doc: NONE — @rationale: D-013a архив, D-013b policy-документ; runtime-проверка
(worktree `make check`) — ручная, вне unit-слоя.

## 11. Acceptance Criteria (summary)

| AC | Проверка |
|----|----------|
| AC1 | 10 задач закрыты; `make check` rc=0; `make agent-check` exit 0; pre-commit green |
| AC2 | grep-проверки literal-AC плана 17 (T2.1/T3.2/T4.2/T6.2/T6.3/T7.3/T8.2) — каждый пуст |
| AC3 | DR-M4: parity-гейт green; provides.networks = фактический subset |
| AC4 | F-032/F-036: owner-стратегия зафиксирована, unit-тесты green |
| AC5 | D-013a → TRAP[ARCHIVED]; D-013b policy-документ создан |

## 12. File Manifest

| Файл | Операция |
|------|----------|
| core/modules/postgres/init/01-create-databases.sql | edit — postgres:16→18.4 |
| tests/unit/test_orphan_reconciler.py, test_orphan_reconciler_selfheal.py, test_security_posture.py, test_status_page.py | edit — фикстуры :16→18.4 |
| core/internal/scripts/sync_env_defaults.py | edit — fallback "600"→"900" |
| core/internal/shared/AGENTS.md | edit — снять 2 стейл-строки inventory |
| core/internal/healthcheck/metrics/json_writer.py | edit — TRAP[DOCKER-BIND-MOUNT]→ARCHIVED |
| core/internal/scaffold/scaffold_helpers.py | edit — снять ruff:ignore[ARG001] |
| tests/unit/test_deploy_orchestrator.py | edit — публичные observable |
| core/platform-infra.yaml | edit — provides.networks SoT-выверка |
| core/modules/postgres/Makefile (+backup config) | edit — restore --clean/empty-volume |
| core/internal/loadtest/runner_cli.py (+ssh path) | edit — saturation-pull |
| tests/e2e/README.md (или core/internal/shared/AGENTS.md) | edit — policy worktree parity |
| .ai/plans/013-resilience-drills-rework/02-Debt.md | edit — D-013a ARCHIVE |

## Next Steps

### Wave 1
Use coder role and read `.ai/plans/016-cross-plan-techdebt/01-DevPlan.md`, implement Wave 1: TASK-1..TASK-6.
Шаги: per-task `make check TEST_FILE=...` → финальный `make check` (батч) → `make agent-check` → pre-commit.

### Wave 2
Use coder role and read `.ai/plans/016-cross-plan-techdebt/01-DevPlan.md`, implement Wave 2: TASK-7
(без owner-гейта). TASK-8 (F-032) и TASK-9 (F-036) — после `question` владельцу о стратегии.

### Wave 3
Use coder role and read `.ai/plans/016-cross-plan-techdebt/01-DevPlan.md`, implement Wave 3: TASK-10.

### Операторские гейты (вне кода, фиксируются для следующей сессии)
D5 (GitHub Billing) · G5/H1 (test-VPS) · G2 (chaos-night) · core-deploy CI `AGE_SECRET_KEY` ·
F-04 (host key) · F-037-residual (reboot-drill + снять drop-in).

$END_DEVPLAN
