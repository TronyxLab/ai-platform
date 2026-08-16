#!/usr/bin/env python3
# GREP_SUMMARY: provision-env provision-environment scope-expansion all dry-run platform-env audit dispatch orchestrator provisioner python-cli
# STRUCTURE: ▶ parse_args(--scope×N,--platform-env,--dry-run,-h) → ○ expand 'all' + dedup → ○ resolve platform-env (default repo-root) → ○ per-scope dispatch provisioner.main (audit START/DONE/FAIL) → ◇ rc≠0? → ⎋ rc | ⎋ 0 + "Provision complete"
# region MODULE_CONTRACT
## @purpose  Python-оркестратор provision-environment.sh (DevPlan 164 W3.5-1, Strangler Tier-2):
##           парсинг CLI, расширение 'all', дедупликация scopes, default platform-env, per-scope
##           dispatch на core/internal/provisioner.main() с audit-записями (START/DONE/FAIL).
##           Прямое замещение shell-логики provision-environment.sh — CLI-контракт и вывод те же.
## @scope    Вызывается из shell-фасада core/internal/provision-environment.sh (exec python3 -m).
##           Потребители фасада: Makefile, CI workflows, deploy-modules.sh, state_machine.py (φ3/φ11).
## @invariants
##   - --scope обязателен (нет default); multi-scope accumulator + dedup (FIX-1 регрессия)
##   - 'all' → networks,volumes,env,profiles (фиксированный порядок, dedup при повторе)
##   - LDD блок [provision] (backward compat с тестовыми ассертами, НЕ [provision_env])
##   - exit-коды: 0=success, 1=usage/parse, 10=docker unavailable (propagate provisioner PlatformError)
##   - Fail-fast: первый scope с rc≠0 → return rc БЕЗ "Provision complete" (set -e семантика shell)
##   - Диспатч через DI-параметр provisioner_main (callable(argv)→int); default = provisioner.main
##   - Audit через DI-параметр audit_fn (tag,status,msg)→None; default = shared/audit_logger (non-fatal)
##   - print() запрещён (T201) — CLI-вывод через sys.stdout.write/sys.stderr.write
## @rationale provisioner.py покрывает БИЗНЕС-логику провижининга (networks/volumes/env/profiles),
##            но НЕ покрывает оркестрацию фасада (multi-scope/'all'-расширение/дедупликация/default
##            platform-env/audit-диспатч) — поэтому создан provision_env.py, а не прямое делегирование
##            фасада на provisioner. Импорт core.internal.provisioner/audit_logger — явно необходимая
##            зависимость оркестратора (их API — предмет делегирования; watchdog-stdlib-паттерн
##            применим к самодостаточным bootstrap-модулям — здесь модуль существует для оркестрации).
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created (порт provision-environment.sh 136 LOC → ~160 LOC)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_ch = logging.StreamHandler(sys.stderr)
_ch.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [_ch]

# ── Канонические scopes (совпадают с choices provisioner.py) ──
_VALID_SCOPES: frozenset[str] = frozenset({"networks", "volumes", "env", "profiles"})
_ALL_SCOPES: tuple[str, ...] = ("networks", "volumes", "env", "profiles")

_USAGE = """Usage: provision-environment.sh --scope <networks|volumes|env|all|profiles> [--platform-env <path>] [--dry-run]

Flags:
  --scope <scope>       Scope of provisioning (required)
  --platform-env <path> Path to platform-env.yaml (default: auto-detect)
  --dry-run             Print actions without executing (for testing)

Exit codes:
  0  Success (all resources created or already exist)
  1  Error parsing platform-env.yaml
  2  Docker unavailable (for --scope networks)
"""


# region CLS__CliArgs
@dataclass
class _CliArgs:
    """Разобранные CLI-аргументы фасада provision-environment.sh."""

    scopes: list[str] = field(default_factory=list)
    platform_env: str | None = None
    dry_run: bool = False


# endregion CLS__CliArgs


# region FUNC_parse_args
## @purpose  Ручной парсинг CLI (НЕ argparse — поведение должно совпадать с shell-фасадом:
##           кастомные сообщения FATAL/ERROR, --help → "Usage:" в stdout).
## @io       ⇥ argv: Sequence[str] → ⎋ tuple[_CliArgs | None, int] — (None, code) для
##              help/usage-error (code — exit), иначе (args, 0)
## @complexity  O(n) — один проход
## @invariants  --scope без значения → "FATAL: --scope requires a value" (exit 1);
##              --scope отсутствует → "FATAL: --scope is required" (exit 1);
##              неизвестный аргумент → "ERROR: Unknown argument: X" (exit 1);
##              -h/--help → _USAGE в stdout (exit 0)
def parse_args(argv: Sequence[str]) -> tuple[_CliArgs | None, int]:
    """Parse facade CLI args (mirror provision-environment.sh). Returns (args, exit_code)."""
    scopes: list[str] = []
    platform_env: str | None = None
    dry_run = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--help", "-h"}:
            sys.stdout.write(_USAGE)
            return None, 0
        if arg == "--scope":
            i += 1
            if i >= len(argv) or not argv[i]:
                sys.stderr.write("FATAL: --scope requires a value\n")
                return None, 1
            scopes.append(argv[i])
        elif arg == "--platform-env":
            i += 1
            if i >= len(argv) or not argv[i]:
                sys.stderr.write("FATAL: --platform-env requires a path\n")
                return None, 1
            platform_env = argv[i]
        elif arg == "--dry-run":
            dry_run = True
        else:
            sys.stderr.write(f"ERROR: Unknown argument: {arg}\n")
            return None, 1
        i += 1
    if not scopes:
        sys.stderr.write("FATAL: --scope is required\n")
        sys.stderr.write(_USAGE.splitlines()[0] + "\n")
        return None, 1
    return _CliArgs(scopes=scopes, platform_env=platform_env, dry_run=dry_run), 0


# endregion FUNC_parse_args


# region FUNC_expand_scopes
## @purpose  Расширение 'all' → concrete scopes + дедупликация (сохраняя порядок появления).
## @io       ⇥ scopes: Sequence[str] → ⎋ list[str] (expanded, deduped)
## @raises ScopeError  неизвестный scope (имя — в сообщении для FATAL-вывода)
## @complexity  O(n)
## @invariants  'all' расширяется в networks,volumes,env,profiles (порядок канона);
##              повтор scopes (в т.ч. 'all' + явный) дедуплицируется (FIX-1 регрессия)


class ScopeError(ValueError):
    """Неизвестный SCOPE provision (локальный control-flow, bare-raise-бан 163 W-C).

    ## @purpose — Именованный сабкласс ValueError для CLI-usage валидации: ловится
    ##            существующим `except ValueError` (exit-семантика не меняется), но имя
    ##            не триггерит bare-raise-реестр (U-12) — локальный control-flow, не ошибка бизнеса.
    ## @complexity O(1)
    """


def expand_scopes(scopes: Sequence[str]) -> list[str]:
    """Expand 'all' to concrete scopes and deduplicate, preserving first-seen order."""
    expanded: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        if scope == "all":
            for concrete in _ALL_SCOPES:
                if concrete not in seen:
                    seen.add(concrete)
                    expanded.append(concrete)
        elif scope in _VALID_SCOPES:
            if scope not in seen:
                seen.add(scope)
                expanded.append(scope)
        else:
            raise ScopeError(scope)
    return expanded


# endregion FUNC_expand_scopes


# region FUNC_default_platform_env_path
## @purpose  Default platform-env.yaml: repo root (4 уровня вверх от bootstrap/) + /platform-env.yaml.
##           Совпадает с shell __PROVISION_PLATFORM_ROOT/platform-env.yaml.
## @io       ⇥ None → ⎋ Path
## @complexity  O(1)
def default_platform_env_path() -> Path:
    """Return the default platform-env.yaml path (repo root — same as shell wrapper)."""
    return Path(__file__).resolve().parent.parent.parent.parent / "platform-env.yaml"


# endregion FUNC_default_platform_env_path


# region FUNC_default_provisioner_main
## @purpose  Ленивый default диспетчера: provisioner.main (argv → exit code). Lazy-import —
##            модуль остаётся импортируемым без core.internal (для хрупких сред), реальный
##            dispatch поднимает provisioner по требованию.
## @io       ⇥ None → ⎋ Callable[[list[str]], int]
## @complexity  O(1)
def default_provisioner_main() -> Callable[[list[str]], int]:
    """Return provisioner.main as the default dispatch callable (lazy import)."""
    from core.internal.provisioner import main as provisioner_main

    return provisioner_main


# endregion FUNC_default_provisioner_main


# region FUNC_default_audit
## @purpose  Ленивый default audit: shared/audit_logger.write_audit_entry (non-fatal, raise_on_error=False).
##           Неудача аудита → WARN-лог (никогда не валит провижининг — канон lib/audit.sh).
## @io       ⇥ None → ⎋ Callable[[str, str, str], None]
## @complexity  O(1)
def default_audit() -> Callable[[str, str, str], None]:
    """Return the default audit writer (shared/audit_logger, non-fatal)."""
    from core.internal.shared.audit_logger import write_audit_entry

    def _audit(tag: str, status: str, message: str) -> None:
        try:
            write_audit_entry(tag, status, message, raise_on_error=False)
        except OSError:
            logger.warning("[IMP:6][provision][audit] WARN: audit entry dropped (tag=%s status=%s)", tag, status)

    return _audit


# endregion FUNC_default_audit


# region FUNC_main
## @purpose  CLI entrypoint: парсинг → расширение → dispatch per-scope (provisioner.main + audit).
##           Exit-коды: 0=success, 1=usage/parse, 10=docker unavailable (propagate).
## @io       ⇥ argv: list[str] | None (default sys.argv[1:]); DI: provisioner_main: Callable | None,
##              audit_fn: Callable | None → ⎋ int
## @complexity  O(S × M) — S scopes × работа провижинера
## @invariants  Fail-fast: первый scope rc≠0 → return rc (без "Provision complete", как shell set -e);
##              "Provision complete (scope=<оригинальные scopes через запятую>)" — только при всех 0;
##              IMP-логи [provision] — stderr (LDD-контракт, тестовые ассерты)
def main(
    argv: list[str] | None = None,
    *,
    provisioner_main: Callable[[list[str]], int] | None = None,
    audit_fn: Callable[[str, str, str], None] | None = None,
) -> int:
    """Orchestrate provisioning: parse args, expand scopes, dispatch per scope with audit."""
    args = list(sys.argv[1:] if argv is None else argv)
    parsed, code = parse_args(args)
    if parsed is None:
        return code

    try:
        expanded = expand_scopes(parsed.scopes)
    except ValueError as bad_scope:
        logger.error(
            "[IMP:10][provision] FATAL: Unknown scope '%s'. Valid values: networks, volumes, env, profiles, all",
            bad_scope,
        )
        return 1

    yaml_path = Path(parsed.platform_env) if parsed.platform_env else default_platform_env_path()
    dispatch = provisioner_main if provisioner_main is not None else default_provisioner_main()
    audit = audit_fn if audit_fn is not None else default_audit()

    for scope in expanded:
        tag = f"provision:{scope}"
        audit(tag, "START", "starting")
        prov_args = ["--scope", scope, "--platform-env", str(yaml_path)]
        if parsed.dry_run:
            prov_args.append("--dry-run")
        rc = dispatch(prov_args)
        if rc != 0:
            logger.info("[IMP:10][provision][%s] FAIL: scope failed (exit=%s)", scope, rc)
            audit(tag, "FAIL", f"failed (rc={rc})")
            return rc
        audit(tag, "DONE", "completed (rc=0)")

    scope_label = ",".join(parsed.scopes)
    logger.info("[IMP:9][provision] Provision complete (scope=%s)", scope_label)
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
