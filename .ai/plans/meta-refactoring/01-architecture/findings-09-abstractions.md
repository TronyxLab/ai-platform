# Findings 09 — Abstractions & overengineering

Excluded as documented decisions (TRAP[DECISION]/@rationale): 3 template mechanisms, dual delivery, thin-shell facades, L1→L2 collapse, practices K1–K5, manifest-generation contract. All findings are NEW disproportionate machinery.

## ARCH-0038 — Third module-deploy route behind a feature flag that is never enabled
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** S · **Phase:** post-launch
- **Files:** `bootstrap/deploy/deploy_orchestrator.py:580` (`_deploy_orchestrator`, ~70 LOC subprocess route), routing `:407` `_route_deploy`, XOR `:508-514`; flag default False `:203`; `deploy-modules.sh:74`; production (`core-deploy.yml:258`, `core_deliverer.py:642`) sets only `DEPLOY_PARALLEL=true`
- **Evidence:** `DEPLOY_ORCHESTRATOR=true` appears nowhere outside docs/tests (rg verified). Fully maintained: dedicated tests, gate carve-outs (`test_gate_single_orchestrator.py:66`), doc contract listing 3 deploy modes in bootstrap AGENTS.md.
- **Impact:** ~90 LOC + tests + gate exceptions maintained for an unexercised alternative; ambiguity about "the" way modules deploy triples the state space of φ8/φ12.
- **Minimal fix:** delete the route + XOR branch (keep the `deploy-many` verb — forced-command contract), or enable it in CI and delete one of the other two paths. One flag, one route.

## ARCH-0039 — Two tar-payload validation/extraction mechanisms on the same VPS receive boundary; the strict one is production-dead
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** S–M · **Phase:** post-launch (⚠ security-relevant — cross-ref security audit)
- **Files:** strict path `deploy/payload_deliverer.py:206,312,372,405` (`deliver/_validate_and_extract/_validate_entry/_atomic_move`: whitelist, traversal/symlink rejection, compose-required; ~270 LOC) vs live channel `deploy/receive_flow.py:316` (`unpack`: plain `tarfile filter="data"`, no filename whitelist) + staging copy :427-465
- **Evidence:** nothing in production invokes the `deliver` stdin verb — only `tests/unit/test_payload_deliverer.py` (316 LOC). The real CI→VPS channel is ReceiveFlow with weaker validation.
- **Impact:** auditor reviews `_validate_entry` believing receive enforces a whitelist — it does not; hardening changes to the dead path are no-ops in production; two trust boundaries for identical input.
- **Minimal fix:** delete the dead extraction half (keep `assemble_payload`), or port `_validate_entry` checks into `ReceiveFlow.unpack`. One extraction path per boundary.

## ARCH-0040 — Canonical `atomic_writer` bypassed by ≥5 hand-rolled tempfile+os.replace copies added *after* the canon
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S · **Phase:** post-launch
- **Files:** canon `shared/atomic_writer.py:83,155,173` (declared "replaces 12+ local copies"); bypassers: `healthcheck/watchdog.py:417-441`, `check_suite/fingerprint.py:207-217`, `bootstrap/reboot_policy.py:216`, `bootstrap/privoxy_config.py:161-170`, `secrets/decrypt_secrets.py` (~:315, incl. PLW0717-extracted helper)
- **Impact:** divergent crash semantics across node state files; a durability fix in atomic_writer silently misses five files (see also ARCH-0021).
- **Minimal fix:** replace bodies with `atomic_write_json`/`atomic_write_text(mode=…)`.

## ARCH-0041 — Sentinel accessors that return a literal empty string, used 12× as env defaults
- **Severity:** P3 · **Confidence:** 0.85 · **Churn:** S · **Phase:** post-launch
- **Files:** `config/platform_config.py:196,:233` (`default_s3_bucket_sentinel()`/`default_context_sentinel()`, each ~15 LOC of docs around `return ""`); call sites `s3_ssl_cache.py` ×6, `preflight.py:513`, `cert_orchestrator.py:575,625`, `context_deployer.py:856,1252`; vulture whitelist entry needed for one of them
- **Impact:** ceremony tax on every S3/CONTEXT env read; reader opens three files to learn the value is `""`.
- **Minimal fix:** inline `""` + one-line comment; delete accessors + whitelist entries.

## ARCH-0042 — Three stacked readers for one config value (`COMPOSE_PROFILES`) with mirrored SoT-resolution logic
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S–M · **Phase:** post-launch
- **Files:** `shared/compose_profiles.py:47-77` (own PLATFORM_ROOT resolution + yaml parse; knowingly «зеркало platform_config» per its own comment) · `config/platform_config.py:80-128` (same resolution, opposite error policy) · `scaffold/scaffold_helpers.py:76-89` (wraps load_profiles, split → re-join to comma-string)
- **Impact:** ~150 LOC where ~30 suffice; adding any `env_defaults.*` consumer forces a choice between two facades with opposite failure semantics; parity gate exists to police divergence these loaders create.
- **Minimal fix:** fold into `platform_config` (required=True mode); drop the join-wrapper.

## ARCH-0043 — `make new-project` spawns an extra Python process just to re-format argv through shell stdout
- **Severity:** P3 · **Confidence:** 0.65 · **Churn:** S · **Phase:** post-launch
- **Files:** `entrypoints/scaffold.sh:44-52` (python normalize → `read -ra args <<<` → exec add-project.sh) → `add-project.sh:12` (exec python scaffolder) → `scaffold/normalize_new_project_args.py:46-106` (113 LOC)
- **Evidence:** bridge exists purely for positional-arg backward compat; `print(" ".join(args))` breaks on values containing spaces/IFS chars.
- **Minimal fix:** move normalization into `project_scaffolder.build_parser()`; reduce the module to a tested function.

## ARCH-0044 — `_CliArgs(Protocol)` typing boilerplate duplicated across ~55 CLIs
- **Severity:** P3 · **Confidence:** 0.6 · **Churn:** M (mechanical, wide) · **Phase:** post-launch
- **Files:** byte-identical Protocol+docstring blocks in ≥11 bootstrap modules (`sudoers_generator.py:509`, `context_deployer.py:1209`, `docker_orchestrator.py:720`, …); total `class _CliArgs` count 55; double-cast idiom repeated everywhere (`context_deployer.py:1232`)
- **Impact:** ~600–800 LOC of ritual; every new CLI copies it.
- **Minimal fix:** `shared/cli_typing.parse_typed(parser, proto)`; mechanically replace cast choreography (fields stay per-CLI).
