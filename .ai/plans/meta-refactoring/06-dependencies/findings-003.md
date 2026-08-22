# Findings 003 · Circular dependencies & import fragility

## DEP-0010 · check_suite `__init__` ↔ manifest/gate cycle — `make check` entrypoint fragility
- Severity: HIGH · Category: circular · Confidence: HIGH
- Files: `core/internal/check_suite/__init__.py:57-65,78+,110,113` ↔ `manifest.py:32`, `gate.py:37`
- Dependency chain: `__init__` re-exports manifest/gate; both import constants (`PROJECT_ROOT`, `VALID_GATE_MODES`, `VALID_TIERS`) back from `__init__`
- Coupling mechanism: module-level cycle that works ONLY because constants are assigned before the re-export block
- Why dangerous: any reorder of `__init__` (constants below re-exports) → `ImportError: cannot import name 'PROJECT_ROOT'` at the production entrypoint of `make check`/`make gate`
- Evidence: exact line numbers above; cycle confirmed by reading imports
- Scenario: innocent cleanup of __init__ ordering during launch week kills the single test command of the platform
- Impact: all verification tooling down
- Minimal decoupling: move the 3 constants to `check_suite/constants.py`; update 2 importers
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (cheap, removes entrypoint landmine)

## DEP-0011 · deploy.engine package ↔ lifecycle module cycle on DeployEngine boot path
- Severity: HIGH · Category: circular · Confidence: HIGH
- Files: `deploy/engine/__init__.py:17-19` → cli → engine.py:49 → lifecycle.py:29 → `from core.internal.deploy.engine import flow`
- Coupling mechanism: partial-initialization reliance — lifecycle pulls `flow` via package attribute while package is mid-init; safe only because `flow` is NOT in `__init__` re-exports
- Why dangerous: adding `flow` to re-exports or importing lifecycle earlier flips latent cycle into hard ImportError inside the deploy engine
- Evidence: lifecycle.py:29 vs engine/__init__.py:17-19 chain verified
- Scenario: refactor of engine exports during a deploy-path hotfix breaks `deploy` CLI at import time
- Impact: deploy path import failure = full deploy outage until revert
- Minimal decoupling: lifecycle imports `engine.flow` directly (module path), not via package attr; add comment pin
- Code churn: S · Regression risk: MED · Phase: Pre-launch candidate (1-line hardening)

## DEP-0012 · shared/s3_client upward leak into config (leaf-layer violation)
- Severity: LO · Category: circular / layering · Confidence: HIGH
- Files: `core/internal/shared/s3_client.py:34` → `from core.internal.config import platform_config`
- Coupling mechanism: the ONLY non-shared import inside shared/ (verified across 43 files); config itself is a leaf so no cycle today
- Why dangerous: breaks "shared is a leaf" invariant; s3_client becomes unusable where config unavailable; invites future reverse edge
- Evidence: grep of all `^from core.internal` in shared/ — 56 intra-shared + this one
- Scenario: someone makes config depend on a shared helper → first real cycle in the hub package
- Impact: architectural erosion of the only clean layer boundary
- Minimal decoupling: inject defaults into s3_client instead of importing platform_config
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0013 · lazy-import workaround cluster (deferred-failure pattern)
- Severity: MED · Category: circular · Confidence: MED (+HYPOTHESIS per-site)
- Files: `scaffold/project_scaffolder.py:380,544,803-892`, `project_remover.py:245`, `context_initializer.py:390`, `practices/check_project/fixer.py:93`, `healthcheck/platform_export_metrics.py:169-246`, `bootstrap/converge/projects.py:297,369`
- Dependency chain: function-body imports of modules imported top-level elsewhere — masks would-be cycles
- Coupling mechanism: deferred imports; some targets reachable ONLY lazily (`github_ops`) = untested import paths
- Why dangerous: failures surface at runtime deep in flows, not at import/CI time; each lazy site is an untracked hidden edge
- Evidence: listed lines; github_ops only-reachable-lazily noted by scanner
- Scenario: rename of a lazily-imported function passes CI, fails during new-project scaffold on node
- Impact: scaffold/reconcile flows degrade late and far from cause
- Minimal decoupling: inventory lazy sites; for each decide: fix cycle properly or promote to top-level import; add grep audit gate
- Code churn: M · Regression risk: MED · Phase: Post-launch

## DEP-0014 · practices.check_project masked by same-name package (dead file)
- Severity: LO · Category: circular / layout trap · Confidence: MED
- Files: `practices/check_project.py` coexists with `practices/check_project/` package
- Dependency chain: `check_project.py:21` imports FROM the package that shadows it — file unreachable
- Coupling mechanism: Python package-over-module shadowing
- Why dangerous: dead code next to the most actively developed subsystem; edits to the .py silently do nothing
- Evidence: :21 self-shadowing import; sync_practices.py:92 / fixer.py:93 lazy-import confusion nearby
- Scenario: agent patches check_project.py for K1 fix → zero effect, false confidence
- Impact: wasted triage time; misleading grep hits
- Minimal decoupling: delete dead file (verify no references), keep package
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (delete-only)

## DEP-0015 · ungated upward edges can silently become cycles (healthcheck→bootstrap)
- Severity: MED · Category: circular / layering · Confidence: HIGH
- Files: `healthcheck/tor_proxy_check.py:37` → `bootstrap.firewall.PRIVOXY_PORT`
- Dependency chain: healthcheck (should be leaf-ish consumer) reaches into bootstrap internals for one constant
- Coupling mechanism: ungated direction — cross_layer_imports gate covers only bootstrap→deploy
- Why dangerous: bootstrap is the largest subsystem (34+ files, state machine); its firewall module evolving freely now has an external pinned consumer
- Evidence: tor_proxy_check.py:37; pairwise scan found no other violations among 9 subsystems (positive signal overall)
- Scenario: firewall module refactor moves PRIVOXY_PORT → healthcheck import error surfaces only on node runs
- Impact: monitoring/healthcheck degradation post-refactor
- Minimal decoupling: move port constant to shared (platform_ports SoT exists!); extend gate to this edge
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (constant relocation + gate line)

Positive finding (context, not a DEP): no bidirectional pairs among {deploy, bootstrap, check_suite, practices, monitoring, scaffold, verify, static, healthcheck}; every cross-package edge one-way. Layering discipline is materially better than typical for this code size.
