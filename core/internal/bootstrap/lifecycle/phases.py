#!/usr/bin/env python3
# GREP_SUMMARY: phases, bootstrap-phase, lifecycle, phase-system-bootstrap, phase-user-accounts, phase-platform-setup, phase-secrets-provision, phase-node-configuration, phase-registry-auth, phase-certificates, phase-deploy-services, phase-converge-services, phase-secrets-update, phase-node-config-update, phase-registry-update, phase-deploy-update, phase-converge-update
# STRUCTURE: ▶ 9 init phases (φ1-φ8.5) + 5 update phases (φ9-φ13) → ◇ each phase: pre-check → execute → post-check → ⊕ LDD logs (IMP:7-10) → ⎋ bool/exception
# region MODULE_CONTRACT
## @purpose  14 standalone phase functions extracted from state_machine.py _execute_init_step()
##           and _execute_update_step() dispatch. Each phase is a business-logic grouping of
##           related bootstrap steps. Phase functions encapsulate the "what" — orchestration
##           (checkpoint-resume, state transitions) remains in state_machine.py.
## @scope    Called by node-lifecycle.sh external orchestration (shell → python3 phases.py)
##           or by higher-level lifecycle orchestrators. Each phase accepts unified signature:
##           core_dir, node_name, node_yaml. Phases return True on success, False on non-fatal
##           failure, raise PlatformFatalError on critical failure.
## @invariants
##   1. Every phase is idempotent — safe to re-run on a provisioned node.
##   2. Non-fatal failures log WARN and return False — do NOT raise.
##   3. Fatal failures (missing node.yaml, decrypt failure, root required) raise PlatformFatalError.
##   4. All subprocess calls use helpers_subprocess.run_subprocess() standard wrapper (capture_output, timeout).
##   5. No direct state mutation — phases do NOT write state.json or manage checkpoints.
##   6. Env var access via os.environ — set by shell-фасад or higher-level orchestrator.
##   7. Import helpers from lifecycle/helpers (public names), never duplicate business logic.
##      Односторонняя зависимость state_machine → phases → helpers (цикл устранён, B9 T1).
##   8. φ1 installs Python 3.14 + platform deps via python_deps.py ensure (FATAL — prerequisite
##      for all Python-orchestrated phases). System /usr/bin/python3 (3.12) is never touched.
## @rationale  Single-dispatch _execute_init_step() in state_machine.py grew to 23 init steps
##             + 9 update steps in one monolithic if/elif chain. Extracting grouped phases
##             enables: (a) independent testing of each phase, (b) reordering/composing phases
##             without touching state machine, (c) partial bootstrap (e.g., secrets only),
##             (d) future parallel phase execution.
## @changes  2026-07-30 | T20c — Created from _execute_init_step / _execute_update_step decomposition.
##             Extracted 9 init phases + 5 update phases. All business logic preserved from
##             state_machine._execute_init_step() and _execute_update_step().
##           2026-08-01 | Python 3.14 (deadsnakes) + platform deps wired into φ1 as step 1.5
##             (python_deps.py ensure, FATAL on failure).
## @modulemap
##   INIT  φ1  phase_system_bootstrap   — root check, apt, python3.14+deps, sops, docker, tor, firewall
##   INIT  φ2  phase_user_accounts      — platform/ci-deploy users, SSH keys, projects base
##   INIT  φ3  phase_platform_setup     — docker auth, metrics cron, setup-node
##   INIT  φ4  phase_secrets_provision  — decrypt secrets, ensure-secrets, init
##   INIT  φ5  phase_node_configuration — validate node.yaml, verify core, verify configs
##   INIT  φ6  phase_registry_auth      — ghcr auth (docker auth — ТОЛЬКО φ3, D2)
##   INIT  φ7  phase_certificates       — install acme.sh, ssl provision
##   INIT  φ8  phase_deploy_services    — deploy modules, deploy context
##   INIT  φ8.5 phase_converge_services  — converge
##   UPDATE φ9  phase_secrets_update    — decrypt secrets
##   UPDATE φ10 phase_node_config_update — read node.yaml, verify core
##   UPDATE φ11 phase_registry_update   — ghcr auth, provision, overlays, llm keys, healthcheck
##   UPDATE φ12 phase_deploy_update     — deploy modules, ssl provision, deploy context
##   UPDATE φ13 phase_converge_update   — converge
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
from core.internal.shared import llm_paths
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains
from core.internal.bootstrap.lifecycle.helpers import reporting as helpers_reporting
from core.internal.bootstrap.lifecycle.helpers import secrets as helpers_secrets
from core.internal.bootstrap.lifecycle.helpers import subprocess_io as helpers_subprocess
from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.bootstrap.lifecycle.helpers import users as helpers_users
from core.internal.bootstrap.lifecycle.helpers import validation as helpers_validation

# ═══════════════════════════════════════════════════════════════════════════
# INIT PHASES — 9 phases for full node bootstrap
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_phase_system_bootstrap
## @purpose φ1: System-level bootstrap — root check, apt packages, sops, Docker, Tor, firewall.
##           Corresponds to init steps: ssh_access (1), apt_deps (2), tor_proxy (3),
##           install_docker (4), firewall (9).
## @io      ⇥ core_dir: platform core directory, node_name: node name, node_yaml: path to node.yaml
##          ⎋ bool: True on success, False on non-fatal failure
##          ⚡ raises PlatformFatalError if not running as root or critical subprocess fails
## @complexity O(P) where P = total apt packages + subprocess calls
## @invariants
##   - Root check is FAIL-FAST: raises immediately if euid != 0
##   - Tor installation is CONDITIONAL on TOR_ENABLED env var
##   - Firewall and Tor are non-fatal (best-effort)
##   - Docker and apt installations are FATAL (prerequisites for everything else)
def phase_system_bootstrap(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ1: System bootstrap — root, packages, Docker, Tor, firewall.

    Pre-check: os.geteuid() == 0 (fail-fast).
    Execute: apt → sops → Docker → [Tor] → firewall.
    Post-check: all critical subprocesses completed without exception.
    """
    # ── Pre-check: root ──
    if os.geteuid() != 0:
        raise PlatformFatalError("phase_system_bootstrap must run as root (euid=0)")
    logger.info("[IMP:9][phase:system_bootstrap] Running as root — OK")

    non_fatal_issues = False

    # ── 1. Install apt dependencies ──
    try:
        tor_enabled = os.environ.get("TOR_ENABLED", "false").lower() == "true"
        packages = ["make", "curl", "ufw", "python3-yaml", "python3-jsonschema"]
        if tor_enabled:
            packages.extend(["tor", "privoxy", "obfs4proxy"])
            logger.info("[IMP:8][phase:system_bootstrap] Tor enabled — added tor/privoxy/obfs4proxy packages")
        helpers_system.install_apt_packages(packages)
        logger.info("[IMP:9][phase:system_bootstrap] Apt packages installed: %s", " ".join(packages))
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:system_bootstrap] Apt package installation failed: %s", e)
        raise PlatformFatalError(f"Apt package installation failed: {e}") from e

    # ── 1.5 Install Python 3.14 (deadsnakes PPA) + platform Python dependencies ──
    # python_deps.py ensure is idempotent: skips when marker (hash + python version) matches.
    # FATAL — Python runtime is a prerequisite for all Python-orchestrated phases (φ8 deploy,
    # converge, healthcheck). A bare `python3` must resolve to 3.14 after this step.
    python_deps_script = os.path.join(core_dir, "internal", "bootstrap", "python_deps.py")
    if os.path.isfile(python_deps_script):
        try:
            helpers_subprocess.run_subprocess(
                ["python3", python_deps_script, "ensure", "--core-dir", core_dir],
                "python_deps",
                timeout=600,
            )
            logger.info("[IMP:9][phase:system_bootstrap] Python 3.14 + dependencies installed")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:system_bootstrap] Python deps installation FAILED: %s", e)
            raise PlatformFatalError(f"Python deps installation failed: {e}") from e
    else:
        logger.warning("[IMP:7][phase:system_bootstrap] python_deps.py not found at %s — skipping", python_deps_script)
        non_fatal_issues = True

    # ── 2. Install sops (non-fatal) ──
    try:
        helpers_system.ensure_sops()
        logger.info("[IMP:9][phase:system_bootstrap] SOPS installed/verified")
    except Exception as e:  # noqa: EXC — non-fatal: sops installation is best-effort
        logger.warning("[IMP:7][phase:system_bootstrap] SOPS installation failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Install Docker ──
    docker_script = os.path.join(core_dir, "internal", "bootstrap", "install-docker.sh")
    if os.path.isfile(docker_script):
        try:
            helpers_subprocess.run_subprocess(["bash", docker_script], "install_docker", timeout=300)
            logger.info("[IMP:9][phase:system_bootstrap] Docker installed successfully")
        except PlatformFatalError:
            logger.error("[IMP:10][phase:system_bootstrap] Docker installation failed")
            raise
    else:
        logger.warning("[IMP:7][phase:system_bootstrap] install-docker.sh not found at %s — skipping", docker_script)
        non_fatal_issues = True

    # ── 4. Install Tor (conditional, non-fatal) ──
    if tor_enabled:
        tor_script = os.path.join(core_dir, "internal", "bootstrap", "install-tor-proxy.sh")
        if os.path.exists(tor_script):
            bridges_file = os.environ.get("TOR_BRIDGES_FILE", "")
            skip_verify = os.environ.get("SKIP_TOR_VERIFY", "false").lower() == "true"
            tor_cmd = ["bash", tor_script]
            if bridges_file:
                tor_cmd.extend(["--tor-bridges-file", bridges_file])
            if skip_verify:
                tor_cmd.append("--skip-tor-verify")
            try:
                helpers_subprocess.run_subprocess(tor_cmd, "tor_proxy", non_fatal=True)
                logger.info("[IMP:9][phase:system_bootstrap] Tor proxy installed")
            except Exception as e:  # noqa: EXC — non-fatal: Tor is best-effort
                logger.warning("[IMP:7][phase:system_bootstrap] Tor installation failed (non-fatal): %s", e)
                non_fatal_issues = True
        else:
            logger.warning(
                "[IMP:7][phase:system_bootstrap] install-tor-proxy.sh not found at %s — skipping Tor", tor_script
            )
            non_fatal_issues = True
    else:
        logger.info("[IMP:7][phase:system_bootstrap] Tor disabled — skipping Tor/Privoxy installation")

    # ── 5. Apply firewall ──
    firewall_script = os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")
    if os.path.isfile(firewall_script):
        try:
            helpers_subprocess.run_subprocess(["bash", firewall_script], "firewall", non_fatal=True)
            logger.info("[IMP:9][phase:system_bootstrap] Firewall applied")
        except Exception as e:  # noqa: EXC — non-fatal: firewall is best-effort on already-configured nodes
            logger.warning("[IMP:7][phase:system_bootstrap] Firewall setup failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:system_bootstrap] firewall.sh not found at %s — skipping", firewall_script)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:system_bootstrap] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:system_bootstrap] φ1 complete — all subsystems bootstrapped")
    return True


# endregion FUNC_phase_system_bootstrap


# region FUNC_phase_user_accounts
## @purpose φ2: Create platform and ci-deploy users, add SSH keys, create projects base dir.
##           Corresponds to init steps: create_platform_user (6), create_ci_deploy_user (7),
##           create_projects_base (8).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - PLATFORM_OWNER_KEY is REQUIRED — missing key triggers PlatformFatalError
##   - PLATFORM_CI_DEPLOY_KEY is semi-optional — missing key logs warning
##   - Both users get 'docker' group membership
##   - ci-deploy gets forced-command prefix for orchestrator_cli dispatch
##     (единственный писатель ci-deploy ключа — users.py add_ssh_key, волна 117 D1)
##   - /opt/projects ownership set to ci-deploy after creation
def phase_user_accounts(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ2: User accounts — platform, ci-deploy, SSH keys, projects base.

    Pre-check: PLATFORM_OWNER_KEY env var present.
    Execute: create platform + ci-deploy users → add SSH keys → create projects base.
    Post-check: users exist, keys added, /opt/projects directory created.
    """
    # ── Pre-check: owner key ──
    owner_key = os.environ.get("PLATFORM_OWNER_KEY", "").strip()
    if not owner_key:
        raise PlatformFatalError("PLATFORM_OWNER_KEY is required for phase_user_accounts")
    ci_deploy_key = os.environ.get("PLATFORM_CI_DEPLOY_KEY", "").strip()
    if not ci_deploy_key:
        logger.warning(
            "[IMP:7][phase:user_accounts] PLATFORM_CI_DEPLOY_KEY not set — ci-deploy user will have no deploy key"
        )

    non_fatal_issues = False

    # ── 1. Create platform user + add owner SSH key ──
    try:
        helpers_users.create_user("platform", ["docker"])
        logger.info("[IMP:9][phase:user_accounts] platform user created/verified")
        helpers_users.add_ssh_key("platform", owner_key)
        logger.info("[IMP:9][phase:user_accounts] SSH key added for platform user")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:user_accounts] Failed to create platform user: %s", e)
        raise PlatformFatalError(f"Platform user creation failed: {e}") from e

    # ── 2. Create ci-deploy user + add deploy SSH key ──
    try:
        helpers_users.create_user("ci-deploy", ["docker"])
        logger.info("[IMP:9][phase:user_accounts] ci-deploy user created/verified")
        if ci_deploy_key:
            forced_command = 'command="python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict'
            helpers_users.add_ssh_key("ci-deploy", ci_deploy_key, forced_command_prefix=forced_command)
            logger.info("[IMP:9][phase:user_accounts] SSH key added for ci-deploy user")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:user_accounts] Failed to create ci-deploy user: %s", e)
        raise PlatformFatalError(f"CI deploy user creation failed: {e}") from e

    # ── 3. Create projects base directory ──
    try:
        helpers_users.ensure_projects_base(core_dir, node_name)
        logger.info("[IMP:9][phase:user_accounts] /opt/projects base directory created with ci-deploy ownership")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:user_accounts] Failed to create projects base (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:user_accounts] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:user_accounts] φ2 complete — users and SSH keys configured")
    return True


# endregion FUNC_phase_user_accounts


# region FUNC_phase_platform_setup
## @purpose φ3: Platform-level setup — Docker Hub auth, setup-node (sudoers), metrics cron.
##           Corresponds to init steps: docker_auth (5), sudoers (17).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - Docker Hub auth is non-fatal — rate-limit warning if creds missing
##   - docker_registry_auth.py may be absent (non-fatal)
##   - sudoers setup is via setup-node.sh — non-fatal if script not found
##   - Metrics cron (step 2.5) is NON-FATAL — install_cron_metrics returns False on failure,
##     phase continues (U-03, DevPlan 116 B3 T1)
##   - sudoers validation is non-fatal (permission denied on restricted nodes)
def phase_platform_setup(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ3: Platform setup — Docker auth, setup-node, metrics cron, sudoers.

    Pre-check: core_dir exists.
    Execute: Docker Hub auth → setup-node.sh (sudoers) → install metrics cron → validate sudoers.
    Post-check: sudoers files validated (best-effort).
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. Docker Hub auth (DevPlan 047: step index 5) ──
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")
    auth_script = os.path.join(bootstrap_dir, "docker_registry_auth.py")
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][phase:platform_setup] Docker Hub credentials not set — rate-limit may apply")
    elif os.path.isfile(auth_script):
        try:
            helpers_subprocess.run_subprocess(["python3", auth_script], "docker_registry_auth", non_fatal=True)
            logger.info("[IMP:9][phase:platform_setup] Docker Hub auth configured")
        except Exception as e:  # noqa: EXC — non-fatal: docker auth is best-effort
            logger.warning("[IMP:7][phase:platform_setup] Docker Hub auth failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:platform_setup] docker_registry_auth.py not found at %s — skipping", auth_script)
        non_fatal_issues = True

    # ── 2. Setup-node (sudoers generation) ──
    setup_script = os.path.join(core_dir, "internal", "bootstrap", "setup-node.sh")
    if os.path.isfile(setup_script):
        try:
            helpers_subprocess.run_subprocess(["bash", setup_script], "setup_node", non_fatal=True)
            logger.info("[IMP:9][phase:platform_setup] setup-node.sh executed (sudoers generated)")
        except Exception as e:  # noqa: EXC — non-fatal: sudoers generation is best-effort
            logger.warning("[IMP:7][phase:platform_setup] setup-node.sh failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:platform_setup] setup-node.sh not found at %s — skipping", setup_script)
        non_fatal_issues = True

    # ── 2.5 Metrics cron (DevPlan 116 B3 T1, U-03) ──
    # install_cron_metrics() → /etc/cron.d/platform-metrics (flock -n + timeout 50s).
    # Non-fatal: cron daemon absence or read-only /etc must not block the phase.
    try:
        cron_ok = helpers_system.install_cron_metrics(core_dir)
        if cron_ok:
            logger.info("[IMP:9][phase:platform_setup] Metrics cron installed (cron.d/platform-metrics)")
        else:
            logger.warning("[IMP:7][phase:platform_setup] Metrics cron install failed (non-fatal)")
            non_fatal_issues = True
    except Exception as e:  # noqa: EXC — non-fatal: metrics cron is best-effort
        logger.warning("[IMP:7][phase:platform_setup] Metrics cron install raised (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Validate sudoers (non-fatal if permission denied) ──
    try:
        helpers_validation.validate_sudoers()
        logger.info("[IMP:9][phase:platform_setup] Sudoers validated")
    except (PlatformError, PermissionError) as e:
        logger.warning("[IMP:7][phase:platform_setup] Sudoers validation (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:platform_setup] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:platform_setup] φ3 complete — platform setup ready")
    return True


# endregion FUNC_phase_platform_setup


# region FUNC_phase_secrets_provision
## @purpose φ4: Decrypt and provision secrets — decrypt AGE-encrypted secrets, ensure secrets.env
##           exists, initialize autogen secrets. BLOCKS deploy if it fails.
##           Corresponds to init steps: decrypt_secrets (12), ensure_secrets (13), secrets_init (14).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails — secrets are critical infrastructure
## @complexity O(S) where S = number of secrets in manifest
## @invariants
##   - Decryption failure is FATAL: continuing with CI defaults would deploy placeholder credentials
##   - secrets.env is sourced into os.environ after decryption
##   - Autogen secrets are managed by secrets_manager module
def phase_secrets_provision(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ4: Secrets provisioning — decrypt, ensure, init.

    Pre-check: core_dir exists.
    Execute: decrypt secrets → ensure secrets.env exists → source into environ → init autogen.
    Post-check: secrets.env file present (validated by _ensure_secrets_exist).
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    # ── 1. Decrypt AGE-encrypted secrets (FATAL on failure) ──
    try:
        helpers_secrets.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_provision] Secrets decrypted successfully")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets decryption FAILED — aborting: %s", e)
        raise PlatformFatalError(f"Secrets decryption failed: {e}") from e

    # ── 2. Ensure secrets.env exists + source into environ + generate autogen ──
    try:
        helpers_secrets.ensure_secrets_exist(core_dir)
        logger.info("[IMP:9][phase:secrets_provision] Secrets verified and autogen secrets generated")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets verification failed — aborting: %s", e)
        raise PlatformFatalError(f"Secrets verification failed: {e}") from e

    # ── 3. Secrets init (placeholder — logic migrated to secrets_manager) ──
    logger.info("[IMP:9][phase:secrets_provision] Secrets init complete (managed by secrets_manager)")

    logger.info("[IMP:9][phase:secrets_provision] φ4 complete — secrets provisioned")
    return True


# endregion FUNC_phase_secrets_provision


# region FUNC_phase_node_configuration
## @purpose φ5: Validate node configuration — read/validate node.yaml, verify core delivery,
##           verify node configs existence. All configuration must be valid before deploy.
##           Corresponds to init steps: verify_core (10), verify_node_configs (11),
##           read_node_yaml (15).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises ConfigNotFoundError if node.yaml is missing or core not delivered
## @complexity O(1) + schema validation
## @invariants
##   - Node.yaml MUST exist — critical precondition for all subsequent phases
##   - Core delivery is verified by checking for node-lifecycle.sh marker file
##   - Schema validation is non-fatal (warning only if schema or jsonschema unavailable)
def phase_node_configuration(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ5: Node configuration — validate node.yaml, verify core and configs.

    Pre-check: node.yaml file exists and is accessible.
    Execute: verify core files → verify node configs → validate node.yaml against schema.
    Post-check: all configuration inputs validated.
    """
    non_fatal_issues = False

    # ── 1. Verify core files delivered ──
    try:
        helpers_validation.verify_core_files(core_dir)
        logger.info("[IMP:9][phase:node_configuration] Core files verified")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:node_configuration] Core files verification FAILED: %s", e)
        raise PlatformFatalError(f"Core files verification failed: {e}") from e

    # ── 2. Verify node configs (node.yaml exists) ──
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(
            f"node.yaml not found: {node_yaml}. Ensure node config is delivered to /opt/node-configs/{node_name}/"
        )
    logger.info("[IMP:9][phase:node_configuration] node.yaml present: %s", node_yaml)

    # ── 3. Validate node.yaml against schema ──
    try:
        helpers_validation.validate_node_yaml(node_yaml, core_dir)
        logger.info("[IMP:9][phase:node_configuration] node.yaml validated against schema")
    except Exception as e:  # noqa: EXC — non-fatal: schema validation is best-effort
        logger.warning("[IMP:7][phase:node_configuration] node.yaml schema validation failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 4. Verify node configs directory exists ──
    node_configs_dir = f"/opt/node-configs/{node_name}"
    if not os.path.isdir(node_configs_dir):
        logger.warning("[IMP:7][phase:node_configuration] Node configs directory not found: %s", node_configs_dir)
        non_fatal_issues = True
    else:
        logger.info("[IMP:8][phase:node_configuration] Node configs directory present: %s", node_configs_dir)

    if non_fatal_issues:
        logger.info("[IMP:8][phase:node_configuration] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:node_configuration] φ5 complete — node configuration validated")
    return True


# endregion FUNC_phase_node_configuration


# region FUNC_phase_registry_auth
## @purpose φ6: Container registry authentication — GHCR (GitHub Container Registry) login
##           for image pulls. Docker Hub auth выполняется ТОЛЬКО в φ3 (docker_registry_auth.py,
##           ранний этап до pull) — дубль вызова убран (волна 117 D2).
##           Corresponds to init steps: ghcr_auth (16).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - GHCR auth uses GHCR_PULL_TOKEN env var — skip if not set
##   - GHCR auth is non-fatal (best-effort)
##   - Docker Hub auth НЕ выполняется в φ6 (единственная точка — φ3 phase_platform_setup, D2)
def phase_registry_auth(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ6: Registry auth — GHCR login.

    Pre-check: None (auth is best-effort, no hard precondition).
    Execute: GHCR auth.
    Post-check: registry credentials configured (best-effort).
    """
    non_fatal_issues = False

    # ── 1. GHCR auth ──
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][phase:registry_auth] GHCR_PULL_TOKEN not set — skipping ghcr auth")
    else:
        try:
            helpers_system.ghcr_auth()
            logger.info("[IMP:9][phase:registry_auth] GHCR auth successful")
        except Exception as e:  # noqa: EXC — non-fatal: ghcr auth is best-effort
            logger.warning("[IMP:7][phase:registry_auth] GHCR auth failed (non-fatal): %s", e)
            non_fatal_issues = True

    # ── 2. Docker Hub auth — ТОЛЬКО в φ3 (волна 117 D2) ──
    # docker_registry_auth.py выполняется единственный раз за init в phase_platform_setup (φ3,
    # ранний этап до pull). Повторный вызов здесь удалён — он давал 2-й systemctl restart docker.
    logger.info(
        "[IMP:7][phase:registry_auth] Docker Hub auth handled in φ3 (docker_registry_auth.py) — skipped in φ6 (D2)"
    )

    if non_fatal_issues:
        logger.info("[IMP:8][phase:registry_auth] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:registry_auth] φ6 complete — registry auth configured")
    return True


# endregion FUNC_phase_registry_auth


# region FUNC__install_acme
def _install_acme(core_dir: str) -> bool:
    """Install acme.sh for SSL provisioning (init only). Returns True on success.

    ## @purpose — Install acme.sh and DNS API extensions. Idempotent: skips if installed.
    ##            Moved from steps.py to phases.py per DevPlan 087 AC4 (no _step_* in steps.py).
    ## @io — ⇥ core_dir: platform core directory path → ⎋ bool (True = success)
    ## @complexity — O(1) + subprocess
    ## @invariants — Non-fatal: if install-acme.sh fails, log WARN and return False
    """
    install_script = os.path.join(core_dir, "internal", "bootstrap", "install-acme.sh")
    if not os.path.isfile(install_script):
        logger.warning("[IMP:7][install_acme] install-acme.sh not found at %s — skipping", install_script)
        return False

    logger.info("[IMP:9][install_acme] Installing acme.sh")
    try:
        result = subprocess.run(
            ["bash", install_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][install_acme] acme.sh installed successfully")
            return True
        logger.warning(
            "[IMP:7][install_acme] acme.sh install failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][install_acme] acme.sh install timed out")
        return False
    except FileNotFoundError as e:
        logger.warning("[IMP:7][install_acme] Command not found: %s", e)
        return False


# endregion FUNC__install_acme


# region FUNC_phase_certificates
## @purpose φ7: SSL certificate provisioning — install acme.sh DNS-01 client, then provision
##           certificates for ALL domains (platform + projects) via cert_orchestrator.
##           Corresponds to init steps: install_acme, ssl_provision.
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(D * T) where D = domain count, T = cert issuance timeout
## @invariants
##   - acme.sh installation is non-fatal (best-effort)
##   - SSL provision is handled by _ssl_provision_via_orchestrator (unified cert entrypoint)
##   - All domains from node.yaml are processed (platform + all projects)
def phase_certificates(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ7: Certificates — install acme.sh, provision SSL for all domains.

    Pre-check: node.yaml exists (needed for domain extraction).
    Execute: install acme.sh → SSL provision via cert_orchestrator.
    Post-check: certificates issued (best-effort, cert_orchestrator handles S3 cache).
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot provision certificates")

    non_fatal_issues = False

    # ── 1. Install acme.sh ──
    try:
        acme_ok = _install_acme(core_dir)
        if acme_ok:
            logger.info("[IMP:9][phase:certificates] acme.sh installed/verified")
        else:
            logger.warning("[IMP:7][phase:certificates] acme.sh installation returned non-success")
            non_fatal_issues = True
    except Exception as e:  # noqa: EXC — non-fatal: acme.sh is best-effort
        logger.warning("[IMP:7][phase:certificates] acme.sh installation failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 2. SSL provision via cert_orchestrator ──
    try:
        helpers_domains.ssl_provision_via_orchestrator(core_dir, node_yaml)
        logger.info("[IMP:9][phase:certificates] SSL certificates provisioned for all domains")
    except Exception as e:  # noqa: EXC — non-fatal: SSL provisioning is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:certificates] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:certificates] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:certificates] φ7 complete — certificates provisioned")
    return True


# endregion FUNC_phase_certificates


# region FUNC_phase_deploy_services
## @purpose φ8: Deploy platform services — run deploy-modules.sh for docker/system modules,
##           then deploy context projects via context_deployer. Corresponds to init steps:
##           node_update (19 — partial, deploy-modules), deploy_context (23).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * D) where M = modules, D = deploy operations per module
## @invariants
##   - deploy-modules.sh is FATAL (core service deployment)
##   - deploy_context is non-fatal (projects are best-effort)
##   - deploy-modules.sh is called with --skip-provision (provision done in platform_setup)
def phase_deploy_services(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ8: Deploy services — modules + context projects.

    Pre-check: node.yaml exists, core_dir exists.
    Execute: deploy-modules.sh (docker + system) → deploy context projects.
    Post-check: subprocess exit codes.
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot deploy services")
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. Deploy modules (docker + system) via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--skip-provision"],
                "deploy_modules",
                timeout=300,
            )
            logger.info("[IMP:9][phase:deploy_services] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_services] Module deployment failed: %s", e)
            raise PlatformFatalError(f"Module deployment failed: {e}") from e
    else:
        logger.warning("[IMP:7][phase:deploy_services] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. Deploy context projects ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_services] Context projects deployed")
    except Exception as e:  # noqa: EXC — non-fatal: context deploy is best-effort
        logger.warning("[IMP:7][phase:deploy_services] Context deploy failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_services] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_services] φ8 complete — all services deployed")
    return True


# endregion FUNC_phase_deploy_services


# region FUNC_phase_converge_services
## @purpose φ8.5: Converge node to desired state — run converge.sh with --node flag.
##           Corresponds to init step: converge (20).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess (converge.sh)
## @invariants
##   - Converge is non-fatal — failures are collected as warnings
##   - AUTO_RECONCILE env var enables --reconcile flag for converge.sh
##   - converge.sh must exist at the expected path; missing script is non-fatal
def phase_converge_services(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ8.5: Converge services — desired-state reconciler.

    Pre-check: converge.sh exists (non-fatal if missing).
    Execute: converge.sh --node <node_name> [--reconcile].
    Post-check: converge.sh exit code (0=clean, 1=warnings, 2=errors).
    """
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if not os.path.isfile(converge_script):
        logger.warning("[IMP:7][phase:converge_services] converge.sh not found at %s — skipping", converge_script)
        return False

    converge_args = ["bash", converge_script, "--node", node_name]
    if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
        converge_args.append("--reconcile")
        logger.info("[IMP:8][phase:converge_services] Auto-reconcile enabled")

    try:
        helpers_subprocess.run_subprocess(converge_args, "converge", non_fatal=True, timeout=300)
        logger.info("[IMP:9][phase:converge_services] Converge completed")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:converge_services] Converge failed (non-fatal): %s", e)
        return False

    logger.info("[IMP:9][phase:converge_services] φ8.5 complete — node converged")
    return True


# endregion FUNC_phase_converge_services


# ═══════════════════════════════════════════════════════════════════════════
# UPDATE PHASES — 5 phases for incremental node update
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_phase_secrets_update
## @purpose φ9: Secrets update (UPDATE mode) — decrypt AGE-encrypted secrets.
##           Corresponds to update step: decrypt_secrets (inside verify_core + provision chain).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails
## @complexity O(1) + subprocess
## @invariants
##   - Same FATAL semantics as init mode: decrypt failure blocks deploy
##   - secrets.env is re-sourced after decryption for fresh env vars
def phase_secrets_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ9: Secrets update — decrypt secrets (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: decrypt AGE-encrypted secrets.
    Post-check: secrets.env present after decryption.
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    # ── Decrypt secrets (FATAL on failure) ──
    try:
        helpers_secrets.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_update] Secrets decrypted successfully (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets decryption FAILED — aborting update: %s", e)
        raise PlatformFatalError(f"Secrets decryption failed during update: {e}") from e

    # Re-source secrets into environ (same as init)
    try:
        helpers_secrets.ensure_secrets_exist(core_dir)
        logger.info("[IMP:9][phase:secrets_update] Secrets re-sourced and verified (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets re-source FAILED — aborting update: %s", e)
        raise PlatformFatalError(f"Secrets re-source failed during update: {e}") from e

    logger.info("[IMP:9][phase:secrets_update] φ9 complete — secrets updated")
    return True


# endregion FUNC_phase_secrets_update


# region FUNC_phase_node_config_update
## @purpose φ10: Node config update (UPDATE mode) — verify core delivery, read/validate
##            node.yaml for fresh configuration. Corresponds to update steps: verify_core (1).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises ConfigNotFoundError if core not delivered or node.yaml missing
## @complexity O(1) + schema validation
## @invariants
##   - Core delivery check is FATAL — update cannot proceed without current core
##   - node.yaml is re-validated to catch config drift (changed domains, projects, modules)
def phase_node_config_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ10: Node config update — verify core, validate node.yaml (UPDATE mode).

    Pre-check: node.yaml exists.
    Execute: verify core files → validate node.yaml against schema.
    Post-check: configuration inputs validated for update.
    """
    non_fatal_issues = False

    # ── 1. Verify core delivery (FATAL) ──
    try:
        helpers_validation.verify_core_files(core_dir)
        logger.info("[IMP:9][phase:node_config_update] Core files verified for update")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:node_config_update] Core files verification FAILED: %s", e)
        raise PlatformFatalError(f"Core files verification failed during update: {e}") from e

    # ── 2. Verify node.yaml exists ──
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot update")
    logger.info("[IMP:9][phase:node_config_update] node.yaml present: %s", node_yaml)

    # ── 3. Validate node.yaml against schema ──
    try:
        helpers_validation.validate_node_yaml(node_yaml, core_dir)
        logger.info("[IMP:9][phase:node_config_update] node.yaml validated against schema")
    except Exception as e:  # noqa: EXC — non-fatal: schema validation is best-effort
        logger.warning("[IMP:7][phase:node_config_update] node.yaml schema validation failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:node_config_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:node_config_update] φ10 complete — node config validated")
    return True


# endregion FUNC_phase_node_config_update


# region FUNC_phase_registry_update
## @purpose φ11: Registry update (UPDATE mode) — GHCR auth, provision environment (networks +
##            volumes), deliver nginx overlays, provision LLM keys, run healthchecks.
##            Corresponds to update steps: provision (2), deliver_overlays (2.5/3),
##            provision_llm_keys (6), healthcheck (7).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * R + N) where M = modules in healthcheck, R = retries, N = overlay files
## @invariants
##   - GHCR auth is best-effort (no token = skip)
##   - Environment provision (networks + volumes) is non-fatal (may already exist)
##   - Nginx overlay reload is non-fatal (nginx may not be running)
##   - LLM key provisioning is non-fatal (optional component)
##   - Healthcheck is STANDALONE (skipped if .hc_done_in_deploy marker present)
def phase_registry_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ11: Registry and services update — GHCR auth, provision, overlays, LLM, healthcheck (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: GHCR auth → provision env → deliver overlays → LLM keys → healthcheck.
    Post-check: healthcheck results (best-effort, warnings collected).
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. GHCR auth ──
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if token:
        try:
            helpers_system.ghcr_auth()
            logger.info("[IMP:9][phase:registry_update] GHCR auth successful")
        except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
            logger.warning("[IMP:7][phase:registry_update] GHCR auth failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.info("[IMP:7][phase:registry_update] GHCR_PULL_TOKEN not set — skipping ghcr auth")

    # ── 2. Provision environment (networks + volumes) ──
    provision_script = os.path.join(core_dir, "internal", "provision-environment.sh")
    if os.path.isfile(provision_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", provision_script, "--scope", "networks", "--scope", "volumes"],
                "provision",
                non_fatal=True,
            )
            logger.info("[IMP:9][phase:registry_update] Environment provisioned (networks + volumes)")
        except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
            logger.warning("[IMP:7][phase:registry_update] Environment provision failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning(
            "[IMP:7][phase:registry_update] provision-environment.sh not found at %s — skipping",
            provision_script,
        )
        non_fatal_issues = True

    # ── 3. Deliver nginx overlays ──
    overlay_dir = f"/opt/node-configs/{node_name}/overlays/nginx"
    if os.path.isdir(overlay_dir):
        conf_files = list(Path(overlay_dir).glob("*.conf"))
        if conf_files:
            logger.info(
                "[IMP:8][phase:registry_update] Found %d overlay(s) in %s — reloading nginx",
                len(conf_files),
                overlay_dir,
            )
            try:
                helpers_subprocess.run_subprocess(
                    ["docker", "exec", "nginx", "nginx", "-s", "reload"],
                    "deliver_overlays",
                    non_fatal=True,
                    check_required=False,
                )
                logger.info("[IMP:9][phase:registry_update] Nginx reloaded with overlays")
            except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
                logger.warning("[IMP:7][phase:registry_update] Nginx reload failed (non-fatal): %s", e)
                non_fatal_issues = True
        else:
            logger.info("[IMP:7][phase:registry_update] No .conf files in %s — skipping nginx reload", overlay_dir)
    else:
        logger.info("[IMP:7][phase:registry_update] No overlay directory at %s — skipping", overlay_dir)

    # ── 4. Provision LLM keys (DevPlan 049 Phase 7) ──
    llm_dir = os.path.join(core_dir, "internal", "llm")
    renderer_script = os.path.join(llm_dir, "config_renderer.py")
    config_output = str(llm_paths.litellm_config_path(core_dir))  # C6: единый путь shared/llm_paths
    if os.path.isfile(renderer_script):
        try:
            helpers_subprocess.run_subprocess(
                ["python3", renderer_script, "--output", config_output],
                "render_litellm_config",
                non_fatal=True,
            )
            logger.info("[IMP:9][phase:registry_update] LiteLLM config rendered")

            provision_entrypoint = os.path.join(core_dir, "entrypoints", "provision-llm.sh")
            if os.path.isfile(provision_entrypoint):
                helpers_subprocess.run_subprocess(
                    ["bash", provision_entrypoint],
                    "provision_llm_keys",
                    non_fatal=True,
                )
                logger.info("[IMP:9][phase:registry_update] LLM virtual keys provisioned")
            else:
                logger.info(
                    "[IMP:7][phase:registry_update] provision-llm.sh not found at %s — skipping LLM key provision",
                    provision_entrypoint,
                )
        except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
            logger.warning("[IMP:7][phase:registry_update] LLM key provisioning failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.info("[IMP:7][phase:registry_update] config_renderer.py not found — skipping LLM provision")

    # ── 5. Healthcheck (standalone, skip if already done in deploy) ──
    hc_done_marker = "/var/lib/platform/.bootstrap/.hc_done_in_deploy"
    if os.path.isfile(hc_done_marker):
        logger.info(
            "[IMP:9][phase:registry_update] Healthcheck already done during deploy "
            "(DEPLOY_PARALLEL) — skipping standalone healthcheck"
        )
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(hc_done_marker)
    elif node_yaml and os.path.isfile(node_yaml):
        try:
            helpers_reporting.run_healthchecks(node_yaml)
            logger.info("[IMP:9][phase:registry_update] Healthchecks completed")
        except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
            logger.warning("[IMP:7][phase:registry_update] Healthchecks failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:registry_update] node.yaml not found — skipping healthchecks")
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:registry_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:registry_update] φ11 complete — registry and services updated")
    return True


# endregion FUNC_phase_registry_update


# region FUNC_phase_deploy_update
## @purpose φ12: Deploy update (UPDATE mode) — deploy modules via deploy-modules.sh, provision
##            SSL certificates, deploy context projects incrementally.
##            Corresponds to update steps: ssl_provision (3/4), deploy_modules (4/5),
##            deploy_context (8).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * D + D_cert * T) where M = modules, D = deploy ops, D_cert = domains
## @invariants
##   - deploy-modules.sh is called with --skip-provision (provision done in registry_update)
##   - SSL provision is via cert_orchestrator (unified entrypoint)
##   - Context deploy is incremental (only changed projects)
def phase_deploy_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ12: Deploy update — modules, SSL, context (UPDATE mode).

    Pre-check: node.yaml exists, core_dir exists.
    Execute: deploy-modules.sh → SSL provision → deploy context.
    Post-check: all deployment operations completed.
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot deploy update")
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. Deploy modules via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--skip-provision"],
                "deploy_modules",
                timeout=300,
            )
            logger.info("[IMP:9][phase:deploy_update] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_update] Module deployment failed: %s", e)
            raise PlatformFatalError(f"Module deployment failed during update: {e}") from e
    else:
        logger.warning("[IMP:7][phase:deploy_update] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. SSL provision via cert_orchestrator ──
    try:
        helpers_domains.ssl_provision_via_orchestrator(core_dir, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] SSL certificates provisioned")
    except Exception as e:  # noqa: EXC — non-fatal: SSL is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:deploy_update] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Deploy context projects (incremental) ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] Context projects deployed incrementally")
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:deploy_update] Context deploy failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_update] φ12 complete — services and SSL deployed")
    return True


# endregion FUNC_phase_deploy_update


# region FUNC_phase_converge_update
## @purpose φ13: Converge update (UPDATE mode) — run converge.sh desired-state reconciler.
##            Corresponds to update step: converge (8).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess (converge.sh)
## @invariants
##   - Non-fatal: converge failures are warnings
##   - AUTO_RECONCILE env var enables --reconcile
##   - converge.sh must exist; missing script is non-fatal warning
def phase_converge_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ13: Converge update — desired-state reconciler (UPDATE mode).

    Pre-check: converge.sh exists (non-fatal if missing).
    Execute: converge.sh --node <node_name> [--reconcile].
    Post-check: converge.sh exit code (0=clean, 1=warnings, 2=errors).
    """
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if not os.path.isfile(converge_script):
        logger.warning("[IMP:7][phase:converge_update] converge.sh not found at %s — skipping", converge_script)
        return False

    converge_args = ["bash", converge_script, "--node", node_name]
    if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
        converge_args.append("--reconcile")
        logger.info("[IMP:8][phase:converge_update] Auto-reconcile enabled")

    try:
        helpers_subprocess.run_subprocess(converge_args, "converge", non_fatal=True, timeout=300)
        logger.info("[IMP:9][phase:converge_update] Converge completed (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:converge_update] Converge failed (non-fatal): %s", e)
        return False

    logger.info("[IMP:9][phase:converge_update] φ13 complete — node converged (update mode)")
    return True


# endregion FUNC_phase_converge_update
