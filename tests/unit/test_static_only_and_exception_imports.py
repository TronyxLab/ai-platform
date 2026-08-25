#!/usr/bin/env python3
# GREP_SUMMARY: static-only-validation, exception-import-path detector, exit-2, dual-class, negative, REF-0107
# STRUCTURE: ▶ probe-tree → ◇ CLI main(--only unknown) → ⎋ 2 · ▶ probe-file (bare shim import)
#            → ◇ detect() → ⊕ Finding · ▶ канонический импорт → ⎋ 0 findings
# region MODULE_CONTRACT
## @purpose  Тесты REF-0107: (1) static --only строго против реестра — неизвестное имя детектора
##           → exit 2 (раньше: тихий skip ВСЕХ детекторов = false-green PASS); (2) детектор
##           exception-import-path ловит неканонические импорты PlatformError-семейства.
## @scope    core/internal/static/__main__.py main(), core/internal/static/exception_imports.py detect().
## @invariants
##   - R5-negative: точный вход исходного бага (`--only exception_patterns` vs имя реестра
##     `exception-patterns`) детектируется как unknown → exit 2
##   - Канонический импорт из shared.exceptions и shim-free код — без находок
## @rationale REF-0107 problem 1: «--only принимает любые имена (rename детектора = тихий no-op)».
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

import logging

from core.internal.static import __main__ as static_main
from core.internal.static.exception_imports import detect

logger = logging.getLogger(__name__)


# region TEST_only_against_registry
def test_static_only_unknown_name_exit_2(capsys, monkeypatch, tmp_path) -> None:
    """R5: неизвестное имя --only → exit 2 (исходный баг: тихий skip всех 14 детекторов)."""
    monkeypatch.chdir(tmp_path)
    rc = static_main.main(["check", "--only", "exception_patterns", "--root", str(tmp_path)])
    assert rc == 2, f"FAIL: unknown --only должен давать exit 2, получен {rc}"
    err = capsys.readouterr().err
    assert "unknown --only" in err and "exception_patterns" in err
    logger.info("[IMP:9][static-only] unknown name → exit 2")


def test_static_only_known_name_runs_detector(monkeypatch, tmp_path, capsys) -> None:
    """Известное имя реестра исполняет детектор (0 findings на пустом дереве → PASS exit 0)."""
    monkeypatch.chdir(tmp_path)
    rc = static_main.main(["check", "--only", "exception-patterns", "--root", str(tmp_path), "--json"])
    assert rc == 0, f"FAIL: known --only на чистом дереве должен быть exit 0, получен {rc}"
    logger.info("[IMP:9][static-only] registry name executes detector cleanly")


def test_static_only_historical_bug_form_rejected(monkeypatch, tmp_path) -> None:
    """R5: точная форма live false-green — underscore вместо hyphen — отвергается."""
    monkeypatch.chdir(tmp_path)
    assert static_main.main(["check", "--only", "bool_string_literals", "--root", str(tmp_path)]) == 2
    assert static_main.main(["check", "--only", "docker_sole_path", "--root", str(tmp_path)]) == 2
    logger.info("[IMP:9][static-only] underscore-forms of hyphen detectors rejected")


# endregion TEST_only_against_registry


# region TEST_exception_import_path_detector
def test_exception_imports_detects_bare_shim(tmp_path, caplog) -> None:
    """R5: bare-shim импорт («from exceptions import PlatformFatalError») — dual-class RED."""
    caplog.set_level(logging.INFO)
    core = tmp_path / "core" / "internal"
    core.mkdir(parents=True)
    probe = core / "shim_consumer.py"
    probe.write_text("from exceptions import PlatformFatalError\n\nraise SystemExit(PlatformFatalError)\n")
    findings = detect(tmp_path)
    assert any(f.rule == "exception-import-path" and f.file.endswith("shim_consumer.py") for f in findings), (
        f"R5 FAIL: bare-shim dual-class import не пойман: {findings}"
    )
    logger.info("[IMP:9][exc-imports] bare shim detected")


def test_exception_imports_canonical_passes(tmp_path) -> None:
    """Канонический импорт shared.exceptions и не-платформенные импорты — 0 findings."""
    core = tmp_path / "core" / "internal"
    core.mkdir(parents=True)
    (core / "ok_module.py").write_text(
        "from core.internal.shared.exceptions import PlatformError, PlatformFatalError\n"
        "from collections.abc import Mapping\n"
    )
    assert detect(tmp_path) == []
    logger.info("[IMP:9][exc-imports] canonical import passes")


# endregion TEST_exception_import_path_detector
