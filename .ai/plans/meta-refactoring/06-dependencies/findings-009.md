# Findings 009 · Shell↔Python↔Make coupling

## DEP-0043 · Canonical verb name hardcoded in 6–8 layers across 4 languages
- Severity: HIGH · Category: shell-make-coupling · Confidence: HIGH
- Files (worked trace for `converge`): `makefiles/bootstrap.mk`, `core/entrypoints/converge.sh:21` (`--verb converge`), `core/internal/bootstrap/remote_dispatch.py` (argparse choices + dispatch), `core/entrypoint-manifest.yaml` allowed_verbs, `AGENTS.md` GENERATED row + hand-written TRAP :411, `README.md`, `.ai/rules/_project.md`; `deploy` is worse — overloaded across deploy/deploy-project/deploy-context/deploy-modules + CI forced-command verb `receive` in core-deploy.yml
- Coupling mechanism: name string in make + sh + py argparse + yaml manifest + generated docs + hand prose; generator covers 4 of 8, rest manual
- Why dangerous: partial rename desyncs one layer; namelint catches manifest but a missed `--verb`/argparse choice fails at RUNTIME on the VPS — the last hop, worst observability
- Evidence: full layer trace above; 80+ `make <verb>` string matches in ≥6 files
- Scenario: verb consolidation during launch cleanup → node-update silently unreachable from make on node
- Impact: operational command path breakage discovered under fire
- Minimal decoupling: none pre-launch (freeze verbs); post-launch: single VERBS constant consumed by argparse+entrypoint generation
- Code churn: M–L · Regression risk: MED · Phase: **Pre-launch freeze** / post-launch consolidate

## DEP-0044 · Make target names in functional hook code + hand-edited prose
- Severity: MED · Category: shell-make-coupling · Confidence: HIGH
- Files: `core/entrypoints/pre-push-gate.sh:108` (`make check-diff`), `.pre-commit-config.yaml:42`, `AGENTS.md:411` TRAP prose, README, .ai/rules
- Coupling mechanism: git hook invokes target by literal name; rename/remove of check-diff breaks every push with misleading "rulesets/auth" symptom (git hides hook stderr)
- Why dangerous: silent push-block with wrong diagnostic hypothesis; documented incident pattern (DevPlan 157)
- Evidence: hook line + config line
- Scenario: check-suite refactor renames diff-scope target during triage → team blocked from pushing release fixes
- Impact: developer pipeline outage at worst moment
- Minimal decoupling: freeze target name pre-launch; post-launch resolve via entrypoint-manifest lookup with fail-loud error text
- Code churn: S · Regression risk: LOW if frozen · Phase: **Pre-launch freeze**

## DEP-0045 · Healthcheck criterion exists as sanctioned shell/Python twin without parity gate
- Severity: MED · Category: shell-make-coupling · Confidence: MED
- Files: `core/lib/healthcheck.sh` ↔ `core/internal/deploy/healthcheck_poller.py`; TRAP[DECISION] documents twin as canon ("тот же критерий")
- Coupling mechanism: one semantic criterion, two implementations kept in sync by convention only
- Why dangerous: criterion change landing in one implementation yields divergent health verdicts local-vs-deploy (deploy uses python poller for rollback decisions!)
- Evidence: TRAP text in root AGENTS.md; no parity test found by scanner
- Scenario: healthcheck strictness tweak before launch applied only to python side → local checks green, rollback logic judges differently
- Impact: false rollbacks or false positives on the deploy path
- Minimal decoupling: add lockstep parity test asserting both implementations agree on fixture container states (cheap, high value)
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (test-only)

## DEP-0046 · CI and pre-push hook invoke same verb family at divergent scopes
- Severity: MED · Category: shell-make-coupling · Confidence: MED (workflow contents inferred from docs, not read)
- Files: `push-gate.yml`, `platform-gate-fast.yml` (`make gate MODE=fast`, `make check MARKER=check-manifests`) vs hook `make check-diff`
- Coupling mechanism: invocation strings hardcoded in 3 layers over shared check-suite.yaml SoT
- Why dangerous: marker/target rename breaks CI without touching hook and vice versa; hook-green weaker than CI-green by design but nobody states the delta contract in code
- Evidence: layered invocations listed
- Scenario: MARKER id change in check-suite.yaml updates CI but stale hook keeps passing broken diffs to launch branch
- Impact: gate coverage erosion
- Minimal decoupling: derive both from check-suite.yaml entries programmatically
- Code churn: S–M · Regression risk: LOW · Phase: Post-launch

## DEP-0047 · Numeric exit codes cross shell/Python boundary as magic numbers
- Severity: MED · Category: shell-make-coupling · Confidence: MED
- Files: `shared/contracts.py` (0/1/2/3/4/10 table), `bootstrap.sh:69` + `core-deliver.sh:88-97` hardcode `rc==3` (node_detect key-absent = non-fatal), remote_dispatch 0|1|2|124; table documented in core/AGENTS.md prose
- Coupling mechanism: int literals as API between languages; prose-documented, not type-checked
- Why dangerous: renumbering/adding a code in contracts.py silently flips non-fatal→fatal in shell consumers with zero compile-time signal
- Evidence: rc==3 sites
- Scenario: exit-code taxonomy cleanup → bootstrap treats key-absence as fatal → node provisioning fails spuriously
- Impact: bootstrap/core-deliver flow misjudgment
- Minimal decoupling: emit codes from contracts.py into generated shell-sourceable file; shells source it
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## Positive results
- Thin-facade claim HOLDS: 12 measured entrypoints max 126 LOC (scaffold.sh); DevPlan 170 already collapsed fat pair. 7 unmeasured small files remain spot-check items.
- No inline-python heredoc violations observed in sampled lib/entrypoint files.
