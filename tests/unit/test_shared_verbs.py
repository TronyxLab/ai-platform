#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-verbs, test-ssh-command-parser, classify-verb, unknown-verb, verb-reserve, project-name, U-56
# STRUCTURE: ▶ 10 scenarios ┌classify (bare+prefix) + unknown + reserve + strip┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T1 — verb-словарь shared/verbs.py + ssh_command_parser
##           exact-match семантика (D2): голый `status` → verb status; unknown verb →
##           ConfigValidationError; platform-deploy strip УДАЛЁН; project «status» невалиден (U-56).
## @scope    Tests public API only: classify_verb(), parse_ssh_command(), _strip_prefixes(),
##           verbs.is_verb(), project_registry.validate_project_name().
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful parse_ssh_command scenario
##   - R5 anti-survivorship: negative-тесты для unknown verb и проекта «status»
## @rationale  DevPlan 116 B1 T1 acceptance: голый status классифицируется как verb; проект
##             «status» невалиден; unknown verb → ошибка (не deploy); `platform-deploy foo`
##             НЕ стрипится (команда остаётся как есть и уходит в unknown).
## @changes    2026-08-01 | Created (DevPlan 116 B1 T1)
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.deploy.ssh_command_parser import _strip_prefixes, classify_verb, parse_ssh_command
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.verbs import CANONICAL_VERBS, VERB_RESERVE, is_verb

# ── LDD helper ─────────────────────────────────────────────────────────────────


def _assert_imp9_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log present."""
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
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# ── verbs.py — словарь и is_verb ──────────────────────────────────────────────


# region FUNC_test_canonical_verbs_set
## @purpose — CANONICAL_VERBS содержит ровно 6 verb'ов (D1); platform-deliver/platform-deploy отсутствуют.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D1 закрытое verb-множество
# · Regression: возврат platform-deliver в словарь (легegacy-канал жив)
# · Scenario: точное сравнение с ожидаемым кортежем
# · Last fail: N/A (new test)
# · Remove if: verb-множество расширяется архитектурным решением
def test_canonical_verbs_set() -> None:
    """CANONICAL_VERBS — закрытое множество из 6 verb'ов (D1)."""
    assert CANONICAL_VERBS == ("ping", "exit", "status", "verify", "remove", "receive")
    assert "platform-deliver" not in CANONICAL_VERBS
    assert "platform-deploy" not in CANONICAL_VERBS
    assert frozenset(CANONICAL_VERBS) == VERB_RESERVE


# endregion FUNC_test_canonical_verbs_set


# region FUNC_test_is_verb
## @purpose — is_verb() exact-match predicate.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 reserve-предикат
# · Regression: нечёткий матч (prefix/подстрока) резервирует лишние имена
# · Scenario: verb → True, не-verb → False, не-str → False
# · Last fail: N/A (new test)
# · Remove if: is_verb удалён из verbs.py
def test_is_verb() -> None:
    """is_verb exact-match: status → True; myproj/None → False."""
    assert is_verb("status") is True
    assert is_verb("receive") is True
    assert is_verb("statusx") is False  # не prefix-match — exact только
    assert is_verb("myproj") is False
    assert is_verb(None) is False
    assert is_verb("") is False


# endregion FUNC_test_is_verb


# ── classify_verb — exact-match, голые verb'ы (U-56) ───────────────────────────


# region FUNC_test_classify_bare_status
## @purpose — голый `status` (без пробела) → verb status. Раньше уходил в deploy (U-56).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 голый status = verb
# · Regression: голый `status` классифицируется как deploy (legacy баг)
# · Scenario: classify_verb("status") == "status"
# · Last fail: legacy — голый status уходил в deploy-фолбэк
# · Remove if: verb-множество изменится
def test_classify_bare_status() -> None:
    """Голый 'status' → verb status (U-56, НЕ deploy)."""
    assert classify_verb("status") == "status"


# endregion FUNC_test_classify_bare_status


# region FUNC_test_classify_status_prefix
## @purpose — "status myproj" → verb status (prefix-match).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · prefix-match status
# · Scenario: 'status myproj' → 'status'
# · Remove if: classify_verb prefix-логика меняется
def test_classify_status_prefix() -> None:
    """'status myproj' → status."""
    assert classify_verb("status myproj") == "status"


# endregion FUNC_test_classify_status_prefix


# region FUNC_test_classify_receive
## @purpose — "receive proj abc123" → verb receive.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · receive классификация
# · Scenario: 'receive proj abc123' → 'receive'
# · Remove if: receive verb удаляется
def test_classify_receive() -> None:
    """'receive proj abc123' → receive."""
    assert classify_verb("receive proj abc123") == "receive"


# endregion FUNC_test_classify_receive


# region FUNC_test_classify_ping
## @purpose — голый `ping` → verb ping (живой потребитель — vps_readiness CMD_PING).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · ping
# · Scenario: classify_verb("ping") == "ping"
# · Remove if: ping verb удаляется
def test_classify_ping() -> None:
    """Голый 'ping' → ping."""
    assert classify_verb("ping") == "ping"


# endregion FUNC_test_classify_ping


# region FUNC_test_classify_all_canonical
## @purpose — каждый CANONICAL_VERBS в голой и prefix-форме классифицируется корректно.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · полнота словаря
# · Scenario: for verb in CANONICAL_VERBS: bare + f"{verb} arg"
# · Remove if: verb-множество меняется
def test_classify_all_canonical() -> None:
    """Все 6 verb'ов классифицируются и в голой, и в prefix-форме."""
    for verb in CANONICAL_VERBS:
        assert classify_verb(verb) == verb
        assert classify_verb(f"{verb} arg") == verb


# endregion FUNC_test_classify_all_canonical


# ── classify_verb — unknown → ConfigValidationError (D2, negative) ─────────────


# region FUNC_test_classify_unknown_deploy_format
## @purpose — legacy-формат `deploy proj sha` → ConfigValidationError (D2: фолбэк удалён).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: legacy deploy → error
# · Regression: legacy `deploy <project> <sha> [env]` молча деплоит (тихий фолбэк)
# · Scenario: classify_verb("deploy proj sha") raises ConfigValidationError
# · Last fail: legacy — дефолт-фолбэк возвращал "deploy"
# · Remove if: legacy-формат сознательно возвращается (запрещено D2)
def test_classify_unknown_deploy_format() -> None:
    """Legacy 'deploy proj sha' → ConfigValidationError (D2, R5 negative)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("deploy proj sha")


# endregion FUNC_test_classify_unknown_deploy_format


# region FUNC_test_classify_unknown_frobnicate
## @purpose — произвольный unknown verb → ConfigValidationError.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · unknown → error
# · Scenario: 'frobnicate x' raises ConfigValidationError
# · Remove if: unknown-семантика меняется
def test_classify_unknown_frobnicate() -> None:
    """'frobnicate x' → ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("frobnicate x")


# endregion FUNC_test_classify_unknown_frobnicate


# ── parse_ssh_command — receive <project> [<sha>] (D5) ─────────────────────────


# region FUNC_test_parse_receive_with_sha
## @purpose — "receive proj abc123" → verb receive, args="proj abc123" (два токена, D5).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D5 версия через аргументы
# · Scenario: parse_ssh_command("receive proj abc123") → verb=receive, args='proj abc123'
# · Last fail: legacy — версия читалась из ai-platform.yaml (phantom-поля)
# · Remove if: receive-формат аргументов меняется
def test_parse_receive_with_sha(caplog: pytest.LogCaptureFixture) -> None:
    """receive proj abc123 → verb=receive, args='proj abc123' (version через аргументы)."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("receive proj abc123")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "receive"
    assert result["args"] == "proj abc123"
    assert result["cleaned"] == "receive proj abc123"


# endregion FUNC_test_parse_receive_with_sha


# region FUNC_test_parse_receive_project_only
## @purpose — "receive proj" → verb receive, args="proj" (без sha — локальные вызовы).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · receive с одним токеном
# · Scenario: parse_ssh_command("receive proj") → args='proj'
# · Remove if: receive-формат меняется
def test_parse_receive_project_only(caplog: pytest.LogCaptureFixture) -> None:
    """receive proj → verb=receive, args='proj'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("receive proj")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "receive"
    assert result["args"] == "proj"


# endregion FUNC_test_parse_receive_project_only


# region FUNC_test_parse_bare_status
## @purpose — голый `status` → verb status, args=None (U-56: verb, НЕ проект).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 negative: status как SSH_ORIGINAL_COMMAND → verb
# · Regression: голый `status` трактовался как имя проекта → deploy
# · Scenario: parse_ssh_command("status") → verb=status, args=None
# · Last fail: legacy — голый status уходил в deploy
# · Remove if: классификация голых verb'ов меняется
def test_parse_bare_status(caplog: pytest.LogCaptureFixture) -> None:
    """Голый 'status' → verb=status, args=None (verb, НЕ проект — U-56)."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("status")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "status"
    assert result["args"] is None


# endregion FUNC_test_parse_bare_status


# region FUNC_test_parse_status_project
## @purpose — "status myproj" → verb status, args="myproj".
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · status с аргументом
# · Scenario: parse_ssh_command("status myproj") → args='myproj'
# · Remove if: status-формат меняется
def test_parse_status_project(caplog: pytest.LogCaptureFixture) -> None:
    """status myproj → verb=status, args='myproj'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("status myproj")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "status"
    assert result["args"] == "myproj"


# endregion FUNC_test_parse_status_project


# region FUNC_test_parse_verify_node
## @purpose — "verify node1" → verb verify, args="node1".
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · verify с node
# · Scenario: parse_ssh_command("verify node1") → args='node1'
# · Remove if: verify-формат меняется
def test_parse_verify_node(caplog: pytest.LogCaptureFixture) -> None:
    """verify node1 → verb=verify, args='node1'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("verify node1")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "verify"
    assert result["args"] == "node1"


# endregion FUNC_test_parse_verify_node


# ── _strip_prefixes — platform-deploy strip УДАЛЁН (D2) ───────────────────────


# region FUNC_test_strip_platform_deploy_not_stripped
## @purpose — `platform-deploy foo` НЕ стрипится (D2): strip удалён — команда остаётся как есть
##            и уходит в unknown verb. R5-negative для T1.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: platform-deploy не префикс
# · Regression: legacy strip оживляет platform-deploy канал
# · Scenario: _strip_prefixes("platform-deploy foo") == "platform-deploy foo" (НЕ стрипится)
# · Last fail: legacy — strip удалял префикс и уходил в deploy
# · Remove if: legacy-префиксы сознательно возвращаются (запрещено D2)
def test_strip_platform_deploy_not_stripped() -> None:
    """'platform-deploy foo' НЕ стрипится — остаётся как есть (уходит в unknown)."""
    assert _strip_prefixes("platform-deploy foo") == "platform-deploy foo"


# endregion FUNC_test_strip_platform_deploy_not_stripped


# region FUNC_test_strip_platform_deploy_parse_unknown
## @purpose — parse_ssh_command("platform-deploy foo") → ConfigValidationError (unknown verb, D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · platform-deploy → unknown error
# · Scenario: parse_ssh_command("platform-deploy foo") raises ConfigValidationError
# · Remove if: unknown-семантика меняется
def test_strip_platform_deploy_parse_unknown() -> None:
    """'platform-deploy foo' → ConfigValidationError (unknown verb после strip-удаления)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        parse_ssh_command("platform-deploy foo")


# endregion FUNC_test_strip_platform_deploy_parse_unknown


# region FUNC_test_strip_path_prefix_kept
## @purpose — path-префикс (deploy.sh) по-прежнему стрипится (T1 шаг 1: оставить path-prefix strip).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · path-prefix сохраняется
# · Scenario: _strip_prefixes("/opt/platform/core/entrypoints/deploy.sh status myproj") == "status myproj"
# · Remove if: path-prefix strip удаляется
def test_strip_path_prefix_kept() -> None:
    """Path-префикс deploy.sh стрипится; остаётся verb-команда."""
    cleaned = _strip_prefixes("/opt/platform/core/entrypoints/deploy.sh status myproj")
    assert cleaned == "status myproj"


# endregion FUNC_test_strip_path_prefix_kept


# ── project_registry — verb-reserve (U-56, R5 negative) ────────────────────────


# region FUNC_test_validate_project_name_rejects_status
## @purpose — проект с именем `status` → validate_project_name False (U-56 verb-reserve).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 negative: проект «status» невалиден
# · Regression: проект «status» регистрируется → SSH_ORIGINAL_COMMAND «status» трактуется как проект
# · Scenario: validate_project_name("status") is False
# · Last fail: legacy — не было reserve-проверки
# · Remove if: reserve-список verb'ов меняется
def test_validate_project_name_rejects_status() -> None:
    """Проект «status» НЕвалиден (verb-reserve, U-56)."""
    assert validate_project_name("status") is False


# endregion FUNC_test_validate_project_name_rejects_status


# region FUNC_test_validate_project_name_rejects_all_verbs
## @purpose — каждый verb-именя из CANONICAL_VERBS отклоняется validate_project_name.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · reserve полнота
# · Scenario: for verb in CANONICAL_VERBS: validate_project_name(verb) is False
# · Remove if: reserve-список меняется
def test_validate_project_name_rejects_all_verbs() -> None:
    """Все verb-имена отклоняются validate_project_name (reserve полнота)."""
    for verb in CANONICAL_VERBS:
        assert validate_project_name(verb) is False, f"verb {verb!r} должен быть отклонён"


# endregion FUNC_test_validate_project_name_rejects_all_verbs


# region FUNC_test_validate_project_name_accepts_normal
## @purpose — обычное имя проекта по-прежнему валидно (reserve не пере-блокирует).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · reserve не блокирует нормальные имена
# · Scenario: validate_project_name("my-project") is True
# · Remove if: validate_project_name меняется
def test_validate_project_name_accepts_normal() -> None:
    """Обычное имя проекта валидно (reserve точечный)."""
    assert validate_project_name("my-project") is True
    assert validate_project_name("webapp1") is True


# endregion FUNC_test_validate_project_name_accepts_normal
