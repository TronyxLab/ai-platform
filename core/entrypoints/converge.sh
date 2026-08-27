#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint converge reconcile remote-dispatch thin-facade unified-verb rc2-discrimination self-env secrets.env NGINX_OVERLAY_DIR node_detect F-015
# STRUCTURE: ▶ init ┌--verb converge┐ → ◇ self-env [source secrets.env ∋ node_detect NODE_NAME ∋ export NGINX_OVERLAY_DIR] → ⚡ python3 remote_dispatch.py --verb converge "$@" → ⎋ exit {0,1,2,124}
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make converge` (DevPlan 170 W9-F2): вся бизнес-логика
##           (--node parse + auto-detect, rc=2 дискриминация R-unit vs no-SSH-host, SSH proxy,
##           локальный fallback) — в core/internal/bootstrap/remote_dispatch.py.
##           Self-env (017 E2 / F-015-class): converge НА НОДЕ исполняется с чистым env —
##           compose-introspection R9-fallback (compose_defined_containers → build_compose_args
##           c root-compose include nginx ${NGINX_OVERLAY_DIR:?required}) падал «required variable
##           NGINX_OVERLAY_DIR is missing» → пустой конфиг → disabled-модуль не останавливался.
## @scope    Called ONLY from Makefile. Owns: единственный вызов dispatch-модуля.
## @invariants
##   - --node опционален (auto-detect в Python); --dry-run/--reconcile/passthrough — в Python
##   - SSH proxy/локальный fallback/exit-коды 0|1|2|124 — контракт remote_dispatch.py (1:1 с прежним)
##   - 0 inline python3 (-c/heredoc): единственные вызовы — script-path python3 (dispatch) и канон
##     node_detect (python3 -m core.internal.shared.node_detect — прецедент healthcheck.sh 017 Phase E)
##   - self-env: source SECRETS_ENV_FILE (default /var/lib/platform/run/secrets.env, если есть);
##     NODE_NAME — уже экспортированный ИЛИ канон-детект; NGINX_OVERLAY_DIR экспортируется
##     ТОЛЬКО при непустом NODE_NAME (фейковый путь /opt/node-configs//overlays/nginx НЕ создаётся —
##     при неопределённой ноде ошибка ${NGINX_OVERLAY_DIR:?} остаётся явной downstream)
## @rationale Strangler-Fig (research-A §9): converge.sh (124 LOC, двойник node-update.sh) → тонкий
##            фасад (<80 LOC с self-env-блоком 017 E2); rc-протоколика унифицирована в Python с unit-тестами.
##            Self-env по образцу nginx_reload_hook.sh (plan 012 T11 / F-023): фасад самодостаточен
##            в env-less ReceiveFlow/нода-прогоне — source secrets.env + экспорт NGINX_OVERLAY_DIR
##            (класс F-015); детект ноды — канон node_detect (healthcheck.sh 017 Phase E прецедент).
## @changes 2026-08-15 | DevPlan 170 W9-F2 — логика извлечена в remote_dispatch.py (было 124 LOC)
## @changes 2026-08-27 | 017 E2 (P1, класс F-015) — self-env: source secrets.env + экспорт
##            NGINX_OVERLAY_DIR через node_detect (по образцу nginx_reload_hook.sh / healthcheck.sh)
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ═══════════════════════════════════════════════════════════════════
# Self-env (017 E2 / F-015-class): converge НА НОДЕ — чистый env
# ═══════════════════════════════════════════════════════════════════
# secrets.env — расшифрованная матрица ноды; overlay-dir — канон node-configs.
# 🧐 TRAP[DECISION] · 2026-08-27 · 017 E2 / F-015-class · self-env по образцу
# nginx_reload_hook.sh (plan 012 T11 / F-023): source secrets.env + экспорт
# NGINX_OVERLAY_DIR — converge самодостаточен в env-less контексте
# · Rejected: полагаться на export из Makefile/CI (нода-прогон converge — чистый env)
# · Reason: R9-fallback compose-интроспекция падала «required variable NGINX_OVERLAY_DIR
#   is missing» → пустой конфиг → disabled-модуль не останавливался (P1 017 E2)
# · Rev: если converge получит гарантированный env-контракт из Makefile — блок можно схлопнуть
_CONVERGE_SECRETS_ENV="${SECRETS_ENV_FILE:-/var/lib/platform/run/secrets.env}"
if [[ -f "$_CONVERGE_SECRETS_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$_CONVERGE_SECRETS_ENV"
    set +a
    echo "[IMP:8][converge][self-env] Sourced ${_CONVERGE_SECRETS_ENV}" >&2
fi
# NODE_NAME: сначала уже экспортированный; иначе — канон-детект node_detect
# (healthcheck.sh 017 Phase E прецедент: ровно один <name>/node.yaml в node-configs).
if [[ -z "${NODE_NAME:-}" ]]; then
    if _CONVERGE_NODE="$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null)"; then
        export NODE_NAME="${_CONVERGE_NODE}"
        echo "[IMP:9][converge][self-env] Auto-detected NODE_NAME=${NODE_NAME}" >&2
    else
        echo "[IMP:7][converge][self-env] Node auto-detection skipped — NGINX_OVERLAY_DIR NOT exported (explicit IMP downstream)" >&2
    fi
fi
# NGINX_OVERLAY_DIR: default канон node-configs; НЕ экспортируется при неопределённой ноде
# (пустой NODE_NAME → фейковый путь не создаётся — ${NGINX_OVERLAY_DIR:?} ошибка остаётся явной).
if [[ -n "${NODE_NAME:-}" ]]; then
    export NGINX_OVERLAY_DIR="${NGINX_OVERLAY_DIR:-/opt/node-configs/${NODE_NAME}/overlays/nginx}"
    echo "[IMP:8][converge][self-env] NGINX_OVERLAY_DIR=${NGINX_OVERLAY_DIR}" >&2
fi

exec python3 "${_EP_DIR}/../internal/bootstrap/remote_dispatch.py" --verb converge "$@"
