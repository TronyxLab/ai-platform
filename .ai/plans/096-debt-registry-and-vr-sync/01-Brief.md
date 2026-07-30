$START_BRIEF
# Brief 096 — Debt Registry & VerificationReports Sync

## $ARTIFACT_CONTRACT
- **PURPOSE:** Актуализировать tracking-артефакты: создать Debt-registry для незакрытых долгов, обновить VerificationReports планов 073/074/076/078 до DONE (они устарели — код реально сделан). Без этого прогресс невидим и создаёт иллюзию «идём по кругу».
- **DESCRIPTION:** (1) Создать `.ai/debt/096-Residual-Debt.md` с реестром: незакрытые AC 087/088/089, косметические gate-баги, пробелы выявленные аудитами. (2) Обновить VR планов, где код сделан но VR устарел. (3) Зафиксировать факт «3 экспертизы работали по устаревшим данным» — lesson learned.
- **RATIONALE:** Debt registry сейчас содержит только `011-Debt.md` (от июля 18). Нет учёта 085-RC3 blockers, нет учёта косметики (region-mismatch, ruff). VR 073/074/076/086 говорят «0% implemented» — но git log показывает коммиты реализации. Отсюда ощущение хождения по кругу: прогресс есть, но не отслежен.
- **ACCEPTANCE_CRITERIA:** `.ai/debt/096-Residual-Debt.md` существует и покрывает все незакрытые долги; VR устаревших планов имеют пометку «SUPERSEDED — см. git history» или обновлены до DONE; `make gate MODE=fast` зелёный (косметика пофикшена).
- **IMPLEMENTS:** Закрытие diagnosis-Б «Debt registry пуста» (1-я экспертиза) + GAP documentation debt (3-я экспертиза).
- **IMPACTS:** `.ai/debt/` (NEW `096-Residual-Debt.md`), `.ai/plans/{073,074,076,078,086}/` (VR updates), `tests/test_contract_deploy_ssh.py` (cosmetic fix).
- **REQUIRES:** Ничего (можно делать первым, параллельно с 091).

## Current Status (Audit 2026-07-30)
- **Debt registry:** только `.ai/debt/011-Debt.md` (2026-07-18). Нет registry для 085-RC3, нет для 087-090 residual.
- **Stale VR (verified против git):**
  - VR 073-provision: «0% implemented» → но `provisioner.py` существует, `provision-environment.sh` мигрирован (commit `c8100e4`).
  - VR 074-monitoring: «0%» → но `monitoring_config_renderer.py` (931 LOC) существует (commit `dfbeb10`).
  - VR 076-reconcile: «0%, CRITICAL design flaw» → но `reconciler_projects.py` (746 LOC) существует.
  - VR 078-secrets: «не запущен» → но Phase A сделана (commit `781b3e1`).
  - VR 086-secrets-parser: «DRIFTED» → но закоммичен (commit `119da0f`).
- **Cosmetic gate blockers (verified):**
  - `tests/test_contract_deploy_ssh.py`: лишний `#endregion` (строка 964), 2 unused imports (pytest, conftest.assert_ldd_stderr).
  - `core/internal/bootstrap/lifecycle/phases.py`: 19 невалидных `# noqa` директив (без кодов).

## Key Findings (verificated — debunking экспертиз)
- **«102 pre-existing test failures»** (1-я экспертиза) — **НЕ подтверждено**. Gate чистый кроме 2 косметических багов.
- **«deploy-project.sh в 2 копиях»** (все 3 экспертизы) — **FALSE**, удалён полностью (089).
- **«WIP 087 не закоммичен»** (1-я экспертиза) — **FALSE**, закоммичен (`f28a0a9`).
- **Lesson:** 3 экспертизы работали по устаревшим snapshot-данным (до коммитов 087-090). Это зафиксировать в debt как `LESSON_LEARNED`.

## Required Actions

### Wave 1: косметика (разблокирует gate)
1. `tests/test_contract_deploy_ssh.py`: удалить лишний `#endregion` (строка 964), удалить 2 unused imports.
2. `phases.py`: исправить 19 невалидных `# noqa` → добавить коды (например `# noqa: E402`) или удалить.
3. `make gate MODE=fast` → зелёный.

### Wave 2: debt registry
4. Создать `.ai/debt/096-Residual-Debt.md` с разделами:
   - **OPEN-087/088/089**: незакрытые AC (детали из VR).
   - **COSMETIC**: region-mismatch (fixed Wave 1), noqa warnings (fixed Wave 1).
   - **SHELL-RESIDUAL**: scaffold/ 1972 LOC (→ 092), validate/checkpoint (→ 093), template-engine (→ 094).
   - **085-RC3-BLOCKERS**: C1 (shared/ ad-hoc), C2 (gate invisible), C3 (watchdog hardcoded paths) — если ещё актуально.
   - **LESSON_LEARNED**: экспертизы по snapshot-данным устаревают за 1 коммит. Верификация против git log обязательна.

### Wave 3: VR sync (stale → SUPERSEDED)
5. Для планов 073/074/076/078/086: добавить в последний VR пометку `**STATUS UPDATE 2026-07-30:** SUPERSEDED — implementation committed (см. git log). См. актуальный статус в DevPlan.md / новых VR.` Не переписывать исторические VR.

### Wave 4: 085-RC3 verification
6. Проверить: актуальны ли 3 CRITICAL blockers из 085-RC3 Gap Analysis? (C1 shared/ ad-hoc — после 088 NodeYaml?, C2 gate invisible — после 090 check-manifests re-enabled?, C3 watchdog paths — после 075?).
7. Если закрыты — отметить в debt. Если нет — оставить как open.

## Verification
- `make gate MODE=fast` → зелёный.
- `.ai/debt/096-Residual-Debt.md` существует, покрывает все 4 раздела.
- Stale VR имеют пометку SUPERSEDED.
- `grep -rn "102 pre-existing\|deploy-project.sh.*дубликат" .ai/` → только в LESSON_LEARNED контексте.

## Anti-Loop Note
**Не переписывать исторические VR** (это journal). Только добавлять STATUS UPDATE. Исторический VR отражает состояние на момент написания — это валидно.

$END_BRIEF
