# GREP_SUMMARY: emitter, emit, stamp, never-overwrite, hermes, role-skill, project-mode, template-filter, cleanup-orphans, manage-config, kilo.json
# STRUCTURE: ┌effective map┐ → ○ filter (project mode) → ○ plan dest paths (kilo + hermes) → ○ strip GREP_SUMMARY → ○ write stripped+stamp ┌skip manual┐ → ○ cleanup orphans → ○ manage_config → ⎋ list[Path]
# region MODULE_CONTRACT
## @purpose  Emit the effective entry map into .kilo/ (and hermes profile skills),
##   stamping every output and never overwriting unstamped manual files
## @scope    Destination mapping, stamp/never-overwrite policy, project-mode template
##   filtering, orphan cleanup, kilo.json management
## @invariants
##   - Every output carries a trailing <!-- ai-instructions:<version> --> stamp
##   - A target file WITHOUT the stamp regex is manual: skipped, never overwritten
##   - Emission is deterministic: same input tree → byte-identical outputs, no timestamps
##   - hermes emits ONLY when config.hermes_enabled AND not project_mode
##   - role-<id> hermes skills are generated ONLY for canon roles with roles_as_skills,
##     and NEVER for roles with frontmatter mode: subagent
##   - Cleanup deletes stamped orphans only; unstamped files are never touched
##   - GREP_SUMMARY HTML-комментарии (<!-- GREP_SUMMARY: … -->) вырезаются из эмиссии
##     (платформенный патч — см. TRAP[DECISION] у _strip_grep_summary): .md-инструкции
##     не несут этого маркера, чек-система платформы его в .md не требует
## @rationale The stamp regex is the compiler's ownership marker: stamped files are
##   compiler-managed and safe to overwrite/delete, unstamped files are user-owned
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import re
from pathlib import Path

import yaml

from ai_instructions.runtime.config import Config
from ai_instructions.runtime.walker import Entry

logger = logging.getLogger(__name__)

STAMP_RE = re.compile(r"<!-- ai-instructions:\d+\.\d+\.\d+ -->")

# ⚠️ TRAP[DECISION] · 2026-08-17 · — · Платформенный патч вендоренного эмиттера: strip
# `<!-- GREP_SUMMARY: … -->` из эмитируемого контента · Rejected: полагаться на чистый канон
# (канон v0.7.0 несёт GREP_SUMMARY в markdown; upstream-фикс — в Tronyx161/AI-instructions) ·
# Reason: GREP_SUMMARY в .md не требуется ни одним чеком платформы (grep-summary — только
# кодовые расширения; doc-headers исключает .kilo/ и .ai/); verbatim-эмиссия тащила мусор
# в .kilo/skills и hermes platform-профиль · Rev: канон очищен upstream → снять патч
# (вернуть verbatim-эмиссию) и удалить _strip_grep_summary
_GREP_SUMMARY_LINE_RE = re.compile(r"^[ \t]*<!--[ \t]*GREP_SUMMARY:.*?-->\s*\n?", re.MULTILINE)


def _strip_grep_summary(content: str) -> str:
    """Remove own-line `<!-- GREP_SUMMARY: ... -->` comments from emitted content."""
    return _GREP_SUMMARY_LINE_RE.sub("", content)


class EmitError(Exception):
    """Raised when emission or kilo.json management fails."""


def _norm_version(value: str) -> str:
    return value.strip().removeprefix("v")


def _stamp(version: str) -> str:
    return f"\n<!-- ai-instructions:{_norm_version(version)} -->\n"


def hermes_skills_root(config: Config, consumer_root: Path) -> Path:
    """Absolute hermes skills output root: <consumer>/<emit_dir>/<profile>/skills."""
    return consumer_root / config.hermes_emit_dir / config.hermes_profile / "skills"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    else:
        return True


def _hermes_active(config: Config, project_mode: bool, hermes_enabled: bool | None) -> bool:
    base = config.hermes_enabled if hermes_enabled is None else hermes_enabled
    return bool(base) and not project_mode


def _filter_entries(
    effective: dict[str, Entry],
    project_mode: bool,
    template: str | None,
) -> dict[str, Entry]:
    """Apply project-mode template filtering (backend/frontend); identity otherwise."""
    if not project_mode or template is None or template == "all":
        return dict(effective)
    out: dict[str, Entry] = {}
    for eid, entry in effective.items():
        d = entry.directives
        has_ls = "language" in d or "stack" in d
        if template == "backend":
            if has_ls and d.get("language") != "python":
                continue
        elif (
            template == "frontend" and has_ls and not (d.get("stack") == "react" and d.get("language") == "typescript")
        ):
            continue
        out[eid] = entry
    return out


# region FUNC_output_paths_for_entry
## @purpose  Compute all destination paths for a single entry (kilo + hermes)
## @io       in: entry + emit context; out: list of destination Paths
## @complexity O(1)
def output_paths_for_entry(
    config: Config,
    entry: Entry,
    *,
    consumer_root: Path,
    hermes_active: bool,
    hermes_roles: bool,
) -> list[Path]:
    """▶ ┌entry┐ → ○ map kind → dests → ○ hermes role-<id>? → ⊕ list[Path] → ⎋"""
    kilo = consumer_root / ".kilo"
    dests: list[Path] = []
    kind = entry.kind
    if kind == "rules":
        dests.append(kilo / "rules" / f"{entry.id}.md")
    elif kind == "roles":
        dests.append(kilo / "agents" / f"{entry.id}.md")
    elif kind == "skills":
        dests.append(kilo / "skills" / entry.id / "SKILL.md")
        if hermes_active:
            dests.append(hermes_skills_root(config, consumer_root) / entry.id / "SKILL.md")
    elif kind == "playbooks":
        dests.append(kilo / "skills" / f"playbook-{entry.id}" / entry.source_path.name)
    elif kind == "policies":
        dests.append(kilo / "policies" / f"{entry.id}.md")
    if hermes_roles and kind == "roles" and entry.is_canon and entry.frontmatter.get("mode") != "subagent":
        dests.append(hermes_skills_root(config, consumer_root) / f"role-{entry.id}" / "SKILL.md")
    return dests


# endregion FUNC_output_paths_for_entry


# region FUNC_plan_outputs
## @purpose  Map every planned destination path to the entry that produces it
## @io       in: emit context; out: dict[Path -> Entry]
## @complexity O(n)
def plan_outputs(
    config: Config,
    effective: dict[str, Entry],
    consumer_root: Path,
    project_mode: bool = False,
    template: str | None = None,
    hermes_enabled: bool | None = None,
) -> dict[Path, Entry]:
    """▶ ┌effective┐ → ○ filter → ○ dests per entry → ⊕ dict[Path, Entry] → ⎋"""
    hermes_active = _hermes_active(config, project_mode, hermes_enabled)
    hermes_roles = hermes_active and config.roles_as_skills
    plan: dict[Path, Entry] = {}
    for entry in _filter_entries(effective, project_mode, template).values():
        for dst in output_paths_for_entry(
            config,
            entry,
            consumer_root=consumer_root,
            hermes_active=hermes_active,
            hermes_roles=hermes_roles,
        ):
            plan[dst] = entry
    return plan


# endregion FUNC_plan_outputs


def _strip_frontmatter(content: str) -> str:
    """Return content after a leading --- YAML frontmatter block, or the content unchanged."""
    if not content.startswith("---"):
        return content
    lines = content.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return content


def _role_skill_content(entry: Entry) -> str:
    """Build a hermes role-<id> SKILL.md: {name, description} frontmatter + role body."""
    name = f"role-{entry.id}"
    description = entry.directives.get("purpose") or entry.frontmatter.get("description") or ""
    fm = yaml.safe_dump({"name": name, "description": str(description)}, sort_keys=False).strip()
    return f"---\n{fm}\n---\n{_strip_frontmatter(entry.body)}"


def _content_for(entry: Entry, dst: Path, config: Config, consumer_root: Path, version: str) -> str:
    if entry.kind == "roles" and _is_under(dst, hermes_skills_root(config, consumer_root)):
        return _strip_grep_summary(_role_skill_content(entry)) + _stamp(version)
    return _strip_grep_summary(entry.content) + _stamp(version)


def _write_with_stamp(path: Path, content: str) -> bool:
    """Write content + stamp unless the target exists as an unstamped manual file."""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[IMP:7][EMIT][SKIP] unreadable target %s: %s", path, exc)
            return False
        if not STAMP_RE.search(existing):
            logger.warning("[IMP:7][EMIT][SKIP] manual file, never overwrite: %s", path)
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def has_stamp(path: Path) -> bool:
    """True if the file carries the compiler ownership stamp regex."""
    try:
        return STAMP_RE.search(path.read_text(encoding="utf-8")) is not None
    except OSError:
        return False


def classify_source(path: Path, entry: Entry, config: Config, consumer_root: Path) -> str:
    """Lock-file source label: hermes for profile outputs, canon/project for kilo outputs."""
    if _is_under(path, hermes_skills_root(config, consumer_root)):
        return "hermes"
    return "canon" if entry.is_canon else "project"


# region FUNC_emit
## @purpose  Write every planned output (verbatim + stamp), skipping manual files
## @io       in: config, effective map, consumer root, canon version, mode flags; out: written Paths
## @complexity O(n × file size)
def emit(
    config: Config,
    effective: dict[str, Entry],
    consumer_root: Path,
    canon_version: str,
    project_mode: bool = False,
    template: str | None = None,
    hermes_enabled: bool | None = None,
) -> list[Path]:
    """▶ ┌plan┐ → ○ for each dst ┌sorted┐ → ◇ manual? ⊕ skip : ⊕ write+stamp → ○ project? manage_config → ⎋ written"""
    version = _norm_version(canon_version)
    plan = plan_outputs(config, effective, consumer_root, project_mode, template, hermes_enabled)
    written: list[Path] = []
    for dst in sorted(plan, key=str):
        entry = plan[dst]
        content = _content_for(entry, dst, config, consumer_root, version)
        if _write_with_stamp(dst, content):
            written.append(dst)
    logger.info("[IMP:9][EMIT][DONE] wrote %d files under %s", len(written), consumer_root)
    if project_mode:
        manage_config(consumer_root)
    return written


# endregion FUNC_emit


def _prune_empty(root: Path) -> None:
    with contextlib.suppress(OSError):
        for d in sorted(
            (p for p in root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            d.rmdir()


# region FUNC_cleanup_orphans
## @purpose  Delete stamped outputs whose id is no longer in the effective map
## @io       in: emit context; out: list of deleted Paths
## @complexity O(files under output roots)
def cleanup_orphans(
    config: Config,
    effective: dict[str, Entry],
    consumer_root: Path,
    project_mode: bool = False,
) -> list[Path]:
    """▶ ┌effective┐ → ○ plan all → ○ scan output roots ┌*.md┐ → ◇ stamped and not planned ? ⊕ unlink : keep → ○ prune empty dirs → ⎋ deleted"""
    plan = plan_outputs(config, effective, consumer_root, project_mode=project_mode)
    expected = set(plan)
    roots = [consumer_root / ".kilo" / d for d in ("rules", "agents", "skills", "policies")]
    if not project_mode and config.hermes_enabled:
        roots.append(hermes_skills_root(config, consumer_root))

    deleted: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*.md"):
            if not f.is_file() or f in expected:
                continue
            if has_stamp(f):
                f.unlink()
                deleted.append(f)

    for root in roots:
        if root.is_dir():
            _prune_empty(root)

    logger.info("[IMP:9][EMIT][CLEAN] deleted %d orphans", len(deleted))
    return deleted


# endregion FUNC_cleanup_orphans


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# region FUNC_manage_config
## @purpose  Ensure kilo.json carries the ".kilo/rules/*.md" instructions glob
## @io       in: project dir; out: True if the file was created or modified
## @complexity O(1)
def manage_config(project_dir: Path) -> bool:
    """▶ ┌kilo.json?┐ → ◇ exists ? ○ load : ⊕ create ┌{"instructions": [glob]}┐ → ◇ glob missing ? ○ append : keep → ⊕ write indent=2 → ⎋ modified"""
    cfg_path = project_dir / "kilo.json"
    instructions_glob = ".kilo/rules/*.md"
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            msg = f"cannot read {cfg_path}: {exc}"
            raise EmitError(msg) from exc
        if not isinstance(data, dict):
            msg = f"{cfg_path} must be a JSON object"
            raise EmitError(msg)
        current = data.get("instructions")
        if not isinstance(current, list):
            data["instructions"] = [instructions_glob]
            modified = True
        elif instructions_glob not in current:
            data["instructions"] = [*current, instructions_glob]
            modified = True
        else:
            modified = False
        if modified:
            _write_json(cfg_path, data)
        logger.info("[IMP:9][CONFIG][MANAGED] %s modified=%s", cfg_path, modified)
        return modified

    _write_json(cfg_path, {"instructions": [instructions_glob]})
    logger.info("[IMP:9][CONFIG][MANAGED] created %s", cfg_path)
    return True


# endregion FUNC_manage_config
