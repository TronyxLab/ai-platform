# GREP_SUMMARY: shared-placement, placement, load-placement, resolve-node-modules, service-host, validate-topology, lint-drift, multi-node, fixtures, s2, s2b, s3, vpn-enforced, acceptance-w1
# STRUCTURE: ▶ tmp_path fake trees (modules_dir + node_configs_dir) → ○ load_placement(fixtures s3/s2b) →
#            ◇ resolver/schema-паттерны (9 тестов) → ◇ validate_topology (полнота/off-deps/exposed) →
#            ⊕ LDD trajectory (assert_ldd_imp9) → ⎋ pass | raise ConfigValidationError
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/shared/placement.py (DevPlan 010 W0 T0.2/T0.4/T0.5).
##           Покрывают: загрузку фикстур S3/S2b из tests/fixtures/placement/, single-node no-op,
##           service_host remote/local, vpn_enforced обязательность, публичный host, полноту
##           инвентаря, off data-plane зависимости, exposed↔nginx-ноды, неизвестную ноду.
##           Acceptance W1 (§5): резолв на S3 даёт точные наборы data-1/agent-1/apps-1.
## @scope    9 test functions + 2 хелпера (модульное дерево, node-configs). Нативные импорты,
##           tmp_path для фиктивных деревьев — БЕЗ subprocess, БЕЗ hardcoded путей
##           (фикстуры — через Path(__file__).relative-резолв).
## @invariants
##   - Zero hardcoded paths: fixtures через Path(__file__).parent.parent / "fixtures"; деревья — tmp_path
##   - Native imports (core.internal.shared.placement) — no subprocess
##   - LDD: assert_ldd_imp9 (tests/helpers/gate_helpers) печатает IMP:7-10 trajectory и требует IMP:9
##   - failure-path тесты: assert_ldd_imp9(require_imp9=False) (print-only) + pytest.raises match
## @rationale T0.5 фикстуры + T0.2/T0.4 юнит-тесты закрепляют контракт Волны 0 до интеграции
##            резолва в деплой (W1). fake-деревья изолируют validate_topology от реального
##            core/modules/ (инвентарь = параметр, не хардкод — как DI-seam NodeYaml).
## @changes 2026-08-22 · DevPlan 010 W0 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.placement import (
    Placement,
    lint_drift,
    load_placement,
    resolve_node_modules,
    service_host,
    validate_topology,
)
from tests.helpers.gate_helpers import assert_ldd_imp9, write_yaml

logger = logging.getLogger(__name__)

# ── Пути к фикстурам сценариев (T0.5): tests/fixtures/placement/ ────────────
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "placement"

# ── Зависимости модулей (зеркало реальных core/modules/*/module.yaml): ──────
# используется для фиктивных деревьев modules_dir (инвентарь = параметр валидатора).
_CONTEXT_MODULE_DEPS: dict[str, list[str]] = {
    "postgres": [],
    "redis": [],
    "minio": [],
    "clickhouse": [],
    "backup-cron": [],
    "service-exporters": [],
    "platform-secrets": [],
    "nginx": [],
    "hermes-agent": ["nginx", "postgres", "redis", "litellm"],
    "litellm": ["postgres"],
    "langfuse": ["postgres", "clickhouse"],
    "monitoring": ["nginx"],
    "logging": [],
    "status-page": ["nginx"],
    "log-collector": ["logging"],
    "node-metrics": [],
}

_CONTEXT_NAME = "tronyx-lab"
_S3_NODES = ["data-1", "agent-1", "apps-1"]
_S2B_NODES = ["data-1", "apps-critical", "apps-canary"]


# region HELPERS


def _make_module_tree(tmp_path: Path, modules: dict[str, list[str]]) -> Path:
    """Build a fake core/modules/ inventory (dir + module.yaml#depends_on) in tmp_path."""
    modules_dir = tmp_path / "modules"
    for name, deps in modules.items():
        mod_dir = modules_dir / name
        mod_dir.mkdir(parents=True, exist_ok=True)
        body = "".join(f"  - {dep}\n" for dep in deps)
        write_yaml(mod_dir / "module.yaml", f"name: {name}\ndepends_on:\n{body}")
    return modules_dir


def _make_node_configs(tmp_path: Path, context: str, node_names: list[str]) -> Path:
    """Build a fake node-configs/<context>/ tree: node.yaml with contexts[0].name == context."""
    node_configs_dir = tmp_path / "node-configs"
    for name in node_names:
        node_yaml_path = node_configs_dir / name / "node.yaml"
        write_yaml(
            node_yaml_path,
            {"contexts": [{"name": context}], "node": {"name": name, "host": "10.8.0.1", "owner_key": "x"}},
        )
    return node_configs_dir


def _s3_placement(tmp_path: Path) -> Placement:
    """Load the S3 fixture and pair it with a complete fake module tree + node-configs."""
    placement = load_placement(_FIXTURES_DIR / "s3.yaml")
    assert placement is not None
    _make_module_tree(tmp_path, _CONTEXT_MODULE_DEPS)
    _make_node_configs(tmp_path, _CONTEXT_NAME, _S3_NODES)
    return placement


# endregion HELPERS


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1: S3 fixture + acceptance W1 resolve sets
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_load_placement_s3_fixture
# 🧪 TRAP[TEST] · 2026-08-22 · S3 fixture load + resolve sets (DevPlan 010 acceptance W1 §5)
# · Regression: if the resolver drops all-nodes/singleton forms, node sets silently shrink
# · Scenario: s3.yaml → data-1/agent-1/apps-1 exact sets (W1)
# · Last fail: N/A — new W0 test
# · Remove if: resolve_node_modules is replaced by a different resolution DSL
def test_load_placement_s3_fixture(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """load_placement(s3.yaml) → Placement; resolve gives acceptance W1 sets (§5)."""
    caplog.set_level(logging.DEBUG)

    placement = load_placement(_FIXTURES_DIR / "s3.yaml")
    assert placement is not None, "s3 fixture must load"
    assert placement.context == _CONTEXT_NAME
    assert placement.vpn_enforced is True
    assert placement.nodes == {
        "data-1": "10.8.0.11",
        "agent-1": "10.8.0.12",
        "apps-1": "10.8.0.13",
    }
    assert placement.modules["nginx"] == {"node": "apps-1"}
    assert placement.modules["node-metrics"] == {"mode": "all-nodes"}

    # Acceptance W1 (§5): точные наборы эффективных модулей по нодам
    assert resolve_node_modules(placement, "data-1") == [
        "backup-cron",
        "clickhouse",
        "log-collector",
        "minio",
        "node-metrics",
        "platform-secrets",
        "postgres",
        "redis",
        "service-exporters",
    ]
    assert resolve_node_modules(placement, "agent-1") == [
        "hermes-agent",
        "langfuse",
        "litellm",
        "log-collector",
        "node-metrics",
    ]
    assert resolve_node_modules(placement, "apps-1") == [
        "log-collector",
        "logging",
        "monitoring",
        "nginx",
        "node-metrics",
        "status-page",
    ]

    logger.info("[IMP:9][test_load_placement_s3_fixture][assert] W1 sets match for all 3 nodes")
    assert_ldd_imp9(caplog)


# endregion TEST_test_load_placement_s3_fixture


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2: single-node no-op
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_single_node_noop
# 🧪 TRAP[TEST] · 2026-08-22 · single-node no-op (DevPlan 010 §1.1/§2.2 п.1)
# · Regression: if load_placement raised on missing file, single-node context breaks (byte-compat)
# · Scenario: no placement.yaml → None (legacy resolve via node.yaml stays intact)
# · Last fail: N/A — new W0 test
# · Remove if: single-node legacy path is dropped
def test_single_node_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """No placement.yaml → load_placement returns None (single-node no-op)."""
    caplog.set_level(logging.DEBUG)

    missing = tmp_path / "placement.yaml"
    result = load_placement(missing)
    assert result is None, "missing placement.yaml must resolve to None (single-node no-op)"

    logger.info("[IMP:9][test_single_node_noop][assert] No-op None returned for missing placement.yaml")
    assert_ldd_imp9(caplog)


# endregion TEST_test_single_node_noop


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3: service_host remote vs local
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_service_host_remote_vs_local
# 🧪 TRAP[TEST] · 2026-08-22 · service_host cross-node host resolution (DevPlan 010 T2.1)
# · Regression: if remote host returned for co-located service, .env.platform gets a wrong IP
# · Scenario: s3 — postgres@agent-1 → 10.8.0.11; postgres@data-1 → None; node-metrics → None
# · Last fail: N/A — new W0 test
# · Remove if: cross-node host emission moves to a different resolver
def test_service_host_remote_vs_local(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """service_host: remote node → host; co-located/off/unknown → None."""
    caplog.set_level(logging.DEBUG)

    placement = load_placement(_FIXTURES_DIR / "s3.yaml")
    assert placement is not None

    # singleton на чужой ноде → host той ноды
    assert service_host(placement, "postgres", "agent-1") == "10.8.0.11"
    assert service_host(placement, "hermes-agent", "data-1") == "10.8.0.12"
    # та же нода → None (Docker DNS alias остаётся у вызывающего)
    assert service_host(placement, "postgres", "data-1") is None
    assert service_host(placement, "nginx", "apps-1") is None
    # all-nodes → co-located везде → None
    assert service_host(placement, "node-metrics", "data-1") is None
    assert service_host(placement, "log-collector", "apps-1") is None
    # модуль вне placement → None
    assert service_host(placement, "ghost-module", "data-1") is None
    # неизвестная consumer-нода → ConfigValidationError (fail-fast)
    with pytest.raises(ConfigValidationError, match="unknown consumer node"):
        service_host(placement, "postgres", "ghost-node")

    logger.info("[IMP:9][test_service_host_remote_vs_local][assert] Remote host / local None resolved")
    assert_ldd_imp9(caplog)


# endregion TEST_test_service_host_remote_vs_local


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4: vpn_enforced обязателен true
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_vpn_enforced_required
# 🧪 TRAP[TEST] · 2026-08-22 · vpn_enforced attestation required (DevPlan 010 §1.7/T2.0d)
# · Regression: if missing/false vpn_enforced passes, unencrypted cross-node traffic is un-attested
# · Scenario: placement.yaml without vpn_enforced → RED; with vpn_enforced: false → RED
# · Last fail: N/A — new W0 test
# · Remove if: vpn_enforced attestation is dropped
def test_vpn_enforced_required(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """vpn_enforced missing or false → ConfigValidationError."""
    caplog.set_level(logging.DEBUG)

    placement_path = tmp_path / "placement.yaml"
    base = "context: tronyx-lab\nnodes:\n  - name: data-1\n    host: 10.8.0.11\nmodules:\n  nginx: { node: data-1 }\n"

    # missing vpn_enforced
    placement_path.write_text(base, encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="vpn_enforced"):
        load_placement(placement_path)

    # vpn_enforced: false — тоже RED (аттестация не дана)
    placement_path.write_text(base + "vpn_enforced: false\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="vpn_enforced"):
        load_placement(placement_path)

    logger.info("[IMP:9][test_vpn_enforced_required][assert] vpn_enforced missing/false rejected")
    assert_ldd_imp9(caplog, require_imp9=False)


# endregion TEST_test_vpn_enforced_required


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5: публичный host отвергнут
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_public_host_rejected
# 🧪 TRAP[TEST] · 2026-08-22 · VPN-only host (DevPlan 010 §1.7, T0.1)
# · Regression: if public IP passes, cross-node traffic leaks outside the private/VPN channel
# · Scenario: host 8.8.8.8 → RED; host "not-an-ip" → RED (ipaddress, no regex)
# · Last fail: N/A — new W0 test
# · Remove if: VPN-only host attestation is dropped
def test_public_host_rejected(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """nodes[].host public IP or non-IP → ConfigValidationError (RFC1918/CGNAT only)."""
    caplog.set_level(logging.DEBUG)

    placement_path = tmp_path / "placement.yaml"
    base = (
        "context: tronyx-lab\n"
        "vpn_enforced: true\n"
        "nodes:\n"
        "  - name: data-1\n"
        "    host: {host}\n"
        "modules:\n"
        "  nginx: {{ node: data-1 }}\n"
    )

    # публичный IP
    placement_path.write_text(base.format(host="8.8.8.8"), encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="not a private address"):
        load_placement(placement_path)

    # не-IP (FQDN/мусор) — тоже RED: cross-node адрес обязан быть IP приватного диапазона
    placement_path.write_text(base.format(host="public.example.com"), encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="not a private address"):
        load_placement(placement_path)

    logger.info("[IMP:9][test_public_host_rejected][assert] Public/non-IP host rejected")
    assert_ldd_imp9(caplog, require_imp9=False)


# endregion TEST_test_public_host_rejected


# ═════════════════════════════════════════════════════════════════════════════
# TEST 6: полнота инвентаря (validate_topology)
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_module_inventory_completeness
# 🧪 TRAP[TEST] · 2026-08-22 · inventory completeness (DevPlan 010 §2.1/§1.3, T0.4(c))
# · Regression: if a module lacks a placement record, typo'd module names silently vanish from deploy
# · Scenario: exact inventory → green; extra inventory dir → RED; missing module dir → RED
# · Last fail: N/A — new W0 test
# · Remove if: completeness invariant is dropped
def test_module_inventory_completeness(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """validate_topology: full record set green; missing/extra inventory records RED."""
    caplog.set_level(logging.DEBUG)

    placement = _s3_placement(tmp_path)
    modules_dir = tmp_path / "modules"
    node_configs_dir = tmp_path / "node-configs"

    # GREEN: ровно те модули, что в placement (полнота + node-configs + context match)
    validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs_dir)

    # RED (c): модуль инвентаря БЕЗ записи размещения → неполнота
    extra_dir = modules_dir / "extra-module"
    extra_dir.mkdir()
    with pytest.raises(ConfigValidationError, match="has no placement record"):
        validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs_dir)
    extra_dir.rmdir()

    # RED (a): ключ placement вне инвентаря → модуль не найден
    missing_module_dir = modules_dir / "logging"
    missing_module_dir.rename(modules_dir / "logging-disabled")
    with pytest.raises(ConfigValidationError, match="not found in modules inventory"):
        validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs_dir)
    (modules_dir / "logging-disabled").rename(missing_module_dir)

    logger.info("[IMP:9][test_module_inventory_completeness][assert] Completeness green + both RED paths")
    assert_ldd_imp9(caplog)


# endregion TEST_test_module_inventory_completeness


# ═════════════════════════════════════════════════════════════════════════════
# TEST 7: off data-plane зависимость → RED; nginx off + живой hermes → GREEN
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_off_module_with_data_plane_dependent_red
# 🧪 TRAP[TEST] · 2026-08-22 · off-dependency rule (DevPlan 010 §2.2 п.8, T0.4(e))
# · Regression: if postgres off passes with placed langfuse, consumers deploy against nothing
# · Scenario: langfuse placed + postgres off → RED; nginx off + hermes placed → GREEN (infra excluded)
# · Last fail: N/A — new W0 test
# · Remove if: DATA_PLANE_DEPS heuristic is replaced by module.yaml classification
def test_off_module_with_data_plane_dependent_red(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Off data-plane dep of a placed module → RED; infra dep (nginx) off → GREEN."""
    caplog.set_level(logging.DEBUG)

    # ── RED: langfuse размещён, его data-plane зависимость postgres — off ──
    red_modules = {
        "langfuse": ["postgres", "clickhouse"],
        "postgres": [],
        "clickhouse": [],
        "node-metrics": [],
        "log-collector": [],
    }
    red_modules_dir = _make_module_tree(tmp_path, red_modules)
    red_node_configs = _make_node_configs(tmp_path / "red", _CONTEXT_NAME, ["data-1"])
    red_placement = Placement(
        context=_CONTEXT_NAME,
        vpn_enforced=True,
        nodes={"data-1": "10.8.0.11"},
        modules={
            "langfuse": {"node": "data-1"},
            "postgres": {"mode": "off"},
            "clickhouse": {"node": "data-1"},
            "node-metrics": {"mode": "all-nodes"},
            "log-collector": {"mode": "all-nodes"},
        },
    )
    with pytest.raises(ConfigValidationError, match="depends on data-plane service 'postgres'"):
        validate_topology(red_placement, modules_dir=red_modules_dir, node_configs_dir=red_node_configs)

    # ── GREEN: nginx off при живом hermes-agent (nginx — infra, исключён из DATA_PLANE_DEPS) ──
    green_modules = {
        "hermes-agent": ["nginx", "postgres", "redis", "litellm"],
        "nginx": [],
        "postgres": [],
        "redis": [],
        "litellm": ["postgres"],
        "node-metrics": [],
        "log-collector": [],
    }
    green_modules_dir = _make_module_tree(tmp_path / "green", green_modules)
    green_node_configs = _make_node_configs(tmp_path / "green", _CONTEXT_NAME, ["data-1"])
    green_placement = Placement(
        context=_CONTEXT_NAME,
        vpn_enforced=True,
        nodes={"data-1": "10.8.0.11"},
        modules={
            "hermes-agent": {"node": "data-1"},
            "nginx": {"mode": "off"},
            "postgres": {"node": "data-1"},
            "redis": {"node": "data-1"},
            "litellm": {"node": "data-1"},
            "node-metrics": {"mode": "all-nodes"},
            "log-collector": {"mode": "all-nodes"},
        },
    )
    validate_topology(green_placement, modules_dir=green_modules_dir, node_configs_dir=green_node_configs)

    logger.info("[IMP:9][test_off_module_with_data_plane_dependent_red][assert] postgres-off RED, nginx-off GREEN")
    assert_ldd_imp9(caplog)


# endregion TEST_test_off_module_with_data_plane_dependent_red


# ═════════════════════════════════════════════════════════════════════════════
# TEST 8: exposed-проект обязан быть на nginx-ноде (S2b multi-ingress)
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_exposed_project_requires_nginx_node
# 🧪 TRAP[TEST] · 2026-08-22 · multi-ingress co-location (DevPlan 010 §2.2 п.7, T0.4(d))
# · Regression: if exposed project lands off the nginx nodes[], its FQDN is never served
# · Scenario: S2b nginx {nodes:[apps-critical,apps-canary]} — green; target_node data-1 → RED;
# ·           duplicate FQDN cross-node → RED
# · Last fail: N/A — new W0 test
# · Remove if: multi-ingress target_node constraint is dropped
def test_exposed_project_requires_nginx_node(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Exposed projects must target an nginx node; cross-node FQDN duplicates rejected (S2b)."""
    caplog.set_level(logging.DEBUG)

    placement = load_placement(_FIXTURES_DIR / "s2b.yaml")
    assert placement is not None
    modules_dir = _make_module_tree(tmp_path, _CONTEXT_MODULE_DEPS)
    node_configs_dir = _make_node_configs(tmp_path / "nodes", _CONTEXT_NAME, _S2B_NODES)

    # GREEN: exposed-проекты на apps-critical / apps-canary (обе — nginx-ноды из nodes[])
    green_projects = [
        {"name": "critical-web", "domain": "critical.example.com", "expose": True, "target_node": "apps-critical"},
        {"name": "canary-web", "domain": "canary.example.com", "expose": True, "target_node": "apps-canary"},
        {"name": "headless-bot", "domain": "", "expose": False, "target_node": "data-1"},
    ]
    validate_topology(
        placement,
        modules_dir=modules_dir,
        node_configs_dir=node_configs_dir,
        projects_scan=lambda: green_projects,
    )

    # RED (d): exposed-проект на data-1 — не nginx-нода
    bad_projects = [
        {"name": "leaky-web", "domain": "leaky.example.com", "expose": True, "target_node": "data-1"},
    ]
    with pytest.raises(ConfigValidationError, match="is not an nginx node"):
        validate_topology(
            placement,
            modules_dir=modules_dir,
            node_configs_dir=node_configs_dir,
            projects_scan=lambda: bad_projects,
        )

    # RED (d): дубликат FQDN кросс-нодово (apps-critical и apps-canary)
    dup_projects = [
        {"name": "dup-a", "domain": "dup.example.com", "expose": True, "target_node": "apps-critical"},
        {"name": "dup-b", "domain": "dup.example.com", "expose": True, "target_node": "apps-canary"},
    ]
    with pytest.raises(ConfigValidationError, match="duplicate exposed FQDN"):
        validate_topology(
            placement,
            modules_dir=modules_dir,
            node_configs_dir=node_configs_dir,
            projects_scan=lambda: dup_projects,
        )

    logger.info("[IMP:9][test_exposed_project_requires_nginx_node][assert] S2b green + both RED paths")
    assert_ldd_imp9(caplog)


# endregion TEST_test_exposed_project_requires_nginx_node


# ═════════════════════════════════════════════════════════════════════════════
# TEST 9: неизвестная нода отвергнута
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_unknown_node_rejected
# 🧪 TRAP[TEST] · 2026-08-22 · unknown node fail-fast (DevPlan 010 §1.3)
# · Regression: if unknown node silently resolves to [], a typo'd node disables all its modules
# · Scenario: resolve_node_modules("ghost") → RED; lint_drift(..., "ghost") → RED
# · Last fail: N/A — new W0 test
# · Remove if: unknown-node fail-fast is dropped
def test_unknown_node_rejected(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Unknown node in resolver/lint_drift → ConfigValidationError."""
    caplog.set_level(logging.DEBUG)

    placement = _s3_placement(tmp_path)

    with pytest.raises(ConfigValidationError, match="unknown node"):
        resolve_node_modules(placement, "ghost-node")
    with pytest.raises(ConfigValidationError, match="unknown node"):
        lint_drift(["nginx"], placement, "ghost-node")

    logger.info("[IMP:9][test_unknown_node_rejected][assert] Unknown node rejected in resolve + lint_drift")
    assert_ldd_imp9(caplog, require_imp9=False)


# endregion TEST_test_unknown_node_rejected


# region TEST_firewall_placement_args


# 🧐 DR-H1 fix (DevPlan 010 T2.3 wiring): фазы φ1/φ11 передают --placement в firewall.sh
# · Rejected: деривация пути в каждой фазе локально
# · Reason: единая деривация рядом с load_placement (SoT), 2 потребителя — shared/AGENTS.md п.3
def test_firewall_placement_args_with_placement(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """placement.yaml существует → ["--placement", <path>] с канонической деривацией."""
    caplog.set_level(logging.DEBUG)
    node_configs = _make_node_configs(tmp_path, _CONTEXT_NAME, ["data-1"])
    node_yaml = node_configs / "data-1" / "node.yaml"
    ctx_dir = tmp_path / "node-configs" / _CONTEXT_NAME
    ctx_dir.mkdir()
    write_yaml(ctx_dir / "placement.yaml", {"context": _CONTEXT_NAME, "vpn_enforced": True})

    from core.internal.shared.placement import firewall_placement_args

    args = firewall_placement_args(node_yaml)

    print("--- LDD TRAJECTORY ---")
    for record in caplog.records:
        if "[firewall_placement_args]" in record.getMessage():
            print(record.getMessage())
    print("--- END LDD TRAJECTORY ---")

    assert args[:1] == ["--placement"], "флаг --placement отсутствует при живом placement.yaml"
    assert Path(args[1]).is_file(), "передан путь на несуществующий файл"
    assert args[1] == str(ctx_dir / "placement.yaml")
    assert_ldd_imp9(caplog)


def test_firewall_placement_args_single_node_noop(tmp_path: Path) -> None:
    """Нет placement.yaml → [] (single-node no-op; байт-совместимость прежнего вызова)."""
    node_configs = _make_node_configs(tmp_path, _CONTEXT_NAME, ["data-1"])
    node_yaml = node_configs / "data-1" / "node.yaml"

    from core.internal.shared.placement import firewall_placement_args

    assert firewall_placement_args(node_yaml) == []


def test_firewall_placement_args_unreadable_node_yaml_fail_open(tmp_path: Path) -> None:
    """Нечитаемый node.yaml → [] fail-open (φ1 может идти до полной валидации)."""
    broken = tmp_path / "broken" / "node.yaml"
    broken.parent.mkdir()
    broken.write_text(":::: not-yaml [\n", encoding="utf-8")

    from core.internal.shared.placement import firewall_placement_args

    assert firewall_placement_args(broken) == []


# endregion TEST_firewall_placement_args


# region TEST_form_node_refs_validation


# 🧐 DR-M1 fix (аудит DevPlan 010): ссылки форм на ноды валидируются при загрузке
# · Rejected: per-consumer проверки (resolver/service_host/firewall по отдельности)
# · Reason: опечатка {node: data-9} молча выпадала из резолва — fail-fast в load_placement
#   закрывает всех потребителей одной проверкой
def test_load_placement_unknown_node_ref_rejected(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """{node: data-9} при known {data-1} → ConfigValidationError при ЗАГРУЗКЕ (не тихий пропуск)."""
    caplog.set_level(logging.DEBUG)
    placement_path = tmp_path / "placement.yaml"
    write_yaml(
        placement_path,
        {
            "context": _CONTEXT_NAME,
            "vpn_enforced": True,
            "nodes": [{"name": "data-1", "host": "10.8.0.11"}],
            "modules": {"postgres": {"node": "data-9"}},
        },
    )

    with pytest.raises(ConfigValidationError, match="data-9"):
        load_placement(placement_path)

    logger.info("[IMP:9][test_refs][assert] unknown {node} ref rejected at load time")
    assert_ldd_imp9(caplog, require_imp9=False)


def test_load_placement_unknown_nodes_list_ref_rejected(tmp_path: Path) -> None:
    """{nodes: [apps-a, apps-z]} с неизвестным apps-z → ConfigValidationError."""
    placement_path = tmp_path / "placement.yaml"
    write_yaml(
        placement_path,
        {
            "context": _CONTEXT_NAME,
            "vpn_enforced": True,
            "nodes": [{"name": "apps-a", "host": "10.8.0.13"}],
            "modules": {"nginx": {"nodes": ["apps-a", "apps-z"]}},
        },
    )

    with pytest.raises(ConfigValidationError, match="apps-z"):
        load_placement(placement_path)


def test_service_host_nodes_branch_unknown_raises_not_keyerror(tmp_path: Path) -> None:
    """DR-L4 fix: nodes[0] вне placement.nodes → ConfigValidationError (exit 4), не KeyError."""
    from core.internal.shared.placement import service_host

    # Программно-сконструированный Placement (load-validation обошла): consumer известен,
    # НЕ входит в nodes[] модуля, а node_list[0] неизвестен — ровно KeyError-ветка
    placement = Placement(
        context="ctx",
        vpn_enforced=True,
        nodes={"known-1": "10.8.0.11", "known-2": "10.8.0.13"},
        modules={"nginx": {"nodes": ["ghost-x", "known-2"]}},
    )

    with pytest.raises(ConfigValidationError, match="ghost-x"):
        service_host(placement, "nginx", "known-1")


# endregion TEST_form_node_refs_validation
