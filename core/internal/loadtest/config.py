#!/usr/bin/env python3
# GREP_SUMMARY: loadtest config scenarios-yaml env-overrides node-resolver validation fail-fast exit-4 SoT
# STRUCTURE: ▶ load_scenarios_yaml → ◇ defaults+scenario merge → ◇ NODE-резолв (node_resolver+NodeYaml)
#           → ◇ env-оверрайды (LOAD_RPS/LOAD_DURATION/LOAD_RESULTS_DIR) → ◇ render {domain}/{host}/{model}/{ENV}
#           → ◇ validate fail-fast (ConfigValidationError=4) → ⎋ LoadtestConfig
# region MODULE_CONTRACT
## @purpose  Конфигурация нагрузочных прогонов (DevPlan 146 W1): загрузка
##           core/loadtest/scenarios.yaml (SoT), резолв NODE → host + platform_domain
##           (node_resolver + NodeYaml — единые фасады чтения node.yaml), merge
##           env-оверрайдов (LOAD_RPS, LOAD_DURATION, LOAD_RESULTS_DIR, LOAD_RUNNER,
##           LOAD_IMAGE, LOAD_CPUS, LOAD_PROMETHEUS_PORT, LOAD_ALLOW_PROD, LOAD_VERSION,
##           LOAD_SCENARIO_<NAME> для optional, LOAD_ENDPOINT_<SCENARIO> — endpoint
##           override, 146-m1 BUG-2), рендер плейсхолдеров и fail-fast
##           валидация с exit 4 (ConfigValidationError) по контракту shared/contracts.py.
##           LOAD_IMAGE default — locustio/locust:2.32.10 (полный semver: minor-only
##           тега 2.32 в Docker Hub НЕ существует — BUG-4 146-m4; совпадает с
##           runner_remote.DEFAULT_IMAGE и pyproject-пином).
## @scope    Потребитель: runner_cli.py (единственный CLI). Чистые функции тестируются
##           native pytest (tests/unit/test_loadtest_config.py) без subprocess.
## @invariants
##   1. exit-коды по shared/contracts.py: 0 ok, 1 generic, 2 ConfigNotFound, 3 ConfigParse,
##      4 ConfigValidation, 10 Fatal — НИКАКИХ «exit 2 = FAIL» (инвариант 9 DevPlan 146).
##   2. users — РАЗМЕР ПУЛА (users = target_rps × 2), НЕ контроль RPS; точный RPS —
##      constant_throughput (wait_time сценариев через LT_TARGET_RPS/LT_USERS,
##      helper rps_wait_time — 146-m1 BUG-1 fix).
##   3. Плейсхолдеры: {domain} → platform_domain (пустой → host), {host} → node.host,
##      {model} → scenario.model, {ANY_ENV_VAR} → os.environ (отсутствие → ConfigValidationError).
##   4. optional-сценарий: enabled=False по умолчанию; включение — LOAD_SCENARIO_<UPPER>=1.
##   5. LOAD_ENDPOINT_<UPPER> (непустой) переопределяет SoT-endpoint сценария
##      (рендер теми же плейсхолдерами) — per-node escape hatch.
##   6. Прямой YAML-парсинг node.yaml запрещён — только NodeYaml-фасад (shared/AGENTS.md).
##   7. main() не вызывается — модуль библиотечный (CLI — runner_cli.py).
## @rationale SoT scenarios.yaml + env-оверрайды = воспроизводимые прогоны без правок кода;
##            NODE-резолв через существующие каноны платформы (node_resolver.py —
##            Python SoT, DevPlan 146 инвариант 7).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_resolver import resolve_node_yaml
from core.internal.shared.node_yaml import NodeYaml

logger = logging.getLogger(__name__)

# ── Канонические имена env-оверрайдов (единая точка — инвариант DRY) ──────────
ENV_RPS = "LOAD_RPS"
ENV_DURATION = "LOAD_DURATION"
ENV_RESULTS_DIR = "LOAD_RESULTS_DIR"
ENV_RUNNER = "LOAD_RUNNER"
ENV_IMAGE = "LOAD_IMAGE"
ENV_CPUS = "LOAD_CPUS"
ENV_PROMETHEUS_PORT = "LOAD_PROMETHEUS_PORT"
ENV_PROMETHEUS_HOST = "LOAD_PROMETHEUS_HOST"  # override для SSH-туннелей/непрямого доступа (146-m2)
ENV_ALLOW_PROD = "LOAD_ALLOW_PROD"
ENV_VERSION = "LOAD_VERSION"
ENV_OPTIONAL_PREFIX = "LOAD_SCENARIO_"
ENV_ENDPOINT_PREFIX = "LOAD_ENDPOINT_"  # per-scenario endpoint override (146-m1 BUG-2)

VALID_MODES: tuple[str, ...] = ("smoke", "regression", "capacity")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


# region DATA_ScenarioSpec
@dataclass(frozen=True)
class ScenarioSpec:
    """Спецификация сценария из SoT (после валидации и merge дефолтов).

    ## @purpose  Валидированная спецификация одного сценария — вход для runner_cli
    ##            (endpoint/users/rps/пороги/длительности). Все поля числовые приведены
    ##            к float/int; пороги > 0 (fail-fast).
    ## @invariants
    ##   - target_rps > 0; users > 0; max_p95/max_p99/max_error > 0 (после валидации)
    ##   - optional → enabled из env LOAD_SCENARIO_<NAME> (по умолчанию False)
    """

    name: str
    description: str
    endpoint_template: str
    paths: tuple[str, ...] = ()
    path: str | None = None
    method: str = "GET"
    stream: bool = False
    model: str | None = None
    body_template: dict | None = None
    headers: dict = field(default_factory=dict)
    users: int = 10
    target_rps: int = 5
    optional: bool = False
    enabled: bool = True
    ssl_verify: bool = False
    max_p95: float = 1.0
    max_p99: float = 3.0
    max_error: float = 0.05
    chunk_timeout: float = 10.0
    capacity_start_rps: int | None = None
    run_time: int = 300
    smoke_duration: int = 90
    capacity_step_duration: int = 60
    baseline_delta_p95: float = 1.5
    baseline_delta_error_pp: float = 2.0


# endregion DATA_ScenarioSpec


# region DATA_LoadtestConfig
@dataclass(frozen=True)
class LoadtestConfig:
    """Полная конфигурация прогона (SoT + NODE + env) — единый вход runner_cli.

    ## @purpose  Сквозная конфигурация одного прогона: сценарий, нода (host/domain),
    ##            режим, пути результатов/истории, remote-параметры, guard-флаги.
    ## @invariants
    ##   - results_dir — LOAD_RESULTS_DIR (default load-results/), целиком gitignored
    ##   - history_dir — core/loadtest/history/<node>/<scenario>/ (коммитится)
    ##   - load_runner ∈ {local, node}; capacity на нетестовой ноде → guard в runner_cli
    ##   - prometheus_host — LOAD_PROMETHEUS_HOST (default node_host): override для
    ##     SSH-туннелей (localhost) и непрямого доступа к Prometheus ноды (146-m2)
    """

    scenario: ScenarioSpec
    node_name: str
    node_host: str
    platform_domain: str
    endpoint: str
    mode: str
    results_dir: Path
    history_dir: Path
    version: str
    load_runner: str
    image: str
    cpus: str
    prometheus_port: int
    prometheus_host: str
    allow_prod: bool
    is_test_node: bool


# endregion DATA_LoadtestConfig


# region FUNC__env_int
def _env_int(name: str, default: int) -> int:
    """Безопасное чтение int-env (invalid → ConfigValidationError, exit 4).

    ▶ ┌name, default┐ → ◇ отсутствует → default → ◇ not int → ConfigValidationError → ⎋ int

    ## @purpose  Единая точка int-оверрайдов (LOAD_RPS/LOAD_DURATION/LOAD_PROMETHEUS_PORT):
    ##            мусор в env = явная конфигурационная ошибка (fail-fast), не ValueError.
    ## @io — ⇥ name: str, default: int → ⎋ int
    ## @complexity — O(1)
    ## @raises — ConfigValidationError: значение не парсится в int
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigValidationError(f"{name}={raw!r} — ожидалось целое число") from exc


# endregion FUNC__env_int


# region FUNC_load_scenarios_yaml
def load_scenarios_yaml(path: str | Path) -> dict:
    """Загрузка scenarios.yaml (canonical yaml.safe_load) с exit-контрактом 2/3.

    ▶ ┌path┐ → ◇ отсутствует → ConfigNotFoundError(2) → ◇ yaml-синтаксис битый →
      ConfigParseError(3) → ○ safe_load → ⎋ dict

    ## @purpose  Единая точка чтения SoT сценариев. Контракт exit-кодов:
    ##            отсутствующий файл → 2 (ConfigNotFound), битый YAML → 3 (ConfigParse).
    ## @io — ⇥ path: str | Path → ⎋ dict (raw содержимое scenarios.yaml)
    ## @complexity — O(N) — YAML parse
    ## @raises — ConfigNotFoundError, ConfigParseError
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigNotFoundError(f"scenarios.yaml не найден: {p} (ожидается core/loadtest/scenarios.yaml)")
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigParseError(f"scenarios.yaml битый YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigParseError(f"scenarios.yaml: ожидался mapping, получен {type(data).__name__}")
    logger.info("[IMP:9][config][load_scenarios_yaml] Loaded %d scenario(s) from %s", len(data.get("scenarios", {})), p)
    return data


# endregion FUNC_load_scenarios_yaml


# region FUNC__as_float_positive
def _as_float_positive(name: str, value: object) -> float:
    """Приведение к float с fail-fast (нечисловое или <= 0 → ConfigValidationError).

    ▶ ┌name, value┐ → ◇ not (int|float) → 4 → ◇ <= 0 → 4 → ⎋ float

    ## @purpose  Валидация порогов (max_p95/max_p99/max_error/baseline_delta_*) —
    ##            мусор в SoT = конфигурационная ошибка (инвариант 1, DevPlan 146 §3.7).
    ## @io — ⇥ name: str (для сообщения), value: object → ⎋ float
    ## @complexity — O(1)
    ## @raises — ConfigValidationError
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"scenario.{name}: ожидалось число, получено {value!r}")
    result = float(value)
    if result <= 0:
        raise ConfigValidationError(f"scenario.{name}: должно быть > 0, получено {result}")
    return result


# endregion FUNC__as_float_positive


# region FUNC_parse_scenario
def parse_scenario(name: str, raw: dict, defaults: dict) -> ScenarioSpec:
    """Парсинг одного сценария SoT: defaults-merge + fail-fast валидация.

    ▶ ┌name, raw, defaults┐ → ○ defaults_merge → ◇ endpoint пустой → 4 → ◇ target_rps<=0 → 4
      → ◇ пороги нечисловые → 4 → ◇ users<=0 → 4 → ⊕ enabled (optional × LOAD_SCENARIO_*) → ⎋ ScenarioSpec

    ## @purpose  Валидация структуры сценария по контракту DevPlan 146 §3.7 («config.validate()
    ##            fail-fast (exit 4): неизвестный сценарий, пустой endpoint, target_rps<=0,
    ##            пороги нечисловые»). Чистая функция — native pytest.
    ## @io — ⇥ name: str, raw: dict (секция сценария), defaults: dict (секция defaults)
    ##       → ⎋ ScenarioSpec
    ## @complexity — O(K) — K = число полей сценария
    ## @raises — ConfigValidationError (exit 4)
    ## @invariants
    ##   - Обязательные: endpoint (непустой), target_rps > 0, users > 0
    ##   - Пороги: max_p95/max_p99/max_error/baseline_delta_* > 0 числовые
    ##   - capacity_start_rps <= 0 → None (валидация только в capacity-режиме)
    ##   - optional + LOAD_SCENARIO_<UPPER>=1 → enabled=True
    """
    if not isinstance(raw, dict):
        raise ConfigValidationError(f"scenario '{name}': ожидался mapping, получен {raw!r}")
    endpoint = str(raw.get("endpoint", "")).strip()
    if not endpoint:
        raise ConfigValidationError(f"scenario '{name}': пустой endpoint (обязательное поле)")

    target_rps = raw.get("target_rps", defaults.get("target_rps", 5))
    users = raw.get("users", defaults.get("users", 10))
    if isinstance(target_rps, bool) or not isinstance(target_rps, (int, float)) or float(target_rps) <= 0:
        raise ConfigValidationError(f"scenario '{name}': target_rps должно быть числом > 0, получено {target_rps!r}")
    if isinstance(users, bool) or not isinstance(users, (int, float)) or float(users) <= 0:
        raise ConfigValidationError(f"scenario '{name}': users должно быть числом > 0, получено {users!r}")

    # Env-оверрайд LOAD_RPS → target_rps; users масштабируются до rps×2 (пул, инвариант 11:
    # users — РАЗМЕР ПУЛА = rps × 2, НЕ контроль RPS — точный RPS задаёт constant_throughput
    # в wait_time сценариев через LT_TARGET_RPS/LT_USERS, 146-m1 BUG-1). max() сохраняет
    # ручные завышения пула (сценарии с latency > 2s увеличивают users вручную в SoT).
    target_rps = _env_int(ENV_RPS, int(target_rps))
    users = max(int(users), int(target_rps) * 2)

    optional = bool(raw.get("optional", False))
    enabled = True
    if optional:
        env_flag = os.environ.get(f"{ENV_OPTIONAL_PREFIX}{name.upper()}", "").strip()
        enabled = env_flag == "1"

    paths = raw.get("paths", [])
    if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
        path_tuple = tuple(p for p in paths if p.strip())
    else:
        path_tuple = ()

    capacity_start_rps = raw.get("capacity_start_rps")
    if isinstance(capacity_start_rps, (int, float)) and not isinstance(capacity_start_rps, bool):
        capacity_start_rps = int(capacity_start_rps)
        if capacity_start_rps <= 0:
            capacity_start_rps = None
    else:
        capacity_start_rps = None

    parsed = ScenarioSpec(
        name=name,
        description=str(raw.get("description", "")),
        endpoint_template=endpoint,
        paths=path_tuple,
        path=raw.get("path"),
        method=str(raw.get("method", "GET")).upper(),
        stream=bool(raw.get("stream", False)),
        model=raw.get("model"),
        body_template=raw.get("body_template") if isinstance(raw.get("body_template"), dict) else None,
        headers=raw.get("headers") if isinstance(raw.get("headers"), dict) else {},
        users=int(users),
        target_rps=int(target_rps),
        optional=optional,
        enabled=enabled,
        ssl_verify=bool(raw.get("ssl_verify", defaults.get("ssl_verify", False))),
        max_p95=_as_float_positive("max_p95", raw.get("max_p95", defaults.get("max_p95", 1.0))),
        max_p99=_as_float_positive("max_p99", raw.get("max_p99", defaults.get("max_p99", 3.0))),
        max_error=_as_float_positive("max_error", raw.get("max_error", defaults.get("max_error", 0.05))),
        chunk_timeout=_as_float_positive("chunk_timeout", raw.get("chunk_timeout", 10.0)),
        capacity_start_rps=capacity_start_rps,
        run_time=int(_env_int(ENV_DURATION, int(raw.get("run_time", defaults.get("run_time", 300))))),
        smoke_duration=int(raw.get("smoke_duration", defaults.get("smoke_duration", 90))),
        capacity_step_duration=int(raw.get("capacity_step_duration", defaults.get("capacity_step_duration", 60))),
        baseline_delta_p95=_as_float_positive(
            "baseline_delta_p95", raw.get("baseline_delta_p95", defaults.get("baseline_delta_p95", 1.5))
        ),
        baseline_delta_error_pp=_as_float_positive(
            "baseline_delta_error_pp", raw.get("baseline_delta_error_pp", defaults.get("baseline_delta_error_pp", 2.0))
        ),
    )
    logger.info(
        "[IMP:9][config][parse_scenario] %s: endpoint=%s rps=%d users=%d optional=%s enabled=%s",
        name,
        endpoint,
        parsed.target_rps,
        parsed.users,
        optional,
        enabled,
    )
    return parsed


# endregion FUNC_parse_scenario


# region FUNC_resolve_node
def resolve_node(node_name: str, platform_root: str | None = None) -> tuple[str, str]:
    """Резолв NODE → (node_host, platform_domain) через канонные фасады.

    ▶ ┌node_name┐ → ○ resolve_node_yaml (3-path search) → ○ NodeYaml.get(node.host) →
      ○ NodeYaml.get(domain) → ⎋ (host, platform_domain)

    ## @purpose  Инвариант 7 DevPlan 146: NODE резолвится через shared/node_resolver.py
    ##            (Python SoT) + NodeYaml-фасад (запрет прямого YAML-парсинга node.yaml).
    ## @io — ⇥ node_name: str, platform_root: str | None → ⎋ (host: str, platform_domain: str)
    ## @complexity — O(N) — YAML parse (NodeYaml)
    ## @raises — ConfigNotFoundError (node.yaml не найден), ConfigParseError (битый YAML),
    ##           ConfigValidationError (нет node.host)
    ## @invariants
    ##   - platform_domain = node.yaml top-level domain ("" если не задан — тестовые ноды)
    ##   - node.host обязателен (без него прогон невозможен) → ConfigValidationError
    """
    yaml_path = resolve_node_yaml(node_name=node_name, platform_root=platform_root)
    node = NodeYaml(yaml_path)
    host = str(node.get("node.host", default="") or "").strip()
    if not host:
        raise ConfigValidationError(f"NODE={node_name}: node.host отсутствует в {yaml_path}")
    domain = str(node.get("domain", default="") or "").strip()
    logger.info("[IMP:9][config][resolve_node] node=%s host=%s domain=%s", node_name, host, domain)
    return host, domain


# endregion FUNC_resolve_node


# region FUNC__is_test_node
def _is_test_node(yaml_path: str) -> bool:
    """Детекция тестовой ноды: node.role == "test" ИЛИ первый контекст "test".

    ▶ ┌yaml_path┐ → ○ node.role → ◇ == "test" → True → ○ contexts[0].name → ◇ == "test" → True → ⎋ False

    ## @purpose  Guard-семантика capacity (DevPlan 146 §3.7: «node.yaml#role != test»).
    ##            Поле role отсутствует в текущих node.yaml (test-e2e маркируется
    ##            contexts[0].name == "test") — проверяются оба признака (forward-compat).
    ## @io — ⇥ yaml_path: str → ⎋ bool
    ## @complexity — O(N) — YAML parse
    ## @invariants
    ##   - node.role == "test" (будущие конфиги) → True
    ##   - contexts[0].name == "test" (текущие тестовые ноды, test-e2e) → True
    ##   - Пустой конфиг / нет признаков → False (production-консервативность)
    """
    node = NodeYaml(yaml_path)
    role = str(node.get("node.role", default="") or "").strip()
    if role == "test":
        return True
    contexts = node.get("contexts", default=[])
    return bool(
        isinstance(contexts, list)
        and contexts
        and isinstance(contexts[0], dict)
        and str(contexts[0].get("name", "") or "").strip() == "test"
    )


# endregion FUNC__is_test_node


# region FUNC_render_template
def render_template(template: str, spec: ScenarioSpec, host: str, domain: str) -> str:
    """Рендер плейсхолдеров {domain}/{host}/{model}/{ENV_VAR} в строке шаблона.

    ▶ ┌template┐ → ○ replace {domain}/{host}/{model} → ○ остальные {VAR} → os.environ
      → ◇ VAR отсутствует → ConfigValidationError(4) → ⎋ rendered

    ## @purpose  Подстановка плейсхолдеров endpoint/headers/body (SoT) — единая точка:
    ##            {domain} → platform_domain (пустой → host — тестовые ноды без домена),
    ##            {host} → node.host, {model} → spec.model, {VAR} → env LOAD_VAR-семантика
    ##            (например {LANGFUSE_PUBLIC_KEY} ← LOAD_LANGFUSE_PUBLIC_KEY — секреты ноды).
    ## @io — ⇥ template: str, spec, host, domain → ⎋ str
    ## @complexity — O(P) — P = число плейсхолдеров
    ## @raises — ConfigValidationError: {VAR} отсутствует в окружении (exit 4)
    ## @invariants
    ##   - domain пустой → подставляется host (тестовые ноды: https://{host}/)
    ##   - {VAR} резолвится из os.environ[VAR]; отсутствие → явная ошибка (не тихая подстановка)
    """
    domain_value = domain if domain else host

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "domain":
            return domain_value
        if key == "host":
            return host
        if key == "model":
            if spec.model is None:
                raise ConfigValidationError(f"плейсхолдер {{model}} в сценарии '{spec.name}', но model не задан в SoT")
            return spec.model
        value = os.environ.get(key)
        if value is None:
            raise ConfigValidationError(
                f"плейсхолдер {{{key}}} в сценарии '{spec.name}' — задайте env {key} (секреты ноды)"
            )
        return value

    return _PLACEHOLDER_RE.sub(_replace, template)


# endregion FUNC_render_template


# region FUNC_render_dict
def render_dict(template: dict | None, spec: ScenarioSpec, host: str, domain: str) -> dict | None:
    """Рекурсивный рендер плейсхолдеров в dict (headers/body_template).

    ▶ ┌template?┐ → ◇ None → None → ○ рекурсия по str-значениям → render_template → ⎋ dict | None

    ## @purpose  Рендер headers/body SoT (например Authorization: "Bearer {LANGFUSE_PUBLIC_KEY}").
    ## @io — ⇥ template: dict | None → ⎋ dict | None (скопирован, rendered)
    ## @complexity — O(N) — N = узлов в dict
    """
    if template is None:
        return None
    rendered: dict = {}
    for key, value in template.items():
        if isinstance(value, str):
            rendered[key] = render_template(value, spec, host, domain)
        elif isinstance(value, dict):
            rendered[key] = render_dict(value, spec, host, domain)
        elif isinstance(value, list):
            rendered[key] = [
                render_dict(item, spec, host, domain) if isinstance(item, dict) else item for item in value
            ]
        else:
            rendered[key] = value
    return rendered


# endregion FUNC_render_dict


# region FUNC_load_config
def load_config(
    scenario_name: str,
    node_name: str,
    mode: str,
    base_dir: str | Path,
    platform_root: str | None = None,
) -> LoadtestConfig:
    """Полная сборка конфигурации прогона: SoT + NODE + env + рендер + валидация.

    ▶ ┌scenario, node, mode, base_dir┐ → ○ load_scenarios_yaml → ○ parse_scenario →
      ○ resolve_node → ○ render endpoint/headers/body → ○ env-оверрайды → ⎋ LoadtestConfig

    ## @purpose  Единая точка входа конфигурации для runner_cli (W1-W5). Сквозной
    ##            конвейер: SoT → валидация (exit 4) → NODE-резолв (exit 2/3) → рендер.
    ## @io — ⇥ scenario_name: str, node_name: str, mode: str, base_dir: str | Path
    ##         (корень репо — для core/loadtest/), platform_root: str | None
    ##       → ⎋ LoadtestConfig
    ## @complexity — O(N) — YAML parse SoT + node.yaml
    ## @raises — ConfigNotFoundError(2)/ConfigParseError(3)/ConfigValidationError(4)
    ## @invariants
    ##   - Неизвестный сценарий/режим → ConfigValidationError (exit 4)
    ##   - capacity требует capacity_start_rps > 0 (иначе 4)
    ##   - results_dir = LOAD_RESULTS_DIR (default <base_dir>/load-results)
    ##   - history_dir = <base_dir>/core/loadtest/history/<node>/<scenario>
    """
    mode = mode.strip().lower()
    if mode not in VALID_MODES:
        raise ConfigValidationError(f"mode={mode!r} — допустимо: {', '.join(VALID_MODES)}")

    base = Path(base_dir)
    scenarios_path = base / "core" / "loadtest" / "scenarios.yaml"
    data = load_scenarios_yaml(scenarios_path)
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    scenarios = data.get("scenarios") if isinstance(data.get("scenarios"), dict) else {}
    if scenario_name not in scenarios:
        raise ConfigValidationError(
            f"Неизвестный сценарий: {scenario_name!r} (доступно: {', '.join(sorted(scenarios)) or '—'})"
        )
    spec = parse_scenario(scenario_name, scenarios[scenario_name], defaults)

    if mode == "capacity" and spec.capacity_start_rps is None:
        raise ConfigValidationError(
            f"scenario '{scenario_name}': capacity требует capacity_start_rps > 0 в scenarios.yaml"
        )
    if spec.optional and not spec.enabled:
        logger.info(
            "[IMP:8][config][load_config] Scenario %s optional и выключен — включите %s%s=1",
            scenario_name,
            ENV_OPTIONAL_PREFIX,
            scenario_name.upper(),
        )

    host, domain = resolve_node(node_name, platform_root=platform_root)
    endpoint = render_template(spec.endpoint_template, spec, host, domain)
    # Per-scenario env-override (escape hatch, 146-m1 BUG-2): LOAD_ENDPOINT_<SCENARIO>
    # — кастомный endpoint для нод с иной топологией (например
    # LOAD_ENDPOINT_LANGFUSE_INGEST=https://n.example.com). Рендерится теми же
    # плейсхолдерами ({domain}/{host}/{ENV_VAR}), что и SoT-endpoint.
    env_override = os.environ.get(f"{ENV_ENDPOINT_PREFIX}{scenario_name.upper()}", "").strip()
    if env_override:
        endpoint = render_template(env_override, spec, host, domain)
        logger.info(
            "[IMP:9][config][load_config] endpoint override %s%s=%s → %s",
            ENV_ENDPOINT_PREFIX,
            scenario_name.upper(),
            env_override,
            endpoint,
        )

    yaml_path = resolve_node_yaml(node_name=node_name, platform_root=platform_root)
    is_test = _is_test_node(yaml_path)

    results_dir = Path(os.environ.get(ENV_RESULTS_DIR, str(base / "load-results")))
    load_runner = os.environ.get(ENV_RUNNER, "local").strip().lower()
    if load_runner not in ("local", "node"):
        raise ConfigValidationError(f"{ENV_RUNNER}={load_runner!r} — допустимо: local | node")

    config = LoadtestConfig(
        scenario=spec,
        node_name=node_name,
        node_host=host,
        platform_domain=domain,
        endpoint=endpoint,
        mode=mode,
        results_dir=results_dir,
        history_dir=base / "core" / "loadtest" / "history" / node_name / scenario_name,
        version=os.environ.get(ENV_VERSION, "").strip() or "unknown",
        load_runner=load_runner,
        image=os.environ.get(ENV_IMAGE, "locustio/locust:2.32.10").strip(),
        cpus=os.environ.get(ENV_CPUS, "2").strip(),
        prometheus_port=_env_int(ENV_PROMETHEUS_PORT, 9090),
        prometheus_host=os.environ.get(ENV_PROMETHEUS_HOST, "").strip() or host,
        allow_prod=os.environ.get(ENV_ALLOW_PROD, "").strip() == "1",
        is_test_node=is_test,
    )
    logger.info(
        "[IMP:9][config][load_config] scenario=%s node=%s mode=%s endpoint=%s rps=%d users=%d",
        scenario_name,
        node_name,
        mode,
        endpoint,
        spec.target_rps,
        spec.users,
    )
    return config


# endregion FUNC_load_config
