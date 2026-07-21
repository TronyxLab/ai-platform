#!/usr/bin/env bash
# GREP_SUMMARY: secrets-init context-generation master-password unified-auth service-passwords explicit-assignment idempotent TRAP-POLICY
# STRUCTURE: ▶ detect call mode → ⚡ MASTER_PWD set? → ┌SERVICE_PASSWORDS[3]┐ → ○ pw_var loop: ─◇─ set? → export/keep → ∑ summary
# region MODULE_CONTRACT
## @purpose  Initialize all service-passwords from PLATFORM_MASTER_PASSWORD — unified auth policy for the platform.
##            Ensures every service that needs a password gets the master password by default, eliminating
##            compose-fallback chains and providing a single point of credential management (secrets.env).
## @scope    Called from node-lifecycle.sh --mode init (after step_12b_ensure_secrets) and from make bootstrap-node.
##            Can be sourced directly for in-script use OR called as a standalone script.
## @invariants
##   - Все сервис-пароли (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD)
##     инициализируются значением PLATFORM_MASTER_PASSWORD при первом bootstrap
##   - Если сервис-пароль уже задан в окружении (operator-defined) — не перезаписывается (idempotent)
##   - Если PLATFORM_MASTER_PASSWORD не задан — скрипт завершается с ошибкой (FAIL-fast)
##   - Compose-файлы НЕ используют fallback-цепочки — строго ${VAR_NAME}
##   - При source-вызове используется return вместо exit (не завершает вызывающий shell)
## @rationale Явное присвоение всех паролей в одном файле (secrets.env) — одна точка управления кредами;
##            compose-файлы не используют fallback-цепочек, исключая silent misconfiguration.
##            Idempotent-логика защищает operator-defined значения при повторных bootstrap.
## @changes  2026-07-21 — Initial implementation (Wave 014, TASK-5)
# endregion MODULE_CONTRACT

set -euo pipefail

# ── Source / direct call detection ──────────────────────────────────────────
# secrets-init.sh может быть sourced (source ./secrets-init.sh) или вызван напрямую
# (bash secrets-init.sh). При source-вызове exit завершит вызывающий shell —
# используем return. SHALL_EXIT определяет команду выхода при FATAL.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    SHALL_EXIT="exit"
else
    SHALL_EXIT="return"
fi

# ⚠️ TRAP[POLICY] · 2026-07-21 · HI · Unified Auth: все сервис-пароли = PLATFORM_MASTER_PASSWORD
# · Правило: при создании нового контекста все *_PASSWORD переменные
# ·   инициализируются значением PLATFORM_MASTER_PASSWORD.
# ·   Оператор может переопределить конкретный сервис-пароль позже — явно в secrets.env.
# ·   Compose-файлы НЕ используют fallback-цепочки — строго ${VAR_NAME}.
# · Rev: если количество сервис-паролей превысит 20 — рассмотреть авто-генерацию из шаблона.

# ── PLATFORM_MASTER_PASSWORD — обязателен ──────────────────────────────────
if [[ -z "${PLATFORM_MASTER_PASSWORD:-}" ]]; then
    echo "[IMP:9][secrets-init] FATAL: PLATFORM_MASTER_PASSWORD not set — cannot initialize service passwords" >&2
    ${SHALL_EXIT} 1
fi

# ── SERVICE_PASSWORDS — инициализируемые из мастер-пароля ──────────────────
# При добавлении нового сервис-пароля в платформу: добавить имя переменной в массив.
# Скрипт idempotent: если переменная уже задана — operator-defined значение сохраняется.
SERVICE_PASSWORDS=(
    "HERMES_DASHBOARD_PASSWORD"
    "GF_SECURITY_ADMIN_PASSWORD"
    "LANGFUSE_INIT_USER_PASSWORD"
)

# ── Инициализация ──────────────────────────────────────────────────────────
INIT_COUNT=0
KEPT_COUNT=0

for pw_var in "${SERVICE_PASSWORDS[@]}"; do
    if [[ -z "${!pw_var:-}" ]]; then
        export "${pw_var}=${PLATFORM_MASTER_PASSWORD}"
        echo "[IMP:8][secrets-init] ${pw_var} ← PLATFORM_MASTER_PASSWORD (initialized)"
        ((INIT_COUNT++))
    else
        echo "[IMP:8][secrets-init] ${pw_var} already set — keeping operator-defined value"
        ((KEPT_COUNT++))
    fi
done

echo "[IMP:9][secrets-init] Service passwords initialized: ${INIT_COUNT} set, ${KEPT_COUNT} kept (from ${#SERVICE_PASSWORDS[@]} total)"

# ── При source-вызове — не выходим из shell ───────────────────────────────
if [[ "${SHALL_EXIT}" == "exit" ]]; then
    exit 0
fi
