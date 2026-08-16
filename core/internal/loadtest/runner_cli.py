#!/usr/bin/env python3
# GREP_SUMMARY: loadtest runner-cli locust headless orchestration modes exit-code csv env build-command guard preflight network duration tasks
# STRUCTURE: ▶ preflight (locust import, mock-probe) → ◇ guard (capacity prod → exit 10) → ◇ config.load_config
#           → ○ режим: smoke|regression (1 прогон) | capacity (plan_steps × run_one_step) → ○ post-run:
#           PromQL saturation → ○ baseline compare+append → ○ report.json/markdown/junit (duration_s + tasks)
#           → ⎋ exit 0|1|4|10
# region MODULE_CONTRACT
## @purpose  CLI-оркестратор нагрузочных прогонов (DevPlan 146 W1-W5 + 148 TASK-7): строит locust-команду
##           (headless, --run-time, --users, --spawn-rate, --csv, --csv-full-history),
##           передаёт RPS-контроль через env LT_TARGET_RPS/LT_USERS (сценарии используют
##           constant_throughput — helper rps_wait_time, 146-m1 BUG-1 fix),
##           запускает (local subprocess | remote docker run через runner_remote — docker-сеть
##           config.network, 148 TASK-4/5), ждёт с
##           timeout-guard, post-run собирает PromQL-saturation, baseline, report и маппит
##           вердикт на exit-код контракта shared/contracts.py: 0 PASS/WARN, 1 FAIL/ошибка,
##           2/3/4 config, 10 guard (инвариант 9).
##           duration_s (t1-t0) и per-task breakdown (read_query/write_query) — в report.json,
##           history.json и markdown (148 TASK-7, SC_STATS/SC_DB_RW).
## @scope    Единственный CLI-вход подсистемы (make load-test → python3 -m
##           core.internal.loadtest.runner_cli). Модуль НЕ импортирует locust —
##           только префлайт find_spec (locust — load extra, не runtime-зависимость).
## @invariants
##   1. Точный RPS — constant_throughput из env LT_TARGET_RPS (wait_time сценариев,
##      helper rps_wait_time; capacity — per-step RPS); users — размер пула LT_USERS
##      (users = rps×2, инвариант 11)
##   2. Exit-коды по контракту: 0 PASS/WARN, 1 FAIL/ошибка/нет locust/нет Prometheus,
##      2/3/4 config-ошибки, 10 capacity на нетестовой ноде без LOAD_ALLOW_PROD=1
##   3. timeout-guard прогона = run_time × 2 + 60s; capacity суммарный =
##      max_steps × (step_duration + 30s) + 120s
##   4. smoke ≥ 90s (инвариант 10: ≥3 сэмпла 30s-метрик, ≥2 по 60s)
##   5. optional-сценарий выключен → WARN + exit 0 (без прогона)
##   6. llm/llm_stream: mock-probe ДО прогона — ранний FAIL с сообщением об
##      установке litellm-config.mock.yml (AC6); на проде mock-модели нет —
##      LOAD_ALLOW_PROD=1 это не отменяет
##   7. env LT_* для locust — единый builder (конфиг → env), тот же для local и remote;
##      содержит LT_TARGET_RPS (target_rps прогона) и LT_USERS (размер пула)
##   8. sys.exit — только в __main__; main() -> int (канон core/AGENTS.md)
##   9. network (148 TASK-4/5): config.network → run_remote_locust --network (host — web/s3,
##      shared-db-net — db); duration_s + tasks (148 TASK-7) — в report.json/history/markdown
## @rationale Один CLI на все режимы (D2 гибридный runner, D6 make-фасад) — единая
##            точка exit-контракта и guard-ов; бизнес-логика прогонов в чистом виде
##            (диспетчеры режимов) — тестируемость через unit-тесты конфигурации/вердиктов.
## @changes  2026-08-11 | DevPlan 146 W1-W5 — Created
## @changes  2026-08-12 | DevPlan 148 TASK-7 — duration_s + tasks в отчёт/history, network проброс
# endregion MODULE_CONTRACT


# region LOADTEST_RUNBOOK
## Runbook нагрузочного тестирования (ПОЛНЫЙ, операционный) — бывш. load-testing.md
## (мигрирован Волной D DevPlan 164, каталог документации удалён)
## Система нагрузочного тестирования платформы: Locust-генератор, 3 режима прогона,
## PromQL-анализ насыщения из существующего Prometheus, baseline-сравнение по датам.
## Единственный CLI-вход — настоящий файл; make-фасад: `make load-test`.
##
## §1. Быстрый старт
##      Установка генератора (load extra — НЕ runtime-зависимость платформы):
##        pip install -e ".[load]"        # или: make venv && .venv/bin/pip install -e ".[load]"
##      Smoke-прогон web-сценария против тестовой VPS (>= 90s, инвариант 10):
##        make load-test SCENARIO=web NODE=test-e2e MODE=smoke
##      Regression (300s, сравнение с previous-прогоном):
##        make load-test SCENARIO=web NODE=test-e2e MODE=regression
##      Capacity (поиск max RPS, автостоп по error>5% | p99>3s):
##        make load-test SCENARIO=llm NODE=test-e2e MODE=capacity
##      Таргет — тонкий фасад: python3 -m core.internal.loadtest.runner_cli
##      --scenario {s} --node {n} --mode {m}.
##
## §2. Exit-коды (контракт shared/contracts.py)
##      | Код | Семантика | Ситуация |
##      | 0   | ok        | PASS и WARN (WARN не блокирует) |
##      | 1   | generic   | вердикт FAIL (regression/capacity), ошибка прогона, недоступный
##      |     | error     | Prometheus, отсутствие locust |
##      | 2   | ConfigNotFound | scenarios.yaml не найден |
##      | 3   | ConfigParse    | битый YAML/JSON (scenarios.yaml, history.json) |
##      | 4   | ConfigValidation| неизвестный сценарий, пустые пороги, rps<=0 (fail-fast) |
##      | 10  | Fatal — ручное вмешательство | capacity на нетестовой ноде без LOAD_ALLOW_PROD=1 |
##
## §3. Архитектура (SoT)
##      core/loadtest/scenarios.yaml              — ЕДИНЫЙ SoT: endpoint, target_rps, users, пороги
##      core/loadtest/scenarios/*.py              — locust-сценарии (web, llm, llm_stream,
##                                                   langfuse_ingest, db*, s3*) — читают env LT_*
##      core/internal/loadtest/config.py          — SoT + env-оверрайды + NODE-резолв + валидация (exit 4)
##      core/internal/loadtest/runner_cli.py      — CLI-оркестратор (этот файл, exit по контракту)
##      core/internal/loadtest/prometheus_pull.py — post-run PromQL saturation (инвариант 5)
##      core/internal/loadtest/report.py          — report.json + markdown + junit
##      core/internal/loadtest/baseline.py        — history.json + регрессионные дельты
##      core/internal/loadtest/capacity.py        — ступенчатый ramp (start×2, max_steps=8, safety-stop)
##      core/internal/loadtest/runner_remote.py   — LOAD_RUNNER=node (docker run на ноде)
##      core/loadtest/history/                    — baseline (КОММИТИТСЯ в репо)
##      load-results/                             — полные отчёты (gitignored целиком)
##      Инварианты: (1) users ≠ rps — точный RPS задаёт constant_throughput через env
##      LT_TARGET_RPS/LT_USERS (сценарии строят wait_time = _rps_wait_time; единый helper
##      core/loadtest/scenarios/__init__.py; BUG-1: CLI-флаг rate-limit в locust отсутствует;
##      users = rps × 2 — запас на latency ≤ 2s); (2) длительности ≥ scrape_interval Prometheus
##      (30s global, 60s cadvisor/node-exporter): smoke ≥ 90s (≥3 сэмпла 30s, ≥2 по 60s);
##      rate-окна запросов ≤ run_time/2 (smoke/capacity → 1m, regression → 2m); метрика с <2
##      сэмплами → insufficient_metrics → WARN (не FAIL); (3) ноль новой мониторинговой
##      инфраструктуры — saturation ТОЛЬКО post-run PromQL pull (порт 9090,
##      LOAD_PROMETHEUS_PORT); (4) LLM-детерминизм — llm/llm_stream гоняются только против
##      mock-модели mock-echo (openai/echo, фикс latency ~50ms); без mock на ноде — ранний
##      FAIL даже при LOAD_ALLOW_PROD=1.
##
## §4. Сценарии (SoT: core/loadtest/scenarios.yaml)
##      | Сценарий        | Описание | По умолчанию |
##      | web             | nginx front: https://{domain}/ + /status | включён |
##      | llm             | POST http://{host}:4000/chat/completions (mock-echo, non-stream) | включён |
##      | llm_stream      | SSE stream=true, chunk-timeout 10s (кастомный клиент) | включён |
##      | langfuse_ingest | POST https://langfuse.{domain}/api/public/traces (Bearer
##      |                  | {LANGFUSE_PUBLIC_KEY}; per-node override — LOAD_ENDPOINT_LANGFUSE_INGEST) | включён |
##      | db              | pg read/write через PG wire protocol (stdlib socket+hmac, без драйверов):
##      |                  | read_query (SELECT count(*)) / write_query (INSERT INTO loadtest_metrics)
##      |                  | вес 1:1 | optional — выключен |
##      | s3              | minio PUT/GET через HTTP API (SigV4 presigned, без boto3) | optional — выключен |
##      Плейсхолдеры: {domain} → node.yaml domain (пустой → host), {host} → node.host,
##      {model} → model сценария, {ANY_VAR} → env (например {LANGFUSE_PUBLIC_KEY} ←
##      LOAD_LANGFUSE_PUBLIC_KEY или LANGFUSE_PUBLIC_KEY; отсутствие → exit 4).
##      Optional-сценарии: включение LOAD_SCENARIO_DB=1 / LOAD_SCENARIO_S3=1 (+ ключи
##      LT_S3_ACCESS_KEY/LT_S3_SECRET_KEY/LT_S3_BUCKET/LT_S3_OBJECT для s3).
##      db: endpoint postgres:5432 — DNS-алиас docker-сети shared-db-net (NO ports:
##      directive); env LT_PG_USER (default postgres), LT_PG_PASSWORD, LT_PG_DB (default
##      platform), LT_PG_TABLE (default loadtest_metrics); прогон ТОЛЬКО с LOAD_RUNNER=node +
##      LOAD_NETWORK=shared-db-net; на старте каждого пользователя — идемпотентная чистая
##      таблица (CREATE TABLE IF NOT EXISTS + DELETE FROM); статистика per-task:
##      read_query/write_query отдельно в отчёте (скорость записи vs чтения).
##
## §5. Режимы и guard-ы
##      | Режим      | Длительность | Критерий вердикта | Применение |
##      | smoke      | 90s (мин) | 0 errors AND p95 < max_p95 → PASS | после деплоя/обновления |
##      | regression | 300s      | p95 ≤ 1.5×prev_p95 AND error ≤ prev+2pp AND p95 < max_p95 | ежемесячно |
##      | capacity   | шаг 60s, max_steps=8 | автостоп (error>5% | p99>3s); max_rps = последний успешный шаг | поиск max нагрузки |
##      Capacity доступен для web, s3, db, llm (capacity_start_rps задан в SoT — иначе exit 4).
##      На тестовой ноде (NODE=test-e2e, contexts[0].name: test) — штатный guard без
##      LOAD_ALLOW_PROD; на production-ноде — только с осознанным LOAD_ALLOW_PROD=1 (exit 10
##      иначе). Env-оверрайды: LOAD_RPS (target_rps; users масштабируются до rps×2),
##      LOAD_DURATION, LOAD_RESULTS_DIR (default load-results/), LOAD_PROMETHEUS_PORT (default
##      9090), LOAD_VERSION (git-sha в отчёте; default "unknown"), LOAD_ENDPOINT_{SCENARIO}
##      (per-scenario endpoint override — escape hatch для нод с нестандартной топологией,
##      напр. LOAD_ENDPOINT_LANGFUSE_INGEST=https://n.example.com), LOAD_NETWORK (docker-сеть
##      контейнера генератора; default из SoT: host для web/s3, shared-db-net для db;
##      allowlist host|shared-db-net — иное → exit 4). Guard-ы: capacity на нетестовой ноде
##      без LOAD_ALLOW_PROD=1 → exit 10 до любой нагрузки; timeout-guard прогона =
##      run_time × 2 + 60s; capacity суммарный = max_steps × (step_duration + 30s) + 120s;
##      preflight: отсутствие locust → exit 1 с инструкцией pip install -e ".[load]".
##
## §6. Saturation-секция (PromQL pull)
##      Post-run query_range в окне [t0-60s, t1+60s], шаг 30s. Пул запросов: CPU/mem
##      контейнеров (cadvisor, label name="nginx" и т.д.), nginx_rps, nginx_conns,
##      pg_backends, redis_ops, redis_clients, litellm_reqs, litellm_err, load1, mem_avail,
##      net_rx. CPU-rate-метрики дополнительно дают pct (avg × 100 — проценты одного ядра).
##      Метрика вне discovery-набора ноды → missing_metrics → WARN; найдена, но <2 сэмплов за
##      окно → insufficient_metrics → WARN; Prometheus недоступен → exit 1. Отключение в
##      dev/e2e: --skip-prometheus (make load-test ... не пробрасывает флаг — прямой CLI).
##
## §7. Baseline и regression
##      core/loadtest/history/{node}/{scenario}/history.json — компактные строки прогонов
##      (коммитится; полные отчёты — в gitignored load-results/). Поле host — детекция
##      пересоздания тестовой VPS (инвариант 9 платформы): смена host → baseline_reset →
##      PASS с пометкой «node recreated», НЕ FAIL. Previous = последний прогон того же режима
##      (smoke-90s vs regression-300s несравнимы). Пороги регрессии из SoT:
##      baseline_delta_p95: 1.5 (×), baseline_delta_error_pp: 2.0 (пп). Первый прогон → PASS +
##      пометка «first run». Регенерация истории — только через прогон. Отдельный снапшот
##      baseline-v1.0.0.json (core/loadtest/history/, точка отсчёта регрессии для релиза
##      v1.0.0, DevPlan 164 W6-1.5) — future-wave артефакт, хранит tree_sha; история на ноде
##      сравнивается с ним (regression-detection). Проверка regression (AC2): два
##      последовательных прогона — второй PASS с delta≈0; искусственный baseline (поднятый
##      prev_p95) → FAIL, exit 1.
##
## §8. Remote-режим (LOAD_RUNNER=node)
##      Генератор выполняется в docker-контейнере НА НОДЕ (слабый канал dev-машины):
##        LOAD_RUNNER=node make load-test SCENARIO=web NODE=test-e2e MODE=smoke
##      Механика: rsync core/loadtest/ → /tmp/loadtest-{ts}/ (SSH через канон shared.ssh_opts)
##      → docker run --rm --network {net} --cpus ${LOAD_CPUS:-2} -v /tmp/loadtest-{ts}:/lt -w
##      /lt ${LOAD_IMAGE:-locustio/locust:2.32.10} -f ... --headless → rsync CSV обратно;
##      PromQL-pull и отчёт — локально. Генератор ВНЕ стека (не compose-сервис, не
##      observability-net); LOAD_IMAGE — ghcr.io-зеркало при Docker Hub rate-limit (StatusReport
##      045); boto3 в locust-образе отсутствует — s3 через HTTP API minio (SigV4). --network:
##      host (default) — web/s3 на host-сети; shared-db-net — db (PostgreSQL публикуется
##      ТОЛЬКО в docker-сеть, контейнер достаёт postgres:5432 по DNS-алиасу):
##        LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net \
##        LT_PG_USER=postgres LT_PG_PASSWORD={secret} LT_PG_DB=platform \
##        make load-test SCENARIO=db NODE=test-e2e MODE=smoke
##
## §9. Mock-модель litellm (установка на тестовую ноду)
##      Сценарии llm/llm_stream требуют модель mock-echo (детерминизм AC6).
##      core/modules/litellm/config/litellm-config.mock.yml — отдельный конфиг (НЕ policy.yaml
##      — инвариант «providers: только DeepSeek»; НЕ litellm-config.test.yml):
##        # 1. Забрать конфиг с dev-машины на ноду (копируется в /opt/platform/core):
##        scp core/modules/litellm/config/litellm-config.mock.yml root@[node]:/opt/platform/core/modules/litellm/config/
##        # 2. Заменить монтируемый конфиг litellm (на ноде):
##        ssh root@[node] "cp /opt/platform/core/modules/litellm/config/litellm-config.mock.yml \
##          /opt/platform/core/modules/litellm/config/litellm-config.yml"
##        # 3. Перезапустить litellm:
##        make restart MODULES=litellm
##      Верификация: первый smoke-прогон llm (mock-probe POST до генерации, _probe_mock_model).
##      Если версия litellm на ноде отклоняет openai/echo — fallback model: "echo" в
##      mock-конфиге. Прод-конфиг не затрагивается; на проде mock-модель отсутствует →
##      ранний FAIL (даже при LOAD_ALLOW_PROD=1).
##
## §10. Отчёт и интерпретация
##      load-results/{node}/{scenario}/{mode}/{ts}/ (gitignored): report.json
##      (машиночитаемый), report.md (сводка в stdout), junit.xml (опция --junit для CI).
##      Ключевые поля: verdict (PASS/WARN/FAIL), stats (rps, p50/p95/p99, error_rate),
##      duration_s (t1−t0 прогона), tasks (per-task breakdown: {name: {rps, p95, p99,
##      error_rate}} — для db отдельно read_query/write_query), saturation (avg/max/pct по
##      метрикам), missing_metrics/insufficient_metrics (WARN-причины), baseline (prev,
##      delta_p95, delta_error_pp, first_run, baseline_reset), capacity_profile (шаги).
##      history.json (smoke/regression) дополнительно хранит duration_s и tasks — источник
##      сводной статистики по волнам. Интерпретация: PASS при нуле ошибок и p95 под порогом;
##      WARN = PASS + диагностика метрик (exit 0); FAIL = ошибки/пороги/регрессия (exit 1);
##      saturation — пиковые (max) и средние (avg) за окно; pct CPU — доля одного ядра.
##
## §11. Ограничения
##      db-сценарий: PostgreSQL публикуется ТОЛЬКО в docker-сеть shared-db-net → прогон только
##      node-runner'ом (LOAD_RUNNER=node + LOAD_NETWORK=shared-db-net); локальный dev-запуск без
##      SSH-туннеля к docker-сети невозможен (предупреждение, не блокирует); transport —
##      чистый stdlib PG wire protocol (pgwire.py): auth SCRAM-SHA-256 + md5 (по коду сервера),
##      cleartext (код 3) отклоняется. s3: presigned SigV4 через stdlib — без boto3 (ограничение
##      locust-образа). e2e-тест (make test-node NODE={test}) требует деплоя nginx на ноде и
##      locust в окружении; PromQL-pull в e2e отключается (--skip-prometheus) — saturation на
##      ноде проверяется ручным AC1-прогоном.
##
## §12. Юнит-тесты
##      tests/unit/test_loadtest_config.py (парсинг/валидация/NODE-резолв/endpoint-override),
##      test_loadtest_runner.py (build locust-argv без rate-limit флага + env LT_TARGET_RPS +
##      helper _rps_wait_time), test_loadtest_prometheus_pull.py (PromQL/discovery/insufficient),
##      test_loadtest_report.py (CSV/verdict/артефакты), test_loadtest_baseline.py
##      (history/host-reset/пороги), test_loadtest_capacity.py (детерминированная симуляция),
##      tests/e2e/test_load_test.py (smoke web на VPS, requires_node).
##      Запуск: make check TEST_FILE=tests/unit/test_loadtest_*.py.
## @links    core/loadtest/scenarios.yaml (SoT сценариев), core/internal/loadtest/config.py,
##           core/internal/loadtest/prometheus_pull.py, core/internal/loadtest/baseline.py,
##           core/internal/loadtest/runner_remote.py, core/modules/litellm/config/litellm-config.mock.yml
# endregion LOADTEST_RUNBOOK

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from core.internal.loadtest import baseline as baseline_mod
from core.internal.loadtest import capacity as capacity_mod
from core.internal.loadtest import prometheus_pull, report, runner_remote
from core.internal.loadtest.baseline import HistoryRun
from core.internal.loadtest.config import ENV_ALLOW_PROD, ENV_DURATION, LoadtestConfig, load_config, render_dict
from core.internal.loadtest.report import CapacityStep, ReportJson, StepStats, TaskStats
from core.internal.shared import http_client  # W3.2 (177): HTTP-слой консолидирован в shared/http_client.py
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)

# W1-A1 (план 170): MOCK_PROBE_TIMEOUT=10 (дубль SoT) → DOCKER_CMD_TIMEOUT (10) — mock-probe
# llm-эндпоинта использует каноническое 10s окно коротких команд.
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = "scenarios"
MOCK_PROBE_SCENARIOS = ("llm", "llm_stream")


# W4a (DevPlan 160 T4.1): import-time env-чтение SSH_USER убрано — ленивый резолв на вызове
# (тот же env-канон SSH_USER → "root"; значения из AppConfig.from_env().ssh_user).
HTTP_ERROR_MIN: int = 400  # HTTP-статусы >= 400 = ошибка


def _ssh_user() -> str:
    """Ленивый резолв SSH-пользователя (SSH_USER → root, call-time, не import-time)."""
    return os.environ.get("SSH_USER", "root")


# region DATA_LoadtestRunError
class LoadtestRunError(Exception):
    """Ошибка прогона (locust нет/упал, Prometheus недоступен, mock-probe fail) — exit 1."""


# endregion DATA_LoadtestRunError


# region DATA_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Замена голого Namespace (динамические атрибуты → Any). Значения НЕ
    ##            задаются class-атрибутами — hasattr(namespace, dest) перебивает
    ##            parser-дефолты (argparse пропускает setattr при существующем атрибуте);
    ##            поля заполняет parse_args(namespace=CliArgs()).
    ## @invariants
    ##   - scenario/node — обязательные позиционные (argparse required=True)
    ##   - platform_root: str | None (default None — env PLATFORM_ROOT в load_config)
    """

    scenario: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    node: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    mode: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    junit: bool  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    skip_prometheus: bool  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    skip_baseline: bool  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    platform_root: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


# endregion DATA_CliArgs


# region FUNC__parse_args
def _parse_args(argv: list[str] | None) -> CliArgs:
    """Аргументы CLI: --scenario --node --mode [--junit --skip-prometheus --skip-baseline --platform-root].

    ▶ ┌argv┐ → ○ argparse → ⎋ Namespace

    ## @purpose  Интерфейс CLI (make load-test SCENARIO/NODE/MODE пробрасывает сюда).
    ## @io — ⇥ argv: list[str] | None → ⎋ argparse.Namespace
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m core.internal.loadtest.runner_cli",
        description="Load-test runner (DevPlan 146) — locust + PromQL saturation + baseline",
    )
    parser.add_argument("--scenario", required=True, help="Scenario name from core/loadtest/scenarios.yaml")
    parser.add_argument("--node", required=True, help="Node name (node-configs/<node>/node.yaml)")
    parser.add_argument(
        "--mode",
        choices=("smoke", "regression", "capacity"),
        default="smoke",
        help="Run mode (default: smoke)",
    )
    parser.add_argument("--junit", action="store_true", help="Write junit.xml next to report.json")
    parser.add_argument("--skip-prometheus", action="store_true", help="Skip PromQL saturation pull (dev)")
    parser.add_argument("--skip-baseline", action="store_true", help="Skip baseline history write/compare (dev)")
    parser.add_argument("--platform-root", default=None, help="NodeYaml config_dir (default: env PLATFORM_ROOT)")
    return parser.parse_args(argv, namespace=CliArgs())


# endregion FUNC__parse_args


# region FUNC__preflight_locust
def _preflight_locust() -> None:
    """Preflight: locust установлен? (отсутствие → exit 1 с инструкцией, guard-таблица §3.7).

    ▶ ┌—┐ → ○ importlib.util.find_spec("locust") → ◇ None → LoadtestRunError(pip install) → ⎋ None

    ## @purpose  Fail-fast до любого прогона: «отсутствие locust → exit 1» (DevPlan 146 §3.7).
    ##            Lazy-проверка (не импорт) — модуль не зависит от locust при импорте.
    ## @io — ⇥ None → ⎋ None | LoadtestRunError
    ## @complexity — O(1)
    ## @raises — LoadtestRunError: locust не установлен (инструкция pip install -e ".[load]")
    """
    if importlib.util.find_spec("locust") is None:
        msg = (
            'locust не установлен — выполните: pip install -e ".[load]" '
            '(или: make venv && .venv/bin/pip install -e ".[load]")'
        )
        raise LoadtestRunError(msg)
    logger.info("[IMP:8][runner][preflight] locust found")


# endregion FUNC__preflight_locust


# region FUNC__probe_mock_model
def _probe_mock_model(config: LoadtestConfig) -> None:
    """Mock-probe для llm/llm_stream: POST /chat/completions ДО прогона (AC6, ранний FAIL).

    ▶ ┌config┐ → ◇ сценарий не llm-семейства → return → ○ POST endpoint+path (body из SoT)
      → ◇ HTTP >= 400 / сеть → LoadtestRunError (установите litellm-config.mock.yml) → ⎋ None

    ## @purpose  AC6 DevPlan 146: «llm/llm-stream используют mock-echo; без mock на prod →
    ##            ранний FAIL с сообщением». Guard работает и при LOAD_ALLOW_PROD=1 —
    ##            mock-модели на проде нет по построению (инвариант 8).
    ## @io — ⇥ config: LoadtestConfig → ⎋ None | LoadtestRunError (exit 1)
    ## @complexity — O(1) — один HTTP-запрос (timeout 10s)
    ## @raises — LoadtestRunError: 4xx/5xx (модель не найдена) или endpoint недоступен
    """
    if config.scenario.name not in MOCK_PROBE_SCENARIOS:
        return
    path = config.scenario.path or "/chat/completions"
    body = render_dict(config.scenario.body_template, config.scenario, config.node_host, config.platform_domain) or {}
    headers = render_dict(config.scenario.headers, config.scenario, config.node_host, config.platform_domain) or {}
    url = config.endpoint.rstrip("/") + "/" + path.lstrip("/")
    try:
        # W3.2 (177): POST-JSON-хелпер shared/http_client (сериализация тела + Content-Type);
        # HTTPError/URLError пробрасываются как есть (детали HTTPError читаются ниже).
        resp = http_client.post_json(url, body, timeout=DOCKER_CMD_TIMEOUT, headers=headers)
        with resp:
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace") if exc.fp else ""
        msg = (
            f"mock-модель не обнаружена (HTTP {exc.code}): {detail.strip()[:300]} — "
            "установите litellm-config.mock.yml на ноду (runbook LOADTEST_RUNBOOK §9 ниже) и перезапустите litellm"
        )
        raise LoadtestRunError(msg) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        msg = f"litellm недоступен для mock-probe ({config.endpoint}): {exc} — проверьте деплой litellm на ноде"
        raise LoadtestRunError(msg) from exc
    if status >= HTTP_ERROR_MIN:
        msg = f"mock-модель не обнаружена (HTTP {status}) — установите litellm-config.mock.yml на ноду"
        raise LoadtestRunError(msg)
    logger.info("[IMP:9][runner][mock_probe] mock-модель подтверждена: HTTP %s на %s", status, url)


# endregion FUNC__probe_mock_model


# region FUNC__locust_env
def _locust_env(
    config: LoadtestConfig,
    rps: int | None = None,
    users: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Сборка env LT_* для locust (единый builder: local subprocess | remote -e).

    ▶ ┌config, rps?, users?┐ → ○ LT_ENDPOINT/SSL/PATHS/METHOD/STREAM → ⊕ LT_PATH/LT_MODEL/
      LT_HEADERS/LT_BODY (rendered) → ⊕ LT_TARGET_RPS/LT_USERS (RPS-контроль) →
      ⊕ passthrough LT_* (env override поверх spec: LT_S3_*/LT_PG_*/LT_LANGFUSE_*/
      LT_CHUNK_TIMEOUT и пр.) → ⎋ dict[str, str]

    ## @purpose  Инвариант 7 + 2 (DevPlan 146): locust-файлы читают ВСЁ из env; значения
    ##            заполняет config.py из SoT (никаких хардкодов в .py сценариях).
    ##            RPS-контроль (146-m1 BUG-1): LT_TARGET_RPS/LT_USERS → сценарии строят
    ##            wait_time = constant_throughput(target/users) через rps_wait_time.
    ##            rps/users параметры — per-step override (capacity: шаг = свой RPS);
    ##            по умолчанию — spec.target_rps/spec.users (smoke/regression).
    ## @io — ⇥ config: LoadtestConfig, rps: int | None (per-step override, capacity),
    ##         users: int | None (per-step пул, capacity)
    ##       → ⎋ dict[str, str] (готов к subprocess env / docker -e)
    ## @complexity — O(K) — K = полей сценария
    """
    spec = config.scenario
    target_rps = rps if rps is not None else spec.target_rps
    pool_users = users if users is not None else spec.users
    env: dict[str, str] = {
        "LT_ENDPOINT": config.endpoint,
        "LT_SSL_VERIFY": "true" if spec.ssl_verify else "false",
        "LT_ENABLED": "true",
        "LT_METHOD": spec.method,
        "LT_PATHS": json.dumps(list(spec.paths)),
        "LT_STREAM": "true" if spec.stream else "false",
        "LT_CHUNK_TIMEOUT": str(spec.chunk_timeout),
        "LT_TARGET_RPS": str(target_rps),
        "LT_USERS": str(pool_users),
    }
    if spec.path:
        env["LT_PATH"] = spec.path
    if spec.model:
        env["LT_MODEL"] = spec.model
    headers = render_dict(spec.headers, spec, config.node_host, config.platform_domain) or {}
    if headers:
        env["LT_HEADERS"] = json.dumps(headers)
    body = render_dict(spec.body_template, spec, config.node_host, config.platform_domain)
    if body:
        env["LT_BODY"] = json.dumps(body)
    env.update({
        key: value for key, value in (os.environ if environ is None else environ).items() if key.startswith("LT_")
    })
    logger.info(
        "[IMP:8][runner][env] LT_* env built: %d vars (target_rps=%s users=%s)", len(env), target_rps, pool_users
    )
    return env


# endregion FUNC__locust_env


# region FUNC__build_locust_args
def _build_locust_args(scenario_file: str, users: int, duration: int, csv_prefix: str) -> list[str]:
    """Сборка locust-argv: headless, users, spawn-rate, run-time, csv.

    ▶ ┌scenario_file, users, duration, csv_prefix┐ → ⎋ ["-f", ..., "--headless", "-u", ...]

    ## @purpose  Единственная точка сборки locust-команды (инвариант 1). RPS-контроль
    ##            НЕ в argv — флаг rate-limit отсутствует в locust 2.x (146-m1 BUG-1);
    ##            RPS передаётся env-ом LT_TARGET_RPS/LT_USERS через _locust_env,
    ##            сценарии строят wait_time = constant_throughput (helper rps_wait_time).
    ##            users — размер пула; spawn-rate = users.
    ## @io — ⇥ scenario_file: str, users: int, duration: int (s),
    ##         csv_prefix: str (базовый путь CSV) → ⎋ list[str]
    ## @complexity — O(1)
    """
    return [
        "-f",
        scenario_file,
        "--headless",
        "-u",
        str(users),
        "-r",
        str(users),
        "--run-time",
        f"{duration}s",
        "--csv",
        csv_prefix,
        "--csv-full-history",
    ]


# endregion FUNC__build_locust_args


# region FUNC__run_locust_process
def _run_locust_process(cmd: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    """subprocess locust с env-инъекцией (local-режим).

    ▶ ┌cmd, env, timeout┐ → ○ subprocess.run(capture, text, env={**os.environ, **env}, timeout)
      → ◇ TimeoutExpired → LoadtestRunError → ◇ FileNotFound → LoadtestRunError → ⎋ CompletedProcess

    ## @purpose  Локальный запуск генератора: env LT_* поверх окружения процесса.
    ##            timeout-guard = run_time × 2 + 60s (инвариант 3).
    ## @io — ⇥ cmd: list[str], env: dict[str, str], timeout: int → ⎋ CompletedProcess
    ## @complexity — O(RT) — RT = run_time
    ## @raises — LoadtestRunError: таймаут (124) / locust отсутствует
    """
    merged = {**os.environ, **env}
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=merged, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"locust-прогон превысил timeout-guard {timeout}s (run_time × 2 + 60) — "
            "проверьте зависшие запросы/сеть до ноды"
        )
        raise LoadtestRunError(msg) from exc
    except FileNotFoundError as exc:
        msg = 'locust не найден в PATH — установите: pip install -e ".[load]"'
        raise LoadtestRunError(msg) from exc


# endregion FUNC__run_locust_process


# region FUNC__run_one_step
def _run_one_step(
    config: LoadtestConfig,
    rps: int,
    users: int,
    duration: int,
    csv_prefix: str,
    remote: ModuleType | None,  # модуль runner_remote (helpers remote-запуска; LOAD_RUNNER=node)
    parse_prefix: str | None = None,
) -> StepStats:
    """Один headless-прогон (шаг smoke/regression/capacity) → метрики | {"error": ...}.

    ▶ ┌config, rps, users, duration, csv_prefix, remote, parse_prefix┐ → ◇ remote →
      ○ run_remote_locust → ○ fetch (ДО парсинга) | → _run_locust_process → ◇ rc != 0
      → {"error"} → ○ parse_stats_csv(parse_prefix) → ⎋ stats-dict

    ## @purpose  Единая точка исполнения шага (local | remote). Возвращает dict
    ##            capacity-контракта step_runner: {"rps","p95","p99","error_rate"} | {"error"}.
    ##            Remote (BUG-8, 146-m8): csv_prefix — контейнерный путь (argv locust),
    ##            parse_prefix — ЛОКАЛЬНЫЙ путь CSV; fetch результатов выполняется ДО
    ##            парсинга (иначе parse_stats_csv читает /lt/results/... на локальной
    ##            машине → CSV не найден → нулевые Stats → ложный FAIL).
    ##            tasks (148 TASK-7): per-task breakdown из parse_stats_csv (read_query/
    ##            write_query для db — скорость записи vs чтения, SC_DB_RW).
    ## @io — ⇥ config, rps: int, users: int, duration: int (s), csv_prefix: str,
    ##         remote: runner_remote-модуль (не None при LOAD_RUNNER=node),
    ##         parse_prefix: str | None (локальный CSV-prefix; default = csv_prefix)
    ##       → ⎋ dict — метрики шага (включая "tasks") или {"error": str}
    ## @complexity — O(RT) — RT = duration + guard
    """
    scenario_file = (
        f"/lt/{SCENARIOS_DIR}/{config.scenario.name}.py"
        if remote
        else str(REPO_ROOT / "core" / "loadtest" / SCENARIOS_DIR / f"{config.scenario.name}.py")
    )
    args = _build_locust_args(scenario_file, users, duration, csv_prefix)
    env = _locust_env(config, rps=rps, users=users)
    timeout = duration * 2 + 60
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if remote:
            # W11: вызовы через импортированный runner_remote (remote-параметр — только
            # ветвление local/node; ModuleType-атрибуты → Any → reportAny)
            runner_remote.run_remote_locust(
                config.node_host,
                _ssh_user(),
                config.image,
                config.cpus,
                _remote_workdir(config),
                env,
                args,
                timeout=timeout,
                network=config.network,  # 148 TASK-4/5: docker-сеть генератора (host | shared-db-net)
            )
            # BUG-8: fetch ДО парсинга — CSV пишется в контейнерный /lt/results,
            # локально доступен только после rsync-обратного забора.
            runner_remote.fetch(
                config.node_host,
                _ssh_user(),
                f"{_remote_workdir(config)}/results",
                str(config.results_dir),
            )
            result = _run_locust_process(["locust", *args], env, timeout=timeout)
            if result.returncode != 0:
                tail = result.stdout.strip()[-1500:] if result.stdout.strip() else result.stderr.strip()[-1500:]
                return {"error": f"locust rc={result.returncode}: {tail}"}
        else:
            # ⚠️ TRAP[BUG] · 2026-08-14 · P1 · Локальный запуск locust отсутствовал после BUG-8
            # · Symptom: load-test (local) → "CSV не найден: .../run_stats.csv" → ложный FAIL
            # ·   (baseline W4-3 169); locust никогда не запускался в local-режиме.
            # · Root: регрессия BUG-8 (1dd928ad6): при переносе remote.fetch ДО парсинга ветка
            # ·   `else: _run_locust_process(...)` была потеряна — вызов остался внутри `if remote:`
            # · Fix: восстановлена else-ветка локального запуска (паритет с 6c7f6925a original)
            # · Prevention: unit-тест на local-режим _run_one_step (tests/unit/test_loadtest_runner.py)
            result = _run_locust_process(["locust", *args], env, timeout=timeout)
            if result.returncode != 0:
                tail = result.stdout.strip()[-1500:] if result.stdout.strip() else result.stderr.strip()[-1500:]
                return {"error": f"locust rc={result.returncode}: {tail}"}
    except runner_remote.RemoteError as exc:
        return {"error": str(exc)}

    stats, tasks = report.parse_stats_csv(f"{parse_prefix if parse_prefix is not None else csv_prefix}_stats.csv")
    logger.info(
        "[IMP:9][runner][step] rps=%s p95=%s p99=%s error_rate=%s (requests=%d, tasks=%s)",
        stats.rps,
        stats.p95,
        stats.p99,
        stats.error_rate,
        stats.total_requests,
        sorted(tasks),
    )
    return {
        "rps": stats.rps,
        "p95": stats.p95,
        "p99": stats.p99,
        "error_rate": stats.error_rate,
        "total_requests": stats.total_requests,
        "total_failures": stats.total_failures,
        "tasks": tasks,
    }


# endregion FUNC__run_one_step


# region FUNC__remote_workdir
def _remote_workdir(config: LoadtestConfig) -> str:
    """Remote-рабочая директория прогона: /tmp/loadtest-<ts> (уникальная).

    ▶ ┌config┐ → ◇ runner_remote.make_remote_workdir → ⎋ str

    ## @purpose  Изоляция прогонов на ноде; мемоизация в config-атрибуте (один ship/fetch
    ##            на режим — все шаги capacity в одной директории).
    ## @io — ⇥ config: LoadtestConfig → ⎋ str
    ## @complexity — O(1)
    """
    cached = config.remote_workdir
    if cached is None:
        cached = runner_remote.make_remote_workdir()
        # ruff: ignore[unnecessary-dunder-call] — frozen dataclass: setattr()/прямая запись
        # → FrozenInstanceError; object.__setattr__ — единственный способ мемоизации
        object.__setattr__(config, "remote_workdir", cached)
    return cached


# endregion FUNC__remote_workdir


# region FUNC__saturation_pull
def _saturation_pull(config: LoadtestConfig, t0: float, t1: float) -> prometheus_pull.SaturationResult:
    """PromQL-saturation (post-run, локальная машина → Prometheus ноды).

    ▶ ┌config, t0, t1┐ → ○ run_saturation(base=prometheus_host:LOAD_PROMETHEUS_PORT,
      окно [t0-60, t1+60]) → ⎋ SaturationResult | LoadtestRunError

    ## @purpose  Инвариант 5: saturation — ТОЛЬКО post-run pull из существующего Prometheus.
    ##            Недоступный Prometheus → LoadtestRunError (exit 1, guard-таблица §3.7).
    ##            host = config.prometheus_host (146-m2): LOAD_PROMETHEUS_HOST override —
    ##            например localhost при SSH-туннеле (ssh -L 19090:localhost:9090), когда
    ##            внешний IP ноды принимает TCP на 9090, но HTTP не отвечает (фаервол ноды).
    ## @io — ⇥ config, t0/t1: float (unix) → ⎋ SaturationResult
    ## @complexity — O(Q×(S+M)) — пул запросов
    ## @raises — LoadtestRunError: Prometheus недоступен (через PrometheusError)
    """
    base_url = f"http://{config.prometheus_host}:{config.prometheus_port}"
    run_time = int(max(1, t1 - t0))
    try:
        return prometheus_pull.run_saturation(base_url, run_time, t0, t1)
    except prometheus_pull.PrometheusError as exc:
        msg = f"Prometheus pull failed: {exc}"
        raise LoadtestRunError(msg) from exc


# endregion FUNC__saturation_pull


# region FUNC__run_single_mode
def _run_single_mode(config: LoadtestConfig, args: CliArgs) -> tuple[int, ReportJson]:
    """Режимы smoke/regression: один прогон → saturation → baseline → отчёт → exit.

    ▶ ┌config, args┐ → ○ duration = regression.run_time | smoke.smoke_duration (LOAD_DURATION override)
      → ○ t0 → step → t1 → ○ saturation (skip-флаг) → ○ baseline compare → ○ report+verdict
      → ○ append history → ○ write report.json/md/junit → ⎋ (exit_code, report)

    ## @purpose  Сквозной поток smoke/regression (DevPlan 146 Data Flow, шаги 1-7).
    ##            Вердикт: smoke — 0 errors + p95<max_p95; regression — дельты baseline.
    ## @io — ⇥ config: LoadtestConfig, args → ⎋ (exit_code: int, report: dict)
    ## @complexity — O(RT + Q×S) — прогон + pull
    """
    spec = config.scenario
    duration = spec.run_time if config.mode == "regression" else spec.smoke_duration
    env_duration = os.environ.get(ENV_DURATION, "").strip()
    if env_duration:
        duration = int(env_duration)

    remote = runner_remote if config.load_runner == "node" else None
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rel_dir = Path(config.node_name) / spec.name / config.mode / ts
    local_dir = config.results_dir / rel_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    if remote:
        runner_remote.ship(config.node_host, _ssh_user(), str(REPO_ROOT / "core" / "loadtest"), _remote_workdir(config))

    t0 = time.time()
    csv_prefix = str(local_dir / "run") if not remote else f"/lt/results/{rel_dir}/run"
    step_stats = _run_one_step(
        config, spec.target_rps, spec.users, duration, csv_prefix, remote, str(local_dir / "run")
    )
    t1 = time.time()
    duration_s = round(t1 - t0, 1)  # 148 TASK-7: «что сколько времени выполняется» (SC_STATS)
    if "error" in step_stats:
        raise LoadtestRunError(step_stats["error"])

    stats = report.Stats(
        rps=step_stats.get("rps"),
        p95=step_stats.get("p95"),
        p99=step_stats.get("p99"),
        error_rate=float(step_stats.get("error_rate") or 0.0),
        total_requests=int(step_stats.get("total_requests") or 0),
        total_failures=int(step_stats.get("total_failures") or 0),
    )

    warnings: list[str] = []
    saturation: prometheus_pull.SaturationResult | None = None
    if not args.skip_prometheus:
        saturation = _saturation_pull(config, t0, t1)
        warnings += [f"метрика отсутствует на ноде: {m}" for m in saturation.missing_metrics]
        warnings += [f"недостаточно сэмплов (<2): {m}" for m in saturation.insufficient_metrics]

    comparison = baseline_mod.BaselineComparison(first_run=True)
    if not args.skip_baseline:
        runs = baseline_mod.load_history(config.history_dir)
        comparison = baseline_mod.compare_previous(
            runs,
            config.mode,
            {"p95": stats.p95, "error_rate": stats.error_rate},
            host=config.node_host,
            delta_p95_mult=spec.baseline_delta_p95,
            delta_error_pp=spec.baseline_delta_error_pp,
        )

    if config.mode == "regression":
        verdict = report.verdict_regression(stats, spec.max_p95, comparison)
    else:
        verdict = report.verdict_smoke(stats, spec.max_p95)
    verdict = report.apply_warnings(verdict, warnings)

    delta_vs_prev = None
    if comparison.delta_p95 is not None or comparison.delta_error_pp is not None:
        delta_vs_prev = {"delta_p95": comparison.delta_p95, "delta_error_pp": comparison.delta_error_pp}

    built = report.build_report(
        scenario=spec.name,
        mode=config.mode,
        node=config.node_name,
        endpoint=config.endpoint,
        version=config.version,
        stats=stats,
        saturation_aggregates=saturation.aggregates if saturation else None,
        missing_metrics=saturation.missing_metrics if saturation else None,
        insufficient_metrics=saturation.insufficient_metrics if saturation else None,
        baseline=comparison,
        duration_s=duration_s,
        tasks=step_stats.get("tasks"),
        verdict=verdict,
        warnings=warnings,
        timestamp=ts,
    )

    if not args.skip_baseline:
        run_row: HistoryRun = {
            "ts": ts,
            "host": config.node_host,
            "mode": config.mode,
            "duration_s": duration_s,
            "tasks": step_stats.get("tasks"),
            "rps": stats.rps,
            "p50": stats.p50,
            "p95": stats.p95,
            "p99": stats.p99,
            "error_rate": stats.error_rate,
            "max_rps": None,
            "verdict": verdict,
            "delta_vs_prev": delta_vs_prev,
            "version": config.version,
        }
        baseline_mod.append_run(config.history_dir, run_row)

    _write_outputs(built, local_dir, args.junit)
    return (0 if verdict in {report.VERDICT_PASS, report.VERDICT_WARN} else 1), built


# endregion FUNC__run_single_mode


# region FUNC__run_capacity_mode
def _run_capacity_mode(config: LoadtestConfig, args: CliArgs) -> tuple[int, ReportJson]:
    """Режим capacity: профиль шагов (start×2, max_steps=8) со safety-stop → отчёт → exit.

    ▶ ┌config, args┐ → ○ guard (уже в main) → ○ t0 → ○ run_capacity(step_runner=1 шаг) →
      → ○ t1 → ○ saturation → ○ report (capacity_profile, max_rps) → ○ history append → ⎋ (exit, report)

    ## @purpose  Сквозной поток capacity (DevPlan 146 §3.3): последовательные headless-
    ##            прогоны по шагу, LT_TARGET_RPS=<step> (constant_throughput per-user через
    ##            _locust_env, 146-m1 BUG-1), users = step×2, стабилизация 60s/шаг,
    ##            safety-stop (error>5% | p99>3s), max_rps = последний успешный шаг.
    ## @io — ⇥ config, args → ⎋ (exit_code: int, report: dict)
    ## @complexity — O(S×RT + Q×S') — S шагов + pull
    """
    spec = config.scenario
    duration = spec.capacity_step_duration
    env_duration = os.environ.get(ENV_DURATION, "").strip()
    if env_duration:
        duration = int(env_duration)

    remote = runner_remote if config.load_runner == "node" else None
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rel_dir = Path(config.node_name) / spec.name / config.mode / ts
    local_dir = config.results_dir / rel_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    if remote:
        runner_remote.ship(config.node_host, _ssh_user(), str(REPO_ROOT / "core" / "loadtest"), _remote_workdir(config))

    def _step(rps: int) -> StepStats:
        csv_prefix = f"/lt/results/{rel_dir}/step-{rps}/run" if remote else str(local_dir / f"step-{rps}" / "run")
        raw = _run_one_step(config, rps, rps * 2, duration, csv_prefix, remote, str(local_dir / f"step-{rps}" / "run"))
        step_tasks[rps] = raw.get("tasks") or {}
        return raw

    step_tasks: dict[int, dict[str, TaskStats]] = {}
    t0 = time.time()
    result = capacity_mod.run_capacity(
        _step,
        start_rps=int(spec.capacity_start_rps or 0),
        max_error=spec.max_error,
        max_p99=spec.max_p99,
    )
    t1 = time.time()
    duration_s = round(t1 - t0, 1)  # 148 TASK-7: суммарная длительность capacity-профиля

    profile_rows: list[CapacityStep] = [
        {
            "step": step.step,
            "rps": step.rps,
            "p95": step.p95,
            "p99": step.p99,
            "error_rate": step.error_rate,
            "success": step.success,
            "reason": step.reason,
        }
        for step in result.profile
    ]
    warnings: list[str] = []
    saturation: prometheus_pull.SaturationResult | None = None
    if not args.skip_prometheus:
        saturation = _saturation_pull(config, t0, t1)
        warnings += [f"метрика отсутствует на ноде: {m}" for m in saturation.missing_metrics]
        warnings += [f"недостаточно сэмплов (<2): {m}" for m in saturation.insufficient_metrics]

    verdict = report.apply_warnings(report.verdict_capacity(result.max_rps), warnings)
    if result.max_rps == 0:
        warnings.append("ни один шаг capacity не успешен (safety-stop на первом шаге)")

    last_ok = next((s for s in reversed(result.profile) if s.success), None)
    stats = report.Stats(
        rps=last_ok.rps if last_ok else None,
        p95=last_ok.p95 if last_ok else None,
        p99=last_ok.p99 if last_ok else None,
        error_rate=float(last_ok.error_rate or 0.0) if last_ok else 0.0,
    )
    last_ok_tasks = step_tasks.get(last_ok.step) if last_ok else None  # 148 TASK-7: per-task на max-нагрузке

    comparison = baseline_mod.BaselineComparison(first_run=True)
    if not args.skip_baseline:
        runs = baseline_mod.load_history(config.history_dir)
        comparison = baseline_mod.compare_previous(
            runs,
            config.mode,
            {"p95": stats.p95, "error_rate": stats.error_rate},
            host=config.node_host,
            delta_p95_mult=spec.baseline_delta_p95,
            delta_error_pp=spec.baseline_delta_error_pp,
        )

    built = report.build_report(
        scenario=spec.name,
        mode=config.mode,
        node=config.node_name,
        endpoint=config.endpoint,
        version=config.version,
        stats=stats,
        saturation_aggregates=saturation.aggregates if saturation else None,
        missing_metrics=saturation.missing_metrics if saturation else None,
        insufficient_metrics=saturation.insufficient_metrics if saturation else None,
        baseline=comparison,
        max_rps=result.max_rps,
        capacity_profile=profile_rows,
        duration_s=duration_s,
        tasks=last_ok_tasks,
        verdict=verdict,
        warnings=warnings,
        timestamp=ts,
    )

    if not args.skip_baseline:
        run_row: HistoryRun = {
            "ts": ts,
            "host": config.node_host,
            "mode": config.mode,
            "duration_s": duration_s,
            "rps": stats.rps,
            "p50": stats.p50,
            "p95": stats.p95,
            "p99": stats.p99,
            "error_rate": stats.error_rate,
            "max_rps": result.max_rps,
            "verdict": verdict,
            "delta_vs_prev": None,
            "version": config.version,
        }
        baseline_mod.append_run(config.history_dir, run_row)

    _write_outputs(built, local_dir, args.junit)
    return (0 if verdict in {report.VERDICT_PASS, report.VERDICT_WARN} else 1), built


# endregion FUNC__run_capacity_mode


# region FUNC__write_outputs
def _write_outputs(built: ReportJson, report_dir: Path, junit: bool) -> None:
    """Запись отчётов: report.json + report.md (atomic) + junit.xml (опция).

    ▶ ┌built, report_dir, junit┐ → ○ write_report_json → ○ write markdown → ◇ junit → write_junit_xml → ⎋

    ## @purpose  Единая точка персистенции отчёта (инвариант 6: полные отчёты — в
    ##            gitignored load-results/; history.json — в core/loadtest/history/).
    ## @io — ⇥ built: dict, report_dir: Path, junit: bool → ⎋ None
    ## @complexity — O(R) — сериализация
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    report.write_report_json(built, report_dir / "report.json")
    md_path = report_dir / "report.md"
    md_path.write_text(report.render_markdown(built), encoding="utf-8")
    if junit:
        report.write_junit_xml(built, report_dir / "junit.xml")
    print(report.render_markdown(built))
    print(f"[LOADTEST] report.json: {report_dir / 'report.json'}")


# endregion FUNC__write_outputs


# region FUNC_main
# region FUNC_raise_capacity_prod_guard
## @purpose  Извлечённый raise из try-тела main (TRY301): capacity-guard на проде.
## @io       ⇥ config → ⎋ NoReturn
## @complexity O(1)
def _raise_capacity_prod_guard(config: LoadtestConfig) -> None:
    """Raise PlatformFatalError when capacity runs on a non-test node without LOAD_ALLOW_PROD."""
    msg = (
        f"capacity на нетестовой ноде ({config.node_name}) запрещён — "
        f"задайте {ENV_ALLOW_PROD}=1 только если это осознанное решение (guard, exit 10)"
    )
    raise PlatformFatalError(msg)


# endregion FUNC_raise_capacity_prod_guard


def main(argv: list[str] | None = None) -> int:
    """CLI-вход runner (make load-test): preflight → guard → режим → отчёт → exit-код.

    ▶ ┌argv┐ → ○ _parse_args → ○ load_config (2/3/4) → ○ guards (capacity prod → 10;
      optional-off → 0) → ○ _preflight_locust → ○ _probe_mock_model → ○ диспетчер режима
      → ○ PlatformError → exit_code → ⎋ int

    ## @purpose  Единственный CLI-вход подсистемы (инвариант 8: sys.exit только в
    ##            __main__; main() -> int). Exit-коды по контракту (инвариант 2).
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 PASS/WARN, 1 FAIL/ошибка, 2/3/4 config, 10 guard)
    ## @complexity — O(прогон)
    """
    args = _parse_args(argv)
    try:
        config = load_config(args.scenario, args.node, args.mode, REPO_ROOT, platform_root=args.platform_root)
    except ConfigValidationError as exc:
        logger.error("[IMP:10][runner][main] Config validation: %s", exc)
        return exc.exit_code
    except ConfigNotFoundError as exc:
        logger.error("[IMP:10][runner][main] %s", exc)
        return exc.exit_code
    except ConfigParseError as exc:
        logger.error("[IMP:10][runner][main] %s", exc)
        return exc.exit_code

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        # Guard: capacity на нетестовой ноде без LOAD_ALLOW_PROD=1 → exit 10 (инвариант 4, §3.7)
        if config.mode == "capacity" and not config.is_test_node and not config.allow_prod:
            _raise_capacity_prod_guard(config)
        # Optional-сценарий выключен → WARN + exit 0 (без прогона)
        if config.scenario.optional and not config.scenario.enabled:
            logger.warning(
                "[IMP:8][runner][main] Scenario %s optional и выключен — пропуск (включите LOAD_SCENARIO_%s=1)",
                config.scenario.name,
                config.scenario.name.upper(),
            )
            print(f"[LOADTEST] scenario {config.scenario.name} optional и выключен — прогон пропущен (exit 0)")
            return 0
        _preflight_locust()
        _probe_mock_model(config)

        if config.mode == "capacity":
            exit_code, _built = _run_capacity_mode(config, args)
        else:
            exit_code, _built = _run_single_mode(config, args)
        logger.info("[IMP:9][runner][main] mode=%s exit=%d", config.mode, exit_code)
    except PlatformFatalError as exc:
        logger.error("[IMP:10][runner][main] Fatal guard: %s", exc)
        return exc.exit_code
    except (LoadtestRunError, runner_remote.RemoteError) as exc:
        logger.error("[IMP:10][runner][main] Run error: %s", exc)
        return 1
    except PlatformError as exc:
        logger.error("[IMP:10][runner][main] %s", exc)
        return exc.exit_code
    except Exception:  # noqa: EXC — top-level CLI handler: любая ошибка видима (logger.exception), exit 1
        logger.exception("[IMP:10][runner][main] Unexpected error:")
        return 1
    else:
        return exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
