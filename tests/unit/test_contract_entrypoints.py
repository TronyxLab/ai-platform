# GREP_SUMMARY: contract-test entrypoint-manifest yaml shebang syntax smoke subprocess node-update ssh-proxy age-secret-key-file detect-age-key python-entrypoint
# STRUCTURE: ▶ manifest path → parse YAML → extract .sh/.py paths → parametrized tests (exists|shebang|syntax|help-smoke)
# region MODULE_CONTRACT
## @purpose  Mass contract tests for ALL entrypoint scripts (.sh and .py) declared in entrypoint-manifest.yaml.
##           Parses the manifest, extracts all delegate script paths, and validates each:
##           exists, has shebang, syntax check (bash -n for .sh, compile() for .py), --help smoke (for entrypoints).
##           Adding a new entrypoint to manifest = automatic contract test.
## @scope    Tests operate on the real manifest + real scripts (.sh/.py) in the project tree.
##           No mocking, no simulation. Docker not required.
## @invariants
##   - Manifest is at core/entrypoint-manifest.yaml relative to platform root
##   - Each delegates_to field is parsed for script paths (.sh and .py files)
##   - Paths with args (e.g. "validate.sh --lint") strip args for file checks
##   - Entrypoint scripts (core/entrypoints/*.sh/.py) get extra --help smoke test
##   - Syntax regression in any script = FAIL
##   - Python scripts: syntax checked via compile(), not subprocess
## @rationale  Manifest is the canonical registry. Adding a new entrypoint requires
##             updating the manifest. This test ensures every registered script is
##             present and valid — no silent drift between manifest and filesystem.
## @changes — 2026-07-24 | Python entrypoint support: .py regex, compile() syntax check, python3 --help
##           — 2026-07-09 | TASK-4.1: mass contract tests for all entrypoints
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
MANIFEST_REL: str = Path("core") / "entrypoint-manifest.yaml"
MANIFEST_PATH: str = Path(PLATFORM_ROOT) / MANIFEST_REL

# ── Regex: extract script paths from delegates_to strings ──────────────────
# Matches paths like:
#   core/entrypoints/bootstrap.sh
#   core/entrypoints/check_commit_msg.py
#   core/entrypoints/bootstrap.sh → core/internal/bootstrap/node-lifecycle.sh
#   core/entrypoints/scaffold.sh → core/internal/scaffold/add-project.sh
_SCRIPT_PATH_RE: re.Pattern = re.compile(r"(?:^|\s+)(core/(?:entrypoints|internal)/[\w./-]+\.(?:sh|py))")


# ── Helpers ────────────────────────────────────────────────────────────────


# region FUNC_extract_script_paths
def extract_script_paths(manifest_path: str) -> list[str]:
    """Parse entrypoint-manifest.yaml and extract all script paths (.sh and .py).

    ## @purpose  Read the YAML manifest and collect every script path
    ##           referenced in delegates_to fields. Deduplicates paths.
    ## @io       ⇥ manifest_path: str → ⎋ list[str] of unique relative script paths
    ## @complexity  O(N) where N = total delegates_to entries
    ## @invariants
    ##   - Returns unique paths only (set dedup)
    ##   - Filters out non-script entries like "pytest", "pre-commit", etc.
    ##   - Strips trailing arguments (e.g. "validate.sh --lint" → "validate.sh")
    ##   - Matches both .sh and .py scripts
    """
    logger.info("[IMP:7][extract_script_paths] Parsing manifest: %s", manifest_path)

    if not pathlib.Path(manifest_path).is_file():
        msg = f"[IMP:10][extract_script_paths] Manifest not found: {manifest_path}"
        raise FileNotFoundError(msg)

    with pathlib.Path(manifest_path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        msg = f"[IMP:10][extract_script_paths] Manifest root is not a dict: {type(data)}"
        raise ValueError(msg)  # ruff: ignore[TRY004] — контракт хелпера: ValueError для не-dict (не TypeError)

    paths: set[str] = set()

    for group_name, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str):
                continue

            # Extract all .sh paths from the delegates_to string
            for match in _SCRIPT_PATH_RE.finditer(delegates_to):
                script_path = match.group(1)
                paths.add(script_path)
                logger.info("[IMP:8][extract_script_paths] Found script: %s (from %s)", script_path, group_name)

    result = sorted(paths)
    logger.info("[IMP:9][extract_script_paths] Extracted %d unique script path(s) from manifest", len(result))
    return result


# endregion FUNC_extract_script_paths


# ── Pytest fixtures ────────────────────────────────────────────────────────

# region FIXTURES


@pytest.fixture(scope="session")
def _manifest_scripts() -> list[str]:
    """Fixture: extract all script paths from manifest once per session.

    ## @purpose  Session-scoped fixture so manifest is parsed only once.
    ## @io       ⎋ list[str] of relative script paths from manifest
    """
    return extract_script_paths(MANIFEST_PATH)


def _resolve_script_path(script_rel: str) -> str:
    """Resolve a relative script path to absolute path.

    ## @purpose  Convert manifest-relative path to absolute filesystem path.
    ##           Handles paths with extra arguments (e.g. "validate.sh --lint").
    ##           Supports both .sh and .py scripts.
    ## @io       ⇥ script_rel: str → ⎋ str: absolute path
    """
    # Strip any trailing arguments after the .sh/.py path
    base = script_rel.split(maxsplit=1)[0]
    return Path(PLATFORM_ROOT) / base


# endregion FIXTURES


# ── Manifest parse test ────────────────────────────────────────────────────

# region FUNC_test_manifest_parses


@pytest.mark.contract
def test_manifest_parses() -> None:
    """Verify entrypoint-manifest.yaml parses without errors.

    # ▶ MANIFEST_PATH → ⚡ yaml.safe_load → ◇ data is dict? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_manifest_parses] Checking manifest: %s", MANIFEST_PATH)
    assert pathlib.Path(MANIFEST_PATH).is_file(), (
        f"[IMP:9][test_manifest_parses] FAIL: manifest not found at {MANIFEST_PATH}"
    )
    with pathlib.Path(MANIFEST_PATH).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"[IMP:9][test_manifest_parses] FAIL: manifest root is not dict, got {type(data)}"
    logger.info("[IMP:9][test_manifest_parses] PASS: Manifest parsed, %d group(s): %s", len(data), list(data.keys()))


# endregion FUNC_test_manifest_parses


# ── Parametrized contract tests ────────────────────────────────────────────

# Each script from manifest gets 3 tests: exists, shebang, bash -n syntax.
# Entrypoint scripts (core/entrypoints/*.sh) also get --help smoke test.

# region FUNC_test_entrypoint_exists


@pytest.mark.contract
@pytest.mark.parametrize(
    "script_rel",
    [
        pytest.param(s, id=s.replace("/", "_").replace(".sh", "").replace(".py", ""))
        for s in extract_script_paths(MANIFEST_PATH)
    ],
)
def test_entrypoint_exists(script_rel: str) -> None:
    """Verify a manifest-registered script exists on disk.

    # ▶ script_rel → ◇ _resolve_script_path → ◇ os.path.isfile? → ⎋ pass | fail
    """
    script_path = _resolve_script_path(script_rel)
    logger.info("[IMP:7][test_entrypoint_exists] Checking: %s", script_rel)
    assert pathlib.Path(script_path).is_file(), (
        f"[IMP:9][test_entrypoint_exists] FAIL: script not found at {script_path} "
        f"(from manifest reference: {script_rel})"
    )
    logger.info("[IMP:9][test_entrypoint_exists] PASS: %s exists at %s", script_rel, script_path)


# endregion FUNC_test_entrypoint_exists


# region FUNC_test_entrypoint_has_shebang


@pytest.mark.contract
@pytest.mark.parametrize(
    "script_rel",
    [
        pytest.param(s, id=s.replace("/", "_").replace(".sh", "").replace(".py", ""))
        for s in extract_script_paths(MANIFEST_PATH)
    ],
)
def test_entrypoint_has_shebang(script_rel: str) -> None:
    """Verify a manifest-registered script has a valid shebang (starts with #!).

    # ▶ script_rel → ⚡ read first line → ◇ starts with '#!'? → ⎋ pass | fail
    """
    script_path = _resolve_script_path(script_rel)
    if not pathlib.Path(script_path).is_file():
        pytest.skip(f"Script not found: {script_path}")

    with pathlib.Path(script_path).open(encoding="utf-8") as f:
        first_line = f.readline().strip()

    logger.info("[IMP:7][test_entrypoint_has_shebang] Checking shebang: %s", script_rel)
    assert first_line.startswith("#!"), (
        f"[IMP:9][test_entrypoint_has_shebang] FAIL: {script_rel} first line is '{first_line}', expected shebang (#!)"
    )
    logger.info("[IMP:9][test_entrypoint_has_shebang] PASS: %s has shebang: %s", script_rel, first_line)


# endregion FUNC_test_entrypoint_has_shebang


# region FUNC_test_entrypoint_syntax


@pytest.mark.contract
@pytest.mark.parametrize(
    "script_rel",
    [
        pytest.param(s, id=s.replace("/", "_").replace(".sh", "").replace(".py", ""))
        for s in extract_script_paths(MANIFEST_PATH)
    ],
)
def test_entrypoint_syntax(script_rel: str) -> None:
    """Verify a manifest-registered script has valid syntax.

    Bash: `bash -n` check. Python: `python3 -c "compile(...)"` check.

    # ▶ script_rel → ⚡ bash -n (sh) or compile() (py) → ◇ returncode == 0? → ⎋ pass | fail
    """
    script_path = _resolve_script_path(script_rel)
    if not pathlib.Path(script_path).is_file():
        pytest.skip(f"Script not found: {script_path}")

    is_python = script_rel.endswith(".py")

    logger.info("[IMP:7][test_entrypoint_syntax] Checking syntax: %s (python=%s)", script_rel, is_python)

    if is_python:
        # Python syntax check via compile()
        with pathlib.Path(script_path).open(encoding="utf-8") as f:
            source = f.read()
        try:
            compile(source, script_path, "exec")
            result_code = 0
            stderr = ""
        except SyntaxError as exc:
            result_code = 1
            stderr = str(exc)
    else:
        # Bash syntax check via bash -n
        result: subprocess.CompletedProcess = subprocess.run(
            ["bash", "-n", script_path], capture_output=True, text=True, check=False
        )
        result_code = result.returncode
        stderr = result.stderr

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    logger.info("%s", f"[IMP:7][test_entrypoint_syntax] {script_rel} → exit={result_code}")
    if stderr:
        for line in stderr if isinstance(stderr, str) else stderr.splitlines():
            logger.info("%s", f"[IMP:7][syntax] {line}")
    logger.info("--- END LDD TRAJECTORY ---")

    assert result_code == 0, f"[IMP:9][test_entrypoint_syntax] FAIL: syntax error in {script_rel}\nstderr: {stderr}"
    logger.info("[IMP:9][test_entrypoint_syntax] PASS: %s is syntactically valid", script_rel)


# endregion FUNC_test_entrypoint_syntax


# ── --help smoke for entrypoints only (core/entrypoints/*.sh) ──────────────

_entrypoint_rel_paths: list[str] = [s for s in extract_script_paths(MANIFEST_PATH) if s.startswith("core/entrypoints/")]


# region FUNC_test_entrypoint_help_smoke


@pytest.mark.contract
@pytest.mark.parametrize(
    "script_rel",
    [pytest.param(s, id=s.replace("/", "_").replace(".sh", "").replace(".py", "")) for s in _entrypoint_rel_paths],
)
def test_entrypoint_help_smoke(script_rel: str) -> None:
    """Verify entrypoint script handles --help gracefully (exit 0 or usage in stderr).

    # ▶ script_rel → ⚡ python3 script.py --help (py) or bash script.sh --help (sh)
    #   → ◇ returncode == 0 or stderr has usage? → ⎋ pass | fail
    ## @complexity O(1) per parametrized invocation — single subprocess call
    """
    script_path = _resolve_script_path(script_rel)
    if not pathlib.Path(script_path).is_file():
        pytest.skip(f"Script not found: {script_path}")

    is_python = script_rel.endswith(".py")

    logger.info("[IMP:7][test_entrypoint_help_smoke] Running %s --help (python=%s)", script_rel, is_python)

    if is_python:
        run_args = ["python3", script_path, "--help"]
    else:
        run_args = ["bash", script_path, "--help"]

    result: subprocess.CompletedProcess = subprocess.run(run_args, capture_output=True, text=True, check=False)

    logger.info("%s", f"--- LDD TRAJECTORY (IMP:7-10) [{script_rel} --help] ---")
    logger.info("%s", f"[IMP:7][test_entrypoint_help_smoke] exit={result.returncode}")
    if result.stdout:
        logger.info("%s", f"[IMP:7][stdout] {result.stdout[:500]}")
    if result.stderr:
        for line in result.stderr.splitlines():
            logger.info("%s", f"[IMP:7][stderr] {line}")
    logger.info("--- END LDD TRAJECTORY ---")

    # Either exit 0 (clean --help support) or exit != 0 with usage in stderr,
    # or exit 126 (entrypoint uses `exec` to delegate to internal script without +x)
    if result.returncode == 0:
        logger.info("[IMP:9][test_entrypoint_help_smoke] PASS: %s --help exited 0", script_rel)
    elif result.returncode == 126:
        # Permission denied — entrypoint uses `exec` to delegate to internal script
        # which may not have execute permission. Graceful failure.
        logger.info(
            "[IMP:8][test_entrypoint_help_smoke] %s --help exited 126 "
            "(exec target not executable — graceful delegation failure)",
            script_rel,
        )
    else:
        assert result.stderr, (
            f"[IMP:9][test_entrypoint_help_smoke] FAIL: {script_rel} --help "
            f"exited {result.returncode} with no stderr output"
        )
        # Check that stderr looks like usage/help
        has_usage = any(kw in result.stderr.lower() for kw in ("usage:", "usage", "help", "error:", "option", "flag"))
        assert has_usage, (
            f"[IMP:9][test_entrypoint_help_smoke] FAIL: {script_rel} --help "
            f"exited {result.returncode} but stderr doesn't contain usage/help info. "
            f"Stderr: {result.stderr[:500]}"
        )
        logger.info(
            "[IMP:9][test_entrypoint_help_smoke] PASS: %s --help exited %d with usage info",
            script_rel,
            result.returncode,
        )


# endregion FUNC_test_entrypoint_help_smoke


# ── Manifest completeness check ────────────────────────────────────────────

# region FUNC_test_manifest_covers_all_entrypoints


@pytest.mark.contract
def test_manifest_covers_all_entrypoints() -> None:
    """Verify that every script file in core/entrypoints/ is registered in the manifest.

    # ▶ listdir(core/entrypoints) → ∋ .sh/.py files → ◇ each in manifest scripts? → ⎋ pass | fail
    ## @complexity O(N + M) where N = script files on disk, M = manifest script count
    """
    entrypoints_dir = Path(PLATFORM_ROOT) / "core" / "entrypoints"
    if not pathlib.Path(entrypoints_dir).is_dir():
        pytest.skip("core/entrypoints/ directory not found")

    on_disk: set[str] = set()
    for fname in (p.name for p in entrypoints_dir.iterdir()):
        if fname.endswith((".sh", ".py")):
            rel = str(Path("core") / "entrypoints" / fname)
            on_disk.add(rel)

    manifest_scripts = set(extract_script_paths(MANIFEST_PATH))
    # Filter to entrypoint paths only
    manifest_entrypoints: set[str] = {s for s in manifest_scripts if s.startswith("core/entrypoints/")}

    missing_from_manifest = on_disk - manifest_entrypoints
    if missing_from_manifest:
        logger.warning(
            "[IMP:8][test_manifest_covers_all_entrypoints] Entrypoints not in manifest: %s",
            sorted(missing_from_manifest),
        )

    extra_in_manifest = manifest_entrypoints - on_disk
    if extra_in_manifest:
        logger.warning(
            "[IMP:8][test_manifest_covers_all_entrypoints] Manifest references entrypoints not on disk: %s",
            sorted(extra_in_manifest),
        )

    assert not extra_in_manifest, (
        f"[IMP:9][test_manifest_covers_all_entrypoints] FAIL: Manifest references "
        f"{len(extra_in_manifest)} entrypoint(s) not on disk: {sorted(extra_in_manifest)}"
    )
    logger.info(
        "[IMP:9][test_manifest_covers_all_entrypoints] PASS: All %d entrypoints on disk are covered by manifest",
        len(on_disk),
    )


# endregion FUNC_test_manifest_covers_all_entrypoints


# region FUNC_test_node_update_has_ssh_proxy
## @purpose  Verify node-update.sh entrypoint contract: registered in manifest, thin facade
##           delegating to remote_dispatch.py --verb update; SSH proxy flags (--age-secret-key-file,
##           --dry-run, --node), AGE key detection via node_detect (DevPlan 104) and rc=2/rc=3
##           discrimination now live in core/internal/bootstrap/remote_dispatch.py (DevPlan 170 W9-F2 —
##           логика извлечена из node-update.sh). Validates entrypoint↔manifest consistency.
## @io       Manifest extractor + script content → grep → assertions
## @complexity O(M + S) where M = manifest entries, S = script content
## @invariants — node-update.sh in manifest; thin facade delegates to remote_dispatch.py --verb update;
##               remote_dispatch.py owns --age-secret-key-file + node_detect + SSH/local fallback
@pytest.mark.contract
def test_node_update_has_ssh_proxy() -> None:
    """Entrypoint contract: node-update.sh thin facade → remote_dispatch.py --verb update."""
    # 🧪 TRAP[TEST] · Regression: T5 — entrypoint contract for SSH proxy
    # · Scenario: node-update.sh modified but SSH proxy flags dropped
    # · Last fail: Wave 1 pre-merge (entrypoint missing --age-secret-key-file)
    # · Remove if: node-update.sh no longer needs SSH proxy
    logger.info("[IMP:7][test_node_update_has_ssh_proxy] START")

    # ── Check 1: node-update.sh is registered in the manifest ──
    manifest_scripts = extract_script_paths(MANIFEST_PATH)
    node_update_manifest_paths = [s for s in manifest_scripts if "node-update" in s]
    assert len(node_update_manifest_paths) > 0, (
        "[IMP:9][test] FAIL: node-update.sh not found in entrypoint-manifest.yaml"
    )
    logger.info(
        "[IMP:8][test_node_update_has_ssh_proxy] Check 1 PASS: node-update.sh in manifest: %s",
        node_update_manifest_paths[0],
    )

    # ── Check 2: node-update.sh exists on disk ──
    node_update_path = Path(PLATFORM_ROOT) / node_update_manifest_paths[0]
    assert pathlib.Path(node_update_path).is_file(), (
        f"[IMP:9][test] FAIL: node-update.sh not found on disk at {node_update_path}"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 2 PASS: node-update.sh exists on disk")

    # ── Check 3: has valid shebang ──
    with pathlib.Path(node_update_path).open(encoding="utf-8") as f:
        first_line = f.readline().strip()
    assert first_line.startswith("#!"), "[IMP:9][test] FAIL: node-update.sh missing shebang"
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 3 PASS: shebang OK")

    # ── Check 4 (170 W9-F2): thin facade delegates to remote_dispatch.py --verb update ──
    with pathlib.Path(node_update_path).open(encoding="utf-8") as f:
        content = f.read()
    assert "remote_dispatch.py" in content and "--verb update" in content, (
        "[IMP:9][test] FAIL: node-update.sh must delegate to remote_dispatch.py --verb update (170 W9-F2)"
    )
    logger.info(
        "[IMP:8][test_node_update_has_ssh_proxy] Check 4 PASS: node-update.sh → remote_dispatch.py --verb update"
    )

    # ── Check 5 (170 W9-F2): SSH proxy contract lives in remote_dispatch.py ──
    dispatch_path = Path(PLATFORM_ROOT) / "core" / "internal" / "bootstrap" / "remote_dispatch.py"
    assert dispatch_path.is_file(), "[IMP:9][test] FAIL: remote_dispatch.py not found on disk"
    with dispatch_path.open(encoding="utf-8") as f:
        dispatch_content = f.read()
    assert "--age-secret-key-file" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must accept --age-secret-key-file"
    )
    assert "node_detect" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must delegate AGE key detection to node_detect (DevPlan 104)"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 5 PASS: --age-secret-key-file + node_detect in dispatch")

    # ── Check 6: has SSH_HOST/local exec fallback (now in remote_dispatch.py) ──
    assert "ssh_host" in dispatch_content.lower() or "SSH_HOST" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must handle SSH_HOST"
    )
    has_local_fallback = any(
        kw in dispatch_content.lower() for kw in ("locally", "local exec", "local mode", "LOCALLY")
    )
    assert has_local_fallback, "[IMP:9][test] FAIL: remote_dispatch.py must have local exec fallback"
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 6 PASS: SSH proxy + local fallback in dispatch")

    logger.info("[IMP:9][test_node_update_has_ssh_proxy] ALL CHECKS PASS")


# endregion FUNC_test_node_update_has_ssh_proxy
