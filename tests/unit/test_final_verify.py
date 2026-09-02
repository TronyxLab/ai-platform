"""
# GREP_SUMMARY: test-final-verify, devplan-029, T5, end-state, exit-10, cert-missing, noop-rerun, dependency-graph, honest-exit0
# STRUCTURE: ▶ fixture node.yaml/state-machine → ◇ dependency graph (converge_services → final_verify) → ◇ order (после converge) →
#           ◇ missing cert → ⚡PlatformFatalError exit 10 → ◇ full pass → True ×2 (идемпотентность, повтор no-op) → ⎋ LDD
# region MODULE_CONTRACT
## @purpose  Unit tests для φ-final-verify (DevPlan 029 T5, core/internal/bootstrap/lifecycle/
##           phases/final_verify.py): 4 end-state assertion'а после φ8.5; FAIL → PlatformFatalError
##           (exit 10); done-фаза → повтор bootstrap = no-op (AC8 — через state skip + чистая
##           повторная идемпотентность функции).
## @scope    Pure unit tests — tmp_path, monkeypatch helpers (0 subprocess, 0 Docker).
## @invariants
##   - dependency graph: final_verify ← converge_services (φ-final-verify после φ8.5)
##   - INIT_PHASE_ORDER: converge_services предшествует final_verify (последняя init-фаза)
##   - Missing cert (on-disk convergence False) → PlatformFatalError exit_code == 10
##   - Полный pass (без доменов, no required∧sops) → True; повторный вызов → True (no-op)
## @changes 2026-09-02 · DevPlan 029 T5 — created
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.phases.final_verify import phase_final_verify
from core.internal.bootstrap.lifecycle.state_machine import BootstrapPhase, _phase_dependency_graph
from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


def _node_yaml(tmp_path: Path, *, with_project: bool = False) -> Path:
    """Минимальный node.yaml (contexts-only или +project с доменом)."""
    path = tmp_path / "node.yaml"
    text = "contexts:\n  - name: testctx\n"
    if with_project:
        text += "projects:\n  - name: myapp\n    domain: app.example.com\n"
    path.write_text(text, encoding="utf-8")
    return path


def _core_dir_with_empty_manifest(tmp_path: Path) -> Path:
    """core_dir с secrets-manifest.yaml без required∧sops (verifier trivially satisfied)."""
    core = tmp_path / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "secrets-manifest.yaml").write_text("secrets: []\n", encoding="utf-8")
    return core


# ═══════════════════════════════════════════════════════════════════
# Dependency graph / order (TEST_SPEC: test_final_verify_after_converge)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_final_verify_after_converge
def test_final_verify_after_converge() -> None:
    """Dependency graph: converge_services → final_verify (φ-final-verify после φ8.5)."""
    assert _phase_dependency_graph[BootstrapPhase.FINAL_VERIFY] == {BootstrapPhase.CONVERGE_SERVICES}, (
        "final_verify обязан зависеть от converge_services (T5)"
    )
    order = list(BootstrapPhase.INIT_PHASE_ORDER)
    assert order.index(BootstrapPhase.CONVERGE_SERVICES) < order.index(BootstrapPhase.FINAL_VERIFY), (
        "final_verify идёт ПОСЛЕ converge_services в INIT_PHASE_ORDER"
    )
    assert order[-1] == BootstrapPhase.FINAL_VERIFY, "final_verify — последняя INIT-фаза"
    logger.info("[IMP:9][test_final_verify_after_converge] PASS: φ-final-verify после φ8.5 (graph+order)")


# endregion FUNC_test_final_verify_after_converge


# ═══════════════════════════════════════════════════════════════════
# Exit-10 fail-loud (TEST_SPEC: test_missing_cert_fails)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_missing_cert_fails
def test_missing_cert_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Серт exposed-домена отсутствует (on-disk convergence False) → PlatformFatalError exit 10."""
    from core.internal.bootstrap.lifecycle.helpers import domains

    node_yaml = _node_yaml(tmp_path, with_project=True)
    core_dir = _core_dir_with_empty_manifest(tmp_path)
    monkeypatch.setattr(domains, "ssl_certs_converged_on_disk", lambda _c, _n: False)

    with pytest.raises(PlatformFatalError) as exc_info:
        phase_final_verify(str(core_dir), "tronyx-vps", str(node_yaml), env={})
    assert exc_info.value.exit_code == 10, f"missing cert → exit 10, получено {exc_info.value.exit_code}"
    assert "final_verify FAIL (a)" in str(exc_info.value)
    logger.info("[IMP:9][test_missing_cert_fails] PASS: missing cert → PlatformFatalError(10)")


# endregion FUNC_test_missing_cert_fails


# region FUNC_test_undeterminable_cert_fails_closed
def test_undeterminable_cert_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Экстрактор сертов недоступен (None) → fail-closed exit 10 (undeterminable ≠ converged)."""
    from core.internal.bootstrap.lifecycle.helpers import domains

    node_yaml = _node_yaml(tmp_path, with_project=True)
    core_dir = _core_dir_with_empty_manifest(tmp_path)
    monkeypatch.setattr(domains, "ssl_certs_converged_on_disk", lambda _c, _n: None)

    with pytest.raises(PlatformFatalError) as exc_info:
        phase_final_verify(str(core_dir), "tronyx-vps", str(node_yaml), env={})
    assert exc_info.value.exit_code == 10
    assert "UNDETERMINABLE" in str(exc_info.value)
    logger.info("[IMP:9][test_undeterminable_cert_fails_closed] PASS: None extractor → exit 10")


# endregion FUNC_test_undeterminable_cert_fails_closed


# ═══════════════════════════════════════════════════════════════════
# Полный pass + идемпотентность (TEST_SPEC: test_noop_on_rerun)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_full_pass_and_rerun_noop
def test_full_pass_and_rerun_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без доменов/проектов (trivially converged) + пустой манифест → True; повтор → True (no-op)."""
    from core.internal.bootstrap.lifecycle.helpers import domains

    node_yaml = _node_yaml(tmp_path, with_project=False)
    core_dir = _core_dir_with_empty_manifest(tmp_path)
    monkeypatch.setattr(domains, "ssl_certs_converged_on_disk", lambda _c, _n: True)

    first = phase_final_verify(str(core_dir), "tronyx-vps", str(node_yaml), env={})
    second = phase_final_verify(str(core_dir), "tronyx-vps", str(node_yaml), env={})
    assert first is True and second is True, "повторный final_verify = no-op (True, без state-мутаций)"
    logger.info("[IMP:9][test_full_pass_and_rerun_noop] PASS: pass + rerun no-op")


# endregion FUNC_test_full_pass_and_rerun_noop


# region FUNC_test_missing_node_yaml_fails
def test_missing_node_yaml_fails(tmp_path: Path) -> None:
    """node.yaml отсутствует → PlatformFatalError exit 10 (нечего верифицировать)."""
    with pytest.raises(PlatformFatalError) as exc_info:
        phase_final_verify(str(tmp_path), "tronyx-vps", str(tmp_path / "absent.yaml"), env={})
    assert exc_info.value.exit_code == 10
    logger.info("[IMP:9][test_missing_node_yaml_fails] PASS: missing node.yaml → exit 10")


# endregion FUNC_test_missing_node_yaml_fails
