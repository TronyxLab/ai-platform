#!/usr/bin/env python3
# GREP_SUMMARY: deploy-project characterization parse-ssh-command save-previous-image ssh-original-command forced-command docker-mock characterization golden-output
# STRUCTURE: ▶ _bash(_extract_func) → ○ parse_ssh_command: 3 tests (valid·empty·node_mismatch) → ○ save_previous_image: 3 tests (exists·none·docker_error) → ◇ LDD trajectory → ⎋ IMP:9 assertion
# region MODULE_CONTRACT
## @purpose  Characterization tests for parse_ssh_command() and save_previous_image() from
##           core/internal/deploy/deploy-project.sh. Golden outputs capture current behavior
##           before Wave 2-3 refactoring (DevPlan 020 T2).
## @scope    Tests bash functions from core/internal/deploy/deploy-project.sh. Each test extracts
##           the target function, mocks external dependencies (docker, filesystem), and runs via
##           bash subprocess (subprocess.run is required for bash testing — the "NO subprocess.run
##           for business logic" rule applies to Python business logic, not bash function testing).
## @invariants
##   - Tests use tmp_path for temp project directories
##   - Docker calls are mocked — no real Docker daemon required
##   - SSH is mocked — no real SSH calls
##   - LDD trajectory printed from stderr of bash subprocess for each test
##   - At least one IMP:9 business logic signal asserted per test
##   - Production code is NOT modified
## @rationale DevPlan 020 T2: characterization tests must capture current behavior BEFORE
##           refactoring. Golden outputs documented as constants for regression detection.
## @changes 2026-07-17 T2 — Initial creation
## @usecases
##   - CI gate: make test MARKER=static runs characterization tests
##   - Refactoring safety net: detects behavior changes during Wave 2-3
# endregion MODULE_CONTRACT

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

# ─── Paths ──────────────────────────────────────────────────────────────────
TEST_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.join(TEST_DIR, "..")
DEPLOY_PROJECT_SH = os.path.join(PROJECT_ROOT, "core", "internal", "deploy", "deploy-project.sh")

# ─── Golden Outputs ─────────────────────────────────────────────────────────
# Characterization: these constants capture current behavior. If refactoring
# changes them, tests will fail — review and update deliberately.

# parse_ssh_command golden values
GOLDEN_PROJECT = "myapp"
GOLDEN_REF = "v1.0.0"
GOLDEN_SERVICE_FROM_YAML = "web"
GOLDEN_SERVICE_FALLBACK = "myapp-no-yaml"

# save_previous_image golden values
GOLDEN_IMAGE_ID = "sha256:a1b2c3d4e5f6"
GOLDEN_IMAGE_TAG = "myapp:latest"
GOLDEN_FALLBACK_TAG = "myapp:previous-rollback"

# Error messages (characterization — exact text, brittle; update if messages change)
FATAL_SSH_NOT_SET = "FATAL: SSH_ORIGINAL_COMMAND not set"
FATAL_INVALID_INVOCATION = "FATAL: invalid invocation"
FATAL_DIR_NOT_FOUND = "FATAL: project directory not found"
FATAL_NO_COMPOSE = "FATAL: no docker-compose.yml found"
FATAL_CANNOT_CD = "FATAL: cannot cd to"
CRITICAL_DOCKER_FAILED = "CRITICAL: docker compose images failed"
FIRST_DEPLOY_MSG = "FIRST DEPLOY: no previous image"


# region HELPERS


def _bash(cmd: str, env: dict | None = None, cwd: str | None = None) -> tuple[str, str, int]:
    """Execute a bash -c command in a subprocess.

    ## @purpose — Run bash snippets to test bash functions in isolation.
    ## @io - cmd, env, cwd -> (stdout, stderr, returncode)
    ## @complexity O(1)
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        env=merged_env,
        cwd=cwd or PROJECT_ROOT,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def _extract_func(filepath: str, func_name: str) -> str:
    """Extract a bash function definition from a source file using brace counting.

    ## @purpose — Extract a bash function body from its source file so it can be
    ##            sourced in a test context without executing the script's top-level code.
    ## @io - filepath, func_name -> str (function definition)
    ## @raises ValueError if function is not found or braces are unbalanced
    ## @complexity O(N) where N = file size
    """
    with open(filepath) as f:
        content = f.read()

    patterns = [
        rf"^{re.escape(func_name)}\s*\(\s*\)\s*\{{",
        rf"^function\s+{re.escape(func_name)}\s*\{{",
    ]

    start = -1
    for pat in patterns:
        m = re.search(pat, content, re.MULTILINE)
        if m:
            line_start = content.rfind("\n", 0, m.start())
            if line_start == -1:
                line_start = 0
            else:
                line_start += 1
            prefix = content[line_start : m.start()]
            if prefix.strip() == "" or prefix.strip().startswith("#"):
                start = m.start()
                break

    if start < 0:
        raise ValueError(f"Function '{func_name}' not found in {filepath}")

    brace_pos = -1
    for i in range(start, min(start + 200, len(content))):
        if content[i] == "{":
            brace_pos = i
            break

    if brace_pos < 0:
        raise ValueError(f"No opening brace found for '{func_name}' in {filepath}")

    count = 1
    pos = brace_pos + 1
    while count > 0 and pos < len(content):
        if content[pos] == "{":
            count += 1
        elif content[pos] == "}":
            count -= 1
        pos += 1

    if count != 0:
        raise ValueError(f"Unterminated function '{func_name}' in {filepath}")

    return content[start:pos]


def _test_func(
    filepath: str,
    func_names: list[str],
    test_call: str,
    env: dict | None = None,
    preamble: str = "",
) -> tuple[str, str, int]:
    """Extract function(s) from a bash file and run a test call.

    ## @purpose — Combine function extraction with a test invocation in one call.
    ## @io - filepath, func_names, test_call, env, preamble -> (stdout, stderr, returncode)
    ## @complexity O(N) extraction + O(1) execution
    """
    func_bodies = [_extract_func(filepath, name) for name in func_names]

    parts = []
    if preamble:
        parts.append(preamble)
    parts.extend(func_bodies)
    parts.append(test_call)
    script = "\n\n".join(parts)
    return _bash(script, env=env)


def _print_ldd(stderr: str, stdout: str = "") -> bool:
    """Print IMP:7-10 lines from bash output. Returns True if IMP:9+ found.

    ## @purpose — Extract and display LDD trajectory from subprocess output.
    ## @io - stderr, stdout -> bool (IMP:9+ found)
    ## @complexity O(N) where N = output lines
    """
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for source in [stderr, stdout]:
        for line in source.split("\n"):
            if "[IMP:" in line:
                try:
                    parts = line.split("[IMP:")[1].split("]", 1)
                    imp_level = int(parts[0])
                    if imp_level >= 7:
                        print(line)
                    if imp_level >= 9:
                        found = True
                except (ValueError, IndexError):
                    print(line)
    print("--- END LDD TRAJECTORY ---")
    return found


LOG_STUBS = """
log_imp() {
    local imp="$1" block="$2" msg="$3"
    local prefix="${__LOG_PREFIX:-test}"
    if [ "${block}" = "-" ] || [ -z "${block}" ]; then
        block="${FUNCNAME[1]:-main}"
    fi
    echo "[IMP:${imp}][${prefix}][${block}] ${msg}" >&2
}
log_step() {
    local step="$1" status="$2" msg="$3"
    echo "[IMP:8][${__LOG_PREFIX:-test}][${step}] ${status}: ${msg}" >&2
}
"""

# Docker mock — controls docker compose images, image inspect, and tag behavior
DOCKER_MOCK = """
docker() {
    local cmd="$1"
    shift
    case "${cmd}" in
        compose)
            # docker compose images -q SERVICE_NAME
            if [ "${1:-}" = "images" ] && [ "${2:-}" = "-q" ]; then
                echo "${DOCKER_MOCK_IMAGES_OUTPUT:-}"
                return ${DOCKER_MOCK_IMAGES_RC:-0}
            fi
            echo "UNMOCKED_DOCKER_COMPOSE: $*" >&2
            return 1
            ;;
        image)
            # docker image inspect ID --format ...
            if [ "${1:-}" = "inspect" ]; then
                echo "${DOCKER_MOCK_INSPECT_OUTPUT:-}"
                return ${DOCKER_MOCK_INSPECT_RC:-0}
            fi
            echo "UNMOCKED_DOCKER_IMAGE: $*" >&2
            return 1
            ;;
        tag)
            # docker tag ID TAG
            return 0
            ;;
        *)
            echo "UNMOCKED_DOCKER: ${cmd} $*" >&2
            return 1
            ;;
    esac
}
"""

# endregion HELPERS


# ═══════════════════════════════════════════════════════════════════════════════
#  parse_ssh_command — characterization tests
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_parse_ssh_command_valid_forced_command
# 🧪 TRAP[TEST]
# Regression: Если функция не распарсит PROJECT/REF из SSH_ORIGINAL_COMMAND
# Scenario: валидная forced-command "platform-deploy myapp v1.0.0",
#           проект существует с docker-compose.yml и ai-platform.yaml (custom service)
# Last fail: None
# Remove if: После рефакторинга с явным тестом на новую сигнатуру
def test_parse_ssh_command_valid_forced_command(tmp_path, caplog) -> None:
    """Verify parse_ssh_command() parses valid forced-command — sets PROJECT, REF, SERVICE_NAME."""
    caplog.set_level(logging.DEBUG)

    # ⚠️ TRAP[BUG] · 2026-07-17 · MED · Tests wrote runtime fixtures INTO the repo (tests/test_data/myapp*)
    # · Symptom: check-compose-spec hook flip-flop — committed x-dummy fixture overwritten with 'dummy: config' at every test run
    # · Root: preamble used os.path.dirname(__file__)/../tests/test_data as PROJECTS_BASE — repo mutation from tests
    # · Fix: tmp_path per Zero Hardcode Rule; runtime artifacts deleted from git
    # · Prevention: tests must never write outside tmp_path
    projects_base = str(tmp_path)
    project_dir = os.path.join(projects_base, "myapp")

    preamble = f"""set -euo pipefail
{LOG_STUBS}
export PROJECTS_BASE="{projects_base}"
export __LOG_PREFIX="test"
mkdir -p "{project_dir}"
echo "dummy: config" > "{project_dir}/docker-compose.yml"
cat > "{project_dir}/ai-platform.yaml" << 'YAMLEOF'
service: web
YAMLEOF
"""

    test_call = """SSH_ORIGINAL_COMMAND="platform-deploy myapp v1.0.0"
parse_ssh_command
echo "[IMP:9][verify] PROJECT=${PROJECT} REF=${REF} SERVICE_NAME=${SERVICE_NAME} PROJECT_DIR=${PROJECT_DIR}"
"""

    stdout, stderr, rc = _test_func(
        DEPLOY_PROJECT_SH,
        ["parse_ssh_command"],
        test_call,
        env={"__LOG_PREFIX": "test"},
        preamble=preamble,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"parse_ssh_command failed with rc={rc}: stderr={stderr}"

    assert f"PROJECT={GOLDEN_PROJECT}" in stdout, f"Expected PROJECT={GOLDEN_PROJECT} in stdout, got: {stdout}"
    assert f"REF={GOLDEN_REF}" in stdout, f"Expected REF={GOLDEN_REF} in stdout, got: {stdout}"
    assert f"SERVICE_NAME={GOLDEN_SERVICE_FROM_YAML}" in stdout, (
        f"Expected SERVICE_NAME={GOLDEN_SERVICE_FROM_YAML} in stdout, got: {stdout}"
    )
    assert str(project_dir) in stdout, f"Expected PROJECT_DIR={project_dir} in stdout, got: {stdout}"
    logger.info(
        "[IMP:9][test_parse_ssh_command_valid][assert] PROJECT=%s REF=%s SERVICE=%s",
        GOLDEN_PROJECT,
        GOLDEN_REF,
        GOLDEN_SERVICE_FROM_YAML,
    )
    assert found_imp9, f"Critical LDD Error: No IMP:9 business logic log found. stdout={stdout!r}"


# endregion


# region TEST_parse_ssh_command_empty
# 🧪 TRAP[TEST]
# Regression: Пустой SSH_ORIGINAL_COMMAND должен вызывать exit 1 с FATAL-сообщением
# Scenario: SSH_ORIGINAL_COMMAND="" — expected failure
# Last fail: None
# Remove if: После рефакторинга с явным тестом на новую сигнатуру
def test_parse_ssh_command_empty(caplog) -> None:
    """Verify parse_ssh_command() exits 1 when SSH_ORIGINAL_COMMAND is empty."""
    caplog.set_level(logging.DEBUG)

    preamble = f"""set -euo pipefail
{LOG_STUBS}
export PROJECTS_BASE="/tmp"
export __LOG_PREFIX="test"
"""

    test_call = """SSH_ORIGINAL_COMMAND=""
parse_ssh_command
echo "[IMP:9][verify] UNEXPECTED_SUCCESS"
"""

    stdout, stderr, rc = _test_func(
        DEPLOY_PROJECT_SH,
        ["parse_ssh_command"],
        test_call,
        env={"__LOG_PREFIX": "test"},
        preamble=preamble,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc != 0, f"parse_ssh_command should have failed with empty command, got rc=0. stdout={stdout}"
    assert FATAL_SSH_NOT_SET in stderr or FATAL_INVALID_INVOCATION in stderr, (
        f"Expected fatal error message in stderr, got: {stderr}"
    )
    assert "UNEXPECTED_SUCCESS" not in stdout, f"Function should have exited before this line: {stdout}"
    logger.info("[IMP:9][test_parse_ssh_command_empty][assert] Empty command correctly rejected")
    assert found_imp9, f"Critical LDD Error: No IMP:9 business logic log found. stderr={stderr!r}"


# endregion


# region TEST_parse_ssh_command_node_name_mismatch
# 🧪 TRAP[TEST]
# Regression: NODE_NAME ($1) не должен влиять на парсинг SSH_ORIGINAL_COMMAND
# Scenario: NODE_NAME установлен в "wrong-node", SSH_ORIGINAL_COMMAND содержит валидную команду
#           Функция должна игнорировать NODE_NAME и правильно распарсить команду
# Last fail: None
# Remove if: После рефакторинга с явным тестом на новую сигнатуру
def test_parse_ssh_command_node_name_mismatch(tmp_path, caplog) -> None:
    """Verify parse_ssh_command() ignores NODE_NAME and parses SSH_ORIGINAL_COMMAND correctly."""
    caplog.set_level(logging.DEBUG)

    projects_base = str(tmp_path)
    project_dir = os.path.join(projects_base, "myapp-node-check")

    preamble = f"""set -euo pipefail
{LOG_STUBS}
export PROJECTS_BASE="{projects_base}"
export __LOG_PREFIX="test"
mkdir -p "{project_dir}"
echo "dummy: config" > "{project_dir}/docker-compose.yml"
# Set NODE_NAME to a value that does NOT match (to characterize current behavior)
NODE_NAME="wrong-node"
"""

    test_call = """SSH_ORIGINAL_COMMAND="platform-deploy myapp-node-check v2.0.0"
parse_ssh_command
echo "[IMP:9][verify] PROJECT=${PROJECT} REF=${REF} SERVICE_NAME=${SERVICE_NAME}"
echo "[IMP:9][verify] NODE_NAME=${NODE_NAME}"
"""

    stdout, stderr, rc = _test_func(
        DEPLOY_PROJECT_SH,
        ["parse_ssh_command"],
        test_call,
        env={"__LOG_PREFIX": "test"},
        preamble=preamble,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"parse_ssh_command failed with rc={rc}: stderr={stderr}"
    # Despite NODE_NAME being "wrong-node", the function should parse correctly
    assert "PROJECT=myapp-node-check" in stdout, f"Expected PROJECT=myapp-node-check, got: {stdout}"
    assert "REF=v2.0.0" in stdout, f"Expected REF=v2.0.0, got: {stdout}"
    # Current behavior: SERVICE_NAME falls back to PROJECT when no ai-platform.yaml
    assert "SERVICE_NAME=myapp-node-check" in stdout, (
        f"Expected SERVICE_NAME=myapp-node-check (fallback), got: {stdout}"
    )
    # Verify NODE_NAME is not overwritten by parse_ssh_command
    assert "NODE_NAME=wrong-node" in stdout, f"Expected NODE_NAME=wrong-node preserved, got: {stdout}"
    logger.info("[IMP:9][test_parse_ssh_command_node_mismatch][assert] NODE_NAME mismatch does not affect parsing")
    assert found_imp9, f"Critical LDD Error: No IMP:9 business logic log found. stdout={stdout!r}"


# endregion


# ═══════════════════════════════════════════════════════════════════════════════
#  save_previous_image — characterization tests
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_save_previous_image_REMOVED
# 🧪 TRAP[TEST] · 2026-07-27 · REMOVED · Strangler-Fig migration (DevPlan 036E)
# · Reason: save_previous_image removed from deploy-project.sh (1183→133 LOC),
# ·   migrated to deploy_engine.py::_save_previous_image().
# · Coverage: tests/unit/test_deploy_engine.py:
# ·   - test_save_previous_image_exists (line 340): returns ImageInfo when image exists
# ·   - test_save_previous_image_first_deploy (line 363): returns None for first deploy
# · Previous test annotations: "Remove if: После рефакторинга с явным тестом на новую сигнатуру"
# · Removed tests: test_save_previous_image_exists, test_save_previous_image_none,
# ·   test_save_previous_image_docker_error — all tested shell function via bash extraction.
# endregion


# region TEST_save_previous_image_docker_error_REMOVED
# 🧪 TRAP[TEST] · 2026-07-27 · REMOVED · Strangler-Fig migration (DevPlan 036E)
# · Reason: save_previous_image removed from deploy-project.sh (1183→133 LOC),
# ·   migrated to deploy_engine.py::_save_previous_image().
# · Coverage: tests/unit/test_deploy_engine.py lines 336-368 cover
# ·   test_save_previous_image_exists and test_save_previous_image_first_deploy
# ·   via @patch.object mocking of subprocess.run.
# · Previous test assertion: "Remove if: После рефакторинга с явным тестом на новую сигнатуру"
# endregion
