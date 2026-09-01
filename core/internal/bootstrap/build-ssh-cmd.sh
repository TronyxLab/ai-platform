# shellcheck shell=bash
# GREP_SUMMARY: build-ssh-cmd build_ssh_cmd build_update_ssh_cmd build_converge_ssh_cmd build_check_security_ssh_cmd build_deploy_context_ssh_cmd build_init_secret_prelude build_update_secret_prelude secret-prelude REF-0007 ssh-command quoting printf D3 python-facade
# STRUCTURE: ▶ ┌7 build-функций┐ → ⚡ python3 -m core.internal.shared.ssh_cmd_builder <mode> "$@" → ⎋ stdout: remote command | secret-prelude (init-secrets/update-secrets → ssh-stdin)
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (DevPlan 164 W3.5-1) над core/internal/shared/ssh_cmd_builder.py:
##           printf %q SSH command builders (D3): build_ssh_cmd (init), build_update_ssh_cmd (update),
##           build_converge_ssh_cmd (converge/reconcile), build_check_security_ssh_cmd (DevPlan 134 L2),
##           build_deploy_context_ssh_cmd (DevPlan 153 T7 N3) + secret-prelude builders (REF-0007,
##           Волна 11-DevPlan): build_init_secret_prelude / build_update_secret_prelude.
##           ВСЯ логика (printf %q, env fallback chain, экспорты) — в Python-модуле; фасад сохраняет
##           имена функций и позиционные аргументы.
## @scope    Sourced by remote-cmd.sh (execute wrappers) и bootstrap.sh (init REMOTE_CMD build).
## @invariants — D3: printf %q quoting — НЕПРИКОСНОВЕННО (TRAP[DECISION] 2026-07-26: shlex.quote() ≠ printf '%q')
##              — PLATFORM_ROOT export обязателен для remote core-скриптов (TRAP[BUG] P1, 2026-07-31)
##              — ci_deploy_key fallback chain: PLATFORM_CI_DEPLOY_KEY → param (TRAP[BUG] P2, 2026-07-17)
##              — ci_root_key (142 W1): fallback chain PLATFORM_CI_ROOT_KEY → param (тот же паттерн)
##              — stdout функции = ТОЛЬКО remote-команда (command-substitution контракт)
##              — REF-0007: *secret_prelude* вывод = export-скрипт ДЛЯ ssh-stdin (`bash -s`);
##                НЕ логировать, НЕ печатать в dry-run (значения ключей)
## @rationale Прямое замещение (Strangler Tier-2): бизнес-логика build-функций переехала в
##            stdlib-only Python-модуль (watchdog-паттерн — вызывается системным python3 без
##            venv-пути); имена/аргументы функций НЕ изменены — вызывающие стороны (bootstrap.sh,
##            remote-cmd.sh) остаются рабочими без правок. PYTHONPATH задаётся при source (repo root).
## ⚠️ TRAP[KEEP] · 173 W3.3 · build-ssh-cmd.sh НЕ схлопывается: build_ssh_cmd source-ит bootstrap.sh
##   (init REMOTE_CMD); контракт-тест test_remote_cmd_has_update_mode (test_node_lifecycle_static.py)
##   требует функцию; функции — однострочные фасады ssh_cmd_builder.py (0 бизнес-логики).
##   Rev: если bootstrap.sh переедет на python3 -m ssh_cmd_builder init напрямую — удалить файл + тест.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — shell-логика (179 LOC) → shared/ssh_cmd_builder.py; фасад <50 LOC
##           2026-08-16 | DevPlan 173 W3.3 — keep-решение (документированный тонкий слой)
##           2026-08-24 | REF-0007 — +build_*_secret_prelude (stdin-транспорт ключей вне argv)
# endregion MODULE_CONTRACT

_BSH_BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${_BSH_BUILD_DIR}/../../..:${PYTHONPATH:-}"
unset _BSH_BUILD_DIR

# ══════════════════════════════════════════════════════════════════════
# BUILD SSH CMD — init mode (printf %q, логика в Python)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_ssh_cmd
build_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder init "$@"; }
# endregion FUNC_build_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD INIT SECRET PRELUDE — REF-0007: AGE/ci-ключи для ssh-stdin (`bash -s`)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_init_secret_prelude
## ⚠️ Вывод содержит ЗНАЧЕНИЯ ключей — только в $(...) подстановку, не в логи/dry-run.
## QA C5 (DevPlan 14 T1.4): значения подаются через STDIN (bash builtin printf → pipe),
## НЕ позиционным argv python-процесса — /proc/<pid>/cmdline не содержит секретов.
build_init_secret_prelude() {
    local _prelude
    _prelude="$(printf '%s\n%s\n%s\n' "${1:-}" "${2:-}" "${3:-}" | python3 -m core.internal.shared.ssh_cmd_builder init-secrets)" || return $?
    # φ4-диагностика (022-launch-validation): digest prelude (НЕ содержимое) — сверка
    # байтов prelude при ре-запусках без раскрытия ключей (секреты не доходят до логов).
    if [ -n "${_prelude}" ]; then
        printf '%s' "${_prelude}" | shasum -a 256 | {
            read -r _d _; echo "[IMP:8][build_init_secret_prelude][diag] prelude digest=${_d} len=${#_prelude}"
        } >&2
    fi
    printf '%s' "${_prelude}"
}
# endregion FUNC_build_init_secret_prelude

# ══════════════════════════════════════════════════════════════════════
# BUILD UPDATE SECRET PRELUDE — REF-0007: AGE-ключ update для ssh-stdin
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_update_secret_prelude
## ⚠️ Вывод содержит ЗНАЧЕНИЕ ключа — только в $(...) подстановку, не в логи/dry-run.
## QA C5 (T1.4): значение через STDIN (вне argv).
build_update_secret_prelude() {
    printf '%s\n' "${1:-}" | python3 -m core.internal.shared.ssh_cmd_builder update-secrets
}
# endregion FUNC_build_update_secret_prelude

# ══════════════════════════════════════════════════════════════════════
# BUILD UPDATE SSH CMD — update mode (printf %q, логика в Python)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_update_ssh_cmd
build_update_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder update "$@"; }
# endregion FUNC_build_update_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# SSH EXEC STDIN — REF-0007: транспорт prelude+body через ssh 'bash -s'
# ══════════════════════════════════════════════════════════════════════
# region FUNC_ssh_exec_stdin
## @purpose  REF-0007 (11-DevPlan Волна 1): exec ssh с secret-prelude в stdin. Тело команды
##           секретов НЕ содержит; ключи не попадают в /proc argv локального ssh и remote shell.
## @io       $1 host; $2 secret-prelude (export-строки или ""); $3 remote command body
## ⚠️ Аргумент $2 содержит ЗНАЧЕНИЯ ключей — передавать только через "$(...)" подстановку,
##    никогда не печатать в логи/dry-run.
## @invariants Требует SSH_OPTS_COMMON (source lib/ssh.sh — выполнено через scp-deliver.sh ранее);
##             пустой prelude → строка `true` (bash -s скрипт остаётся валидным);
##             exit = rc ssh (pipeline pipefail);
##             DevPlan 16 T2.B (P1-15): ssh-exec под `timeout <DEPLOY_TIMEOUT>` — SoT
##             shared/timeouts.py через CLI-режим ssh-exec-timeout (0 литералов в shell);
##             класс P02 CI-hang закрыт.
# 🧐 TRAP[DECISION] · 2026-08-25 · DevPlan 16 T2.B · timeout-резолв ленивый одноразовый ·
# Rejected: литерал 900 в shell / хардкод на source-времени ·
# Reason: parity-требование (значение только из SoT); lazy-resolve при первом вызове + кэш
# в переменной процесса — python3-fork один раз на lifetime фасада ·
# Rev: если CLI-резолв станет недоступен в окружении вызова — пробросить через env явно.
ssh_exec_stdin() {
    local host="$1"
    local prelude="$2"
    local body="$3"
    if [ -z "${SSH_EXEC_TIMEOUT_S:-}" ]; then
        SSH_EXEC_TIMEOUT_S="$(python3 -m core.internal.shared.ssh_cmd_builder ssh-exec-timeout)" || {
            echo "[IMP:10][ssh_exec_stdin] cannot resolve SSH timeout from SoT (timeouts.py)" >&2
            return 1
        }
        export SSH_EXEC_TIMEOUT_S
    fi
    printf '%s\n%s\n' "${prelude:-true}" "${body}" | timeout "$SSH_EXEC_TIMEOUT_S" \
        ssh "${SSH_OPTS_COMMON[@]}" "root@${host}" "bash -s"
}
# endregion FUNC_ssh_exec_stdin

# ══════════════════════════════════════════════════════════════════════
# BUILD CONVERGE SSH CMD (printf %q, логика в Python)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_converge_ssh_cmd
build_converge_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder converge "$@"; }
# endregion FUNC_build_converge_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD CHECK-SECURITY SSH CMD (printf %q) — DevPlan 134 L2
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_check_security_ssh_cmd
build_check_security_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder check-security "$@"; }
# endregion FUNC_build_check_security_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD DEPLOY-CONTEXT SSH CMD (printf %q) — DevPlan 153 T7 (N3)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_deploy_context_ssh_cmd
build_deploy_context_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder deploy-context "$@"; }
# endregion FUNC_build_deploy_context_ssh_cmd
