# GREP_SUMMARY: test-deploy-gates static-audit env-requires hermes-image-check clickhouse-password url-safe minio-credentials
# STRUCTURE: ▶ test_env_requires_gate_present → ▶ test_no_hardcoded_hermes_images → ▶ test_clickhouse_password_url_safe → ▶ test_minio_env_requires_documented
# region MODULE_CONTRACT
## @purpose  Validate deploy-modules.sh env_requires gate (T3), hermes image check (T4),
##           CLICKHOUSE_PASSWORD URL-safe constraint, and MINIO env_requires documentation.
## @scope    Static audit — reads shell scripts and config files as text, parses YAML.
##           All tests are @pytest.mark.static_audit — no Docker daemon required.
## @invariants
##   - test_env_requires_gate_present: secrets_validator imported by deploy_orchestrator.py (D1);
##     _check_env_requires (sequential) + _batch_check_env (parallel) invoked BEFORE deploy;
##     shell facade delegates via exec python3 deploy/deploy_orchestrator.py
##   - test_no_hardcoded_hermes_images: no ghcr.io/tronyx161/hermes-agent literal in deploy-modules.sh
##   - test_clickhouse_password_url_safe: dev values in .env.example and platform-env.yaml match ^[A-Za-z0-9._-]+$
##   - test_minio_env_requires_documented: MINIO_ROOT_USER and MINIO_ROOT_PASSWORD documented in .env.example
##   - Acceptance criteria A4, A5 from DevPlan 001
## @rationale — Static gates catch drift at PR time, before deployment.
##   A4: Module with empty env_requires var fails before compose up.
##   A5: No hardcoded hermes images in deploy-modules.sh; image check from compose config.
##   DevPlan 100: env_requires gate moved from shell (secrets_validator.py --action check-env)
##   to deploy_orchestrator.py native import + per-path invocations — test greps the Python
##   orchestrator, not the thin shell facade.
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
# DevPlan 100: env_requires gate lives in the Python orchestrator — static grep targets this file.
_DEPLOY_ORCHESTRATOR_PY = repo_root() / "core" / "internal" / "bootstrap" / "deploy" / "deploy_orchestrator.py"
_ENV_EXAMPLE = repo_root() / ".env.example"
_PLATFORM_ENV_YAML = repo_root() / "platform-env.yaml"


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: env_requires check via secrets_validator (DevPlan 100 — Python orchestrator)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_env_requires_gate_present
## @purpose  Assert the env_requires gate (A4) lives in deploy/deploy_orchestrator.py:
##           secrets_validator is imported natively (D1) and invoked BEFORE module deploy in
##           BOTH paths — _deploy_sequential calls _check_env_requires per module before deploy,
##           _deploy_parallel calls _batch_check_env before group deploy. The thin shell facade
##           deploy-modules.sh delegates via `exec python3 deploy/deploy_orchestrator.py` and
##           contains no direct secrets_validator.py call.
##           Acceptance criterion A4: module with empty env_requires var fails before compose up.
## @io       ⇥ caplog → ⎋ None (pytest.fail if delegation call missing)
## @complexity 1 — static grep on file content (orchestrator + facade)
## @invariants
##   - deploy_orchestrator.py imports secrets_validator as _secrets_validator (import-native, D1)
##   - _deploy_sequential body calls _check_env_requires BEFORE deploy_docker_module / invoke_module_interface
##   - _deploy_parallel body calls _batch_check_env BEFORE deploy_docker_group / _deploy_orchestrator
##   - deploy-modules.sh facade execs python3 deploy/deploy_orchestrator.py (no direct secrets_validator call)


# 🧪 TRAP[TEST] · Regression · env-requires gate A4: orchestrator import + sequential/parallel env-check ordering
# · Last fail: 2026-07-31 (old shell secrets_validator.py --action check-env / --module-name / --secrets-manifest / FAILED+=)
# · Remove if: env_requires validation moves out of deploy_orchestrator.py
@pytest.mark.static_audit
def test_env_requires_gate_present(caplog) -> None:
    """
    # ◇ read deploy_orchestrator.py + deploy-modules.sh → ⚡ grep import + env-check calls
    # → ◇ sequential (check_env < deploy) ∧ parallel (batch_check_env < deploy) ∧ facade exec?
    # → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_env_requires_gate] Reading deploy_orchestrator.py + deploy-modules.sh ...")
    orch_content = _DEPLOY_ORCHESTRATOR_PY.read_text()
    shell_content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. secrets_validator imported natively (DevPlan 100 D1 — no subprocess) ──
    has_import = "secrets_validator as _secrets_validator" in orch_content
    logger.critical("[IMP:9][test_env_requires_gate] secrets_validator imported in orchestrator: %s", has_import)
    assert has_import, (
        "deploy_orchestrator.py must import secrets_validator natively (D1) — "
        "env_requires validation moved from shell to Python (DevPlan 100)"
    )

    # ── 2. Sequential path: _check_env_requires BEFORE deploy (A4 per-module gate) ──
    seq_start = orch_content.find("def _deploy_sequential(")
    seq_end = orch_content.find("# endregion FUNC__deploy_sequential")
    seq_body = orch_content[seq_start:seq_end] if seq_start != -1 and seq_end != -1 else ""
    has_check_env = "_secrets_validator._check_env_requires" in seq_body
    check_before_deploy = False
    if has_check_env:
        idx_check = seq_body.find("_secrets_validator._check_env_requires")
        idx_docker = seq_body.find("deploy_docker_module")
        idx_system = seq_body.find('_invoke_module_interface(m_name, "install")')
        first_deploy = min(i for i in (idx_docker, idx_system) if i != -1)
        check_before_deploy = 0 <= idx_check < first_deploy
    logger.critical("[IMP:9][test_env_requires_gate] sequential _check_env_requires call: %s", has_check_env)
    logger.critical("[IMP:9][test_env_requires_gate] sequential check BEFORE deploy: %s", check_before_deploy)
    assert has_check_env, "deploy_orchestrator.py _deploy_sequential must call _check_env_requires per module"
    assert check_before_deploy, "env_requires check must run BEFORE module deploy (A4 — fail before compose up)"

    # ── 3. Parallel path: _batch_check_env BEFORE group deploy ──
    par_start = orch_content.find("def _deploy_parallel(")
    par_end = orch_content.find("# endregion FUNC__deploy_parallel")
    par_body = orch_content[par_start:par_end] if par_start != -1 and par_end != -1 else ""
    has_batch = "_secrets_validator._batch_check_env" in par_body
    batch_before_groups = False
    if has_batch:
        idx_batch = par_body.find("_secrets_validator._batch_check_env")
        idx_groups = par_body.find("deploy_docker_group")
        idx_orch = par_body.find("_deploy_orchestrator(")
        first_deploy_par = min(i for i in (idx_groups, idx_orch) if i != -1)
        batch_before_groups = 0 <= idx_batch < first_deploy_par
    logger.critical("[IMP:9][test_env_requires_gate] parallel _batch_check_env call: %s", has_batch)
    logger.critical("[IMP:9][test_env_requires_gate] parallel batch check BEFORE groups: %s", batch_before_groups)
    assert has_batch, "deploy_orchestrator.py _deploy_parallel must call _batch_check_env before deploy groups"
    assert batch_before_groups, "batch env_requires check must run BEFORE group deploy (A4 — fail before compose up)"

    # ── 4. Shell facade delegates to the orchestrator (no direct secrets_validator call) ──
    has_exec = "exec python3" in shell_content and "deploy/deploy_orchestrator.py" in shell_content
    logger.critical("[IMP:9][test_env_requires_gate] facade exec python3 deploy_orchestrator.py: %s", has_exec)
    assert has_exec, "deploy-modules.sh must exec python3 deploy/deploy_orchestrator.py (DevPlan 100 delegation)"
    assert "secrets_validator.py" not in shell_content, (
        "deploy-modules.sh must NOT call secrets_validator.py directly — env_requires check lives in "
        "deploy_orchestrator.py (DevPlan 100)"
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
