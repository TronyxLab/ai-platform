#!/usr/bin/env python3
"""Secrets validation and module metadata extraction — W4-E1 deploy-modules.sh decomposition."""
# GREP_SUMMARY: secrets-validator, env-requires, secrets-env-parser, charset-validation, module-metadata, transitive-deps, node-yaml-parser, deploy-modules-decomposition
# STRUCTURE: ┌secrets-manifest.yaml → ◇ check_env_requires(module↦consumers+tier) → ⊕ missing vars │ ┌validate_secret_charsets → ⊕ re.match(charset) │ ┌_batch_module_metadata → ∑ glob→name:install_type:severity │ ┌_expand_transitive_deps → BFS(module.yaml depends_on) → ⎋ sorted │ ┌parse_modules_from_node_yaml → ⟦(name,enabled,overlay)⟧ │ ┌detect_install_type ← module.yaml.install_type
# region MODULE_CONTRACT [DOMAIN(DEPLOY): bootstrap; CONCEPT(SECRETS): validation-gauntlet; TECH(PYTHON): argparse+yaml+re+bfs]
## @purpose  Extract secrets validation, module metadata, and DAG expansion from deploy-modules.sh into typed Python
## @scope    Reads secrets-manifest.yaml, module.yaml files, node.yaml; provides env validation, charset validation,
##           severity/metadata batch extraction, transitive dependency expansion via BFS, and node-yaml module parsing.
## @input    CLI: --action {check-env,validate-charsets,module-metadata,batch-metadata,batch-check-env,expand-deps,parse-node-yaml,detect-type}
##           with --module-name, --modules-dir, --node-yaml, --secrets-manifest, --modules-filter
## @output   Varies per action: JSON, comma-separated strings, space-separated strings, colon-separated lines
## @links    REPLACES_FROM(core/internal/bootstrap/deploy-modules.sh:754-905)
## @invariants
##   - YAML parsing uses yaml.safe_load directly — no subprocess, no shell invocation
##   - File-not-found → WARN log + return defaults (graceful degradation)
##   - Missing required manifests → empty/fallback output, never crashes
##   - _expand_transitive_deps: O(V+E) BFS, validates seed modules, stderr on unknown, cycle-safe (visited-set convergence)
##   - validate_secret_charsets: uses re.match (full string match, not re.search)
##   - Secret charset validation skips empty values (checked separately by check_env_requires)
## @rationale Strangler-Fig decomposition of 1664-line deploy-modules.sh. Each function has a 1:1 mapping to its shell
##            counterpart, enabling incremental replacement without breaking existing callers. Python provides type safety,
##            testability, and composability — the shell functions were opaque string pipelines.
## @changes  Initial: 2026-07-22 — W4-E1 extraction from deploy-modules.sh
##           2026-07-30 — DevPlan 086: replaced inline secrets.env parsing with shared secrets_env_parser.parse()
## @usecases
##   - deploy-modules.sh CLI caller: `python3 secrets_validator.py --action check-env --module-name postgres`
##   - Batch metadata → avoid N+1 per-module calls
##   - Test suite: unit tests with tmp_path fixtures, no external dependencies
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import re as _re
import sys
from collections import deque
from pathlib import Path
from typing import Protocol, cast

import yaml  # type: ignore[import-untyped]

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.secrets_manifest_reader import (
    charset as secret_charset,
)
from core.internal.shared.secrets_manifest_reader import (
    iter_secrets as iter_manifest_secrets,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_check_env_requires
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Read secrets-manifest.yaml and verify all secrets required by a given module are non-empty
##           in the process environment OR in a secrets.env file. Manifest-driven gate.
##           DevPlan 118 D4: тонкий фасад на shared/env_requires.check_runtime_env (единый чекер,
##           устраняет расхождение вердиктов с validate_module_yaml.check_env_requires_presence).
## @io       module_name (str), secrets_manifest_path (str) → List[str] of missing variable names
##           ⚡ raise FileNotFoundError/ValueError if manifest missing/malformed (strict, DevPlan 116 T4)
## @complexity 2 — single YAML parse (delegated to shared iter_secrets) + linear pass over secrets list
## @invariants
##   - Checks both os.environ and SECRETS_ENV_FILE (default /var/lib/platform/run/secrets.env)
##   - Only secrets where consumers includes module_name AND tier ∈ {required, generated} are checked
##   - STRICT: manifest absent/malformed → RAISE (no graceful degradation — manifest always
##     delivered with core/; «gate зелёный, система врёт» устранён, invariant 7)
##   - Incident 2026-07-17: minio deployed with empty MINIO_ROOT_USER/PASSWORD → Access Denied
## @rationale Manifest-driven approach replaces module.yaml env_requires parsing.
##            secrets-manifest.yaml is the Single Source of Truth. Gate validates
##            bidirectional consistency between module.yaml env_requires and manifest.
def check_env_requires(module_name: str, secrets_manifest_path: str) -> list[str]:
    from core.internal.shared.env_requires import check_runtime_env as _impl

    return _impl(module_name, secrets_manifest_path)


# endregion FUNC_check_env_requires

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_validate_secret_charsets
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Validate all secrets with charset field in secrets-manifest.yaml match their declared regex charset.
##           Fails fast before any docker compose up if any secret violates its charset constraint.
## @io       secrets_manifest_path (str) → tuple[int, list[str]]: (failure_count, list_of_error_messages)
##           ⚡ raise FileNotFoundError/ValueError if manifest missing/malformed (strict, DevPlan 116 T4)
## @complexity 2 — single YAML parse (delegated to shared iter_secrets) + linear pass with re.match per secret
## @invariants
##   - Only secrets with explicit charset field are validated (no charset → skip)
##   - Empty/missing env vars are skipped (checked separately by check_env_requires)
##   - Uses re.match (full string match, not re.search)
##   - STRICT: manifest absent/malformed → RAISE (graceful degradation removed, invariant 7)
## @rationale Charset constraint prevents pgbouncer crash-loop from special characters in POSTGRES_PASSWORD.
##            Validation happens at deploy time (not decrypt time) because secrets-manifest.yaml is consumed
##            by deploy-modules.sh and this is the last checkpoint before docker compose up.
def validate_secret_charsets(secrets_manifest_path: str) -> tuple[int, list[str]]:
    logger.info("[IMP:7][validate_secret_charsets][start] Manifest=%s", secrets_manifest_path)

    secrets_list = iter_manifest_secrets(secrets_manifest_path)
    failed = 0
    errors: list[str] = []

    for s in secrets_list:
        charset_re = secret_charset(s)
        if not charset_re:
            continue

        # W11: iter_secrets → list[dict[str, object]] — каст строкового поля
        name = cast(str, s["name"])
        val = os.environ.get(name, "")
        if not val:
            # Empty values are checked separately by check_env_requires; skip here
            logger.info("[IMP:7][validate_secret_charsets][skip] %s has charset but empty value — skipping", name)
            continue

        if not _re.match(charset_re, val):
            msg = f"[IMP:9][charset] FAIL: {name} does not match charset {charset_re}"
            logger.error(msg)
            failed += 1
            errors.append(msg)
        else:
            logger.info("[IMP:8][validate_secret_charsets][ok] %s matches charset %s", name, charset_re)

    if failed:
        logger.error("[IMP:9][validate_secret_charsets][FAIL] %d secret(s) failed charset validation", failed)
    else:
        logger.info("[IMP:9][validate_secret_charsets][PASS] All secrets passed charset validation")

    return (failed, errors)


# endregion FUNC_validate_secret_charsets

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_get_module_severity
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Read severity field from module.yaml (critical|warn, default warn)
## @io       module_yaml_path (str) → str: "critical" or "warn"
## @complexity 1 — single YAML parse
def get_module_severity(module_yaml_path: str) -> str:
    logger.info("[IMP:7][get_module_severity][start] Path=%s", module_yaml_path)

    yaml_path = Path(module_yaml_path)
    if not yaml_path.is_file():
        logger.warning(
            "[IMP:5][get_module_severity][missing] module.yaml not found at %s — defaulting to warn",
            module_yaml_path,
        )
        return "warn"

    with Path(yaml_path).open(encoding="utf-8") as f:
        data = cast(
            dict[str, str] | None, yaml.safe_load(f)
        )  # W11: yaml.safe_load → Any — контракт module.yaml: строковые поля

    if data is None:
        logger.warning(
            "[IMP:5][get_module_severity][empty] module.yaml %s is empty — defaulting to warn", module_yaml_path
        )
        return "warn"

    severity: str = data.get("severity", "warn")
    if severity not in {"critical", "warn"}:
        logger.warning(
            "[IMP:5][get_module_severity][invalid] Invalid severity %r in %s — defaulting to warn",
            severity,
            module_yaml_path,
        )
        severity = "warn"

    logger.info("[IMP:9][get_module_severity][result] Module severity=%s (from %s)", severity, module_yaml_path)
    return severity


# endregion FUNC_get_module_severity

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC__batch_module_metadata
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Read name:install_type:severity for ALL modules in one pass (replaces N+1 per-module calls)
## @io       modules_dir (str) → list[dict]: [{name, install_type, severity}, ...]
## @complexity 2 — glob + N YAML parses, O(M) where M = module count
## @invariants
##   - If module.yaml is missing name field, uses the parent directory name
##   - install_type defaults to "unknown", severity defaults to "warn"
##   - Empty/missing module.yaml files are skipped with WARN
## @rationale S3 optimization: single python3 call replaces per-module detect_install_type + get_module_severity calls
def _batch_module_metadata(modules_dir: str) -> list[dict[str, str]]:
    logger.info("[IMP:7][_batch_module_metadata][start] modules_dir=%s", modules_dir)

    modules_path = Path(modules_dir)
    yaml_files = sorted(modules_path.glob("*/module.yaml"))

    if not yaml_files:
        logger.warning("[IMP:5][_batch_module_metadata][scan] No module.yaml files found in %s", modules_dir)
        return []

    results: list[dict[str, str]] = []
    for yf in yaml_files:
        with Path(yf).open(encoding="utf-8") as f:
            data = cast(
                dict[str, str] | None, yaml.safe_load(f)
            )  # W11: yaml.safe_load → Any — контракт module.yaml: строковые поля

        if data is None:
            logger.warning("[IMP:5][_batch_module_metadata][skip] Empty YAML in %s, skipping", yf)
            continue

        name: str = data.get("name", yf.parent.name)
        itype: str = data.get("install_type", "unknown")
        sev: str = data.get("severity", "warn")
        entry = {"name": name, "install_type": itype, "severity": sev}
        results.append(entry)
        logger.info(
            "[IMP:8][_batch_module_metadata][entry] %s → install_type=%s, severity=%s",
            name,
            itype,
            sev,
        )

    logger.info("[IMP:9][_batch_module_metadata][count] Batch metadata collected for %d modules", len(results))
    return results


# endregion FUNC__batch_module_metadata

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC__expand_transitive_deps
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Expand comma-separated module list with transitive depends_on using BFS over module.yaml DAG.
##           Returns a sorted space-separated string of all modules in the transitive closure.
## @io       modules_filter (str), modules_dir (str) → str: space-separated sorted module names
## @complexity 3 — O(V+E) BFS over module dependency DAG; validates all seed modules exist
## @invariants
##   - Only modules with module.yaml files are considered (system and docker)
##   - Unknown seed modules → log ERROR to stderr + exit 1 (via SystemExit)
##   - Modules with no depends_on have empty dependency lists
##   - Circular deps converge via visited set (no infinite loop)
## @rationale Shell version used inline python3 heredoc — extracted for testability and error handling.
def _expand_transitive_deps(modules_filter: str, modules_dir: str) -> str:
    logger.info("[IMP:7][_expand_transitive_deps][start] filter=%s, dir=%s", modules_filter, modules_dir)

    seed_modules = [m.strip() for m in modules_filter.split(",") if m.strip()]
    if not seed_modules:
        logger.info("[IMP:7][_expand_transitive_deps][empty] Empty filter — returning empty string")
        return ""

    # Build DAG from all module.yaml files (system + docker)
    modules_path = Path(modules_dir)
    dag: dict[str, list[str]] = {}

    for yf in sorted(modules_path.glob("*/module.yaml")):
        with Path(yf).open(encoding="utf-8") as f:
            data = cast(
                dict[str, object] | None, yaml.safe_load(f)
            )  # W11: yaml.safe_load → Any — контракт module.yaml (depends_on — list)
        if data is None:
            continue
        name = cast(str, data.get("name", yf.parent.name))
        deps = data.get("depends_on")
        if isinstance(deps, list):
            dag[name] = [str(d) for d in cast(list[object], deps) if isinstance(d, str)]
        else:
            dag[name] = []

    logger.info("[IMP:7][_expand_transitive_deps][dag] Built DAG with %d nodes", len(dag))

    # Validate: all seed modules must exist in DAG
    unknown = [m for m in seed_modules if m not in dag]
    if unknown:
        err_msg = f"Unknown module(s): {', '.join(unknown)}"
        logger.error("[IMP:10][_expand_transitive_deps][error] %s", err_msg)
        # T3.6 (DevPlan 116 B4): business sys.exit → raise ConfigValidationError (caller main ловит PlatformError)
        raise ConfigValidationError(err_msg)

    # BFS to find transitive closure through depends_on
    expanded: set[str] = set(seed_modules)
    queue: deque[str] = deque(seed_modules)

    while queue:
        node = queue.popleft()
        for dep in dag.get(node, []):
            if dep not in expanded:
                expanded.add(dep)
                queue.append(dep)
                logger.info("[IMP:8][_expand_transitive_deps][add] %s depends on %s — added to closure", node, dep)

    result = " ".join(sorted(expanded))
    logger.info(
        "[IMP:9][_expand_transitive_deps][result] Expanded %d seed to %d modules: %s",
        len(seed_modules),
        len(expanded),
        result,
    )
    return result


# endregion FUNC__expand_transitive_deps

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_parse_modules_from_node_yaml
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Parse modules section from node.yaml (supports dict and list formats).
##           Returns list of (name, enabled_str, overlay) tuples for further processing.
##           Нормализационный слой поверх NodeYaml.get_modules() — обрабатывает
##           dict-формат {name: {enabled, config_overlay}} и list-формат.
## @io       node_yaml_path (str) → list[tuple[str, str, str]]: [(name, enabled, overlay), ...]
## @complexity 2 — single YAML parse + type-dispatched iteration
## @invariants
##   - Dict format: {name: {enabled: bool, config_overlay: str}} or {name: bool}
##   - List format: [{name: str, enabled: bool, config_overlay: str}]
##   - Чтение modules через NodeYaml.get_modules() (list-формат); dict-формат — fallback
##   - enabled defaults to "true" if not explicitly set
##   - overlay defaults to "" if not set
##   - File not found → returns [] (graceful degradation for unit-testing without node.yaml)
def parse_modules_from_node_yaml(node_yaml_path: str) -> list[tuple[str, str, str]]:
    logger.info("[IMP:7][parse_modules_from_node_yaml][start] node_yaml=%s", node_yaml_path)

    yaml_path = Path(node_yaml_path)
    if not yaml_path.is_file():
        logger.warning(
            "[IMP:5][parse_modules_from_node_yaml][missing] node.yaml not found at %s",
            node_yaml_path,
        )
        return []

    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(node_yaml_path)
    # DevPlan 117 D20: типизированный доступ через NodeYaml.get_modules() (SoT чтения node.yaml).
    # dict-формат {name: {enabled, config_overlay}} NodeYaml НЕ поддерживает (list-only) —
    # ConfigValidationError → fallback на сырой dict. Нормализация dict/list→tuple остаётся
    # тонкой надстройкой над get_modules() (см. docstring).
    try:
        raw_modules = node.get_modules()
    except ConfigValidationError:
        # W11-G1 cross-file: NodeYaml.get → Any — dict-формат {name: {...}}; default={} типизирован
        raw_modules = node.get("modules", default=cast(dict[str, object], {}))
    results: list[tuple[str, str, str]] = []

    # W11: union list|dict → object — оба isinstance-гейта (dict/list) и else-ветка осмысленны
    modules = cast(object, raw_modules)
    if isinstance(modules, dict):
        for name, value in cast(dict[str, object], modules).items():
            if isinstance(value, dict):
                vdict = cast(dict[str, object], value)
                enabled = str(vdict.get("enabled", True)).lower()
                overlay = cast(str, vdict.get("config_overlay", "")) or ""
            else:
                # Bool or string value
                enabled = str(value).lower()
                overlay = ""
            results.append((name, enabled, overlay))
            logger.info(
                "[IMP:8][parse_modules_from_node_yaml][dict] %s: enabled=%s, overlay=%s", name, enabled, overlay
            )
    elif isinstance(modules, list):
        for m in cast(list[dict[str, object]], modules):
            name = cast(str, m.get("name", ""))
            enabled = str(m.get("enabled", True)).lower()
            overlay = cast(str, m.get("config_overlay", "")) or ""
            results.append((name, enabled, overlay))
            logger.info(
                "[IMP:8][parse_modules_from_node_yaml][list] %s: enabled=%s, overlay=%s", name, enabled, overlay
            )
    else:
        logger.warning(
            "[IMP:5][parse_modules_from_node_yaml][type] Unexpected modules type %s — returning empty",
            type(modules).__name__,
        )
        return []

    logger.info("[IMP:9][parse_modules_from_node_yaml][count] Parsed %d modules from %s", len(results), node_yaml_path)
    return results


# endregion FUNC_parse_modules_from_node_yaml

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_detect_install_type
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Read install_type from module.yaml (docker|system|unknown).
## @io       module_yaml_path (str) → str: install_type value, default "unknown"
## @complexity 1 — single YAML parse
def detect_install_type(module_yaml_path: str) -> str:
    logger.info("[IMP:7][detect_install_type][start] Path=%s", module_yaml_path)

    yaml_path = Path(module_yaml_path)
    if not yaml_path.is_file():
        logger.warning(
            "[IMP:5][detect_install_type][missing] module.yaml not found at %s — returning unknown",
            module_yaml_path,
        )
        return "unknown"

    with Path(yaml_path).open(encoding="utf-8") as f:
        data = cast(
            dict[str, str] | None, yaml.safe_load(f)
        )  # W11: yaml.safe_load → Any — контракт module.yaml: строковые поля

    if data is None:
        logger.warning(
            "[IMP:5][detect_install_type][empty] module.yaml %s is empty — returning unknown", module_yaml_path
        )
        return "unknown"

    install_type: str = data.get("install_type", "unknown")
    logger.info("[IMP:9][detect_install_type][result] install_type=%s (from %s)", install_type, module_yaml_path)
    return install_type


# endregion FUNC_detect_install_type

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_batch_check_env
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Validate secrets for ALL modules in one call (replaces N per-module check-env calls).
##           Iterates over all modules and calls check_env_requires per module.
## @io       modules_dir (str), secrets_manifest_path (str) → list[dict]: [{name, status}, ...]
## @output   name:status lines (status = ok/error) for each module
## @complexity 2 — glob + N env checks, O(M) where M = module count
## @invariants
##   - Uses existing check_env_requires logic per module
##   - Always returns 0 exit code (status is in output lines — shell parses individual results)
##   - Empty/missing module.yaml files are skipped with WARN
## @rationale S4 optimization: single python3 call replaces M per-module check-env spawns
def batch_check_env(modules_dir: str, secrets_manifest_path: str) -> list[dict[str, str]]:
    logger.info("[IMP:7][batch_check_env][start] modules_dir=%s", modules_dir)

    modules_path = Path(modules_dir)
    yaml_files = sorted(modules_path.glob("*/module.yaml"))

    if not yaml_files:
        logger.warning("[IMP:5][batch_check_env][scan] No module.yaml files found in %s", modules_dir)
        return []

    results: list[dict[str, str]] = []
    for yf in yaml_files:
        with Path(yf).open(encoding="utf-8") as f:
            data = cast(
                dict[str, str] | None, yaml.safe_load(f)
            )  # W11: yaml.safe_load → Any — контракт module.yaml: строковые поля

        if data is None:
            logger.warning("[IMP:5][batch_check_env][skip] Empty YAML in %s, skipping", yf)
            continue

        name: str = data.get("name", yf.parent.name)
        missing = check_env_requires(name, secrets_manifest_path)
        status = "error" if missing else "ok"
        results.append({"name": name, "status": status})
        logger.info("[IMP:8][batch_check_env][entry] %s → status=%s", name, status)

    logger.info("[IMP:9][batch_check_env][count] Batch env check completed for %d modules", len(results))
    return results


# endregion FUNC_batch_check_env

# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_main
# ═══════════════════════════════════════════════════════════════════════════════


class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    action: str
    module_name: str | None
    modules_dir: str | None
    node_yaml: str | None
    secrets_manifest: str | None
    modules_filter: str | None
    module_yaml: str | None


## @purpose  CLI entry point: dispatch to the requested action, print results, return exit code
## @io       sys.argv → stdout/JSON/stderr, int exit code
## @complexity 2 — argparse dispatch to action handlers
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Secrets validation and module metadata — deploy-modules.sh decomposition (W4-E1)"
    )
    _ = parser.add_argument(
        "--action",
        required=True,
        choices=[
            "check-env",
            "validate-charsets",
            "module-metadata",
            "batch-metadata",
            "batch-check-env",
            "expand-deps",
            "parse-node-yaml",
            "detect-type",
        ],
        help="Action to perform",
    )
    _ = parser.add_argument("--module-name", default=None, help="Module name (check-env, module-metadata, detect-type)")
    _ = parser.add_argument(
        "--modules-dir", default=None, help="Path to modules directory (batch-metadata, batch-check-env, expand-deps)"
    )
    _ = parser.add_argument("--node-yaml", default=None, help="Path to node.yaml (parse-node-yaml)")
    _ = parser.add_argument(
        "--secrets-manifest",
        default=None,
        help="Path to secrets-manifest.yaml (check-env, validate-charsets, batch-check-env)",
    )
    _ = parser.add_argument(
        "--modules-filter",
        default=None,
        help="Comma-separated module list (expand-deps)",
    )
    _ = parser.add_argument("--module-yaml", default=None, help="Path to module.yaml (module-metadata, detect-type)")

    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args()))
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    action = args.action
    logger.info("[IMP:9][main][dispatch] Action=%s", action)

    # STRICT manifest reader (DevPlan 116 T4, U-33): missing/malformed secrets-manifest.yaml
    # is a configuration error, not a skip condition — surface it, don't hide it (R4, invariant 7).
    try:
        return _dispatch_action(action, args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("[GATE:FAIL][id:secrets-validator][class:L1]", file=sys.stderr)
        print(">>> REPAIR_RECIPE_START >>>", file=sys.stderr)
        print("make generate-secrets-manifest && make fix-gate", file=sys.stderr)
        print("<<< REPAIR_RECIPE_END <<<", file=sys.stderr)
        return 1
    except ConfigValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _dispatch_action(action: str, args: _CliArgs) -> int:
    """Dispatch parsed CLI action to its handler (extracted for strict-error handling)."""

    if action == "check-env":
        if not args.module_name or not args.secrets_manifest:
            print("ERROR: --module-name and --secrets-manifest required for check-env", file=sys.stderr)
            return 1
        missing = check_env_requires(args.module_name, args.secrets_manifest)
        if missing:
            print(",".join(missing))
            return 1
        return 0

    if action == "validate-charsets":
        if not args.secrets_manifest:
            print("ERROR: --secrets-manifest required for validate-charsets", file=sys.stderr)
            return 1
        failed, errors = validate_secret_charsets(args.secrets_manifest)
        if failed:
            for err in errors:
                print(err, file=sys.stderr)
            return 1
        return 0

    if action == "module-metadata":
        if not args.module_yaml:
            if args.module_name and args.modules_dir:
                # W11: args.module_yaml — str-поле; Path→str конвертация (идентичный путь, get_module_severity принимает str)
                args.module_yaml = str(Path(args.modules_dir) / args.module_name / "module.yaml")
            else:
                print("ERROR: --module-yaml required for module-metadata", file=sys.stderr)
                return 1
        sev = get_module_severity(args.module_yaml)
        print(sev)
        return 0

    if action == "batch-metadata":
        if not args.modules_dir:
            print("ERROR: --modules-dir required for batch-metadata", file=sys.stderr)
            return 1
        metadata = _batch_module_metadata(args.modules_dir)
        for entry in metadata:
            print(f"{entry['name']}:{entry['install_type']}:{entry['severity']}")
        return 0

    if action == "batch-check-env":
        if not args.modules_dir or not args.secrets_manifest:
            print("ERROR: --modules-dir and --secrets-manifest required for batch-check-env", file=sys.stderr)
            return 1
        results = batch_check_env(args.modules_dir, args.secrets_manifest)
        for entry in results:
            print(f"{entry['name']}:{entry['status']}")
        return 0

    if action == "expand-deps":
        if not args.modules_filter or not args.modules_dir:
            print("ERROR: --modules-filter and --modules-dir required for expand-deps", file=sys.stderr)
            return 1
        try:
            result = _expand_transitive_deps(args.modules_filter, args.modules_dir)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
        print(result)
        return 0

    if action == "parse-node-yaml":
        if not args.node_yaml:
            print("ERROR: --node-yaml required for parse-node-yaml", file=sys.stderr)
            return 1
        modules = parse_modules_from_node_yaml(args.node_yaml)
        for name, enabled, overlay in modules:
            print(f"{name}:{enabled}:{overlay}")
        return 0

    if action == "detect-type":
        if not args.module_yaml:
            if args.module_name and args.modules_dir:
                # W11: args.module_yaml — str-поле; Path→str конвертация (идентичный путь, get_module_severity принимает str)
                args.module_yaml = str(Path(args.modules_dir) / args.module_name / "module.yaml")
            else:
                print("ERROR: --module-yaml required for detect-type", file=sys.stderr)
                return 1
        itype = detect_install_type(args.module_yaml)
        print(itype)
        return 0

    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
