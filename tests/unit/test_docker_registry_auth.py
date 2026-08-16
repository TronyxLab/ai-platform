"""
# GREP_SUMMARY: test_docker_registry_auth, docker-hub-login, daemon-json, log-driver, idempotent, missing-creds, DI, FakeCommandRunner, daemon_json_path
# STRUCTURE: ▶ tmp_path + DI-параметры (daemon_json_path/docker_config_path/login_fn/restart_fn) → ◇ docker login → ◇ daemon.json idempotent → ◇ missing creds warn → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for docker_registry_auth.py — Docker Hub auth + daemon.json log-config.
## @scope    Tests configure_docker_auth, _write_daemon_json, _docker_login.
## @invariants
##   - Все subprocess/файловые зависимости через DI-параметры (E1): daemon_json_path,
##     docker_config_path, login_fn, restart_fn — 0 monkeypatch subprocess.run/os
##   - daemon.json пишется в tmp_path (daemon_json_path)
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory
## @rationale DevPlan 047 Phase 7: Docker Hub auth eliminates rate-limit during bootstrap.
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
##           2026-08-13 | E1 (160) — DI-конвертация (setattr 13 → 0, −100%)
##           2026-08-13 | 164 W0-3.7 — registry-mirror удалён: тесты переписаны на log-config
##            семантику; test_daemon_json_merges_mirrors удалён (inventory: changelog)
# endregion MODULE_CONTRACT
"""

import json
import logging
import sys
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import docker_registry_auth as dra
import pytest

pytestmark = pytest.mark.static_audit


class _FakeFacts:
    """Fake EnvironmentFacts: файлы существуют по факту (real os.path.isfile на tmp_path)."""

    def is_root(self) -> bool:
        return True

    def which(self, _binary) -> str | None:
        return None

    def path_isfile(self, path) -> bool:
        return Path(path).is_file()


def _ok_login(username: str, token: str) -> bool:
    """Fake docker login: успех (DI login_fn вместо subprocess)."""
    return True


# ═══════════════════════════════════════════════════════════════════
# region Tests: docker login
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Docker login via DI login_fn — success/failure (P-консолидация 168)
# · Scenario: login_fn (DI) True/False → _do_login propagates; configure_docker_auth продолжает (non-fatal)
# · Last fail: N/A (new test)
# · Remove if: docker login logic changes
@pytest.mark.parametrize(
    "login_fn, expected, log_msg",
    [
        pytest.param(_ok_login, True, "Docker login success — valid creds accepted", id="success"),
        pytest.param(lambda _, __: False, False, "Docker login failure — invalid creds rejected", id="fail"),
    ],
)
@ldd_trajectory
def test_docker_login(caplog, login_fn, expected, log_msg):
    """_do_login propagates login_fn result via shared docker_auth DI-shim (P-консолидация 168).

    Cases (1:1 из test_docker_login_success/test_docker_login_fail): fake login_fn
    True → ok is True; fake login_fn False → ok is False.
    """
    ok = dra._do_login(login_fn, "testuser", "testtoken")
    assert ok is expected
    logger.critical("[IMP:9][test] %s", log_msg)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: daemon.json idempotency
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · daemon.json write is idempotent (skip if log config present)
# · Scenario: Write daemon.json with log config, write again → second call returns False (no change)
# · Last fail: N/A (new test)
# · Remove if: daemon.json idempotency logic changes
@ldd_trajectory
def test_daemon_json_idempotent(caplog, tmp_path):
    """_write_daemon_json should skip if log-driver config already present."""
    daemon_path = str(tmp_path / "daemon.json")

    # First write — should return True (written)
    written1 = dra._write_daemon_json(daemon_path, facts=_FakeFacts())
    assert written1 is True
    assert Path(daemon_path).is_file()

    # Verify content
    with Path(daemon_path).open(encoding="utf-8") as f:
        data = json.load(f)
    assert data["log-driver"] == "json-file"
    assert data["log-opts"] == {"max-size": "10m", "max-file": "3"}
    assert "registry-mirrors" not in data, "registry-mirror УДАЛЁН (164 W0-3.7) — не должен записываться"

    # Second write — should return False (already configured)
    written2 = dra._write_daemon_json(daemon_path, facts=_FakeFacts())
    assert written2 is False
    logger.critical("[IMP:9][test] daemon.json idempotent — second write skipped")


# 🧪 TRAP[TEST] · Regression · DevPlan 162 W5-2 · _write_daemon_json сохраняет default-address-pools
# · Scenario: docker_installer пишет default-address-pools (10.32.0.0/16) в daemon.json; auth-модуль
# ·   (φ3) НЕ должен затереть ключ при merge log-config (merge, не overwrite)
# · Last fail: 2026-08-13 — W5-2: пулы добавлены в DAEMON_JSON_DEFAULT; merge-корректность не покрыта
# · Remove if: _write_daemon_json перестанет быть merge-only
@ldd_trajectory
def test_daemon_json_preserves_default_address_pools(caplog, tmp_path):
    """_write_daemon_json должен сохранить default-address-pools (162 W5-2) при merge log-config."""
    daemon_path = str(tmp_path / "daemon.json")
    pools = [{"base": "10.32.0.0/16", "size": 24}]
    with Path(daemon_path).open("w", encoding="utf-8") as f:
        json.dump({"live-restore": True, "default-address-pools": pools}, f)

    # DI (167 D4): daemon_json_path параметром — уже-DI шов (E1, 160); monkeypatch константы не нужен
    written = dra._write_daemon_json(daemon_path, facts=_FakeFacts())

    assert written is True
    with Path(daemon_path).open(encoding="utf-8") as f:
        data = json.load(f)
    assert data["default-address-pools"] == pools, "default-address-pools потеряны при merge (W5-2)"
    assert data["live-restore"] is True
    assert data["log-driver"] == "json-file"
    logger.critical("[IMP:9][test] daemon.json merge preserves default-address-pools (W5-2)")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: configure_docker_auth (integration)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · configure_docker_auth warns when creds missing (non-fatal)
# · Scenario: Empty username/token → WARN logged, returns True (log config still written)
# · Last fail: N/A (new test)
# · Remove if: missing creds handling changes
@ldd_trajectory
def test_missing_creds_warn(caplog, tmp_path):
    """configure_docker_auth should warn when creds missing but still configure daemon.json."""
    daemon_path = str(tmp_path / "daemon.json")
    restarts: list[int] = []

    ok = dra.configure_docker_auth(
        "",
        "",
        daemon_json_path=daemon_path,
        facts=_FakeFacts(),
        restart_fn=lambda: restarts.append(1) or True,
    )
    assert ok is True  # Non-fatal: log config written even without creds
    assert Path(daemon_path).is_file()
    assert len(restarts) == 1, "daemon.json записан → restart docker (log config применён)"
    logger.critical("[IMP:9][test] Missing creds WARN — daemon.json log config still written")


# 🧪 TRAP[TEST] · Regression · configure_docker_auth succeeds with valid creds
# · Scenario: Valid creds + fake login/restart → returns True, daemon.json written
# · Last fail: N/A (new test)
# · Remove if: configure_docker_auth integration changes
@ldd_trajectory
def test_configure_docker_auth_success(caplog, tmp_path):
    """configure_docker_auth should succeed with valid creds."""
    daemon_path = str(tmp_path / "daemon.json")
    restarts: list[int] = []

    ok = dra.configure_docker_auth(
        "user",
        "token",
        daemon_json_path=daemon_path,
        docker_config_path=str(tmp_path / "config.json"),  # отсутствует → auth_before=False
        facts=_FakeFacts(),
        login_fn=_ok_login,
        restart_fn=lambda: restarts.append(1) or True,
    )

    assert ok is True
    assert Path(daemon_path).is_file()
    assert len(restarts) == 1, "auth появилась → restart docker (1 раз)"
    logger.critical("[IMP:9][test] configure_docker_auth success — full flow OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D2 — restart guard, повторный вызов = no-op (AC-A2)
# · Scenario: daemon.json уже сконфигурирован И auth-запись уже есть → restart НЕ вызывается
# · Last fail: K2 (два restarts docker за init — φ3+φ6 вызывали docker_registry_auth.py)
# · Remove if: restart-guard логика меняется
@ldd_trajectory
def test_configure_docker_auth_no_restart_when_no_change(caplog, tmp_path):
    """configure_docker_auth: 0 restarts when auth state did not change (idempotent, D2)."""
    daemon_path = str(tmp_path / "daemon.json")
    config_path = str(tmp_path / "config.json")
    # Пред-сконфигурированный daemon.json (log config уже есть) → written=False
    dra._write_daemon_json(daemon_path, facts=_FakeFacts())
    # auth-запись УЖЕ существует → docker login no-op → auth_changed=False
    with Path(config_path).open("w", encoding="utf-8") as f:
        json.dump({"auths": {"registry-1.docker.io": {"auth": "dGVzdDp0ZXN0"}}}, f)
    restarts: list[int] = []

    ok = dra.configure_docker_auth(
        "user",
        "token",
        daemon_json_path=daemon_path,
        docker_config_path=config_path,
        facts=_FakeFacts(),
        login_fn=_ok_login,
        restart_fn=lambda: restarts.append(1) or True,
    )

    assert ok is True
    assert len(restarts) == 0, f"Expected 0 docker restarts (no auth-state change), got {len(restarts)}"
    logger.critical("[IMP:9][test] No auth-state change → 0 docker restarts (D2 guard) — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D2 — restart при появлении auth-записи
# · Scenario: auth-записи не было до login → появилась → restart вызывается ровно 1 раз
# · Last fail: N/A (new test — D2 guard)
# · Remove if: restart-guard логика меняется
@ldd_trajectory
def test_configure_docker_auth_restart_when_auth_appeared(caplog, tmp_path):
    """configure_docker_auth: 1 restart when auth entry appeared after login (D2 guard)."""
    daemon_path = str(tmp_path / "daemon.json")
    config_path = str(tmp_path / "config.json")
    # Пред-сконфигурированный daemon.json → written=False; auth появилась → auth_changed=True
    dra._write_daemon_json(daemon_path, facts=_FakeFacts())
    restarts: list[int] = []

    ok = dra.configure_docker_auth(
        "user",
        "token",
        daemon_json_path=daemon_path,
        docker_config_path=config_path,  # отсутствует ДО → auth_before=False
        facts=_FakeFacts(),
        login_fn=_ok_login,
        restart_fn=lambda: restarts.append(1) or True,
    )

    assert ok is True
    assert len(restarts) == 1, f"Expected exactly 1 docker restart, got {len(restarts)}"
    logger.critical("[IMP:9][test] Auth entry appeared → 1 docker restart (D2 guard) — OK")


# endregion
