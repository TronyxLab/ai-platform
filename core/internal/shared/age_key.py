#!/usr/bin/env python3
# GREP_SUMMARY: age-key, detect-age-key, AGE_SECRET_KEY, SOPS_AGE_KEY, AGE_SECRET_KEY_FILE, shared, compat-shim, re-export
# STRUCTURE: ▶ import node_detect.detect_age_key (canonical SoT) → ⊕ re-export → ◇ __main__? → ○ CLI print key → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Compat-re-export shim for detect_age_key(). The canonical implementation
##           lives in node_detect.py (DevPlan 104). This module preserves backward
##           compatibility for consumers importing `from age_key import detect_age_key`
##           (decrypt_secrets.py, tests/unit/test_age_key.py) and for the standalone
##           CLI (`python3 age_key.py`).
## @scope    Re-exports only — no business logic. All detection logic is in node_detect.py.
## @invariants
##   1. detect_age_key is re-exported unchanged (chain: AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE)
##   2. CLI behavior identical to pre-shim: stdout key + exit 0 / stderr diagnostic + exit 1
##   3. No new logic here — node_detect.py is the single source of truth
## @rationale DevPlan 104 D1/P3: creating a second detect_age_key copy in node_detect.py would
##            violate DRY-first. age_key.py → re-export shim keeps existing consumers working
##            (decrypt_secrets.py imports via sys.path bootstrap, not package path) while
##            consolidating logic in one module.
## @changes  2026-07-25 | DevPlan 078 — Created shared detect_age_key module
##           2026-07-31 | DevPlan 104 — Reduced to compat-re-export shim (logic → node_detect.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# ── Re-export from canonical module (DevPlan 104 D1) ──
# 🧐 TRAP[DECISION] · 2026-07-31 · — · Dual-context import in compat shim
# · Rejected: unconditional `from node_detect import detect_age_key` (breaks when age_key
# ·   is imported package-qualified as core.internal.shared.age_key)
# · Reason: consumers resolve age_key via DIFFERENT sys.path layouts — decrypt_secrets.py /
# ·   test_age_key.py insert only core/internal/shared (no project root), while python3 -m
# ·   pytest runs have project root on sys.path. try/except ModuleNotFoundError covers both;
# ·   node_detect.py has zero third-party imports, so the fallback cannot mask real failures.
# · Rev: if node_detect.py ever gains third-party deps, replace fallback with explicit
# ·   sys.path bootstrap + local import only.
# Prefer the package-qualified import (project root on sys.path — e.g. python3 -m pytest);
# fall back to the local sibling import for standalone contexts where only
# core/internal/shared is on sys.path (decrypt_secrets.py sys.path bootstrap).
try:
    from core.internal.shared.node_detect import detect_age_key
except ModuleNotFoundError:  # standalone context — shared/ is on sys.path
    from node_detect import detect_age_key


# region FUNC_CLI
## @purpose — CLI entrypoint for standalone testing (backward compat with `python3 age_key.py`).
##            Prints key to stdout or exits 1. Delegates to the canonical node_detect logic.
## @io — ⇥ sys.argv → ⎋ exit code (0 = key found, 1 = not found)
## @complexity — O(1)
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    result = detect_age_key()
    if result:
        print(result)
        sys.exit(0)
    else:
        print("AGE_SECRET_KEY not found", file=sys.stderr)
        sys.exit(1)
# endregion FUNC_CLI
