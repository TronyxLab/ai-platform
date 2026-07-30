#!/usr/bin/env python3
# GREP_SUMMARY: generate_secrets_manifest, secrets-manifest, consumers-computation, module-yaml, env-requires, CLI
# STRUCTURE: ▶ load_secret_definitions ┐
#           ▶ load_module_yamls       ┤ → ◇ compute_consumers → ⊕ generate → ⎋ YAML output
#           ▶ main (--secret-defs, --modules-dir, --output)
# region MODULE_CONTRACT
## @purpose  Генератор secrets-manifest.yaml — объединяет secret-definitions.yaml с динамически
##           вычисленными consumers из module.yaml#env_requires. Consumers — это список модулей,
##           чей env_requires включает имя секрета.
## @scope    CLI-утилита; импортируется из CI/CD и Makefile.
## @invariants
##   - Входной secret-definitions.yaml остаётся неизменным (read-only)
##   - Все секреты из secret-definitions.yaml копируются как есть в output
##   - consumers вычисляются строго по env_requires в module.yaml
##   - consumers массив пуст, если ни один module.yaml не требует секрет
##   - Порядок секретов сохраняется из secret-definitions.yaml
## @rationale Разделение definitions + consumers: definitions меняются редко, consumers
##            пересчитываются при изменении module.yaml. Автоматизация предотвращает дрейф
##            consumers при добавлении/удалении env_requires в модулях.
## @changes  Plan 041 — created: consumers computation from module.yaml env_requires
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import difflib
import io
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

# region CONSTANTS

logger = logging.getLogger(__name__)

# endregion CONSTANTS


# region FUNC_load_secret_definitions
def load_secret_definitions(path: Path | str) -> list[dict[str, Any]]:
    if isinstance(path, str):
        path = Path(path)
    """Load secret definitions from secret-definitions.yaml.

    ## @purpose  Parse YAML and extract secrets list from the document.
    ##            Validates that the 'secrets' key exists and is a list.
    ## @io        ⇥ path: Path → ⎋ list[dict]: list of secret entry dicts
    ## @complexity O(N) where N = number of secrets in file
    ## @invariants
    ##   - Returns empty list if file has no 'secrets' key
    ##   - Raises FileNotFoundError if path does not exist
    ##   - Raises yaml.YAMLError on malformed YAML
    """
    logger.info("[IMP:7][load_secret_definitions][START] Loading secret definitions from %s", path)

    if not path.is_file():
        logger.warning(
            "[IMP:8][load_secret_definitions][SKIP] Secret definitions file not found: %s — returning empty", path
        )
        return []

    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    secrets: list[dict[str, Any]] = data.get("secrets", [])
    if not isinstance(secrets, list):
        logger.error("[IMP:9][load_secret_definitions][ERROR] 'secrets' key is not a list in %s", path)
        return []

    logger.info(
        "[IMP:9][load_secret_definitions][OK] Loaded %d secret definitions from %s",
        len(secrets),
        path,
    )
    return secrets


# endregion FUNC_load_secret_definitions


# region FUNC_load_module_yamls
def load_module_yamls(modules_dir: Path | str) -> list[dict[str, Any]]:
    if isinstance(modules_dir, str):
        modules_dir = Path(modules_dir)
    """Load all module.yaml files from modules directory.

    ## @purpose  Recursively glob core/modules/*/module.yaml and parse each file.
    ##            Returns list of parsed YAML dicts with 'name' field guaranteed.
    ## @io        ⇥ modules_dir: Path → ⎋ list[dict]: list of module metadata dicts
    ## @complexity O(M) where M = number of module.yaml files
    ## @invariants
    ##   - Skips directories without module.yaml
    ##   - Each returned dict has a 'name' key (module name)
    ##   - Each returned dict has an 'env_requires' key (default [])
    ##   - Skips malformed YAML files with warning
    """
    logger.info("[IMP:7][load_module_yamls][START] Scanning for module.yaml files in %s", modules_dir)

    if not modules_dir.is_dir():
        raise NotADirectoryError(f"Modules directory not found: {modules_dir}")

    modules: list[dict[str, Any]] = []
    pattern = "*/module.yaml"

    for mod_yaml_path in sorted(modules_dir.glob(pattern)):
        logger.info("[IMP:8][load_module_yamls][PROCESS] Reading %s", mod_yaml_path)

        try:
            with open(mod_yaml_path) as f:
                mod_data: dict[str, Any] = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:8][load_module_yamls][SKIP] Failed to parse %s: %s", mod_yaml_path, exc)
            continue

        if not isinstance(mod_data, dict):
            logger.warning("[IMP:8][load_module_yamls][SKIP] %s is not a valid YAML dict", mod_yaml_path)
            continue

        module_name = mod_data.get("name")
        if not module_name:
            logger.warning("[IMP:8][load_module_yamls][SKIP] %s has no 'name' field", mod_yaml_path)
            continue

        env_requires_raw = mod_data.get("env_requires") or []
        if not isinstance(env_requires_raw, list):
            env_requires_raw = []

        # Normalize env_requires: bare strings vs typed objects
        env_requires: list[str] = []
        for entry in env_requires_raw:
            if isinstance(entry, str):
                env_requires.append(entry)
            elif isinstance(entry, dict):
                name_val = entry.get("name")
                if name_val and isinstance(name_val, str):
                    env_requires.append(name_val)

        modules.append(
            {
                "name": module_name,
                "env_requires": env_requires,
            }
        )

        logger.info(
            "[IMP:9][load_module_yamls][MODULE] %s requires %d secrets: %s",
            module_name,
            len(env_requires),
            env_requires,
        )

    logger.info("[IMP:9][load_module_yamls][OK] Loaded %d module.yaml files", len(modules))
    return modules


# endregion FUNC_load_module_yamls


# region FUNC_compute_consumers
def compute_consumers(secret_name: str, modules: list[dict[str, Any]]) -> list[str]:
    """Compute list of module names that require the given secret.

    ## @purpose  Scan all modules' env_requires for the secret_name.
    ##            A module is a consumer if its env_requires contains the secret_name.
    ## @io        ⇥ secret_name: str, modules: list[dict] → ⎋ list[str]: sorted module names
    ## @complexity O(M * E) where M = modules, E = avg env_requires per module
    ## @invariants
    ##   - Returns alphabetically sorted list
    ##   - Returns empty list if no module requires the secret
    ##   - Case-sensitive comparison (secret names are uppercase)
    """
    consumers: list[str] = []

    for mod in modules:
        env_req: list[str] = mod.get("env_requires", [])
        if secret_name in env_req:
            consumers.append(str(mod["name"]))

    consumers.sort()
    return consumers


# endregion FUNC_compute_consumers


# region FUNC_generate
def generate(secret_defs: list[dict[str, Any]], modules: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine secret definitions with computed consumers.

    ## @purpose  For each secret in secret_defs, compute consumers list from modules
    ##            and add it to the secret entry. Preserves original secret order.
    ## @io        ⇥ secret_defs: list[dict], modules: list[dict]
    ##            → ⎋ dict: {"version": 1, "secrets": [...]}
    ## @complexity O(S * M * E) where S = secrets, M = modules, E = avg env_requires
    ## @invariants
    ##   - All original secret fields are preserved
    ##   - consumers is always a list (may be empty)
    ##   - Output has 'version: 1' at top level
    """
    logger.info(
        "[IMP:7][generate][START] Computing consumers for %d secrets across %d modules",
        len(secret_defs),
        len(modules),
    )

    output_secrets: list[dict[str, Any]] = []

    for secret in secret_defs:
        secret_name: str = secret.get("name", "")
        if not secret_name:
            logger.warning("[IMP:8][generate][SKIP] Secret entry without 'name' — skipping")
            continue

        consumers = compute_consumers(secret_name, modules)
        entry: dict[str, Any] = dict(secret)  # shallow copy
        entry["consumers"] = consumers

        output_secrets.append(entry)

        logger.info(
            "[IMP:9][generate][SECRET] %s → consumers: %s",
            secret_name,
            consumers,
        )

    result: dict[str, Any] = {
        "version": 1,
        "secrets": output_secrets,
    }

    logger.info(
        "[IMP:9][generate][OK] Generated manifest with %d secret entries",
        len(output_secrets),
    )
    return result


# endregion FUNC_generate


# region FUNC_check_output
def _check_output(generated_content: str, output_path: Path) -> None:
    """Byte-level comparison for --check mode. Exit 0 if fresh, 1 if stale.

    ## @purpose  Compare generated content with existing file byte-by-byte.
    ##            Prints diff (first 20 lines) to stderr on divergence.
    ##            NEVER writes to disk.
    ## @io        ⇥ generated_content: str, output_path: Path → ⎋ sys.exit(0|1)
    ## @complexity O(N) where N = file size in bytes
    ## @invariants
    ##   - NEVER writes to disk — pure read-only comparison
    ##   - Exit 0 if content matches, 1 if divergence
    ##   - Prints first 20 lines of diff to stderr on divergence
    """
    logger.info("[IMP:7][_check_output][START] Checking output against %s", output_path)

    if not output_path.is_file():
        logger.error("[IMP:9][_check_output][ERROR] Output file %s does not exist — cannot compare", output_path)
        sys.exit(1)

    generated_bytes = generated_content.encode("utf-8")
    existing_bytes = output_path.read_bytes()

    if generated_bytes == existing_bytes:
        logger.info("[IMP:9][_check_output][OK] Output is fresh — matches %s", output_path)
        sys.exit(0)

    # Divergence — compute and print diff
    diff_lines = list(
        difflib.unified_diff(
            existing_bytes.decode("utf-8").splitlines(keepends=True),
            generated_content.splitlines(keepends=True),
            fromfile=str(output_path),
            tofile="generated",
        )
    )
    for line in diff_lines[:20]:
        sys.stderr.write(line)
    if len(diff_lines) > 20:
        sys.stderr.write(f"... ({len(diff_lines) - 20} more lines)\n")

    logger.error(
        "[IMP:9][_check_output][FAIL] Divergence detected — %s is stale. Regenerate without --check.",
        output_path,
    )
    sys.exit(1)


# endregion FUNC_check_output


# region FUNC_main
def main() -> None:
    """CLI entrypoint.

    ## @purpose  Parse CLI args, load definitions + modules, compute consumers,
    ##            write output YAML. --check mode: byte-level comparison.
    ## @io        ⇥ sys.argv → ⎋ exit code 0/1
    ## @complexity O(1) dispatch to sub-functions
    ## @invariants
    ##   - Writes output file only if computation succeeds
    ##   - --check mode: byte-level comparison, never writes to disk
    ##   - Exit 0 on success / fresh, 1 on error or divergence
    ##   --output file is overwritten if exists
    """
    parser = argparse.ArgumentParser(
        description="Generate secrets-manifest.yaml from secret-definitions.yaml and module.yaml files",
    )
    parser.add_argument(
        "--secret-defs",
        required=True,
        type=str,
        help="Path to secret-definitions.yaml",
    )
    parser.add_argument(
        "--modules-dir",
        required=True,
        type=str,
        help="Path to core/modules/ directory",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=str,
        help="Output path for generated secrets-manifest.yaml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with existing file. Exit 0 if fresh, 1 if stale. Read-only.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[IMP:%(levelno)s][%(name)s][%(funcName)s] %(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:7][main][START] generate_secrets_manifest.py")

    secret_defs_path = Path(args.secret_defs).resolve()
    modules_dir_path = Path(args.modules_dir).resolve()
    output_path = Path(args.output).resolve()

    # ── Pre-flight check ──
    preflight_ok = True
    if not secret_defs_path.is_file():
        logger.error("[IMP:9][main][PREFLIGHT] --secret-defs file not found: %s", secret_defs_path)
        preflight_ok = False
    if not modules_dir_path.is_dir():
        logger.error("[IMP:9][main][PREFLIGHT] --modules-dir not found: %s", modules_dir_path)
        preflight_ok = False

    if not preflight_ok:
        logger.error("[IMP:10][main][FAIL] Pre-flight checks failed — aborting")
        sys.exit(1)

    logger.info("[IMP:8][main][PREFLIGHT] All pre-flight checks passed")

    # ── Load ──
    secret_defs: list[dict[str, Any]] = load_secret_definitions(secret_defs_path)
    modules: list[dict[str, Any]] = load_module_yamls(modules_dir_path)

    if not secret_defs:
        logger.warning("[IMP:8][main][WARN] No secret definitions loaded — output will be empty")

    # ── Generate ──
    manifest: dict[str, Any] = generate(secret_defs, modules)

    # ── Render output to string (shared between check and write) ──
    buf = io.StringIO()
    buf.write(
        "# core/secrets-manifest.yaml\n"
        "# GREP_SUMMARY: secrets-manifest, sso, tier-model, required, generated, optional, consumers, anti-drift\n"
        "# STRUCTURE: ┌secrets[]┐ → ◇ tier(required|generated|optional|removed) → ⊕ consumers[] → ⟦source(sops|autogen|ci-secret)⟧ → ⎋ gate-verifiable\n"
        "# region MODULE_CONTRACT\n"
        "## @purpose  Единый SSoT для всех секретов платформы. Anti-drift механизм: gate блокирует незарегистрированные секреты.\n"
        "## @scope    Auto-generated from core/secret-definitions.yaml + module.yaml consumers.\n"
        "##           Consumed by CI gates, deploy-modules.sh, secrets-init.sh.\n"
        "## @invariants\n"
        "##   tier ∈ {required, generated, optional, removed}\n"
        "##   source ∈ {sops, autogen, ci-secret, provisioner}\n"
        "##   generated-секреты всегда имеют gen_command\n"
        "##   ci-secret секреты имеют consumers: [] (CI-side, не модули)\n"
        "##   removed-секреты имеют tier=removed (историческая запись)\n"
        "## @rationale SSoT предотвращает дрейф: gate блокирует добавление секретов без регистрации в манифесте\n"
        "# endregion MODULE_CONTRACT\n"
        "\n"
    )
    yaml.dump(manifest, buf, default_flow_style=False, sort_keys=False, allow_unicode=True)
    output_content: str = buf.getvalue()

    # ── Check mode (read-only, byte-level comparison) ──
    if args.check:
        _check_output(output_content, output_path)

    # ── Write output ──
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(output_content)

    logger.info(
        "[IMP:9][main][OK] Written %d secrets to %s",
        len(manifest.get("secrets", [])),
        output_path,
    )
    print(f"[IMP:9][main] Secrets manifest written to {output_path}")


# endregion FUNC_main

if __name__ == "__main__":
    main()
