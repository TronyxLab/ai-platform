# 06-dependencies · Findings Index

Audit: dependency/coupling, pre-launch. 9 research subagents (2 waves) + synthesis. No code changed.

**60 findings** · CRITICAL 4 · HIGH 12 · MED 27 · LO 17 (incl. 4 positive-control records)
Files: findings-001…011.md · Cascade answer: 00-cascade-answer.md · Method: 99-method.md

| ID | Sev | Category | Finding | File |
|----|-----|----------|---------|------|
| DEP-0001 | CRIT | hub | shared = universal hub, ~216 prod files | 001 |
| DEP-0002 | CRIT | hub | shared.timeouts gate-enforced hub, ~89 files | 001 |
| DEP-0003 | CRIT | hub | exceptions dual-class hazard (proven incident) | 001 |
| DEP-0004 | HIGH | hub | bootstrap→deploy upward on deploy path | 001 |
| DEP-0005 | HIGH | hub | deploy/orchestrator mega-hub, lazy back-edges | 002 |
| DEP-0006 | MED | hub | shared↔test_runner documented cycle | 002 |
| DEP-0007 | MED | hub | deploy→practices K3 chain, ungated edge | 002 |
| DEP-0008 | MED | hub | scaffold↔bootstrap mutual reach (TRAP contradiction) | 002 |
| DEP-0009 | LO | hub | manifest generator timeouts dual-SoT | 002 |
| DEP-0010 | HIGH | circular | check_suite __init__↔manifest cycle (make check landmine) | 003 |
| DEP-0011 | HIGH | circular | deploy.engine↔lifecycle cycle | 003 |
| DEP-0012 | LO | circular | shared/s3_client→config leaf leak | 003 |
| DEP-0013 | MED | circular | lazy-import workaround cluster | 003 |
| DEP-0014 | LO | circular | practices/check_project.py masked dead file | 003 |
| DEP-0015 | MED | circular | ungated healthcheck→bootstrap edge | 003 |
| DEP-0016 | CRIT | hidden | static --only silent false PASS | 004 |
| DEP-0017 | HIGH | hidden | AGE_SECRET_KEY 35+ hardcoded files | 004 |
| DEP-0018 | HIGH | hidden | file-path importlib + warning-swallow φ7/φ8 | 004 |
| DEP-0019 | MED | hidden | detector-name triple store | 004 |
| DEP-0020 | MED | hidden | module names as string CLI contracts, 10+ sites | 005 |
| DEP-0021 | MED | hidden | PLATFORM_POSTGRES_DSN no Python constant | 005 |
| DEP-0022 | LO | hidden | pytest markers as dispatch keys | 005 |
| DEP-0023 | LO | hidden | entrypoint-manifest scattered ~40 files | 005 |
| DEP-0024 | LO | hidden | monkey-patching absent (positive control) | 005 |
| DEP-0025 | MED | mutable | platform_config._loaded latch bug | 006 |
| DEP-0026 | MED | mutable | _TEMP_FILES live-list in signal handler | 006 |
| DEP-0027 | MED | mutable | state.json last-writer-wins + --force unguarded | 006 |
| DEP-0028 | LO | mutable | audit_logger global set | 006 |
| DEP-0029 | LO | mutable | converge globals + lru_cache staleness | 006 |
| DEP-0030 | MED | infra | network names duplicated, no code SoT/gate | 007 |
| DEP-0031 | MED | infra | raw docker run in scaffold | 007 |
| DEP-0032 | MED | infra | bash -c ssh_exec bypass | 007 |
| DEP-0033 | MED | infra | /opt path defaults outside SoT (test-node literal) | 007 |
| DEP-0034 | MED | infra | env scatter, S3 endpoint triplicated | 007 |
| DEP-0035 | LO | infra | postgres:5432 in 5 files (documented bypass) | 007 |
| DEP-0036 | LO | infra | ~/projects + DSN literals in scaffold | 007 |
| DEP-0037 | MED | init | secrets_manager dual-mode fallback masks bugs | 008 |
| DEP-0038 | MED | init | ≥5 PLATFORM_ROOT re-derivations | 008 |
| DEP-0039 | MED | init | --run-phase trusts state over artifacts | 008 |
| DEP-0040 | MED | init | secrets.env env-channel best-effort sourcing | 008 |
| DEP-0041 | LO | init | 65+ sys.path sites, 8 TRAP incidents | 008 |
| DEP-0042 | LO | init | lone cwd dependency validate_module_yaml | 008 |
| DEP-0043 | HIGH | shell-make | verb name in 6–8 layers, fails at VPS hop | 009 |
| DEP-0044 | MED | shell-make | check-diff in pre-push hook + prose | 009 |
| DEP-0045 | MED | shell-make | healthcheck twin without parity gate | 009 |
| DEP-0046 | MED | shell-make | CI vs hook divergent scopes | 009 |
| DEP-0047 | MED | shell-make | exit codes as magic numbers across boundary | 009 |
| DEP-0048 | CRIT | abstraction | OrchestratorDeployResult god-DTO + wire contract | 010 |
| DEP-0049 | HIGH | abstraction | StatusResult vs ProjectStatus duplicates | 010 |
| DEP-0050 | HIGH | abstraction | orchestrator private-API-as-public surface | 010 |
| DEP-0051 | MED | abstraction | StepState kwarg-removal incident + state schema | 010 |
| DEP-0052 | MED | abstraction | module.yaml ad-hoc readers ≥5 | 010 |
| DEP-0053 | MED | abstraction | node_yaml dual role, ~33 consumers | 010 |
| DEP-0054 | HIGH | gates | add-one-module = 5–8 edits, silent gaps | 011 |
| DEP-0055 | HIGH | gates | env_defaults value-pins ×5+ gates | 011 |
| DEP-0056 | HIGH | gates | generator change → 6–7 gates | 011 |
| DEP-0057 | MED | gates | manifest 22-gate hub, negative pins | 011 |
| DEP-0058 | MED | gates | double/triple SoT pinning | 011 |
| DEP-0059 | MED | gates | "exactly N" cardinality pins | 011 |

Confidence: HIGH = verified by reading/grep; MED = strong signal, partial trace; items marked HYPOTHESIS in files = plausible-unverified, not counted as facts.
