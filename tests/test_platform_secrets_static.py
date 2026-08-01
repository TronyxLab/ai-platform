# GREP_SUMMARY: platform-secrets static-tests module-yaml service-execstart environmentfile install-sh
# STRUCTURE: ▶ platform_root → ∋ core/modules/platform-secrets/** → ◇ test_module_yaml_schema ⊕ test_module_files_present ⊕ test_service_execstart_path_exists ⊕ test_agekey_environmentfile_format_contract → ⎋ pass|fail
# @file test_platform_secrets_static.py
# @purpose  Static validation tests for platform-secrets module: module.yaml schema,
#           required file presence, ExecStart path validity (B2),
#           and EnvironmentFile/install.sh format contract (B3).
# @scope    Pure file I/O — no Docker, no subprocess, no external dependencies.
#           Tests are lightweight and runnable in any environment (local, CI, pre-commit).
# @invariants
#   - All tests are marked @pytest.mark.static_audit
#   - YAML parsing via yaml.safe_load() (stdlib compatible)
#   - Service-file parsing via line-based .ini-style parsing
#   - Cross-file invariant: install.sh format must match what platform-secrets.service expects
#   - IMP:9 logging for all pass/fail assertions (LDD trajectory)
# @rationale  Static validation catches regressions in module.yaml,
#             and critical boot-chain paths before Docker deployment. Tests B2 and B3
#             cover known regressions confirmed in live verification.
# @changes    CREATED: 2026-07-15 | DevPlan 008 T5.1 platform-secrets audit

# region MODULE_CONTRACT
## @purpose  Static validation for platform-secrets module: module.yaml schema,
##           required file inventory, service ExecStart path (B2),
##           and install.sh→service EnvironmentFile format contract (B3).
## @scope    4 tests — all pure file I/O, no Docker or subprocess.
## @invariants
##   - Test 1: module.yaml has name="platform-secrets", install_type="system",
##             env_requires contains "AGE_SECRET_KEY"
##   - Test 2: All required module files exist (module.yaml, install.sh,
##             Makefile, platform-secrets.service)
##   - Test 3: platform-secrets.service ExecStart maps to /opt/platform/core/
##             internal/secrets/decrypt-secrets.sh (NOT /opt/core/scripts/) —
##             RED before fix B2
##   - Test 4: install.sh writes age key file in EnvironmentFile-compatible format
##             (KEY=VALUE) — RED before fix B3
##   - All tests use @ldd_trajectory decorator for IMP:9 verification
## @rationale — Catches boot-chain regressions before systemd service installation.
##              Follows pattern from test_clickhouse_static.py.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import pathlib

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
MODULE_DIR: str = os.path.join(PLATFORM_ROOT, "core", "modules", "platform-secrets")

MODULE_YAML_PATH: str = os.path.join(MODULE_DIR, "module.yaml")
INSTALL_SH_PATH: str = os.path.join(MODULE_DIR, "install.sh")
MAKEFILE_PATH: str = os.path.join(MODULE_DIR, "Makefile")
SERVICE_PATH: str = os.path.join(MODULE_DIR, "platform-secrets.service")

# Expected files for the module (core/modules/platform-secrets/ inventory)
# Волна 117 D14: healthcheck.sh удалён — system-модули НЕ содержат healthcheck.sh
# (контракт core/modules/AGENTS.md); интерфейс healthcheck не зарегистрирован в module.yaml.
REQUIRED_FILES: list[str] = [
    "module.yaml",
    "install.sh",
    "Makefile",
    "platform-secrets.service",
]

# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: module.yaml schema
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_module_yaml_schema(caplog: pytest.LogCaptureFixture) -> None:
    """Verify platform-secrets/module.yaml has required fields and correct values.

    ## @purpose — module.yaml is the machine-readable registry of module metadata.
    ##            platform-secrets is a system-type module (not Docker), so its
    ##            schema differs from docker-type modules. Key invariants: name,
    ##            install_type="system", and env_requires contains AGE_SECRET_KEY.
    ## @io — ⇥ caplog → ⎋ None (asserts YAML structure)
    ## @complexity — O(1)
    ## @scenario — AC-1: Module YAML has complete and valid structure
    """
    logger.info("[IMP:7][ps-yaml] Checking module.yaml: %s", MODULE_YAML_PATH)

    assert os.path.isfile(MODULE_YAML_PATH), f"[IMP:9][ps-yaml] FAIL: module.yaml not found at {MODULE_YAML_PATH}"

    with open(MODULE_YAML_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    # ── name ──
    assert data.get("name") == "platform-secrets", (
        f"[IMP:9][ps-yaml] FAIL: expected name='platform-secrets', got '{data.get('name')}'"
    )
    logger.info("[IMP:8][ps-yaml] name = 'platform-secrets'")

    # ── install_type ──
    assert data.get("install_type") == "system", (
        f"[IMP:9][ps-yaml] FAIL: expected install_type='system', got '{data.get('install_type')}'"
    )
    logger.info("[IMP:8][ps-yaml] install_type = 'system'")

    # ── env_requires contains AGE_SECRET_KEY ──
    env_requires = data.get("env_requires", [])
    assert isinstance(env_requires, list), (
        f"[IMP:9][ps-yaml] FAIL: env_requires must be a list, got {type(env_requires).__name__}"
    )
    assert "AGE_SECRET_KEY" in env_requires, (
        f"[IMP:9][ps-yaml] FAIL: env_requires missing 'AGE_SECRET_KEY', got {env_requires}"
    )
    logger.info("[IMP:8][ps-yaml] env_requires includes AGE_SECRET_KEY")

    # ── depends_on must be a list ──
    depends_on = data.get("depends_on", [])
    assert isinstance(depends_on, list), (
        f"[IMP:9][ps-yaml] FAIL: depends_on must be a list, got {type(depends_on).__name__}"
    )
    logger.info("[IMP:8][ps-yaml] depends_on = %s", depends_on)

    logger.info("[IMP:9][ps-yaml] PASS: module.yaml schema valid")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: Module file inventory
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_module_files_present(caplog: pytest.LogCaptureFixture) -> None:
    """Verify all required files exist in core/modules/platform-secrets/.

    ## @purpose — Missing files indicate an incomplete checkout, merge conflict,
    ##            or accidental deletion. Each required file serves a specific
    ##            lifecycle role: module.yaml (registry), install.sh (bootstrap),
    ##            Makefile (lifecycle),
    ##            platform-secrets.service (systemd unit for boot-time decrypt).
    ## @io — ⇥ caplog → ⎋ None (asserts all files exist)
    ## @complexity — O(N) where N = len(REQUIRED_FILES)
    ## @scenario — AC-2: All required module files present
    """
    logger.info("[IMP:7][ps-files] Checking %d required files in %s", len(REQUIRED_FILES), MODULE_DIR)

    assert os.path.isdir(MODULE_DIR), f"[IMP:9][ps-files] FAIL: module directory not found: {MODULE_DIR}"

    missing: list[str] = []
    for fname in REQUIRED_FILES:
        fpath = os.path.join(MODULE_DIR, fname)
        if os.path.isfile(fpath):
            logger.info("[IMP:8][ps-files] Found: %s", fname)
        else:
            logger.error("[IMP:9][ps-files] MISSING: %s", fname)
            missing.append(fname)

    assert not missing, f"[IMP:9][ps-files] FAIL: missing files: {', '.join(missing)}"
    logger.info("[IMP:9][ps-files] PASS: All %d required files present", len(REQUIRED_FILES))


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Service ExecStart path validity (B2 — RED until fix)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_service_execstart_path_exists(caplog: pytest.LogCaptureFixture) -> None:
    """Verify ExecStart in platform-secrets.service points to an existing file.

    ## @purpose — B2 audit finding: ExecStart=/opt/core/scripts/decrypt-secrets.sh
    ##            is dead — core/scripts/ is a forbidden (deleted) directory.
    ##            The real script lives at core/internal/secrets/decrypt-secrets.sh.
    ##            Canonical deploy path is /opt/platform/core/ (not /opt/core/).
    ##            This test catches the dead path: (a) must start with /opt/platform/core/,
    ##            (b) the mapped repo path must exist, (c) path must NOT contain /scripts/.
    ## @io — ⇥ caplog → ⎋ None (asserts ExecStart is valid)
    ## @complexity — O(1) — single file parse + path check
    ## @scenario — AC-4: Service ExecStart points to a real script at valid path
    ## @rationale — make secrets-unlock uses exec (not bash) on decrypt-secrets.sh.
    ##             If the path is wrong, the service silently fails with exit 203
    ##             (exec format error) or exit 1 (file not found), blocking boot.
    ##             This test catches dead ExecStart paths before deployment.
    """
    logger.info("[IMP:7][ps-execstart] Checking ExecStart in: %s", SERVICE_PATH)

    assert os.path.isfile(SERVICE_PATH), (
        f"[IMP:9][ps-execstart] FAIL: platform-secrets.service not found at {SERVICE_PATH}"
    )

    # Parse ExecStart= from the service file (simple .ini-style parsing)
    execstart_value: str | None = None
    with open(SERVICE_PATH, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("ExecStart="):
                execstart_value = stripped[len("ExecStart=") :]
                break

    assert execstart_value is not None, "[IMP:9][ps-execstart] FAIL: ExecStart= not found in platform-secrets.service"
    logger.info("[IMP:7][ps-execstart] ExecStart = %s", execstart_value)

    # ── Check (a): path must start with /opt/platform/core/ ──
    starts_correct = execstart_value.startswith("/opt/platform/core/")
    logger.info("[IMP:7][ps-execstart] Starts with /opt/platform/core/: %s", starts_correct)
    assert starts_correct, (
        f"[IMP:9][ps-execstart] FAIL: ExecStart '{execstart_value}' does NOT start with "
        f"/opt/platform/core/ — core/scripts/ is a forbidden directory"
    )

    # ── Check (b): mapped repo path exists ──
    # Map /opt/platform/core/ → <repo>/core/
    repo_relative = execstart_value.replace("/opt/platform/core/", "core/", 1)
    mapped_path = os.path.join(PLATFORM_ROOT, repo_relative)
    mapped_exists = os.path.isfile(mapped_path)
    logger.info("[IMP:7][ps-execstart] Mapped repo path: %s (exists: %s)", mapped_path, mapped_exists)
    assert mapped_exists, f"[IMP:9][ps-execstart] FAIL: mapped repo path does not exist: {mapped_path}"

    # ── Check (c): path must NOT contain /scripts/ (forbidden dir) ──
    has_forbidden = "/scripts/" in execstart_value
    logger.info("[IMP:7][ps-execstart] Contains /scripts/: %s", has_forbidden)
    assert not has_forbidden, (
        "[IMP:9][ps-execstart] FAIL: ExecStart contains '/scripts/' — core/scripts/ is a forbidden (deleted) directory"
    )

    logger.info("[IMP:9][ps-execstart] PASS: ExecStart is valid: %s", execstart_value)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: EnvironmentFile / install.sh format contract (B3 — RED until fix)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_agekey_environmentfile_format_contract(caplog: pytest.LogCaptureFixture) -> None:
    """Cross-file invariant: if service uses EnvironmentFile=/etc/platform/age-key.txt,
    install.sh must write the file in KEY=VALUE format.

    ## @purpose — B3 audit finding: platform-secrets.service:10 declares
    ##            EnvironmentFile=/etc/platform/age-key.txt, but install.sh:45
    ##            writes a bare key (printf '%%s\\n' "$AGE_SECRET_KEY").
    ##            systemd EnvironmentFile requires KEY=VALUE format; a bare value
    ##            without '=' is silently ignored → AGE_SECRET_KEY not set →
    ##            decrypt-secrets.sh fails with "Neither AGE_SECRET_KEY nor
    ##            SOPS_AGE_KEY is set". This test is the cross-file invariant guard.
    ## @io — ⇥ caplog → ⎋ None (asserts format contract)
    ## @complexity — O(1) — reads install.sh, searches for printf line writing age_key
    ## @scenario — AC-5: install.sh writes age key in EnvironmentFile-compatible format
    ## @invariants
    ##   - If service has EnvironmentFile=/etc/platform/age-key.txt (confirmed),
    ##     install.sh must write "AGE_SECRET_KEY=<value>" not just "<value>"
    ##   - The format must use printf with 'AGE_SECRET_KEY=%s' pattern
    ##   - Idempotent migration path must also exist for existing files
    ## @rationale — systemd's EnvironmentFile= directive parses KEY=VALUE lines.
    ##             Lines without '=' are silently ignored (not an error). This
    ##             makes the bug silent at boot: systemctl shows the service
    ##             as "active (exited)" even though AGE_SECRET_KEY is empty.
    """
    logger.info("[IMP:7][ps-envfile] Checking cross-file EnvironmentFile format contract")

    # ── Confirm service declares EnvironmentFile ──
    assert os.path.isfile(SERVICE_PATH), (
        f"[IMP:9][ps-envfile] FAIL: platform-secrets.service not found at {SERVICE_PATH}"
    )
    with open(SERVICE_PATH, encoding="utf-8") as fh:
        service_content = fh.read()

    has_envfile = "EnvironmentFile=/etc/platform/age-key.txt" in service_content
    logger.info("[IMP:7][ps-envfile] Service declares EnvironmentFile=/etc/platform/age-key.txt: %s", has_envfile)
    assert has_envfile, (
        "[IMP:9][ps-envfile] FAIL: service file does not declare "
        "EnvironmentFile=/etc/platform/age-key.txt — test precondition failed"
    )

    # ── Check install.sh: must write age_key in KEY=VALUE format ──
    assert os.path.isfile(INSTALL_SH_PATH), f"[IMP:9][ps-envfile] FAIL: install.sh not found at {INSTALL_SH_PATH}"
    with open(INSTALL_SH_PATH, encoding="utf-8") as fh:
        install_content = fh.read()

    # Look for the printf line that writes to "$age_key" variable
    # The variable is defined as local age_key="/etc/platform/age-key.txt"
    # The write happens with: printf ... > "$age_key"
    # We need to verify it contains "AGE_SECRET_KEY=%s"
    has_env_format = "AGE_SECRET_KEY=%s" in install_content  # EnvironmentFile format
    has_bare_format = (
        "printf '%s\\n' \"$AGE_SECRET_KEY\"" in install_content
        or "printf '%s\\n' \"$AGE_SECRET_KEY\"" in install_content.replace("'", "'\\''")
    )

    logger.info("[IMP:7][ps-envfile] Has AGE_SECRET_KEY=%%s format: %s", has_env_format)
    logger.info("[IMP:7][ps-envfile] Has bare format (no key=): %s", has_bare_format)

    assert has_env_format, (
        "[IMP:9][ps-envfile] FAIL: install.sh writes age key in BARE format "
        "(printf '%%s\\\\n' \"$AGE_SECRET_KEY\") but systemd EnvironmentFile requires "
        "KEY=VALUE format. Use: printf 'AGE_SECRET_KEY=%%s\\\\n' \"$AGE_SECRET_KEY\". "
        "This is cross-file invariant B3 — see DevPlan 008 T5.1."
    )

    logger.info("[IMP:9][ps-envfile] PASS: install.sh writes age key in EnvironmentFile-compatible format")
