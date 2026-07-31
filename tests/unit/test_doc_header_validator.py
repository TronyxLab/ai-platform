"""
# GREP_SUMMARY: test doc-header validator regions structure module-contract yaml-purpose namelint manifest tmp_path
# STRUCTURE: ⚡ tmp_path → write headers fixtures → call check_regions_balanced / check_grep_summary_presence /
#            check_structure / check_module_contract / check_yaml_purpose / check_md_sh_refs / validate_file /
#            validate_files / validate_make_target_names → assert per-§9 scenarios
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/lint/doc_header_validator.py (DevPlan 106 §9 $TEST_SPEC)
## @scope    regions balance, GREP_SUMMARY presence (first-10), STRUCTURE, module-contract region,
##           yaml-purpose, .md sh-refs resolution (lib/ strip, find internal), ext-фильтр, namelint
## @invariants
##   - tmp_path isolated repos/manifests — zero hardcoded paths
##   - Direct function calls (native pytest, no subprocess)
##   - LDD trajectory printed + IMP:9 asserted before assertions (Anti-Illusion)
## @changes 2026-07-31 | Created (DevPlan 106 Strangler-Fig)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

from core.internal.lint.doc_header_validator import (
    check_grep_summary_presence,
    check_md_sh_refs,
    check_module_contract,
    check_regions_balanced,
    check_structure,
    check_yaml_purpose,
    validate_file,
    validate_files,
    validate_make_target_names,
)

logger = logging.getLogger("test_doc_header_validator")


def _assert_ldd(caplog) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log (LDD protocol)."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        msg = getattr(record, "message", "")
        if "[IMP:" in str(msg):
            imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(msg)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


def test_regions_balanced(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: равные/неравные счётчики # region/# endregion (включая FUNC_).
    Scenario: сбалансированный файл (включая FUNC_-регионы) → pass; 2 opens / 1 close → [FAIL].
    Last fail: n/a (new test). Remove if: regions-семантика изменена."""
    caplog.set_level(logging.INFO)
    ok = tmp_path / "mod.py"
    ok.write_text("# region A\n# endregion A\n# region FUNC_x\n# endregion FUNC_x\n")
    broken = tmp_path / "broken.py"
    broken.write_text("# region A\n# region B\n# endregion B\n")

    ok_errs = check_regions_balanced(ok)
    broken_errs = check_regions_balanced(broken)

    _assert_ldd(caplog)
    assert ok_errs == [], f"balanced regions must pass: {ok_errs}"
    assert len(broken_errs) == 1, f"imbalance must fail: {broken_errs}"
    assert "2" in broken_errs[0] and "1" in broken_errs[0]
    logger.critical("[IMP:9][test] regions_balanced: ok=%s broken=%s — OK", ok_errs, broken_errs)


def test_grep_summary_presence_first10(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: '# GREP_SUMMARY:' presence в первых 10 строках; отсутствие → ошибка.
    Scenario: GREP_SUMMARY на строке 2 → pass; 12 строк без GREP_SUMMARY → [FAIL].
    Last fail: n/a (new test). Remove if: presence-семантика (head -10) изменена."""
    caplog.set_level(logging.INFO)
    ok = tmp_path / "ok.py"
    ok.write_text("# GREP_SUMMARY: alpha\n# alpha here\n")
    missing = tmp_path / "missing.py"
    missing.write_text("# no summary line at all\n" * 12)

    ok_errs = check_grep_summary_presence(ok)
    missing_errs = check_grep_summary_presence(missing)

    _assert_ldd(caplog)
    assert ok_errs == [], f"presence must pass: {ok_errs}"
    assert len(missing_errs) == 1 and "GREP_SUMMARY" in missing_errs[0]
    logger.critical("[IMP:9][test] grep_summary_presence: ok=%s missing=%s — OK", ok_errs, missing_errs)


def test_structure_presence(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: '# STRUCTURE:' в первых 10 строках; отсутствие → ошибка.
    Scenario: STRUCTURE на строке 2 → pass; файл без STRUCTURE → [FAIL].
    Last fail: n/a (new test). Remove if: structure-семантика изменена."""
    caplog.set_level(logging.INFO)
    ok = tmp_path / "s.py"
    ok.write_text("# STRUCTURE: flow → end\n# GREP_SUMMARY: foo\n# foo flow end\n")
    missing = tmp_path / "no_s.py"
    missing.write_text("x\n" * 12)

    ok_errs = check_structure(ok)
    missing_errs = check_structure(missing)

    _assert_ldd(caplog)
    assert ok_errs == [], f"structure must pass: {ok_errs}"
    assert len(missing_errs) == 1 and "STRUCTURE" in missing_errs[0]
    logger.critical("[IMP:9][test] structure_presence: ok=%s missing=%s — OK", ok_errs, missing_errs)


def test_module_contract_presence(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: region + endregion MODULE_CONTRACT обязательны.
    Scenario: обе стороны → pass; только region → [FAIL] про endregion.
    Last fail: n/a (new test). Remove if: module-contract семантика изменена."""
    caplog.set_level(logging.INFO)
    ok = tmp_path / "m.py"
    ok.write_text("# region MODULE_CONTRACT\n# endregion MODULE_CONTRACT\n")
    partial = tmp_path / "m2.py"
    partial.write_text("# region MODULE_CONTRACT\n")

    ok_errs = check_module_contract(ok)
    partial_errs = check_module_contract(partial)

    _assert_ldd(caplog)
    assert ok_errs == [], f"full contract must pass: {ok_errs}"
    assert len(partial_errs) == 1 and "endregion" in partial_errs[0]
    logger.critical("[IMP:9][test] module_contract: ok=%s partial=%s — OK", ok_errs, partial_errs)


def test_yaml_purpose_required(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: yaml без '## @purpose' → ошибка; py/md не проверяются.
    Scenario: yaml с @purpose → pass; yaml без → [FAIL]; validate_file(.py) не выдаёт yaml-purpose ошибок.
    Last fail: n/a (new test). Remove if: yaml-purpose семантика изменена."""
    caplog.set_level(logging.INFO)
    ok = tmp_path / "conf.yaml"
    ok.write_text("# GREP_SUMMARY: x\n# x\n## @purpose  test purpose\n")
    bad = tmp_path / "bad.yaml"
    bad.write_text("# no purpose tag here\n")
    py = tmp_path / "plain.py"
    py.write_text("# GREP_SUMMARY: x\n# STRUCTURE: y\n# region MODULE_CONTRACT\n# endregion MODULE_CONTRACT\n# x y\n")

    ok_errs = check_yaml_purpose(ok)
    bad_errs = check_yaml_purpose(bad)
    py_errs = validate_file(py, tmp_path)

    _assert_ldd(caplog)
    assert ok_errs == [], f"yaml purpose must pass: {ok_errs}"
    assert len(bad_errs) == 1 and "@purpose" in bad_errs[0]
    assert not any("purpose" in e for e in py_errs), f"py must not be yaml-checked: {py_errs}"
    logger.critical("[IMP:9][test] yaml_purpose: ok=%s bad=%s py_errs=%s — OK", ok_errs, bad_errs, py_errs)


def test_md_sh_refs_resolution(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · AC10: backtick-ссылки разрешаются (dirs + lib/ strip + find internal); битая → ошибка;
    абсолютный /* → skip.
    Scenario: `scripts/real.sh` (dirs), `lib/ssh.sh` (strip), `core/entrypoints/deploy.sh` (dirs),
    `tool.sh` (find core/internal maxdepth 5), `/opt/x/y.sh` (skip) → pass; `scripts/missing.sh` → [FAIL].
    Last fail: n/a (new test). Remove if: md sh-refs resolution изменена."""
    caplog.set_level(logging.INFO)
    repo = tmp_path / "repo"
    (repo / "core" / "lib").mkdir(parents=True)
    (repo / "core" / "entrypoints").mkdir(parents=True)
    (repo / "core" / "internal" / "deep" / "deep2").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "core" / "lib" / "ssh.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "core" / "entrypoints" / "deploy.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "core" / "internal" / "deep" / "deep2" / "tool.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "scripts" / "real.sh").write_text("#!/usr/bin/env bash\n")
    doc = repo / "doc.md"
    doc.write_text(
        "`scripts/real.sh` ok; `lib/ssh.sh` ok; `core/entrypoints/deploy.sh` ok; `tool.sh` ok; `/opt/x/y.sh` skip\n"
    )
    broken = repo / "broken.md"
    broken.write_text("`scripts/missing.sh` nope\n")

    ok_errs = check_md_sh_refs(doc, repo_root=repo)
    broken_errs = check_md_sh_refs(broken, repo_root=repo)

    _assert_ldd(caplog)
    assert ok_errs == [], f"resolvable refs must pass: {ok_errs}"
    assert len(broken_errs) == 1 and "scripts/missing.sh" in broken_errs[0]
    logger.critical("[IMP:9][test] md_sh_refs: ok=%s broken=%s — OK", ok_errs, broken_errs)


def test_validate_file_ext_filter(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: .venv/node_modules/__pycache__ skip; ext вне списка skip; без аргументов → pass.
    Scenario: .venv/lib.py, node_modules/pkg.md, notes.txt → checked==0, errors==[]; пустой список → pass;
    валидный ok.py → checked==1, errors==[].
    Last fail: n/a (new test). Remove if: ext-фильтр изменён."""
    caplog.set_level(logging.INFO)
    repo = tmp_path / "repo"
    (repo / ".venv").mkdir(parents=True)
    (repo / "node_modules").mkdir(parents=True)
    (repo / ".venv" / "lib.py").write_text("pass\n")
    (repo / "node_modules" / "pkg.md").write_text("no headers\n")
    (repo / "notes.txt").write_text("no headers\n")
    ok = repo / "ok.py"
    ok.write_text(
        "# GREP_SUMMARY: alpha\n# STRUCTURE: x\n# region MODULE_CONTRACT\n# endregion MODULE_CONTRACT\n# alpha x\n"
    )

    files = [str(repo / ".venv" / "lib.py"), str(repo / "notes.txt"), str(repo / "node_modules" / "pkg.md")]
    errors, checked = validate_files(files, repo_root=repo)
    errors0, checked0 = validate_files([], repo_root=repo)
    errors_ok, checked_ok = validate_files([str(ok)], repo_root=repo)

    _assert_ldd(caplog)
    assert errors == [] and checked == 0, f"skipped files must not be checked: {errors}/{checked}"
    assert errors0 == [] and checked0 == 0, "no args must pass"
    assert errors_ok == [] and checked_ok == 1, f"valid file must be checked: {errors_ok}/{checked_ok}"
    logger.critical(
        "[IMP:9][test] ext_filter: skipped=%d/%d empty=%d/%d ok=%d/%d — OK",
        len(errors),
        checked,
        len(errors0),
        checked0,
        len(errors_ok),
        checked_ok,
    )


def test_namelint_targets(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: namelint allowed/forbidden/lifecycle/system-исключения/неизвестный.
    Scenario: foo (allowed) pass; push-core (forbidden) FAIL; restart (lifecycle) pass; test-/gate-/pre-commit-
    префиксы pass; help/venv (system_exceptions) pass; unknown → FAIL. Чистый набор → errors==[].
    Last fail: n/a (new test). Remove if: namelint политика изменена."""
    caplog.set_level(logging.INFO)
    repo = tmp_path / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "entrypoint-manifest.yaml").write_text(
        "allowed_verbs:\n- foo\n- deploy\nmodule_lifecycle:\n- restart\n"
        "forbidden_verbs:\n- push-core\nname_linter:\n  system_exceptions:\n  - help\n  - venv\n"
        "  system_prefixes:\n  - test-\n  - gate-\n  - pre-commit-\n"
    )
    (repo / "Makefile").write_text(
        ".PHONY: foo deploy push-core restart help venv test-x gate-x pre-commit-x unknown\n"
    )
    (repo / "makefiles").mkdir()
    (repo / "makefiles" / "extra.mk").write_text(".PHONY: foo\n")

    errors = validate_make_target_names(repo)

    clean = tmp_path / "clean"
    (clean / "core").mkdir(parents=True)
    (clean / "core" / "entrypoint-manifest.yaml").write_text(
        "allowed_verbs:\n- foo\n- deploy\nmodule_lifecycle:\n- restart\n"
        "forbidden_verbs:\n- push-core\nname_linter:\n  system_exceptions:\n  - help\n  - venv\n"
        "  system_prefixes:\n  - test-\n  - gate-\n  - pre-commit-\n"
    )
    (clean / "Makefile").write_text(".PHONY: foo deploy restart help venv test-x gate-x pre-commit-x\n")
    clean_errors = validate_make_target_names(clean)

    _assert_ldd(caplog)
    assert any("push-core" in e and "FORBIDDEN" in e for e in errors), f"forbidden not detected: {errors}"
    assert any("unknown" in e for e in errors), f"unknown not detected: {errors}"
    assert not any("foo" in e or "restart" in e or "help" in e or "venv" in e for e in errors), errors
    assert clean_errors == [], f"clean targets must pass: {clean_errors}"
    logger.critical("[IMP:9][test] namelint_targets: %d errors, clean=%d — OK", len(errors), len(clean_errors))


def test_namelint_missing_manifest(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: манифест отсутствует → FAIL "Manifest not found" (lint.sh:90-94).
    Scenario: пустой tmp_path (нет core/entrypoint-manifest.yaml) → ровно одна [FAIL]-ошибка.
    Last fail: n/a (new test). Remove if: manifest-missing поведение изменено."""
    caplog.set_level(logging.INFO)
    errors = validate_make_target_names(tmp_path)

    _assert_ldd(caplog)
    assert len(errors) == 1 and "Manifest not found" in errors[0], f"unexpected: {errors}"
    logger.critical("[IMP:9][test] namelint_missing_manifest: %s — OK", errors)
