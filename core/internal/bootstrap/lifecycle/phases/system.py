#!/usr/bin/env python3
# GREP_SUMMARY: phases-system, system-bootstrap, user-accounts, platform-setup, node-configuration, converge-services, node-config-update, converge-update, bootstrap-phase, E3
# STRUCTURE: ▶ system-фазы (φ1 φ2 φ3 φ5 φ8.5 φ10 φ13) → ◇ each: pre-check → execute → post-check → ⊕ LDD logs → ⎋ bool/exception
# region MODULE_CONTRACT
## @purpose  System-domain bootstrap phases (DevPlan 119 E3) — φ1 system_bootstrap, φ2 user_accounts,
##           φ3 platform_setup, φ5 node_configuration, φ8.5 converge_services, φ10 node_config_update,
##           φ13 converge_update. Интерфейс (core_dir, node_name, node_yaml) -> bool сохранён.
## @scope    Consumed by lifecycle/phases/__init__.py (агрегатор) → state_machine.py execute_phase.
##           Извлечено из lifecycle/phases.py (DevPlan 119 E3, AUDIT-2 M3).
## @invariants
##   1. Every phase is idempotent — safe to re-run on a provisioned node.
##   2. Non-fatal failures log WARN and return False — do NOT raise.
##   3. Fatal failures raise PlatformFatalError.
##   4. All subprocess calls use helpers_subprocess.run_subprocess() (B4 единый канон).
##   5. No direct state mutation — phases do NOT write state.json.
## @rationale E3: phases.py 1080 LOC → доменные модули (паттерн lifecycle/helpers). system-фазы —
##           системный домен (users/packages/sudoers/cron/converge).
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
## @changes  2026-08-05 · DevPlan 136 W3 — φ1 шаг 5.6: sshd MaxStartups drop-in (security_posture --apply-sshd)
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared import deploy_paths
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)
from core.internal.shared.timeouts import APT_TIMEOUT

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.bootstrap.lifecycle.helpers import users as helpers_users
from core.internal.bootstrap.lifecycle.helpers import validation as helpers_validation
from core.internal.shared import (
    subprocess_io as helpers_subprocess,  # B4: единый канон (копия lifecycle/helpers удалена)
)


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
                timeout=600,
                check=True,
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
            helpers_subprocess.run_subprocess(["bash", docker_script], timeout=300, check=True)
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
                helpers_subprocess.run_subprocess(tor_cmd, non_fatal=True, fatal_rc=(127,), timeout=120)
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
            helpers_subprocess.run_subprocess(["bash", firewall_script], non_fatal=True, fatal_rc=(127,), timeout=120)
            logger.info("[IMP:9][phase:system_bootstrap] Firewall applied")
        except Exception as e:  # noqa: EXC — non-fatal: firewall is best-effort on already-configured nodes
            logger.warning("[IMP:7][phase:system_bootstrap] Firewall setup failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:system_bootstrap] firewall.sh not found at %s — skipping", firewall_script)
        non_fatal_issues = True

    # ── 5.1 Journald Storage=persistent (DevPlan 132 W3, D6) ──
    # journald-скрейп в Loki (126 D-1) требует persistent-storage: volatile-journal
    # не переживает reboot. Non-fatal (best-effort — как firewall/tor).
    try:
        journald_ok = helpers_system.ensure_journald_persistent()
        if journald_ok:
            logger.info("[IMP:9][phase:system_bootstrap] Journald persistent storage configured")
        else:
            logger.warning("[IMP:7][phase:system_bootstrap] Journald persistent setup failed (non-fatal)")
            non_fatal_issues = True
    except Exception as e:  # noqa: EXC — non-fatal: journald config is best-effort
        logger.warning("[IMP:7][phase:system_bootstrap] Journald persistent setup raised (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 5.5 Apply unattended-upgrades security policy (DevPlan 134 L1) ──
    # security_updates.py ensure — идемпотентно (content-match no-op). Non-fatal (best-effort,
    # как firewall/tor): security-политика не должна ронять bootstrap. SECURITY_AUTO_REBOOT=false
    # отключает Automatic-Reboot (04:30) — для нод, где ночной ребут недопустим.
    security_script = os.path.join(core_dir, "internal", "bootstrap", "security_updates.py")
    if os.path.isfile(security_script):
        auto_reboot = os.environ.get("SECURITY_AUTO_REBOOT", "true").lower() == "true"
        try:
            helpers_subprocess.run_subprocess(
                ["python3", security_script, "--auto-reboot", "true" if auto_reboot else "false"],
                non_fatal=True,
                fatal_rc=(127,),
                timeout=APT_TIMEOUT,
            )
            logger.info("[IMP:9][phase:system_bootstrap] Unattended-upgrades security policy applied")
        except Exception as e:  # noqa: EXC — non-fatal: security updates are best-effort
            logger.warning("[IMP:7][phase:system_bootstrap] Security updates setup failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning(
            "[IMP:7][phase:system_bootstrap] security_updates.py not found at %s — skipping", security_script
        )
        non_fatal_issues = True

    # ── 5.6 Apply sshd MaxStartups drop-in (DevPlan 136 W3) ──
    # security_posture.py --apply-sshd — идемпотентно (content-match no-op, reload sshd
    # только при изменении содержимого; sshd -T в S4 читает эффективное значение включая drop-in).
    # sshd_config.d drop-in — НЕ правка основного sshd_config (канон drop-in, переживает
    # apt-обновления sshd_config). Non-fatal (best-effort, как firewall/security_updates):
    # MaxStartups не должен ронять bootstrap; повторный бутстрап = no-op.
    posture_script = os.path.join(core_dir, "internal", "bootstrap", "security_posture.py")
    if os.path.isfile(posture_script):
        try:
            helpers_subprocess.run_subprocess(
                ["python3", posture_script, "--apply-sshd"],
                non_fatal=True,
                fatal_rc=(127,),
                timeout=120,
            )
            logger.info("[IMP:9][phase:system_bootstrap] sshd MaxStartups drop-in applied")
        except Exception as e:  # noqa: EXC — non-fatal: sshd hardening is best-effort
            logger.warning("[IMP:7][phase:system_bootstrap] sshd MaxStartups drop-in failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        # Отсутствие скрипта (тест-окружения/tmp CORE_DIR) — WARN, НЕ non_fatal:
        # MaxStartups drop-in best-effort, фаза не должна уходить в done_with_warnings
        # из-за него (канон φ3 provision-environment.sh: «best-effort, фаза не должна
        # уходить в done_with_warnings из-за него»); на реальной ноде скрипт гарантирован
        # core-доставкой (φ5 verify_core_files), S4-проверка живёт в том же файле.
        # ⚠️ TRAP[DECISION] · 2026-08-05 · MED · missing security_posture.py → WARN (не non_fatal)
        # · Rejected: non_fatal (паттерн security_updates.py шаг 5.5) — ломает committed-фикстуры
        #   test_state_machine.py (happy path «все True» перечисляет скрипты явно, без
        #   security_posture.py) и классифицирует отсутствие в тест-окружении как WARN-фазу
        # · Reason: best-effort hardening; реальная нода всегда имеет скрипт (core-доставка);
        #   повторный бутстрап no-op; при добавлении скрипта в фикстуры поведение идентично
        # · Rev: если security_posture.py станет обязательным прекондишеном φ1 — перейти на non_fatal
        logger.warning(
            "[IMP:7][phase:system_bootstrap] security_posture.py not found at %s — skipping MaxStartups",
            posture_script,
        )

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
            # ⚠️ TRAP[BUG] 2026-08-03 · forced-command без cd/PYTHONPATH — канал мёртв
            # · Symptom: SSH forced-command receive падал «ModuleNotFoundError: No module
            #   named 'core'» — sshd исполняет command с cwd=HOME(ci-deploy), python3 -m
            #   core.internal... не находит core (sys.path[0]='' → cwd, а не /opt/platform).
            # · Fix: cd {base} (канон platform_remote_base) + PYTHONPATH — паттерн
            #   deliver_payload (cd {remote_root} && PYTHONPATH={remote_root} python3 -m ...).
            # · DevPlan 125 T3 (FL20): литерал /opt/platform заменён каноном
            #   shared/deploy_paths.platform_remote_base() — единый источник remote base.
            remote_base = str(deploy_paths.platform_remote_base())
            forced_command = (
                f'command="cd {remote_base} && PYTHONPATH={remote_base} '
                'python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict'
            )
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
    if os.path.isfile(auth_script):
        # Script self-handles missing creds: mirror.gcr.io конфигурируется в любом
        # случае (без auth), docker login — только при наличии кредов. Раньше при
        # пустых DOCKER_HUB_* скрипт пропускался целиком → mirror не настраивался
        # → anonymous rate-limit (429) на первом бутстрапе (φ3 идёт до φ4 secrets).
        try:
            helpers_subprocess.run_subprocess(["python3", auth_script], non_fatal=True, fatal_rc=(127,), timeout=120)
            logger.info("[IMP:9][phase:platform_setup] Docker Hub auth configured")
        except Exception as e:  # noqa: EXC — non-fatal: docker auth is best-effort
            logger.warning("[IMP:7][phase:platform_setup] Docker Hub auth failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        # Отсутствие скрипта (test-окружения/tmp CORE_DIR) — WARN, не non_fatal:
        # инвариант «Docker Hub auth is non-fatal» (rate-limit warning if creds missing).
        logger.warning("[IMP:7][phase:platform_setup] docker_registry_auth.py not found at %s — skipping", auth_script)

    # ── 1.5 Environment provision (networks + volumes) ──
    # φ8 (deploy-modules) вызывается с --skip-provision (комментарий «provision done in
    # platform_setup»), но φ3 provision НЕ выполнял (латентный баг с wave4 a461573):
    # свежий bootstrap падал на external networks (observability-net/backup-net) в φ8.
    # Канон: provision-environment.sh --scope networks/volumes (идемпотентен, non-fatal).
    # Скрипт живёт в core/internal/provision-environment.sh (PATHS_INTERNAL_DIR).
    prov_script = os.path.join(core_dir, "internal", "provision-environment.sh")
    if os.path.isfile(prov_script):
        try:
            for scope in ("networks", "volumes"):
                helpers_subprocess.run_subprocess(
                    ["bash", prov_script, "--scope", scope],
                    non_fatal=True,
                    fatal_rc=(127,),
                    timeout=180,
                )
            logger.info("[IMP:9][phase:platform_setup] Environment provisioned (networks+volumes)")
        except Exception as e:  # noqa: EXC — non-fatal: provision is best-effort
            logger.warning("[IMP:7][phase:platform_setup] Environment provision failed (non-fatal): %s", e)
    else:
        # Отсутствие скрипта (тест-окружения/tmp CORE_DIR) — WARN, НЕ non_fatal:
        # provision best-effort, фаза не должна уходить в done_with_warnings из-за него.
        logger.warning("[IMP:7][phase:platform_setup] provision-environment.sh not found — skipping")

    # ── 2. Setup-node (sudoers generation) ──
    setup_script = os.path.join(core_dir, "internal", "bootstrap", "setup-node.sh")
    if os.path.isfile(setup_script):
        try:
            helpers_subprocess.run_subprocess(["bash", setup_script], non_fatal=True, fatal_rc=(127,), timeout=120)
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

    # ── 2.6 Watchdog cron (DevPlan 132 W1) ──
    # install_cron_watchdog() → /etc/cron.d/platform-watchdog (flock -n + timeout 50s,
    # watchdog.py — host-cron авто-рестарта unhealthy-контейнеров). Non-fatal (тот же паттерн).
    try:
        watchdog_cron_ok = helpers_system.install_cron_watchdog(core_dir)
        if watchdog_cron_ok:
            logger.info("[IMP:9][phase:platform_setup] Watchdog cron installed (cron.d/platform-watchdog)")
        else:
            logger.warning("[IMP:7][phase:platform_setup] Watchdog cron install failed (non-fatal)")
            non_fatal_issues = True
    except Exception as e:  # noqa: EXC — non-fatal: watchdog cron is best-effort
        logger.warning("[IMP:7][phase:platform_setup] Watchdog cron install raised (non-fatal): %s", e)
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
    node_configs_dir = str(deploy_paths.node_configs_remote() / node_name)
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
        helpers_subprocess.run_subprocess(converge_args, non_fatal=True, fatal_rc=(127,), timeout=300)
        logger.info("[IMP:9][phase:converge_services] Converge completed")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:converge_services] Converge failed (non-fatal): %s", e)
        return False

    logger.info("[IMP:9][phase:converge_services] φ8.5 complete — node converged")
    return True


# endregion FUNC_phase_converge_services


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
        helpers_subprocess.run_subprocess(converge_args, non_fatal=True, fatal_rc=(127,), timeout=300)
        logger.info("[IMP:9][phase:converge_update] Converge completed (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:converge_update] Converge failed (non-fatal): %s", e)
        return False

    logger.info("[IMP:9][phase:converge_update] φ13 complete — node converged (update mode)")
    return True


# endregion FUNC_phase_converge_update
