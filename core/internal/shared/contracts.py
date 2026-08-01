#!/usr/bin/env python3
# GREP_SUMMARY: contracts, operational-policies, deploy-best-effort, exit-codes, legacy-parity, machine-readable, EXIT_OK, EXIT_FATAL
# STRUCTURE: ▶ ┌DEPLOY_BEST_EFFORT policy┐ → ◇ exit-code constants (0/1/2/3/4/10) → ◇ TRAP[DECISION] rev-date → ⎋ importable by orchestrators + gates
# region MODULE_CONTRACT
## @purpose  Контракт операционных политик платформы (DevPlan 116 B4 T1, U-39) — машиночитаемое
##           оформление политик, которые ранее декларировались комментариями («legacy parity» ×12)
##           вместо контракта. DEPLOY_BEST_EFFORT фиксирует политику deploy-канала:
##           «failing step не прерывает деплой; WARN→exit 0; HC_DONE_MARKER всегда».
##           Константы exit-кодов — единый machine-readable источник для гейтов и CLI.
## @scope    Потребители: bootstrap/deploy/deploy_orchestrator.py (комментарии-инварианты → импорт),
##           гейт T8 (test_gate_broad_except_allowlist.py), любые модули, проверяющие exit-коды.
##           Значения констант ДОЛЖНЫ совпадать с exceptions.py (exit_code атрибуты классов).
## @invariants
##   1. DEPLOY_BEST_EFFORT=True — deploy-политика legacy parity: failing step → WARN, деплой продолжается.
##   2. EXIT_* константы согласованы с shared/exceptions.py: 0=ok, 1=generic, 2=ConfigNotFound,
##      3=ConfigParse, 4=ConfigValidation, 10=Fatal — единый контракт на весь core.
##   3. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз).
##   4. Гейт test_gate_exit_codes_documented.py проверяет документацию core/AGENTS.md
##      по этим кодам; расхождение констант↔exceptions↔docs = RED.
## @rationale Q: Почему политика legacy parity живёт в shared/, а не в deploy_orchestrator?
##            A: Потребителей ≥2 (deploy_orchestrator, гейт T8) — критерий shared/ (минимум 2
##            потребителя). Размещение в shared/ делает политику видимой для гейтов и будущих
##            каналов (greenfield-деплой B1 проектирует единый канал и сожмёт legacy-точки).
##            Q: Почему константы exit-кодов отдельно от exceptions.py?
##            A: exceptions.py — runtime-классы (нельзя импортировать в тест-гейт без загрузки
##            иерархии); константы — data-only, безопасны для AST/манифест-гейтов.
## @changes  2026-08-01 | DevPlan 116 B4 T1 — Created (контракт операционных политик, D5)
# ⚠️ TRAP[DECISION] · 2026-08-01 · HI · Legacy parity как осознанная политика, а не долг
# · Rejected: немедленное переписывание 12 legacy-точек deploy_orchestrator (риск регрессии
# ·   деплой-канала в волне про исключения)
# · Reason: greenfield-деплой (B1) проектирует единый канал — legacy parity сожмётся там;
# ·   B4 фиксирует политику контрактом + гейтом, не меняя поведение (D6)
# · Rev: 2026-10-21 — если B1 не сожмёт legacy parity → пересмотреть DEPLOY_BEST_EFFORT=False
# ·   и мигрировать точки
# endregion MODULE_CONTRACT

# Deploy-политика legacy parity (U-39): «failing step не прерывает деплой; WARN→exit 0;
# HC_DONE_MARKER всегда». Поведение НЕ меняется волной B4 (D6) — только формализация.
DEPLOY_BEST_EFFORT: bool = True

# Единый machine-readable контракт exit-кодов (семантика — shared/exceptions.py).
EXIT_OK: int = 0
EXIT_GENERIC: int = 1
EXIT_CONFIG_NOT_FOUND: int = 2
EXIT_CONFIG_PARSE: int = 3
EXIT_CONFIG_VALIDATION: int = 4
EXIT_FATAL: int = 10
