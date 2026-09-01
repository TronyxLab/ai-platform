# GREP_SUMMARY: test-ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes, canonical-verbs, unknown-verb
# STRUCTURE: ┌direct calls (no mock/no FS)┐ → ○ test scenarios: strip → classify → parse → CLI → unknown
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/ssh_command_parser.py
##           Pure string-parsing tests — no filesystem, no subprocess.
##           DevPlan 116 B1 T1 (D2): exact-match семантика, unknown → ConfigValidationError,
##           platform-deploy/platform-deliver кейсы удалены.
##           W2 T2.1 (DevPlan 160): канон классификации — поглотил classify/parse-сценарии
##           test_shared_verbs.py (classify_all_canonical, unknown deploy-format/frobnicate,
##           receive-project-only, bare-status); verbs.py-словарь → test_verbs.py,
##           verb-reserve validate_project_name → test_project_registry.py.
## @scope    Tests: _strip_prefixes, classify_verb, parse_ssh_command, CLI entry point.
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful scenario
##   - R5: negative-тесты для unknown verb (не deploy-фолбэк)
## @rationale  New shared module requires test coverage to prevent regressions
##             when the forced-command dispatcher uses this parser.
## @changes 2026-08-01 | DevPlan 116 B1 T1 — platform-deploy/platform-deliver кейсы удалены,
##                     unknown → ConfigValidationError, receive <project> [<sha>]
##          2026-08-12 | DevPlan 160 W2 T2.1 — MERGE сценариев test_shared_verbs.py (canon parser)
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import sys
from unittest.mock import patch

import pytest

from core.internal.deploy.ssh_command_parser import (
    _strip_prefixes,
    classify_verb,
    parse_ssh_command,
)
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── _strip_prefixes tests ─────────────────────────────────────────────────────


# region FUNC_test_deploy_sh_path_not_stripped
## @purpose — R5-negative (164 W3-1): deploy.sh-путь БОЛЬШЕ не стрипится — deploy.sh удалён,
##            dispatch-канал получает чистый verb; команда уходит в unknown verb.
# 🧪 TRAP[TEST] · 2026-08-13 · 164 W3-1 · deploy.sh удалён — path-strip ветки нет
# · Last fail: — strip удалял префикс /opt/.../deploy.sh (вход только через deploy.sh)
# · Remove if: deploy.sh-канал сознательно возвращается (запрещено — dispatch единственный канал)
def test_deploy_sh_path_not_stripped() -> None:
    """Path-префикс deploy.sh НЕ стрипится (R5 negative, 164 W3-1)."""
    raw = "/opt/platform/core/entrypoints/deploy.sh receive proj sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == raw.strip(), "deploy.sh-путь не должен стрипиться (deploy.sh удалён)"
    with pytest.raises(ConfigValidationError):
        classify_verb(cleaned)


# endregion FUNC_test_deploy_sh_path_not_stripped


# region FUNC_test_strip_old_platform_deploy_kept
## @purpose — "platform-deploy " НЕ стрипится (D2, DevPlan 116 B1) — префикс удалён
##            из стриппера; команда остаётся как есть (уходит в unknown verb). R5-negative.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: platform-deploy не стрипится
# · Last fail: — strip удалял префикс и уходил в deploy
# · Remove if: префиксы сознательно возвращаются (запрещено D2)
def test_strip_old_platform_deploy_kept() -> None:
    """platform-deploy prefix НЕ стрипится (D2)."""
    raw = "platform-deploy project sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "platform-deploy project sha"


# endregion FUNC_test_strip_old_platform_deploy_kept


# region FUNC_test_strip_bare_platform_deploy_kept
## @purpose — Bare "platform-deploy" (no args) НЕ стрипится (D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: bare platform-deploy не стрипится
# · Last fail: — bare platform-deploy становился пустой строкой
# · Remove if: префиксы сознательно возвращаются
def test_strip_bare_platform_deploy_kept() -> None:
    """Bare platform-deploy НЕ стрипится (остаётся как есть)."""
    raw = "platform-deploy"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "platform-deploy"


# endregion FUNC_test_strip_bare_platform_deploy_kept


# region FUNC_test_strip_plain_variants
## @purpose — Plain strip behavior (whitespace trim, no-prefix passthrough, empty input)
##            consolidated into one parametrized test (F5-reduction). R5-негативы
##            (deploy.sh / platform-deploy prefixes) живут отдельно ниже.
# 🧪 TRAP[TEST] · Regression · Scenario: trailing whitespace trimmed after stripping
# · Last fail: leading whitespace caused startswith miss (fixed in test input)
# · Remove if: _strip_prefixes trims input before prefix checks
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("   status myproj   ", "status myproj"),  # trailing/leading whitespace trimmed
        ("  status myproj  ", "status myproj"),  # no known prefix — pass through trimmed
        ("", ""),  # empty input stays empty
    ],
)
def test_strip_plain_variants(raw: str, expected: str) -> None:
    """_strip_prefixes: whitespace trim / no-prefix passthrough / empty input."""
    assert _strip_prefixes(raw) == expected


# endregion FUNC_test_strip_plain_variants


# ── classify_verb tests ───────────────────────────────────────────────────────


# region FUNC_test_classify_verb_variants
## @purpose — Verb-карта: каждый канонический verb в голой/prefix-форме классифицируется.
##            Консолидировано из 6 отдельных тестов (F5-reduction). Полнота словаря
##            дополнительно гарантирована test_classify_all_canonical (все CANONICAL_VERBS).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 голый status (НЕ проект)
# · Last fail: — голый status уходил в deploy
# · Remove if: classify_verb голый-verb семантика меняется
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ping", "ping"),
        ("exit", "exit"),
        ("remove project1", "remove"),
        ("status project1", "status"),
        ("health project1", "health"),
        ("verify node1", "verify"),
        ("rollback project1", "rollback"),
        ("status", "status"),  # bare verb → verb, НЕ проект (U-56)
    ],
)
def test_classify_verb_variants(raw: str, expected: str) -> None:
    """classify_verb: канонические verbs в голой и prefix-форме."""
    assert classify_verb(raw) == expected


# endregion FUNC_test_classify_verb_variants


# region FUNC_test_classify_unknown
## @purpose — Unknown input → ConfigValidationError (D2: дефолт-фолбэк удалён).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: unknown → error
# · Last fail: — "deploy" фолбэк для любого unrecognized input
# · Remove if: classify_verb unknown-семантика меняется
def test_classify_unknown() -> None:
    """Unknown commands raise ConfigValidationError (никакого deploy-фолбэка)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("platform-deploy project sha")
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("platform-deliver org project")
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("project sha")


# endregion FUNC_test_classify_unknown


# region FUNC_test_classify_all_canonical
## @purpose — MERGE (W2 T2.1): каждый CANONICAL_VERBS в голой и prefix-форме классифицируется
##            корректно (перенесено из tests/unit/test_shared_verbs.py — полнота словаря).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · полнота словаря (merged W2)
# · Scenario: for verb in CANONICAL_VERBS: bare + f"{verb} arg"
# · Last fail: N/A (new test)
# · Remove if: verb-множество меняется
def test_classify_all_canonical() -> None:
    """Все 8 verb'ов классифицируются и в голой, и в prefix-форме."""
    from core.internal.shared.verbs import CANONICAL_VERBS

    for verb in CANONICAL_VERBS:
        assert classify_verb(verb) == verb
        assert classify_verb(f"{verb} arg") == verb


# endregion FUNC_test_classify_all_canonical


# region FUNC_test_classify_unknown_deploy_format
## @purpose — MERGE (W2 T2.1): формат `deploy proj sha` → ConfigValidationError
##            (перенесено из test_shared_verbs.py — R5 negative, D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: deploy → error
# · Regression: `deploy <project> <sha> [env]` молча деплоит (тихий фолбэк)
# · Scenario: classify_verb("deploy proj sha") raises ConfigValidationError
# · Last fail: — дефолт-фолбэк возвращал "deploy"
# · Remove if: формат сознательно возвращается (запрещено D2)
def test_classify_unknown_deploy_format() -> None:
    """'deploy proj sha' → ConfigValidationError (D2, R5 negative)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("deploy proj sha")


# endregion FUNC_test_classify_unknown_deploy_format


# region FUNC_test_classify_unknown_frobnicate
## @purpose — MERGE (W2 T2.1): произвольный unknown verb → ConfigValidationError
##            (перенесено из test_shared_verbs.py).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · unknown → error (merged W2)
# · Scenario: 'frobnicate x' raises ConfigValidationError
# · Remove if: unknown-семантика меняется
def test_classify_unknown_frobnicate() -> None:
    """'frobnicate x' → ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("frobnicate x")


# endregion FUNC_test_classify_unknown_frobnicate


# ── parse_ssh_command tests ───────────────────────────────────────────────────


# region FUNC_test_parse_receive
## @purpose — parse_ssh_command with receive command produces correct dict
##            and verifies IMP:9 log.
# 🧪 TRAP[TEST] · Regression · Scenario: receive command → verb=receive
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command return format changes
def test_parse_receive(caplog: pytest.LogCaptureFixture) -> None:
    """Receive command parses with verb='receive'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("receive my-project abc123")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "receive"
    assert result["args"] == "my-project abc123"
    assert result["raw"] == "receive my-project abc123"
    assert result["cleaned"] == "receive my-project abc123"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_receive


# region FUNC_test_parse_ping
## @purpose — "ping" command → verb="ping", args=None, and IMP:9 logged.
# 🧪 TRAP[TEST] · Regression · Scenario: ping → verb=ping, args=None, IMP:9
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command ping handling changes
def test_parse_ping(caplog: pytest.LogCaptureFixture) -> None:
    """Ping command parses with verb='ping' and args=None."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("ping")

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "ping"
    assert result["args"] is None
    assert result["cleaned"] == "ping"

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_ping


# region FUNC_test_parse_exit
## @purpose — "exit" command → verb="exit", args=None.
# 🧪 TRAP[TEST] · Regression · Scenario: exit → verb=exit, args=None
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command exit handling changes
def test_parse_exit() -> None:
    """Exit command parses with verb='exit'."""
    result = parse_ssh_command("exit")
    assert result["verb"] == "exit"
    assert result["args"] is None
    assert result["cleaned"] == "exit"


# endregion FUNC_test_parse_exit


# region FUNC_test_parse_remove
## @purpose — "remove project1" → verb="remove", args="project1".
# 🧪 TRAP[TEST] · Regression · Scenario: remove → verb=remove, args=project
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command remove handling changes
def test_parse_remove() -> None:
    """Remove command extracts args."""
    result = parse_ssh_command("remove my-project")
    assert result["verb"] == "remove"
    assert result["args"] == "my-project"


# endregion FUNC_test_parse_remove


# region FUNC_test_parse_status
## @purpose — "status project1" → verb="status", args="project1".
# 🧪 TRAP[TEST] · Regression · Scenario: status → verb=status, args=project
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command status handling changes
def test_parse_status() -> None:
    """Status command extracts args."""
    result = parse_ssh_command("status my-project")
    assert result["verb"] == "status"
    assert result["args"] == "my-project"


# endregion FUNC_test_parse_status


# region FUNC_test_parse_rollback
## @purpose — "rollback project1 [snapshot-id]" → verb="rollback", args="project1 [snapshot-id]"
##            (snapshot опционален — дефолт latest в handler'е, D8 launch-validation).
# 🧪 TRAP[TEST] · 2026-09-01 · D8 launch-validation · rollback verb (dispatch forced-command)
# · Scenario: rollback my-project → verb=rollback, args="my-project";
# ·   rollback my-project snap-123 → args="my-project snap-123" (snapshot вторым токеном)
# · Last fail: — rollback НЕ распознавался dispatch (unknown verb exit 4)
# · Remove if: parse_ssh_command rollback handling меняется
def test_parse_rollback() -> None:
    """Rollback command extracts args (project [snapshot-id])."""
    result = parse_ssh_command("rollback my-project")
    assert result["verb"] == "rollback"
    assert result["args"] == "my-project"

    with_snapshot = parse_ssh_command("rollback my-project snap-123")
    assert with_snapshot["verb"] == "rollback"
    assert with_snapshot["args"] == "my-project snap-123"


# endregion FUNC_test_parse_rollback


# region FUNC_test_parse_health
## @purpose — "health project1 [service]" → verb="health", args="project1 [service]"
##            (service опционален — дефолт = project в handler'е, B3 fix-forward).
# 🧪 TRAP[TEST] · Regression · B3 fix-forward — health verb (read-only docker inspect)
# · Scenario: health my-project → verb=health, args="my-project";
# ·   health my-project web → args="my-project web" (service вторым токеном)
# · Last fail: N/A (new verb)
# · Remove if: parse_ssh_command health handling меняется
def test_parse_health() -> None:
    """Health command extracts args (project [service])."""
    result = parse_ssh_command("health my-project")
    assert result["verb"] == "health"
    assert result["args"] == "my-project"

    with_service = parse_ssh_command("health my-project web")
    assert with_service["verb"] == "health"
    assert with_service["args"] == "my-project web"


# endregion FUNC_test_parse_health


# region FUNC_test_parse_verify
## @purpose — "verify node1" → verb="verify", args="node1".
# 🧪 TRAP[TEST] · Regression · Scenario: verify → verb=verify, args=node
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command verify handling changes
def test_parse_verify() -> None:
    """Verify command extracts args."""
    result = parse_ssh_command("verify node1")
    assert result["verb"] == "verify"
    assert result["args"] == "node1"


# endregion FUNC_test_parse_verify


# region FUNC_test_parse_unknown_raises
## @purpose — Unknown verb (включая platform-deploy/platform-deliver) → ConfigValidationError.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: unknown → error через parse
# · Last fail: — дефолт-фолбэк deploy
# · Remove if: unknown-семантика меняется
def test_parse_unknown_raises() -> None:
    """Unknown verb (platform-deploy/platform-deliver/bare) raises ConfigValidationError."""
    for raw in (
        "platform-deploy my-project abc123",
        "platform-deliver org project",
        "deploy my-project abc123",
        "my-project abc123 production",
    ):
        with pytest.raises(ConfigValidationError, match="unknown verb"):
            parse_ssh_command(raw)


# endregion FUNC_test_parse_unknown_raises


# region FUNC_test_parse_empty_raw_raises
## @purpose — Empty raw input → ConfigValidationError with correct message.
# 🧪 TRAP[TEST] · Regression · Scenario: empty raw → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_empty_raw_raises() -> None:
    """Empty raw input raises ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="empty command after stripping"):
        parse_ssh_command("")


# endregion FUNC_test_parse_empty_raw_raises


# region FUNC_test_parse_none_raises
## @purpose — Whitespace-only input raises ConfigValidationError.
# 🧪 TRAP[TEST] · Regression · Scenario: whitespace-only → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_none_raises() -> None:
    """Empty string (whitespace) raises ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="empty command after stripping"):
        parse_ssh_command("   ")


# endregion FUNC_test_parse_none_raises


# region FUNC_test_parse_preserves_raw
## @purpose — raw-поле в result всегда равно входу. Покрыто test_parse_receive (assert raw) —
##            дубль удалён (F5-reduction).
# endregion FUNC_test_parse_preserves_raw


# region FUNC_test_parse_receive_project_only
## @purpose — MERGE (W2 T2.1): "receive proj" → verb receive, args="proj" — локальные вызовы
##            без sha (перенесено из test_shared_verbs.py).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · receive с одним токеном (merged W2)
# · Scenario: parse_ssh_command("receive proj") → args='proj'
# · Remove if: receive-формат меняется
def test_parse_receive_project_only(caplog: pytest.LogCaptureFixture) -> None:
    """receive proj → verb=receive, args='proj' (без sha — локальные вызовы)."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("receive proj")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "receive"
    assert result["args"] == "proj"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_receive_project_only


# region FUNC_test_parse_bare_status
## @purpose — MERGE (W2 T2.1): голый `status` → verb status, args=None (U-56: verb, НЕ проект;
##            перенесено из test_shared_verbs.py).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 negative: status как SSH_ORIGINAL_COMMAND → verb
# · Regression: голый `status` трактовался как имя проекта → deploy
# · Scenario: parse_ssh_command("status") → verb=status, args=None
# · Last fail: — голый status уходил в deploy
# · Remove if: классификация голых verb'ов меняется
def test_parse_bare_status(caplog: pytest.LogCaptureFixture) -> None:
    """Голый 'status' → verb=status, args=None (verb, НЕ проект — U-56)."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("status")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
    logger.info("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "status"
    assert result["args"] is None
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_bare_status


# ── CLI tests ─────────────────────────────────────────────────────────────────


# region FUNC_test_cli_parse
## @purpose — CLI parse mode outputs JSON to stdout (argv-канон main(argv), W4a).
# 🧪 TRAP[TEST] · Regression · Scenario: CLI parse mode
# · Last fail: N/A (new test)
# · Remove if: CLI interface or main changes
def test_cli_parse() -> None:
    """CLI parse mode outputs JSON and returns exit code 0."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "parse", "receive my-project sha"]
    with patch("sys.stderr"):
        rc = main(test_args[1:])
    assert rc == 0, f"main should return 0 for valid parse, got {rc}"


# endregion FUNC_test_cli_parse


# region FUNC_test_cli_classify
## @purpose — CLI classify mode outputs bare verb string to stdout.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI classify mode
# · Last fail: N/A (new test)
# · Remove if: CLI interface or main changes
def test_cli_classify() -> None:
    """CLI classify mode prints verb string."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "classify", "remove my-project"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        main(test_args[1:])

    assert stdout_lines == ["remove"], f"Expected ['remove'], got {stdout_lines}"


# endregion FUNC_test_cli_classify


# region FUNC_test_cli_parse_json_output
## @purpose — CLI parse mode produces valid JSON with expected fields.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI parse JSON output
# · Last fail: N/A (new test)
# · Remove if: CLI parse JSON format changes
def test_cli_parse_json_output() -> None:
    """CLI parse mode produces valid JSON."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "parse", "ping"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        main(test_args[1:])

    assert len(stdout_lines) == 1
    output = json.loads(stdout_lines[0])
    assert output["verb"] == "ping"
    assert output["args"] is None
    assert output["raw"] == "ping"
    assert output["cleaned"] == "ping"


# endregion FUNC_test_cli_parse_json_output


# region FUNC_test_cli_no_args
## @purpose — CLI with no arguments prints usage and exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI no args → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI argument parsing changes
def test_cli_no_args() -> None:
    """CLI with no arguments exits with code 1."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py"]
    captured_stderr: list[str] = []

    def fake_stderr_write(msg: str) -> int:
        captured_stderr.append(msg)
        return len(msg)

    with (
        patch.object(sys, "stderr") as mock_stderr,
    ):
        mock_stderr.write = fake_stderr_write  # type: ignore[method-assign]
        assert main(test_args[1:]) == 1
    assert any("Usage" in line for line in captured_stderr)


# endregion FUNC_test_cli_no_args


# region FUNC_test_cli_invalid_input_exit_one
## @purpose — CLI с неизвестным mode/--format значением → exit 1 (консолидировано, F5-reduction).
# 🧪 TRAP[TEST] · Regression · Scenario: CLI unknown mode → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI mode dispatch changes
@pytest.mark.parametrize(
    "cli_args",
    [
        ["unknown", "arg"],  # unknown mode
        ["--format", "xml", "parse", "ping"],  # unknown --format value
    ],
)
def test_cli_invalid_input_exit_one(cli_args: list[str]) -> None:
    """CLI с unknown mode/--format значением exits with code 1."""
    from core.internal.deploy.ssh_command_parser import main

    assert main(cli_args) == 1


# endregion FUNC_test_cli_invalid_input_exit_one


# region FUNC_test_cli_parse_format_lines
## @purpose — CLI parse mode with --format lines outputs verb/args/cleaned on separate lines.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines produces line-by-line output
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines() -> None:
    """CLI --format lines parse outputs verb/args/cleaned on separate lines."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = [
        "ssh_command_parser.py",
        "--format",
        "lines",
        "parse",
        "receive my-project abc123",
    ]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        main(test_args[1:])

    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "receive"
    assert stdout_lines[1] == "my-project abc123"
    assert stdout_lines[2] == "receive my-project abc123"


# endregion FUNC_test_cli_parse_format_lines


# region FUNC_test_cli_parse_format_lines_ping
## @purpose --format lines parse "ping" — verb=ping, args empty string, cleaned=ping.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines ping command
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines_ping() -> None:
    """CLI --format lines parse ping — args is empty string."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "--format", "lines", "parse", "ping"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        main(test_args[1:])

    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "ping"
    assert not stdout_lines[1]
    assert stdout_lines[2] == "ping"


# endregion FUNC_test_cli_parse_format_lines_ping


# region FUNC_test_cli_parse_format_lines_empty
## @purpose --format lines parse empty command — exits with code 4 (ConfigValidationError.exit_code).
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines parse empty → exit 4
# · Last fail: N/A (new test)
# · Remove if: --format lines error handling changes
def test_cli_parse_format_lines_empty() -> None:
    """CLI --format lines parse empty command exits 4."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "--format", "lines", "parse", ""]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch("builtins.print", fake_print),
    ):
        assert main(test_args[1:]) == 4  # ConfigValidationError.exit_code
    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "error"
    assert "empty command" in stdout_lines[1]


# endregion FUNC_test_cli_parse_format_lines_empty


# region FUNC_test_cli_format_lines_unknown_format
## @purpose — --format unknown → exit 1. Консолидирован в test_cli_invalid_input_exit_one (F5).
# endregion FUNC_test_cli_format_lines_unknown_format


# region FUNC_test_cli_parse_unknown_verb
## @purpose — CLI parse mode on unknown verb exits with code 4 and JSON error (D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 CLI negative: unknown verb → exit 4 + JSON
# · Last fail: — CLI молча возвращал deploy
# · Remove if: CLI unknown-verb handling changes
def test_cli_parse_unknown_verb() -> None:
    """CLI parse with unknown verb exits 4 with JSON error."""
    from core.internal.deploy.ssh_command_parser import main

    test_args = ["ssh_command_parser.py", "parse", "deploy my-project abc123"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch("builtins.print", fake_print),
    ):
        assert main(test_args[1:]) == 4  # ConfigValidationError.exit_code
    assert len(stdout_lines) == 1
    err = json.loads(stdout_lines[0])
    assert "error" in err
    assert "unknown verb" in err["error"]


# endregion FUNC_test_cli_parse_unknown_verb
