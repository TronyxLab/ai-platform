# GREP_SUMMARY: test-watchdog unhealthy-restart cooldown dry-run stdlib-only docker-ps-inspect run-cmd-DI state-file is-n-loop cron-idempotent journald-persistent
# STRUCTURE: ┌run_cmd DI + facts.which(docker)┐ → ○ scenarios ∋ (healthy no-op / 1-run wait / 2-runs restart / cooldown / restart:no / RestartCount>5 / dry-run / docker-fail) → ⊕ state assertions + IMP:9 → ┤ cron/journald helper idempotency (tmp_path)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/healthcheck/watchdog.py (DevPlan 132 W1) +
##           install_cron_watchdog / ensure_journald_persistent idempotency (helpers/system.py).
## @scope    Pure Python — docker CLI полностью замокан (run_cmd DI-канал; which docker через
##           EnvironmentFacts-fake параметром, DevPlan 160 W4b).
##           tmp_path для state-файла; CRON_WATCHDOG_FILE/JOURNALD_CONF перенаправляются в tmp_path.
## @invariants
##   - 0 реальных docker-вызовов (тесты прогоняются на машинах без docker)
##   - state-файл всегда в tmp_path (Zero Hardcode Rule)
##   - LDD: сценарии с действием assert IMP:9 RESTART; R5 negative с оригинальной формой
##   - R1: каждый тест имеет реальные assertions (нет pass-тестов)
## @rationale  DevPlan 132 W1 §TEST_SPEC: healthy→no-op, 1 run→wait, 2 runs→restart+state,
##             cooldown, restart:"no", RestartCount>5, dry-run, IMP:9, R5 negative.
## @changes  2026-08-04 | DevPlan 132 W1 — created
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import system as _system_helpers
from core.internal.healthcheck import watchdog

logger = logging.getLogger(__name__)


# region HELPERS


class FakeFacts:
    """EnvironmentFacts-fake (DevPlan 160 W4b): which('docker') через параметр, не monkeypatch."""

    def __init__(self, docker_path: str | None) -> None:
        self._docker = docker_path

    def is_root(self) -> bool:  # pragma: no cover
        return True

    def which(self, binary: str) -> str | None:
        return self._docker if binary == "docker" else None

    def path_isfile(self, _path) -> bool:  # pragma: no cover
        return False


def _inspect_json(name, health, restart_count=0, restart_policy="unless-stopped") -> list:
    """Build docker inspect JSON output for a single container."""
    state: dict = {"RestartCount": restart_count}
    if health is not None:
        state["Health"] = {"Status": health}
    return [
        {
            "Name": f"/{name}",
            "State": state,
            "HostConfig": {"RestartPolicy": {"Name": restart_policy}},
        }
    ]


class FakeDocker:
    """Fake _run_cmd: serves docker ps/inspect (batch)/restart + telegram notify from in-memory data."""

    def __init__(self, containers: list) -> None:
        self.containers = containers  # list of inspect-JSON lists
        self.restart_calls: list[str] = []
        self.notify_calls: list[list[str]] = []

    def __call__(self, cmd, timeout: int = 30, env=None) -> subprocess.CompletedProcess:  # ruff: ignore[ARG002]
        if cmd[:2] == ["docker", "ps"]:
            ids = "\n".join(str(i) for i in range(len(self.containers)))
            return subprocess.CompletedProcess(cmd, 0, ids, "")
        if cmd[:2] == ["docker", "inspect"]:
            # T2.7: batch inspect — cmd[2:] = все контейнеры, JSON-массив в порядке аргументов
            ids = cmd[2:]
            payload = [self.containers[int(i)][0] for i in ids]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        if cmd[:2] == ["docker", "restart"]:
            self.restart_calls.append(cmd[2])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["python3", "-m", "core.internal.shared.notifications"]:
            self.notify_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        msg = f"unexpected cmd: {cmd}"
        raise AssertionError(msg)


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """State file in tmp_path (never touches /run/platform)."""
    return tmp_path / "watchdog-state.json"


def _run(containers, state_path: Path, now: float, dry_run: bool = False):
    """Run run_watchdog with docker mocked via run_cmd DI-канал (167 D0) and `now` injected."""
    fake = FakeDocker(containers)
    exit_code = watchdog.run_watchdog(
        dry_run=dry_run,
        state_file=str(state_path),
        now=now,
        facts=FakeFacts(docker_path="/usr/bin/docker"),
        run_cmd=fake,
    )
    return exit_code, fake


# endregion HELPERS


# region W1_WATCHDOG_SCENARIOS


# 🧪 TRAP[TEST] · Regression · Scenario: healthy container → no-op
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: watchdog decision logic changes
def test_healthy_container_noop(state_file: Path, caplog) -> None:
    """Healthy containers: no restart, exit 0, empty unhealthy_since state."""
    caplog.set_level(logging.INFO)
    containers = [_inspect_json("postgres", "healthy", restart_count=0)]

    exit_code, fake = _run(containers, state_file, now=100.0)

    assert exit_code == 0, "healthy stack → exit 0"
    assert fake.restart_calls == [], "no restart for healthy container"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["unhealthy_since"] == {}, "healthy container must not be tracked"
    logger.info("[IMP:9][test_watchdog] healthy → no-op PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: first unhealthy run records since, waits
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: unhealthy_since tracking logic changes
def test_unhealthy_first_run_waits(state_file: Path, caplog) -> None:
    """1st unhealthy run: records unhealthy_since, no restart (age < 10 min)."""
    caplog.set_level(logging.INFO)
    containers = [_inspect_json("redis", "unhealthy", restart_count=1)]

    exit_code, fake = _run(containers, state_file, now=100.0)

    assert exit_code == 0
    assert fake.restart_calls == [], "first run must wait (unhealthy_since just recorded)"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["unhealthy_since"].get("redis") == 100.0, "unhealthy_since must be recorded"
    assert "last_restart" not in saved or saved["last_restart"] == {}, "no restart yet"
    logger.info("[IMP:9][test_watchdog] 1-run wait PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: unhealthy ≥10 min → restart + state + IMP:9
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: restart decision logic changes
def test_unhealthy_second_run_restarts(state_file: Path, caplog) -> None:
    """2nd run (age ≥ 10 min): docker restart + last_restart recorded + IMP:9 RESTART log."""
    caplog.set_level(logging.INFO)
    state_file.write_text(json.dumps({"unhealthy_since": {"redis": 100.0}, "last_restart": {}}), encoding="utf-8")
    containers = [_inspect_json("redis", "unhealthy", restart_count=1)]

    exit_code, fake = _run(containers, state_file, now=100.0 + 10 * 60 + 1)

    assert exit_code == 0
    assert len(fake.restart_calls) == 1, "container unhealthy ≥10 min must be restarted"
    assert fake.notify_calls, "restart must trigger telegram notify"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "redis" in saved["last_restart"], "last_restart must be recorded after restart"

    found_imp9 = any("[IMP:9][watchdog] RESTART redis" in r.message for r in caplog.records)
    assert found_imp9, "Critical LDD Error: no IMP:9 RESTART log"
    logger.info("[IMP:9][test_watchdog] 2-run restart + state + IMP:9 PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: cooldown 30 min suppresses restart
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: cooldown logic changes
def test_cooldown_prevents_restart(state_file: Path, caplog) -> None:
    """Unhealthy ≥10 min but last_restart < 30 min ago → wait (no restart)."""
    caplog.set_level(logging.INFO)
    state_file.write_text(
        json.dumps({"unhealthy_since": {"redis": 100.0}, "last_restart": {"redis": 100.0 + 10 * 60}}), encoding="utf-8"
    )
    containers = [_inspect_json("redis", "unhealthy", restart_count=1)]

    exit_code, fake = _run(containers, state_file, now=100.0 + 15 * 60)

    assert exit_code == 0
    assert fake.restart_calls == [], "cooldown 30 min must suppress restart"
    logger.info("[IMP:9][test_watchdog] cooldown PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: restart policy "no" (one-shot) excluded
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: restart:"no" filter changes
def test_restart_no_policy_excluded(state_file: Path, caplog) -> None:
    """One-shot containers (restart:"no": prometheus-config-init, minio-createbuckets) never restart."""
    caplog.set_level(logging.INFO)
    containers = [_inspect_json("prometheus-config-init", "unhealthy", restart_count=0, restart_policy="no")]

    exit_code, fake = _run(containers, state_file, now=100.0)

    assert exit_code == 0
    assert fake.restart_calls == [], "restart:'no' containers must be excluded"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["unhealthy_since"] == {}, "one-shot container must not be tracked"
    logger.info("[IMP:9][test_watchdog] restart:no exclusion PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: RestartCount > 5 (CrashLoopBackOff) skipped
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: is_n_loop guard changes
def test_restart_count_over_5_skipped(state_file: Path, caplog) -> None:
    """CrashLoopBackOff (RestartCount > 5) — restart не лечит, watchdog пропускает (is_n_loop канон)."""
    caplog.set_level(logging.INFO)
    containers = [_inspect_json("litellm", "unhealthy", restart_count=6)]

    exit_code, fake = _run(containers, state_file, now=100.0)

    assert exit_code == 0
    assert fake.restart_calls == [], "RestartCount>5 must skip (CrashLoopBackOff)"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["unhealthy_since"] == {}, "crash-looping container must not be tracked"
    logger.info("[IMP:9][test_watchdog] RestartCount>5 skip PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: dry-run prints plan, 0 mutations
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: dry-run semantics change
def test_dry_run_no_restart_no_state_mutation(state_file: Path, caplog) -> None:
    """dry-run: no restart, no notify, no state file write (0 mutations)."""
    caplog.set_level(logging.INFO)
    state_file.write_text(json.dumps({"unhealthy_since": {"redis": 100.0}, "last_restart": {}}), encoding="utf-8")
    containers = [_inspect_json("redis", "unhealthy", restart_count=1)]

    exit_code, fake = _run(containers, state_file, now=100.0 + 10 * 60 + 1, dry_run=True)

    assert exit_code == 0
    assert fake.restart_calls == [], "dry-run must not restart"
    assert fake.notify_calls == [], "dry-run must not notify"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "last_restart" not in saved or saved["last_restart"] == {}, "dry-run must not mutate state"
    assert any("ACTION: restart" in r.message for r in caplog.records), "dry-run must print planned action"
    logger.info("[IMP:9][test_watchdog] dry-run 0 mutations PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: docker CLI unavailable → IMP:7 + exit 0
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: docker-unavailable semantics change
def test_docker_cli_unavailable_non_fatal(state_file: Path, caplog) -> None:
    """docker CLI недоступен (facts.which → None) → IMP:7 + exit 0 (cron продолжает работать)."""
    caplog.set_level(logging.INFO)

    exit_code = watchdog.run_watchdog(state_file=str(state_file), now=100.0, facts=FakeFacts(docker_path=None))

    assert exit_code == 0, "docker unavailable must be non-fatal (exit 0)"
    assert any("docker CLI unavailable" in r.message for r in caplog.records), "IMP:7 warning expected"
    logger.info("[IMP:9][test_watchdog] docker unavailable → exit 0 PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: docker ps failure → IMP:10 + exit 1
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: internal-error exit semantics change
def test_docker_ps_failure_exit_1(state_file: Path, caplog) -> None:
    """docker ps rc != 0 → IMP:10 + exit 1 (внутренняя ошибка)."""
    caplog.set_level(logging.INFO)

    def failing(cmd, timeout=30, env=None):
        return subprocess.CompletedProcess(cmd, 1, "", "Cannot connect to the Docker daemon")

    exit_code = watchdog.run_watchdog(
        state_file=str(state_file), now=100.0, facts=FakeFacts(docker_path="/usr/bin/docker"), run_cmd=failing
    )

    assert exit_code == 1, "docker ps failure → internal error (exit 1)"
    assert any("[IMP:10]" in r.message and "docker ps failed" in r.message for r in caplog.records), (
        "IMP:10 docker error log expected"
    )
    logger.info("[IMP:9][test_watchdog] docker ps fail → exit 1 PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · «живой, но unhealthy» контейнер детектится на рестарт
# · Scenario: оригинальная форма бага аудита 2026-08-03 — docker restart-политика перезапускает
# ·   ТОЛЬКО упавшие контейнеры; «живой, но unhealthy» (health=unhealthy, restart=unless-stopped,
# ·   RestartCount=0 — контейнер НЕ падал, docker его не перезапустит) висит вечно.
# ·   Watchdog обязан принять restart-решение на ЭТОТ точный вход.
# · Last fail: до 132 W1 — unhealthy контейнер с restart=unless-stopped и RestartCount=0 не
# ·   перезапускался никем (gap аудита 2026-08-03, приоритет A)
# · Remove if: watchdog перестаёт рестартить live-unhealthy контейнеры
def test_live_unhealthy_container_detected_negative(state_file: Path, caplog) -> None:
    """R5 negative: оригинальный вход аудита (live-unhealthy + unless-stopped + RestartCount=0) → restart."""
    caplog.set_level(logging.INFO)
    # Точный вход из аудита: контейнер жив (не упал → RestartCount=0), health=unhealthy,
    # restart=unless-stopped — docker НЕ перезапустит его сам (нет crash).
    state_file.write_text(json.dumps({"unhealthy_since": {"nginx": 100.0}, "last_restart": {}}), encoding="utf-8")
    containers = [_inspect_json("nginx", "unhealthy", restart_count=0, restart_policy="unless-stopped")]

    exit_code, fake = _run(containers, state_file, now=100.0 + 10 * 60 + 1)

    assert exit_code == 0
    assert fake.restart_calls == ["0"], (
        "R5 FAIL: watchdog пропустил live-unhealthy контейнер (оригинальный вход аудита)"
    )
    logger.info("[IMP:9][test_watchdog][R5] live-unhealthy контейнер детектирован на рестарт PASS")


# endregion W1_WATCHDOG_SCENARIOS


# region W1_STDLIB_INVARIANT_162


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · WATCHDOG_STATE_FILE env overrides DEFAULT_STATE_FILE
# · Scenario: env WATCHDOG_STATE_FILE задан → DEFAULT_STATE_FILE (модульный литерал + env) отражает env
# · Last fail: до 162 W1-1 DEFAULT_STATE_FILE брался из deploy_paths.watchdog_state_file() (импорт core.internal)
# · Remove if: константа DEFAULT_STATE_FILE перестаёт читать WATCHDOG_STATE_FILE
def test_state_file_env_override(monkeypatch, tmp_path: Path, caplog) -> None:
    """WATCHDOG_STATE_FILE env overrides the module-level DEFAULT_STATE_FILE (162 W1-1)."""
    caplog.set_level(logging.INFO)
    import importlib

    override = tmp_path / "custom-state.json"
    monkeypatch.setenv("WATCHDOG_STATE_FILE", str(override))
    try:
        mod = importlib.reload(watchdog)
        assert str(override) == mod.DEFAULT_STATE_FILE, "env WATCHDOG_STATE_FILE must override literal default"
        assert str(override) == mod._get_state_file(), "_get_state_file() must reflect env override"
    finally:
        importlib.reload(watchdog)  # restore canonical module state (env unset by monkeypatch teardown)
    logger.info("[IMP:9][test_watchdog] WATCHDOG_STATE_FILE env override PASS")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · watchdog.py has 0 module-level core.internal imports
# · Scenario: cron запускает watchdog БЕЗ PYTHONPATH — module-level импорты core.internal = ModuleNotFoundError
# · Last fail: 2026-08-13 — «from core.internal.shared.deploy_paths import ...» (строка 56) ломал cron каждые 5 минут
# · Remove if: watchdog перестаёт исполняться из cron без PYTHONPATH (stdlib-only инвариант снят)
# GUARD-PRESERVE (168): stdlib-only инвариант watchdog.py — 0 module-level core.internal импортов
# (cron запускает watchdog БЕЗ PYTHONPATH; импорт = ModuleNotFoundError каждые 5 минут, R5-контекст 162 W1-1)
def test_no_core_internal_import_at_module_level(caplog) -> None:
    """@invariant 1: watchdog.py — 0 module-level `import/from core.internal` (cron без PYTHONPATH)."""
    caplog.set_level(logging.INFO)
    source = Path(watchdog.__file__).read_text(encoding="utf-8")
    # Module-level ТОЛЬКО: без lstrip — функция-level импорты (env_facts lazy, 162 W1-1) легитимны
    import_lines = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    offending = [ln for ln in import_lines if "core.internal" in ln]
    assert offending == [], f"module-level core.internal import(s) found in watchdog.py: {offending}"
    logger.info("[IMP:9][test_watchdog] stdlib-only invariant (0 core.internal imports) PASS")


# endregion W1_STDLIB_INVARIANT_162


# region W1_CRON_HELPERS_IDEMPOTENCY

_helpers = _system_helpers


class _JournaldRunner:
    """CommandRunner-fake (W-H DevPlan 163): systemctl restart assertion (0 патчей run_subprocess)."""

    def run(self, cmd, **_kw):
        assert cmd[:2] == ["systemctl", "restart"], f"unexpected cmd: {cmd}"
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def cron_dir(tmp_path: Path) -> Path:
    """DI (W-H): install_cron_watchdog(cron_file=tmp_path) — 0 патчей константы."""
    return tmp_path


# 🧪 TRAP[TEST] · Regression · Scenario: install_cron_watchdog fresh install + idempotent no-op
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: install_cron_watchdog contract changes
def test_install_cron_watchdog_idempotent(cron_dir: Path, tmp_path: Path, caplog) -> None:
    """Fresh install writes cron line; second call = no-op (identical content → SKIP)."""
    caplog.set_level(logging.INFO)
    core_dir = str(tmp_path / "core")

    assert _helpers.install_cron_watchdog(core_dir, cron_file=str(tmp_path / "platform-watchdog")) is True
    cron_file = tmp_path / "platform-watchdog"
    assert cron_file.exists(), "watchdog cron file must be created"
    content = cron_file.read_text(encoding="utf-8")
    assert "*/5 * * * * root" in content, "cron schedule */5 required"
    assert "flock -n /run/lock/platform-watchdog.lock" in content, "flock -n lock required"
    assert "timeout 50" in content, "timeout 50 required"
    assert "internal/healthcheck/watchdog.py" in content, "absolute watchdog.py path required"
    mtime_before = cron_file.stat().st_mtime

    # ── Second call → no-op (content match) ──
    assert _helpers.install_cron_watchdog(core_dir, cron_file=str(tmp_path / "platform-watchdog")) is True
    assert cron_file.stat().st_mtime == mtime_before, "idempotent second call must not rewrite the file"
    assert any("no-op (idempotent)" in r.message for r in caplog.records), "idempotency log expected"
    logger.info("[IMP:9][test_watchdog] install_cron_watchdog idempotent PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: install_cron_watchdog content change → rewrite
# · Last fail: N/A (new test — DevPlan 132 W1)
# · Remove if: install_cron_watchdog rewrite logic changes
def test_install_cron_watchdog_rewrites_on_content_change(cron_dir: Path, tmp_path: Path, caplog) -> None:
    """Changed core_dir (content mutation) → file overwritten with new line."""
    caplog.set_level(logging.INFO)
    assert (
        _helpers.install_cron_watchdog(str(tmp_path / "core-a"), cron_file=str(tmp_path / "platform-watchdog")) is True
    )
    assert (
        _helpers.install_cron_watchdog(str(tmp_path / "core-b"), cron_file=str(tmp_path / "platform-watchdog")) is True
    )
    content = (tmp_path / "platform-watchdog").read_text(encoding="utf-8")
    assert "core-b" in content and "core-a" not in content, "content mutation must be overwritten"
    logger.info("[IMP:9][test_watchdog] install_cron_watchdog rewrite PASS")


@pytest.fixture
def journald_conf(tmp_path: Path) -> Path:
    """DI (W-H): ensure_journald_persistent(conf=, runner=) — 0 патчей константы/канала."""
    return tmp_path / "journald.conf"


# 🧪 TRAP[TEST] · Regression · Scenario: ensure_journald_persistent sets Storage=persistent (idempotent)
# · Last fail: N/A (new test — DevPlan 132 W3)
# · Remove if: ensure_journald_persistent contract changes
def test_ensure_journald_persistent_idempotent(journald_conf: Path, caplog) -> None:
    """Default config (#Storage=auto) → Storage=persistent appended; second call → no-op."""
    caplog.set_level(logging.INFO)
    journald_conf.write_text("#Storage=auto\n#Compress=yes\n", encoding="utf-8")

    assert _helpers.ensure_journald_persistent(str(journald_conf), runner=_JournaldRunner()) is True
    content = journald_conf.read_text(encoding="utf-8")
    assert "Storage=persistent" in content, "Storage=persistent must be set"
    assert "#Storage=auto" in content, "commented line must be preserved (append active line)"

    # ── Second call → no-op (Storage=persistent already present) ──
    mtime_before = journald_conf.stat().st_mtime
    assert _helpers.ensure_journald_persistent(str(journald_conf), runner=_JournaldRunner()) is True
    assert journald_conf.stat().st_mtime == mtime_before, "idempotent second call must not rewrite"
    assert any("no-op (idempotent)" in r.message for r in caplog.records), "idempotency log expected"
    logger.info("[IMP:9][test_watchdog] ensure_journald_persistent idempotent PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: ensure_journald_persistent replaces active Storage=auto
# · Last fail: N/A (new test — DevPlan 132 W3)
# · Remove if: _set_storage_persistent replace logic changes
def test_ensure_journald_persistent_replaces_active_value(journald_conf: Path, caplog) -> None:
    """Active `Storage=auto` line → replaced with Storage=persistent (не append дубля)."""
    caplog.set_level(logging.INFO)
    journald_conf.write_text("Storage=auto\n", encoding="utf-8")

    assert _helpers.ensure_journald_persistent(str(journald_conf), runner=_JournaldRunner()) is True
    content = journald_conf.read_text(encoding="utf-8")
    assert content.count("Storage=persistent") == 1, "exactly one active Storage=persistent"
    assert "Storage=auto" not in content, "active Storage=auto must be replaced"
    logger.info("[IMP:9][test_watchdog] journald Storage=auto replacement PASS")


# endregion W1_CRON_HELPERS_IDEMPOTENCY
