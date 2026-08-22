# Findings 06 — Infrastructure ↔ application/domain coupling

Enforcement baseline (gates that already own their scopes): `test_gate_timeout_literals`, `test_gate_port_parity`, `test_gate_ssh_opts_sole_path`, `test_gate_subprocess_io_sole`, `test_gate_networks_sot`, `test_gate_deploy_paths`. Findings sit **outside** these scopes.

## ARCH-0022 — Deployment identity ("tronyx-vps", org defaults) hardcoded in ≥7 generic files, with conflicting env names and org fallbacks
- **Severity:** P1 · **Confidence:** 0.9 · **Churn:** M · **Phase:** pre-launch
- **Files:** `shared/app_config.py:61-62` (`_DEFAULT_ORG="personal"`, `_DEFAULT_NODE="tronyx-vps"` — sanctioned root) · `shared/project_yaml.py:299` (inline re-implementation, env `PLATFORM_DEFAULT_NODE` → literal) · `scaffold/project_scaffolder.py:61` (`_DEFAULT_NODE`, **no env fallback at all**) · `scaffold/context_initializer.py:50-51` (env names `NODE`/`NODE_ORG`, org default `"tronyx-lab"` contradicting AppConfig) · `scaffold/scaffold_helpers.py:58`, `scaffold/gen_project_platform_md.py:585`, `reconciler_projects.py:185-188`
- **Evidence:** three distinct fallback chains, two env-var names for the same concept, two different default orgs — in code claiming to be context-generic (root invariant 3: org = context).
- **Failure scenario:** onboarding a second context (the platform's stated purpose): documented env fixes some entry points but not `project_scaffolder` (hard literal) nor `context_initializer` (listens to `NODE`) → scaffolding silently targets `tronyx-vps`.
- **Impact:** topology change amplifies to a 7-file hunt; partial overrides create silent wrong-node deploys.
- **Minimal fix:** single `default_node()`/`default_org()` resolver in `shared/app_config.py`; replace all inline chains; kill `NODE`/`NODE_ORG` aliases.

## ARCH-0023 — Firewall deny-list is an ungated second mirror of the port registry (security-relevant rot)
- **Severity:** P2 · **Confidence:** 0.9 · **Churn:** S · **Phase:** pre-launch
- **Files:** `core/internal/bootstrap/firewall.py:81,89-103` (`DENY_PORT`, `MODULE_PORTS_DENY` = 6379, 8123, 9000, 9001, 4000, 3001, 3100, 9090, 3000, 9119, 8642, 9113, 9100) · SoT `shared/platform_ports.py:40-72`; firewall imports neither platform_ports nor platform-infra (in-file comment admits copy)
- **Evidence:** `test_gate_port_parity` asserts only `PLATFORM_PORT_*`; no gate reads `MODULE_PORTS_DENY`.
- **Failure scenario:** litellm host port 4000→4001 (gate forces SoT+compose update) → firewall keeps denying stale 4000, never denies 4001 → future non-loopback bind publishes LiteLLM publicly. Silent.
- **Impact:** defense-in-depth layer rots invisibly exactly when services migrate ports.
- **Minimal fix:** construct `MODULE_PORTS_DENY` from imported `PLATFORM_PORT_*`; extend parity gate with `MODULE_PORTS_DENY ⊇ {PLATFORM_PORT_*}`.

## ARCH-0024 — `port_scanner._PORT_NAME_MAP`: third ungated port copy feeding generated `.env.platform` names
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** S–M · **Phase:** pre-launch
- **Files:** `core/internal/scripts/port_scanner.py:31-52` (`_PORT_NAME_MAP`: `3001:"langfuse"`, `8080:"cadvisor"` vs SoT `PLATFORM_PORT_STATUS_PAGE=8080`) → consumer `generate_platform_env`; unit test asserts the map **against itself**
- **Evidence:** split-brain built in: `PLATFORM_PORT_LANGFUSE=3000` (container facet) vs `3001` (host facet) — two registries, no cross-check; parity gate covers only the former.
- **Failure scenario:** host port 3001→3002 → generated `.env.platform` emits generic/dropped names → `PLATFORM_LANGFUSE_URL` consumers break at next `make sync-env`, discovered at deploy time.
- **Minimal fix:** derive names from `platform-infra.yaml` or add parity assertion `∀ provides.port ∈ _PORT_NAME_MAP`. (Related: ARCH-0023 — same fragmentation.)

## ARCH-0025 — `/opt` path-gate blind spot: suffixed/f-string literals escape the regex; node.yaml resolution hand-rolled 3×
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** S · **Phase:** pre-launch
- **Files:** gate regex `tests/gates/test_gate_timeout_literals.py:660` (exact quoted string only) vs escapees: `healthcheck/metrics/cert_collector.py:338` (default `"/opt/node-configs/test-node/node.yaml"` — **bakes a concrete node name**), `healthcheck/modules_healthcheck.py:283-288` (f-string path + inline `NODE_YAML`/`NODE_NAME` env reads), `shared/node_yaml/resolve.py:111`, `secrets/decrypt_secrets.py:369` (`_NODE_CONFIGS_SECRETS_DIR`)
- **Failure scenario:** configs root relocated (deploy_paths updated, gate green) → healthcheck enabled-module filter silently returns None (= "all modules"), cert collector probes nonexistent default, decrypt writes to old dir.
- **Minimal fix:** route all three through `NodeYaml.resolve`/`deploy_paths`; widen gate regex to `/opt/(projects|platform|node-configs)\b`.

## ARCH-0026 — `/opt/acme.sh` defined independently in 5 files — absent from `deploy_paths` entirely
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** S · **Phase:** pre-launch
- **Files:** `bootstrap/issue_cert.py:90` (+env override :808), `cert_orchestrator.py:530,916,937`, `cron_installer.py:54,118`, `install_acme.py:52`, `s3_ssl_cache.py:87`
- **Failure scenario:** acme.sh relocated → issuance updated in one file, renewal cron and S3 sync still point at old home → certs stop renewing; discovered at first expiry (~90 days later). Highest-latency failure mode on the node.
- **Minimal fix:** `acme_home(env)` in `deploy_paths.py`; re-point five definitions; one-line gate addition.

## ARCH-0027 — Timeout SoT leaks in unlisted domains
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S · **Phase:** pre-launch
- **Files:** `practices/maturity.py:64` (`_GIT_TIMEOUT_SEC = 5`, used :177; practices/ absent from gate's domain lists) · `scaffold/project_scaffolder.py:200-211` (`subprocess.run(rsync_cmd, check=True)` — **no timeout kwarg**; gate flags only calls *with* literals)
- **Scenario:** stalled network volume → `make new-project` blocks forever with no tunable; slow clone killed by magic `5`s nobody owns.
- **Minimal fix:** `GIT_TIMEOUT` → `shared/timeouts.py`; pass `timeouts.RSYNC_TIMEOUT` to rsync.

Minor residue (P3, 0.7): `loadtest/prometheus_pull.py:74-77` encodes container names into PromQL (`name="postgres"`) — silent loss of capacity-report rows on module rename; parameterize from scenarios. `bootstrap/converge/networks.py:160` uses bare `"proxy-net"` instead of the `PROXY_NET` constant (`converge/infra.py:63`).
