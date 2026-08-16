"""
# GREP_SUMMARY: test phase-certificates contract phase_certificates ssl_provision_via_orchestrator hasattr signature delegation B10-T2
# STRUCTURE: ▶ import phase_certificates (phases.py) + ssl_provision_via_orchestrator (helpers/domains.py) → ◇ hasattr + inspect.signature →
#            ◇ call with fake context (tmp node.yaml + monkeypatched I/O) → ⊕ assert delegation/return → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Native contract tests for the φ7 certificates phase (core/internal/bootstrap/lifecycle/phases.py
##           phase_certificates) and the SSL provision helper (lifecycle/helpers/domains.py
##           ssl_provision_via_orchestrator). Replaces deleted grep-asserts in
##           tests/test_node_lifecycle_static.py:410-428 and tests/test_cert_backup_gap.py:566-582.
##           DevPlan 116 B10 T2 (D2: Python-модули → native: импорт + вызов с фейковым контекстом).
## @scope    No subprocess to bootstrap scripts; I/O boundary (acme install, orchestrate_certs)
##           monkeypatched. Inspects public API (hasattr + signature) — NOT source internals
##           (inspect.getsource/AST запрещены по плану).
## @invariants
##   - phase_certificates(core_dir, node_name, node_yaml) → bool (True = φ7 complete)
##   - ssl_provision_via_orchestrator(core_dir, node_yaml) → None (non-fatal)
##   - Missing node.yaml → ConfigNotFoundError (precondition)
##   - ssl_provision with unresolvable domains → graceful return (no raise)
## @rationale  Grep-ассерты проверяли подстроки вместо поведения; native-тесты вызывают функции
##             с фейковым контекстом и ассертят результат делегирования (канон инварианта 2).
## @changes  2026-08-01 · Created (DevPlan 116 B10 T2)
# endregion MODULE_CONTRACT
"""

import inspect
from types import SimpleNamespace

import pytest

from core.internal.bootstrap.lifecycle import phases as phases_mod
from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers

pytestmark = pytest.mark.static_audit

logger = pytest.importorskip("logging").getLogger(__name__)


def _certs_helpers(calls: list, *, ssl_provision=None, install_acme=None) -> SimpleNamespace:
    """Fake helper-namespace (DI-канон 163 W-H, DevPlan 167 D3) для phase_certificates —
    0 setattr. Дефолты записывают вызовы в calls."""
    return SimpleNamespace(
        _install_acme=install_acme if install_acme is not None else (lambda _: True),
        ssl_provision_via_orchestrator=(
            ssl_provision
            if ssl_provision is not None
            else (lambda core_dir_arg, node_yaml_arg: calls.append((str(core_dir_arg), str(node_yaml_arg))))
        ),
    )


# ═════════════════════════════════════════════════════════════════════════════
# region Contract: API surface (hasattr + signature)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · contract · phase_certificates exists with (core_dir, node_name, node_yaml)
# · Regression: B9 T1 — phase moved from state_machine to phases.py
# · Last fail: N/A (native replacement for test_node_lifecycle_static.py:410-428 grep)
# · Remove if: phase function renamed/removed
def test_phase_certificates_api_contract() -> None:
    """phase_certificates must exist with the documented signature (+ DI helpers, 167 D3)."""
    assert hasattr(phases_mod, "phase_certificates"), "phases.py must define phase_certificates()"
    sig = inspect.signature(phases_mod.phase_certificates)
    params = list(sig.parameters)
    assert params[:3] == ["core_dir", "node_name", "node_yaml"], (
        f"phase_certificates signature must be (core_dir, node_name, node_yaml, *, helpers), got {params}"
    )
    # 167 D3: keyword-only DI-шов helpers (helper-namespace) — опциональный, default None
    assert params[3:] == ["helpers"], f"167 D3: helpers keyword-only ожидался, got {params}"
    assert sig.parameters["helpers"].default is None
    logger.critical(
        "[IMP:9][test] phase_certificates API contract — signature (core_dir, node_name, node_yaml, *, helpers) OK"
    )


# 🧪 TRAP[TEST] · 2026-08-01 · contract · ssl_provision_via_orchestrator exists with (core_dir, node_yaml)
# · Regression: B9 T1 — I/O-хелпер извлечён в helpers/domains.py
# · Last fail: N/A (native replacement for test_cert_backup_gap.py:566-582 grep)
# · Remove if: helper renamed/removed
def test_ssl_provision_via_orchestrator_api_contract() -> None:
    """ssl_provision_via_orchestrator must exist with the documented signature."""
    assert hasattr(domains_helpers, "ssl_provision_via_orchestrator"), (
        "helpers/domains.py must define ssl_provision_via_orchestrator()"
    )
    sig = inspect.signature(domains_helpers.ssl_provision_via_orchestrator)
    params = list(sig.parameters)
    assert params == ["core_dir", "node_yaml"], (
        f"ssl_provision_via_orchestrator signature must be (core_dir, node_yaml), got {params}"
    )
    logger.critical("[IMP:9][test] ssl_provision_via_orchestrator API contract — signature (core_dir, node_yaml) OK")


# 🧪 TRAP[TEST] · 2026-08-01 · contract · extract_domains public helper exists
# · Regression: CS-1 — публичная extract_domains_for_context через extract_domains
# · Last fail: N/A (native contract)
# · Remove if: domain extraction API changes
def test_extract_domains_api_contract() -> None:
    """extract_domains(core_dir, node_yaml, context) helper must exist (CS-1 public contract)."""
    assert hasattr(domains_helpers, "extract_domains"), "helpers/domains.py must define extract_domains()"
    sig = inspect.signature(domains_helpers.extract_domains)
    assert list(sig.parameters) == ["core_dir", "node_yaml", "context"]
    logger.critical("[IMP:9][test] extract_domains API contract — signature (core_dir, node_yaml, context) OK")


# endregion Contract: API surface (hasattr + signature)


# ═════════════════════════════════════════════════════════════════════════════
# region Behavior: phase_certificates with fake context
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · behavior · phase_certificates delegates to ssl_provision_via_orchestrator
# · Regression: DevPlan 087/052 — φ7 must wire cert orchestration via helpers/domains
# · Last fail: N/A (native replacement for test_update_ssl_step_sources_secrets_env check 3)
# · Remove if: phase delegation flow changes
def test_phase_certificates_delegates_to_orchestrator(tmp_path, caplog) -> None:
    """phase_certificates(core_dir, node_name, node_yaml) → True; calls ssl_provision_via_orchestrator(core_dir, node_yaml)."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    calls: list[tuple[str, str]] = []
    # 167 D3: helper-namespace DI (0 setattr на доменном модуле / helpers_domains)
    helpers = _certs_helpers(calls)

    result = phases_mod.phase_certificates(str(core_dir), "test-node", str(node_yaml), helpers=helpers)

    assert result is True, "φ7 must complete (return True) when acme + orchestration succeed"
    assert calls == [(str(core_dir), str(node_yaml))], (
        f"ssl_provision_via_orchestrator must be called with (core_dir, node_yaml), got {calls}"
    )
    logger.critical("[IMP:9][test] phase_certificates delegates to ssl_provision_via_orchestrator(core_dir, node_yaml)")


# 🧪 TRAP[TEST] · 2026-08-01 · behavior · phase_certificates non-fatal on orchestrator failure
# · Regression: SSL provisioning is best-effort (S3 cache fallback) — phase must not crash
# · Last fail: N/A (native contract)
# · Remove if: best-effort SSL semantics change
def test_phase_certificates_non_fatal_on_orchestrator_failure(tmp_path, caplog) -> None:
    """phase_certificates returns False (not raises) when ssl_provision raises."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    def _boom(core_dir_arg, node_yaml_arg):
        msg = "orchestrator unavailable"
        raise RuntimeError(msg)

    # 167 D3: helper-namespace DI (0 setattr на доменном модуле / helpers_domains)
    helpers = _certs_helpers([], ssl_provision=_boom)

    result = phases_mod.phase_certificates(str(tmp_path / "core"), "test-node", str(node_yaml), helpers=helpers)

    assert result is False, "φ7 must degrade to False (non-fatal) when orchestration fails"
    logger.critical("[IMP:9][test] phase_certificates non-fatal on orchestrator failure — returned False")


# 🧪 TRAP[TEST] · 2026-08-01 · behavior · phase_certificates raises ConfigNotFoundError on missing node.yaml
# · Regression: φ7 precondition — node.yaml required for domain extraction
# · Last fail: N/A (native contract)
# · Remove if: precondition semantics change
def test_phase_certificates_missing_node_yaml_raises(tmp_path, caplog) -> None:
    """phase_certificates with a nonexistent node.yaml → ConfigNotFoundError (precondition)."""
    caplog.set_level(0)
    from core.internal.shared.exceptions import ConfigNotFoundError

    with pytest.raises(ConfigNotFoundError):
        phases_mod.phase_certificates(str(tmp_path / "core"), "test-node", str(tmp_path / "missing.yaml"))
    logger.critical("[IMP:9][test] phase_certificates precondition — ConfigNotFoundError on missing node.yaml")


# endregion Behavior: phase_certificates with fake context


# ═════════════════════════════════════════════════════════════════════════════
# region Behavior: ssl_provision_via_orchestrator with fake context
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · behavior · ssl_provision gracefully skips when domains unresolvable
# · Regression: non-fatal — missing context_deployer must not crash ssl_provision
# · Last fail: N/A (native replacement for test_cert_backup_gap.py:566-582 grep)
# · Remove if: graceful no-domains handling changes
def test_ssl_provision_no_domains_non_fatal(tmp_path, caplog) -> None:
    """ssl_provision_via_orchestrator(fake_core_dir, node_yaml) → None, no raise (extract_domains [])."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")
    fake_core_dir = tmp_path / "core-no-deployer"
    fake_core_dir.mkdir()

    # context_deployer.py is absent in fake_core_dir → extract_domains returns [] → graceful return
    result = domains_helpers.ssl_provision_via_orchestrator(str(fake_core_dir), str(node_yaml))

    assert result is None, f"ssl_provision_via_orchestrator must return None (non-fatal), got {result!r}"
    logger.critical("[IMP:9][test] ssl_provision no domains — graceful return (non-fatal)")


# endregion Behavior: ssl_provision_via_orchestrator with fake context
