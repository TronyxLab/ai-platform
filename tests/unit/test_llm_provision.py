# GREP_SUMMARY: test-llm-provision render litellm-config provision-llm subprocess non-fatal CORE_DIR
# STRUCTURE: ┌6 test functions┐ → ◇ success (1) → ◇ render script missing (1) → ◇ provision missing (1)
#            → ◇ provision non-zero (1) → ◇ subprocess raises (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/deploy/llm_provision.py — render_and_provision_llm
##           (DevPlan 117 G T58.5 extraction from context_deployer.py).
## @scope    No real subprocess — all runs mocked, CORE_DIR pointed at tmp_path fixtures.
## @invariants
##   - All subprocess calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.5 §TEST_SPEC — llm_provision direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.5 — created
# endregion MODULE_CONTRACT

from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.deploy.llm_provision import render_and_provision_llm

pytestmark = pytest.mark.static_audit


class TestRenderAndProvisionLlm:
    """Tests for render_and_provision_llm — all subprocess mocked."""

    # 🧪 TRAP[TEST] · Regression · Scenario: both subprocess calls succeed
    # · Expect: IMP:9 logs for render + provision
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: llm pipeline flow changes
    def test_render_and_provision_success(self, tmp_path: Path, caplog) -> None:
        """Both steps succeed → IMP:9 logs present."""
        caplog.set_level(0)
        core_dir = Path(str(tmp_path))
        renderer = core_dir / "internal" / "llm" / "config_renderer.py"
        renderer.parent.mkdir(parents=True, exist_ok=True)
        renderer.write_text("mock\n", encoding="utf-8")
        provision = core_dir / "entrypoints" / "provision-llm.sh"
        provision.parent.mkdir(parents=True, exist_ok=True)
        provision.write_text("mock\n", encoding="utf-8")

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="ok", stderr="")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", return_value=True),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm(core_dir_override=str(tmp_path))

        assert len(calls) == 2
        assert any("litellm-config.yml rendered" in r.message for r in caplog.records)
        assert any("Key provisioning succeeded" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: config_renderer.py missing
    # · Expect: WARN (non-fatal), provision still attempted
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: render-missing handling changes
    def test_render_missing_script_non_fatal(self, tmp_path: Path, caplog) -> None:
        """config_renderer.py absent → WARN, no exception."""
        caplog.set_level(0)

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", return_value=False),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run") as mock_run,
        ):
            render_and_provision_llm(core_dir_override=str(tmp_path))

        mock_run.assert_not_called()
        assert any("config_renderer.py not found" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: provision-llm.sh missing
    # · Expect: render runs, provision WARN (non-fatal)
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: provision-missing handling changes
    def test_provision_missing_script(self, tmp_path: Path, caplog) -> None:
        """provision-llm.sh absent → WARN, only render runs."""
        caplog.set_level(0)
        calls = []

        def isfile(path):
            return str(path).endswith("config_renderer.py")

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="ok", stderr="")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm(core_dir_override=str(tmp_path))

        assert len(calls) == 1
        assert any("provision-llm.sh not found" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: provision returns non-zero
    # · Expect: WARN with stderr excerpt, no exception
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: provision-nonzero handling changes
    def test_provision_nonzero_warns(self, tmp_path: Path, caplog) -> None:
        """provision-llm.sh returns 3 → WARN with stderr excerpt."""
        caplog.set_level(0)

        def isfile(path):
            return True

        def mock_run(cmd, **kwargs):
            if "config_renderer.py" in cmd[1]:
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            return mock.MagicMock(returncode=3, stdout="", stderr="boom\nboom2\n")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm(core_dir_override=str(tmp_path))

        assert any("Key provisioning returned 3" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: subprocess raises during render
    # · Expect: WARN (non-fatal), provision still attempted
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: subprocess error handling changes
    @pytest.mark.parametrize(
        "exc",
        [
            __import__("subprocess").CalledProcessError(1, "python3"),
            OSError("boom"),
            FileNotFoundError("python3"),
        ],
        ids=["called-process", "os-error", "file-not-found"],
    )
    def test_render_subprocess_error(self, exc, tmp_path: Path, caplog) -> None:
        """Render subprocess raises → WARN, pipeline continues."""
        caplog.set_level(0)

        def isfile(path):
            return True

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=exc),
        ):
            render_and_provision_llm(core_dir_override=str(tmp_path))

        assert any("Failed to render litellm-config.yml" in r.message for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════
# QA L6/G2 (DevPlan 14 T1.3): admin_client.list_keys pagination —
# строгая конверсия total_pages + transport-error fail-loud
# ($TEST_SPEC: module under test = admin_client.list_keys)
# ═══════════════════════════════════════════════════════════════════

import httpx

from core.internal.llm.admin_client import (
    LiteLLMAdminClient,
    LiteLLMTransportError,
)

_KEY_PAGE_BODY = {
    "key": "sk-x",
    "models": [],
    "max_budget": 0,
    "rpm_limit": 1,
    "metadata": {},
}


def _paginated_handler(total_pages_value: object, seen_pages: list[str]):
    """MockTransport handler: /key/info?page=N → keys page с заданным total_pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        seen_pages.append(page)
        body = {"keys": [dict(_KEY_PAGE_BODY, key=f"sk-page{page}")], "total_pages": total_pages_value}
        return httpx.Response(200, json=body)

    return handler


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · QA L6 — строковый total_pages "2"
# · Scenario: LiteLLM отдал total_pages строкой "2" → isinstance(int) ложно → листинг молча
#   обрывался после страницы 1 → provisioner генерировал дубликаты ключей за её пределами
# · Last fail: 2026-08-25 (admin_client.py:476) — строка "2" не проходила isinstance(int)
# · Remove if: LiteLLM гарантированно отдаёт int total_pages (контракт зафиксирован)
def test_pagination_string_total_pages():
    """str '2' → обе страницы прочитаны (строгая конверсия QA L6)."""
    seen_pages: list[str] = []
    client = LiteLLMAdminClient(
        base_url="http://litellm-test:4000",
        master_key="mk-test",
        transport=httpx.MockTransport(_paginated_handler("2", seen_pages)),
    )
    keys = client.list_keys()
    client.close()

    assert len(keys) == 2, f"обе страницы обязаны быть прочитаны: {len(keys)}"
    assert {k["key"] for k in keys} == {"sk-page1", "sk-page2"}
    assert seen_pages == ["1", "2"], f"пагинация должна пройти страницы 1→2: {seen_pages}"
    print("[IMP:9][test][pagination] string '2' → pages fetched:", seen_pages)


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · int total_pages ≥2 страниц (базовая пагинация)
# · Last fail: N/A (preventive — guard базового пути при ужесточении конверсии)
# · Remove if: вместе с детектором
def test_pagination_int_total_pages_two_pages():
    """int 2 → обе страницы прочитаны."""
    seen_pages: list[str] = []
    client = LiteLLMAdminClient(
        base_url="http://litellm-test:4000",
        master_key="mk-test",
        transport=httpx.MockTransport(_paginated_handler(2, seen_pages)),
    )
    keys = client.list_keys()
    client.close()

    assert len(keys) == 2 and seen_pages == ["1", "2"]


@pytest.mark.parametrize("junk", [{"pages": True}, 2.5, True, "две"])
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · malformed pagination payload → fail-loud
# · Scenario: мусорный total_pages (dict/float/bool/нечисловая строка) раньше молча трактовался
#   как single-page; теперь → LiteLLMTransportError (fail-closed)
# · Last fail: N/A (preventive, паритет strict-conversion QA L6)
# · Remove if: политика пагинации меняется
def test_pagination_malformed_total_pages_raises(junk):
    """Мусорный total_pages → LiteLLMTransportError('malformed pagination payload')."""
    seen_pages: list[str] = []
    client = LiteLLMAdminClient(
        base_url="http://litellm-test:4000",
        master_key="mk-test",
        transport=httpx.MockTransport(_paginated_handler(junk, seen_pages)),
    )
    with pytest.raises(LiteLLMTransportError, match="malformed pagination payload"):
        client.list_keys()
    client.close()
    print(f"[IMP:9][test][pagination] malformed total_pages={junk!r} → TransportError")


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · transport-error ≠ 404 → TransportError
# · Scenario: ConnectError при GET /key/info — раньше generic-исключение абортило фазу;
#   контракт REF-0104: transport-сбой оборачивается в LiteLLMTransportError (ловится
#   provisioner'ом как WARN+failed), а не маскируется в «нет ключей»
# · Last fail: 2026-08-25 (QA C4) — кортеж provisioner'а физически не ловил TransportError
# · Remove if: транспорт мигрирует на другой HTTP-стек
def test_list_keys_connect_error_raises_transport_error():
    """httpx.ConnectError → LiteLLMTransportError (не None, не сырой httpx)."""

    def failing_handler(request: httpx.Request) -> httpx.Response:
        connect_err = httpx.ConnectError("connection refused", request=request)
        raise connect_err

    client = LiteLLMAdminClient(
        base_url="http://litellm-test:4000",
        master_key="mk-test",
        transport=httpx.MockTransport(failing_handler),
    )
    with pytest.raises(LiteLLMTransportError, match="ConnectError"):
        client.list_keys()
    client.close()
    print("[IMP:9][test][transport] ConnectError wrapped into LiteLLMTransportError")
