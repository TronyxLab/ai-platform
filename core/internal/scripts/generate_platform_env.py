#!/usr/bin/env python3
# GREP_SUMMARY: generate_platform_env, platform-env, generator, port-scanning, profiles-discovery, ci-defaults, smoke-env, helpers
# STRUCTURE: ▶ discover_profiles ┐
#           ▶ scan_compose_ports ┤ → ◇ load_infra → ⊕ generate_yaml → ┌ output (platform-env.yaml) ┐
#           ▶ scan_test_ports   ┤                                    ├ smoke_env_generated.py   ┤
#           ▶ load_ci_defaults  ┘                                    └ env_defaults_generated.py ┘
# region MODULE_CONTRACT
## @purpose  Генератор platform-env.yaml и сопутствующих Python-файлов (smoke_env_generated.py,
##           env_defaults_generated.py). Собирает статическую инфраструктуру (networks, volumes,
##           proxy, provides) из platform-infra.yaml и динамически вычисляет profiles,
##           port_mappings, test_ports, env_defaults из модульной файловой структуры.
## @scope    CLI-утилита; вызывается из Makefile и CI. Регенерация при изменении модулей.
## @invariants
##   - platform-infra.yaml — read-only вход (не изменяется)
##   - port_mappings извлекаются из docker-compose.base.yml port-маппингов
##   - test_ports извлекаются из docker-compose.test.yml (паттерн 1XXXX:YYYY)
##   - profiles = sorted имена папок в --modules-dir
##   - env_defaults = merged: non-secret from platform-infra.yaml env_defaults + secret ci_default from secret-definitions.yaml (latter takes precedence)
##   - generated файлы — валидный Python с корректными импортами
## @rationale Автоматическая генерация platform-env.yaml устраняет дрейф между реальной
##            конфигурацией модулей и декларацией в platform-env.yaml.
## @changes  Plan 041 — created: dynamic generator for platform-env.yaml
##           Plan 082 — merged env_defaults from platform-infra.yaml (non-secret) with
##                      ci_defaults from secret-definitions.yaml (secret, takes precedence)
##           Plan 090 — added --check mode: generates all 3 outputs in memory,
##                      compares byte-by-byte, exit 0/1, diff on stderr
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# ── sys.path bootstrap for direct-script invocation (DevPlan 116 B4 T2: core.* импорты) ──
_PLATFORM_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

from core.internal.shared.exceptions import ConfigParseError

# region CONSTANTS

logger = logging.getLogger(__name__)

# ⚠️ NOTE (DevPlan 117 G T56): _PORT_NAME_MAP moved to core/internal/scripts/port_scanner.py
# (single source of truth). generate_platform_env.py no longer defines it — the scanner
# functions below are lazy facades delegating to port_scanner.py.

# endregion CONSTANTS


# region FUNC_load_infra
def load_infra(infra_path: Path) -> dict[str, Any]:
    """Load platform-infra.yaml and validate required sections.

    ## @purpose  Parse YAML and verify that required sections (networks, volumes,
    ##            proxy, provides) exist.
    ## @io        ⇥ infra_path: Path → ⎋ dict[str, Any]: infra data
    ## @complexity O(1) — single YAML load
    ## @invariants
    ##   - Missing sections default to empty lists/dicts
    ##   - networks and volumes default to []
    ##   - proxy defaults to {}
    ##   - provides defaults to {}
    """
    logger.info("[IMP:7][load_infra][START] Loading infra from %s", infra_path)

    if not infra_path.is_file():
        raise FileNotFoundError(f"Infra file not found: {infra_path}")

    with open(infra_path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ConfigParseError(f"Infra file {infra_path} is not a valid YAML dict")

    result: dict[str, Any] = {
        "networks": data.get("networks", []),
        "volumes": data.get("volumes", []),
        "proxy": data.get("proxy", {}),
        "provides": data.get("provides", {}),
        "env_defaults": data.get("env_defaults", {}),
    }

    if not isinstance(result["networks"], list):
        result["networks"] = []
    if not isinstance(result["volumes"], list):
        result["volumes"] = []
    if not isinstance(result["proxy"], dict):
        result["proxy"] = {}

    logger.info(
        "[IMP:9][load_infra][OK] Loaded %d networks, %d volumes, proxy, %d provides",
        len(result["networks"]),
        len(result["volumes"]),
        len(result["provides"]),
    )
    return result


# endregion FUNC_load_infra


# region FUNC_discover_profiles
def discover_profiles(modules_dir: Path | str) -> list[str]:
    """Discover module names from directories in modules dir.

    ## @purpose  List all subdirectories in modules_dir — each is a profile name.
    ##            Excludes system modules (install_type: system).
    ##            Returns alphabetically sorted list.
    ## @io        ⇥ modules_dir: Path → ⎋ list[str]: sorted profile names
    ## @complexity O(D) where D = number of directories
    ## @invariants
    ##   - Only directories are included (not files)
    ##   - Hidden directories (starting with .) are excluded
    ##   - System modules (install_type: system) are excluded
    ##   - Result is sorted alphabetically
    """
    if isinstance(modules_dir, str):
        modules_dir = Path(modules_dir)

    logger.info("[IMP:7][discover_profiles][START] Discovering profiles in %s", modules_dir)

    if not modules_dir.is_dir():
        raise NotADirectoryError(f"Modules directory not found: {modules_dir}")

    profiles: list[str] = []
    for entry in sorted(modules_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        # Exclude system modules (install_type: system)
        module_yaml = entry / "module.yaml"
        if module_yaml.is_file():
            try:
                with open(module_yaml) as f:
                    mod_data = yaml.safe_load(f)
                if isinstance(mod_data, dict) and mod_data.get("install_type") == "system":
                    logger.info(
                        "[IMP:8][discover_profiles][SKIP] %s — system module (install_type: system)", entry.name
                    )
                    continue
            except (yaml.YAMLError, OSError):
                pass  # If we can't read module.yaml, include the directory
        profiles.append(entry.name)

    logger.info(
        "[IMP:9][discover_profiles][OK] Discovered %d profiles: %s",
        len(profiles),
        profiles,
    )
    return profiles


# endregion FUNC_discover_profiles


# region FUNC_extract_host_port
def extract_host_port(port_mapping: str) -> int | None:
    """Lazy facade for core.internal.scripts.port_scanner.extract_host_port.

    ## @purpose — Backward-compatible entry point retained in generate_platform_env so
    ##            existing callers keep the same import path. Implementation moved
    ##            verbatim to port_scanner.py (DevPlan 117 G T56). Lazy import keeps
    ##            start-up time unchanged (AC-G5).
    ## @io — ⇥ port_mapping: str → ⎋ int | None: host port or None
    ## @complexity — O(1) + delegate
    """
    from core.internal.scripts.port_scanner import extract_host_port as _impl

    return _impl(port_mapping)


# endregion FUNC_extract_host_port


# region FUNC_scan_compose_ports
def scan_compose_ports(modules_dir: Path) -> dict[str, int]:
    """Lazy facade for core.internal.scripts.port_scanner.scan_compose_ports.

    ## @purpose — Backward-compatible entry point retained in generate_platform_env so
    ##            existing callers (main, gate test) keep the same import path.
    ##            Implementation moved verbatim to port_scanner.py (DevPlan 117 G T56).
    ## @io — ⇥ modules_dir: Path → ⎋ dict[str, int]: {VAR_NAME: port}
    ## @complexity — O(1) + delegate
    """
    from core.internal.scripts.port_scanner import scan_compose_ports as _impl

    return _impl(modules_dir)


# endregion FUNC_scan_compose_ports


# region FUNC_scan_test_ports
def scan_test_ports(modules_dir: Path) -> dict[str, dict[str, int]]:
    """Lazy facade for core.internal.scripts.port_scanner.scan_test_ports.

    ## @purpose — Backward-compatible entry point retained in generate_platform_env so
    ##            existing callers (main, gate test) keep the same import path.
    ##            Implementation moved verbatim to port_scanner.py (DevPlan 117 G T56).
    ## @io — ⇥ modules_dir: Path → ⎋ dict[str, dict[str, int]]: test port map
    ## @complexity — O(1) + delegate
    """
    from core.internal.scripts.port_scanner import scan_test_ports as _impl

    return _impl(modules_dir)


# endregion FUNC_scan_test_ports


# region FUNC_load_ci_defaults
def load_ci_defaults(secret_defs_path: Path | str) -> dict[str, str]:
    if isinstance(secret_defs_path, str):
        secret_defs_path = Path(secret_defs_path)
    """Load ci_default values from secret-definitions.yaml.

    ## @purpose  Extract the ci_default field from each secret entry.
    ##            Secrets without ci_default or with empty ci_default are omitted.
    ## @io        ⇥ secret_defs_path: Path → ⎋ dict[str, str]: {SECRET_NAME: ci_default}
    ## @complexity O(N) where N = number of secrets
    """
    logger.info("[IMP:7][load_ci_defaults][START] Loading ci_default from %s", secret_defs_path)

    if not secret_defs_path.is_file():
        logger.warning(
            "[IMP:8][load_ci_defaults][SKIP] Secret definitions file not found: %s — returning empty", secret_defs_path
        )
        return {}

    with open(secret_defs_path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    secrets: list[dict[str, Any]] = data.get("secrets", [])
    ci_defaults: dict[str, str] = {}

    if not isinstance(secrets, list):
        logger.warning("[IMP:8][load_ci_defaults][WARN] 'secrets' key is not a list")
        return ci_defaults

    for secret in secrets:
        name: str = secret.get("name", "")
        ci_val = secret.get("ci_default")

        if name and ci_val is not None and ci_val != "":
            ci_defaults[name] = str(ci_val)
            logger.info("[IMP:9][load_ci_defaults][ENTRY] %s = %s", name, ci_val)

    logger.info("[IMP:9][load_ci_defaults][OK] Loaded %d ci_default values", len(ci_defaults))
    return ci_defaults


# endregion FUNC_load_ci_defaults


# region FUNC_generate_platform_env_yaml
# 🧐 TRAP[DECISION] · 2026-08-03 · — · D-10 (DevPlan 125 T12): Jinja2-миграция ОТКЛОНЕНА — keep by design
# · Rejected: миграция рендера на Jinja2 (долг D-10: «f-string → jinja», LOW, опциональный)
# · Reason: рендер УЖЕ структурный — dict-композиция + yaml.dump (sort_keys=False, allow_unicode),
# ·   не f-string-интерполяция (долг описывал более раннюю версию). Jinja2 не добавит
# ·   ценности: циклов/условий нет (плоские секции), byte-детерминизм обеспечивается
# ·   yaml.dump, тестовое покрытие — 11 unit-тестов + гейт test_gate_yaml_deterministic_output.
# ·   Миграция = риск изменения вывода без выгоды (инвариант 2: прод-рендер byte-for-byte).
# · Rev: если появятся условные/циклические секции в platform-env.yaml (Jinja2-домен
# ·   по шаблонной политике AGENTS.md) — пересмотреть; до тех пор рендер остаётся as-is.
def generate_platform_env_yaml(
    infra: dict[str, Any],
    profiles: list[str],
    port_mappings: dict[str, int],
    test_ports: dict[str, dict[str, int]],
    env_defaults: dict[str, str],
) -> str:
    """Generate the complete platform-env.yaml content as a string.

    ## @purpose  Combine static infra sections with generated sections into
    ##            a well-formatted YAML document with header comments.
    ## @io        ⇥ infra, profiles, port_mappings, test_ports, env_defaults
    ##            → ⎋ str: YAML document
    ## @complexity O(N) — YAML dump
    """
    output: dict[str, Any] = {}

    # Static infra sections from platform-infra.yaml
    output["networks"] = infra.get("networks", [])
    output["volumes"] = infra.get("volumes", [])
    output["proxy"] = infra.get("proxy", {})
    output["provides"] = infra.get("provides", {})

    # Generated sections
    output["port_mappings"] = port_mappings
    output["env_defaults"] = env_defaults
    output["test_ports"] = test_ports
    output["profiles"] = profiles

    yaml_str: str = yaml.dump(
        output,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )

    header = (
        "# GREP_SUMMARY: platform-env, environment-descriptor, networks, volumes, env-defaults, profiles, generated\n"
        "# STRUCTURE: ┌networks┐ → ┌volumes┐ → ┌proxy┐ → ┌port_mappings┐ → ┌env_defaults┐ → ┌test_ports┐ → ┌provides┐ → ┌profiles┐\n"
        "# region MODULE_CONTRACT\n"
        "## @purpose  Canonical environment descriptor — generated from platform-infra.yaml + secret-definitions.yaml + module discovery\n"
        "## @scope    Consumed by provision-environment.sh, tests/_conftest/infra.py, deploy-modules.sh, CI workflows, Makefile\n"
        "## @invariants\n"
        "##   - This is the ONLY place where networks, volumes, and CI env vars are defined\n"
        "##   - Consumers MUST read this file, never hardcode\n"
        "##   - env_defaults are CI/test defaults only — do NOT override .env\n"
        "##   - profiles are auto-discovered from core/modules/ directory names\n"
        "# endregion MODULE_CONTRACT\n"
        "#\n"
        "# platform-env.yaml — GENERATED by generate_platform_env.py\n"
        "# DO NOT EDIT — changes will be overwritten on next generation.\n"
        "# Edit core/platform-infra.yaml for static sections, or\n"
        "# core/secret-definitions.yaml for CI default values.\n"
        "# Profiles, port_mappings, test_ports are auto-discovered.\n"
        "\n"
    )

    return header + yaml_str


# endregion FUNC_generate_platform_env_yaml


# region FUNC_generate_smoke_env_py
def generate_smoke_env_py(ci_defaults: dict[str, str]) -> str:
    """Generate Python file with SMOKE_ENV_GENERATED dict.

    ## @purpose  Produce valid Python code defining SMOKE_ENV_GENERATED dict
    ##            with all ci_default values from secret-definitions.yaml.
    ## @io        ⇥ ci_defaults: dict[str, str] → ⎋ str: Python module content
    ## @complexity O(K) where K = number of ci_defaults
    ## @invariants
    ##   - Output is valid Python
    ##   - Keys are uppercase strings
    ##   - Values are properly escaped strings
    """
    lines: list[str] = [
        "# GREP_SUMMARY: smoke_env_generated, ci-defaults, generated, test-env",
        "# STRUCTURE: ┌ci_defaults┐ → ○ sorted keys → ⊕ SMOKE_ENV_GENERATED dict → ⎋ test env vars",
        "# region MODULE_CONTRACT",
        '"""## @purpose  AUTO-GENERATED CI defaults for smoke tests. Source: core/secret-definitions.yaml ci_default fields.',
        "## @scope     Consumed by smoke tests to provide default values for all platform secrets.",
        "## @invariants  Keys are uppercase strings matching secret names. Values are CI/test-only, never production.",
        '"""',
        "# endregion MODULE_CONTRACT",
        "#",
        "# GENERATED by generate_platform_env.py — DO NOT EDIT",
        "# Changes will be overwritten on next generation.",
        "#",
        "",
        "from __future__ import annotations",
        "",
        "SMOKE_ENV_GENERATED: dict[str, str] = {",
    ]

    for key in sorted(ci_defaults.keys()):
        value = ci_defaults[key]
        escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        lines.append(f'    "{key}": "{escaped}",')

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


# endregion FUNC_generate_smoke_env_py


# region FUNC_generate_helpers_py
def generate_helpers_py(ci_defaults: dict[str, str]) -> str:
    """Generate Python file with _PREFIX_* constants (underscore-prefixed).

    ## @purpose  Produce valid Python code defining module-private constants for
    ##            each ci_default value. Named _PREFIX_{NAME} for backward
    ##            compatibility with existing imports (e.g. _POSTGRES_PASSWORD).
    ## @io        ⇥ ci_defaults: dict[str, str] → ⎋ str: Python module content
    ## @complexity O(K) where K = number of ci_defaults
    ## @invariants
    ##   - Each constant is named _{SECRET_NAME}
    ##   - Values are typed as str
    ##   - Includes __all__ list for clean imports
    """
    lines: list[str] = [
        "# GREP_SUMMARY: env_defaults_generated, ci-defaults, constants, generated",
        "# STRUCTURE: ┌ci_defaults┐ → ○ sorted keys → ⊕ _PREFIX_* constants → ⎋ test helpers",
        "# region MODULE_CONTRACT",
        '"""## @purpose  AUTO-GENERATED CI default constants for test helpers. Source: core/secret-definitions.yaml ci_default fields.',
        "## @scope     Consumed by test helpers to provide default values for all platform secrets.",
        "## @invariants  Each constant is named _{SECRET_NAME}. Values are CI/test-only, never production.",
        '"""',
        "# endregion MODULE_CONTRACT",
        "#",
        "# GENERATED by generate_platform_env.py — DO NOT EDIT",
        "# Changes will be overwritten on next generation.",
        "#",
        "",
        "from __future__ import annotations",
        "",
    ]

    for key in sorted(ci_defaults.keys()):
        value = ci_defaults[key]
        escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        lines.append(f'_{key}: str = "{escaped}"')

    lines.append("")
    lines.append("__all__ = [")
    lines.extend(f'    "_{key}",' for key in sorted(ci_defaults.keys()))
    lines.append("]")
    lines.append("")

    return "\n".join(lines)


# endregion FUNC_generate_helpers_py


# region FUNC_check_generated_content
def _check_generated_content(content: str, path: Path, label: str) -> int:
    """Compare generated content with existing file byte-by-byte.

    ## @purpose  Byte-level comparison for --check mode. Returns 0 if match,
    ##            1 if divergence. Prints first 20 lines of unified diff on stderr.
    ## @io        ⇥ content: generated string, path: existing file, label: display name
    ##           → ⎋ int: 0=match, 1=diverges
    ## @complexity O(N) where N = file size
    ## @invariants
    ##   - Reads file as text (UTF-8)
    ##   - Prints diff only on divergence
    ##   - Never writes to disk
    """
    logger.info("[IMP:7][check][START] Checking %s against %s", label, path)

    if not path.is_file():
        logger.error("[IMP:1][check][FAIL] File not found: %s — cannot check", path)
        print(f"[IMP:1][check] File not found: {path} — cannot check", file=sys.stderr)
        return 1

    existing = path.read_text(encoding="utf-8")
    if content == existing:
        logger.info("[IMP:9][check][OK] %s — matches on disk", label)
        return 0

    logger.warning("[IMP:6][check][DIVERGE] %s — content differs from %s", label, path)
    print(f"[IMP:6][check] Divergence in {label}:", file=sys.stderr)
    diff_lines = list(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"{label} (file)",
            tofile=f"{label} (generated)",
        )
    )
    for line in diff_lines[:20]:
        print(line, end="", file=sys.stderr)
    if len(diff_lines) > 20:
        print(f"[IMP:6][check] ... truncated ({len(diff_lines) - 20} more lines)", file=sys.stderr)
    return 1


# endregion FUNC_check_generated_content


# region FUNC_main
def main() -> int:
    """CLI entrypoint.

    ## @purpose  Parse CLI args, load infra + discover modules + scan ports + load defaults,
    ##            generate all output files. Supports --check mode for byte-level verification.
    ## @io        ⇥ sys.argv → ⎋ int exit code (контракт T4: main() -> int)
    ## @complexity O(1) dispatch to sub-functions
    ## @invariants
    ##   - All output files are overwritten if exist (normal mode)
    ##   - --check mode never writes to disk, compares byte-by-byte
    ##   - --smoke-env-output and --helpers-output are optional in both modes
    ##   - --check without --output → error (no file to compare)
    ##   - Exit 0 on success/up-to-date, 1 on error/divergence
    """
    parser = argparse.ArgumentParser(
        description="Generate platform-env.yaml and associated Python files",
    )
    parser.add_argument(
        "--infra",
        required=True,
        type=str,
        help="Path to core/platform-infra.yaml",
    )
    parser.add_argument(
        "--modules-dir",
        required=True,
        type=str,
        help="Path to core/modules/ directory",
    )
    parser.add_argument(
        "--secret-defs",
        required=True,
        type=str,
        help="Path to core/secret-definitions.yaml",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=str,
        help="Output path for generated platform-env.yaml",
    )
    parser.add_argument(
        "--smoke-env-output",
        type=str,
        default=None,
        help="Output path for generated smoke_env_generated.py",
    )
    parser.add_argument(
        "--helpers-output",
        type=str,
        default=None,
        help="Output path for generated env_defaults_generated.py",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with existing files byte-by-byte. "
        "Never writes to disk. Exit 0 if all match, 1 if any diverges.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[IMP:%(levelno)s][%(name)s][%(funcName)s] %(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:7][main][START] generate_platform_env.py")

    infra_path = Path(args.infra).resolve()
    modules_dir_path = Path(args.modules_dir).resolve()
    secret_defs_path = Path(args.secret_defs).resolve()
    output_path = Path(args.output).resolve()

    # ── Pre-flight check ──
    preflight_ok = True
    if not infra_path.is_file():
        logger.error("[IMP:9][main][PREFLIGHT] --infra file not found: %s", infra_path)
        preflight_ok = False
    if not modules_dir_path.is_dir():
        logger.error("[IMP:9][main][PREFLIGHT] --modules-dir not found: %s", modules_dir_path)
        preflight_ok = False
    if not secret_defs_path.is_file():
        logger.error("[IMP:9][main][PREFLIGHT] --secret-defs file not found: %s", secret_defs_path)
        preflight_ok = False

    if not preflight_ok:
        logger.error("[IMP:10][main][FAIL] Pre-flight checks failed — aborting")
        return 1

    logger.info("[IMP:8][main][PREFLIGHT] All pre-flight checks passed")

    # ── Load static infra ──
    infra: dict[str, Any] = load_infra(infra_path)

    # ── Discover profiles ──
    profiles: list[str] = discover_profiles(modules_dir_path)

    # ── Scan compose ports ──
    port_mappings: dict[str, int] = scan_compose_ports(modules_dir_path)

    # ── Scan test ports ──
    test_ports: dict[str, dict[str, int]] = scan_test_ports(modules_dir_path)

    # ── Load CI defaults ──
    ci_defaults: dict[str, str] = load_ci_defaults(secret_defs_path)

    # ── Merge non-secret env_defaults from infra ──
    non_secret: dict[str, str] = {k: str(v) for k, v in infra.get("env_defaults", {}).items()}
    # ci_defaults (secret values) take precedence over non-secret env_defaults
    merged_env_defaults: dict[str, str] = {**non_secret, **ci_defaults}

    logger.info(
        "[IMP:8][main][SUMMARY] Profiles=%d, Ports=%d, TestPorts=%d, Defaults=%d (merged: %d non-secret + %d secret)",
        len(profiles),
        len(port_mappings),
        len(test_ports),
        len(merged_env_defaults),
        len(non_secret),
        len(ci_defaults),
    )

    # ── Generate platform-env.yaml ──
    yaml_content: str = generate_platform_env_yaml(
        infra=infra,
        profiles=profiles,
        port_mappings=port_mappings,
        test_ports=test_ports,
        env_defaults=merged_env_defaults,
    )

    # ── Generate smoke_env_generated.py (optional) ──
    smoke_content: str | None = None
    smoke_env_path: Path | None = None
    if args.smoke_env_output:
        smoke_env_path = Path(args.smoke_env_output).resolve()
        smoke_content = generate_smoke_env_py(ci_defaults)

    # ── Generate env_defaults_generated.py (optional) ──
    helpers_content: str | None = None
    helpers_path: Path | None = None
    if args.helpers_output:
        helpers_path = Path(args.helpers_output).resolve()
        helpers_content = generate_helpers_py(ci_defaults)

    # ══════════════════════════════════════════════════════════════
    # ── CHECK MODE: compare byte-by-byte, never write ──
    # ══════════════════════════════════════════════════════════════
    if args.check:
        logger.info("[IMP:7][main][CHECK] Running check mode — comparing with existing files")

        exit_code = _check_generated_content(yaml_content, output_path, "platform-env.yaml")
        if smoke_content is not None and smoke_env_path is not None:
            exit_code |= _check_generated_content(smoke_content, smoke_env_path, "smoke_env_generated.py")
        if helpers_content is not None and helpers_path is not None:
            exit_code |= _check_generated_content(helpers_content, helpers_path, "env_defaults_generated.py")

        if exit_code == 0:
            logger.info("[IMP:9][main][CHECK] All outputs match — exit 0")
            return 0
        logger.warning("[IMP:6][main][CHECK] Some outputs diverge — exit 1")
        return 1

    # ══════════════════════════════════════════════════════════════
    # ── NORMAL MODE: write outputs to disk ──
    # ══════════════════════════════════════════════════════════════

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(yaml_content)

    logger.info("[IMP:9][main][OK] Written platform-env.yaml to %s", output_path)
    print(f"[IMP:9][main] platform-env.yaml written to {output_path}")

    # ── Generate smoke_env_generated.py (optional) ──
    if smoke_content is not None and smoke_env_path is not None:
        smoke_env_path.parent.mkdir(parents=True, exist_ok=True)
        with open(smoke_env_path, "w") as f:
            f.write(smoke_content)

        logger.info("[IMP:9][main][OK] Written smoke_env_generated.py to %s", smoke_env_path)
        print(f"[IMP:9][main] smoke_env_generated.py written to {smoke_env_path}")

    # ── Generate env_defaults_generated.py (optional) ──
    if helpers_content is not None and helpers_path is not None:
        helpers_path.parent.mkdir(parents=True, exist_ok=True)
        with open(helpers_path, "w") as f:
            f.write(helpers_content)

        logger.info("[IMP:9][main][OK] Written env_defaults_generated.py to %s", helpers_path)
        print(f"[IMP:9][main] env_defaults_generated.py written to {helpers_path}")

    logger.info("[IMP:9][main][DONE] All outputs generated successfully")
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
