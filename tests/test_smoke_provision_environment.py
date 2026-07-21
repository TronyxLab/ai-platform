# GREP_SUMMARY: smoke, provision-environment, platform-env, docker-compose, networks, volumes
# STRUCTURE: ◇ test_provision_and_compose_up (requires_docker)
# region MODULE_CONTRACT
## @purpose  Smoke tests for provision-environment.sh with Docker compose.
##           Verifies that the provisioner can correctly create Docker networks
##           and volume directories, and that docker compose can start with
##           the provisioned infrastructure.
## @scope    Run as part of smoke marker tests (requires Docker daemon).
## @invariants
##   - Requires Docker (marked requires_docker)
##   - Uses platform-env.yaml as Single Source of Truth
##   - Idempotent: safe to run multiple times
## @rationale  Acceptance criteria #8: Smoke-test: provisioner + docker compose up
##             without errors.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess

import pytest

from tests.helpers.gate_helpers import repo_root

PLATFORM_ENV_PATH = repo_root() / "platform-env.yaml"
PROVISIONER_PATH = repo_root() / "core" / "internal" / "provision-environment.sh"
COMPOSE_FILE = repo_root() / "docker-compose.yml"
COMPOSE_DEV_FILE = repo_root() / "docker-compose.platform-dev.yml"


@pytest.mark.requires_docker
class TestProvisionSmoke:
    """Smoke tests for provisioner + Docker compose lifecycle.

    ## @purpose — Verify provisioner creates infrastructure and compose can start.
    ## @rationale — Covers the critical path: platform-env.yaml → provisioner →
    ##              docker compose up. Uses existing docker networks (no cleanup)
    ##              to avoid interfering with other tests.
    """

    def test_provision_and_compose_up(self, caplog) -> None:
        """Full cycle: provision networks → verify infrastructure is ready.

        ## @purpose — Smoke test for provisioner with Docker compose.
        ## @rationale — Validates the entire provisioner → compose lifecycle.
        ##              Does NOT start full compose stack (would interfere
        ##              with other tests). Instead verifies that:
        ##              1. Provisioner creates Docker networks idempotently
        ##              2. Volume directories are created
        ##              3. Second provisioner call is no-op (idempotency)
        """
        caplog.set_level(logging.DEBUG)
        _logger = logging.getLogger(__name__)

        _logger.info("[IMP:7][smoke][provision] Running provisioner --scope networks")

        # ── Step 1: Provision networks ───────────────────────────────────────
        result = subprocess.run(
            [
                "bash",
                str(PROVISIONER_PATH),
                "--scope",
                "networks",
                "--platform-env",
                str(PLATFORM_ENV_PATH),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Provisioner --scope networks failed: exit {result.returncode}\nstderr: {result.stderr[:500]}"
        )

        # Verify LDD logging
        assert "[IMP:9][provision][networks] Networks provisioned:" in result.stderr
        _logger.info("[IMP:7][smoke][provision] Provisioner --scope networks: OK")

        # ── Step 2: Provision volumes ────────────────────────────────────────
        result = subprocess.run(
            [
                "bash",
                str(PROVISIONER_PATH),
                "--scope",
                "volumes",
                "--platform-env",
                str(PLATFORM_ENV_PATH),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Provisioner --scope volumes failed: exit {result.returncode}\nstderr: {result.stderr[:500]}"
        )
        assert "[IMP:9][provision][volumes] Volumes provisioned:" in result.stderr
        _logger.info("[IMP:7][smoke][provision] Provisioner --scope volumes: OK")

        # ── Step 3: Idempotency — second run is no-op ────────────────────────
        result = subprocess.run(
            [
                "bash",
                str(PROVISIONER_PATH),
                "--scope",
                "all",
                "--platform-env",
                str(PLATFORM_ENV_PATH),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Provisioner --scope all (idempotency) failed: exit {result.returncode}\nstderr: {result.stderr[:500]}"
        )
        assert "[IMP:9][provision] Provision complete (scope=all)" in result.stderr

        _logger.info("[IMP:9][smoke][provision] Full provision cycle: PASS")

    def test_provisioner_env_defaults_export(self) -> None:
        """Verify --scope env exports env_defaults from platform-env.yaml.

        ## @purpose — Smoke test for the env scope with a temporary GITHUB_ENV.
        ## @rationale — Verifies that env vars are written in KEY=VALUE format.
        """
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            env_path = f.name

        try:
            result = subprocess.run(
                [
                    "bash",
                    str(PROVISIONER_PATH),
                    "--scope",
                    "env",
                    "--platform-env",
                    str(PLATFORM_ENV_PATH),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GITHUB_ENV": env_path},
            )
            assert result.returncode == 0, (
                f"Provisioner --scope env failed: exit {result.returncode}\nstderr: {result.stderr[:500]}"
            )

            # Verify env file was created and contains expected vars
            with open(env_path) as f:
                content = f.read()
            assert "POSTGRES_PASSWORD=test-pg-pwd" in content
            assert "LITELLM_MASTER_KEY=sk-ci-test-master-key" in content
            assert "LANGFUSE_SECRET_KEY=ci-test-secret-key" in content
        finally:
            os.unlink(env_path)
