# GREP_SUMMARY: loadtest scenarios locust package SoT
# STRUCTURE: ┌пакет locust-сценариев┐ → ◇ web/llm/llm_stream/langfuse_ingest/db/s3 → ⎋
# region MODULE_CONTRACT
## @purpose  Пакет locust-сценариев нагрузочного тестирования (DevPlan 146 W1).
##           Каждый модуль — отдельный locust-файл (-f core/loadtest/scenarios/<name>.py),
##           читающий параметры из env LT_* (заполняет config.py из scenarios.yaml SoT).
## @scope    Запускается ТОЛЬКО locust — платформенный код core/internal/loadtest/
##           эти модули НЕ импортирует (locust — optional-зависимость, load extra).
## @invariants
##   - Никаких захардкоженных RPS/порогов/endpoint-ов в .py (инвариант 2 DevPlan 146)
##   - optional-сценарии (db, s3) — guard LT_ENABLED с выходом до создания user-классов
## @rationale Пакет-контракт: сценарии изолированы от платформы (не импортируются),
##            распространяются rsync-ом на ноду целиком (core/loadtest/ → /tmp/loadtest-<ts>/).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT
