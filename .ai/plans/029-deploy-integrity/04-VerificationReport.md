<!-- GREP_SUMMARY: deploy-integrity VerificationReport QA verification devplan-029 T1-T9 fail-loud allow-autogen final-verify honesty-gate preflight-input verify-desired-state bootstrap-sh-loc R1-pass-test manifests-uncommitted verdict-degraded -->
# GREP_SUMMARY: deploy-integrity, VerificationReport, QA, T1-T9, verdict-DEGRADED, bootstrap.sh-LOC, R1-pass-test, manifests-uncommitted, final-verify, honesty-gate
# STRUCTURE: ▶ вердикт → ◇ AC1-AC8 матрица → ◇ находки F1/F2/F3/F4 → ◇ качество тестов R1-R5 → ⎋ делегирование фиксов

<!-- $START_VERIFICATION_REPORT -->

## $ARTIFACT_CONTRACT

- **PURPOSE:** Независимая QA-верификация реализации девплана `029-deploy-integrity/02-DevPlan.md`
  (T1–T9) — сверка «девплан ↔ факт», измеримый вердикт по AC1–AC8, находки с серьёзностью,
  передача фиксов кодеру (QA не правит код).
- **DESCRIPTION:** DoD/AC-матрица по 8 критериям + $TEST_SPEC; полный прогон unit+gates
  (5499 passed / 18 skipped / 3 failed); три находки (два реальных дефекта + один transient);
  аудит качества тестов R1–R5.
- **RATIONALE:** Волна реализована, но не закоммичена; рантайм-гейт `make check` красный по
  3 позициям — статический проход должен отделить реальные дефекты (блокируют релиз) от
  состояния незакоммиченного дерева.
- **ACCEPTANCE_CRITERIA:** каждый пункт AC1–AC8 имеет статус/доказательство; каждая находка —
  серьёзность + воспроизводимый след (файл:строка + команда); BLOCKED зафиксирован без обходов.
- **IMPLEMENTS:** 029-deploy-integrity/02-DevPlan.md §6 (Acceptance Criteria), §9 ($TEST_SPEC);
  конституция QA-роли (находки не исправляются, фиксы делегируются).
- **IMPACTS:** делегирование фиксов F1/F2 кодеру; F3 закрывается коммитом; live-верификация
  AC1–AC8 — за владельцем (чистая VM, Wave 4).
- **REQUIRES:** коммит HEAD 85f2b0f + незакоммиченное дерево; `make check`
  (SoT `core/check-suite.yaml`); Python 3.14.6 venv.

<!-- $END_ARTIFACT_CONTRACT -->

# 029-deploy-integrity — VerificationReport (QA, 2026-09-02)

## 0. Вердикт

**DEGRADED (реализация полна и когерентна, но `make check` красный — 2 реальных дефекта +
1 transient).**

Реализация T1–T9 присутствует целиком и соответствует замыслу девплана: fail-loud с
`allow_autogen` (T1), overlay clone → exit 10 (T2), ssl fail-closed (T3), converge
postconditions (T4), φ-final-verify после φ8.5 (T5), overlay deploy-key авто-провижин (T6),
preflight input-contract + `validate-node-input` (T7), honesty-гейт (T8), env-hermeticity (T9).

Все новые/изменённые unit-тесты и honesty-гейт зелёные (171 тест). Полный прогон
`tests/unit + tests/gates`: **5499 passed, 18 skipped, 3 failed**. Три провала:

| # | Гейт | Характер | Блокирует |
|---|---|---|---|
| F1 | `test_bootstrap_batch::test_loc_under_100` | Реальный регресс: bootstrap.sh = 105 LOC (>100) из-за T6 | да |
| F2 | `test_gate_r1_no_pass_tests` | Реальный дефект теста: pass-test без assertion (test_secrets.py:159) | да |
| F3 | `test_gate_manifests_up_to_date` | Transient: generated-файлы незакоммичены (диск == генераторам) | до коммита |

## 1. AC-матрица (сводная §6 девплана)

| # | Критерий | Статус | Доказательство |
|---|---|---|---|
| AC1 | Чистая нода без enc + без `allow_autogen` → fail-loud exit 10 | ✅ unit | `helpers/secrets.py` verify_required_sops_secrets: no enc + allow_autogen=False → `ConfigValidationError`; φ4 `_run_secrets_step` → `PlatformFatalError` (exit 10). Тесты `test_secrets.py` 4 шт. зелёные |
| AC2 | Overlay clone-fail → exit 10 | ✅ unit | `context_overlay.py` `_clone_context_repo`: TimeoutExpired/FileNotFound/rc≠0 → `PlatformFatalError`; `deploy_orchestrator._preflight` re-raise. Тесты `test_context_overlay.py` 9/9 |
| AC3 | Удаление серта/vhost/контейнера → converge лечит или fail-loud | ✅ unit (vhost) | `converge/vhosts.py verify_vhosts` → `report_add("fail", "<domain>.conf not found")` + exit 2; ssl `reconcile_ssl_certs` None→warn+exit 1; runtime F-09 (absent-deploy) pre-existing. Тесты `test_converge_vhosts/ssl_certs` |
| AC4 | final-verify: 4 assertion → exit 10 при нарушении | ✅ unit | `phases/final_verify.py`: (a) серты on-disk, (b) secrets.env, (c) exposed vhost, (d) GHCR≠skip. `test_final_verify.py` 5/5 (missing cert / None / pass+rerun / missing node.yaml) |
| AC5 | `make new-context` + bootstrap клонирует overlay без ручных шагов | ✅ статика (живой прогон — за владельцем) | `context_initializer.py` `install_overlay_deploy_key_node_side` + CLI `install-node-deploy-key`; `bootstrap.sh` overlay-key шаг после SCP. Тесты `test_context_initializer.py` 20/20 |
| AC6 | `make validate-node-input` fail до SSH на кривом входе | ✅ unit | `preflight.py --scope input` (AGE-форма/sops/required), `validate-node-input.sh` 0 remote, `Makefile` target + manifest verb. Тесты `test_preflight.py` 6 новых |
| AC7 | Новый silent-паттерн → honesty-гейт RED | ✅ gate | `test_gate_deploy_honesty.py`: tree-clean + 2 R5 negative (F-01 trigger, count-zero). 3/3 зелёные |
| AC8 | Повторный bootstrap = no-op (включая final-verify) | ✅ unit | `test_final_verify.py::test_full_pass_and_rerun_noop` (повтор → True без мутаций); `_HASH_INVALIDATED_PHASES` включает FINAL_VERIFY |

## 2. Находки

### F1 [HIGH] · bootstrap.sh = 105 LOC — нарушение thin-facade контракта (регресс T6)
- **След:** `tests/unit/test_bootstrap_batch.py:118 test_loc_under_100` — «bootstrap.sh is 105 LOC — target ≤ 100 (DevPlan 170 W9-F1)».
- **Причина:** T6 добавил overlay-key шаг (+8 строк: dry-run echo, 2 строки вызова
  `python3 -m ... install-node-deploy-key`, FATAL-ветка, комментарии) — скрипт превысил
  лимит 100 LOC.
- **Почему блокирует:** W9-F1 — канон тонкого фасада (бизнес-логика в Python, shell <100 LOC);
  гейт RED.
- **Fix (кодер):** сократить bootstrap.sh до ≤100 LOC — вынести verbose-echo/комментарии,
  консолидировать overlay-key блок (однострочный вызов + компактная FATAL-проверка), логика
  уже в `context_initializer.install_overlay_deploy_key_node_side`.

### F2 [HIGH] · R1 pass-test: test_secrets.py:159 без assertion
- **След:** `tests/gates/test_gate_r1_no_pass_tests.py` — «test function
  'test_ensure_allow_autogen_true_resolved_from_node_yaml_passes' without assertion mechanism».
- **Причина:** тест только вызывает `ensure_secrets_exist(...)` и логирует; отсутствует
  assert/pytest.raises. R1 (testing.md): «тест без assertions = RED (блокирует merge)».
- **Fix (кодер):** добавить реальную проверку — например caplog-assert на IMP:8
  «allow_autogen=true» (зеркало `test_allow_autogen_true_permits_autogen`), либо
  `pytest.raises`-негатив отсутствует + явный assert результата.

### F3 [INFO] · generated-файлы незакоммичены (transient)
- **След:** `test_gate_manifests_up_to_date.py` — «generated files differ from committed HEAD»
  (core/AGENTS.md GENERATED-секция + core/entrypoint-manifest.yaml).
- **Причина:** гейт проверяет `дерево == HEAD`; работа не закоммичена. Сам гейт подтверждает
  «диск УЖЕ соответствует генераторам» — файлы сгенерированы корректно (validate-node-input
  verb + gates + «15 фаз» описание согласованы с SoT).
- **Fix:** `git add` + commit (не `make generate-manifests`). После коммита гейт зелёный.

### F4 [LOW] · Drift File-Manifest девплана (§7) vs факт
- **След:** девплан §7 указывает `phases/secrets.py`, `node_detect.py`, `state_store.py` как
  изменяемые. Факт: allow_autogen живёт в `helpers/secrets.py`; AGE-форма проверяется инлайн
  в `preflight.py` (не node_detect.py); `state_store.py` НЕ тронут (ключ `final_verify`
  неявный — steps-дикт name-based, `BootstrapPhase.FINAL_VERIFY.value == "final_verify"`).
  T3/T4 converge-исходники (ssl_certs.py, vhosts.py, runtime.py) не менялись в этой волне —
  поведение pre-existing (027 F-09/F-10), 029 добавил тесты + потребление в final_verify.
- **Fix:** косметическая правка §7 манифеста (не блокирует).

### F5 [INFO] · Pre-existing file_lock KeyError под xdist (вне скоупа 029)
- **След:** `test_state_store_concurrent_writers.py` — `PytestUnhandledThreadExceptionWarning`
  `KeyError` в `file_lock.py:410 release()` при `-n auto`. Не в списке FAILED (warning).
- **Причина:** файл `file_lock.py`/`state_store.py` не менялись в 029; воспроизводится только
  под параллельным xdist. Вне скоупа плана.

## 3. Качество тестов (R1–R5)

| Правило | Оценка | Деталь |
|---|---|---|
| R1 NO pass-tests | ❌ RED | F2 — `test_ensure_allow_autogen_true_resolved_from_node_yaml_passes` без assertion |
| R2 NO unfalsifiable | ✅ | asserts на поведение/статусы, не на языковые гарантии |
| R3 STALE SKIP | ✅ | новых skip-маркеров нет |
| R4 NO_SERVICE=FAIL | ✅ | skip только документированные (module-hooks, nginx-template) |
| R5 ANTI-SURVIVORSHIP | ✅ | honesty-гейт несёт 2 negative (F-01 trigger + count-zero); `test_context_overlay` — негативы M13a/T2 с суффиксом `_negative`; `test_final_verify` — негативы missing-cert/None/missing-node.yaml |

## 4. BLOCKED / вне прогона

- **Live-верификация AC1–AC8 (чистая VM)** — за владельцем (Wave 4). Требует multipass/VPS;
  не исполнялась QA (нет доступа к ноде, правило 7 конституции).
- **Полный `make check`** (check-suite: docker-гейты, e2e, requires_node) — не гонялся целиком;
  детерминированная часть (unit+gates) прогнана. requires_node — ручной канон (root AGENTS.md).

## 5. Делегирование фиксов

1. **F1** — bootstrap.sh ≤100 LOC (сократить overlay-key блок).
2. **F2** — добавить assertion в `test_ensure_allow_autogen_true_resolved_from_node_yaml_passes`.
3. **F3** — закоммитить generated-файлы (после F1/F2) — закрывает manifests-гейт.
4. **F4/F5** — опционально: правка §7 манифеста; file_lock KeyError — отдельный трекинг.

После F1–F3 повторный батч: `make check` → чистота → Wave 4 live-верификация владельцем.

<!-- $END_VERIFICATION_REPORT -->
