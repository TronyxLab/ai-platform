# GREP_SUMMARY: manifest-integrity entrypoint-manifest allowed-verbs forbidden-verbs delegates-to AGENTS.md makefile module-targets name-linter module-dictionary
# STRUCTURE: ▶ manifest(session-scoped) → _extract_delegate_paths ∥ _load_makefile_targets ∥ _extract_agents_verbs ∥ _extract_module_verbs ∥ _get_module_makefiles ∥ _resolve_module_targets ∥ _extract_phony_targets ∥ _extract_explicit_targets ∥ _read_included_contents ∥ _get_all_targets ∥ _get_entrypoint_scripts ∥ _load_module_dictionary ∥ _load_name_linter_config → ○ 11 tests
# region MODULE_CONTRACT
## @purpose — Merge gate: manifest ↔ Makefile ↔ AGENTS.md ↔ module targets bidirectional integrity.
##            Replaces test_gate_manifest_parity.py + test_gate_name_linter.py + test_gate_module_targets_manifest.py.
## @scope
##   Direction A (manifest → reality):
##     1. Every delegates_to shell-script path exists on disk
##     2. Every allowed_verbs has a corresponding Makefile phony target
##     3. Every forbidden_scripts name is NOT found in core/ tree
##     4. Every forbidden_directories does NOT exist on disk
##   Direction B (reality → manifest):
##     5. core/AGENTS.md table matched against manifest allowed_verbs
##     6. core/modules/AGENTS.md module targets checked against forbidden_verbs
##   Naming convention (name-linter):
##     7. No Makefile target uses a forbidden verb or unregistered verb
##     8. Module Makefile targets use canonical names from module dictionary
##     9. Every entrypoint script is referenced in manifest delegates_to
##   Module lifecycle (module-targets-manifest):
##     10. Module lifecycle targets registered in manifest module_lifecycle
##     11. No module- prefix in Makefile.common / module.mk
## @input — core/entrypoint-manifest.yaml, Makefile, core/AGENTS.md, core/modules/AGENTS.md,
##          core/modules/*/Makefile, core/Makefile.common, core/templates/module.mk, core/entrypoints/
## @output — pytest assert failures with structured error codes
## @invariants — All test functions are marked @pytest.mark.gate
## @rationale — Merge of 3 gate files into 1 reduces test startup overhead, eliminates duplicate
##              manifest loads via session-scoped fixture.
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)

_MANIFEST_PATH: str = os.path.join(_PROJECT_ROOT, "core", "entrypoint-manifest.yaml")
_MAKEFILE_PATH: str = os.path.join(_PROJECT_ROOT, "Makefile")
_CORE_AGENTS_PATH: str = os.path.join(_PROJECT_ROOT, "core", "AGENTS.md")
_MODULES_AGENTS_PATH: str = os.path.join(_PROJECT_ROOT, "core", "modules", "AGENTS.md")
_MODULES_DIR: str = os.path.join(_PROJECT_ROOT, "core", "modules")
_ENTRYPOINTS_DIR: str = os.path.join(_PROJECT_ROOT, "core", "entrypoints")
_MAKEFILE_COMMON: str = os.path.join(_PROJECT_ROOT, "core", "Makefile.common")
_MODULE_MK: str = os.path.join(_PROJECT_ROOT, "core", "templates", "module.mk")

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────
_CONVENIENCE_TARGETS: set[str] = {"venv", "help", "pre-commit-install", "pre-commit-run"}
_MODULE_SCOPED_VERBS: set[str] = {"build", "logs", "start", "stop"}


# ── Linter config (module-level, loaded from manifest) ────────────────────────
MODULE_DICTIONARY: set[str] | None = None
SYSTEM_EXCEPTIONS: set[str] | None = None
SYSTEM_PREFIXES: tuple[str, ...] | None = None
NAMESPACE_COLLISION_NAMES: tuple[str, ...] | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


# region HELPER_load_manifest
_manifest_cache: dict | None = None


def _load_manifest() -> dict:
    """Load and cache core/entrypoint-manifest.yaml.

    ## @purpose — Single load per session. Cached after first call so the
    ##            session-scoped fixture and module-level init share one read.
    ## @io — ⎋ dict: parsed YAML content
    """
    global _manifest_cache
    if _manifest_cache is None:
        with open(_MANIFEST_PATH) as f:
            _manifest_cache = yaml.safe_load(f)
        logger.info("[IMP:9][_load_manifest] Loaded %d top-level group(s)", len(_manifest_cache))
    return _manifest_cache


# endregion HELPER_load_manifest


# region HELPER_extract_delegate_paths
def _extract_delegate_paths(delegates_to: str) -> list[str]:
    """Extract `.sh` file paths from a delegates_to delegation chain.

    ## @purpose — Isolate filesystem paths from mixed delegation descriptions.
    ## @io — ⇥ delegates_to: str → ⎋ list[str] of relative .sh paths
    ## @complexity — O(n), n = →-segments
    """
    paths: list[str] = []
    for chunk in delegates_to.split("→"):
        chunk = chunk.strip()
        if ".sh" in chunk:
            first_word = chunk.split()[0]
            if "/" in first_word and first_word.endswith(".sh"):
                paths.append(first_word)
    logger.info("[IMP:9][_extract_delegate_paths] Extracted %d path(s) from: %.60s", len(paths), delegates_to)
    return paths


# endregion HELPER_extract_delegate_paths


# region HELPER_load_makefile_targets
def _load_makefile_targets() -> set[str]:
    """Extract phony target names from ALL Makefile `.PHONY:` lines.

    ## @purpose — Get authoritative list of registered make targets.
    ## @io — ⎋ set[str]: all target names across all .PHONY declarations
    ## @complexity — O(N), N = lines in Makefile
    """
    targets: set[str] = set()
    phony_count: int = 0
    with open(_MAKEFILE_PATH) as f:
        for line in f:
            if line.startswith(".PHONY:"):
                phony_count += 1
                tokens = line.split()
                if len(tokens) > 1:
                    targets.update(tokens[1:])
    logger.info(
        "[IMP:9][_load_makefile_targets] Found %d phony targets across %d .PHONY line(s)",
        len(targets),
        phony_count,
    )
    return targets


# endregion HELPER_load_makefile_targets


# region HELPER_extract_agents_verbs
def _extract_agents_verbs(filepath: str, prefix: str = "make") -> set[str]:
    """Extract `` `make <verb>` `` sequences from an AGENTS.md file.

    ## @purpose — Parse canonical operation table to extract target verb names.
    ## @io — ⇥ filepath, prefix → ⎋ set[str] of verb names
    ## @complexity — O(N), N = file lines
    """
    verbs: set[str] = set()
    pattern = re.compile(rf"`{re.escape(prefix)}\s+([\w-]+)`")
    with open(filepath) as f:
        for line in f:
            for m in pattern.finditer(line):
                verbs.add(m.group(1))
    logger.info(
        "[IMP:9][_extract_agents_verbs] Extracted %d verb(s) from %s",
        len(verbs),
        os.path.basename(filepath),
    )
    return verbs


# endregion HELPER_extract_agents_verbs


# region HELPER_extract_module_verbs
def _extract_module_verbs(filepath: str) -> set[str]:
    """Extract backtick-enclosed verb names from markdown list items.

    ## @purpose — Parse module target list from modules/AGENTS.md.
    ## @io — ⇥ filepath → ⎋ set[str] of verb names
    ## @complexity — O(N), N = file lines
    """
    verbs: set[str] = set()
    with open(filepath) as f:
        for line in f:
            m = re.match(r"^\s*-\s+`(\w+)`", line)
            if m:
                verbs.add(m.group(1))
    logger.info(
        "[IMP:9][_extract_module_verbs] Extracted %d module verb(s) from %s",
        len(verbs),
        os.path.basename(filepath),
    )
    return verbs


# endregion HELPER_extract_module_verbs


# region HELPER_get_module_makefiles
def _get_module_makefiles() -> list[str]:
    """Discover all module Makefiles under core/modules/*/Makefile.

    ## @io — ⎋ list[str] of absolute paths to module Makefiles
    ## @complexity — O(N) where N = number of module directories
    """
    if not os.path.isdir(_MODULES_DIR):
        return []
    return sorted(
        [
            os.path.join(_MODULES_DIR, d, "Makefile")
            for d in os.listdir(_MODULES_DIR)
            if os.path.isdir(os.path.join(_MODULES_DIR, d))
            and os.path.isfile(os.path.join(_MODULES_DIR, d, "Makefile"))
        ]
    )


# endregion HELPER_get_module_makefiles


# region HELPER_resolve_module_targets
def _resolve_module_targets(makefile_path: str) -> set[str]:
    """Resolve all targets available to a module Makefile, following includes.

    ## @purpose — Read module Makefile and recursively follow include directives.
    ## @io — ⇥ makefile_path: str → ⎋ set[str] of target names
    ## @complexity — O(N * D) where N = lines, D = include depth
    """
    base_dir: str = os.path.dirname(makefile_path)

    def _extract_phony(text: str) -> set[str]:
        result: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(".PHONY:"):
                parts = stripped[len(".PHONY:") :].strip().split()
                for part in parts:
                    part = part.strip()
                    if part and not part.startswith("$"):
                        result.add(part)
        return result

    def _extract_explicit(text: str) -> set[str]:
        result: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*[:?+]?=", stripped):
                continue
            match = re.match(r"^([a-zA-Z0-9_.\-]+)\s*:", stripped)
            if match:
                target = match.group(1)
                if not target.startswith("$") and target != ".PHONY":
                    result.add(target)
        return result

    def _resolve_include(inc_rel: str, including_file_dir: str) -> str | None:
        candidate = os.path.normpath(os.path.join(base_dir, inc_rel))
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.normpath(os.path.join(including_file_dir, inc_rel))
        if os.path.isfile(candidate):
            return candidate
        return None

    def _read_with_includes(filepath: str, depth: int = 0) -> list[str]:
        if depth > 5:
            return []
        if not os.path.isfile(filepath):
            return []
        with open(filepath) as f:
            text = f.read()
        contents: list[str] = [text]
        including_dir = os.path.dirname(filepath)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("include "):
                inc_rel = stripped[len("include ") :].strip()
                inc_path = _resolve_include(inc_rel, including_dir)
                if inc_path is not None:
                    contents.extend(_read_with_includes(inc_path, depth + 1))
        return contents

    targets: set[str] = set()
    for text in _read_with_includes(makefile_path):
        targets |= _extract_phony(text)
        targets |= _extract_explicit(text)
    return targets


# endregion HELPER_resolve_module_targets


# region HELPERS_name_linter
def _extract_phony_targets(text: str) -> set[str]:
    """Extract target names from .PHONY: declarations in Makefile text."""
    targets: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            parts = stripped[len(".PHONY:") :].strip().split()
            for part in parts:
                part = part.strip()
                if part and not part.startswith("$"):
                    targets.add(part)
    return targets


def _extract_explicit_targets(text: str) -> set[str]:
    """Extract target names from explicit Makefile target definitions."""
    targets: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*[:?+]?=", stripped):
            continue
        match = re.match(r"^([a-zA-Z0-9_.\-]+)\s*:", stripped)
        if match:
            target = match.group(1)
            if not target.startswith("$") and target != ".PHONY":
                targets.add(target)
    return targets


def _read_included_contents(filepath: str, depth: int = 0) -> list[str]:
    """Recursively read content of Makefiles referenced via `include` directives.

    ## @purpose — Follow include directives to resolve template targets.
    ## @io — ⇥ filepath: str, depth: int → ⎋ list[str] of included file contents
    ## @complexity — O(n * d) where n = lines, d = include depth
    """
    if depth > 5:
        logger.warning("[IMP:4][_read_included_contents] Max recursion depth reached at %s", filepath)
        return []
    if not os.path.isfile(filepath):
        return []
    with open(filepath) as f:
        text = f.read()
    contents: list[str] = []
    makefile_dir = os.path.dirname(filepath)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("include "):
            inc_rel = stripped[len("include ") :].strip()
            inc_path = os.path.normpath(os.path.join(makefile_dir, inc_rel))
            if os.path.isfile(inc_path):
                logger.debug("[IMP:7][_read_included_contents] Following include: %s → %s", inc_rel, inc_path)
                with open(inc_path) as inc_f:
                    contents.append(inc_f.read())
                contents.extend(_read_included_contents(inc_path, depth + 1))
            else:
                logger.debug("[IMP:4][_read_included_contents] Include target not found: %s", inc_path)
    return contents


def _get_all_targets(filepath: str) -> set[str]:
    """Get all declared and explicit target names from a Makefile, following includes.

    ## @purpose — Unified extraction combining .PHONY declarations, explicit target
    ##            definitions, and targets inherited from included template Makefiles.
    ## @io — ⇥ filepath: str → ⎋ set[str] of all target names
    """
    with open(filepath) as f:
        text = f.read()
    targets: set[str] = _extract_phony_targets(text)
    targets |= _extract_explicit_targets(text)
    for inc_text in _read_included_contents(filepath):
        targets |= _extract_phony_targets(inc_text)
        targets |= _extract_explicit_targets(inc_text)
    return targets


def _is_system_exception(target: str) -> bool:
    """Check if target is a system exception (allowed without dictionary registration)."""
    if target in SYSTEM_EXCEPTIONS:
        return True
    return bool(target.startswith(SYSTEM_PREFIXES))


def _get_entrypoint_scripts() -> list[str]:
    """Discover all entrypoint shell scripts under core/entrypoints/*.sh.

    ## @io — ⎋ list[str] of sorted absolute paths to .sh scripts
    ## @complexity — O(N) where N = number of .sh files
    """
    if not os.path.isdir(_ENTRYPOINTS_DIR):
        logger.warning("[IMP:4][_get_entrypoint_scripts] Entrypoints directory not found: %s", _ENTRYPOINTS_DIR)
        return []
    return sorted(
        [
            os.path.join(_ENTRYPOINTS_DIR, f)
            for f in os.listdir(_ENTRYPOINTS_DIR)
            if f.endswith(".sh") and os.path.isfile(os.path.join(_ENTRYPOINTS_DIR, f))
        ]
    )


def _is_namespace_collision(target: str) -> bool:
    """Check if target is a bare deploy/build name (namespace collision for modules).

    ## @purpose — Module Makefiles must not define bare deploy/build targets because
    ##            those names belong to the root Makefile.
    ## @io — ⇥ target: str → ⎋ bool
    """
    return target in NAMESPACE_COLLISION_NAMES


def _load_module_dictionary() -> set[str]:
    """Build MODULE_DICTIONARY from manifest: module_lifecycle ∪ system_module_lifecycle ∪ {backup, help}."""
    manifest = _load_manifest()
    module_lifecycle: set[str] = set(manifest.get("module_lifecycle", []))

    # System module lifecycle targets (e.g. install) — different contract from Docker
    system_module_lifecycle = manifest.get("system_module_lifecycle", [])
    system_targets: set[str] = set()
    for entry in system_module_lifecycle:
        if isinstance(entry, dict) and "targets" in entry:
            system_targets.update(entry["targets"].keys())

    module_dict: set[str] = module_lifecycle | system_targets | {"backup", "help"}
    logger.debug(
        "[IMP:8][_load_module_dictionary] Loaded %d targets (Docker: %d, system: %d, extras: 2)",
        len(module_dict),
        len(module_lifecycle),
        len(system_targets),
    )
    return module_dict


def _load_name_linter_config() -> dict:
    """Load name_linter configuration from manifest.

    ## @purpose — Read system_exceptions, system_prefixes, namespace_collision_names
    ##            from the name_linter section of entrypoint-manifest.yaml.
    ## @io — ⎋ dict with keys: system_exceptions, system_prefixes, namespace_collision_names
    """
    manifest = _load_manifest()
    config: dict = manifest.get("name_linter", {})
    logger.debug("[IMP:8][_load_name_linter_config] Loaded name_linter config from manifest")
    return config


def _initialize_linter_config() -> None:
    """Initialize module-level linter constants from manifest (G1.3)."""
    global MODULE_DICTIONARY, SYSTEM_EXCEPTIONS, SYSTEM_PREFIXES, NAMESPACE_COLLISION_NAMES
    MODULE_DICTIONARY = _load_module_dictionary()
    config = _load_name_linter_config()
    SYSTEM_EXCEPTIONS = set(config.get("system_exceptions", ["help", "venv"]))
    SYSTEM_PREFIXES = tuple(config.get("system_prefixes", ["test-", "gate-", "pre-commit-"]))
    NAMESPACE_COLLISION_NAMES = tuple(config.get("namespace_collision_names", ["deploy", "build"]))
    logger.info(
        "[IMP:9][_initialize_linter_config] Initialized: %d module dict, %d exceptions, %d prefixes, %d collisions",
        len(MODULE_DICTIONARY),
        len(SYSTEM_EXCEPTIONS),
        len(SYSTEM_PREFIXES),
        len(NAMESPACE_COLLISION_NAMES),
    )


# endregion HELPERS_name_linter

# Initialize linter config at module import time
_initialize_linter_config()


# ── Session-scoped fixture ────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def manifest():
    """Load entrypoint-manifest.yaml once per test session.

    ## @purpose — Single YAML parse shared across all tests.
    ## @io — ⎋ dict: full manifest content
    """
    return _load_manifest()


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — Manifest → Reality (from manifest_parity)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_delegates_to_paths_exist
## @purpose — Verify every delegates_to shell-script path in manifest exists on disk.
##            FAIL code: MANIFEST_STALE

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_delegates_to_paths_exist(caplog) -> None:
    """Direction A.1-A.2: manifest → delegates_to files exist."""
    # 🧪 TRAP[TEST] · 2026-07-09 · gate/manifest-parity · delegates_to path existence check
    # · Regression: if a delegate script is deleted without updating manifest
    # · Remove if: delegate scripts are verified by another mechanism (e.g. CI pipeline)

    logger.info("[IMP:8][test_delegates_to_paths_exist] === Direction A: manifest → files ===")
    manifest = _load_manifest()

    missing: list[tuple[str, str]] = []
    checked: int = 0

    for group_name, entries in manifest.items():
        if group_name in ("forbidden_directories", "forbidden_scripts", "forbidden_verbs", "allowed_verbs"):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to: str = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str) or not delegates_to:
                continue
            make_target: str = entry.get("make_target", "?")
            rel_paths = _extract_delegate_paths(delegates_to)
            for rel_path in rel_paths:
                checked += 1
                abs_path = os.path.join(_PROJECT_ROOT, rel_path)
                if os.path.exists(abs_path):
                    logger.info("[IMP:9][delegates_to][EXISTS] %s → %s", make_target, rel_path)
                else:
                    logger.warning("[IMP:7][delegates_to][MISSING] %s → %s (not found on disk)", make_target, rel_path)
                    missing.append((make_target, rel_path))

    assert not missing, f"MANIFEST_STALE: {len(missing)} delegates_to path(s) not found:\n" + "\n".join(
        f"  {tgt} → {p}" for tgt, p in missing
    )
    logger.info("[IMP:9][test_delegates_to_paths_exist] Checked %d path(s): ALL EXIST", checked)


# endregion FUNC_test_delegates_to_paths_exist


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_allowed_verbs_match_makefile
## @purpose — Verify every manifest allowed_verbs entry matches a Makefile phony target.
##            FAIL codes: MANIFEST_INCOMPLETE, MANIFEST_STALE
def test_allowed_verbs_match_makefile(caplog) -> None:
    """Direction A.3: manifest allowed_verbs → Makefile phony targets."""
    # 🧪 TRAP[TEST] · 2026-07-10 · gate/manifest-parity · allowed_verbs vs Makefile
    # · Regression: adding a Makefile target without registering in manifest (or vice versa)
    # · Remove if: Makefile is auto-generated from manifest

    logger.info("[IMP:8][test_allowed_verbs_match_makefile] === Direction A.3: manifest → Makefile ===")

    manifest = _load_manifest()
    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    makefile_targets: set[str] = _load_makefile_targets()

    operational_targets: set[str] = makefile_targets - _CONVENIENCE_TARGETS
    root_allowed_verbs: set[str] = allowed_verbs - _MODULE_SCOPED_VERBS

    manifest_not_in_makefile: set[str] = root_allowed_verbs - operational_targets
    makefile_not_in_manifest: set[str] = operational_targets - root_allowed_verbs

    logger.info(
        "[IMP:8][allowed_verbs] %d in manifest (%d root after -module-scoped), %d operational in Makefile",
        len(allowed_verbs),
        len(root_allowed_verbs),
        len(operational_targets),
    )

    if manifest_not_in_makefile:
        logger.warning(
            "[IMP:7][allowed_verbs][MISSING_IN_MAKEFILE] %d verb(s): %s",
            len(manifest_not_in_makefile),
            sorted(manifest_not_in_makefile),
        )
    else:
        logger.info("[IMP:9][allowed_verbs][MAKEFILE_OK] All manifest verbs have Makefile targets")

    if makefile_not_in_manifest:
        logger.warning(
            "[IMP:7][allowed_verbs][MISSING_IN_MANIFEST] %d target(s): %s",
            len(makefile_not_in_manifest),
            sorted(makefile_not_in_manifest),
        )
    else:
        logger.info("[IMP:9][allowed_verbs][MANIFEST_OK] All Makefile targets registered in manifest")

    errors: list[str] = []
    if manifest_not_in_makefile:
        errors.append(
            f"MANIFEST_INCOMPLETE: {len(manifest_not_in_makefile)} verb(s) in manifest "
            f"allowed_verbs but missing from Makefile .PHONY:\n"
            + "\n".join(f"  {v}" for v in sorted(manifest_not_in_makefile))
        )
    if makefile_not_in_manifest:
        errors.append(
            f"MANIFEST_STALE: {len(makefile_not_in_manifest)} Operational Makefile target(s) registered "
            f"in .PHONY but missing from manifest allowed_verbs (convenience targets excluded):\n"
            + "\n".join(f"  {t}" for t in sorted(makefile_not_in_manifest))
        )

    assert not errors, "\n\n".join(errors)
    logger.info(
        "[IMP:9][test_allowed_verbs_match_makefile] %d root allowed verbs ↔ %d targets: SYNC OK",
        len(root_allowed_verbs),
        len(operational_targets),
    )


# endregion FUNC_test_allowed_verbs_match_makefile


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_agents_md_synced_with_manifest
## @purpose — Verify core/AGENTS.md ↔ manifest allowed_verbs and modules/AGENTS.md ↔ forbidden_verbs.
##            FAIL code: DOC_MISMATCH
def test_agents_md_synced_with_manifest(caplog) -> None:
    """Direction B.5-B.6: AGENTS.md tables ↔ manifest."""
    # 🧪 TRAP[TEST] · 2026-07-09 · gate/manifest-parity · AGENTS.md ↔ manifest sync
    # · Regression: AGENTS.md table edited without updating manifest (phantom verbs)
    # · Remove if: AGENTS.md is auto-generated from manifest

    logger.info("[IMP:8][test_agents_md_synced_with_manifest] === Direction B: docs → manifest ===")

    manifest = _load_manifest()
    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    forbidden_verbs: set[str] = set(manifest.get("forbidden_verbs", []))

    agents_verbs: set[str] = _extract_agents_verbs(_CORE_AGENTS_PATH)
    root_agents_verbs: set[str] = agents_verbs - _MODULE_SCOPED_VERBS
    agents_not_in_manifest: set[str] = root_agents_verbs - allowed_verbs
    manifest_not_in_agents: set[str] = allowed_verbs - root_agents_verbs

    logger.info("[IMP:8][agents_md] %d verb(s) in core/AGENTS.md table", len(agents_verbs))
    if agents_not_in_manifest:
        logger.warning(
            "[IMP:7][agents_md][EXTRA_IN_DOCS] %d verb(s) in core/AGENTS.md but not in manifest: %s",
            len(agents_not_in_manifest),
            sorted(agents_not_in_manifest),
        )
    if manifest_not_in_agents:
        logger.warning(
            "[IMP:7][agents_md][DOCS_GAP] %d manifest verb(s) not documented in core/AGENTS.md: %s",
            len(manifest_not_in_agents),
            sorted(manifest_not_in_agents),
        )

    module_verbs: set[str] = _extract_module_verbs(_MODULES_AGENTS_PATH)
    module_forbidden_conflict: set[str] = module_verbs & forbidden_verbs

    logger.info("[IMP:8][modules_agents] %d module verb(s) in core/modules/AGENTS.md", len(module_verbs))
    if module_forbidden_conflict:
        logger.warning(
            "[IMP:7][modules_agents][FORBIDDEN_CONFLICT] %d module verb(s) are in manifest forbidden_verbs: %s",
            len(module_forbidden_conflict),
            sorted(module_forbidden_conflict),
        )
    else:
        logger.info("[IMP:9][modules_agents][NO_CONFLICT] No module verbs conflict with forbidden_verbs")

    errors: list[str] = []
    if agents_not_in_manifest:
        errors.append(
            f"DOC_MISMATCH: {len(agents_not_in_manifest)} verb(s) in core/AGENTS.md table "
            f"but not registered in manifest allowed_verbs:\n"
            + "\n".join(f"  {v}" for v in sorted(agents_not_in_manifest))
        )
    if module_forbidden_conflict:
        errors.append(
            f"DOC_MISMATCH: {len(module_forbidden_conflict)} verb(s) from core/modules/AGENTS.md "
            f"appear in manifest forbidden_verbs:\n" + "\n".join(f"  {v}" for v in sorted(module_forbidden_conflict))
        )
    if manifest_not_in_agents:
        errors.append(
            f"DOC_MISMATCH: {len(manifest_not_in_agents)} manifest verb(s) not documented "
            f"in core/AGENTS.md (bidirectional parity violation):\n"
            + "\n".join(f"  {v}" for v in sorted(manifest_not_in_agents))
        )

    assert not errors, "\n\n".join(errors)
    logger.info(
        "[IMP:9][test_agents_md_synced_with_manifest] core/AGENTS.md ↔ manifest: %d verbs in sync; modules/AGENTS.md ↔ forbidden: 0 conflicts",
        len(allowed_verbs),
    )


# endregion FUNC_test_agents_md_synced_with_manifest


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_forbidden_directories_absent
## @purpose — Verify forbidden directories do NOT exist and forbidden scripts are NOT present.
##            FAIL code: MANIFEST_STALE
def test_forbidden_directories_absent(caplog) -> None:
    """Direction A.4 + B.7: forbidden directories and scripts must not exist."""
    # 🧪 TRAP[TEST] · 2026-07-09 · gate/manifest-parity · forbidden structures absence
    # · Regression: a forbidden script gets re-introduced; forbidden directory re-created
    # · Remove if: forbidden lists are removed from manifest

    logger.info("[IMP:8][test_forbidden_directories_absent] === Forbidden structures check ===")

    manifest = _load_manifest()
    forbidden_dirs: list[str] = manifest.get("forbidden_directories", [])
    forbidden_scripts: list[str] = manifest.get("forbidden_scripts", [])

    logger.info(
        "[IMP:8][forbidden] %d forbidden dir(s), %d forbidden script(s)", len(forbidden_dirs), len(forbidden_scripts)
    )

    found_dirs: list[str] = []
    for rel_dir in forbidden_dirs:
        abs_dir = os.path.join(_PROJECT_ROOT, rel_dir)
        if os.path.isdir(abs_dir):
            logger.warning("[IMP:7][forbidden_dir][EXISTS] %s", rel_dir)
            found_dirs.append(rel_dir)
        else:
            logger.info("[IMP:9][forbidden_dir][ABSENT] %s", rel_dir)

    found_scripts: list[str] = []
    core_dir: str = os.path.join(_PROJECT_ROOT, "core")
    if os.path.isdir(core_dir):
        for root, _dirs, files in os.walk(core_dir):
            for filename in files:
                if filename in forbidden_scripts:
                    rel_path = os.path.relpath(os.path.join(root, filename), _PROJECT_ROOT)
                    logger.warning("[IMP:7][forbidden_script][FOUND] %s", rel_path)
                    found_scripts.append(rel_path)

    if not found_scripts:
        logger.info("[IMP:9][forbidden_scripts][ABSENT] No forbidden scripts found in core/ tree")

    errors: list[str] = []
    if found_dirs:
        errors.append(
            f"MANIFEST_STALE: {len(found_dirs)} forbidden director(ies) still exist on disk:\n"
            + "\n".join(f"  {d}" for d in found_dirs)
        )
    if found_scripts:
        errors.append(
            f"MANIFEST_STALE: {len(found_scripts)} forbidden script(s) still exist in core/:\n"
            + "\n".join(f"  {s}" for s in found_scripts)
        )

    assert not errors, "\n\n".join(errors)
    logger.info(
        "[IMP:9][test_forbidden_directories_absent] %d dir(s) + %d script(s): ALL ABSENT — clean",
        len(forbidden_dirs),
        len(forbidden_scripts),
    )


# endregion FUNC_test_forbidden_directories_absent


# ═══════════════════════════════════════════════════════════════════════════════
# Module verb parity (from manifest_parity)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_module_makefiles_no_deprecated_module_deploy
## @purpose — Verify NO module Makefile (including include chain) contains `module-deploy`.
##            FAIL code: DEPRECATED_TARGET
def test_module_makefiles_no_deprecated_module_deploy(caplog) -> None:
    """Verify no module Makefile (incl. includes) has deprecated `module-deploy`."""
    # 🧪 TRAP[TEST] · 2026-07-10 · gate/manifest-parity · module-deploy absence
    # · Regression: module-deploy re-introduced via include chain or direct definition
    # · Remove if: module-deploy is permanently removed from project vocabulary

    logger.info("[IMP:8][test_no_deprecated_module_deploy] === Module deploy parity check ===")

    module_makefiles = _get_module_makefiles()
    logger.info("[IMP:8][module_makefiles] Found %d module Makefiles", len(module_makefiles))

    found_deprecated: list[str] = []
    for mf_path in module_makefiles:
        targets = _resolve_module_targets(mf_path)
        module_name = os.path.basename(os.path.dirname(mf_path))
        if "module-deploy" in targets:
            logger.warning("[IMP:7][DEPRECATED] %s has module-deploy", module_name)
            found_deprecated.append(module_name)
        else:
            logger.info("[IMP:9][CLEAN] %s — no module-deploy", module_name)

    assert not found_deprecated, (
        f"DEPRECATED_TARGET: {len(found_deprecated)} module(s) still define 'module-deploy':\n"
        + "\n".join(f"  {m}" for m in found_deprecated)
    )
    logger.info("[IMP:9][test_no_deprecated_module_deploy] ALL PASS — no module-deploy in any module")


# endregion FUNC_test_module_makefiles_no_deprecated_module_deploy


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_module_makefiles_have_required_module_targets
## @purpose — Verify EVERY module Makefile has `module-up`, `module-status`, `build`.
##            FAIL code: MISSING_TARGET
def _get_install_type(module_dir: str) -> str:
    """Read install_type from module.yaml. Defaults to 'docker' if absent."""
    module_yaml_path = os.path.join(module_dir, "module.yaml")
    if os.path.exists(module_yaml_path):
        with open(module_yaml_path) as f:
            module_yaml = yaml.safe_load(f)
            if module_yaml and "install_type" in module_yaml:
                return module_yaml["install_type"]
    return "docker"


def test_module_makefiles_have_required_module_targets(caplog) -> None:
    """Verify every module Makefile (incl. includes) has required targets per install_type."""
    # 🧪 TRAP[TEST] · 2026-07-10 · gate/manifest-parity · module-up + module-status presence
    # · Regression: module-up or module-status removed from include chain
    # · Remove if: module-up and module-status are permanently frozen
    # · Updated 2026-07-18 · system-module contract (D3): check install_type for required targets

    logger.info("[IMP:8][test_required_module_targets] === Module required targets check ===")

    module_makefiles = _get_module_makefiles()
    logger.info("[IMP:8][module_makefiles] Found %d module Makefiles", len(module_makefiles))

    errors: list[str] = []

    for mf_path in module_makefiles:
        targets = _resolve_module_targets(mf_path)
        module_dir = os.path.dirname(mf_path)
        module_name = os.path.basename(module_dir)
        install_type = _get_install_type(module_dir)

        if install_type == "system":
            # System modules require: install, status, restart, logs (module-system.mk contract)
            for req in ("install", "status", "restart", "logs"):
                if req not in targets:
                    errors.append(f"MISSING_TARGET: System module '{module_name}' missing '{req}'")
                    logger.warning("[IMP:7][MISSING] %s — missing %s", module_name, req)
                else:
                    logger.info("[IMP:9][OK] %s — has %s", module_name, req)

            # Docker targets forbidden in system modules
            for fbd in ("up", "build", "backup", "down", "start", "stop"):
                if fbd in targets:
                    errors.append(f"FORBIDDEN_TARGET: System module '{module_name}' has Docker target '{fbd}'")
                    logger.warning("[IMP:7][FORBIDDEN] %s — has Docker target %s", module_name, fbd)
        else:
            # Docker modules require: up, status, build (module.mk contract)
            if "up" not in targets:
                errors.append(f"MISSING_TARGET: Docker module '{module_name}' missing 'up'")
                logger.warning("[IMP:7][MISSING] %s — missing up", module_name)
            else:
                logger.info("[IMP:9][OK] %s — has up", module_name)

            if "status" not in targets:
                errors.append(f"MISSING_TARGET: Docker module '{module_name}' missing 'status'")
                logger.warning("[IMP:7][MISSING] %s — missing status", module_name)
            else:
                logger.info("[IMP:9][OK] %s — has status", module_name)

            if "build" not in targets:
                errors.append(f"MISSING_TARGET: Docker module '{module_name}' missing 'build'")
                logger.warning("[IMP:7][MISSING] %s — missing build", module_name)
            else:
                logger.info("[IMP:9][OK] %s — has build", module_name)

    assert not errors, "\n\n".join(errors)
    logger.info(
        "[IMP:9][test_required_module_targets] ALL PASS — all %d modules have required targets per install_type",
        len(module_makefiles),
    )


# endregion FUNC_test_module_makefiles_have_required_module_targets


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — Name linter (from name_linter)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-09 · gate/name-linter · forbidden verbs + unregistered verbs in root & module Makefiles
def test_no_forbidden_verbs_in_makefiles(caplog) -> None:
    """Validate that no Makefile target uses a forbidden verb or an unregistered verb."""
    manifest = _load_manifest()
    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    forbidden_verbs: set[str] = set(manifest.get("forbidden_verbs", []))
    logger.info(
        "[IMP:8][test_no_forbidden_verbs_in_makefiles] Manifest loaded: %d allowed, %d forbidden",
        len(allowed_verbs),
        len(forbidden_verbs),
    )

    errors: list[str] = []
    root_targets = _get_all_targets(_MAKEFILE_PATH)
    logger.info("[IMP:8][test_no_forbidden_verbs_in_makefiles] Root Makefile: %d targets", len(root_targets))

    for target in sorted(root_targets):
        if target in forbidden_verbs:
            msg = f"FORBIDDEN_VERB: Root Makefile target '{target}' is in forbidden_verbs {sorted(forbidden_verbs)}"
            errors.append(msg)
            logger.error("[IMP:9][test_no_forbidden_verbs_in_makefiles] %s", msg)
        if target not in allowed_verbs and not _is_system_exception(target):
            msg = (
                f"UNKNOWN_VERB: Root Makefile target '{target}' "
                f"is not in allowed_verbs and not a system exception "
                f"(test-*, gate-*, pre-commit-*, help, venv)"
            )
            errors.append(msg)
            logger.error("[IMP:9][test_no_forbidden_verbs_in_makefiles] %s", msg)

    for mf in _get_module_makefiles():
        module_targets = _get_all_targets(mf)
        for target in sorted(module_targets):
            if target in forbidden_verbs:
                msg = (
                    f"FORBIDDEN_VERB: Module '{os.path.basename(os.path.dirname(mf))}' Makefile target "
                    f"'{target}' is in forbidden_verbs {sorted(forbidden_verbs)}"
                )
                errors.append(msg)
                logger.error("[IMP:9][test_no_forbidden_verbs_in_makefiles] %s", msg)

    if errors:
        logger.error("[IMP:9][test_no_forbidden_verbs_in_makefiles] %d violation(s) found", len(errors))
        pytest.fail("\n".join(errors))

    logger.info("[IMP:9][test_no_forbidden_verbs_in_makefiles] ALL PASS — no forbidden verbs, all targets registered")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-09 · gate/name-linter · module Makefile targets must use canonical names, no bare deploy/build
def test_module_targets_use_canonical_names(caplog) -> None:
    """Validate that module Makefile targets use canonical names from the module dictionary."""
    module_makefiles = _get_module_makefiles()
    logger.info("[IMP:8][test_module_targets_use_canonical_names] Found %d module Makefiles", len(module_makefiles))

    manifest = _load_manifest()
    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    errors: list[str] = []
    for mf in module_makefiles:
        module_name = os.path.basename(os.path.dirname(mf))
        targets = _get_all_targets(mf)
        logger.debug(
            "[IMP:7][test_module_targets_use_canonical_names] Module '%s': %d targets", module_name, len(targets)
        )

        for target in sorted(targets):
            if _is_namespace_collision(target):
                msg = (
                    f"NAMESPACE_COLLISION: Module '{module_name}' has target "
                    f"'{target}' which conflicts with root Makefile namespace — "
                    f"use 'module-{target}' instead"
                )
                errors.append(msg)
                logger.error("[IMP:9][test_module_targets_use_canonical_names] %s", msg)
            elif target not in MODULE_DICTIONARY and target not in allowed_verbs and not _is_system_exception(target):
                msg = (
                    f"NOT_IN_DICTIONARY: Module '{module_name}' has target "
                    f"'{target}' which is not in the module dictionary "
                    f"{sorted(MODULE_DICTIONARY)}, not in allowed_verbs, "
                    f"and not a system exception"
                )
                errors.append(msg)
                logger.error("[IMP:9][test_module_targets_use_canonical_names] %s", msg)

    if errors:
        logger.error("[IMP:9][test_module_targets_use_canonical_names] %d violation(s) found", len(errors))
        pytest.fail("\n".join(errors))

    logger.info(
        "[IMP:9][test_module_targets_use_canonical_names] ALL PASS — all %d module Makefiles use canonical names",
        len(module_makefiles),
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-09 · gate/name-linter · entrypoint scripts must be registered in manifest delegates_to
def test_entrypoint_names_match_manifest(caplog) -> None:
    """Validate that every entrypoint script in core/entrypoints/ is referenced in the manifest."""
    manifest = _load_manifest()

    delegates_to_values: set[str] = set()
    for group_val in manifest.values():
        if isinstance(group_val, list):
            for entry in group_val:
                if isinstance(entry, dict) and "delegates_to" in entry:
                    delegates_to_values.add(entry["delegates_to"])

    logger.info(
        "[IMP:8][test_entrypoint_names_match_manifest] Manifest has %d delegates_to entries", len(delegates_to_values)
    )

    scripts = _get_entrypoint_scripts()
    logger.info("[IMP:8][test_entrypoint_names_match_manifest] Found %d entrypoint scripts", len(scripts))

    errors: list[str] = []
    for script_path in scripts:
        script_name = os.path.basename(script_path)
        referenced = any(script_name in dt for dt in delegates_to_values)
        if not referenced:
            msg = (
                f"UNREGISTERED_ENTRYPOINT: '{script_name}' exists at core/entrypoints/"
                f"{script_name} but is not referenced in any manifest delegates_to field"
            )
            errors.append(msg)
            logger.error("[IMP:9][test_entrypoint_names_match_manifest] %s", msg)
        else:
            logger.debug("[IMP:7][test_entrypoint_names_match_manifest] '%s' registered in manifest", script_name)

    if errors:
        logger.error("[IMP:9][test_entrypoint_names_match_manifest] %d violation(s) found", len(errors))
        pytest.fail("\n".join(errors))

    logger.info(
        "[IMP:9][test_entrypoint_names_match_manifest] ALL PASS — all %d entrypoint scripts are registered in manifest",
        len(scripts),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS — Module lifecycle in manifest (from module_targets_manifest)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-15 · gate/module-targets-manifest · Регресс: module_lifecycle таргеты удалены из entrypoint-manifest.yaml
def test_module_targets_in_manifest(caplog) -> None:
    """Verify module lifecycle targets are registered in manifest module_lifecycle."""
    manifest = _load_manifest()
    module_lifecycle = manifest.get("module_lifecycle", [])

    for target in ("build", "up"):
        assert target in module_lifecycle, (
            f"Target '{target}' missing from entrypoint-manifest.yaml module_lifecycle. Current: {module_lifecycle}"
        )

    for verb in ("build", "logs", "start", "stop"):
        assert verb in module_lifecycle, (
            f"Verb '{verb}' missing from entrypoint-manifest.yaml module_lifecycle. Current: {module_lifecycle}"
        )

    allowed_verbs = manifest.get("allowed_verbs", [])
    assert "up" in allowed_verbs, (
        f"Verb 'up' missing from entrypoint-manifest.yaml allowed_verbs. Current: {allowed_verbs}"
    )

    logger.info("[IMP:9][test_module_targets_in_manifest] ALL PASS — module lifecycle targets registered")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-15 · gate/module-targets-manifest · Регресс: module- префикс восстановлен в Makefile.common или module.mk
def test_no_module_prefix(caplog) -> None:
    """Verify no module- prefix targets in Makefile.common and module.mk."""
    for name, path in [("Makefile.common", _MAKEFILE_COMMON), ("module.mk", _MODULE_MK)]:
        with open(path) as f:
            content = f.read()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("module-") and ":" in stripped:
                target = stripped.split(":")[0].strip()
                pytest.fail(
                    f"{name}: target '{target}' has deprecated 'module-' prefix. "
                    f"Use target name without prefix (e.g. 'build' instead of 'module-build')."
                )

    logger.info("[IMP:9][test_no_module_prefix] ALL PASS — no module- prefix in Makefile.common or module.mk")
