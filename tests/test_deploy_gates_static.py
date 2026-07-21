# GREP_SUMMARY: test-deploy-gates static-audit env-requires hermes-image-check clickhouse-password url-safe minio-credentials
# STRUCTURE: ▶ test_env_requires_gate_present → ▶ test_no_hardcoded_hermes_images → ▶ test_clickhouse_password_url_safe → ▶ test_minio_env_requires_documented
# region MODULE_CONTRACT
## @purpose  Validate deploy-modules.sh env_requires gate (T3), hermes image check (T4),
##           CLICKHOUSE_PASSWORD URL-safe constraint, and MINIO env_requires documentation.
## @scope    Static audit — reads shell scripts and config files as text, parses YAML.
##           All tests are @pytest.mark.static_audit — no Docker daemon required.
## @invariants
##   - test_env_requires_gate_present: _check_env_requires function defined + called in both deploy functions
##   - test_no_hardcoded_hermes_images: no ghcr.io/tronyx161/hermes-agent literal in deploy-modules.sh
##   - test_clickhouse_password_url_safe: dev values in .env.example and platform-env.yaml match ^[A-Za-z0-9._-]+$
##   - test_minio_env_requires_documented: MINIO_ROOT_USER and MINIO_ROOT_PASSWORD documented in .env.example
##   - Acceptance criteria A4, A5 from DevPlan 001
## @rationale — Static gates catch drift at PR time, before deployment.
##   A4: Module with empty env_requires var fails before compose up.
##   A5: No hardcoded hermes images in deploy-modules.sh; image check from compose config.
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
_ENV_EXAMPLE = repo_root() / ".env.example"
_PLATFORM_ENV_YAML = repo_root() / "platform-env.yaml"


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: env_requires gate presence
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_env_requires_gate_present
## @purpose  Assert deploy-modules.sh defines _check_env_requires() and calls it in
##           both deploy_docker_module() and deploy_system_module() before any deploy action.
##           Acceptance criterion A4: module with empty env_requires var fails before compose up.
## @io       ⇥ caplog → ⎋ None (pytest.fail if function missing or not called in both branches)
## @complexity 1 — static grep on file content
## @invariants
##   - Function definition MUST exist
##   - At least 2 non-definition occurrences (calls in both deploy functions)
##   - Function location follows _get_module_severity (sibling, after it)


@pytest.mark.static_audit
def test_env_requires_gate_present(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _check_env_requires → ⊕ definition + calls → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_env_requires_gate] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── Function definition ────────────────────────────────────────────────
    has_definition = "_check_env_requires()" in content
    logger.critical("[IMP:9][test_env_requires_gate] Function _check_env_requires() defined: %s", has_definition)
    assert has_definition, "_check_env_requires() function not defined in deploy-modules.sh"

    # ── Count calls (not the definition) ────────────────────────────────────
    # Search for all lines containing _check_env_requires, exclude definition line
    all_occurrences = content.count("_check_env_requires")
    logger.info("[IMP:8][test_env_requires_gate] _check_env_requires appears %d times total", all_occurrences)
    assert all_occurrences >= 3, (
        f"_check_env_requires should appear >=3 times "
        f"(1 definition in CHECK_ENV_REQUIRES + >=2 calls in deploy functions), found {all_occurrences}"
    )

    # ── Present in both deploy functions (by scanning function regions) ─────
    # deploy_docker_module region ends at endregion DEPLOY_DOCKER_MODULE
    docker_region_start = content.find("# region DEPLOY_DOCKER_MODULE")
    docker_region_end = content.find("# endregion DEPLOY_DOCKER_MODULE")
    assert docker_region_start >= 0, "DEPLOY_DOCKER_MODULE region not found"
    assert docker_region_end > docker_region_start, "DEPLOY_DOCKER_MODULE region end before start"

    docker_content = content[docker_region_start:docker_region_end]
    has_docker_call = "_check_env_requires" in docker_content
    logger.critical(
        "[IMP:9][test_env_requires_gate] deploy_docker_module calls _check_env_requires: %s", has_docker_call
    )
    assert has_docker_call, "deploy_docker_module() does not call _check_env_requires"

    # deploy_system_module region
    system_region_start = content.find("# region DEPLOY_SYSTEM_MODULE")
    system_region_end = content.find("# endregion DEPLOY_SYSTEM_MODULE")
    assert system_region_start >= 0, "DEPLOY_SYSTEM_MODULE region not found"
    assert system_region_end > system_region_start, "DEPLOY_SYSTEM_MODULE region end before start"

    system_content = content[system_region_start:system_region_end]
    has_system_call = "_check_env_requires" in system_content
    logger.critical(
        "[IMP:9][test_env_requires_gate] deploy_system_module calls _check_env_requires: %s", has_system_call
    )
    assert has_system_call, "deploy_system_module() does not call _check_env_requires"

    # ── LDD trajectory ─────────────────────────────────────────────────────
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_env_requires_gate_present


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: No hardcoded hermes images
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_hardcoded_hermes_images
## @purpose  Verify deploy-modules.sh does not contain hardcoded ghcr.io/tronyx161/hermes-agent image
##           references. Image checks now derive from `docker compose config --images` (T4).
##           Acceptance criterion A5: no hardcoded hermes images in deploy-modules.sh.
## @io       ⇥ caplog → ⎋ None (pytest.fail if hardcoded images found)
## @complexity 1 — static grep on file content
## @invariants
##   - The literal pattern ghcr.io/tronyx161/hermes-agent MUST NOT appear anywhere in the file
##   - TRAP[BUG] comments referencing the incident are allowed (contain the pattern in comments)


@pytest.mark.static_audit
def test_no_hardcoded_hermes_images(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep for ghcr.io/tronyx161/hermes-agent
    # → ◇ any matches? → ⎋ fail | pass
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_no_hardcoded_hermes] Scanning deploy-modules.sh for hardcoded hermes images ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # Search for the hardcoded image pattern. The TRAP[BUG] comment also contains
    # the pattern as part of the documentation — we need to check ONLY the code
    # section, not the comment. But the safest approach: if the pattern exists
    # ANYWHERE in executable code, it's a violation. However, TRAP comments are
    # comments (lines starting with #), so we filter those out.

    lines = content.splitlines()
    hardcoded_lines = [
        (i + 1, line.strip())
        for i, line in enumerate(lines)
        if "ghcr.io/tronyx161/hermes-agent" in line and not line.strip().startswith("#")
    ]

    if hardcoded_lines:
        for lineno, line_text in hardcoded_lines:
            logger.error("[IMP:4][test_no_hardcoded_hermes] Line %d: %s", lineno, line_text)

        logger.critical(
            "[IMP:9][test_no_hardcoded_hermes] Found %d hardcoded hermes image reference(s)",
            len(hardcoded_lines),
        )
        pytest.fail(
            f"deploy-modules.sh contains {len(hardcoded_lines)} hardcoded 'ghcr.io/tronyx161/hermes-agent' reference(s).\n"
            f"Image checks must derive from `docker compose config --images` (T4).\n"
            f"Offending lines: {[line for line, _ in hardcoded_lines]}"
        )

    logger.critical("[IMP:9][test_no_hardcoded_hermes] ✅ No hardcoded hermes images found")

    # ── LDD trajectory ─────────────────────────────────────────────────────
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_no_hardcoded_hermes_images


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: CLICKHOUSE_PASSWORD URL-safe constraint
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_clickhouse_password_url_safe
## @purpose  Verify dev values of CLICKHOUSE_PASSWORD in .env.example and platform-env.yaml
##           match ^[A-Za-z0-9._-]+$ (URL-safe charset). Also verify the constraint comment
##           exists in .env.example.
## @io       ⇥ caplog → ⎋ None (pytest.fail if constraint violated)
## @complexity 1 — YAML parse + regex match
## @invariants
##   - .env.example CLICKHOUSE_PASSWORD value matches ^[A-Za-z0-9._-]+$
##   - platform-env.yaml CLICKHOUSE_PASSWORD value matches ^[A-Za-z0-9._-]+$
##   - .env.example has a constraint comment mentioning the charset restriction
##   - Rationale: password is embedded un-encoded into CLICKHOUSE_MIGRATION_URL
##     (clickhouse://user:pass@host:9000) in langfuse compose — special chars
##     would break the URL


@pytest.mark.static_audit
def test_clickhouse_password_url_safe(caplog) -> None:
    """
    # ◇ .env.example + platform-env.yaml → ∋ CLICKHOUSE_PASSWORD
    # → ⚡ regex ^[A-Za-z0-9._-]+$ → ◇ match + constraint_comment?
    # → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)

    URL_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

    # ── .env.example ───────────────────────────────────────────────────────
    logger.info("[IMP:7][test_clickhouse_pwd] Checking .env.example ...")
    env_content = _ENV_EXAMPLE.read_text()

    # Extract CLICKHOUSE_PASSWORD value
    match = re.search(r"^CLICKHOUSE_PASSWORD=(.+)", env_content, re.MULTILINE)
    assert match is not None, "CLICKHOUSE_PASSWORD not found in .env.example"
    env_value = match.group(1).strip()

    is_url_safe = bool(URL_SAFE_RE.match(env_value))
    logger.critical(
        "[IMP:9][test_clickhouse_pwd] .env.example CLICKHOUSE_PASSWORD='%s' matches %s: %s",
        env_value,
        URL_SAFE_RE.pattern,
        is_url_safe,
    )
    assert is_url_safe, (
        f".env.example CLICKHOUSE_PASSWORD '{env_value}' contains characters not allowed in URL.\n"
        f"Must match {URL_SAFE_RE.pattern} — it is embedded un-encoded in CLICKHOUSE_MIGRATION_URL."
    )

    # Check constraint comment exists near CLICKHOUSE_PASSWORD
    has_constraint_comment = "CLICKHOUSE_PASSWORD must match" in env_content or "[A-Za-z0-9._-]" in env_content
    logger.critical(
        "[IMP:9][test_clickhouse_pwd] Constraint comment present in .env.example: %s",
        has_constraint_comment,
    )
    assert has_constraint_comment, (
        "Missing URL-safe constraint comment near CLICKHOUSE_PASSWORD in .env.example.\n"
        "Must document that CLICKHOUSE_PASSWORD must match ^[A-Za-z0-9._-]+$."
    )

    # ── platform-env.yaml ─────────────────────────────────────────────────
    logger.info("[IMP:7][test_clickhouse_pwd] Checking platform-env.yaml ...")
    assert _PLATFORM_ENV_YAML.is_file(), f"platform-env.yaml not found: {_PLATFORM_ENV_YAML}"
    with open(_PLATFORM_ENV_YAML) as f:
        pe_data = yaml.safe_load(f)

    # Key is under env_defaults:
    pe_value = ""
    if isinstance(pe_data, dict):
        pe_vars = pe_data.get("env_defaults") or pe_data
        if isinstance(pe_vars, dict):
            pe_value = pe_vars.get("CLICKHOUSE_PASSWORD", "")

    # Ensure pe_value is a string
    if not isinstance(pe_value, str):
        pe_value = str(pe_value) if pe_value else ""

    is_pe_safe = bool(URL_SAFE_RE.match(pe_value))
    logger.critical(
        "[IMP:9][test_clickhouse_pwd] platform-env.yaml CLICKHOUSE_PASSWORD='%s' matches %s: %s",
        pe_value,
        URL_SAFE_RE.pattern,
        is_pe_safe,
    )
    assert is_pe_safe, (
        f"platform-env.yaml CLICKHOUSE_PASSWORD '{pe_value}' contains characters not allowed in URL.\n"
        f"Must match {URL_SAFE_RE.pattern}."
    )

    # ── LDD trajectory ─────────────────────────────────────────────────────
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_clickhouse_password_url_safe


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: MINIO env_requires documented
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_minio_env_requires_documented
## @purpose  Verify MINIO_ROOT_USER and MINIO_ROOT_PASSWORD are documented in .env.example
##           as REQUIRED (env_requires of minio module).
## @io       ⇥ caplog → ⎋ None (pytest.fail if missing)
## @complexity 1 — static grep
## @invariants
##   - MINIO_ROOT_USER appears in .env.example
##   - MINIO_ROOT_PASSWORD appears in .env.example
##   - Acceptance criterion A4: documented as REQUIRED


@pytest.mark.static_audit
def test_minio_env_requires_documented(caplog) -> None:
    """
    # ◇ .env.example → ⚡ grep MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
    # → ◇ both present? → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_minio_env] Checking .env.example for MINIO variables ...")
    env_content = _ENV_EXAMPLE.read_text()

    has_user = "MINIO_ROOT_USER" in env_content
    has_password = "MINIO_ROOT_PASSWORD" in env_content

    logger.critical("[IMP:9][test_minio_env] MINIO_ROOT_USER in .env.example: %s", has_user)
    logger.critical("[IMP:9][test_minio_env] MINIO_ROOT_PASSWORD in .env.example: %s", has_password)

    assert has_user, "MINIO_ROOT_USER is NOT present in .env.example — required by minio module env_requires"
    assert has_password, "MINIO_ROOT_PASSWORD is NOT present in .env.example — required by minio module env_requires"

    logger.info("[IMP:8][test_minio_env] ✅ Both MINIO_ROOT_USER and MINIO_ROOT_PASSWORD documented")

    # ── LDD trajectory ─────────────────────────────────────────────────────
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_minio_env_requires_documented
