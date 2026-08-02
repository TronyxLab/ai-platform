#!/usr/bin/env python3
# GREP_SUMMARY: tls-wildcard predeploy-gate nginx-vhost ssl-certificate letsencrypt node-yaml domain server-name hardcoded-domain wildcard-cert contract-test bash-syntax acme-sh tls-scripts subprocess
# STRUCTURE: ▶ platform_root → ◇ static_validation(vhost_configs,server_names,node_yaml) ∋ ssl_cert_path ⊕ server_name_template ⊕ hermes_vhost → ⊕ contract_test ∋ tls_scripts_exist ⊕ bash_syntax ⊕ acme_sh_available → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Pre-deploy TLS wildcard gate + contract tests. Static analysis of nginx vhost
##           configs and node.yaml to ensure all TLS references use the wildcard-ready
##           ${PLATFORM_DOMAIN} template, and no hardcoded domain names or non-wildcard
##           cert paths are present. PLUS contract tests that call real bash scripts
##           (issue-cert.sh, acme.sh) to verify they exist and are valid.
##           Replaces the old Simulator-only approach with REAL subprocess contract tests.
## @scope    Static file analysis + subprocess contract calls. No network access required.
##           Validates: node.yaml domain, nginx vhost SSL cert paths, server_name template,
##           platform-default HTTPS structure, hermes-dashboard.conf structure, TLS script
##           existence/syntax, acme.sh availability.
## @invariants
##   - Static tests use @ldd_trajectory — IMP:9 business logic log required
##   - No hardcoded domain names in assertions (uses PLATFORM_DOMAIN_TEMPLATE)
##   - Contract tests call REAL bash scripts via subprocess — NO mocking, NO simulation
##   - Excludes nginx.conf (main config, not a vhost)
##   - Excludes platform-http.conf from SSL checks (HTTP-only bootstrap)
##   - Contract tests skip if TLS scripts not found (graceful skip)
## @rationale  Pre-deploy static check catches TLS misconfiguration BEFORE deployment.
##             Contract tests verify the real TLS scripts are intact and syntactically valid.
##             Together they replace the Simulator approach with real bash verification.
## @changes — REWRITTEN: 2026-07-09 | TASK-4A: replaced Simulator with contract tests
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml
from _conftest.honesty import require_script_or_fail
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Relative path from project root to nginx vhost config directory
VHOST_CONFIG_DIR_REL: str = os.path.join("core", "modules", "nginx", "config")

# The wildcard-ready domain template used in all vhost configs
# ⚡ TRAP[DECISION] · 2026-07-04 · — · Using ${PLATFORM_DOMAIN} template
# ·   Rejected: hardcoded domain in configs (e.g. tronyx.ru)
# ·   Reason: ${PLATFORM_DOMAIN} allows per-deployment domain substitution via sed.
# ·           Hardcoded domain breaks staging/test environments with different domains.
PLATFORM_DOMAIN_TEMPLATE: str = "${PLATFORM_DOMAIN}"

# Expected Let's Encrypt certificate path prefix
LE_CERT_PATH_PREFIX: str = "/etc/letsencrypt/live/"

# Vhost config files that SHOULD contain SSL blocks (HTTPS enabled)
SSL_VHOST_FILES: frozenset = frozenset(
    {
        "platform-default.conf.template",
        "hermes-dashboard.conf",
        "grafana-vhost.conf",
        "prometheus-vhost.conf",
        "loki-vhost.conf",
        "langfuse-vhost.conf",
    }
)

# All vhost config files (including HTTP-only bootstrap), excluding main nginx.conf
ALL_VHOST_FILES: frozenset = frozenset(
    {
        "platform-default.conf.template",
        "platform-http.conf",
        "hermes-dashboard.conf",
        "grafana-vhost.conf",
        "prometheus-vhost.conf",
        "loki-vhost.conf",
        "langfuse-vhost.conf",
    }
)

# Files that are explicitly HTTP-only (no SSL expected)
HTTP_ONLY_VHOST_FILES: frozenset = frozenset(
    {
        "platform-http.conf",
    }
)

# File explicitly excluded from vhost checks (main nginx config, not a vhost)
EXCLUDED_FILES: frozenset = frozenset(
    {
        "nginx.conf",
    }
)

# Paths to TLS-related scripts for contract tests
# Relative from platform root
TLS_SCRIPT_PATHS: list[str] = [
    os.path.join("core", "internal", "bootstrap", "issue-cert.sh"),
]

# ── Regex Patterns ─────────────────────────────────────────────────────────────

# Match server_name directive in nginx config: `server_name value;`
_SERVER_NAME_RE: re.Pattern = re.compile(
    r"^\s*server_name\s+(.+?);\s*$",
    re.MULTILINE,
)

# Match ssl_certificate directive in nginx config: `ssl_certificate path;`
_SSL_CERT_RE: re.Pattern = re.compile(
    r"^\s*ssl_certificate\s+(.+?);\s*$",
    re.MULTILINE,
)


# ── Static Analysis Helpers ─────────────────────────────────────────────────

# region HELPERS


def _read_conf(platform_root: str, filename: str) -> str:
    """Read an nginx config file from the project tree.

    ## @purpose — Read a single nginx vhost config file by name.
    ## @io — ⇥ platform_root: str, filename: str → ⎋ str (file content)
    ## @complexity — O(L) where L = file line count
    ## @invariants
    ##   - File must exist at {platform_root}/{VHOST_CONFIG_DIR_REL}/{filename}
    ##   - Raises FileNotFoundError with descriptive message on failure
    """
    config_dir = os.path.join(platform_root, VHOST_CONFIG_DIR_REL)
    filepath = os.path.join(config_dir, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"[IMP:9][_read_conf] Vhost config not found: {filepath}")
    with open(filepath) as f:
        content = f.read()
    logger.info("[IMP:8][_read_conf] Read %s (%d bytes)", filename, len(content))
    return content


def _extract_server_names(text: str) -> list[str]:
    """Extract individual server_name values from nginx config text.

    ## @purpose — Parse server_name directives and return individual name strings.
    ## @io — ⇥ text: str (config content) → ⎋ list[str] (individual server names)
    ## @complexity — O(N * M) where N = matches, M = names per match
    ## @invariants
    ##   - Supports single and multi-name server_name lines
    ##   - Skips empty/whitespace-only tokens
    ##   - Returns names in order of appearance
    """
    names: list[str] = []
    for match in _SERVER_NAME_RE.finditer(text):
        raw = match.group(1).strip()
        for name in raw.split():
            name = name.strip().rstrip(";")
            if name:
                names.append(name)
    logger.info("[IMP:8][_extract_server_names] Found %d server_name value(s)", len(names))
    return names


def _extract_ssl_cert_paths(text: str) -> list[str]:
    """Extract ssl_certificate path values from nginx config text.

    ## @purpose — Parse ssl_certificate directives and return paths.
    ## @io — ⇥ text: str (config content) → ⎋ list[str] (certificate paths)
    ## @complexity — O(N) where N = number of ssl_certificate directives
    """
    paths = [m.group(1).strip().rstrip(";") for m in _SSL_CERT_RE.finditer(text)]
    logger.info("[IMP:8][_extract_ssl_cert_paths] Found %d ssl_certificate directive(s)", len(paths))
    return paths


def _is_valid_server_name(name: str) -> bool:
    """Check if a server_name value uses the domain template or default catch-all.

    ## @purpose — Validate that a server_name is either the default catch-all `_`
    ##            or contains the ${PLATFORM_DOMAIN} template. Any value that looks
    ##            like a hardcoded FQDN (contains '.' but not the template) is invalid.
    ## @io — ⇥ name: str → ⎋ bool
    ## @complexity — O(1)
    ## @invariants
    ##   - '_' (catch-all) is always valid
    ##   - Any value containing ${PLATFORM_DOMAIN} is valid
    ##   - Empty string is invalid
    ##   - 'localhost' without template is invalid (not a vhost pattern)
    ## @rationale — Wildcard domain template must be used for all server names to
    ##              support per-deployment domain substitution. Hardcoded domains
    ##              break staging/test environments.
    """
    if not name:
        return False
    if name == "_":
        return True
    return PLATFORM_DOMAIN_TEMPLATE in name


# endregion HELPERS


# ── Fixtures ───────────────────────────────────────────────────────────────────

# region FIXTURES


@pytest.fixture(scope="module")
def _platform_root() -> str:
    """Resolve project root from test file location.

    ## @purpose — Compute absolute path to project root (ai-platform/).
    ## @io — ⎋ str: absolute POSIX path to project root
    ## @complexity — O(1)
    ## @invariants — Uses pathlib.Path(__file__).resolve().parent.parent
    """
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    logger.info("[IMP:7][_platform_root] Resolved platform root: %s", root)
    return root


@pytest.fixture(scope="module")
def _tls_script_paths(_platform_root: str) -> list[str]:
    """Resolve absolute paths to TLS-related scripts.

    ## @purpose — Convert relative TLS_SCRIPT_PATHS to absolute paths using platform root.
    ## @io — ⇥ _platform_root → ⎋ list[str] of absolute script paths
    ## @complexity — O(N) where N = len(TLS_SCRIPT_PATHS)
    """
    paths = [os.path.join(_platform_root, rel) for rel in TLS_SCRIPT_PATHS]
    logger.info("[IMP:7][_tls_script_paths] Resolved %d TLS script path(s)", len(paths))
    return paths


# endregion FIXTURES


# ══════════════════════════════════════════════════════════════════════════
# STATIC VALIDATION TESTS (preserved from original)
# ══════════════════════════════════════════════════════════════════════════

# region TESTS_STATIC


# region FUNC_test_node_yaml_has_domain_field
## @purpose — Verify that a valid node.yaml contains the `domain` field with
##            a non-empty string value. The domain field is required by the
##            node.schema.json for wildcard TLS deployment.
## @io — ⇥ tmp_path, caplog → ⎋ None (pytest.fail on missing/empty domain)
## @complexity — O(1)
## @invariants
##   - Creates synthetic node.yaml in tmp_path (not reading test_data/node.yaml)
##   - Requires yaml.safe_load() for parsing
##   - domain must be present, non-empty, and a string
## @rationale — Without the domain field, the wildcard TLS deployment cannot derive
##              the certificate path (/etc/letsencrypt/live/${domain}/). The schema
##              enforces this, but the gate test catches it before CI.


@pytest.mark.predeploy
@ldd_trajectory
def test_node_yaml_has_domain_field(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """
    # ▶ tmp_path → ∋ node.yaml{domain} → ⚡ yaml.safe_load → ◇ 'domain' in data?
    # → ◇ data['domain'] non-empty str? → ⎋ pass | fail
    """

    # 🧪 TRAP[TEST] · Regression: no · Scenario: synthetic node.yaml in tmp_path → yaml.safe_load → assert domain present · Last fail: Never · Remove if: node.schema.json domain field removed from required
    # region BLOCK_Setup
    logger.info("[IMP:7][test_node_yaml_has_domain_field] Creating test node.yaml in tmp_path ...")
    # endregion

    # region BLOCK_CreateYaml
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("""\
domain: example.com
node:
  name: test-node
  host: 192.168.1.1
  owner_key: ssh-ed25519 test-key
modules:
  - name: nginx
    enabled: true
""")
    logger.info("[IMP:8][test_node_yaml_has_domain_field] Written: %s", node_yaml)
    # endregion

    # region BLOCK_Parse
    with open(node_yaml) as f:
        data: dict = yaml.safe_load(f)
    logger.info("[IMP:8][test_node_yaml_has_domain_field] Loaded YAML keys: %s", list(data.keys()))
    # endregion

    # region BLOCK_Assert
    assert "domain" in data, "[IMP:9][test_node_yaml_has_domain_field] FAIL: node.yaml missing 'domain' field"
    logger.info("[IMP:9][test_node_yaml_has_domain_field] 'domain' key present")

    assert data["domain"], "[IMP:9][test_node_yaml_has_domain_field] FAIL: node.yaml 'domain' field is empty"
    assert isinstance(data["domain"], str), (
        "[IMP:9][test_node_yaml_has_domain_field] FAIL: node.yaml 'domain' is not string"
    )
    logger.info("[IMP:9][test_node_yaml_has_domain_field] ✅ domain='%s' (type=str)", data["domain"])
    # endregion


# endregion FUNC_test_node_yaml_has_domain_field


# region FUNC_test_vhost_configs_use_wildcard_cert_path
## @purpose — Verify all SSL-enabled nginx vhost configs reference the correct
##            Let's Encrypt wildcard cert path: /etc/letsencrypt/live/${PLATFORM_DOMAIN}/.
##            This ensures the wildcard-ready template is used, not a hardcoded path.
## @io — ⇥ _platform_root, caplog → ⎋ None (pytest.fail on wrong cert path)
## @complexity — O(F * P) where F = vhost files, P = paths per file
## @invariants
##   - Only checks SSL_VHOST_FILES (files expected to have ssl_certificate)
##   - Each ssl_certificate path must contain LE_CERT_PATH_PREFIX
##   - Each ssl_certificate path must contain PLATFORM_DOMAIN_TEMPLATE
##   - Files with no ssl_certificate directives fail (expected SSL missing)
## @rationale — Non-template cert paths (like /etc/ssl/certs/self-signed.crt) or
##              hardcoded domains in the path would break TLS on deployment.


@pytest.mark.predeploy
@ldd_trajectory
def test_vhost_configs_use_wildcard_cert_path(
    caplog: pytest.LogCaptureFixture,
    _platform_root: str,
) -> None:
    """
    # ▶ _platform_root + config_dir → SSL_VHOST_FILES → ∋ each .conf
    # → ⚡ _extract_ssl_cert_paths() → ◇ contains LE_CERT_PATH_PREFIX?
    # → ◇ contains PLATFORM_DOMAIN_TEMPLATE? → ⊕ all pass? → ⎋ pass | fail
    """

    # 🧪 TRAP[TEST] · Regression: all SSL vhosts must use LE path + PLATFORM_DOMAIN · Scenario: read each .conf → regex ssl_certificate → assert prefix + template · Last fail: Never · Remove if: nginx vhost template changed to non-LE cert path
    # region BLOCK_Setup
    logger.info(
        "[IMP:7][test_vhost_configs_use_wildcard_cert_path] Checking %d SSL vhost file(s) ...", len(SSL_VHOST_FILES)
    )
    errors: list[str] = []
    # endregion

    # region BLOCK_Scan
    for filename in sorted(SSL_VHOST_FILES):
        config_dir = os.path.join(_platform_root, VHOST_CONFIG_DIR_REL)
        filepath = os.path.join(config_dir, filename)

        if not os.path.isfile(filepath):
            errors.append(f"[{filename}] File not found at {filepath}")
            logger.error("[IMP:4][test_vhost_configs_use_wildcard_cert_path] Missing: %s", filepath)
            continue

        with open(filepath) as f:
            content = f.read()

        paths = _extract_ssl_cert_paths(content)

        # Wave 1: SSL certificate paths delegated to ssl-params.conf include snippet
        if not paths and "include /etc/nginx/conf.d/ssl-params.conf" in content:
            config_dir = os.path.join(_platform_root, VHOST_CONFIG_DIR_REL)
            for candidate in ("ssl-params.conf", "ssl-params.conf.template"):
                ssl_params_path = os.path.join(config_dir, candidate)
                if os.path.isfile(ssl_params_path):
                    with open(ssl_params_path) as sp_f:
                        paths = _extract_ssl_cert_paths(sp_f.read())
                    logger.info(
                        "[IMP:8][test_vhost_configs_use_wildcard_cert_path] %s: resolved %d cert path(s) via %s",
                        filename,
                        len(paths),
                        candidate,
                    )
                    break
        if not paths:
            errors.append(f"[{filename}] No ssl_certificate directives found (expected SSL-configured vhost)")
            logger.error("[IMP:4][test_vhost_configs_use_wildcard_cert_path] %s: no ssl_certificate found", filename)
            continue

        for cert_path in paths:
            if LE_CERT_PATH_PREFIX not in cert_path:
                errors.append(
                    f"[{filename}] ssl_certificate path '{cert_path}' does not contain '{LE_CERT_PATH_PREFIX}'"
                )
                logger.error(
                    "[IMP:9][test_vhost_configs_use_wildcard_cert_path] %s: bad prefix → %s", filename, cert_path
                )
            elif PLATFORM_DOMAIN_TEMPLATE not in cert_path:
                errors.append(
                    f"[{filename}] ssl_certificate path '{cert_path}' "
                    f"does not contain wildcard template '{PLATFORM_DOMAIN_TEMPLATE}'"
                )
                logger.error(
                    "[IMP:9][test_vhost_configs_use_wildcard_cert_path] %s: no template → %s", filename, cert_path
                )
            else:
                logger.info("[IMP:8][test_vhost_configs_use_wildcard_cert_path] ✅ %s: %s", filename, cert_path)
    # endregion

    # region BLOCK_Assert
    if errors:
        pytest.fail("SSL certificate path validation failed:\n" + "\n".join(errors))

    logger.info(
        "[IMP:9][test_vhost_configs_use_wildcard_cert_path] ✅ All %d SSL vhost file(s) use wildcard cert path",
        len(SSL_VHOST_FILES),
    )
    # endregion


# endregion FUNC_test_vhost_configs_use_wildcard_cert_path


# region FUNC_test_vhost_server_name_uses_domain_template
## @purpose — Verify all nginx vhost server_name directives use the
##            ${PLATFORM_DOMAIN} template or the default catch-all `_`.
##            Hardcoded domain names (e.g. tronyx.ru) in configs → FAIL.
## @io — ⇥ _platform_root, caplog → ⎋ None (pytest.fail on hardcoded domain)
## @complexity — O(F * N) where F = vhost files, N = server_name values per file
## @invariants
##   - Checks ALL_VHOST_FILES (all vhost configs including HTTP-only)
##   - server_name `_` (catch-all) is always valid
##   - server_name containing ${PLATFORM_DOMAIN} is valid
##   - Any other server_name (hardcoded FQDN, localhost, etc.) → FAIL
##   - nginx.conf is excluded (not a vhost)
## @rationale — Hardcoded domains break deployment on environments with different
##              domains (staging, CI). Wildcard template ${PLATFORM_DOMAIN} ensures
##              domain substitution at deploy time.


@pytest.mark.predeploy
@ldd_trajectory
def test_vhost_server_name_uses_domain_template(
    caplog: pytest.LogCaptureFixture,
    _platform_root: str,
) -> None:
    """
    # ▶ _platform_root + config_dir → ALL_VHOST_FILES → ∋ each .conf
    # → ⚡ _extract_server_names() → ∋ each name → ◇ _is_valid_server_name(name)?
    # → ⊕ invalid_names → ◇ any? → ⎋ fail | pass
    """

    # 🧪 TRAP[TEST] · Regression: no · Scenario: read each vhost .conf → regex server_name → assert _ or PLATFORM_DOMAIN template · Last fail: Never · Remove if: nginx vhosts use different domain substitution mechanism
    # region BLOCK_Setup
    logger.info(
        "[IMP:7][test_vhost_server_name_uses_domain_template] Checking %d vhost file(s) for hardcoded domains ...",
        len(ALL_VHOST_FILES),
    )
    invalid: list[tuple[str, str]] = []  # (filename, server_name)
    # endregion

    # region BLOCK_Scan
    for filename in sorted(ALL_VHOST_FILES):
        config_dir = os.path.join(_platform_root, VHOST_CONFIG_DIR_REL)
        filepath = os.path.join(config_dir, filename)

        if not os.path.isfile(filepath):
            logger.warning("[IMP:4][test_vhost_server_name_uses_domain_template] Missing: %s (skipping)", filepath)
            continue

        with open(filepath) as f:
            content = f.read()

        names = _extract_server_names(content)

        if not names:
            logger.warning(
                "[IMP:7][test_vhost_server_name_uses_domain_template] %s: no server_name directives found", filename
            )
            continue

        for name in names:
            if _is_valid_server_name(name):
                logger.info(
                    "[IMP:8][test_vhost_server_name_uses_domain_template] ✅ %s: server_name '%s'", filename, name
                )
            else:
                invalid.append((filename, name))
                logger.error(
                    "[IMP:9][test_vhost_server_name_uses_domain_template] "
                    "❌ %s: hardcoded/non-template server_name '%s'",
                    filename,
                    name,
                )
    # endregion

    # region BLOCK_Assert
    if invalid:
        detail = "\n".join(
            f"  [{f}] server_name '{n}' — must be '_' or contain '{PLATFORM_DOMAIN_TEMPLATE}'" for f, n in invalid
        )
        pytest.fail(f"Found {len(invalid)} hardcoded/non-template server_name directive(s):\n{detail}")

    logger.info("[IMP:9][test_vhost_server_name_uses_domain_template] ✅ All server_name values use domain template")
    # endregion


# endregion FUNC_test_vhost_server_name_uses_domain_template


# region FUNC_test_platform_default_conf_has_ssl
## @purpose — Verify platform-default.conf.template contains a proper HTTPS block:
##            `listen 443 ssl` and `ssl_certificate` referencing ${PLATFORM_DOMAIN}.
##            This is the primary platform vhost — if it lacks SSL, the entire
##            platform is served over HTTP.
## @io — ⇥ _platform_root, caplog → ⎋ None (pytest.fail on missing SSL)
## @complexity — O(L) where L = config file line count
## @invariants
##   - Must contain `listen 443 ssl` (may also have http2, default_server)
##   - Must contain at least one ssl_certificate directive
##   - The ssl_certificate path must contain PLATFORM_DOMAIN_TEMPLATE
## @rationale — platform-default.conf.template is the main vhost for ${PLATFORM_DOMAIN}.
##              Without its HTTPS block, the platform root URL serves HTTP only.


@pytest.mark.predeploy
@ldd_trajectory
def test_platform_default_conf_has_ssl(
    caplog: pytest.LogCaptureFixture,
    _platform_root: str,
) -> None:
    """
    # ▶ platform-default.conf.template → ⚡ read → ◇ 'listen 443 ssl'?
    # → ◇ ssl_certificate + PLATFORM_DOMAIN_TEMPLATE? → ⎋ pass | fail
    """

    # 🧪 TRAP[TEST] · Regression: platform-default.conf HTTPS was commented out (TRAP[BUG] 2026-06-07) · Scenario: read config → check 'listen 443 ssl' + ssl_certificate PLATFORM_DOMAIN · Last fail: Never · Remove if: platform vhost template replaced with non-nginx solution
    # region BLOCK_Setup
    filename = "platform-default.conf.template"
    logger.info("[IMP:7][test_platform_default_conf_has_ssl] Checking %s for HTTPS block ...", filename)
    # endregion

    # region BLOCK_Read
    content = _read_conf(_platform_root, filename)
    # endregion

    # region BLOCK_CheckListen
    has_ssl_listen = "listen 443 ssl" in content
    logger.info("[IMP:8][test_platform_default_conf_has_ssl] Contains 'listen 443 ssl': %s", has_ssl_listen)
    # endregion

    # region BLOCK_CheckCert
    cert_paths = _extract_ssl_cert_paths(content)
    has_cert = len(cert_paths) > 0
    has_template_cert = any(PLATFORM_DOMAIN_TEMPLATE in p for p in cert_paths)
    logger.info(
        "[IMP:8][test_platform_default_conf_has_ssl] ssl_certificate count: %d, contains template: %s",
        len(cert_paths),
        has_template_cert,
    )
    for p in cert_paths:
        logger.info("[IMP:8][test_platform_default_conf_has_ssl]   cert path: %s", p)
    # endregion

    # region BLOCK_Assert
    failures: list[str] = []
    if not has_ssl_listen:
        failures.append("platform-default.conf.template: missing 'listen 443 ssl' directive")
    if not has_cert:
        failures.append("platform-default.conf.template: no ssl_certificate directive found")
    if not has_template_cert:
        failures.append(
            f"platform-default.conf.template: ssl_certificate paths missing '{PLATFORM_DOMAIN_TEMPLATE}' template"
        )

    if failures:
        pytest.fail("platform-default.conf.template SSL validation failed:\n" + "\n".join(f"  - {f}" for f in failures))

    logger.info(
        "[IMP:9][test_platform_default_conf_has_ssl] ✅ %s: HTTPS block with wildcard cert path confirmed", filename
    )
    # endregion


# endregion FUNC_test_platform_default_conf_has_ssl


# region FUNC_test_hermes_vhost_conditionally_deployed
## @purpose — Verify hermes-dashboard.conf exists and has the correct structure:
##            HTTP → HTTPS redirect block + HTTPS proxy_pass to hermes-agent:9119 (Docker-DNS).
##            Hermes Dashboard is conditionally deployed (depends on
##            PLATFORM_HERMES_ENABLED in node.yaml) but the template must be valid.
## @io — ⇥ _platform_root, caplog → ⎋ None (pytest.fail on structure errors)
## @complexity — O(L) where L = config file line count
## @invariants
##   - File must exist in the nginx config directory
##   - Must contain an HTTP server block with `listen 80` and `return 301 https://`
##   - Must contain an HTTPS server block with `listen 443 ssl` and `proxy_pass`
##   - proxy_pass must use Docker-DNS variable pattern: `set $upstream_hermes hermes-agent:9119`
##     + `proxy_pass http://$upstream_hermes` (NOT loopback 127.0.0.1)
##   - SSL cert must use PLATFORM_DOMAIN_TEMPLATE
## @rationale — Hermes Dashboard is a critical platform UI. The conditional deploy
##              pattern must not break TLS. Template must always be valid regardless
##              of whether the module is enabled.
## @changes 2026-07-16 | Updated per TASK-3 DevPlan 001: loopback→Docker-DNS variable pattern


@pytest.mark.predeploy
@ldd_trajectory
def test_hermes_vhost_conditionally_deployed(
    caplog: pytest.LogCaptureFixture,
    _platform_root: str,
) -> None:
    """
    # ▶ hermes-dashboard.conf → ⚡ read → ◇ HTTP(80→redirect) → ◇ HTTPS(443+proxy_pass)
    # → ◇ set $upstream_hermes hermes-agent:9119 → ◇ proxy_pass $upstream_hermes → ⎋ pass | fail
    """

    # 🧪 TRAP[TEST] · Regression: TASK-3 DevPlan 001 loopback→Docker-DNS · Scenario: read hermes-dashboard.conf → assert HTTP redirect + set $upstream_hermes hermes-agent:9119 + proxy_pass http://$upstream_hermes + cert template · Last fail: Never · Remove if: Hermes Dashboard deployed via different mechanism
    # region BLOCK_Setup
    filename = "hermes-dashboard.conf"
    logger.info("[IMP:7][test_hermes_vhost_conditionally_deployed] Checking %s structure ...", filename)
    # endregion

    # region BLOCK_Read
    content = _read_conf(_platform_root, filename)
    # endregion

    # region BLOCK_CheckHttpRedirect
    has_http_redirect = "listen 80" in content and "return 301 https://" in content
    logger.info("[IMP:8][test_hermes_vhost_conditionally_deployed] HTTP redirect block: %s", has_http_redirect)
    # endregion

    # region BLOCK_CheckHttpsProxy
    has_ssl_block = "listen 443 ssl" in content
    has_set_upstream = "set $upstream_hermes hermes-agent:9119;" in content
    has_var_proxy_pass = "proxy_pass http://$upstream_hermes" in content
    logger.info(
        "[IMP:8][test_hermes_vhost_conditionally_deployed] HTTPS block: %s, set $upstream_hermes: %s, var proxy_pass: %s",
        has_ssl_block,
        has_set_upstream,
        has_var_proxy_pass,
    )
    # endregion

    # region BLOCK_CheckCert
    cert_paths = _extract_ssl_cert_paths(content)
    # Wave 1: SSL delegated to ssl-params.conf snippet — resolve from shared file
    if not cert_paths and "include /etc/nginx/conf.d/ssl-params.conf" in content:
        config_dir = os.path.join(_platform_root, VHOST_CONFIG_DIR_REL)
        for candidate in ("ssl-params.conf", "ssl-params.conf.template"):
            ssl_params_path = os.path.join(config_dir, candidate)
            if os.path.isfile(ssl_params_path):
                with open(ssl_params_path) as sp_f:
                    cert_paths = _extract_ssl_cert_paths(sp_f.read())
                if cert_paths:
                    logger.info(
                        "[IMP:8][test_hermes_vhost_conditionally_deployed] Resolved %d cert path(s) from %s",
                        len(cert_paths),
                        candidate,
                    )
                break
    has_template_cert = any(PLATFORM_DOMAIN_TEMPLATE in p for p in cert_paths)
    logger.info(
        "[IMP:8][test_hermes_vhost_conditionally_deployed] Cert paths: %d, contains template: %s",
        len(cert_paths),
        has_template_cert,
    )
    # endregion

    # region BLOCK_Assert
    failures: list[str] = []
    if not has_http_redirect:
        failures.append(f"{filename}: missing HTTP→HTTPS redirect block (listen 80 + return 301 https://)")
    if not has_ssl_block:
        failures.append(f"{filename}: missing HTTPS block (listen 443 ssl)")
    if not has_set_upstream:
        failures.append(f"{filename}: missing 'set $upstream_hermes hermes-agent:9119;'")
    if not has_var_proxy_pass:
        failures.append(f"{filename}: missing 'proxy_pass http://$upstream_hermes'")
    if not has_template_cert:
        failures.append(f"{filename}: ssl_certificate missing '{PLATFORM_DOMAIN_TEMPLATE}' template")

    if failures:
        pytest.fail(f"{filename} structure validation failed:\n" + "\n".join(f"  - {f}" for f in failures))

    logger.info(
        "[IMP:9][test_hermes_vhost_conditionally_deployed] ✅ %s: HTTP redirect + Docker-DNS proxy_pass structure confirmed",
        filename,
    )
    # endregion


# endregion FUNC_test_hermes_vhost_conditionally_deployed

# endregion TESTS_STATIC


# ══════════════════════════════════════════════════════════════════════════
# CONTRACT TESTS (new — call real bash scripts via subprocess)
# ══════════════════════════════════════════════════════════════════════════

# region TESTS_CONTRACT


# region FUNC_test_tls_scripts_exist
## @purpose  Verify TLS-related bash scripts (issue-cert.sh) exist on disk.
##           These scripts handle certificate issuance, renewal, and acme.sh integration.
## @io       ⇥ _tls_script_paths → ⎋ None (asserts)
## @complexity  O(N) where N = number of script paths
## @invariants
##   - Each path in TLS_SCRIPT_PATHS must resolve to an existing regular file
##   - Missing script = bootstrap/TLS pipeline is broken
## @rationale  Contract test: verifies the real TLS scripts are present in the project tree.
##             If a script was moved or deleted, TLS issuance would fail silently at bootstrap.


# Both @pytest.mark.contract AND @pytest.mark.predeploy — contract tests run in both suites
@pytest.mark.contract
@pytest.mark.predeploy
def test_tls_scripts_exist(_tls_script_paths: list[str]) -> None:
    """
    # ▶ _tls_script_paths → ∋ each path → ◇ os.path.isfile? → ⊕ results → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_tls_scripts_exist] Checking %d TLS script(s)", len(_tls_script_paths))
    missing: list[str] = []

    for script_path in _tls_script_paths:
        if os.path.isfile(script_path):
            logger.info("[IMP:8][test_tls_scripts_exist] ✅ Found: %s", script_path)
        else:
            missing.append(script_path)
            logger.error("[IMP:9][test_tls_scripts_exist] ❌ Missing: %s", script_path)

    assert not missing, f"[IMP:9][test_tls_scripts_exist] FAIL: {len(missing)} TLS script(s) missing:\n" + "\n".join(
        f"  - {p}" for p in missing
    )
    logger.info("[IMP:9][test_tls_scripts_exist] PASS: All %d TLS scripts exist", len(_tls_script_paths))


# endregion FUNC_test_tls_scripts_exist


# region FUNC_test_tls_scripts_syntax
## @purpose  Verify TLS-related bash scripts are syntactically valid via `bash -n`.
##           A syntax error in issue-cert.sh would block wildcard certificate
##           issuance and renewal, breaking TLS for all platform services.
## @io       ⇥ _tls_script_paths → ⎋ None (asserts bash -n returncode == 0 for each)
## @complexity  O(N) where N = number of scripts
## @invariants
##   - bash -n validates each script without executing it
##   - returncode != 0 means syntax error → deployment + TLS renewal broken
## @rationale  Contract test: catches bash syntax regressions in critical TLS scripts
##             via REAL bash binary, not simulation.


# Both @pytest.mark.contract AND @pytest.mark.predeploy — contract tests run in both suites
@pytest.mark.contract
@pytest.mark.predeploy
def test_tls_scripts_syntax(_tls_script_paths: list[str]) -> None:
    """
    # ▶ _tls_script_paths → ∋ each path → ⚡ bash -n → ◇ returncode == 0? → ⊕ results → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_tls_scripts_syntax] Checking syntax for %d script(s)", len(_tls_script_paths))
    errors: list[str] = []

    for script_path in _tls_script_paths:
        if not os.path.isfile(script_path):
            errors.append(f"[{script_path}] File not found — skipping syntax check")
            continue

        result = subprocess.run(
            ["bash", "-n", script_path],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("[IMP:8][test_tls_scripts_syntax] ✅ Syntax OK: %s", script_path)
        else:
            err_msg = f"[{script_path}] Syntax error (exit={result.returncode}): {result.stderr.strip()}"
            errors.append(err_msg)
            logger.error("[IMP:9][test_tls_scripts_syntax] ❌ %s", err_msg)

        # Print LDD trajectory
        print(f"[IMP:7][test_tls_scripts_syntax] bash -n {script_path} → exit={result.returncode}")
        if result.stderr:
            for line in result.stderr.splitlines():
                print(f"[IMP:7][bash-n/stderr] {line}")

    assert not errors, (
        f"[IMP:9][test_tls_scripts_syntax] FAIL: {len(errors)} script(s) with syntax errors:\n" + "\n".join(errors)
    )
    logger.info("[IMP:9][test_tls_scripts_syntax] PASS: All TLS scripts syntactically valid")


# endregion FUNC_test_tls_scripts_syntax


# region FUNC_test_acme_sh_available
## @purpose  Check if acme.sh is available in PATH. acme.sh is the ACME client used for
##           wildcard Let's Encrypt certificate issuance via DNS-01 challenge.
##           R4 (Test Honesty, DevPlan 119 F1 R4-3): отсутствие acme.sh = конфигурационная
##           ошибка, не повод для skip. Диспетчеризация через require_script_or_fail —
##           REQUIRE_HONESTY_MODE=marker (локально) → skip; =fail (CI) → FAIL.
## @io       — (uses shutil.which) → ⎋ None (skip|fail через honesty-диспетчер)
## @complexity  O(1)
## @invariants
##   - shutil.which("acme.sh") checks PATH for the binary
##   - If not found: require_script_or_fail() диспетчеризует по REQUIRE_HONESTY_MODE
##   - If found: binary path is logged at IMP:9
## @rationale  Contract test: verifies the ACME client is available. R4: NO_SERVICE = FAIL,
##             not skip (environmental absence is a configuration error — surface it).


# Both @pytest.mark.contract AND @pytest.mark.predeploy — contract tests run in both suites
@pytest.mark.contract
@pytest.mark.predeploy
def test_acme_sh_available() -> None:
    """
    # ▶ shutil.which("acme.sh") → ◇ binary found? → ⎋ require_script_or_fail|pass
    """
    acme_path = shutil.which("acme.sh")
    logger.info("[IMP:7][test_acme_sh_available] Searching for acme.sh in PATH ...")

    if acme_path is None:
        # R4 (DevPlan 119 F1 R4-3): acme.sh не найден → require_script_or_fail
        # (marker→skip локально, fail→FAIL в CI). НЕ pytest.skip напрямую.
        logger.info("[IMP:7][test_acme_sh_available] acme.sh not in PATH — dispatching honesty mode")
        require_script_or_fail(
            pathlib.Path("/usr/local/bin/acme.sh"),
            reason="acme.sh not found in PATH — it is installed during bootstrap step ⑪",
        )
    else:
        logger.info("[IMP:9][test_acme_sh_available] ✅ acme.sh found: %s", acme_path)
        # Verify it's executable
        assert os.access(acme_path, os.X_OK), (
            f"[IMP:9][test_acme_sh_available] FAIL: acme.sh at {acme_path} is not executable"
        )
        logger.info("[IMP:9][test_acme_sh_available] PASS: acme.sh is available at %s", acme_path)


# endregion FUNC_test_acme_sh_available


# region FUNC_test_no_simulator_code
# ⚠️ TRAP[FIX] · 2026-07-10 · Epic 2 T2.1 · Replaced chr()-encoded self-read with glob-based scan
# · Old: chr(99)+chr(108)+... pattern masked self-reference; read_text(__file__) only checked own file
# · New: glob("tests/**/*.py") scans ENTIRE test suite for any Simulator class definition
_SIMULATOR_CLASS_PATTERN = re.compile(r"class\s+\w*Simulator")


@pytest.mark.contract
def test_no_simulator_code() -> None:
    """Verify that no Simulator class code exists anywhere in the test suite.

    # ▶ glob tests/**/*.py → ◇ for each file: re.findall(class.*Simulator) == 0? → ⎋ pass | fail
    ## @complexity O(F * L) where F = test files, L = lines per file
    """
    logger.info("[IMP:7][test_no_simulator_code] Scanning tests/ for Simulator class remnants...")

    tests_dir = pathlib.Path(__file__).resolve().parent
    all_py_files = sorted(tests_dir.rglob("*.py"))
    total_occurrences = 0

    for py_file in all_py_files:
        text = py_file.read_text(encoding="utf-8")
        count = len(_SIMULATOR_CLASS_PATTERN.findall(text))
        if count > 0:
            logger.warning("[IMP:7][test_no_simulator_code] Found %d in %s", count, py_file.relative_to(tests_dir))
            total_occurrences += count

    logger.info(
        "[IMP:8][test_no_simulator_code] Scanned %d Python files, found %d Simulator class reference(s)",
        len(all_py_files),
        total_occurrences,
    )

    assert total_occurrences == 0, (
        f"[IMP:9][test_no_simulator_code] FAIL: Found {total_occurrences} Simulator class reference(s) across "
        f"{len(all_py_files)} files — contract tests must use real bash, not simulation. "
        f"Run: grep -rn 'class.*Simulator' tests/"
    )
    logger.info(
        "[IMP:9][test_no_simulator_code] PASS: No Simulator class code found in any test file (%d scanned)",
        len(all_py_files),
    )


# endregion FUNC_test_no_simulator_code

# endregion TESTS_CONTRACT
