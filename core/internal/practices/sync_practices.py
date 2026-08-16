#!/usr/bin/env python3
# GREP_SUMMARY: sync-practices, project-sync-practices, regenerate, repair, drift, GENERATED-files, practices-lock, atomic-write
# STRUCTURE: ▶ sync_practices(project_dir) → load_manifest → resolve language/level → maturity → evaluate (state) → render_project_files → write_generated_file × N (skip manual) → render_lock (maturity-снапшот + generator_hash) → write_lock_file → ⎋ SyncReport
# region MODULE_CONTRACT
## @purpose  Перегенерация GENERATED-файлов практик до канона (DevPlan 137 §2.1A, аналог
##           generate-manifests / sync_env_defaults): рендер pyproject/.pre-commit/conftest/
##           test_health + practices.lock (version/level/state/maturity-снапшот/generator_hash)
##           через shared/atomic_writer. Единый repair дрейфа GENERATED-практик: локально
##           (project-check), в CI (maturity-warn), на VPS (verify version-warn) рекомендуют
##           `make project-sync-practices` — эта команда его исполняет.
## @scope    K1 локальный канал. Makefile: project-sync-practices. Библиотечная функция
##           sync_practices() + CLI main() (паритет sync_env_defaults.py, DevPlan 137 §2.1A).
## @invariants
##   - Все writes через shared/atomic_writer (единый writer, DevPlan 119 E5)
##   - Существующий файл БЕЗ GENERATED-шапки (ручной) → skip + warning (force=True → перезапись)
##   - practices.lock пишется ВСЕГДА (снапшот состояния + maturity для VPS)
##   - maturity-снапшот обязателен (носитель state для K3 — на VPS нет git)
##   - exit-коды из shared/contracts.py: 0 ок, 1 generic, 4 ConfigValidationError
##   - Библиотечные функции не вызывают sys.exit; main() -> int (контракт core/AGENTS.md)
## @rationale Один repair-канал вместо ручных правок GENERATED-файлов (дрейф → байт-сверка
##            канона, как check-manifests). Паритет sync_env_defaults (библиотека + CLI).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from core.internal.practices.escalator import evaluate
from core.internal.practices.generators import (
    render_lock,
    render_project_files,
    write_generated_file,
    write_lock_file,
)
from core.internal.practices.manifest import LANGUAGE_FOR_TYPE, load_manifest
from core.internal.practices.maturity import compute_maturity
from core.internal.shared.contracts import EXIT_CONFIG_VALIDATION, EXIT_OK
from core.internal.shared.exceptions import ConfigValidationError, PlatformError
from core.internal.shared.project_yaml import get_name, get_project_type, load_project_yaml

logger = logging.getLogger(__name__)


# region FUNC_SyncReport
## @purpose  Frozen-отчёт синхронизации: статус каждого файла + lock + state.
## @io       ⇥ files_status/lock_status/state/level/skipped → ⎋ SyncReport
## @complexity O(1)
@dataclass(frozen=True)
class SyncReport:
    """Report of a project-sync-practices run."""

    files_status: tuple[tuple[str, str], ...]  # (relative_path, written|updated|skipped)
    lock_status: str
    state: str
    level: str


# endregion FUNC_SyncReport


# region FUNC_sync_practices
## @purpose  Перегенерация GENERATED-файлов практик + practices.lock до канона (repair дрейфа).
##           Библиотечная функция (тесты вызывают напрямую).
## @io       ⇥ project_dir: Path, force: bool → ⎋ SyncReport
## @raises   ConfigValidationError — сломанный канон (exit 4)
## @complexity O(N * C) где N = файлы, C = размер контента (рендер + атомарные записи)
## @invariants
##   - force=False: ручные файлы (без GENERATED-шапки) пропускаются с warning
##   - lock пишется всегда; state = escalator.evaluate (level из ai-platform.yaml, default auto)
##   - Уровень рендера = level_setting как есть (full → full-конфиги; auto/baseline → baseline)
def sync_practices(project_dir: Path, *, force: bool = False) -> SyncReport:
    """Regenerate GENERATED practices files + practices.lock to canon (drift repair)."""
    project_dir = Path(project_dir)
    manifest = load_manifest()

    data = load_project_yaml(project_dir)
    project_name = get_name(data) or project_dir.name
    ptype = get_project_type(data)
    languages = LANGUAGE_FOR_TYPE.get(ptype)
    language = languages[0] if languages else "python"
    # W11-G4 cross-file (shared/project_yaml → dict[str, object] после типизации G1):
    # .get возвращает object — isinstance-гейт сохраняет прежнюю семантику `or {}`
    quality_data = data.get("quality")
    quality: dict[str, object] = quality_data if isinstance(quality_data, dict) else {}
    level_setting = str(quality.get("level", "auto") or "auto")

    maturity = compute_maturity(project_dir)
    from core.internal.practices.generators import read_lock

    lock = read_lock(project_dir)
    decision = evaluate(maturity, level_setting, lock)

    files = render_project_files(project_name, language, level_setting, manifest.pins, project_type=ptype)
    files_status: list[tuple[str, str]] = []
    for rel_path, content in files.items():
        status = write_generated_file(project_dir / rel_path, content, force=force)
        files_status.append((rel_path, status))

    lock_content = render_lock(
        manifest,
        level_setting,
        decision,
        maturity,
        files,
        language,
    )
    lock_status = write_lock_file(project_dir, lock_content, force=force)

    report = SyncReport(
        files_status=tuple(files_status),
        lock_status=lock_status,
        state=decision.state_name,
        level=level_setting,
    )
    logger.info(
        "[IMP:9][sync_practices][done] files=%d lock=%s state=%s level=%s",
        len(files_status),
        lock_status,
        report.state,
        report.level,
    )
    return report


# endregion FUNC_sync_practices


# region FUNC_format_report
## @purpose  Вывод [PRACTICES:...] отчёта синхронизации для агента.
## @io       ⇥ report: SyncReport → ⎋ str
## @complexity O(N)
def format_report(report: SyncReport) -> str:
    """Render sync report lines (agent-visible)."""
    lines: list[str] = [
        f"[PRACTICES:SYNC][state:{report.state}][level:{report.level}][lock:{report.lock_status}]",
    ]
    for rel_path, status in report.files_status:
        lines.append(f"[PRACTICES:SYNC][file:{rel_path}] {status}")
    return "\n".join(lines)


# endregion FUNC_format_report


# region FUNC_main
## @purpose  CLI: python3 -m core.internal.practices.sync_practices --project-dir DIR [--force].
## @exitcode 0 — синхронизировано; 1 — generic; 4 — ConfigValidationError (канон сломан)
def main(argv: list[str] | None = None) -> int:
    """CLI for project-sync-practices (exit 0/1/4)."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),  # pyright: ignore[reportAny] W11-G4: getattr(logging, str) → Any (уровень из env — динамический атрибут)
        format="[%(levelname)s][practices_sync] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Regenerate GENERATED practices files to canon")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory")
    parser.add_argument("--force", action="store_true", help="Overwrite manual files (no GENERATED header)")
    args = parser.parse_args(argv)

    try:
        # argparse Namespace — нетипизированные атрибуты (Any) → cast на границе CLI (W11-G4)
        report = sync_practices(Path(cast(str, args.project_dir)), force=cast(bool, args.force))
    except ConfigValidationError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_VALIDATION
    except PlatformError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code

    print(format_report(report))
    logger.info("[IMP:9][sync_practices][main] exit=%d", EXIT_OK)
    return EXIT_OK


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
