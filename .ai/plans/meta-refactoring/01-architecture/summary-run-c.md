# Summary — TOP-10 Architectural Risks (run-c, 2026-08-24)

Independent synthesis of run-c: 48 findings (`ARCH-0001`…`ARCH-0048`, files `findings-01-boundaries.md`…
`findings-10-hotspots.md`), produced by 10 fresh parallel subagents on the current tree. Complements the
merged Aug-22 runs in `summary.md` (run-a + run-b, 103 findings) — cross-run agreement raises confidence:
the same PRIVOXY_PORT reach-in, shared-leaf breach, deploy-god-cluster and state-machine hash gaps appear
in both syntheses independently.

Ranked by severity × confidence × blast radius:

| # | ID | Risk | Sev | Conf | Phase |
|---|----|------|-----|------|-------|
| 1 | ARCH-0033 | **Silent update no-op**: `node-update` content-hash covers only `state_machine.py` bytes, not the code φ12 executes → CI ships new core, phases log "already done — skipping", exit 0. Delivered code never runs; audit green. | P1 | 0.85 | post-launch |
| 2 | ARCH-0022 | **Deployment identity hardcoded** in ≥7 files with 3 fallback chains, 2 env names, 2 conflicting default orgs → second context/node onboarding silently targets `tronyx-vps`; wrong-node deploys. Blocks the platform's stated multi-context purpose. | P1 | 0.90 | pre-launch |
| 3 | ARCH-0045 | **Docker-smoke execution contract triplicated** (check-suite.yaml / compose.py / platform-test.yml); proven day-long hang incident class (DevPlan 006/007). | P1 | 0.85 | pre-launch |
| 4 | ARCH-0039 | **Payload-validation illusion at VPS receive boundary**: strict whitelist/traversal validator exists only on a production-dead path; live `ReceiveFlow.unpack` is plain `tarfile filter="data"`. Security-relevant (cross-ref 03-security SEC-0011/SEC-0032 family). | P2 | 0.80 | post-launch |
| 5 | ARCH-0028 | **Core-delivery channel divergence**: CI rsync excludes drifted from `core_deliverer.py`; test-compose files land in prod; both channels `--delete` → flap between delivery channels rewrites the prod tree. | P2 | 0.90 | pre-launch |
| 6 | ARCH-0036 | **Secrets pipeline fail-open mix**: decrypt=fail-closed but source/autogen/validator-error=fail-open inside the same FATAL-wrapped step → modules deploy without creds; failures surface far from cause. | P2 | 0.80 | post-launch |
| 7 | ARCH-0029 | **Health-criterion drift on user-facing status page**: collector contradicts canonical poller (no-healthcheck→unhealthy, starting→WARN) → false degradation signals per restart. | P2 | 0.90 | post-launch |
| 8 | ARCH-0034/35 | **Deploy ordering/gating races**: sequential path ignores `module.yaml#depends_on` (node.yaml list order = hidden timing contract); topo failure degrades to unordered; failed group doesn't gate dependents. | P2 | 0.75 | pre-launch |
| 9 | ARCH-0010 | **`check_suite` hub inversion**: 6 submodules late-bind the package root; one lazy band-aid masks the closing edge → one innocent module-level constant breaks `make check` platform-wide in a specific import order. | P2 | 0.90 | post-launch |
| 10 | ARCH-0023/24 | **Port-registry fragmentation**: firewall deny-list and port-scanner map are ungated hand mirrors of platform-infra.yaml → port migration leaves stale deny rules (possible public exposure) and corrupts generated `.env.platform`. | P2 | 0.90 | pre-launch |

Near-miss tier: ARCH-0018 dual module identity for `monitoring/*`; ARCH-0001 two "deploy" packages;
ARCH-0002 shared-leaf breach via config; ARCH-0017 import-time signal hijack; ARCH-0046 compose.py
fixture-orchestrator; ARCH-0006 parity gate cementing upward PRIVOXY_PORT imports.

## Pattern read

1. **SoT without enforcement tail** — registries exist (ports, paths, timeouts, payload lists) but mirrors/gates don't cover all copies (ARCH-0023/24/25/26, 30).
2. **Contract-by-convention at lifecycle boundaries** — phase ordering, group gating, secrets failure modes, hash invalidation rely on side effects rather than declared contracts (ARCH-0033/34/35/36).
3. **Facade erosion by accretion** — god-inits, mixed-responsibility files, dead alternative routes around otherwise clean cores (ARCH-0012/13/38/39).

## Suggested sequencing (not executed)

Pre-launch: #2, #5, #3, #10, plus ARCH-0026 (acme path) — small churn, high tail-risk reduction.
Post-launch wave 1: #1, #6, #7, #4. Post-launch wave 2: structural splits (findings-03/04/09) behind existing test coverage.
