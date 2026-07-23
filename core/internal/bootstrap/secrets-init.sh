#!/usr/bin/env bash
# GREP_SUMMARY: secrets-init context-generation master-password unified-auth service-passwords service-users explicit-assignment idempotent TRAP-POLICY
# STRUCTURE: ▶ detect call mode → ⚡ MASTER_PWD/EMAIL set? → ┌SERVICE_PASSWORDS[3]┐+┌SERVICE_USERS[3]┐ → ○ pw_var loop → ○ user_var loop → ∑ summary
# region MODULE_CONTRACT
## @purpose  Initialize all service-passwords from PLATFORM_MASTER_PASSWORD and all service-users
##            (username/email) from PLATFORM_MASTER_EMAIL — unified auth policy for the platform.
## @scope    Called from node-lifecycle.sh --mode init (after step_12b_ensure_secrets) and from make bootstrap-node.
##            Can be sourced directly for in-script use OR called as a standalone script.
## @invariants
##   - Все сервис-пароли (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD)
##     инициализируются значением PLATFORM_MASTER_PASSWORD при первом bootstrap
##   - Все сервис-пользователи (HERMES_DASHBOARD_USERNAME, GF_SECURITY_ADMIN_USER, LANGFUSE_INIT_USER_EMAIL)
##     инициализируются значением PLATFORM_MASTER_EMAIL при первом bootstrap
##   - Если переменная уже задана в окружении (operator-defined) — не перезаписывается (idempotent)
##   - Если PLATFORM_MASTER_PASSWORD не задан — скрипт завершается с ошибкой (FAIL-fast)
##   - Если PLATFORM_MASTER_EMAIL не задан — user-поля не инициализируются, только предупреждение
##   - Compose-файлы НЕ используют fallback-цепочки — строго ${VAR_NAME}
##   - При source-вызове используется return вместо exit (не завершает вызывающий shell)
## @rationale Явное присвоение всех кредов в одном файле (secrets.env) — одна точка управления;
##            compose-файлы не используют fallback-цепочек, исключая silent misconfiguration.
##            Idempotent-логика защищает operator-defined значения при повторных bootstrap.
## @changes  2026-07-21 — Initial implementation (Wave 014, TASK-5)
##            2026-07-24 — Added SERVICE_USERS auto-generation from PLATFORM_MASTER_EMAIL
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

# ── Инициализация паролей ──────────────────────────────────────────────────
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

# ── SERVICE_USERS — инициализируемые из PLATFORM_MASTER_EMAIL ──────────────
# При добавлении нового сервис-пользователя в платформу: добавить имя переменной в массив.
# Правило: имя/email пользователя по умолчанию = admin@PLATFORM_DOMAIN = PLATFORM_MASTER_EMAIL.
# Скрипт idempotent: если переменная уже задана — operator-defined значение сохраняется.
# Если PLATFORM_MASTER_EMAIL не задан — user-поля не инициализируются (только warning).
SERVICE_USERS=(
    "HERMES_DASHBOARD_USERNAME"
    "GF_SECURITY_ADMIN_USER"
    "LANGFUSE_INIT_USER_EMAIL"
)

USER_INIT_COUNT=0
USER_KEPT_COUNT=0

if [[ -n "${PLATFORM_MASTER_EMAIL:-}" ]]; then
    for user_var in "${SERVICE_USERS[@]}"; do
        if [[ -z "${!user_var:-}" ]]; then
            export "${user_var}=${PLATFORM_MASTER_EMAIL}"
            echo "[IMP:8][secrets-init] ${user_var} ← ${PLATFORM_MASTER_EMAIL} (initialized)"
            ((USER_INIT_COUNT++))
        else
            echo "[IMP:8][secrets-init] ${user_var} already set — keeping operator-defined value"
            ((USER_KEPT_COUNT++))
        fi
    done
    echo "[IMP:9][secrets-init] Service users initialized: ${USER_INIT_COUNT} set, ${USER_KEPT_COUNT} kept (from ${#SERVICE_USERS[@]} total)"
else
    echo "[IMP:7][secrets-init] PLATFORM_MASTER_EMAIL not set — skipping service user initialization"
fi

# ── При source-вызове — не выходим из shell ───────────────────────────────
if [[ "${SHALL_EXIT}" == "exit" ]]; then
    exit 0
fi
