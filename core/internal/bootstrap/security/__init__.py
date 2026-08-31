#!/usr/bin/env python3
# GREP_SUMMARY: security-posture package re-export check_* run_all_checks render_report main CheckResult STATUS constants backward-compat
# STRUCTURE: ▶ доменные модули (apt_security/sshd_policy/docker_posture/fs_perms/deploy_channel_posture/run) → ⊕ flat re-export всех публичных символов + констант → ⎋ core.internal.bootstrap.security
# region MODULE_CONTRACT
## @purpose  Пакет core/internal/bootstrap/security (план 170 W6-D1) — декомпозиция монолита
##           security_posture.py (1131 LOC). Единая точка re-export публичных символов для
##           фасада security_posture.py и прямых потребителей: все check_* (S1-S9),
##           run_all_checks/aggregate_exit_code/render_report/main, CheckResult/STATUS_*,
##           apply/dropin-функции sshd, публичные константы.
## @scope    Внутренний интерфейс пакета. Внешний доступ: через фасад core.internal.bootstrap.
##           security_posture (backward-compat) ИЛИ напрямую из пакета.
## @invariants
##   - re-export 1:1 — тела функций НЕ дублируются (только импорты)
##   - parse_ufw_status — из firewall.py (identity: security_posture.parse_ufw_status
##     is firewall.parse_ufw_status — контракт теста test_security_posture.py:236)
##   - Никакой логики в __init__ (диспетчеризация/циклы — в run.py и доменных модулях)
## @rationale Flat re-export-хаб (паттерн пакетов W3-W5 плана 170): публичный surface пакета
##            = поверхность монолита; фасад остаётся тонким (<30 LOC).
## @changes 2026-08-15 | план 170 W6-D1 — создано (извлечение из security_posture.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

from core.internal.bootstrap.firewall import parse_ufw_status

from ._shared import STATUS_FAIL, STATUS_PASS, STATUS_WARN, CheckResult
from .apt_security import (
    APT_CHECK_BIN,
    APT_GET_SIM_CMD,
    AUTO_UPDATES_FILE,
    UNATTENDED_FILE,
    check_pending_security_updates,
    check_unattended_upgrades,
)
from .deploy_channel_posture import (
    AUTHORIZED_KEYS_MODE,
    CI_DEPLOY_AUTHORIZED_KEYS,
    check_forced_command,
)
from .docker_posture import (
    DOCKER_DAEMON_JSON,
    check_docker,
    check_image_freshness,
    check_listening_ports,
)
from .fs_perms import PLATFORM_BASE, check_file_perms
from .run import (
    aggregate_exit_code,
    check_ufw,
    main,
    render_report,
    run_all_checks,
)
from .sshd_policy import (
    SSHD_ALLOW_USERS,
    SSHD_CLIENT_ALIVE_INTERVAL_MIN,
    SSHD_HARDENING_DROPIN,
    SSHD_HARDENING_MACS,
    SSHD_LOGIN_GRACE_TIME_MAX,
    SSHD_MAXSTARTUPS_DROPIN,
    SSHD_MAXSTARTUPS_MIN,
    SSHD_MAXSTARTUPS_STR,
    apply_sshd_dropin,
    check_sshd,
    desired_maxstartups_dropin,
    desired_ssh_hardening_dropin,
)

__all__ = [
    "APT_CHECK_BIN",
    "APT_GET_SIM_CMD",
    "AUTHORIZED_KEYS_MODE",
    "AUTO_UPDATES_FILE",
    "CI_DEPLOY_AUTHORIZED_KEYS",
    "DOCKER_DAEMON_JSON",
    "PLATFORM_BASE",
    "SSHD_ALLOW_USERS",
    "SSHD_CLIENT_ALIVE_INTERVAL_MIN",
    "SSHD_HARDENING_DROPIN",
    "SSHD_HARDENING_MACS",
    "SSHD_LOGIN_GRACE_TIME_MAX",
    "SSHD_MAXSTARTUPS_DROPIN",
    "SSHD_MAXSTARTUPS_MIN",
    "SSHD_MAXSTARTUPS_STR",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_WARN",
    "UNATTENDED_FILE",
    "CheckResult",
    "aggregate_exit_code",
    "apply_sshd_dropin",
    "check_docker",
    "check_file_perms",
    "check_forced_command",
    "check_image_freshness",
    "check_listening_ports",
    "check_pending_security_updates",
    "check_sshd",
    "check_ufw",
    "check_unattended_upgrades",
    "desired_maxstartups_dropin",
    "desired_ssh_hardening_dropin",
    "main",
    "parse_ufw_status",
    "render_report",
    "run_all_checks",
]
