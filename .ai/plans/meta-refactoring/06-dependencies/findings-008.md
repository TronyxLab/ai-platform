# Findings 008 · Initialization-order dependencies

## DEP-0037 · secrets_manager dual-mode import fallback masks real import bugs
- Severity: MED · Category: init-order · Confidence: HIGH
- Files: `core/internal/bootstrap/lifecycle/secrets_manager.py:106-173` (try canonical imports → `except ModuleNotFoundError:` → importlib fallback + `cast` re-exports); TRAP[BUG] 2026-08-12 + 2026-07-31 ×2 at same site
- Coupling mechanism: broad except catches ALL import failures of the try-block, not just missing-module; silently switches invocation mode
- Why dangerous: a typo/regression in any of 9 canonical imports activates fallback path — behavior differs between `python3 -m` and standalone; bugs reproduce under one mode only; 3 incident TRAPs already at this exact code
- Evidence: except scope + importlib fallback + re-export block
- Scenario: launch-week edit breaks an import → φ4 runs degraded fallback on node, passes locally
- Impact: secrets provisioning divergence between environments
- Minimal decoupling: narrow the except to the single expected first-import; assert both modes in smoke test
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate**

## DEP-0038 · ≥5 independent PLATFORM_ROOT re-derivations with different parent depths
- Severity: MED · Category: init-order · Confidence: HIGH (HYPOTHESIS: no gate enforces single local-root resolver)
- Files: `config/platform_config.py:91` (parents[3]), `shared/app_config.py:60` (parents[3]), `hermes_images.py:43`, `scripts/generate_platform_env.py:47` (4×parent), `secrets/decrypt_secrets.py:68` (5×parent os.path.join), `validate_orchestrator.py:70`
- Coupling mechanism: each module re-derives repo root with its own idiom/depth; deploy_paths covers remote paths only, not local root
- Why dangerous: moving any file one level breaks only its resolver — silent drift masked by pytest rootdir in sys.path
- Evidence: depth variants listed; 8 sys.path-related TRAP[BUG]s since 2026-07-31 evidence recurring fragility class
- Scenario: scripts reorganization during post-launch cleanup breaks one generator's standalone mode
- Impact: tooling breakage discovered late
- Minimal decoupling: single `shared/local_root.py` resolver adopted incrementally
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0039 · `--run-phase` DAG checks state.json only — state can lie about artifacts
- Severity: MED · Category: init-order · Confidence: HIGH (architecture-verified)
- Files: `bootstrap/lifecycle/cli.py:366-383`, `state_machine.py:693-700`, `phases/preconditions.py` (checks scripts, not artifacts)
- Coupling mechanism: phase dependencies enforced via STATUS in state.json, not existence of produced artifacts (cert dirs, secrets.env, overlay dirs)
- Why dangerous: after manual artifact removal (/etc/letsencrypt wiped, secrets.env deleted), rerun of dependent phase reads absent/stale artifacts while DAG says "done"
- Evidence: precondition functions target scripts only; handoff table rows 2/5/9 implicit
- Scenario: DR restore or cleanup during launch prep → φ8 deploys with stale certs
- Impact: deploy with invalid inputs, hard to diagnose
- Minimal decoupling: add artifact-existence preconditions for the 3 critical handoffs (secrets.env, letsencrypt live dir, overlay dir) — cheap checks, big safety
- Code churn: S–M · Regression risk: LOW · Phase: **Pre-launch candidate**

## DEP-0040 · φ4→φ6/φ8 secrets handoff via os.environ sourcing is best-effort
- Severity: MED · Category: init-order · Confidence: HIGH
- Files: `bootstrap/lifecycle/helpers/secrets.py:99,112-119` (source-if-absent, warning-only :123), `htpasswd.py:186`; FATAL-on-missing added by TRAP[BUG] 2026-07-23 P0 but sourcing remains lenient
- Coupling mechanism: env-var channel between phases/processes; setdefault semantics skip refresh when key exists
- Why dangerous: stale secret from earlier process wins over fresh decrypt if key pre-exists; subprocess without inherited env loses secrets silently
- Evidence: source-only-if-absent logic
- Scenario: rotated secret not picked up by φ8 because placeholder existed in env from CLI injection order
- Impact: modules deployed with wrong credentials
- Minimal decoupling: explicit freshness check (compare mtime/hash of secrets.env vs loaded values) at phase entry
- Code churn: S · Regression risk: MED · Phase: Post-launch (verify behavior first)

## DEP-0041 · 65+ sys.path bootstrap sites, 3 idioms, 8 recurring TRAP[BUG] incidents
- Severity: LO · Category: init-order · Confidence: HIGH (counts verified)
- Files: pattern instances across core/internal (decrypt_secrets.py:71-72 canonical form), loadtest/scenarios ×6, backup-cron scripts ×2, monitoring ×7
- Coupling mechanism: script-vs-module dual invocation needs root insert; three depth idioms (`parents[n]`, nested `.parent` chains, `os.path.join`)
- Why dangerous: known-biting class (TRAP history 07-31…08-16) but each instance isolated and now idempotent+str()-hardened
- Evidence: incident dates listed in agent report
- Scenario: covered by DEP-0038 consolidation
- Impact: low per-site
- Minimal decoupling: same as DEP-0038 (single resolver), plus prefer `-m` invocation everywhere
- Code churn: M · Regression risk: LOW · Phase: Post-launch

## DEP-0042 · lone remaining cwd dependency in validate_module_yaml
- Severity: LO · Category: init-order · Confidence: MED (+HYPOTHESIS on usage intent)
- Files: `core/internal/scripts/validate_module_yaml.py:56` (`Path.cwd()`), sys.path bootstrap at :44-45
- Coupling mechanism: cwd feeds resolution contrary to declared no-cwd-heuristic canon (DevPlan 116 B5 T8 removed cwd-эвристика)
- Why dangerous: inconsistent with canon; masked because make runs from repo root
- Evidence: single true Path.cwd() site in core/internal
- Scenario: direct invocation from subdir misresolves
- Impact: minor tooling
- Minimal decoupling: replace with script-relative root
- Code churn: S · Regression risk: LOW · Phase: Post-launch

Positive finding: phase DAG itself is explicit (state_machine.py:226-246, static PHASE_DISPATCH); no phase reads env before the phase that sets it (verified `_inject_cli_env` ordering at cli.py:419).
