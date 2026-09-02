#!/usr/bin/env python3
# GREP_SUMMARY: phases-capabilities, ENV_AWARE_PHASES, FACTS_AWARE_PHASES, capability-sets, DI, bootstrap, lifecycle
# STRUCTURE: ▶ capabilities.py (leaf констант) → ┌ENV_AWARE_PHASES (6 фаз env=)┐ → ┌FACTS_AWARE_PHASES (6 фаз facts=)┐ → ⎋ re-export через phases/__init__.py → state_machine
# region MODULE_CONTRACT
## @purpose  Capability-сеты фаз bootstrap lifecycle (план 170 W5-C3): какие фазы принимают
##           env= Mapping (W4e, DevPlan 160 E2) и facts= EnvironmentFacts (E3, DevPlan 160)
##           DI-параметры. Живут в phases/ рядом с сигнатурами фаз (фактический источник
##           правды о DI-параметрах); state_machine.execute_phase собирает kwargs по ним.
## @scope    Только константы (leaf, 0 зависимостей от lifecycle) — RUF067 (non-empty-init-module)
##           запрещает константы в phases/__init__.py (только docstrings + re-export):
##           capability-сеты в отдельном модуле, __init__ re-export'ит (публичный контракт).
## @invariants
##   - Значения — строковые литералы BootstrapPhase (НЕ импорт state_machine — phases →
##     state_machine создал бы цикл; литералы синхронизированы с BootstrapPhase-значениями)
##   - ПУБЛИЧНЫЕ имена (гейт no_private_cross_module_imports, allowlist пуст): state_machine
##     импортирует с приватным алиасом (from ... import ENV_AWARE_PHASES as _ENV_AWARE_PHASES)
##   - Только фазы с env=/facts=-параметром в сигнатуре (phases/system.py W4d) — передача
##     facts фазам без параметра = TypeError
## @rationale W5-C3 (research-A §4): capability-сеты жили в state_machine.py:257-276 — переезжают
##           в phases/ (рядом с сигнатурами фаз). Выделение в capabilities.py (не в __init__) —
##           требование RUF067; re-export сохраняет путь импорта state_machine.
## @changes  2026-08-15 · план 170 W5-C3 — создан (перенос из state_machine.py + RUF067-выделение)
# endregion MODULE_CONTRACT

# ── Фазы, принимающие env= Mapping (W4e, DevPlan 160 E2) ──────────────────────────────
# Читают ключевые env-переменные (TOR_ENABLED, SECRETS_ENV_FILE) из переданного дикта,
# а не os.environ. Остальные фазы читают env через os.environ напрямую (вне скоупа волны).
# W-H (DevPlan 163): +USER_ACCOUNTS (PLATFORM_* ключи — E3 сигнатура уже принимает env),
# +REGISTRY_AUTH (GHCR_PULL_TOKEN) — тесты flow передают полный env-дикт (0 setenv).
# 167 D5 (DI-zero): +NODE_CONFIGURATION (φ5 — NODE_CONFIGS_REMOTE_BASE для node_configs_dir;
# path-injection убирает os.path.isdir-патч flow-тестов, см. TRAP[DI-SEAM] в phases/system.py).
ENV_AWARE_PHASES = frozenset({
    "system_bootstrap",  # φ1: TOR_ENABLED/TOR_BRIDGES_FILE/SKIP_TOR_VERIFY/SECURITY_AUTO_REBOOT
    "user_accounts",  # φ2: PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY (E3)
    "secrets_provision",  # φ4: SECRETS_ENV_FILE (через helpers_secrets.ensure_secrets_exist)
    "registry_auth",  # φ6: GHCR_PULL_TOKEN (W-H 163: env-дикт вместо os.environ)
    "secrets_update",  # φ9: SECRETS_ENV_FILE (через helpers_secrets.ensure_secrets_exist)
    "node_configuration",  # φ5: NODE_CONFIGS_REMOTE_BASE (167 D5, path-injection node_configs_dir)
    "final_verify",  # φ-final-verify (DevPlan 029 T5): SECRETS_ENV_FILE/NODE_CONFIGS_DIR/GHCR_PULL_TOKEN
})

# ── Фазы, принимающие facts= EnvironmentFacts (E3, DevPlan 160) ───────────────────────
# System-факты (is_root/path_isfile) через DI — тесты фаз без monkeypatch os.geteuid/
# os.path.isfile. Только фазы с facts-параметром в сигнатуре (phases/system.py W4d) —
# передача facts фазам без параметра = TypeError.
FACTS_AWARE_PHASES = frozenset({
    "system_bootstrap",  # φ1: facts.is_root/path_isfile
    "platform_setup",  # φ3: facts.path_isfile
    "node_configuration",  # φ5: facts.path_isfile
    "converge_services",  # φ8.5: facts.path_isfile
    "node_config_update",  # φ10: facts.path_isfile
    "converge_update",  # φ13: facts.path_isfile
})
