# GREP_SUMMARY: test node.yaml domain extraction python inline script PLATFORM_DOMAIN PLATFORM_PROJECT_DOMAINS
# STRUCTURE: python3 inline script from node-lifecycle.sh --mode update -> parse tests/test_data/node.yaml -> verify extracted domains
# region MODULE_CONTRACT
## @purpose  Verify the Python inline script in node-lifecycle.sh --mode update
##           correctly extracts PLATFORM_DOMAIN, PLATFORM_EMAIL, and PLATFORM_PROJECT_DOMAINS
##           from node.yaml (which has domain, email, and projects[].domain fields).
##           Previously in step_14_deploy_modules() — migrated to --mode update per T19.
## @scope    Executes the same Python code used in node-lifecycle.sh against the test node.yaml
## @invariants
##   - Must extract platform_domain from node.yaml domain field
##   - Must extract email from node.yaml email field
##   - Must extract all project domains from node.yaml projects[].domain
##   - Must handle empty projects list gracefully
## @rationale  Domain extraction is the precondition for project cert issuance.
##             If extraction fails, PLATFORM_PROJECT_DOMAINS stays empty and
##             _issue_project_certs() skips. This test validates the pipeline.
# endregion MODULE_CONTRACT

"""Tests for node.yaml domain extraction — node-lifecycle.sh --mode update inline Python script."""

import logging
import pathlib
import subprocess

import pytest

from tests.conftest import ldd_trajectory

_logger = logging.getLogger(__name__)

_TEST_DATA_DIR = pathlib.Path(__file__).resolve().parent / "test_data"
_NODE_YAML_PATH = _TEST_DATA_DIR / "node.yaml"


@pytest.mark.contract
@ldd_trajectory
def test_node_yaml_domain_extraction(caplog) -> None:
    """Verify Python inline script extracts domain, email, and project domains from node.yaml.

    ## @purpose  The node-lifecycle.sh --mode update step contains a Python inline
    ##           script that parses node.yaml (migrated from step_14_deploy_modules() per T19).
    ##           This test runs that same logic against tests/test_data/node.yaml.
    ## @scenario  python3 inline script → node.yaml → check stdout tokens
    ## @regression  Node.yaml format change breaks domain extraction
    """
    python_script = r"""
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get('domain', '')
email = data.get('email', '')
projects = data.get('projects', [])
project_domains = [p.get('domain', '') for p in projects if isinstance(p, dict) and p.get('domain')]
print(f"platform_domain:{domain}")
print(f"email:{email}")
print(f"project_domains:{' '.join(project_domains)}")
"""

    result = subprocess.run(
        ["python3", "-c", python_script, str(_NODE_YAML_PATH)],
        capture_output=True,
        text=True,
    )

    print("--- STDOUT ---")
    print(result.stdout)
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END ---")

    assert result.returncode == 0, f"Python script failed:\n{result.stderr}"

    stdout_lines = result.stdout.strip().splitlines()
    _logger.critical("[IMP:9][test][node_yaml] Parsed %d stdout lines", len(stdout_lines))

    # Verify platform_domain
    domain_line = [line for line in stdout_lines if line.startswith("platform_domain:")]
    assert domain_line, "Missing platform_domain line in output"
    assert domain_line[0] == "platform_domain:test.example.com", (
        f"Expected 'platform_domain:test.example.com', got '{domain_line[0]}'"
    )
    _logger.critical("[IMP:9][test][node_yaml] platform_domain extracted: %s", domain_line[0])

    # Verify email
    email_line = [line for line in stdout_lines if line.startswith("email:")]
    assert email_line, "Missing email line in output"
    assert email_line[0] == "email:test@example.com", f"Expected 'email:test@example.com', got '{email_line[0]}'"
    _logger.critical("[IMP:9][test][node_yaml] email extracted: %s", email_line[0])

    # Verify project_domains
    project_line = [line for line in stdout_lines if line.startswith("project_domains:")]
    assert project_line, "Missing project_domains line in output"
    project_domains = project_line[0].split(":", 1)[1].strip()
    assert "test-site.example.com" in project_domains, (
        f"Expected 'test-site.example.com' in project_domains: '{project_domains}'"
    )
    _logger.critical(
        "[IMP:9][test][node_yaml] project_domains extracted: %d domains: %s",
        len(project_domains.split()),
        project_domains,
    )

    # Verify the single domain is present
    domains_list = project_domains.split()
    assert len(domains_list) == 1, f"Expected 1 project domain, got {len(domains_list)}: {domains_list}"


@pytest.mark.contract
@ldd_trajectory
def test_node_yaml_no_projects(caplog, tmp_path) -> None:
    """Verify extraction handles node.yaml without projects field gracefully.

    ## @purpose  Not all node yamls have projects; the script must handle
    ##           missing or empty projects field without error.
    ## @scenario  Create minimal node.yaml without projects → script extracts
    ##           domain but project_domains is empty.
    """
    minimal_yaml = tmp_path / "minimal.yaml"
    minimal_yaml.write_text("domain: example.com\nemail: test@example.com\n")

    python_script = r"""
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get('domain', '')
projects = data.get('projects', [])
project_domains = [p.get('domain', '') for p in projects if isinstance(p, dict) and p.get('domain')]
print(f"platform_domain:{domain}")
print(f"project_domains:{' '.join(project_domains)}")
"""

    result = subprocess.run(
        ["python3", "-c", python_script, str(minimal_yaml)],
        capture_output=True,
        text=True,
    )

    print("--- STDOUT ---")
    print(result.stdout)
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END ---")

    _logger.critical("[IMP:9][test][node_yaml] Testing node.yaml without projects field")
    assert result.returncode == 0
    assert "platform_domain:example.com" in result.stdout
    _logger.critical("[IMP:9][test][node_yaml] platform_domain extracted (no projects): example.com")
    assert "project_domains:" in result.stdout
    # project_domains should be empty
    project_part = next(line for line in result.stdout.splitlines() if line.startswith("project_domains:"))
    assert project_part == "project_domains:", f"Expected empty project_domains, got: {project_part}"
    _logger.critical("[IMP:9][test][node_yaml] Empty project_domains confirmed — graceful handling of missing projects")
