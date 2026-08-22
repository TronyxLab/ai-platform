# Findings 010 · Unstable abstractions / interfaces that cannot evolve independently

## DEP-0048 · `OrchestratorDeployResult` — 11-field god-DTO across 3 subsystems + CI wire contract
- Severity: CRITICAL · Category: unstable-abstraction · Confidence: HIGH
- Files: `deploy/orchestrator.py:113` (definition), `:136-149 to_dict`; consumers: `orchestrator_cli.py:552,704,733,756,796`, `bootstrap/deploy/context_deployer.py:74,326` (imports the class!), `reconciler_projects.py:41,294-295`, `receive_flow.py:472`
- Dependency chain: deploy CLI ↔ bootstrap φ8 ↔ reconciler ↔ CI forced-command JSON (DevPlan 116 B1 AC2 wire contract)
- Coupling mechanism: single nominal dataclass doubles as internal result AND serialized wire schema; disjoint field subsets per consumer; `to_dict()` silently drops stdout/stderr (written by callers, never read post-wire — dead weight inviting confusion)
- Why dangerous: adding/renaming a field = constructor + to_dict + CI receive JSON + 3 subsystems' call sites in one commit; import of the class welds bootstrap φ8 to orchestrator internals
- Evidence: verified field/caller lists above
- Scenario: launch-week addition of e.g. `image_digest` for audit → missed context_deployer/receive_flow update breaks node deploys
- Impact: deploy pipeline outage risk on every interface evolution
- Minimal decoupling: split wire-DTO (serialized subset) from internal result; stop importing the class in context_deployer (accept dict/protocol)
- Code churn: M · Regression risk: HIGH (touches deploy) · Phase: **Post-launch** (pre-launch: additive-only fields + freeze)

## DEP-0049 · StatusResult vs ProjectStatus — duplicate status types held together only by parity test
- Severity: HIGH · Category: unstable-abstraction · Confidence: HIGH
- Files: `deploy/engine/results.py:54,67` (`StatusResult`) ↔ `deploy/orchestrator.py:156` (`ProjectStatus`); documented invariant "поля НЕ расходятся… тест set-сравнения"
- Coupling mechanism: two nominal classes modeling one concept, no shared base/Protocol; third copy exists in on-node JSON statuses
- Why dangerous: field addition to one silently invalidates other until hand-updated test; triple representation cannot evolve independently
- Evidence: results.py invariant comment
- Scenario: status enrichment (new health metric) during triage desyncs engine vs CLI views
- Impact: inconsistent operator visibility; low deploy-breakage but high confusion cost
- Minimal decoupling: shared keys Protocol or single source class with views
- Code churn: S–M · Regression risk: MED · Phase: Post-launch

## DEP-0050 · DeployOrchestrator public surface wider than its real audience
- Severity: HIGH · Category: unstable-abstraction · Confidence: HIGH
- Files: `orchestrator.py:252,645,711,781,817,900`; sole-caller evidence `orchestrator_cli.py:323,340,462,733,756,800`; multi-caller: `deploy` ×3 (CLI, context_deployer, reconciler)
- Coupling mechanism: 4 of 6 methods have exactly 1 caller (same dispatcher module); `deploy` has 3 callers × 7 params incl. stringly-typed `metadata.get("payload_backup_dir")` :340
- Why dangerous: private-API-as-public — signature churn looks safe (1 caller) yet these are exactly the verbs CI forced-command relies on; conversely `deploy` is a de-facto cross-subsystem contract with no protocol definition
- Evidence: caller counts verified by grep
- Scenario: "safe" rollback() signature tweak breaks CI receive dispatch discovered only on next release
- Impact: false-confidence refactors on critical path
- Minimal decoupling: document dispatcher-internal methods as non-public; define narrow Protocol for `deploy()` consumed by bootstrap/reconciler
- Code churn: S (docs/Protocol) · Regression risk: LOW if types-only · Phase: Pre-launch docs possible; code Post-launch

## DEP-0051 · StepState/BootstrapState — wide single-subsystem DTO doubling as state.json schema; field-removal already bit
- Severity: MED · Category: unstable-abstraction · Confidence: HIGH
- Files: `bootstrap/lifecycle/state_store.py:83,148`; 6 consumer files all inside bootstrap/lifecycle; TRAP[BUG] cli.py:932-939 ("unexpected keyword 'done'" after field removal)
- Coupling mechanism: dataclass kwarg evolution unguarded at call sites; BootstrapState serialized to on-node state.json (field rename = state migration)
- Why dangerous: incident already happened; next field change repeats it unless constructor call sites are centralized
- Evidence: TRAP text + consumer list
- Scenario: phase-tracking cleanup during launch prep → runtime TypeError mid-bootstrap
- Impact: bootstrap interruption on node
- Minimal decoupling: centralize StepState construction in one factory; version key in state.json before any future rename
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (version key + factory)

## DEP-0052 · module.yaml: central validator exists, but ≥5 ad-hoc yaml.safe_load readers
- Severity: MED · Category: unstable-abstraction · Confidence: HIGH
- Files: validator `scripts/validate_module_yaml.py` (D5 jsonschema); raw readers `generate_platform_env.py:162,166`, `generate_secrets_manifest.py:117`, `module_interface.py:127` (claims "единая точка чтения", scoped to hooks only), `env_requires.py`, `modules_healthcheck.py`, `module_discovery.py`, `orchestrator_metrics.py`
- Coupling mechanism: schema enforced only when validator runs; readers accept missing keys silently (partial parse)
- Why dangerous: adding required key → validator + 5-7 independent parsers + generators must change in lockstep or manifest-parity gate trips; readers give no early signal
- Evidence: reader file list verified
- Scenario: new module.yaml field for launch feature → one generator misses it → generated platform-env lacks value, surfaces as deploy env gap
- Impact: config generation drift
- Minimal decoupling: single load_module_yaml() helper (validated read) adopted by all readers incrementally
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0053 · node_yaml package: parser lib + CLI-by-string dual role, ~33+ consumers incl. module layer
- Severity: MED · Category: unstable-abstraction · Confidence: MED (consumer count partial-grep ~33; HYPOTHESIS ≥26 py + ~8 shell)
- Files: `shared/node_yaml/__init__.py:84,149-168` (7 mixins + PEP 562 CLI dispatch); consumers span reconciler, verify_sweep, converge, cert_orchestrator, scaffold, monitoring, lifecycle helpers, `core/modules/postgres/hooks/on_project_deploy.py:61`
- Coupling mechanism: NodeYaml aggregate type + `raw()` untyped-dict escape hatch used by validation/metrics consumers; node.schema.json exists but UNVERSIONED
- Why dangerous: widest fan-in after shared itself; raw() bypasses typed getters (which were already removed once — wave 118 B3 churn evidence); schema change ungated at read time
- Evidence: mixin structure + raw() sites helpers/validation.py:79, platform_export_metrics.py:336
- Scenario: node.yaml key rename for multi-node rollout → 30+ consumers updated by hand, raw() users compile-clean but behavior-broken
- Impact: largest single-type blast radius in platform
- Minimal decoupling: version the schema; deprecate raw() in favor of typed accessors; keep additive-only until launch
- Code churn: M–L · Regression risk: MED · Phase: Post-launch (pre-launch freeze)

Positive counter-example: `shared/env_facts.EnvironmentFacts` is a `@runtime_checkable Protocol` (env_facts.py:45-72) with structural DI across 5+ subsystems and zero nominal coupling — proof the team knows the right pattern; minor doc drift (AGENTS table lists 6 consumers, docstring claims 7).
