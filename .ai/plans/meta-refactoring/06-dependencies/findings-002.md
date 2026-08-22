# Findings 002 · Dependency hubs (MED/LO) · sources: fan-in scan

## DEP-0005 · `deploy/orchestrator.py` — internal mega-hub with lazy back-edges
- Severity: HIGH · Category: dependency-hub · Confidence: MED
- Files: `core/internal/deploy/orchestrator.py:52-56,929,1042`
- Dependency chain: orchestrator → {audit, channels, deploy_engine, healthcheck_poller, hooks}; lazy: receive_flow, payload_deliverer; consumed by CLI + bootstrap context deploy + reconciler
- Coupling mechanism: god-orchestrator importing 5+ siblings at top plus 2 lazily; three external consumers share its API surface
- Why dangerous: changing DeliveryChannel/DeployHistory signatures breaks CLI, node context-deploy AND reconciler simultaneously; lazy imports hide cycle-prone cluster
- Evidence: orchestrator.py:929 `from ...receive_flow import ReceiveFlow`, :1042 payload_deliverer
- Scenario: payload contract tweak → three entrypoints break at once during launch week hotfix
- Impact: deploy path blast radius ×3
- Minimal decoupling: interface segregation — expose narrow per-consumer facades; no logic move
- Code churn: M · Regression risk: HIGH · Phase: **Post-launch**

## DEP-0006 · documented shared↔test_runner import cycle
- Severity: MED · Category: dependency-hub / circular · Confidence: HIGH
- Files: `core/internal/shared/test_journal.py:22` (comment), `core/internal/test_runner.py`
- Coupling mechanism: test_runner imports shared.exceptions; shared.test_journal documents the reverse edge as known cycle
- Why dangerous: cycle rooted in the most-imported package; refactor = import-order roulette
- Evidence: test_journal.py:22 comment names the cycle explicitly
- Scenario: moving test_journal into test_runner's package or vice versa reactivates ImportError
- Impact: blocks clean extraction of test_runner (a frequent change target during launch triage)
- Minimal decoupling: extract shared error type used by both into leaf module
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0007 · deploy → practices coupling in K3 verify chain (ungated upward edge)
- Severity: MED · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/deploy/verify_contracts.py:71-72`
- Dependency chain: on-VPS contract verification → practices lock/manifest SoT
- Coupling mechanism: direct import of `practices.generators.PracticesLock, read_lock` + `practices.manifest.load_manifest`
- Why dangerous: practices evolution (lock schema change) directly breaks deploy verification; edge is NOT covered by the cross-layer gate (only bootstrap→deploy is)
- Evidence: verify_contracts.py:71-72 imports
- Scenario: practices.lock v2 field rename → every deploy fails K3 verification
- Impact: deploy-blocking; practices is an active development area
- Minimal decoupling: versioned lock reader with explicit schema version + tolerant read; add gate for this edge direction
- Code churn: S · Regression risk: MED · Phase: Pre-launch guard (gate) / post-launch (versioned reader)

## DEP-0008 · scaffold ↔ bootstrap mutual reach (converge pulls operator-side generators)
- Severity: MED · Category: dependency-hub / circular · Confidence: MED
- Files: `core/internal/bootstrap/converge/projects.py:297,369`; rejected-direction proof: `core/modules/postgres/hooks/on_project_deploy.py:35`
- Dependency chain: node converge → scaffold.gen_env_platform / gen_project_platform_md (operator-side codegen) while scaffold elsewhere reaches into bootstrap
- Coupling mechanism: lazy imports inside reconcile; a postgres hook TRAP explicitly REJECTS the same import the converge path still performs
- Why dangerous: team's own TRAP shows this direction is considered wrong, yet converge keeps it; scaffold refactor breaks node reconciliation
- Evidence: projects.py:297 vs on_project_deploy.py:35 TRAP[DECISION]
- Scenario: gen_env_platform signature change → `make deploy-context` / converge degrades on node
- Impact: node reconcile path; medium blast radius
- Minimal decoupling: move the two generator functions to a leaf module both can import
- Code churn: S–M · Regression risk: MED · Phase: Post-launch

## DEP-0009 · manifest generator duplicates timeouts SoT (dual-SoT drift)
- Severity: LO · Category: dependency-hub · Confidence: HIGH
- Files: `core/internal/scripts/generate_entrypoint_manifest.py:112`
- Coupling mechanism: local timeout constants instead of importing shared.timeouts; TRAP documents intent + Rev path
- Why dangerous: two sources of the same timeout values can drift; manifest gates would not catch value drift
- Evidence: TRAP[DECISION] 2026-08-14 at :112
- Scenario: SoT timeout updated, manifest copy stays stale → gate expectations diverge silently
- Impact: low today (values equal), drift trap for launch-week edits
- Minimal decoupling: none pre-launch (TRAP says keep); post-launch restore SoT import per Rev
- Code churn: S · Regression risk: LOW · Phase: Post-launch (Rev condition documented)
