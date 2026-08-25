#!/usr/bin/env python3
# GREP_SUMMARY: ssh-cmd-builder printf_q printf-%q build_ssh_cmd build_update_ssh_cmd build_converge_ssh_cmd build_check_security_ssh_cmd build_deploy_context_ssh_cmd build_init_secret_prelude build_update_secret_prelude stdin-transport secret-prelude REF-0007 remote-command D3 PLATFORM_ROOT ci_deploy_key ci_root_key python-cli
# STRUCTURE: ▶ ┌args+environ┐ → ○ printf_q (bash printf %q, D3) → ⊕ body exports (PLATFORM_ROOT/DOMAIN/CONTEXT — БЕЗ секретов) → ⊕ orchestrator args (%q) → ○ passthrough (%q) → ⎋ echo remote cmd ── ▶ secret_prelude (AGE/CI-ключи) → ⎋ echo export-скрипт для ssh-stdin (bash -s)
# region MODULE_CONTRACT
## @purpose  Bash-совместимые printf %q SSH command builders (DevPlan 164 W3.5-1, порт
##           build-ssh-cmd.sh): build_ssh_cmd (init), build_update_ssh_cmd (update),
##           build_converge_ssh_cmd (converge/reconcile), build_check_security_ssh_cmd,
##           build_deploy_context_ssh_cmd. Прямое замещение shell-библиотеки — имена функций,
##           позиционные аргументы и ВЫВОД совпадают посимвольно (parity с bash printf %q).
##           REF-0007 (Волна 1): AGE_SECRET_KEY/PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY
##           ВЫНЕСЕНЫ из remote-команды в ОТДЕЛЬНЫЙ secret-prelude (build_*_secret_prelude),
##           доставляемый вызывающей стороной через ssh-stdin (`bash -s`) — ключи больше НЕ
##           светятся в /proc/<pid>/cmdline ни локально (ssh argv), ни на ноде (remote shell).
## @scope    Потребители: build-ssh-cmd.sh (фасад, sourced bootstrap.sh + remote-cmd.sh) —
##           python3 -m core.internal.shared.ssh_cmd_builder \<mode\> \<args...\>. Самодостаточен
##           (stdlib-only, watchdog-паттерн): НЕ импортирует core.internal — вызывается системным
##           python3 из shell-фасадов (тесты/extraction без venv-пути).
## @invariants
##   - D3: printf %q quoting — НЕПРИКОСНОВЕННО (TRAP[DECISION] 2026-07-26: shlex.quote() ≠ printf '%q').
##     printf_q() воспроизводит bash %q посимвольно: safe-набор [A-Za-z0-9_@%+=:,./-~] + non-ASCII
##     literal; printable-unsafe → backslash; control (C0/DEL) → $'...'; пустая строка → ''
##   - PLATFORM_ROOT export обязателен для remote core-скриптов (TRAP[BUG] P1, 2026-07-31)
##   - ci_deploy_key fallback chain: PLATFORM_CI_DEPLOY_KEY → param (TRAP[BUG] P2, 2026-07-17);
##     fallback chain живёт в build_init_secret_prelude (stdin-канал, REF-0007)
##   - ci_root_key (142 W1): fallback chain PLATFORM_CI_ROOT_KEY → param (тот же паттерн)
##   - REF-0007: тело build_ssh_cmd/build_update_ssh_cmd НЕ содержит значений ключей
##     (argv-тест test_build_ssh_cmd_no_secrets_in_argv); ключи — ТОЛЬКО в prelude,
##     который печатается в stdout ТОЛЬКО по явному *-secrets mode и уходит в ssh-stdin
##   - CLI печатает в stdout ТОЛЬКО команду (command-substitution контракт $(build_ssh_cmd ...));
##     LDD-логи — stderr. print() запрещён (T201) — sys.stdout.write/sys.stderr.write (CLI-канал)
## @rationale Прямое замещение (DevPlan 164 W3.5-1, Strangler Tier-2): вся build-логика переехала
##            из shell в типизированный stdlib-модуль; shell-фасад сохраняет имена/аргументы —
##            вызывающие стороны (bootstrap.sh, remote-cmd.sh) не меняются. DI через environ-параметр
##            (Mapping) — тесты без monkeypatch os.environ.
##            Q (REF-0007): почему stdin→bash -s, а не SCP 0600 root-file + unset? A: файл на диске
##            переживает crash между scp и rm (класс «permanent world-readable копия», SEC-0015),
##            требует cleanup-автоматики и уникальных имён; stdin не оставляет артефактов вовсе.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created (порт build-ssh-cmd.sh 179 LOC → ~150 LOC)
##           2026-08-24 | REF-0007 (11-DevPlan Волна 1) — секреты вне argv: +build_*_secret_prelude;
##                      тело init/update без AGE/ci-ключей (транспорт ssh-stdin → bash -s)
# ⚠️ TRAP[DECISION] · 2026-07-26 · — · printf %q quoting — НЕПРИКОСНОВЕННО (D3)
# · Rejected: shlex.quote() (Python stdlib) — single-quote-wrapping ≠ printf %q backslash-escaping;
# ·   смена форматирования ломает byte-parity с bash-эпохой и глобавльный diff-аудит remote-команд
# · Reason: printf_q() ниже воспроизводит bash %q (C locale) посимвольно — verified против bash 5.x
# · Rev: если bash сменит формат %q (control chars) — обновить printf_q + parity-тест
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Канонические remote-базы (RC 121: PLATFORM_ROOT исключён из remote-цепочки) ──
# Значения — из SoT shared/deploy_paths (гейт opt-path-literals: 0 литералов вне SoT).
# stdlib-only fallback: само-вывод от __file__ (parents[3] = platform root на ноде).
def _remote_base_default() -> str:
    """Resolve remote platform base default (deploy_paths-канон → self-derived fallback)."""
    try:
        from core.internal.shared import deploy_paths  # SoT: PLATFORM_REMOTE_BASE → platform base

        return str(deploy_paths.platform_remote_base())
    except ImportError:
        return str(Path(__file__).resolve().parents[3])


def _node_configs_remote_default() -> str:
    """Resolve remote node-configs base default (deploy_paths-канон → self-derived fallback)."""
    try:
        from core.internal.shared import deploy_paths  # SoT: NODE_CONFIGS_REMOTE_BASE → node-configs

        return str(deploy_paths.node_configs_remote())
    except ImportError:
        return str(Path(__file__).resolve().parents[3].parent / "node-configs")


_DEFAULT_REMOTE_BASE = _remote_base_default()
_DEFAULT_NODE_CONFIGS_REMOTE_BASE = _node_configs_remote_default()

# ── Константы printf_q (PLR2004: магические значения → именованные) ──
_NON_ASCII_MIN = 0x80  # non-ASCII (UTF-8 multibyte) — bash %q оставляет literal
_CONTROL_MAX = 0x1F  # C0 control range
_DEL = 0x7F  # DEL

# ── Константы CLI ──
_INIT_MIN_ARGS = 4  # init: node, owner_key, ci_deploy_key, age_key
_UPDATE_SECRETS_ARITY = 2  # update-secrets: node + age-key (T2.B P1-17 strict)

# safe-набор bash printf %q (C locale) — символы, не требующие экранирования
_PRINTF_Q_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_@%+=:,./-~")

# named C-escapes для control chars внутри $'...' (bash printf %q)
_C_ESCAPE_NAMES: dict[int, str] = {
    0x0A: "n",
    0x09: "t",
    0x0D: "r",
    0x07: "a",
    0x08: "b",
    0x0C: "f",
    0x0B: "v",
}


# region FUNC__environ
## @purpose  DI-резолвер окружения: None → os.environ (prod), задан → Mapping (тесты).
## @io       ⇥ environ: Mapping[str, str] | None → ⎋ Mapping[str, str]
## @complexity  O(1)
def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    """Return the effective environment mapping (DI seam, W4-канон)."""
    return os.environ if environ is None else environ


# endregion FUNC__environ


# region FUNC_printf_q
## @purpose  Bash-совместимый printf %q (D3 invariant): POSIX-safe quoting через backslash,
##           НЕ shlex.quote(). Byte-parity с bash 5.x (C locale) — verified 2026-08-14.
## @io       ⇥ value: str → ⎋ str (bash %q-вывод)
## @complexity  O(n) — посимвольный обход
## @invariants
##   - safe-набор: [A-Za-z0-9_@%+=:,./-~] + non-ASCII (ord ≥ 0x80, UTF-8 literal как bash)
##   - printable-unsafe: backslash-escape (space → \ , $ → \$ ...)
##   - control chars (C0 + DEL): $'...' с named-escapes (`\n \t \r \a \b \f \v`) или \0NNN octal
##   - пустая строка → ''
def printf_q(value: str) -> str:
    """Quote a string exactly like bash `printf %q` (D3 — NOT shlex.quote)."""
    if not value:
        return "''"
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch in _PRINTF_Q_SAFE or code >= _NON_ASCII_MIN:
            out.append(ch)
        elif code in _C_ESCAPE_NAMES:
            out.append(f"$'\\{_C_ESCAPE_NAMES[code]}'")
        elif code < _CONTROL_MAX or code == _DEL:
            out.append(f"$'\\{code:03o}'")
        else:
            out.append("\\" + ch)
    return "".join(out)


# endregion FUNC_printf_q


# region FUNC__append_export
## @purpose  Добавить ` && export NAME=<printf_q value>` в список частей команды.
## @io       ⇥ parts: list[str], name: str, value: str → ⎋ None (mutates parts)
## @complexity  O(len(value))
def _append_export(parts: list[str], name: str, value: str) -> None:
    """Append ` && export NAME=<%q-value>` segment to the command parts."""
    parts.append(f" && export {name}={printf_q(value)}")


# endregion FUNC__append_export


# region FUNC__append_passthrough
## @purpose  Добавить %q-quoted passthrough-аргументы в конец команды (PERF401: extend).
## @io       ⇥ parts: list[str], passthrough_args: Sequence[str] → ⎋ None (mutates parts)
## @complexity  O(n × len) — посимвольный %q на каждый аргумент
def _append_passthrough(parts: list[str], passthrough_args: Sequence[str]) -> None:
    """Append ` <%q-arg>` segments for each passthrough argument."""
    parts.extend(f" {printf_q(arg)}" for arg in passthrough_args)


# endregion FUNC__append_passthrough


# region FUNC_build_init_secret_prelude
## @purpose  REF-0007: secret-prelude INIT-режима — export-строки AGE_SECRET_KEY +
##           PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY (fallback chain env → param, TRAP P2)
##           для доставки через ssh-stdin (`bash -s`). Печатается ТОЛЬКО по явному
##           init-secrets CLI-mode; НИКОГДА не попадает в argv и в логи.
## @io       ⇥ ci_deploy_key/age_key/ci_root_key: str, environ: Mapping | None (DI) → ⎋ str
##              (многострочный export-скрипт; "" = секретов нет — stdin можно не пайпить)
## @complexity  O(n) — конкатенация %q-сегментов
## @invariants
##   - Имена env ЗАМОРОЖЕНЫ (DEP-0017): AGE_SECRET_KEY не переименовывать
##   - fallback chain PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY → param сохранён (TRAP P2)
def build_init_secret_prelude(
    ci_deploy_key: str,
    age_key: str,
    ci_root_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build stdin prelude with AGE/ci key exports (REF-0007). Empty string = no secrets."""
    env = _environ(environ)
    lines: list[str] = []
    if age_key:
        lines.append(f"export AGE_SECRET_KEY={printf_q(age_key)}")
    effective_ci_key = env.get("PLATFORM_CI_DEPLOY_KEY") or ci_deploy_key
    if effective_ci_key:
        lines.append(f"export PLATFORM_CI_DEPLOY_KEY={printf_q(effective_ci_key)}")
    effective_ci_root_key = env.get("PLATFORM_CI_ROOT_KEY") or ci_root_key
    if effective_ci_root_key:
        lines.append(f"export PLATFORM_CI_ROOT_KEY={printf_q(effective_ci_root_key)}")
    if not lines:
        return ""
    logger.info("[IMP:9][build_init_secret_prelude][build] INIT secret prelude built (%d exports)", len(lines))
    return "\n".join(lines)


# endregion FUNC_build_init_secret_prelude


# region FUNC_build_update_secret_prelude
## @purpose  REF-0007: secret-prelude UPDATE-режима — export AGE_SECRET_KEY для φ9
##           (secrets-update читает env; --age-secret-key флага в update нет по D2).
## @io       ⇥ age_key: str → ⎋ str ("" = ключа нет)
## @complexity  O(1)
## @invariants  Имя AGE_SECRET_KEY заморожено (DEP-0017)
def build_update_secret_prelude(age_key: str) -> str:
    """Build stdin prelude with the AGE key export for node-update (REF-0007)."""
    if not age_key:
        return ""
    logger.info("[IMP:9][build_update_secret_prelude][build] UPDATE secret prelude built (1 export)")
    return f"export AGE_SECRET_KEY={printf_q(age_key)}"


# endregion FUNC_build_update_secret_prelude


# region FUNC_build_ssh_cmd
## @purpose  INIT-режим: remote-команда node-lifecycle.sh --mode init (полный bootstrap).
##           Порт build_ssh_cmd() из build-ssh-cmd.sh (DevPlan 101 D1, 142 W1).
##           REF-0007: тело БЕЗ значений AGE/ci-ключей — они доставляются отдельным
##           secret-prelude через ssh-stdin (см. build_init_secret_prelude); CLI-флаги
##           --ci-deploy-key/--ci-root-key НЕ эмитятся (lifecycle читает те же значения
##           из env, который приносит prelude — cli.py _CLI_ENV_INJECTIONS setdefault).
## @io       ⇥ node_name/owner_key/ci_deploy_key/age_key/ci_root_key: str, passthrough_args: Sequence[str],
##              environ: Mapping[str, str] | None (DI) → ⎋ str (remote command, bash -c)
## @complexity  O(n) — конкатенация %q-сегментов
## @invariants
##   - БЕЗ значений ключей в выводе (argv-тест test_build_ssh_cmd_no_secrets_in_argv);
##     параметры ci_deploy_key/age_key/ci_root_key оставлены в сигнатуре ради parity shell-фасада —
##     игнорируются телом (ключи уходят только в prelude)
##   - --owner-key остаётся в argv — это PUBLIC ключ (cli.py help: "SSH public key")
##   - --resume всегда (INIT resume-семантика); passthrough — %q в конце
def build_ssh_cmd(
    node_name: str,
    owner_key: str,
    ci_deploy_key: str,
    age_key: str,
    ci_root_key: str = "",
    passthrough_args: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build remote init bootstrap command (printf %q, D3) — exact port of shell build_ssh_cmd()."""
    del ci_deploy_key, age_key, ci_root_key  # REF-0007: ключи вне argv — только в secret-prelude
    env = _environ(environ)
    remote_root = env.get("PLATFORM_REMOTE_BASE", _DEFAULT_REMOTE_BASE)
    remote_orchestrator = f"{remote_root}/core/internal/bootstrap/node-lifecycle.sh"
    remote_node_yaml = f"{env.get('NODE_CONFIGS_REMOTE_BASE', _DEFAULT_NODE_CONFIGS_REMOTE_BASE)}/{node_name}/node.yaml"

    parts: list[str] = ["set -euo pipefail"]
    # TRAP[BUG] P1 (2026-07-31): PLATFORM_ROOT обязателен для remote core-скриптов
    _append_export(parts, "PLATFORM_ROOT", remote_root)
    domain = env.get("PLATFORM_DOMAIN")
    if domain:
        _append_export(parts, "PLATFORM_DOMAIN", domain)
    context = env.get("CONTEXT")
    if context:
        _append_export(parts, "CONTEXT", context)

    parts.append(f" && bash {printf_q(remote_orchestrator)} --mode init")
    parts.append(f" --node-name {printf_q(node_name)}")
    parts.append(f" --node-yaml {printf_q(remote_node_yaml)}")
    parts.append(f" --owner-key {printf_q(owner_key)}")
    parts.append(" --resume")
    _append_passthrough(parts, passthrough_args)

    cmd = "".join(parts)
    logger.info("[IMP:9][build_ssh_cmd][build] INIT remote command built (node=%s, secrets=stdin-prelude)", node_name)
    return cmd


# endregion FUNC_build_ssh_cmd


# region FUNC_build_update_ssh_cmd
## @purpose  UPDATE-режим: remote-команда node-lifecycle.sh --mode update. Без --owner-key,
##           без --resume (D2), без ci-ключей. Порт build_update_ssh_cmd().
##           REF-0007: AGE_SECRET_KEY НЕ в теле — доставляется через ssh-stdin prelude
##           (build_update_secret_prelude).
## @io       ⇥ node_name/age_key: str, passthrough_args: Sequence[str],
##              environ: Mapping[str, str] | None → ⎋ str
## @complexity  O(n)
## @invariants  PLATFORM_ROOT export — тот же канон (remote_root = scp-deliver base);
##              age_key параметр игнорируется телом (parity сигнатуры shell-фасада)
def build_update_ssh_cmd(
    node_name: str,
    age_key: str,
    passthrough_args: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build remote node-update command (printf %q, D3) — exact port of build_update_ssh_cmd()."""
    del age_key  # REF-0007: ключ вне argv — только в secret-prelude
    env = _environ(environ)
    remote_root = env.get("PLATFORM_REMOTE_BASE", _DEFAULT_REMOTE_BASE)
    remote_orchestrator = f"{remote_root}/core/internal/bootstrap/node-lifecycle.sh"
    remote_node_yaml = f"{env.get('NODE_CONFIGS_REMOTE_BASE', _DEFAULT_NODE_CONFIGS_REMOTE_BASE)}/{node_name}/node.yaml"

    parts: list[str] = ["set -euo pipefail"]
    _append_export(parts, "PLATFORM_ROOT", remote_root)
    domain = env.get("PLATFORM_DOMAIN")
    if domain:
        _append_export(parts, "PLATFORM_DOMAIN", domain)
    context = env.get("CONTEXT")
    if context:
        _append_export(parts, "CONTEXT", context)

    parts.append(f" && bash {printf_q(remote_orchestrator)} --mode update")
    parts.append(f" --node-name {printf_q(node_name)}")
    parts.append(f" --node-yaml {printf_q(remote_node_yaml)}")
    _append_passthrough(parts, passthrough_args)

    cmd = "".join(parts)
    logger.info(
        "[IMP:9][build_update_ssh_cmd][build] UPDATE remote command built (node=%s, secrets=stdin-prelude)", node_name
    )
    return cmd


# endregion FUNC_build_update_ssh_cmd


# region FUNC_build_converge_ssh_cmd
## @purpose  CONVERGE-режим: remote-команда converge.sh --node \<name\>. Используется и для
##           reconcile (passthrough "--reconcile" добавляет вызывающая сторона).
## @io       ⇥ node_name: str, passthrough_args: Sequence[str], environ → ⎋ str
## @complexity  O(n)
## @invariants  Без AGE/ci-ключей/домена — только PLATFORM_ROOT export (как shell)
def build_converge_ssh_cmd(
    node_name: str,
    passthrough_args: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build remote converge command (printf %q, D3) — exact port of build_converge_ssh_cmd()."""
    env = _environ(environ)
    remote_root = env.get("PLATFORM_REMOTE_BASE", _DEFAULT_REMOTE_BASE)
    remote_converge = f"{remote_root}/core/internal/bootstrap/converge.sh"

    parts: list[str] = ["set -euo pipefail"]
    _append_export(parts, "PLATFORM_ROOT", remote_root)
    parts.append(f" && bash {printf_q(remote_converge)} --node {printf_q(node_name)}")
    _append_passthrough(parts, passthrough_args)

    cmd = "".join(parts)
    logger.info("[IMP:9][build_converge_ssh_cmd][build] CONVERGE remote command built (node=%s)", node_name)
    return cmd


# endregion FUNC_build_converge_ssh_cmd


# region FUNC_build_check_security_ssh_cmd
## @purpose  CHECK-SECURITY-режим: remote security_posture.py --node \<name\> (DevPlan 134 L2).
##           Дополнительно экспортирует PYTHONPATH — security_posture.py импортирует core.internal.*
##           (TRAP[BUG] 2026-07-31 converge.sh:66: shell-фасад/SSH-команда экспортирует PYTHONPATH).
## @io       ⇥ node_name: str, passthrough_args: Sequence[str], environ → ⎋ str
## @complexity  O(n)
## @invariants  PYTHONPATH=remote_root экспорт обязателен (канон converge.sh:66)
def build_check_security_ssh_cmd(
    node_name: str,
    passthrough_args: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build remote security-posture command (printf %q, D3) — port of build_check_security_ssh_cmd()."""
    env = _environ(environ)
    remote_root = env.get("PLATFORM_REMOTE_BASE", _DEFAULT_REMOTE_BASE)
    remote_posture = f"{remote_root}/core/internal/bootstrap/security_posture.py"

    parts: list[str] = ["set -euo pipefail"]
    _append_export(parts, "PLATFORM_ROOT", remote_root)
    # TRAP[BUG] 2026-07-31: security_posture.py импортирует core.internal.* — PYTHONPATH канон
    _append_export(parts, "PYTHONPATH", remote_root)
    parts.append(f" && python3 {printf_q(remote_posture)} --node {printf_q(node_name)}")
    _append_passthrough(parts, passthrough_args)

    cmd = "".join(parts)
    logger.info("[IMP:9][build_check_security_ssh_cmd][build] CHECK-SECURITY remote command built (node=%s)", node_name)
    return cmd


# endregion FUNC_build_check_security_ssh_cmd


# region FUNC_build_deploy_context_ssh_cmd
## @purpose  DEPLOY-CONTEXT-режим: remote context_deployer.py --node-yaml \<remote path\> (DevPlan 153 T7 N3).
##           NODE_CONFIGS_REMOTE_BASE — deploy_paths.node_configs_remote() канон (default /opt/node-configs).
## @io       ⇥ node_name: str, passthrough_args: Sequence[str], environ → ⎋ str
## @complexity  O(n)
## @invariants  PYTHONPATH export обязателен (context_deployer.py импортирует core.internal.*)
def build_deploy_context_ssh_cmd(
    node_name: str,
    passthrough_args: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Build remote deploy-context command (printf %q, D3) — port of build_deploy_context_ssh_cmd()."""
    env = _environ(environ)
    remote_root = env.get("PLATFORM_REMOTE_BASE", _DEFAULT_REMOTE_BASE)
    nc_base = env.get("NODE_CONFIGS_REMOTE_BASE", _DEFAULT_NODE_CONFIGS_REMOTE_BASE)
    remote_deployer = f"{remote_root}/core/internal/bootstrap/deploy/context_deployer.py"
    remote_node_yaml = f"{nc_base}/{node_name}/node.yaml"

    parts: list[str] = ["set -euo pipefail"]
    _append_export(parts, "PLATFORM_ROOT", remote_root)
    _append_export(parts, "PYTHONPATH", remote_root)
    parts.append(f" && python3 {printf_q(remote_deployer)} --node-yaml {printf_q(remote_node_yaml)}")
    _append_passthrough(parts, passthrough_args)

    cmd = "".join(parts)
    logger.info("[IMP:9][build_deploy_context_ssh_cmd][build] DEPLOY-CONTEXT remote command built (node=%s)", node_name)
    return cmd


# endregion FUNC_build_deploy_context_ssh_cmd


# region FUNC__dispatch_build
## @purpose  Per-mode dispatch: валидация позиционных аргументов + вызов build-функции.
##           Вынесен из cli() (PLW0717: try-клауза >5 операторов).
## @io       ⇥ mode: str, rest: list[str] → ⎋ str (remote command)
## @raises ValueError  неизвестный mode / не хватает аргументов
## @complexity  O(n)
## @invariants  passthrough-аргументы с '-' префиксом не интерпретируются как опции (ручной парсинг)
def _dispatch_build(mode: str, rest: list[str]) -> str:
    """Validate args and delegate to the matching build function."""
    if mode == "init":
        _require(rest, _INIT_MIN_ARGS, mode)
        node, owner, ci_deploy, age = rest[0], rest[1], rest[2], rest[3]
        ci_root = rest[_INIT_MIN_ARGS] if len(rest) > _INIT_MIN_ARGS else ""
        return build_ssh_cmd(node, owner, ci_deploy, age, ci_root, rest[_INIT_MIN_ARGS + 1 :])
    if mode == "update":
        _require(rest, 2, mode)
        return build_update_ssh_cmd(rest[0], rest[1], rest[2:])
    # REF-0007: *-secrets modes печатают ТОЛЬКО secret-prelude (stdout → ssh-stdin канал)
    if mode in {"init-secrets", "update-secrets"}:
        return _dispatch_secrets(mode, rest)
    if mode == "converge":
        _require(rest, 1, mode)
        return build_converge_ssh_cmd(rest[0], rest[1:])
    if mode == "check-security":
        _require(rest, 1, mode)
        return build_check_security_ssh_cmd(rest[0], rest[1:])
    if mode == "deploy-context":
        _require(rest, 1, mode)
        return build_deploy_context_ssh_cmd(rest[0], rest[1:])
    # DevPlan 16 T2.B (P1-15): SoT-emit таймаута ssh-exec для shell-фасадов —
    # build-ssh-cmd.sh резолвит значение через этот режим (0 литералов в shell, parity)
    if mode == "ssh-exec-timeout":
        from core.internal.shared.timeouts import DEPLOY_TIMEOUT

        return str(DEPLOY_TIMEOUT)
    msg = f"unknown build mode '{mode}'"
    raise BuildModeError(msg)


# endregion FUNC__dispatch_build


# region FUNC__dispatch_secrets
## @purpose  Диспетчеризация *-secrets режимов (REF-0007; DevPlan 16 T2.B C901-декомпозиция).
## @io       ⇥ mode: init-secrets|update-secrets, rest → ⎋ str prelude ⚡ BuildModeError
def _dispatch_secrets(mode: str, rest: list[str]) -> str:
    """Dispatch *-secrets modes with strict arity (P1-17 fail-loud)."""
    if mode == "init-secrets":
        _require(rest, _INIT_MIN_ARGS, mode)
        # DevPlan 16 T2.B (P1-17): лишние позиционные НЕ глотаются молча — fail-loud
        if len(rest) > _INIT_MIN_ARGS + 1:
            msg = (
                f"mode 'init-secrets' takes at most {_INIT_MIN_ARGS + 1} args "
                f"(node owner ci-deploy age [ci-root]), got {len(rest)}"
            )
            raise BuildModeError(msg)
        ci_deploy, age = rest[2], rest[3]
        ci_root = rest[_INIT_MIN_ARGS] if len(rest) > _INIT_MIN_ARGS else ""
        return build_init_secret_prelude(ci_deploy, age, ci_root)
    # update-secrets
    _require(rest, _UPDATE_SECRETS_ARITY, mode)
    if len(rest) > _UPDATE_SECRETS_ARITY:
        msg = f"mode 'update-secrets' takes exactly {_UPDATE_SECRETS_ARITY} args (node age-key), got {len(rest)}"
        raise BuildModeError(msg)
    return build_update_secret_prelude(rest[1])


# endregion FUNC__dispatch_secrets


# region FUNC__require## @purpose  Валидация числа позиционных аргументов режима (fail-fast).
## @io       ⇥ rest: list[str], need: int, mode: str → ⎋ None ⚡ BuildModeError
## @complexity  O(1)


class BuildModeError(ValueError):
    """Некорректный build-режим/аргументы CLI (локальный control-flow, bare-raise-бан 163 W-C).

    ## @purpose — Именованный сабкласс ValueError для CLI-usage валидации: ловится
    ##            существующим `except ValueError` в cli() (exit 2 не меняется), но имя
    ##            не триггерит bare-raise-реестр (U-12) — локальный control-flow, не ошибка бизнеса.
    ## @complexity O(1)
    """


def _require(rest: list[str], need: int, mode: str) -> None:
    """Raise BuildModeError if the mode does not have enough positional args."""
    if len(rest) < need:
        msg = f"mode '{mode}' requires at least {need} argument(s)"
        raise BuildModeError(msg)


# endregion FUNC__require


# region FUNC_cli
## @purpose  CLI entrypoint: python3 -m core.internal.shared.ssh_cmd_builder \<mode\> \<args...\>.
##           Ручной парсинг (НЕ argparse) — passthrough-аргументы могут начинаться с '-' (--force,
##           --reconcile), argparse съел бы их как опции (142 B32 урок). Печатает ТОЛЬКО команду в stdout.
## @io       ⇥ argv: list[str] | None (default sys.argv[1:]) → ⎋ int exit code (0=ok, 2=usage)
## @complexity  O(n)
## @invariants  stdout = только remote-команда (command-substitution контракт)
def cli(argv: list[str] | None = None) -> int:
    """CLI: build-ssh-cmd \\<mode\\> \\<args...\\> → prints remote command to stdout."""
    _configure_stderr_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(
            "FATAL: missing build mode (init|update|converge|check-security|deploy-context|init-secrets|update-secrets)\n"
        )
        return 2
    mode, rest = args[0], args[1:]
    try:
        command = _dispatch_build(mode, rest)
    except ValueError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    sys.stdout.write(command + "\n")
    logger.info("[IMP:9][cli][dispatch] mode=%s — remote command printed", mode)
    return 0


# endregion FUNC_cli


# region FUNC__configure_stderr_logging
## @purpose  Ленивая конфигурация stderr-хэндлера логгера (LDD-канал CLI). Идемпотентно.
## @io       ⇥ None → ⎋ None (side-effect: logger handler)
## @complexity  O(1)
def _configure_stderr_logging() -> None:
    """Add a stderr handler to the module logger once (LDD telemetry for CLI runs)."""
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


# endregion FUNC__configure_stderr_logging


if __name__ == "__main__":
    sys.exit(cli())
