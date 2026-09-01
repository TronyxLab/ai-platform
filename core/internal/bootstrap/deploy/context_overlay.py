# GREP_SUMMARY: context-overlay, ensure_context_repo, git-clone, git-pull, s9-cache, deploy-modules, strangler
# STRUCTURE: ┌ensure_context_repo()┐ → ◇ node.yaml read → ◇ clone/pull с s9-cache → ⊕ return exit_code
"""
# region MODULE_CONTRACT
## @purpose  Git context-overlay git repo clone/pull with S9 caching. Extracted from deploy-modules.sh (W4-E1).
## @scope    Context overlay repository management for /opt/{context}/platform/. Reads node.yaml via NodeYaml facade,
##           manages git clone/pull lifecycle with 5-minute pull caching. Called from deploy-modules.sh shell facade.
## @invariants
##   - No context: field in node.yaml → SKIP (return 0)
##   - Context path exists + cached (<300s) → SKIP pull
##   - Context path exists + cache expired → git pull --ff-only (non-fatal if fails)
##   - Context path absent + repos.core URL → git clone (return 1 if fails)
##   - Context path absent + no URL → WARN (return 0)
## @rationale Replaces shell ensure_context_repo() function (lines 219-269) from deploy-modules.sh 1664-line monolith.
##            Uses NodeYaml facade instead of inline python3 -c or direct yaml.safe_load for node.yaml parsing.
##            S9 pull caching (300s) avoids redundant git operations during CI retry cycles.
## @changes
##   2026-07-22 · Created (W4-E1 Strangler extraction)
# endregion MODULE_CONTRACT

Replaces the ensure_context_repo() shell function (lines 219-269 of deploy-modules.sh)
with typed Python: NodeYaml-based node.yaml parsing, subprocess git operations, S9 pull caching.

# STRUCTURE: ┌parse node.yaml (context+repos)┐ → ◇ context? no→SKIP ┐
#                               ↓ yes                                │
#                    ┌path exists?┐                                  │
#                    │ yes        │ no                               │
#                    ▼            ▼                                  │
#              ◇ cache <300s?  read repos.core                  │
#              │ yes→SKIP┐     ┌─url? no→WARN───────────────────────┤
#              │ no      │     │ yes                                │
#              ▼         │     ▼                                    │
#           git pull     │  git clone                               │
#           update ts    │  update ts                               │
#              └─────────┴─────┘                                    │
#                           └──⎋ return 0 (success/skip) / 1 (fail)
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol, cast

from core.internal.shared.deploy_paths import context_pull_ts_path
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_yaml import NodeYaml

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 60 (git pull) → SYSTEM_CMD_TIMEOUT; 120 (git clone) → LIFECYCLE_CMD_TIMEOUT.
from core.internal.shared.timeouts import LIFECYCLE_CMD_TIMEOUT, SYSTEM_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ⚠️ TRAP[DECISION] · 2026-07-22 · — · Cache duration 300s matches deploy-modules.sh original
# · Rejected: no caching (git pull on every deploy cycle — N+1, wasteful at scale)
# · Reason: 300s = typical deploy-cycle interval; avoids redundant pulls during CI retries
# · Rev: if context-overlay git server rate-limits clients → increase to 600s or add exponential backoff

CONTEXT_PULL_CACHE_SECONDS: int = 300
"""Seconds before a new git pull is allowed (S9 cache cooldown)."""

CONTEXT_PULL_TS_PATH: str = str(context_pull_ts_path())
"""Filesystem path storing the last git pull Unix timestamp (канон deploy_paths, 170 W1-A2)."""

# M13a (security hardening): контекст = kebab-case org-имя (node.yaml#context). Класс совпадает
# с validate_project_name, БЕЗ verb-reserve — reject slashes/dots/инжекционных символов
# (path traversal через /opt/{context}/platform и git clone).
_CONTEXT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


# region FUNC_ensure_context_repo
## @purpose  Ensure context overlay git repository is present and up-to-date.
##           Clone if absent; git pull --ff-only with S9 caching if present.
## @io       ⇥ node_yaml_path: str (path to node.yaml on disk)
##           ⎋ int: 0 = success or skip, 1 = clone failed (warn)
## @complexity  O(1) + subprocess git operations
## @invariants
##   - If node.yaml has no `context:` field → SKIP immediately (return 0)
##   - If context path already exists → pull with S9 cache check
##   - If context path does NOT exist → clone from repos.core URL
##   - Git pull failure is non-fatal (return 0, log WARN)
##   - Git clone failure returns 1 (log WARN)
## @rationale Extracted directly from deploy-modules.sh (lines 219-269).
##            Uses NodeYaml facade instead of inline python3 -c or direct yaml.safe_load.
##            See TRAP[DECISION] on CONTEXT_PULL_CACHE_SECONDS for caching rationale.
def ensure_context_repo(node_yaml_path: str) -> int:
    """Clone or pull the context overlay git repo with 5-minute pull caching.

    Returns 0 on success/skip, 1 on clone failure.
    """
    logger.info("[IMP:7][ensure_context_repo][start] node_yaml=%s", node_yaml_path)

    # 1. Read `context:` field from node.yaml (using yaml, not grep)
    context_name = _read_context_name(node_yaml_path)
    if not context_name:
        logger.info("[IMP:8][ensure_context_repo][skip] No context field in node.yaml — SKIP")
        return 0

    # M13a (security hardening): валидация context_name ПЕРЕД построением /opt/{name}/platform
    # и git clone — защита от path traversal/инжекции через node.yaml#context.
    if not _CONTEXT_NAME_RE.match(context_name):
        logger.error("[IMP:10][ensure_context_repo][invalid] Invalid context name: %r (M13a)", context_name)
        return 1

    # 3. Context path = /opt/{context_name}/platform
    context_path = f"/opt/{context_name}/platform"
    logger.info(
        "[IMP:7][ensure_context_repo][context] context_name=%s, context_path=%s",
        context_name,
        context_path,
    )

    # 4. If path exists → pull with S9 cache
    # os.path.isdir — тестовый seam: test_context_overlay мокает context_overlay.os.path.isdir
    # (Path.is_dir() обходит мок); PTH112 — per-file-ignore (ruff_policy.md)
    if os.path.isdir(context_path):
        logger.info("[IMP:8][ensure_context_repo][branch] Context path exists — pull branch")
        return _pull_with_cache(context_path)

    # 5. If path does NOT exist → clone
    logger.info("[IMP:8][ensure_context_repo][branch] Context path absent — clone branch")
    return _clone_context_repo(node_yaml_path, context_path)


# endregion FUNC_ensure_context_repo


# region FUNC__read_context_name
## @purpose  Extract `context:` field from node.yaml via NodeYaml facade.
## @io       ⇥ node_yaml_path: str → ⎋ str (context name, empty if absent or error)
## @complexity  O(1) — single key lookup via NodeYaml
def _read_context_name(node_yaml_path: str) -> str:
    """Read the `context:` field from node.yaml via NodeYaml.

    Uses the NodeYaml facade (NodeYaml.get_context()) instead of direct yaml.safe_load.

    Returns the context name string, or '' if absent/unreadable.
    """
    ## @invariants
    ##   - Returns '' on any exception (file not found, yaml parse error)
    ##   - Never raises — all exceptions caught and logged at IMP:7
    try:
        ctx = NodeYaml(node_yaml_path).get_context()
        logger.info("[IMP:8][_read_context_name] context='%s' (from %s)", ctx, node_yaml_path)
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        logger.warning("[IMP:7][_read_context_name][error] Failed to read context: %s", exc)
        return ""
    else:
        return ctx


# endregion FUNC__read_context_name


# region FUNC__pull_with_cache
## @purpose  Execute git pull --ff-only with S9 cooldown cache (skip if pulled <300s ago).
## @io       ⇥ context_path: str (path to existing git repo)
##           ⎋ int: always 0 (pull failure is non-fatal per original shell behavior)
## @complexity  O(1) + git pull subprocess
## @invariants
##   - Pull skipped if timestamp file shows <300s since last pull
##   - Timestamp file is created/updated regardless of pull success (non-fatal semantics)
##   - Path.mkdir(parents=True) ensures /var/lib/platform/ exists on first pull
##   - git pull failure logged as WARN, does NOT propagate
def _pull_with_cache(context_path: str) -> int:
    """Git pull --ff-only with S9 caching logic (skip if pulled <300s ago).

    Matches original shell behavior: timestamp check, pull, timestamp update.
    Returns 0 (always — pull failure is non-fatal).
    """
    pull_ts_path = Path(CONTEXT_PULL_TS_PATH)
    now = int(time.time())
    last_pull = 0

    # Read existing timestamp file
    if pull_ts_path.exists():
        try:
            last_pull = int(pull_ts_path.read_text().strip())
            logger.info("[IMP:8][_pull_with_cache][ts] Last pull timestamp: %d", last_pull)
        except (ValueError, OSError) as exc:
            last_pull = 0
            logger.warning("[IMP:7][_pull_with_cache][ts] Invalid or unreadable timestamp: %s", exc)

    elapsed = now - last_pull
    if elapsed < CONTEXT_PULL_CACHE_SECONDS:
        logger.info(
            "[IMP:8][_pull_with_cache][skip] Pulled %ds ago (<%ds cache) — SKIP (S9 cache)",
            elapsed,
            CONTEXT_PULL_CACHE_SECONDS,
        )
        return 0

    logger.info("[IMP:8][_pull_with_cache][pull] git pull --ff-only at %s", context_path)
    try:
        result = subprocess.run(
            ["git", "-C", context_path, "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=SYSTEM_CMD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][_pull_with_cache][warn] git pull timed out (60s): %s", context_path)
        _update_timestamp(pull_ts_path, now)
        return 0
    except FileNotFoundError:
        logger.warning("[IMP:7][_pull_with_cache][warn] git binary not found — cannot pull: %s", context_path)
        _update_timestamp(pull_ts_path, now)
        return 0

    if result.returncode != 0:
        logger.warning(
            "[IMP:7][_pull_with_cache][warn] git pull failed (non-fatal): %s — stderr: %s",
            context_path,
            result.stderr.strip(),
        )
    else:
        logger.info("[IMP:9][_pull_with_cache][done] git pull successful: %s", context_path)

    # Update timestamp regardless of pull result (per original shell behavior)
    _update_timestamp(pull_ts_path, now)
    return 0


# endregion FUNC__pull_with_cache


# region FUNC__clone_context_repo
## @purpose  Clone context overlay git repo from repos.core URL in node.yaml.
## @io       ⇥ node_yaml_path: str, context_path: str
##           ⎋ int: 0 = success / no URL (skip), 1 = clone failed
## @complexity  O(1) + git clone subprocess
## @invariants
##   - If repos.core is absent/empty → log WARN, return 0 (no-op)
##   - git clone failure → log WARN with remediation instructions, return 1
##   - Clone timeout = 120s (typically larger repos)
# 🧐 TRAP[DECISION] · 2026-09-01 · — · Доступ VPS к приватному overlay-репо — read-only deploy key
# ·   + SSH-алиас (DevPlan 022 TASK-5; repos.core = `git@github.com-overlay:<org>/<ctx>-overlay.git`)
# · Rejected: (1) публичный overlay-репо (нулевая настройка, но публичный IP ноды + домены в репо);
#   (2) token в HTTPS URL (токен в node.yaml → sprawl в /opt/node-configs) · Решение пользователя
#   2026-09-01 · Reason: старое зеркало `<org>/ai-platform` было PUBLIC — канал клона был
#   unauthenticated HTTPS; приватный overlay требует auth, deploy key scoped на один репо,
#   приватный ключ живёт ТОЛЬКО на ноде (~/.ssh/id_ed25519_github_overlay, вне git) ·
# · Rev: если появится >1 приватный git-канал с ноды — вынести в общий git credential helper
def _clone_context_repo(node_yaml_path: str, context_path: str) -> int:
    """Clone context overlay repo from repos.core URL.

    If repo URL is missing from node.yaml, logs a WARN and returns 0.
    If clone fails, logs WARN with remediation and returns 1.
    """
    repo_url = _read_repo_url(node_yaml_path)

    if not repo_url:
        logger.warning("[IMP:7][_clone_context_repo][warn] No repos.core in node.yaml")
        logger.warning(
            "[IMP:7][_clone_context_repo][warn] Create %s manually or add repos.core to node.yaml",
            context_path,
        )
        return 0

    logger.info("[IMP:8][_clone_context_repo][clone] git clone %s → %s", repo_url, context_path)
    try:
        result = subprocess.run(
            ["git", "clone", repo_url, context_path],
            capture_output=True,
            text=True,
            timeout=LIFECYCLE_CMD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][_clone_context_repo][warn] git clone timed out (120s): %s", repo_url)
        return 1
    except FileNotFoundError:
        logger.warning("[IMP:7][_clone_context_repo][warn] git binary not found — cannot clone")
        return 1

    if result.returncode != 0:
        logger.warning(
            "[IMP:7][_clone_context_repo][warn] git clone failed: %s — stderr: %s",
            repo_url,
            result.stderr.strip(),
        )
        logger.warning(
            "[IMP:7][_clone_context_repo][warn] Create %s manually or add repos.core to node.yaml",
            context_path,
        )
        return 1

    logger.info("[IMP:9][_clone_context_repo][done] Context repo cloned: %s", context_path)
    return 0


# endregion FUNC__clone_context_repo


# region FUNC__read_repo_url
## @purpose  Extract repos.core URL from node.yaml via NodeYaml facade.
## @io       ⇥ node_yaml_path: str → ⎋ str (repo URL, empty if absent)
## @complexity  O(1) — dotted-key lookup via NodeYaml
def _read_repo_url(node_yaml_path: str) -> str:
    """Read the `repos.core` field from node.yaml via NodeYaml.

    Uses NodeYaml.get("repos.core", default="") instead of direct yaml.safe_load.

    Returns the repo URL string, or '' if absent/unreadable.
    """
    ## @invariants
    ##   - Returns '' on any exception (file not found, yaml parse error)
    ##   - Handles None values safely via NodeYaml.get(default="")
    try:
        url = NodeYaml(node_yaml_path).get("repos.core", default="")
        logger.info("[IMP:8][_read_repo_url] repos.core='%s' (from %s)", url, node_yaml_path)
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        logger.warning("[IMP:7][_read_repo_url][error] Failed to read repos.core: %s", exc)
        return ""
    else:
        return url


# endregion FUNC__read_repo_url


# region FUNC__update_timestamp
## @purpose  Write current Unix timestamp to pull-cache file, creating parent dirs as needed.
## @io       ⇥ pull_ts_path: Path, now: int → ⎋ None (side-effect: file write)
## @complexity  O(1)
def _update_timestamp(pull_ts_path: Path, now: int) -> None:
    """Write the current timestamp to the pull-cache file.

    Creates /var/lib/platform/ directory if it does not exist.
    Failures are logged as WARN (non-fatal).
    """
    try:
        pull_ts_path.parent.mkdir(parents=True, exist_ok=True)
        _ = pull_ts_path.write_text(str(now))
        logger.info("[IMP:8][_update_timestamp] Wrote timestamp %d to %s", now, pull_ts_path)
    except OSError as exc:
        logger.warning("[IMP:7][_update_timestamp][error] Cannot write timestamp %s: %s", pull_ts_path, exc)


# endregion FUNC__update_timestamp


# region FUNC_main
## @purpose  CLI entrypoint: --action {ensure} --node-yaml <path\>
##           Called from deploy-modules.sh shell facade after Strangler extraction.
## @io       ⇥ sys.argv → ⎋ exit code via sys.exit
## @complexity  O(1) + delegated ensure_context_repo
## @invariants
##   - --action must be one of: ensure
##   - --node-yaml is required
##   - Exits with delegated return code (0=success/skip, 1=clone warn/fail)
## @rationale Standard CLI entrypoint for shell→Python Strangler pattern.
##            Each Python module in deploy/ has its own main() for independent invocation.
class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    action: str
    node_yaml: str


def main() -> int:
    """CLI entrypoint: context_overlay.py --action ensure --node-yaml <path>"""
    parser = argparse.ArgumentParser(
        description="Context overlay git operations (clone/pull with S9 caching)",
    )
    _ = parser.add_argument(
        "--action",
        required=True,
        choices=["ensure"],
        help="Action: ensure = clone or pull context overlay repo",
    )
    _ = parser.add_argument(
        "--node-yaml",
        required=True,
        dest="node_yaml",
        help="Path to node.yaml file (e.g. /opt/platform/core/node-configs/<node>.yaml)",
    )

    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args()))

    logging.basicConfig(
        level=logging.INFO,
        format="[IMP:%(levelno)s][%(name)s][%(funcName)s] %(message)s",
    )

    logger.info("[IMP:7][main][start] action=%s, node_yaml=%s", args.action, args.node_yaml)

    if args.action == "ensure":
        exit_code = ensure_context_repo(args.node_yaml)
        logger.info("[IMP:9][main][exit] ensure_context_repo returned %d", exit_code)
        return exit_code
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
