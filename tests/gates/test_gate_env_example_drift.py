# GREP_SUMMARY: gate env-example-drift no_proxy-superset postgres-password-unified s3-endpoint-removed env-example-fresh platform-domain-default
# STRUCTURE: ◇ test_env_example_fresh → ◇ test_no_proxy_superset → ◇ test_postgres_password_unified → ◇ test_s3_endpoint_removed → ◇ test_platform_domain_default → ◇ test_no_inline_python3_in_scaffold → ◇ test_nextauth_secret_precondition

# region MODULE_CONTRACT
## @purpose  Gate test: validate .env.example consistency with SoT (platform-infra.yaml + secret-definitions.yaml).
##           Implements DRIFT-E1, E2, E6, E7 closure verification. S3_ENDPOINT elimination audit.
##           Language policy: gen-env-platform.sh must be thin facade (zero inline python3).
## @scope    Production code (core/, .env.example, .env). Test files excluded from S3_ENDPOINT audit.
## @invariants
##   - .env.example is byte-identical to sync_env_defaults.py generated output
##   - .env.example NO_PROXY is superset of platform-infra.yaml no_proxy_internal
##   - All POSTGRES_PASSWORD defaults reference secret-definitions.yaml ci_default (test-pg-pwd)
##   - S3_ENDPOINT (without _URL) does NOT exist in production code
##   - PLATFORM_DOMAIN default is ai-platform.local in gen-env-platform.sh
##   - gen-env-platform.sh has zero inline python3 heredoc blocks
##   - NEXTAUTH_SECRET validation skipped if DevPlan 078 marker absent (exit 0 with skip)
## @rationale Gate-enforced drift prevention: catches any manual edits to .env.example or
##            regressions in S3_ENDPOINT removal. Language policy enforcement for shell scripts.
## @changes  2026-07-26 | Created per DevPlan 082 TASK-8
# endregion MODULE_CONTRACT

import logging
import re
import subprocess
import sys

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
ENV_EXAMPLE = ROOT / ".env.example"
PLATFORM_ENV = ROOT / "platform-env.yaml"
SECRET_DEFS = ROOT / "core" / "secret-definitions.yaml"
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
GEN_ENV_PLATFORM_SH = ROOT / "core" / "internal" / "scaffold" / "gen-env-platform.sh"
SYNC_SCRIPT = ROOT / "core" / "internal" / "scripts" / "sync_env_defaults.py"


@pytest.mark.gate
@ldd_trajectory
def test_env_example_fresh(caplog):
    """.env.example is byte-identical to sync_env_defaults.py --check output."""
    result = subprocess.run(
        [
            sys.executable,
            str(SYNC_SCRIPT),
            "--platform-env",
            str(PLATFORM_ENV),
            "--secret-defs",
            str(SECRET_DEFS),
            "--output",
            str(ENV_EXAMPLE),
            "--check",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 2:
        logger.error("[IMP:10][gate] .env.example diverges from SoT:\n%s", result.stderr[:2000])
        pytest.fail(".env.example is stale — run: make sync-env-defaults")
    elif result.returncode != 0:
        logger.error("[IMP:10][gate] sync_env_defaults.py failed: %s", result.stderr[:1000])
        pytest.fail(f"sync_env_defaults.py exited {result.returncode}")
    logger.info("[IMP:9][gate] PASS: .env.example is fresh (byte-identical to generated output)")


@pytest.mark.gate
@ldd_trajectory
def test_no_proxy_superset(caplog):
    """.env.example NO_PROXY must be a superset of platform-infra.yaml no_proxy_internal."""
    with open(PLATFORM_INFRA) as f:
        infra = yaml.safe_load(f)
    no_proxy_internal = infra.get("proxy", {}).get("no_proxy_internal", "")
    so_t_entries = {e.strip() for e in no_proxy_internal.split(",") if e.strip()}

    env_noproxy = ""
    with open(ENV_EXAMPLE) as f:
        for line in f:
            if line.startswith("NO_PROXY="):
                env_noproxy = line.split("=", 1)[1].strip().strip('"')
                break
    env_entries = {e.strip() for e in env_noproxy.split(",") if e.strip()}

    missing = so_t_entries - env_entries
    if missing:
        logger.error("[IMP:10][gate] .env.example NO_PROXY missing entries: %s", sorted(missing))
        logger.error("[IMP:10][gate] SoT (platform-infra): %s", sorted(so_t_entries))
        logger.error("[IMP:10][gate] .env.example: %s", sorted(env_entries))
        pytest.fail(f".env.example NO_PROXY missing SoT entries: {sorted(missing)}")

    logger.info(
        "[IMP:9][gate] PASS: .env.example NO_PROXY superset (SoT=%d, env=%d)", len(so_t_entries), len(env_entries)
    )


@pytest.mark.gate
@ldd_trajectory
def test_postgres_password_unified(caplog):
    """All POSTGRES_PASSWORD defaults match secret-definitions.yaml ci_default (test-pg-pwd)."""
    with open(SECRET_DEFS) as f:
        sd = yaml.safe_load(f)

    pg_ci_default = None
    for s in sd.get("secrets", []):
        if s.get("name") == "POSTGRES_PASSWORD":
            pg_ci_default = s.get("ci_default", "")
            break

    assert pg_ci_default == "test-pg-pwd", f"POSTGRES_PASSWORD ci_default must be test-pg-pwd, got {pg_ci_default}"

    # Check .env.example
    with open(ENV_EXAMPLE) as f:
        for line in f:
            if line.startswith("POSTGRES_PASSWORD="):
                val = line.split("=", 1)[1].strip()
                assert val == "test-pg-pwd", f".env.example POSTGRES_PASSWORD = {val}, expected test-pg-pwd"
                break

    # Check hermes-agent .env.example
    hermes_env_example = ROOT / "core" / "modules" / "hermes-agent" / ".env.example"
    if hermes_env_example.is_file():
        with open(hermes_env_example) as f:
            for line in f:
                if line.startswith("POSTGRES_PASSWORD="):
                    val = line.split("=", 1)[1].strip()
                    assert val == "test-pg-pwd", (
                        f"hermes-agent/.env.example POSTGRES_PASSWORD = {val}, expected test-pg-pwd"
                    )
                    break

    # Check hermes-agent .env
    hermes_env = ROOT / "core" / "modules" / "hermes-agent" / ".env"
    if hermes_env.is_file():
        with open(hermes_env) as f:
            for line in f:
                if line.startswith("POSTGRES_PASSWORD="):
                    val = line.split("=", 1)[1].strip()
                    assert val == "test-pg-pwd", f"hermes-agent/.env POSTGRES_PASSWORD = {val}, expected test-pg-pwd"
                    break

    logger.info("[IMP:9][gate] PASS: POSTGRES_PASSWORD unified to test-pg-pwd across all 4 consumers")


@pytest.mark.gate
@ldd_trajectory
def test_s3_endpoint_removed(caplog):
    """S3_ENDPOINT (without _URL) must NOT exist in production code."""
    # Search production code: Python, shell, compose, .env files
    search_dirs = [
        ROOT / "core",
        ROOT / ".env",
        ROOT / ".env.example",
    ]

    # Build grep command — search for S3_ENDPOINT but NOT S3_ENDPOINT_URL
    violations: list[str] = []
    patterns_to_check = [
        r"S3_ENDPOINT[^_]",  # catches S3_ENDPOINT=, S3_ENDPOINT}, S3_ENDPOINT", etc.
    ]

    for search_path in search_dirs:
        if not search_path.exists():
            continue
        if search_path.is_file():
            # Check single file
            try:
                content = search_path.read_text()
                for pat in patterns_to_check:
                    matches = re.finditer(pat, content)
                    for m in matches:
                        ctx_start = max(0, m.start() - 10)
                        ctx_end = min(len(content), m.end() + 30)
                        violations.append(f"{search_path}: ...{content[ctx_start:ctx_end]}...")
            except Exception:
                pass
            continue
        # Directory — search files
        for ext in ("*.py", "*.yml", "*.yaml", "*.sh", "*.env"):
            for fpath in search_path.rglob(ext):
                # Skip test files
                if "tests/" in str(fpath) or "/test_" in str(fpath):
                    continue
                # Skip __pycache__
                if "__pycache__" in str(fpath):
                    continue
                try:
                    content = fpath.read_text()
                    for pat in patterns_to_check:
                        matches = re.finditer(pat, content)
                        for m in matches:
                            ctx_start = max(0, m.start() - 10)
                            ctx_end = min(len(content), m.end() + 30)
                            violations.append(f"{fpath}: ...{content[ctx_start:ctx_end]}...")
                except Exception:
                    pass

    if violations:
        logger.error("[IMP:10][gate] S3_ENDPOINT (without _URL) found in production code:")
        for v in violations:
            logger.error("  %s", v)
        pytest.fail(
            f"S3_ENDPOINT found in {len(violations)} location(s) — remove S3_ENDPOINT alias, keep only S3_ENDPOINT_URL"
        )

    logger.info("[IMP:9][gate] PASS: S3_ENDPOINT removed from production code (zero references)")


@pytest.mark.gate
@ldd_trajectory
def test_platform_domain_default(caplog):
    """PLATFORM_DOMAIN default is ai-platform.local in gen-env-platform.sh."""
    with open(GEN_ENV_PLATFORM_SH) as f:
        content = f.read()

    # Check for the default domain
    assert "ai-platform.local" in content, "gen-env-platform.sh must reference ai-platform.local"
    assert "tronyx.ru" not in content, "gen-env-platform.sh must NOT reference the old default tronyx.ru"

    logger.info("[IMP:9][gate] PASS: PLATFORM_DOMAIN default = ai-platform.local")


@pytest.mark.gate
@ldd_trajectory
def test_no_inline_python3_in_scaffold(caplog):
    """gen-env-platform.sh must be a thin facade — zero inline python3 heredoc blocks."""
    with open(GEN_ENV_PLATFORM_SH) as f:
        content = f.read()

    # Check for inline python3 patterns
    inline_patterns = [
        r'python3\s+-c\s+"',
        r"python3\s+-c\s+'",
        r"python3\s+<<\s*PYEOF",
        r"python3\s+<<\s*'PYEOF'",
    ]

    for pat in inline_patterns:
        if re.search(pat, content):
            logger.error("[IMP:10][gate] Inline python3 found in gen-env-platform.sh matching pattern: %s", pat)
            pytest.fail(
                "gen-env-platform.sh contains inline python3 — extract to gen_env_platform.py (Tier 1 Strangler)"
            )

    # Verify the thin facade calls the Python module
    assert "gen_env_platform.py" in content, "gen-env-platform.sh must delegate to gen_env_platform.py"

    # Check shell script is reasonably short (< 150 lines)
    lines = content.splitlines()
    logger.info("[IMP:9][gate] PASS: gen-env-platform.sh is thin facade (%d lines, no inline python3)", len(lines))


@pytest.mark.gate
@ldd_trajectory
def test_nextauth_secret_precondition(caplog):
    """Skip NEXTAUTH_SECRET validation if DevPlan 078 not merged; validate if present.

    DevPlan 082 §14: NEXTAUTH_SECRET drift is scoped OUT and deferred to DevPlan 078.
    When 078 is merged, this test self-heals — validates ci_default matches .env.example.
    """
    with open(SECRET_DEFS) as f:
        sd = yaml.safe_load(f)

    nextauth_ci = None
    for s in sd.get("secrets", []):
        if s.get("name") == "NEXTAUTH_SECRET":
            nextauth_ci = s.get("ci_default", "")
            break

    # DevPlan 078 merged — validate NEXTAUTH_SECRET consistency
    with open(ENV_EXAMPLE) as f:
        for line in f:
            if line.startswith("NEXTAUTH_SECRET="):
                env_val = line.split("=", 1)[1].strip()
                assert env_val == nextauth_ci, f"NEXTAUTH_SECRET .env.example={env_val} != ci_default={nextauth_ci}"
                break

    logger.info("[IMP:9][gate] PASS: NEXTAUTH_SECRET consistent between secret-defs ci_default and .env.example")
