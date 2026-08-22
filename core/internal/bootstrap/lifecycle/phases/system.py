#!/usr/bin/env python3
# GREP_SUMMARY: phases-system, system-bootstrap, user-accounts, platform-setup, node-configuration, converge-services, node-config-update, converge-update, bootstrap-phase, E3, DI, runner-param, facts-param, W4d, T2.1, run-converge
# STRUCTURE: ▶ system-фазы (φ1 φ2 φ3 φ5 φ8.5 φ10 φ13) → ◇ each: pre-check → execute → post-check → ⊕ LDD logs → ⎋ bool/exception
#           → ◇ T2.1: близнецы φ8.5/φ13 (converge) и φ5/φ10 (node-config) → общие шаги (_run_converge/_verify_core_files/_ensure_node_yaml/_validate_node_yaml)
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
##   4. All subprocess calls use runner.run() (CommandRunner DI-канал, W4d) — ленивый default
##      default_command_runner() = канон shared/subprocess_io.run_subprocess (B4 единый канон).
##   5. No direct state mutation — phases do NOT write state.json.
##   6. System-факты (root / path-isfile) — через facts: EnvironmentFacts (W4d); os.path.isdir
##      НЕ покрыт протоколом EnvironmentFacts (только isfile) — остаётся прямым вызовом os
##      (φ5 node_configs_dir резолвится через node_configs_remote(env) — path-injection, 167 D5).
##   7. helpers/system.py и helpers/validation.py НЕ переведены на DI (вне скоупа W4d) —
##      их subprocess-вызовы остаются каноном run_subprocess внутри helpers.
##   8. φ8.5/φ13 (converge): rc propagate — rc=0 → True; rc=1 (warnings) / rc=2 (drift) → False
##      (done_with_warnings) — exit 0 ≠ «чисто» (DevPlan 162 W7-2)
## @rationale E3: phases.py 1080 LOC → доменные модули (паттерн lifecycle/helpers). system-фазы —
##           системный домен (users/packages/sudoers/cron/converge).
##           W4d (160 T4.4): runner/facts параметры убирают monkeypatch os/subprocess из тестов фаз
##           (fake-раннер/fake-факты вместо патчей пол-ОС); helpers_users DI переиспользуется
##           без дублирования (W4b: create_user/add_ssh_key/ensure_projects_base уже принимают runner).
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
## @changes  2026-08-05 · DevPlan 136 W3 — φ1 шаг 5.6: sshd MaxStartups drop-in (security_posture --apply-sshd)
## @changes  2026-08-13 · DevPlan 160 W4d — +runner: CommandRunner / +facts: EnvironmentFacts (DI)
## @changes  2026-08-13 · DevPlan 160 E3 — φ2 +env: Mapping (PLATFORM_* ключи через DI-дикт)
## @changes  2026-08-13 · DevPlan 162 — φ1 шаг 1.6 timezone (W7-3), шаг 5.7 zram (W4-1),
##           шаг 5.8 prune cron (W4-4), шаг 5.9 cruft purge (W10-1); φ8.5/φ13 converge rc-пропагация (W7-2)
## @changes  2026-08-14 · DevPlan 167 D5 — φ5 +env: Mapping (NODE_CONFIGS_REMOTE_BASE path-injection)
## @changes  2026-08-22 · T2.1 — близнецы: φ8.5/φ13 → _run_converge (rc-маппинг W7-2);
##           φ5/φ10 → _verify_core_files/_ensure_node_yaml/_validate_node_yaml (общие шаги)
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import cast

# B8 (142 W7): node.yaml — YAML (json.load падал); python3-yaml — платформенная зависимость φ1
import yaml

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared import deploy_paths
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

# W1-A1 (план 170): литералы таймаутов lifecycle-фаз → канон SoT (AMBER-зачистка research-D §D1).
# 600 (python_deps ensure) → DEPLOY_TIMEOUT; 60 (timedatectl) → SYSTEM_CMD_TIMEOUT;
# 300 (install-docker) → DOCKER_APT_TIMEOUT; 300 (install-tor) → APT_TIMEOUT;
# 120 (bash-скрипты фаз) → LIFECYCLE_CMD_TIMEOUT; 300 (converge.sh) → PULL_TIMEOUT.
from core.internal.shared.timeouts import (
    APT_TIMEOUT,
    DEPLOY_TIMEOUT,
    DOCKER_APT_TIMEOUT,
    LIFECYCLE_CMD_TIMEOUT,
    PULL_TIMEOUT,
    SYSTEM_CMD_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
import pathlib

from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.bootstrap.lifecycle.helpers import users as helpers_users
from core.internal.bootstrap.lifecycle.helpers import validation as helpers_validation

# _PROVISION_TIMEOUT=180 — уникальное значение (единственное timeout=180 в коде, research-D §D1):
# provision-environment.sh --scope networks/volumes (создание external-сетей/volumes Docker).
# Значение вне SoT-набора канонических docker/ssh-констант (COMPOSE_UP_TIMEOUT=180 семантически
# про compose up, не про provision) — модульная константа с TRAP.
# 🧐 TRAP[DECISION] · 2026-08-14 · — · _PROVISION_TIMEOUT=180 — уникальное значение provision-домена
# · Rejected: импорт COMPOSE_UP_TIMEOUT (180, SoT) · Reason: семантика иная — provision-environment.sh
# ·   создаёт external-сети/volumes, а не compose up; единственный потребитель 180 в коде — канонизация
# ·   значения в SoT раздула бы реестр ради одного вызова (правило п.12: новые SoT-константы ≥3 повторов)
# · Rev: при появлении второго вызова provision-домена с 180 — канонизировать в shared/timeouts
_PROVISION_TIMEOUT = 180


# region FUNC__log_best_effort
## @purpose  Логирование результата best-effort шага (PLW0717 extraction): info при ok, warning при нет.
## @io       ⇥ ok: bool, ok_msg: str, warn_msg: str → ⎋ bool (проброшенный ok-флаг)
## @complexity O(1)
## @invariants — Не бросает исключений (logging); call-сайт сам выставляет non_fatal_issues
def _log_best_effort(ok: bool, ok_msg: str, warn_msg: str) -> bool:
    """Log info/warning for a best-effort step and return the ok-flag."""
    if ok:
        logger.info(ok_msg)
    else:
        logger.warning(warn_msg)
    return ok


# endregion FUNC__log_best_effort


# region FUNC__install_apt_packages
## @purpose  Apt-установка базовых пакетов φ1 (+tor/privoxy/obfs4proxy при TOR_ENABLED) — PLW0717 extraction.
## @io       ⇥ source: Mapping[str, str] (env) → ⎋ None ⚡ PlatformError/TimeoutExpired (проброс)
## @complexity O(P) — P пакетов apt
def _install_apt_packages(source: Mapping[str, str], hs: object) -> bool:
    """Install base apt packages (tor trio added when TOR_ENABLED); return tor_enabled flag."""
    tor_enabled = source.get("TOR_ENABLED", "false").lower() == "true"
    # 142 W7 (T9): age — CLI для decrypt-проверок chaos T9 (age -d) и операторских
    # операций на ноде; отсутствовал в φ1 → chaos T9 падал «age: command not found».
    packages = ["make", "curl", "ufw", "python3-yaml", "python3-jsonschema", "age"]
    if tor_enabled:
        packages.extend(["tor", "privoxy", "obfs4proxy"])
        logger.info("[IMP:8][phase:system_bootstrap] Tor enabled — added tor/privoxy/obfs4proxy packages")
    hs.install_apt_packages(packages)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
    logger.info("[IMP:9][phase:system_bootstrap] Apt packages installed: %s", " ".join(packages))
    return tor_enabled


# endregion FUNC__install_apt_packages


# region CLASS_IssueCollector
## @purpose  Накопитель non-fatal issue-причин φ1 (план 170 W5-C3): замена булева
##           non_fatal_issues (research-A §4 — фаза теряла причины WARN-шагов).
## @io       ⇥ add(detail: str = "") → ⎋ None; has_issues → ⎋ bool (read-only)
## @complexity O(1)
## @invariants
##   - add() накапливает причины (список); чтение has_issues НЕ мутирует (immutable view)
##   - has_issues=True → фаза возвращает False (done_with_warnings, волна 117 D5)
class IssueCollector:
    """Накопитель non-fatal issue-причин φ1 (immutable на чтении)."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def add(self, detail: str = "") -> None:
        """Зафиксировать non-fatal issue (пустой detail → обобщённая причина)."""
        self._items.append(detail or "best-effort step failed")

    @property
    def has_issues(self) -> bool:
        """True если хотя бы один non-fatal issue зафиксирован (не сбрасывает коллекцию)."""
        return bool(self._items)


# endregion CLASS_IssueCollector


# region FUNC__best_effort
## @purpose  Best-effort helper-call (план 170 W5-C3): конденсирует 6 копий
##           try/_log_best_effort/except в phase_system_bootstrap (journald/zram/prune/cruft/
##           provider/fstab). Единый try/except: run() → False (WARN fail_msg) или raise
##           (OSError/PlatformError, WARN raised_msg) → issues.add(). Успех → INFO ok_msg.
## @io       ⇥ run: Callable[[], bool], ok/fail/raised_msg: str, issues: IssueCollector → ⎋ None
## @complexity O(1) — один вызов + logging
## @invariants — Не бросает исключений (non-fatal контракт); возвращает None
def _best_effort(
    *,
    run: Callable[[], bool],
    ok_msg: str,
    fail_msg: str,
    raised_msg: str,
    issues: IssueCollector,
) -> None:
    """Best-effort helper call — False/raise → non-fatal issue, успех → INFO."""
    try:
        if not _log_best_effort(run(), ok_msg, fail_msg):
            issues.add()
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: best-effort
        logger.warning(raised_msg, e)
        issues.add()


# endregion FUNC__best_effort


# region FUNC__run_best_effort_script
## @purpose  Best-effort bash/python3-скрипт (план 170 W5-C3): конденсирует 5 копий
##           isfile→try→non_fatal в phase_system_bootstrap (tor/firewall/security_updates/
##           posture/reboot_policy). missing → WARN (missing_non_fatal=True → issues.add());
##           raise (OSError/PlatformError) → WARN warn_msg → issues.add(); успех → INFO ok_msg.
## @io       ⇥ runner: CommandRunner, facts: EnvironmentFacts, script: str (isfile-проверка),
##              args: list[str] | None (аргументы скрипта), timeout: int,
##              ok/warn/missing_msg: str, missing_non_fatal: bool, issues: IssueCollector → ⎋ None
## @complexity O(1) — isfile + 1 runner-вызов
## @invariants
##   - Интерпретатор по расширению: .py → python3, иначе bash (факт исходников φ1)
##   - runner.run(non_fatal=True, fatal_rc=(127,)) — канон best-effort скриптов φ1
##   - FATAL-скрипты (python_deps/docker) НЕ проходят через эту функцию (свои функции —
##     _ensure_python_runtime/_install_docker_core)
##   - missing_non_fatal=False (posture/reboot_policy) — WARN без issues (канон TRAP[DECISION] 5.6)
def _run_best_effort_script(
    *,
    runner: CommandRunner,
    facts: EnvironmentFacts,
    script: str,
    args: list[str] | None = None,
    timeout: int,
    ok_msg: str,
    warn_msg: str,
    missing_msg: str,
    missing_non_fatal: bool,
    issues: IssueCollector,
) -> None:
    """Best-effort script run — missing/raise → non-fatal issue, успех → INFO."""
    if not facts.path_isfile(script):
        logger.warning(missing_msg)
        if missing_non_fatal:
            issues.add()
        return
    interpreter = "python3" if script.endswith(".py") else "bash"
    try:
        runner.run([interpreter, script, *(args or [])], non_fatal=True, fatal_rc=(127,), timeout=timeout)
        logger.info(ok_msg)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: best-effort script
        logger.warning(warn_msg, e)
        issues.add()


# endregion FUNC__run_best_effort_script


# region FUNC__ensure_python_runtime
## @purpose  Установка Python 3.14 (deadsnakes PPA) + platform deps (φ1 шаг 1.5, W5-C3) —
##           FATAL (prerequisite для всех Python-orchestrated фаз: φ8 deploy, converge,
##           healthcheck). Голый `python3` обязан резолвиться в 3.14 после этого шага.
## @io       ⇥ core_dir, runner, facts, issues → ⎋ None ⚡ PlatformFatalError
## @complexity O(1) + 1 runner-вызов (DEPLOY_TIMEOUT)
## @invariants
##   - python_deps.py ensure идемпотентен: skip при совпадении маркера (hash + python version)
##   - Отсутствие скрипта (test-окружения/tmp CORE_DIR) → WARN + non-fatal (НЕ raise)
##   - Ошибка выполнения → PlatformFatalError (Python runtime обязателен)
def _ensure_python_runtime(
    core_dir: str,
    runner: CommandRunner,
    facts: EnvironmentFacts,
    issues: IssueCollector,
) -> None:
    """Install Python 3.14 + platform deps (FATAL — prerequisite для Python-фаз)."""
    python_deps_script = os.path.join(core_dir, "internal", "bootstrap", "python_deps.py")
    if not facts.path_isfile(python_deps_script):
        logger.warning("[IMP:7][phase:system_bootstrap] python_deps.py not found at %s — skipping", python_deps_script)
        issues.add()
        return
    try:
        runner.run(
            ["python3", python_deps_script, "ensure", "--core-dir", core_dir],
            timeout=DEPLOY_TIMEOUT,
            check=True,
        )
        logger.info("[IMP:9][phase:system_bootstrap] Python 3.14 + dependencies installed")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:system_bootstrap] Python deps installation FAILED: %s", e)
        msg = f"Python deps installation failed: {e}"
        raise PlatformFatalError(msg) from e


# endregion FUNC__ensure_python_runtime


# region FUNC__install_docker_core
## @purpose  Установка Docker (φ1 шаг 3, W5-C3) — FATAL (prerequisite для всего стека).
## @io       ⇥ core_dir, runner, facts, issues → ⎋ None ⚡ PlatformFatalError (re-raise)
## @complexity O(1) + 1 runner-вызов (DOCKER_APT_TIMEOUT)
## @invariants
##   - Отсутствие install-docker.sh (test-окружения) → WARN + non-fatal (НЕ raise)
##   - PlatformFatalError от runner → re-raise (Docker обязателен для φ8 deploy)
def _install_docker_core(
    core_dir: str,
    runner: CommandRunner,
    facts: EnvironmentFacts,
    issues: IssueCollector,
) -> None:
    """Install Docker (FATAL — prerequisite для всего стека)."""
    docker_script = os.path.join(core_dir, "internal", "bootstrap", "install-docker.sh")
    if not facts.path_isfile(docker_script):
        logger.warning("[IMP:7][phase:system_bootstrap] install-docker.sh not found at %s — skipping", docker_script)
        issues.add()
        return
    try:
        runner.run(["bash", docker_script], timeout=DOCKER_APT_TIMEOUT, check=True)
        logger.info("[IMP:9][phase:system_bootstrap] Docker installed successfully")
    except PlatformFatalError:
        logger.error("[IMP:10][phase:system_bootstrap] Docker installation failed")
        raise


# endregion FUNC__install_docker_core


# region FUNC__apply_timezone
## @purpose  Применение timezone из node.yaml (φ1 шаг 1.6, DevPlan 162 W7-3, W5-C3) —
##           non-fatal (время не роняет bootstrap — канон firewall/tor); unset → INFO skip.
## @io       ⇥ node_yaml: str, runner, issues → ⎋ None
## @complexity O(1) + 1 runner-вызов (SYSTEM_CMD_TIMEOUT)
## @invariants — timezone из node.timezone (schema 162); unset/битый → skip (default UTC)
def _apply_timezone(node_yaml: str, runner: CommandRunner, issues: IssueCollector) -> None:
    """Apply timezone from node.yaml (DevPlan 162 W7-3) — non-fatal."""
    node_tz = _node_timezone(node_yaml)
    if not node_tz:
        logger.info("[IMP:7][phase:system_bootstrap] timezone not set in node.yaml — keeping system default (UTC)")
        return
    try:
        runner.run(
            ["timedatectl", "set-timezone", node_tz],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=SYSTEM_CMD_TIMEOUT,
        )
        logger.info("[IMP:9][phase:system_bootstrap] Timezone set to %s (from node.yaml)", node_tz)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: timezone is best-effort
        logger.warning("[IMP:7][phase:system_bootstrap] Timezone apply failed (non-fatal): %s", e)
        issues.add()


# endregion FUNC__apply_timezone


# region FUNC__install_tor
## @purpose  Установка Tor/Privoxy (φ1 шаг 4, conditional TOR_ENABLED, non-fatal, W5-C3) —
##           сборка args из env (TOR_BRIDGES_FILE/SKIP_TOR_VERIFY) + best-effort run.
## @io       ⇥ core_dir, source (env), runner, facts, issues → ⎋ None
## @complexity O(1) + 1 runner-вызов (APT_TIMEOUT)
## @invariants
##   - Только при tor_enabled (вызывается из if-ветки phase_system_bootstrap)
##   - W4d: os.path.exists → facts.path_isfile (isfile семантически эквивалентен для скрипта)
##   - 141 B7 / 142 B31: APT_TIMEOUT=300 — privoxy listen docker-мостов полагается на
##     завершение write_privoxy_config (B7-корень устранён)
def _install_tor(
    core_dir: str,
    source: Mapping[str, str],
    runner: CommandRunner,
    facts: EnvironmentFacts,
    issues: IssueCollector,
) -> None:
    """Install Tor/Privoxy (conditional, non-fatal) — args из env."""
    tor_script = os.path.join(core_dir, "internal", "bootstrap", "install-tor-proxy.sh")
    bridges_file = source.get("TOR_BRIDGES_FILE", "")
    skip_verify = source.get("SKIP_TOR_VERIFY", "false").lower() == "true"
    tor_args: list[str] = []
    if bridges_file:
        tor_args.extend(["--tor-bridges-file", bridges_file])
    if skip_verify:
        tor_args.append("--skip-tor-verify")
    _run_best_effort_script(
        runner=runner,
        facts=facts,
        script=tor_script,
        args=tor_args,
        timeout=APT_TIMEOUT,
        ok_msg="[IMP:9][phase:system_bootstrap] Tor proxy installed",
        warn_msg="[IMP:7][phase:system_bootstrap] Tor installation failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:system_bootstrap] install-tor-proxy.sh not found at {tor_script} — skipping Tor",
        missing_non_fatal=True,
        issues=issues,
    )


# endregion FUNC__install_tor


# region FUNC_phase_system_bootstrap
## @purpose φ1: System-level bootstrap — root check, apt packages, sops, Docker, Tor, firewall.
##           Corresponds to init steps: ssh_access (1), apt_deps (2), tor_proxy (3),
##           install_docker (4), firewall (9).
## @io      ⇥ core_dir: platform core directory, node_name: node name, node_yaml: path to node.yaml,
##          runner: CommandRunner | None, facts: EnvironmentFacts | None,
##          env: Mapping | None (DI — TOR_ENABLED/TOR_BRIDGES_FILE/SKIP_TOR_VERIFY/SECURITY_AUTO_REBOOT),
##          helpers: object | None (DI, W-H DevPlan 163 — namespace system-хелперов:
##              install_apt_packages/ensure_sops/ensure_journald_persistent/install_zram/
##              install_cron_prune/purge_cruft/purge_provider_repos/ensure_fstab_policy;
##              None = lifecycle.helpers.system канон)
##          ⎋ bool: True on success, False on non-fatal failure
##          ⚡ raises PlatformFatalError if not running as root or critical subprocess fails
## @complexity O(P) where P = total apt packages + subprocess calls
## @invariants
##   - Root check is FAIL-FAST: raises immediately if euid != 0 (facts.is_root())
##   - Tor installation is CONDITIONAL on TOR_ENABLED env var
##   - Firewall and Tor are non-fatal (best-effort)
##   - Docker and apt installations are FATAL (prerequisites for everything else)
##   - DI: helpers= None → канонический модуль (поведение без изменений); тесты передают
##     fake-неймспейс (запись вызовов, scripted True) вместо monkeypatch.setattr helpers (W-H)
def phase_system_bootstrap(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    env: Mapping[str, str] | None = None,
    helpers: object | None = None,
) -> bool:
    """φ1: System bootstrap — root, packages, Docker, Tor, firewall.

    Pre-check: facts.is_root() == True (fail-fast).
    Execute: apt → sops → Docker → [Tor] → firewall (+ python/timezone/best-effort hardening).
    Post-check: critical subprocesses completed; non-fatal issues → False (done_with_warnings).
    """
    runner = runner if runner is not None else default_command_runner()
    facts = facts or default_env_facts()
    # W-H (DevPlan 163): helper-неймспейс DI (тесты без monkeypatch.setattr helpers); None = канон
    hs = helpers_system if helpers is None else helpers
    # W4e (DevPlan 160 E2): env-дикт ключевых переменных (TOR_ENABLED/...); None = os.environ
    source: Mapping[str, str] = os.environ if env is None else env

    # ── Pre-check: root (FAIL-FAST) ──
    if not facts.is_root():
        msg = "phase_system_bootstrap must run as root (euid=0)"
        raise PlatformFatalError(msg)
    logger.info("[IMP:9][phase:system_bootstrap] Running as root — OK")

    issues = IssueCollector()

    # ── 1. Install apt dependencies (FATAL) ──
    try:
        tor_enabled = _install_apt_packages(source, hs)
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:system_bootstrap] Apt package installation failed: %s", e)
        msg = f"Apt package installation failed: {e}"
        raise PlatformFatalError(msg) from e

    # ── 1.5 Python 3.14 + platform deps (FATAL) ──
    _ensure_python_runtime(core_dir, runner, facts, issues)

    # ── 1.6 Apply timezone from node.yaml (DevPlan 162 W7-3, non-fatal) ──
    _apply_timezone(node_yaml, runner, issues)

    # ── 2. Install sops (non-fatal) ──
    try:
        hs.ensure_sops()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
        logger.info("[IMP:9][phase:system_bootstrap] SOPS installed/verified")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: sops installation is best-effort
        logger.warning("[IMP:7][phase:system_bootstrap] SOPS installation failed (non-fatal): %s", e)
        issues.add()

    # ── 3. Install Docker (FATAL) ──
    _install_docker_core(core_dir, runner, facts, issues)

    # ── 4. Install Tor (conditional TOR_ENABLED, non-fatal) ──
    if tor_enabled:
        _install_tor(core_dir, source, runner, facts, issues)
    else:
        logger.info("[IMP:7][phase:system_bootstrap] Tor disabled — skipping Tor/Privoxy installation")

    # ── 5. Apply firewall (non-fatal) ──
    firewall_script = os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")
    _run_best_effort_script(
        runner=runner,
        facts=facts,
        script=firewall_script,
        timeout=LIFECYCLE_CMD_TIMEOUT,
        ok_msg="[IMP:9][phase:system_bootstrap] Firewall applied",
        warn_msg="[IMP:7][phase:system_bootstrap] Firewall setup failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:system_bootstrap] firewall.sh not found at {firewall_script} — skipping",
        missing_non_fatal=True,
        issues=issues,
    )

    # ── 5.1 Journald Storage=persistent (DevPlan 132 W3, D6, non-fatal) ──
    _best_effort(
        run=hs.ensure_journald_persistent,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] Journald persistent storage configured",
        fail_msg="[IMP:7][phase:system_bootstrap] Journald persistent setup failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] Journald persistent setup raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.5 Unattended-upgrades security policy (DevPlan 134 L1, non-fatal) ──
    # 164 W1-3: SECURITY_AUTO_REBOOT default=false — платформенный таймер (5.12) —
    # единственный ребут-канал; unattended-reboot отключён (вариант A).
    security_script = os.path.join(core_dir, "internal", "bootstrap", "security_updates.py")
    auto_reboot = source.get("SECURITY_AUTO_REBOOT", "false").lower() == "true"
    _run_best_effort_script(
        runner=runner,
        facts=facts,
        script=security_script,
        args=["--auto-reboot", "true" if auto_reboot else "false"],
        timeout=APT_TIMEOUT,
        ok_msg="[IMP:9][phase:system_bootstrap] Unattended-upgrades security policy applied",
        warn_msg="[IMP:7][phase:system_bootstrap] Security updates setup failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:system_bootstrap] security_updates.py not found at {security_script} — skipping",
        missing_non_fatal=True,
        issues=issues,
    )

    # ── 5.6 sshd MaxStartups drop-in (DevPlan 136 W3, non-fatal) ──
    # ⚠️ TRAP[DECISION] · 2026-08-05 · MED · missing security_posture.py → WARN (не non_fatal)
    # · Rejected: non_fatal (паттерн security_updates.py шаг 5.5) — ломает committed-фикстуры
    # ·   test_state_machine.py (happy path «все True» без security_posture.py)
    # · Reason: best-effort hardening; реальная нода всегда имеет скрипт (core-доставка)
    # · Rev: если security_posture.py станет обязательным прекондишеном φ1 — перейти на non_fatal
    posture_script = os.path.join(core_dir, "internal", "bootstrap", "security_posture.py")
    _run_best_effort_script(
        runner=runner,
        facts=facts,
        script=posture_script,
        args=["--apply-sshd"],
        timeout=LIFECYCLE_CMD_TIMEOUT,
        ok_msg="[IMP:9][phase:system_bootstrap] sshd MaxStartups drop-in applied",
        warn_msg="[IMP:7][phase:system_bootstrap] sshd MaxStartups drop-in failed (non-fatal): %s",
        missing_msg=(
            f"[IMP:7][phase:system_bootstrap] security_posture.py not found at {posture_script} — skipping MaxStartups"
        ),
        missing_non_fatal=False,
        issues=issues,
    )

    # ── 5.7 zram swap (DevPlan 162 W4-1, non-fatal) ──
    _best_effort(
        run=hs.install_zram,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] zram swap configured",
        fail_msg="[IMP:7][phase:system_bootstrap] zram setup failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] zram setup raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.8 Prune cron (DevPlan 162 W4-4, non-fatal) ──
    _best_effort(
        run=hs.install_cron_prune,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] Prune cron installed (cron.d/platform-prune)",
        fail_msg="[IMP:7][phase:system_bootstrap] Prune cron install failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] Prune cron install raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.9 Cruft purge (DevPlan 162 W10-1, non-fatal) ──
    _best_effort(
        run=hs.purge_cruft,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] Cruft purge completed",
        fail_msg="[IMP:7][phase:system_bootstrap] Cruft purge failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] Cruft purge raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.10 Provider repo purge (DevPlan 164 W0-3.2, non-fatal) ──
    _best_effort(
        run=hs.purge_provider_repos,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] Provider apt repos purged",
        fail_msg="[IMP:7][phase:system_bootstrap] Provider repo purge failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] Provider repo purge raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.11 fstab policy (DevPlan 164 W0-3.4, non-fatal) ──
    _best_effort(
        run=hs.ensure_fstab_policy,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI (object), W-H
        ok_msg="[IMP:9][phase:system_bootstrap] fstab policy applied (defaults + fstrim.timer)",
        fail_msg="[IMP:7][phase:system_bootstrap] fstab policy failed (non-fatal)",
        raised_msg="[IMP:7][phase:system_bootstrap] fstab policy raised (non-fatal): %s",
        issues=issues,
    )

    # ── 5.12 Reboot-policy units (DevPlan 164 W1-3, non-fatal) ──
    # Отсутствие скрипта (тест-окружения) — WARN, НЕ non_fatal (канон security_posture.py 5.6).
    reboot_script = os.path.join(core_dir, "internal", "bootstrap", "reboot_policy.py")
    _run_best_effort_script(
        runner=runner,
        facts=facts,
        script=reboot_script,
        args=["install"],
        timeout=LIFECYCLE_CMD_TIMEOUT,
        ok_msg="[IMP:9][phase:system_bootstrap] Reboot-policy units installed (04:30, Persistent=true)",
        warn_msg="[IMP:7][phase:system_bootstrap] Reboot-policy install failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:system_bootstrap] reboot_policy.py not found at {reboot_script} — skipping install",
        missing_non_fatal=False,
        issues=issues,
    )

    if issues.has_issues:
        logger.info("[IMP:8][phase:system_bootstrap] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:system_bootstrap] φ1 complete — all subsystems bootstrapped")
    return True


# endregion FUNC_phase_system_bootstrap


# region FUNC__node_timezone
## @purpose  Прочитать timezone из node.yaml (schema: node.timezone, default UTC). Pure helper
##           для шага 1.6 (DevPlan 162 W7-3) — тестируем без subprocess.
## @io       ⇥ node_yaml: str — путь к node.yaml (может быть "" в тестах/ранних фазах)
##           ⎋ str — timezone ("Europe/Moscow") или "" (unset/битый/файл отсутствует)
## @complexity O(N) — чтение + yaml.safe_load (N = размер node.yaml)
## @invariants
##   - node_yaml отсутствует/"" → "" (skip — системный default остаётся)
##   - Ключ читается из node.timezone (schema 162: timezone под node:)
##   - Битый YAML/OSError → "" (non-fatal — время не роняет bootstrap)
def _node_timezone(node_yaml: str) -> str:
    """Read timezone from node.yaml (node.timezone). Empty string if unset/unparseable."""
    if not node_yaml or not os.path.isfile(node_yaml):
        return ""
    try:
        with pathlib.Path(node_yaml).open(encoding="utf-8") as f:
            data = cast(
                "dict[str, object]", yaml.safe_load(f) or {}
            )  # W11-G3: yaml.safe_load → Any; YAML-граница node.yaml
        tz = cast("dict[str, object]", data.get("node") or {}).get("timezone") or ""
        return str(tz).strip()
    except (OSError, yaml.YAMLError) as e:
        logger.warning("[IMP:7][phase:system_bootstrap] Cannot read timezone from %s: %s", node_yaml, e)
        return ""


# endregion FUNC__node_timezone


# region FUNC__create_ci_deploy_user
## @purpose  Создание ci-deploy user + forced-command SSH ключ (PLW0717 extraction из phase_user_accounts).
## @io       ⇥ ci_deploy_key: str | None, runner: CommandRunner → ⎋ None ⚡ PlatformError/TimeoutExpired
## @complexity O(1) — 2-3 useradd/ssh-key операции
## @invariants — create_user идемпотентен; forced-command канон (cd+PYTHONPATH, DevPlan 125 T3)
def _create_ci_deploy_user(ci_deploy_key: str | None, runner: CommandRunner, hu: object) -> None:
    """Create ci-deploy user (+platform group) and add forced-command SSH key."""
    # B20b (141 r2): ci-deploy в группу platform — пост-деплой чейн (receive) пишет
    # /opt/platform артефакты root:platform (catalog.json 664, prometheus-targets 2775).
    # create_user идемпотентен: существующий юзер получит группу через usermod -aG.
    hu.create_user("ci-deploy", ["docker", "platform"], runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
    logger.info("[IMP:9][phase:user_accounts] ci-deploy user created/verified (groups: docker, platform)")
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
        hu.add_ssh_key("ci-deploy", ci_deploy_key, forced_command_prefix=forced_command, runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
        logger.info("[IMP:9][phase:user_accounts] SSH key added for ci-deploy user")


# endregion FUNC__create_ci_deploy_user


# region FUNC_phase_user_accounts
## @purpose φ2: Create platform and ci-deploy users, add SSH keys, create projects base dir.
##           Corresponds to init steps: create_platform_user (6), create_ci_deploy_user (7),
##           create_projects_base (8).
## @io      ⇥ core_dir, node_name, node_yaml, runner: CommandRunner | None,
##          env: Mapping | None (DI — PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY),
##          users_helpers: object | None (DI, W-H DevPlan 163 — namespace: create_user/add_ssh_key/
##              ensure_projects_base; None = lifecycle.helpers.users канон)
##          → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - PLATFORM_OWNER_KEY is REQUIRED — missing key triggers PlatformFatalError
##   - PLATFORM_CI_DEPLOY_KEY is semi-optional — missing key logs warning
##   - PLATFORM_CI_ROOT_KEY (142 W1) is semi-optional — missing key logs warning;
##     add_ssh_key("root", key) идемпотентен (duplicate-check, T9.18) — существующие
##     строки не дублируются, owner-root; root authorized_keys вне S7-скоупа (S7 — ci-deploy)
##   - Both users get 'docker' group membership
##   - ci-deploy gets forced-command prefix for orchestrator_cli dispatch
##     (единственный писатель ci-deploy ключа — users.py add_ssh_key, волна 117 D1)
##   - /opt/projects ownership set to ci-deploy after creation
##   - Все user-операции делегируются в helpers_users с runner= (DI переиспользуется из W4b)
##   - env= Mapping (E3, DevPlan 160) — override для PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY/
##     PLATFORM_CI_ROOT_KEY (тесты φ2 без monkeypatch.setenv); None = os.environ (поведение неизменно)
##   - users_helpers= None → канонический модуль; тесты передают fake-неймспейс (W-H)
def phase_user_accounts(
    core_dir: str,
    node_name: str,
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    runner: CommandRunner | None = None,
    env: Mapping[str, str] | None = None,
    users_helpers: object | None = None,
) -> bool:
    """φ2: User accounts — platform, ci-deploy, SSH keys, projects base.

    Pre-check: PLATFORM_OWNER_KEY env var present.
    Execute: create platform + ci-deploy users → add SSH keys → CI-root ключ (root) →
    create projects base.
    Post-check: users exist, keys added, /opt/projects directory created.
    """
    runner = runner if runner is not None else default_command_runner()
    # W-H (DevPlan 163): user-хелперы DI (тесты без monkeypatch.setattr helpers_users); None = канон
    hu = helpers_users if users_helpers is None else users_helpers
    # E3 (DevPlan 160): env-дикт ключей пользователей (DI вместо monkeypatch.setenv); None = os.environ
    source: Mapping[str, str] = os.environ if env is None else env
    # ── Pre-check: owner key ──
    owner_key = source.get("PLATFORM_OWNER_KEY", "").strip()
    if not owner_key:
        msg = "PLATFORM_OWNER_KEY is required for phase_user_accounts"
        raise PlatformFatalError(msg)
    ci_deploy_key = source.get("PLATFORM_CI_DEPLOY_KEY", "").strip()
    if not ci_deploy_key:
        logger.warning(
            "[IMP:7][phase:user_accounts] PLATFORM_CI_DEPLOY_KEY not set — ci-deploy user will have no deploy key"
        )
    # 142 W1 (A1): CI-root ключ (ПУБЛИЧНАЯ часть VPS_SSH_KEY) — root authorized_keys.
    # Раньше добавлялся ВРУЧНУЮ после bootstrap (2 цикла 141) — core-deploy root-канал
    # (core-deploy.yml C-8) не мог войти на свежую ноду. Теперь φ2 сам доставляет ключ.
    ci_root_key = source.get("PLATFORM_CI_ROOT_KEY", "").strip()
    if not ci_root_key:
        logger.warning(
            "[IMP:7][phase:user_accounts] PLATFORM_CI_ROOT_KEY not set — root authorized_keys "
            "без CI-root ключа (core-deploy root-канал недоступен, 142 W1)"
        )

    non_fatal_issues = False

    # ── 1. Create platform user + add owner SSH key ──
    try:
        hu.create_user("platform", ["docker"], runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
        logger.info("[IMP:9][phase:user_accounts] platform user created/verified")
        hu.add_ssh_key("platform", owner_key, runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
        logger.info("[IMP:9][phase:user_accounts] SSH key added for platform user")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:user_accounts] Failed to create platform user: %s", e)
        msg = f"Platform user creation failed: {e}"
        raise PlatformFatalError(msg) from e

    # ── 2. Create ci-deploy user + add deploy SSH key ──
    try:
        _create_ci_deploy_user(ci_deploy_key, runner, hu)
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:user_accounts] Failed to create ci-deploy user: %s", e)
        msg = f"CI deploy user creation failed: {e}"
        raise PlatformFatalError(msg) from e

    # ── 2.5 CI-root ключ в root authorized_keys (142 W1, A1) ──
    # add_ssh_key идемпотентен (duplicate-check по содержимому authorized_keys, T9.18):
    # повторный bootstrap = no-op. Owner-root ключ — владелец файла root:root (домашняя
    # директория root = /root, резолв через home_dir=None → /home/root — НЕ верно для root).
    # ⚠️ TRAP[BUG] · 2026-08-06 · 142 W1 · add_ssh_key("root") требует home_dir="/root"
    # · Symptom: add_ssh_key("root", key) писал бы в /home/root/.ssh (несуществующая
    # ·   директория для root; реальный home = /root) → ключ не попал бы в authorized_keys.
    # · Fix: явный home_dir="/root" для root-пользователя (единственный вызов с override;
    # ·   остальные пользователи резолвят /home/<user>).
    # · Prevention: add_ssh_key вызывает os.makedirs(ssh_dir) — молча создала бы
    # ·   /home/root/.ssh; корень проблемы — passwd-резолв, не хардкод-путь.
    if ci_root_key:
        try:
            hu.add_ssh_key("root", ci_root_key, home_dir="/root", runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
            logger.info("[IMP:9][phase:user_accounts] CI-root SSH key added to /root/.ssh/authorized_keys")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:user_accounts] Failed to add CI-root SSH key: %s", e)
            msg = f"CI-root SSH key setup failed: {e}"
            raise PlatformFatalError(msg) from e

    # ── 3. Create projects base directory ──
    try:
        hu.ensure_projects_base(core_dir, node_name, runner=runner)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
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
## @io      ⇥ core_dir, node_name, node_yaml, runner: CommandRunner | None,
##          facts: EnvironmentFacts | None → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - Docker Hub auth is non-fatal — rate-limit warning if creds missing
##   - docker_registry_auth.py may be absent (non-fatal)
##   - sudoers setup is via setup-node.sh — non-fatal if script not found
##   - Metrics cron (step 2.5) is NON-FATAL — install_cron_metrics returns False on failure,
##     phase continues (U-03, DevPlan 116 B3 T1)
##   - sudoers validation is non-fatal (permission denied on restricted nodes)
##   - DI (W-H DevPlan 163): sys_helpers/val_helpers — namespace-инъекция (None = канонические
##     модули); тесты передают fake-неймспейсы вместо monkeypatch.setattr (0 патчей)
def phase_platform_setup(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    sys_helpers: object | None = None,
    val_helpers: object | None = None,
) -> bool:
    """φ3: Platform setup — Docker auth, setup-node, metrics cron, sudoers.

    Pre-check: core_dir exists.
    Execute: Docker Hub auth → setup-node.sh (sudoers) → install metrics cron → validate sudoers.
    Post-check: sudoers files validated (best-effort).
    """
    runner = runner if runner is not None else default_command_runner()
    facts = facts or default_env_facts()
    # W-H (DevPlan 163): helper-неймспейсы DI (тесты без monkeypatch.setattr); None = канон
    hs = helpers_system if sys_helpers is None else sys_helpers
    hv = helpers_validation if val_helpers is None else val_helpers
    # os.path.isdir НЕ покрыт протоколом EnvironmentFacts (только path_isfile) — оставлен прямым
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    non_fatal_issues = False

    # ── 1. Docker Hub auth (DevPlan 047: step index 5) ──
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")
    auth_script = os.path.join(bootstrap_dir, "docker_registry_auth.py")
    if facts.path_isfile(auth_script):
        # Script self-handles missing creds: daemon.json log-config (json-file rotation)
        # конфигурируется в любом случае, docker login — только при наличии кредов.
        # Раньше при пустых DOCKER_HUB_* скрипт пропускался целиком → лог-конфиг
        # не настраивался. Docker Hub rate-limit покрывается authenticated login.
        try:
            runner.run(["python3", auth_script], non_fatal=True, fatal_rc=(127,), timeout=LIFECYCLE_CMD_TIMEOUT)
            logger.info("[IMP:9][phase:platform_setup] Docker Hub auth configured")
        except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: docker auth is best-effort
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
    if facts.path_isfile(prov_script):
        try:
            for scope in ("networks", "volumes"):
                runner.run(
                    ["bash", prov_script, "--scope", scope],
                    non_fatal=True,
                    fatal_rc=(127,),
                    timeout=_PROVISION_TIMEOUT,
                )
            logger.info("[IMP:9][phase:platform_setup] Environment provisioned (networks+volumes)")
        except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: provision is best-effort
            logger.warning("[IMP:7][phase:platform_setup] Environment provision failed (non-fatal): %s", e)
    else:
        # Отсутствие скрипта (тест-окружения/tmp CORE_DIR) — WARN, НЕ non_fatal:
        # provision best-effort, фаза не должна уходить в done_with_warnings из-за него.
        logger.warning("[IMP:7][phase:platform_setup] provision-environment.sh not found — skipping")

    # ── 2. Setup-node (sudoers generation) ──
    setup_script = os.path.join(core_dir, "internal", "bootstrap", "setup-node.sh")
    if facts.path_isfile(setup_script):
        try:
            runner.run(["bash", setup_script], non_fatal=True, fatal_rc=(127,), timeout=LIFECYCLE_CMD_TIMEOUT)
            logger.info("[IMP:9][phase:platform_setup] setup-node.sh executed (sudoers generated)")
        except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: sudoers generation is best-effort
            logger.warning("[IMP:7][phase:platform_setup] setup-node.sh failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:platform_setup] setup-node.sh not found at %s — skipping", setup_script)
        non_fatal_issues = True

    # ── 2.5 Metrics cron (DevPlan 116 B3 T1, U-03) ──
    # install_cron_metrics() → /etc/cron.d/platform-metrics (flock -n + timeout 50s).
    # Non-fatal: cron daemon absence or read-only /etc must not block the phase.
    try:
        if not _log_best_effort(
            hs.install_cron_metrics(core_dir),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI helpers (object), W-H DevPlan 163
            "[IMP:9][phase:platform_setup] Metrics cron installed (cron.d/platform-metrics)",
            "[IMP:7][phase:platform_setup] Metrics cron install failed (non-fatal)",
        ):
            non_fatal_issues = True
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: metrics cron is best-effort
        logger.warning("[IMP:7][phase:platform_setup] Metrics cron install raised (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 2.6 Watchdog cron (DevPlan 132 W1) ──
    # install_cron_watchdog() → /etc/cron.d/platform-watchdog (flock -n + timeout 50s,
    # watchdog.py — host-cron авто-рестарта unhealthy-контейнеров). Non-fatal (тот же паттерн).
    try:
        if not _log_best_effort(
            hs.install_cron_watchdog(core_dir),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] — namespace-DI helpers (object), W-H DevPlan 163
            "[IMP:9][phase:platform_setup] Watchdog cron installed (cron.d/platform-watchdog)",
            "[IMP:7][phase:platform_setup] Watchdog cron install failed (non-fatal)",
        ):
            non_fatal_issues = True
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: watchdog cron is best-effort
        logger.warning("[IMP:7][phase:platform_setup] Watchdog cron install raised (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Validate sudoers (non-fatal if permission denied) ──
    try:
        hv.validate_sudoers()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — namespace-DI helpers (object), W-H DevPlan 163
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


# region FUNC__verify_core_files
## @purpose  FATAL-обёртка verify_core_files (T2.1): общий шаг φ5/φ10 — сбой core-доставки
##           → PlatformFatalError (fail-fast). Тексты логов per-mode.
## @io       ⇥ core_dir: str, ok_msg: str, err_log: str (ERROR %s), fatal_prefix: str → ⎋ None
##              ⚡ PlatformFatalError
## @complexity O(1) + verify-хелпер
## @invariants — не глотает PlatformError/TimeoutExpired (core обязателен)
def _verify_core_files(
    core_dir: str,
    *,
    ok_msg: str,
    err_log: str,
    fatal_prefix: str,
) -> None:
    """Verify core delivery (FATAL) — shared step φ5/φ10 (T2.1)."""
    try:
        helpers_validation.verify_core_files(core_dir)
        logger.info(ok_msg)
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error(err_log, e)
        msg = f"{fatal_prefix}: {e}"
        raise PlatformFatalError(msg) from e


# endregion FUNC__verify_core_files


# region FUNC__ensure_node_yaml
## @purpose  FATAL-проверка node.yaml (T2.1): общий шаг φ5/φ10 — отсутствие конфига блокирует
##           все последующие фазы → ConfigNotFoundError (fail-fast).
## @io       ⇥ node_yaml: str, facts: EnvironmentFacts, tag: str (тег логов),
##              missing_msg: str (ConfigNotFoundError-сообщение per-mode) → ⎋ None
##              ⚡ ConfigNotFoundError
## @complexity O(1) — isfile-проверка
## @invariants — пустой node_yaml или отсутствующий файл → ConfigNotFoundError
def _ensure_node_yaml(node_yaml: str, facts: EnvironmentFacts, *, tag: str, missing_msg: str) -> None:
    """Ensure node.yaml exists (FATAL) — shared step φ5/φ10 (T2.1)."""
    if not node_yaml or not facts.path_isfile(node_yaml):
        raise ConfigNotFoundError(missing_msg)
    logger.info("[IMP:9][phase:%s] node.yaml present: %s", tag, node_yaml)


# endregion FUNC__ensure_node_yaml


# region FUNC__validate_node_yaml
## @purpose  Non-fatal schema-валидация node.yaml (T2.1): общий шаг φ5/φ10 — сбой WARN + True
##           (done_with_warnings), не роняет фазу.
## @io       ⇥ node_yaml: str, core_dir: str, tag: str → ⎋ bool (True = non-fatal issue)
## @complexity O(N) — N = размер node.yaml (jsonschema)
## @invariants — OSError/PlatformError → WARN + True (schema-валидация best-effort)
def _validate_node_yaml(node_yaml: str, core_dir: str, *, tag: str) -> bool:
    """Validate node.yaml against schema (non-fatal) — shared step φ5/φ10 (T2.1)."""
    try:
        helpers_validation.validate_node_yaml(node_yaml, core_dir)
        logger.info("[IMP:9][phase:%s] node.yaml validated against schema", tag)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: schema validation is best-effort
        logger.warning("[IMP:7][phase:%s] node.yaml schema validation failed (non-fatal): %s", tag, e)
        return True
    else:
        return False


# endregion FUNC__validate_node_yaml


# region FUNC_phase_node_configuration
## @purpose φ5: Validate node configuration — read/validate node.yaml, verify core delivery,
##           verify node configs existence. All configuration must be valid before deploy.
##           Corresponds to init steps: verify_core (10), verify_node_configs (11),
##           read_node_yaml (15). T2.1: общие шаги _verify_core_files/_ensure_node_yaml/
##           _validate_node_yaml (с φ10); φ5-специфичен только node_configs_dir-проверка.
## @io      ⇥ core_dir, node_name, node_yaml, facts: EnvironmentFacts | None,
##          env: Mapping | None (DI, 167 D5 — NODE_CONFIGS_REMOTE_BASE для node_configs_dir;
##              None = os.environ, поведение неизменно) → ⎋ bool
##          ⚡ raises ConfigNotFoundError if node.yaml is missing or core not delivered
## @complexity O(1) + schema validation
## @invariants
##   - Node.yaml MUST exist — critical precondition for all subsequent phases
##   - Core delivery is verified by checking for node-lifecycle.sh marker file
##   - Schema validation is non-fatal (warning only if schema or jsonschema unavailable)
##   - node_configs_dir резолвится через deploy_paths.node_configs_remote(env) — env
##     NODE_CONFIGS_REMOTE_BASE (канон C7) делает путь инъекцируемым (тесты → tmp_path)
def phase_node_configuration(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    *,
    facts: EnvironmentFacts | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """φ5: Node configuration — validate node.yaml, verify core and configs.

    Pre-check: node.yaml file exists and is accessible.
    Execute: verify core files → verify node configs → validate node.yaml against schema.
    Post-check: all configuration inputs validated.
    """
    facts = facts or default_env_facts()
    non_fatal_issues = False

    # ── 1. Verify core files delivered (FATAL) ──
    _verify_core_files(
        core_dir,
        ok_msg="[IMP:9][phase:node_configuration] Core files verified",
        err_log="[IMP:10][phase:node_configuration] Core files verification FAILED: %s",
        fatal_prefix="Core files verification failed",
    )

    # ── 2. Verify node configs (node.yaml exists, FATAL) ──
    _ensure_node_yaml(
        node_yaml,
        facts,
        tag="node_configuration",
        missing_msg=(
            f"node.yaml not found: {node_yaml}. Ensure node config is delivered to /opt/node-configs/{node_name}/"
        ),
    )

    # ── 3. Validate node.yaml against schema (non-fatal) ──
    non_fatal_issues = _validate_node_yaml(node_yaml, core_dir, tag="node_configuration")

    # ── 4. Verify node configs directory exists (φ5-only, non-fatal) ──
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · node_configs_dir path-injection через env
    # · Rejected: прямой os.path.isdir(node_configs_remote()) с monkeypatch-патчем в flow-тестах
    # · Reason: seam = тестируемость реального вызова — node_configs_remote(env) уже канон C7
    # ·   (NODE_CONFIGS_REMOTE_BASE); тест передаёт tmp-путь и создаёт реальную папку (0 патчей)
    # · Rev: если node_configs_dir перестанет зависеть от env-базы — вернуть прямой вызов
    node_configs_dir = str(deploy_paths.node_configs_remote(dict(env) if env is not None else None) / node_name)
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


# region FUNC__run_converge
## @purpose  Общий converge-шаг (T2.1): близнецы φ8.5/φ13 различались ТОЛЬКО phase-тегом и
##           2 суффиксами логов — тело консолидировано (одна точка правки rc-маппинга W7-2).
## @io       ⇥ core_dir, node_name, runner: CommandRunner, facts: EnvironmentFacts,
##              update_mode: bool (False = φ8.5, True = φ13) → ⎋ bool
## @complexity O(1) + subprocess (converge.sh, PULL_TIMEOUT)
## @invariants
##   - rc-пропагация (DevPlan 162 W7-2): rc=0 → True; rc=1/2+ → False (done_with_warnings)
##   - AUTO_RECONCILE env → --reconcile; missing converge.sh → non-fatal False
##   - Логи байт-идентичны прежним per-phase (тесты пинят подстроки, не зависящие от тега)
def _run_converge(
    core_dir: str,
    node_name: str,
    *,
    runner: CommandRunner,
    facts: EnvironmentFacts,
    update_mode: bool = False,
) -> bool:
    """Run converge.sh desired-state reconciler — shared by φ8.5/φ13 (T2.1)."""
    tag = "converge_update" if update_mode else "converge_services"
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if not facts.path_isfile(converge_script):
        logger.warning("[IMP:7][phase:%s] converge.sh not found at %s — skipping", tag, converge_script)
        return False

    converge_args = ["bash", converge_script, "--node", node_name]
    if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
        converge_args.append("--reconcile")
        logger.info("[IMP:8][phase:%s] Auto-reconcile enabled", tag)

    try:
        # W7-2 (DevPlan 162): rc propagate — 0=clean, 1=warnings, 2=drift. НЕ глотать rc
        # (раньше run_subprocess с non_fatal=True возвращал CompletedProcess, но rc игнорировался).
        result = runner.run(converge_args, non_fatal=True, fatal_rc=(127,), timeout=PULL_TIMEOUT)
        completed_fmt = (
            "[IMP:9][phase:converge_update] Converge completed (update, rc=%d)"
            if update_mode
            else "[IMP:9][phase:converge_services] Converge completed (rc=%d)"
        )
        logger.info(completed_fmt, result.returncode)
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][phase:%s] Converge failed (non-fatal): %s", tag, e)
        return False

    if result.returncode == 0:
        rc0_fmt = (
            "[IMP:9][phase:converge_update] Converge clean (rc=0) — update mode"
            if update_mode
            else "[IMP:9][phase:converge_services] Converge clean (rc=0)"
        )
        logger.info(rc0_fmt)
        return True
    if result.returncode == 1:
        logger.warning("[IMP:8][phase:%s] Converge completed with warnings (rc=1)", tag)
        return False
    # rc=2 (drift) или любой другой ненулевой rc — FAIL
    logger.error("[IMP:10][phase:%s] Converge FAILED (rc=%d — drift/errors)", tag, result.returncode)
    return False


# endregion FUNC__run_converge


# region FUNC_phase_converge_services
## @purpose φ8.5: Converge node to desired state (init: converge step 20) — тонкая обёртка
##           над общим _run_converge (T2.1; логика/rc-маппинг — там).
## @io      ⇥ core_dir, node_name, node_yaml, runner: CommandRunner | None,
##          facts: EnvironmentFacts | None → ⎋ bool
## @complexity O(1) + subprocess (converge.sh)
## @invariants — см. _run_converge: non-fatal, rc-пропагация W7-2, AUTO_RECONCILE, missing non-fatal
def phase_converge_services(
    core_dir: str,
    node_name: str,
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """φ8.5: Converge services — desired-state reconciler (T2.1: делегирует _run_converge)."""
    runner = runner if runner is not None else default_command_runner()
    facts = facts or default_env_facts()
    return _run_converge(core_dir, node_name, runner=runner, facts=facts)


# endregion FUNC_phase_converge_services


# region FUNC_phase_node_config_update
## @purpose φ10: Node config update (UPDATE mode) — verify core delivery, read/validate
##            node.yaml for fresh configuration. Corresponds to update steps: verify_core (1).
##            T2.1: близнец φ5 — общие шаги _verify_core_files/_ensure_node_yaml/
##            _validate_node_yaml; φ10 не имеет node_configs_dir-проверки (φ5-only).
## @io      ⇥ core_dir, node_name, node_yaml, facts: EnvironmentFacts | None → ⎋ bool
##          ⚡ raises ConfigNotFoundError if core not delivered or node.yaml missing
## @complexity O(1) + schema validation
## @invariants
##   - Core delivery check is FATAL — update cannot proceed without current core
##   - node.yaml is re-validated to catch config drift (changed domains, projects, modules)
def phase_node_config_update(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,
    *,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """φ10: Node config update — verify core, validate node.yaml (UPDATE mode).

    Pre-check: node.yaml exists.
    Execute: verify core files → validate node.yaml against schema.
    Post-check: configuration inputs validated for update.
    """
    facts = facts or default_env_facts()
    non_fatal_issues = False

    # ── 1. Verify core delivery (FATAL) ──
    _verify_core_files(
        core_dir,
        ok_msg="[IMP:9][phase:node_config_update] Core files verified for update",
        err_log="[IMP:10][phase:node_config_update] Core files verification FAILED: %s",
        fatal_prefix="Core files verification failed during update",
    )

    # ── 2. Verify node.yaml exists (FATAL) ──
    _ensure_node_yaml(
        node_yaml,
        facts,
        tag="node_config_update",
        missing_msg=f"node.yaml not found: {node_yaml} — cannot update",
    )

    # ── 3. Validate node.yaml against schema (non-fatal) ──
    non_fatal_issues = _validate_node_yaml(node_yaml, core_dir, tag="node_config_update")

    if non_fatal_issues:
        logger.info("[IMP:8][phase:node_config_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:node_config_update] φ10 complete — node config validated")
    return True


# endregion FUNC_phase_node_config_update


# region FUNC_phase_converge_update
## @purpose φ13: Converge update (UPDATE mode: converge step 8) — тонкая обёртка над общим
##           _run_converge (T2.1; логика/rc-маппинг — там).
## @io      ⇥ core_dir, node_name, node_yaml, runner: CommandRunner | None,
##          facts: EnvironmentFacts | None → ⎋ bool
## @complexity O(1) + subprocess (converge.sh)
## @invariants — см. _run_converge: non-fatal, rc-пропагация W7-2, AUTO_RECONCILE, missing non-fatal
def phase_converge_update(
    core_dir: str,
    node_name: str,
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """φ13: Converge update — desired-state reconciler (T2.1: делегирует _run_converge, update_mode)."""
    runner = runner if runner is not None else default_command_runner()
    facts = facts or default_env_facts()
    return _run_converge(core_dir, node_name, runner=runner, facts=facts, update_mode=True)


# endregion FUNC_phase_converge_update
