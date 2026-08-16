# GREP_SUMMARY: canon-source, resolve-canon, git-clone, cache, read-version, CanonError
# STRUCTURE: ┌canon_path?┐ → ◇ local valid ? ⊕ return : ○ cache ┌~/.cache/ai-instructions/tag┐ → ◇ VERSION match ? ⊕ return : ○ git clone ──branch tag → ⊕ return → ⎋ CanonError(tried)
# region MODULE_CONTRACT
## @purpose  Resolve the canon source tree: explicit local path, pinned cache, or git clone
## @scope    canon resolution priority chain, VERSION reading, cache layout under ~/.cache
## @invariants
##   - Priority is strict: local canon_path → pin cache → git clone
##   - A resolved canon must contain rules/ and VERSION
##   - When ALL sources are unavailable, raise CanonError listing every source tried
##   - Cache VERSION content must match the requested tag (normalized, "v" prefix tolerant)
## @rationale Fail-fast with a tried-sources report gives the operator an actionable
##   message instead of a bare clone failure deep inside the pipeline
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
from pathlib import Path

from ai_instructions.runtime.config import Config

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^(?:v)?(\d+\.\d+\.\d+)$")


class CanonError(Exception):
    """Raised when no canon source could be resolved."""


def _normalize_version(value: str) -> str:
    """Strip whitespace and an optional leading 'v' from a version string."""
    value = value.strip()
    m = _VERSION_RE.match(value)
    return m.group(1) if m else value


# region FUNC_read_version
## @purpose  Read the VERSION file from a canon directory
## @io       in: canon dir; out: version string (whitespace-stripped); raises CanonError
## @complexity O(1)
def read_version(canon_dir: str | Path) -> str:
    """▶ ┌canon_dir/VERSION┐ → ○ read text → ⊕ strip → ⎋ version"""
    p = Path(canon_dir) / "VERSION"
    if not p.is_file():
        msg = f"VERSION file missing in {canon_dir}"
        raise CanonError(msg)
    return p.read_text(encoding="utf-8").strip()
# endregion FUNC_read_version


def _is_valid_canon_dir(p: Path) -> bool:
    return (p / "rules").is_dir() and (p / "VERSION").is_file()


def _cache_dir(config: Config) -> Path:
    return Path.home() / ".cache" / "ai-instructions" / config.canon_tag


def _clone_canon(config: Config, cache_dir: Path) -> Path:
    """Clone the canon remote into the cache dir; raise CanonError on failure."""
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", config.canon_tag, config.canon_remote, str(cache_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        msg = (
            f"git clone {config.canon_remote} @{config.canon_tag} failed "
            f"(exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
        raise CanonError(msg)
    if not _is_valid_canon_dir(cache_dir):
        msg = f"git clone succeeded but {cache_dir} lacks rules/ or VERSION"
        raise CanonError(msg)
    return cache_dir


# region FUNC_resolve_canon
## @purpose  Resolve the canon source tree using the priority chain
## @io       in: config, optional explicit canon_path, consumer root; out: canon dir Path
## @complexity O(1) for local/cache, O(clone) for git
def resolve_canon(config: Config, canon_path: str | None, consumer_root: Path) -> Path:
    """▶ canon_path? → ◇ local valid ? ⊕ return : ○ cache → ◇ VERSION match ? ⊕ return : ○ git clone → ⎋ CanonError(tried)"""
    tried: list[str] = []

    if canon_path:
        p = Path(canon_path)
        if not p.is_absolute():
            p = consumer_root / p
        if _is_valid_canon_dir(p):
            logger.info("[IMP:9][CANON][RESOLVED] local %s", p)
            return p
        tried.append(f"canon-path {p} (missing rules/ or VERSION)")

    cache_dir = _cache_dir(config)
    if (cache_dir / "VERSION").is_file():
        try:
            if _normalize_version(read_version(cache_dir)) == _normalize_version(config.canon_tag):
                logger.info("[IMP:9][CANON][RESOLVED] cache %s", cache_dir)
                return cache_dir
        except CanonError:
            pass
    tried.append(f"pin cache {cache_dir} (missing or tag mismatch)")

    try:
        resolved = _clone_canon(config, cache_dir)
        logger.info("[IMP:9][CANON][RESOLVED] cloned %s@%s -> %s", config.canon_remote, config.canon_tag, resolved)
    except CanonError as exc:
        tried.append(str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        tried.append(f"git clone {config.canon_remote} @{config.canon_tag} (failed: {exc})")
    else:
        return resolved

    logger.error("[IMP:10][CANON][FAIL] cannot resolve canon source; tried:\n  - %s", "\n  - ".join(tried))
    raise CanonError("cannot resolve canon source; tried:\n  - " + "\n  - ".join(tried))
# endregion FUNC_resolve_canon
