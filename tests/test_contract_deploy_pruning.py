#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-project prune prune_old_images KEEP_IMAGES docker images rmi fallback pattern bash subprocess
# STRUCTURE: ▶ source deploy-project.sh → ∋ prune_old_images → ◇ docker compose config? → ⊕ image_pattern → ◇ docker images count > KEEP -> ⊕ tail -n +N → ○ for img: docker rmi → ⎋ removed=N failed=M
# region MODULE_CONTRACT
## @purpose  Contract tests for deploy-project.sh prune_old_images(). Verifies image
##           retention policy (KEEP_IMAGES=3 via PLATFORM_DEPLOY_KEEP_IMAGES env),
##           fallback image_pattern when docker compose config fails, and graceful
##           error handling for docker rmi failures.
## @scope    Five test cases: exact retention, fallback pattern, rmi error handling,
##           no-op when count <= keep, and empty image list. All use subprocess
##           isolation with mock docker commands.
## @invariants
##   - prune_old_images keeps exactly KEEP_IMAGES most recent images
##   - KEEP_IMAGES is readonly in deploy-project.sh — use PLATFORM_DEPLOY_KEEP_IMAGES env
##   - When docker compose config fails, falls back to PROJECT name as pattern
##   - docker rmi errors are logged but non-fatal (function continues)
##   - No-op when image count <= KEEP_IMAGES
##   - All tests use tmp_path for isolation (Zero Hardcode Rule)
## @rationale Q: Why test all docker rmi error paths?
##            A: Docker images referenced by multiple tags cannot be removed without
##            --force. Graceful error handling prevents cascade failures.
## @changes CREATED: 2026-07-17 | T3: Contract tests — deploy-project.sh (pruning)
# endregion MODULE_CONTRACT

import os
import pathlib
import subprocess

import pytest
from conftest import assert_ldd_stderr

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DEPLOY_SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, "core", "internal", "deploy", "deploy-project.sh")


# ── Helpers ─────────────────────────────────────────────────────────────────


# region FUNC__run_bash
## @purpose  Source deploy-project.sh, remove traps, then run provided bash code
##           with a comprehensive docker mock that controls docker compose config,
##           docker images list, and docker rmi behavior.
##           KEEP_IMAGES is readonly — use PLATFORM_DEPLOY_KEEP_IMAGES env var.
## @io       ⇥ (tmp_path, code, env, docker_mock_code) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 15s timeout
def _run_bash(
    tmp_path: pathlib.Path,
    code: str,
    env: dict[str, str] | None = None,
    docker_mock_code: str | None = None,
) -> subprocess.CompletedProcess:
    script = tmp_path / "test_prune.sh"
    deploy_path_escaped = str(DEPLOY_SCRIPT_PATH)

    # Default docker mock — handles all subcommands used by prune_old_images
    default_docker_mock = (
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        config)\n"
        '          echo "services:"\n'
        '          echo "  test-app:"\n'
        '          echo "    image: registry.io/test/app:latest"\n'
        "          ;;\n"
        "        *) return 0 ;;\n"
        "      esac\n"
        "      ;;\n"
        "    images)\n"
        '      echo "sha256:aaa registry.io/test/app 2026-07-17"\n'
        '      echo "sha256:bbb registry.io/test/app 2026-07-10"\n'
        '      echo "sha256:ccc registry.io/test/app 2026-07-01"\n'
        '      echo "sha256:ddd registry.io/test/app 2026-06-15"\n'
        '      echo "sha256:eee registry.io/test/app 2026-06-01"\n'
        "      ;;\n"
        "    rmi)\n"
        "      return 0\n"
        "      ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )

    docker_mock = docker_mock_code if docker_mock_code is not None else default_docker_mock

    script_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'logger() { local tag="$2"; shift 2; echo "[MOCK:logger] tag=" "$tag" "msg=$*" >&2; }\n'
        f"{docker_mock}\n"
        "export -f logger docker\n"
        f'source "{deploy_path_escaped}"\n'
        "trap - ERR EXIT\n"
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    # PROJECTS_BASE is readonly — ensure default
    full_env.setdefault("PROJECTS_BASE", "/opt/projects")
    if env:
        full_env.update(env)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        env=full_env,
    )


# endregion FUNC__run_bash


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: prune_old_images
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_prune_enforces_keep
@pytest.mark.contract
## @purpose  prune_old_images removes images beyond KEEP_IMAGES=3, keeping the
##           3 most recent images based on CreatedAt date (sorted descending).
## @scenario  5 mock images → prune_old_images(KEEP=3) → assert 2 removed
##            KEEP_IMAGES is readonly, set via PLATFORM_DEPLOY_KEEP_IMAGES env.
def test_prune_enforces_keep(tmp_path: pathlib.Path) -> None:
    """
    # ▶ 5 docker images, PLATFORM_DEPLOY_KEEP_IMAGES=3 → prune_old_images
    #   → ◇ 2 removed → ⎋ pass
    """
    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-project"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune complete"\n'
    )

    result = _run_bash(tmp_path, code, env={"PLATFORM_DEPLOY_KEEP_IMAGES": "3"})

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "Pruning old images",
            "Prune complete: removed=2",
        ],
    )

    print("[IMP:9][test_prune_enforces_keep] PASS: 2 of 5 images removed (KEEP_IMAGES=3)")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_enforces_keep


# region FUNC_test_prune_default_keep
@pytest.mark.contract
## @purpose  prune_old_images uses default KEEP_IMAGES=3 when PLATFORM_DEPLOY_KEEP_IMAGES
##           env var is not set (readonly default from deploy-project.sh).
## @scenario  5 images, no override → assert default 3 kept, 2 removed
def test_prune_default_keep(tmp_path: pathlib.Path) -> None:
    """
    # ▶ 5 images, no KEEP_IMAGES override → default KEEP=3 → 2 removed → ⎋ pass
    """
    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-project"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune complete"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "Pruning old images",
            "Prune complete: removed=2",
        ],
    )

    print("[IMP:9][test_prune_default_keep] PASS: default KEEP_IMAGES=3 enforced")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_default_keep


# region FUNC_test_prune_fallback_pattern
@pytest.mark.contract
## @purpose  When docker compose config fails, prune_old_images falls back to
##           PROJECT name as image_pattern for grepping docker images.
## @scenario  docker compose config fails → fallback to PROJECT → still correct removal
def test_prune_fallback_pattern(tmp_path: pathlib.Path) -> None:
    """
    # ▶ docker compose config fails → fallback to PROJECT="test-app" → ◇ pruning works → ⎋ pass
    """
    mock_with_fallback = (
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        config) return 1 ;;\n"  # Simulate failure
        "        *) return 0 ;;\n"
        "      esac\n"
        "      ;;\n"
        "    images)\n"
        '      echo "sha256:aaa test-app-image 2026-07-17"\n'
        '      echo "sha256:bbb test-app-image 2026-07-10"\n'
        '      echo "sha256:ccc test-app-image 2026-07-01"\n'
        '      echo "sha256:ddd test-app-image 2026-06-15"\n'
        '      echo "sha256:eee test-app-image 2026-06-01"\n'
        "      ;;\n"
        "    rmi) return 0 ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )

    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-app"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune complete with fallback"\n'
    )

    result = _run_bash(tmp_path, code, env={"PLATFORM_DEPLOY_KEEP_IMAGES": "3"}, docker_mock_code=mock_with_fallback)

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "docker compose config failed",
            "Prune complete: removed=2",
        ],
    )

    print("[IMP:9][test_prune_fallback_pattern] PASS: fallback to PROJECT name when compose config fails")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_fallback_pattern


# region FUNC_test_prune_rmi_error_handling
@pytest.mark.contract
## @purpose  prune_old_images handles docker rmi failures gracefully — failed
##           removals are counted but don't abort the function.
## @scenario  docker rmi fails for 1 image → assert removed=1 failed=1
def test_prune_rmi_error_handling(tmp_path: pathlib.Path) -> None:
    """
    # ▶ docker rmi fails for 1 image → prune_old_images → ◇ removed=1 failed=1 → ⎋ pass
    """
    mock_with_rmi_failure = (
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        config)\n"
        '          echo "services:"\n'
        '          echo "  test-app:"\n'
        '          echo "    image: registry.io/test/app:latest"\n'
        "          ;;\n"
        "        *) return 0 ;;\n"
        "      esac\n"
        "      ;;\n"
        "    images)\n"
        '      echo "sha256:aaa registry.io/test/app 2026-07-17"\n'
        '      echo "sha256:bbb registry.io/test/app 2026-07-10"\n'
        '      echo "sha256:ccc registry.io/test/app 2026-07-01"\n'
        '      echo "sha256:ddd registry.io/test/app 2026-06-15"\n'
        '      echo "sha256:eee registry.io/test/app 2026-06-01"\n'
        "      ;;\n"
        "    rmi)\n"
        '      if echo "$*" | grep -q ddd; then\n'
        '        echo "Error: image referenced by multiple tags" >&2\n'
        "        return 1\n"
        "      fi\n"
        "      return 0\n"
        "      ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )

    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-project"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune with rmi errors"\n'
    )

    result = _run_bash(tmp_path, code, env={"PLATFORM_DEPLOY_KEEP_IMAGES": "3"}, docker_mock_code=mock_with_rmi_failure)

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "Pruning old images",
            "Could not remove image",
            "Prune complete: removed=1 failed=1",
        ],
    )

    print("[IMP:9][test_prune_rmi_error_handling] PASS: rmi failures counted, function continues")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_rmi_error_handling


# region FUNC_test_prune_noop_when_below_keep
@pytest.mark.contract
## @purpose  prune_old_images does nothing when image count <= KEEP_IMAGES.
## @scenario  2 images, KEEP_IMAGES=3 → assert no-op log "nothing to prune"
def test_prune_noop_when_below_keep(tmp_path: pathlib.Path) -> None:
    """
    # ▶ 2 images, KEEP=3 → prune_old_images → ◇ "nothing to prune" → ⎋ pass
    """
    mock_few_images = (
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        config)\n"
        '          echo "services:"\n'
        '          echo "  test-app:"\n'
        '          echo "    image: registry.io/test/app:latest"\n'
        "          ;;\n"
        "        *) return 0 ;;\n"
        "      esac\n"
        "      ;;\n"
        "    images)\n"
        '      echo "sha256:aaa registry.io/test/app 2026-07-17"\n'
        '      echo "sha256:bbb registry.io/test/app 2026-07-10"\n'
        "      ;;\n"
        "    rmi) return 0 ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )

    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-project"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune noop"\n'
    )

    result = _run_bash(tmp_path, code, env={"PLATFORM_DEPLOY_KEEP_IMAGES": "3"}, docker_mock_code=mock_few_images)

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "Pruning old images",
            "nothing to prune",
        ],
    )

    print("[IMP:9][test_prune_noop_when_below_keep] PASS: no-op when count ≤ KEEP")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_noop_when_below_keep


# region FUNC_test_prune_empty_images
@pytest.mark.contract
## @purpose  prune_old_images handles empty docker images list gracefully.
## @scenario  docker images returns nothing → assert "No images found" log
def test_prune_empty_images(tmp_path: pathlib.Path) -> None:
    """
    # ▶ docker images returns empty → prune_old_images → ◇ "No images found" → ⎋ pass
    """
    mock_empty = (
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        config)\n"
        '          echo "services:"\n'
        '          echo "  test-app:"\n'
        '          echo "    image: registry.io/test/app:latest"\n'
        "          ;;\n"
        "        *) return 0 ;;\n"
        "      esac\n"
        "      ;;\n"
        "    images)\n"
        "      return 0\n"
        "      ;;\n"
        "    rmi) return 0 ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )

    code = (
        'SERVICE_NAME="test-app"\n'
        'PROJECT="test-project"\n'
        "prune_old_images\n"
        'log_imp 9 "test" "Verification: prune empty"\n'
    )

    result = _run_bash(tmp_path, code, docker_mock_code=mock_empty)

    assert_ldd_stderr(result, expected_patterns=["No images found"])

    print("[IMP:9][test_prune_empty_images] PASS: handles empty image list gracefully")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_prune_empty_images
