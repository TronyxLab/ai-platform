# Findings 007 · Infrastructure leakage

## DEP-0030 · Docker network names duplicated in 12+ py files; manifest SoT never read by code
- Severity: MED · Category: infra-leakage · Confidence: HIGH
- Files: `bootstrap/converge/networks.py` (`"proxy-net"` ~30 hits), `scaffold/compose_validator.py`, `loadtest/config.py:88` (`ALLOWED_NETWORKS=("host","shared-db-net")`) + loadtest db/pgwire/runner_remote, backup-cron scripts
- Coupling mechanism: network-name literals in Python; platform-infra.yaml is nominal SoT but no code reads it for names; no parity gate covers networks
- Why dangerous: rename of a compose network → converge creates stale net, loadtest validation diverges silently
- Evidence: counts per file above
- Scenario: security-driven network segmentation rename during launch hardening
- Impact: converge/loadtest breakage discovered at deploy time
- Minimal decoupling: consume names via generated platform-env (already GENERATED pipeline); add parity assertion
- Code churn: S–M · Regression risk: LOW · Phase: Post-launch (pre-launch: freeze names)

## DEP-0031 · scaffold business module bypasses docker facade with raw `docker run`
- Severity: MED · Category: infra-leakage · Confidence: HIGH
- Files: `scaffold/nginx_harness.py:217-232`
- Coupling mechanism: direct subprocess docker run for `nginx -t`; docker_sole_path gate blocks only `docker compose` outside docker_compose.py
- Why dangerous: enforcement gap class — next raw-docker call slips through too; vhost validation env drifts from real stack
- Evidence: subprocess.run(["docker","run","--rm",...])
- Scenario: docker CLI change/aliasing breaks vhost render validation only in scaffold path
- Impact: tooling-only, but same failure shape as deploy path bugs
- Minimal decoupling: route through shared.docker_ops
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0032 · Python business layer routes SSH through shell lib instead of remote_executor
- Severity: MED · Category: infra-leakage · Confidence: HIGH
- Files: `scaffold/project_remover.py:320-330` (`bash -c 'source core/lib/ssh.sh && ssh_exec ...'`)
- Coupling mechanism: python→shell→ssh chain duplicates the ssh_cmd_builder/remote_executor seam; flags happen to come from ssh_opts SoT
- Why dangerous: two live ssh seams; changes to ssh_exec shell API break python caller invisibly to gates
- Evidence: exact command construction
- Scenario: ssh.sh refactor during supply-chain hardening silently breaks remove-project
- Impact: project removal flow degradation
- Minimal decoupling: replace with remote_executor call
- Code churn: S · Regression risk: LOW · Phase: Post-launch

## DEP-0033 · `/opt` infra-path defaults outside paths SoT (incl. test-node literal in healthcheck collector)
- Severity: MED · Category: infra-leakage · Confidence: HIGH
- Files: `healthcheck/metrics/cert_collector.py:338` (`NODE_YAML_PATH` default `/opt/node-configs/test-node/node.yaml`), `modules/platform-secrets/installer.py:48` (bare `/opt/platform`), `bootstrap/deploy/llm_provision.py:105` (CORE_DIR dup of deploy_paths resolver), `platform-export-metrics.sh:33`
- Coupling mechanism: run_paths_sole gate covers `/var/lib/platform` only; `/opt/*` and bare root slip allowlists
- Why dangerous: node-configs relocation or per-node naming change breaks healthcheck metrics collection with stale default pointing at a LITERAL TEST NODE
- Evidence: paths above; gate scope verified
- Scenario: multi-node rollout renames configs dir → monitoring collectors go quiet
- Impact: observability blind spot exactly when needed pre-launch
- Minimal decoupling: move defaults into shared.deploy_paths; kill test-node literal (derive from node context)
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate** (observability integrity)

## DEP-0034 · Env-config scatter: ~250-300 read sites; manifest names duplicated with own defaults
- Severity: MED · Category: infra-leakage · Confidence: HIGH
- Files: `bootstrap/preflight.py:241,253,512` (`S3_ENDPOINT_URL` default `"https://s3.timeweb.cloud"` ×3), `monitoring/langfuse_projects.py:90` (`LANGFUSE_SECRET_KEY` direct read), `llm/key_provisioner.py:338,777`, `shared/vps_readiness.py:180` + `verify_sweep/collection.py:335` (`NODE_HOST_MAP` ×2)
- Coupling mechanism: env names+defaults re-stated per module; platform-infra.yaml is endpoint SoT
- Why dangerous: endpoint/provider migration (timeweb→other) leaves stale fallbacks; secret reads in monitoring widen credential surface
- Evidence: listed triplications
- Scenario: S3 provider swap during launch week → half the modules keep old endpoint via local defaults
- Impact: config drift with silent fallback behavior
- Minimal decoupling: route defaults through env_defaults_generated (already generated); forbid literal URLs in getenv defaults via grep gate
- Code churn: M · Regression risk: LOW · Phase: Post-launch (freeze values pre-launch)

## DEP-0035 · postgres:5432 knowledge in 5 files (documented pgbouncer-bypass)
- Severity: LO · Category: infra-leakage · Confidence: HIGH
- Files: `modules/backup-cron/scripts/backup_postgres.py:181` (bypass TRAP-documented), `loadtest/{config,db,pgwire,runner_cli}.py`
- Coupling mechanism: direct container-port literals; port_parity gate doesn't include 5432
- Why dangerous: low — bypass is deliberate (backups must not go through pooler)
- Evidence: TRAP[BUG] documents rationale
- Scenario: postgres port remap breaks backups+loadtest
- Impact: narrow
- Minimal decoupling: none required; optionally extend port_parity
- Code churn: S · Regression risk: LOW · Phase: N/A

## DEP-0036 · `~/projects` layout + DSN literal duplicated in scaffold generators
- Severity: LO · Category: infra-leakage · Confidence: HIGH
- Files: `scaffold/context_initializer.py:49` (`Path(HOME,"projects")`), `scaffold/gen_env_platform.py:200` (`pgbouncer:6432` literal instead of PLATFORM_PORT_PGBOUNCER)
- Coupling mechanism: project_registry already owns DEFAULT_PROJECTS_ROOT; DSN parts re-derived
- Why dangerous: projects-root relocation or pooler port change desyncs scaffolder output
- Evidence: listed lines
- Scenario: low likelihood pre-launch
- Impact: developer-facing
- Minimal decoupling: import registry constant; use generated port value
- Code churn: S · Regression risk: LOW · Phase: Post-launch

Positive finding: deploy-critical path (receive → DeployOrchestrator, forced-command channel, docker_orchestrator, phases/docker.py, provisioner) correctly uses docker_ops/subprocess_io/ssh_opts facades — facade discipline holds where it matters most; violations concentrated in scaffold/loadtest/healthcheck edges.
