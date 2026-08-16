# shellcheck shell=bash
# GREP_SUMMARY: build-ssh-cmd build_ssh_cmd build_update_ssh_cmd build_converge_ssh_cmd build_check_security_ssh_cmd build_deploy_context_ssh_cmd ssh-command quoting printf D3 python-facade
# STRUCTURE: ▶ ┌5 build-функции┐ → ⚡ python3 -m core.internal.shared.ssh_cmd_builder <mode> "$@" → ⎋ stdout: remote command
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (DevPlan 164 W3.5-1) над core/internal/shared/ssh_cmd_builder.py:
##           printf %q SSH command builders (D3): build_ssh_cmd (init), build_update_ssh_cmd (update),
##           build_converge_ssh_cmd (converge/reconcile), build_check_security_ssh_cmd (DevPlan 134 L2),
##           build_deploy_context_ssh_cmd (DevPlan 153 T7 N3). ВСЯ логика (printf %q, env fallback
##           chain, экспорты) — в Python-модуле; фасад сохраняет имена функций и позиционные аргументы.
## @scope    Sourced by remote-cmd.sh (execute wrappers) и bootstrap.sh (init REMOTE_CMD build).
## @invariants — D3: printf %q quoting — НЕПРИКОСНОВЕННО (TRAP[DECISION] 2026-07-26: shlex.quote() ≠ printf '%q')
##              — PLATFORM_ROOT export обязателен для remote core-скриптов (TRAP[BUG] P1, 2026-07-31)
##              — ci_deploy_key fallback chain: PLATFORM_CI_DEPLOY_KEY → param (TRAP[BUG] P2, 2026-07-17)
##              — ci_root_key (142 W1): fallback chain PLATFORM_CI_ROOT_KEY → param (тот же паттерн)
##              — stdout функции = ТОЛЬКО remote-команда (command-substitution контракт)
## @rationale Прямое замещение (Strangler Tier-2): бизнес-логика build-функций переехала в
##            stdlib-only Python-модуль (watchdog-паттерн — вызывается системным python3 без
##            venv-пути); имена/аргументы функций НЕ изменены — вызывающие стороны (bootstrap.sh,
##            remote-cmd.sh) остаются рабочими без правок. PYTHONPATH задаётся при source (repo root).
## ⚠️ TRAP[KEEP] · 173 W3.3 · build-ssh-cmd.sh НЕ схлопывается: build_ssh_cmd source-ит bootstrap.sh
##   (init REMOTE_CMD); контракт-тест test_remote_cmd_has_update_mode (test_node_lifecycle_static.py)
##   требует функцию; 5 функций — однострочные фасады ssh_cmd_builder.py (0 бизнес-логики).
##   Rev: если bootstrap.sh переедет на python3 -m ssh_cmd_builder init напрямую — удалить файл + тест.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — shell-логика (179 LOC) → shared/ssh_cmd_builder.py; фасад <50 LOC
## @changes 2026-08-16 | DevPlan 173 W3.3 — keep-решение (документированный тонкий слой)
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
# BUILD UPDATE SSH CMD — update mode (printf %q, логика в Python)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_update_ssh_cmd
build_update_ssh_cmd() { python3 -m core.internal.shared.ssh_cmd_builder update "$@"; }
# endregion FUNC_build_update_ssh_cmd

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
