# GREP_SUMMARY: check-project-drift, drift-gate, practices-lock, detect-drift, repair-drift, canon-hash, version-stale, GENERATED-diff
# STRUCTURE: ▶ _detect_drift (lock missing → version < canon → file-level hash vs disk → canon-hash stale via project_profile) → ⊕ _repair_drift (fixer.repair_practices — sync force) → ▶ check_drift_gate (fix? repair : WARN/FAIL) → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  Drift-gate практик (DevPlan 170 W10-A декомпозиция, L2): детект дрейфа
##           GENERATED-практик проекта (practices.lock version < canon; файлы lock.files vs
##           диск — ручная правка; lock.generator_hash vs актуальный канон-рендер) + repair
##           (--fix → перегенерация через sync_practices force). Декомпозиция god-функции
##           check_drift_gate (71 LOC, research-A §2): детект (pure) и repair вынесены —
##           _detect_drift возвращает (cause, drifted), _repair_drift — единая repair-точка.
## @scope    Потребители: runner.py (handler-реестр через checks/__init__), tests (drift
##           R5-negative: ручная правка GENERATED-файла → FAIL в active-full, repair → PASS).
## @invariants
##   - lock missing → WARN (не блок, не FAIL) без --fix; repair → PASS «created»
##   - lock.version < canon → FAIL (в active-full блок); repair → PASS «version updated»
##   - file-drift: ручной файл (без GENERATED-шапки) ВНЕ дрейф-гейта (sync его пропустит)
##   - canon-stale: lock.generator_hash != compute_generator_hash(актуальный рендер) → FAIL
##   - repair-ветка ВСЕГДА вызывает fixer.repair_practices (единая точка sync_practices)
## @rationale Разделение детекта/repair/решения: детект — pure-функция (тестируемая без
##            --fix), repair — единственный канал перегенерации (W10: lazy-дубль ×3 устранён).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:736-810)
##           2026-08-22 · T2.12 — canon-hash резолвит name/language/ptype через practices/profile.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import hashlib
from pathlib import Path

from core.internal.practices.check_project.fixer import repair_practices
from core.internal.practices.check_project.models import CheckResult
from core.internal.practices.generators import (
    GENERATED_HEADER,
    PracticesLock,
    compute_generator_hash,
    read_lock,
    render_project_files,
)
from core.internal.practices.manifest import PracticeCheck, PracticesManifest, load_manifest
from core.internal.practices.profile import project_profile
from core.internal.shared.env_facts import EnvironmentFacts

# Причины дрейфа (машиночитаемые для check_drift_gate-решения)
_CAUSE_MISSING = "missing"
_CAUSE_VERSION = "version"
_CAUSE_FILE = "file"
_CAUSE_NONE = "none"


# region FUNC__detect_drift
## @purpose  Детект дрейфа практик (pure, read-only): (1) lock отсутствует, (2) lock.version
##           < канон (устарел), (3) диск GENERATED-файла расходится с lock.files hash
##           (ручная правка; ручные файлы без шапки — вне гейта), (4) lock.generator_hash
##           != актуальный канон-рендер (канон изменился с момента sync).
## @io       ⇥ project_dir: Path, manifest: PracticesManifest, lock: PracticesLock | None
##           → ⎋ tuple[str, list[str]] — (cause, drifted) где cause ∈ {missing, version, file, none}
## @complexity O(F * C + R) — файлы × содержимое + рендер канона
def _detect_drift(project_dir: Path, manifest: PracticesManifest, lock: PracticesLock | None) -> tuple[str, list[str]]:
    """Detect practices drift; returns (cause, drifted) — cause ∈ {missing, version, file, none}."""
    if lock is None:
        return _CAUSE_MISSING, []

    if lock.version < manifest.version:
        return _CAUSE_VERSION, []

    # ── file-level drift: диск GENERATED-файла vs hash в lock (ручная правка) ──
    drifted: list[str] = []
    for rel, expected_hash in lock.files.items():
        path = project_dir / rel
        if not path.is_file():
            drifted.append(f"{rel} (missing)")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if GENERATED_HEADER not in content:
            continue  # ручной файл — вне дрейф-гейта (sync его пропустит)
        actual = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
        if actual != expected_hash:
            drifted.append(f"{rel} (modified)")

    # ── canon-hash: lock устарел относительно актуального рендера канона ──
    # T2.12: единый project_profile — name/language/ptype одним чтением ai-platform.yaml
    profile = project_profile(project_dir)
    files = render_project_files(profile.name, profile.language, lock.level, manifest.pins, project_type=profile.ptype)
    expected_canon = compute_generator_hash(files, manifest.version, lock.level)
    if lock.generator_hash != expected_canon:
        drifted.append("practices.lock (canon stale)")

    return (_CAUSE_FILE, drifted) if drifted else (_CAUSE_NONE, [])


# endregion FUNC__detect_drift


# region FUNC__repair_drift
## @purpose  Drift-repair: перегенерация GENERATED-практик + practices.lock через
##           fixer.repair_practices (sync_practices force=True — единая точка).
## @io       ⇥ project_dir: Path → ⎋ None (мутация проекта: регенерация до канона)
## @complexity O(N * C) — рендер + атомарные записи
def _repair_drift(project_dir: Path) -> None:
    """Regenerate GENERATED practices to canon (single repair channel)."""
    repair_practices(project_dir)


# endregion FUNC__repair_drift


# ═══════════════════════════════════════════════════════════════════
# region CHECK_drift_gate
def check_drift_gate(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """drift-gate: practices.lock version + file-level drift (lock.files vs disk) + canon-hash
    против актуального канона (L2). auto_fix (--fix) → перегенерация sync_practices (repair).
    Дрейф-детект: (1) lock.version < canon — версия устарела; (2) диск GENERATED-файла
    расходится с lock.files hash (ручная правка); (3) lock.generator_hash != актуальный
    канон-рендер (канон изменился с момента sync). Любой → make project-sync-practices."""
    manifest = load_manifest()
    lock = read_lock(project_dir)
    cause, drifted = _detect_drift(project_dir, manifest, lock)

    if cause == _CAUSE_MISSING:
        if fix:
            _repair_drift(project_dir)
            return CheckResult(check.id, "PASS", "practices.lock created via project-sync-practices", 0.0)
        return CheckResult(check.id, "WARN", "practices.lock missing — run: make project-sync-practices", 0.0)

    if cause == _CAUSE_VERSION:
        if fix:
            _repair_drift(project_dir)
            return CheckResult(check.id, "PASS", "practices.lock version updated via project-sync-practices", 0.0)
        return CheckResult(
            check.id,
            "FAIL",
            f"practices.lock version {lock.version} < canon {manifest.version} — run: make project-sync-practices",
            0.0,
        )

    if cause == _CAUSE_FILE:
        if fix:
            _repair_drift(project_dir)
            return CheckResult(check.id, "PASS", "GENERATED files regenerated via project-sync-practices", 0.0)
        detail = drifted[0] + (f" (+{len(drifted) - 1} more)" if len(drifted) > 1 else "")
        return CheckResult(check.id, "FAIL", f"practices drift: {detail} — run: make project-sync-practices", 0.0)

    return CheckResult(check.id, "PASS", "practices.lock in sync with canon", 0.0)


# endregion CHECK_drift_gate
