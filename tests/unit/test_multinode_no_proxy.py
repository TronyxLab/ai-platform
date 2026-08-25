"""Unit-тесты EXTRA_NO_PROXY content (аудит DevPlan 010, T2.5 test-gap).

# GREP_SUMMARY: test_multinode_no_proxy extra-no-proxy node-ips leading-comma sorted placement t2.5
# STRUCTURE: ▶ placement fixture (s3) → ◇ multinode_runtime_env → ⊕ EXTRA_NO_PROXY == ","+sorted(hosts) → ⎋
# region MODULE_CONTRACT
## @purpose  Контракт ЗНАЧЕНИЯ EXTRA_NO_PROXY (T2.5): ведущая запятая + отсортированные host
##           IP ВСЕХ нод контекста — прокси-канал не перехватывает cross-node вызовы.
##           Раньше ассертилось только наличие passthrough, не содержимое.
## @scope    core/internal/bootstrap/deploy/deploy_orchestrator.py::multinode_runtime_env (pure)
## @invariants
##   - Формат: "," + ",".join(sorted(placement.nodes.values())) — ведущая запятая ОБЯЗАТЕЛЬНА
##     (интерполяция compose "${NO_PROXY:-...}${EXTRA_NO_PROXY}" плоская)
##   - Single-node (placement None) → _apply_multinode_runtime_env не трогает os.environ
## @rationale Содержательный контракт значения — защита от silent-деградации сортировки/формата.
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.deploy_orchestrator import multinode_runtime_env
from core.internal.shared.placement import load_placement

pytestmark = pytest.mark.static_audit

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "placement"


def test_extra_no_proxy_content_s3_fixture(caplog: pytest.LogCaptureFixture) -> None:
    """S3: EXTRA_NO_PROXY == ','+sorted hosts всех 3 нод; SERVICE_BIND_HOST/LOKI_TENANT консистентны."""
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None

    env = multinode_runtime_env(placement, "apps-1")

    expected = "," + ",".join(sorted(placement.nodes.values()))
    print("--- LDD TRAJECTORY ---")
    print(f"EXTRA_NO_PROXY={env.get('EXTRA_NO_PROXY')}")
    print("--- END LDD TRAJECTORY ---")

    # [IMP:9] контракт значения: все IP нод, отсортированы, с ведущей запятой
    assert env["EXTRA_NO_PROXY"] == expected, f"контракт T2.5 нарушен: {env['EXTRA_NO_PROXY']!r} != {expected!r}"
    assert env["EXTRA_NO_PROXY"].startswith(","), "ведущая запятая обязательна (плоская интерполяция compose)"
    for ip in ("10.8.0.11", "10.8.0.12", "10.8.0.13"):
        assert ip in env["EXTRA_NO_PROXY"], f"IP ноды {ip} отсутствует в no-proxy"
    assert env["SERVICE_BIND_HOST"] == "10.8.0.13", "SERVICE_BIND_HOST должен быть host'ом consumer-ноды"
    assert env["LOKI_TENANT"] == "tronyx-lab", "tenant = имя контекста"


def test_extra_no_proxy_single_node_env_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-node (placement None): apply — no-op, os.environ не мутируется (байт-совместимость §1.1)."""
    from core.internal.bootstrap.deploy.deploy_orchestrator import _apply_multinode_runtime_env

    monkeypatch.setenv("EXTRA_NO_PROXY", "sentinel")
    monkeypatch.setenv("SERVICE_BIND_HOST", "sentinel")

    _apply_multinode_runtime_env(None, "")

    import os

    assert os.environ["EXTRA_NO_PROXY"] == "sentinel", "single-node: os.environ изменён (регрессия §1.1)"
    assert os.environ["SERVICE_BIND_HOST"] == "sentinel"
