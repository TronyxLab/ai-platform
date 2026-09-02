<!-- GREP_SUMMARY: deploy-integrity devplan tasks parallel-groups test-spec verify-desired-state final-verify allow-autogen overlay-deploy-key preflight honesty-gate -->
# GREP_SUMMARY: deploy-integrity, devplan, tasks, parallel-groups, test-spec, verify-desired-state, final-verify, allow-autogen, overlay-deploy-key, preflight, honesty-gate
# STRUCTURE: ┌Brief-решения┐ → ◇ Draft Code Graph → ⊕ $TASKS → ◇ $PARALLEL_GROUPS → ⊕ $TEST_SPEC → ⎋ Next Steps

<!-- $START_DEVPLAN -->

## $ARTIFACT_CONTRACT

- **PURPOSE:** Детальный план реализации P0-пути Brief 01 — устранить первопричину (неверный предикат успеха) и закрепить её гейтами.
- **DESCRIPTION:** 8 задач: fail-loud с `allow_autogen`, overlay clone exit 10, ssl fail-closed, converge postconditions, final-verify фаза, overlay deploy-key авто-провижин, preflight input-contract, honesty-гейт. Плюс Debt-артефакт на автоматизацию арены.
- **RATIONALE:** Минимальный разрыв цикла «deploy → ошибка → fix» без большого рефакторинга; расширение существующих гейтов (honesty_mode, preflight, state_machine) вместо параллельных механизмов.
- **ACCEPTANCE_CRITERIA:** Совпадают с §Acceptance Criteria ниже (AC1–AC8) + `make check` до чистоты.
- **IMPLEMENTS:** `029-deploy-integrity/01-Brief.md` (P0-путь, D1–D5).
- **IMPACTS:** ~20 файлов кода + ~10 файлов тестов + schema/contract-изменения (node.yaml `allow_autogen`, новая фаза state machine, verb `validate-node-input`).
- **REQUIRES:** Чистая VM для live-верификации (владелец); `make check` (SoT `core/check-suite.yaml`).

---

## 1. Requirements Analysis — ключевые критерии успеха

1. **Честность exit 0:** bootstrap/converge репортят success только при верифицированном end-state (серты на диске, secrets.env полный, проекты serving, GHCR ≠ skip).
2. **Fail-loud на контракт:** отсутствие required-секретов (без `allow_autogen`), overlay clone-fail, отсутствие deploy-key → exit 10 с причиной, не WARN.
3. **Реконсиляция, не чекпойнт-skip:** «done» = артефакт существует И hash совпал; дрейф (stub/absent/cert) видим и лечится.
4. **Входной контракт проверяется до SCP:** AGE-форма, приоритет env-vs-file, sops-наличие, required-ключи — 0 remote.
5. **Класс silent-success закрыт детектором:** новый паттерн → honesty-гейт RED.

## 2. Architecture Overview — Draft Code Graph

```
node.yaml (secrets.allow_autogen) ──► phases/secrets.py (fail-loud vs autogen)
context_overlay.py ──► clone-fail → PlatformFatalError(10)
converge/{vhosts,ssl_certs,runtime}.py ──► postcondition verify-desired-state
state_machine.py ──► +FINAL_VERIFY (φ-final-verify) после converge_services
phases/final_verify.py (new) ──► 4 end-state assertions → exit 10
context_initializer.py ──► deploy-key node-side через core-канал (SCP/rsync)
preflight.py ──► --scope input (AGE-форма/приоритет/sops/required-ключи)
core/entrypoints/validate-node-input.sh (new thin facade)
tests/gates/test_gate_deploy_honesty.py (new static detector)
```

**Ключевые контракты (верифицированы чтением кода):**
- `BootstrapPhase` (enum, 14 значений) + `INIT_PHASE_ORDER` + `_phase_dependency_graph` + `PHASE_DISPATCH` в `state_machine.py` — новая фаза добавляется во все 4 точки + `state_store.py` ключ.
- `phases/secrets.py` уже несёт `verify_required_sops_secrets` + autogen-инициализацию — флаг встраивается в этот узел.
- `converge/ssl_certs.py#reconcile_ssl_certs`, `converge/vhosts.py#verify_vhosts`, `converge/runtime.py#reconcile_runtime_state` — точки postcondition.
- `preflight.py` — FATAL={ssh,disk}, WARN={s3,ghcr,docker_hub,dns}; расширяется новым scope без ломки существующих проб.

## 3. Data Flow (изменённые пути)

1. `make validate-node-input NODE=<n>` → `preflight.py --scope input` → проверка AGE-формы/приоритета/sops/required-ключей → exit 1 с причиной ДО любого SSH.
2. `make bootstrap-node` → φ4: `allow_autogen` отсутствует + required∧sops missing → exit 10; иначе autogen.
3. `deploy_orchestrator._preflight` → `context_overlay.ensure_context_repo` clone-fail → `PlatformFatalError(10)`.
4. `converge` → R5/R6/R-ssl/R9: артефакт отсутствует → дрейф → восстановление или fail-loud (не «no action»).
5. φ8.5 converge → **φ-final-verify**: 4 end-state assertions → FAIL exit 10; повторный bootstrap = no-op фазы.
6. `make new-context` → `provision_deploy_key` + core-канал доставки ключа+алиаса на ноду → `ensure_context_repo` клонирует без ручных шагов.

## 4. $TASKS

| ID | Задача | Артефакт | Acceptance Criteria | Deps | Сложн. |
|---|---|---|---|---|---|
| T1 | `allow_autogen` флаг: schema + validation + secrets-фаза | `node.schema.json`, `node_yaml/validation.py`, `phases/secrets.py`, фикстуры, unit-тесты | node.yaml с `secrets.allow_autogen:true` проходит schema; без флага + required∧sops missing → exit 10 (не autogen); с флагом → autogen разрешён | — | 4 |
| T2 | Overlay clone-fail → exit 10 | `context_overlay.py`, `deploy_orchestrator.py`, `tests/unit/test_context_overlay.py` | clone-fail → `PlatformFatalError(10)` (не WARN+return 1); no repos.core + context present → hard error; тест asserts fail-loud | — | 3 |
| T3 | ssl_certs fail-closed на None extractor | `converge/ssl_certs.py`, `tests/unit/test_converge_ssl_certs.py` | extractor None → статус ≠ «converged»; честный маппинг issued/restored/skipped/failed; нет ложного «provisioned» | — | 3 |
| T4 | Converge postconditions (verify-desired-state) | `converge/vhosts.py`, `converge/runtime.py`, unit-тесты | vhost-render: rendered-count == exposed-projects (иначе drift); absent-but-enabled = drift; удаление артефакта → converge лечит или fail-loud | T3 (ssl уже отдельно) | 4 |
| T5 | Final-verify фаза после φ8.5 | `state_machine.py`, `phases/__init__.py`, `phases/final_verify.py` (new), `state_store.py`, `AGENTS.md` | 4 end-state assertions (серты/secrets.env/exposed-serving/GHCR≠skip) → FAIL exit 10; идемпотентна (повтор = no-op); dependency converge_services→final_verify | T1, T3, T4 | 6 |
| T6 | Overlay deploy-key node-side авто-провижин | `context_initializer.py`, core-канал, `bootstrap/AGENTS.md` runbook | `make new-context` + bootstrap клонирует приватный overlay без ручных scp/chmod/ssh-config; ключ+алиас через core-канал; отсутствие → fail-loud | — | 5 |
| T7 | Preflight input-contract + verb `validate-node-input` | `preflight.py` (--scope input), `node_detect.py` (форма), `core/entrypoints/validate-node-input.sh` (new), `entrypoint-manifest.yaml`, `Makefile`, unit-тесты | single-line AGE, env-vs-file приоритет, sops-наличие, required-ключи → exit 1 с причиной ДО SSH; verb зарегистрирован в allowed_verbs | — | 5 |
| T8 | Honesty-гейт для деплой-кода | `tests/gates/test_gate_deploy_honesty.py`, `entrypoint-manifest.yaml` (gates) | статический детектор ловит новые silent-success паттерны в deploy/converge; R5 negative на synthetic-паттерн; trinity-регистрация | T2,T3,T4,T5 (код уже честен) | 4 |
| T9 | Env-hermeticity fixture (P1) | `tests/_conftest/`, conftest-хук, gate | session fixture snapshot/clean платформенных env-ключей; polluter-тест → check RED | — | 3 |

**Merge-правило применено:** T2/T3 (≤2 файла, ≤20 строк ядра) оставлены раздельными — концептуально отличные fail-loud точки (overlay vs ssl), `@keep_separate`.

**Критический путь:** T1 → T5 → (интеграционная live-верификация). T3, T4 питают T5.

## 5. $PARALLEL_GROUPS

### Wave 1 (независимы, нет общих файлов)
- Задачи: T1, T2, T3, T4, T6, T9
- Команда: `coder Read .ai/plans/029-deploy-integrity/02-DevPlan.md, implement Wave 1: T1, T2, T3, T4, T6, T9`

### Wave 2 (зависимости из Wave 1)
- Задачи: T5 (deps T1,T3,T4), T7 (независим, держим для manifest-консистентности)
- Команда: `coder ... implement Wave 2: T5, T7`

### Wave 3 (manifest-регистрация + детектор после рефактора)
- Задачи: T8 (deps T2–T5; регистрация gates в entrypoint-manifest после T7)
- Команда: `coder ... implement Wave 3: T8`

### Wave 4 (верификация)
- `make check` до чистоты → live-верификация владельцем на чистой VM (критерии AC1–AC8).

## 6. Acceptance Criteria (сводная)

| # | Критерий | Проверка |
|---|---|---|
| AC1 | Чистая нода без enc-файла и без `allow_autogen` → fail-loud | φ4 exit 10 с причиной |
| AC2 | Overlay clone-fail → exit 10 | удалить deploy-key → bootstrap fail-loud |
| AC3 | Удаление серта/vhost/контейнера → converge лечит или fail-loud | drift-injection дрилл |
| AC4 | final-verify: 4 assertions → exit 10 при нарушении | сломать одно из 4 → bootstrap exit 10 |
| AC5 | `make new-context` + bootstrap клонирует overlay без ручных шагов | свежая нода |
| AC6 | `make validate-node-input` fail до SSH на кривом входе | кривой AGE / нет sops / нет ключа |
| AC7 | Новый silent-паттерн → honesty-гейт RED | R5 negative |
| AC8 | Повторный bootstrap = no-op (включая final-verify) | re-run no-op |

## 7. File Manifest

```
core/schemas/node.schema.json                    # +secrets.allow_autogen
core/internal/shared/node_yaml/validation.py      # валидация allow_autogen
core/internal/bootstrap/lifecycle/phases/secrets.py   # fail-loud vs autogen
core/internal/bootstrap/deploy/context_overlay.py      # clone-fail → exit 10
core/internal/bootstrap/deploy/deploy_orchestrator.py  # preflight overlay fatal
core/internal/bootstrap/converge/ssl_certs.py          # fail-closed None
core/internal/bootstrap/converge/vhosts.py             # postcondition rendered-count
core/internal/bootstrap/converge/runtime.py            # postcondition absent-but-enabled
core/internal/bootstrap/lifecycle/state_machine.py     # +FINAL_VERIFY enum/order/graph/dispatch
core/internal/bootstrap/lifecycle/phases/__init__.py   # export final_verify
core/internal/bootstrap/lifecycle/phases/final_verify.py  # (new) 4 end-state assertions
core/internal/bootstrap/lifecycle/state_store.py       # final_verify key
core/internal/scaffold/context_initializer.py          # deploy-key node-side core-канал
core/internal/bootstrap/preflight.py                   # --scope input
core/internal/shared/node_detect.py                    # AGE-форма single-line
core/entrypoints/validate-node-input.sh                # (new) thin facade
core/entrypoint-manifest.yaml                          # verb + gate registration
Makefile                                               # validate-node-input target
core/internal/bootstrap/AGENTS.md                      # runbook обновление
tests/gates/test_gate_deploy_honesty.py                # (new) static detector
tests/unit/test_{secrets,context_overlay,converge_ssl_certs,converge_vhosts,state_machine,preflight}.py  # + негативы
tests/_conftest/                                       # env-hermeticity fixture
```

## 8. Design Decisions (с @rationale)

- **DD-1 `allow_autogen` в `secrets.` а не на уровне context** — **@rationale:** флаг относится к политике провижининга секретов, а не к идентичности контекста; резолв `NodeYaml.get("secrets.allow_autogen", default=False)` — одна точка чтения, гейтится schema.
- **DD-2 Final-verify как фаза, не as healthcheck-расширение** — **@rationale:** фаза даёт checkpoint/идемпотентность и exit 10 через существующую state machine; healthcheck — liveness-инструмент, не end-state.
- **DD-3 Honesty-детектор = статика, allowlist пуст** — **@rationale:** deny-by-default (паттерн REF-0107 honesty_mode): любой новый success-лог без post-check = RED, allowlist расширяется только с ревью. FP-риск ниже цены ложного зелёного (RC2 = 16 фиксов).
- **DD-4 validate-node-input = фасад над preflight, не новый модуль** — **@rationale:** dual-mechanism дрейф (step 1.10): один input-contract, два точки входа (bootstrap.sh первый шаг + operator verb).
- **DD-5 deploy-key через core-канал** — **@rationale:** решение владельца (D2 answer); приватный ключ остаётся вне git, шипится по существующему push-каналу core (SCP/rsync), не через overlay git.

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|---|---|---|---|
| tests/unit/test_secrets.py | test_allow_autogen_true_permits_autogen | флаг true → autogen разрешён | phases/secrets.py |
| tests/unit/test_secrets.py | test_no_autogen_required_missing_fails | без флага + required∧sops missing → PlatformFatalError(10) | phases/secrets.py |
| tests/unit/test_context_overlay.py | test_clone_fail_raises_fatal | git clone rc≠0 → PlatformFatalError(10) | context_overlay.py |
| tests/unit/test_converge_ssl_certs.py | test_extractor_none_not_converged | extractor None → статус ≠ converged | converge/ssl_certs.py |
| tests/unit/test_converge_vhosts.py | test_rendered_count_mismatch_is_drift | rendered < exposed → drift entry | converge/vhosts.py |
| tests/unit/test_state_machine.py | test_final_verify_after_converge | dependency graph: converge_services → final_verify | state_machine.py |
| tests/unit/test_final_verify.py | test_missing_cert_fails | нет серта exposed-домена → exit 10 | phases/final_verify.py |
| tests/unit/test_final_verify.py | test_noop_on_rerun | повтор → no-op (state done) | phases/final_verify.py |
| tests/unit/test_preflight.py | test_input_scope_multiline_age_fails | multi-line AGE → exit 1 до SSH | preflight.py --scope input |
| tests/unit/test_preflight.py | test_input_scope_priority_env_over_file | env перекрывает файл → предупреждение/контракт | preflight.py --scope input |
| tests/gates/test_gate_deploy_honesty.py | test_silent_success_pattern_detected | synthetic success-лог без post-check → RED | static detector |
| tests/gates/test_gate_deploy_honesty.py | test_negative_original_trigger | R5: исходный паттерн F-01 (rendered при 0) детектируется | static detector |

## Next Steps

### Wave 1
Используй роль Coder, прочитай `.ai/plans/029-deploy-integrity/02-DevPlan.md`, реализуй Wave 1: T1, T2, T3, T4, T6, T9. Верификация — `make check` (до чистоты).

### Wave 2
`coder ... implement Wave 2: T5, T7` → `make check`.

### Wave 3
`coder ... implement Wave 3: T8` → `make check`.

### Wave 4
`make check` финально; live-верификация владельцем (чистая VM): AC1–AC8. Результат фиксируется в VerificationReport.

<!-- $END_DEVPLAN -->
