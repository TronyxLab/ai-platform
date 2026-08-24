# GREP_SUMMARY: hc-marker, run-scope, REF-0005, unlink-init, healthcheck-done, deploy-orchestrator, phases-docker, stale-sweep
# STRUCTURE: ▶ _set_hc_marker [failed==[] ? touch .hc_done_in_deploy[.<ctx>].<run-id> : skip] → ◇ φ11 reader [glob run-scoped + legacy exact] → ◇ _sweep_stale_hc_markers [init/update start] → ⎋ honest healthcheck per run
# region MODULE_CONTRACT
## @purpose  Unit-тесты REF-0005 (DevPlan 11 W0): run-scoped hc-done маркер — честный success-
##           marker (failed==[]), имя с run-id, unlink на старте init/update (φ8/φ12).
## @scope    Писатель deploy_orchestrator._set_hc_marker, читатель phases/docker.
##           _registry_step_healthcheck, свип phases/docker._sweep_stale_hc_markers — реальные
##           файлы в tmp_path (native imports, без subprocess).
## @invariants
##   - Маркер пишется ТОЛЬКО при пустом failed (success-marker после доказательства)
##   - Имя содержит run-id формата YYYYMMDDTHHMMSS-<pid> (чужой запуск не гасит наш healthcheck)
##   - Свип на старте φ8/φ12 удаляет legacy (bare/per-context) + run-scoped варианты СВОЕГО scope
## @rationale BUG-0501≡BUG-0703: вечный маркер прошлого запуска гасил единственный глубокий
##            healthcheck φ11 (unlink→rewrite цикл); три слоя защиты: honesty-write,
##            run-scoping, sweep-at-start.
# endregion MODULE_CONTRACT

import inspect
import logging
import re
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy import deploy_orchestrator as orch
from core.internal.bootstrap.deploy import orchestrator_metrics as om
from core.internal.bootstrap.lifecycle.phases import docker as phases_docker

logger = logging.getLogger(__name__)

_RUN_ID_SUFFIX_RE = re.compile(r"\.\d{8}T\d{6}-\d+$")


@pytest.fixture()
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Перенаправить PLATFORM_STATE_DIR-константу маркера в tmp (SoT-path остаётся hc_marker_path)."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(om, "_HC_DONE_MARKER", str(state / ".hc_done_in_deploy"))
    return state


def _markers(state: Path) -> list[str]:
    return sorted(p.name for p in state.iterdir())


# region TEST_writer_honesty
class TestSetHcMarkerHonesty:
    # 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · REF-0005/BUG-0501 — success-marker после доказательства
    # · Scenario: failed=[] → ровно один run-scoped маркер; failed=["postgres"] → 0 маркеров.
    # · Last fail: 2026-08-24 — маркер писался БЕЗУСЛОВНО даже при failed-группах.
    # · Remove if: hc-done сигнализация переезжает из файлового маркера в другой канал.
    def test_writes_run_scoped_only_on_success(self, state_dir: Path, caplog) -> None:
        """failed=[] → run-scoped маркер; failed непустой → маркер НЕ создаётся."""
        caplog.set_level(logging.DEBUG)
        orch._set_hc_marker([])
        names = _markers(state_dir)
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
                print(record.message)
        print("--- END LDD TRAJECTORY ---")
        logger.info("[IMP:9][test] writer success: markers=%s", names)
        assert len(names) == 1, f"ровно один маркер при failed==[]: {names}"
        assert names[0].startswith(".hc_done_in_deploy"), names
        assert _RUN_ID_SUFFIX_RE.search(names[0]), f"имя обязано содержать run-id: {names[0]}"

        # ── honesty: failed>0 → маркер не пишется ──
        for p in state_dir.iterdir():
            p.unlink()
        orch._set_hc_marker(["postgres"])
        logger.info("[IMP:9][test] writer skipped-on-failed: markers=%s", _markers(state_dir))
        assert _markers(state_dir) == [], "маркер запрещено писать при failed>0 (REF-0005)"

    # 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · REF-0005 — per-context run-scoped имя
    # · Last fail: N/A (расширение T9.19 на run-id).
    # · Remove if: контекстная суффиксация маркера меняется.
    def test_run_scoped_with_context(self, state_dir: Path, monkeypatch) -> None:
        """CONTEXT=ctx-a → имя .hc_done_in_deploy.ctx-a.<run-id>."""
        monkeypatch.setenv("CONTEXT", "ctx-a")
        orch._set_hc_marker([])
        names = _markers(state_dir)
        logger.info("[IMP:9][test] writer context: markers=%s", names)
        assert len(names) == 1
        assert names[0].startswith(".hc_done_in_deploy.ctx-a."), names
        assert _RUN_ID_SUFFIX_RE.search(names[0]), names


# endregion TEST_writer_honesty


# region TEST_reader_and_sweep
class TestReaderAndSweep:
    # 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · REF-0005 — читатель находит run-scoped маркер
    # · Scenario: run-scoped маркер текущего прогона существует → standalone HC пропускается
    # ·   И маркер снимается (unlink→rewrite цикл не накапливает мёртвые маркеры).
    # · Last fail: 2026-08-24 — читатель проверял ТОЛЬКО точный legacy-путь → run-scoped
    # ·   маркер невидим → двойной healthcheck.
    # · Remove if: reader/scope семантика маркера меняется.
    def test_reader_skips_and_unlinks_run_scoped(self, state_dir: Path, caplog) -> None:
        """Run-scoped маркер → skip standalone healthcheck + unlink файла."""
        caplog.set_level(logging.DEBUG)
        base = om.hc_marker_path("")
        run_marker = Path(f"{base}.20260824T211000-4242")
        run_marker.touch()

        hc_calls: list[int] = []
        rc = phases_docker._registry_step_healthcheck(
            "",  # node.yaml не нужен: выход по маркеру раньше node_yaml-проверки
            context="",
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        logger.info(
            "[IMP:9][test] reader run-scoped: rc=%s hc_calls=%d marker_exists=%s",
            rc,
            len(hc_calls),
            run_marker.exists(),
        )
        assert rc is False
        assert not hc_calls, "маркер текущего запуска гасит standalone HC (в пределах одного прогона)"
        assert not run_marker.exists(), "читатель обязан снять маркер (unlink)"
        assert _markers(state_dir) == []

    # 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · REF-0005 — unlink-on-init/update (свип)
    # · Scenario: свип своего scope удаляет legacy-bare + legacy-per-context + run-scoped;
    # ·   чужой контекст НЕ затрагивается; повторный вызов = no-op.
    # · Last fail: 2026-08-24 — маркер переживал прогон → φ11 следующего update пропускал
    # ·   единственный глубокий healthcheck по чужому маркеру.
    # · Remove if: sweep-at-start заменяется lifetime/TTL механизмом.
    def test_sweep_removes_own_scope_idempotent(self, state_dir: Path, caplog) -> None:
        """Свип context='' убирает bare+run, оставляет чужой контекст; идемпотентен."""
        caplog.set_level(logging.DEBUG)
        base = om.hc_marker_path("")
        other_ctx = Path(f"{base}.ctx-other")
        own_run = Path(f"{base}.20260824T210000-111")
        other_run = Path(f"{base}.ctx-other.20260824T210001-222")
        bare = Path(base)
        for p in (bare, other_ctx, own_run, other_run):
            p.touch()

        removed = phases_docker._sweep_stale_hc_markers(context="")
        logger.info("[IMP:9][test] sweep empty-ctx: removed=%d left=%s", removed, _markers(state_dir))
        assert removed == 2, f"bare + свой run-scoped: removed={removed}"
        assert not bare.exists() and not own_run.exists()
        assert other_ctx.exists() and other_run.exists(), "чужой контекст свип не трогает"

        # ── свой контекст: legacy-per-context + его run-scoped ──
        removed = phases_docker._sweep_stale_hc_markers(context="ctx-other")
        logger.info("[IMP:9][test] sweep ctx-other: removed=%d left=%s", removed, _markers(state_dir))
        assert removed == 2
        assert _markers(state_dir) == [], "после обоих свипов состояние чистое"

        # ── идемпотентность: второй прогон = 0 удалений ──
        assert phases_docker._sweep_stale_hc_markers(context="") == 0

    # 🧪 TRAP[TEST] · 2026-08-24 · STRUCTURAL · REF-0005 — свип вызывается на старте φ8/φ12
    # · Last fail: 2026-08-24 — unlink-on-start отсутствовал вовсе.
    # · Remove if: фазы φ8/φ12 перестают быть стартом lifecycle-прогона деплоя.
    def test_phases_call_sweep_at_start(self, caplog) -> None:
        """φ8 phase_deploy_services и φ12 phase_deploy_update вызывают _sweep_stale_hc_markers."""
        caplog.set_level(logging.INFO)
        src_init = inspect.getsource(phases_docker.phase_deploy_services)
        src_update = inspect.getsource(phases_docker.phase_deploy_update)
        logger.info("[IMP:9][test] structural: both phases reference sweep")
        assert "_sweep_stale_hc_markers" in src_init, "φ8 обязан свипать stale-маркеры на старте"
        assert "_sweep_stale_hc_markers" in src_update, "φ12 обязан свипать stale-маркеры на старте"


# endregion TEST_reader_and_sweep
