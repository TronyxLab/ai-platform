"""Gate-тесты DevPlan 010: контракт placement (T0.6) + drift-WARNING семантика (T1.2)."""
# GREP_SUMMARY: test_gate_placement placement gate fail-fast peer-firewall matrix stale-reconcile vpn_enforced exposed nginx drift-warning schema-parity single-node
# STRUCTURE: ▶ fixtures S2/S2b/S3 → ◇ load/resolve/validate → 🔒 fail-fast матрица → ⊕ firewall peer/delete/verify → ∑ parity+drift → ⎋ single-node no-op
# region MODULE_CONTRACT
## @purpose  Гейты контракта размещения модулей (DevPlan 010): схема↔загрузчик parity,
##           fail-fast матрица (неизвестный модуль/нода, неполнота, публичный host, отсутствие
##           vpn_enforced, off-зависимости data-plane, exposed вне nginx-нод), peer-firewall
##           канон (матрица портов, delete с source IP, verify-семантика), 1-контекст-гейт,
##           drift-WARNING (не RED), single-node no-op
## @scope    core/internal/shared/placement.py; core/internal/bootstrap/firewall.py;
##           core/internal/shared/node_yaml/validation.py; core/schemas/placement.schema.json
## @invariants
##   - Каждый сценарий падения из §1.3/§4 плана ловится соответствующим тестом
##   - Drift node.yaml↔placement — WARNING, не ошибка (placement авторитетен)
##   - Схема НЕ содержит follow/public_host (r2)
## @rationale Gate trinity: tests/gates/ + pytest.mark.gate + GENERATED manifest (регенерация
##           generate-manifests). LDD-телеметрия через ldd_trajectory.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap import firewall
from core.internal.bootstrap.deploy.deploy_orchestrator import _parse_modules
from core.internal.shared.placement import (
    Placement,
    lint_drift,
    load_placement,
    resolve_node_modules,
    validate_topology,
)
from core.internal.shared.schema_validator import validate_dict_against_schema
from tests.helpers.gate_helpers import repo_root

pytestmark = pytest.mark.gate

logger = logging.getLogger(__name__)

FIXTURES = repo_root() / "tests" / "fixtures" / "placement"
SCHEMA = repo_root() / "core" / "schemas" / "placement.schema.json"
REPO_MODULES_DIR = repo_root() / "core" / "modules"

S2 = FIXTURES / "s2.yaml"
S2B = FIXTURES / "s2b.yaml"
S3 = FIXTURES / "s3.yaml"


def _load(path: Path) -> Placement:
    placement = load_placement(path)
    assert placement is not None, f"{path} должен загружаться (fixture битая?)"
    return placement


# ═══════════════════════════════════════════════════════════════════════════
# region SECTION_fail_fast
# ═══════════════════════════════════════════════════════════════════════════


def test_multi_context_rejected(tmp_path: Path) -> None:
    """contexts[] > 1 → ConfigValidationError («1 нода = 1 контекст», T0.3)."""
    from core.internal.shared.exceptions import ConfigValidationError
    from core.internal.shared.node_yaml import NodeYaml

    node_file = tmp_path / "node.yaml"
    node_file.write_text(
        "node:\n  name: n1\n  host: 10.8.0.1\ncontexts:\n  - name: ctx-a\n  - name: ctx-b\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError, match="1 нода = 1 контекст"):
        NodeYaml(str(node_file)).validate()


def test_unknown_module_or_node_red(tmp_path: Path) -> None:
    """Placement ссылается на несуществующий модуль/ноду → RED (fail-fast §1.3)."""
    from core.internal.shared.exceptions import ConfigValidationError

    placement = _load(S3)

    with pytest.raises(ConfigValidationError):
        resolve_node_modules(placement, "ghost-node")

    # Неизвестный модуль: синтетическое дерево инвентаря без директории модуля
    modules_dir = tmp_path / "modules"
    (modules_dir / "postgres").mkdir(parents=True)
    node_configs = tmp_path / "node-configs"
    for name in placement.nodes:
        d = node_configs / name
        d.mkdir(parents=True)
        (d / "node.yaml").write_text(
            f"node:\n  name: {name}\n  host: 10.8.0.1\ncontexts:\n  - name: {placement.context}\n",
            encoding="utf-8",
        )
    with pytest.raises(ConfigValidationError):
        validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs)


def test_public_host_rejected(tmp_path: Path) -> None:
    """nodes[].host = публичный IP → ConfigValidationError (инвариант 7 VPN-only)."""
    from core.internal.shared.exceptions import ConfigValidationError

    tmp = tmp_path / "_gate_pub_host.yaml"
    tmp.write_text(
        "context: pub-lab\nvpn_enforced: true\n"
        "nodes:\n  - name: edge-1\n    host: 203.0.113.5\n"
        "modules:\n  postgres: {node: edge-1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError):
        load_placement(tmp)


def test_vpn_enforced_required(tmp_path: Path) -> None:
    """Multi-node без vpn_enforced:true → ConfigValidationError (инвариант 7)."""
    from core.internal.shared.exceptions import ConfigValidationError

    tmp = tmp_path / "_gate_no_vpn.yaml"
    tmp.write_text(
        "context: novpn-lab\nvpn_enforced: false\nnodes:\n  - name: a\n    host: 10.8.0.1\n"
        "modules:\n  postgres: {node: a}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError):
        load_placement(tmp)


# endregion SECTION_fail_fast


# ═══════════════════════════════════════════════════════════════════════════
# region SECTION_topology
# ═══════════════════════════════════════════════════════════════════════════


def _topo_tree(tmp_path: Path, placement: Placement | None = None) -> tuple[Path, Path]:
    """Синтетическое дерево инвентаря/нод под заданный placement (по умолчанию S3)."""
    modules_dir = tmp_path / "modules"
    node_configs = tmp_path / "node-configs"
    placement = placement or _load(S3)
    for mod in placement.modules:
        (modules_dir / mod).mkdir(parents=True, exist_ok=True)
        (modules_dir / mod / "module.yaml").write_text("name: x\ndepends_on: []\n", encoding="utf-8")
    for name in placement.nodes:
        d = node_configs / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "node.yaml").write_text(
            f"node:\n  name: {name}\n  host: 10.8.0.1\ncontexts:\n  - name: {placement.context}\n",
            encoding="utf-8",
        )
    return modules_dir, node_configs


def test_module_inventory_completeness(tmp_path: Path) -> None:
    """В multi-node placement без записи для модуля инвентаря → RED (полнота записей)."""
    from core.internal.shared.exceptions import ConfigValidationError

    placement = _load(S3)
    modules_dir, node_configs = _topo_tree(tmp_path)
    validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs)

    # Добавляем «сироту» в инвентарь без записи в placement → следующий вызов RED
    (modules_dir / "orphan-mod").mkdir()
    with pytest.raises(ConfigValidationError, match=r"полнот|completeness|orphan|без записи"):
        validate_topology(placement, modules_dir=modules_dir, node_configs_dir=node_configs)


def test_off_module_with_data_plane_dependent_red(tmp_path: Path) -> None:
    """off у postgres при размещённом langfuse → RED; off у nginx при живом hermes → GREEN."""
    from core.internal.shared.exceptions import ConfigValidationError

    modules_dir, node_configs = _topo_tree(tmp_path)
    # langfuse требует postgres (data-plane dep): пишем depends_on в module.yaml langfuse
    lf = modules_dir / "langfuse" / "module.yaml"
    if lf.exists():
        lf.write_text("name: langfuse\ndepends_on: [postgres]\n", encoding="utf-8")

    placement = _load(S3)
    off_pg = Placement(
        context=placement.context,
        vpn_enforced=placement.vpn_enforced,
        nodes=dict(placement.nodes),
        modules={**placement.modules, "postgres": {"mode": "off"}},
    )
    with pytest.raises(ConfigValidationError):
        validate_topology(off_pg, modules_dir=modules_dir, node_configs_dir=node_configs)

    # nginx — инфра-dep: off при живом hermes-agent валидно
    if "nginx" in placement.modules and "hermes-agent" in placement.modules:
        off_nginx = Placement(
            context=placement.context,
            vpn_enforced=placement.vpn_enforced,
            nodes=dict(placement.nodes),
            modules={**placement.modules, "nginx": {"mode": "off"}},
        )
        hm = modules_dir / "hermes-agent" / "module.yaml"
        hm.write_text("name: hermes-agent\ndepends_on: [nginx]\n", encoding="utf-8")
        validate_topology(off_nginx, modules_dir=modules_dir, node_configs_dir=node_configs)


def test_exposed_project_requires_nginx_node(tmp_path: Path) -> None:
    """Exposed-проект вне nginx-нод → RED; дубликат FQDN кросс-нодово → RED; S2b → GREEN."""

    def scan_bad():
        return [
            {"project": "web-critical", "expose": True, "domain": "web.example.com", "target_node": "apps-canary"},
            {"project": "web-dup", "expose": True, "domain": "web.example.com", "target_node": "apps-critical"},
        ]

    def scan_ok():
        return [{"project": "web-ok", "expose": True, "domain": "web.example.com", "target_node": "apps-canary"}]

    def scan_outside():
        return [{"project": "web-out", "expose": True, "domain": "x.example.com", "target_node": "data-1"}]

    s2b = _load(S2B)
    modules_dir, node_configs = _topo_tree(tmp_path, s2b)

    # GREEN: exposed на ноде из nginx nodes[]
    validate_topology(s2b, modules_dir=modules_dir, node_configs_dir=node_configs, projects_scan=scan_ok)

    from core.internal.shared.exceptions import ConfigValidationError

    # RED: дубликат FQDN на другой ноде
    with pytest.raises(ConfigValidationError):
        validate_topology(s2b, modules_dir=modules_dir, node_configs_dir=node_configs, projects_scan=scan_bad)

    # RED: exposed вне nginx-нод (data-1 не в nodes[] nginx)
    with pytest.raises(ConfigValidationError):
        validate_topology(s2b, modules_dir=modules_dir, node_configs_dir=node_configs, projects_scan=scan_outside)


# endregion SECTION_topology


# ═══════════════════════════════════════════════════════════════════════════
# region SECTION_peer_firewall
# ═══════════════════════════════════════════════════════════════════════════


def test_peer_firewall_matrix_canonical() -> None:
    """build_rules(S3) содержит peer-открытия матрицы; БЕЗ Anywhere и БЕЗ 5432."""
    s3 = _load(S3)
    rules = firewall.build_rules([], peer_rules=firewall.build_peer_rules(s3))
    flat = [" ".join(r) for r in rules]

    joined = "\n".join(flat)
    assert "from" in joined, "peer-правила обязаны быть source-scoped"
    # Порт-матрица канона (§9): суммарно по открытиям S3.
    # 9113 НЕ входит (DR-H2 fix): nginx-exporter co-located с monitoring на apps-1 →
    # локальный Docker-DNS scrape, кросс-нодовое правило не порождается (co-location skip)
    matrix = {"6432", "6379", "9000", "8123", "19000", "3100", "9100", "8080", "9187", "9121"}
    found_ports = {p for p in matrix if f"port {p}/tcp" in joined}
    logger.info("[IMP:8][gate-placement][firewall] covered ports: %s", sorted(found_ports))
    assert found_ports == matrix, f"неполная порт-матрица: отсутствуют {matrix - found_ports}"
    # 5432 ЗАПРЕЩЁН (потребители едут с postgres)
    assert "port 5432/tcp" not in joined, "прямой 5432 публиковать запрещено"
    # Ни одного Anywhere-allow среди platform-peer
    for line in flat:
        if "# platform-peer" in line or ("allow" in line and "port" in line):
            assert " anywhere" not in f" {line} ", f"Anywhere-публикация запрещена: {line}"


def test_stale_reconcile_delete_carries_source() -> None:
    """≥2 пира на одном порту → каждая delete-команда несёт `from <ip>` (инвариант 4)."""
    status = (
        "6432/tcp                     ALLOW IN    10.8.0.12   # platform-peer-6432-agent-1\n"
        "6432/tcp                     ALLOW IN    10.8.0.13   # platform-peer-6432-apps-1\n"
        "6400/tcp                     ALLOW IN    Anywhere    # platform-baseline-stale\n"
    )
    deletes = firewall.collect_stale_platform_rules(status, desired_allow=set(), peer_ports={6432})
    flat = [" ".join(d) for d in deletes]
    print("--- LDD TRAJECTORY (deletes) ---")
    print("\n".join(flat))
    print("--- END ---")
    for d in flat:
        if "6432" in d:
            assert " from " in d and "10.8.0." in d, f"delete обязан нести source IP: {d}"
    assert any("6400" in d for d in flat), "stale baseline-порт должен удаляться"


def test_verify_firewall_accepts_peer_allow() -> None:
    """Peer-ALLOW от известного пира = PASS; Anywhere на том же порту = FAIL."""
    base = (
        "Status: active\n"
        "22/tcp                      ALLOW IN    Anywhere\n"
        "80/tcp                      ALLOW IN    Anywhere\n"
        "443/tcp                     ALLOW IN    Anywhere\n"
        "5432/tcp                    DENY IN     Anywhere\n"
    )
    ok_status = base + "6432/tcp                ALLOW IN    10.8.0.12   # platform-peer-6432-agent-1\n"
    bad_anywhere = base + "6432/tcp                ALLOW IN    Anywhere    # platform-peer-violation\n"

    # Multi-node: peer-scoped ALLOW от известного пира → PASS
    assert firewall.verify_firewall(ok_status, zabbix_monitoring=False, peer_ips={"10.8.0.12"}) is True
    # Single-node (peer_ips=None): Anywhere на кросс-нодовом порту → FAIL
    assert firewall.verify_firewall(bad_anywhere, zabbix_monitoring=False, peer_ips=None) is False
    # Multi-node с ДРУГИМ пиром: источник вне peer_ips → FAIL
    assert firewall.verify_firewall(ok_status, zabbix_monitoring=False, peer_ips={"10.8.0.99"}) is False


# endregion SECTION_peer_firewall


# ═══════════════════════════════════════════════════════════════════════════
# region SECTION_parity_drift_noop
# ═══════════════════════════════════════════════════════════════════════════


def test_schema_loader_parity() -> None:
    """Схема отвергает follow/public_host (r2); все фикстуры проходят валидацию."""
    import yaml as yaml_mod

    schema = (
        yaml_mod.safe_load(SCHEMA.read_text(encoding="utf-8"))
        if SCHEMA.suffix in {".yml", ".yaml"}
        else __import__("json").loads(SCHEMA.read_text(encoding="utf-8"))
    )
    base = {
        "context": "parity-lab",
        "vpn_enforced": True,
        "nodes": [{"name": "a", "host": "10.8.0.1"}],
        "modules": {"postgres": {"node": "a"}},
    }
    assert validate_dict_against_schema(base, schema) == []

    banned = {"follow": "a", "public_host": "1.2.3.4"}
    for key, value in banned.items():
        bad = {**base, "modules": {"postgres": {"node": "a", key: value}}}
        errors = validate_dict_against_schema(bad, schema)
        logger.info("[IMP:8][gate-placement][parity] %s rejected: %s", key, errors)
        assert errors, f"схема обязана отвергнуть форму {key} (r2: удалена)"

    for fx in (S2, S2B, S3):
        data = yaml_mod.safe_load(fx.read_text(encoding="utf-8"))
        assert validate_dict_against_schema(data, schema) == [], f"{fx.name}: parity нарушена"


def test_drift_is_warning_not_error(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T1.2: enabled в node.yaml но не размещён → WARNING с repair-подсказкой; деплой живёт."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(
        "node:\n  name: apps-1\n  host: 10.8.0.13\ncontexts:\n  - name: tronyx-lab\n"
        "modules:\n  monitoring: {enabled: true}\n  ghost-mod: {enabled: true}\n",
        encoding="utf-8",
    )
    # placement отсутствует → легаси путь (drift не применим); проверяем lint напрямую + deploy-WARNING
    s3 = _load(S3)
    warnings = lint_drift(["monitoring", "ghost-mod"], s3, "apps-1")
    print("--- LDD TRAJECTORY (drift) ---")
    print("\n".join(warnings))
    print("--- END ---")
    assert warnings, "ожидались drift-WARNING строки"
    assert all("placement" in w.lower() for w in warnings), "нет repair-подсказки"

    # Интеграция: _parse_modules с placement-файлом не бросает из-за drift
    root = tmp_path / "cfg"
    (root / "apps-1").mkdir(parents=True)
    (root / "tronyx-lab").mkdir()
    (root / "apps-1" / "node.yaml").write_text(node_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "tronyx-lab" / "placement.yaml").write_text(S3.read_text(encoding="utf-8"), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        lists = _parse_modules(str(root / "apps-1" / "node.yaml"), "", "")
    assert lists.enabled_names, "деплой-резолв обязан остаться авторитетным из placement"


def test_single_node_noop_gate() -> None:
    """Нет placement.yaml → load_placement=None (легаси no-op, инвариант 1)."""
    assert load_placement(FIXTURES / "_absent_.yaml") is None


# endregion SECTION_parity_drift_noop
