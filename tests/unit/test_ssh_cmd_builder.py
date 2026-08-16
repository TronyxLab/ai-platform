# GREP_SUMMARY: test ssh_cmd_builder printf_q printf-%q parity D3 build_ssh_cmd init update converge check-security deploy-context env-fallback R5 LDD
# STRUCTURE: ┌parity-батарея printf_q (D3, bash-verified)┐ → ◇ init build (exports+flags+passthrough) → ◇ update → ◇ converge → ◇ check-security → ◇ deploy-context → ◇ env fallback chain → ◇ CLI → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssh_cmd_builder.py (DevPlan 164 W3.5-1): bash printf %q
##           byte-parity (D3 invariant — НЕПРИКОСНОВЕННО), 5 build-функций (env exports, flags,
##           passthrough quoting), env fallback chain (PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY),
##           CLI контракт. R5-негативы: --age-secret-key НИКОГДА в remote-команде (env-only hardening);
##           env-ключ не эмитит CLI-флаг; printf_q ≠ shlex.quote (backslash, не single-quote).
## @scope    Native imports; tmp_path не нужен (чистые функции); caplog LDD (IMP:9 assert).
## @invariants
##   - printf_q() byte-parity с bash 5.x printf %q (C locale) — verified 2026-08-14 (bash 5.3.9)
##   - Вывод build-функций совпадает с shell build-ssh-cmd.sh посимвольно (прямое замещение)
##   - --age-secret-key отсутствует в remote-команде (AGE key — env export ONLY, ps aux hardening)
##   - CLI печатает в stdout ТОЛЬКО команду; usage-error → exit 2 + stderr
## @rationale D3 TRAP[DECISION] 2026-07-26: shlex.quote() ≠ printf '%q' — parity-тест фиксирует
##            backslash-формат и защищает от регрессии на single-quote-wrapping.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

import pytest
from _conftest.ldd import ldd_trajectory

from core.internal.shared.ssh_cmd_builder import (
    build_check_security_ssh_cmd,
    build_converge_ssh_cmd,
    build_deploy_context_ssh_cmd,
    build_ssh_cmd,
    build_update_ssh_cmd,
    cli,
    printf_q,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── Parity-батарея (ожидаемые значения — фактические выводы bash 5.3.9 printf %q, 2026-08-14) ──
_PRINTF_Q_CASES: list[tuple[str, str]] = [
    ("simple", "simple"),
    ("with space", "with\\ space"),
    ("a$b", "a\\$b"),
    ("it's", "it\\'s"),
    ('quote"double', 'quote\\"double'),
    ("back\\slash", "back\\\\slash"),
    ("star*", "star\\*"),
    ("quest?on", "quest\\?on"),
    ("br[ac]ket", "br\\[ac\\]ket"),
    ("par(en)th", "par\\(en\\)th"),
    ("semi;colon", "semi\\;colon"),
    ("amp&ersand", "amp\\&ersand"),
    ("pipe|char", "pipe\\|char"),
    ("lt<gt>", "lt\\<gt\\>"),
    ("bt`ick", "bt\\`ick"),
    ("hash#t", "hash\\#t"),
    ("tild~e", "tild~e"),
    ("at@sign", "at@sign"),
    ("pct%cent", "pct%cent"),
    ("plus+sign", "plus+sign"),
    ("eq=sign", "eq=sign"),
    ("colon:sign", "colon:sign"),
    ("comma,sign", "comma,sign"),
    ("dot.sign", "dot.sign"),
    ("slash/sign", "slash/sign"),
    ("dash-sign", "dash-sign"),
    ("under_score", "under_score"),
    ("", "''"),
    ("\n", "$'\\n'"),
    ("\t", "$'\\t'"),
    ("\r", "$'\\r'"),
    ("\a", "$'\\a'"),
    ("\b", "$'\\b'"),
    ("\f", "$'\\f'"),
    ("\v", "$'\\v'"),
    ("\x01", "$'\\001'"),
    ("\x1b", "$'\\033'"),
    ("\x7f", "$'\\177'"),
    ("unicode-ключ", "unicode-ключ"),
    ("áéí", "áéí"),
    ("a\tb", "a$'\\t'b"),
    ("a\nb", "a$'\\n'b"),
    ("lead \tmid", "lead\\ $'\\t'mid"),
    ("x\x01y", "x$'\\001'y"),
    ("a b$c", "a\\ b\\$c"),
    ("q'\"mix", "q\\'\\\"mix"),
    ("end\\", "end\\\\"),
]


# region FUNC_test_printf_q_parity_bash
# 🧪 TRAP[TEST] · Regression (D3) · printf_q byte-parity с bash printf %q
# · Scenario: 47 входов (safe/unsafe/control/unicode/пустая строка) → ожидаемый %q-вывод
# · Last fail: N/A (2026-08-14 verified против bash 5.3.9; D3 TRAP[DECISION] 2026-07-26: shlex ≠ %q)
# · Remove if: bash printf %q формат меняется ИЛИ D3-инвариант снят (утверждением Архитектора)
@ldd_trajectory
def test_printf_q_parity_bash(caplog: pytest.LogCaptureFixture) -> None:
    """printf_q() byte-parity с bash `printf %q` (D3) — батарея из 47 входов."""
    caplog.set_level(logging.DEBUG)
    failures: list[str] = []
    for value, expected in _PRINTF_Q_CASES:
        got = printf_q(value)
        if got != expected:
            failures.append(f"{value!r}: got {got!r}, expected {expected!r}")
        logger.info("[IMP:9][test][parity] printf_q(%r) = %r — %s", value, got, "OK" if got == expected else "MISMATCH")
    assert not failures, "printf_q parity FAIL:\n" + "\n".join(failures)


# endregion FUNC_test_printf_q_parity_bash


# region FUNC_test_printf_q_not_shlex_quote
# 🧪 TRAP[TEST] · NEGATIVE (R5/D3) · printf_q НЕ shlex.quote — backslash, не single-quote
# · Scenario: "a b" → `a\ b` (backslash); shlex.quote дал бы `'a b'` — смена формата = регрессия D3
# · Last fail: N/A (guard на TRAP[DECISION] 2026-07-26)
# · Remove if: D3-инвариант снят (printf %q → shlex.quote утверждено Архитектором)
@ldd_trajectory
def test_printf_q_not_shlex_quote(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: printf_q использует backslash-экранирование, НЕ single-quote-wrapping (D3)."""
    caplog.set_level(logging.DEBUG)
    got = printf_q("a b")
    logger.info("[IMP:9][test][d3] printf_q('a b') = %r", got)
    assert got == "a\\ b", f"D3 FAIL: expected backslash-form 'a\\ b', got {got!r}"
    assert got != "'a b'", "D3 FAIL: shlex.quote single-quote-wrapping запрещён (TRAP 2026-07-26)"


# endregion FUNC_test_printf_q_not_shlex_quote


# region FUNC_test_build_ssh_cmd_init_structure
# 🧪 TRAP[TEST] · Regression · init build: exports + флаги + порядок (parity с shell)
# · Scenario: build_ssh_cmd(node, owner, ci_deploy, age) → set -euo pipefail + AGE/PLATFORM_ROOT/
# ·   CI_DEPLOY_KEY exports + node-lifecycle.sh --mode init ... --owner-key ... --resume
# · Last fail: N/A (порт build-ssh-cmd.sh; 2026-08-14 byte-parity check)
# · Remove if: build_ssh_cmd сигнатура/формат меняется
@ldd_trajectory
def test_build_ssh_cmd_init_structure(caplog: pytest.LogCaptureFixture) -> None:
    """build_ssh_cmd() init: env exports + флаги + --resume (parity с shell)."""
    caplog.set_level(logging.DEBUG)
    cmd = build_ssh_cmd(
        "test-node",
        "ssh-ed25519 AAAATestKey test@example.com",
        "ssh-ed25519 AAAACiKey ci-deploy@test",
        "AGE-SECRET-KEY-12345",
    )
    logger.info("[IMP:9][test][init] cmd=%s", cmd)
    assert cmd.startswith("set -euo pipefail")
    assert "export AGE_SECRET_KEY=AGE-SECRET-KEY-12345" in cmd
    assert "export PLATFORM_ROOT=/opt/platform" in cmd
    assert "export PLATFORM_CI_DEPLOY_KEY=ssh-ed25519\\ AAAACiKey\\ ci-deploy@test" in cmd
    assert "/opt/platform/core/internal/bootstrap/node-lifecycle.sh" in cmd
    assert "--mode init" in cmd
    assert "--node-name test-node" in cmd
    assert "--node-yaml /opt/node-configs/test-node/node.yaml" in cmd
    assert "--owner-key ssh-ed25519\\ AAAATestKey\\ test@example.com" in cmd
    assert "--ci-deploy-key ssh-ed25519\\ AAAACiKey\\ ci-deploy@test" in cmd
    assert "--resume" in cmd
    assert "--ci-root-key" not in cmd


# endregion FUNC_test_build_ssh_cmd_init_structure


# region FUNC_test_build_ssh_cmd_age_key_env_only
# 🧪 TRAP[TEST] · NEGATIVE (R5) · AGE key env-only: --age-secret-key НИКОГДА в remote-команде
# · Scenario: DevPlan 003 TASK-2 — ключ уходит env export'ом, НЕ CLI-флагом (ps aux visibility)
# · Last fail: N/A (guard; shell build-ssh-cmd.sh тоже env-only)
# · Remove if: решение env-only снято (ключ разрешён в argv remote-команды)
@ldd_trajectory
def test_build_ssh_cmd_age_key_env_only(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: --age-secret-key отсутствует в init-команде (AGE key — env export ONLY)."""
    caplog.set_level(logging.DEBUG)
    cmd = build_ssh_cmd("n1", "owner-key", "", "AGE-SECRET-KEY-12345")
    logger.info("[IMP:9][test][age-key] cmd=%s", cmd)
    assert "--age-secret-key" not in cmd, "AGE key must not appear as CLI arg (ps aux hardening)"
    assert "export AGE_SECRET_KEY=AGE-SECRET-KEY-12345" in cmd


# endregion FUNC_test_build_ssh_cmd_age_key_env_only


# region FUNC_test_build_ssh_cmd_empty_ci_keys_omitted
# 🧪 TRAP[TEST] · Regression · пустые ci-ключи → нет флагов и нет env-экспортов
# · Scenario: ci_deploy_key="" ci_root_key="" → --ci-deploy-key/--ci-root-key/export отсутствуют
# · Last fail: N/A (parity с 4-аргументными вызовами)
# · Remove if: build_ssh_cmd ci-ключи перестают быть опциональными
@ldd_trajectory
def test_build_ssh_cmd_empty_ci_keys_omitted(caplog: pytest.LogCaptureFixture) -> None:
    """Пустые ci-ключи → флаги и env-экспорты отсутствуют (backward compat)."""
    caplog.set_level(logging.DEBUG)
    cmd = build_ssh_cmd("n1", "owner-key", "", "AGE-SECRET-KEY-12345", "")
    logger.info("[IMP:9][test][ci-keys] cmd=%s", cmd)
    assert "--ci-deploy-key" not in cmd
    assert "--ci-root-key" not in cmd
    assert "export PLATFORM_CI_DEPLOY_KEY=" not in cmd
    assert "export PLATFORM_CI_ROOT_KEY=" not in cmd
    assert "--owner-key" in cmd
    assert "--resume" in cmd


# endregion FUNC_test_build_ssh_cmd_empty_ci_keys_omitted


# region FUNC_test_build_ssh_cmd_env_fallback_ci_deploy_key
# 🧪 TRAP[TEST] · NEGATIVE (R5) · env PLATFORM_CI_DEPLOY_KEY → export, НО НЕ CLI-флаг
# · Scenario: TRAP[BUG] P2 2026-07-17 fallback chain — env-ключ экспортируется; флаг --ci-deploy-key
# ·   эмитится ТОЛЬКО от параметра (env-ключ не должен светиться в argv remote-команды)
# · Last fail: N/A (guard на поведение shell: flag от param, export от effective)
# · Remove if: fallback chain env→param меняется
@ldd_trajectory
def test_build_ssh_cmd_env_fallback_ci_deploy_key(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: env PLATFORM_CI_DEPLOY_KEY → export присутствует, CLI-флаг — НЕТ."""
    caplog.set_level(logging.DEBUG)
    env = {"PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 ENVKEY env@test"}
    cmd = build_ssh_cmd("n1", "owner-key", "", "age-key", environ=env)
    logger.info("[IMP:9][test][env-fallback] cmd=%s", cmd)
    assert "export PLATFORM_CI_DEPLOY_KEY=ssh-ed25519\\ ENVKEY\\ env@test" in cmd
    assert "--ci-deploy-key" not in cmd, "env-ключ не должен эмитить CLI-флаг (argv visibility)"
    # параметр НЕ задан → флаг отсутствует даже при non-empty env


# endregion FUNC_test_build_ssh_cmd_env_fallback_ci_deploy_key


# region FUNC_test_build_ssh_cmd_remote_base_override
# 🧪 TRAP[TEST] · Regression · PLATFORM_REMOTE_BASE переопределяет remote-базу (RC 121)
# · Scenario: PLATFORM_REMOTE_BASE=/custom → node-lifecycle.sh путь и PLATFORM_ROOT export = /custom
# · Last fail: N/A (RC 121: PLATFORM_ROOT исключён из remote-цепочки — base = PLATFORM_REMOTE_BASE)
# · Remove if: канон remote-базы меняется
@ldd_trajectory
def test_build_ssh_cmd_remote_base_override(caplog: pytest.LogCaptureFixture) -> None:
    """PLATFORM_REMOTE_BASE env → remote-пути и PLATFORM_ROOT export переопределяются."""
    caplog.set_level(logging.DEBUG)
    cmd = build_ssh_cmd("n1", "ok", "", "", environ={"PLATFORM_REMOTE_BASE": "/custom/base"})
    logger.info("[IMP:9][test][remote-base] cmd=%s", cmd)
    assert "export PLATFORM_ROOT=/custom/base" in cmd
    assert "/custom/base/core/internal/bootstrap/node-lifecycle.sh" in cmd


# endregion FUNC_test_build_ssh_cmd_remote_base_override


# region FUNC_test_build_update_ssh_cmd_structure
# 🧪 TRAP[TEST] · Regression · update build: без --owner-key/--resume/ci-флагов
# · Scenario: build_update_ssh_cmd(node, age) → node-lifecycle.sh --mode update --node-name --node-yaml
# · Last fail: N/A (parity с build_update_ssh_cmd)
# · Remove if: update build сигнатура меняется
@ldd_trajectory
def test_build_update_ssh_cmd_structure(caplog: pytest.LogCaptureFixture) -> None:
    """build_update_ssh_cmd(): update-команда без owner-key/resume/ci-флагов."""
    caplog.set_level(logging.DEBUG)
    cmd = build_update_ssh_cmd("n1", "age-key-1", ["--force"])
    logger.info("[IMP:9][test][update] cmd=%s", cmd)
    assert "export AGE_SECRET_KEY=age-key-1" in cmd
    assert "export PLATFORM_ROOT=/opt/platform" in cmd
    assert "--mode update" in cmd
    assert "--node-name n1" in cmd
    assert "--node-yaml /opt/node-configs/n1/node.yaml" in cmd
    assert "--owner-key" not in cmd
    assert "--resume" not in cmd
    assert "--force" in cmd  # passthrough


# endregion FUNC_test_build_update_ssh_cmd_structure


# region FUNC_test_build_converge_ssh_cmd_passthrough
# 🧪 TRAP[TEST] · Regression · converge build + reconcile passthrough
# · Scenario: build_converge_ssh_cmd(node, ["--reconcile"]) → converge.sh --node n1 --reconcile
# · Last fail: N/A (parity с прежним; execute_remote_reconcile добавляет "--reconcile" passthrough'ом)
# · Remove if: converge build сигнатура меняется
@ldd_trajectory
def test_build_converge_ssh_cmd_passthrough(caplog: pytest.LogCaptureFixture) -> None:
    """build_converge_ssh_cmd(): converge.sh --node + passthrough (--reconcile, --force)."""
    caplog.set_level(logging.DEBUG)
    cmd = build_converge_ssh_cmd("n1", ["--reconcile", "--force"])
    logger.info("[IMP:9][test][converge] cmd=%s", cmd)
    assert "export PLATFORM_ROOT=/opt/platform" in cmd
    assert "/opt/platform/core/internal/bootstrap/converge.sh" in cmd
    assert "--node n1" in cmd
    assert "--reconcile" in cmd
    assert "--force" in cmd
    assert "AGE_SECRET_KEY" not in cmd
    assert "PLATFORM_CI" not in cmd


# endregion FUNC_test_build_converge_ssh_cmd_passthrough


# region FUNC_test_build_check_security_ssh_cmd_pythonpath
# 🧪 TRAP[TEST] · Regression · check-security: PYTHONPATH export (TRAP[BUG] 2026-07-31)
# · Scenario: security_posture.py импортирует core.internal.* → PYTHONPATH=remote_root обязателен
# · Last fail: N/A (guard на канон TRAP[BUG] 2026-07-31 converge.sh:66)
# · Remove if: security_posture.py перестаёт импортировать core.internal.*
@ldd_trajectory
def test_build_check_security_ssh_cmd_pythonpath(caplog: pytest.LogCaptureFixture) -> None:
    """build_check_security_ssh_cmd(): python3 + PYTHONPATH export (канон TRAP[BUG] 2026-07-31)."""
    caplog.set_level(logging.DEBUG)
    cmd = build_check_security_ssh_cmd("n1")
    logger.info("[IMP:9][test][check-security] cmd=%s", cmd)
    assert "export PLATFORM_ROOT=/opt/platform" in cmd
    assert "export PYTHONPATH=/opt/platform" in cmd
    assert "python3 /opt/platform/core/internal/bootstrap/security_posture.py" in cmd
    assert "--node n1" in cmd


# endregion FUNC_test_build_check_security_ssh_cmd_pythonpath


# region FUNC_test_build_deploy_context_ssh_cmd_node_configs_override
# 🧪 TRAP[TEST] · Regression · deploy-context: NODE_CONFIGS_REMOTE_BASE override (deploy_paths канон)
# · Scenario: NODE_CONFIGS_REMOTE_BASE=/opt/nc → --node-yaml /opt/nc/<node>/node.yaml
# · Last fail: N/A (DevPlan 153 T7 N3; deploy_paths.node_configs_remote() канон)
# · Remove if: deploy-context build сигнатура меняется
@ldd_trajectory
def test_build_deploy_context_ssh_cmd_node_configs_override(caplog: pytest.LogCaptureFixture) -> None:
    """build_deploy_context_ssh_cmd(): --node-yaml с NODE_CONFIGS_REMOTE_BASE override."""
    caplog.set_level(logging.DEBUG)
    cmd = build_deploy_context_ssh_cmd("n1", environ={"NODE_CONFIGS_REMOTE_BASE": "/opt/nc"})
    logger.info("[IMP:9][test][deploy-context] cmd=%s", cmd)
    assert "python3 /opt/platform/core/internal/bootstrap/deploy/context_deployer.py" in cmd
    assert "--node-yaml /opt/nc/n1/node.yaml" in cmd
    assert "export PYTHONPATH=/opt/platform" in cmd


# endregion FUNC_test_build_deploy_context_ssh_cmd_node_configs_override


# region FUNC_test_cli_init_prints_command
# 🧪 TRAP[TEST] · Regression · CLI init: stdout = ТОЛЬКО команда, exit 0
# · Scenario: cli(["init", node, owner, ci, age]) → exit 0, stdout начинается с "set -euo pipefail"
# · Last fail: N/A (command-substitution контракт $(build_ssh_cmd ...))
# · Remove if: CLI контракт меняется
@ldd_trajectory
def test_cli_init_prints_command(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI init: печатает ТОЛЬКО remote-команду в stdout, exit 0."""
    caplog.set_level(logging.DEBUG)
    rc = cli(["init", "n1", "owner-key", "ci-key", "age-key"])
    out, _err = capsys.readouterr()
    logger.info("[IMP:9][test][cli] rc=%s, stdout=%s", rc, out.splitlines()[0] if out else "")
    assert rc == 0
    assert out.startswith("set -euo pipefail")
    # stdout = ТОЛЬКО команда (command-substitution): одна строка + терминальный \n
    assert out.count("\n") == 1, "CLI stdout должен содержать ТОЛЬКО команду (command-substitution)"


# endregion FUNC_test_cli_init_prints_command


# region FUNC_test_cli_unknown_mode_usage_error
# 🧪 TRAP[TEST] · NEGATIVE (R5) · CLI: неизвестный mode → exit 2 + stderr
# · Scenario: cli(["bogus", "n1"]) → exit 2, "unknown build mode" в stderr, stdout пуст
# · Last fail: N/A (fail-fast валидация входов)
# · Remove if: CLI контракт меняется
@ldd_trajectory
def test_cli_unknown_mode_usage_error(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """R5 negative: неизвестный build mode → exit 2, сообщение в stderr."""
    caplog.set_level(logging.DEBUG)
    rc = cli(["bogus-mode", "n1"])
    _out, err = capsys.readouterr()
    logger.info("[IMP:9][test][cli-err] rc=%s stderr=%s", rc, err.strip())
    assert rc == 2
    assert "unknown build mode" in err
    assert "bogus-mode" in err


# endregion FUNC_test_cli_unknown_mode_usage_error


# region FUNC_test_cli_init_missing_args_usage_error
# 🧪 TRAP[TEST] · NEGATIVE (R5) · CLI: не хватает позиционных аргументов → exit 2
# · Scenario: cli(["init", "n1"]) → exit 2 (init требует ≥4 аргументов), сообщение в stderr
# · Last fail: N/A (fail-fast; shell падал бы set -u)
# · Remove if: CLI контракт меняется
@ldd_trajectory
def test_cli_init_missing_args_usage_error(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """R5 negative: init без полного набора аргументов → exit 2 (fail-fast)."""
    caplog.set_level(logging.DEBUG)
    rc = cli(["init", "n1"])
    _out, err = capsys.readouterr()
    logger.info("[IMP:9][test][cli-args] rc=%s stderr=%s", rc, err.strip())
    assert rc == 2
    assert "requires at least 4" in err


# endregion FUNC_test_cli_init_missing_args_usage_error
