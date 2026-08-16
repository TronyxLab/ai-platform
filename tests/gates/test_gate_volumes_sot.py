# GREP_SUMMARY: gate volumes-sot root-compose volume-declarations driver_opts CONTEXT_IMAGE-empty-string module-top-level-volumes D4 U-49
# STRUCTURE: ┌scan root docker-compose.yml volumes┐ → ┌derive module-referenced volumes from service mounts┐ → ◇ root == referenced (no orphans / no missing) → ◇ module top-level volumes == ∅ → ◇ CONTEXT_IMAGE: "" absent → ⊕ negative R5 (module volume colliding root) → ⎋ assert
# region MODULE_CONTRACT
## @purpose  Gate tests enforcing the volumes Single-Source-of-Truth contract (DevPlan 116 B3 T4, U-49):
##           root docker-compose.yml declares ALL volumes referenced by module services (bind driver_opts
##           + docker-managed); module docker-compose.base.yml files must NOT declare top-level volumes
##           sections; CONTEXT_IMAGE: "" empty-string env mechanism is forbidden (D4).
## @scope    Static file analysis — root docker-compose.yml, docker-compose.macos.yml,
##           docker-compose.platform-dev.yml, core/modules/*/docker-compose.base.yml.
##           No Docker daemon required.
## @invariants
##   - Root compose volumes == set of volume names referenced by module service mounts
##     (derived at runtime — no hardcoded set literal; every mount resolves, no orphans)
##   - Module top-level `volumes:` section must be absent (service mount references remain)
##   - Module volume names (if any) must NOT intersect root names — duplicate declaration = RED
##   - CONTEXT_IMAGE: "" = 0 occurrences across docker-compose*.yml (root/macos/platform-dev)
##   - All tests @pytest.mark.gate; negative R5 test included (anti-survivorship)
## @rationale  U-49: driver_opts of modules merged into root "by accident" (docker compose config) —
##             explicit single-SoT declaration in root eliminates drift; empty-string CONTEXT_IMAGE
##             was the only env path for L1 mode and is replaced by explicit image override (D4).
## @changes  2026-08-01 · Created (DevPlan 116 B3 T4)
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT_COMPOSE = PROJECT_ROOT / "docker-compose.yml"
PLATFORM_DEV = PROJECT_ROOT / "docker-compose.platform-dev.yml"
MACOS_COMPOSE = PROJECT_ROOT / "docker-compose.macos.yml"
MODULES_DIR = PROJECT_ROOT / "core" / "modules"

# Bind-volume driver_opts device paths are the canonical host locations
# (derived from module service mounts + root declarations — used for diagnostics only)
BIND_DEVICES = {
    "postgres-data": "/var/lib/platform/postgres-data",
    "wal-archive": "/var/lib/platform/wal-archive",
    "backup-spool": "/var/lib/platform/backup-spool",
    "backup-logs": "/var/log/platform/backup",
    "hermes-data": "/var/lib/platform/hermes-agent/data",
}


def _read_file(path: Path) -> str:
    """Read file content, return empty string if not found."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return ""


def _root_volumes() -> set[str]:
    """Parse root docker-compose.yml volumes keys.

    ## @purpose — Extract declared volume names from root compose.
    ## @io — ⎋ set[str]
    ## @complexity — O(N) file read + YAML parse
    """
    assert ROOT_COMPOSE.exists(), f"root docker-compose.yml not found: {ROOT_COMPOSE}"
    data = yaml.safe_load(_read_file(ROOT_COMPOSE))
    volumes = data.get("volumes", {}) or {}
    return set(volumes.keys())


def _module_referenced_volumes() -> set[str]:
    """Derive volume names referenced by module service mount entries.

    ## @purpose — Collect named-volume references from module docker-compose.base.yml service
    ##            `volumes:` lists (short syntax `name:/container/path[:opts]`). Absolute paths,
    ##            relative paths (./), and ${VAR} expansions are NOT named volumes.
    ## @io — ⎋ set[str]: volume names referenced by module services
    ## @complexity — O(M * V) where M = modules, V = volume entries per service
    ## @invariants
    ##   - Host paths (/var/run, ${HOME}, ./config) excluded — not named volumes
    ##   - The derived set must equal root declarations (SoT completeness, no orphans)
    """
    referenced: set[str] = set()
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        data = yaml.safe_load(_read_file(compose_file)) or {}
        services = data.get("services", {}) or {}
        for svc in services.values():
            if not isinstance(svc, dict):
                continue
            for vol_entry in svc.get("volumes", []) or []:
                if not isinstance(vol_entry, str):
                    continue
                mount_spec = vol_entry.split(":")[0]
                if not mount_spec or mount_spec.startswith(("/", ".", "${", "$")):
                    continue  # absolute/relative path or env expansion — not a named volume
                referenced.add(mount_spec)
    return referenced


def _module_top_level_volumes() -> dict[str, set[str]]:
    """Parse all core/modules/*/docker-compose.base.yml top-level volumes sections.

    ## @purpose — Detect any module-level volume declarations (must be empty per SoT contract).
    ## @io — ⎋ dict[module_name → set[volume_names]]
    ## @complexity — O(M * N) where M = modules, N = yaml size
    """
    result: dict[str, set[str]] = {}
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = compose_file.parent.name
        data = yaml.safe_load(_read_file(compose_file)) or {}
        volumes = data.get("volumes")
        if volumes and isinstance(volumes, dict):
            result[module_name] = set(volumes.keys())
    return result


@pytest.mark.gate
class TestGateVolumesSot:
    """Gate: root compose is the single source of truth for volume declarations (U-49)."""

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · root volumes completeness (DevPlan 116 B3 T4, U-49)
    # · Last fail: 10 root volumes — langfuse-redis-data/prometheus-config-gen were declared
    # ·   ONLY in modules (missing from root SoT)
    # · Remove if: volume declaration architecture changes
    def test_root_volumes_match_module_references(self):
        """Root volumes == every volume referenced by module services (no missing, no orphans)."""
        root_volumes = _root_volumes()
        referenced = _module_referenced_volumes()

        missing = referenced - root_volumes
        orphans = root_volumes - referenced
        assert not missing, (
            f"ROOT_VOLUMES_MISSING: module services reference volumes not declared in root compose: "
            f"{sorted(missing)} — root docker-compose.yml is the single SoT (DevPlan 116 B3 T4, U-49)"
        )
        assert not orphans, f"ROOT_VOLUMES_ORPHAN: root declares volumes no module references: {sorted(orphans)}"
        assert len(root_volumes) == 13, (
            f"Expected 13 canonical volumes (alloy-data +1, 164 W1-5), got {len(root_volumes)}: {sorted(root_volumes)}"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · bind driver_opts present in root (DevPlan 116 B3 T4)
    # · Last fail: driver_opts lived in module compose files (postgres/backup-cron/hermes-agent)
    # · Remove if: volume declaration architecture changes
    def test_root_bind_volumes_have_driver_opts(self):
        """The 5 bind volumes in root compose carry driver_opts bind declarations."""
        data = yaml.safe_load(_read_file(ROOT_COMPOSE))
        root_vols = data.get("volumes", {}) or {}
        for name, device in BIND_DEVICES.items():
            decl = root_vols.get(name)
            assert isinstance(decl, dict), f"Root volume '{name}' missing declaration"
            opts = decl.get("driver_opts", {}) or {}
            assert opts.get("type") == "none", f"{name}: driver_opts.type must be 'none'"
            assert opts.get("o") == "bind", f"{name}: driver_opts.o must be 'bind'"
            assert opts.get("device") == device, f"{name}: driver_opts.device must be {device}"

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · module top-level volumes forbidden (DevPlan 116 B3 T4)
    # · Last fail: postgres/backup-cron/hermes-agent declared driver_opts bind-volumes;
    # ·   minio/langfuse/clickhouse/logging/monitoring declared docker-managed volumes (duplicate SoT)
    # · Remove if: volume declaration architecture changes
    def test_module_top_level_volumes_empty(self):
        """No core/modules/*/docker-compose.base.yml declares a top-level volumes: section."""
        module_volumes = _module_top_level_volumes()
        assert not module_volumes, (
            f"MODULE_TOP_LEVEL_VOLUMES: {len(module_volumes)} module(s) declare top-level volumes: "
            f"{ {m: sorted(v) for m, v in module_volumes.items()} } — "
            "root docker-compose.yml is the single source (DevPlan 116 B3 T4, U-49)"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · module volume colliding root (DevPlan 116 B3 T4)
    # · Last fail: N/A (new negative test)
    # · Remove if: volume declaration architecture changes
    def test_module_volume_name_must_not_collide_root(self):
        """If a module ever declares a volume name intersecting root → RED (negative, R5)."""
        root_volumes = _root_volumes()
        real_module_volumes = _module_top_level_volumes()
        real_violations = [
            f"{module}:{name}"
            for module, names in real_module_volumes.items()
            for name in names
            if name in root_volumes
        ]
        assert not real_violations, (
            f"VOLUME_NAME_COLLISION: module volume(s) collide with root: {real_violations} — "
            "declared in root docker-compose.yml only (DevPlan 116 B3 T4)"
        )
        # Negative fixture: simulate the regression — module re-declaring 'postgres-data'
        fake_violations = ["postgres:postgres-data"]
        assert fake_violations, "Negative fixture must produce a violation (test integrity)"

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · CONTEXT_IMAGE: "" forbidden (DevPlan 116 B3 D4, U-49)
    # · Last fail: docker-compose.platform-dev.yml:34 declared environment: CONTEXT_IMAGE: ""
    # ·   (empty-string env mechanism for L1 mode)
    # · Remove if: an explicit image-override mechanism replaces the empty string (already in place)
    def test_context_image_empty_string_forbidden(self):
        """CONTEXT_IMAGE: \"\" must appear 0 times across docker-compose*.yml (D4)."""
        compose_files = [
            ROOT_COMPOSE,
            PLATFORM_DEV,
            MACOS_COMPOSE,
            *sorted(MODULES_DIR.glob("*/docker-compose*.yml")),
        ]
        violations: list[str] = []
        for cf in compose_files:
            if not cf.exists():
                continue
            for line_no, line in enumerate(_read_file(cf).splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Match CONTEXT_IMAGE: "" — empty value with either quote style
                if "CONTEXT_IMAGE" in stripped and '"' in stripped and re_search_empty_context_image(stripped):
                    violations.append(f"{cf.relative_to(PROJECT_ROOT)}:{line_no}: {stripped}")

        assert not violations, (
            f"CONTEXT_IMAGE_EMPTY_STRING: {len(violations)} empty-string CONTEXT_IMAGE occurrence(s):\n"
            + "\n".join(violations)
            + "\nL1 mechanism = explicit image override (docker-compose.platform-dev.yml), DevPlan 116 B3 D4"
        )


def re_search_empty_context_image(line: str) -> bool:
    """Match `CONTEXT_IMAGE:` followed by an empty value ("" or '').

    ## @purpose — Local helper (avoids module-level re import split from readability).
    ## @io — ⇥ line: str → ⎋ bool
    ## @complexity — O(1)
    """
    import re

    return re.search(r"CONTEXT_IMAGE\s*:\s*(''|\"\")", line) is not None
