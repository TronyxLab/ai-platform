#!/usr/bin/env python3
# GREP_SUMMARY: generate-catalog, catalog.json, project-registry, ai-platform-yaml, index
# STRUCTURE: ▶ init args → ○ scan $PROJECTS_ROOT/*/*/ai-platform.yaml → ⊕ parse YAML → ⊕ build catalog[] → ∑ sort(org, name) → write catalog.json → ⎋ return count
# region MODULE_CONTRACT
## @purpose  Generate catalog.json — центральный реестр всех проектов платформы. Сканирует
##           PROJECTS_ROOT/*/*/ai-platform.yaml, извлекает метаданные, пишет JSON-массив.
## @scope    Вызывается после успешного деплоя (reconfigure monitoring),
##           а также standalone через make generate-catalog.
## @invariants
##   - Обходит $PROJECTS_ROOT/*/*/ai-platform.yaml (org/project двухуровневая вложенность)
##   - Генерирует валидный JSON-массив с name, type, node, org, domain, database, metrics_port
##   - catalog.json сохраняется в CATALOG_FILE (по умолчанию /opt/platform/catalog.json)
##   - Ошибки YAML-парсинга одного проекта НЕ блокируют остальные — WARN + continue
##   - Сортировка по (org, name) для детерминированного вывода
## @rationale Единый источник правды для AI-агентов и мониторинга о составе проектов платформы.
##           Извлечён из inline python3 heredoc generate-catalog.sh в отдельный тестируемый
##           Python-модуль (Strangler-Fig декомпозиция).
## @changes  Extracted from generate-catalog.sh inline heredoc → standalone module with CLI args
## @usecases
##   - make generate-catalog (через generate-catalog.sh facade)
##   - reconfigure monitoring после успешного деплоя
##   - node-update → deploy-modules → healthcheck pipeline
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
from typing import Any

import yaml

from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# ── logging setup ──────────────────────────────────────────────────────────────


class _ImpFilter(logging.Filter):
    """Ensure every log record has a default imp_level for the formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "imp_level"):
            record.imp_level = 0  # type: ignore[attr-defined]
        return True


logging.basicConfig(
    level=logging.INFO,
    format="[IMP:%(imp_level)s][%(funcName)s] %(message)s",
    stream=sys.stderr,
    force=True,
)
log = logging.getLogger("generate_catalog")
log.addFilter(_ImpFilter())


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_generate_catalog
## @purpose  Scan projects_root for ai-platform.yaml files, build and write catalog JSON
## @io       Input:  projects_root (str) — root directory with org/project/ layout
##                   catalog_file (str) — output JSON file path
##           Output: int — number of projects registered in catalog
## @complexity O(n) where n = total project directories scanned
def generate_catalog(projects_root: str, catalog_file: str) -> int:
    """
    Scan PROJECTS_ROOT/*/*/ai-platform.yaml and produce catalog.json.

    Args:
        projects_root: Path to projects root directory (e.g. /opt/projects)
        catalog_file: Path to output JSON file (e.g. /opt/platform/catalog.json)

    Returns:
        Number of projects registered in the catalog.

    Raises:
        SystemExit(1) on unrecoverable I/O errors writing the catalog file.
    """
    # region FUNC_generate_catalog_impl

    catalog: list[dict[str, Any]] = []

    # ▶ discover: iterate org/project two-level hierarchy
    # ────────────────────────────────────────────────────────────────────────────
    if not os.path.isdir(projects_root):
        log.warning("PROJECTS_ROOT does not exist or is not a directory: %s", projects_root, extra={"imp_level": 6})  # type: ignore[call-arg]
        return 0

    for org_dir in os.listdir(projects_root):
        org_path = os.path.join(projects_root, org_dir)
        if not os.path.isdir(org_path):
            continue

        for proj_dir in os.listdir(org_path):
            proj_path = os.path.join(org_path, proj_dir)
            if not os.path.isdir(proj_path):
                continue

            yaml_file = os.path.join(proj_path, "ai-platform.yaml")
            if not os.path.isfile(yaml_file):
                continue

            # ⚡ parse YAML: extract metadata per project
            # ────────────────────────────────────────────────────────────────────
            try:
                entry: dict[str, Any] = _parse_project_yaml(yaml_file, org_dir, proj_dir)
                catalog.append(entry)
                log.log(8, "%s/%s (type=%s)", org_dir, proj_dir, entry["type"], extra={"imp_level": 8})  # type: ignore[call-arg]
            except (OSError, yaml.YAMLError, AttributeError) as exc:
                log.log(6, "WARN: %s: %s", yaml_file, exc, extra={"imp_level": 6})  # type: ignore[call-arg]

    # ∑ sort and persist
    # ────────────────────────────────────────────────────────────────────────────
    catalog.sort(key=lambda x: (x["org"], x["name"]))

    try:
        catalog_dir = os.path.dirname(catalog_file)
        os.makedirs(catalog_dir, exist_ok=True)
        with open(catalog_file, "w") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log.log(9, "FATAL: cannot write %s: %s", catalog_file, exc, extra={"imp_level": 9})  # type: ignore[call-arg]
        # T3.6 (DevPlan 116 B4): business sys.exit → raise PlatformFatalError (IO — ручное вмешательство)
        raise PlatformFatalError(f"Cannot write catalog {catalog_file}: {exc}") from exc

    count = len(catalog)
    log.log(9, "DONE: %d projects registered in %s", count, catalog_file, extra={"imp_level": 9})  # type: ignore[call-arg]
    return count

    # endregion FUNC_generate_catalog_impl


# endregion FUNC_generate_catalog


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC__parse_project_yaml
## @purpose  Parse a single ai-platform.yaml and extract catalog entry fields
## @io       Input:  yaml_file (str) — path to ai-platform.yaml
##                   org_dir  (str) — organization directory name
##                   proj_dir (str) — project directory name
##           Output: dict with keys: name, type, node, org, domain, database, metrics_port
## @complexity O(1) — single file parse + dict lookups
## @rationale Extracted to separate function for testability of individual project parsing
def _parse_project_yaml(yaml_file: str, org_dir: str, proj_dir: str) -> dict[str, Any]:
    """
    Read and parse a single ai-platform.yaml, returning a catalog entry dict.

    Args:
        yaml_file: Absolute path to the YAML file.
        org_dir:   Organization directory name (e.g. "myorg").
        proj_dir:  Project directory name (e.g. "myproject").

    Returns:
        Dictionary with catalog entry fields.

    Raises:
        Various exceptions from yaml.safe_load / file I/O — caller handles them.
    """
    import yaml  # lazy import — only needed when actually parsing YAML

    with open(yaml_file) as f:
        data = yaml.safe_load(f)

    if data is None:
        data = {}

    entry: dict[str, Any] = {
        "name": data.get("name", proj_dir),
        "type": data.get("type", "unknown"),
        "node": data.get("target_node", ""),
        "org": org_dir,
        "domain": None,
        "database": None,
        "metrics_port": None,
    }

    # Extract optional 'needs' block
    needs = data.get("needs", {})
    if isinstance(needs, dict):
        domain_val = needs.get("domain")
        if domain_val and domain_val is not False:
            entry["domain"] = domain_val
        db_val = needs.get("database")
        if db_val and db_val is not False:
            entry["database"] = db_val

    # Extract optional 'monitoring' block
    monitoring = data.get("monitoring", {})
    if isinstance(monitoring, dict):
        entry["metrics_port"] = monitoring.get("metrics_port")

    return entry


# endregion FUNC__parse_project_yaml


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_parse_cli_args
## @purpose  Parse CLI arguments with env var fallback for catalog-file and projects-root
## @io       Input:  argv (list[str]) — command-line arguments
##           Output: argparse.Namespace with catalog_file, projects_root
## @complexity O(1)
def parse_cli_args(argv: list[str]) -> argparse.Namespace:
    """
    Parse CLI arguments, falling back to environment variables if not provided.

    Priority: CLI arg > env var > built-in default.

    Environment variables:
        CATALOG_FILE   — default /opt/platform/catalog.json
        PROJECTS_ROOT  — default /opt/projects

    Returns:
        Namespace with .catalog_file and .projects_root attributes.
    """
    parser = argparse.ArgumentParser(
        description="Generate platform project catalog from ai-platform.yaml files",
    )
    parser.add_argument(
        "--catalog-file",
        default=os.environ.get("CATALOG_FILE", "/opt/platform/catalog.json"),
        help="Output catalog JSON path (default: $CATALOG_FILE or /opt/platform/catalog.json)",
    )
    parser.add_argument(
        "--projects-root",
        default=os.environ.get("PROJECTS_ROOT", "/opt/projects"),
        help="Projects root directory (default: $PROJECTS_ROOT or /opt/projects)",
    )
    return parser.parse_args(argv[1:])


# endregion FUNC_parse_cli_args


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entrypoint — parse args, call generate_catalog, exit with code
## @io       Input:  argv (list[str]) — command-line arguments
##           Output: None (calls sys.exit)
## @complexity O(n) — delegates to generate_catalog
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: parse args, generate catalog, return status code (T4: main() -> int)."""
    try:
        args = parse_cli_args(argv if argv is not None else sys.argv)
        count = generate_catalog(
            projects_root=args.projects_root,
            catalog_file=args.catalog_file,
        )
        return 0 if count >= 0 else 1
    except PlatformError as e:
        log.log(10, "[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)  # type: ignore[call-arg]
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
