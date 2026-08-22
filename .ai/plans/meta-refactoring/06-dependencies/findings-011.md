# Findings 011 · Gate-pinned change amplification (tests/gates → prod lockstep)

## DEP-0054 · Add-one-module = 5–8 coordinated edits incl. ≥4 gate files with hardcoded lists
- Severity: HIGH · Category: gate-amplification · Confidence: HIGH
- Files: `test_gate_healthcheck_contract.py:27-34` (`HEALTHCHECK_FILES` dict, 8 entries), `test_gate_test_infra_consistency.py:39` (`_AC6E_WHITELIST_FILES`), `test_gate_make_contract.py:41` (`_DOCKER_MODULES`), `test_gate_compose_restart_policies.py:42` + `test_gate_structural_consistency.py:~227` (restart carve-outs), `test_gate_local_stack.py:35-36` (floors EXPECTED_NETWORKS=6/VOLUMES=10); prod side: platform-infra.yaml COMPOSE_PROFILES, platform-env profiles↔dir 1:1 (platform_env_schema:280), root compose include, module.yaml, entrypoint-manifest
- Coupling mechanism: hardcoded module lists in gates + value-pinned manifests; ~25 dynamic glob gates re-arm on every new module
- Why dangerous: registering a module — the highest-frequency launch-week operation — silently leaves a new healthcheck.sh UNCHECKED if one list is missed (gap, not failure)
- Evidence: verified line numbers above; VERIFIED vs ESTIMATED split documented in agent report
- Scenario: emergency module addition before launch → one forgotten whitelist → module ships unhealthchecked
- Impact: silent coverage gap exactly where visibility matters
- Minimal decoupling: derive whitelists from directory glob with explicit-exemption lists (invert default); pre-launch at minimum add checklist comment to each list
- Code churn: M (post) / S (comments now) · Regression risk: LOW · Phase: Post-launch refactor, **pre-launch awareness**

## DEP-0055 · SoT env_defaults value-pins: one default change = ≥5 gate files + 3 regenerated outputs
- Severity: HIGH · Category: gate-amplification · Confidence: HIGH
- Files: `core/platform-infra.yaml` env_defaults ↔ gates `domain_parity` (`"ai-platform.local"` literal), `env_example_drift` (byte-identical .env.example), `status_page_port_parity` (8080), `profiles_parity` (string equality), `port_parity`, `image_tag_form`, `platform_env_schema`; outputs platform-env.yaml + smoke_env_generated.py + env_defaults_generated.py
- Coupling mechanism: literal value assertions ×2 for domain; byte-parity for generated example
- Why dangerous: the anti-drift mechanism is itself the amplification vector — routine config change becomes 5+ file coordinated edit under time pressure
- Evidence: per-gate assertions listed
- Scenario: domain/port change during infra migration → partial gate updates → red CI noise masking real regressions
- Impact: velocity loss + real-signal dilution during triage
- Minimal decoupling: consolidate multi-gate pins into single parity table test; freeze values until launch
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0056 · Generator change fans out to 6–7 gates + 6 committed outputs
- Severity: HIGH · Category: gate-amplification · Confidence: HIGH
- Files: G2 `generate_platform_env.py` (3 outputs), G3 `generate_entrypoint_manifest.yaml`, G1 secrets-manifest; gates `yaml_deterministic_output`, `atomic_generation_no_partial_writes` (runs REAL generator mains with fault injection), `manifests_up_to_date` (git-diff), `manifest_signature_parity`, `generated_marker_orphan`, `_CANONICAL_GENERATORS` list in `test_gate_no_shell_manifest_generators.py:41-47`
- Coupling mechanism: Manifest Generation Contract (invariant 11) makes generators highest-leverage edit point; refactoring GENERATOR (not output) fires import-level/static gates
- Why dangerous: innocent generator refactor during stabilization = 7-gate red wall; adding a generator requires editing a hardcoded list
- Evidence: gate-to-generator mapping above
- Scenario: post-launch cleanup touches G2 → CI red cascade misread as regression
- Impact: maintenance friction, deliberate trade-off of invariant 11
- Minimal decoupling: none structural (contract is sound); document amplification map near generators
- Code churn: S (docs) · Regression risk: LOW · Phase: Awareness only

## DEP-0057 · entrypoint-manifest.yaml pinned by 22 gate files incl. negative pins
- Severity: MED · Category: gate-amplification · Confidence: HIGH
- Files: `test_gate_deploy_channel` (workflow verbs ⊆ {ping,receive,verify}), `ci_coverage:813` (asserts `test-node` ABSENT from allowed_verbs — negative pin), `make_contract:580-584`, `manifest_integrity`, `check_suite_consistency`, `no_unregistered_entrypoint`
- Coupling mechanism: value+negative pins over central registry
- Why dangerous: negative pins break the moment verb set legitimately grows; directional constraints conflict across gates
- Evidence: cited lines
- Scenario: adding a needed CI verb → 6-gate negotiation mid-launch
- Impact: process friction
- Minimal decoupling: convert negative pins to policy tests reading intent metadata rather than enumerations
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0058 · Double/triple pinning of shared SoT modules (ports/timeouts/ssh_opts/domain/profiles)
- Severity: MED · Category: gate-amplification · Confidence: HIGH
- Files: ports asserted in 4 gates (port_parity, test_infra_consistency, status_page_port_parity, litellm_health_url_parity); timeouts pinned by 999-line timeout_literals (~15 canonical files, exact-import asserts :573,598,577 incl. absence assertion) + ssh_opts_sole_path + retry-policy gates; COMPOSE_PROFILES quadruple-pinned
- Coupling mechanism: parity re-implementations of the same SoT comparison in triplicate
- Why dangerous: single numeric change = multi-gate coordination each with independent expectations; sole-path gates create illusion of single enforcement while parity copies drift
- Evidence: counts above
- Scenario: covered by DEP-0055 scenario
- Impact: same as DEP-0055
- Minimal decoupling: shared fixture/table consumed by all parity gates
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0059 · "Exactly N" cardinality pins block organic growth
- Severity: MED · Category: gate-amplification · Confidence: HIGH
- Files: `test_gate_deploy_paths.py:130` (`len(canonical)==6`, comment demands Architect approval), `test_gate_workflow_consistency.py:218` (`==8` workflows), `makefile_targets:305-310` (exactly 6 .mk includes), `no_shell_manifest_generators:41-47` (5 generators)
- Coupling mechanism: structural lists pinned as cardinality equality — fail on intermediate states during legit growth
- Why dangerous: every growth event edits the gate itself; "exactly" form also breaks mid-refactor states
- Evidence: cited asserts
- Scenario: adding 7th deploy path or 9th workflow during scaling prep → gate edit ceremony under deadline
- Impact: deliberate friction (some intentional per comments) but misclassified as structural
- Minimal decoupling: ≥ floors instead of ==; keep == only where truly closed sets
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## Net amplification metric (for cascade answer)
Routine launch-week change types and their measured cost:
- new module → ≥5–8 file edits, ~25 gates re-fire
- env-default change → ≥5 gate files + 3 regen outputs
- new verb → 6–8 layers (4 auto via generator)
- new detector → 3 stores (1 silent-failure direction)
- top-3 amplification hubs: entrypoint-manifest.yaml (22 gates), platform-infra.yaml env_defaults (13), generator trio (6–7 each)
