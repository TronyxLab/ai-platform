#!/usr/bin/env python3
# GREP_SUMMARY: preconditions, bootstrap, lifecycle, phase-preconditions, PRECONDITIONS, check-command-exists, shutil.which, DI, facts, env
# STRUCTURE: ▶ PRECONDITIONS registry {phase: handler} → ○ check_phase(phase_value) ┌env/facts DI┐ → ◇ per-phase handler → ◇ _check_command_exists ┌shutil.which┐ → ⎋ None ⚡ PhasePreconditionError
# region MODULE_CONTRACT
## @purpose  Intra-phase precondition checks bootstrap lifecycle (план 170 W5-C1, research-A §4):
##           извлечено из state_store.BootstrapState.precondition_check (139 LOC/CC30/depth17 —
##           монолит в persistence-модуле). Реестр PRECONDITIONS: dict[phase, Callable] +
##           per-фаза функции; _check_command_exists переведён на shutil.which (subprocess → stdlib).
## @scope    Фазы с прекондишенами: φ1 system_bootstrap (root+apt/dpkg), φ2 user_accounts
##           (useradd/id/chown), φ4 secrets_provision (AGE-цепочка), φ6 registry_auth (WARN),
##           φ5 node_configuration (NODE_YAML), φ7 certificates (WARN), φ8/φ12 deploy (скрипт+docker),
##           φ8.5/φ13 converge (WARN). Фазы без прекондишенов (φ3, φ9-φ11) — handler отсутствует.
##           BootstrapState.precondition_check (state_store.py) — ТОНКАЯ ОБЁРТКА над check_phase
##           (контракт state_machine.execute_phase и тестов сохраняется).
## @invariants
##   - Прекондишен-failure BLOCKING: raise PhasePreconditionError → фаза НЕ выполняется
##   - registry — единый контракт handler'а: (phase_value, source, facts, facts_provided, core_dir)
##   - _check_command_exists — shutil.which (stdlib): путь или None; raise-сообщения сохранены 1:1
##   - env= None → os.environ; facts= None → default_env_facts() (поведение по умолчанию неизменно)
##   - facts_provided различает реальный env (euid-детализация в root-сообщении) и DI-facts
##     (тесты получают консистентный "(euid=0)") — E3 (DevPlan 160)
##   - core_dir передаётся из StateMachine (self.core_dir) — единый источник резолюции;
##     fallback: CORE_DIR env → platform_remote_base()/core (TRAP[BUG] 2026-07-31, P1)
##   - docker-проверка (φ8/φ12) — shared/docker_ops.docker_info (W1: docker info примитив,
##     гейт docker_sole_path); НЕ прямой subprocess
## @rationale W5-C1 (research-A §4): precondition_check жил в state_store.py (persistence-модуль)
##            → переезд в phases/preconditions.py (домен прекондишенов). Одновременно разрывает
##            цикл №2 state_store ↔ state_machine (lazy-импорт PhasePreconditionError убран:
##            exceptions.py — общий leaf). _check_command_exists: `command -v` через bash -c
##            (subprocess) → shutil.which — тот же паттерн, что helpers/system.ensure_sops
##            (B-4, DevPlan 136 W9 T9.12); семантика: which возвращает путь или None.
## @changes  2026-08-15 · план 170 W5-C1 — создан (перенос из state_store.py:187-359)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from core.internal.bootstrap.lifecycle.exceptions import PhasePreconditionError
from core.internal.shared import docker_ops  # W1: docker info примитив (гейт docker_sole_path)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts

logger = logging.getLogger(__name__)

# W4 (DevPlan 140): /etc/age/key.txt — restore-first fallback (ручной перенос ключа
# оператором), НЕ канон для φ4. Канон — env-цепочка (AGE_SECRET_KEY/SOPS_AGE_KEY/
# AGE_SECRET_KEY_FILE) → tmpfs decrypt-only (S-13). Константа — для тестируемости.
_ETC_AGE_KEY_FILE = "/etc/age/key.txt"


# region FUNC__check_command_exists
## @purpose  Проверка существования системной команды через shutil.which (stdlib).
## @io       ⇥ cmd: str → ⎋ bool (path найден → True; None → False)
## @complexity O(1)
## @invariants
##   - shutil.which ищет по PATH (эквивалент `command -v`): возвращает путь или None
##   - НЕ бросает исключений (None → False) — вызывающая сторона сама raise'ит
##   - Только проверка существования (не исполняемости на конкретном пользователе)
##   ⚠️ TRAP[BUG] · 2026-07-31 · P1 · `command -v` через прямой exec НИКОГДА не работал
##   · Symptom: precondition_check(φ1 system_bootstrap) падал на ЧИСТОЙ ноде:
##   ·   "Phase system_bootstrap requires 'apt-get' which is not available" —
##   ·   apt-get существует (which apt-get → /usr/bin/apt-get). E2E DevPlan 095 T6.
##   · Root: `command` — bash-встроенная (builtin), НЕ исполняемый файл.
##   ·   subprocess.run(["command", "-v", cmd]) → FileNotFoundError →
##   ·   except → return False: функция возвращала False для ЛЮБОЙ команды.
##   · Fix (2026-07-31): /bin/bash -c "command -v <cmd>" — builtin внутри bash.
##   · Fix (2026-08-15, W5-C1): shutil.which — stdlib-резолвер по PATH, тот же паттерн
##   ·   helpers/system.ensure_sops (B-4, DevPlan 136 W9 T9.12 — `command -v sops` →
##   ·   shutil.which). Семантика which: путь или None; прекондишен-сообщения не изменились.
##   · Prevention: не использовать bash builtins через subprocess.run(list) —
##   ·   builtin обязан вызываться через bash -c; для PATH-резолва — shutil.which.
##   · Source: обнаружено при верификации DevPlan 095 AC4 (cold-start bootstrap).
def _check_command_exists(cmd: str) -> bool:
    """Check if a system command is available via shutil.which (stdlib PATH resolver)."""
    return shutil.which(cmd) is not None


# endregion FUNC__check_command_exists


# region FUNC__precondition_system_bootstrap
## @purpose φ1: root-доступ (FAIL-FAST) + базовые system-инструменты (apt-get/dpkg).
## @io      ⇥ phase_value, source (env), facts, facts_provided, core_dir (unused) → ⎋ None ⚡ PhasePreconditionError
## @complexity O(1) — is_root + 2 which-проверки
## @invariants
##   - root-проверка ПЕРВАЯ (fail-fast): is_root()==False → raise до command-проверок
##   - euid-детализация в сообщении ТОЛЬКО при facts_provided=False (реальный env)
def _precondition_system_bootstrap(
    phase_value: str,
    source: Mapping[str, str],  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт, source не нужен
    facts: EnvironmentFacts,
    facts_provided: bool,
    core_dir: str | None,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт, core_dir не нужен
) -> None:
    """φ1: root + apt-get/dpkg availability (FAIL-FAST root)."""
    if not facts.is_root():
        euid_detail = "" if facts_provided else f", got euid={os.geteuid()}"
        msg = f"Phase {phase_value} (system-bootstrap) requires root access (euid=0){euid_detail}"
        raise PhasePreconditionError(msg)
    # Verify basic system tools
    for cmd in ("apt-get", "dpkg"):
        if not _check_command_exists(cmd):
            msg = f"Phase {phase_value} requires '{cmd}' which is not available"
            raise PhasePreconditionError(msg)


# endregion FUNC__precondition_system_bootstrap


# region FUNC__precondition_user_accounts
## @purpose φ2: user-инструменты (useradd/id/chown) доступны.
## @io      ⇥ phase_value, source (unused), facts (unused), facts_provided (unused), core_dir (unused) → ⎋ None ⚡ PhasePreconditionError
## @complexity O(1) — 3 which-проверки
def _precondition_user_accounts(
    phase_value: str,
    source: Mapping[str, str],  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    facts: EnvironmentFacts,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
) -> None:
    """φ2: verify user management tools available."""
    for cmd in ("useradd", "id", "chown"):
        if not _check_command_exists(cmd):
            msg = f"Phase {phase_value} requires '{cmd}' which is not available"
            raise PhasePreconditionError(msg)


# endregion FUNC__precondition_user_accounts


# region FUNC__precondition_secrets_provision
## @purpose φ4: AGE-ключ доступен (env-цепочка канон → /etc/age/key.txt restore-first fallback).
## @io      ⇥ phase_value, source (AGE_*), facts (path_isfile), facts_provided (unused), core_dir (unused) → ⎋ None ⚡ PhasePreconditionError
## @complexity O(1)
## @invariants
##   - env-цепочка первична (канон, W4 DevPlan 140): AGE_SECRET_KEY / SOPS_AGE_KEY /
##     AGE_SECRET_KEY_FILE — файл-на-диске не требуется (чтение — ответственность
##     node_detect.detect_age_key, отсутствие файла → warning, не блок)
##   - /etc/age/key.txt — ТОЛЬКО restore-first fallback (ручной перенос ключа оператором)
def _precondition_secrets_provision(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
) -> None:
    """φ4: AGE_SECRET_KEY / SOPS_AGE_KEY / AGE_SECRET_KEY_FILE env или /etc/age/key.txt."""
    age_key = (
        source.get("AGE_SECRET_KEY", "") or source.get("SOPS_AGE_KEY", "") or source.get("AGE_SECRET_KEY_FILE", "")
    )
    if not age_key and not facts.path_isfile(_ETC_AGE_KEY_FILE):
        msg = (
            f"Phase {phase_value} requires AGE_SECRET_KEY / SOPS_AGE_KEY / AGE_SECRET_KEY_FILE env "
            f"(canonical) or {_ETC_AGE_KEY_FILE} restore-first fallback (manual) for secret decryption"
        )
        raise PhasePreconditionError(msg)


# endregion FUNC__precondition_secrets_provision


# region FUNC__precondition_registry_auth
## @purpose φ6: GHCR_PULL_TOKEN отсутствует → WARN (Docker Hub rate-limit), НЕ блок.
## @io      ⇥ phase_value, source (GHCR_PULL_TOKEN), facts (unused), facts_provided (unused), core_dir (unused) → ⎋ None
## @complexity O(1)
def _precondition_registry_auth(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
) -> None:
    """φ6: GHCR token optional — warn if missing (rate-limit может применяться)."""
    ghcr_token = source.get("GHCR_PULL_TOKEN", "")
    if not ghcr_token:
        logger.warning(
            "[IMP:7][precondition] Phase %s: GHCR_PULL_TOKEN not set — Docker Hub rate-limit may apply (~100 pulls/6h)",
            phase_value,
        )


# endregion FUNC__precondition_registry_auth


# region FUNC__precondition_node_configuration
## @purpose φ5: NODE_YAML задан и файл существует.
## @io      ⇥ phase_value, source (NODE_YAML), facts (path_isfile), facts_provided (unused), core_dir (unused) → ⎋ None ⚡ PhasePreconditionError
## @complexity O(1)
def _precondition_node_configuration(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
) -> None:
    """φ5: valid NODE_YAML path required."""
    node_yaml = source.get("NODE_YAML", "")
    if not node_yaml or not facts.path_isfile(node_yaml):
        msg = f"Phase {phase_value} requires valid NODE_YAML path: {node_yaml}"
        raise PhasePreconditionError(msg)


# endregion FUNC__precondition_node_configuration


# region FUNC__precondition_certificates
## @purpose φ7: install-acme.sh отсутствует → WARN (acme.sh install может упасть), НЕ блок.
## @io      ⇥ phase_value, core_dir (резолюция скрипта), facts (path_isfile), source (unused), facts_provided (unused) → ⎋ None
## @complexity O(1)
## ⚠️ TRAP[BUG] · 2026-07-31 · P1 · precondition искал core по CORE_DIR env (default /opt/platform)
## · Symptom: φ8 precondition: "deploy-modules.sh at /opt/platform/core/... required" на ноде,
## ·   где core лежит по mirror-пути PLATFORM_ROOT (см. remote-cmd.sh TRAP[BUG] PLATFORM_ROOT).
## ·   CORE_DIR env не экспортируется remote-командой — использовался дефолт /opt/platform.
## · Fix: резолвить core_dir через self.core_dir (единый источник с execute_phase).
## · Prevention: не дублировать резолюцию core_dir в прекондишенах — всегда self.core_dir.
def _precondition_certificates(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,
) -> None:
    """φ7: verify acme.sh or install script available (WARN, не блок)."""
    core_dir = core_dir or source.get("CORE_DIR", str(platform_remote_base() / "core"))
    acme_script = Path(core_dir) / "internal" / "bootstrap" / "install-acme.sh"
    if not facts.path_isfile(acme_script):
        logger.warning(
            "[IMP:7][precondition] Phase %s: install-acme.sh not found at %s — acme.sh installation may fail",
            phase_value,
            acme_script,
        )


# endregion FUNC__precondition_certificates


# region FUNC__precondition_deploy
## @purpose φ8/φ12: deploy-modules.sh существует И Docker daemon running (BLOCKING).
## @io      ⇥ phase_value, core_dir, facts (path_isfile), source (CORE_DIR fallback), facts_provided (unused) → ⎋ None ⚡ PhasePreconditionError
## @complexity O(1) + docker info (subprocess, docker_ops)
## @invariants
##   - deploy-modules.sh missing → PhasePreconditionError (блок)
##   - docker daemon не запущен → PhasePreconditionError (блок)
def _precondition_deploy(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,
) -> None:
    """φ8/φ12: deploy-modules.sh + Docker daemon running."""
    core_dir = core_dir or source.get("CORE_DIR", str(platform_remote_base() / "core"))
    deploy_script = Path(core_dir) / "internal" / "bootstrap" / "deploy-modules.sh"
    if not facts.path_isfile(deploy_script):
        msg = f"Phase {phase_value} requires deploy-modules.sh at {deploy_script}"
        raise PhasePreconditionError(msg)
    # Docker must be running (W1: docker info — shared/docker_ops, DOCKER_CMD_TIMEOUT=10 канон)
    docker_check = docker_ops.docker_info()
    if docker_check.returncode != 0:
        msg = f"Phase {phase_value} requires Docker daemon running: {docker_check.stderr.strip()[:200]}"
        raise PhasePreconditionError(msg)


# endregion FUNC__precondition_deploy


# region FUNC__precondition_converge
## @purpose φ8.5/φ13: converge.sh отсутствует → WARN (converge будет пропущен), НЕ блок.
## @io      ⇥ phase_value, core_dir, facts (path_isfile), source (unused), facts_provided (unused) → ⎋ None
## @complexity O(1)
def _precondition_converge(
    phase_value: str,
    source: Mapping[str, str],
    facts: EnvironmentFacts,
    facts_provided: bool,  # ruff: ignore[ARG001] / pyright: ignore[reportUnusedParameter] — единый registry-контракт — единый registry-контракт
    core_dir: str | None,
) -> None:
    """φ8.5/φ13: converge.sh must exist (WARN, не блок)."""
    core_dir = core_dir or source.get("CORE_DIR", str(platform_remote_base() / "core"))
    converge_script = Path(core_dir) / "internal" / "bootstrap" / "converge.sh"
    if not facts.path_isfile(converge_script):
        logger.warning(
            "[IMP:7][precondition] Phase %s: converge.sh not found at %s — converge will be skipped",
            phase_value,
            converge_script,
        )


# endregion FUNC__precondition_converge


# ── Реестр прекондишенов per-фаза (W5-C1: из if/elif-монолита state_store → dict-диспетчер) ──
# Единый контракт handler'а: (phase_value, source, facts, facts_provided, core_dir) -> None.
# Фазы БЕЗ прекондишенов (φ3 platform_setup, φ9-φ11 update-лёгкие) — handler отсутствует →
# check_phase просто логирует satisfied (pass-ветки удалены, план 170 W2-A1).
PRECONDITIONS: dict[str, Callable[[str, Mapping[str, str], EnvironmentFacts, bool, str | None], None]] = {
    "system_bootstrap": _precondition_system_bootstrap,  # φ1
    "user_accounts": _precondition_user_accounts,  # φ2
    "secrets_provision": _precondition_secrets_provision,  # φ4
    "node_configuration": _precondition_node_configuration,  # φ5
    "registry_auth": _precondition_registry_auth,  # φ6 (WARN-only)
    "certificates": _precondition_certificates,  # φ7 (WARN-only)
    "deploy_services": _precondition_deploy,  # φ8
    "deploy_update": _precondition_deploy,  # φ12
    "converge_services": _precondition_converge,  # φ8.5 (WARN-only)
    "converge_update": _precondition_converge,  # φ13 (WARN-only)
}


# region FUNC_check_phase
## @purpose  Dispatch прекондишен-проверки фазы по PRECONDITIONS-реестру (обёртка
##           BootstrapState.precondition_check делегирует сюда — контракт сохраняется).
## @io       ⇥ phase_value: str, core_dir: str | None = None,
##              env: Mapping | None (DI — NODE_YAML/AGE_*/GHCR_PULL_TOKEN/CORE_DIR, DevPlan 160 E2),
##              facts: EnvironmentFacts | None (DI — is_root/path_isfile, DevPlan 160 E3)
##           → ⎋ None (raises PhasePreconditionError on failure)
## @complexity O(1) — registry lookup + handler
## @invariants
##   - precondition failures BLOCKING — phase will not execute
##   - Error message human-readable for operator action
##   - facts= None → default_env_facts() (поведение неизменно: is_root → os.geteuid,
##     path_isfile → os.path.isfile); root-сообщение сохраняет euid-детализацию ТОЛЬКО
##     когда facts не предоставлен (real env) — тесты получают консистентный "(euid=0)"
def check_phase(
    phase_value: str,
    *,
    core_dir: str | None = None,
    env: Mapping[str, str] | None = None,
    facts: EnvironmentFacts | None = None,
) -> None:
    """Validate preconditions for a given phase value. Raises PhasePreconditionError on failure."""
    source: Mapping[str, str] = os.environ if env is None else env
    facts_provided = facts is not None
    facts = facts or default_env_facts()

    handler = PRECONDITIONS.get(phase_value)
    if handler is not None:
        handler(phase_value, source, facts, facts_provided, core_dir)

    logger.info(
        "[IMP:8][precondition_check] Phase %s preconditions satisfied",
        phase_value,
    )


# endregion FUNC_check_phase
