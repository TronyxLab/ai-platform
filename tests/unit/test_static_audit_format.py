"""Static layer: audit-format detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static audit-format unified-writer direct-write pipe-format R5 U-10 shared-audit-logger
# STRUCTURE: ▶ synthetic open(audit.jsonl, "w") → RED | ▶ R5-оригинал bad_writer.py (open
#            audit.log "a" + f.write(f"[ts]...)) → RED | ▶ control read-mode audit.log → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора audit_format (DevPlan 163 W-C C2): позитивный тест на
##           синтетическое нарушение (open(audit.jsonl, "w") — write-режим), R5-негатив
##           на ОРИГИНАЛЬНЫЙ вход гейта (bad_writer.py: open(audit.log, "a") +
##           f.write(f"[{ts}]...") — точная фикстура test_negative_direct_write_detected),
##           PASS-контроль (read-режим audit.log не RED).
## @scope    Native imports; probe-файлы в tmp_path (Zero Hardcode Rule).
## @invariants
##   - open(audit.*, "a"/"w")/f.write/write_text + audit-файл вне shared → RED
##   - f.write(f"[{ts}] — free-text pipe формат → RED
##   - open(audit.log, "r") (read) → PASS
##   - shared/audit_logger.py исключается из скана
## @rationale R5 anti-survivorship (U-10): 3 writer'а с разными форматами ломали
##            observability; детектор обязан ловить точный вход, сломавший гейт.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.audit_format import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic open(audit.jsonl, "w") → RED
# · Scenario: probe `with open("/var/log/platform/audit.jsonl", "w") as f:` — write-режим
# ·   на audit.jsonl вне shared/audit_logger.py (вариант с .jsonl)
# · Last fail: N/A (синтетический вариант)
# · Remove if: audit logging superseded
@ldd_trajectory
def test_audit_format_write_mode_jsonl_detected(caplog, tmp_path) -> None:
    """Synthetic positive: open(audit.jsonl, "w") вне shared детектируется."""
    probe = tmp_path / "_probe_write.py"
    probe.write_text(
        'with open("/var/log/platform/audit.jsonl", "w") as f:\n    f.write("line\\n")\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_write" in f.file]
    assert hits, "R5 FAIL: write-mode audit.jsonl open not detected"
    assert "audit" in hits[0].message
    logger.info("[IMP:9][test_audit_format] write-mode jsonl RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал гейта bad_writer.py → RED (U-10)
# · Scenario: `open("/var/log/platform/audit.log", "a")` + `f.write(f"[{ts}] bootstrap:init
# ·   DONE | node={node}")` — точный вход test_negative_direct_write_detected (R2, U-10/D1)
# · Last fail: 2026-08-01 — 3 writer'а (shared, deploy/audit_logger.py, reporting pipe)
# · Remove if: audit logging superseded
@ldd_trajectory
def test_audit_format_negative_original_bad_writer(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход гейта (open audit.log "a" + pipe f.write) → RED."""
    probe = tmp_path / "bad_writer.py"
    probe.write_text(
        'with open("/var/log/platform/audit.log", "a") as f:\n'
        '    f.write(f"[{ts}] bootstrap:init DONE | node={node}\\n")\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "bad_writer.py" in f.file]
    assert hits, "R5 FAIL: original bad_writer.py input (U-10) not detected"
    logger.info("[IMP:9][test_audit_format] R5 original bad_writer RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · read-режим audit.log → PASS
# · Scenario: `with open("audit.log", "r") as f:` — чтение не является записью (D1) → 0 RED
# · Last fail: N/A (control — read не триггерит write-паттерны)
# · Remove if: audit logging superseded
@ldd_trajectory
def test_audit_format_read_mode_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: open(audit.log, "r") (read) не RED."""
    probe = tmp_path / "_probe_read.py"
    probe.write_text(
        'with open("/var/log/platform/audit.log", "r") as f:\n    data = f.read()\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_read" in f.file]
    assert not hits, f"PASS-control FAIL: read-mode audit.log flagged: {hits}"
    logger.info("[IMP:9][test_audit_format] read-mode audit.log not flagged")
