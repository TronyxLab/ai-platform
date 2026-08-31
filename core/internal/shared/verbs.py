#!/usr/bin/env python3
# GREP_SUMMARY: verbs, canonical-verbs, verb-dictionary, is-verb, dispatch, SSH_ORIGINAL_COMMAND, reserve-names, health, rollback, U-56
# STRUCTURE: ▶ CANONICAL_VERBS (ping|exit|status|health|verify|remove|receive|rollback) → ◇ is_verb(name) → ⎋ bool → ⊕ consumer: ssh_command_parser + project_registry
# region MODULE_CONTRACT
## @purpose  Canonical SSH forced-command verb dictionary (DevPlan 116 B1 T1, U-56). Single source of
##           truth for the verb set dispatched by `orchestrator_cli dispatch` via SSH_ORIGINAL_COMMAND.
##           Also exposes the reserve-список for validate_project_name: verb-имена НЕ доступны как
##           имена проектов (проект «status» задиспатчился бы как verb — U-56).
## @scope    Core/internal/shared — pure data + predicate layer (no I/O, no business logic).
##           Consumers: ssh_command_parser.py (classify_verb), project_registry.py (validate_project_name),
##           tests/gates/test_gate_deploy_channel.py (1:1 CLI↔verbs parity).
## @invariants
##   1. CANONICAL_VERBS — закрытое множество (D1: ровно 8 verbs): ping, exit, status, health, verify, remove, receive, rollback.
##   2. is_verb(name) — exact-match predicate (case-sensitive); не verb → False (никогда не raise).
##   3. Verb-имена резервируются: validate_project_name(name) → False для любого CANONICAL_VERBS (U-56).
##   4. Модуль не импортирует ничего из core/internal/deploy/ (shared-слой ниже по зависимостям).
## @rationale U-56: проект с именем «status» был неотличим от verb `status` — SSH_ORIGINAL_COMMAND
##            диспетчеризовался неверно. Единый словарь делает reserve-проверку и classify_verb
##            согласованными (один источник, ноль дрейфа). Критерий shared-модуля (≥2 потребителя):
##            ssh_command_parser + project_registry + gate-тест.
##            `health` (read-only verb, B3 fix-forward): project_payload_delivery health-предпробка
##            «уже live» шлёт `health <project>` вместо raw `docker inspect` — ci-deploy
##            authorized_keys forced-command-restricted, произвольные команды невозможны.
##            `rollback` (launch-validation D8): ручной откат проекта оператором через forced-command
##            `rollback <project> [<snapshot-id>]` — тот же DeployOrchestrator.rollback(), что main-CLI.
## @changes 2026-08-01 | DevPlan 116 B1 T1 — Created (D1 verb-множество: deliver-verb УДАЛЁН)
## @changes 2026-08-27 | B3 fix-forward — +health (read-only verb docker inspect State.Health.Status);
##            probe переведён на него (project_payload_delivery)
## @changes 2026-09-01 | launch-validation D8 — +rollback (8-й verb, forced-command
##            `rollback <project> [<snapshot-id>]` → DeployOrchestrator.rollback(); snapshot-based)
# endregion MODULE_CONTRACT

from __future__ import annotations

# ── Canonical verb dictionary (D1) ───────────────────────────────────────────
# Закрытое множество verb'ов forced-command диспетчера (orchestrator_cli dispatch):
# любой вход вне CANONICAL_VERBS → ConfigValidationError (unknown verb, честный exit 1).
# `health` — read-only verb (docker inspect State.Health.Status): потребитель —
# project_payload_delivery B3-предпробка (ci-deploy forced-command-restricted).
# `rollback` — snapshot-based откат проекта (launch-validation D8): тот же
# DeployOrchestrator.rollback(), что main-CLI `orchestrator_cli rollback`.
CANONICAL_VERBS: tuple[str, ...] = (
    "ping",
    "exit",
    "status",
    "health",
    "verify",
    "remove",
    "receive",
    "rollback",
)

# Резервное множество для validate_project_name (U-56): имена, недоступные для проектов
VERB_RESERVE: frozenset[str] = frozenset(CANONICAL_VERBS)


# region FUNC_is_verb
## @purpose  Predicate: является ли строка каноническим verb'ом (exact-match, U-56).
## @io       ⇥ name: str → ⎋ bool (True — это verb, проект с таким именем запрещён)
## @complexity — O(N) где N = len(CANONICAL_VERBS) (8)
## @invariants
##   - Exact-match, case-sensitive: "Status" != "status" (strict, D2 — никаких нечётких матчей)
##   - Возвращает bool, никогда не raise (predicate contract)
##   - None/не-str → False (fail-safe: пустое имя не резервируется, validate_project_name сам отклонит)
def is_verb(name: str | None) -> bool:
    """Return True if name is a canonical forced-command verb (reserved for projects).

    ▶ ┌name┐ → ◇ isinstance str? → ◇ name in VERB_RESERVE → ⎋ bool
    """
    if not isinstance(name, str):
        return False
    return name in VERB_RESERVE


# endregion FUNC_is_verb
