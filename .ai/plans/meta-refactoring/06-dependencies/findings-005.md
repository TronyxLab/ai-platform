# Findings 005 · Hidden dependencies (MED/LO)

## DEP-0020 · `core.internal.*` module names as string CLI contracts across 10+ call sites
- Severity: MED · Category: hidden-dep · Confidence: HIGH
- Files: `validate_orchestrator.py:280,353,429,458` (node_yaml), `reboot_policy.py:272`, `cert_expiry_check.py:277`, `watchdog.py:576` (notifications), `lifecycle/cli.py:557`, `deploy_orchestrator.py:599` (orchestrator_cli), `bootstrap_resolver.py:111`, `orchestrator_cli.py:382`, `agent_check/__init__.py:657`
- Dependency chain: python → subprocess `python3 -m core.internal.<X>` with dotted path literals
- Coupling mechanism: module rename = runtime stacktrace-poor failure on node; contract duplicated in code + docstrings + entrypoint-manifest `delegates_to` (3 drifting copies)
- Why dangerous: no SoT constant; failures visible but far from cause (exit≠0 on node)
- Evidence: listed sites
- Scenario: package reshuffle of node_yaml/notifications breaks watchdog + cert expiry checks on nodes simultaneously
- Impact: multi-consumer runtime breakage on refactor
- Minimal decoupling: constants module for dispatched module paths; assert importability in a smoke gate
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0021 · `PLATFORM_POSTGRES_DSN` exists only in templates/tests — zero Python constant
- Severity: MED · Category: hidden-dep · Confidence: MED
- Files: `templates/template-backend/{.env.example,README.md,snippets/db.py}`, `tests/unit/test_gen_env_platform.py:90,140`, `test_scaffold_env_platform.py:205`, generator sibling pattern `modules/postgres/hooks/on_project_deploy.py:366-368`
- Dependency chain: platform-infra.yaml provides-facade → generator materializes name → projects consume
- Coupling mechanism: contract name materialized by convention, never referenced in core/internal Python
- Why dangerous: rename in infra manifest → tests fail loudly (good) but shipped templates + project READMEs drift silently; nothing to grep as canonical constant
- Evidence: grep shows 6 prod hits, all templates/docs
- Scenario: facade rename (pgbouncer port change) leaves generated project docs pointing at dead DSN
- Impact: developer-facing breakage, low production risk
- Minimal decoupling: emit the constant into env_defaults/generated files (already GENERATED pipeline) and reference from tests
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0022 · pytest marker strings as dispatch keys (same silent-pass class as DEP-0016)
- Severity: LO · Category: hidden-dep · Confidence: MED
- Files: `core/check-suite.yaml:151,164,173,186,202,229,237` (`--marker contract|ai-instructions|static_audit|predeploy|smoke|component|integration`)
- Coupling mechanism: marker names in yaml must match `@pytest.mark.<x>` in test files; rename → 0 collected
- Why dangerous: partial guard only (pytest exit 5 for no-tests mitigates); suite could still run a subset unnoticed if markers split
- Evidence: listed cmd entries
- Scenario: marker consolidation during launch triage halves a suite silently
- Impact: verification coverage erosion
- Minimal decoupling: check-suite runner asserts expected minimum counts per marker (baseline snapshot)
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0023 · entrypoint-manifest filename/name scattered ~40 files (architecture-as-designed)
- Severity: LO · Category: hidden-dep · Confidence: MED
- Files: `manifest.mk:36-39`, generate_*.py ×4, `verb_register.py:47`, `dead_code.py:259`, workflows, `tests/contracts/test_make_target_contracts.py:36`
- Coupling mechanism: relative path + registry name literals; cwd/root-relative assumptions
- Why dangerous: parity gates (G3/G4) mitigate semantic drift; residual risk is hardcoded relative paths breaking when run outside repo root
- Evidence: listed references
- Scenario: CI runs consumer from subdir → file-not-found instead of validation result
- Impact: tooling friction, not launch risk
- Minimal decoupling: shared deploy_paths-style resolver already exists — route consumers through it
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0024 · monkey-patching absent by design (positive control)
- Severity: LO · Category: hidden-dep (negative finding) · Confidence: HIGH
- Files: 13 `setattr(` matches are ALL comments/TRAPs documenting DI-over-mocks canon (DevPlan 163 W-H): orchestrator_cli.py:606, orchestrator.py:218,489, context_deployer.py:489 etc.
- Evidence: zero actual production patching found
- Why notable: confirms constructor/namespace DI discipline; no action needed. Recorded to prevent future auditors re-flagging.
