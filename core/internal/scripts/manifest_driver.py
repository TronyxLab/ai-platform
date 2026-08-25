#!/usr/bin/env python3
# GREP_SUMMARY: manifest_driver, manifest-check, G1-G6, check-subcommand, freshness, CI, direct-call, check-suite
# STRUCTURE: ▶ check (G1→G6 --check) → ◇ aggregate errors → ◇ CI-DIAG diff → ⊕ exit 0|1
# region MODULE_CONTRACT
## @purpose  Единый Python-драйвер проверки актуальности сгенерированных манифестов
##           (G1-G6). Прямой вызов из core/check-suite.yaml (суит check-manifests) —
##           заменяет make-таргет check-manifests (План 175 W2.1/W2.2): суиты
##           check-suite.yaml больше НЕ вызывают make-таргеты.
## @scope    Standalone-скрипт (как генераторы G1-G6): make/check-suite запускают ФАЙЛОМ
##           без PYTHONPATH — yaml/argparse/subprocess/shutil только stdlib.
## @invariants
##   - check: запускает G1-G6 с --check в каноническом порядке; exit 0 = все fresh, 1 = любой stale
##   - Аргументы G1-G6 — константы (зеркало make-переменных G1-G6_ARGS из makefiles/manifest.mk);
##     дрейф generate/check ловится самим --check (сравнение с коммиченными файлами)
##   - G3 gmake-path резолвится рантаймом (shutil.which gmake → make → "make")
##   - При stale печатается git diff по генерируемым путям (CI-самодиагностика, паритет
##     прежнего check-manifests REPAIR_RECIPE)
## @rationale check-manifests (30+ LOC inline-shell в manifest.mk) → Python-драйвер:
##            языковая политика (новый код = Python), единый SoT прямых вызовов суитов.
## @changes  2026-08-16 | Created (План 175 W2.1 — manifest_driver check)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import ClassVar

# region CONSTANTS

# Аргументы генераторов G1-G6 (зеркало make-переменных G1-G6_ARGS из makefiles/manifest.mk).
# ⚠️ TRAP[DECISION] · 2026-08-16 · — · G1-G6_ARGS в двух местах (manifest.mk generate + здесь check)
# · Rejected: инжекция аргументов через env/make (runtime-связность фасадов) · Reason: check — прямой
# ·   вызов без make-индрекции; generate-таргеты (DAG в manifest.mk) — отдельный контур. Дрейф
# ·   ловится --check: рассинхрон аргументов = рассинхрон генерируемых файлов = RED.
_G1_ARGS = [
    "--secret-defs",
    "core/secret-definitions.yaml",
    "--modules-dir",
    "core/modules",
    "--output",
    "core/secrets-manifest.yaml",
]
_G2_ARGS = [
    "--infra",
    "core/platform-infra.yaml",
    "--modules-dir",
    "core/modules",
    "--secret-defs",
    "core/secret-definitions.yaml",
    "--output",
    "platform-env.yaml",
    "--smoke-env-output",
    "tests/_conftest/smoke_env_generated.py",
    "--helpers-output",
    "tests/helpers/env_defaults_generated.py",
]
_G4_ARGS = [
    "--manifest",
    "core/entrypoint-manifest.yaml",
    "--agents-md",
    "core/AGENTS.md",
    "--marker",
    "canon_table",
]
_G4R_ARGS = [
    "--target",
    "root",
    "--manifest",
    "core/entrypoint-manifest.yaml",
    "--agents-md",
    "AGENTS.md",
]
_G5_ARGS = [
    "--platform-env",
    "platform-env.yaml",
    "--secret-defs",
    "core/secret-definitions.yaml",
    "--output",
    ".env.example",
]
_G6_ARGS = [
    "--policy",
    "core/internal/llm/policy.yaml",
    "--output",
    "core/modules/litellm/config/litellm-config.yml",
]

# Генерируемые пути для CI-DIAG diff при stale (паритет check-manifests REPAIR_RECIPE).
_GENERATED_PATHS = [
    "core/secrets-manifest.yaml",
    "platform-env.yaml",
    "core/entrypoint-manifest.yaml",
    "core/AGENTS.md",
    "AGENTS.md",
    ".env.example",
    "core/modules/litellm/config/litellm-config.yml",
]

_PY = sys.executable

# endregion CONSTANTS


# region CHECK_LOGIC


def _gmake_path() -> str:
    """Resolve gmake path: shutil.which(gmake) → which(make) → 'make'."""
    return shutil.which("gmake") or shutil.which("make") or "make"


def _g3_args() -> list[str]:
    """G3 args (dynamic gmake-path)."""
    return [
        "--makefile-dir",
        ".",
        "--gmake-path",
        _gmake_path(),
        "--existing-manifest",
        "core/entrypoint-manifest.yaml",
        "--tests-dir",
        "tests/gates",
        "--output",
        "core/entrypoint-manifest.yaml",
    ]


def _run_check(label: str, script: str, args: list[str]) -> int:
    """Run one generator with --check; return 0 (fresh) or 1 (stale/error).

    ## @purpose  Один G-шаг проверки: subprocess python3 <script> <args> --check.
    ## @io       ⇥ label, script, args → ⎋ 0/1 (fresh/stale)
    ## @complexity O(1) + время генератора
    ## @invariants — stderr passthrough; rc≠0 → stale
    """
    print(f"[IMP:8][manifest_driver] {label}: {' '.join(args[:4])} ...", file=sys.stderr)
    proc = subprocess.run(
        [_PY, script, *args, "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"[IMP:6][manifest_driver] {label}: STALE (exit {proc.returncode})", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr[-4000:], file=sys.stderr)
        return 1
    return 0


# endregion CHECK_LOGIC


# region CLI


class _DriverArgs(argparse.Namespace):
    """Typed argparse namespace (W11)."""

    subcommand: ClassVar[str]


def main(argv: list[str] | None = None) -> int:
    """CLI: `check` — прогнать G1-G6 --check, агрегировать ошибки.

    ## @purpose  Точка входа check-suite (суит check-manifests, План 175 W2.1).
    ##            АРБИТР ВОПРОСА «диск == генераторы» (DevPlan 16 T2.E): divergence =
    ##            диск устарел относительно SoT — repair = make generate-manifests.
    ##            (Парный арбитр «дерево == HEAD» — test_gate_manifests_up_to_date,
    ##            repair = commit.)
    ## @io       ⇥ argv → ⎋ exit 0 (все fresh) | 1 (хотя бы один stale) | 2 (неизвестный subcommand)
    ## @complexity O(6) — шесть генераторов
    ## @invariants — порядок G1-G6; при stale — git diff по генерируемым путям (CI-DIAG)
    """
    parser = argparse.ArgumentParser(
        prog="manifest_driver.py",
        description="Manifest generation/freshness driver (План 175 W2)",
    )
    parser.add_argument(
        "subcommand",
        choices=("check",),
        help="check: run G1-G6 with --check; exit 0 if all fresh, 1 if any stale",
    )
    parser.parse_args(argv, namespace=_DriverArgs())

    print("[IMP:7][manifest_driver] Checking all generated manifests are up to date...", file=sys.stderr)
    errors = 0
    errors += _run_check("G1: secrets-manifest", "core/internal/scripts/generate_secrets_manifest.py", _G1_ARGS)
    errors += _run_check("G2: platform-env", "core/internal/scripts/generate_platform_env.py", _G2_ARGS)
    errors += _run_check("G3: entrypoint-manifest", "core/internal/scripts/generate_entrypoint_manifest.py", _g3_args())
    errors += _run_check("G4: AGENTS.md", "core/internal/scripts/generate_agents_md.py", _G4_ARGS)
    errors += _run_check("G4-root: root AGENTS.md glossary", "core/internal/scripts/generate_agents_md.py", _G4R_ARGS)
    errors += _run_check("G5: .env.example", "core/internal/scripts/sync_env_defaults.py", _G5_ARGS)
    errors += _run_check("G6: litellm-config", "core/internal/llm/config_renderer.py", _G6_ARGS)

    if errors > 0:
        print("[GATE:FAIL][id:check-manifests][class:L1]", file=sys.stderr)
        # DevPlan 16 T2.E (проц.№1): арбитр вопроса «диск == генераторы» — правильное
        # действие РЕГЕНЕРАЦИЯ (в отличие от pytest-арбитра test_manifests_up_to_date,
        # который проверяет «дерево == HEAD» и требует commit).
        print(">>> REPAIR_RECIPE_START >>>", file=sys.stderr)
        print(
            "Run: make generate-manifests   # регенерировать диск из SoT (арбитр 'диск == генераторы')", file=sys.stderr
        )
        print("make fix-gate && git add -u", file=sys.stderr)
        print("<<< REPAIR_RECIPE_END <<<", file=sys.stderr)
        print(
            "Если файлы правились руками ВНЕ GENERATED-регионов — регенерация затрёт правку:",
            "правь SoT, не generated-файл.",
            file=sys.stderr,
        )
        print("=== [CI-DIAG][check-manifests] FULL git diff по генерируемым путям ===", file=sys.stderr)
        subprocess.run(["git", "--no-pager", "diff", "--", *_GENERATED_PATHS], check=False)
        return 1
    print("[IMP:9][manifest_driver] All generated manifests are up to date.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
