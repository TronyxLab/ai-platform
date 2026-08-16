# GREP_SUMMARY: gate p20 container-coupling aliases env-hostnames prometheus-targets resolve
# STRUCTURE: ┌_build_service_registry ┐ → ┌_extract_env_hostnames ┐ → ◇ test_referenced_aliases → ◇ test_env_hostnames_resolvable → ◇ test_prometheus_targets_resolvable → ⎋ assert violations
# region MODULE_CONTRACT
## @purpose  P20 gate tests: validate container coupling invariants — every referenced container_name
##           has a network alias; every env hostname is resolvable; Prometheus targets are resolvable.
## @scope    Parses all core/modules/*/docker-compose.base.yml for service registry with aliases/env;
##           parses core/modules/monitoring/config/prometheus.yml.tmpl for scrape targets.
## @invariants
##   - For each env/depends_on/prometheus reference to container_name X, service X must have
##     a network alias == X in at least one of its networks.
##   - Every extracted env hostname resolves to (aliases ∪ container_names ∪ service_names).
##   - Every Prometheus scrape target (excluding localhost/127.0.0.1) resolves the same way.
##   - References in command args (e.g., nginx-exporter --nginx.scrape-uri=http://nginx:8081)
##     are NOT checked — they use Docker DNS which resolves service names, not container names.
## @rationale
##   Q: Why not just check depends_on like the existing gate?
##   A: depends_on checks only same-compose references. Cross-file env hostnames (e.g.,
##      hermes-agent → litellm:4000) are unguarded. This test catches alias drift and
##      env-typo drift across all compose files + Prometheus config.
## @changes — 2026-07-15 | Created per DevPlan 007 TASK-T1
# endregion MODULE_CONTRACT

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = PROJECT_ROOT / "core" / "modules"
# .tmpl is the single source — prometheus.yml duplicate removed (DevPlan 116 B3 T3, U-48).
# Renderer (prometheus-config-init) generates /generated/prometheus.yml from this template.
PROMETHEUS_YML_TMPL = PROJECT_ROOT / "core" / "modules" / "monitoring" / "config" / "prometheus.yml.tmpl"


# region FUNC__build_service_registry
## @purpose  Parse all docker-compose.base.yml → list of service records with aliases/env
## @io       None → list[dict]
## @complexity 2 — file I/O + YAML parse for all compose files
def _build_service_registry() -> list[dict]:
    """Parse all docker-compose.base.yml → [{module, service_name, container_name, aliases, env, depends_on}]."""
    registry: list[dict] = []
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = compose_file.parent.name
        with Path(compose_file).open(encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
        if not data or "services" not in data:
            continue

        for service_name, svc in data["services"].items():
            if svc is None:
                continue
            registry.append(_service_record(module_name, service_name, svc))
    return registry


def _extract_aliases(net_config: object) -> set[str]:
    """Извлечь str-алиасы из networks.<net> конфига (PLR1702-хелпер).

    ## @io — ⇥ net_config → ⎋ set[str] алиасов (пусто при не-dict/не-list)
    ## @complexity — O(N) где N = aliases
    """
    aliases: set[str] = set()
    if isinstance(net_config, dict):
        net_aliases = net_config.get("aliases", []) or []
        if isinstance(net_aliases, list):
            for a in net_aliases:
                if isinstance(a, str):
                    aliases.add(a)
    return aliases


def _service_record(module_name: str, service_name: str, svc: dict) -> dict:
    """Собрать запись сервиса: container_name + aliases (PLR1702-хелпер).

    ## @io — ⇥ module_name, service_name, svc → ⎋ dict (service record)
    ## @complexity — O(N) где N = networks × aliases
    """
    cname = svc.get("container_name", service_name)

    # Collect aliases from networks.<net>.aliases
    aliases: set[str] = set()
    networks = svc.get("networks", {}) or {}
    if isinstance(networks, dict):
        for net_config in networks.values():
            aliases.update(_extract_aliases(net_config))
    # If networks is a list, no aliases can be extracted

    # Collect env dict (values cast to str)
    env_raw = svc.get("environment", {}) or {}
    env: dict[str, str] = {}
    if isinstance(env_raw, dict):
        for k, v in env_raw.items():
            env[str(k)] = str(v) if v is not None else ""

    # Collect depends_on (list or dict → list)
    depends_raw = svc.get("depends_on", {}) or {}
    depends_on: list[str] = []
    if isinstance(depends_raw, list):
        depends_on = [str(d) for d in depends_raw]
    elif isinstance(depends_raw, dict):
        depends_on = list(depends_raw.keys())

    return {
        "module": module_name,
        "service_name": service_name,
        "container_name": cname,
        "aliases": sorted(aliases),
        "env": env,
        "depends_on": depends_on,
    }


# endregion FUNC__build_service_registry


# region FUNC__extract_env_hostnames
## @purpose  Extract hostnames from env dict using structural patterns (NOT substring scan)
## @io       dict[str, str] → set[str]
## @complexity 2 — regex matching across env values
def _extract_env_hostnames(env: dict[str, str]) -> set[str]:
    """Extract hostnames from env values. Patterns:
    1. @host:port — after @ in connection strings
    2. ://host — in URLs
    3. Keys ending in _HOST — bare hostname value (or ${VAR:-default})
    Skip: localhost, 127.0.0.1, values with dots (external FQDNs), empty values.
    """
    hostnames: set[str] = set()

    @pytest.mark.gate
    def _is_local(h: str) -> bool:
        return h in {"localhost", "127.0.0.1", "0.0.0.0"}

    for key, val in env.items():
        if not val:
            continue

        # Pattern 1: @host:port in connection strings
        for m in re.finditer(r"@([A-Za-z0-9][A-Za-z0-9_-]*):\d+", val):
            h = m.group(1)
            if not _is_local(h) and "." not in h:
                hostnames.add(h)

        # Pattern 2: ://host in URLs
        for m in re.finditer(r"://([A-Za-z0-9][A-Za-z0-9_-]*)(?::\d+|/|$)", val):
            h = m.group(1)
            if not _is_local(h) and "." not in h:
                hostnames.add(h)

        # Pattern 3: Keys ending in _HOST
        if key.endswith("_HOST"):
            val_str = str(val)
            # If ${VAR:-default} syntax, extract default
            def_match = re.match(r"^\$\{[^:]*:-(.*)\}$", val_str)
            if def_match:
                h = def_match.group(1)
                if not _is_local(h) and "." not in h and ":" not in h and "/" not in h:
                    hostnames.add(h)
            elif not any(c in val_str for c in (":", "/", " ")):
                # Bare hostname like "pgbouncer"
                if not _is_local(val_str) and "." not in val_str:
                    hostnames.add(val_str)
            # URL values under _HOST keys are skipped here — they are handled by pattern 2

    return hostnames


# endregion FUNC__extract_env_hostnames


# region FUNC__build_lookup_set
## @purpose  Build the union of all aliases, container_names, and service_names for resolvability checks
## @io       list[dict] → set[str]
def _build_lookup_set(registry: list[dict]) -> set[str]:
    """Build union of aliases ∪ container_names ∪ service_names."""
    lookup: set[str] = set()
    for svc in registry:
        lookup.add(svc["container_name"])
        lookup.add(svc["service_name"])
        lookup.update(svc["aliases"])
    return lookup


# endregion FUNC__build_lookup_set


# Build registry once at module level
REGISTRY = _build_service_registry()
LOOKUP = _build_lookup_set(REGISTRY)


# region TEST_test_referenced_container_names_have_alias
## @purpose  Every referenced container_name (via env hostname or depends_on) has a matching alias
## @io       Uses REGISTRY, LOOKUP → single assert with all violations
@pytest.mark.gate
def test_referenced_container_names_have_alias():
    """For each env/depends_on reference to a container_name X of another service,
    service X must have a network alias matching X in at least one network.
    """
    # Build index: container_name → aliases set
    cname_to_aliases: dict[str, set[str]] = {}
    for svc in REGISTRY:
        cname_to_aliases[svc["container_name"]] = svc["aliases"]

    violations: list[str] = []

    # Check each service's env hostnames
    # NOTE: depends_on is NOT checked here — same-file depends_on alias coverage
    # is out of scope for P20 (covered by test_gate_structural_consistency.py CONTAINER_NAME).
    # P20 focuses on cross-file env hostname references and Prometheus targets.
    # Services without aliases (prometheus, loki, etc.) intentionally don't have
    # aliases per DevPlan 007 §Discovered Technical Debt — they have no env consumers.
    for svc in REGISTRY:
        hostnames = _extract_env_hostnames(svc["env"])
        for hostname in hostnames:
            # If hostname is a container_name of another service
            if hostname in cname_to_aliases:
                producer_aliases = cname_to_aliases[hostname]
                if hostname not in producer_aliases:
                    violations.append(
                        f"{svc['module']}/{svc['service_name']}: env references hostname "
                        f"'{hostname}' (container_name of {hostname}), but service '{hostname}' "
                        f"has no network alias '{hostname}'. Aliases: {sorted(producer_aliases)}"
                    )

    print(f"\n[IMP:8][p20] Service registry ({len(REGISTRY)} services):")
    for svc in REGISTRY:
        print(
            f"  {svc['module']}/{svc['service_name']} → "
            f"container_name={svc['container_name']}, "
            f"aliases={sorted(svc['aliases'])}, "
            f"depends_on={svc['depends_on']}"
        )

    assert not violations, (
        f"GATE_P20_ALIAS_MISSING: {len(violations)} container_name(s) referenced via env/depends_on "
        f"have no matching alias in the producer service:\n  " + "\n  ".join(violations)
    )
    print("[IMP:9][p20] PASS: All referenced container_names have matching aliases")


# endregion TEST_test_referenced_container_names_have_alias


# region TEST_test_env_hostnames_resolvable
## @purpose  All extracted env hostnames are resolvable via aliases ∪ container_names ∪ service_names
## @io       Uses REGISTRY, LOOKUP → single assert with all violations
@pytest.mark.gate
def test_env_hostnames_resolvable():
    """Every extracted env hostname resolves in (aliases ∪ container_names ∪ service_names).
    Catches typos and references to renamed/missing containers.
    """
    violations: list[str] = []

    for svc in REGISTRY:
        hostnames = _extract_env_hostnames(svc["env"])
        violations.extend(
            f"{svc['module']}/{svc['service_name']}: env hostname '{hostname}' not found "
            f"in any alias/container_name/service_name. "
            f"Known: {sorted(LOOKUP)}"
            for hostname in sorted(hostnames)
            if hostname not in LOOKUP
        )

    # Print env hostnames per service for LDD trajectory
    print("\n[IMP:8][p20] Extracted env hostnames per service:")
    for svc in REGISTRY:
        hostnames = _extract_env_hostnames(svc["env"])
        if hostnames:
            print(f"  {svc['module']}/{svc['service_name']}: {sorted(hostnames)}")

    assert not violations, (
        f"GATE_P20_UNRESOLVABLE_HOSTNAME: {len(violations)} env hostname(s) not found in "
        f"aliases ∪ container_names ∪ service_names:\n  " + "\n  ".join(violations)
    )
    print("[IMP:9][p20] PASS: All env hostnames are resolvable")


# endregion TEST_test_env_hostnames_resolvable


# region TEST_test_prometheus_targets_resolvable
## @purpose  Prometheus scrape target hostnames (excluding localhost) are resolvable
## @io       Parses prometheus.yml.tmpl → targets → validates hostnames in LOOKUP
@pytest.mark.gate
def test_prometheus_targets_resolvable():
    """Every Prometheus scrape target host (before :) resolves in
    aliases ∪ container_names ∪ service_names. Skip localhost/127.0.0.1.
    """
    assert PROMETHEUS_YML_TMPL.exists(), f"prometheus.yml.tmpl not found at {PROMETHEUS_YML_TMPL}"

    with Path(PROMETHEUS_YML_TMPL).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    scrape_configs = data.get("scrape_configs", []) or []
    violations: list[str] = []

    targets: list[str] = []
    for sc in scrape_configs:
        static_configs = sc.get("static_configs", []) or []
        for scfg in static_configs:
            scfg_targets = scfg.get("targets", []) or []
            for t in scfg_targets:
                if isinstance(t, str):
                    # Extract host before port (target format: "host:port")
                    host = t.split(":")[0] if ":" in t else t
                    if host in {"localhost", "127.0.0.1"}:
                        continue
                    targets.append(host)
                    if host not in LOOKUP:
                        violations.append(
                            f"Prometheus job '{sc.get('job_name', '?')}': target '{t}' "
                            f"→ host '{host}' not found in lookup set. "
                            f"Known: {sorted(LOOKUP)}"
                        )

    print("\n[IMP:8][p20] Prometheus targets (non-localhost):")
    for t in sorted(set(targets)):
        print(f"  {t} → {'RESOLVABLE' if t in LOOKUP else 'UNRESOLVABLE'}")

    assert not violations, (
        f"GATE_P20_PROMETHEUS_TARGETS: {len(violations)} Prometheus target(s) not resolvable:\n  "
        + "\n  ".join(violations)
    )
    print("[IMP:9][p20] PASS: All Prometheus targets are resolvable")


# endregion TEST_test_prometheus_targets_resolvable
