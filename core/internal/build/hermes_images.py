#!/usr/bin/env python3
# GREP_SUMMARY: hermes-images hermes-agent-context build-context platform-amd64 buildkit cache CONTEXT-guard
# STRUCTURE: ▶ parse action (build-context|L2) → ◇ CONTEXT guard (fail-fast) → ○ docker build (--platform linux/amd64, BuildKit cache, -f единый Dockerfile) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Build hermes-agent-context image (единый Dockerfile, L1→L2 коллапс DevPlan 002):
##           docker build через subprocess (docker_orchestrator-стиль), CONTEXT guard в Python.
## @scope    Called by `make hermes-build-context CONTEXT=<org>` напрямую
##           (`python3 -m core.internal.build.hermes_images build-context`) — middle-hop
##           hermes-images.sh схлопнут (DevPlan 173 W1.1), entrypoint build.sh удалён (DevPlan 002 W2).
## @invariants
##   - Единственный action: build-context (L2) — L1 build удалён (build-platform/L1 dispatch gone)
##   - CONTEXT required — guard exit 1 (fail-fast до docker build)
##   - --platform linux/amd64 forced (QEMU on ARM64, native no-op on x86_64)
##   - BuildKit local cache /tmp/.hermes-build-cache (mode=max both directions)
##   - Единый Dockerfile: core/modules/hermes-agent/Dockerfile (base-стадия + final-стадия)
##   - No GHCR push logic — images built locally only (push: make hermes-push-l2)
## @rationale L1 (hermes-agent-base) схлопнут в L2 (hermes-agent-context) — единый образ,
##            единый build-path. Version-машинерия (L1 sha/version-теги) удалена.
## @changes  2026-08-02 | DevPlan 118 E8 — Created (Python-порт hermes-images.sh, 77 LOC)
## @changes  2026-08-16 | DevPlan 173 W1.1 — middle-hop hermes-images.sh удалён; entrypoint вызывает напрямую
## @changes  2026-08-16 | DevPlan 002 W2 T2.1 — build_l1/L1_IMAGE/build-platform dispatch удалены (коллапс L1→L2);
##            build_l2 → build_context; Dockerfile — единый core/modules/hermes-agent/Dockerfile
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_NAME = "hermes-agent-context"
BUILD_PLATFORM = "linux/amd64"
_CACHE_DIR = "/tmp/.hermes-build-cache"  # nosec B108 — BuildKit local cache dir (канон 118 E8)
# Канон-таймаут docker build (hermes-сборка — тяжёлая, BUILD_TIMEOUT=300 из shared/timeouts)
from core.internal.shared.timeouts import BUILD_TIMEOUT

PLATFORM_ROOT = Path(__file__).resolve().parents[3]  # repo root (core/internal/build → root)

# W11: DI-тип runner (DevPlan 167 D1) — subprocess.run-контракт (fake-раннеры тестов — тот же)
BuildRunner = Callable[..., subprocess.CompletedProcess[str]]


# region DATA_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Значения НЕ задаются class-атрибутами — hasattr(namespace, dest)
    ##            перебивает parser-дефолты; поля заполняет parse_args(namespace=CliArgs()).
    """

    action: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


# endregion DATA_CliArgs


# region FUNC_build_context
## @purpose  Собрать hermes-agent-context (единый Dockerfile): CONTEXT guard (fail-fast) +
##           docker build с --build-arg CONTEXT.
## @io       ⇥ context: str (из CONTEXT env), runner: Callable | None, env: dict[str, str] | None
##           → ⎋ bool (success)
## @complexity O(1) — один docker build subprocess
def build_context(
    context: str,
    runner: BuildRunner | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Build hermes-agent-context image. Requires non-empty CONTEXT (guard).

    Args:
        context: CONTEXT value (must be non-empty — guard).
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional path-env override (DI — DevPlan 167 D1): keys PLATFORM_ROOT/CACHE_DIR.
    """
    if not context:
        logger.error("[IMP:10][hermes-images] ERROR: CONTEXT env var is required for hermes-agent build")
        return False
    env = env if env is not None else {}
    platform_root = Path(env.get("PLATFORM_ROOT", str(PLATFORM_ROOT)))
    cache_dir = env.get("CACHE_DIR", _CACHE_DIR)
    dockerfile = platform_root / "core" / "modules" / "hermes-agent" / "Dockerfile"
    Path(cache_dir).mkdir(exist_ok=True, parents=True)
    logger.info("[IMP:9][hermes-images] BuildKit cache directory ready: %s", cache_dir)

    cmd = [
        "docker",
        "build",
        "--platform",
        BUILD_PLATFORM,
        "-t",
        IMAGE_NAME,
        "--build-arg",
        f"CONTEXT={context}",
        "--cache-from",
        f"type=local,src={cache_dir}",
        "--cache-to",
        f"type=local,dest={cache_dir},mode=max",
        "-f",
        str(dockerfile),
        str(platform_root),
    ]
    logger.info("[IMP:8][hermes-images] docker build (context=%s)", context)
    return _run(cmd, runner=runner)


# endregion FUNC_build_context


# region FUNC_run
## @purpose  Выполнить docker build через subprocess с канон-таймаутом (BUILD_TIMEOUT=300).
## @io       ⇥ cmd: list[str], runner: Callable | None → ⎋ bool
## @complexity O(1) — single subprocess.run
def _run(cmd: list[str], runner: BuildRunner | None = None) -> bool:
    """Run docker build subprocess with BUILD_TIMEOUT; False on failure/timeout.

    Args:
        cmd: docker build command list.
        runner: Optional subprocess.run override (DI — DevPlan 167 D1). None = real subprocess.run.
    """
    run = runner if runner is not None else subprocess.run
    try:
        result = run(cmd, timeout=BUILD_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("[IMP:9][hermes-images] docker build error: %s", exc)
        return False
    if result.returncode != 0:
        logger.error("[IMP:10][hermes-images] docker build FAILED (exit %d)", result.returncode)
        return False
    logger.info("[IMP:9][hermes-images] === build complete: %s ===", IMAGE_NAME)
    return True


# endregion FUNC_run


# region FUNC_main
def main(
    argv: list[str] | None = None,
    runner: BuildRunner | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """CLI entry: `python3 -m core.internal.build.hermes_images {build-context|L2}`.

    ▶ ┌argv┐ → ◇ action dispatch → ◇ CONTEXT guard → ⎋ exit 0|1

    Args:
        argv: Optional CLI args override (DI — DevPlan 167 D1, AF-4). None = sys.argv.
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional env override (DI — DevPlan 167 D1): CONTEXT/PLATFORM_ROOT/CACHE_DIR.
            None/пусто = os.environ fallback для CONTEXT + модульные константы.
    """
    env = env if env is not None else {}
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Build hermes-agent-context Docker image")
    parser.add_argument(
        "action",
        choices=["build-context", "L2"],
        help="build-context (L2, requires CONTEXT env) — единственный action после L1 коллапса",
    )
    # W11: типизированный namespace — действия build-context/L2 эквивалентны (L1 удалён DevPlan 002)
    _ = parser.parse_args(argv, namespace=CliArgs())

    context = env.get("CONTEXT", os.environ.get("CONTEXT", ""))
    return 0 if build_context(context, runner=runner, env=env) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
