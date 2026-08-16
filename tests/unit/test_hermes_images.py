# GREP_SUMMARY: test-hermes-images hermes-agent-context build guard context docker-build mock-subprocess buildkit L1-collapse
# STRUCTURE: ┌DI runner + paths_env┐ → ◇ test build_context CONTEXT guard (empty → False) → ◇ test build_context cmd → ◇ test main dispatch (build-context/L2) → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/build/hermes_images.py (DevPlan 118 E8 — Python-порт
##           hermes-images.sh; L1→L2 коллапс DevPlan 002: build_l1/build-platform удалены).
##           Native imports, DI runner (subprocess override) — no real docker.
## @scope    Tests: build_context CONTEXT guard (fail-fast), build command construction
##           with --build-arg, единый Dockerfile path, subprocess failure propagation,
##           main() dispatch (build-context|L2).
## @invariants
##   - Все тесты используют DI runner (build_context(runner=...)) — no real docker (DevPlan 167 D1)
##   - PLATFORM_ROOT/_CACHE_DIR инжектятся через env-dict (paths_env) — zero hardcoded repo paths
##   - LDD: IMP:9 log on success, IMP:10 on CONTEXT guard failure
## @rationale E8 Strangler: docker build оркестрация → Python. Guard и build-команда — тестируемы.
##            L1 build удалён (DevPlan 002): единственный путь — build_context (единый Dockerfile).
## @changes  2026-08-02 | DevPlan 118 E8 — Created
## @changes  2026-08-14 | DevPlan 167 D1 — setattr→DI (runner + env-dict)
## @changes  2026-08-16 | DevPlan 002 W5 T5.1 — rewrite под build_context (guard + cmd + failure + main-dispatch)
# endregion MODULE_CONTRACT

import logging
from unittest import mock

import pytest

from core.internal.build import hermes_images

pytestmark = pytest.mark.static_audit


@pytest.fixture
def paths_env(tmp_path) -> dict[str, str]:
    """Point PLATFORM_ROOT/CACHE_DIR at tmp_path (env-dict DI — DevPlan 167 D1)."""
    fake_root = tmp_path / "repo"
    (fake_root / "core" / "modules" / "hermes-agent").mkdir(parents=True)
    return {"PLATFORM_ROOT": str(fake_root), "CACHE_DIR": str(tmp_path / "cache")}


# region TEST_build_context_guard
def test_build_context_empty_context_fails_fast(caplog: pytest.LogCaptureFixture, paths_env: dict[str, str]) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l2_empty_context_fails_fast — DevPlan 118 E migration unit test
    """build_context: empty CONTEXT → False (guard), no docker build invoked."""
    caplog.set_level(logging.INFO)
    called = mock.MagicMock()

    ok = hermes_images.build_context("", runner=called, env=paths_env)
    assert ok is False
    called.assert_not_called(), "docker build must NOT run when CONTEXT is empty (guard)"

    found_imp10 = any("[IMP:10]" in r.message and "CONTEXT" in r.message for r in caplog.records)
    assert found_imp10, "IMP:10 CONTEXT guard error expected"


def test_build_context_constructs_cmd_with_build_arg(
    caplog: pytest.LogCaptureFixture, paths_env: dict[str, str]
) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l2_constructs_cmd_with_build_arg — DevPlan 118 E migration unit test
    """build_context: docker build with --build-arg CONTEXT=<ctx>, -t hermes-agent-context, единый Dockerfile."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=0))

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return mock_run.return_value

    ok = hermes_images.build_context("my-org", runner=_fake_run, env=paths_env)
    assert ok is True

    cmd = captured[0]
    assert "--build-arg" in cmd and "CONTEXT=my-org" in cmd
    assert "-t" in cmd and "hermes-agent-context" in cmd
    assert "--platform" in cmd and "linux/amd64" in cmd
    assert any("type=local,src=" in c for c in cmd), "BuildKit cache-from expected"
    assert any("type=local,dest=" in c and "mode=max" in c for c in cmd), "BuildKit cache-to mode=max expected"
    # DevPlan 002: единый Dockerfile (не build/Dockerfile, не context/Dockerfile)
    dockerfile_idx = cmd.index("-f")
    assert cmd[dockerfile_idx + 1].endswith("core/modules/hermes-agent/Dockerfile"), (
        f"docker build должен использовать единый Dockerfile: {cmd[dockerfile_idx + 1]}"
    )
    assert not any("build/Dockerfile" in c for c in cmd), "build/Dockerfile удалён (L1 коллапс)"
    assert not any("context/Dockerfile" in c for c in cmd), "context/Dockerfile удалён (L1 коллапс)"

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 build-complete log expected"


def test_build_context_subprocess_failure_returns_false(
    caplog: pytest.LogCaptureFixture, paths_env: dict[str, str]
) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l1_subprocess_failure_returns_false — DevPlan 118 E migration unit test
    """build_context: docker build non-zero exit → False + IMP:10 log."""
    caplog.set_level(logging.INFO)
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=1))

    assert hermes_images.build_context("ctx", runner=lambda *_a, **_kw: mock_run.return_value, env=paths_env) is False
    found_imp10 = any("[IMP:10]" in r.message for r in caplog.records)
    assert found_imp10, "IMP:10 FAILED log expected"


# endregion


# region TEST_main_dispatch
def test_main_build_context_dispatches(caplog: pytest.LogCaptureFixture, paths_env: dict[str, str]) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_build_context_guard_via_main — DevPlan 118 E migration unit test
    """main(): build-context with CONTEXT → build_context через DI runner (exit 0)."""
    caplog.set_level(logging.INFO)
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=0))
    env = {**paths_env, "CONTEXT": "my-org"}
    rc = hermes_images.main(
        argv=["build-context"],
        runner=lambda *_a, **_kw: mock_run.return_value,
        env=env,
    )
    assert rc == 0


def test_main_l2_alias_dispatches(caplog: pytest.LogCaptureFixture, paths_env: dict[str, str]) -> None:
    # 🧪 TRAP[TEST] · 2026-08-16 · test_main_l2_alias — DevPlan 002 (L2-алиас сохранён)
    """main(): L2 (алиас build-context) → build_context через DI runner (exit 0)."""
    caplog.set_level(logging.INFO)
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=0))
    env = {**paths_env, "CONTEXT": "my-org"}
    rc = hermes_images.main(
        argv=["L2"],
        runner=lambda *_a, **_kw: mock_run.return_value,
        env=env,
    )
    assert rc == 0


def test_main_build_context_guard_via_main(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_build_context_guard_via_main — DevPlan 118 E migration unit test
    """main(): build-context without CONTEXT env → exit 1."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("CONTEXT", raising=False)
    assert hermes_images.main(argv=["build-context"]) == 1
    found = any("[IMP:10]" in r.message and "CONTEXT" in r.message for r in caplog.records)
    assert found, "IMP:10 CONTEXT guard error must be logged on main() dispatch"


def test_main_rejects_build_platform(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-16 · test_main_rejects_build_platform — DevPlan 002 R5 (negative)
    """main(): build-platform (L1 dispatch) → SystemExit (choices удалены)."""
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit):
        hermes_images.main(argv=["build-platform"])
    assert hermes_images.main is not None


# endregion
