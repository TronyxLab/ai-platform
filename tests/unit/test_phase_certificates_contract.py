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
##   - ssl_provision_via_orchestrator(core_dir, node_yaml) → str
##     (provisioned|converged|skipped_import|error; P0 2026-08-27 — тихий import-skip устранён)
##   - Missing node.yaml → ConfigNotFoundError (precondition)
##   - ssl_provision with unresolvable domains → graceful return (no raise, статус converged)
## @rationale  Grep-ассерты проверяли подстроки вместо поведения; native-тесты вызывают функции
##             с фейковым контекстом и ассертят результат делегирования (канон инварианта 2).
## @changes  2026-08-01 · Created (DevPlan 116 B10 T2)
## @changes  2026-08-27 · P0 — контракт возврата ssl_provision_via_orchestrator: str-статус
##           вместо None; +тесты skipped_import/converged/provisioned (импорт-скип НЕ тихий)
# endregion MODULE_CONTRACT
"""

import inspect
from types import SimpleNamespace

import pytest

from core.internal.bootstrap.lifecycle import phases as phases_mod
from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers
from core.internal.shared import deploy_paths

pytestmark = pytest.mark.static_audit

logger = pytest.importorskip("logging").getLogger(__name__)


def _default_ssl_provision(calls: list[tuple[str, str]], core_dir_arg: str, node_yaml_arg: str) -> str:
    """Default ssl_provision fake: записывает вызов, возвращает "provisioned" (успех)."""
    calls.append((str(core_dir_arg), str(node_yaml_arg)))
    return "provisioned"


def _certs_helpers(calls: list, *, ssl_provision=None, install_acme=None) -> SimpleNamespace:
    """Fake helper-namespace (DI-канон 163 W-H, DevPlan 167 D3) для phase_certificates —
    0 setattr. Дефолты записывают вызовы в calls."""
    return SimpleNamespace(
        _install_acme=install_acme if install_acme is not None else (lambda _: True),
        ssl_provision_via_orchestrator=(
            ssl_provision
            if ssl_provision is not None
            else (lambda core_dir_arg, node_yaml_arg: _default_ssl_provision(calls, core_dir_arg, node_yaml_arg))
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
def test_ssl_provision_no_domains_converged(tmp_path, caplog) -> None:
    """ssl_provision_via_orchestrator(fake_core_dir, node_yaml) → "converged", no raise (domains [])."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")
    fake_core_dir = tmp_path / "core-no-deployer"
    fake_core_dir.mkdir()

    # No domains extracted → nothing to issue → converged (успех, НЕ наказание повтором)
    result = domains_helpers.ssl_provision_via_orchestrator(str(fake_core_dir), str(node_yaml))

    assert result == "converged", (
        f"ssl_provision_via_orchestrator must return 'converged' on empty domains, got {result!r}"
    )
    logger.critical("[IMP:9][test] ssl_provision no domains — converged (graceful return)")


# region Behavior: P0 (2026-08-27) — import-unavailable → честный статус (не тихий skip)


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · import-unavailable + серты НЕ на диске → skipped_import
# · Scenario: cert_orchestrator not importable (холодный bootstrap, неполный core) + fullchain.pem
# ·   отсутствует → helper обязан вернуть "skipped_import" (фаза → done_with_warnings → resume).
# · Last fail: 2026-08-27 P0 на tronyx-vps — тихий skip → φ7 done при пустом S3-кеше
# · Remove if: контракт статусов ssl_provision_via_orchestrator изменится
def test_ssl_provision_import_unavailable_certs_missing_skipped_import(tmp_path, caplog, monkeypatch) -> None:
    """orchestrate_certs=None + серты НЕ на диске → "skipped_import" + IMP:10 критический лог."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    monkeypatch.setattr(domains_helpers, "orchestrate_certs", None)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])
    monkeypatch.setattr(deploy_paths, "letsencrypt_live", lambda: tmp_path)  # LE live → tmp (без сертов)

    result = domains_helpers.ssl_provision_via_orchestrator(str(tmp_path), str(node_yaml))

    assert result == "skipped_import", (
        f"import-unavailable + certs-missing обязан давать 'skipped_import', got {result!r}"
    )
    # Импорт-скип НЕ тихий: IMP:10 фиксирует отказ (Anti-Illusion — реальный траекторный след)
    assert any("[IMP:10]" in r.message for r in caplog.records), (
        "P0 FAIL: import-скип обязан логироваться IMP:10 (не тихий WARN)"
    )
    logger.critical("[IMP:9][test] import-unavailable + certs-missing → skipped_import (IMP:10 logged)")


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · import-unavailable + серты НА диске → converged
# · Scenario: orchestrate_certs недоступен, но acme.sh уже выдал fullchain.pem для всех доменов —
# ·   считаем converged, НЕ наказываем повтором issuance (resume не перевыпускает готовое).
# · Last fail: N/A (новое поведение — дисковая converged-проверка)
# · Remove if: converged-семантика изменится
def test_ssl_provision_import_unavailable_certs_present_converged(tmp_path, caplog, monkeypatch) -> None:
    """orchestrate_certs=None + все fullchain.pem на диске → "converged" (без повторного issue)."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    # Серты уже на диске для всех доменов (LE live → tmp_path)
    (tmp_path / "example.com").mkdir()
    (tmp_path / "example.com" / "fullchain.pem").write_text("FAKE-CERT")

    monkeypatch.setattr(domains_helpers, "orchestrate_certs", None)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])
    monkeypatch.setattr(deploy_paths, "letsencrypt_live", lambda: tmp_path)

    result = domains_helpers.ssl_provision_via_orchestrator(str(tmp_path), str(node_yaml))

    assert result == "converged", f"import-unavailable + certs-present обязан давать 'converged', got {result!r}"
    logger.critical("[IMP:9][test] import-unavailable + certs-present → converged (без повторного issue)")


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · import-unavailable + домены неопределимы → skipped_import
# · Scenario: BOTH cert_orchestrator и context_deployer недоступны (совместный импорт-фейл при
# ·   неполном core) → домены НЕЛЬЗЯ проверить → консервативно skipped_import (resume перевыполнит).
# ·   Это точный сценарий P0 2026-08-27 (общий try-блок валил оба модуля).
# · Last fail: 2026-08-27 P0 на tronyx-vps
# · Remove if: контракт статусов изменится
def test_ssl_provision_import_unavailable_domains_undeterminable_skipped_import(tmp_path, caplog, monkeypatch) -> None:
    """orchestrate_certs=None + context_deployer=None (домены неопределимы) → "skipped_import"."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    monkeypatch.setattr(domains_helpers, "orchestrate_certs", None)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", None)  # домены неопределимы
    monkeypatch.setattr(deploy_paths, "letsencrypt_live", lambda: tmp_path)

    result = domains_helpers.ssl_provision_via_orchestrator(str(tmp_path), str(node_yaml))

    assert result == "skipped_import", (
        f"домены неопределимы → обязан 'skipped_import' (нельзя подтвердить converged), got {result!r}"
    )
    logger.critical("[IMP:9][test] import-unavailable + домены неопределимы → skipped_import (консервативно)")


# 🧪 TRAP[TEST] · 2026-08-27 · Regression (c) · импорт доступен → прежнее поведение (issuance)
# · Scenario: orchestrate_certs доступен → orchestrate_certs вызывается, статус "provisioned".
# · Last fail: N/A (эталонное поведение — регрессионный щит для нового контракта)
# · Remove if: orchestration-контракт изменится
def test_ssl_provision_import_available_provisioned(tmp_path, caplog, monkeypatch) -> None:
    """orchestrate_certs доступен → вызывается, возвращается "provisioned" (прежнее поведение)."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    calls: list[tuple] = []

    # 2026-09-02 (F-10): helper маппит по счётчикам CertResult — фейк несёт реальные counters.
    def _fake_orchestrate(domains, issue_cert_script, secrets_env, migrate_cron=False):
        from core.internal.bootstrap.cert_orchestrator import CertResult, DomainCertResult

        calls.append((list(domains), str(issue_cert_script)))
        result = CertResult()
        result.add(DomainCertResult(domain="example.com", status="issued"))
        return result

    monkeypatch.setattr(domains_helpers, "orchestrate_certs", _fake_orchestrate)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])

    result = domains_helpers.ssl_provision_via_orchestrator(str(tmp_path), str(node_yaml))

    assert result == "provisioned", f"импорт доступен → обязан 'provisioned', got {result!r}"
    assert calls, "orchestrate_certs обязан вызываться при доступном импорте (regression c)"
    assert calls[0][0] == ["example.com"], f"домены должны дойти до оркестратора, got {calls[0][0]}"
    logger.critical("[IMP:9][test] import-available → provisioned (orchestrate_certs вызван)")


# endregion Behavior: P0 (2026-08-27) — import-unavailable → честный статус


# region Behavior: P0 (2026-08-31, F-01) — extractor-unavailable ≠ converged (домены неопределимы)


# 🧪 TRAP[TEST] · 2026-08-31 · P0 (F-01) · экстрактор недоступен + orchestrate доступен → skipped_import
# · Scenario: cert_orchestrator импортировался, но context_deployer НЕ импортировался
# ·   (ModuleNotFoundError: pydantic на системном python3 3.12 голой ноды) → домены НЕОПРЕДЕЛИМЫ.
# ·   ДО фикса это давало "converged" → φ7 «SSL certificates provisioned» (done) при НЕвыпущенных
# ·   сертах → nginx crash-loop «cannot load certificate» (bootstrap exit 2, asi-team-vps).
# · Last fail: 2026-08-31 P0 cold bootstrap asi-team-vps
# · Remove if: контракт статусов ssl_provision_via_orchestrator изменится
def test_ssl_provision_extractor_unavailable_skipped_import_not_converged(tmp_path, caplog, monkeypatch) -> None:
    """orchestrate_certs доступен + extract_domains_for_context=None → "skipped_import", НЕ "converged"."""
    caplog.set_level(0)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    orchestrate_calls: list[tuple] = []

    def _fake_orchestrate(*args, **kwargs):
        orchestrate_calls.append((args, kwargs))
        return object()  # never reached — extractor unavailable → skipped_import ДО вызова

    monkeypatch.setattr(domains_helpers, "orchestrate_certs", _fake_orchestrate)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", None)  # экстрактор недоступен

    result = domains_helpers.ssl_provision_via_orchestrator(str(tmp_path), str(node_yaml))

    assert result == "skipped_import", (
        f"экстрактор недоступен → обязан 'skipped_import' (не 'converged' — ложный success φ7), got {result!r}"
    )
    assert not orchestrate_calls, "orchestrate_certs НЕ должен вызываться при недоступном экстракторе"
    # Импорт-скип НЕ тихий: IMP:10 фиксирует неопределимость доменов (Anti-Illusion, R1)
    assert any("[IMP:10]" in r.message for r in caplog.records), (
        "P0 FAIL: недоступный экстрактор обязан логироваться IMP:10 (не тихий skip)"
    )
    logger.critical("[IMP:9][test] extractor-unavailable → skipped_import (НЕ converged) — φ7 не рапортует done")


# endregion Behavior: P0 (2026-08-31, F-01) — extractor-unavailable ≠ converged (домены неопределимы)


# endregion Behavior: ssl_provision_via_orchestrator with fake context
