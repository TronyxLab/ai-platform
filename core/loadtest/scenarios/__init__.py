# GREP_SUMMARY: loadtest scenarios locust package SoT rps-wait-time helper constant-throughput lt-target-rps
# STRUCTURE: ┌пакет locust-сценариев┐ → ◇ rps_wait_time (constant_throughput | between fallback) → ◇ web/llm/llm_stream/langfuse_ingest/db/s3 → ⎋
# region MODULE_CONTRACT
## @purpose  Пакет locust-сценариев нагрузочного тестирования (DevPlan 146 W1).
##           Каждый модуль — отдельный locust-файл (-f core/loadtest/scenarios/<name>.py),
##           читающий параметры из env LT_* (заполняет config.py из scenarios.yaml SoT).
##           Общий helper rps_wait_time (DevPlan 146-m1 TASK-2, BUG-1 fix) — единый
##           RPS-механизм: constant_throughput из locust.wait_time (штатное средство
##           locust 2.x; CLI-флаг --max-rps НЕ существует) с env-параметризацией
##           LT_TARGET_RPS/LT_USERS, заполняемой runner_cli._locust_env.
## @scope    Запускается ТОЛЬКО locust — платформенный код core/internal/loadtest/
##           эти модули НЕ импортирует (locust — optional-зависимость, load extra).
## @invariants
##   - Никаких захардкоженных RPS/порогов/endpoint-ов в .py (инвариант 2 DevPlan 146)
##   - optional-сценарии (db, s3) — guard LT_ENABLED с выходом до создания user-классов
##   - RPS-контроль: единый helper rps_wait_time — constant_throughput(target_rps/users)
##     при LT_TARGET_RPS>0 и LT_USERS>0; иначе fallback between(0.05, 0.2)
## @rationale Пакет-контракт: сценарии изолированы от платформы (не импортируются),
##            распространяются rsync-ом на ноду целиком (core/loadtest/ → /tmp/loadtest-<ts>/).
##            Helper в __init__.py — DRY (DevPlan 146-m1 инвариант 5): 6 файлов × 5 строк
##            дублирования → 1 функция. constant_throughput вместо --max-rps — нулевые
##            новые зависимости (locust-plugins отклонён, DevPlan 146-m1 §2).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
## @changes  2026-08-11 | DevPlan 146-m1 TASK-2 — добавлен rps_wait_time (RPS-фикс BUG-1)
# endregion MODULE_CONTRACT

from locust import between, constant_throughput


# region FUNC_rps_wait_time
def rps_wait_time(target_rps: float, users: int):
    """wait_time для target total RPS: constant_throughput (per-user) или between-fallback.

    ▶ ┌target_rps, users┐ → ◇ rps>0 и users>0 → constant_throughput(rps/users) → ⎋
      | → between(0.05, 0.2) fallback (без RPS-контроля) → ⎋

    ## @purpose  Единый RPS-механизм всех сценариев (DevPlan 146-m1 TASK-2, BUG-1 fix):
    ##            locust НЕ имеет --max-rps — точный RPS задаётся constant_throughput
    ##            (per-user task_runs_per_second = target/users, latency-адаптивный).
    ##            env LT_TARGET_RPS (общий target) и LT_USERS (размер пула) заполняет
    ##            runner_cli._locust_env (в capacity — per-step значения).
    ## @io — ⇥ target_rps: float (>0 → активный контроль), users: int (размер пула)
    ##       → ⎋ wait_time-функция (constant_throughput | between(0.05, 0.2))
    ## @complexity — O(1)
    ## @invariants
    ##   - target_rps <= 0 ИЛИ users <= 0 → fallback between(0.05, 0.2) (без контроля)
    ##   - per-user RPS = target_rps / users (users — пул, users ≠ контроль RPS)
    ##   - constant_throughput учитывает latency: wait = max(0, 1/per_user - run_time)
    """
    if target_rps > 0 and users > 0:
        return constant_throughput(target_rps / users)
    return between(0.05, 0.2)


# endregion FUNC_rps_wait_time
