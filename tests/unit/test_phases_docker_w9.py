#!/usr/bin/env python3
# GREP_SUMMARY: test-phases-docker-w9, T9.14, nginx-overlay, content-hash, reload-gate, T9.16, provision-scopes, networks-volumes, T9.19, hc-marker, per-context
# STRUCTURE: ▶ test_*_overlay_hash ┌overlay .conf v1┐ → reload; ┌v1 (без изменений)┐ → НЕТ reload; ┌удаление .conf┐ → reload (deletions в hash) │ ▶ test_*_provision_both_scopes → cmd содержит --scope networks --scope volumes │ ▶ test_*_hc_marker_per_context → CONTEXT → маркер .hc_done_in_deploy.<ctx>
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.14 (B-10), T9.16 (B-13), T9.19 (B-11) DevPlan 136 W9:
##           nginx overlay reload-gate по content-hash ВСЕГО содержимого dir (включая deletions);
##           φ11 provision передаёт ОБА scope (networks+volumes); .hc_done_in_deploy маркер
##           per-context (не node-global).
## @scope    unit-тесты: NODE_CONFIGS_REMOTE_BASE=tmp (overlay dir); monkeypatch nginx_reload/
##           run_subprocess/os.path.isfile; tmp_path.
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - Overlay без изменений → 0 reload; изменение/удаление → reload (R5-negative на deletion)
##   - Маркер: CONTEXT=ctx-a → /var/lib/platform/.bootstrap/.hc_done_in_deploy.ctx-a (не node-global)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: T9.14/T9.16/T9.19 — hash всего содержимого dir
##            (включая deletions); тест на сети+тома; marker scope per-context.
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import logging
import os
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.orchestrator_metrics import hc_marker_path
from core.internal.bootstrap.lifecycle.phases import docker as phases_docker
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _make_overlay(tmp_path: Path, node: str) -> Path:
    """Overlay dir с одним .conf (NODE_CONFIGS_REMOTE_BASE=tmp)."""
    overlay = tmp_path / "node-configs" / node / "overlays" / "nginx"
    overlay.mkdir(parents=True)
    (overlay / "site.conf").write_text("server { listen 80; }\n", encoding="utf-8")
    return overlay


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.14/B-10 — overlay без изменений → 0 reload
# · Scenario: первый вызов reload; второй вызов (без изменений) → НЕТ reload (hash-marker)
# · Last fail: 2026-08-05 — reload был на КАЖДЫЙ φ11 (без hash-gate, B-10)
# · Remove if: overlay reload-gate semantics change
@ldd_trajectory
def test_nginx_overlay_reload_gated_by_hash(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.14: reload ТОЛЬКО при изменении содержимого overlay (hash-gate)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("NODE_CONFIGS_REMOTE_BASE", str(tmp_path / "node-configs"))
    monkeypatch.setenv("PLATFORM_STATE_DIR", str(tmp_path / "state"))  # изоляция marker-файлов
    _make_overlay(tmp_path, "test-node")
    reloads: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.phases.docker.shared_docker_compose_nginx_reload",
        lambda *a, **k: reloads.append(1),
    )

    # 1-й вызов: overlay новый → reload
    assert phases_docker._registry_step_nginx_overlays("test-node") is False
    assert len(reloads) == 1, "первый вызов обязан reload'ить"

    # 2-й вызов: содержимое не изменилось → НЕТ reload (no-op)
    assert phases_docker._registry_step_nginx_overlays("test-node") is False
    assert len(reloads) == 1, "без изменений → 0 reload (T9.14)"
    assert "reload skipped" in caplog.text

    # 3-й вызов: файл ИЗМЕНИЛСЯ → reload
    (tmp_path / "node-configs" / "test-node" / "overlays" / "nginx" / "site.conf").write_text(
        "server { listen 80; server_name changed; }\n", encoding="utf-8"
    )
    assert phases_docker._registry_step_nginx_overlays("test-node") is False
    assert len(reloads) == 2, "изменение содержимого → reload"
    logger.critical("[IMP:9][test] overlay reload gated by content-hash — OK (T9.14)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.14/B-10 — УДАЛЕНИЕ .conf инвалидирует hash
# · Scenario: .conf удалён из overlay → hash меняется (deletions в наборе) → reload применяет удаление
# · Last fail: 2026-08-05 — hash считался только по существующим файлам → deletion невидим → reload нет
# · Remove if: overlay reload-gate semantics change
@ldd_trajectory
def test_nginx_overlay_deletion_invalidates_hash(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.14: удаление .conf из overlay → reload (deletions учитываются в hash)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("NODE_CONFIGS_REMOTE_BASE", str(tmp_path / "node-configs"))
    monkeypatch.setenv("PLATFORM_STATE_DIR", str(tmp_path / "state"))  # изоляция marker-файлов
    overlay = _make_overlay(tmp_path, "test-node")
    reloads: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.phases.docker.shared_docker_compose_nginx_reload",
        lambda *a, **k: reloads.append(1),
    )

    assert phases_docker._registry_step_nginx_overlays("test-node") is False
    assert len(reloads) == 1

    (overlay / "site.conf").unlink()  # deletion
    assert phases_docker._registry_step_nginx_overlays("test-node") is False
    assert len(reloads) == 2, "удаление .conf обязано инвалидировать hash → reload (T9.14)"
    logger.critical("[IMP:9][test] overlay deletion invalidates hash — OK (T9.14)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.16/B-13 — φ11 provision передаёт ОБА scope
# · Scenario: _registry_step_provision_env вызывает provision-environment.sh с
# ·   --scope networks --scope volumes (оба, как φ3); wrapper аккумулирует (FIX-1)
# · Remove if: provision scope semantics change
@ldd_trajectory
def test_registry_provision_passes_both_scopes(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.16: provision sub-step вызывает --scope networks --scope volumes (не только volumes)."""
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    provision_script = core_dir / "internal" / "provision-environment.sh"
    provision_script.parent.mkdir(parents=True)
    provision_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    captured: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.phases.docker.helpers_subprocess.run_subprocess",
        lambda *a, **k: captured.append(a),
    )

    assert phases_docker._registry_step_provision_env(str(core_dir)) is False
    assert captured, "provision обязан вызываться"
    cmd = captured[0][0]
    assert cmd == ["bash", str(provision_script), "--scope", "networks", "--scope", "volumes"], (
        f"ОБА scope обязаны передаваться (B-13): {cmd}"
    )
    logger.critical("[IMP:9][test] provision passes networks+volumes scopes — OK (T9.16)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.19/B-11 — маркер per-context (не node-global)
# · Scenario: CONTEXT=ctx-a → маркер .hc_done_in_deploy.ctx-a; контекст A не подавляет
# ·   standalone healthcheck контекста B (пути различны)
# · Last fail: 2026-08-05 — маркер /var/lib/platform/.bootstrap/.hc_done_in_deploy (node-global):
# ·   деплой context A подавлял healthcheck context B (B-11)
# · Remove if: hc-marker scope semantics change
@ldd_trajectory
def test_hc_marker_per_context(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """T9.19: hc_marker_path per-context — пути контекстов различны, legacy без CONTEXT сохранён."""
    caplog.set_level(logging.INFO)
    assert hc_marker_path("ctx-a") != hc_marker_path("ctx-b"), "маркеры разных контекстов различны"
    assert hc_marker_path("ctx-a").endswith(".hc_done_in_deploy.ctx-a")
    assert hc_marker_path(None) == "/var/lib/platform/.bootstrap/.hc_done_in_deploy", "legacy-путь сохранён"

    # Reader: _registry_step_healthcheck с CONTEXT=ctx-a и маркером ctx-a → skip healthcheck
    marker = hc_marker_path("ctx-a")
    real_isfile = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: p == marker or real_isfile(p))
    monkeypatch.setenv("CONTEXT", "ctx-a")
    hc_calls: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.phases.docker.helpers_reporting.run_healthchecks",
        lambda *a, **k: hc_calls.append(1),
    )

    assert phases_docker._registry_step_healthcheck("/tmp/nonexistent-node.yaml") is False
    assert not hc_calls, "маркер контекста → standalone healthcheck пропускается (T9.19)"
    logger.critical("[IMP:9][test] hc marker per-context — OK (T9.19)")
