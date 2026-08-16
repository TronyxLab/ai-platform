# GREP_SUMMARY: resolver, override, protected-collision, effective-map, ResolverError, warnings
# STRUCTURE: ┌canon_entries + project_entries┐ → ○ merge → ◇ id in canon ? ◇ canon protected ? ⊕ ResolverError : ⊕ override : ⊕ add-only → ⎋ (effective, warnings)
# region MODULE_CONTRACT
## @purpose  Merge canon and consumer-project entries into a single effective map with
##   override semantics and fail-fast protected collision detection
## @scope    Override resolution only; no file I/O happens here
## @invariants
##   - Canon entries are the starting map; project entries may override or add
##   - Overriding a canon entry with protected=True raises ResolverError (fail-fast)
##   - A project entry with protected=True is terminal: it stays in the map untouched
##   - The effective map preserves insertion order (canon first, then additions)
## @rationale Protected canon entries are business invariants a consumer must not
##   silently replace; failing fast with both paths prevents subtle configuration drift
# endregion MODULE_CONTRACT

import logging

from ai_instructions.runtime.walker import Entry

logger = logging.getLogger(__name__)


class ResolverError(Exception):
    """Raised when a project entry collides with a protected canon entry."""


# region FUNC_resolve
## @purpose  Compute the effective entry map from canon + project trees
## @io       in: canon dict, project dict (keyed by (kind, id)); out: (effective dict, warning strings)
## @complexity O(n + m)
def resolve(
    canon_entries: dict[tuple[str, str], Entry],
    project_entries: dict[tuple[str, str], Entry],
) -> tuple[dict[tuple[str, str], Entry], list[str]]:
    """▶ ┌canon┐ → ○ copy → ○ for project entry → ◇ in canon ? ◇ protected ? ⊕ raise : ⊕ replace : ⊕ add → ⎋ (effective, warnings)"""
    effective: dict[tuple[str, str], Entry] = dict(canon_entries)
    warnings: list[str] = []
    for key, entry in project_entries.items():
        canon = effective.get(key)
        if canon is None:
            effective[key] = entry
            continue
        if bool(canon.directives.get("protected", False)):
            kind, pid = key
            msg = (
                f"protected collision: kind={kind} id={pid} "
                f"canon={canon.source_path} project={entry.source_path}"
            )
            raise ResolverError(msg)
        warnings.append(f"override: id={key[1]} project entry replaces canon entry")
        effective[key] = entry
    logger.info(
        "[IMP:9][RESOLVE][DONE] effective=%d entries, %d overrides, %d project additions",
        len(effective),
        len(warnings),
        len(project_entries),
    )
    return effective, warnings
# endregion FUNC_resolve
