# Findings 005 — Ignored parameters & fake configuration
# Wave 1 · agent: param-ignored/fake-config · D4-D6 adversarially verified

## AI-0026 [LOW·VERIFIED] [fake-flag]
Files: core/internal/bootstrap/deploy/context_deployer.py:1193-1202,1221,1227-1266,471-504
Symbols: --no-fallback-build → args parsed → never read; ghcr_fallback_build threaded but unread (_ghcr_fallback_build, DevPlan 091 removed fallback)
Evidence (verifier): argparse→_CliArgs→dropped at main(); deploy_context has no such param; underscore = intentionally unused.
Impact: operator believes build-fallback disabled — it was REMOVED entirely.
Cleanup: remove flag (or implement). Churn <10 lines. Confidence: high.

## AI-0027 [LOW·VERIFIED] [fake-knob]
Files: core/internal/deploy/engine/engine.py:154-162 (# ruff ignore[ARG002]); cli.py:43,71,98
Symbols: deploy(keep_images=3), --keep-images
Evidence (verifier): zero prune/rmi logic in engine+flow+lifecycle+docker_compose (only unrelated history snapshot prune); docstring "keep during prune" describes nonexistent behavior. No functional effect.
Cleanup: remove param/flag or document as unimplemented. Churn <10 lines. Confidence: high.

## AI-0028 [MEDIUM] [dead-schema-promise]
Files: core/schemas/node.schema.json:276-288; node-configs/*/node.yaml (both prod nodes populate); shared/node_yaml/__init__.py:18 (getter deleted wave 118 B3, verify-then-delete=0 consumers)
Symbols: node.yaml#postgres_init_databases
Problem: operator declares bootstrap-time DB creation; nothing creates it (project DBs come from needs.database hook) — schema validates a promise nobody keeps.
Cleanup: drop schema block + entries + comment. Churn ~15 lines. Pre-launch: yes (bootstrap expectations). Confidence: high.

## AI-0029 [MEDIUM] [dead-schema-promise]
Files: core/schemas/node.schema.json:288-296; node-configs/*/#repos.core/repos.node_configs vs deleted get_repos
Evidence: negative rg for repos accessors across core/ py — empty. Also contradicts core-never-via-git invariant.
Cleanup: same as AI-0028. Churn ~15 lines. Pre-launch: bundle. Confidence: high.

## AI-0030 [MEDIUM] [schema-field-ignored]
Files: core/schemas/module.schema.json:117; core/modules/platform-secrets/module.yaml:29-32 vs installer.py:38 hardcoded UNIT_NAME; RequiredBy lives in .service file
Evidence: rg systemd accessor usage across core/tests — empty.
Problem: editing module.yaml#systemd.* validates clean and changes nothing at runtime.
Cleanup: honor fields in installer OR remove from module.yaml+schema. Churn ~20 lines. Post-launch OK. Confidence: high.

## AI-0031 [LOW] [ignored-flags-uniform-signature]
Files: bootstrap/converge/reconciler.py:167-168,220 threading dry_run/report_only into volumes.py:139-142 / vhosts.py:147-151 which suppress ARG001 and never branch (docstrings claim they do); networks.py/runtime.py DO honor flags.
Risk: future mutation code inherits silently-ignored flags. Cleanup: wire or drop params. Churn <10 lines. Confidence: high.

## AI-0032 [LOW] [self-admitted-dead-flag]
Files: bootstrap/deploy/docker_orchestrator.py:732,763 — help text literally says "(unused in docker_orchestrator)". Cleanup: delete. Churn <5 lines. Confidence: high.

## AI-0033 [LOW] [param-ignored/misleading-signatures]
Files: scaffold/scaffold_helpers.py:113,292,482,547 — gen_ai_platform_yaml(org), gen_project_makefile(domain), gen_project_platform_md(name), register_in_node_yaml(node): ARG001-suppressed; real callers pass values that vanish (AST-verified identifiers only in signatures).
Cleanup: drop params or use them. Churn ~15 lines. Confidence: high.

## AI-0034 [LOW] [param-ignored]
Files: bootstrap/deploy/orchestrator_metrics.py:99 exit_code_from_results(crit,warn,deployed) ignores deployed; deploy/context_promoter.py:195 verify_mirror(context,…) ignores context. Cleanup: trim signatures. Churn <10 lines. Confidence: high.

## AI-0035 [LOW·documented-drift-risk] [vestigial-param ×3 wrappers]
Files: shared/node_resolver.py:96-99,117-118; bootstrap/remote_executor.py:85; bootstrap/overlay_deliverer.py:92 — resolve_node_yaml(projects_dir) documented VESTIGIAL (glob env-driven); callers passing explicit dir silently get global search.
Cleanup: remove param chain or honor it. Churn ~20 lines. Confidence: high.

# Note: fp_registry.yaml was empty; candidates mined via AST scan instead.
