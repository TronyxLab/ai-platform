#!/usr/bin/env python3
# GREP_SUMMARY: gate sudoers-hardening no-docker-compose-run no-docker-exec no-rsync NOPASSWD setup-node audit-jsonl platform-module-deny T10.1 T10.9 W10
# STRUCTURE: ▶ parse setup-node.sh heredoc → ◇ assert NO docker compose/exec/rsync NOPASSWD → ◇ assert keep (nginx/node-lifecycle) → ◇ audit.jsonl sync → ◇ sudoers_generator make-only → ⊕ violations → ⎋ assert 0
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 136 W10 T10.14, G-AC10): sudoers-шаблон БЕЗ опасных паттернов PRIVESC.
##           Проверяет: (1) heredoc setup-node.sh — НЕТ `docker compose *`/`docker exec *`/`rsync *`
##           NOPASSWD (S-1/S-2/S-3 — root-escape через docker run/exec, root-запись через rsync);
##           (2) позитив: nginx systemctl + node-lifecycle.sh остаются (легитимные операционные права);
##           (3) audit-путь в sudoers синхронизирован с shared/audit_logger.DEFAULT_LOG_FILE
##           (audit.jsonl, НЕ audit.log — T10.9/S-11); (4) sudoers_generator.py (sudo-whitelist.template)
##           генерирует ТОЛЬКО `make -C` правила (не docker/rsync wildcard).
## @scope    Статический код-скан (нет Docker/субпроцессов): парсит setup-node.sh heredoc и
##           sudoers_generator.py _MAKE_BIN. Документирует осознанное сужение W10 T10.1
##           (решение на основе верификации на test-VPS: доставка core — rsync user=root без sudo,
##           docker через docker-group, φ2 phases/system.py).
## @invariants
##   - Allowlist опасных паттернов ПУСТ: docker compose run/exec NOPASSWD, docker exec, rsync * — RED
##   - `make -C` правила (sudoers_generator) — безопасная форма (модульные Makefile root-owned)
##   - audit.jsonl — единственный audit-файл (D1); упоминание audit.log в sudoers — RED
## @rationale CRITICAL-цепочки S-1/S-2/S-3 закрыты сужением шаблона (W10 T10.1) — gate фиксирует
##            суженный шаблон как инвариант: любой возврат docker/rsync NOPASSWD = красный gate.
## @changes 2026-08-05 · DevPlan 136 W10 T10.14 — Created
## @links   core/internal/bootstrap/setup-node.sh, core/internal/bootstrap/deploy/sudoers_generator.py,
##          core/internal/shared/audit_logger.py (DEFAULT_LOG_FILE)
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_SETUP_NODE = repo_root() / "core" / "internal" / "bootstrap" / "setup-node.sh"
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


def _extract_setup_node_heredoc() -> str:
    """Извлечь содержимое sudoers heredoc из setup-node.sh (cat > "$tmp_sudoers" <<EOF ... EOF)."""
    content = _SETUP_NODE.read_text(encoding="utf-8")
    m = re.search(r'cat > "\$tmp_sudoers" <<EOF\n(.*?)\nEOF', content, re.DOTALL)
    assert m, f"setup-node.sh: sudoers heredoc (<<EOF ... EOF) not found in {_SETUP_NODE}"
    return m.group(1)


def _extract_sudoers_generator_rules() -> str:
    """Собрать текстовое представление правил sudoers_generator (make-only форма)."""
    return _SUDOERS_GENERATOR.read_text(encoding="utf-8")


@pytest.mark.gate
def test_no_dangerous_privesc_patterns_in_setup_node(caplog: pytest.LogCaptureFixture) -> None:
    """T10.1: sudoers heredoc НЕ содержит docker compose/exec, rsync *, docker run, audit.log."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.1 (S-1/S-2/S-3) — sudoers PRIVESC
    # · Scenario: вернуть `docker compose *`/`docker exec *`/`rsync *` NOPASSWD в setup-node.sh
    # · Last fail: 2026-08-05 — W10 верификация на test-VPS подтвердила PRIVESC (все три удалены)
    # · Remove if: sudoers сужение отменено через TRAP[DECISION] (Rev-условие 2026-10-21)
    caplog.set_level(logging.INFO)

    heredoc = _extract_setup_node_heredoc()
    violations: list[str] = []
    for pattern, reason in _DANGEROUS_PATTERNS:
        # Проверяем только в некомментарных строках (комментарии-объяснения разрешены)
        for lineno, line in enumerate(heredoc.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern in stripped:
                violations.append(f"setup-node.sh heredoc L{lineno}: {pattern} — {reason}")
                logger.info("[IMP:9][sudoers-gate] DANGEROUS: %s", violations[-1])

    logger.info("[IMP:9][sudoers-gate] PRIVESC-паттернов в setup-node.sh heredoc: %d", len(violations))

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
    logger.info("[IMP:9][sudoers-gate] PASS: sudoers heredoc clean (docker/rsync NOPASSWD отсутствуют)")


@pytest.mark.gate
def test_legitimate_ops_entries_present(caplog: pytest.LogCaptureFixture) -> None:
    """T10.1: легитимные операционные sudoers-записи остаются (nginx systemctl + node-lifecycle.sh)."""
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.1 — легитимные права не выпилены вместе с PRIVESC
    # · Scenario: удалить nginx/node-lifecycle sudoers — конверг/операции platform сломаются
    # · Last fail: N/A (позитив-контракт)
    # · Remove if: операционная модель platform user изменена
    caplog.set_level(logging.INFO)

    heredoc = _extract_setup_node_heredoc()
    required = (
        "/bin/systemctl reload nginx",
        "/usr/sbin/nginx -t",
        "node-lifecycle.sh",
        "ci-deploy ALL=(root) NOPASSWD: /bin/systemctl reload nginx",
    )
    missing = [req for req in required if req not in heredoc]
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
    heredoc = _extract_setup_node_heredoc()
    # sudoers cat-путь должен указывать на ЕДИНЫЙ audit-файл (audit.jsonl), не audit.log
    audit_cat_lines = [ln for ln in heredoc.splitlines() if "/var/log/platform/audit" in ln]
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
