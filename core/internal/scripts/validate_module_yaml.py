#!/usr/bin/env python3
# GREP_SUMMARY: validate_module_yaml, D5-validator, jsonschema, env_requires-typed, restart-drift, static-cross-check, CLI
# STRUCTURE: ▶ load_module → ◇ normalize_env_requires (bare→object) → ⊕ validate_schema (jsonschema) → ◇ check_env_requires_presence (.env.example + secrets-manifest) → ◇ check_restart_drift (module.yaml ↔ base.yml) → ⊕ collect_violations → ⎋ main (--all | --module | --schema-strict)
# region MODULE_CONTRACT
## @purpose  D5-контракт валидатор для module.yaml (Strangler Tier-1 extraction per DevPlan 033 W3-E1).
##           Конвертирует D4-декларативный контракт в D5 machine-enforced через jsonschema + semantic cross-checks.
## @scope    CLI + importable functions; consumes core/schemas/module.schema.json (D5), core/secrets-manifest.yaml, .env.example,
##           docker-compose.base.yml (для restart-drift). Вызывается из `make validate-modules` и CI workflows.
## @invariants
##   - Exit 0 = все module.yaml валидны по D5-контракту (schema + presence + restart-drift)
##   - Exit 1 = обнаружено нарушение, подробности в stderr + LDD [IMP:9]
##   - Backward-compat: bare-string env_requires нормализуется в {type: secret, required: true}
##   - Не модифицирует файлы — read-only валидатор
##   - Negative-test path: --schema-strict детектирует D4/D5-нарушения
## @rationale
##   - jsonschema уже в deps (template_engine.py демонстрирует pattern) — principle 8 (расширение существующего)
##   - Principle 6 (Small Simple Blocks): каждый check — отдельная pure function, легко unit-тестируемая
##   - Strangler Tier-1: новый Python-модуль вместо inline python3 в shell (языковая политика AGENTS.md)
##   - P07 закрыт через статический cross-check (presence в .env.example + secrets-manifest) — дополняет
##     runtime-fail-fast через ${VAR:?error} (W3-E3 Option A)
##   - P08 закрыт через restart-drift detection (module.yaml.restart ↔ base.yml per-service restart)
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 033 W3-E1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

# region CONSTANTS

logger = logging.getLogger(__name__)

# Default paths — resolved relative to repo root (caller's CWD or module location)
_REPO_ROOT_CANDIDATES = [
    Path(__file__).resolve().parents[3],  # core/internal/scripts/ → repo root
    Path.cwd(),
]

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "module.schema.json"
DEFAULT_MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"
DEFAULT_ENV_EXAMPLE = None  # resolved dynamically (see _find_env_example)
DEFAULT_SECRETS_MANIFEST = Path(__file__).resolve().parents[2] / "secrets-manifest.yaml"

# Critical secrets W3-E3: must use ${VAR:?error} in docker-compose.base.yml (Option A collapse)
CRITICAL_SECRETS = {
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "LITELLM_MASTER_KEY",
    "MINIO_ROOT_PASSWORD",
}

# Default restart policy for production base-compose (severity:critical → always OK as carve-out)
BASE_ALLOWED_RESTART = {"always", "unless-stopped"}

# env_requires type enum (D5 schema)
ENV_REQUIRES_TYPES = {"string", "secret", "int", "bool"}

# endregion CONSTANTS


# region PRIVATE_HELPERS


def _find_repo_root() -> Path:
    """Find repo root by marker (heuristic: contains .git or AGENTS.md)."""
    for candidate in _REPO_ROOT_CANDIDATES:
        if (candidate / "AGENTS.md").exists() or (candidate / ".git").exists():
            return candidate
    # Fallback: assume script-path-derived root
    return _REPO_ROOT_CANDIDATES[0]


def _find_env_example() -> Path:
    """Resolve path to .env.example (repo root or platform/ overlay)."""
    root = _find_repo_root()
    candidate = root / ".env.example"
    if candidate.exists():
        return candidate
    # Fall back to script-relative if CWD differs
    return DEFAULT_ENV_EXAMPLE or candidate


def _load_yaml_file(path: Path) -> Any:
    """Load YAML file with safe_load. Raises FileNotFoundError/YAMLError on failure."""
    if not path.exists():
        raise FileNotFoundError(f"[validate_module_yaml] File not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _load_json_file(path: Path) -> Any:
    """Load JSON file. Raises FileNotFoundError/JSONDecodeError on failure."""
    if not path.exists():
        raise FileNotFoundError(f"[validate_module_yaml] Schema file not found: {path}")
    with open(path) as f:
        return json.load(f)


def _normalize_env_requires_entry(entry: Any) -> dict[str, Any]:
    """Normalize a single env_requires entry into D5 object form.

    Bare string "VAR_NAME" → {"name": "VAR_NAME", "type": "secret", "required": true}
    Object {"name": "VAR", "type": "secret"} → filled with defaults for missing fields
    """
    if isinstance(entry, str):
        return {"name": entry, "type": "secret", "required": True}
    if isinstance(entry, dict):
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(f"env_requires object missing 'name' field: {entry}")
        return {
            "name": name,
            "type": entry.get("type", "secret"),
            "required": entry.get("required", True),
        }
    raise ValueError(f"env_requires entry must be string or object, got {type(entry).__name__}: {entry}")


def _env_var_in_dotenv(env_example_path: Path, var_name: str) -> tuple[bool, str]:
    """Check presence and non-empty value of var in .env.example.

    Returns (present, value). present=False if var not declared.
    Lines like `VAR=` (no value) are treated as EMPTY unless preceded by a marker-comment
    that documents the variable as generated/SOPS-only at runtime (P07 enforcement with carve-out
    for legitimately-empty placeholders in .env.example).

    Recognized markers (in any comment line within the 5-line block above the var declaration,
    or in an inline comment after `#`):
      - `# GENERATED` / `# Генерация:` / `# generate` — value generated at runtime (secrets-init.sh, SOPS)
      - `# SOPS` / `# sops` — value provided via SOPS/age on VPS
      - `# NOT for production` — placeholder only, real value from SOPS
      - `# REQUIRED` — explicit acknowledgement (still must be set somewhere)
      - `# Инициализируется` / `# Заполняется` — Russian runtime-fill markers
    """
    if not env_example_path.exists():
        return False, ""
    pattern = re.compile(rf"^{re.escape(var_name)}=(.*)$")
    marker_re = re.compile(
        r"#.*(generated|генерация|generate|sops|not for production|required|инициализируется|заполняется)",
        re.IGNORECASE,
    )

    # First pass: collect all lines and their indices to scan a 5-line window above each match.
    with open(env_example_path) as f:
        lines = f.readlines()

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\n")
        match = pattern.match(stripped)
        if not match:
            continue
        value = match.group(1).strip()
        # Inline comment after value: VAR=val # comment
        inline_comment = ""
        if "#" in value:
            parts = value.split("#", 1)
            value = parts[0].strip()
            inline_comment = parts[1]
        # Scan up to 8 preceding lines for marker comments.
        # Note: we deliberately do NOT stop at another VAR= declaration, because .env.example
        # groups multiple related vars under a single comment block (e.g., PLATFORM_MASTER_EMAIL
        # and PLATFORM_MASTER_PASSWORD share the "Генерация:" marker).
        context_lines = [inline_comment]
        for back in range(1, 9):
            back_idx = idx - back
            if back_idx < 0:
                break
            back_stripped = lines[back_idx].rstrip("\n").lstrip()
            context_lines.append(back_stripped)
        context = " ".join(context_lines)
        if not value and marker_re.search(context):
            return True, "<marker:runtime-generated>"
        return True, value
    return False, ""


def _env_var_in_secrets_manifest(manifest_path: Path, var_name: str) -> bool:
    """Check presence of var in secrets-manifest.yaml (any tier except 'removed')."""
    if not manifest_path.exists():
        return False
    try:
        manifest = _load_yaml_file(manifest_path)
    except (yaml.YAMLError, FileNotFoundError):
        return False
    if not isinstance(manifest, dict):
        return False
    secrets = manifest.get("secrets", [])
    if not isinstance(secrets, list):
        return False
    for entry in secrets:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == var_name and entry.get("tier") != "removed":
            return True
    return False


def _extract_per_service_restart(compose_base_path: Path) -> dict[str, str]:
    """Extract per-service restart policy from docker-compose.base.yml.

    Returns {service_name: restart_policy}. Missing restart = "" (empty).
    """
    if not compose_base_path.exists():
        return {}
    try:
        compose = _load_yaml_file(compose_base_path)
    except (yaml.YAMLError, FileNotFoundError):
        return {}
    if not isinstance(compose, dict):
        return {}
    services = compose.get("services", {})
    if not isinstance(services, dict):
        return {}
    result: dict[str, str] = {}
    for svc_name, svc_def in services.items():
        if isinstance(svc_def, dict):
            restart = svc_def.get("restart", "")
            if isinstance(restart, str):
                result[svc_name] = restart
    return result


def _is_critical_with_always_carveout(module: dict[str, Any], service_restart: str) -> bool:
    """W3-R7 carve-out: severity:critical → restart: always OK even if compose unless-stopped.

    Returns True if this is a documented carve-out (NOT a drift violation).
    """
    severity = module.get("severity", "normal")
    return severity == "critical" and service_restart in {"always", "unless-stopped"}


# endregion PRIVATE_HELPERS


# region PUBLIC_API


# region FUNC_load_module
def load_module(path: Path) -> dict[str, Any]:
    """Load module.yaml, normalize env_requires bare-strings → objects (D5 backward-compat).

    ## @purpose Load + normalize module.yaml to canonical D5 form (env_requires always list of dicts)
    ## @io ⇥ path: Path to module.yaml → ⎋ dict (normalized)
    ## @complexity O(n) where n = env_requires size
    ## @invariants
    ##   - Returns dict with env_requires as list[dict] (never list[str])
    ##   - Each env_requires entry has keys: name, type, required
    """
    logger.info("[IMP:7][load_module] Loading %s", path)
    try:
        module = _load_yaml_file(path)
    except yaml.YAMLError as e:
        raise ValueError(f"[load_module] YAML parse error in {path}: {e}") from e
    if not isinstance(module, dict):
        raise ValueError(f"[load_module] module.yaml must be a mapping, got {type(module).__name__}: {path}")
    env_req = module.get("env_requires", [])
    if env_req is None:
        env_req = []
    if not isinstance(env_req, list):
        raise ValueError(f"[load_module] env_requires must be a list, got {type(env_req).__name__}: {path}")
    normalized = []
    for entry in env_req:
        try:
            normalized.append(_normalize_env_requires_entry(entry))
        except ValueError as e:  # noqa: PERF203
            raise ValueError(f"[load_module] {path}: {e}") from e
    module["env_requires"] = normalized
    logger.info("[IMP:7][load_module] Normalized %d env_requires entries for %s", len(normalized), path)
    return module


# endregion FUNC_load_module


# region FUNC_validate_schema
def validate_schema(module: dict[str, Any], schema_path: Path = DEFAULT_SCHEMA_PATH) -> list[str]:
    """Validate module dict against D5 JSON Schema.

    ## @purpose Structural schema validation (jsonschema draft-07)
    ## @io ⇥ module: dict, schema_path: Path → ⎋ list[str] (empty = OK)
    ## @complexity O(n) on schema complexity
    ## @invariants
    ##   - Returns list of violation messages (empty = valid)
    ##   - Does not raise on validation errors — returns them as strings
    """
    try:
        schema = _load_json_file(schema_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return [f"[validate_schema] Cannot load schema {schema_path}: {e}"]

    # Note: module.env_requires was already normalized to objects in load_module.
    # The schema allows both string and object form; both will pass.
    try:
        jsonschema.validate(module, schema)
    except jsonschema.ValidationError as e:
        path_str = ".".join(str(p) for p in e.absolute_path) or "<root>"
        return [f"Schema violation at {path_str}: {e.message}"]
    except jsonschema.SchemaError as e:
        return [f"Schema itself is invalid: {e.message}"]
    return []


# endregion FUNC_validate_schema


# region FUNC_check_env_requires_presence
def check_env_requires_presence(
    module: dict[str, Any],
    env_example_path: Path | None = None,
    secrets_manifest_path: Path | None = None,
) -> list[str]:
    """For each env_requires{required:true}: check presence in .env.example and secrets-manifest.yaml.

    Implements P07 static cross-check (DevPlan 033 §2 Option B layer — complements W3-E3 Option A runtime-fail).

    ## @purpose Ensure required env vars are declared in .env.example (non-empty) and registered in secrets-manifest.yaml (for type=secret)
    ## @io ⇥ module: dict, env_example_path, secrets_manifest_path → ⎋ list[str] violations
    ## @complexity O(n*m) where n = env_requires, m = .env.example lines
    ## @invariants
    ##   - For {required: true, type: secret}: must be present in secrets-manifest.yaml
    ##   - For {required: true}: must be present in .env.example with non-empty value
    ##   - For {required: false}: skipped (optional)
    """
    if env_example_path is None:
        env_example_path = _find_env_example()
    if secrets_manifest_path is None:
        secrets_manifest_path = DEFAULT_SECRETS_MANIFEST

    violations: list[str] = []
    module_name = module.get("name", "<unknown>")
    env_requires = module.get("env_requires", [])

    for req in env_requires:
        if not isinstance(req, dict):
            violations.append(f"{module_name}: env_requires entry not normalized: {req}")
            continue
        name = req.get("name")
        req_type = req.get("type", "secret")
        required = req.get("required", True)

        if not required:
            logger.info("[IMP:7][check_env_requires_presence] %s: %s optional — skipped", module_name, name)
            continue

        # (a) presence + non-empty in .env.example
        present, value = _env_var_in_dotenv(env_example_path, name)
        if not present:
            violations.append(f"{module_name}: required env var '{name}' missing from {env_example_path}")
            logger.info(
                "[IMP:9][check_env_requires_presence] FAIL: %s — '%s' missing in .env.example",
                module_name,
                name,
            )
        elif not value:
            violations.append(f"{module_name}: required env var '{name}' declared but EMPTY in {env_example_path}")
            logger.info(
                "[IMP:9][check_env_requires_presence] FAIL: %s — '%s' empty value in .env.example",
                module_name,
                name,
            )

        # (b) secrets-manifest registration for type=secret
        if req_type == "secret" and not _env_var_in_secrets_manifest(secrets_manifest_path, name):
            violations.append(
                f"{module_name}: secret env var '{name}' not registered in {secrets_manifest_path} (tier != removed)"
            )
            logger.info(
                "[IMP:9][check_env_requires_presence] FAIL: %s — '%s' not in secrets-manifest",
                module_name,
                name,
            )

    if not violations:
        logger.info(
            "[IMP:9][check_env_requires_presence] PASS: %s — all %d required env vars present",
            module_name,
            sum(1 for r in env_requires if isinstance(r, dict) and r.get("required", True)),
        )
    return violations


# endregion FUNC_check_env_requires_presence


# region FUNC_check_restart_drift
def check_restart_drift(module: dict[str, Any], compose_base_path: Path) -> list[str]:
    """Cross-check module.yaml `restart` against per-service restart in docker-compose.base.yml.

    Implements P08 drift detection (DevPlan 033 §1.3). Scope: base-compose only.
    Test-compose `restart: "no"` enforcement — separate gate `test_gate_compose_restart_consistency.py`.

    ## @purpose Detect drift between declared restart policy in module.yaml and actual compose
    ## @io ⇥ module: dict (must have 'name' and optional 'restart'), compose_base_path: Path → ⎋ list[str]
    ## @complexity O(n) where n = services in compose
    ## @invariants
    ##   - If module.yaml has no `restart` field → no check (backward-compat with D4)
    ##   - Carve-out: severity:critical → restart: always OK even if compose says unless-stopped (W3-R7)
    ##   - Multi-service compose: check primary service (matches module.name) first;
    ##     other services with different restart → warning, not violation (documented carve-out)
    """
    if not compose_base_path.exists():
        logger.info("[IMP:7][check_restart_drift] compose base not found: %s — skipped", compose_base_path)
        return []

    declared_restart = module.get("restart")
    if not declared_restart:
        # D4 module.yaml without restart field — no drift check possible
        logger.info("[IMP:7][check_restart_drift] module has no 'restart' field — drift check skipped")
        return []

    module_name = module.get("name", "<unknown>")
    per_service = _extract_per_service_restart(compose_base_path)
    if not per_service:
        return [f"{module_name}: cannot extract per-service restart from {compose_base_path}"]

    violations: list[str] = []
    severity = module.get("severity", "normal")

    # Strategy: check if ALL non-init services match declared_restart OR fall under carve-out.
    # init-services (one-shot, restart: "no") are excluded from drift check (they are by design different).
    for svc_name, svc_restart in per_service.items():
        # Init/one-shot services always have restart: "no" — skip them (not a drift)
        if svc_restart == "no":
            logger.info(
                "[IMP:7][check_restart_drift] %s/%s: restart=no (init/one-shot) — skipped",
                module_name,
                svc_name,
            )
            continue

        if svc_restart == declared_restart:
            logger.info(
                "[IMP:9][check_restart_drift] %s/%s: restart=%s matches module.yaml ✓",
                module_name,
                svc_name,
                svc_restart,
            )
            continue

        # Carve-out: severity:critical + compose restart in {always, unless-stopped} + declared in {always, unless-stopped}
        if severity == "critical" and svc_restart in BASE_ALLOWED_RESTART and declared_restart in BASE_ALLOWED_RESTART:
            logger.info(
                "[IMP:9][check_restart_drift] %s/%s: critical carve-out — %s vs %s accepted",
                module_name,
                svc_name,
                svc_restart,
                declared_restart,
            )
            continue

        violations.append(
            f"{module_name}: restart drift — module.yaml declares '{declared_restart}' "
            f"but service '{svc_name}' in {compose_base_path.name} has '{svc_restart}'"
        )
        logger.info(
            "[IMP:9][check_restart_drift] FAIL: %s/%s drift: module=%s compose=%s",
            module_name,
            svc_name,
            declared_restart,
            svc_restart,
        )

    if not violations:
        logger.info("[IMP:9][check_restart_drift] PASS: %s — restart drift check clean", module_name)
    return violations


# endregion FUNC_check_restart_drift


# region FUNC_validate_module
def validate_module(
    module_yaml_path: Path,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    env_example_path: Path | None = None,
    secrets_manifest_path: Path | None = None,
    strict: bool = False,
) -> list[str]:
    """Full validation pipeline for a single module.yaml (D5 contract).

    ## @purpose Orchestrate schema + presence + restart-drift checks for one module
    ## @io ⇥ module_yaml_path, schema_path, ... → ⎋ list[str] violations (empty = valid)
    ## @complexity O(n+m+k) — schema + env_example scan + services
    ## @invariants
    ##   - Combines all three checks; returns aggregate violations
    ##   - If module.yaml cannot be loaded → returns single error message
    ##   - strict=True: treat warnings as violations (reserved for --schema-strict path)
    """
    violations: list[str] = []

    # Step 1: load + normalize
    try:
        module = load_module(module_yaml_path)
    except (FileNotFoundError, yaml.YAMLError, ValueError) as e:
        return [f"[validate_module] Cannot load {module_yaml_path}: {e}"]

    # Step 2: schema
    violations.extend(validate_schema(module, schema_path))

    # Step 3: env_requires presence
    violations.extend(check_env_requires_presence(module, env_example_path, secrets_manifest_path))

    # Step 4: restart-drift (only if module declares restart)
    module_dir = module_yaml_path.parent
    compose_base = module_dir / "docker-compose.base.yml"
    if compose_base.exists():
        violations.extend(check_restart_drift(module, compose_base))
    elif strict:
        # Docker modules should have base.yml — flag missing in strict mode
        install_type = module.get("install_type", "")
        if install_type == "docker":
            violations.append(f"{module.get('name', '?')}: install_type=docker but docker-compose.base.yml missing")

    return violations


# endregion FUNC_validate_module


# endregion PUBLIC_API


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    ## @purpose argparse-based CLI dispatch for D5 validator
    ## @io ⇥ argv: list[str] → ⎋ int (exit code: 0=pass, 1=violation, 2=usage error)
    ## @complexity O(M * (n+m+k)) where M = modules count
    ## @invariants
    ##   - --all: validate every core/modules/*/module.yaml
    ##   - --module NAME: validate single module by name
    ##   - --schema-strict: stricter checks (e.g., missing base.yml for docker modules)
    ##   - Always exits 0 on success, 1 on any violation
    """
    parser = argparse.ArgumentParser(
        prog="validate_module_yaml",
        description="D5 module.yaml contract validator (Wave 3 W3-E1).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="Validate all core/modules/*/module.yaml")
    mode.add_argument("--module", metavar="NAME", help="Validate single module by name")
    parser.add_argument("--schema-strict", action="store_true", help="Strict mode (extra checks)")
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH, help="Path to module.schema.json")
    parser.add_argument("--modules-dir", type=Path, default=DEFAULT_MODULES_DIR, help="Path to core/modules/")
    parser.add_argument("--env-example", type=Path, default=None, help="Path to .env.example (default: auto-detect)")
    parser.add_argument(
        "--secrets-manifest",
        type=Path,
        default=DEFAULT_SECRETS_MANIFEST,
        help="Path to secrets-manifest.yaml",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")

    args = parser.parse_args(argv)

    log_level = logging.WARNING - 10 * args.verbose
    logging.basicConfig(
        level=max(log_level, logging.DEBUG),
        format="[%(levelname)s] %(message)s",
        stream=sys.stderr,
    )

    modules_dir: Path = args.modules_dir
    schema_path: Path = args.schema_path
    env_example: Path | None = args.env_example
    # secrets_manifest_path flows directly from args.secrets_manifest into validate_module below.

    # Collect targets
    if args.all:
        targets = sorted(modules_dir.glob("*/module.yaml"))
        if not targets:
            print(
                f"[IMP:9][validate_module_yaml] ERROR: no module.yaml found in {modules_dir}",
                file=sys.stderr,
            )
            return 1
    else:
        target = modules_dir / args.module / "module.yaml"
        if not target.exists():
            print(
                f"[IMP:9][validate_module_yaml] ERROR: module.yaml not found: {target}",
                file=sys.stderr,
            )
            return 1
        targets = [target]

    total_violations = 0
    failed_modules: list[str] = []
    print(
        f"[IMP:7][validate_module_yaml] Validating {len(targets)} module(s) against D5 schema",
        file=sys.stderr,
    )

    for yaml_path in targets:
        module_name = yaml_path.parent.name
        violations = validate_module(
            yaml_path,
            schema_path=schema_path,
            env_example_path=env_example,
            secrets_manifest_path=args.secrets_manifest,
            strict=args.schema_strict,
        )
        if violations:
            total_violations += len(violations)
            failed_modules.append(module_name)
            print(f"[IMP:9][validate_module_yaml] FAIL: {module_name}", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
        else:
            print(f"[IMP:9][validate_module_yaml] PASS: {module_name}", file=sys.stderr)

    # Summary
    passed = len(targets) - len(failed_modules)
    print(
        f"[IMP:9][validate_module_yaml] SUMMARY: {passed}/{len(targets)} modules valid, "
        f"{total_violations} violation(s)",
        file=sys.stderr,
    )

    return 0 if total_violations == 0 else 1


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
