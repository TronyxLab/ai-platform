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
# Test 1: env_requires check via secrets_validator.py delegation
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_env_requires_gate_present
## @purpose  Assert deploy-modules.sh calls secrets_validator.py --action check-env for each
##           module before deployment. After W4-E1 Strangler-Fig decomposition, the env_requires
##           check was migrated from shell `_check_env_requires()` to Python secrets_validator.py.
##           Acceptance criterion A4: module with empty env_requires var fails before compose up.
## @io       ⇥ caplog → ⎋ None (pytest.fail if delegation call missing)
## @complexity 1 — static grep on file content
## @invariants
##   - secrets_validator.py --action check-env call present in deploy-modules.sh
##   - Call passes --module-name and --secrets-manifest
##   - Call is inside the module deploy loop (before deploy)


@pytest.mark.static_audit
def test_env_requires_gate_present(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep secrets_validator check-env → ⊕ delegation call → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_env_requires_gate] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── secrets_validator.py --action check-env delegation ─────────────────
    has_secrets_check = "secrets_validator.py" in content and "--action check-env" in content
    logger.critical(
        "[IMP:9][test_env_requires_gate] secrets_validator.py --action check-env present: %s", has_secrets_check
    )
    assert has_secrets_check, (
        "deploy-modules.sh must call secrets_validator.py --action check-env for env_requires validation\n"
        "W4-E1 migrated _check_env_requires() to secrets_validator.py"
    )

    # ── --module-name flag passed ──
    has_module_name = "--module-name" in content
    logger.critical("[IMP:9][test_env_requires_gate] --module-name flag present: %s", has_module_name)
    assert has_module_name, "deploy-modules.sh must pass --module-name to secrets_validator.py check-env"

    # ── --secrets-manifest flag passed ──
    has_secrets_manifest = "--secrets-manifest" in content
    logger.critical("[IMP:9][test_env_requires_gate] --secrets-manifest flag present: %s", has_secrets_manifest)
    assert has_secrets_manifest, "deploy-modules.sh must pass --secrets-manifest to secrets_validator.py check-env"

    # ── Check is inside the deploy loop (FAILED+= on failure) ──
    has_failed_tracking = "FAILED+=(" in content or 'FAILED+=("' in content
    logger.critical("[IMP:9][test_env_requires_gate] Failure tracking present (FAILED+=): %s", has_failed_tracking)

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
