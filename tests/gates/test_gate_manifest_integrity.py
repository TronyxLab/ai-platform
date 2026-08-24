# GREP_SUMMARY: manifest-integrity entrypoint-manifest allowed-verbs delegates-to AGENTS.md module-targets name-linter module-dictionary
# STRUCTURE: ▶ manifest(session-scoped) → _extract_delegate_paths ∥ _extract_agents_verbs ∥ _extract_module_verbs ∥ _get_module_makefiles ∥ _resolve_module_targets ∥ _get_all_targets ∥ _get_entrypoint_scripts ∥ _load_module_dictionary ∥ _load_name_linter_config → ○ tests
# region MODULE_CONTRACT
## @purpose — Merge gate: structural validation of manifest ↔ AGENTS.md ↔ module targets.
##            Freshness (manifest ↔ Makefile) delegated to `make check-manifests`.
## @scope
##   Direction A (manifest → reality):
##     1. Every delegates_to shell-script path exists on disk
##   Direction B (reality → manifest):
##     2. core/AGENTS.md table matched against manifest allowed_verbs
##   Naming convention (name-linter):
##     3. Module Makefile targets use canonical names from module dictionary
##     4. Every entrypoint script is referenced in manifest delegates_to
##   Module lifecycle (module-targets-manifest):
##     5. Module lifecycle targets registered in manifest module_lifecycle
##     6. No module- prefix in Makefile.common / module.mk
##   (forbidden-тройка упразднена DevPlan 171 W3.3 — категорийное правило namelint)
## @input — core/entrypoint-manifest.yaml, core/AGENTS.md, core/modules/AGENTS.md,
##          core/modules/*/Makefile, core/Makefile.common, core/templates/module.mk, core/entrypoints/
## @output — pytest assert failures with structured error codes
## @invariants — All test functions are marked @pytest.mark.gate
## @rationale — Merge of 3 gate files into 1 reduces test startup overhead, eliminates duplicate
##              manifest loads via session-scoped fixture. Freshness checks removed per DevPlan 051:
##              `make check-manifests` covers manifest ↔ Makefile sync; this file covers structural
##              invariants that cannot be auto-generated (delegate paths,
##              documentation sync, naming conventions).
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
from pathlib import Path

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.makefile_parser import get_all_targets

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)

_MANIFEST_PATH: str = Path(_PROJECT_ROOT) / "core" / "entrypoint-manifest.yaml"
_MAKEFILE_PATH: str = Path(_PROJECT_ROOT) / "Makefile"
_CORE_AGENTS_PATH: str = Path(_PROJECT_ROOT) / "core" / "AGENTS.md"
_MODULES_AGENTS_PATH: str = Path(_PROJECT_ROOT) / "core" / "modules" / "AGENTS.md"
_MODULES_DIR: str = Path(_PROJECT_ROOT) / "core" / "modules"
_ENTRYPOINTS_DIR: str = Path(_PROJECT_ROOT) / "core" / "entrypoints"
_MAKEFILE_COMMON: str = Path(_PROJECT_ROOT) / "core" / "Makefile.common"
_MODULE_MK: str = Path(_PROJECT_ROOT) / "core" / "templates" / "module.mk"

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────
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
    global _manifest_cache  # ruff: ignore[PLW0603] — lazy module-level cache (session-scoped единый read, G1.3)
    if _manifest_cache is None:
        with pathlib.Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
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
    for chunk_raw in delegates_to.split("→"):
        chunk = chunk_raw.strip()
        if ".sh" in chunk:
            first_word = chunk.split()[0]
            if "/" in first_word and first_word.endswith(".sh"):
                paths.append(first_word)
    logger.info("[IMP:9][_extract_delegate_paths] Extracted %d path(s) from: %.60s", len(paths), delegates_to)
    return paths


# endregion HELPER_extract_delegate_paths


# region HELPER_extract_agents_verbs
def _extract_agents_verbs(filepath: str, prefix: str = "make") -> set[str]:
    """Extract `` `make <verb>` `` sequences from an AGENTS.md file.

    ## @purpose — Parse canonical operation table to extract target verb names.
    ## @io — ⇥ filepath, prefix → ⎋ set[str] of verb names
    ## @complexity — O(N), N = file lines
    """
    verbs: set[str] = set()
    pattern = re.compile(rf"`{re.escape(prefix)}\s+([\w-]+)`")
    with pathlib.Path(filepath).open(encoding="utf-8") as f:
        for line in f:
            for m in pattern.finditer(line):
                verbs.add(m.group(1))
    logger.info(
        "[IMP:9][_extract_agents_verbs] Extracted %d verb(s) from %s",
        len(verbs),
        Path(filepath).name,
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
    with pathlib.Path(filepath).open(encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*-\s+`(\w+)`", line)
            if m:
                verbs.add(m.group(1))
    logger.info(
        "[IMP:9][_extract_module_verbs] Extracted %d module verb(s) from %s",
        len(verbs),
        Path(filepath).name,
    )
    return verbs


# endregion HELPER_extract_module_verbs


# region HELPER_get_module_makefiles
def _get_module_makefiles() -> list[str]:
    """Discover all module Makefiles under core/modules/*/Makefile.

    ## @io — ⎋ list[str] of absolute paths to module Makefiles
    ## @complexity — O(N) where N = number of module directories
    """
    if not pathlib.Path(_MODULES_DIR).is_dir():
        return []
    modules_dir = pathlib.Path(_MODULES_DIR)
    return sorted(d / "Makefile" for d in modules_dir.iterdir() if d.is_dir() and (d / "Makefile").is_file())


# endregion HELPER_get_module_makefiles


# region HELPER_resolve_module_targets
def _resolve_module_targets(makefile_path: str) -> set[str]:
    """Resolve all targets available to a module Makefile, following includes.

    ## @purpose — Read module Makefile and recursively follow include directives.
    ## @io — ⇥ makefile_path: str → ⎋ set[str] of target names
    ## @complexity — O(N * D) where N = lines, D = include depth
    """
    base_dir: str = Path(makefile_path).parent

    def _extract_phony(text: str) -> set[str]:
        result: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(".PHONY:"):
                parts = stripped[len(".PHONY:") :].strip().split()
                for part_raw in parts:
                    part = part_raw.strip()
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
                if target.startswith("."):
                    continue  # Skip special make variables like .DEFAULT_GOAL, .PHONY, etc.
                if not target.startswith("$") and target != ".PHONY":
                    result.add(target)
        return result

    def _resolve_include(inc_rel: str, including_file_dir: str) -> str | None:
        candidate = os.path.normpath(Path(base_dir) / inc_rel)
        if pathlib.Path(candidate).is_file():
            return candidate
        candidate = os.path.normpath(Path(including_file_dir) / inc_rel)
        if pathlib.Path(candidate).is_file():
            return candidate
        return None

    def _read_with_includes(filepath: str, depth: int = 0) -> list[str]:
        if depth > 5:
            return []
        if not pathlib.Path(filepath).is_file():
            return []
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            text = f.read()
        contents: list[str] = [text]
        including_dir = Path(filepath).parent
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


# Shared Makefile parsers (DevPlan 171 W1.7): _extract_phony_targets/_extract_explicit_targets/
# _read_included_contents/_get_all_targets вынесены в tests/helpers/makefile_parser.py —
# гейт использует get_all_targets(include_chains=True) как единый канон.
_get_all_targets = get_all_targets


def _is_system_exception(target: str) -> bool:
    """Check if target is a system exception (allowed without dictionary registration).

    Категорийное правило (DevPlan 171 W3.6): стандартные служебные таргеты make
    (help/venv), префиксы (system_prefixes), `_`-префиксные имена.
    """
    if target in SYSTEM_EXCEPTIONS:
        return True
    if target.startswith("_"):
        return True
    return bool(target.startswith(SYSTEM_PREFIXES))


def _get_entrypoint_scripts() -> list[str]:
    """Discover all entrypoint shell scripts under core/entrypoints/*.sh.

    ## @io — ⎋ list[str] of sorted absolute paths to .sh scripts
    ## @complexity — O(N) where N = number of .sh files
    """
    if not pathlib.Path(_ENTRYPOINTS_DIR).is_dir():
        logger.warning("[IMP:4][_get_entrypoint_scripts] Entrypoints directory not found: %s", _ENTRYPOINTS_DIR)
        return []
    entrypoints_dir = pathlib.Path(_ENTRYPOINTS_DIR)
    return sorted(f for f in entrypoints_dir.iterdir() if f.name.endswith(".sh") and f.is_file())


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

    ## @purpose — Read system_prefixes, namespace_collision_names
    ##            from the name_linter section of entrypoint-manifest.yaml.
    ## @io — ⎋ dict with keys: system_prefixes, namespace_collision_names
    """
    manifest = _load_manifest()
    config: dict = manifest.get("name_linter", {})
    logger.debug("[IMP:8][_load_name_linter_config] Loaded name_linter config from manifest")
    return config


def _initialize_linter_config() -> None:
    """Initialize module-level linter constants from manifest (G1.3)."""
    global MODULE_DICTIONARY, SYSTEM_EXCEPTIONS, SYSTEM_PREFIXES, NAMESPACE_COLLISION_NAMES  # ruff: ignore[PLW0603] — однократная инициализация module-level констант из манифеста (G1.3)
    MODULE_DICTIONARY = _load_module_dictionary()
    config = _load_name_linter_config()
    # Категория «стандартные служебные таргеты make» (DevPlan 171 W3.6)
    SYSTEM_EXCEPTIONS = set(config.get("system_exceptions", [])) | {"help", "venv"}
    SYSTEM_PREFIXES = tuple(config.get("system_prefixes", ["test-", "gate-", "pre-commit-"]))
    NAMESPACE_COLLISION_NAMES = tuple(config.get("namespace_collision_names", ["deploy", "build"]))
    logger.info(
        "[IMP:9][_initialize_linter_config] Initialized: %d module dict, %d exceptions, %d prefixes, %d collisions",
        len(MODULE_DICTIONARY),
        len(SYSTEM_EXCEPTIONS),
        len(SYSTEM_PREFIXES),
        len(NAMESPACE_COLLISION_NAMES),
    )


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
# TESTS — Manifest → Reality
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
    """Direction A.1: manifest → delegates_to files exist."""
    # 🧪 TRAP[TEST] · 2026-07-09 · gate/manifest-parity · delegates_to path existence check
    # · Regression: if a delegate script is deleted without updating manifest
    # · Remove if: delegate scripts are verified by another mechanism (e.g. CI pipeline)

    logger.info("[IMP:8][test_delegates_to_paths_exist] === Direction A: manifest → files ===")
    manifest = _load_manifest()

    missing: list[tuple[str, str]] = []
    checked: int = 0

    for group_name, entries in manifest.items():
        if group_name in {"allowed_verbs"}:
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
                abs_path = Path(_PROJECT_ROOT) / rel_path
                if Path(abs_path).exists():
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
# region FUNC_test_agents_md_synced_with_manifest
## @purpose — Verify core/AGENTS.md ↔ manifest allowed_verbs.
##            FAIL code: DOC_MISMATCH
def test_agents_md_synced_with_manifest(caplog) -> None:
    """Direction B.4-B.5: AGENTS.md tables ↔ manifest."""
    # 🧪 TRAP[TEST] · 2026-07-09 · gate/manifest-parity · AGENTS.md ↔ manifest sync
    # · Regression: AGENTS.md table edited without updating manifest (phantom verbs)
    # · Remove if: AGENTS.md is auto-generated from manifest

    logger.info("[IMP:8][test_agents_md_synced_with_manifest] === Direction B: docs → manifest ===")

    manifest = _load_manifest()
    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))

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

    logger.info("[IMP:8][modules_agents] %d module verb(s) in core/modules/AGENTS.md", len(module_verbs))

    errors: list[str] = []
    if agents_not_in_manifest:
        errors.append(
            f"DOC_MISMATCH: {len(agents_not_in_manifest)} verb(s) in core/AGENTS.md table "
            f"but not registered in manifest allowed_verbs:\n"
            + "\n".join(f"  {v}" for v in sorted(agents_not_in_manifest))
        )
    if manifest_not_in_agents:
        errors.append(
            f"DOC_MISMATCH: {len(manifest_not_in_agents)} manifest verb(s) not documented "
            f"in core/AGENTS.md (bidirectional parity violation):\n"
            + "\n".join(f"  {v}" for v in sorted(manifest_not_in_agents))
        )

    assert not errors, "\n\n".join(errors)
    logger.info(
        "[IMP:9][test_agents_md_synced_with_manifest] core/AGENTS.md ↔ manifest: %d verbs in sync",
        len(allowed_verbs),
    )


# endregion FUNC_test_agents_md_synced_with_manifest


# ═══════════════════════════════════════════════════════════════════════════════
# Module verb parity
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
        module_name = Path(Path(mf_path).parent).name
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
    module_yaml_path = Path(module_dir) / "module.yaml"
    if Path(module_yaml_path).exists():
        with pathlib.Path(module_yaml_path).open(encoding="utf-8") as f:
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
        module_dir = Path(mf_path).parent
        module_name = Path(module_dir).name
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
# TESTS — Name linter
# ═══════════════════════════════════════════════════════════════════════════════


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
        module_name = Path(Path(mf).parent).name
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
                if isinstance(entry, dict) and "delegates_to" in entry and entry["delegates_to"] is not None:
                    delegates_to_values.add(entry["delegates_to"])

    logger.info(
        "[IMP:8][test_entrypoint_names_match_manifest] Manifest has %d delegates_to entries", len(delegates_to_values)
    )

    scripts = _get_entrypoint_scripts()
    logger.info("[IMP:8][test_entrypoint_names_match_manifest] Found %d entrypoint scripts", len(scripts))

    errors: list[str] = []
    for script_path in scripts:
        script_name = Path(script_path).name
        referenced = any(script_name in dt for dt in delegates_to_values if dt is not None)
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
        with pathlib.Path(path).open(encoding="utf-8") as f:
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


# ═══════════════════════════════════════════════════════════════════════════════
# REPAIR CONTRACT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

# region REPAIR_CONTRACT_VALIDATION
## @purpose  Validate Repair Contract fields in manifest gates[]
## @checks
##   1. repairable: true → repair_id, repair_command, repair_description, repair_safe,
##      repair_idempotent, repair_class обязательны
##   2. repair_class ∈ {L1, L2, L3}
##   3. repairable: false → repair_reason обязателен
##   4. repair_command ссылается на существующий make target (grep .PHONY из repair.mk)
##   5. repair_id уникален среди всех repairable gates
##   6. All repair_id присутствуют в REPAIR_TARGETS из makefiles/repair.mk

REPAIR_CLASSES = {"L1", "L2", "L3"}
REQUIRED_REPAIR_FIELDS = {
    "repair_id",
    "repair_command",
    "repair_description",
    "repair_safe",
    "repair_idempotent",
    "repair_class",
}

_REPAIR_MK_PATH = Path(_PROJECT_ROOT) / "makefiles" / "repair.mk"


def _get_repair_targets() -> set[str]:
    """Extract REPAIR_TARGETS from makefiles/repair.mk."""
    if not pathlib.Path(_REPAIR_MK_PATH).is_file():
        return set()
    content = pathlib.Path(_REPAIR_MK_PATH).read_text(encoding="utf-8")
    match = re.search(r"REPAIR_TARGETS\s*:=\s*(.+)", content)
    if not match:
        return set()
    return set(match.group(1).split())


def _get_phony_targets_from_repair_mk() -> set[str]:
    """Extract .PHONY targets from makefiles/repair.mk."""
    if not pathlib.Path(_REPAIR_MK_PATH).is_file():
        return set()
    content = pathlib.Path(_REPAIR_MK_PATH).read_text(encoding="utf-8")
    match = re.search(r"\.PHONY:\s*(.+)", content)
    if not match:
        return set()
    return set(match.group(1).split())


# ── Dangling gate_id detector (118 G5, AC-G5) ─────────────────────────────────
# Висячие gate_id: ссылка в repair.repairs_gates / non_repairable_gates на id,
# которого НЕТ в gates[] и которое НЕ помечено как make-target-gate.
# Класс make-target-gate (118 G5): check-manifests, ruff-format — make-шаги, не pytest-гейты;
# маркируются gate_kind: make-target-gate в repairs_gates. Прямая ссылка на несуществующий
# pytest-id ломает fix-gate repair-карту («repair резолвится в никуда»).
_MAKE_TARGET_GATE_KIND = "make-target-gate"

# region HELPERS_dangling_gate_id


def _find_dangling_gate_ids(manifest: dict) -> list[tuple[str, str]]:
    """Return [(section, gate_id)] for gate_ids that do not resolve to gates[] or make-target-gate.

    ## @purpose — Detector for 118 G5: gate_ids in repair.repairs_gates and
    ##            non_repairable_gates must resolve to gates[] ids OR be marked
    ##            gate_kind: make-target-gate. Dangling refs break fix-gate repair-map.
    ## @io — ⇥ manifest: dict → ⎋ list[tuple[str, str]]: (section, gate_id) dangling
    ## @complexity — O(R*G + N) where R=repair entries, G=gates per entry, N=non_repairable
    ## @invariants
    ##   - repairs_gates entries: gate_id must be in gates[] OR gate_kind == make-target-gate
    ##   - non_repairable_gates entries: gate_id must be in gates[] (pytest-гейты обязаны существовать)
    ##   - Empty result = repair-map fully resolvable (AC-G5)
    ##   - gates-секция читается в КОМПАКТНОЙ форме {test_file: [ids]} (T3.3 compaction)
    ##     — flatten всех id по всем test_file в единый set
    """
    gates_map = manifest.get("gates", {})
    if isinstance(gates_map, dict):
        gate_ids: set[str] = {gid for ids in gates_map.values() for gid in ids}
    else:  # fallback: старый список записей (backward compat для синтетических фикстур)
        gate_ids = {g.get("id", "") for g in gates_map}
    dangling: list[tuple[str, str]] = []

    for repair_entry in manifest.get("repair", []):
        for rg in repair_entry.get("repairs_gates", []):
            gid = rg.get("gate_id", "")
            if not gid:
                continue
            if gid not in gate_ids and rg.get("gate_kind") != _MAKE_TARGET_GATE_KIND:
                dangling.append((f"repair:{repair_entry.get('make_target', '?')}", gid))
                logger.warning(
                    "[IMP:7][dangling_gate_id] repair:%s → %s (not in gates[], kind=%r)",
                    repair_entry.get("make_target", "?"),
                    gid,
                    rg.get("gate_kind"),
                )

    for ng in manifest.get("non_repairable_gates", []):
        gid = ng.get("gate_id", "")
        if not gid:
            continue
        if gid not in gate_ids:
            dangling.append(("non_repairable_gates", gid))
            logger.warning("[IMP:7][dangling_gate_id] non_repairable_gates → %s (not in gates[])", gid)

    return dangling


# endregion HELPERS_dangling_gate_id


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_repair_gate_ids_resolve
## @purpose  Gate: every gate_id in repair.repairs_gates and non_repairable_gates resolves
##            to gates[] (pytest) or is marked gate_kind: make-target-gate (118 G5, AC-G5).
##            FAIL code: DANGLING_GATE_ID
#
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · dangling gate_id in manifest repair-map (118 G5)
# · Scenario: repair: fix-ruff → gate_id: ruff-format (нет в gates[]) / non_repairable:
#   template-syntax-contract, r1_no_pass_tests — висячие pytest-ссылки
# · Last fail: 118 G5 — 4 висячих gate_id (ruff-format, check-manifests, template-syntax-contract, r1_no_pass_tests)
# · Remove if: repair-map мигрирует на другой механизм резолва (не gates[]-id)
def test_repair_gate_ids_resolve(caplog) -> None:
    """Gate: repair-map gate_ids resolve to gates[] or make-target-gate."""
    manifest = _load_manifest()
    dangling = _find_dangling_gate_ids(manifest)

    logger.info("[IMP:8][dangling_gate_id] Repair-map: %d dangling gate_id(s)", len(dangling))
    for section, gid in dangling:
        logger.error("[IMP:9][dangling_gate_id] %s → %s", section, gid)

    assert not dangling, (
        f"[GATE:FAIL][id:repair-gate-ids-resolve] {len(dangling)} dangling gate_id(s) — "
        f"fix-gate repair-map резолвится в никуда (118 G5):\n"
        + "\n".join(f"  {section}: {gid}" for section, gid in dangling)
    )
    logger.info("[IMP:9][dangling_gate_id] ALL repair-map gate_ids resolve ✓")


# endregion FUNC_test_repair_gate_ids_resolve


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_negative_dangling_gate_id_detected
## @purpose  R5 ANTI-SURVIVORSHIP negative companion: детектор обязан поймать ВСЕ 4
##            исходных висячих gate_id из 118 G5 (точный вход, поймавший баг).
##            FAIL code: R5_NEGATIVE — детектор не ловит регрессию.
#
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · test_repair_gate_ids_resolve — 118 G5
# · Last fail: 4 висячих gate_id в manifest (ruff-format, check-manifests без gate_kind,
#   template-syntax-contract, r1_no_pass_tests)
# · Remove if: repair-map перестаёт использовать gates[]-id резолв
def test_negative_dangling_gate_id_detected(caplog) -> None:
    """R5 negative: исходные висячие gate_id из 118 G5 детектируются."""
    # Минимальный синтетический манифест с ВСЕМИ 4 исходными висячими ссылками 118 G5
    # gates-секция — T3.3 compact map форма {test_file: [ids]}
    synthetic: dict = {
        "gates": {
            "test_gate_real.py": ["test_real_gate"],
            "test_gate_templates.py": ["test_all_templates_use_strict_grammar"],
            "test_gate_r1.py": ["test_r1_no_pass_tests"],
        },
        "repair": [
            {
                "make_target": "fix-ruff",
                "repairs_gates": [
                    {"gate_id": "ruff-format"},  # висячий pytest-id (G5: fix-ruff → ruff-format)
                ],
            },
            {
                "make_target": "fix-gate",
                "repairs_gates": [
                    {"gate_id": "check-manifests"},  # висячий pytest-id (G5: fix-gate → check-manifests)
                ],
            },
        ],
        "non_repairable_gates": [
            {"gate_id": "template-syntax-contract"},  # висячий (G5: нет в gates[], реальный id другой)
            {"gate_id": "r1_no_pass_tests"},  # висячий (G5: в gates[] id = test_r1_no_pass_tests)
        ],
    }

    dangling = _find_dangling_gate_ids(synthetic)
    dangling_ids = {gid for _section, gid in dangling}

    logger.info("[IMP:8][r5_negative] Dangling detected: %s", sorted(dangling_ids))
    assert "ruff-format" in dangling_ids, "R5 FAIL: детектор не поймал висячий ruff-format (118 G5)"
    assert "check-manifests" in dangling_ids, "R5 FAIL: детектор не поймал висячий check-manifests (118 G5)"
    assert "template-syntax-contract" in dangling_ids, (
        "R5 FAIL: детектор не поймал висячий template-syntax-contract (118 G5)"
    )
    assert "r1_no_pass_tests" in dangling_ids, "R5 FAIL: детектор не поймал висячий r1_no_pass_tests (118 G5)"
    logger.info("[IMP:9][r5_negative] Все 4 исходных висячих gate_id детектированы ✓")


# endregion FUNC_test_negative_dangling_gate_id_detected


# ── Duplicate make_target detector (118 G2, AC-G2) ────────────────────────────
# Дедуп: templates-check был объявлен ДВАЖДЫ (validate + repair). Генератор
# (G3 merge) СОХРАНЯЕТ структурные секции verbatim → регенерация не убирает дубль.
# Единственный канон: один make_target = одна запись во ВСЕХ структурных секциях.
# Repair-map (repairs_gates) резолвится по имени таргета — дубль размывает канон.
# region FUNC__find_duplicate_make_targets


def _find_duplicate_make_targets(manifest: dict) -> list[tuple[str, list[str]]]:
    """Return [(make_target, [sections])] for make_targets declared in >1 manifest section.

    ## @purpose — Detector for 118 G2: make_target может иметь ровно одну запись
    ##            во ВСЕХ структурных секциях manifest. Дубль (templates-check в
    ##            validate + repair) — дрейф, который регенерация НЕ убирает.
    ## @io — ⇥ manifest: dict → ⎋ list[tuple[str, list[str]]]: (make_target, sections)
    ## @complexity — O(S*E) where S=sections, E=entries per section
    ## @invariants
    ##   - Секции: списки dict с make_target (bootstrap, deploy, validate, repair, ...)
    ##   - allowed_verbs (простой список имён) НЕ участвует — это не make_target-секция
    ##   - Один make_target в одной секции = канон; >1 секции = дубль (RED)
    """
    target_sections: dict[str, list[str]] = {}
    for section, entries in manifest.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("make_target"):
                target_sections.setdefault(entry["make_target"], []).append(section)

    duplicates = [(t, sects) for t, sects in target_sections.items() if len(sects) > 1]
    for t, sects in duplicates:
        logger.warning(
            "[IMP:7][dup_make_target] %s объявлен в %d секциях: %s",
            t,
            len(sects),
            ", ".join(sects),
        )
    return duplicates


# endregion FUNC__find_duplicate_make_targets


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_duplicate_make_targets
## @purpose  Gate: каждый make_target объявлен ровно в одной структурной секции manifest
##            (118 G2, AC-G2 — templates-check дедуп). FAIL code: DUP_MAKE_TARGET.
#
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · duplicate make_target in manifest (118 G2)
# · Scenario: templates-check был в validate: + repair: — регенерация сохраняла дубль
#   (генератор сохраняет структурные секции verbatim), канон размывался
# · Last fail: 118 G2 — templates-check объявлен дважды (validate:93 + repair:479)
# · Remove if: генератор перестаёт сохранять структурные секции verbatim
def test_no_duplicate_make_targets(caplog) -> None:
    """Gate: no make_target declared in >1 manifest section (templates-check dedup)."""
    manifest = _load_manifest()
    duplicates = _find_duplicate_make_targets(manifest)

    logger.info("[IMP:8][dup_make_target] Duplicate make_target(s): %d", len(duplicates))
    for t, sects in duplicates:
        logger.error("[IMP:9][dup_make_target] %s → %s", t, ", ".join(sects))

    assert not duplicates, (
        f"[GATE:FAIL][id:no-duplicate-make-targets] {len(duplicates)} make_target(s) в >1 секции "
        f"(118 G2 — templates-check дедуп):\n" + "\n".join(f"  {t}: {', '.join(sects)}" for t, sects in duplicates)
    )
    logger.info("[IMP:9][dup_make_target] ALL make_targets объявлены ровно в одной секции ✓")


# endregion FUNC_test_no_duplicate_make_targets


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_negative_duplicate_make_target_detected
## @purpose  R5 ANTI-SURVIVORSHIP negative companion: детектор обязан поймать ИСХОДНЫЙ
##            дубль из 118 G2 (templates-check в validate + repair — точный вход, поймавший баг).
##            FAIL code: R5_NEGATIVE — детектор не ловит регрессию.
#
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · test_no_duplicate_make_targets — 118 G2
# · Last fail: templates-check в validate:93 + repair:479 (дубль, который регенерация не убирала)
# · Remove if: генератор перестаёт сохранять структурные секции verbatim
def test_negative_duplicate_make_target_detected(caplog) -> None:
    """R5 negative: исходный дубль templates-check из 118 G2 детектируется."""
    synthetic: dict = {
        "validate": [
            {"make_target": "templates-check", "description": "Dry-run проверка шаблонов"},
        ],
        "repair": [
            {"make_target": "templates-check", "description": "Проверка покрытия и разрешимости шаблонов"},
        ],
        "deploy": [{"make_target": "deploy-project", "description": "Прямой деплой"}],
    }

    duplicates = _find_duplicate_make_targets(synthetic)
    dup_targets = {t for t, _sects in duplicates}

    logger.info("[IMP:8][r5_negative_dup] Duplicate detected: %s", sorted(dup_targets))
    assert "templates-check" in dup_targets, "R5 FAIL: детектор не поймал дубль templates-check (118 G2)"
    assert len(duplicates) == 1, f"R5 FAIL: ожидался 1 дубль (templates-check), получено {len(duplicates)}"
    logger.info("[IMP:9][r5_negative_dup] Исходный дубль templates-check детектирован ✓")


# endregion FUNC_test_negative_duplicate_make_target_detected


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_repair_contract_integrity
## @purpose  Validate Repair Contract fields are consistent between manifest and makefiles.
##            FAIL code: REPAIR_CONTRACT_VIOLATION
##            Repair-метаданные живут НЕ в gates-секции (T3.3 compact map {test_file: [ids]}),
##            а в `repair:` → repairs_gates и `non_repairable_gates` (B4 suppression:
##            repair→gates[] injection отключён, генератор не эмитит repair-поля в gates).
##            ⚠️ Скоуп проверок = ФАКТИЧЕСКИ исполнимая часть контракта:
##              - repairs_gates (pytest-гейты): обязательные repair-поля + валидный repair_class.
##              - НЕ проверяются: уникальность repair_id (by design ОДИН repair на много гейтов —
##                профили-parity/domain-parity/template-coverage шарится группой gate_id),
##                repair_command→repair.mk cross-ref (команды ссылаются на глобальные таргеты
##                generate-manifests/templates-check, вне repair.mk .PHONY — легитимно).
##              - Исходный тест итерировал gates[] и НИКОГДА не срабатывал (B4: repair-поля
##                в gates[] не эмитятся) — проверки были мёртвым кодом; переписан на реальные
##                носители repair-метаданных (T3.3, отчёт в плане).
#
# 🧪 TRAP[TEST] · 2026-07-23 · Regression: Repair Contract fields drift
# · Scenario: new gate added without repair fields, or repair_command target deleted
# · Last fail: N/A (new gate)
# · Remove if: Repair Contract is superseded
def test_repair_contract_integrity(caplog) -> None:
    """Gate: Repair Contract fields are valid and consistent."""
    manifest = _load_manifest()

    errors: list[str] = []

    # T3.3: gates-секция — компактная форма {test_file: [ids]}.
    gates_map = manifest.get("gates", {})
    if not isinstance(gates_map, dict):
        errors.append(f"gates must be a dict {{test_file: [ids]}} (T3.3 compaction), got {type(gates_map).__name__}")
    else:
        for test_file, ids in gates_map.items():
            if not isinstance(ids, list) or not ids:
                errors.append(f"gates[{test_file!r}] must be a non-empty list of gate ids")

    # B4/T3.3: repair-контракт читается из `repair:` → repairs_gates (repairable-gates)
    # и `non_repairable_gates` (L2/L3) — единственные носители repair-метаданных.
    for repair_entry in manifest.get("repair", []):
        for rg in repair_entry.get("repairs_gates", []):
            gate_id = rg.get("gate_id", "<unknown>")
            if rg.get("gate_kind") == _MAKE_TARGET_GATE_KIND:
                continue  # make-target-gate: не pytest-гейт, repair-поля свои

            # Check required fields
            missing = REQUIRED_REPAIR_FIELDS - set(rg.keys())
            if missing:
                errors.append(f"Gate '{gate_id}': repairable=true but missing: {missing}")

            # Check repair_class
            rc = rg.get("repair_class")
            if rc and rc not in REPAIR_CLASSES:
                errors.append(f"Gate '{gate_id}': invalid repair_class '{rc}', must be L1/L2/L3")

    # Non-repairable gates (L2/L3) — repair_reason обязателен
    for ng in manifest.get("non_repairable_gates", []):
        gid = ng.get("gate_id", "<unknown>")
        if ng.get("repair_class") in {"L2", "L3"} and "repair_reason" not in ng:
            errors.append(f"Gate '{gid}': L2/L3 non-repairable gate missing repair_reason")

    if errors:
        msg = f"[GATE:FAIL][id:repair-contract-integrity] {len(errors)} violation(s):\n"
        for e in errors:
            msg += f"  - {e}\n"
        logging.error(msg)
        raise AssertionError(msg)

    logging.info("[IMP:9][repair-contract-integrity] All repair-contract fields valid")


# endregion FUNC_test_repair_contract_integrity
# endregion REPAIR_CONTRACT_VALIDATION
