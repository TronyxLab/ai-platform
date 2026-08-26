# GREP_SUMMARY: test-phases-docker-w9, T9.14, nginx-overlay, content-hash, reload-gate, T9.16, provision-scopes, networks-volumes, T9.19, hc-marker, per-context
# STRUCTURE: ▶ test_*_overlay_hash ┌overlay .conf v1┐ → reload; ┌v1 (без изменений)┐ → НЕТ reload; ┌удаление .conf┐ → reload (deletions в hash) │ ▶ test_*_provision_both_scopes → cmd содержит --scope networks --scope volumes │ ▶ test_*_hc_marker_per_context → CONTEXT → маркер .hc_done_in_deploy.<ctx> │ ▶ test_llm_provision_failure_surfaces → provision rc≠0 → failed_consumers=N + issue; rc=0 → IMP:9 success
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
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.orchestrator_metrics import hc_marker_path
from core.internal.bootstrap.lifecycle.phases import docker as phases_docker
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

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
def test_nginx_overlay_reload_gated_by_hash(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.14: reload ТОЛЬКО при изменении содержимого overlay (hash-gate)."""
    caplog.set_level(logging.INFO)
    _make_overlay(tmp_path, "test-node")
    reloads: list = []

    # DI (W-H): node_configs_remote_base/state_dir/reload_fn параметрами (0 патчей env/функций)
    kw = {
        "node_configs_remote_base": str(tmp_path / "node-configs"),
        "state_dir": str(tmp_path / "state"),
        "reload_fn": lambda *_, **__: reloads.append(1),
    }

    # 1-й вызов: overlay новый → reload
    assert phases_docker._registry_step_nginx_overlays("test-node", **kw) is False
    assert len(reloads) == 1, "первый вызов обязан reload'ить"

    # 2-й вызов: содержимое не изменилось → НЕТ reload (no-op)
    assert phases_docker._registry_step_nginx_overlays("test-node", **kw) is False
    assert len(reloads) == 1, "без изменений → 0 reload (T9.14)"
    assert "reload skipped" in caplog.text

    # 3-й вызов: файл ИЗМЕНИЛСЯ → reload
    (tmp_path / "node-configs" / "test-node" / "overlays" / "nginx" / "site.conf").write_text(
        "server { listen 80; server_name changed; }\n", encoding="utf-8"
    )
    assert phases_docker._registry_step_nginx_overlays("test-node", **kw) is False
    assert len(reloads) == 2, "изменение содержимого → reload"
    logger.critical("[IMP:9][test] overlay reload gated by content-hash — OK (T9.14)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.14/B-10 — УДАЛЕНИЕ .conf инвалидирует hash
# · Scenario: .conf удалён из overlay → hash меняется (deletions в наборе) → reload применяет удаление
# · Last fail: 2026-08-05 — hash считался только по существующим файлам → deletion невидим → reload нет
# · Remove if: overlay reload-gate semantics change
@ldd_trajectory
def test_nginx_overlay_deletion_invalidates_hash(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.14: удаление .conf из overlay → reload (deletions учитываются в hash)."""
    caplog.set_level(logging.INFO)
    overlay = _make_overlay(tmp_path, "test-node")
    reloads: list = []

    kw = {
        "node_configs_remote_base": str(tmp_path / "node-configs"),
        "state_dir": str(tmp_path / "state"),
        "reload_fn": lambda *_, **__: reloads.append(1),
    }

    assert phases_docker._registry_step_nginx_overlays("test-node", **kw) is False
    assert len(reloads) == 1

    (overlay / "site.conf").unlink()  # deletion
    assert phases_docker._registry_step_nginx_overlays("test-node", **kw) is False
    assert len(reloads) == 2, "удаление .conf обязано инвалидировать hash → reload (T9.14)"
    logger.critical("[IMP:9][test] overlay deletion invalidates hash — OK (T9.14)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.16/B-13 — φ11 provision передаёт ОБА scope
# · Scenario: _registry_step_provision_env вызывает provision-environment.sh с
# ·   --scope networks --scope volumes (оба, как φ3); wrapper аккумулирует (FIX-1)
# · Remove if: provision scope semantics change
@ldd_trajectory
def test_registry_provision_passes_both_scopes(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.16: provision sub-step вызывает --scope networks --scope volumes (не только volumes)."""
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    provision_script = core_dir / "internal" / "provision-environment.sh"
    provision_script.parent.mkdir(parents=True)
    provision_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    captured: list = []
    assert (
        phases_docker._registry_step_provision_env(str(core_dir), run_subprocess_fn=lambda *a, **__: captured.append(a))
        is False
    )
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
def test_hc_marker_per_context(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """T9.19: hc_marker_path per-context; 1:1 fallback без CONTEXT (консистентность читателя/писателя)."""
    caplog.set_level(logging.INFO)
    assert hc_marker_path("ctx-a") != hc_marker_path("ctx-b"), "маркеры разных контекстов различны"
    assert hc_marker_path("ctx-a").endswith(".hc_done_in_deploy.ctx-a")
    assert hc_marker_path(None) == "/var/lib/platform/.bootstrap/.hc_done_in_deploy", "1:1 fallback (CONTEXT пуст)"

    # QA R2/T2.B: freshness-читатель делает РЕАЛЬНЫЙ stat маркера — маркер создаётся
    # по hermetic tmp-базе (_HC_DONE_MARKER monkeypatch); run-start неизвестен →
    # legacy-семантика подавления (контракт T9.19 не меняется).
    from core.internal.bootstrap.deploy import orchestrator_metrics as om
    from core.internal.bootstrap.lifecycle import state_machine as stm

    monkeypatch.setattr(om, "_HC_DONE_MARKER", str(tmp_path / "state" / ".hc_done_in_deploy"))
    stm.reset_run_start_ts()

    marker = Path(hc_marker_path("ctx-a"))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    hc_calls: list = []

    assert (
        phases_docker._registry_step_healthcheck(
            "/tmp/nonexistent-node.yaml",
            context="ctx-a",
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        is False
    )
    assert not hc_calls, "маркер контекста → standalone healthcheck пропускается (T9.19)"
    assert not marker.exists(), "читатель снимает поглотивший маркер"
    logger.critical("[IMP:9][test] hc marker per-context — OK (T9.19)")


def _fake_run_subprocess(rc_by_bin: dict[str, int], stderr_by_bin: dict[str, str] | None = None):
    """Fake helpers_subprocess.run_subprocess: rc/stderr по первому токену cmd."""
    from subprocess import CompletedProcess

    stderr_by_bin = stderr_by_bin or {}

    def _run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        # entrypoint-вызовы имеют форму ["bash", <script>] — матчим любой токен
        names = [Path(token).name for token in cmd[:2]]
        bin_key = next((n for n in names if n in rc_by_bin), None)
        return CompletedProcess(
            cmd,
            rc_by_bin.get(bin_key, 0),
            "",
            stderr_by_bin.get(bin_key, ""),
        )

    return _run


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · rc≠0 провижинера обязан surfaced в сводке φ11 (AI-0010r)
# · Regression: run_subprocess(non_fatal=True) результат не читался → ложный IMP:9
#   «LLM virtual keys provisioned» при rc≠0 → φ11 done зелёным без ключей
# · Scenario: renderer rc=0, provision rc=1 (stderr «FAILED for 2 consumer(s)») → шаг True,
#   ERROR «llm_provision: failed_consumers=2», success-лог отсутствует;
#   контрсценарий rc=0 → прежний IMP:9 success сохранён
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0010)
# · Remove if: llm-provision переезжает из φ11 в отдельный verb с собственным отчётом
@ldd_trajectory
def test_llm_provision_failure_surfaces(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI-0010r: rc≠0 provision-llm.sh → ERROR failed_consumers=N + issue=True; rc=0 → success."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    (core_dir / "internal" / "llm").mkdir(parents=True)
    (core_dir / "entrypoints").mkdir(parents=True)
    (core_dir / "internal" / "llm" / "config_renderer.py").write_text("# renderer\n", encoding="utf-8")
    (core_dir / "entrypoints" / "provision-llm.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    # ── failure: provision rc=1 с failed_consumers=2 в stderr ──
    monkeypatch.setattr(
        phases_docker.helpers_subprocess,
        "run_subprocess",
        _fake_run_subprocess(
            {"provision-llm.sh": 1},
            {"provision-llm.sh": "LLM key provisioning FAILED for 2 consumer(s): ['litellm', 'monitoring']"},
        ),
    )
    assert phases_docker._registry_step_llm_provision(str(core_dir)) is True, (
        "rc≠0 провижинера обязан пометить фазу issue (True)"
    )
    assert "failed_consumers=2" in caplog.text, "failed_consumers=N обязан доезжать до сводки"
    assert "provisioned" not in caplog.text.split("failed_consumers")[0] or (
        "LLM virtual keys provisioned" not in caplog.text
    ), "ложный success-лог при rc≠0 запрещён"

    # ── success: оба rc=0 → прежний IMP:9 success ──
    caplog.clear()
    monkeypatch.setattr(phases_docker.helpers_subprocess, "run_subprocess", _fake_run_subprocess({}))
    assert phases_docker._registry_step_llm_provision(str(core_dir)) is False
    assert "LLM virtual keys provisioned" in caplog.text, "успешный прогон сохраняет IMP:9 success"
    logger.critical("[IMP:9][test] llm provision failure surfaces — OK (AI-0010r)")
