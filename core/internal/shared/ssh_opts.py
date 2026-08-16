#!/usr/bin/env python3
# GREP_SUMMARY: ssh-opts, shared, SSH_OPTS, build-rsync-ssh-opts, batchmode, connect-timeout, server-alive, sole-source-of-truth, cli
# STRUCTURE: ▶ ┌SSH_OPTS list (SoT)┐ → ◇ build_rsync_ssh_opts() → "ssh <flags>" → ◇ CLI --shell (bash read -r -a) / --rsync-e → ⎋ stdout/exit 0
# region MODULE_CONTRACT
## @purpose  Единый Source of Truth SSH-флагов платформы (DevPlan 116 B5 T2, D1, U-15).
##           SSH_OPTS — единственное определение списка `-o ...` флагов в Python. 5 копий
##           (core_deliverer, overlay_deliverer, channels ×2, remote_executor) заменены импортом.
##           lib/ssh.sh — тонкий shell-фасад, получающий флаги через `python3 -m ... --shell`.
## @scope    Все Python-модули core/internal, выполняющие ssh/rsync/scp операции.
##           CLI: `python3 -m core.internal.shared.ssh_opts --shell` (флаги через пробел для
##           bash `read -r -a`) и `--rsync-e` (строка `ssh -o ...` для rsync -e).
## @invariants
##   1. SSH_OPTS содержит РОВНО канонический набор: BatchMode=yes, StrictHostKeyChecking=accept-new,
##      ConnectTimeout=<SSH_CONNECT_TIMEOUT>, ServerAliveInterval=30, ServerAliveCountMax=10.
##   2. ConnectTimeout берётся из timeouts.SSH_CONNECT_TIMEOUT (единый таймаут-реестр).
##   3. build_rsync_ssh_opts() — ЕДИНСТВЕННАЯ реализация `f"ssh {' '.join(SSH_OPTS)}"`.
##   4. CLI никогда не печатает секреты (флаги не содержат credentials).
##   5. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз).
## @rationale D1 (DevPlan 116): 5 Python-копий SSH_OPTS (дублирующие списки флагов) + 1 shell-копия.
##            ConnectTimeout=10 outlier в context_promoter. Триггер «extract when consumers > 3»
##            (vps_readiness.py:37-42) сработал — 5 потребителей. Python SoT уменьшает
##            bash-поверхность (пожелание пользователя); shell получает флаги через python3 -m.
## @changes  2026-08-01 | DevPlan 116 B5 T2 — Created (единый SoT SSH-флагов)
# ⚡ TRAP[DECISION] · 2026-08-01 · — · SSH_OPTS Python SoT — триггер vps_readiness:37-42 сработал
# · Rejected: оставить дублирующие копии SSH_OPTS (5 шт.) — каждая правка ConnectTimeout = 6 правок
# · Reason: потребителей > 3 (core_deliverer, overlay_deliverer, channels ×2, remote_executor) —
# ·   порог «extract when consumers > 3» достигнут; D1 пользователя 2026-08-01.
# · Rev: если появится второй shell-потребитель флагов — пересмотреть фасад lib/ssh.sh.
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import sys

from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

# Единый канонический набор SSH-флагов платформы (U-15, D1).
# ⚠️ Порядок флагов — часть канона: lib/ssh.sh читает их через `read -r -a` (bash 3.2).
SSH_OPTS: list[str] = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=10",
]


# region FUNC_build_rsync_ssh_opts
def build_rsync_ssh_opts() -> str:
    """Build rsync -e argument from SSH_OPTS (эквивалент shell ${SSH_OPTS_COMMON[*]}).

    ▶ ┌SSH_OPTS┐ → ○ ' '.join → ⎋ f"ssh {flags}"

    ## @purpose — ЕДИНСТВЕННАЯ реализация сборки rsync -e из SSH_OPTS (переезд из
    ##            core_deliverer.py:89-92 / overlay_deliverer.py:103-106).
    ## @io — ⇥ None → ⎋ str: `ssh -o BatchMode=yes ...`
    ## @complexity — O(k) где k = len(SSH_OPTS)
    ## @invariants — Возвращает строку без секретов; флаги не содержат пробелов в значениях.
    """
    return f"ssh {' '.join(SSH_OPTS)}"


# endregion FUNC_build_rsync_ssh_opts


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: --shell печатает флаги через пробел; --rsync-e печатает строку ssh.

    ▶ ┌argv┐ → ◇ --shell? → ' '.join(SSH_OPTS) | ◇ --rsync-e? → build_rsync_ssh_opts() → ⎋ exit 0

    ## @purpose — Интерфейс для shell-фасадов: lib/ssh.sh (SSH_OPTS_COMMON) и rsync -e.
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0)
    ## @complexity — O(1)
    ## @invariants
    ##   - --shell: флаги через пробел (bash 3.2 `read -r -a` — без mapfile).
    ##   - --rsync-e: строка `ssh -o ...` (для rsync -e).
    ##   - Без аргументов — error exit 2 (fail-fast).
    """
    parser = argparse.ArgumentParser(description="SSH_OPTS — единый SoT SSH-флагов (D1)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shell", action="store_true", help="Print SSH_OPTS space-separated (for bash read -r -a)")
    group.add_argument("--rsync-e", action="store_true", help="Print rsync -e ssh string")
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    from dataclasses import dataclass
    from typing import cast

    @dataclass
    class _CliArgs:
        shell: bool
        rsync_e: bool

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    if args.shell:
        sys.stdout.write(" ".join(SSH_OPTS) + "\n")
    else:
        sys.stdout.write(build_rsync_ssh_opts() + "\n")
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
