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
import urllib.request
from pathlib import Path

from core.internal.loadtest import baseline as baseline_mod
from core.internal.loadtest import capacity as capacity_mod
from core.internal.loadtest import prometheus_pull, report, runner_remote
from core.internal.loadtest.config import ENV_ALLOW_PROD, ENV_DURATION, load_config, render_dict
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = "scenarios"
MOCK_PROBE_SCENARIOS = ("llm", "llm_stream")
MOCK_PROBE_TIMEOUT = 10
SSH_USER = os.environ.get("SSH_USER", "root")


# region DATA_LoadtestRunError
class LoadtestRunError(Exception):
    """Ошибка прогона (locust нет/упал, Prometheus недоступен, mock-probe fail) — exit 1."""


# endregion DATA_LoadtestRunError


# region FUNC__parse_args
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
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
    return parser.parse_args(argv)


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
        raise LoadtestRunError(
            'locust не установлен — выполните: pip install -e ".[load]" '
            '(или: make venv && .venv/bin/pip install -e ".[load]")'
        )
    logger.info("[IMP:8][runner][preflight] locust found")


# endregion FUNC__preflight_locust


# region FUNC__probe_mock_model
def _probe_mock_model(config) -> None:
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
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=MOCK_PROBE_TIMEOUT) as resp:  # nosec B310 — internal litellm endpoint (node host)
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace") if exc.fp else ""
        raise LoadtestRunError(
            f"mock-модель не обнаружена (HTTP {exc.code}): {detail.strip()[:300]} — "
            "установите litellm-config.mock.yml на ноду (docs/load-testing.md) и перезапустите litellm"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LoadtestRunError(
            f"litellm недоступен для mock-probe ({config.endpoint}): {exc} — проверьте деплой litellm на ноде"
        ) from exc
    if status >= 400:
        raise LoadtestRunError(
            f"mock-модель не обнаружена (HTTP {status}) — установите litellm-config.mock.yml на ноду"
        )
    logger.info("[IMP:9][runner][mock_probe] mock-модель подтверждена: HTTP %s на %s", status, url)


# endregion FUNC__probe_mock_model


# region FUNC__locust_env
def _locust_env(config, rps: int | None = None, users: int | None = None) -> dict[str, str]:
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
    env.update({key: value for key, value in os.environ.items() if key.startswith("LT_")})
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
def _run_locust_process(cmd: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
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
        return subprocess.run(cmd, capture_output=True, text=True, env=merged, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LoadtestRunError(
            f"locust-прогон превысил timeout-guard {timeout}s (run_time × 2 + 60) — "
            "проверьте зависшие запросы/сеть до ноды"
        ) from exc
    except FileNotFoundError as exc:
        raise LoadtestRunError('locust не найден в PATH — установите: pip install -e ".[load]"') from exc


# endregion FUNC__run_locust_process


# region FUNC__run_one_step
def _run_one_step(
    config,
    rps: int,
    users: int,
    duration: int,
    csv_prefix: str,
    remote: runner_remote | None,
    parse_prefix: str | None = None,
) -> dict:
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
    try:
        if remote:
            remote.run_remote_locust(
                config.node_host,
                SSH_USER,
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
            remote.fetch(
                config.node_host,
                SSH_USER,
                f"{_remote_workdir(config)}/results",
                str(config.results_dir),
            )
        else:
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
def _remote_workdir(config) -> str:
    """Remote-рабочая директория прогона: /tmp/loadtest-<ts> (уникальная).

    ▶ ┌config┐ → ◇ runner_remote.make_remote_workdir → ⎋ str

    ## @purpose  Изоляция прогонов на ноде; мемоизация в config-атрибуте (один ship/fetch
    ##            на режим — все шаги capacity в одной директории).
    ## @io — ⇥ config: LoadtestConfig → ⎋ str
    ## @complexity — O(1)
    """
    if not hasattr(config, "_remote_workdir"):
        object.__setattr__(config, "_remote_workdir", runner_remote.make_remote_workdir())
    return config._remote_workdir


# endregion FUNC__remote_workdir


# region FUNC__saturation_pull
def _saturation_pull(config, t0: float, t1: float) -> prometheus_pull.SaturationResult:
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
        raise LoadtestRunError(f"Prometheus pull failed: {exc}") from exc


# endregion FUNC__saturation_pull


# region FUNC__run_single_mode
def _run_single_mode(config, args) -> tuple[int, dict]:
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
        runner_remote.ship(config.node_host, SSH_USER, str(REPO_ROOT / "core" / "loadtest"), _remote_workdir(config))

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
        run_row = {
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
    return (0 if verdict in (report.VERDICT_PASS, report.VERDICT_WARN) else 1), built


# endregion FUNC__run_single_mode


# region FUNC__run_capacity_mode
def _run_capacity_mode(config, args) -> tuple[int, dict]:
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
        runner_remote.ship(config.node_host, SSH_USER, str(REPO_ROOT / "core" / "loadtest"), _remote_workdir(config))

    def _step(rps: int) -> dict:
        csv_prefix = f"/lt/results/{rel_dir}/step-{rps}/run" if remote else str(local_dir / f"step-{rps}" / "run")
        raw = _run_one_step(config, rps, rps * 2, duration, csv_prefix, remote, str(local_dir / f"step-{rps}" / "run"))
        step_tasks[rps] = raw.get("tasks") or {}
        return raw

    step_tasks: dict[int, dict] = {}
    t0 = time.time()
    result = capacity_mod.run_capacity(
        _step,
        start_rps=int(spec.capacity_start_rps or 0),
        max_error=spec.max_error,
        max_p99=spec.max_p99,
    )
    t1 = time.time()
    duration_s = round(t1 - t0, 1)  # 148 TASK-7: суммарная длительность capacity-профиля

    profile_rows = [
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
        run_row = {
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
    return (0 if verdict in (report.VERDICT_PASS, report.VERDICT_WARN) else 1), built


# endregion FUNC__run_capacity_mode


# region FUNC__write_outputs
def _write_outputs(built: dict, report_dir: Path, junit: bool) -> None:
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

    try:
        # Guard: capacity на нетестовой ноде без LOAD_ALLOW_PROD=1 → exit 10 (инвариант 4, §3.7)
        if config.mode == "capacity" and not config.is_test_node and not config.allow_prod:
            raise PlatformFatalError(
                f"capacity на нетестовой ноде ({config.node_name}) запрещён — "
                f"задайте {ENV_ALLOW_PROD}=1 только если это осознанное решение (guard, exit 10)"
            )
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
        return exit_code
    except PlatformFatalError as exc:
        logger.error("[IMP:10][runner][main] Fatal guard: %s", exc)
        return exc.exit_code
    except (LoadtestRunError, runner_remote.RemoteError) as exc:
        logger.error("[IMP:10][runner][main] Run error: %s", exc)
        return 1
    except PlatformError as exc:
        logger.error("[IMP:10][runner][main] %s", exc)
        return exc.exit_code
    except Exception as exc:  # noqa: EXC — top-level CLI handler: любая ошибка видима (logger.exception), exit 1
        logger.exception("[IMP:10][runner][main] Unexpected error: %s", exc)
        return 1


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
