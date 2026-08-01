#!/usr/bin/env python3
# GREP_SUMMARY: gate audit-format unified-writer shared-audit-logger jsonl json-loads free-text-pipe direct-write audit-file
# STRUCTURE: ▶ scan core/**/*.py (write ops на audit-файлы вне shared/audit_logger.py) → ◇ scan pipe-формата (f.write "[ts]") → ◇ format check (write → json.loads построчно) → ⊕ violations → ⎋ assert 0
# region MODULE_CONTRACT
## @purpose  Gate R2 (DevPlan 116 B11 T2, U-10/D1): единый audit-writer enforced.
##           Проверяет: (1) 0 прямых open(..., "a"/"w")/f.write/write_text на audit-файлы
##           (audit.log/audit.jsonl) вне core/internal/shared/audit_logger.py — allowlist ПУСТ (строгий);
##           (2) 0 free-text pipe-записей (f.write(f"[{ts}] ... | ...") в reporting/state_machine/steps;
##           (3) формат: запись через shared write_audit_entry → каждая строка парсится json.loads
##           (jq-эквивалент), расширенная схема (ts/tag/status/msg + extra) валидна.
## @scope  Код-скан core/ (production Python) + поведенческая проверка формата через shared-модуль.
##         Не сканирует тесты (инвентарь тестов — другой гейт). Не требует Docker.
## @invariants
##   - Allowlist пуст (канон B8 D3 — строгий): любое прямое f.write на audit-файл → RED
##   - shared/audit_logger.py — единственный разрешённый writer (исключение скана)
##   - Паттерны детекции: `open(` + (audit.log|audit.jsonl) в write-режиме; `f.write` + audit-файл;
##     `write_text` + audit-файл; `f.write(f"[{ts}]`-pipe-формат
##   - Формат-проверка: json.loads на КАЖДУЮ строку; base-схема ts/tag/status/msg + extra-поля при передаче
## @rationale U-10: 3 writer'а с разными форматами ломали observability. После D1-консолидации
##            гейт делает возврат прямых f.write невозможным (R2).
## @changes 2026-08-01 | DevPlan 116 B11 T2 — Created (R2, trinity: файл + @pytest.mark.gate + manifest auto-discover)
# endregion MODULE_CONTRACT

import json
import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_CORE_DIR = repo_root() / "core"
_SHARED_AUDIT_LOGGER = _CORE_DIR / "internal" / "shared" / "audit_logger.py"

# ── Detection patterns (строгий allowlist — пуст) ─────────────────────────────
_RE_AUDIT_FILE = re.compile(r"audit\.(log|jsonl)")
_RE_OPEN_WRITE = re.compile(r"open\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)[^)]*[\"'](?:a|w|a\+|w\+)[\"']")
_RE_FWRITE_AUDIT = re.compile(r"f\.write\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)")
_RE_WRITETEXT_AUDIT = re.compile(r"write_text\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)")
_RE_PIPE_FORMAT = re.compile(r"f\.write\s*\(f[\"']\[?\{?\s*ts")


def _scan_audit_writers(core_dir: pathlib.Path) -> list[str]:
    """Scan core/**/*.py for direct audit-file writes outside shared/audit_logger.py.

    ## @purpose — Код-скан R2: единственный разрешённый writer — shared/audit_logger.py.
    ##            Allowlist пуст (строгий). Возвращает список file:line нарушений.
    ## @io — ⇥ core_dir: Path → ⎋ list[str] violation strings
    ## @complexity — O(L) where L = total lines across core/**/*.py
    ## @invariants
    ##   - shared/audit_logger.py исключается из скана (канонический writer)
    ##   - Паттерны: open(write-mode)+audit-файл, f.write+audit-файл, write_text+audit-файл
    ##   - Паттерн pipe-формата (f.write(f"[{ts}] ... |") — отдельная проверка
    """
    violations: list[str] = []
    for py_file in sorted(core_dir.rglob("*.py")):
        if py_file.resolve() == _SHARED_AUDIT_LOGGER.resolve():
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("[IMP:7][audit-format] Cannot read %s: %s", py_file, exc)
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if _RE_AUDIT_FILE.search(line) and (
                _RE_OPEN_WRITE.search(line) or _RE_FWRITE_AUDIT.search(line) or _RE_WRITETEXT_AUDIT.search(line)
            ):
                try:
                    rel = py_file.resolve().relative_to(repo_root().resolve()).as_posix()
                except ValueError:
                    rel = py_file.as_posix()
                violations.append(f"{rel}:{lineno} — direct audit-file write outside shared")
            if _RE_PIPE_FORMAT.search(line):
                try:
                    rel = py_file.resolve().relative_to(repo_root().resolve()).as_posix()
                except ValueError:
                    rel = py_file.as_posix()
                violations.append(f"{rel}:{lineno} — free-text pipe audit format")
    logger.info("[IMP:8][audit-format] Scanned %d .py files for direct audit writes", len(list(core_dir.rglob("*.py"))))
    return violations


# ── Tests ─────────────────────────────────────────────────────────────────────


# region FUNC_test_no_direct_audit_writes
## @purpose — R2: 0 прямых audit-записей вне shared/audit_logger.py (allowlist пуст).
## @io — ⇥ caplog → ⎋ None (pytest.fail с file:line списком)
## @complexity — O(L)
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · Direct audit writes returned (U-10)
# · Scenario: открытие/запись audit.log|audit.jsonl вне shared/audit_logger.py
# · Last fail: 2026-08-01 — 3 writer'а (shared, deploy/audit_logger.py, reporting pipe) + vhost_renderer pipe
# · Remove if: audit logging superseded by another mechanism
def test_no_direct_audit_writes(caplog: pytest.LogCaptureFixture) -> None:
    """R2: единый audit-writer — 0 прямых f.write на audit-файлы вне shared (D1)."""
    caplog.set_level(logging.INFO)

    violations = _scan_audit_writers(_CORE_DIR)

    if violations:
        logger.error(
            "[IMP:9][audit-format] FAIL — %d direct audit write(s):\n%s", len(violations), "\n".join(violations)
        )
        pytest.fail(
            f"AUDIT_WRITER_VIOLATION: {len(violations)} direct audit-file write(s) outside "
            f"shared/audit_logger.py:\n" + "\n".join(violations)
        )
    logger.info("[IMP:9][audit-format] ✅ 0 direct audit writes outside shared/audit_logger.py")


# endregion FUNC_test_no_direct_audit_writes


# region FUNC_test_no_free_text_pipe_format
## @purpose — R2: 0 free-text pipe-записей (f.write(f"[{ts}] ... | ...") — D1 миграция).
## @io — ⇥ caplog → ⎋ None (pytest.fail)
## @complexity — O(L)
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · Free-text pipe audit format returned
# · Scenario: f.write(f"[{ts}] bootstrap:init DONE | node=... | warnings=...") в reporting/state_machine/steps
# · Last fail: 2026-08-01 — reporting.py::write_audit_log pipe-формат
# · Remove if: audit logging superseded
def test_no_free_text_pipe_format(caplog: pytest.LogCaptureFixture) -> None:
    """R2: 0 free-text pipe-записей — все audit-записи JSONL через shared (D1)."""
    caplog.set_level(logging.INFO)

    pipe_violations: list[str] = []
    for py_file in sorted(_CORE_DIR.rglob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if _RE_PIPE_FORMAT.search(line):
                pipe_violations.append(f"{py_file.relative_to(repo_root())}:{lineno} — {line.strip()[:80]}")

    if pipe_violations:
        logger.error("[IMP:9][audit-format] FAIL — pipe-format entries:\n%s", "\n".join(pipe_violations))
        pytest.fail(
            f"AUDIT_PIPE_FORMAT: {len(pipe_violations)} free-text pipe audit write(s):\n" + "\n".join(pipe_violations)
        )
    logger.info("[IMP:9][audit-format] ✅ 0 free-text pipe audit formats")


# endregion FUNC_test_no_free_text_pipe_format


# region FUNC_test_shared_audit_writes_valid_jsonl
## @purpose — R2 формат: write через shared → построчный json.loads (jq-эквивалент);
##            расширенная схема ts/tag/status/msg + extra.
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(E) where E = entries
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · audit.jsonl format invalid (U-10)
# · Scenario: каждая строка audit.jsonl парсится json.loads; extra-поля в той же строке
# · Last fail: 2026-08-01 — deploy/audit_logger.py писал в audit.log без base-схемы
# · Remove if: audit logging superseded
def test_shared_audit_writes_valid_jsonl(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """R2 формат: JSONL-валидация вывода shared write_audit_entry (jq-эквивалент)."""
    caplog.set_level(logging.INFO)

    from core.internal.shared.audit_logger import write_audit_entry

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry(
        "deploy:deploy",
        "DEPLOYED",
        "deploy project=proj channel=scp",
        log_file=str(log_file),
        operation="deploy",
        project="proj",
        channel="scp",
        result="DEPLOYED",
        duration_s=3.5,
        snapshot_id="snap-1",
    )

    raw = log_file.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected 1 JSON line, got {len(lines)}"

    # jq-эквивалент: построчный json.loads
    entry = json.loads(lines[0])
    assert isinstance(entry, dict)
    # Base schema
    for key in ("ts", "tag", "status", "msg"):
        assert key in entry, f"Base schema key missing: {key}"
    # Extended schema (D1)
    assert entry["operation"] == "deploy"
    assert entry["project"] == "proj"
    assert entry["channel"] == "scp"
    assert entry["result"] == "DEPLOYED"
    assert entry["duration_s"] == 3.5
    assert entry["snapshot_id"] == "snap-1"
    logger.info(
        "[IMP:9][audit-format] ✅ JSONL valid: ts/tag/status/msg + extra (operation/project/channel/result/duration_s/snapshot_id)"
    )


# endregion FUNC_test_shared_audit_writes_valid_jsonl


# region FUNC_test_negative_direct_write_detected
## @purpose — R5 anti-survivorship: inline-фикстура с f.write в audit → RED.
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · R5 negative — gate must detect direct write
# · Scenario: tmp .py с open("audit.log", "a") + f.write — сканер ДОЛЖЕН поймать (RED)
# · Last fail: 2026-08-01 — гейт отсутствовал (ничего не детектил)
# · Remove if: audit logging superseded
def test_negative_direct_write_detected(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """R5 negative: прямой f.write в audit-файл вне shared → детектируется (RED)."""
    caplog.set_level(logging.INFO)

    # Inline-фикстура: файл с прямым open(audit.log, "a") + f.write (нарушение D1)
    bad_file = tmp_path / "bad_writer.py"
    bad_file.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        'with open("/var/log/platform/audit.log", "a") as f:\n'
        '    f.write(f"[{ts}] bootstrap:init DONE | node={node}\\n")\n'
    )

    # Сканируем через сканер R2 (функция принимает core_dir — тестируем на tmp dir)
    violations = _scan_audit_writers(tmp_path)
    assert violations, "R5 FAIL: direct audit write must be detected (RED)"
    assert any("bad_writer.py" in v for v in violations), f"Expected bad_writer.py in violations: {violations}"
    logger.info("[IMP:9][audit-format] ✅ R5 negative: direct write detected — %s", violations[0])


# endregion FUNC_test_negative_direct_write_detected
