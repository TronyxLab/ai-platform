# GREP_SUMMARY: loadtest config unit scenarios-yaml parse validation node-resolve fail-fast exit-4 env-overrides network
# STRUCTURE: ▶ fixtures (tmp repo_dir + node.yaml) → ◇ load/parse (defaults, validation, optional-env, network)
#           → ◇ render (domain-fallback, env-плейсхолдеры) → ◇ load_config (NODE-резолв, guard-ы, LOAD_NETWORK)
#           → ⎋ 21 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты конфигурации loadtest (DevPlan 146 W1 + 148 TASK-10, tests/unit/test_loadtest_config.py):
##           парсинг scenarios.yaml SoT, defaults-merge, fail-fast валидация (exit 4),
##           NODE-резолв (tmp_path фикстуры, hermetic), env-оверрайды (LOAD_RPS/LOAD_DURATION/
##           LOAD_NETWORK — 148 TASK-5), network-allowlist (host|shared-db-net),
##           рендер плейсхолдеров ({domain}/{host}/{model}/{ENV_VAR}), optional-gate.
## @scope    Чистые функции core/internal/loadtest/config.py — без subprocess, без сети.
##           node.yaml пишется в tmp_path/node-configs/<unique>/ (Path 1 NodeYaml.resolve),
##           SoT — в tmp_path/core/loadtest/scenarios.yaml (структура как в репо).
## @invariants
##   - Каждый тест изолирован: unique node name + monkeypatch env (никакого дрейфа от окружения)
##   - LDD: caplog IMP:7-10 траектория печатается ДО ассертов; успешные сценарии
##     содержат IMP:9 (Anti-Illusion Rule, .kilo/rules/testing.md)
## @rationale Конфиг — ядро воспроизводимости прогонов (SoT+env): атомарные тесты
##            покрывают контракт fail-fast (exit 4) и NODE-резолв канонов платформы.
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
import yaml

from core.internal.loadtest.config import (
    LoadtestConfig,
    load_config,
    load_scenarios_yaml,
    parse_scenario,
    render_dict,
    render_template,
)
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError

logger = logging.getLogger(__name__)

# ── SoT-фикстур (структура как в репо: <root>/core/loadtest/scenarios.yaml) ─────


# region FIXTURE_repo_dir
@pytest.fixture
def repo_dir(tmp_path) -> Path:
    """Имитация корня репо: tmp_path/core/loadtest/scenarios.yaml (минимальный SoT).

    ## @purpose — Запись SoT в tmp_path (Zero Hardcode Rule): defaults + web/llm/llm_stream/db.
    ##            Единый tmp_path делится с node_dir (fixture-композиция в load_config-тестах).
    """
    data = {
        "defaults": {
            "ssl_verify": False,
            "run_time": 300,
            "smoke_duration": 90,
            "max_error": 0.05,
            "max_p99": 3.0,
            "max_p95": 1.0,
            "baseline_delta_p95": 1.5,
            "baseline_delta_error_pp": 2.0,
        },
        "scenarios": {
            "web": {
                "description": "nginx front",
                "endpoint": "https://{domain}/",
                "paths": ["/", "/status"],
                "users": 20,
                "target_rps": 10,
            },
            "llm": {
                "description": "litellm mock",
                "endpoint": "http://{host}:4000",
                "path": "/chat/completions",
                "model": "mock-echo",
                "body_template": {"model": "{model}", "messages": [{"role": "user", "content": "ping"}]},
                "users": 40,
                "target_rps": 20,
                "capacity_start_rps": 2,
            },
            "llm_stream": {
                "endpoint": "http://{host}:4000",
                "path": "/chat/completions",
                "stream": True,
                "model": "mock-echo",
                "users": 20,
                "target_rps": 5,
            },
            "langfuse_ingest": {
                "description": "langfuse traces ingest (146-m1 BUG-2: langfuse.{domain} SoT)",
                "endpoint": "https://langfuse.{domain}",
                "path": "/api/public/traces",
                "method": "POST",
                "users": 10,
                "target_rps": 5,
            },
            "db": {
                "optional": True,
                "endpoint": "postgres:5432",
                "network": "shared-db-net",
                "users": 10,
                "target_rps": 5,
            },
            "s3": {"optional": True, "endpoint": "http://{host}:9000", "users": 10, "target_rps": 5},
        },
    }
    p = tmp_path / "core" / "loadtest"
    p.mkdir(parents=True)
    (p / "scenarios.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


# endregion FIXTURE_repo_dir


# region FIXTURE_node_dir
@pytest.fixture
def node_dir(tmp_path) -> dict:
    """Уникальная тестовая нода: tmp_path/node-configs/<unique>/node.yaml с host и domain.

    ## @purpose — Hermetic NODE-резолв (Path 1 NodeYaml.resolve): уникальное имя
    ##            исключает коллизию с реальными node-configs/ (~/projects glob).
    """
    node = f"loadtest-node-{uuid.uuid4().hex[:8]}"
    config_dir = tmp_path / "node-configs" / node
    config_dir.mkdir(parents=True)
    (config_dir / "node.yaml").write_text(
        yaml.safe_dump(
            {
                "contexts": [{"name": "test"}],
                "node": {"name": node, "host": "203.0.113.10"},
                "domain": "test.example.com",
            }
        ),
        encoding="utf-8",
    )
    return {"name": node, "dir": str(tmp_path), "host": "203.0.113.10", "domain": "test.example.com"}


# endregion FIXTURE_node_dir


# region FIXTURE_no_domain_node_dir
@pytest.fixture
def no_domain_node_dir(tmp_path) -> dict:
    """Нода БЕЗ domain (тестовые ноды, invariant 2) — endpoint fallback на host."""
    node = f"loadtest-node-{uuid.uuid4().hex[:8]}"
    config_dir = tmp_path / "node-configs" / node
    config_dir.mkdir(parents=True)
    (config_dir / "node.yaml").write_text(
        yaml.safe_dump({"node": {"name": node, "host": "198.51.100.7"}}), encoding="utf-8"
    )
    return {"name": node, "dir": str(tmp_path), "host": "198.51.100.7"}


# endregion FIXTURE_no_domain_node_dir


# region HELPER_assert_ldd_imp9
def _assert_ldd_imp9(caplog) -> None:
    """Печать LDD-траектории IMP:7-10 + assert наличия IMP:9 (Anti-Illusion Rule).

    ## @purpose — Единая точка LDD-телеметрии тестов config (контракт .kilo/rules/testing.md).
    ## @io — ⇥ caplog → ⎋ None (assert found IMP:9)
    """
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
            if "[IMP:9]" in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion HELPER_assert_ldd_imp9


# ═══════════════════════════════════════════════════════════════════════════════
# Загрузка SoT (exit-контракт 2/3)
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_load_scenarios_yaml
# 🧪 TRAP[TEST] · Scenario: загрузка SoT (ok/not-found/parse-error)
# · Regression: exit-контракт 2/3 (ConfigNotFound/ConfigParse) — инвариант 9 DevPlan 146
# · Last fail: N/A (new)
# · Remove if: контракт загрузки YAML изменён
class TestLoadScenariosYaml:
    def test_load_ok(self, repo_dir, caplog):
        """SoT парсится: scenarios + defaults присутствуют."""
        caplog.set_level(logging.INFO)
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        _assert_ldd_imp9(caplog)
        assert "web" in data["scenarios"] and "defaults" in data
        assert data["scenarios"]["llm"]["capacity_start_rps"] == 2

    def test_missing_file(self, tmp_path):
        """Отсутствующий файл → ConfigNotFoundError (exit 2)."""
        with pytest.raises(ConfigNotFoundError):
            load_scenarios_yaml(tmp_path / "nope.yaml")

    def test_broken_yaml(self, tmp_path):
        """Битый YAML → ConfigParseError (exit 3)."""
        path = tmp_path / "broken.yaml"
        path.write_text("scenarios:\n  web: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigParseError):
            load_scenarios_yaml(path)


# endregion TEST_load_scenarios_yaml


# ═══════════════════════════════════════════════════════════════════════════════
# parse_scenario — defaults + fail-fast валидация (exit 4)
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_parse_scenario
# 🧪 TRAP[TEST] · Scenario: defaults-merge + fail-fast валидация (exit 4)
# · Regression: пустой endpoint / rps<=0 / нечисловые пороги → ConfigValidationError
# · Last fail: N/A (new)
# · Remove if: схема scenarios.yaml изменена (новые обязательные поля)
class TestParseScenario:
    def test_defaults_merge(self, caplog):
        """Минимальный raw → пороги/длительности из defaults, поля типизированы."""
        caplog.set_level(logging.INFO)
        spec = parse_scenario(
            "web", {"endpoint": "https://x/", "paths": ["/", "/status"], "target_rps": 5, "users": 10}, {}
        )
        _assert_ldd_imp9(caplog)
        assert spec.max_p95 == 1.0 and spec.max_p99 == 3.0 and spec.max_error == 0.05
        assert spec.run_time == 300 and spec.smoke_duration == 90
        assert spec.baseline_delta_p95 == 1.5 and spec.baseline_delta_error_pp == 2.0
        assert spec.optional is False and spec.enabled is True
        assert spec.paths == ("/", "/status")

    def test_parse_from_fixture(self, repo_dir):
        """Парсинг SoT-фикстура: llm (model/capacity_start), llm_stream (stream), db (optional)."""
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        defaults = data["defaults"]
        llm = parse_scenario("llm", data["scenarios"]["llm"], defaults)
        assert llm.model == "mock-echo" and llm.capacity_start_rps == 2
        assert llm.body_template["model"] == "{model}"
        assert llm.stream is False
        stream = parse_scenario("llm_stream", data["scenarios"]["llm_stream"], defaults)
        assert stream.stream is True
        assert parse_scenario("db", data["scenarios"]["db"], defaults).optional is True

    def test_empty_endpoint_rejected(self):
        """Пустой endpoint → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            parse_scenario("web", {"endpoint": "  ", "target_rps": 5, "users": 10}, {})

    def test_target_rps_zero_rejected(self):
        """target_rps <= 0 → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            parse_scenario("web", {"endpoint": "https://x/", "target_rps": 0, "users": 10}, {})

    def test_non_numeric_threshold_rejected(self):
        """Нечисловой порог max_p95 → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            parse_scenario("web", {"endpoint": "https://x/", "target_rps": 5, "users": 10, "max_p95": "fast"}, {})

    def test_optional_env_gate(self, repo_dir, monkeypatch):
        """optional-сценарий: выключен по умолчанию, LOAD_SCENARIO_DB=1 включает."""
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        defaults = data["defaults"]
        monkeypatch.delenv("LOAD_SCENARIO_DB", raising=False)
        assert parse_scenario("db", data["scenarios"]["db"], defaults).enabled is False
        monkeypatch.setenv("LOAD_SCENARIO_DB", "1")
        assert parse_scenario("db", data["scenarios"]["db"], defaults).enabled is True

    def test_env_rps_override_and_users_scale(self, repo_dir, monkeypatch):
        """LOAD_RPS=25 → target_rps=25, users масштабируется до rps×2 (max(20, 50))."""
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        monkeypatch.setenv("LOAD_RPS", "25")
        spec = parse_scenario("web", data["scenarios"]["web"], data["defaults"])
        assert spec.target_rps == 25
        assert spec.users == 50  # max(20, 25×2)


# endregion TEST_parse_scenario


# ═══════════════════════════════════════════════════════════════════════════════
# network — docker-сеть генератора (148 TASK-5)
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_parse_scenario_network
# 🧪 TRAP[TEST] · Scenario: network из SoT (db → shared-db-net; default host; LOAD_NETWORK override; allowlist)
# · Regression: docker-сеть генератора не пробрасывается в runner_remote → db-контейнер
# ·   не достаёт postgres (NO ports: directive) → ConnectionError молча на ноде
# · Last fail: 2026-08-12 — db-сценарий не существовал (заглушка 146 W1)
# · Remove if: механизм сети генератора заменён (не docker run --network)
class TestParseScenarioNetwork:
    def test_network_from_sot(self, repo_dir, caplog):
        """db → network=shared-db-net (из SoT); defaults без network → host."""
        caplog.set_level(logging.INFO)
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        db = parse_scenario("db", data["scenarios"]["db"], data["defaults"])
        logger.info("[IMP:9][test][network] db network из SoT: %s", db.network)
        _assert_ldd_imp9(caplog)
        assert db.network == "shared-db-net"

    def test_network_default_host(self, repo_dir, caplog):
        """web (без network в SoT) → default "host" (web/s3 не меняются)."""
        caplog.set_level(logging.INFO)
        data = load_scenarios_yaml(str(repo_dir / "core" / "loadtest" / "scenarios.yaml"))
        web = parse_scenario("web", data["scenarios"]["web"], data["defaults"])
        logger.info("[IMP:9][test][network] web network default: %s", web.network)
        _assert_ldd_imp9(caplog)
        assert web.network == "host"

    def test_network_invalid_sot_rejected(self):
        """Сеть вне allowlist в SoT → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            parse_scenario("db", {"endpoint": "postgres:5432", "network": "typo-net", "target_rps": 5}, {})


# endregion TEST_parse_scenario_network


# region TEST_load_config_network
# 🧪 TRAP[TEST] · Scenario: LOAD_NETWORK override + невалидная сеть → exit 4 (148 TASK-5)
# · Regression: LOAD_NETWORK игнорируется / не валидируется → опечатка сети = silent fail docker run
# · Last fail: N/A (new) — 148 TASK-5
# · Remove if: allowlist сетей генератора изменён
class TestLoadConfigNetwork:
    def test_network_env_override(self, repo_dir, node_dir, monkeypatch, caplog):
        """LOAD_NETWORK=shared-db-net приоритетнее SoT (web → host перекрывается env)."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("LOAD_NETWORK", raising=False)
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.network == "host"
        monkeypatch.setenv("LOAD_NETWORK", "shared-db-net")
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        logger.info("[IMP:9][test][network] LOAD_NETWORK override → %s", cfg.network)
        _assert_ldd_imp9(caplog)
        assert cfg.network == "shared-db-net"

    def test_network_invalid_rejected(self, repo_dir, node_dir, monkeypatch):
        """LOAD_NETWORK=badnet → ConfigValidationError (exit 4, allowlist)."""
        monkeypatch.setenv("LOAD_NETWORK", "badnet")
        with pytest.raises(ConfigValidationError):
            load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))

    def test_db_local_runner_warns(self, repo_dir, node_dir, monkeypatch, caplog):
        """db + LOAD_RUNNER=local → logger.warning (инвариант 8: db требует node-runner)."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("LOAD_NETWORK", raising=False)
        monkeypatch.setenv("LOAD_SCENARIO_DB", "1")  # optional-сценарий включён
        load_config("db", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        warnings = [r.message for r in caplog.records if "LOAD_RUNNER=node" in r.message]
        assert warnings, "ожидалось предупреждение db + local runner"
        logger.info("[IMP:9][test][network] db+local предупреждение: %s", warnings[0])
        _assert_ldd_imp9(caplog)


# endregion TEST_load_config_network


# ═══════════════════════════════════════════════════════════════════════════════
# render — плейсхолдеры {domain}/{host}/{model}/{ENV}
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_render
# 🧪 TRAP[TEST] · Scenario: рендер плейсхолдеров (domain-fallback, env-плейсхолдеры)
# · Regression: {VAR} без env → ConfigValidationError (НЕ тихая подстановка)
# · Last fail: N/A (new)
# · Remove if: схема плейсхолдеров изменена
class TestRender:
    def test_domain_rendered(self):
        """{domain} → platform_domain."""
        spec = parse_scenario("web", {"endpoint": "https://{domain}/", "target_rps": 5, "users": 10}, {})
        assert render_template(spec.endpoint_template, spec, "1.2.3.4", "example.com") == "https://example.com/"

    def test_domain_fallback_to_host(self):
        """Пустой domain → host (тестовые ноды без домена, invariant 2)."""
        spec = parse_scenario("web", {"endpoint": "https://{domain}/", "target_rps": 5, "users": 10}, {})
        assert render_template(spec.endpoint_template, spec, "1.2.3.4", "") == "https://1.2.3.4/"

    def test_model_placeholder(self):
        """{model} → spec.model (llm body)."""
        spec = parse_scenario(
            "llm",
            {
                "endpoint": "http://{host}:4000",
                "path": "/chat/completions",
                "model": "mock-echo",
                "body_template": {"model": "{model}"},
                "target_rps": 5,
                "users": 10,
            },
            {},
        )
        body = render_dict(spec.body_template, spec, "1.2.3.4", "")
        assert body["model"] == "mock-echo"

    def test_env_placeholder(self, monkeypatch):
        """{LANGFUSE_PUBLIC_KEY} ← env; отсутствие env → ConfigValidationError."""
        spec = parse_scenario("langfuse", {"endpoint": "https://n.{domain}", "target_rps": 5, "users": 10}, {})
        headers = {"Authorization": "Bearer {LANGFUSE_PUBLIC_KEY}"}
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-123")
        assert render_dict(headers, spec, "h", "example.com")["Authorization"] == "Bearer pk-lf-123"
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        with pytest.raises(ConfigValidationError):
            render_dict(headers, spec, "h", "example.com")


# endregion TEST_render


# ═══════════════════════════════════════════════════════════════════════════════
# load_config — сквозной конвейер
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_load_config
# 🧪 TRAP[TEST] · Scenario: сквозной load_config (NODE-резолв, guard-ы, env-оверрайды)
# · Regression: capacity без capacity_start_rps → 4; неизвестный сценарий → 4; LOAD_RUNNER → 4
# · Last fail: N/A (new)
# · Remove if: конвейер конфигурации изменён
class TestLoadConfig:
    def test_full_resolution(self, repo_dir, node_dir, caplog, monkeypatch):
        """web + нода с domain: endpoint=https://test.example.com/, is_test_node=True."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("LOAD_RESULTS_DIR", raising=False)
        monkeypatch.delenv("LOAD_RUNNER", raising=False)
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        _assert_ldd_imp9(caplog)
        assert isinstance(cfg, LoadtestConfig)
        assert cfg.endpoint == "https://test.example.com/"
        assert cfg.node_host == "203.0.113.10"
        assert cfg.is_test_node is True
        assert cfg.results_dir == repo_dir / "load-results"
        assert cfg.history_dir.name == "web" and "history" in str(cfg.history_dir)

    def test_endpoint_fallback_no_domain(self, repo_dir, no_domain_node_dir):
        """Нода без domain: endpoint = https://{host}/ (fallback, invariant 2)."""
        cfg = load_config("web", no_domain_node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.endpoint == "https://198.51.100.7/"
        assert cfg.is_test_node is False  # нет contexts[0].name == "test"

    def test_unknown_scenario(self, repo_dir, node_dir):
        """Неизвестный сценарий → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            load_config("nope", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))

    def test_invalid_mode(self, repo_dir, node_dir):
        """Недопустимый режим → ConfigValidationError (exit 4)."""
        with pytest.raises(ConfigValidationError):
            load_config("web", node_dir["name"], "nightly", str(repo_dir), platform_root=str(repo_dir))

    def test_capacity_requires_start_rps(self, repo_dir, node_dir):
        """capacity + сценарий без capacity_start_rps (web) → ConfigValidationError."""
        with pytest.raises(ConfigValidationError):
            load_config("web", node_dir["name"], "capacity", str(repo_dir), platform_root=str(repo_dir))

    def test_capacity_ok_with_start_rps(self, repo_dir, node_dir):
        """capacity + llm (capacity_start_rps=2) → конфиг собирается."""
        cfg = load_config("llm", node_dir["name"], "capacity", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.scenario.capacity_start_rps == 2
        assert cfg.mode == "capacity"

    def test_invalid_runner(self, repo_dir, node_dir, monkeypatch):
        """LOAD_RUNNER=weird → ConfigValidationError."""
        monkeypatch.setenv("LOAD_RUNNER", "weird")
        with pytest.raises(ConfigValidationError):
            load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))

    def test_runner_node_env(self, repo_dir, node_dir, monkeypatch):
        """LOAD_RUNNER=node + LOAD_IMAGE/LOAD_CPUS → remote-параметры в конфиге."""
        monkeypatch.setenv("LOAD_RUNNER", "node")
        monkeypatch.setenv("LOAD_IMAGE", "ghcr.io/mirror/locust:2.32")
        monkeypatch.setenv("LOAD_CPUS", "4")
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.load_runner == "node"
        assert cfg.image == "ghcr.io/mirror/locust:2.32"
        assert cfg.cpus == "4"

    def test_allow_prod_env(self, repo_dir, node_dir, monkeypatch):
        """LOAD_ALLOW_PROD=1 → allow_prod=True (guard-флаг для capacity)."""
        monkeypatch.setenv("LOAD_ALLOW_PROD", "1")
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.allow_prod is True

    def test_results_dir_override(self, repo_dir, node_dir, monkeypatch):
        """LOAD_RESULTS_DIR → кастомный results_dir (gitignored)."""
        monkeypatch.setenv("LOAD_RESULTS_DIR", "/tmp/lt-results")
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert str(cfg.results_dir) == "/tmp/lt-results"

    def test_endpoint_override_langfuse(self, repo_dir, node_dir, monkeypatch):
        """LOAD_ENDPOINT_LANGFUSE_INGEST переопределяет SoT-endpoint (escape hatch, 146-m1 BUG-2)."""
        monkeypatch.delenv("LOAD_ENDPOINT_LANGFUSE_INGEST", raising=False)
        cfg = load_config("langfuse_ingest", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.endpoint == "https://langfuse.test.example.com"
        monkeypatch.setenv("LOAD_ENDPOINT_LANGFUSE_INGEST", "https://n.test.local")
        cfg = load_config("langfuse_ingest", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.endpoint == "https://n.test.local"

    # 🧪 TRAP[TEST] · Scenario: prometheus_host override (146-m2 — SSH-туннель к Prometheus ноды)
    # · Regression: LOAD_PROMETHEUS_HOST игнорируется → pull идёт на node_host (внешний IP: TCP ок, HTTP timeout)
    # · Last fail: 2026-08-11 — первый боевой прогон tronyx-vps: Prometheus pull timed out (фаервол/докер-прокси)
    # · Remove if: Prometheus-доступ к ноде перестанет требовать туннель (LOAD_PROMETHEUS_HOST удалён)
    def test_prometheus_host_override(self, repo_dir, node_dir, monkeypatch, caplog):
        """LOAD_PROMETHEUS_HOST=localhost → prometheus_host=localhost; без env → node_host (backward-compat)."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("LOAD_PROMETHEUS_HOST", raising=False)
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.prometheus_host == "203.0.113.10"  # default = node_host (поведение без env не меняется)
        monkeypatch.setenv("LOAD_PROMETHEUS_HOST", "localhost")
        cfg = load_config("web", node_dir["name"], "smoke", str(repo_dir), platform_root=str(repo_dir))
        assert cfg.prometheus_host == "localhost"
        logger.info(
            "[IMP:9][test][load_config] prometheus_host override: LOAD_PROMETHEUS_HOST=localhost → %s",
            cfg.prometheus_host,
        )
        _assert_ldd_imp9(caplog)


# endregion TEST_load_config
