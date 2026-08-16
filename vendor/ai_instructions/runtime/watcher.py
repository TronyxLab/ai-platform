# GREP_SUMMARY: watcher, watch, polling, mtime, sha256, debounce, rebuild, KeyboardInterrupt
# STRUCTURE: ┌watch set┐ → ○ loop sleep 1s → ○ stat mtime → ◇ changed ? ┌sha256┐ → ◇ changed ? ○ debounce 500ms → ○ rebuild pipeline → ⊕ [IMP:9] rebuilt n → ⎋ KeyboardInterrupt exit 0
# region MODULE_CONTRACT
## @purpose  Poll the canon + consumer trees and rebuild outputs when any watched file changes
## @scope    Pure stdlib polling (no watchdog); rebuild re-runs walk → resolve → emit-changed
##   → cleanup → lock
## @invariants
##   - Poll cycle: sleep 1.0s, stat mtimes, re-hash only files whose mtime changed
##   - On change: sleep 0.5s debounce, then rebuild
##   - Rebuild emits ONLY entries whose source changed; cleanup + lock always run fully
##   - KeyboardInterrupt exits cleanly (no exception escapes the loop)
## @rationale mtime-first polling keeps steady-state cost at one stat per file per second;
##   content hashing after an mtime bump avoids missing same-size in-place rewrites
# endregion MODULE_CONTRACT

import logging
import subprocess
import time
from pathlib import Path

from ai_instructions.runtime import canon_source as canon_source_mod
from ai_instructions.runtime.config import Config
from ai_instructions.runtime.emitter import (
    classify_source,
    cleanup_orphans,
    emit,
    has_stamp,
    plan_outputs,
)
from ai_instructions.runtime.lock import sha256_file, write_lock
from ai_instructions.runtime.resolver import resolve
from ai_instructions.runtime.walker import Entry, collect, walk_tree

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
DEBOUNCE = 0.5


def _watch_files(canon_dir: Path, consumer_root: Path) -> list[Path]:
    """Collect every file under the watched dirs (canon rules/roles/skills + consumer .ai + AGENTS.md)."""
    files: list[Path] = []
    for sub in ("rules", "roles", "skills"):
        d = canon_dir / sub
        if d.is_dir():
            files.extend(f for f in d.rglob("*") if f.is_file())
    ai = consumer_root / ".ai"
    for sub in ("rules", "roles", "skills"):
        d = ai / sub
        if d.is_dir():
            files.extend(f for f in d.rglob("*") if f.is_file())
    agents_md = consumer_root / "AGENTS.md"
    if agents_md.is_file():
        files.append(agents_md)
    return files


def _poll(files: list[Path], last: dict[Path, tuple[int, str]]) -> list[Path]:
    """Return files whose mtime or content hash changed since the previous cycle."""
    changed: list[Path] = []
    current = set(files)
    for p in list(last):
        if p not in current:
            changed.append(p)
            del last[p]
    for p in files:
        try:
            st = p.stat()
        except OSError:
            if p in last:
                changed.append(p)
                del last[p]
            continue
        prev = last.get(p)
        if prev is None or prev[0] != st.st_mtime_ns:
            digest = sha256_file(p)
            if prev is None or prev[1] != digest:
                changed.append(p)
            last[p] = (st.st_mtime_ns, digest)
    return changed


def _rebuild(config: Config, consumer_root: Path, canon_dir: Path, changed_paths: list[Path]) -> int:
    """Re-run the full sync pipeline, emitting only entries whose source changed."""
    changed_set = set(changed_paths)
    canon_entries = collect(walk_tree(canon_dir, is_canon=True))
    ai = consumer_root / ".ai"
    project_entries = collect(walk_tree(ai, is_canon=False)) if ai.is_dir() else {}
    effective, warnings = resolve(canon_entries, project_entries)
    for w in warnings:
        logger.warning("[IMP:5][WATCH][WARN] %s", w)

    all_entries: dict[tuple[str, str], Entry] = {}
    all_entries.update(canon_entries)
    all_entries.update(project_entries)
    changed_ids = {key for key, e in all_entries.items() if e.source_path in changed_set}
    changed_map = {key: effective[key] for key in changed_ids if key in effective}

    canon_version = canon_source_mod.read_version(canon_dir).strip()
    written = emit(config, changed_map, consumer_root, canon_version)
    cleanup_orphans(config, effective, consumer_root)

    plan = plan_outputs(config, effective, consumer_root)
    managed = [p for p in plan if p.is_file() and has_stamp(p)]
    files = [
        (str(p.relative_to(consumer_root)), classify_source(p, plan[p], config, consumer_root))
        for p in sorted(managed, key=str)
    ]
    write_lock(consumer_root, canon_version, files)
    return len(written)


# region FUNC_watch
## @purpose  Poll the watch set forever and rebuild outputs on change
## @io       in: config, consumer root, canon dir; out: None (returns on KeyboardInterrupt)
## @complexity O(watch set) per second
def watch(config: Config, consumer_root: Path, canon_dir: Path) -> None:
    """▶ ┌watch set┐ → ○ loop: sleep 1s → ○ poll mtimes → ◇ changed ? ○ debounce → ○ rebuild → ⎋ KeyboardInterrupt"""
    logger.info("[IMP:5][WATCH][START] watching canon=%s consumer=%s", canon_dir, consumer_root)
    last: dict[Path, tuple[int, str]] = {}
    while True:
        time.sleep(POLL_INTERVAL)
        files = _watch_files(canon_dir, consumer_root)
        changed = _poll(files, last)
        if not changed:
            continue
        time.sleep(DEBOUNCE)
        try:
            n = _rebuild(config, consumer_root, canon_dir, changed)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("[IMP:10][WATCH][FAIL] rebuild failed: %s", exc)
            continue
        logger.info("[IMP:9][WATCH][REBUILD] rebuilt %d outputs", n)
# endregion FUNC_watch
