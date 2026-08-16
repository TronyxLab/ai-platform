"""
# GREP_SUMMARY: test_install_acme, acme.sh, install, git-clone, merge-fallback, ecc, idempotent, apt-timeout, DI, runner
# STRUCTURE: ▶ tmp_path + FakeGitRunner (DI) → ◇ idempotent skip → ◇ fresh install → ◇ merge-fallback (*_ecc) → ◇ total failure → ◇ dnsapi_ext non-fatal → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/bootstrap/install_acme.py (DevPlan 164 W3.5-1) —
##           acme.sh install orchestration: idempotent skip, git clone + merge-fallback
##           (сохранение *_ecc), apt-get под APT_TIMEOUT, dnsapi_ext non-fatal.
## @scope    DI: FakeGitRunner (запись вызовов + симуляция git clone-семантики: clone в
##           непустой каталог → fail; clone-tmp/dnsapi_ext → ok). Без сети/root/реального git.
## @invariants
##   - Все subprocess через runner DI (0 monkeypatch subprocess)
##   - tmp_path (Zero Hardcode); каждый тест валидирует IMP:9 (ldd_trajectory)
##   - FakeGitRunner повторяет семантику mock git из test_nginx_acme.py D4 (017e1c1)
## @rationale W3.5-1: install-acme.sh 93 LOC → install_acme.py; unit-тесты заменяют
##            интеграционную фикстуру (D4) на быстрые hermetic-проверки.
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — создан
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap import install_acme
from core.internal.shared.timeouts import APT_TIMEOUT
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


class FakeGitRunner:
    """DI-раннер со встроенной git clone-семантикой (аналог mock git D4, 017e1c1).

    ## @purpose — Запись вызовов (calls) + симуляция git: clone в непустой существующий
    ##            каталог → rc 128 (как реальный git); clone-tmp/dnsapi_ext → rc 0 с
    ##            созданием файлов (side-effect, который потребляет _merge_no_clobber).
    ## @complexity — O(1) — решение по последнему аргументу команды
    """

    def __init__(self, *, clone_tmp_fail: bool = False, dnsapi_ext_fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.clone_tmp_fail = clone_tmp_fail
        self.dnsapi_ext_fail = dnsapi_ext_fail

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        self.kwargs.append({"timeout": timeout})
        if cmd and cmd[0] == "apt-get":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd and cmd[0] == "git":
            dest = cmd[-1]
            if "clone-tmp" in dest:
                if self.clone_tmp_fail:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: clone-tmp failed")
                p = Path(dest)
                p.mkdir(parents=True, exist_ok=True)
                (p / "acme.sh").write_text("mock acme.sh\n", encoding="utf-8")
                (p / "dnsapi").mkdir(exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "dnsapi_ext" in dest:
                if self.dnsapi_ext_fail:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: dnsapi_ext failed")
                p = Path(dest)
                p.mkdir(parents=True, exist_ok=True)
                (p / "README.md").write_text("mock dnsapi ext\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            # clone в ACME_HOME
            dest_path = Path(dest)
            if dest_path.exists() and any(dest_path.iterdir()):
                return subprocess.CompletedProcess(cmd, 128, "", f"fatal: destination path '{dest}' already exists")
            dest_path.mkdir(parents=True, exist_ok=True)
            (dest_path / "acme.sh").write_text("mock acme.sh\n", encoding="utf-8")
            (dest_path / "dnsapi").mkdir(exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _write_acme_binary(acme_home: Path) -> None:
    """Создать существующий акме-бинарник (идемпотентный skip-триггер)."""
    acme_home.mkdir(parents=True, exist_ok=True)
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text("#!/bin/sh\necho mock\n", encoding="utf-8")
    acme_sh.chmod(0o755)


# ═════════════════════════════════════════════════════════════════════════════
# region Tests
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · install_acme idempotent skip (acme.sh уже установлен)
# · Scenario: ACME_HOME с executable acme.sh → SKIP (True), 0 git/apt вызовов
# · Last fail: N/A (W3.5-1 новый модуль) · Remove if: idempotency логика меняется
@ldd_trajectory
def test_install_acme_idempotent_skip(caplog, tmp_path) -> None:
    """acme.sh уже установлен → True, никаких мутаций (git/apt не вызываются)."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    _write_acme_binary(acme_home)
    runner = FakeGitRunner()

    ok = install_acme.install_acme(str(acme_home), runner=runner)

    assert ok is True
    git_calls = [c for c in runner.calls if c and c[0] == "git"]
    assert not git_calls, f"идемпотентный skip не должен клонировать: {git_calls}"
    logger.critical("[IMP:9][test] install_acme SKIP — idempotent, 0 git calls")


# 🧪 TRAP[TEST] · Regression · install_acme fresh install (clone успешен)
# · Scenario: пустой ACME_HOME → apt-get git (APT_TIMEOUT) + git clone + dnsapi_ext clone
# · Last fail: N/A (W3.5-1) · Remove if: install-оркестрация меняется
@ldd_trajectory
def test_install_acme_fresh_install(caplog, tmp_path) -> None:
    """Fresh install: apt-get под APT_TIMEOUT, clone acme.sh + dnsapi_ext, exit True."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    acme_home.mkdir(parents=True, exist_ok=True)  # пустой каталог — clone OK
    runner = FakeGitRunner()

    ok = install_acme.install_acme(str(acme_home), runner=runner)

    assert ok is True
    cmds = [" ".join(c) for c in runner.calls]
    assert any(c.startswith("apt-get install") for c in cmds), f"apt-get git не вызван: {cmds}"
    assert any("git clone" in c and "acme.sh" in c and "clone-tmp" not in c for c in cmds), (
        f"clone acme.sh не вызван: {cmds}"
    )
    assert any("dnsapi_ext" in c for c in cmds), f"dnsapi_ext clone не вызван: {cmds}"
    assert (acme_home / "acme.sh").exists(), "acme.sh должен быть склонирован"
    logger.critical("[IMP:9][test] install_acme fresh — apt + clone + dnsapi_ext, exit True")


# 🧪 TRAP[TEST] · Regression · apt-get вызывается под timeout=APT_TIMEOUT (T7 канон)
# · Scenario: первый вызов — apt-get; kwargs.timeout == APT_TIMEOUT (300)
# · Last fail: install-acme.sh — 'timeout 300 apt-get' GNU wrapper (литерал) → канон APT_TIMEOUT
# · Remove if: apt-get убирается из install_acme
@ldd_trajectory
def test_install_acme_apt_get_uses_apt_timeout(caplog, tmp_path) -> None:
    """apt-get install git → timeout=APT_TIMEOUT (канон shared/timeouts, DevPlan 123 T7)."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    acme_home.mkdir(parents=True, exist_ok=True)
    runner = FakeGitRunner()

    install_acme.install_acme(str(acme_home), runner=runner)

    apt_index = next(i for i, c in enumerate(runner.calls) if c and c[0] == "apt-get")
    assert runner.kwargs[apt_index]["timeout"] == APT_TIMEOUT, (
        f"apt-get должен быть под timeout=APT_TIMEOUT, got {runner.kwargs[apt_index]['timeout']}"
    )
    logger.critical("[IMP:9][test] install_acme apt-get под timeout=APT_TIMEOUT (%d)", APT_TIMEOUT)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · D4 merge-fallback сохраняет *_ecc (исходный вход 017e1c1)
# · Scenario: ACME_HOME существует с *_ecc cert stores; clone в него FAIL (128) → clone-tmp +
# ·   no-clobber merge → *_ecc сохранены, acme.sh скопирован, WARN «merged with fresh clone»
# · Last fail: 2026-08-04 — bare git clone падал на re-run (bootstrap φ7 blocker), cert stores терялись
# · Remove if: merge-fallback заменяется другой идемпотентной стратегией
@ldd_trajectory
def test_install_acme_merge_fallback_preserves_ecc(caplog, tmp_path) -> None:
    """D4: непустой ACME_HOME + *_ecc → merge-fallback без перезаписи (cp -rn семантика)."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    (acme_home / "tronyx.ru_ecc").mkdir(parents=True)
    cert_cer = acme_home / "tronyx.ru_ecc" / "tronyx.ru.cer"
    cert_cer.write_text("existing-cert-data\n", encoding="utf-8")
    runner = FakeGitRunner()

    ok = install_acme.install_acme(str(acme_home), runner=runner)

    assert ok is True
    assert (acme_home / "tronyx.ru_ecc").is_dir(), "*_ecc dir must survive merge-fallback"
    assert cert_cer.read_text(encoding="utf-8") == "existing-cert-data\n", "cert data must NOT be overwritten"
    assert (acme_home / "acme.sh").exists(), "fresh clone acme.sh must be merged"
    assert any("merged with fresh clone" in r.message for r in caplog.records), (
        "WARN «merged with fresh clone» отсутствует"
    )
    # clone-tmp удалён после merge
    assert not (tmp_path / "acme.sh.clone-tmp").exists(), "clone-tmp должен быть удалён"
    logger.critical("[IMP:9][test] install_acme merge-fallback — *_ecc сохранены (D4)")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · полный провал clone → False (исходный вход: clone-catastrophe)
# · Scenario: clone в ACME_HOME fail + clone-tmp fail → install_acme False (exit 1)
# · Last fail: N/A (W3.5-1) · Remove if: clone-обработка меняется
@ldd_trajectory
def test_install_acme_total_clone_failure(caplog, tmp_path) -> None:
    """Оба clone провалились → False (fail-fast, exit 1 канон)."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    acme_home.mkdir(parents=True, exist_ok=True)
    (acme_home / "leftover_ecc").mkdir()  # непустой каталог → первый clone FAIL (128)
    runner = FakeGitRunner(clone_tmp_fail=True)

    ok = install_acme.install_acme(str(acme_home), runner=runner)

    assert ok is False
    logger.critical("[IMP:9][test] install_acme total clone failure → False")


# 🧪 TRAP[TEST] · Regression · dnsapi_ext clone провал — non-fatal WARN
# · Scenario: dnsapi_ext clone fail → WARN, install_acme всё равно True (контракт install-acme)
# · Last fail: N/A (W3.5-1) · Remove if: dnsapi_ext становится обязательным
@ldd_trajectory
def test_install_acme_dnsapi_ext_failure_non_fatal(caplog, tmp_path) -> None:
    """dnsapi_ext clone провал → WARN + True (webnames TLS не работает, install не блокируется)."""
    caplog.set_level(logging.INFO)
    acme_home = tmp_path / "acme.sh"
    acme_home.mkdir(parents=True, exist_ok=True)
    runner = FakeGitRunner(dnsapi_ext_fail=True)

    ok = install_acme.install_acme(str(acme_home), runner=runner)

    assert ok is True
    assert any("webnames TLS will not work" in r.message for r in caplog.records), "WARN dnsapi_ext отсутствует"
    logger.critical("[IMP:9][test] install_acme dnsapi_ext failure non-fatal (WARN, exit True)")


# endregion
