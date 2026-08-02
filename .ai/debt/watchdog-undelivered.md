# Watchdog subsystem not delivered — DEBT (DevPlan 119 C2)

> Создан: 2026-08-02 | DevPlan 119 C2 | Требует решения пользователя (D-1, Brief 119 → волна 120)

## Суть

`core/modules/hermes-agent/watchdog/*` (`agent_watchdog.py`, `circuit_breaker.py`, `docker_ops.py`)
— подсистема НЕ доставляется: 0 ссылок в Dockerfile/compose/systemd/CI. Потребители —
только тесты и `env_requires` в `module.yaml` (секция Watchdog env, DevPlan 117 D33).

## Наблюдение (Observed)

- 0 упоминаний watchdog в Dockerfile/compose/systemd/workflows
- Импортируется только тестами (`tests/unit/test_agent_watchdog*.py` и т.п.)
- `module.yaml#env_requires` декларирует watchdog-переменные, но контейнер не запускается

## Гипотеза (Suspected)

Feature-flag, ожидающий активации, ИЛИ заброшенный прототип. Неизвестные планы на
watchdog — деструктивное удаление НЕ планировалось в 119 без явного решения пользователя.

## Влияние (Impact)

Мёртвый код в репозитории; тесты покрывают недоставленную функциональность (стоимость
поддержки без пользы). Риск удаления: неизвестные планы (возможный feature flag).

## Действие

TRAP[DEBT] добавлен на каждый из 3 файлов (agent_watchdog.py, circuit_breaker.py, docker_ops.py).
Никаких деструктивных действий в 119. Полный sweep (код + тесты + module.yaml + env_requires) —
на волну 120 при решении «удалить».

| Status | Rev |
|--------|-----|
| OPEN | 2026-08-31 (решение владельца на волну 120) |
