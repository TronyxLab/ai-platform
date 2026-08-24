# Findings 03 — Privilege escalation

## SEC-0011 — L1 capability gate is bypassable: `volumes:` (docker.sock, `/`, `/etc`), `network_mode: host`, `pid`, `sysctls`, `userns_mode` never inspected for project compose
- **Severity:** CRITICAL · **Attack surface:** project deploy channel (`receive`) — the only privilege gate between a project maintainer and host root · **Confidence:** 0.95 · **Must fix before launch: YES**
- **Files:** `core/internal/deploy/verify_contracts.py:286-294` (L1 set = secrets/ports/healthcheck/networks/env-file/labels/limits + only `_check_privileged`:648, `_check_cap_add`:680, `_check_devices`:711); same set pre-up in `receive_flow.py:408-420`; in-repo admission TRAP `verify_contracts.py:637` («новые капабилити-векторы compose (pid:, sysctls, userns_mode) — в R5-negative-набор» — not implemented); grep: zero volume/socket/pid/sysctls inspection in verify_contracts/receive_flow/compose_preflight/practices
- **Preconditions:** control of any project repo wired to deploy-project.yml. No platform privileges needed.
- **Attack path:** commit compose with `volumes: ["/var/run/docker.sock:/var/run/docker.sock"]` or `/:/host` (no privileged/cap_add/devices) → push → CI `receive` → gate passes (no contract inspects volumes) → compose up as ci-deploy (docker group) → docker.sock = full API = privileged sibling → node root; or host-bind `/` rw → chroot.
- **Impact:** single payload commit = root on multi-tenant node + all secrets (`/opt/platform/secrets`, all `.platform-db.env`). Defeats the stated purpose of C1 («единственная реальная root-эскалация закрыта», verify_contracts.py:46-47).
- **Minimal fix:** add `_check_dangerous_volumes` (deny socket mounts and absolute host binds outside allowlist; require named volumes), deny-keys `network_mode: host`, `pid`, `userns_mode`, `cgroup*`, `sysctls`; wire into both l1_only and K3; R5-negative tests with the exact C1 input.
- **Regression test:** gate test feeding compose with docker.sock bind + `/opt/projects:ro` mount asserting blocking violation; positive named-volume pass.

## SEC-0012 — Docker socket mounted into log-collector/alloy — `:ro` is not a privilege reduction; cAdvisor holds SYS_ADMIN + host fs mounts
- **Severity:** HIGH · **Attack surface:** container→host boundary on every node · **Confidence:** 0.9 · **Must fix:** NO (conscious keep-list W2-5, but stated mitigation technically void — reopen)
- **Files:** `core/modules/log-collector/docker-compose.base.yml:78` (`docker.sock:ro`), `:19` (invariant claims :ro suffices); `core/modules/logging/docker-compose.base.yml:134`; `core/modules/infra-metrics/docker-compose.base.yml:68-78` (cAdvisor cap_add SYS_ADMIN,DAC_READ_SEARCH + `/:/rootfs:ro`, `/var/lib/docker:ro`); hermes removed its socket explicitly as «host-root вектор» (:169) — precedent
- **Preconditions:** RCE inside alloy/cAdvisor container (image/parser compromise); alloy runs as root (no user: directive).
- **Attack path:** read-only *bind-mount flag* restricts filesystem writes to the mountpoint, NOT Docker API verbs: `POST /containers/create` with privileged+binds works identically over a `:ro` socket → host root.
- **Minimal fix:** docker-socket-proxy allowing only containers/events GETs on dedicated net; S-check/gate asserting no docker.sock mount outside the proxy allowlist; register cAdvisor in the promised gated-allowlist registry (verify_contracts.py:26 — currently comment-only).
- **Regression test:** static gate scanning all compose files for docker.sock bind sources vs one-entry allowlist.

## SEC-0013 — ci-deploy holds `docker` group (= root-equivalent); L1 regime is advisory against this principal + TOCTOU window
- **Severity:** HIGH · **Confidence:** 0.9 · **Must fix:** NO (documented risk-acceptance TRAP setup_node.py:47-57; residual must be stated)
- **Files:** `lifecycle/phases/system.py:723` (`create_user("platform", ["docker"])`), `:631` (`ci-deploy ["docker","platform"]`); gate→exec race window `receive_flow.py:408-420` → orchestrator.deploy re-reads ci-deploy-mutable staging dir
- **Attack path:** any code execution as ci-deploy (one compromised project CI runner) → direct `docker run -v /:/host alpine chroot /host` on the socket → root; forced-command restrict irrelevant once any ci-deploy-owned process runs locally. TOCTOU: same-user racing process swaps validated file before compose up.
- **Minimal fix:** rootless mode / socket-proxy for project deploys; userns-remap for tenant containers; hash-pin staged compose at exec time.
- **Regression test:** chaos test asserting ci-deploy cannot create privileged/host-bind container via allowed API surface.

## SEC-0014 — sudoers entry for `node-lifecycle.sh` has unrestricted arguments → persistent root SSH-key injection
- **Severity:** MED-HIGH · **Confidence:** 0.85 · **Must fix before launch: YES** (one-line arg pinning)
- **Files:** `core/internal/bootstrap/setup_node.py:176` (`platform ALL=(root) NOPASSWD: …/node-lifecycle.sh` — no argument spec); `lifecycle/cli.py:186-190` accepts `--ci-root-key/--owner-key/--state-file/--run-phase`; sink `lifecycle/helpers/users.py:751-758` writes attacker pubkey into `/root/.ssh/authorized_keys`
- **Preconditions:** shell as `platform` user (owner-key holder). Converts platform-account compromise into durable root.
- **Attack path:** `sudo node-lifecycle.sh --run-phase user_accounts --ci-root-key 'ssh-ed25519 AAAA…attacker'` → φ2 injects key into root authorized_keys → reboot/redesign-surviving backdoor. Secondary: `--state-file <path>` = arbitrary-path JSON write as root.
- **Evidence:** design TRAP (setup_node.py:47-57) considered the symlink vector but not argument injection. Contrast: make-rules ARE pinned exact-argv (`sudoers_generator.py:211`).
- **Minimal fix:** pin args: two rules `--mode init` / `--mode update`, or root-owned launcher whitelisting flags and ignoring `--*-key`/`--state-file`.
- **Regression test:** extend sudoers-hardening gate asserting rendered rule contains explicit argument spec.

Checked-and-clean: generated make-sudoers rules tightly pinned `<user> ALL=(root) NOPASSWD: /usr/bin/make -C <abs-dir> <fixed-target>` (golden-tested, no editor/pager/env_keep vectors, visudo-validated 0440 atomic install); root crons/systemd execute only root-delivered code under /opt/platform/core (no writable-between-delivery-exec window found); no curl|sh in remote chain; acme reloadcmd `$Le_Domain` double-eval RCE rejected (single-pass expansion); hermes-agent non-root USER 10000; project payloads land in ci-deploy-owned dirs; group-writable artifacts under /opt consumed by nothing root-executed.

Cross-ref: unpinned root supply chain (sops binary download without checksum, acme.sh/dnsapi cloned at HEAD) recorded as SEC-0045-adjacent under findings-09 (supply-chain class).
