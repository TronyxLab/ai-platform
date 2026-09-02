"""
# GREP_SUMMARY: test-converge-ssl, r-ssl, reconcile-ssl-certs, cert-restore, ssl-provision, cache-drill, f-02, restore-first, ssl-dispatch-before-r6
# STRUCTURE: ▶ tmp_path + monkeypatch domains.ssl_provision_via_orchestrator → ◇ R-ssl status-mapping 4× (provisioned-mutated / converged-noop / error-fail / skipped-import-fail) → ◇ preview dry-run (no mutation, WOULD) → ◇ dispatch integration 2× (R-ssl before R6: provisioned → rc1 / converged → rc0) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/ssl.py R-ssl reconcile_ssl_certs (F-02 приёмо-сдаточной
##           валидации, cache-drill C2): converge самолечит отсутствующие сертификаты
##           restore-first (disk → S3 → issue) ПЕРЕД R6 verify_vhosts.
## @scope    Tests status-маппинг ssl_provision_via_orchestrator (provisioned|converged|error|
##           skipped_import), preview-режим (dry_run/report_only — без мутаций) и диспатч
##           оркестратора (R-ssl вызывается ПЕРЕД R6). DI/monkeypatch по паттерну
##           test_reconciler.py / test_converge_vhosts.py — без docker, без ноды, tmp_path.
## @invariants
##   - domains.ssl_provision_via_orchestrator monkeypatch'ится (никаких реальных S3/ACME)
##   - domains.ssl_certs_converged_on_disk monkeypatch'ится в preview-тестах (нет /etc/letsencrypt)
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - dispatch-тесты стабят ВСЕ прочие R-юниты (call-order запись) — реально выполняется
##     только R-ssl + домены-стабы; infra.reset_state в фикстуре (леak-prevention)
## @rationale F-02: converge падал на R6 (nginx -t «cannot load certificate») без попытки
##   восстановления сертов — R-ssl ПЕРЕД R6 + статус-маппинг domains.py (P0-честность 2026-08-27)
##   покрываются на уровне юнита (маппинг) и диспатча (порядок/аргументы).
## @changes  2026-09-02 · F-02 (cache-drill C2) — Created
## @links    core/internal/bootstrap/converge/ssl_certs.py, converge/reconciler.py,
##           lifecycle/helpers/domains.py, tests/unit/test_reconciler.py (паттерн)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.ssl_certs as _converge_ssl
from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.lifecycle.helpers import domains

# Re-export for fixture cleanups
MODULE = reconciler

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


# endregion Fixtures


def _write_node_yaml(tmp_path: Path) -> Path:
    """Создать минимальный node.yaml (main() проверяет is_file — shell setup_environment parity)."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("context: test-context\nprojects: []\n", encoding="utf-8")
    return node_yaml


# ═══════════════════════════════════════════════════════════════════
# R-ssl status-маппинг (domains.ssl_provision_via_orchestrator)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ssl_restore_provisioned_mutated
## 🧪 TRAP[TEST] · R-ssl provisioned → mutated · Scenario: серт отсутствовал, restore из S3
##   (или issuance) выполнен → status=mutated + exit 1 (warnings — паттерн R1/R8 мутаций)
## · Regression: F-02 — converge НЕ вызывал ssl_provision вовсе; теперь "provisioned" →
## ·   отчёт mutated (дрейф устранён), НЕ ошибка (exit 2)
## · Last fail: 2026-09-01 tronyx-vps (cache-drill C2: live-серт удалён → converge exit 2 без restore)
## · Remove if: статус-маппинг domains.ssl_provision_via_orchestrator изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_restore_provisioned_mutated(tmp_path, monkeypatch, caplog):
    """R-ssl: ssl_provision → 'provisioned' → status=mutated, exit 1 (warnings, НЕ ошибка)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R-ssl provisioned → mutated (restore-first сработал)")

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "provisioned")

    entry = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"))

    assert entry["unit"] == "R-ssl"
    assert entry["status"] == "mutated"
    assert not infra.has_errors, "provisioned = дрейф устранён, НЕ ошибка (exit 2)"
    assert infra.has_warnings, "мутация (restore) обязана выставить has_warnings (паттерн R1/R8)"
    assert infra.exit_code == 1
    mutated = [d for d in infra.drifts if d["status"] == "mutated"]
    assert any("provisioned via orchestrator" in d["detail"] for d in mutated), f"drifts: {infra.drifts}"


# endregion FUNC_test_ssl_restore_provisioned_mutated


# region FUNC_test_ssl_restore_converged_noop
## 🧪 TRAP[TEST] · R-ssl converged → no-op · Scenario: повторный converge на живых сертах —
##   ssl_provision возвращает "converged" → status=converged, БЕЗ exit-кодов (идемпотентность)
## · Regression: F-02 требование идемпотентности — повторный converge = SKIP, ноль мутаций/ошибок
## · Last fail: никогда (новый целевой контракт)
## · Remove if: статус-маппинг domains.ssl_provision_via_orchestrator изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_restore_converged_noop(tmp_path, monkeypatch, caplog):
    """R-ssl: ssl_provision → 'converged' → status=converged, no-op (без exit-кодов)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R-ssl converged → no-op (повторный converge = SKIP)")

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "converged")

    entry = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"))

    assert entry["unit"] == "R-ssl"
    assert entry["status"] == "converged"
    assert not infra.has_errors, "converged — no-op, ошибок быть не должно"
    assert not infra.has_warnings, "converged — no-op, мутаций не было (has_warnings должен быть False)"
    assert infra.exit_code == 0


# endregion FUNC_test_ssl_restore_converged_noop


# region FUNC_test_ssl_restore_error_fail_exit2
## 🧪 TRAP[TEST] · R-ssl error → fail exit2 · Scenario: оркестрация упала ("error") →
##   status=fail + exit 2 (P0-честность: НЕ проглатывать, ошибки → exit 2 канон)
## · Regression: F-02 — converge падал на R6 ПОСЛЕ молчаливого отсутствия restore;
## ·   теперь отказ restore сам по себе = fail (exit 2), видимый в отчёте
## · Last fail: никогда (новый целевой контракт)
## · Remove if: статус-маппинг domains.ssl_provision_via_orchestrator изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_restore_error_fail_exit2(tmp_path, monkeypatch, caplog):
    """R-ssl: ssl_provision → 'error' → status=fail + exit 2 (НЕ проглатывается)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R-ssl error → fail + exit 2 (P0-честность: отказ restore виден)")

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "error")

    entry = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"))

    assert entry["unit"] == "R-ssl"
    assert entry["status"] == "fail"
    assert infra.has_errors, "error → exit 2 (канон: ошибки R-юнита не проглатываются)"
    assert infra.exit_code == 2
    fails = [d for d in infra.drifts if d["status"] == "fail"]
    assert any("SSL cert restore failed: error" in d["detail"] for d in fails), f"drifts: {infra.drifts}"


# endregion FUNC_test_ssl_restore_error_fail_exit2


# region FUNC_test_ssl_restore_skipped_import_fail_exit2
## 🧪 TRAP[TEST] · R-ssl skipped_import → fail exit2 · Scenario: cert_orchestrator недоступен
##   (guarded-import) → domains возвращает "skipped_import" → fail + exit 2
## · Regression: P0 2026-08-27 (тихий import-skip маскировал отказ φ7) — converge НЕ должен
## ·   рапортовать converged при неопределимом/недоступном состоянии
## · Last fail: никогда (новый целевой контракт)
## · Remove if: статус-маппинг domains.ssl_provision_via_orchestrator изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_restore_skipped_import_fail_exit2(tmp_path, monkeypatch, caplog):
    """R-ssl: ssl_provision → 'skipped_import' → status=fail + exit 2 (не тихий skip)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R-ssl skipped_import → fail + exit 2 (P0-честность: не «сделано» при skip)")

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "skipped_import")

    entry = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"))

    assert entry["unit"] == "R-ssl"
    assert entry["status"] == "fail"
    assert infra.has_errors, "skipped_import → exit 2 (нельзя молча считать серты готовыми)"
    assert infra.exit_code == 2


# endregion FUNC_test_ssl_restore_skipped_import_fail_exit2


# region FUNC_test_ssl_preview_dry_run_no_mutation
## 🧪 TRAP[TEST] · R-ssl preview dry-run · Scenario: --dry-run/--report-only — ssl_provision
##   НЕ вызывается (он мутирует!), on-disk проверка: missing → mutated-WOULD, present → converged
## · Regression: mode-контракт reconciler («--dry-run: plan without mutations, exit 0») — R-ssl
## ·   обязан НЕ восстанавливать серты в preview (иначе dry-run мутирует)
## · Last fail: никогда (новый целевой контракт)
## · Remove if: preview-ветка R-ssl заменена на иной механизм отчёта
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_preview_dry_run_no_mutation(tmp_path, monkeypatch, caplog):
    """R-ssl preview: dry_run — НЕ вызывает ssl_provision (мутация), отчитывает WOULD/converged по диску."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R-ssl preview (dry-run): on-disk check, ноль мутаций")

    def _fail_if_called(*_args, **_kwargs):  # pragma: no cover — guard: ssl_provision НЕ должен зваться
        msg = "ssl_provision_via_orchestrator НЕ должен вызываться в dry_run (он мутирует)"
        raise AssertionError(msg)

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", _fail_if_called)

    # ── missing certs → mutated (WOULD restore), exit 1 ──
    infra.reset_state()
    monkeypatch.setattr(domains, "ssl_certs_converged_on_disk", lambda _c, _n: False)
    entry = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"), dry_run=True)
    assert entry["status"] == "mutated", f"missing → WOULD restore, получено {entry}"
    assert "WOULD" in entry["detail"]
    assert infra.has_warnings, "WOULD-мутация в dry-run → warnings (паттерн R1 dry-run)"
    assert not infra.has_errors

    # ── certs present → converged (no-op, без exit) ──
    infra.reset_state()
    monkeypatch.setattr(domains, "ssl_certs_converged_on_disk", lambda _c, _n: True)
    entry2 = _converge_ssl.reconcile_ssl_certs(str(tmp_path), str(tmp_path / "node.yaml"), report_only=True)
    assert entry2["status"] == "converged", f"present → converged, получено {entry2}"
    assert not infra.has_warnings and not infra.has_errors, "converged preview — no-op без exit-кодов"


# endregion FUNC_test_ssl_preview_dry_run_no_mutation


# ═══════════════════════════════════════════════════════════════════
# Диспатч оркестратора: R-ssl ПЕРЕД R6 (test (a)/(b) из требования)
# ═══════════════════════════════════════════════════════════════════


def _stub_units_record_order(monkeypatch: pytest.MonkeyPatch, call_order: list[str]) -> None:
    """Стаб всех R-юнитов (кроме R-ssl) с записью call-order — реально выполняется только R-ssl.

    ## @purpose — dispatch-тест: reconciler.main() гоняет ВСЕ юниты; стабы-заглушки (converged)
    ##            изолируют проверку порядка «R-ssl ПЕРЕД R6» и аргументов ssl_provision.
    """
    for unit_name in [
        "reconcile_perms",
        "reconcile_audit_log",
        "reconcile_projects",
        "reconcile_networks",
        "detect_hosts_drift",
        "verify_vhosts",
        "reconcile_volumes",
        "reconcile_sudoers",
        "reconcile_runtime_state",
        "reconcile_prometheus_tsdb",
        "reconcile_prometheus_node_targets",
    ]:
        monkeypatch.setattr(reconciler, unit_name, _make_stub(unit_name, call_order))


def _make_stub(unit_name: str, call_order: list[str]):
    """Фабрика стаба: запись в call_order + сходимость (converged, без set_exit)."""

    def stub(*_args, **_kwargs):
        call_order.append(unit_name)
        return {"unit": unit_name, "status": "converged", "detail": "stub"}

    return stub


# region FUNC_test_ssl_dispatch_provisioned_before_r6
## 🧪 TRAP[TEST] · R-ssl диспатч ПЕРЕД R6 (a) · Scenario: серт отсутствовал → ssl_provision
##   вызывается с (core_dir, node_yaml), возвращает "provisioned" → R-ssl mutated (exit 1),
##   R6 verify_vhosts выполняется ПОСЛЕ (на восстановленных сертах)
## · Regression: F-02 — converge завершался на R6 (nginx -t cannot load certificate) БЕЗ restore;
## ·   теперь restore-first ПЕРЕД проверкой vhost'ов — R6 получает серты на диске
## · Last fail: 2026-09-01 tronyx-vps (cache-drill C2) — «provisioned» только при ручном вызове helpers
## · Remove if: порядок R-юнитов в unit_actions изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_dispatch_provisioned_before_r6(tmp_path, monkeypatch, caplog):
    """Диспатч: missing cert → ssl_provision('provisioned') вызывается до R6; R6 выполняется после."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] Диспатч R-ssl ПЕРЕД R6: restore-first до nginx -t (F-02)")

    node_yaml = _write_node_yaml(tmp_path)
    call_order: list[str] = []
    ssl_calls: list[tuple[str, str]] = []

    def fake_ssl_provision(core_dir: str, node_yaml_path: str) -> str:
        ssl_calls.append((core_dir, node_yaml_path))
        return "provisioned"

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", fake_ssl_provision)
    _stub_units_record_order(monkeypatch, call_order)
    real_ssl = reconciler.reconcile_ssl_certs

    def ssl_wrapper(*args, **kwargs):
        call_order.append("R-ssl")
        return real_ssl(*args, **kwargs)

    monkeypatch.setattr(reconciler, "reconcile_ssl_certs", ssl_wrapper)

    # reconciler.main() парсит sys.argv (argparse без argv-параметра) — канон-подмена argv
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconciler.py",
            "--node-yaml",
            str(node_yaml),
            "--node-name",
            "test-node",
            "--core-dir",
            str(tmp_path),
        ],
    )
    rc = reconciler.main()

    # (a) ssl_provision вызывается с канон-аргументами (core_dir, node_yaml)
    assert ssl_calls, "ssl_provision_via_orchestrator обязан вызываться (missing cert, F-02)"
    assert ssl_calls[0] == (str(tmp_path), str(node_yaml)), f"аргументы: {ssl_calls}"
    # (a) R-ssl выполняется ПЕРЕД R6 (restore-first до проверки vhost'ов)
    assert call_order.index("R-ssl") < call_order.index("verify_vhosts"), f"R-ssl обязан быть ПЕРЕД R6: {call_order}"
    # (a) provisioned → mutated (exit 1 = warnings, НЕ ошибка) — R6 при этом ВЫПОЛНЕН
    assert rc == 1, f"provisioned → мутация применена → exit 1 (warnings), получено rc={rc}"
    mutated = [d for d in infra.drifts if d["unit"] == "R-ssl" and d["status"] == "mutated"]
    assert mutated, f"R-ssl обязан рапортовать mutated: {infra.drifts}"
    assert "verify_vhosts" in call_order, "R6 verify_vhosts обязан выполниться после restore"


# endregion FUNC_test_ssl_dispatch_provisioned_before_r6


# region FUNC_test_ssl_dispatch_converged_noop
## 🧪 TRAP[TEST] · R-ssl диспатч converged no-op (b) · Scenario: живые серты → ssl_provision
##   возвращает "converged" → R-ssl converged, exit 0 (повторный converge = no-op, идемпотентность)
## · Regression: F-02 требование №3 — повторный converge на здоровых сертах НЕ должен
## ·   мутировать/падать (idempotency: "converged" → SKIP)
## · Last fail: никогда (новый целевой контракт)
## · Remove if: порядок R-юнитов в unit_actions изменится
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_ssl_dispatch_converged_noop(tmp_path, monkeypatch, caplog):
    """Диспатч: живые серты → ssl_provision('converged') → no-op, exit 0 (идемпотентность)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] Диспатч R-ssl converged: повторный converge = no-op (идемпотентность)")

    node_yaml = _write_node_yaml(tmp_path)
    call_order: list[str] = []

    monkeypatch.setattr(domains, "ssl_provision_via_orchestrator", lambda _c, _n: "converged")
    _stub_units_record_order(monkeypatch, call_order)
    real_ssl = reconciler.reconcile_ssl_certs

    def ssl_wrapper(*args, **kwargs):
        call_order.append("R-ssl")
        return real_ssl(*args, **kwargs)

    monkeypatch.setattr(reconciler, "reconcile_ssl_certs", ssl_wrapper)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconciler.py",
            "--node-yaml",
            str(node_yaml),
            "--node-name",
            "test-node",
            "--core-dir",
            str(tmp_path),
        ],
    )
    rc = reconciler.main()

    assert call_order.index("R-ssl") < call_order.index("verify_vhosts"), f"порядок: {call_order}"
    assert rc == 0, f"converged → no-op → exit 0, получено rc={rc}"
    assert not infra.has_errors and not infra.has_warnings, f"no-op без exit-кодов: drifts={infra.drifts}"
    rssl = [d for d in infra.drifts if d["unit"] == "R-ssl"]
    assert rssl and rssl[0]["status"] == "converged", f"R-ssl обязан рапортовать converged: {infra.drifts}"


# endregion FUNC_test_ssl_dispatch_converged_noop
