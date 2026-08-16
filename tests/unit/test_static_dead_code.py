"""Static layer: dead-code detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static dead-code shell reachability call-graph orphan entrypoint internal R5 5G3
# STRUCTURE: ▶ orphan entrypoint (synthetic) → RED | ▶ orphan internal (R5 5G3-сценарий) → RED
#            → ▶ source-цепочка (manifest seed → source lib.sh) → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора dead_code (DevPlan 163 W-C C2): позитивный тест на
##           синтетический orphan-entrypoint, R5-негатив на сценарий исходного гейта
##           (orphan в core/internal/ — LAST_FAIL: core/internal/validate/lint.sh,
##           core/internal/bootstrap/tls.sh), PASS-контроль source-достижимости (BFS).
## @scope    Native imports; probe-деревья в tmp_path с layout core/entrypoints|internal
##           (Zero Hardcode Rule); манифест/seed-цепочка — только в контрольном тесте.
## @invariants
##   - shebang .sh под core/entrypoints/ без caller'а → RED (synthetic)
##   - shebang .sh под core/internal/ без caller'а → RED (R5: внутренний orphan-класс гейта)
##   - Скрипт, зарегистрированный в манифесте delegates_to И source-достижимый → PASS
## @rationale R5 (Test Honesty): детектор обязан ловить исходный класс дефекта гейта
##            TASK-5G3 (orphan-скрипты без живого caller'а); контроль — против ложных
##            RED на достижимых через source/./exec скриптах.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.dead_code import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic orphan entrypoint → RED
# · Scenario: core/entrypoints/orphan.sh (shebang) в дереве без манифеста/seed-ссылок —
# ·   нет живого caller'а → DEAD_CODE
# · Last fail: N/A (синтетический вариант)
# · Remove if: dead-code gate superseded (фаза 2 миграции)
@ldd_trajectory
def test_dead_code_orphan_entrypoint_detected(caplog, tmp_path) -> None:
    """Synthetic positive: unreferenced entrypoint детектируется как DEAD_CODE."""
    entrypoint = tmp_path / "core" / "entrypoints" / "orphan-entrypoint.sh"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/usr/bin/env bash\necho orphan\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "orphan-entrypoint.sh" in f.file]
    assert hits, "R5 FAIL: orphan entrypoint not detected as dead code"
    assert hits[0].rule == "dead-code"
    assert "DEAD_CODE" in hits[0].message
    logger.info("[IMP:9][test_dead_code] orphan entrypoint RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · orphan internal-скрипт → RED (класс 5G3)
# · Scenario: core/internal/orphan.sh (shebang) без caller'а — класс дефекта исходного
# ·   гейта (LAST_FAIL: core/internal/validate/lint.sh, core/internal/bootstrap/tls.sh)
# · Last fail: core/internal/validate/lint.sh + core/internal/bootstrap/tls.sh (5G3)
# · Remove if: dead-code gate superseded
@ldd_trajectory
def test_dead_code_negative_orphan_internal(caplog, tmp_path) -> None:
    """R5 negative: orphan internal-скрипт (исходный класс 5G3) детектируется."""
    orphan = tmp_path / "core" / "internal" / "orphan-internal.sh"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("#!/usr/bin/env bash\necho orphan\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "orphan-internal.sh" in f.file]
    assert hits, "R5 FAIL: orphan internal script (5G3 defect class) not detected"
    logger.info("[IMP:9][test_dead_code] R5 orphan internal RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · source-цепочка (manifest seed → source lib.sh) → PASS
# · Scenario: core/entrypoint-manifest.yaml delegates_to root.sh (seed); root.sh source
# ·   lib.sh → lib.sh достижим по BFS → 0 DEAD_CODE находок
# · Last fail: N/A (control — BFS не должен давать ложных RED на source-достижимых)
# · Remove if: dead-code gate superseded
@ldd_trajectory
def test_dead_code_sourced_script_reachable(caplog, tmp_path) -> None:
    """PASS-контроль: скрипт, зарегистрированный в манифесте и source-достижимый, не RED."""
    core_dir = tmp_path / "core"
    (core_dir / "entrypoints").mkdir(parents=True)
    (core_dir / "entrypoint-manifest.yaml").write_text(
        "entrypoints:\n- make_target: probe\n  delegates_to: core/entrypoints/root.sh\n",
        encoding="utf-8",
    )
    (core_dir / "entrypoints" / "root.sh").write_text("#!/usr/bin/env bash\nsource ./lib.sh\n", encoding="utf-8")
    (core_dir / "entrypoints" / "lib.sh").write_text("#!/usr/bin/env bash\nprobe_helper(){ :; }\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if f.file.startswith("core/entrypoints/")]
    assert not hits, f"PASS-control FAIL: reachable scripts flagged dead: {hits}"
    logger.info("[IMP:9][test_dead_code] manifest seed + source-chain reachable (0 findings)")
