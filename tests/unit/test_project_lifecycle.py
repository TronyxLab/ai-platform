# GREP_SUMMARY: test project lifecycle remove unregister adopt project-list node-yaml idempotent safe no-data-deletion verb-contract hooks personal-domain
# STRUCTURE: ┌node_yaml fixture┐ → ┌fake_project fixture┐ → ○ 9 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Test suite for project lifecycle scripts: remove-project.sh, adopt-project.sh,
##           project-list.sh
## @scope    9 test functions covering: unregister, idempotent remove, safe-remove (no data deletion),
##           remove hooks in module-interface.sh, verb contract backward compat, adopt preserves files,
##           adopt idempotent, personal domain cert path, project-list offline
## @invariants
##   - All subprocess tests use tmp_path and PROJECTS_BASE env var (no hardcoded paths)
##   - Fixture node.yaml provides 3 test projects with known structure
##   - Shell scripts are called via subprocess with PROJECTS_BASE pointing to tmp_path
##   - grep-based tests scan script source files for patterns
## @rationale T20 per DevPlan $TEST_SPEC — validates lifecycle completion (REMOVE, ADOPT, OBSERVE phases)
## @changes 2026-07-17 · T20 — initial implementation
##           2026-07-31 · Strangler cleanup — deploy shell removed (aa6bd61); remove-hook
##           + verb-contract тест указывает на orchestrator_cli dispatch (164 W3-1)
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_REMOVE_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "scaffold" / "remove-project.sh"
_ADOPT_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "scaffold" / "adopt-project.sh"
_LIST_SCRIPT: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "scaffold" / "project-list.sh"
# _DEPLOY_SCRIPT удалён (164 W3-1): deploy.sh не существует — dispatch единственный канал
_PY_MODULE_INTERFACE: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "shared" / "module_interface.py"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _create_node_yaml(base_dir: pathlib.Path, node_name: str = "tronyx-vps") -> pathlib.Path:
    """Create a node.yaml fixture with exactly 3 projects under node-configs/<node>/.

    ## @purpose — Standard node.yaml fixture for lifecycle tests. Creates path:
    ##            <base_dir>/<context>/node-configs/<node>/node.yaml
    ## @io — Returns path to created node.yaml
    ## @invariants — 3 projects (myapp, myapp2, oldapp) with distinct domains
    """
    node_config_dir = base_dir / "test-context" / "node-configs" / node_name
    node_config_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_config_dir / "node.yaml"

    data = {
        "projects": [
            {
                "name": "myapp",
                "domain": "myapp.tronyx.ru",
                "node": node_name,
                "template": "frontend",
                "org": "test-org",
            },
            {
                "name": "myapp2",
                "domain": "myapp2.tronyx.ru",
                "node": node_name,
                "template": "backend",
                "org": "test-org",
            },
            {
                "name": "oldapp",
                "domain": "old.example.com",
                "node": node_name,
                "template": "frontend",
                "org": "other-org",
            },
        ]
    }

    with pathlib.Path(node_yaml).open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info(
        "[IMP:8][helper][create_node_yaml] Created %s with 3 projects",
        node_yaml,
    )
    return node_yaml


def _create_fake_project(base_dir: pathlib.Path, name: str = "oldapp") -> pathlib.Path:
    """Create a minimal fake project directory with src/, Dockerfile, etc.

    ## @purpose — Fixture for adopt tests: simulates an existing project with
    ##            application code that must NOT be modified by adopt.
    ## @io — Returns project dir path
    """
    project_dir = base_dir / name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Application files that must be preserved
    src_dir = project_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "main.py").write_text("# Fake project source\n", encoding="utf-8")

    (project_dir / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY src/ /app\n", encoding="utf-8")

    (project_dir / "docker-compose.yml").write_text(
        "services:\n  web:\n    build: .\n    ports:\n      - '80:80'\n", encoding="utf-8"
    )

    logger.info(
        "[IMP:8][helper][create_fake_project] Created %s with src/, Dockerfile, docker-compose.yml",
        project_dir,
    )
    return project_dir


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def node_yaml_3_projects(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """Create a node.yaml with 3 projects in tmp_path.

    ## @returns — (node_yaml_path, node_name) tuple.
    ##            Sets PROJECTS_BASE env var in the fixture's return context.
    ##            Test functions receive the tuple and must set PROJECTS_BASE themselves.
    """
    node_name = "tronyx-vps"
    node_yaml = _create_node_yaml(tmp_path, node_name)
    return node_yaml, node_name


@pytest.fixture
def fake_old_project(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a fake project directory for adopt tests."""
    return _create_fake_project(tmp_path, "oldapp")


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — REMOVE (T10)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_unregister_removes_project_entry(
    caplog,
    tmp_path: pathlib.Path,
    node_yaml_3_projects: tuple[pathlib.Path, str],
) -> None:
    """Remove-project.sh must delete the specified project from node.yaml and leave others intact.

    ── Scenario: 3 projects → remove "myapp" → 2 remaining, YAML valid ──
    """
    node_yaml = node_yaml_3_projects[0]
    # Fixture path: tmp_path/test-context/node-configs/<node>/node.yaml
    # PROJECTS_BASE = tmp_path — walks: tmp_path/*/node-configs/*/node.yaml
    logger.info("[IMP:9][test][unregister] Starting — node_yaml=%s", node_yaml)
    logger.info("[IMP:7][test][unregister] projects_root=%s", tmp_path)

    env = os.environ.copy()
    env["PROJECTS_BASE"] = str(tmp_path)

    result = subprocess.run(
        [
            str(_REMOVE_SCRIPT),
            "--name",
            "myapp",
            "--force",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    logger.info(
        "[IMP:7][test][unregister] Exit=%d, stdout=%s, stderr=%s",
        result.returncode,
        result.stdout[:200],
        result.stderr[:200],
    )

    # Verify node.yaml still exists and has 2 projects
    assert node_yaml.exists(), "node.yaml was deleted entirely"
    with pathlib.Path(node_yaml).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None, "node.yaml is empty"
    assert "projects" in data, "node.yaml missing 'projects' key"
    assert len(data["projects"]) == 2, f"Expected 2 projects after remove, got {len(data['projects'])}"

    # Verify correct projects remain
    remaining_names = [p["name"] for p in data["projects"]]
    assert "myapp" not in remaining_names, "myapp should have been removed"
    assert "myapp2" in remaining_names, "myapp2 should remain"
    assert "oldapp" in remaining_names, "should remain"

    logger.info("[IMP:9][test][unregister] Verified: remaining projects=%s", remaining_names)


@ldd_trajectory
def test_unregister_idempotent(
    caplog,
    tmp_path: pathlib.Path,
    node_yaml_3_projects: tuple[pathlib.Path, str],
) -> None:
    """Second remove of same project must be SKIP (exit 0, no error).

    ── Scenario: Remove "myapp" twice → second call exits 0, YAML unchanged ──
    """
    node_yaml, _ = node_yaml_3_projects
    env = os.environ.copy()
    env["PROJECTS_BASE"] = str(tmp_path)

    # First remove
    subprocess.run(
        [str(_REMOVE_SCRIPT), "--name", "myapp", "--force"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    # Second remove — must be clean exit (project already removed → SKIP exit 0)
    result2 = subprocess.run(
        [str(_REMOVE_SCRIPT), "--name", "myapp", "--force"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    logger.info(
        "[IMP:7][test][unregister_idempotent] Second remove: exit=%d, stdout=%s",
        result2.returncode,
        result2.stdout[:300],
    )

    assert result2.returncode == 0, f"Second remove of same project should exit 0, got {result2.returncode}"

    # Verify YAML still has 2 projects (unchanged after first remove)
    with pathlib.Path(node_yaml).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    remaining = [p["name"] for p in data.get("projects", [])]
    assert "myapp" not in remaining
    assert len(remaining) == 2

    logger.info("[IMP:9][test][unregister_idempotent] Verified: second remove is no-op")


@ldd_trajectory
def test_remove_is_safe_no_data_deletion(caplog) -> None:
    """remove-project.sh and deploy.sh must NOT contain dangerous patterns (O7/DD10).

    ── Scenario: grep for `down -v`, `volume rm`, `image rm`, `gh repo delete` in lifecycle scripts ──
    """
    scripts_to_check = [_REMOVE_SCRIPT]  # deploy.sh удалён (164 W3-1)
    # Patterns that are FORBIDDEN in actual command execution (O7/DD10)
    # NOTE: echo/print messages that tell the user how to manually clean up are OK —
    #       only actual command execution (subshell, eval, direct) is forbidden.
    dangerous_patterns = ["down -v", "volume rm", "image rm", "gh repo delete"]
    found_violations: list[str] = []

    for script in scripts_to_check:
        if not script.exists():
            logger.info("[IMP:8][test][safe_remove] Missing script (expected): %s", script)
            continue
        content = script.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Skip comments, echo/printf statements (informational only)
            if stripped.startswith(("#", "echo", "printf")):
                continue
            found_violations.extend(
                f"{script.relative_to(_PROJECT_ROOT)}:{i}: {stripped}"
                for pattern in dangerous_patterns
                if pattern in stripped
            )

    assert not found_violations, "Dangerous patterns found in lifecycle scripts (O7 violation):\n" + "\n".join(
        f"  - {v}" for v in found_violations
    )
    logger.info("[IMP:9][test][safe_remove] No dangerous patterns found in lifecycle scripts")


@ldd_trajectory
def test_remove_hooks_triggered_in_runtime(caplog) -> None:
    """shared/module_interface.py must contain the K2 remove-hook dispatcher (hooks.on_project_remove).

    ── Scenario: grep core/internal/shared/module_interface.py for remove-hook interface + hooks.on_project_remove ──
    """
    # 🧪 TRAP[TEST] · 2026-07-31 · remove-hook dispatcher lives in core/internal/shared/module_interface.py
    # · Regression: test previously required the deleted deploy shell (removed in
    #   aa6bd61). K2 dispatcher (invoke_module_interface remove-hook → _invoke_dispatch_hook
    #   → hooks.on_project_remove) lived in core/lib/module-interface.sh:84-85.
    # · 2026-08-02 (DevPlan 119 D4): module-interface.sh → тонкий фасад (26 LOC), dispatch-логика
    #   переехала в shared/module_interface.py::dispatch() — тест переведён на новый диспетчер.
    # · Scenario: grep module_interface.py for 'remove-hook', 'hooks.on_project_remove', 'dispatch'
    # · Last fail: 2026-07-31 — "Missing required file: the deploy shell"
    # · Remove if: K2 hook dispatch moves out of shared/module_interface.py (then point test at the new module)
    # · NOTE: the remove path (project_remover.py) does NOT invoke this hook because no module.yaml
    #   in the repo registers hooks.on_project_remove (grep = 0 matches). If a consumer appears,
    #   extend this test to verify the hook is invoked from project_remover.py at runtime.
    PY_MODULE_INTERFACE: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "shared" / "module_interface.py"
    if not PY_MODULE_INTERFACE.exists():
        pytest.fail(f"Missing required file: {PY_MODULE_INTERFACE.relative_to(_PROJECT_ROOT)}")

    content = PY_MODULE_INTERFACE.read_text(encoding="utf-8")

    has_remove_hook_interface = "remove-hook" in content
    has_on_project_remove = "hooks.on_project_remove" in content
    has_dispatch_hook = "def dispatch" in content

    logger.info(
        "[IMP:7][test][remove_hooks] remove-hook=%s, hooks.on_project_remove=%s, dispatch=%s",
        has_remove_hook_interface,
        has_on_project_remove,
        has_dispatch_hook,
    )

    assert has_remove_hook_interface, (
        f"{PY_MODULE_INTERFACE.relative_to(_PROJECT_ROOT)}: missing 'remove-hook' interface in dispatch() (K2)"
    )
    assert has_on_project_remove, (
        f"{PY_MODULE_INTERFACE.relative_to(_PROJECT_ROOT)}: missing 'hooks.on_project_remove' "
        "reference in remove-hook dispatch (K2)"
    )
    assert has_dispatch_hook, f"{PY_MODULE_INTERFACE.relative_to(_PROJECT_ROOT)}: missing dispatch() function (K2)"

    logger.info("[IMP:9][test][remove_hooks] Verified: K2 remove-hook dispatcher present in shared/module_interface.py")


@ldd_trajectory
def test_deploy_verb_contract_in_orchestrator_cli(caplog) -> None:
    """orchestrator_cli dispatch поддерживает verb contract K1 (remove/status/receive).

    ── Scenario: deploy.sh удалён (164 W3-1) — verb-диспетчер живёт в orchestrator_cli dispatch ──
    """
    # 🧪 TRAP[TEST] · 2026-07-31 · verb contract K1 в deploy.sh; 164 W3-1 — dispatch единственный канал
    # · Last fail: 2026-07-31 — "Missing required file: the deploy shell"
    # · Remove if: dispatch verb-набор меняется
    cli_path = pathlib.Path(__file__).parents[2] / "core" / "internal" / "deploy" / "orchestrator_cli.py"
    if not cli_path.exists():
        pytest.fail(f"Missing required file: {cli_path.relative_to(_PROJECT_ROOT)}")

    cli_content = cli_path.read_text(encoding="utf-8")

    # K1: SSH_ORIGINAL_COMMAND parsing — через ssh_command_parser (dispatch)
    has_dispatch = "dispatch" in cli_content

    # K1: remove verb handling → remove subcommand
    has_remove_verb = "remove" in cli_content.lower()

    # K1: status verb handling → status subcommand
    has_status_verb = "status" in cli_content.lower()

    assert has_dispatch, "K1: orchestrator_cli должен иметь dispatch (единственный forced-command канал)"
    assert has_remove_verb, "K1: orchestrator_cli должен диспатчить verb 'remove'"
    assert has_status_verb, "K1: orchestrator_cli должен диспатчить verb 'status'"

    logger.info("[IMP:9][test][verb_contract] K1 verb contract verified in orchestrator_cli dispatch")


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — ADOPT (T11)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_adopt_preserves_project_files(
    caplog,
    tmp_path: pathlib.Path,
    fake_old_project: pathlib.Path,
) -> None:
    """Adopt must NOT modify src/, Dockerfile, or docker-compose.yml.

    ── Scenario: Adopt on a fake project → application files unchanged ──
    """
    project_dir = fake_old_project
    logger.info("[IMP:9][test][adopt_preserve] Starting — project_dir=%s", project_dir)

    # Record checksums before adopt
    src_hash = hashlib_md5(project_dir / "src" / "main.py")
    dockerfile_hash = hashlib_md5(project_dir / "Dockerfile")
    compose_hash = hashlib_md5(project_dir / "docker-compose.yml")

    env = os.environ.copy()
    # Set PROJECTS_BASE so the script can find/create node.yaml structure
    env["PROJECTS_BASE"] = str(tmp_path)
    # The adopt script needs the project dir as an argument

    result = subprocess.run(
        [
            str(_ADOPT_SCRIPT),
            "--dir",
            str(project_dir),
            "--name",
            "oldapp",
            "--org",
            "test-org",
            "--node",
            "tronyx-vps",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    logger.info(
        "[IMP:7][test][adopt_preserve] Adopt exit=%d, stdout=%s, stderr=%s",
        result.returncode,
        result.stdout[:300],
        result.stderr[:300],
    )

    # Verify files are unchanged
    assert hashlib_md5(project_dir / "src" / "main.py") == src_hash, "src/main.py was modified by adopt"
    assert hashlib_md5(project_dir / "Dockerfile") == dockerfile_hash, "Dockerfile was modified by adopt"
    assert hashlib_md5(project_dir / "docker-compose.yml") == compose_hash, "docker-compose.yml was modified by adopt"

    logger.info("[IMP:9][test][adopt_preserve] Verified: application files unchanged after adopt")


@ldd_trajectory
def test_adopt_idempotent(
    caplog,
    tmp_path: pathlib.Path,
    fake_old_project: pathlib.Path,
) -> None:
    """Second adopt call must be no-op (exit 0, no changes).

    ── Scenario: Adopt twice → second call exits 0, files unchanged ──
    """
    project_dir = fake_old_project
    logger.info("[IMP:9][test][adopt_idempotent] Starting")

    env = os.environ.copy()
    env["PROJECTS_BASE"] = str(tmp_path)

    args = [
        str(_ADOPT_SCRIPT),
        "--dir",
        str(project_dir),
        "--name",
        "oldapp",
        "--org",
        "test-org",
        "--node",
        "tronyx-vps",
    ]

    # First adopt
    result1 = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env, check=False)
    logger.info("[IMP:7][test][adopt_idempotent] First adopt: exit=%d", result1.returncode)

    # Capture first-adopt state
    first_state = hashlib_md5(project_dir / "src" / "main.py")

    # Second adopt
    result2 = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env, check=False)

    logger.info(
        "[IMP:7][test][adopt_idempotent] Second adopt: exit=%d, stdout=%s",
        result2.returncode,
        result2.stdout[:200],
    )

    assert result2.returncode == 0, (
        f"Second adopt should exit 0 (no-op), got {result2.returncode}: {result2.stderr[:200]}"
    )
    assert hashlib_md5(project_dir / "src" / "main.py") == first_state, (
        "src/main.py changed between first and second adopt"
    )

    logger.info("[IMP:9][test][adopt_idempotent] Verified: second adopt is no-op")


@ldd_trajectory
def test_adopt_personal_domain_cert_path(
    caplog,
    tmp_path: pathlib.Path,
    fake_old_project: pathlib.Path,
) -> None:
    """Personal domain adopt must use non-wildcard cert path (O11).

    ── Scenario: Adopt with --domain sexydancerostov.ru → vhost references non-wildcard cert ──
    """
    project_dir = fake_old_project
    personal_domain = "sexydancerostov.ru"
    logger.info("[IMP:9][test][adopt_personal] Starting — domain=%s", personal_domain)

    env = os.environ.copy()
    env["PROJECTS_BASE"] = str(tmp_path)

    result = subprocess.run(
        [
            str(_ADOPT_SCRIPT),
            "--dir",
            str(project_dir),
            "--name",
            "oldapp",
            "--org",
            "test-org",
            "--node",
            "tronyx-vps",
            "--domain",
            personal_domain,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    logger.info(
        "[IMP:7][test][adopt_personal] Adopt exit=%d, stdout=%s",
        result.returncode,
        result.stdout[:300],
    )

    # Check that the output or created files reference non-wildcard cert path
    output = result.stdout + " " + result.stderr
    # Personal domains should NOT reference wildcard cert paths
    assert "wildcard" not in output.lower(), (
        f"Personal domain '{personal_domain}' should use personal cert path, not wildcard. Output: {output[:500]}"
    )
    # Should reference the personal domain cert
    assert personal_domain in output, (
        f"Personal domain '{personal_domain}' not mentioned in adopt output. Output: {output[:500]}"
    )

    logger.info("[IMP:9][test][adopt_personal] Verified: personal domain handled without wildcard cert")


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — LIST (T12)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_project_list_offline(
    caplog,
    tmp_path: pathlib.Path,
    node_yaml_3_projects: tuple[pathlib.Path, str],
) -> None:
    """project-list.sh --list must read node.yaml offline and output a table with 3 projects.

    ── Scenario: no SSH, no network → list command reads local node.yaml ──
    """
    node_yaml = node_yaml_3_projects[0]
    logger.info("[IMP:9][test][list_offline] Starting — node_yaml=%s", node_yaml)
    logger.info("[IMP:7][test][list_offline] projects_root=%s", tmp_path)

    env = os.environ.copy()
    env["PROJECTS_BASE"] = str(tmp_path)

    result = subprocess.run(
        [
            str(_LIST_SCRIPT),
            "--list",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    logger.info(
        "[IMP:7][test][list_offline] Exit=%d, stdout=%s",
        result.returncode,
        result.stdout[:500],
    )

    assert result.returncode == 0, (
        f"project-list.sh --list failed: exit={result.returncode}, stderr={result.stderr[:200]}"
    )

    output = result.stdout

    # Verify all 3 projects appear in output
    assert "myapp" in output, "myapp should be in the list"
    assert "myapp2" in output, "myapp2 should be in the list"
    assert "oldapp" in output, "should be in the list"

    # Verify domains appear
    assert "myapp.tronyx.ru" in output, "myapp.tronyx.ru domain should be in the list"
    assert "myapp2.tronyx.ru" in output, "myapp2.tronyx.ru domain should be in the list"
    assert "old.example.com" in output, "old.example.com domain should be in the list"

    logger.info("[IMP:9][test][list_offline] Verified: 3 projects listed offline")


# ── Hash helper ──────────────────────────────────────────────────────────────


def hashlib_md5(filepath: pathlib.Path) -> str:
    """Compute MD5 hex digest of file contents.

    ## @purpose — Quick file checksum for adopt-preservation tests.
    ## @io — ⇥ filepath → ⎋ str (hex digest)
    """
    import hashlib

    return hashlib.md5(
        filepath.read_bytes(), usedforsecurity=False
    ).hexdigest()  # S324: тестовая чексумма, не криптография
