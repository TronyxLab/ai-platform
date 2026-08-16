"""Static layer: inline-secrets detector tests (DevPlan 163 W-C C3).

# GREP_SUMMARY: test-static inline-secrets secrets-env-patterns source_secrets_env shared-only R5 086
# STRUCTURE: ▶ synthetic P1 (for-line open(secrets)) → RED | ▶ synthetic P3 (set -a; source secrets)
#            → RED | ▶ R5-оригинал 086 (source_secrets_env вызов) → RED | ▶ control: shared-импорт → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора inline_secrets (DevPlan 163 W-C C3): позитивные тесты на
##           синтетические нарушения (P1 for-line-итерация open(secrets); P3 shell set -a;
##           source secrets), R5-негатив на ОРИГИНАЛЬНЫЙ вход гейта (086:
##           source_secrets_env вызов — DevPlan 086 migration target), PASS-контроль
##           (импорт shared/secrets_env_parser — легитимен).
## @scope    Native imports; probe-файлы в tmp_path (для деревьев без core/ детектор
##           сканирует все *.py/*.sh рекурсивно).
## @invariants
##   - P1 for-line-итерация secrets.env → RED
##   - P3 shell set -a; source secrets → RED
##   - P2 source_secrets_env (имя) → RED
##   - shared-модуль не триггерит (эксклюзия скана)
## @rationale R5 anti-survivorship (086): 7 inline-парсеров консолидированы в
##            shared/secrets_env_parser.py; детектор не даёт регрессии.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.inline_secrets import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic P1: for-line open(secrets) → RED
# · Scenario: probe .py с `for line in open("/opt/run/secrets.env")` — Python
# ·   file-iteration парсинг (паттерн P1 гейта 086) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: inline-secrets гейт отменяется
@ldd_trajectory
def test_inline_secrets_p1_for_line_open_detected(caplog, tmp_path) -> None:
    """Synthetic positive: P1 for-line итерация по secrets.env детектируется."""
    probe = tmp_path / "_probe_p1.py"
    probe.write_text(
        'for line in open("/opt/platform/run/secrets.env"):\n    k, v = line.split("=")\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_p1" in f.file]
    assert hits, "R5 FAIL: P1 for-line open(secrets) not detected"
    assert "[P1]" in hits[0].message
    logger.info("[IMP:9][test_inline_secrets] P1 for-line RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · synthetic P3: set -a; source secrets → RED
# · Scenario: probe .sh с `set -a; source /opt/run/secrets.env` — shell batch-export
# ·   паттерн (P3) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: inline-secrets гейт отменяется
@ldd_trajectory
def test_inline_secrets_p3_set_a_source_detected(caplog, tmp_path) -> None:
    """Synthetic positive: P3 shell set -a; source secrets детектируется."""
    probe = tmp_path / "_probe_p3.sh"
    probe.write_text(
        "#!/usr/bin/env bash\nset -a; source /opt/run/secrets.env; set +a\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_p3" in f.file]
    assert hits, "R5 FAIL: P3 set -a; source secrets not detected"
    assert "[P3]" in hits[0].message
    logger.info("[IMP:9][test_inline_secrets] P3 set -a source RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал 086: source_secrets_env вызов → RED
# · Scenario: probe .py с `source_secrets_env(env_path)` — функция-имя
# ·   (DevPlan 086 migration target, паттерн P2 гейта) → RED
# · Last fail: DevPlan 086 — 7 inline-парсеров (source_secrets_env и др.) размазаны
# · Remove if: inline-secrets гейт отменяется
@ldd_trajectory
def test_inline_secrets_negative_original_086_input(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход 086 — source_secrets_env вызов."""
    probe = tmp_path / "_probe_p2.py"
    probe.write_text('source_secrets_env("/opt/run/secrets.env")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_p2" in f.file]
    assert hits, "R5 FAIL: source_secrets_env (086 original class) not detected"
    assert "[P2]" in hits[0].message
    logger.info("[IMP:9][test_inline_secrets] R5 086 source_secrets_env RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · импорт shared/secrets_env_parser → PASS
# · Scenario: probe с `from core.internal.shared.secrets_env_parser import parse` — канонический
# ·   импорт (086-консолидация) → 0 RED; shared/ директория исключается из скана
# · Last fail: N/A (control — shared-импорт легитимен)
# · Remove if: inline-secrets гейт отменяется
@ldd_trajectory
def test_inline_secrets_shared_import_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: импорт shared/secrets_env_parser (086 канон) не RED."""
    probe = tmp_path / "_probe_ok.py"
    probe.write_text(
        "from core.internal.shared.secrets_env_parser import parse\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_ok" in f.file]
    assert not hits, f"PASS-control FAIL: shared import flagged: {hits}"
    logger.info("[IMP:9][test_inline_secrets] shared/secrets_env_parser import not flagged")
