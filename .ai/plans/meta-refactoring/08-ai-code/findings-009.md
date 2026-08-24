# Findings 009 — Dead / unreachable / obsolete code
# Wave 2 · agent: dead-code retry · mechanical scans + dynamic-dispatch filtering

## AI-0058 [MEDIUM] [dead-code/gate-pinned]
Files: core/internal/scripts/generate_entrypoint_manifest.py:320
Symbols: load_existing_manifest
Proof: rg whole-repo (py/sh/Makefile/*.mk/yaml) → production refs=0; live refs=28 all tests; gate test_gate_generate_entrypoint_manifest_no_self_read.py:107 ASSERTS main() must NOT call it.
Note: keep-rationale "external consumers" has zero in-repo evidence; replaced by load_structural_sections() (G3 cycle break).
Cleanup: delete function + its pinning-gate clause + tests. Churn ~30 lines. Post-launch OK. Confidence: high.

## AI-0059 [MEDIUM] [dead-code/false-docstring]
Files: core/internal/shared/compose_files.py:84
Symbols: requires_compose_project
Proof: non-test refs = def + own comments + AGENTS.md inventory row; docstring claims «используется converge (runtime/volumes)» — converge has zero calls; consumers use resolve_compose_file() directly.
Cleanup: delete fn + inventory row + tests; fix docstring of module. Churn ~20 lines. Confidence: high.

## AI-0060 [LOW] [test-only-wrappers]
Files: shared/deploy_paths.py:130,142 — get_canonical_paths/get_deprecated_paths: production refs=0; exist solely to serve their own gate tests over the real SoT constants consumed directly. Cleanup: point gate tests at constants, drop getters. Churn <15 lines. Confidence: high.

## AI-0061 [LOW] [test-only-export]
Files: shared/secrets_env_parser.py:327 export_shell: production refs=0 (own docs + inventory); live refs=19 all in its own test trio. NOTE: decrypt_secrets._yaml_to_env re-implements its logic (see AI-0055) — decide together: either adopt export_shell at that site or delete both paths consistently. Churn <15 lines. Confidence: high.

## AI-0062 [MEDIUM] [orphaned-CLI-surface]
Files: shared/project_registry.py:143,221,283 (+argparse dispatch :410)
Symbols: register_project/deregister_project/list_projects CLI surface
Proof: zero invocations of module CLI anywhere outside tests; canon routes through scaffold (make project-list → scaffold/project-list.sh); module itself alive via validate_project_name/discover_llm_projects imports.
Risk: two competing "registry CLIs"; orphaned surface drifts from real workflow.
Cleanup: strip argparse dispatch + three verbs, keep library functions used by scaffold. Churn ~40 lines. Post-launch OK. Confidence: high.

# Negative results: no *_legacy/*_compat files; no sys.version_info guards below floor (3.10); verify_sweep re-export facade live; all B-list same-file symbols alive.
