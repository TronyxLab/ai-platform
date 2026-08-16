"""Static layer: local-path-remote detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static local-path-remote passthrough build-ssh-cmd execute-remote forbidden-vars R5 FL6
# STRUCTURE: ▶ synthetic build_update_ssh_cmd + $NODE_CONFIGS_DIR → RED | ▶ R5-оригиналы FL6
#            (PASSTHROUGH_ARGS+=$AGE_SECRET_KEY_FILE; execute_remote --age-secret-key-file) → RED
#            → ▶ control (passthrough без запрещённой переменной) → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора local_path_remote (DevPlan 163 W-C C2): позитивный тест на
##           синтетическое нарушение (build_update_ssh_cmd + $NODE_CONFIGS_DIR), R5-негативы
##           на ОБА оригинальных входа FL6 (PASSTHROUGH_ARGS+=$AGE_SECRET_KEY_FILE;
##           execute_remote_converge --age-secret-key-file "${PLATFORM_ROOT}/..."), PASS-контроль
##           (passthrough без запрещённой переменной не RED).
## @scope    Native imports; probe-файлы в tmp_path (для деревьев без core/ детектор
##           сканирует root.rglob("*")).
## @invariants
##   - passthrough-конструкция ∧ запрещённая переменная-путь на ОДНОЙ строке → RED
##   - PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}") → RED (оригинальный вход FL6)
##   - execute_remote_* --age-secret-key-file "${PLATFORM_ROOT}/..." → RED (оригинал FL6)
##   - passthrough без запрещённой переменной/флага → PASS
## @rationale R5 anti-survivorship (FL6, RC 121 прод): --age-secret-key-file уходил в
##            remote passthrough (bootstrap.sh:48-50) — локальный ключ читался на ноде.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.local_path_remote import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · POSITIVE · synthetic build_update_ssh_cmd + $NODE_CONFIGS_DIR → RED
# · Scenario: строка `build_update_ssh_cmd "node" "$NODE_CONFIGS_DIR/conf"` — passthrough
# ·   конструкция + запрещённая переменная-путь (NODE_CONFIGS_DIR) → RED
# · Last fail: N/A (синтетический вариант)
# · Remove if: FL6 локальный-путь→remote гейт отменяется
@ldd_trajectory
def test_local_path_remote_build_ssh_cmd_detected(caplog, tmp_path) -> None:
    """Synthetic positive: build_update_ssh_cmd + $NODE_CONFIGS_DIR детектируется."""
    probe = tmp_path / "_probe_build.sh"
    probe.write_text(
        '#!/usr/bin/env bash\nbuild_update_ssh_cmd "node1" "$NODE_CONFIGS_DIR/conf"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_build" in f.file]
    assert hits, "R5 FAIL: build_update_ssh_cmd + $NODE_CONFIGS_DIR not detected"
    logger.info("[IMP:9][test_local_path_remote] synthetic build_ssh_cmd RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал FL6 PASSTHROUGH_ARGS+=$AGE_SECRET_KEY_FILE → RED
# · Scenario: `PASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}")` — точный вход RC 121
# ·   false-lead 6 (AGE_SECRET_KEY_FILE как аргумент в passthrough)
# · Last fail: RC 121 false-lead 6 — AGE_SECRET_KEY_FILE в passthrough (bootstrap.sh:48-50)
# · Remove if: гейт локальный-путь→remote отменяется
@ldd_trajectory
def test_local_path_remote_negative_passthrough_age_key(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход FL6 — PASSTHROUGH_ARGS+=(${AGE_SECRET_KEY_FILE}) → RED."""
    probe = tmp_path / "_probe_pass.sh"
    probe.write_text(
        '#!/usr/bin/env bash\nPASSTHROUGH_ARGS+=("${AGE_SECRET_KEY_FILE}")\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_pass" in f.file]
    assert hits, "R5 FAIL: PASSTHROUGH_ARGS+=(${AGE_SECRET_KEY_FILE}) not detected"
    logger.info("[IMP:9][test_local_path_remote] R5 passthrough RED: %s", hits[0])


# 🧪 TRAP[TEST] · NEGATIVE (R5) · оригинал FL6 execute_remote --age-secret-key-file → RED
# · Scenario: `execute_remote_converge "node1" --age-secret-key-file "${PLATFORM_ROOT}/age-key.txt"`
# ·   — путь AGE-ключа как remote-флаг (ловушка node-lifecycle.sh:28)
# · Last fail: RC 121 false-lead 6 — путь AGE-ключа как remote-флаг (node-lifecycle.sh:28)
# · Remove if: гейт локальный-путь→remote отменяется
@ldd_trajectory
def test_local_path_remote_negative_remote_flag(caplog, tmp_path) -> None:
    """R5 negative: оригинальный вход FL6 — execute_remote_* с --age-secret-key-file → RED."""
    probe = tmp_path / "_probe_remote_flag.sh"
    probe.write_text(
        '#!/usr/bin/env bash\nexecute_remote_converge "node1" --age-secret-key-file "${PLATFORM_ROOT}/age-key.txt"\n',
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_remote_flag" in f.file]
    assert hits, "R5 FAIL: execute_remote_converge --age-secret-key-file not detected"
    logger.info("[IMP:9][test_local_path_remote] R5 remote-flag RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · passthrough без запрещённой переменной → PASS
# · Scenario: `PASSTHROUGH_ARGS+=("--verbose")` — passthrough без $VAR-пути/флага → 0 RED
# · Last fail: N/A (control — passthrough-конструкция сама по себе не нарушение)
# · Remove if: гейт локальный-путь→remote отменяется
@ldd_trajectory
def test_local_path_remote_passthrough_without_path_allowed(caplog, tmp_path) -> None:
    """PASS-контроль: passthrough без запрещённой переменной/флага не RED."""
    probe = tmp_path / "_probe_clean.sh"
    probe.write_text('#!/usr/bin/env bash\nPASSTHROUGH_ARGS+=("--verbose")\n', encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "_probe_clean" in f.file]
    assert not hits, f"PASS-control FAIL: clean passthrough flagged: {hits}"
    logger.info("[IMP:9][test_local_path_remote] passthrough without forbidden path not flagged")
