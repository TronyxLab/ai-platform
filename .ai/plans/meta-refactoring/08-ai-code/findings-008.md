# Findings 008 — Duplicated abstractions, unnecessary genericity, contradictory validation
# Wave 1 · agent: abstraction-dupe

## AI-0053 [MEDIUM] [dead-accessors/single-impl-genericity]
Files: core/internal/config/platform_config.py:161,177,214,250-251
Symbols: default_s3_prefix(), default_platform_context() (zero callers; docstring admits), remaining accessors = 1-line aliases of get_default(key)
Evidence: repo-wide rg excl. defs/docs → 0 call sites.
Problem: dead typed-facade surface misleads agents into "canonical" accessor use nobody consumes.
Minimal cleanup: delete two accessors + alias layer where unused. Churn ~20 lines. Post-launch OK. Confidence: high.

## AI-0054 [MEDIUM] [abstraction-dupe/env-dependent-semantics]
Files: core/internal/validate/validate_orchestrator.py:139-168,252-314 vs core/internal/shared/schema_validator.py:14 ("единственная Draft7Validator-точка")
Symbols: _detect_validator (ajv|python), validate_with_ajv
Evidence: ajv takes PRIORITY when installed on machine; same node.yaml/module.schema.json validated by DIFFERENT engines dev-vs-CI (draft/format edge behavior + error formats diverge).
Problem: identical YAML passes locally, fails CI (or vice versa); documented single-validator invariant bypassable by PATH contents.
Minimal cleanup: pin python Draft7Validator everywhere (drop ajv branch) or make choice explicit config, not environment sniffing. Churn ~30 lines. Pre-launch desirable (CI/dev parity). Confidence: high.

## AI-0055 [MEDIUM] [contradictory-validation/three-dotenv-grammars]
Files: shared/env_reader.py:52,73-75; shared/secrets_env_parser.py:127-148; secrets/decrypt_secrets.py:194-210
Symbols: env_reader._LINE_RE («всё после первого =», no quote/comment handling) vs secrets_env_parser._parse_line (strips quotes + unquoted comments) vs _yaml_to_env re-impl quote-strip + shell-escape duplicating export_shell:353-356
Evidence: same line FOO="bar #x" parses differently per parser.
Problem: value read via make/env_reader ≠ same var read by bootstrap parsers — silent divergence for quoted values containing #.
Minimal cleanup: single parser entry point (secrets_env_parser) consumed by all three sites. Churn ~40 lines. Pre-launch: yes if any prod values contain quoted #. Confidence: med (divergence proven; real-world trigger depends on secret content).

## AI-0056 [LOW] [wrapper-chain/expired-shim]
Files: core/internal/deploy/engine/engine.py:448-479 → flow.py:44-77 → shared/retry_pull, docker_compose_up
Evidence: three engine methods are single-statement pass-throughs (AST-verified); production callers of flow fns = only these shims; rest are tests targeting shims.
Cleanup: inline or fold flow into shared. Churn ~40 lines. Post-launch OK. Confidence: high.

## AI-0057 [HYPOTHESIS·LOW] [canon-adoption-gap]
Files: shared/subprocess_io.py:157 (run_subprocess canon w/ rc124/127 semantics) vs 92 files calling raw subprocess.run (29 import canon)
Evidence: gate enforces single DEFINITION only, not exclusive use; drift NOT proven (many raw sites are justified primitives like docker_ops/http_probe). Needs triage pass before acting. Do not refactor pre-launch on this basis.
