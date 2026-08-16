"""Static layer: sys-exit-contract detector tests (DevPlan 163 W-C C3).

# GREP_SUMMARY: test-static sys-exit-contract sys.exit main-int importability U-29 R5 AST
# STRUCTURE: ▶ synthetic sys.exit в business-функции → RED | ▶ R5-оригинал U-29 (sys.exit в
#            библиотечной функции + def main() -> None) → RED | ▶ control: sys.exit в main()/__main__
#            + main() -> int → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора sys_exit_contract (DevPlan 163 W-C C3): позитивный тест на
##           синтетическое нарушение (sys.exit вне main()), R5-негативы на ОРИГИНАЛЬНЫЕ
##           входы гейта (U-29: sys.exit в библиотечной функции + def main() -> None),
##           PASS-контроль (sys.exit в main()/__main__ блоке + main() -> int).
## @scope    Native imports; probe-файлы в tmp_path (для деревьев без core/ детектор
##           сканирует root.rglob("*.py")).
## @invariants
##   - sys.exit вне main()/__main__ → RED
##   - def main() без -> int / -> None → RED
##   - sys.exit в main() теле + __main__ блоке → PASS
##   - os._exit — не sys.exit, не триггерит
## @rationale R5 anti-survivorship (U-29): sys.exit жил в библиотечных функциях
##            (provisioner:154, deploy_engine:953) — caller не мог программно обработать.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.sys_exit_contract import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic sys.exit в business-функции → RED
# · Scenario: probe `def deploy(): sys.exit(1)` — sys.exit вне main()/__main__ → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: sys.exit-контракт отменяется
@ldd_trajectory
def test_sys_exit_contract_synthetic_business_func(caplog, tmp_path) -> None:
    """Synthetic positive: sys.exit в business-функции (вне main) детектируется."""
    probe = tmp_path / "_probe_exit.py"
    probe.write_text("import sys\n\ndef deploy():\n    sys.exit(1)\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_exit" in f.file]
    assert hits, "R5 FAIL: sys.exit outside main() not detected"
    assert "outside main" in hits[0].message
    logger.info("[IMP:9][test_sys_exit] synthetic business-func sys.exit RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал U-29: sys.exit в библиотечной функции → RED
# · Scenario: probe core/internal/probe.py с sys.exit в helper-функции — точный класс U-29
# ·   (provisioner:154, deploy_engine:953 — sys.exit жил в библиотечных функциях)
# · Last fail: DevPlan 116 B4 T6 — sys.exit в provisioner:154/deploy_engine:953
# · Remove if: sys.exit-контракт отменяется
@ldd_trajectory
def test_sys_exit_contract_negative_original_u29_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход U-29 — sys.exit в библиотечной функции."""
    probe_dir = tmp_path / "core" / "internal"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "provisioner.py"
    probe.write_text("import sys\n\ndef provision():\n    sys.exit(10)\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "provisioner.py" in f.file]
    assert hits, "R5 FAIL: sys.exit in library function (U-29 original class) not detected"
    logger.info("[IMP:9][test_sys_exit] R5 U-29 library-func sys.exit RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · def main() -> None → RED (контракт D3)
# · Scenario: probe core/internal/probe.py с `def main() -> None:` — контракт D3
# ·   (все main() -> int), точный класс гейта test_sys_exit_only_in_main_and_main_returns_int
# · Last fail: DevPlan 116 B4 T6 — main() без -> int / -> None не контрактны
# · Remove if: sys.exit-контракт отменяется
@ldd_trajectory
def test_sys_exit_contract_negative_main_returns_none(caplog, tmp_path) -> None:
    """R5 negative: def main() -> None детектируется (контракт D3: -> int)."""
    probe_dir = tmp_path / "core" / "internal"
    probe_dir.mkdir(parents=True)
    probe = probe_dir / "probe_main.py"
    probe.write_text('def main() -> None:\n    print("x")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "probe_main.py" in f.file]
    assert hits, "R5 FAIL: def main() -> None (D3) not detected"
    assert "-> int" in hits[0].message
    logger.info("[IMP:9][test_sys_exit] R5 main() -> None RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · sys.exit в main()/__main__ + main() -> int → PASS
# · Scenario: probe с def main() -> int: sys.exit(main()) в __main__ блоке → 0 RED
# ·   (канонический CLI-паттерн, contract D3)
# · Last fail: N/A (control — легитимная граница CLI)
# · Remove if: sys.exit-контракт отменяется
@ldd_trajectory
def test_sys_exit_contract_main_boundary_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: sys.exit в main()/__main__ + main() -> int не RED."""
    probe = tmp_path / "_probe_main_ok.py"
    probe.write_text(
        'import sys\n\ndef main() -> int:\n    return 0\n\nif __name__ == "__main__":\n    sys.exit(main())\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_main_ok" in f.file]
    assert not hits, f"PASS-control FAIL: CLI boundary sys.exit flagged: {hits}"
    logger.info("[IMP:9][test_sys_exit] main()/__main__ CLI boundary not flagged")
