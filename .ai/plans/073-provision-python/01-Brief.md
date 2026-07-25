# Brief 073 — Provision Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Migrate provision-environment.sh (442 LOC, 13 inline python3 calls) to Python module `core/internal/provisioner.py`.
- **DESCRIPTION:** Eliminate all inline python3 from shell. Reduce shell to <50 LOC thin wrapper. Python: PlatformEnv/NetworkConfig/VolumeConfig dataclasses, PyYAML parsing, per-scope provision functions with idempotency checks. 18 unit tests planned.
- **RATIONALE:** Worst shell→Python violator in wave 3 of Strangler decomposition.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 073.
- **IMPACTS:** provision-environment.sh, deploy-modules.sh, state_machine.py, helpers.mk, modules.mk, entrypoint-manifest.yaml.
- **REQUIRES:** Nothing (except plan revision before Wave 2).

## Current Status (Revised 2026-07-25)
- **Verdict:** READY — Plan revised (03-DevPlan-fix.md), all F1-F7 findings addressed.
- **Implementation:** 0% (не начата).
- **Test baseline:** 28/28 unit tests pass, 2/2 smoke tests pass.
- **Revised DevPlan:** `03-DevPlan-fix.md` (replaces `01-DevPlan.md`)

## Key Findings (from 02-VerificationReport.md) — All Addressed
- **F1 (HIGH):** ✅ **FIXED** — §5.1-5.2: двухуровневая тестовая архитектура (unit + integration), 48 тестов total. Существующие тесты сохраняются как subprocess-level integration coverage.
- **F2 (HIGH):** ✅ **FIXED** — §6.2: deploy-modules.sh и state_machine.py добавлены как consumers (интерфейс совместим, изменений не требуют).
- **F3 (MEDIUM):** ✅ **FIXED** — §10: Wave 2 = TASK-2 only, Wave 3 = TASK-3 + TASK-4 parallel, Wave 4 = TASK-5 + TASK-6.
- **F4 (MEDIUM):** ✅ **FIXED** — §8.1: block name `[provision]` (не `[provisioner]`) для обратной совместимости с 20 test-assertions.
- **F5 (MEDIUM):** ✅ **FIXED** — §11: realistic LOC estimates (wrapper ~50-55, provisioner.py ~340, tests ~250-300).
- **W6:** ✅ **FIXED** — §6.3 + TASK-6: entrypoint-manifest.yaml delegates_to chain обновлён.
- **W7:** ✅ **FIXED** — §5.3: added note о реальном platform-env.yaml (13 networks, 17 volumes) с рекомендацией exact-count assertions в integration tests.
