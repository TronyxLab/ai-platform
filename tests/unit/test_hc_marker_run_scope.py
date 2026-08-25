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
import os
import re
import time
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy import deploy_orchestrator as orch
from core.internal.bootstrap.deploy import orchestrator_metrics as om
from core.internal.bootstrap.lifecycle import state_machine as stm
from core.internal.bootstrap.lifecycle.phases import docker as phases_docker

logger = logging.getLogger(__name__)

_RUN_ID_SUFFIX_RE = re.compile(r"\.\d{8}T\d{6}-\d+$")


@pytest.fixture(autouse=True)
def _reset_run_start_ts():
    """QA R2/T2.B: module-global run-start не должен протекать между тестами (xdist-гигиена)."""
    stm.reset_run_start_ts()
    yield
    stm.reset_run_start_ts()


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


# ═══════════════════════════════════════════════════════════════════
# region TEST_freshness_r2 — QA R2 (DevPlan 14 T2.B): mtime ≥ run-start
# ═══════════════════════════════════════════════════════════════════


class TestMarkerFreshnessR2:
    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R2/T2.B — stale-маркер прошлого прогона
    # · Scenario: φ11 reader исполняется ДО φ12-писателя в том же прогоне → любой найденный
    #   маркер создан ПРОШЛЫМ прогоном; без freshness-проверки он глотал глубокий healthcheck
    #   после ротации секретов в φ9 (REGRESSIONS.md R2)
    # · Last fail: 2026-08-25 — читатель принимал любой маркер независимо от возраста
    # · Remove if: порядок фаз изменится так, что φ11 исполняется после писателя
    def test_stale_marker_does_not_suppress_deep_hc(self, state_dir: Path, caplog) -> None:
        """Маркер с mtime < run-start НЕ подавляет healthcheck; файл снимается."""
        caplog.set_level(logging.DEBUG)
        base = om.hc_marker_path("")
        old_marker = Path(f"{base}.20250101T000000-111")
        old_marker.touch()
        past = time.time() - 3600
        os.utime(old_marker, (past, past))

        hc_calls: list[int] = []
        rc = phases_docker._registry_step_healthcheck(
            str(state_dir / "node.yaml"),  # валидный путь — доходим до запуска healthcheck
            context="",
            run_start_ts=time.time(),  # прогон начался ПОСЛЕ создания маркера
            isfile_fn=lambda _p: True,
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        logger.info(
            "[IMP:9][test][freshness-r2] stale marker: rc=%s hc_calls=%d exists=%s",
            rc,
            len(hc_calls),
            old_marker.exists(),
        )
        assert rc is False
        assert hc_calls, "R2 FAIL: stale-маркер прошлого прогона не должен глушить healthcheck"
        assert not old_marker.exists(), "stale-маркер обязан быть снят читателем"

        # Канал state_machine: run-start зарегистрирован глобально (как из cli._run_phases)
        legacy = Path(om.hc_marker_path(""))
        legacy.touch()
        os.utime(legacy, (past, past))
        stm.set_run_start_ts(time.time())
        hc_calls.clear()
        rc2 = phases_docker._registry_step_healthcheck(
            str(state_dir / "node.yaml"),
            context="",
            isfile_fn=lambda _p: True,
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        assert rc2 is False
        assert hc_calls, "R2 FAIL: канал state_machine не применил freshness"
        assert not legacy.exists()
        logger.info("[IMP:9][test][freshness-r2] state_machine channel enforces freshness too")

    # 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · QA R2/T2.B — свежий маркер подавляет (retry)
    # · Scenario: retry-семантика — маркер, созданный ПОСЛЕ старта текущего прогона
    #   (писатель φ12 уже отработал в предыдущей попытке), продолжает гасить standalone HC
    # · Last fail: N/A (preventive guard против over-blocking при введении freshness)
    # · Remove if: freshness-критерий отменён
    def test_fresh_marker_suppresses_deep_hc(self, state_dir: Path, caplog) -> None:  # ruff: ignore[ARG002]
        """Маркер с mtime ≥ run-start → skip + unlink (поведение REF-0005 сохранено)."""
        caplog.set_level(logging.DEBUG)
        run_start = time.time() - 5
        base = om.hc_marker_path("")
        fresh_marker = Path(f"{base}.{time.strftime('%Y%m%dT%H%M%S')}-999")
        fresh_marker.touch()

        hc_calls: list[int] = []
        rc = phases_docker._registry_step_healthcheck(
            "",
            context="",
            run_start_ts=run_start,
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        logger.info(
            "[IMP:9][test][freshness-r2] fresh marker: rc=%s hc_calls=%d exists=%s",
            rc,
            len(hc_calls),
            fresh_marker.exists(),
        )
        assert rc is False
        assert not hc_calls, "свежий маркер текущего прогона обязан гасить standalone HC"
        assert not fresh_marker.exists(), "читатель обязан снять поглотивший маркер"

    # 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · run-start неизвестен → legacy семантика
    # · Scenario: standalone-исполнение фазы вне cli._run_phases (run-start None) — поведение
    #   идентично до-T2.B (маркер подавляет), свип φ12 остаётся вторым слоем защиты
    # · Last fail: N/A (backward-compat guard)
    # · Remove if: run-start становится обязательным контрактом всех точек входа
    def test_unknown_run_start_legacy_semantics(self, state_dir: Path, caplog) -> None:  # ruff: ignore[ARG002]
        """run_start None (не задан нигде) → маркер подавляет как раньше."""
        caplog.set_level(logging.DEBUG)
        base = om.hc_marker_path("")
        marker = Path(f"{base}.20250101T000000-222")
        marker.touch()

        hc_calls: list[int] = []
        rc = phases_docker._registry_step_healthcheck(
            "",
            context="",
            run_healthchecks_fn=lambda *_, **__: hc_calls.append(1),
        )
        assert rc is False
        assert not hc_calls, "без знания о run-start сохраняется legacy-подавление"
        assert not marker.exists()
        logger.info("[IMP:9][test][freshness-r2] unknown run-start keeps legacy suppression")


# endregion TEST_freshness_r2
