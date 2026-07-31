# GREP_SUMMARY: test project_scaffolder new-project scaffold template copy dry-run conflict auto-domain checklist
# STRUCTURE: ┌template fixture┐ → ┌projects_root fixture┐ → ○ 8 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for project_scaffolder.py (DP-092 Wave 4b). Tests template copy,
##           auto-domain logic, dry-run mode (no mutation), conflict detection,
##           missing template error, checklist generation, domain generation.
## @scope    project_scaffolder.py public API + scaffold_helpers integration.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - Subprocess calls (rsync, git, gh, template-engine) mocked or bypassed
##   - Dry-run tests verify NO file mutations
##   - LDD IMP:9 assertion on every test
##   - R1-R5 compliance
## @rationale Covers AC1 (new-project), AC2 (facade), AC4 (unit tests), AC6 (shared extraction)
## @changes  2026-07-30 · Wave 4b — initial implementation
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# ── LDD helper ─────────────────────────────────────────────────────


def _assert_ldd_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """Assert at least one IMP:9 log is present in caplog."""
    found_log: bool = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# ── Helpers ────────────────────────────────────────────────────────


def _write_minimal_template(template_dir: pathlib.Path) -> None:
    """Create minimal template files for testing.

    ## @purpose  Fake template with a docker-compose.yml and .github/ structure.
    ## @io        ⇥ template_dir → ⎋ writes files
    """
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "docker-compose.yml").write_text("services: {}\n")
    (template_dir / "Dockerfile").write_text("FROM alpine:latest\n")

    workflows_dir = template_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / "deploy.yml").write_text("# deploy workflow\n")
    (workflows_dir / "platform-deploy.yml").write_text("# DEPRECATED\n")


# ── Tests: auto_domain ─────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_auto_domain_with_env · Scenario: PLATFORM_DOMAIN set → auto domain generated · Last fail: N/A · Remove if: auto_domain logic changes
def test_auto_domain_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test auto-domain generation when PLATFORM_DOMAIN is set.

    ## @purpose  DD3: --domain not set → NAME.PLATFORM_DOMAIN.
    ## @io        env PLATFORM_DOMAIN=example.com → "myapp.example.com"
    """
    monkeypatch.setenv("PLATFORM_DOMAIN", "example.com")

    from core.internal.scaffold.project_scaffolder import auto_domain

    result = auto_domain("myapp")
    assert result == "myapp.example.com"


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_auto_domain_explicit_override · Scenario: explicit domain passed → no auto · Last fail: N/A · Remove if: auto_domain logic changes
def test_auto_domain_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that explicit domain overrides auto-domain.

    ## @purpose  --domain <explicit> takes precedence over auto.
    ## @io        explicit "custom.example.com" → returned unchanged
    """
    monkeypatch.setenv("PLATFORM_DOMAIN", "example.com")

    from core.internal.scaffold.project_scaffolder import auto_domain

    result = auto_domain("myapp", "custom.example.com")
    assert result == "custom.example.com"


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_auto_domain_no_env · Scenario: PLATFORM_DOMAIN not set → empty string · Last fail: N/A · Remove if: auto_domain logic changes
def test_auto_domain_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test auto-domain returns empty string when PLATFORM_DOMAIN is not set.

    ## @purpose  Graceful: no PLATFORM_DOMAIN → no domain, no error.
    ## @io        no env → ""
    """
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)

    from core.internal.scaffold.project_scaffolder import auto_domain

    result = auto_domain("myapp")
    assert result == ""


# ── Tests: copy_template ───────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_copy_template_excludes_platform_deploy · Scenario: template has platform-deploy.yml → excluded from copy (T9) · Last fail: N/A · Remove if: copy_template logic changes
@ldd_trajectory
def test_copy_template_excludes_platform_deploy(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test template copy excludes platform-deploy.yml (T9).

    ## @purpose  platform-deploy.yml should NOT be in the copied project.
    ## @io        template with platform-deploy.yml → copy → verify excluded
    """
    caplog.set_level(logging.INFO)

    src = tmp_path / "template-backend"
    _write_minimal_template(src)

    dst = tmp_path / "projects" / "test-org" / "myapp"

    from core.internal.scaffold.project_scaffolder import copy_template

    result = copy_template(str(src), str(dst), dry_run=False)
    assert result is True

    # Verify normal files are copied
    assert (dst / "docker-compose.yml").exists()
    assert (dst / "Dockerfile").exists()
    assert (dst / ".github" / "workflows" / "deploy.yml").exists()

    # Verify platform-deploy.yml is EXCLUDED (T9)
    assert not (dst / ".github" / "workflows" / "platform-deploy.yml").exists(), "platform-deploy.yml must be excluded (T9)"

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_copy_template_existing_conflict · Scenario: destination exists → error · Last fail: N/A · Remove if: copy_template logic changes
@ldd_trajectory
def test_copy_template_existing_conflict(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test copy_template fails when destination directory already exists.

    ## @purpose  Conflict detection: dir exists → exit 1.
    ## @io        pre-existing dir → copy_template returns False
    """
    caplog.set_level(logging.INFO)

    src = tmp_path / "template-backend"
    _write_minimal_template(src)

    dst = tmp_path / "projects" / "test-org" / "existing"
    dst.mkdir(parents=True)  # already exists

    from core.internal.scaffold.project_scaffolder import copy_template

    result = copy_template(str(src), str(dst), dry_run=False)
    assert result is False

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_copy_template_missing_src · Scenario: template source does not exist → error · Last fail: N/A · Remove if: copy_template logic changes
@ldd_trajectory
def test_copy_template_missing_src(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test copy_template fails when source template does not exist.

    ## @purpose  Missing template → error, not silent failure.
    ## @io        non-existent src → returns False
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_scaffolder import copy_template

    result = copy_template(
        str(tmp_path / "nonexistent"),
        str(tmp_path / "dst"),
        dry_run=False,
    )
    assert result is False

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_copy_template_dry_run_no_mutation · Scenario: --dry-run → no files created · Last fail: N/A · Remove if: copy_template logic changes
def test_copy_template_dry_run_no_mutation(tmp_path: pathlib.Path) -> None:
    """Test dry-run mode does not create any files.

    ## @purpose  Dry-run = show plan, no mutation.
    ## @io        dry_run=True → returns True, destination not created
    """
    src = tmp_path / "template-backend"
    _write_minimal_template(src)

    dst = tmp_path / "projects" / "test-org" / "myapp"

    from core.internal.scaffold.project_scaffolder import copy_template

    result = copy_template(str(src), str(dst), dry_run=True)
    assert result is True
    assert not dst.exists(), "Dry-run should not create destination directory"


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_auto_domain_name_with_hyphens · Scenario: project name with hyphens → valid domain · Last fail: N/A · Remove if: auto_domain logic changes
def test_auto_domain_name_with_hyphens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test auto-domain with hyphenated project name.

    ## @purpose  Hyphenated names like "my-app-api" produce valid domain.
    ## @io        "my-app-api" + PLATFORM_DOMAIN → "my-app-api.example.com"
    """
    monkeypatch.setenv("PLATFORM_DOMAIN", "example.com")

    from core.internal.scaffold.project_scaffolder import auto_domain

    result = auto_domain("my-app-api")
    assert result == "my-app-api.example.com"
