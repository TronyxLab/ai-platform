#!/usr/bin/env python3
# GREP_SUMMARY: set-practices, project-set-practices, level, baseline, full, auto, ai-platform-yaml, quality, practices-lock, audit
# STRUCTURE: ▶ set_practices(project_dir, level) → validate level → _set_quality_level (ai-platform.yaml#quality.level, line-preserving) → sync_practices(force=True) → audit transition → ⎋ SetReport
# region MODULE_CONTRACT
## @purpose  Установка уровня практик проекта (DevPlan 137 §4.6/§4.7): пишет
##           ai-platform.yaml#quality.level (baseline|full|auto) + перегенерирует GENERATED-файлы
##           и practices.lock под новый уровень (force — уровень меняется по явной команде).
##           level=full → active-full (согласие пользователя, автопромоута НЕТ); level=baseline →
##           откат-форс; level=auto → эскалатор решает. Аудит-запись перехода через
##           shared/audit_logger (единый writer, DevPlan 116 B11 T2).
## @scope    K1 локальный канал. Makefile: project-set-practices LEVEL=LEVEL. Библиотечная
##           функция set_practices() + CLI main() (паритет sync_env_defaults.py).
## @invariants
##   - level ∈ {baseline, full, auto} (validate_level_setting — fail-fast, exit 4 семантика)
##   - ai-platform.yaml#quality.level обновляется line-preserving (комментарии сохраняются)
##   - GENERATED-файлы + lock перегенерируются с force=True (уровень меняется осознанно)
##   - active-full ТОЛЬКО по явному set-practices full (никаких авто-переходов)
##   - Аудит-запись practices_state_transition с from/to/reason (DevPlan 137 §4.6:
##     «event=practices_state_transition project=<name> from=<a> to=<b> reason=...»;
##     audit_logger единый writer; OSError → False, не raise)
##   - exit-коды из shared/contracts.py: 0 ок, 1 generic, 4 ConfigValidationError
## @rationale Единственная команда смены уровня (вместо ручной правки yaml + lock):
##            атомарность и аудит. Паритет sync_env_defaults (библиотека + CLI).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
##           2026-08-05 · DevPlan 137 W3 — аудит §4.6 расширен: from/to/reason (prev_state
##                      из practices.lock до sync; reason="manual:<level>")
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from core.internal.practices.escalator import validate_level_setting
from core.internal.practices.sync_practices import SyncReport, sync_practices
from core.internal.shared.atomic_writer import atomic_write_text
from core.internal.shared.contracts import EXIT_CONFIG_VALIDATION, EXIT_OK
from core.internal.shared.exceptions import ConfigValidationError, PlatformError
from core.internal.shared.project_yaml import get_name, load_project_yaml

logger = logging.getLogger(__name__)

# ── Паттерн quality-блока в ai-platform.yaml (top-level quality: + indented children) ──
_QUALITY_BLOCK_RE = re.compile(r"^quality:.*(?:\n[ \t]+.*)*", re.MULTILINE)

# ── Уровни (паритет escalator) ──
_VALID_LEVELS = ("baseline", "full", "auto")


# region FUNC_SetReport
## @purpose  Frozen-отчёт установки уровня: yaml_status + sync-отчёт + state/level.
## @io       ⇥ yaml_status/sync → ⎋ SetReport
## @complexity O(1)
@dataclass(frozen=True)
class SetReport:
    """Report of a project-set-practices run."""

    yaml_status: str  # created | updated | skipped
    sync: SyncReport


# endregion FUNC_SetReport


# region FUNC_set_practices
## @purpose  Установить уровень практик: validate → ai-platform.yaml quality.level →
##           force-sync (GENERATED-файлы + lock) → аудит-запись перехода.
## @io       ⇥ project_dir: Path, level: str → ⎋ SetReport
## @raises   ConfigValidationError — сломанный канон (exit 4); ValueError — невалидный level
## @complexity O(N * C) (рендер + записи)
## @invariants
##   - Невалидный level → ValueError (main перехватывает → exit 4 семантика)
##   - ai-platform.yaml отсутствует → yaml_status="skipped" (lock всё равно обновляется)
##   - prev_state из practices.lock ДО sync (переход аудируется с from/to — DevPlan 137 §4.6)
def set_practices(project_dir: Path, level: str) -> SetReport:
    """Set practices level (ai-platform.yaml quality.level + force re-sync)."""
    project_dir = Path(project_dir)
    validate_level_setting(level)

    from core.internal.practices.generators import read_lock

    prev_lock = read_lock(project_dir)
    prev_state = prev_lock.state if prev_lock is not None else "none"

    yaml_path = project_dir / "ai-platform.yaml"
    if yaml_path.is_file():
        yaml_status = _set_quality_level(yaml_path, level)
    else:
        yaml_status = "skipped"
        logger.info("[IMP:7][set_practices][yaml] ai-platform.yaml not found — level not persisted")

    sync = sync_practices(project_dir, force=True)

    _audit_transition(project_dir, level, prev_state, sync.state)
    logger.info(
        "[IMP:9][set_practices][done] level=%s state=%s (from=%s) yaml=%s",
        level,
        sync.state,
        prev_state,
        yaml_status,
    )
    return SetReport(yaml_status=yaml_status, sync=sync)


# endregion FUNC_set_practices


# region FUNC__set_quality_level
## @purpose  Обновить quality.level в ai-platform.yaml (line-preserving: заменяет ВЕСЬ
##           quality-блок новым; при отсутствии quality — добавляет в конец). Атомарная запись.
## @io       ⇥ yaml_path: Path, level: str → ⎋ str — "created" | "updated"
## @complexity O(L) где L = строки файла
## @invariants
##   - Комментарии вне quality-блока сохраняются (substitution только quality-блока)
##   - Атомарная запись через shared/atomic_writer (DevPlan 119 E5)
def _set_quality_level(yaml_path: Path, level: str) -> str:
    """Set quality.level in ai-platform.yaml (line-preserving quality block)."""
    text = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else ""
    new_block = f"quality:\n  level: {level}    # baseline | full | auto (default auto)\n"
    if "quality:" in text:
        updated = _QUALITY_BLOCK_RE.sub(new_block.rstrip("\n"), text, count=1) + "\n"
        status = "updated"
    else:
        updated = text.rstrip("\n") + "\n\n" + new_block
        status = "created"
    atomic_write_text(yaml_path, updated)
    logger.info("[IMP:9][set_practices][yaml] quality.level=%s (%s): %s", level, status, yaml_path)
    return status


# endregion FUNC__set_quality_level


# region FUNC__audit_transition
## @purpose  Аудит-запись перехода состояния практик (event=practices_state_transition,
##           DevPlan 137 §4.6): project + from/to + reason (manual:level). Формат записи:
##           event=practices_state_transition project=NAME from=A to=B reason=... —
##           поля from/to/reason в расширенной схеме audit_logger (**extra). OSError → False
##           (не маскирует; dev-машина без /var/log/platform → warning).
## @io       ⇥ project_dir, level, prev_state, new_state → ⎋ bool (True=записано)
## @complexity O(1)
def _audit_transition(project_dir: Path, level: str, prev_state: str, new_state: str) -> bool:
    """Write audit entry for practices state transition (non-raising)."""
    try:
        from core.internal.shared.audit_logger import write_audit_entry

        data = load_project_yaml(project_dir)
        name = get_name(data) or project_dir.name
        ok = write_audit_entry(
            "practices_state_transition",
            "DONE",
            f"practices state transition: {prev_state} → {new_state} (level={level})",
            operation="project-set-practices",
            project=name,
            level=level,
            **{"from": prev_state, "to": new_state, "reason": f"manual:{level}"},
        )
        logger.info(
            "[IMP:9][set_practices][audit] transition recorded: %s → %s (level=%s, written=%s)",
            prev_state,
            new_state,
            level,
            ok,
        )
        return ok
    except (OSError, ImportError) as exc:
        logger.warning("[IMP:7][set_practices][audit] audit skipped: %s", exc)
        return False


# endregion FUNC__audit_transition


# region FUNC_main
## @purpose  CLI: python3 -m core.internal.practices.set_practices --project-dir DIR --level LEVEL.
## @exitcode 0 — уровень установлен; 1 — generic; 4 — невалидный level / ConfigValidationError
def main(argv: list[str] | None = None) -> int:
    """CLI for project-set-practices (exit 0/1/4)."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][practices_set] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Set practices level (baseline|full|auto)")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory")
    parser.add_argument("--level", required=True, type=str, choices=_VALID_LEVELS, help="baseline|full|auto")
    args = parser.parse_args(argv)

    try:
        report = set_practices(Path(args.project_dir), args.level)
    except ValueError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_VALIDATION
    except ConfigValidationError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_VALIDATION
    except PlatformError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code

    print(
        f"[PRACTICES:SET][level:{args.level}][state:{report.sync.state}][yaml:{report.yaml_status}][lock:{report.sync.lock_status}]"
    )
    logger.info("[IMP:9][set_practices][main] exit=%d", EXIT_OK)
    return EXIT_OK


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
