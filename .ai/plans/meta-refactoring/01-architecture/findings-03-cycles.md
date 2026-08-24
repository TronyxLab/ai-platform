# Findings 03 — Circular dependencies

Method: AST import graph over `core/**/*.py` (366 modules; 914 static / 144 lazy / 5 TYPE_CHECKING edges), Tarjan SCC + shortest-cycle BFS, lazy-edge mask detection, fresh-interpreter import probes. Cross-package graph is acyclic; all 3 SCCs are intra-package. Shell source-graph and Make include-graph: clean DAGs (0 cycles).

## ARCH-0009 — `deploy/engine`: triangular static cycle built around a "single patchable holder"
- **Severity:** P2 · **Confidence:** 0.95 · **Churn:** M · **Phase:** post-launch
- **Files:** `core/internal/deploy/engine/__init__.py:18` → `engine/engine.py:44,49` → `engine/lifecycle.py:29` → back to package `__init__`
- **Symbols:** `DeployEngine`, `handle_first_deploy`, `perform_rollback`, `save_previous_image`, `_flow`; TRAP[DECISION] 2026-08-15 documents the holder intent
- **Evidence:** importing the package reaches back into the *partially initialized* package; survives only via CPython parent-package-first init + `IMPORT_FROM` submodule fallback (verified by fresh-interpreter probes). Any module-level use of `_flow.<attr>` during init, attribute re-export added to `__init__` before line 18, or non-standard loader → `AttributeError` in the production deploy path.
- **Impact:** latent partial-init crash on the deploy engine; `flow`/`lifecycle` extraction coupled three ways.
- **Minimal fix:** `lifecycle` imports `…engine.flow` by full module path, or move the `shared_docker_compose_up` holder into leaf `results.py`.

## ARCH-0010 — `check_suite`: hub inversion — 6 submodules late-bind the package root that re-exports them; one lazy band-aid masks the closing edge
- **Severity:** P2 · **Confidence:** 0.90 · **Churn:** M–L · **Phase:** post-launch
- **Files:** `check_suite/__init__.py:78-107` (re-export hub); back-edges `from core.internal import check_suite as cs` at `diagnostic.py:34`, `diff.py:31`, `fingerprint.py:35`, `gate.py:36`, plus runner.py/single.py. Masked lazy edge: `diagnostic.py:245` (`from …check_suite.fingerprint import CheckCacheDict` inside `_persist_cache()`)
- **Evidence:** SCC size 8 (`__init__:78 → diagnostic:34 → __init__`). Hoisting L245 closes a static triangle `fingerprint → __init__ → diagnostic → fingerprint`. All uses call-time today — probes pass — but one innocent module-level `cs.<const>` makes `check_suite` unimportable in a specific order, breaking CI diagnostics platform-wide (`make check` everywhere).
- **Impact:** fragility of the platform's single test-entry machinery; monkeypatch contract depends on attributes landing on the partial package.
- **Minimal fix:** extract leaf DI-seam `check_suite/_runtime.py` (`run_cmd`, `load_manifest`, `cache_path`); children import it directly.

## ARCH-0011 — `bootstrap/deploy`: `__init__ ↔ deploy_orchestrator` namespace fan-out through the package object
- **Severity:** P3 · **Confidence:** 0.85 · **Churn:** S · **Phase:** post-launch
- **Files:** `core/internal/bootstrap/deploy/__init__.py:19` ←→ `deploy_orchestrator.py:87-93` (six sibling imports via package object; `orchestrate()` defined at :195, after cycle point)
- **Evidence:** benign today (siblings never import back; verified 2-node SCC). Latent: any sibling reading a package attribute at init, or attribute-based exports before line 19 → partial-init `AttributeError` at bootstrap-node/deploy-context startup.
- **Minimal fix:** rewrite L87-93 as direct submodule-path imports.
