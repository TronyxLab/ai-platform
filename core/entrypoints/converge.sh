#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint converge reconcile remote-dispatch thin-facade unified-verb rc2-discrimination
# STRUCTURE: ▶ init ┌--verb converge┐ → ⚡ python3 remote_dispatch.py --verb converge "$@" → ⎋ exit {0,1,2,124}
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make converge` (DevPlan 170 W9-F2): вся бизнес-логика
##           (--node parse + auto-detect, rc=2 дискриминация R-unit vs no-SSH-host, SSH proxy,
##           локальный fallback) — в core/internal/bootstrap/remote_dispatch.py.
## @scope    Called ONLY from Makefile. Owns: единственный вызов dispatch-модуля.
## @invariants
##   - --node опционален (auto-detect в Python); --dry-run/--reconcile/passthrough — в Python
##   - SSH proxy/локальный fallback/exit-коды 0|1|2|124 — контракт remote_dispatch.py (1:1 с прежним)
##   - 0 inline python3 в executable-коде: единственный вызов — script-path python3
## @rationale Strangler-Fig (research-A §9): converge.sh (124 LOC, двойник node-update.sh) → тонкий
##            фасад (<60 LOC); rc-протоколика унифицирована в Python с unit-тестами.
## @changes 2026-08-15 | DevPlan 170 W9-F2 — логика извлечена в remote_dispatch.py (было 124 LOC)
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${_EP_DIR}/../internal/bootstrap/remote_dispatch.py" --verb converge "$@"
