# Findings 01 — Module/package boundaries

> Provenance: run-c (2026-08-24 re-run, 10 fresh subagents). Restored after run-a sweep displaced the
> 2026-08-22 originals to `attic/` (this file + `findings-10-hotspots.md` were lost there and rewritten
> verbatim from session record).

## ARCH-0001 — Two packages named "deploy" with colliding orchestrator identities
- **Severity:** P2 · **Confidence:** 0.9 · **Churn:** M · **Phase:** post-launch (node runtime paths ship atomically via core-deliver)
- **Files:** `core/internal/deploy/orchestrator.py:186` (`DeployOrchestrator`, project delivery, 8.8k LOC pkg) · `core/internal/bootstrap/deploy/deploy_orchestrator.py:985` (`orchestrate()`, platform-module deploy, 8.3k LOC pkg, 18 modules)
- **Symbols:** `DeployOrchestrator.receive()`, `deploy_orchestrator.orchestrate()`; path `core.internal.bootstrap.deploy.deploy_orchestrator` contains "deploy" 3×
- **Evidence:** `.importlinter:79-85` manages only the edge (`deploy` never imports `bootstrap`); no contract addresses the name collision. `bootstrap/deploy/*` has zero non-test importers outside `bootstrap/`.
- **Scenario:** agent asked to change deploy rollback greps "deploy orchestrator", finds both, edits the wrong one; `forbidden-deploy-bootstrap` contract is ambiguous ("which deploy?").
- **Impact:** discovery cost, wrong-site edits on the most safety-critical subsystem.
- **Minimal fix:** rename `bootstrap/deploy/` → `bootstrap/modules_deploy/`; 10-line compat shim re-exporting for ~16 test files + shell facades; update `.importlinter` source_modules.

## ARCH-0002 — `shared/` leaf contract breached via `config`; importlinter enumerates only 3 of ~20 sibling domains
- **Severity:** P2 · **Confidence:** 0.95 (violation) / 0.7 (harm today) · **Churn:** S · **Phase:** pre-launch
- **Files:** `core/internal/shared/s3_client.py:34` (`from core.internal.config import platform_config`) · `.importlinter:106-115` (`forbidden-shared-domains`: only bootstrap, deploy, static) · `core/internal/config/platform_config.py:66` vs `shared/yaml_loader.py:93` — two parallel readers of `platform-infra.yaml#env_defaults` with divergent None-semantics
- **Evidence:** declared canon: shared is the leaf (`shared/AGENTS.md` инвариант 5). The edge passes CI because `config` is missing from the forbidden list. A future `config`→`yaml_loader` import instantly creates a `shared↔config` cycle under a green gate.
- **Impact:** erosion of the only formally clean leaf; silent drift between the two SoT readers.
- **Minimal fix:** make `get_s3_client()` pure (callers pass resolved endpoint/keys); enumerate all `core.internal.*` children in `forbidden-shared-domains`.

## ARCH-0003 — Flatland at `core/internal/` root: five domain modules without a home (3.4k LOC)
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** M · **Phase:** pre-launch (pure moves) / post-launch (reconciler)
- **Files:** `template_engine.py` (852 LOC; imported by scaffold + monitoring×3 + bootstrap sudoers×2 + validators), `test_runner.py` (909 LOC; subprocess-called by `check_suite/single.py:97`), `dev_hosts.py` (643; imports nginx module internals — see ARCH-0007), `reconciler_projects.py` (562; invoked **by filesystem path** from `bootstrap/converge.py:316`), `provisioner.py` (407)
- **Evidence:** `template_engine` satisfies the ≥2-consumers rule for `shared/` yet lives outside the inventory claiming to own reusable logic; reconciler split across root file AND `bootstrap/converge/projects.py`.
- **Impact:** hub growth at the wrong level; path-based dispatch makes future moves risky; invisible to package-layering docs.
- **Minimal fix:** `template_engine` → `shared/`; `test_runner` → `check_suite/`; `reconciler_projects` → `bootstrap/converge/projects_reconciler.py` (fix path dispatch in converge.py).

## ARCH-0004 — Healthcheck domain has four owners; readiness runner sits in bootstrap, not healthcheck/
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S · **Phase:** post-launch
- **Files:** `healthcheck/` (watchdog, modules_healthcheck, metrics/) · `deploy/healthcheck_poller.py:212` (`HealthcheckPoller`) · `shared/docker_compose.py:520` (`healthcheck_poll`, canonical criterion) · `bootstrap/deploy/healthcheck_runner.py:54` (`wait_for_readiness`, own retry constants 15×2s beside canonical `HEALTHCHECK_POLL_*`)
- **Evidence:** both `modules_healthcheck` and bootstrap's `healthcheck_runner` wrap the identical `invoke_module_interface(module,"healthcheck")` primitive from two different packages.
- **Impact:** unclear ownership for readiness-policy changes; two retry vocabularies for one interface.
- **Minimal fix:** relocate `healthcheck_runner.py` → `healthcheck/module_runner.py` (direction stays legal); unify retry constants via `shared/timeouts`.

## ARCH-0005 — Dead facade documentation: `deploy/__init__.py` promises exports it doesn't have
- **Severity:** P3 · **Confidence:** 0.95 · **Churn:** S · **Phase:** pre-launch
- **Files:** `core/internal/deploy/__init__.py` — docstring lists `DeployOrchestrator, DeliveryChannel, …`; file contains zero import statements (verified). Repo-wide style: 921 deep-imports vs 77 hub-style.
- **Scenario:** contributor writes the documented `from core.internal.deploy import DeployOrchestrator` → immediate ImportError; agents trust docstrings as contract.
- **Impact:** misleading public-API declaration.
- **Minimal fix:** correct the docstring ("deep-import convention") or add the re-export block.

See also ARCH-0006 (PRIVOXY_PORT upward reach-in — boundary violation tracked in findings-02).
