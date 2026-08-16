"""
# GREP_SUMMARY: test-setup-node sudoers render-sudoers visudo os.replace atomic validate-node-name PRIVESC S-9 T10.7 W3.5-1
# STRUCTURE: ▶ FakeRunner (visudo rc) + FakeFacts (is_root) + tmp_path → ◇ validate_node_name (valid/injection) → ◇ render_sudoers (rules/PRIVESC) → ◇ install (visudo-fail → no file / atomic-idempotent) → ◇ main (root-guard/env) → ⊕ LDD IMP:9 → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit-тесты setup_node.py (DevPlan 164 W3.5-1, SH→Python setup-node.sh): чистая
##           генерация render_sudoers (правила NOPASSWD, PRIVESC-отсутствие), валидация NODE_NAME
##           (S-9/T10.7 инъекция пути), оркестрация SudoersInstaller (visudo-fail → файл НЕ записан,
##           atomic-mv идемпотентность, temp-cleanup), main() (root-guard, NODE_NAME env).
## @scope    tests/unit/test_setup_node.py — native imports (core.internal.bootstrap.setup_node),
##           tmp_path для файловых операций, FakeRunner/FakeFacts DI (ноль monkeypatch, W4c/W4b).
## @invariants
##   - Никаких реальных subprocess/visudo — FakeRunner (CommandRunner protocol)
##   - sudoers_dir/tmp_dir → tmp_path (Zero Hardcode Rule)
##   - Каждая тест-функция: # 🧪 TRAP[TEST] + LDD IMP:9 траектория (@ldd_trajectory)
##   - Содержимое render_sudoers проверяется на 1:1-свойства heredoc (правила/сужение)
## @rationale  W3.5-1: бизнес-логика generate_sudoers переехала в Python — unit-тесты без Docker,
##            subprocess-каналы (visudo) инъектируемы, файловые операции — tmp_path.
## @changes 2026-08-14 · DevPlan 164 W3.5-1 — Created
## @links   core/internal/bootstrap/setup_node.py, tests/gates/test_gate_sudoers_hardening.py
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.setup_node import (
    DEFAULT_PLATFORM_ROOT,
    SudoersError,
    SudoersInstaller,
    render_sudoers,
    resolve_node_name,
    resolve_platform_root,
    utc_timestamp,
    validate_node_name,
)
from core.internal.bootstrap.setup_node import main as setup_node_main
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

_FIXED_TIMESTAMP = "2026-08-14T00:00:00Z"


# ── Fakes (CommandRunner / EnvironmentFacts DI, W4c/W4b паттерн) ──


class FakeRunner:
    """CommandRunner fake: фиксированный visudo rc + запись вызовов (0 monkeypatch)."""

    def __init__(self, visudo_rc: int = 0) -> None:
        self.visudo_rc = visudo_rc
        self.calls: list[list[str]] = []

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int | None = None,  # ruff: ignore[ARG002]
        check: bool = False,  # ruff: ignore[ARG002]
        non_fatal: bool = False,  # ruff: ignore[ARG002]
        fatal_rc: tuple[int, ...] = (),  # ruff: ignore[ARG002]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        rc = self.visudo_rc if cmd and cmd[0] == "visudo" else 0
        err = "" if rc == 0 else "visudo: /tmp/platform-sudoers-XXXXXX: syntax error near 'NOPASSWD'"
        return subprocess.CompletedProcess(list(cmd), rc, "", err)


class FakeFacts:
    """EnvironmentFacts fake: is_root DI (W4b — root-guard тестируем без root)."""

    def __init__(self, *, is_root_result: bool = True) -> None:
        self._is_root = is_root_result

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:  # ruff: ignore[ARG002] — protocol conformance
        return None

    def path_isfile(self, path: str | os.PathLike[str]) -> bool:
        return Path(path).is_file()


# ═══════════════════════════════════════════════════════════════════════
# validate_node_name (S-9 / T10.7)
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_validate_node_name_accepts_valid(caplog) -> None:
    """S-9: допустимые имена нод (буквы/цифры/-/_) принимаются."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 T10.7 (S-9) — канон имени ноды
    # · Scenario: валидация отвергает легитимное имя (node-01) → sudoers не генерируется
    # · Last fail: N/A (позитив-контракт S-9)
    # · Remove if: канон имени ноды изменён (расширен регекс)
    assert validate_node_name("node-01")
    assert validate_node_name("node_02")
    assert validate_node_name("testnode")
    logger.critical("[IMP:9][test][validate] Допустимые имена приняты: node-01, node_02, testnode")


@ldd_trajectory
def test_validate_node_name_rejects_path_injection(caplog) -> None:
    """S-9 negative: инъекция пути в NODE_NAME отвергается (запись в произвольный sudoers.d)."""
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 136 T10.7 (S-9) — инъекция пути
    # · Scenario: NODE_NAME='../etc' → /etc/sudoers.d/platform-../etc — запись вне каталога
    # · Last fail: 2026-08-05 — W10: имя ноды НЕ валидировалось до генерации фрагмента
    # · Remove if: sudoers-файл перестанет строиться из имени ноды (иной механизм именования)
    rejected = ("../etc/sudoers", "node name", "node/name", "node.name", "node:name", "", "..", "node;rm")
    for name in rejected:
        assert not validate_node_name(name), f"S-9 FAIL: инъекция '{name}' не отвергнута"
    logger.critical("[IMP:9][test][validate] Инъекции пути отвергнуты: %d вариантов", len(rejected))


# ═══════════════════════════════════════════════════════════════════════
# render_sudoers (контент-инвариант, 1:1 heredoc)
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_render_sudoers_platform_rules_present(caplog) -> None:
    """T10.1 позитив: платформенные NOPASSWD-правила (node-lifecycle + диагностика) присутствуют."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.1 — keep-правила не выпилены
    # · Scenario: удалить node-lifecycle.sh NOPASSWD → операционные права platform сломаны
    # · Last fail: N/A (позитив-контракт; DevPlan 145 W3: nginx entries удалены by design)
    # · Remove if: операционная модель platform user изменена
    content = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, _FIXED_TIMESTAMP)
    required = (
        f"{DEFAULT_PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh",
        "/usr/sbin/ufw status verbose",
        "/usr/bin/cat /var/log/platform/audit.jsonl",
        "/usr/sbin/ss -tlnp",
        "/usr/sbin/iptables -t nat -L -n",
    )
    for rule in required:
        assert rule in content, f"Отсутствует платформенное NOPASSWD-правило: {rule}"
    assert "# core sudoers — node-a" in content, "node_name не интерполирован"
    assert _FIXED_TIMESTAMP in content, "timestamp не интерполирован"
    logger.critical("[IMP:9][test][render] Все %d платформенных NOPASSWD-правил присутствуют", len(required))


# GUARD-PRESERVE (168): security-guard S-1/S-2/S-3 + R5-негатив — сужение NOPASSWD (PRIVESC-паттерны запрещены)
@ldd_trajectory
def test_render_sudoers_no_privesc_rules(caplog) -> None:
    """T10.1 negative: PRIVESC-паттерны (docker compose/exec/run, rsync *, audit.log) — только комментарии."""
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 136 T10.1 (S-1/S-2/S-3) — sudoers сужение
    # · Scenario: в render_sudoers вернулось NOPASSWD-правило docker compose → root-escape S-1
    # · Last fail: 2026-08-05 — W10: docker compose run/exec/rsync NOPASSWD присутствовали
    # · Remove if: сужение отменено (TRAP[DECISION] Rev 2026-10-21)
    content = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, _FIXED_TIMESTAMP)
    dangerous = ("docker compose", "docker exec", "docker run", "rsync *", "audit.log")
    for pattern in dangerous:
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert pattern not in stripped, f"S-1/S-2/S-3 FAIL: '{pattern}' в некомментарной строке L{lineno}"
    logger.critical("[IMP:9][test][render] PRIVESC-паттернов в некомментарных строках: 0")


@ldd_trajectory
def test_render_sudoers_trailing_newline_and_audit_path(caplog) -> None:
    """1:1 heredoc: контент заканчивается \n; audit-путь = audit.jsonl (T10.9)."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 T10.9 (S-11) — audit-путь синхронизирован
    # · Scenario: cat-путь указывает на audit.log → диагностика слепа к audit.jsonl
    # · Last fail: 2026-08-05 — W10: setup-node.sh имел audit.log
    # · Remove if: audit-файл переименован
    content = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, _FIXED_TIMESTAMP)
    assert content.endswith("\n"), "Контент должен заканчиваться \n (heredoc <<EOF parity)"
    audit_lines = [ln for ln in content.splitlines() if "/var/log/platform/audit" in ln]
    assert audit_lines, "Нет cat-строки на audit-файл"
    for ln in audit_lines:
        assert "audit.jsonl" in ln and "audit.log" not in ln
    logger.critical("[IMP:9][test][render] audit-путь = audit.jsonl (T10.9), trailing \\n подтверждён")


# ═══════════════════════════════════════════════════════════════════════
# SudoersInstaller (visudo → atomic os.replace)
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_install_visudo_fail_leaves_no_file(tmp_path, caplog) -> None:
    """SC5: visudo -c FAIL → sudoers НЕ записан, temp удалён, exit 1 (lockout-safe)."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 SC5 — visudo guard
    # · Scenario: битый sudoers-синтаксис → mv без валидации = root lockout на ноде
    # · Last fail: N/A (контракт; shell-версия: `! visudo -c` → rm temp → exit 1)
    # · Remove if: visudo-валидация заменена иным механизмом
    runner = FakeRunner(visudo_rc=1)
    rc = setup_node_main(
        argv=None,
        env={"NODE_NAME": "node-a"},
        facts=FakeFacts(is_root_result=True),
        runner=runner,
        tmp_dir=str(tmp_path),
        sudoers_dir=str(tmp_path),
    )
    assert rc == 1, "visudo-fail должен давать exit 1"
    assert not (tmp_path / "platform-node-a").exists(), "Целевой sudoers НЕ должен быть записан (SC5)"
    leftovers = list(tmp_path.glob("platform-sudoers-*"))
    assert leftovers == [], f"Temp-файлы не очищены: {leftovers}"
    assert runner.calls and runner.calls[0][0] == "visudo", "visudo -c должен быть вызван"
    logger.critical("[IMP:9][test][install] visudo-fail → файл не записан, temp очищен, exit 1")


@ldd_trajectory
def test_install_atomic_mv_idempotent(tmp_path, caplog) -> None:
    """SC5/идемпотентность: повторный install → контент идентичен, mode 0440, temp чист."""
    # 🧪 TRAP[TEST] · REGRESSION · N/A (позитив) — atomic mv + идемпотентность
    # · Scenario: повторный bootstrap перезаписывает sudoers частично → битый файл
    # · Last fail: N/A (контракт; os.replace atomic — нет окна частичной записи)
    # · Remove if: механизм записи заменён (не os.replace)
    runner = FakeRunner(visudo_rc=0)
    installer = SudoersInstaller(runner=runner, tmp_dir=str(tmp_path), sudoers_dir=str(tmp_path))
    installer.install("node-a", platform_root=DEFAULT_PLATFORM_ROOT, timestamp=_FIXED_TIMESTAMP)
    first_content = (tmp_path / "platform-node-a").read_text(encoding="utf-8")
    first_mode = (tmp_path / "platform-node-a").stat().st_mode & 0o777

    installer.install("node-a", platform_root=DEFAULT_PLATFORM_ROOT, timestamp=_FIXED_TIMESTAMP)
    second_content = (tmp_path / "platform-node-a").read_text(encoding="utf-8")

    assert first_content == second_content, "Повторный install должен давать идентичный контент"
    assert first_content == render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, _FIXED_TIMESTAMP)
    assert first_mode == 0o440, f"sudoers mode должен быть 0440, got {oct(first_mode)}"
    assert len(runner.calls) == 2, "Две visudo-валидации на два install"
    assert list(tmp_path.glob("platform-sudoers-*")) == [], "Temp-файлы должны быть удалены"
    logger.critical("[IMP:9][test][install] atomic-mv идемпотентен: контент идентичен, mode 0440")


@ldd_trajectory
def test_install_invalid_name_raises_without_file(tmp_path, caplog) -> None:
    """S-9: инъекция имени → SudoersError ДО создания файла (запись в произвольный sudoers.d)."""
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 136 T10.7 (S-9) — валидация ДО генерации
    # · Scenario: NODE_NAME='../etc' → install создаёт /etc/sudoers.d/platform-../etc
    # · Last fail: 2026-08-05 — W10: имя НЕ валидировалось до записи фрагмента
    # · Remove if: sudoers-файл не строится из имени ноды
    installer = SudoersInstaller(runner=FakeRunner(visudo_rc=0), tmp_dir=str(tmp_path), sudoers_dir=str(tmp_path))
    with pytest.raises(SudoersError, match="S-9"):
        installer.install("../etc", platform_root=DEFAULT_PLATFORM_ROOT, timestamp=_FIXED_TIMESTAMP)
    assert list(tmp_path.iterdir()) == [], "Никаких файлов при invalid name (S-9)"
    logger.critical("[IMP:9][test][install] Инъекция имени → SudoersError, файлов не создано")


# ═══════════════════════════════════════════════════════════════════════
# main() — root-guard, NODE_NAME env, exit-контракт
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_main_requires_root(tmp_path, caplog) -> None:
    """Root-guard: euid != 0 → exit 1 БЕЗ генерации (shell `id -u` parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · shell `id -u` guard
    # · Scenario: setup-node.sh запущен не-root → sudoers не должен генерироваться
    # · Last fail: N/A (контракт; W4b facts DI)
    # · Remove if: root-требование снято
    rc = setup_node_main(
        argv=None,
        env={"NODE_NAME": "node-a"},
        facts=FakeFacts(is_root_result=False),
        runner=FakeRunner(visudo_rc=0),
        tmp_dir=str(tmp_path),
        sudoers_dir=str(tmp_path),
    )
    assert rc == 1, "non-root должен давать exit 1"
    assert not (tmp_path / "platform-node-a").exists(), "non-root НЕ должен генерировать sudoers"
    logger.critical("[IMP:9][test][main] root-guard: non-root → exit 1, файл не создан")


@ldd_trajectory
def test_main_success_with_env_node_name(tmp_path, caplog) -> None:
    """E2E-контур: NODE_NAME env → generate_sudoers → exit 0 + файл записан."""
    # 🧪 TRAP[TEST] · REGRESSION · N/A (полный сценарий φ3)
    # · Scenario: phase_platform_setup вызывает bash setup-node.sh (без аргументов) → NODE_NAME env
    # · Last fail: N/A (позитив; W3.5-1 полный контур)
    # · Remove if: вызов из phases/system.py изменён
    runner = FakeRunner(visudo_rc=0)
    rc = setup_node_main(
        argv=None,
        env={"NODE_NAME": "node-a"},
        facts=FakeFacts(is_root_result=True),
        runner=runner,
        tmp_dir=str(tmp_path),
        sudoers_dir=str(tmp_path),
    )
    assert rc == 0, "Успешная генерация должна давать exit 0"
    target = tmp_path / "platform-node-a"
    assert target.is_file(), "sudoers файл должен быть записан"
    content = target.read_text(encoding="utf-8")
    assert "NODE_NAME" not in content and "node-a" in content, "NODE_NAME env интерполирован в контент"
    logger.critical("[IMP:9][test][main] NODE_NAME env → exit 0, sudoers записан")


# GUARD-PRESERVE (168): единственное покрытие utc_timestamp (формат-контракт шапки sudoers, shell `date -u` parity)
@ldd_trajectory
def test_utc_timestamp_format(caplog) -> None:
    """utc_timestamp: формат shell `date -u '+%Y-%m-%dT%H:%M:%SZ'` parity."""
    # 🧪 TRAP[TEST] · REGRESSION · формат-контракт шапки sudoers
    # · Scenario: сломанный формат → шапка sudoers непарсится
    # · Last fail: N/A (чистый генератор)
    # · Remove if: шапка sudoers не содержит timestamp
    from datetime import datetime, timezone

    ts = utc_timestamp(datetime(2026, 8, 14, 12, 30, 45, tzinfo=timezone.utc))
    assert ts == "2026-08-14T12:30:45Z", f"Формат timestamp: {ts}"
    logger.critical("[IMP:9][test][timestamp] Формат timestamp подтверждён: %s", ts)


@ldd_trajectory
def test_resolve_platform_root_and_node_name(caplog) -> None:
    """Резолверы: PLATFORM_ROOT env | /opt/platform; NODE_NAME env | hostname."""
    # 🧪 TRAP[TEST] · REGRESSION · paths.sh parity
    # · Scenario: PLATFORM_ROOT unset → /opt/platform (shell `${PLATFORM_ROOT:-/opt/platform}`)
    # · Last fail: N/A (чистые резолверы)
    # · Remove if: PLATFORM_ROOT канон изменён
    assert resolve_platform_root({}) == "/opt/platform"
    assert resolve_platform_root({"PLATFORM_ROOT": "/custom/platform"}) == "/custom/platform"
    assert resolve_node_name({"NODE_NAME": "node-b"}) == "node-b"
    logger.critical("[IMP:9][test][resolve] PLATFORM_ROOT/NODE_NAME резолверы подтверждены")
