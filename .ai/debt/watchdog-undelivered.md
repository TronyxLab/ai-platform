# Watchdog subsystem not delivered — DEBT (DevPlan 119 C2) — FIXED

> Создан: 2026-08-02 | DevPlan 119 C2 | Закрыт: 2026-08-03 (RC-сессия 121, решение пользователя)
> Status: FIXED | Rev: 2026-08-03

## Решение пользователя (ночная RC-сессия 2026-08-03): УДАЛИТЬ ПОЛНОСТЬЮ

Выполнено:
- `core/modules/hermes-agent/watchdog/` (agent_watchdog.py, circuit_breaker.py, docker_ops.py) — удалены
- `tests/unit/test_agent_watchdog.py`, `test_watchdog_circuit_breaker.py`, `test_watchdog_docker_ops.py` — удалены
- `module.yaml#env_requires` — watchdog-секция (8 переменных) удалена
- `shared/timeouts.py` — WATCHDOG_* константы удалены (TOR_PROXY_CURL_TIMEOUT сохранён — живёт в tor-домене)
- allowlist-гейты: cross_layer_imports (8 записей), secrets_parser_import (1), timeout_literals (_MODULE_DOMAIN_FILES 3), no_unregistered_entrypoint (glob watchdog/*.sh), scripts_audit (glob) — очищены
- доки/комментарии (platform_config, telegram_notifier, docker_compose, healthcheck_deps, secret-definitions, hermes profile config.yaml) — очищены
- манифесты (entrypoint-manifest, secrets-manifest, test_inventory) — регенерированы

Rev-условие (закрыто): подсистема не имела ни одного runtime-потребителя (0 ссылок в Dockerfile/compose/systemd/CI).
