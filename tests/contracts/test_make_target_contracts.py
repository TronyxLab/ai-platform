# GREP_SUMMARY: contract make-target manifest parity T2.3 delegates_to bidirectional
# STRUCTURE: ▶ parse Makefile .PHONY → ◇ parse manifest make_target → ⟦assert bidirectional parity⟧ → ◇ assert delegates_to files exist
# region MODULE_CONTRACT
## @purpose — Bidirectional parity check between Makefile .PHONY targets and
##            core/entrypoint-manifest.yaml make_target entries. Also validates
##            that all delegates_to script paths exist on disk.
## @scope — Contract tests for T2.3: validate Makefile ↔ manifest ↔ filesystem consistency.
##          Static analysis only — does NOT run make -n (dry-run).
## @invariants
##   - Every make_target in manifest must have a corresponding .PHONY in Makefile
##   - Every .PHONY in Makefile must have a corresponding make_target in manifest
##     (except system exceptions: venv, help, pre-commit-install, pre-commit-run)
##   - Every delegates_to path (file references) must exist on disk
##   - Does NOT validate non-file delegates_to values (make/docker compose commands, descriptions)
## @rationale — D3: static analysis sufficient for contract validation.
##              Dry-run would require argument injection for parameterised targets
##              (deploy, bootstrap-node, etc.) and risks unintended side effects.
## @usecases — T2.3 contract gate: CI runs make test MARKER=contract to validate
##             the Makefile/manifest/filesystem triad consistency.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_MAKEFILE_PATH = _PROJECT_ROOT / "Makefile"
_MANIFEST_PATH = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"

# System exceptions — make targets that are valid but not in entrypoint-manifest
# These are declared in manifest name_linter.system_exceptions and system_prefixes
_SYSTEM_EXCEPTIONS = {
    "help",  # name_linter.system_exceptions
    "venv",  # name_linter.system_exceptions
    "pre-commit-install",  # name_linter.system_prefixes: pre-commit-
    "pre-commit-run",  # name_linter.system_prefixes: pre-commit-
    "_get_all_profiles",  # name_linter.system_exceptions (DevPlan 138 S3 — технический помощник parity-гейта)
}


# region HELPERS


def _parse_phony_targets(makefile_path: pathlib.Path) -> set[str]:
    """Parse Makefile and all makefiles/*.mk and extract all .PHONY target names.

    ## @purpose — Read root Makefile + makefiles/*.mk and collect every target
    ##            declared after .PHONY:. After W4-E4 include-split, .PHONY:
    ##            declarations live in makefiles/*.mk files (not root Makefile).
    ## @io — ⇥ makefile_path (root) → ⎋ set[str] of target names from all makefiles
    ## @complexity — O(F * L) where F = 7 files, L = max lines per file
    ## @invariants
    ##   - Reads root Makefile AND all makefiles/*.mk
    ##   - Handles multiple .PHONY: declarations across files
    ##   - Targets are whitespace-separated
    """
    targets: set[str] = set()
    makefiles_dir = makefile_path.parent / "makefiles"

    # Collect all files to scan: root Makefile + makefiles/*.mk
    files_to_scan: list[pathlib.Path] = [makefile_path]
    if makefiles_dir.is_dir():
        files_to_scan.extend(sorted(makefiles_dir.glob("*.mk")))

    for mk_file in files_to_scan:
        text = mk_file.read_text()
        for match in re.finditer(r"^\.PHONY:\s*(.+)$", text, re.MULTILINE):
            line = match.group(1).strip()
            targets.update(line.split())

    logger.info(
        "[IMP:7][_parse_phony_targets] Found %d .PHONY target(s) across %d file(s)", len(targets), len(files_to_scan)
    )
    return targets


def _parse_manifest_targets(manifest_path: pathlib.Path) -> set[str]:
    """Parse entrypoint-manifest.yaml and extract all make_target values.

    ## @purpose — Read manifest and collect every make_target field from all sections.
    ## @io — ⇥ manifest_path → ⎋ set[str] of make_target names
    ## @complexity — O(E) where E = number of entries in manifest
    ## @invariants
    ##   - Handles entries with or without make_target key
    ##   - Skips entries that only have script: key (lint.sh hook)
    """
    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    targets: set[str] = set()
    if not isinstance(data, dict):
        return targets

    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "make_target" in entry and entry["make_target"] is not None:
                targets.add(entry["make_target"])

    logger.info("[IMP:7][_parse_manifest_targets] Found %d make_target(s) in manifest", len(targets))
    return targets


def _parse_delegates_to_files(manifest_path: pathlib.Path) -> list[str]:
    """Parse entrypoint-manifest.yaml and extract all script file paths from delegates_to.

    ## @purpose — Extract file paths from delegates_to fields for filesystem validation.
    ## @io — ⇥ manifest_path → ⎋ list[str] of file paths (relative to project root)
    ## @complexity — O(E * P) where E = entries, P = path segments per entry
    ## @invariants
    ##   - Splits chained delegates_to by ' → ' separator
    ##   - Filters out non-file values (make commands, docker compose, descriptions)
    ##   - Handles ' + ' separator for multiple scripts in one segment
    ##   - Strips trailing flags ('--lint') and subcommand arguments ('build-platform') from paths
    """
    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    files: list[str] = []
    if not isinstance(data, dict):
        return files

    # Values that are NOT file paths (descriptions, make commands, docker compose commands)
    _non_file_phrases = {
        "make test",
        "make gate",
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker compose ps",
        "Module backup scripts",
        "Module restore scripts",
    }

    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to = entry.get("delegates_to")
            if not isinstance(delegates_to, str) or not delegates_to.strip():
                continue

            # Skip non-file values
            if any(delegates_to.startswith(p) for p in _non_file_phrases):
                continue

            # Split by chaining separator
            segments = [s.strip() for s in delegates_to.split("→")]
            for segment in segments:
                # Split by ' + ' for multiple file references in one segment
                for part in segment.split("+"):
                    part = part.strip()
                    if not part:
                        continue
                    # If it looks like a file path (starts with core/ or similar)
                    if part.startswith("core/") and not part.startswith("core/modules/"):
                        files.append(part)
                    elif not part.startswith("core/"):
                        # Could be quoted or be a description — skip
                        continue

    # Deduplicate but preserve order
    seen: set[str] = set()
    unique_files: list[str] = []
    for f in files:
        # Strip trailing flags like '--lint' and trailing subcommand args like 'build-platform'
        # e.g. 'core/internal/build/hermes-images.sh build-platform' → 'core/internal/build/hermes-images.sh'
        # Also handle .py scripts with trailing args like 'orchestrator_cli.py receive'
        words = f.split()
        if len(words) > 1 and (words[0].endswith(".sh") or words[0].endswith(".py")) and not words[1].startswith("--"):
            # If the first word is a .sh/.py script and second is not a flag,
            # the second word is a subcommand argument — keep only the script path
            base = words[0]
        else:
            # Strip trailing flags
            base = f.split(" --")[0].strip()
        if base not in seen:
            seen.add(base)
            unique_files.append(base)

    logger.info("[IMP:7][_parse_delegates_to_files] Found %d unique delegates_to file path(s)", len(unique_files))
    return unique_files


def _load_manifest_entry(manifest_path: pathlib.Path, target: str) -> dict:
    """Load a single make_target entry dict from entrypoint-manifest.yaml.

    ## @purpose — K2 (139 W1): fetch one manifest entry by make_target name for
    ##            registration-contract assertions (signature/delegates_to/mechanism).
    ## @io — ⇥ manifest_path, target → ⎋ dict (entry) | {} (not found)
    ## @complexity — O(E) where E = number of manifest entries
    """
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return {}
    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("make_target") == target:
                logger.info(
                    "[IMP:8][_load_manifest_entry] Found manifest entry for '%s'",
                    target,
                )
                return entry
    logger.warning("[IMP:7][_load_manifest_entry] Manifest entry '%s' NOT found", target)
    return {}


def _makefile_doc_block(makefile_path: pathlib.Path, target: str) -> str:
    """Extract the doc-comment block above a make target recipe.

    ## @purpose — K2 (139 W1): read the `## ` doc lines immediately preceding the
    ##            target recipe — the Makefile-level registration point for variables.
    ## @io — ⇥ makefile_path, target → ⎋ str (doc block) | "" (not found)
    ## @complexity — O(L) where L = makefile lines
    """
    text = makefile_path.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"{target}:":
            doc: list[str] = []
            j = i - 1
            while j >= 0 and lines[j].startswith("##"):
                doc.append(lines[j])
                j -= 1
            return "\n".join(reversed(doc))
    return ""


# endregion HELPERS


# region TEST: bidirectional parity


@pytest.mark.contract
@ldd_trajectory
def test_every_manifest_target_has_makefile_entry(caplog) -> None:
    """Check each manifest make_target exists in Makefile .PHONY.

    ## @purpose — Verify no orphan make_target in manifest without a corresponding
    ##            make target in the Makefile.
    ## @scenario — Parse Makefile .PHONY → parse manifest make_target → assert subset
    ## @regression — T2.3 contract: drift between manifest and Makefile
    ## @last_fail — N/A (new test)
    ## @remove_if — Makefile or manifest structure fundamentally changes
    """

    phony_targets = _parse_phony_targets(_MAKEFILE_PATH)
    manifest_targets = _parse_manifest_targets(_MANIFEST_PATH)

    missing: set[str] = manifest_targets - phony_targets

    logger.info(
        "[IMP:9][test_every_manifest_target_has_makefile_entry] Manifest targets: %d, Makefile .PHONY: %d, Missing: %s",
        len(manifest_targets),
        len(phony_targets),
        missing,
    )

    # Also check system exceptions coverage
    missing_system_exceptions = phony_targets - manifest_targets - _SYSTEM_EXCEPTIONS
    logger.info(
        "[IMP:8][test_every_manifest_target_has_makefile_entry] "
        "Makefile targets missing from manifest (excluding system exceptions): %s",
        missing_system_exceptions,
    )

    assert not missing, (
        f"Manifest make_targets not in Makefile .PHONY: {missing}\n"
        f"Every entrypoint-manifest make_target must have a corresponding "
        f".PHONY declaration in the Makefile."
    )

    logger.critical(
        "[IMP:9][test_every_manifest_target_has_makefile_entry] PASS: All %d manifest targets "
        "have Makefile .PHONY entries",
        len(manifest_targets),
    )


@pytest.mark.contract
@ldd_trajectory
def test_every_makefile_target_has_manifest_entry(caplog) -> None:
    """Check each Makefile .PHONY target exists in manifest (bidirectional parity).

    ## @purpose — Verify no orphan .PHONY target without a corresponding make_target
    ##            in the manifest. Excludes system exceptions (venv, help,
    ##            pre-commit-install, pre-commit-run).
    ## @scenario — Parse Makefile .PHONY → parse manifest make_target → assert subset
    ##            (minus _SYSTEM_EXCEPTIONS)
    ## @regression — T2.3 contract: drift between Makefile and manifest
    ## @last_fail — N/A (new test)
    ## @remove_if — Makefile or manifest structure fundamentally changes
    """

    phony_targets = _parse_phony_targets(_MAKEFILE_PATH)
    manifest_targets = _parse_manifest_targets(_MANIFEST_PATH)

    # Remove system exceptions
    phony_except_exceptions = phony_targets - _SYSTEM_EXCEPTIONS

    missing: set[str] = phony_except_exceptions - manifest_targets

    logger.info(
        "[IMP:9][test_every_makefile_target_has_manifest_entry] "
        "Makefile .PHONY (excl. system): %d, Manifest targets: %d, Missing: %s",
        len(phony_except_exceptions),
        len(manifest_targets),
        missing,
    )

    assert not missing, (
        f"Makefile .PHONY targets missing from manifest: {missing}\n"
        f"Every non-system Makefile .PHONY target must have a corresponding "
        f"make_target entry in entrypoint-manifest.yaml."
    )

    logger.critical(
        "[IMP:9][test_every_makefile_target_has_manifest_entry] PASS: All %d Makefile .PHONY targets "
        "(excl. system exceptions) have manifest entries",
        len(phony_except_exceptions),
    )


# endregion


# region TEST: delegates_to script existence


@pytest.mark.contract
@ldd_trajectory
def test_manifest_delegate_scripts_exist(caplog) -> None:
    """Check that every delegates_to script path exists on disk.

    ## @purpose — Verify shell scripts referenced in manifest delegates_to fields
    ##            actually exist on the filesystem. Prevents stale references
    ##            to deleted or renamed scripts.
    ## @scenario — Parse manifest delegates_to → extract file paths → assert each
    ##            path exists relative to project root
    ## @regression — T2.3 contract: stale script references in manifest
    ## @last_fail — N/A (new test)
    ## @remove_if — Manifest structure fundamentally changes
    """

    delegate_files = _parse_delegates_to_files(_MANIFEST_PATH)

    missing: list[str] = []
    for rel_path in delegate_files:
        full_path = _PROJECT_ROOT / rel_path
        if not full_path.exists():
            missing.append(rel_path)

    logger.info(
        "[IMP:9][test_manifest_delegate_scripts_exist] Delegates_to paths: %d, Missing: %d",
        len(delegate_files),
        len(missing),
    )

    assert not missing, (
        f"delegates_to scripts NOT FOUND on disk: {missing}\n"
        f"Every delegates_to file path in entrypoint-manifest.yaml must "
        f"correspond to an existing file."
    )

    logger.critical(
        "[IMP:9][test_manifest_delegate_scripts_exist] PASS: All %d delegates_to scripts exist on disk",
        len(delegate_files),
    )


# endregion


# region TEST: deploy NODE/LAUNCH/preflight registration contract (139 W1 K2)


@pytest.mark.contract
@ldd_trajectory
def test_deploy_registration_contract(caplog) -> None:
    """K2 (139 W1): NODE/LAUNCH/preflight-семантика зарегистрирована в entrypoint-manifest.

    ## @purpose — Контракт РЕГИСТРАЦИИ (не симуляция bash): make_target deploy в
    ##            entrypoint-manifest.yaml обязан регистрировать make-переменные
    ##            PROJECT/NODE/LAUNCH в signature и preflight/launch-семантику в
    ##            delegates_to; Makefile deploy-таргет регистрирует те же переменные
    ##            в doc-блоке (parity Makefile ↔ manifest). Заменяет удалённый
    ##            tests/test_sequencing.py (P0-синтетика, DevPlan 139 W1 K2).
    ## @scenario — Load manifest deploy entry → assert signature vars + delegates_to verbs
    ##            → assert makefiles/deploy.mk doc registers NODE/LAUNCH
    ## @regression — Деploy sequencing-контракт дрейфует (манифест теряет NODE/LAUNCH)
    ## @last_fail — N/A (новый контракт; замена bash-симуляций test_sequencing.py)
    ## @remove_if — deploy-таргет намеренно перестаёт поддерживать NODE/LAUNCH
    """
    deploy_entry = _load_manifest_entry(_MANIFEST_PATH, "deploy")

    # ── 1. Запись зарегистрирована (make_target + mechanism) ──
    assert deploy_entry, "manifest entry 'deploy' missing — NODE/LAUNCH/preflight не зарегистрированы"
    assert deploy_entry.get("mechanism") == "git-push", (
        f"deploy mechanism must be git-push, got {deploy_entry.get('mechanism')!r}"
    )
    logger.info("[IMP:9][deploy_contract] deploy entry registered (mechanism=git-push)")

    # ── 2. signature регистрирует переменные PROJECT (required) + NODE + LAUNCH ──
    signature = str(deploy_entry.get("signature", ""))
    assert "PROJECT=" in signature, f"deploy signature must register PROJECT=, got: {signature!r}"
    assert "NODE=" in signature, f"deploy signature must register NODE= (preflight trigger), got: {signature!r}"
    assert "LAUNCH=" in signature, f"deploy signature must register LAUNCH= (deliver mode), got: {signature!r}"
    logger.info("[IMP:9][deploy_contract] signature registers PROJECT/NODE/LAUNCH: %s", signature)

    # ── 3. delegates_to регистрирует receive verb + orchestrator_cli (LAUNCH deliver path) ──
    delegates_to = str(deploy_entry.get("delegates_to", ""))
    assert "receive" in delegates_to, f"delegates_to must register 'receive' verb, got: {delegates_to!r}"
    assert "orchestrator_cli" in delegates_to, (
        f"delegates_to must register orchestrator_cli dispatch, got: {delegates_to!r}"
    )
    logger.info("[IMP:9][deploy_contract] delegates_to registers receive → orchestrator_cli dispatch")

    # ── 4. Makefile parity: deploy doc-блок регистрирует те же NODE/LAUNCH переменные ──
    deploy_mk = _PROJECT_ROOT / "makefiles" / "deploy.mk"
    doc_block = _makefile_doc_block(deploy_mk, "deploy")
    assert doc_block, "makefiles/deploy.mk: deploy doc-блок не найден (registration point)"
    assert "NODE=" in doc_block, f"deploy.mk doc must register NODE=, got: {doc_block!r}"
    assert "LAUNCH=" in doc_block, f"deploy.mk doc must register LAUNCH=, got: {doc_block!r}"
    assert "pre-flight" in doc_block or "preflight" in doc_block, (
        f"deploy.mk doc must register pre-flight semantics, got: {doc_block!r}"
    )
    logger.info("[IMP:9][deploy_contract] makefiles/deploy.mk doc registers NODE/LAUNCH/pre-flight (parity)")

    logger.critical(
        "[IMP:9][test_deploy_registration_contract] PASS: NODE/LAUNCH/preflight зарегистрированы "
        "(manifest signature + delegates_to + Makefile parity)"
    )


# endregion
