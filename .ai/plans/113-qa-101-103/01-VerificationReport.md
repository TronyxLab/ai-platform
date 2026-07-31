$START_VERIFICATION_REPORT
# VerificationReport — QA 101 + 103

$ARTIFACT_CONTRACT
PURPOSE:               Semantic QA verification of DevPlan 101 (remote-cmd.sh 266→≤60 LOC Strangler-Fig)
                       and DevPlan 103 (context-promote.sh 161→≤40 LOC Python migration). Cross-plan
                       diagnostics for test_shell_facade_contract.py failures (Plan 100 artifact).
DESCRIPTION:           Static audit (all ACs), drift detection (manifest consumers, deploy.mk comment),
                       runtime validation (52 tests across 4 suites: 23 unit + 29 integration),
                       cross-plan failure root-cause analysis. 2 findings requiring fix: MANIFEST_DRIFT
                       (audit.sh consumer) + CONTRACT_TEST_STALE (test_shell_facade_contract.py 4 FAIL).
RATIONALE:             Combined report per QA precedent 112-qa-verification-099-100-102 — both plans
                       are Strangler-Fig shell→Python migrations achieved in parallel by Coder.
ACCEPTANCE_CRITERIA:   AC1-AC9 for Plan 101, AC1-AC9 for Plan 103 — all PASS, no regressions.
IMPLEMENTS:            DevPlan 101 (§7 $TASKS), DevPlan 103 (§7 $TASKS)
IMPACTS:               .ai/plans/113-qa-101-103/01-VerificationReport.md (NEW)
REQUIRES:              DevPlan 101, DevPlan 103, current HEAD fbe306d4284d9105193605378be28eb64b3c6795
$END_ARTIFACT_CONTRACT

🔒 Verified against SHA `fbe306d4284d9105193605378be28eb64b3c6795`

---

## Section 1 — Static Audit (Phase 1)

### Plan 101 — File Compliance Matrix

| File | Exists | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region/#endregion | LDD IMP:7-10 | No secrets |
|------|:------:|:------------:|:---------:|:---------------:|:-------------------:|:------------:|:----------:|
| `core/internal/bootstrap/build-ssh-cmd.sh` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×3) | ✅ printf %q logs (D3) | ✅ |
| `core/internal/bootstrap/remote_executor.py` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×9) | ✅ IMP:7-10 throughout | ✅ |
| `core/internal/bootstrap/remote-cmd.sh` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×4) | ✅ IMP:7-10 via Python | ✅ |
| `tests/unit/test_remote_executor.py` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×11) | ✅ caplog IMP:9 | ✅ |

### Plan 103 — File Compliance Matrix

| File | Exists | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region/#endregion | LDD IMP:7-10 | No secrets |
|------|:------:|:------------:|:---------:|:---------------:|:-------------------:|:------------:|:----------:|
| `core/internal/deploy/context_promoter.py` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×6) | ✅ IMP:7-10 throughout | ✅ |
| `core/entrypoints/context-promote.sh` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×1) | ✅ IMP:10 ERROR | ✅ |
| `tests/unit/test_context_promoter.py` | ✅ | ✅ | ✅ | ✅ | ✅ paired (×12) | ✅ caplog IMP:9 | ✅ |

### Compliance Summary

- Все 7 файлов имеют MODULE_CONTRACT, GREP_SUMMARY, STRUCTURE
- Все #region/#endregion пары сбалансированы (проверено grep'ом открывающих/закрывающих маркеров)
- Ни один файл не содержит exposed secrets (токенов, паролей, ключей в plaintext)
- D3 printf %q неприкосновенен — 32 использования в build-ssh-cmd.sh
- Нет bare `except:` или `except: pass`
- TRAP[BUG] P0/P1/P2/P4 сохранены в соответствующих файлах

---

## Section 2 — Drift Analysis (Phase 2)

### DRIFT Register

| DRIFT-ID | Severity | Files | Expected | Actual | Fix |
|----------|----------|-------|----------|--------|-----|
| **DRIFT-1** | **HIGH** | `core/entrypoint-manifest.yaml:541` vs `core/entrypoints/context-promote.sh` | `audit.sh` consumer list НЕ должен включать context-promote.sh (больше не source'ит) | `consumers:` включает `core/entrypoints/context-promote.sh` (строка 541) | Удалить `- core/entrypoints/context-promote.sh` из consumers audit.sh в entrypoint-manifest.yaml |
| **DRIFT-2** | INFO | `makefiles/deploy.mk:88` vs `core/entrypoints/context-promote.sh` | Комментарий должен отражать механизм: `git push --mirror` + `Python context_promoter.py` | `→ copies to <context>/ai-platform` — устаревшая формулировка из 161-LOC shell-версии | Обновить комментарий: `→ core/internal/deploy/context_promoter.py → git push --mirror to <context>/ai-platform` |
| **DRIFT-3** | MINOR | `core/internal/bootstrap/remote-cmd.sh:6` @scope | `@scope Sourced by bootstrap.sh, node-update.sh, converge.sh` | bootstrap.sh больше не source'ит remote-cmd.sh (source'ит build-ssh-cmd.sh напрямую с DevPlan 101) | Обновить @scope: убрать bootstrap.sh, добавить «build-ssh-cmd.sh sourced separately by bootstrap.sh» |

### Cross-File Value Mismatches

| Check | Status |
|-------|--------|
| **Dead code removal** — `execute_remote_reconcile_entrypoint` | ✅ 0 matches in all .sh/.py files (only in plan documents) |
| **Dead code removal** — `_resolve_and_extract` | ✅ 0 matches in source files (only in plan documents) |
| **D3 printf %q preserved** | ✅ 32 printf '%q' calls in build-ssh-cmd.sh (verbatim extraction) |
| **TRAP[BUG] P1 PLATFORM_ROOT** | ✅ build-ssh-cmd.sh:26-36 + build-ssh-cmd.sh:12 |
| **TRAP[BUG] P2 ci_deploy_key** | ✅ build-ssh-cmd.sh:46-51 + build-ssh-cmd.sh:13 |
| **TRAP[BUG] P0 VPS self-SSH** | ✅ remote_executor.py:21-27 |
| **TRAP[BUG] P4 ssh_exec** | ✅ remote_executor.py:28-32 |
| **GIT_MIRROR_TOKEN safety** | ✅ context_promoter.py:168 — literal string `${GIT_MIRROR_TOKEN}`, NOT f-string. Token never in argv/URL (only in env). Подтверждено test_promote_via_https_token_not_in_argv PASS |
| **entrypoint-manifest delegates_to** | ✅ line 54: `core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py` |
| **audit.sh NOT sourced** | ✅ context-promote.sh: grep `source.*audit` → 0 hits |

### Contract Violations

**CONTRACT-VIOLATION-1** (HIGH): `entrypoint-manifest.yaml` lib section audit.sh CONSUMER list includes `core/entrypoints/context-promote.sh` (line 541), but context-promote.sh facade (DevPlan 103) no longer sources `audit.sh`. Audit is now done via Python `shared/audit_logger.write_audit_entry()` (D3 in DevPlan 103).

---

## Section 3 — Invariant Status (Phase 3)

Выборочная проверка ключевых инвариантов, затрагиваемых планами:

| Invariant | Source | Status | Evidence |
|-----------|--------|:------:|----------|
| Makefile — единый фасад (root AGENTS.md #1) | AGENTS.md | **HELD** | `make context-promote` → deploy.mk:95 → context-promote.sh → context_promoter.py. `make node-update` → node-update.sh → remote-cmd.sh → remote_executor.py. API не менялся. |
| Python-first (root AGENTS.md языковая политика) | AGENTS.md | **HELD** | Новый код — только Python: context_promoter.py (359 LOC), remote_executor.py (265 LOC). Shell — тонкие фасады (60 + 32 LOC). |
| Strangler-Fig shell→Python (root AGENTS.md) | AGENTS.md | **HELD** | Both plans follow canonical pattern: shell thin facade → `python3 -m core.*` |
| D3 printf %q неприкосновенен (DevPlan 101 D3) | DevPlan 101 | **HELD** | build-ssh-cmd.sh содержит 32 printf '%q' вызова, извлечённых verbatim |
| Manifest Generation Contract (root AGENTS.md #11) | AGENTS.md | **AT_RISK** | DRIFT-1: consumers list в manifest содержит устаревшую запись audit.sh → context-promote.sh. CI gate `check-manifests` может не детектировать (consumers — structural секция, сохраняется verbatim генератором) |
| Zero inline python3 (AGENTS.md bootstrap) | bootstrap/AGENTS.md | **HELD** | Все inline python3 блоки мигрированы в Python-функции. В фасадах 0 `python3 -c` / `<<PYEOF`. |
| Shell facade LOC caps | DevPlans | **HELD** | remote-cmd.sh = 60 LOC (AC2 ≤60), context-promote.sh = 32 LOC (AC3 ≤40), build-ssh-cmd.sh = 122 LOC (~100 target) |

---

## Section 4 — Test Quality (Phase 4)

### Coverage Summary

| Suite | Tests | Passed | Failed | Skip rate |
|-------|:-----:|:------:|:------:|:---------:|
| `tests/unit/test_remote_executor.py` | 11 | 11 | 0 | 0% |
| `tests/unit/test_context_promoter.py` | 12 | 12 | 0 | 0% |
| `tests/test_node_lifecycle_static.py` | 11 | 11 | 0 | 0% |
| `tests/test_bootstrap_auto.py` | 15 | 15 | 0 | 0% |
| `tests/test_unit_age_key_env_only.py` | 3 | 3 | 0 | 0% |
| `tests/unit/test_shell_facade_contract.py` (cross-plan) | 6 | 2 | 4 | 0% |
| **Total** | **58** | **54** | **4** | **0%** |

### LDD IMP:9 Coverage

- `test_ldd_imp9_logs_on_success` (remote_executor) — ✅ PASS, IMP:9 лог присутствует
- `test_audit_logging_imp9` (context_promoter) — ✅ PASS, IMP:9 лог присутствует
- Anti-Illusion Rule: ✅ выполнено — все успешные сценарии содержат IMP:9 business-logic логи

### Test Honesty (R1-R5)

- **R1 (no pass-tests):** ✅ Все тесты имеют asserts (проверено grep'ом `assert ` в обоих тестовых файлах)
- **R2 (no unfalsifiable):** ✅ Все asserts проверяют бизнес-логику (exit codes, mock-вызовы, caplog логи) — не language guarantees
- **R3 (stale skips):** ✅ 0 skip-маркеров во всех 5 файлах
- **R4 (service-not-available):** ✅ Нет skip'ов с "no service" причинами
- **R5 (negative tests):** ✅ Не применимо (тесты не reference bug IDs)

### Cross-Plan Test Quality: test_shell_facade_contract.py

**4 FAIL, root cause = Plan 100 (deploy-modules.sh thin facade), NOT Plan 101/103:**

| Test | Проверяемый паттерн в deploy-modules.sh | Статус | Причина FAIL |
|------|----------------------------------------|:------:|--------------|
| `test_shell_has_context_overlay` | `context_overlay.py` + `--action ensure` | **FAIL** | deploy-modules.sh теперь 50-LOC фасад → exec'ит `deploy_orchestrator.py`. Имена Python-модулей больше не в shell. |
| `test_shell_has_python_delegation` | 5 модулей: `context_overlay.py`, `secrets_validator.py`, `docker_orchestrator.py`, `sudoers_generator.py`, `orphan_reconciler.py` | **FAIL** | Все 5 модулей импортируются **внутри** `deploy_orchestrator.py` (Python→Python импорт), shell фасад знает только `deploy_orchestrator.py`. |
| `test_shell_has_severity_exit` | `FAILED=()` array, `module-metadata` severity loop, exit codes | **FAIL** | Severity aggregation теперь в Python (`deploy_orchestrator.py`). Shell просто exec'ит Python и пробрасывает exit code через `exec`. |
| `test_shell_has_sudoers_orphan_post_deploy` | `sudoers_generator.py --action batch-generate`, `orphan_reconciler.py` | **FAIL** | Post-deploy логика в Python. Shell не содержит этих вызовов. |

**Предлагаемый фикс:** Тесты должны проверять НОВЫЙ контракт тонкого фасада:
1. `deploy-modules.sh` содержит `exec python3` + `deploy_orchestrator.py` (единая точка делегирования)
2. `deploy-modules.sh` НЕ содержит прямых вызовов 5 Python-модулей (они внутри orchestrator)
3. Severity exit: shell пробрасывает exit code через `exec`, проверять в `deploy_orchestrator.py` (отдельный тест)
4. Sudoers/orphan/context_overlay: добавить тест `deploy_orchestrator.py` на импорт всех 5 модулей (contract test Python-уровня)

**Конкретные изменения в test_shell_facade_contract.py:**

```python
# S3: заменить проверку 5 модулей на проверку единой делегации
def test_shell_has_python_delegation(caplog):
    """deploy-modules.sh must delegate to deploy_orchestrator.py (Plan 100 thin facade)."""
    content = _read_deploy_modules_shell()
    has_delegation = "deploy_orchestrator.py" in content
    assert has_delegation, "deploy-modules.sh must exec deploy_orchestrator.py"
    # Индивидуальные модули — внутри orchestrator, проверяется в test_orchestrator_imports.py

# S4: заменить FAILED array на exec exit-code проброс
def test_shell_has_severity_exit(caplog):
    """deploy-modules.sh thin facade: exec propagates Python exit {0,1,2}."""
    content = _read_deploy_modules_shell()
    assert "exec python3" in content, "shell must use exec for exit code propagation"
    # Severity логика — в deploy_orchestrator.py, тестируется отдельно

# S5: заменить context_overlay.py на проверку что --node-yaml проброшен в orchestrator
def test_shell_has_context_overlay(caplog):
    """deploy-modules.sh passes --node-yaml to orchestrator (context overlay inside)."""
    content = _read_deploy_modules_shell()
    assert "deploy_orchestrator.py" in content
    assert "--node-yaml" in content  # ✅ уже присутствует (строка 45 deploy-modules.sh)

# S6: заменить sudoers/orphan на проверку orchestrator delegation
def test_shell_has_sudoers_orphan_post_deploy(caplog):
    """Post-deploy logic lives in deploy_orchestrator.py (Plan 100)."""
    content = _read_deploy_modules_shell()
    assert "deploy_orchestrator.py" in content
    # Sudoers/orphan внутри orchestrator — тестируется в test_orchestrator_contract.py
```

---

## Section 5 — Runtime Validation (Phase 5)

### Test Results

```
tests/unit/test_remote_executor.py ........... 11 passed
tests/unit/test_context_promoter.py ............ 12 passed
tests/test_node_lifecycle_static.py ........... 11 passed
tests/test_bootstrap_auto.py ............... 15 passed
tests/test_unit_age_key_env_only.py ... 3 passed
─────────────────────────────────────────────────
TOTAL: 52 passed, 0 failed (unit + integration)
```

### LDD Trace Analysis

Все тесты содержат IMP:9 трассировку через caplog:
- `test_ldd_imp9_logs_on_success` (remote_executor): IMP:9 лог `[IMP:9][ssh_exec][exec] OK: root@host` присутствует
- `test_audit_logging_imp9` (context_promoter): IMP:9 лог присутствует
- Anti-Illusion вердикт: **PASS** — IMP:9 business-logic логи подтверждены для всех успешных сценариев

### Shell Syntax

`bash -n` проверка заблокирована правилами проекта (не `python3 -m pytest*`). Статический анализ структуры: все 4 скрипта имеют корректный shebang, `set -euo pipefail`, сбалансированные кавычки/скобки, валидные source-пути (проверено grep'ом реального существования source'имых файлов).

### Acceptance Criteria Verification

#### Plan 101

| AC | Описание | Статус | Evidence |
|----|----------|:------:|----------|
| AC1 | remote_executor.py с execute_* + CLI | ✅ PASS | Файл 265 LOC. CLI: `execute-update|execute-converge|execute-reconcile` subcommands. |
| AC2 | remote-cmd.sh ≤ 60 LOC | ✅ PASS | `wc -l` → 60 строк (ровно ≤60). build-ssh-cmd.sh = 122 LOC. |
| AC3 | execute_remote_update идентичен | ✅ PASS | Python: resolve → VPS detect → sync-core → ssh_exec. Exit 0/1/2. 5 unit tests pass. |
| AC4 | execute_remote_converge идентичен | ✅ PASS | Python: resolve → prepare opts → ssh exec (без sync-core). 1 unit test pass. |
| AC5 | execute_remote_reconcile идентичен | ✅ PASS | --reconcile flag detection + delegate converge. execute_remote_reconcile_entrypoint удалён (0 grep hits). 1 unit test pass. |
| AC6 | DRY_RUN сохранён | ✅ PASS | --dry-run печатает команды, не вызывает ssh/rsync, exit 0. 1 unit test pass. |
| AC7 | AGENTS.md обновлён | ✅ PASS | bootstrap/AGENTS.md:249-258 таблица: remote-cmd.sh 266→60, build-ssh-cmd.sh ~100, remote_executor.py ~200. @rationale в remote-cmd.sh:8 обновлён: 672→~60 LOC. |
| AC8 | TRAP сохранены | ✅ PASS | P0 (VPS self-SSH) + P4 (ssh_exec) → remote_executor.py:21-32. P1 (PLATFORM_ROOT) + P2 (ci_deploy_key) → build-ssh-cmd.sh:26-51. D3 printf %q → build-ssh-cmd.sh (32 вызова). |
| AC9 | Обратная совместимость | ✅ PASS | bootstrap.sh:35 → source build-ssh-cmd.sh. node-update.sh + converge.sh → source remote-cmd.sh (API неизменен). 29 integration tests pass. |

#### Plan 103

| AC | Описание | Статус | Evidence |
|----|----------|:------:|----------|
| AC1 | context_promoter.py с 5 функциями + CLI | ✅ PASS | Файл 359 LOC. check_ssh_available, promote_via_ssh, promote_via_https, verify_mirror, promote_context + main(). |
| AC2 | GIT_ASKPASS token handling — subprocess env | ✅ PASS | tempfile с literal `${GIT_MIRROR_TOKEN}` (not f-string). finally: os.unlink. subprocess.run с env={**os.environ, "GIT_ASKPASS": path}. |
| AC3 | Shell фасад ≤ 40 LOC | ✅ PASS | context-promote.sh = 32 строки. PYTHONPATH export (строка 21). exec python3. |
| AC4 | SSH primary идентичен | ✅ PASS | Те же флаги: `ssh -T -o ConnectTimeout=10 -o BatchMode=yes git@github.com`. Тест test_check_ssh_available_success PASS. |
| AC5 | HTTPS fallback идентичен | ✅ PASS | GIT_ASKPASS скрипт → git push --mirror https://. Тест test_promote_via_https_success PASS. |
| AC6 | Mirror verification идентична | ✅ PASS | git rev-parse HEAD == git ls-remote HEAD. Тесты test_verify_mirror_match/mismatch PASS. |
| AC7 | Токен не в process list | ✅ PASS | test_promote_via_https_token_not_in_argv: assert токен не в subprocess.run args. URL чистый. |
| AC8 | make context-promote без изменений | ✅ PASS | deploy.mk:95 — вызов не менялся. entrypoint-manifest.yaml:54 delegates_to обновлён. |
| AC9 | TRAP сохранены | ✅ PASS | TRAP[DECISION] 2026-07-18 (SSH primary, HTTPS fallback) → context_promoter.py:31-36. |

---

## Section 6 — Config Sync (Phase 6)

### Env Variable Propagation

| Variable | .env | conftest.py SMOKE_ENV | CI workflows | Status |
|----------|:----:|:---------------------:|:------------:|:------:|
| `GIT_MIRROR_TOKEN` | ✅ (если задан) | — (not in SMOKE_ENV) | — (runtime only) | ✅ Не требуется для CI — runtime env на машине оператора |

### Compose Override Consistency

Не применимо — планы не затрагивают docker-compose файлы.

### Network/Volume Consistency

Не применимо — планы не затрагивают сети/volumes.

---

## Cross-Plan Diagnostic: test_shell_facade_contract.py

### Обнаружение

Файл `tests/unit/test_shell_facade_contract.py` → 4 FAIL из 6 тестов. Все 4 FAIL проверяют структурные паттерны в `deploy-modules.sh`, которые были удалены **планом 100** (deploy-modules.sh 1664→50 LOC thin facade). Планы 101 и 103 НЕ затрагивают deploy-modules.sh — эти падения ПРЕДСУЩЕСТВУЮЩИЕ и не вызваны верифицируемыми планами.

### Детальный анализ

| Тест | Assertion | Текущий deploy-modules.sh (50 LOC) | Где логика сейчас |
|------|-----------|-------------------------------------|-------------------|
| `test_shell_has_context_overlay` | `"context_overlay.py" in content` + `"--action ensure"` | НЕТ — shell знает только `deploy_orchestrator.py` | `deploy_orchestrator.py` импортирует `context_overlay` |
| `test_shell_has_python_delegation` | 5 модулей (`context_overlay`, `secrets_validator`, `docker_orchestrator`, `sudoers_generator`, `orphan_reconciler`) в shell | Только `deploy_orchestrator.py` (строка 44) | Все 5 импортируются внутри `deploy_orchestrator.py` |
| `test_shell_has_severity_exit` | `FAILED=()` array, severity loop, exit 0/2 | Shell использует `exec python3` → exit code пробрасывается автоматически | `deploy_orchestrator.py` содержит severity aggregation |
| `test_shell_has_sudoers_orphan_post_deploy` | `sudoers_generator.py --action batch-generate`, `orphan_reconciler.py` в shell | НЕТ — shell не содержит post-deploy логики | Внутри `deploy_orchestrator.py` |

### Кто виноват

**План 100** (deploy-modules.sh thin facade migration). Тесты написаны под старый 1664-LOC shell и не были адаптированы при миграции. План 100 был APPROVED QA (report 112), но test_shell_facade_contract.py не был упомянут в том отчёте.

### Точный предлагаемый фикс

Не исправлять в рамках данного QA (правило: delegate, don't fix). Предлагаемые изменения:

1. **S3 (test_shell_has_python_delegation):** Заменить проверку 5 модулей на проверку единой делегации `deploy_orchestrator.py`. 5 модулей → новый тест в `tests/unit/test_orchestrator_imports.py` (greps `deploy/deploy_orchestrator.py` на импорт всех 5).

2. **S4 (test_shell_has_severity_exit):** Заменить `FAILED=()` array на проверку `exec python3` + `deploy_orchestrator.py`. Severity логика → отдельный тест orchestrator.

3. **S5 (test_shell_has_context_overlay):** Упростить до проверки что `--node-yaml` проброшен в orchestrator (уже присутствует). Context overlay вызов внутри orchestrator.

4. **S6 (test_shell_has_sudoers_orphan_post_deploy):** Упростить до проверки делегирования orchestrator. Sudoers/orphan внутри orchestrator.

**Impact:** 4 теста изменяются, 2 теста (S1 arg_parsing, S2 provisioner) остаются без изменений. 0 новых FAIL вводится.

---

## Cross-Plan Checks (флаги кодеров)

### (а) manifest lib audit.sh consumers → context-promote.sh

**DRIFT-1 (HIGH):** `core/entrypoint-manifest.yaml:541` — `consumers:` список для `audit.sh` включает `core/entrypoints/context-promote.sh`. После DevPlan 103 фасад context-promote.sh больше НЕ source'ит audit.sh (подтверждено: grep `source.*audit` → 0 hits). Audit теперь через Python `shared/audit_logger.write_audit_entry()`.

**Нужно ли править:** ДА. Запись устарела. Удалить строку `- core/entrypoints/context-promote.sh` из consumers (строка 541).

### (б) deploy.mk:88 комментарий

```
##   Delegates to core/entrypoints/context-promote.sh → copies to <context>/ai-platform
```

**DRIFT-2 (INFO):** Формулировка «copies to» — устаревшая. Новый механизм: `git push --mirror`. Предлагаемое обновление:
```
##   Delegates to core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py → git push --mirror to <context>/ai-platform
```

---

## Semantic Verdict

### Plan 101 — remote-cmd.sh Strangler-Fig

**APPROVED** — все 9 AC выполнены. 23 unit-теста + 29 интеграционных — PASS. TRAP сохранены. D3 printf %q неприкосновенен. Dead code удалён (execute_remote_reconcile_entrypoint, _resolve_and_extract → 0 grep hits). AGENTS.md обновлён корректно.

Единственное замечание: DRIFT-3 (MINOR) — @scope в remote-cmd.sh:6 упоминает bootstrap.sh, который больше не source'ит remote-cmd.sh напрямую. Косметическое.

### Plan 103 — context-promote.sh Python migration

**APPROVED** — все 9 AC выполнены. 12 unit-тестов PASS. GIT_MIRROR_TOKEN безопасность подтверждена (literal string, не f-string; token не в argv/URL). Shell фасад 32 LOC ≤40. entrypoint-manifest.yaml delegates_to обновлён.

Замечания:
- DRIFT-1 (HIGH) — manifest audit.sh consumers → устаревшая запись context-promote.sh. Требует исправления, но не блокирует merge (не нарушает работу, только метаданные).
- DRIFT-2 (INFO) — deploy.mk:88 комментарий устарел формулировкой.

### Cross-Plan: test_shell_facade_contract.py

**DEGRADED** — 4 FAIL из 6 тестов. Вызваны Планом 100 (deploy-modules.sh thin facade), НЕ планами 101/103. Тесты требуют адаптации под новый контракт тонкого фасада (один `deploy_orchestrator.py` вместо пяти отдельных модулей). Блокирует make gate MODE=fast.

### Общий вердикт

| План | Вердикт | Блокирующие находки |
|------|:-------:|---------------------|
| **101** | **APPROVED** | Нет (1× MINOR cosmetic) |
| **103** | **APPROVED-WITH-FIX** | DRIFT-1 (HIGH): manifest consumers требует правки |
| **cross-plan** | **NEEDS-FIX (Plan 100 artifact)** | 4 FAIL в test_shell_facade_contract.py |

**Сводка находок по severity:**

| # | Severity | Что | Где | Предлагаемый фикс |
|---|----------|-----|-----|-------------------|
| F1 | **HIGH** | manifest audit.sh consumers → context-promote.sh устарела | entrypoint-manifest.yaml:541 | Удалить `- core/entrypoints/context-promote.sh` из consumers |
| F2 | **MEDIUM** | 4 FAIL в test_shell_facade_contract.py | tests/unit/test_shell_facade_contract.py | Адаптировать тесты под новый контракт тонкого фасада (см. Section 5) |
| F3 | **MINOR** | @scope в remote-cmd.sh упоминает bootstrap.sh | remote-cmd.sh:6 | Обновить @scope |
| F4 | **INFO** | deploy.mk:88 комментарий «copies to» | makefiles/deploy.mk:88 | Обновить на «git push --mirror» |

**Рекомендация:** Мержить оба плана. F1 и F2 исправить в отдельном follow-up (одним Coder-таском). F3 и F4 — оппортунистически при следующем касании файлов.

$END_VERIFICATION_REPORT
