#!/usr/bin/env python3
# GREP_SUMMARY: practices, package, domain, inherit, behavior, project-practices, 137
# STRUCTURE: ┌package marker┐ → ◇ exports (manifest/maturity/escalator/generators/check/sync/set) → ⎋ domain contract
# region MODULE_CONTRACT
## @purpose  Пакетный маркер домена core/internal/practices/ — наследование защитных практик
##           платформы (линт, pre-commit, CI-gate, дрейф-гейты, деплой-контракты) в проекты
##           через ПОВЕДЕНИЕ (проверки исполняются платформенными каналами K1-K5), а не код.
##           Домен 137: канон практик (practices_manifest.yaml) + генераторы GENERATED-файлов
##           + локальный канал (check_project) + repair (sync/set_practices).
## @scope    Модули пакета: manifest.py (канон), maturity.py (зрелость), escalator.py (состояние),
##           generators.py (рендер GENERATED-файлов), check_project.py (K1 CLI), sync_practices.py,
##           set_practices.py. Однонаправленная зависимость: scaffold → practices → shared
##           (practices НЕ импортирует scaffold — языковая политика + DDD, DevPlan 137 §6.3).
## @invariants
##   - practices/ — библиотечный домен (НЕ оркестратор); scaffold импортирует practices
##   - Все GENERATED-файлы пишутся через shared/atomic_writer (единый writer, DevPlan 119 E5)
##   - Канон валидируется через shared/schema_validator (единственная Draft7-точка)
##   - Exit-коды из shared/contracts.py (0/1/2/3/4/10) — НЕ хардкодить
##   - Автопромоута НЕТ (решение пользователя 2026-08-05): active-full только по согласию
## @rationale Отдельный домен вместо копипаста практик в шаблоны: копии порождают дрейф
##            (как allowlist-дрейф 116 T9) и заставляют проекты поддерживать чужой код.
## @changes  2026-08-05 · DevPlan 137 W1 — создан (канон + генераторы + K1 локальный канал)
# endregion MODULE_CONTRACT
