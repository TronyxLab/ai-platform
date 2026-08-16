"""Vulture whitelist — registry-паттерны и контрактные параметры (DevPlan 163 W-D D4).

# GREP_SUMMARY: vulture, whitelist, dead-code, registry-pattern, dynamic-dispatch, state-machine, provider-registry, platform-config, signal-handler, context-manager, advisory
# STRUCTURE: ┌registry-паттерны (динамический dispatch: state_machine, provider_registry, platform_config)┐ + ┌контрактные параметры (signal/__exit__)┐ → ⎋ vulture core/ --whitelist-файл
"""
# region MODULE_CONTRACT
## @purpose  Vulture whitelist: имена, которые vulture ошибочно считает мёртвыми, но которые
##           реально используются (registry/динамический dispatch, контрактные сигнатуры).
##           Прогон: vulture core/ core/internal/static/vulture_whitelist.py --min-confidence 100
## @scope    Два класса (задание W-D D4):
##           1. Registry-паттерны — методы, вызываемые динамически (CLI-dispatch, фазы state
##              machine, registry-резолверы) — vulture не видит статических вызовов.
##           2. Контрактные параметры — обязательные параметры сигнатур (signal-обработчик,
##              context-manager __exit__), не используемые телом.
##           НЕ включены 60%-кандидаты мёртвого кода (advisory покажет; разбор — W-G/дед-код).
## @invariants
##   - Формат: голое имя (vulture-usage, --make-whitelist-стиль) + ruff: ignore[B018, F821] маркеры
# ruff: file-ignore[B018, F821] — vulture-whitelist: голые имена по дизайну (undefined by design)
##   - Каждая запись имеет обоснование (registry-контракт + доказанный вызов)
##   - Новый мёртвый код НЕ маскируется: whitelist покрывает только доказанные динамические вызовы
##   - vulture — ADVISORY (non_blocking) в check-suite (консенсус 4 DevPlan 163)
## @rationale Vulture статически не видит динамический dispatch (execute_phase, resolve_provider
##            вызываются по имени из CLI/оркестраторов). Whitelist = документация реестра.
## @changes  2026-08-13 | DevPlan 163 W-D D4 — создан по vulture core/ (100% и 60% confidence)
# endregion MODULE_CONTRACT

# ─── 1. Registry-паттерны (динамический dispatch, доказанные вызовы) ─────────
# state_machine.py: методы вызываются из lifecycle/cli.py по имени: setup_state (cli.py:225,248),
# dry_run_plan (226), execute_phase (263,475), phase_needs_rerun (458,467,562),
# validate_bootstrap_env (243), reset (221), current_step — sm.state.current_step (247,250).
setup_state  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
dry_run_plan  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
execute_phase  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
phase_needs_rerun  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
validate_bootstrap_env  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
reset  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
current_step  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]

# provider_registry.py: registry-резолверы из cert_orchestrator.py — load_registry (84-89),
# resolve_provider (244), challenge_mode (532), all_cred_names (539), provider_env (allowlist env).
load_registry  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
resolve_provider  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
provider_env  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
all_cred_names  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
challenge_mode  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]

# platform_config.py: default_context_sentinel — bootstrap/deploy/context_deployer.py:797,1156
default_context_sentinel  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]

# ─── 2. Контрактные параметры (обязательные сигнатуры, тело их не использует) ─
# decrypt_secrets.py:_signal_handler(signum, frame) — сигнатура signal-обработчика требует
# второй параметр; frame не используется телом (удалять нельзя — сигнатура).
frame  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
# file_lock.py:FileLock.__exit__(self, exc_type, exc_value, traceback) — контракт
# context-manager требует 3 параметра; тело использует только self.
exc_type  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
exc_value  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
traceback  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]

# ─── 3. HTTP-handlers stdlib-диспатча (FP-класс, W2-11 169) ───────────────────
# status-page/app.py:do_GET/do_POST — BaseHTTPRequestHandler.handle_one_request()
# вызывает getattr(self, "do_" + command) динамически; vulture не видит диспатч.
# Доказательство: app.py:462 ThreadingHTTPServer(..., StatusPageHandler); живые
# запросы в test_status_page.py:1162-1219; do_POST → _handle_refresh (app.py:433).
do_GET  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
do_POST  # pyright: ignore[reportUndefinedVariable, reportUnusedExpression]
