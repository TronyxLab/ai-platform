#!/usr/bin/env bash
# GREP_SUMMARY: provision-environment thin-wrapper provision_env.py scope dispatch python-facade
# STRUCTURE: ▶ set PYTHONPATH (repo root) → exec python3 -m core.internal.bootstrap.provision_env "$@" → ⎋ exit-code passthrough
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (DevPlan 164 W3.5-1) для core/internal/bootstrap/provision_env.py —
##           Python-оркестратора поверх core/internal/provisioner.py: парсинг CLI, расширение 'all',
##           дедупликация scopes, default platform-env, per-scope dispatch + audit.
## @scope    Called from Makefile, CI workflows, deploy-modules.sh, state_machine.py.
## @invariants
##   - ZERO inline python3 -c / heredoc (языковая политика)
##   - Вся бизнес-логика в provision_env.py + provisioner.py
##   - --scope required (multi-scope accumulator, 'all' expansion, dedup — FIX-1)
##   - Exit codes propagate: 0=success, 1=parse/usage error, 10=docker unavailable
## @rationale Strangler-Fig (DevPlan 164 W3.5-1): 442 LOC shell → ~12 LOC facade + provision_env.py
##            + provisioner.py. Прямое замещение: путь/аргументы фасада не изменены — все вызывающие
##            стороны (Makefile, CI, deploy-modules.sh, phases/system.py, phases/docker.py) работают
##            без правок. PYTHONPATH задаётся локально (repo root) — cwd-независимость сохранена.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — shell arg-parsing/audit-диспатч → bootstrap/provision_env.py
# endregion MODULE_CONTRACT

set -euo pipefail

_PROV_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${_PROV_ENV_DIR}/../..:${PYTHONPATH:-}"
unset _PROV_ENV_DIR

exec python3 -m core.internal.bootstrap.provision_env "$@"
