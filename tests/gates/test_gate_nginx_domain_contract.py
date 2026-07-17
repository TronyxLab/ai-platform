# GREP_SUMMARY: gate nginx domain-contract PLATFORM_DOMAIN server_name ssl_certificate templates envsubst regression D12
# STRUCTURE: ─▶ test_no_host_variable_in_server_name → ◇ rg `server_name \$host` + `live/\$host` in config/*.conf → ⊕ assert 0 matches → ─▶ test_platform_domain_placeholder_present → ◇ 5 vhosts: server_name + ssl_certificate contain ${PLATFORM_DOMAIN} → ◇ platform-default: at least 1 occurrence → ─▶ test_base_yml_mounts_templates → ◇ yaml.safe_load base.yml → ◇ for each conf volume (excl nginx.conf): target starts with /etc/nginx/templates/ and ends .conf.template
# region MODULE_CONTRACT
## @purpose  Gate tests: prevent regressions of D12 ($host-in-server_name), verify ${PLATFORM_DOMAIN} placerholder usage,
##           and validate envsubst-templates mount convention in docker-compose.base.yml
## @scope    Static analysis of nginx config files and docker-compose.base.yml — no Docker required.
##           Tests run as pytest.mark.gate (CI gate).
## @invariants
##   - test_no_host_variable_in_server_name: zero tolerance for `server_name $host` / `live/$host` in any config/*.conf
##   - test_platform_domain_placeholder_present: every vhost config MUST use `${PLATFORM_DOMAIN}` in server_name AND ssl_certificate paths
##   - test_base_yml_mounts_templates: every conf volume (except nginx.conf) targets /etc/nginx/templates/*.conf.template
## @rationale  D12 root cause: `server_name $host` is literal in nginx — variable interpolation does NOT
##             work in server_name. All 5 vhosts fell through to default_server. Revert + enforcement gate.
##             envsubst-templates is the official nginx image mechanism for env var substitution.
## @changes — 2026-07-16 | NEW: D12 regression gate (DevPlan-fix-D12 F7)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NGINX_CONFIG_DIR = _PROJECT_ROOT / "core" / "modules" / "nginx" / "config"
_BASE_YML = _PROJECT_ROOT / "core" / "modules" / "nginx" / "docker-compose.base.yml"

# Files that must use templates (7 files, excludes nginx.conf)
_TEMPLATE_CONF_FILES = {
    "platform-default.conf",
    "platform-http.conf",
    "grafana-vhost.conf",
    "hermes-dashboard.conf",
    "langfuse-vhost.conf",
    "loki-vhost.conf",
    "prometheus-vhost.conf",
}

# 5 vhost configs that must contain ${PLATFORM_DOMAIN} in server_name AND ssl_certificate
_VHOST_CONF_FILES = {
    "grafana-vhost.conf",
    "hermes-dashboard.conf",
    "langfuse-vhost.conf",
    "loki-vhost.conf",
    "prometheus-vhost.conf",
}


# region FUNC_test_no_host_variable_in_server_name
@pytest.mark.gate
def test_no_host_variable_in_server_name(caplog) -> None:
    """Regression D12: запрещены `server_name $host` и `live/$host` в config/*.conf.

    ## @purpose — Zero-tolerance gate: no config file may contain `server_name $host` or
    ##            `live/$host` patterns. These caused D12 where all vhost requests fell
    ##            through to default_server because nginx does NOT interpolate variables
    ##            in server_name. ssl_certificate paths containing `live/$host/` prevent
    ##            wildcard cert resolution.
    ## @io — ⇥ glob config/*.conf → ⚡ regex search → ⊕ assert 0 matches
    ## @complexity — O(N × L) where N = file count, L = file length
    """
    # 🧪 TRAP[TEST] · 2026-07-16 · gate/nginx-domain · Регресс D12: server_name $host
    caplog.set_level(logging.DEBUG)
    config_files = sorted(_NGINX_CONFIG_DIR.glob("*.conf"))
    assert config_files, f"No config files found in {_NGINX_CONFIG_DIR}"

    violations: list[str] = []
    for cf in config_files:
        text = cf.read_text()
        for pattern, label in [
            ("server_name\\s+\\$host", "server_name $host"),
            ("live/\\$host", "live/$host"),
        ]:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                violations.append(f"{cf.name}:{line_no}: {label}")
                logger.info("[IMP:9][gate] FAIL: %s:%d found %s", cf.name, line_no, label)

    assert not violations, (
        f"D12 regression: {len(violations)} violation(s) of `server_name $host` / `live/$host`:\n"
        + "\n".join(violations)
    )
    logger.info("[IMP:9][gate] PASS: No D12 regressions in %d config files", len(config_files))


# endregion FUNC_test_no_host_variable_in_server_name


# region FUNC_test_platform_domain_placeholder_present
@pytest.mark.gate
def test_platform_domain_placeholder_present(caplog) -> None:
    """Vhost configs используют `${PLATFORM_DOMAIN}` в server_name и ssl_certificate.

    ## @purpose — Verify each vhost config contains `${PLATFORM_DOMAIN}` in server_name
    ##            directives AND ssl_certificate paths. Also verify platform-default.conf
    ##            contains the placerholder at least once.
    ## @io — ⇥ 5 vhost confs + platform-default.conf → ⚡ regex search → ⊕ assert all required
    ## @complexity — O(N × L)
    """
    # 🧪 TRAP[TEST] · 2026-07-16 · gate/nginx-domain · Revert D12 — verify PLATFORM_DOMAIN restored
    caplog.set_level(logging.DEBUG)
    violations: list[str] = []

    # ── Check 5 vhost configs for server_name + ssl_certificate ──────────────
    for fname in sorted(_VHOST_CONF_FILES):
        fpath = _NGINX_CONFIG_DIR / fname
        if not fpath.exists():
            violations.append(f"{fname}: file not found")
            logger.info("[IMP:9][gate] FAIL: %s not found", fname)
            continue
        text = fpath.read_text()

        # Check server_name contains ${PLATFORM_DOMAIN}
        if "${PLATFORM_DOMAIN}" not in text:
            # Check specifically in server_name lines
            server_name_lines = re.findall(r"server_name\s+([^;]+);", text)
            has_in_server_name = any("${PLATFORM_DOMAIN}" in line for line in server_name_lines)
            if not has_in_server_name:
                violations.append(f"{fname}: server_name does not contain ${{PLATFORM_DOMAIN}}")
                logger.info("[IMP:9][gate] FAIL: %s server_name missing PLATFORM_DOMAIN", fname)

        # Check ssl_certificate contains ${PLATFORM_DOMAIN}
        ssl_cert_lines = re.findall(r"ssl_certificate\s+([^;]+);", text)
        has_in_ssl_cert = any("${PLATFORM_DOMAIN}" in line for line in ssl_cert_lines)
        if not has_in_ssl_cert:
            violations.append(f"{fname}: ssl_certificate does not contain ${{PLATFORM_DOMAIN}}")
            logger.info("[IMP:9][gate] FAIL: %s ssl_certificate missing PLATFORM_DOMAIN", fname)

        if has_in_ssl_cert:
            logger.info("[IMP:8][gate] OK: %s has PLATFORM_DOMAIN in server_name + ssl_certificate", fname)

    # ── Check platform-default.conf at least one occurrence ─────────────────
    pd_path = _NGINX_CONFIG_DIR / "platform-default.conf"
    if pd_path.exists():
        pd_text = pd_path.read_text()
        count = pd_text.count("${PLATFORM_DOMAIN}")
        if count < 1:
            violations.append("platform-default.conf: does not contain ${PLATFORM_DOMAIN}")
            logger.info("[IMP:9][gate] FAIL: platform-default.conf missing PLATFORM_DOMAIN")
        else:
            logger.info("[IMP:8][gate] OK: platform-default.conf contains %d PLATFORM_DOMAIN occurrences", count)
    else:
        violations.append("platform-default.conf: file not found")
        logger.info("[IMP:9][gate] FAIL: platform-default.conf not found")

    assert not violations, f"PLATFORM_DOMAIN placerholder violations ({len(violations)}):\n" + "\n".join(violations)
    logger.info(
        "[IMP:9][gate] PASS: All %d vhost configs + platform-default.conf use PLATFORM_DOMAIN",
        len(_VHOST_CONF_FILES),
    )


# endregion FUNC_test_platform_domain_placeholder_present


# region FUNC_test_base_yml_mounts_templates
@pytest.mark.gate
def test_base_yml_mounts_templates(caplog) -> None:
    """Conf mounts in docker-compose.base.yml target /etc/nginx/templates/*.conf.template.

    ## @purpose — Verify all 7 conf volume mounts (excluding nginx.conf) in base.yml
    ##            use the envsubst-templates convention: target path starts with
    ##            /etc/nginx/templates/ and ends with .conf.template.
    ## @io — ⇥ yaml.safe_load(base.yml) → ◇ iterate volumes → ◇ filter .conf sources → ⊕ assert template path
    ## @complexity — O(V) where V = volume count
    """
    # 🧪 TRAP[TEST] · 2026-07-16 · gate/nginx-domain · envsubst-templates mount convention
    caplog.set_level(logging.DEBUG)
    assert _BASE_YML.exists(), f"base.yml not found: {_BASE_YML}"

    with open(_BASE_YML) as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "nginx" in services, "base.yml has no nginx service"

    volumes: list[str] = services["nginx"].get("volumes", [])
    violations: list[str] = []
    checked: set[str] = set()

    for vol_entry in volumes:
        if not isinstance(vol_entry, str):
            continue

        # Match known template conf filenames in the volume string.
        # Direct split(":") is unsafe because ${NGINX_CONF_DIR:-./config} contains ':'.
        matched_fname = None
        for fname in _TEMPLATE_CONF_FILES:
            if fname in vol_entry:
                matched_fname = fname
                break
        if matched_fname is None:
            continue

        checked.add(matched_fname)

        # Extract target path: find position of matched filename + ":"
        # vol_entry looks like:
        #   .../grafana-vhost.conf:/etc/nginx/templates/grafana-vhost.conf.template:ro
        fname_pos = vol_entry.index(matched_fname) + len(matched_fname)
        # After the filename there should be ':'
        after_fname = vol_entry[fname_pos:]
        colon_idx = after_fname.find(":")
        if colon_idx == -1:
            violations.append(f"{matched_fname}: no colon after filename in '{vol_entry}'")
            continue
        target = after_fname[colon_idx + 1 :]
        # Strip trailing :ro, :rw, :z, :Z options
        for suffix in (":ro", ":rw", ":z", ":Z"):
            if target.endswith(suffix):
                target = target[: -len(suffix)]
                break

        expected_prefix = "/etc/nginx/templates/"
        expected_suffix = ".conf.template"
        if not target.startswith(expected_prefix):
            violations.append(f"{matched_fname}: target '{target}' does not start with '{expected_prefix}'")
            logger.info("[IMP:9][gate] FAIL: %s target prefix mismatch: %s", matched_fname, target)
        if not target.endswith(expected_suffix):
            violations.append(f"{matched_fname}: target '{target}' does not end with '{expected_suffix}'")
            logger.info("[IMP:9][gate] FAIL: %s target suffix mismatch: %s", matched_fname, target)
        if target.startswith(expected_prefix) and target.endswith(expected_suffix):
            logger.info("[IMP:8][gate] OK: %s → %s", matched_fname, target)

    # Verify all expected template files are present
    missing = _TEMPLATE_CONF_FILES - checked
    if missing:
        violations.append(f"Missing template mounts: {', '.join(sorted(missing))}")
        for m in sorted(missing):
            logger.info("[IMP:9][gate] FAIL: %s not mounted as template", m)

    assert not violations, f"Template mount violations ({len(violations)}):\n" + "\n".join(violations)
    logger.info("[IMP:9][gate] PASS: All %d conf mounts use /etc/nginx/templates/*.conf.template", len(checked))


# endregion FUNC_test_base_yml_mounts_templates
