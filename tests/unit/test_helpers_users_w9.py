# GREP_SUMMARY: test-helpers-users-w9, T9.18, authorized-keys, reconcile, forced-command, prefix-drift, add-ssh-key
# STRUCTURE: ▶ test_*_append → ключа нет → append │ ▶ test_*_matching_noop → строка == expected → skip │ ▶ test_*_stale_prefix → другой command= → reconcile (перезапись) │ ▶ test_*_missing_prefix → ключ без prefix → reconcile (добавление)
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.18 (B-5) DevPlan 136 W9: add_ssh_key реконсилит command=
##           префикс существующей записи authorized_keys (дрейф → перезапись, не дубль/пропуск).
##           W4b (160 T4.2): subprocess-канал через CommandRunner-fake параметром (0 monkeypatch).
## @scope    unit-тесты: home_dir=tmp_path (override — без реального /home); CommandRunner-fake
##           с ассертами вызовов (chown/usermod) — никаких реальных subprocess.
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - R5-negative: stale prefix (тот вход, что молча пропускался) → запись ПЕРЕЗАПИСАНА
##   - Совпадение строки → no-op (без записи); ключ без префикса при expected → префикс добавлен
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: T9.18 — parse существующей записи, сравнить
##            command= prefix, reconcile при drift.
## @changes  2026-08-05 · Created (DevPlan 136 W9)
## @changes  2026-08-13 · DevPlan 160 W4b — +CommandRunner-fake (убраны monkeypatch users.run_subprocess/subprocess.run)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers.users import add_ssh_key, create_user
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test-key"
PREFIX = 'command="cd /opt/platform && python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict'
STALE_PREFIX = 'command="cd /opt/platform && python3 -m core.internal.deploy.orchestrator_cli OLD_DISPATCH",restrict'


# region CLASS_FakeCommandRunner
class FakeCommandRunner:
    """CommandRunner-fake (DevPlan 160 W4b): записывает вызовы, отдаёт предзаданные rc/stdout.

    ## @purpose — DI-замена subprocess-канала: create_user/add_ssh_key принимают runner
    ##            параметром — тест передаёт fake с ассертами вместо monkeypatch.
    ## @complexity — O(1) per call
    """

    def __init__(self, responses: dict[str, tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        # "cmd..." → (rc, stdout); отсутствующий ключ → (0, "") (успех без вывода)
        self.responses: dict[str, tuple[int, str]] = responses or {}

    def run(self, cmd, *, timeout: int = 30, check: bool = False, non_fatal: bool = False, fatal_rc=()):  # ruff: ignore[ARG002]
        """CommandRunner.run — имитация канона: graceful rc | raise при check=True."""
        self.calls.append(list(cmd))
        key = " ".join(cmd)
        rc, out = self.responses.get(key, (0, ""))
        if check and rc != 0:
            msg = f"fake command failed (exit={rc}): {' '.join(cmd)}"
            raise RuntimeError(msg)
        return subprocess.CompletedProcess(cmd, rc, out, "")


# endregion CLASS_FakeCommandRunner


def _auth_keys(home: Path) -> Path:
    return home / ".ssh" / "authorized_keys"


def _write_auth(home: Path, lines: list[str]) -> None:
    ssh = home / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    _auth_keys(home).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _call(home: Path, runner: FakeCommandRunner) -> None:
    add_ssh_key("ci-deploy", KEY, forced_command_prefix=PREFIX, home_dir=str(home), runner=runner)


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.18 — ключ отсутствует → append
# · Scenario: authorized_keys пуст → add_ssh_key добавляет запись с command= префиксом
# · Remove if: add_ssh_key semantics change
@ldd_trajectory
def test_add_ssh_key_appends_when_missing(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.18: ключ отсутствует → append (существующее поведение сохраняется)."""
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner()
    _call(tmp_path, runner)
    content = _auth_keys(tmp_path).read_text(encoding="utf-8")
    assert f"{PREFIX} {KEY}" in content, "запись с префиксом обязана добавиться"
    # chown выполнен через runner (ни одного реального subprocess)
    assert any(cmd[0] == "chown" for cmd in runner.calls), "chown обязан идти через CommandRunner"
    logger.critical("[IMP:9][test] key appended when missing — OK (T9.18)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.18 — точное совпадение → no-op (без перезаписи)
# · Scenario: запись == expected → add_ssh_key не трогает файл (skip)
# · Remove if: add_ssh_key semantics change
@ldd_trajectory
def test_add_ssh_key_matching_entry_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.18: строка == ожидаемая → no-op (skip, mtime не меняется)."""
    caplog.set_level(logging.INFO)
    _write_auth(tmp_path, [f"{PREFIX} {KEY}"])
    before = _auth_keys(tmp_path).read_text(encoding="utf-8")
    _call(tmp_path, FakeCommandRunner())
    after = _auth_keys(tmp_path).read_text(encoding="utf-8")
    assert before == after, "no-op: файл не меняется при точном совпадении"
    assert "already present with matching prefix" in caplog.text
    logger.critical("[IMP:9][test] matching entry → no-op — OK (T9.18)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.18/B-5 — stale command= префикс реконсилится
# · Scenario: запись с ключом, но СТАРЫМ command= префиксом (тот вход, что молча пропускался
# ·   по «key in content») → add_ssh_key перезаписывает строку на актуальный префикс
# · Last fail: 2026-08-05 — duplicate-check по ключу без сравнения префикса → канал оставался
# ·   на старом диспетчере после обновления платформы (B-5)
# · Remove if: add_ssh_key semantics change
@ldd_trajectory
def test_add_ssh_key_reconciles_stale_prefix(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.18: stale command= префикс → строка перезаписывается (reconcile, не дубль)."""
    caplog.set_level(logging.INFO)
    _write_auth(tmp_path, [f"{STALE_PREFIX} {KEY}"])
    _call(tmp_path, FakeCommandRunner())

    content = _auth_keys(tmp_path).read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if KEY in line]
    assert len(lines) == 1, f"ключ не должен дублироваться: {lines}"
    assert f"{PREFIX} {KEY}" in content, "актуальный префикс обязан заменить stale"
    assert "STALE" not in content and "OLD_DISPATCH" not in content, "stale-префикс удалён"
    assert "prefix reconciled" in caplog.text
    logger.critical("[IMP:9][test] stale prefix reconciled — OK (T9.18)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.18 — ключ без префикса при expected → префикс добавлен
# · Scenario: запись = голый ключ (без forced-command), expected prefix задан → reconcile
# · Remove if: add_ssh_key semantics change
@ldd_trajectory
def test_add_ssh_key_adds_missing_prefix(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.18: ключ есть БЕЗ префикса → префикс добавляется (reconcile)."""
    caplog.set_level(logging.INFO)
    _write_auth(tmp_path, [KEY])
    _call(tmp_path, FakeCommandRunner())

    content = _auth_keys(tmp_path).read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if KEY in line]
    assert len(lines) == 1
    assert lines[0] == f"{PREFIX} {KEY}", f"префикс обязан быть добавлен: {lines[0]!r}"
    logger.critical("[IMP:9][test] missing prefix added — OK (T9.18)")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · create_user — B20b (141 r2)
# · Last fail: существующий ci-deploy без группы platform → пост-деплой чейн (receive)
# ·   писал с Permission denied в /opt/platform артефакты root:platform (catalog.json,
# ·   prometheus-targets) — WARN'ы скрыты, молчаливая деградация.
# · Remove if: create_user перестанет реконсилить группы существующих юзеров
@ldd_trajectory
def test_create_user_existing_adds_missing_groups(caplog: pytest.LogCaptureFixture) -> None:
    """B20b: существующий юзер получает недостающие группы (usermod -aG)."""
    caplog.set_level(logging.INFO)
    # Точный вход B20b: `id ci-deploy` rc=0 (существует), `id -Gn` без platform
    runner = FakeCommandRunner(
        responses={
            "id ci-deploy": (0, "uid=999(ci-deploy) gid=999(ci-deploy)"),
            "id -Gn ci-deploy": (0, "ci-deploy docker"),
        }
    )

    create_user("ci-deploy", ["docker", "platform"], runner=runner)

    usermod_calls = [c for c in runner.calls if c[0] == "usermod"]
    logger.info("[IMP:9][unit][users] usermod_calls=%s", usermod_calls)
    assert len(usermod_calls) == 1, f"ожидался 1 usermod -aG, получено {len(usermod_calls)}: {usermod_calls}"
    assert usermod_calls[0][:3] == ["usermod", "-aG", "platform"], f"usermod args: {usermod_calls[0]}"
    assert "added to groups" in caplog.text
    logger.critical("[IMP:9][test] existing user groups reconciled — OK (B20b)")
