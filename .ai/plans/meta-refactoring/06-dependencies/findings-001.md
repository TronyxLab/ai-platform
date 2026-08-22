# Findings 001 · Dependency hubs (CRITICAL/HIGH) · sources: fan-in scan

## DEP-0001 · `core.internal.shared` — universal hub imported by ~half of production code
- Severity: CRITICAL · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/shared/*.py` (~50 modules); worst consumers: `bootstrap/deploy/context_deployer.py:75-113`, `deploy/orchestrator_cli.py:79-89`, `modules/postgres/hooks/on_project_deploy.py:61`
- Dependency chain: ALL subsystems (bootstrap, deploy, check_suite, practices, monitoring, scaffold, verify, static, agent_check, secrets, llm, loadtest…) → shared
- Coupling mechanism: direct imports of constants/exceptions/path helpers; ~216 prod files + ~95 test files
- Why dangerous: any signature/semantic change in shared ripples through every subsystem incl. the deploy path; shared cannot evolve independently
- Evidence: grep `from core.internal.shared` = 216 prod files; fan-in table in agent stats (top module by wide margin)
- Scenario: refactor of `shared.deploy_paths` or `shared.exceptions` semantics → compile-safe edits break behavior in dozens of CLIs at once
- Impact: launch-blocking regressions hard to localize; every hotfix touches the most-loaded layer
- Minimal decoupling: split shared into `leaf primitives` (timeouts/paths/exceptions) vs domain helpers; add layering gate forbidding new upward deps; freeze signatures until launch
- Code churn: L · Regression risk: MED · Phase: **Post-launch** (pre-launch: freeze + gate only)

## DEP-0002 · `shared/timeouts.py` — gate-enforced constant hub, ~89 importing files
- Severity: CRITICAL · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/shared/timeouts.py`; importers incl. `bootstrap/lifecycle/phases/docker.py:57`, `deploy/engine/flow.py:32`, `core_deliverer.py:80`; enforcer `tests/gates/test_gate_timeout_literals.py:573,598,814`
- Coupling mechanism: named-constant imports across 12+ subsystems AND gate tests assert the exact import lines in canonical files
- Why dangerous: double coupling — code fan-in (~89 files) + parity gate pins literals; rename/type-change = 89-file cascade turning gate RED
- Evidence: timeouts.py:11 documents "константы импортируются напрямую"; gate asserts canonical import sites
- Scenario: someone changes `DEPLOY_DEFAULT_TIMEOUT_S` type to enum/tuple → mass ImportError + gate failure simultaneously
- Impact: deploy-path timeout semantics locked platform-wide; emergency tuning requires broad edit
- Minimal decoupling: pre-launch policy — additive-only changes (new constants OK, never rename/retype); post-launch: accessor functions with defaults
- Code churn: S (policy/gate tweak) · Regression risk: LOW if frozen · Phase: **Pre-launch freeze**

## DEP-0003 · `shared/exceptions.py` — error-contract hub with proven dual-class drift hazard
- Severity: CRITICAL · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/shared/exceptions.py` (~99 importers); duplicate-class proof: `secrets/decrypt_secrets.py:60`, re-export `shared/node_yaml/__init__.py:43`
- Coupling mechanism: exception classes used in `except` clauses across all subsystems; multiple import paths can yield distinct class objects
- Why dangerous: in-code comment proves "ДВА разных класса PlatformFatalError" already fired — an `except PlatformFatalError` silently misses raises from the other import path
- Evidence: decrypt_secrets.py:60 comment documenting the duplicate-class incident
- Scenario: new module imports exceptions via node_yaml re-export while callers catch via shared.exceptions → swallowed/mishandled fatal errors during deploy
- Impact: error-handling correctness of the whole platform depends on import-path discipline nobody enforces
- Minimal decoupling: single canonical import path enforced by lint rule (`forbidden-parent-classes` style check for re-exported exceptions); kill redundant re-exports
- Code churn: S–M · Regression risk: LOW · Phase: **Pre-launch candidate** (small, closes a proven incident class)

## DEP-0004 · bootstrap → deploy upward dependency on the canonical deploy path
- Severity: HIGH · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/bootstrap/deploy/context_deployer.py:73-74` (`deploy.channels.LocalChannel`, `deploy.orchestrator.DeployOrchestrator`); also `reconciler_projects.py:40-41`
- Dependency chain: node-side φ8/φ12 bootstrap step → operator-side deploy CLI internals
- Coupling mechanism: top-level imports of DeployOrchestrator/DeliveryChannel APIs inside bootstrap phase runner
- Why dangerous: sanctioned direction (gated by cross_layer_imports) but semantically inverted: node reconcile depends on operator CLI's internal API; channel/orchestrator refactor cascades into bootstrap phases
- Evidence: context_deployer.py:73-74; core/AGENTS.md sanctions direction only
- Scenario: DeliveryChannel interface evolution (new required method) breaks context deploy on nodes mid-bootstrap
- Impact: deploy path — the single most launch-critical flow
- Minimal decoupling: narrow protocol (struct) between context_deployer and deploy.channels; don't touch orchestrator
- Code churn: M · Regression risk: HIGH (touches deploy) · Phase: **Post-launch**
