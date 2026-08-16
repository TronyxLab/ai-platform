#!/usr/bin/env bash
# GREP_SUMMARY: ssh, facade, timeout, ssh-exec, ssh-read, ssh-opts, remote-cmd
# STRUCTURE: ▶ ┌SSH_OPTS_COMMON readonly const┐ → ○ ssh_exec() → ⚡ validate() → ⚡ timeout "''${timeout}" ssh "''${SSH_OPTS_COMMON[@]}" → ◇ exit=124:return 124 | exit=0:return 0 | return rc → ○ ssh_read() → ssh_exec timeout=60 → ⎋ exit
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — SSH Facade Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @purpose  Единая SSH-фасадная функция с timeout-wrapper — single source of truth
##           для всех remote-операций платформы. Заменяет 6+ разбросанных inline-ssh
##           конструкций единым контрактом с валидацией и явной детекцией timeout.
## @scope    Sourced by bootstrap/*.sh, scaffold/*.sh, deploy/*.sh, lib/*.sh scripts.
##           Provides: SSH_OPTS_COMMON (readonly const array), ssh_exec(), ssh_read().
##           Requires logging.sh (log_imp) sourced first.
## @invariants
##   - Каждый ssh_exec/ssh_read вызов обёрнут в `timeout`
##   - exit=124 детектируется явно → log_imp 1 "SSH timeout"
##   - SSH_OPTS_COMMON — readonly (защита от случайной мутации)
##   - timeout default: deploy mode = 600s, read mode = 60s
##   - fail-fast: пустой host/cmd или non-int timeout → return 2
## @rationale Q: Why a shared SSH facade instead of inline ssh calls?
##            A: Устраняет CRITICAL-проблему P02 (CI hangs из-за SSH-вызовов без timeout).
##               Единый source of truth для всех remote-операций (SSH_OPTS, timeout, error handling).
##               Уменьшает DRIFT между 6+ потребителями SSH-вызовов.
##               Следует принципу AI-First Architecture: module boundary = lib/ssh.sh.
## @changes  LAST_CHANGE: 2026-07-21 | W2-E1 — Initial implementation (DevPlan 029)
## @modulemap — SSH_OPTS_COMMON   [R]   Readonly const array, общие SSH-флаги
##             — ssh_exec          [W:100] Основная SSH-функция с timeout-wrapper
##             — ssh_read          [W:80]  Алиас read-only (60s default)
## @usecases  — deploy: ssh_exec "host" "ci-deploy" "docker compose pull" 600 deploy
##             — status: ssh_read "host" "ci-deploy" "docker ps" 60
# endregion MODULE_CONTRACT
# GREP_SUMMARY: ssh, facade, timeout, ssh-exec, ssh-read, ssh-opts, remote-cmd, bootstrap, scaffold, deploy
# STRUCTURE: ▶ ┌SSH_OPTS_COMMON readonly┐ → ○ ssh_exec(h,u,c,t=600,m) → ◇ validate:h/c nonempty,t=int → ⚡ timeout t ssh opts u@h c → ◇ exit124→log1:ret124 | exit0→log9:ret0 | rc≠0→log7:retRC → ○ ssh_read(h,u,c,t=60)→ssh_exec
#            └─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

# ── Default prefix ─────────────────────────────────────────────────
# Preserve caller's __LOG_PREFIX if set, default to "ssh" for standalone use
__LOG_PREFIX="${__LOG_PREFIX:-ssh}"

# ═══════════════════════════════════════════════════════════════════
# SSH_OPTS_COMMON — shared readonly SSH options (Python SoT, DevPlan 116 B5 D1)
# ═══════════════════════════════════════════════════════════════════
# region CONST_SSH_OPTS_COMMON
## @purpose  Default SSH options for all platform SSH connections — ЕДИНЫЙ SoT в
##           core/internal/shared/ssh_opts.py (Python, DevPlan 116 B5 T2 D1).
##           BatchMode=yes, StrictHostKeyChecking=accept-new, ConnectTimeout=<SSH_CONNECT_TIMEOUT>,
##           ServerAliveInterval=30, ServerAliveCountMax=10.
## @invariants Readonly array — защита от случайной мутации.
##             Source-guard: повторный source безопасен (var уже readonly).
##             PYTHONPATH-init по паттерну core/lib/audit.sh (repo root = core/lib/../..).
##             Fail-fast: python3 недоступен или пустой вывод → return 1 с IMP:10
##             (иначе ssh с пустыми флагами молча повиснет).
##             bash 3.2 (macOS): НЕ mapfile — read -r -a по IFS (значения без пробелов).
if ! declare -p SSH_OPTS_COMMON &>/dev/null; then
    _SSH_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export PYTHONPATH="${_SSH_LIB_DIR}/../..:${PYTHONPATH:-}"
    # ⚡ TRAP[DECISION] · 2026-08-01 · — · SSH_OPTS_COMMON из shared/ssh_opts.py (Python SoT, D1)
    # · Rejected: 5 дублирующих копий SSH_OPTS (core_deliverer, overlay_deliverer, channels ×2,
    # ·   remote_executor) + этот shell — ConnectTimeout=10 outlier в context_promoter.
    # · Reason: триггер «extract when consumers > 3» (vps_readiness:37-42) сработал — 5 потребителей;
    # ·   D1 пользователя 2026-08-01 — Python SoT уменьшает bash-поверхность (паттерн audit.sh).
    # · Rev: если появится второй shell-потребитель флагов — пересмотреть фасад.
    if ! command -v python3 >/dev/null 2>&1; then
        if declare -f log_imp >/dev/null 2>&1; then
            log_imp 10 "ssh" "python3 required for SSH_OPTS_COMMON (core.internal.shared.ssh_opts)"
        else
            echo "[IMP:10][ssh] python3 required for SSH_OPTS_COMMON (core.internal.shared.ssh_opts)" >&2
        fi
        return 1
    fi
    read -r -a SSH_OPTS_COMMON <<< "$(python3 -m core.internal.shared.ssh_opts --shell)"
    if [ "${#SSH_OPTS_COMMON[@]}" -eq 0 ]; then
        if declare -f log_imp >/dev/null 2>&1; then
            log_imp 10 "ssh" "SSH_OPTS_COMMON empty — python3 -m core.internal.shared.ssh_opts --shell failed"
        else
            echo "[IMP:10][ssh] SSH_OPTS_COMMON empty — python3 -m core.internal.shared.ssh_opts --shell failed" >&2
        fi
        return 1
    fi
    readonly -a SSH_OPTS_COMMON
fi
# endregion CONST_SSH_OPTS_COMMON

# ═══════════════════════════════════════════════════════════════════
# ssh_exec — main SSH execution with timeout wrapper
# ═══════════════════════════════════════════════════════════════════
# region FUNC_ssh_exec
## @purpose  Execute a command on a remote host via SSH with timeout wrapper.
##           Detects exit=124 (timeout), logs appropriately, and propagates exit code.
##           Входная точка для всех SSH-команд платформы.
## @param $1  SSH host (IP or domain)
## @param $2  SSH user
## @param $3  Command to execute on remote host
## @param $4  Timeout in seconds (default: 600 for deploy mode)
## @param $5  Mode: "deploy" (default, 600s) or "read" (60s)
## @return   0   — success (SSH command completed)
## @return   124 — timeout (SSH command exceeded timeout limit)
## @return   2   — input validation failure (empty host/cmd, non-int timeout)
## @return   *   — SSH native exit codes propagated (1-255)
## @sideeffect stderr: LDD logs at IMP:1 (timeout), IMP:7 (fail), IMP:9 (ok)
## @complexity O(1) — single timeout-wrapped SSH call
## @invariants
##   - Всегда обёрнут в `timeout`
##   - Валидация: host и cmd непустые, timeout — целое число
##   - exit=124 детектируется явно, NOT silent-fail
##   - Нет DRY_RUN-ветки: entrypoints используют свои --dry-run флаги
ssh_exec() {
    local host="$1"
    local user="$2"
    local cmd="$3"
    local timeout="${4:-600}"
    local mode="${5:-deploy}"

    # ── Input validation (fail-fast) ──────────────────────────────
    if [[ -z "${host}" ]]; then
        log_imp 1 "validate" "FAIL-FAST: host is empty"
        return 2
    fi
    if [[ -z "${cmd}" ]]; then
        log_imp 1 "validate" "FAIL-FAST: command is empty"
        return 2
    fi
    if ! [[ "${timeout}" =~ ^[0-9]+$ ]]; then
        log_imp 1 "validate" "FAIL-FAST: timeout='${timeout}' is not an integer"
        return 2
    fi

    # ── Log execution ──────────────────────────────────────────────
    log_imp 7 "exec" "Starting: timeout ${timeout}s ssh ${user}@${host} (mode=${mode})"

    # ── Execute SSH with timeout wrapper ───────────────────────────
    local rc=0
    timeout "${timeout}" ssh "${SSH_OPTS_COMMON[@]}" "${user}@${host}" "${cmd}" || rc=$?

    # ── Handle exit codes ──────────────────────────────────────────
    if [[ ${rc} -eq 0 ]]; then
        log_imp 9 "exec" "OK: ${user}@${host} — command completed"
        return 0
    elif [[ ${rc} -eq 124 ]]; then
        log_imp 1 "exec" "TIMEOUT: ${user}@${host} — ${timeout}s exceeded"
        return 124
    else
        log_imp 7 "exec" "FAIL: ${user}@${host} — exit=${rc}"
        return ${rc}
    fi
}
# endregion FUNC_ssh_exec

# ═══════════════════════════════════════════════════════════════════
# ssh_read — read-only short commands (60s default timeout)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_ssh_read
## @purpose  Read-only SSH command with shorter default timeout (60s).
##           Алиас для ssh_exec с mode=read. Используется для read-only probes:
##           docker ps, project-list, healthcheck, etc.
## @param $1  SSH host
## @param $2  SSH user
## @param $3  Command to execute
## @param $4  Timeout in seconds (default: 60)
## @return   Same as ssh_exec
## @complexity O(1)
## @rationale Отдельная функция с коротким дефолтным timeout предотвращает
##            зависание read-only проб. Caller'ы не должны помнить timeout=60.
## @invariants
##   - Default timeout = 60s (не 600s)
##   - Делегирует в ssh_exec с mode=read
ssh_read() {
    local host="$1"
    local user="$2"
    local cmd="$3"
    local timeout="${4:-60}"

    log_imp 7 "read" "ssh_read: ${user}@${host} (timeout=${timeout}s)"
    ssh_exec "${host}" "${user}" "${cmd}" "${timeout}" "read"
}
# endregion FUNC_ssh_read

# 🧐 TRAP[DECISION] · 2026-07-21 · HI · Timeout-дефолты 600s deploy / 60s read
# · Rejected: единый timeout=300 (риск: прерывание длинных rsync на медленных каналах)
# · Reason: remote-deploy (rsync/docker-pull/converge): ServerAliveCountMax=10 ×
# ·         ServerAliveInterval=30s = 5 мин safe-margin. 600s = 10 мин ≈ 2× safe-margin
# ·         для длинных docker-pull на медленных каналах.
# ·         read-only (docker ps, project-list): короткие команды, 60s = 2× типичного
# ·         времени ответа сервера.
# · Rev: если CI-deploy стабильно < 300s → снизить deploy-default до 400s
