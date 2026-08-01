# GREP_SUMMARY: orphan-reconciler, docker, container, reconcile, batch, orphan, compose, inspect
# STRUCTURE: ▶ ┌module_entries + modules_dir┐ → ⚡ find compose files → ⚡ docker ps -a (single call) → ○ for each service: docker compose config → ◇ container in existing set? → docker inspect project label → ◇ project != module_name? → ⊕ orphans[] → ⎋ return orphans
# region MODULE_CONTRACT
## @purpose  S8: Batch orphan container reconciliation — replaces inline python3
##           in deploy-modules.sh _batch_orphan_reconciliation(). Collects all compose
##           files, does one docker ps -a, compares container names with compose project
##           labels, and returns list of orphan (foreign) containers.
## @scope    Called during module deployment to detect and reconcile containers that belong
##           to a different compose project than expected (e.g., after module rename or
##           migration). The shell facade reads the output and stops+removes each orphan.
## @invariants
##   - One docker ps -a call for all modules (not per-module)
##   - Compose file discovery order: compose.yaml → docker-compose.yaml → docker-compose.base.yml
##   - Subprocess errors are WARN-logged, never raised — always graceful degradation
##   - Returned orphans always have "container_name" and "project" keys
##   - Project is empty string if inspect fails or label is missing
##   - Empty module_entries returns empty list immediately
## @rationale  Per-module orphan detection created 13 subprocess spawns per update cycle.
##             Batch approach: 1 python3 call for all modules, reducing total spawn time
##             and consolidating container state into a single snapshot.
## @changes
##   2026-07-22 · Created (W4-E1 extraction from deploy-modules.sh §1190-1261)
##   2026-07-22 · Added W5-E5 self-heal functions (_self_heal_orphan_containers, _self_heal_aged_images)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# DevPlan 116 B5 T3: shared docker compose config — sole path (гейт docker_sole_path)
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11)
from core.internal.shared.timeouts import IMAGE_CHECK_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants ──
DEFAULT_IMAGE_RETENTION_DAYS: int = 30
DOCKER_RM_TIMEOUT: int = 30

COMPOSE_FILE_CANDIDATES: list[str] = [
    "compose.yaml",
    "docker-compose.yaml",
    "docker-compose.base.yml",
]
"""## @invariant Compose file search priority — first match wins, mirroring docker compose CLI behavior."""

DOCKER_PS_TIMEOUT: int = 15
"""## @invariant docker ps -a timeout (seconds). Matches deploy-modules.sh value."""

DOCKER_INSPECT_TIMEOUT: int = 15
"""## @invariant docker inspect timeout (seconds). Single container lookup."""


# region FUNC__find_compose_files
## @purpose  Discover compose files for each module entry in priority order
## @io       ⇥ module_entries: list[str], modules_dir: str → ⎋ list[tuple[str, str]] (module_name, compose_path)
## @complexity O(N * C) where N = len(entries), C = len(COMPOSE_FILE_CANDIDATES) = 3
## @invariants
##   - Each entry is "module_name:overlay_dir" or just "module_name"
##   - Only the module_name segment (before ':') is used for compose file discovery
##   - First matching compose file in priority order wins
##   - Entries without any compose file are silently skipped
##   - Entry format: "module_name[:overlay_dir]" — overlay subdirectory is ignored (only relevant for sudoers/shell)
def _find_compose_files(module_entries: list[str], modules_dir: str) -> list[tuple[str, str]]:
    """Discover compose files for each module entry.

    Scans each module directory for compose files in priority order:
    compose.yaml → docker-compose.yaml → docker-compose.base.yml.
    Returns a list of (module_name, compose_file_path) tuples for
    entries that have a compose file.
    """
    logger.info("[IMP:7][_find_compose_files] Scanning %d entries in %s", len(module_entries), modules_dir)

    result: list[tuple[str, str]] = []
    for entry in module_entries:
        mod_name = entry.split(":")[0]
        mod_dir = Path(modules_dir) / mod_name

        if not mod_dir.is_dir():
            logger.warning("[IMP:8][_find_compose_files] Module directory not found: %s", mod_dir)
            continue

        found = False
        for cf in COMPOSE_FILE_CANDIDATES:
            cf_path = mod_dir / cf
            if cf_path.is_file():
                result.append((mod_name, str(cf_path)))
                logger.info("[IMP:7][_find_compose_files] Found compose file for %s: %s", mod_name, cf_path)
                found = True
                break

        if not found:
            logger.warning(
                "[IMP:8][_find_compose_files] No compose file found for module %s (searched: %s)",
                mod_name,
                COMPOSE_FILE_CANDIDATES,
            )

    logger.info(
        "[IMP:7][_find_compose_files] Resolved %d compose files from %d entries", len(result), len(module_entries)
    )
    return result


# endregion FUNC__find_compose_files


# region FUNC__get_existing_containers
## @purpose  Run a single docker ps -a to get all container names known to the daemon
## @io       ⇥ None → ⎋ set[str] (container names)
## @complexity 1 — single subprocess call with timeout
## @invariants
##   - On subprocess error: log WARN, return empty set (graceful degradation)
##   - Empty lines are filtered out
##   - Timeout is DOCKER_PS_TIMEOUT seconds
def _get_existing_containers() -> set:
    """Run docker ps -a once and return set of container names.

    Returns empty set on any subprocess error — never raises.
    This allows the caller to continue gracefully if docker is unavailable.
    """
    logger.info("[IMP:7][_get_existing_containers] Querying docker ps -a for all containers")
    try:
        ps_r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_PS_TIMEOUT,
        )
        names = {line.strip() for line in ps_r.stdout.splitlines() if line.strip()}
        logger.info("[IMP:7][_get_existing_containers] Found %d containers in docker ps -a", len(names))
        return names
    except FileNotFoundError:
        logger.warning("[IMP:8][_get_existing_containers] docker binary not found — returning empty container set")
        return set()
    except subprocess.TimeoutExpired:
        logger.warning(
            "[IMP:8][_get_existing_containers] docker ps -a timed out after %ds — returning empty container set",
            DOCKER_PS_TIMEOUT,
        )
        return set()
    except subprocess.CalledProcessError as e:
        logger.warning(
            "[IMP:8][_get_existing_containers] docker ps -a failed (returncode=%d): %s — returning empty container set",
            e.returncode,
            e.stderr.strip() if e.stderr else "no stderr",
        )
        return set()
    except OSError as e:
        logger.warning(
            "[IMP:8][_get_existing_containers] Unexpected docker ps error: %s — returning empty container set",
            e,
        )
        return set()


# endregion FUNC__get_existing_containers


# region FUNC__get_compose_services
## @purpose  Parse container names from docker compose config --format json output
## @io       ⇥ compose_path: str, module_name: str → ⎋ list[str] (container names from this compose file)
## @complexity 2 — subprocess call + JSON parse + iteration over services
## @invariants
##   - On subprocess error: log WARN, return empty list
##   - JSON parse error: log WARN, return empty list
##   - Service container_name resolves to explicit container_name or service name
##   - Services without container_name AND without name field are skipped
##   - --profile <module_name\> is passed to get the correct config resolution
def _get_compose_services(compose_path: str, module_name: str) -> list[str]:
    """Run docker compose config --format json and extract container names.

    Uses --profile <module_name\\> so compose resolves the correct service set
    for the given module. Returns a list of container names (explicit or
    service-name fallback).
    """
    logger.info("[IMP:7][_get_compose_services] Resolving services for %s from %s", module_name, compose_path)
    # Shared docker_compose_config — sole path (DevPlan 116 B5 T3, гейт docker_sole_path).
    # Shared возвращает CompletedProcess (никогда не raise) — try/except TimeoutExpired/OSError удалены.
    cfg_r = _shared_docker_compose_config(
        os.path.dirname(compose_path),
        compose_args=["-f", compose_path, "--profile", module_name],
        flags=["--format", "json"],
    )
    if cfg_r.returncode != 0:
        logger.warning(
            "[IMP:8][_get_compose_services] docker compose config failed for %s (returncode=%d): %s",
            module_name,
            cfg_r.returncode,
            cfg_r.stderr.strip() if cfg_r.stderr else "no stderr",
        )
        return []

    cfg_stdout = cfg_r.stdout
    if isinstance(cfg_stdout, bytes):
        cfg_stdout = cfg_stdout.decode("utf-8")
    try:
        cfg = json.loads(cfg_stdout)
    except json.JSONDecodeError as e:
        logger.warning(
            "[IMP:8][_get_compose_services] Invalid JSON from docker compose config for %s: %s",
            module_name,
            e,
        )
        return []

    services = cfg.get("services", {})
    container_names: list[str] = []

    for svc_name, svc in services.items():
        cname = svc.get("container_name", "") or svc.get("name", "")
        if cname:
            container_names.append(cname)
            logger.info("[IMP:7][_get_compose_services] %s → container_name: %s", svc_name, cname)

    logger.info(
        "[IMP:7][_get_compose_services] Resolved %d container names from %s",
        len(container_names),
        compose_path,
    )
    return container_names


# endregion FUNC__get_compose_services


# region FUNC__inspect_project_label
## @purpose  Get the com.docker.compose.project label from a running/stopped container
## @io       ⇥ container_name: str → ⎋ str (project label, or empty string on error)
## @complexity 1 — single subprocess call per container
## @invariants
##   - On subprocess error: log WARN, return empty string (treated as orphan)
##   - Empty label is returned as empty string (not "None")
##   - Whitespace is stripped from the output
def _inspect_project_label(container_name: str) -> str:
    """Retrieve the com.docker.compose.project label from a container.

    Returns an empty string if:
    - docker is unavailable
    - the container does not exist
    - the label is not set on the container
    An empty string project label is treated as orphan by the caller.
    """
    logger.info("[IMP:7][_inspect_project_label] Inspecting project label for container %s", container_name)
    try:
        ins_r = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_INSPECT_TIMEOUT,
        )
        proj = ins_r.stdout.strip()
        logger.info("[IMP:7][_inspect_project_label] Container %s → project label: '%s'", container_name, proj)
        return proj
    except FileNotFoundError:
        logger.warning(
            "[IMP:8][_inspect_project_label] docker binary not found — returning empty project for %s", container_name
        )
        return ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "[IMP:8][_inspect_project_label] docker inspect timed out for %s after %ds — returning empty project",
            container_name,
            DOCKER_INSPECT_TIMEOUT,
        )
        return ""
    except (OSError, subprocess.CalledProcessError) as e:
        logger.warning(
            "[IMP:8][_inspect_project_label] Unexpected docker inspect error for %s: %s — returning empty",
            container_name,
            e,
        )
        return ""


# endregion FUNC__inspect_project_label


# region FUNC__batch_orphan_reconciliation
## @purpose  Main entry point: detect orphan containers across all modules in one pass
## @io       ⇥ module_entries: list[str], modules_dir: str → ⎋ list[dict[str, str]]
## @complexity 3 — multi-step pipeline: compose discovery → docker ps → per-service inspect
## @invariants
##   - Returns empty list on any fatal error (docker unavailable, no compose files, etc.)
##   - Each orphan dict has "container_name" and "project" keys
##   - Project is empty string if label not found
##   - Container is orphan if project label != module_name
##   - Container not in docker ps -a output is silently skipped (not an orphan)
##   - All subprocess errors are caught and logged at WARN level — never raised
## @rationale  One-pass algorithm avoids per-module docker ps -a (O(N) → O(1) docker calls).
##             The single docker ps -a call provides a consistent snapshot of container state,
##             avoiding TOCTOU race conditions that per-module calls would create.
def _batch_orphan_reconciliation(module_entries: list[str], modules_dir: str) -> list[dict[str, str]]:
    """Detect orphan containers across all modules.

    Algorithm:
    1. Find compose files for each module entry
    2. Run docker ps -a once for all modules
    3. For each module's compose services: resolve container names
    4. For each resolved container that exists: check project label
    5. If project label != module_name → it's a foreign/orphan container

    Returns a list of orphan dicts: [{"container_name": "...", "project": "..."}]
    The shell facade reads this output and stops+removes each orphan.
    """
    logger.info(
        "[IMP:9][_batch_orphan_reconciliation] Starting batch orphan reconciliation for %d entries", len(module_entries)
    )

    if not module_entries:
        logger.info("[IMP:9][_batch_orphan_reconciliation] Empty module entries — no orphans to reconcile")
        return []

    # Step 1: Find compose files
    compose_files = _find_compose_files(module_entries, modules_dir)
    if not compose_files:
        logger.info("[IMP:9][_batch_orphan_reconciliation] No compose files found — no orphans to reconcile")
        return []

    # Step 2: Single docker ps -a
    existing = _get_existing_containers()
    if not existing:
        logger.info(
            "[IMP:9][_batch_orphan_reconciliation] No existing containers from docker ps -a — no orphans to reconcile"
        )
        return []

    logger.info(
        "[IMP:7][_batch_orphan_reconciliation] %d containers in docker ps -a, %d compose files to check",
        len(existing),
        len(compose_files),
    )

    # Steps 3-5: For each compose file, resolve services and check project labels
    orphans: list[dict[str, str]] = []

    for mod_name, cf_path in compose_files:
        logger.info("[IMP:7][_batch_orphan_reconciliation] Checking module: %s (compose: %s)", mod_name, cf_path)

        container_names = _get_compose_services(cf_path, mod_name)
        logger.info(
            "[IMP:7][_batch_orphan_reconciliation] Module %s has %d container names from compose config",
            mod_name,
            len(container_names),
        )

        for cname in container_names:
            if cname not in existing:
                logger.info("[IMP:7][_batch_orphan_reconciliation] Container %s not in docker ps -a — skipping", cname)
                continue

            # Check the project label
            proj = _inspect_project_label(cname)

            # Container is orphan if: no project label OR project label != module_name
            if not proj or proj != mod_name:
                logger.info(
                    "[IMP:9][_batch_orphan_reconciliation] ORPHAN DETECTED: container=%s, expected_project=%s, actual_project='%s'",
                    cname,
                    mod_name,
                    proj,
                )
                orphans.append({"container_name": cname, "project": proj})
            else:
                logger.info(
                    "[IMP:7][_batch_orphan_reconciliation] Container %s OK — project label matches module %s",
                    cname,
                    mod_name,
                )

    logger.info(
        "[IMP:9][_batch_orphan_reconciliation] Batch orphan reconciliation complete — %d orphans found across %d modules",
        len(orphans),
        len(compose_files),
    )
    return orphans


# endregion FUNC__batch_orphan_reconciliation


# region FUNC__self_heal_orphan_containers
## @purpose  Remove orphan containers detected by _batch_orphan_reconciliation.
##           Only active when --self-heal flag is set (W5-E5).
def _self_heal_orphan_containers(orphans: list[dict[str, str]]) -> int:
    """Remove orphan containers using docker rm -f.

    ## @io — ⇥ orphans: list of {container_name, project} dicts (from batch_orphan_reconciliation)
    ##           → ⎋ removed_count: int
    ## @complexity — O(n) where n = len(orphans)
    """
    removed = 0
    for orphan in orphans:
        cname = orphan.get("container_name", "") or orphan.get("container", "")
        if not cname:
            continue
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", cname],
                capture_output=True,
                timeout=DOCKER_RM_TIMEOUT,
                check=False,
            )
            if result.returncode == 0:
                logger.info(
                    "[IMP:9][self_heal][orphan] Removed orphan container: %s (module: %s)",
                    cname,
                    orphan.get("project", "unknown"),
                )
                removed += 1
            else:
                logger.warning(
                    "[IMP:8][self_heal][orphan] Failed to remove orphan %s: %s",
                    cname,
                    result.stderr.strip()[:200],
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("[IMP:5][self_heal][orphan] Error removing %s: %s", cname, exc)
    return removed


# endregion FUNC__self_heal_orphan_containers


# region FUNC__self_heal_aged_images
## @purpose  Prune aged Docker images to free disk space (W5-E5).
##           Only active when --self-heal flag is set.
def _self_heal_aged_images(retention_days: int = DEFAULT_IMAGE_RETENTION_DAYS) -> int:
    """Prune Docker images older than retention_days.

    ## @io — ⇥ retention_days: int (default 30) → ⎋ pruned_count: int
    ## @complexity — O(1)
    ## @invariants
    ##   - Filters by label=com.docker.compose.project (NOT dangling-only)
    ##   - Default retention = 30d (overridable via node.yaml image_retention_days)
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "image",
                "prune",
                "-f",
                "--filter",
                f"until={retention_days * 24}h",
                "--filter",
                "label=com.docker.compose.project",
            ],
            capture_output=True,
            timeout=IMAGE_CHECK_TIMEOUT,
            check=False,
        )
        # Parse prune output for count (docker image prune reports "Total reclaimed space")
        stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode()
        # Count lines that look like deleted images (they contain the image tag/id)
        lines = [line for line in stdout.splitlines() if line.strip() and not line.startswith("Total")]
        pruned = len(lines)

        if pruned > 0:
            logger.info(
                "[IMP:9][self_heal][prune] Pruned %d aged images (retention: %d days)",
                pruned,
                retention_days,
            )
        else:
            logger.info(
                "[IMP:7][self_heal][prune] No aged images to prune (retention: %d days)",
                retention_days,
            )
        return pruned
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][self_heal][prune] Image prune failed: %s", exc)
        return 0


# endregion FUNC__self_heal_aged_images


# region FUNC_main
## @purpose  CLI entrypoint: parse args, run reconciliation, optionally self-heal (--self-heal).
##           Outputs orphans as pipe-delimited lines. With --self-heal, removes orphan containers
##           and prunes aged Docker images (W5-E5).
## @io       ⇥ sys.argv → ⎋ exit 0 (always); stdout: "container_name|project_name" per orphan
## @complexity 2 — reconciliation + optional self-heal dispatch
## @invariants
##   --module-entries accepts comma-separated "name:overlay" or just "name"
##   --modules-dir defaults to "/opt/platform/modules" (standard deploy path)
##   --self-heal enables docker rm -f + docker image prune after detection
##   - Output is on stdout, one orphan per line in "container_name|project_name" format
##   - Empty project field outputs as empty string: "container_name|"
##   - Exit code is always 0 — shell reads stdout and handles stop/rm
def main() -> int:
    """CLI entrypoint for orphan_reconciler.py.

    Usage:
        python3 orphan_reconciler.py --module-entries "module1:overlay1,module2" --modules-dir /opt/platform/modules
        python3 orphan_reconciler.py --module-entries "module1:overlay1" --self-heal

    Output format (one orphan per line):
        container_name|project_name

    With --self-heal:
        Removes orphan containers (docker rm -f) and prunes aged images (docker image prune).
    """
    parser = argparse.ArgumentParser(
        description="Batch orphan container reconciliation — detect containers "
        "whose compose project label does not match their module.",
    )
    parser.add_argument(
        "--module-entries",
        required=True,
        type=str,
        help="Comma-separated list of module entries: 'name:overlay' or just 'name'",
    )
    parser.add_argument(
        "--modules-dir",
        default=os.path.join(os.environ.get("PLATFORM_ROOT", "/opt/platform"), "modules"),
        type=str,
        help="Path to modules directory (default: PLATFORM_ROOT/modules)",
    )
    parser.add_argument(
        "--self-heal",
        action="store_true",
        default=False,
        help="Enable self-heal mode: remove orphan containers and prune aged images (default: detect-only)",
    )

    args = parser.parse_args()

    # Parse comma-separated entries
    entries = [e.strip() for e in args.module_entries.split(",") if e.strip()]
    logger.info(
        "[IMP:7][main] CLI args: module_entries=%s, modules_dir=%s, self_heal=%s",
        entries,
        args.modules_dir,
        args.self_heal,
    )

    # Run reconciliation
    orphans = _batch_orphan_reconciliation(entries, args.modules_dir)

    # ── Self-heal mode (W5-E5) ──
    if args.self_heal:
        if orphans:
            removed = _self_heal_orphan_containers(orphans)
            logger.info("[IMP:9][main][self_heal] Removed %d orphan container(s)", removed)
        else:
            logger.info("[IMP:7][main][self_heal] No orphan containers to remove")
        pruned = _self_heal_aged_images()
        logger.info("[IMP:9][main][self_heal] Pruned %d aged image(s)", pruned)

    # Output each orphan on its own line as "container_name|project_name"
    for orphan in orphans:
        print(f"{orphan['container_name']}|{orphan['project']}")

    logger.info("[IMP:7][main] CLI complete — %d orphans output to stdout", len(orphans))
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
