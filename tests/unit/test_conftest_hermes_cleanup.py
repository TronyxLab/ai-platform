"""
# GREP_SUMMARY: test-conftest-hermes-cleanup, sessionfinish, hermes-test, sweep, label-only, ai-platform.test, W12-T13, R5
# STRUCTURE: ▶ DI _docker_ps_ids (ps_ids_fn) + runner → ◇ _final_hermes_test_cleanup × 3 (label rm / no-containers / name-filter negative) → ⊕ assert rm -f label-only → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for tests/_conftest/session.py::_final_hermes_test_cleanup (DevPlan 140 W5,
##           W12-T13): sessionfinish-свип hermes-test-* контейнеров работает label-only
##           (ai-platform.test=true). Name-prefix fallback УДАЛЁН — sweep НЕ должен обращаться
##           к docker ps по фильтру name=hermes-test- (контракт §4.5: label-first единственный путь).
## @scope    Только функция _final_hermes_test_cleanup (DI: ps_ids_fn + runner — без реального
##           docker daemon). Дополняет test_session_xdist_guards.py, который
##           тестирует master-guard хука, а не внутренности свипа.
## @invariants
##   - DI: _final_hermes_test_cleanup(ps_ids_fn=..., runner=...) — fake-функции параметром
##     (DevPlan 167 D2 CommandRunner-seam — 0 патчей)
##   - R5 (Test Honesty): negative-тест — свип НИКОГДА не запрашивает name=hermes-test- (детектор
##     старого fallback-пути); контейнер без label не подхватывается sweep'ом
##   - Native imports: from tests._conftest.ldd import ldd_trajectory; import _conftest.session
##   - LDD: @ldd_trajectory asserts IMP:9 presence (тестовый логгер)
##   - Test Honesty R1/R2: реальные falsifiable assertions
## @rationale DevPlan 140 W5 (W12-T13): fallback удалён — если кто-то вернёт name-фильтр в свип,
##            negative-тест упадёт (R5-гейт честности). Создатель контейнеров (test_hermes_init.py)
##            использует ту же константу _HERMES_TEST_LABEL — метка и свип не расходятся.
## @changes 2026-08-06 | Created (DevPlan 140 W5)
##           2026-08-14 | DevPlan 167 D2 — monkeypatch → DI-параметры (ps_ids_fn/runner), 5 → 0 setattr
# endregion MODULE_CONTRACT
"""

import logging
import subprocess

import _conftest.session as session_mod

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# region TEST_DOUBLES
def _make_fake_docker_ps_ids(calls: list[list[str]]) -> list[str]:
    """Build a _docker_ps_ids fake: records every call, returns IDs only for the label filter.

    ## @purpose  Имитация docker ps -a --filter label=ai-platform.test=true. Любой запрос по
    ##            другому фильтру (напр. name=hermes-test-) вернёт [] — контейнер без метки
    ##            НЕ подхватывается свипом. Передаётся в _final_hermes_test_cleanup(ps_ids_fn=).
    ## @io       ⇥ calls: list (мутируемый recorder) → ⎋ fake-функция
    ## @complexity O(1)
    """

    def _fake(extra_filters: list[str]) -> list[str]:
        calls.append(extra_filters)
        if extra_filters == ["--filter", f"label={session_mod._HERMES_TEST_LABEL}"]:
            return ["abc123label", "def456label"]
        return []  # любой иной фильтр (в т.ч. name=hermes-test-) — пусто: без метки не подхватывается

    return _fake


def _make_fake_subprocess_run(rm_calls: list[list[str]]) -> object:
    """Build a subprocess.run fake: record every docker rm -f call (DI runner, 0 патчей).

    ## @purpose  Fake CommandRunner для _final_hermes_test_cleanup(runner=) — свип не трогает
    ##           реальный docker daemon (DevPlan 167 D2).
    ## @io       ⇥ rm_calls: list (мутируемый recorder) → ⎋ fake-runner (Callable)
    ## @complexity O(1)
    """

    def _fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 3 and cmd[0] == "docker" and cmd[1] == "rm" and cmd[2] == "-f":
            rm_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _fake_run


# endregion TEST_DOUBLES


# ══════════════════════════════════════════════════════════════════════════════
# Tests: _final_hermes_test_cleanup — label-only sweep (DevPlan 140 W5, W12-T13)
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_label_sweep_removes_labeled_containers
## @purpose  Контейнеры с меткой ai-platform.test=true удаляются через docker rm -f (label-first)
## @io       caplog → None (assert rm -f вызван с label-ID + единственный label-запрос)
## @complexity 1 — DI wiring assert
# 🧪 TRAP[TEST] · Regression · Scenario: label-контейнеры существуют → docker rm -f по label-ID ·
# · Last fail: 2026-08-05 — свип зависел от name-fallback (контейнеры без метки) · Remove if: метка удалена из создателя
@ldd_trajectory
def test_label_sweep_removes_labeled_containers(caplog) -> None:
    """Label-sweep: containers carrying ai-platform.test=true are removed via docker rm -f."""
    docker_ps_calls: list[list[str]] = []
    rm_calls: list[list[str]] = []
    session_mod._final_hermes_test_cleanup(
        ps_ids_fn=_make_fake_docker_ps_ids(docker_ps_calls),
        runner=_make_fake_subprocess_run(rm_calls),
    )

    # Единственный запрос docker ps — по label (name-фильтра нет вовсе)
    assert docker_ps_calls == [["--filter", f"label={session_mod._HERMES_TEST_LABEL}"]], (
        f"sweep должен запрашивать ТОЛЬКО label-фильтр: {docker_ps_calls}"
    )
    # docker rm -f вызван ровно один раз со всеми label-ID
    assert len(rm_calls) == 1, f"expected 1 docker rm -f call, got {rm_calls}"
    assert rm_calls[0] == ["docker", "rm", "-f", "abc123label", "def456label"], rm_calls[0]
    logger.info("[IMP:9][test][label-sweep] removed %d label-container(s) via docker rm -f", len(rm_calls[0]) - 3)


# endregion FUNC_test_label_sweep_removes_labeled_containers


# region FUNC_test_label_sweep_no_containers_no_rm
## @purpose  Нет label-контейнеров → docker rm -f НЕ вызывается, логируется «no containers to remove»
## @io       caplog → None (assert no rm, no docker ps name-filter)
## @complexity 1 — recorder assert
# 🧪 TRAP[TEST] · Regression · Scenario: нет label-контейнеров → sweep no-op без rm · Last fail: N/A · Remove if: no-op семантика изменена
@ldd_trajectory
def test_label_sweep_no_containers_no_rm(caplog) -> None:
    """No label containers → docker rm -f is NOT called (no-op, 'no containers to remove')."""
    docker_ps_calls: list[list[str]] = []
    rm_calls: list[list[str]] = []

    def _fake_empty(extra_filters: list[str]) -> list[str]:
        docker_ps_calls.append(extra_filters)
        return []

    session_mod._final_hermes_test_cleanup(
        ps_ids_fn=_fake_empty,
        runner=_make_fake_subprocess_run(rm_calls),
    )

    assert docker_ps_calls == [["--filter", f"label={session_mod._HERMES_TEST_LABEL}"]], docker_ps_calls
    assert rm_calls == [], f"docker rm -f не должен вызываться без label-контейнеров: {rm_calls}"
    logger.info("[IMP:9][test][label-sweep] no label containers → no rm calls (sweep no-op)")


# endregion FUNC_test_label_sweep_no_containers_no_rm


# region FUNC_test_label_sweep_negative_name_filter_never_used
## @purpose  R5 negative (W12-T13): свип НЕ запрашивает docker ps по name=hermes-test- — контейнер
##           БЕЗ метки не подхватывается; name-prefix fallback удалён (контракт §4.5 label-only)
## @io       caplog → None (assert NO name-фильтр в запросах)
## @complexity 1 — recorder assert
# 🧪 TRAP[TEST] · NEGATIVE (R5) · session._final_hermes_test_cleanup — W12-T13 (name-fallback)
# · Scenario: исходный вход бага — контейнер hermes-test-* БЕЗ метки; fallback ловил его по имени.
# ·   Negative: свип НЕ должен запрашивать name=hermes-test- и НЕ должен rm по имени —
# ·   иначе вернётся 503 false-lead для чужих/немеченых контейнеров
# · Last fail: 2026-08-05 — fallback name=hermes-test- активен (DevPlan 140 §4.5 фиксирует)
# · Remove if: label-first отменён и name-свип введён как канонический путь (запрещено §4.5)
@ldd_trajectory
def test_label_sweep_negative_name_filter_never_used(caplog) -> None:
    """R5 negative: sweep never queries docker ps by name=hermes-test- (name-fallback removed)."""
    docker_ps_calls: list[list[str]] = []
    rm_calls: list[list[str]] = []
    session_mod._final_hermes_test_cleanup(
        ps_ids_fn=_make_fake_docker_ps_ids(docker_ps_calls),
        runner=_make_fake_subprocess_run(rm_calls),
    )

    # Ни один запрос к docker ps не содержит name-фильтра (fallback удалён)
    name_filters = [c for c in docker_ps_calls if "name=hermes-test-" in " ".join(c)]
    assert name_filters == [], f"R5 FAIL: sweep использует name-фильтр hermes-test-: {name_filters}"
    # docker rm -f не содержит имён (только label-ID)
    for cmd in rm_calls:
        assert not any("hermes-test-" in str(arg) for arg in cmd), f"rm по имени запрещён (label-only): {cmd}"
    assert docker_ps_calls, "sweep должен хотя бы раз запросить docker ps"
    logger.info(
        "[IMP:9][test][label-sweep][negative] name-fallback отсутствует: %d docker ps call(s), all label-only",
        len(docker_ps_calls),
    )


# endregion FUNC_test_label_sweep_negative_name_filter_never_used
