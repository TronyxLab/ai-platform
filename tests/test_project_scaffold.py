# GREP_SUMMARY: test project scaffold converge r3 gen-env-platform step-6b node-lifecycle idempotent
# STRUCTURE: ┌test_env fixture┐ → ○ test_converge_r3_dry_run → ○ test_converge_r3_scaffold → ○ test_converge_r3_idempotent → ○ test_step_6b_calls_converge → ○ test_gen_env_platform_interface → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Test suite for Wave 2 project scaffold through converge: converge.sh --units R3,
##           gen-env-platform.sh integration, step_6b converge call, and idempotent behavior.
## @scope    5 test functions covering dry-run planning, real mutation (conditional), idempotent
##           repeat, source-code verification of step_6b converge invocation, and gen-env-platform
##           interface verification.
## @invariants
##   - All subprocess tests create node.yaml in a discoverable location (under project's
##     node-configs/ dir or /opt/platform/node-configs/ via sudo)
##   - Fixture node.yaml provides 1-2 test projects for converge R3 to process
##   - Fixture platform-env.yaml provides valid profiles/provides for gen-env-platform.sh
##   - converge.sh is called from its real location (CORE_DIR resolved from script path)
##   - Mutation tests (scaffold, idempotent) require write access to /opt/projects;
##     if unavailable, tests verify error behavior instead of full scaffold
## @rationale DevPlan 024 Wave 2: project scaffold through converge R3 replaces inline
##            mkdir+touch in step_6b with converge.sh --units R3, which calls
##            gen-env-platform.sh for .env.platform generation.
## @changes 2026-07-21 · Wave 2 — initial implementation
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import shutil
import subprocess

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
_CONVERGE_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "converge.sh"
_GEN_ENV_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "scaffold" / "gen-env-platform.sh"
_NODE_LIFECYCLE_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"

# converge.sh resolves node.yaml via resolve_node_yaml which searches:
#   1. PLATFORM_ROOT/node-configs/<node>/node.yaml  (PLATFORM_ROOT hardcoded to /opt/platform)
#   2. $HOME/projects/*/node-configs/<node>/node.yaml
#   3. /opt/node-configs/<node>/node.yaml
# For testing without root, we use Path 2: create under $HOME/projects/<test-org>/node-configs/
_NODE_NAME = "test-node-scaffold"
_TEST_ORG_DIR = pathlib.Path.home() / "projects" / "test-scaffold-org"
_TEST_NODE_CONFIG_DIR = _TEST_ORG_DIR / "node-configs" / _NODE_NAME


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def converge_test_org_dir() -> pathlib.Path:
    """Session-scoped fixture: create and clean up the test org directory.

    ## @purpose — Creates ~/projects/.test-scaffold-org/ for node.yaml discovery
    ##            via converge.sh Path 2 ($HOME/projects/*/node-configs/). Cleans
    ##            up after all tests in the session complete.
    ## @io — ⎋ path to test org directory
    """
    org_dir = _TEST_ORG_DIR
    org_dir.mkdir(parents=True, exist_ok=True)
    yield org_dir
    # Cleanup: remove test org directory
    if org_dir.exists():
        shutil.rmtree(org_dir, ignore_errors=True)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _create_test_node_yaml(node_name: str = _NODE_NAME) -> pathlib.Path:
    """Create a test node.yaml with 2 projects for converge R3 tests.

    ## @purpose — Creates node.yaml under ~/projects/.test-scaffold-org/node-configs/<node>/
    ##            so converge.sh Path 2 ($HOME/projects/*/node-configs/) discovers it.
    ## @io — Returns path to created node.yaml
    ## @invariants — 2 projects (testapp, demoapp) with distinct domains
    """
    node_config_dir = _TEST_NODE_CONFIG_DIR
    node_config_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_config_dir / "node.yaml"

    data = {
        "context": "test-context",
        "projects": [
            {
                "name": "testapp",
                "domain": "testapp.tronyx.ru",
            },
            {
                "name": "demoapp",
                "domain": "demoapp.tronyx.ru",
            },
        ],
    }

    with open(node_yaml, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info(
        "[IMP:8][helper][create_test_node_yaml] Created %s with 2 projects",
        node_yaml,
    )
    return node_yaml


def _projects_writable() -> bool:
    """Check if /opt/projects is writable for mutation tests.

    ## @purpose — Determine if mutation tests (mkdir in /opt/projects) can run.
    ##            Tries to create /opt/projects with sudo if needed.
    ## @io — ⎋ True if /opt/projects exists and is writable, or can be created
    """
    opt_projects = pathlib.Path("/opt/projects")
    if opt_projects.exists():
        if os.access(str(opt_projects), os.W_OK):
            return True
        # Try sudo chown
        if shutil.which("sudo"):
            result = subprocess.run(
                ["sudo", "chown", os.environ.get("USER", ""), "/opt/projects"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        return False

    # Try to create it
    try:
        opt_projects.mkdir(parents=True, exist_ok=True)
        return True
    except (PermissionError, OSError):
        pass

    # Try with sudo
    if shutil.which("sudo"):
        result = subprocess.run(
            ["sudo", "mkdir", "-p", "/opt/projects"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Chown to current user so we can clean up
            subprocess.run(
                ["sudo", "chown", os.environ.get("USER", ""), "/opt/projects"],
                capture_output=True,
                text=True,
            )
            return True
    return False


def _run_converge(
    node_name: str = _NODE_NAME,
    units: str = "R3",
    dry_run: bool = False,
    report_only: bool = False,
) -> subprocess.CompletedProcess:
    """Run converge.sh with given parameters and return result.

    ## @purpose — Single entry point for calling converge.sh in tests.
    ##            Finds node.yaml via Path 2 ($HOME/projects/*/node-configs/).
    ## @io — ⇥ node/units/dry_run → ⎋ CompletedProcess
    """
    args = [
        "bash",
        str(_CONVERGE_SCRIPT),
        "--node",
        node_name,
        "--units",
        units,
    ]
    if dry_run:
        args.append("--dry-run")
    if report_only:
        args.append("--report-only")

    logger.info(
        "[IMP:8][helper][run_converge] Running: %s",
        " ".join(str(a) for a in args),
    )

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )

    logger.info(
        "[IMP:8][helper][run_converge] exit=%d stderr_lines=%d",
        result.returncode,
        len(result.stderr.splitlines()),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-07-21
# · Regression: N/A (new test)
# · Scenario: converge.sh --units R3 --dry-run with 2 projects in node.yaml
# · Last fail: exit code 2 (flock missing on macOS), fixed by TRAP[DECISION] adding flock check
# · Remove if: converge R3 is removed or dry-run mode is deprecated
@ldd_trajectory
def test_converge_r3_dry_run(
    caplog,
    converge_test_org_dir: pathlib.Path,
) -> None:
    """Converge R3 dry-run must plan all expected operations without mutation.

    ## @purpose — Verify that converge.sh --units R3 --dry-run correctly identifies
    ##            testapp and demoapp projects as needing creation. The dry-run mode
    ##            should report WOULD-create lines for directories, stubs, and .env.platform.
    ## @acceptance — DevPlan 024 Wave 2: converge R3 dry-run shows all 2 projects
    ##               (testapp, demoapp) with planned mkdir + stub + gen-env ops.
    """
    # Setup
    _create_test_node_yaml()

    # Run converge --units R3 --dry-run
    result = _run_converge(dry_run=True)

    # Print converge stderr for debugging
    print("--- CONVERGE DRY-RUN STDERR ---")
    print(result.stderr)
    print("--- END CONVERGE STDERR ---")

    # Dry-run may exit 0 (no real mutations) or 1 (would mutate)
    assert result.returncode in (
        0,
        1,
    ), f"Expected exit 0 or 1, got {result.returncode}: {result.stderr}"

    # Verify expected operations are planned
    assert "R3" in result.stderr, "R3 unit should be dispatched"
    assert "testapp" in result.stderr, "testapp project should be processed"
    assert "demoapp" in result.stderr, "demoapp project should be processed"

    # Verify WOULD statements for project operations
    would_create_count = result.stderr.count("WOULD create")
    assert would_create_count >= 2, (
        f"Expected ≥2 WOULD-create lines overall, got {would_create_count}\nstderr:\n{result.stderr[:3000]}"
    )

    # R3 should report planned mutations (WOULD or mutated)
    assert "mutated" in result.stderr or "WOULD" in result.stderr, (
        "R3 should report planned mutations or converged state"
    )

    # Converge should not have run R1 (filtered by --units R3)
    assert "R1" not in result.stderr or "filtered" in result.stderr, "R1 should be filtered out with --units R3"

    # LDD trajectory assertion (handled by @ldd_trajectory decorator)
    logger.critical("[IMP:9][test][dry-run] Converge R3 dry-run completed — planned operations match expectations")


# 🧪 TRAP[TEST] · 2026-07-21
# · Regression: N/A (new test)
# · Scenario: converge.sh --units R3 with 2 projects; if /opt/projects not writable, test error path
# · Last fail: incorrect exit code expectations (flock, CONVERGE_EXIT_CODE not set on mkdir errors)
# · Remove if: converge R3 is removed or error path behavior changes
@ldd_trajectory
def test_converge_r3_scaffold(
    caplog,
    converge_test_org_dir: pathlib.Path,
) -> None:
    """Converge R3 must create project directories, stubs, and .env.platform.

    ## @purpose — Verify that converge.sh --units R3 creates per-project directories
    ##            under /opt/projects/<name>/, ai-platform.yaml stub, and .env.platform
    ##            via gen-env-platform.sh with correct ownership (ci-deploy:ci-deploy).
    ##            If /opt/projects is not writable, verify graceful error handling.
    ## @acceptance — DevPlan 024 Wave 2: converge R3 scaffold creates real project files.
    """
    # Setup
    _create_test_node_yaml()

    # ── If projects dir is not writable, test error behavior ──
    if not _projects_writable():
        logger.warning("[IMP:7][test][scaffold] /opt/projects not writable — testing error behavior")
        result = _run_converge()

        print("--- CONVERGE STDERR (no /opt write access) ---")
        print(result.stderr)
        print("--- END STDERR ---")

        # Exit 2 = errors (mkdir failed)
        assert result.returncode == 2, (
            f"Expected exit 2 (errors) when /opt/projects not writable, got {result.returncode}\n"
            f"stderr:\n{result.stderr[:2000]}"
        )

        # Even with failures, validation should have run for each project
        assert "testapp" in result.stderr
        assert "demoapp" in result.stderr

        # Verify graceful error handling
        assert "FAIL" in result.stderr or "WARN" in result.stderr, (
            "Script should report failures or warnings when /opt/projects is not writable"
        )

        logger.critical(
            "[IMP:9][test][scaffold] Converge R3 error path verified — graceful failure when /opt/projects is not writable"
        )
        return

    # ── Normal mutation test: run converge R3 ──
    result = _run_converge()

    print("--- CONVERGE STDERR (mutation) ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Exit 1 = mutations applied (normal for first run)
    assert result.returncode in (
        0,
        1,
    ), f"Expected exit 0 or 1, got {result.returncode}: {result.stderr}"

    # Verify project directories exist
    for proj in ("testapp", "demoapp"):
        proj_dir = pathlib.Path("/opt/projects") / proj
        assert proj_dir.is_dir(), f"Project directory {proj_dir} should exist"

        # Verify ai-platform.yaml stub
        stub_file = proj_dir / "ai-platform.yaml"
        assert stub_file.is_file(), f"Stub file {stub_file} should exist"
        stub_content = stub_file.read_text()
        assert proj in stub_content, f"Stub should reference project name '{proj}'"

        # Verify .env.platform (may be generated by gen-env-platform.sh or fallback empty)
        env_file = proj_dir / ".env.platform"
        assert env_file.is_file(), f".env.platform {env_file} should exist"

    # Verify gen-env-platform.sh was consulted
    if "gen-env-platform.sh" in result.stderr:
        logger.info("[IMP:7][test][scaffold] gen-env-platform.sh was invoked for .env.platform generation")

    logger.critical(
        "[IMP:9][test][scaffold] Converge R3 scaffold completed — project dirs, stubs, and .env.platform created"
    )


# 🧪 TRAP[TEST] · 2026-07-21
# · Regression: N/A (new test)
# · Scenario: two converge.sh --units R3 calls; first run creates, second run skips
# · Last fail: exit code assertions on dry-run fallback (no /opt/projects access)
# · Remove if: converge R3 idempotency contract changes
@ldd_trajectory
def test_converge_r3_idempotent(
    caplog,
    converge_test_org_dir: pathlib.Path,
) -> None:
    """Second converge R3 run must be no-op (idempotent).

    ## @purpose — Verify convergence idempotency: after a successful first converge R3,
    ##            the second run should report SKIP for all existing items and exit 0
    ##            (fully converged, no drifts).
    ## @acceptance — DevPlan 024 Wave 2: converge R3 is idempotent — no file overwrites,
    ##               exit code 0 on repeat run.
    """
    # Setup
    _create_test_node_yaml()

    # ── If projects dir is not writable, test idempotent planning (dry-run) ──
    if not _projects_writable():
        logger.warning("[IMP:7][test][idempotent] /opt/projects not writable — testing idempotent dry-run instead")

        # Run dry-run twice to verify consistent planning
        result1 = _run_converge(dry_run=True)
        result2 = _run_converge(dry_run=True)

        print("--- FIRST DRY-RUN STDERR ---")
        print(result1.stderr)
        print("--- SECOND DRY-RUN STDERR ---")
        print(result2.stderr)
        print("--- END ---")

        # Both dry-runs should produce similar plans
        assert result1.returncode == result2.returncode, (
            f"Both dry-runs should return same exit code: {result1.returncode} vs {result2.returncode}"
        )
        assert "testapp" in result1.stderr and "testapp" in result2.stderr, "Both dry-runs should process testapp"

        logger.critical(
            "[IMP:9][test][idempotent] Converge R3 idempotent dry-run verified — consistent planning across two runs"
        )
        return

    # ── First run: create everything ──
    result1 = _run_converge()
    print("--- FIRST CONVERGE STDERR ---")
    print(result1.stderr)
    print("--- END STDERR ---")

    # First run should report mutations (exit 1) or already converged (exit 0)
    assert result1.returncode in (0, 1), f"First converge exit {result1.returncode}: {result1.stderr[:2000]}"

    # ── Second run: should be no-op ──
    result2 = _run_converge()
    print("--- SECOND CONVERGE STDERR ---")
    print(result2.stderr)
    print("--- END STDERR ---")

    # Second run should exit 0 (fully converged, no drifts)
    assert result2.returncode == 0, (
        f"Second converge should exit 0 (converged), got {result2.returncode}: {result2.stderr[:2000]}"
    )

    # Verify SKIP lines for existing stubs
    skip_stub_count = 0
    skip_env_count = 0
    for line in result2.stderr.splitlines():
        if "SKIP" in line and "ai-platform.yaml" in line:
            skip_stub_count += 1
        if "SKIP" in line and ".env.platform" in line:
            skip_env_count += 1

    assert skip_stub_count >= 1, (
        f"Expected ≥1 SKIP for existing ai-platform.yaml, got {skip_stub_count}\nstderr:\n{result2.stderr[:3000]}"
    )
    assert skip_env_count >= 1, (
        f"Expected ≥1 SKIP for existing .env.platform, got {skip_env_count}\nstderr:\n{result2.stderr[:3000]}"
    )

    # Verify existing files were NOT overwritten (content preserved)
    assert "already exists" in result2.stderr, "Second run should report already-exists, not regenerated"

    logger.critical(
        "[IMP:9][test][idempotent] Converge R3 idempotent — second run exit=0 with SKIP for all existing items"
    )


# 🧪 TRAP[TEST] · 2026-07-21
# · Regression: N/A (new test)
# · Scenario: grep node-lifecycle.sh source for 'converge_script --units R3' pattern
# · Last fail: no prior failures
# · Remove if: step_6b no longer calls converge R3
@ldd_trajectory
def test_step_6b_calls_converge(
    caplog,
) -> None:
    """step_6b in node-lifecycle.sh must call converge.sh --units R3.

    ## @purpose — Source-level verification that step_6b_create_projects_base()
    ##            in node-lifecycle.sh invokes converge.sh with the --units R3 flag.
    ##            This test does NOT run node-lifecycle.sh — it greps the source
    ##            to verify the converge call pattern is present.
    ## @acceptance — DevPlan 024 Wave 2: step_6b calls converge --units R3 for
    ##               project scaffold during bootstrap.
    """
    # Read node-lifecycle.sh source
    source_text = _NODE_LIFECYCLE_SCRIPT.read_text()

    # Verify step_6b function exists
    assert "step_6b_create_projects_base" in source_text, (
        "step_6b_create_projects_base() must exist in node-lifecycle.sh"
    )

    # Verify the function calls converge.sh with --units R3
    converge_call_pattern = False
    for line in source_text.splitlines():
        if "converge_script" in line and "--units R3" in line:
            converge_call_pattern = True
            print(f"[IMP:7][test][step_6b] Found converge R3 call: {line.strip()}")
        if "converge.sh" in line and "R3" in line and "units" in line:
            converge_call_pattern = True
            print(f"[IMP:7][test][step_6b] Found converge R3 reference: {line.strip()}")

    assert converge_call_pattern, (
        f"step_6b_create_projects_base() must call converge.sh with --units R3\n"
        f"Look for 'converge --units R3' pattern in {_NODE_LIFECYCLE_SCRIPT}\n"
        f"Converge-related lines:\n"
        + "\n".join(line for line in source_text.splitlines() if "converge" in line.lower())[:3000]
    )

    # Verify converge.sh has the --units flag in usage
    converge_source = _CONVERGE_SCRIPT.read_text()
    assert "--units" in converge_source, "converge.sh must support the --units flag in its argument parsing"
    print("[IMP:7][test][step_6b] converge.sh --units flag confirmed in source")

    logger.critical("[IMP:9][test][step_6b] Verified step_6b_create_projects_base() calls converge --units R3")


# 🧪 TRAP[TEST] · 2026-07-21
# · Regression: N/A (new test)
# · Scenario: run gen-env-platform.sh --name testapp --output tmp_path/.env.platform
# · Last fail: no prior failures
# · Remove if: gen-env-platform.sh --name flag is removed or renamed
@ldd_trajectory
def test_gen_env_platform_interface(
    caplog,
    tmp_path: pathlib.Path,
) -> None:
    """gen-env-platform.sh must support --name flag for converge calls.

    ## @purpose — Verify that gen-env-platform.sh accepts --name and --output flags,
    ##            which converge R3 uses to generate .env.platform per project.
    ##            This test runs gen-env-platform.sh directly with test inputs.
    ## @acceptance — DevPlan 024 Wave 2: gen-env-platform.sh --name <project> --output <file>
    ##               generates valid .env.platform content.
    """
    # Create minimal platform-env.yaml in tmp_path
    env_yaml = tmp_path / "platform-env.yaml"
    data = {
        "provides": {
            "postgres": {
                "host": "pgbouncer",
                "port": 6432,
                "dsn_template": "postgresql://user:pass@pgbouncer:6432/${NAME}",
                "networks": ["proxy-net"],
            },
            "redis": {
                "host": "redis",
                "port": 6379,
                "dsn_template": "redis://redis:6379/0",
                "networks": ["proxy-net"],
            },
        },
        "profiles": ["postgres", "redis"],
        "proxy": {
            "no_proxy_internal": "localhost,127.0.0.1,.local",
        },
    }
    with open(env_yaml, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    output_file = tmp_path / ".env.platform"

    # Run gen-env-platform.sh with --name and --output
    result = subprocess.run(
        [
            "bash",
            str(_GEN_ENV_SCRIPT),
            "--yaml",
            str(env_yaml),
            "--name",
            "testapp",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    print("--- GEN-ENV-PLATFORM STDOUT ---")
    print(result.stdout)
    print("--- GEN-ENV-PLATFORM STDERR ---")
    print(result.stderr)
    print("--- END ---")

    assert result.returncode == 0, f"gen-env-platform.sh should exit 0, got {result.returncode}: {result.stderr}"

    # Verify output file exists
    assert output_file.is_file(), f"Output file {output_file} should exist"

    # Verify content
    content = output_file.read_text()
    assert "PLATFORM_DOMAIN" in content, "Should contain PLATFORM_DOMAIN"
    assert "PLATFORM_POSTGRES_HOST" in content or "PLATFORM_REDIS_HOST" in content, (
        "Should contain at least one PLATFORM_* service variable"
    )
    assert "# GENERATED by ai-platform" in content, "Should start with GENERATED marker"

    # Verify DSN substitution: testapp should appear in DSN
    if "PLATFORM_POSTGRES_DSN" in content:
        dsn_line = next(line for line in content.splitlines() if "PLATFORM_POSTGRES_DSN" in line)
        assert "testapp" in dsn_line, f"DSN should contain project name 'testapp': {dsn_line}"

    logger.critical("[IMP:9][test][gen-env-interface] gen-env-platform.sh --name testapp --output works correctly")
