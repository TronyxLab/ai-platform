# Findings 02 — Dependency direction

Baseline: `.importlinter` contracts verified green (`forbidden-deploy-bootstrap`, `acyclic-internal-domains` with empty ignore list, `layers-core`). Findings are gaps the contracts don't cover.

## ARCH-0006 — `PRIVOXY_PORT` SoT lives in the orchestration layer; a parity gate *forces* upward imports out of leaf domains
- **Severity:** P2 · **Confidence:** 0.95 · **Churn:** M · **Phase:** pre-launch
- **Files:** `core/internal/bootstrap/firewall.py:118` (self-declared SoT) · `core/internal/healthcheck/tor_proxy_check.py:36-37` (imports it upward) · `tests/gates/test_gate_port_parity.py:26,52` (`_PRIVOXY_ALLOWLIST = {FIREWALL.resolve()}`) · acknowledged debt TRAP at `bootstrap/docker_user_policy.py:74-76`
- **Symbols:** `PRIVOXY_PORT = 8118`; consumers `privoxy_config.py:40`, `install_tor_proxy.py:88`, `lifecycle/cli.py:50`, `lifecycle/helpers/reporting.py:39`
- **Evidence:** healthcheck (leaf/monitoring domain) imports bootstrap (top orchestrator); no importlinter contract forbids it. Worse, the parity gate bans literal `8118` everywhere except `firewall.py` — **the gate cements the wrong placement**, compelling every future consumer to import upward.
- **Failure scenario:** next consumer needing the privoxy port adds a bootstrap import; monitoring-domain code transitively couples to iptables/firewall refactors; firewall extraction breaks on-node watchdog.
- **Impact:** layer erosion of the domain that runs inside node cron contexts.
- **Minimal fix:** move `PRIVOXY_PORT` → `shared/platform_ports.py` (its declared role), repoint allowlist + 5 consumers; resolves the existing TRAP.

## ARCH-0007 — Internal→modules dotted Python import bypasses the `invoke_module_interface` typed contract
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S · **Phase:** pre-launch
- **Files:** `core/internal/dev_hosts.py:71` — `from core.modules.nginx.dev_cert_generator import DEFAULT_DEV_CERTS_DIR, get_cert_sans`
- **Evidence:** contract (`core/AGENTS.md` §Cross-layer rules): internal→modules only via registered interface. Enforcement gap: `.importlinter` layers permit internal→modules; `cross_layer_linter.py` flags direct calls only for `.sh` (line 190) — `.py` dotted imports unscanned. Sole instance in tree (rg verified). Also stacks a second sibling dep (`scaffold.vhost_renderer`, line 70).
- **Scenario:** nginx module refactor moves `dev_cert_generator` → `make dev-hosts` breaks at runtime with no gate signal.
- **Minimal fix:** move the two helpers into `shared/` or `scaffold/`; or register a module interface.

## ARCH-0008 — CI calls unregistered internal scripts directly, bypassing the Make facade
- **Severity:** P3 · **Confidence:** 0.8 · **Churn:** S · **Phase:** post-launch (hygiene)
- **Files:** `.github/workflows/platform-test.yml:151-153` (`python3 core/internal/scripts/validate_dora_dashboard.py`), `:231` (`python3 core/internal/scripts/module_discovery.py --format lines`)
- **Evidence:** zero registration hits across `Makefile`, `makefiles/*`, `entrypoint-manifest.yaml`, `check-suite.yaml` — violates invariant 1 (Makefile единый фасад) + invariant 5 (manifest as CI-gate registry). Contrast: `core-deploy.yml:127` correctly uses `python3 -m core.internal.shared.ssh_opts --shell`.
- **Scenario:** script CLI signature changes → platform-test breaks with no local reproduction verb (`make <verb>` doesn't exist); no runs.jsonl audit trail for these calls.
- **Minimal fix:** wrap in internal make targets or register in `check-suite.yaml`; workflows call make.

See also ARCH-0002 in findings-01 (shared leaf breach — direction violation tracked there).
