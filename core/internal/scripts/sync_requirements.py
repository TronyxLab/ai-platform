#!/usr/bin/env python3
# GREP_SUMMARY: sync-requirements, requirements.txt, generator, pyproject-dependencies, single-sot, check, tomllib, atomic-write
# STRUCTURE: ▶ parse_args → load_dependencies(tomllib) → generate_requirements(header+pkgs) → ◇ --check? (byte-compare+diff → 0/1) → write_atomic
# region MODULE_CONTRACT
## @purpose  Generate core/requirements.txt from pyproject.toml [project].dependencies — единый
##           SoT runtime-зависимостей. Устраняет ручное дублирование списков (FL7, DevPlan 123 T11):
##           requirements.txt дублировал jsonschema (Step 1b python_deps уже ставит его
##           --ignore-installed) и нёс dev-пакеты (requests, python-dotenv) + транзитивный httpcore.
## @scope    CLI utility; called from Makefile (make generate-requirements, make check-requirements).
##           Standalone — работает на system python3 (macOS/CI) и Python 3.14 (VPS, deadsnakes).
## @invariants
##   - requirements.txt is GENERATED — never edit manually (инвариант 11, Manifest Generation Contract)
##   - SoT: pyproject.toml [project].dependencies — единственный источник runtime-зависимостей
##   - Порядок пакетов = порядок в pyproject.toml (уже алфавитный — детерминирован)
##   - --check mode: byte-level comparison → exit 0/1 + unified diff в stderr (по образцу sync_env_defaults)
##   - Atomic write через shared atomic_writer (DevPlan 119 E5: tempfile + fsync + os.replace)
##   - tomllib (Python 3.11+) — fail-fast с читаемым сообщением на старых интерпретаторах
## @rationale Q: Почему pyproject.toml как SoT? A: PEP 621 — стандарт метаданных проекта. Дублирование
##            списков (pyproject + requirements.txt) — источник дрейфа: dev-пакеты утекали в runtime-список,
##            транзитивный httpcore закреплялся вручную, jsonschema дублировал Step 1b. Единый SoT →
##            единый генератор → zero drift (тот же паттерн, что sync_env_defaults → .env.example).
## @changes 2026-08-03 | DevPlan 123 T11 (FL7) — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

# Standalone CLI bootstrap: `python3 core/internal/scripts/sync_requirements.py` (makefile)
# не имеет `core` пакета на sys.path — добавляем repo root (паттерн sync_env_defaults.py L42-45,
# DevPlan 119 E5: atomic_writer импорт — после bootstrap; иначе system python3 падает
# ModuleNotFoundError: No module named 'core').
if __name__ == "__main__" or not __package__:
    _REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_text as _atomic_write_text
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

# Header-комментарий requirements.txt — маркер GENERATED (инвариант 11) + подсказка регенерации.
GENERATED_HEADER = (
    "# GREP_SUMMARY: requirements.txt, generated, python-runtime-deps, pyproject-dependencies, single-sot\n"
    "# GENERATED from pyproject.toml [project].dependencies — НЕ редактировать вручную "
    "(инвариант 11, DevPlan 123 T11)\n"
    "# Регенерация: make generate-requirements · Проверка: make check-requirements\n"
)


# region FUNC_load_dependencies
def load_dependencies(pyproject_path: Path) -> list[str]:
    """Read [project].dependencies from pyproject.toml preserving declaration order.

    ▶ ┌pyproject_path┐ → ○ tomllib.load → ◇ [project].dependencies? → ⊕ [str, ...] → ⎋ list[str]

    ## @purpose  Extract runtime dependency specifiers (single SoT). Order preserved — pyproject
    ##            declares them alphabetically, so output is deterministic.
    ## @io        ⇥ pyproject_path: Path → ⎋ list[str]
    ##            ⚡ FileNotFoundError — pyproject.toml missing
    ##            ⚡ ConfigValidationError — [project] or [project].dependencies absent
    ##            ⚡ tomllib.TOMLDecodeError — malformed TOML
    ## @complexity O(n) — single TOML parse + list walk
    ## @invariants
    ##   - Fail-fast: missing section raises ConfigValidationError (no silent empty list)
    ##   - Non-string entries rejected (tomllib yields str for valid PEP 508 specifiers)
    """
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    if not isinstance(project, dict) or "dependencies" not in project:
        raise ConfigValidationError(
            f"[IMP:10][sync_req] {pyproject_path} has no [project].dependencies — SoT runtime-зависимостей отсутствует"
        )
    deps = project["dependencies"]
    if not isinstance(deps, list):
        raise ConfigValidationError(
            f"[IMP:10][sync_req] [project].dependencies must be a list, got {type(deps).__name__}"
        )
    result = [str(d) for d in deps]
    logger.info("[IMP:9][sync_req][load] Loaded %d runtime dependencies from %s", len(result), pyproject_path)
    return result


# endregion FUNC_load_dependencies


# region FUNC_generate_requirements
def generate_requirements(dependencies: list[str]) -> str:
    """Render requirements.txt content: GENERATED header + one package per line.

    ▶ ┌dependencies┐ → ⊕ [GENERATED_HEADER] + deps → ⎋ str (trailing newline)

    ## @purpose  Deterministic renderer — byte-identical output for identical input (--check contract).
    ## @io        ⇥ dependencies: list[str] → ⎋ str
    ## @complexity O(n)
    ## @invariants
    ##   - Trailing newline после последнего пакета (POSIX-стандарт, byte-level сравнение)
    ##   - Порядок пакетов НЕ пересортировывается — сохраняет SoT-порядок
    """
    lines = [GENERATED_HEADER]
    lines.extend(f"{d}\n" for d in dependencies)
    return "".join(lines)


# endregion FUNC_generate_requirements


# region FUNC_write_requirements
def write_requirements(content: str, output_path: Path) -> None:
    """Write requirements.txt atomically via shared atomic_writer (E5 — tempfile + fsync + replace)."""
    logger.info("[IMP:7][sync_req][write] Writing %d bytes to %s", len(content), output_path)
    _atomic_write_text(output_path, content)
    logger.info("[IMP:9][sync_req][write] Written atomically to %s", output_path)


# endregion FUNC_write_requirements


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate core/requirements.txt from pyproject.toml [project].dependencies"
    )
    parser.add_argument("--pyproject", required=True, type=str, help="Path to pyproject.toml (SoT)")
    parser.add_argument("--output", required=True, type=str, help="Path to write requirements.txt")
    parser.add_argument("--check", action="store_true", help="Dry-run: diff with existing, exit 1 on divergence")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[IMP:%(levelno)s][sync_req] %(message)s", stream=sys.stderr)

    pyproject_path = Path(args.pyproject).resolve()
    output_path = Path(args.output).resolve()

    if not pyproject_path.is_file():
        logger.error("[IMP:10][sync_req] pyproject.toml not found: %s", pyproject_path)
        return 2
    if tomllib is None:
        logger.error(
            "[IMP:10][sync_req] tomllib unavailable — Python >= 3.11 required (got %s). "
            "Ubuntu 24.04 (VPS 3.14) / macOS / CI python3 — 3.11+",
            sys.version.split()[0],
        )
        return 2

    try:
        dependencies = load_dependencies(pyproject_path)
    except (ConfigValidationError, tomllib.TOMLDecodeError) as exc:
        logger.error("%s", exc)
        return 2

    generated = generate_requirements(dependencies)

    if args.check:
        if not output_path.is_file():
            logger.error("[IMP:9][sync_req][CHECK] Output file %s does not exist — cannot compare", output_path)
            return 1
        existing = output_path.read_text()
        if existing != generated:
            diff_lines = list(
                difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    generated.splitlines(keepends=True),
                    fromfile=str(output_path),
                    tofile="generated",
                )
            )
            for line in diff_lines[:20]:
                sys.stderr.write(line)
            if len(diff_lines) > 20:
                sys.stderr.write(f"... ({len(diff_lines) - 20} more lines)\n")
            logger.error(
                "[IMP:9][sync_req][CHECK] Divergence detected — requirements.txt is stale. Run: make generate-requirements"
            )
            return 1
        logger.info("[IMP:9][sync_req][CHECK] requirements.txt is up-to-date")
        return 0

    write_requirements(generated, output_path)
    logger.info("[IMP:9][sync_req] requirements.txt generated at %s", output_path)
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
