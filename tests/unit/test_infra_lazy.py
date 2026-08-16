"""
# GREP_SUMMARY: test infra-lazy proxy no-subprocess-on-import first-accessor single-subprocess cached _conftest B10-T5
# STRUCTURE: ▶ import tests._conftest.infra → ◇ module infra is _LazyTestInfraProxy (delegate None = 0 subprocess) →
#            ◇ proxy.accessor → exactly 1 _load_test_infra call → ◇ repeat access → cached (still 1) → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for the lazy test-infra proxy (DevPlan 116 B10 T5, U-74): importing
##           tests/_conftest/infra must NOT run the discover_modules.py subprocess; the FIRST
##           accessor call triggers exactly ONE load; subsequent calls reuse the cache.
## @scope    Tests _LazyTestInfraProxy semantics + module-level `infra` laziness. No Docker.
## @invariants
##   - Module import → proxy created with _delegate None (0 subprocess)
##   - First accessor → _load_test_infra() called exactly once
##   - Repeat access → cached (still 1 call)
##   - T21 import protocol preserved: `from _conftest.infra import infra` works unchanged
## @rationale  U-74: infra.py:271 ran subprocess at import — every static session paid the cost;
##             lazy init isolates static sessions (0 subprocess) and keeps Docker sessions at 1.
## @changes  2026-08-01 · Created (DevPlan 116 B10 T5)
# endregion MODULE_CONTRACT
"""

import importlib

import pytest

from tests._conftest import infra as infra_mod

pytestmark = pytest.mark.static_audit

logger = pytest.importorskip("logging").getLogger(__name__)

_FAKE_DATA = [
    {
        "module": "postgres",
        "container_names": ["postgres-test"],
        "networks": [],
        "ports": {},
        "compose_base": "/x",
        "compose_test": "/y",
    }
]


@pytest.fixture(autouse=True)
def _reset_infra_singleton(monkeypatch) -> None:
    """Reset the _TestInfra._instance class singleton before each proxy test.

    ## @purpose — _TestInfra._instance is a class-level singleton shared across tests;
    ##            without reset, a proxy would reuse a delegate built under a previous
    ##            test's monkeypatch. Reset guarantees each test measures its own lazy load.
    """
    # 📝 TRAP[DI-KEEP] · 2026-08-14 · — · reset _TestInfra._instance = None (DevPlan 167 D0)
    # · Rejected: DI-шов (reset — состояние, не I/O; loader-канал уже инъецируется через
    # ·   _LazyTestInfraProxy(loader=...), singleton-reset невыразим параметром)
    # · Reason: honest-floor §4 — reset-состояния легитимен (не патч внешнего I/O); единственный
    # ·   способ изолировать класс-синглтон между тестами (иначе delegate переиспользуется)
    # · Rev: при отказе от singleton-паттерна (DI-фабрика вместо __new__) — патч исчезает
    monkeypatch.setattr(infra_mod._TestInfra, "_instance", None)
    yield


# ═════════════════════════════════════════════════════════════════════════════
# region Module import — no subprocess
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T5 · module-level infra is a lazy proxy (delegate None)
# · Regression: U-74 — infra.py:271 instantiated _TestInfra at import → subprocess on import
# · Last fail: N/A (new lazy proxy)
# · Remove if: lazy infra mechanism replaced
def test_module_infra_is_lazy_proxy() -> None:
    """Importing _conftest.infra must not build the delegate (no subprocess at import)."""
    assert isinstance(infra_mod.infra, infra_mod._LazyTestInfraProxy), "module-level `infra` must be the lazy proxy"
    assert infra_mod.infra._delegate is None, "import must NOT instantiate the delegate — subprocess would have run"
    logger.critical("[IMP:9][test] module import — infra is lazy proxy, delegate None (0 subprocess)")


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T5 · fresh module reload still defers subprocess
# · Regression: U-74 — import-time subprocess
# · Last fail: N/A (new lazy proxy)
# · Remove if: lazy infra mechanism replaced
def test_module_import_no_subprocess() -> None:
    """A fresh import of _conftest.infra runs zero subprocess / zero _load_test_infra calls.

    DI (DevPlan 167 D0): инвариант «import не запускает subprocess» эквивалентен
    «delegate is None» — делегат строится ТОЛЬКО через _load_test_infra → subprocess.run.
    Проверка _delegate после reload заменяет 2 monkeypatch-патча (subprocess.run +
    _load_test_infra): если reload начнёт вызывать loader — delegate станет не-None → FAIL.
    """
    importlib.reload(infra_mod)

    assert infra_mod.infra._delegate is None, (
        "import must not build the delegate — subprocess/_load_test_infra would have run"
    )
    logger.critical("[IMP:9][test] fresh import — proxy delegate None (0 subprocess / 0 loads)")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Proxy accessor semantics — 1 load, then cached
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T5 · first accessor triggers exactly one load
# · Regression: U-74 — eager subprocess at import
# · Last fail: N/A (new lazy proxy)
# · Remove if: lazy infra mechanism replaced
def test_first_accessor_triggers_single_load() -> None:
    """First accessor call triggers exactly one _load_test_infra() invocation."""
    calls: list[str] = []

    def counting_loader():
        calls.append("load")
        return _FAKE_DATA

    proxy = infra_mod._LazyTestInfraProxy(loader=counting_loader)
    assert calls == [], "proxy construction must not load"

    _ = proxy.all_modules  # first accessor → load (B018: явное присвоение)
    assert calls == ["load"], f"first accessor must trigger exactly 1 load, got: {calls}"
    assert proxy._delegate is not None, "delegate must be built after first access"
    logger.critical("[IMP:9][test] first accessor — exactly 1 _load_test_infra call")


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T5 · repeat access is cached (still 1 load)
# · Regression: U-74 — repeated loads would re-run discover_modules
# · Last fail: N/A (new lazy proxy)
# · Remove if: caching semantics change
# GUARD-PRESERVE (168): единственное покрытие кэш-семантики lazy-прокси (повторный доступ =
# 1 load; повторный discover_modules на статических сессиях = лишний subprocess на каждый доступ)
def test_repeat_access_cached() -> None:
    """Subsequent accessor calls reuse the cached delegate — still exactly 1 load."""
    calls: list[str] = []

    def counting_loader():
        calls.append("load")
        return _FAKE_DATA

    proxy = infra_mod._LazyTestInfraProxy(loader=counting_loader)
    _ = proxy.all_modules  # first accessor → load (B018: явное присвоение)
    _ = proxy.all_modules  # cached after first load (B018: явное присвоение)
    _ = proxy.get_container_name("postgres")

    assert calls == ["load"], f"cached delegate must not re-run _load_test_infra, got: {calls}"
    logger.critical("[IMP:9][test] repeat access — cached, 1 total load")


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T5 · accessor returns real data through the proxy
# · Scenario: get_container_name via proxy resolves from loaded data
# · Last fail: N/A (new lazy proxy)
# · Remove if: proxy delegation removed
# GUARD-PRESERVE (168): единственное покрытие делегирования accessor'а через lazy-прокси
# (T21 import-протокол `from _conftest.infra import infra` работает без изменений)
def test_proxy_delegates_accessor_result() -> None:
    """Accessor results flow through the proxy unchanged (T21 protocol preserved)."""
    proxy = infra_mod._LazyTestInfraProxy(loader=lambda: _FAKE_DATA)

    assert proxy.get_container_name("postgres") == "postgres-test", "proxy must delegate to the real accessor result"
    logger.critical("[IMP:9][test] proxy delegation — get_container_name('postgres')='postgres-test'")


# endregion
