#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint node-update update remote-dispatch thin-facade unified-verb age-key rc3-semantics
# STRUCTURE: ▶ init ┌--verb update┐ → ⚡ python3 remote_dispatch.py --verb update "$@" → ⎋ exit {0,1,2,124}
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make node-update` (DevPlan 170 W9-F2): вся бизнес-логика
##           (--node validation, AGE-ключ детекция rc=3 non-fatal, vhost overlay delivery S2,
##           SSH proxy, локальный fallback node-lifecycle.sh) — в core/internal/bootstrap/
##           remote_dispatch.py.
## @scope    Called ONLY from Makefile. Owns: единственный вызов dispatch-модуля.
## @invariants
##   - --node ОБЯЗАТЕЛЕН; --age-secret-key-file/--dry-run/--reconcile/passthrough — в Python
##   - SSH proxy/локальный fallback/exit-коды 0|1|2|124 — контракт remote_dispatch.py (1:1 с прежним)
##   - 0 inline python3 в executable-коде: единственный вызов — script-path python3
## @rationale Strangler-Fig (research-A §9): node-update.sh (119 LOC, двойник converge.sh) → тонкий
##            фасад (<60 LOC); rc=3/rc=2 дискриминация унифицирована в Python с unit-тестами.
## @changes 2026-08-15 | DevPlan 170 W9-F2 — логика извлечена в remote_dispatch.py (было 119 LOC)
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${_EP_DIR}/../internal/bootstrap/remote_dispatch.py" --verb update "$@"
