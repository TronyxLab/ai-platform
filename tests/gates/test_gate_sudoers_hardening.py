#!/usr/bin/env python3
# GREP_SUMMARY: gate sudoers-hardening no-docker-compose-run no-docker-exec no-rsync NOPASSWD setup-node audit-jsonl platform-module-deny T10.1 T10.9 W10 W3.5-1
# STRUCTURE: ▶ render sudoers (setup_node.render_sudoers — Python-модуль, DevPlan 164 W3.5-1) → ◇ assert NO docker compose/exec/rsync NOPASSWD → ◇ assert keep (nginx/node-lifecycle) → ◇ audit.jsonl sync → ◇ sudoers_generator make-only → ⊕ violations → ⎋ assert 0
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 136 W10 T10.14, G-AC10): sudoers-шаблон БЕЗ опасных паттернов PRIVESC.
##           Проверяет: (1) render_sudoers() из core/internal/bootstrap/setup_node.py — НЕТ
##           `docker compose *`/`docker exec *`/`rsync *` NOPASSWD (S-1/S-2/S-3 — root-escape через
##           docker run/exec, root-запись через rsync); (2) позитив: nginx systemctl + node-lifecycle.sh
##           остаются (легитимные операционные права); (3) audit-путь в sudoers синхронизирован
##           с shared/audit_logger.DEFAULT_LOG_FILE (audit.jsonl, НЕ audit.log — T10.9/S-11);
##           (4) sudoers_generator.py (sudo-whitelist.template) генерирует ТОЛЬКО `make -C` правила
##           (не docker/rsync wildcard).
## @scope    Статический код-скан (нет Docker/субпроцессов): рендерит sudoers-контент через
##           setup_node.render_sudoers (W3.5-1 — логика generate_sudoers мигрирована из heredoc
##           setup-node.sh в Python-модуль; гейт теперь парсит модуль, а не heredoc) и
##           сканирует sudoers_generator.py _MAKE_BIN. Документирует осознанное сужение W10 T10.1
##           (решение на основе верификации на test-VPS: доставка core — rsync user=root без sudo,
##           docker через docker-group, φ2 phases/system.py).
## @invariants
##   - Allowlist опасных паттернов ПУСТ: docker compose run/exec NOPASSWD, docker exec, rsync * — RED
##   - `make -C` правила (sudoers_generator) — безопасная форма (модульные Makefile root-owned)
##   - audit.jsonl — единственный audit-файл (D1); упоминание audit.log в sudoers — RED
##   - R5-негатив: детектор ОБЯЗАН ловить возврат запрещённого правила (test_..._negative)
## @rationale CRITICAL-цепочки S-1/S-2/S-3 закрыты сужением шаблона (W10 T10.1) — gate фиксирует
##            суженный шаблон как инвариант: любой возврат docker/rsync NOPASSWD = красный gate.
##            W3.5-1 (DevPlan 164): источник контента сменился с heredoc .sh на render_sudoers()
##            Python-модуля — проверяемые свойства (содержимое) идентичны 1:1.
## @changes 2026-08-05 · DevPlan 136 W10 T10.14 — Created
## @changes 2026-08-14 · DevPlan 164 W3.5-1 — источник контента: heredoc setup-node.sh →
##           setup_node.render_sudoers() (SH→Python); +R5 negative test
## @links   core/internal/bootstrap/setup_node.py (render_sudoers — SoT sudoers-контента),
##          core/internal/bootstrap/deploy/sudoers_generator.py,
##          core/internal/shared/audit_logger.py (DEFAULT_LOG_FILE)
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# W3.5-1: sudoers-контент живёт в Python-модуле setup_node.render_sudoers (SH→Python, DevPlan 164).
# Гейт рендерит контент напрямую (фиксированные параметры) — heredoc setup-node.sh больше не парсится.
from core.internal.bootstrap.setup_node import DEFAULT_PLATFORM_ROOT, render_sudoers

_SETUP_NODE = repo_root() / "core" / "internal" / "bootstrap" / "setup_node.py"
_SUDOERS_GENERATOR = repo_root() / "core" / "internal" / "bootstrap" / "deploy" / "sudoers_generator.py"
_AUDIT_LOGGER = repo_root() / "core" / "internal" / "shared" / "audit_logger.py"

# Опасные паттерны PRIVESC (S-1/S-2/S-3) — allowlist пуст
_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("docker compose", "docker compose NOPASSWD — root-escape через `docker compose run/exec` (S-1)"),
    ("docker exec", "docker exec NOPASSWD — root в контейнере (S-2)"),
    ("rsync *", "rsync * NOPASSWD — root-запись в любые файлы (S-3)"),
    ("docker run", "docker run NOPASSWD — root-escape (S-1)"),
    ("audit.log", "audit.log (мёртвый путь) — синхронизация с audit.jsonl (S-11, T10.9)"),
)

# Фиксированные параметры рендера (содержимое не зависит от runtime-значений — контент-инвариант)
_RENDER_NODE_NAME = "gate-test-node"
_RENDER_TIMESTAMP = "2026-08-14T00:00:00Z"


def _render_setup_node_sudoers() -> str:
    """Рендер sudoers-контента через setup_node.render_sudoers (W3.5-1 — Python-модуль).

    ## @purpose — Единственный источник sudoers-контента (1:1 с прежним heredoc setup-node.sh).
    ##            Гейт проверяет СВОЙСТВА контента (безопасность правил), не механизм генерации.
    """
    assert _SETUP_NODE.is_file(), f"setup_node.py not found: {_SETUP_NODE} (W3.5-1 миграция нарушена)"
    return render_sudoers(node_name=_RENDER_NODE_NAME, platform_root=DEFAULT_PLATFORM_ROOT, timestamp=_RENDER_TIMESTAMP)


def _scan_dangerous(content: str) -> list[str]:
    """Сканировать sudoers-контент на PRIVESC-паттерны (некомментарные строки).

    ## @purpose — Общий детектор: используется позитив-тестом (контент чист) и R5-негативом
    ##            (запрещённое правило ОБЯЗАНО быть поймано). Комментарии-объяснения
    ##            (docker compose сужение) не считаются правилами.
    """
    violations: list[str] = []
    for pattern, reason in _DANGEROUS_PATTERNS:
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern in stripped:
                violations.append(f"sudoers L{lineno}: {pattern} — {reason}")
                logger.info("[IMP:9][sudoers-gate] DANGEROUS: %s", violations[-1])
    return violations


def _extract_sudoers_generator_rules() -> str:
    """Собрать текстовое представление правил sudoers_generator (make-only форма)."""
    return _SUDOERS_GENERATOR.read_text(encoding="utf-8")


@pytest.mark.gate
def test_no_dangerous_privesc_patterns_in_setup_node(caplog: pytest.LogCaptureFixture) -> None:
    """T10.1: sudoers-контент (render_sudoers) НЕ содержит docker compose/exec, rsync *, docker run, audit.log."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.1 (S-1/S-2/S-3) — sudoers PRIVESC
    # · Scenario: вернуть `docker compose *`/`docker exec *`/`rsync *` NOPASSWD в render_sudoers()
    # · Last fail: 2026-08-05 — W10 верификация на test-VPS подтвердила PRIVESC (все три удалены);
    # ·   W3.5-1 (2026-08-14) — источник сменился на Python-модуль, свойства контента те же
    # · Remove if: sudoers сужение отменено через TRAP[DECISION] (Rev-условие 2026-10-21)
    caplog.set_level(logging.INFO)

    content = _render_setup_node_sudoers()
    violations = _scan_dangerous(content)
    logger.info("[IMP:9][sudoers-gate] PRIVESC-паттернов в setup_node.render_sudoers: %d", len(violations))

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert not violations, f"[IMP:9][sudoers-gate] FAIL: {len(violations)} PRIVESC-паттерн(ов): {'; '.join(violations)}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][sudoers-gate] PASS: render_sudoers чист (docker/rsync NOPASSWD отсутствуют)")


@pytest.mark.gate
def test_legitimate_ops_entries_present(caplog: pytest.LogCaptureFixture) -> None:
    """T10.1: легитимные операционные sudoers-записи остаются (node-lifecycle.sh + diagnostics)."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.1 — легитимные права не выпилены вместе с PRIVESC
    # · Scenario: удалить node-lifecycle sudoers — конверг/операции platform сломаются
    # · Last fail: N/A (позитив-контракт)
    # · Remove if: операционная модель platform user изменена
    # · 2026-08-11 · DevPlan 145 W3 D-136-W10: nginx systemctl entries удалены из required —
    # ·   обе ноды Docker (nginx в контейнере, systemctl unit not found на test-VPS).
    # ·   Required-список сужен до node-lifecycle.sh + diagnostics (ufw/cat audit/ss/iptables).
    caplog.set_level(logging.INFO)

    content = _render_setup_node_sudoers()
    required = (
        "node-lifecycle.sh",
        "/usr/sbin/ufw status verbose",
        "/var/log/platform/audit.jsonl",
        "/usr/sbin/ss -tlnp",
    )
    missing = [req for req in required if req not in content]
    logger.info("[IMP:9][sudoers-gate] Отсутствующие легитимные записи: %s", missing)

    found_imp9 = False
    for record in caplog.records:
        if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 9:
            found_imp9 = True
            print(record.message)
    assert not missing, f"[IMP:9][sudoers-gate] FAIL: missing legitimate entries: {missing}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][sudoers-gate] PASS: легитимные операционные записи присутствуют")


@pytest.mark.gate
def test_audit_path_synced_with_jsonl(caplog: pytest.LogCaptureFixture) -> None:
    """T10.9 (S-11): sudoers cat-путь синхронизирован с audit.jsonl (DEFAULT_LOG_FILE audit_logger)."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.9 (S-11) — audit.log мёртв, путь расходился
    # · Scenario: sudoers содержит /var/log/platform/audit.log → диагностика platform слепа к audit.jsonl
    # · Last fail: 2026-08-05 — W10: setup-node.sh имел audit.log, audit_logger пишет audit.jsonl
    # · Remove if: audit-файл переименован (тогда синхронизировать и тут, и в audit_logger)
    caplog.set_level(logging.INFO)

    audit_logger_src = _AUDIT_LOGGER.read_text(encoding="utf-8")
    m = re.search(r'DEFAULT_LOG_FILE = "([^"]+)"', audit_logger_src)
    assert m, "audit_logger.DEFAULT_LOG_FILE not found"
    default_log_file = m.group(1)
    content = _render_setup_node_sudoers()
    # sudoers cat-путь должен указывать на ЕДИНЫЙ audit-файл (audit.jsonl), не audit.log
    audit_cat_lines = [ln for ln in content.splitlines() if "/var/log/platform/audit" in ln]
    logger.info("[IMP:9][sudoers-gate] audit cat-строки в sudoers: %s", audit_cat_lines)
    logger.info("[IMP:9][sudoers-gate] audit_logger.DEFAULT_LOG_FILE: %s", default_log_file)

    assert audit_cat_lines, "sudoers: нет диагностической cat-строки на audit-файл"
    for ln in audit_cat_lines:
        assert "audit.jsonl" in ln and "audit.log" not in ln, (
            f"sudoers audit-путь рассинхронизирован: '{ln}' (ожидается {default_log_file}, T10.9)"
        )
        assert default_log_file in ln, f"sudoers cat-путь '{ln}' != DEFAULT_LOG_FILE '{default_log_file}'"

    found_imp9 = any(
        "[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 9 for r in caplog.records
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][sudoers-gate] PASS: sudoers audit-путь = audit.jsonl (синхронизирован)")


@pytest.mark.gate
def test_sudoers_generator_make_only(caplog: pytest.LogCaptureFixture) -> None:
    """T10.14: sudoers_generator (sudo-whitelist.template) генерирует ТОЛЬКО make -C правила."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.14 — генератор не должен эмитить docker/rsync
    # · Scenario: sudo-whitelist.template получил docker/rsync строку → парсер эмитит PRIVESC-правило
    # · Last fail: N/A (preventive — W10 gate)
    # · Remove if: sudoers_generator заменён
    caplog.set_level(logging.INFO)

    src = _extract_sudoers_generator_rules()
    # _MAKE_BIN = /usr/bin/make — единственный разрешённый бинарь в генерируемых правилах
    assert '"/usr/bin/make"' in src, "sudoers_generator._MAKE_BIN != /usr/bin/make — контракт сломан"
    # Правила строятся только через f"{username} ALL=(root) NOPASSWD: {_MAKE_BIN} -C ..."
    assert "NOPASSWD:" in src and "_MAKE_BIN} -C" in src, "make -C rule template not found"
    logger.info("[IMP:9][sudoers-gate] sudoers_generator: make-only шаблон подтверждён (_MAKE_BIN=/usr/bin/make)")

    template = repo_root() / "core" / "templates" / "sudo-whitelist.template"
    tpl = template.read_text(encoding="utf-8")
    for pattern in ("docker", "rsync", "docker compose", "docker exec"):
        non_comment = [ln for ln in tpl.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        hits = [ln for ln in non_comment if pattern in ln]
        assert not hits, f"sudo-whitelist.template: PRIVESC-паттерн '{pattern}' в некомментарной строке: {hits}"
    logger.info("[IMP:9][sudoers-gate] sudo-whitelist.template: PRIVESC-паттернов в некомментарных строках: 0")

    found_imp9 = any(
        "[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 9 for r in caplog.records
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][sudoers-gate] PASS: sudoers_generator эмитит только make -C правила")


@pytest.mark.gate
def test_dangerous_privesc_detected_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative (T10.1): возврат запрещённого docker compose NOPASSWD правила → детектируется."""
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 136 W10 T10.1 (S-1) — детектор обязан ловить регрессию
    # · Scenario: в render_sudoers() вернулась строка `platform ALL=(root) NOPASSWD: docker compose run`
    # ·   (root-escape S-1) — исходный вход, поймавший баг W10
    # · Last fail: 2026-08-05 — W10: docker compose run/exec NOPASSWD присутствовали в sudoers
    # ·   (PRIVESC, удалены по результатам верификации test-VPS)
    # · Remove if: детектор заменён на другой механизм (allowlist-подход, не сканер)
    caplog.set_level(logging.INFO)

    clean = _render_setup_node_sudoers()
    # Точный вход, поймавший оригинальный баг (S-1): docker compose NOPASSWD в некомментарной строке
    regressed = clean + "\nplatform ALL=(root) NOPASSWD: /usr/bin/docker compose run --rm shell\n"

    violations = _scan_dangerous(regressed)
    logger.info("[IMP:9][sudoers-gate][negative] Детектировано PRIVESC-нарушений: %d", len(violations))

    found_imp9 = any(
        "[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 9 for r in caplog.records
    )
    assert violations, "R5 FAIL: детектор не поймал возврат запрещённого docker compose NOPASSWD (S-1)"
    assert any("docker compose" in v for v in violations), "R5 FAIL: детектор сработал не на том паттерне"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][sudoers-gate][negative] PASS: возврат docker compose NOPASSWD детектирован (R5)")
