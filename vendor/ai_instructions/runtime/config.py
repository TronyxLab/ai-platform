# GREP_SUMMARY: config, pins, yaml, canon-tag, digest, hermes, templates, validation, Config
# STRUCTURE: ┌pins.yaml┐ → ○ yaml.safe_load → ◇ validate canon.tag + digest → ⊕ Config dataclass → ⎋
# region MODULE_CONTRACT
## @purpose  Load and validate the consumer pins YAML into a typed Config dataclass
## @scope    --config file parsing, tag/digest format validation, defaults for missing keys
## @invariants
##   - canon.tag is REQUIRED and must match v<major>.<minor>.<patch>
##   - canon.digest, when present, must match sha256:<64 lowercase hex>
##   - Missing optional keys fall back to documented defaults (hermes disabled, etc.)
##   - Malformed input raises ConfigError (fail-fast) with a clear message
## @rationale Fail-fast validation catches bad pins files before any canon resolution or
##   emission, so a typo in the consumer config cannot silently produce wrong output
# endregion MODULE_CONTRACT

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

DEFAULT_HERMES_EMIT_DIR = "core/modules/hermes-agent/build/templates/profiles"
DEFAULT_HERMES_PROFILE = "platform"
DEFAULT_CANON_REMOTE = "https://github.com/Tronyx161/AI-instructions.git"


class ConfigError(Exception):
    """Raised when the pins YAML is missing, malformed, or fails validation."""


@dataclass
class Config:
    """Resolved consumer configuration for the convention compiler."""

    canon_tag: str
    canon_digest: str | None = None
    hermes_enabled: bool = False
    roles_as_skills: bool = False
    hermes_profile: str = DEFAULT_HERMES_PROFILE
    hermes_emit_dir: str = DEFAULT_HERMES_EMIT_DIR
    requires_instructions_version: str | None = None
    canon_remote: str = DEFAULT_CANON_REMOTE


def _as_bool(value: object, default: bool = False) -> bool:
    """Coerce a YAML scalar to bool, tolerating quoted 'true'/'false' strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _as_mapping(data: object, section: str) -> dict[str, object]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{section} section in config must be a YAML mapping"
        raise ConfigError(msg)
    return {str(k): v for k, v in data.items()}


# region FUNC_load_config
## @purpose  Parse pins YAML into Config with fail-fast validation
## @io       in: path to pins YAML; out: Config; raises ConfigError on any problem
## @complexity O(n) over YAML document size
def load_config(path: str | Path) -> Config:
    """▶ ┌pins.yaml┐ → ○ yaml.safe_load → ◇ validate tag/digest → ⊕ Config → ⎋"""
    p = Path(path)
    if not p.is_file():
        msg = f"config file not found: {p}"
        raise ConfigError(msg)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in {p}: {exc}"
        raise ConfigError(msg) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        msg = f"config {p} must be a YAML mapping at the top level"
        raise ConfigError(msg)

    canon = _as_mapping(data.get("canon"), "canon")
    tag = canon.get("tag")
    if tag is None or not str(tag).strip():
        msg = f"missing required key: canon.tag in {p}"
        raise ConfigError(msg)
    tag = str(tag).strip()
    if not TAG_RE.match(tag):
        msg = f"invalid canon.tag {tag!r} in {p}: expected v<major>.<minor>.<patch>"
        raise ConfigError(msg)

    digest = canon.get("digest")
    if digest is not None:
        digest = str(digest).strip()
        if not DIGEST_RE.match(digest):
            msg = f"invalid canon.digest {digest!r} in {p}: expected sha256:<64 hex>"
            raise ConfigError(msg)
    else:
        digest = None

    remote = canon.get("remote")
    remote = str(remote).strip() if remote is not None else DEFAULT_CANON_REMOTE

    hermes = _as_mapping(data.get("hermes"), "hermes")
    templates = _as_mapping(data.get("templates"), "templates")
    req = templates.get("requires_instructions_version")

    cfg = Config(
        canon_tag=tag,
        canon_digest=digest,
        hermes_enabled=_as_bool(hermes.get("enabled")),
        roles_as_skills=_as_bool(hermes.get("roles_as_skills")),
        hermes_profile=str(hermes.get("profile") or DEFAULT_HERMES_PROFILE).strip(),
        hermes_emit_dir=str(hermes.get("emit_dir") or DEFAULT_HERMES_EMIT_DIR).strip(),
        requires_instructions_version=str(req).strip() if req is not None else None,
        canon_remote=remote,
    )
    logger.info(
        "[IMP:9][CONFIG][LOADED] %s: canon=%s digest=%s hermes=%s",
        p, cfg.canon_tag, cfg.canon_digest or "none", cfg.hermes_enabled,
    )
    return cfg
# endregion FUNC_load_config
