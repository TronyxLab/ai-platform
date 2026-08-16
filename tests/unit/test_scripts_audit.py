# GREP_SUMMARY: test-scripts-audit shebang-registration exception-manifest yaml-parser tmp-fixtures unregistered
# STRUCTURE: ┌tmp core tree fixtures┐ → ◇ collect_shebang_scripts → ◇ is_exception → ◇ is_registered(yaml) → ◇ audit → ⎋ exit 0|1 assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/scripts_audit.py (DevPlan 118 E6 — Python-порт scripts-audit.sh).
##           Native imports, tmp_path fixtures — zero hardcoded paths.
## @scope    Tests: shebang detection (with/without #!), exception fnmatch, yaml-substring registration,
##           full audit clean/violation, exclude dirs (__pycache__/.backup/node_modules).
## @invariants
##   - Native imports only (no subprocess) — direct calls to scripts_audit functions
##   - All fixtures in tmp_path — no project-relative hardcoding
##   - LDD: IMP:9 log assertion on audit clean path
## @rationale E6 Strangler: grep-аудит → Python yaml-парсер. Тесты фиксируют поведение до/после
##           (та же семантика: exception-паттерны, substring-registration, shebang-first-line).
## @changes  2026-08-02 | DevPlan 118 E6 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.scripts import scripts_audit

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# region HELPER_fixtures


def _make_core_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a fake core/ tree: project_root/core/{entrypoints,internal,lib,modules}.

    Returns (project_root, core_dir).
    """
    core_dir = tmp_path / "core"
    (core_dir / "entrypoints").mkdir(parents=True)
    (core_dir / "internal").mkdir()
    (core_dir / "lib").mkdir()
    (core_dir / "modules" / "demo" / "scripts").mkdir(parents=True)
    return tmp_path, core_dir


def _write_shebang(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")


# endregion HELPER_fixtures


# region TEST_collect_shebang_scripts
def test_collect_only_shebang_files(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_collect_only_shebang_files — DevPlan 118 E migration unit test
    """collect_shebang_scripts: only files whose first line starts with #! are collected."""
    caplog.set_level(logging.INFO)
    _project, core_dir = _make_core_tree(tmp_path)

    _write_shebang(core_dir / "entrypoints" / "ok.sh")
    _write_shebang(core_dir / "internal" / "also-ok.sh")
    # Non-shebang file (first line not #!)
    (core_dir / "internal" / "no-shebang.sh").write_text("echo plain\n", encoding="utf-8")

    result = scripts_audit.collect_shebang_scripts(core_dir)
    rels = {str(p.relative_to(core_dir)) for p in result}

    assert rels == {"entrypoints/ok.sh", "internal/also-ok.sh"}, f"Got {rels}"
    # W5 T5.4: IMP:8 flow-ассерт удалён (business-ассерт rels достаточен; Anti-Illusion — IMP:9 test-лог)


# endregion


# region TEST_collect_excludes_cached_dirs
def test_collect_excludes_cache_backup_node_modules(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_collect_excludes_cache_backup_node_modules — DevPlan 118 E migration unit test
    """collect_shebang_scripts: __pycache__/.backup/node_modules directories are excluded."""
    _project, core_dir = _make_core_tree(tmp_path)
    _write_shebang(core_dir / "__pycache__" / "a.sh")
    _write_shebang(core_dir / ".backup" / "b.sh")
    _write_shebang(core_dir / "node_modules" / "c.sh")
    _write_shebang(core_dir / "entrypoints" / "keep.sh")

    result = scripts_audit.collect_shebang_scripts(core_dir)
    rels = {str(p.relative_to(core_dir)) for p in result}
    assert rels == {"entrypoints/keep.sh"}, f"Excluded dirs leaked: {rels}"


# endregion


# region TEST_is_exception
def test_is_exception_matches_lib_and_healthcheck() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_is_exception_matches_lib_and_healthcheck — DevPlan 118 E migration unit test
    """is_exception: core/lib/* and core/internal/healthcheck/* are exempt patterns."""
    assert scripts_audit.is_exception("core/lib/logging.sh") is True
    assert scripts_audit.is_exception("core/internal/healthcheck/tor-proxy-healthcheck.sh") is True
    assert scripts_audit.is_exception("core/internal/scripts-audit.sh") is True  # Self
    assert scripts_audit.is_exception("core/entrypoints/check-file-lines.sh") is False
    assert scripts_audit.is_exception("core/modules/demo/install.sh") is True


# endregion


# region TEST_is_registered_yaml_substring
def test_is_registered_yaml_substring(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_is_registered_yaml_substring — DevPlan 118 E migration unit test
    """is_registered: rel path found as substring in any YAML string value → registered."""
    manifest = tmp_path / "entrypoint-manifest.yaml"
    manifest.write_text(
        """\
deploy:
- make_target: deploy
  delegates_to: core/entrypoints/check-file-lines.sh → core/internal/deploy/orchestrator.py
  description: Deploy project
""",
        encoding="utf-8",
    )
    strings = scripts_audit.collect_manifest_strings(manifest)
    assert scripts_audit.is_registered("core/entrypoints/check-file-lines.sh", strings) is True
    assert scripts_audit.is_registered("core/internal/deploy/orchestrator.py", strings) is True
    assert scripts_audit.is_registered("core/entrypoints/missing.sh", strings) is False


def test_collect_manifest_strings_parse_error_returns_empty(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_collect_manifest_strings_parse_error_returns_empty — DevPlan 118 E migration unit test
    """collect_manifest_strings: malformed YAML → empty corpus (graceful, не crash)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid", encoding="utf-8")
    assert scripts_audit.collect_manifest_strings(bad) == []


# endregion


# region TEST_audit_full
def test_audit_clean_when_all_registered(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_audit_clean_when_all_registered — DevPlan 118 E migration unit test
    """audit: all shebang scripts registered in manifest → empty violation list (exit 0)."""
    caplog.set_level(logging.INFO)
    project, core_dir = _make_core_tree(tmp_path)
    _write_shebang(core_dir / "entrypoints" / "check-file-lines.sh")

    manifest = core_dir / "entrypoint-manifest.yaml"
    manifest.write_text("deploy:\n- delegates_to: core/entrypoints/check-file-lines.sh\n", encoding="utf-8")

    violations = scripts_audit.audit(core_dir, project, manifest)
    assert violations == [], f"Expected clean audit, got {violations}"
    logger.info("%s", f"--- audit clean, violations={violations} ---")


def test_audit_reports_unregistered(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_audit_reports_unregistered — DevPlan 118 E migration unit test
    """audit: shebang script not in manifest and not in exceptions → reported."""
    caplog.set_level(logging.INFO)
    project, core_dir = _make_core_tree(tmp_path)
    _write_shebang(core_dir / "entrypoints" / "check-file-lines.sh")
    _write_shebang(core_dir / "internal" / "orphan.sh")  # not registered anywhere

    manifest = core_dir / "entrypoint-manifest.yaml"
    manifest.write_text("deploy:\n- delegates_to: core/entrypoints/check-file-lines.sh\n", encoding="utf-8")

    violations = scripts_audit.audit(core_dir, project, manifest)
    assert violations == ["core/internal/orphan.sh"], f"Got {violations}"


def test_audit_exception_script_not_reported(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_audit_exception_script_not_reported — DevPlan 118 E migration unit test
    """audit: script matching exception pattern (e.g. core/lib/*) not reported."""
    project, core_dir = _make_core_tree(tmp_path)
    _write_shebang(core_dir / "lib" / "logging.sh")

    manifest = core_dir / "entrypoint-manifest.yaml"
    manifest.write_text("empty: []\n", encoding="utf-8")

    violations = scripts_audit.audit(core_dir, project, manifest)
    assert violations == [], f"core/lib/* must be exception, got {violations}"


# endregion


# region TEST_main_exit_codes
def test_main_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_exit_codes — DevPlan 118 E migration unit test
    """main(): exit code mapping — 0 on clean, 1 on violations.

    Monkeypatches scripts_audit.main's resolved core_dir/project_root via module-level
    __file__ override (main() derives paths from __file__).
    """
    _project, core_dir = _make_core_tree(tmp_path)

    # Point __file__ at the fake core tree so main() derives paths from it
    fake_module_file = core_dir / "internal" / "scripts" / "scripts_audit.py"
    fake_module_file.parent.mkdir(parents=True, exist_ok=True)
    fake_module_file.write_text("", encoding="utf-8")
    # 🧐 TRAP[DI-KEEP] · 2026-08-14 · — · __file__ keep (модульная идентичность, §4 floor)
    # · Rejected: DI-шов (main() выводит пути из __file__ — модульная константа, не параметр)
    # · Reason: тест проверяет поведение main() под фейковой модульной идентичностью —
    # ·   DI --root-параметр заменил бы сам тестируемый механизм резолюции путей;
    # ·   __file__-оверрайд — единственный честный способ протестировать реальный main()
    # · Rev: при добавлении DI --root-параметра в scripts_audit.main()
    monkeypatch.setattr(scripts_audit, "__file__", str(fake_module_file))

    # Case 1: clean → exit 0
    _write_shebang(core_dir / "entrypoints" / "check-file-lines.sh")
    (core_dir / "entrypoint-manifest.yaml").write_text(
        "deploy:\n- delegates_to: core/entrypoints/check-file-lines.sh\n", encoding="utf-8"
    )
    assert scripts_audit.main() == 0
    out = capsys.readouterr().out
    assert "All shebang scripts registered" in out

    # Case 2: orphan script → exit 1
    _write_shebang(core_dir / "internal" / "orphan.sh")
    assert scripts_audit.main() == 1
    out2 = capsys.readouterr().out
    assert "UNREGISTERED SCRIPTS FOUND" in out2
    assert "core/internal/orphan.sh" in out2


# endregion
