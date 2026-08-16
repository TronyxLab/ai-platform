# GREP_SUMMARY: walker, Entry, walk-tree, directives, MODULE_CONTRACT, frontmatter, collect, WalkError, yaml
# STRUCTURE: ┌root┐ → ○ scan rules/ roles/ skills/ playbooks/ policies/ → ○ parse MODULE_CONTRACT directives → ○ parse YAML frontmatter → ⊕ list[Entry] → ○ collect → ⎋ dict[id]
# region MODULE_CONTRACT
## @purpose  Walk a canon or consumer `.ai/` tree and parse every markdown entry into a
##   typed Entry (id, kind, directives, frontmatter, body, content)
## @scope    rules/*.md, roles/*/role.md, skills/*/SKILL.md, playbooks/**/*.md, policies/**/*.md
## @invariants
##   - id derivation: rules/policies = stem, roles/skills = dir name, playbooks = rel path w/o .md
##   - Directives are parsed ONLY from the # region MODULE_CONTRACT block
##   - Unknown directive keys log a warning and are skipped (never fatal)
##   - collect() raises WalkError on duplicate id within one tree, listing both paths
## @rationale Deterministic sorted scans keep emission and packing reproducible; tolerant
##   directive parsing keeps the compiler forward-compatible with new canon annotations
# endregion MODULE_CONTRACT

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

KNOWN_DIRECTIVES = frozenset(
    {"purpose", "scope", "invariants", "protected", "order", "roles", "model", "description", "language", "stack", "rationale"}
)

KINDS = ("rules", "roles", "skills", "playbooks", "policies")

_REGION_START_RE = re.compile(r"^\s*#\s*region\s+MODULE_CONTRACT\b")
_REGION_END_RE = re.compile(r"^\s*#\s*endregion\s+MODULE_CONTRACT\b")
_DIRECTIVE_RE = re.compile(r"^##\s+@(\w+)\s*(.*)$")
_ITEM_DIRECTIVE_RE = re.compile(r"^##\s+-\s+@(\w+)\s+(.*)$")
_ITEM_PROSE_RE = re.compile(r"^##\s+(.*)$")


class WalkError(Exception):
    """Raised when a tree contains duplicate entry ids or unreadable content."""


@dataclass
class Entry:
    """A single parsed markdown entry from a canon or consumer tree."""

    id: str
    source_path: Path
    kind: str
    is_canon: bool
    directives: dict[str, object]
    frontmatter: dict[str, object]
    body: str
    content: str


def _coerce(key: str, value: str) -> object:
    """Type-coerce directive values: protected→bool, order→int, roles→list[str]."""
    if key == "protected":
        return value.strip().lower() == "true"
    if key == "order":
        try:
            return int(value)
        except ValueError:
            logger.warning("[IMP:5][WALK][WARN] invalid order directive %r (using 1000)", value)
            return 1000
    if key == "roles":
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _parse_directives(content: str, path: Path) -> dict[str, object]:
    """Parse ## @directives from the MODULE_CONTRACT region into a dict."""
    directives: dict[str, object] = {}
    in_region = False
    in_invariants = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if _REGION_START_RE.match(line):
            in_region = True
            in_invariants = False
            continue
        if _REGION_END_RE.match(line):
            break
        if not in_region:
            continue
        item = _ITEM_DIRECTIVE_RE.match(line)
        if item:
            key, value = item.group(1), item.group(2).strip()
            if key not in KNOWN_DIRECTIVES:
                logger.warning("[IMP:5][WALK][WARN] unknown directive %s in %s", key, path)
                continue
            directives[key] = _coerce(key, value)
            continue
        directive = _DIRECTIVE_RE.match(line)
        if directive:
            key, value = directive.group(1), directive.group(2).strip()
            if key not in KNOWN_DIRECTIVES:
                logger.warning("[IMP:5][WALK][WARN] unknown directive %s in %s", key, path)
                continue
            if key == "invariants":
                directives[key] = []
                in_invariants = True
            else:
                directives[key] = _coerce(key, value)
                in_invariants = False
            continue
        prose = _ITEM_PROSE_RE.match(line)
        if prose and in_invariants:
            text = prose.group(1).strip()
            text = re.sub(r"^\d+\.\s*", "", text)
            text = re.sub(r"^\s*[-*]\s+", "", text)
            invariants = directives.setdefault("invariants", [])
            if isinstance(invariants, list):
                invariants.append(text)
    return directives


def _parse_frontmatter(content: str, path: Path) -> dict[str, object]:
    """Parse a leading YAML frontmatter block (--- delimited) into a dict."""
    if not content.startswith("---"):
        return {}
    lines = content.split("\n")
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end <= 1:
        return {}
    yaml_text = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        logger.warning("[IMP:5][WALK][WARN] invalid frontmatter in %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _make_entry(path: Path, id_: str, kind: str, is_canon: bool) -> Entry:
    content = path.read_text(encoding="utf-8")
    directives = _parse_directives(content, path)
    frontmatter: dict[str, object] = {}
    if kind in {"roles", "skills"}:
        frontmatter = _parse_frontmatter(content, path)
    return Entry(
        id=id_,
        source_path=path,
        kind=kind,
        is_canon=is_canon,
        directives=directives,
        frontmatter=frontmatter,
        body=content,
        content=content,
    )


def _scan_dir(root: Path, pattern: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob(pattern))


# region FUNC_walk_tree
## @purpose  Scan every markdown entry under a root and parse it into Entry objects
## @io       in: tree root + is_canon flag; out: list[Entry]
## @complexity O(files × size)
def walk_tree(root: Path, is_canon: bool) -> list[Entry]:
    """▶ ┌root┐ → ○ scan 5 kind dirs → ○ parse directives + frontmatter → ⊕ list[Entry] → ⎋"""
    entries: list[Entry] = []

    entries.extend(_make_entry(f, f.stem, "rules", is_canon) for f in _scan_dir(root / "rules", "*.md"))
    entries.extend(
        _make_entry(rf, rf.parent.name, "roles", is_canon) for rf in _scan_dir(root / "roles", "*/role.md")
    )
    entries.extend(
        _make_entry(sf, sf.parent.name, "skills", is_canon) for sf in _scan_dir(root / "skills", "*/SKILL.md")
    )

    playbooks_dir = root / "playbooks"
    if playbooks_dir.is_dir():
        for f in sorted(playbooks_dir.rglob("*.md")):
            rel = f.relative_to(playbooks_dir)
            entries.append(_make_entry(f, str(rel.with_suffix("")), "playbooks", is_canon))

    policies_dir = root / "policies"
    if policies_dir.is_dir():
        for f in sorted(policies_dir.rglob("*.md")):
            rel = f.relative_to(policies_dir)
            entries.append(_make_entry(f, str(rel.with_suffix("")), "policies", is_canon))

    logger.info("[IMP:9][WALK][DONE] walked %s (canon=%s): %d entries", root, is_canon, len(entries))
    return entries
# endregion FUNC_walk_tree


# region FUNC_collect
## @purpose  Index a walked entry list by (kind, id), failing fast on duplicates
## @io       in: list[Entry]; out: dict[(kind, id) -> Entry]; raises WalkError on duplicate (kind, id)
## @complexity O(n)
## @rationale The canon legitimately contains same-named rule and skill (rules/superposition.md
##   and skills/superposition/SKILL.md, DevPlan 001 migration map) — ids are unique per kind;
##   destinations are per-kind directories, so a cross-kind name collision is not a conflict.
def collect(walk_results: list[Entry]) -> dict[tuple[str, str], Entry]:
    """▶ ┌entries┐ → ○ index by (kind, id) → ◇ duplicate ? ⊕ WalkError : ⊕ map → ⎋"""
    by_id: dict[tuple[str, str], Entry] = {}
    for entry in walk_results:
        key = (entry.kind, entry.id)
        existing = by_id.get(key)
        if existing is not None:
            msg = (
                f"duplicate id in tree: kind={entry.kind} id={entry.id} "
                f"paths={existing.source_path} and {entry.source_path}"
            )
            raise WalkError(msg)
        by_id[key] = entry
    return by_id
# endregion FUNC_collect
