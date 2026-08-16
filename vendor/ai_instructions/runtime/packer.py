# GREP_SUMMARY: packer, pack, single-markdown, deterministic-order, canon-first, out-path
# STRUCTURE: ┌effective┐ → ○ sort (canon first, order, id) → ○ join ┌# id + content┐ → ⊕ write out_path → ⎋ Path
# region MODULE_CONTRACT
## @purpose  Pack the effective entry map into one deterministic markdown document
## @scope    Ordering and serialization only; the output is a single .md file
## @invariants
##   - Sort key: (not is_canon, order, id) — canon entries first, then project entries
##   - order defaults to 1000 when the directive is absent
##   - Each section is "# <id>" + blank line + full entry content + blank line
##   - Output is byte-for-byte deterministic for the same input map
## @rationale A single packed markdown is a human/agent-friendly digest of the whole
##   canon; pure deterministic ordering keeps diffs minimal across rebuilds
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

from ai_instructions.runtime.config import Config
from ai_instructions.runtime.walker import Entry

logger = logging.getLogger(__name__)

DEFAULT_ORDER = 1000


def _sort_key(entry: Entry) -> tuple[bool, int, str]:
    try:
        order = int(entry.directives.get("order") or DEFAULT_ORDER)
    except (TypeError, ValueError):
        order = DEFAULT_ORDER
    return (not entry.is_canon, order, entry.id)


# region FUNC_pack
## @purpose  Serialize the effective map into a single deterministic markdown file
## @io       in: config (unused — kept for API symmetry), effective map, canon version, out path; out: written Path
## @complexity O(n × file size)
def pack(
    config: Config,
    effective: dict[str, Entry],
    canon_version: str,
    out_path: Path,
) -> Path:
    """▶ ┌effective┐ → ○ sorted(canon, order, id) → ○ "# id" + content per entry → ⊕ write → ⎋ out_path"""
    del config  # kept for API symmetry with emit(); no pack-specific config today
    ordered = sorted(effective.values(), key=_sort_key)
    sections = [f"# {entry.id}\n\n{entry.content}" for entry in ordered]
    text = "\n\n".join(sections) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    logger.info(
        "[IMP:9][PACK][DONE] packed %d entries (canon %s) -> %s",
        len(ordered),
        canon_version,
        out_path,
    )
    return out_path
# endregion FUNC_pack
