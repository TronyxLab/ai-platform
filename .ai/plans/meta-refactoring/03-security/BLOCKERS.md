# BLOCKERS — Real Launch Blockers

Criteria applied strictly: vulnerability reachable in the **default deployment** (no exotic preconditions),
confidence ≥0.75 verified by code reading, material impact on confidentiality/integrity/availability of a
multi-project production node. Generic hardening, single-operator accepted risks, and pre-first-external-tenant
items are deliberately **excluded** (see «Not blockers» below). 49 total findings → 18 blockers.

## The core chain (why these block launch)

Any holder of ONE project repo (`CI_DEPLOY_KEY` exists ×N repos) reaches host root through three independent
doors: compose volumes bypass (B1), ungated redeploy channels (B2), symlink/root-write primitives (B4/B5).
Root then reads the AGE master key out of process cmdlines (B6) → every secret on the node. Fix order matters:
B1+B2 close the doors; B6 removes the crown-jewel exposure even for insiders.

| # | ID | Blocker | Sev | Fix cost |
|---|----|---------|-----|----------|
| B1 | SEC-0011 | L1 gate ignores `volumes:`/host-modes → docker.sock or `/` bind = node root from any project commit | CRITICAL | S–M |
| B2 | SEC-0030 | Gate lives only in `receive`; bootstrap φ8/φ12 + deploy-context execute ci-deploy-mutable compose **ungated** as root | HIGH | S |
| B3 | SEC-0006 | No credential↔project binding: any CI key receives/removes/statuses ANY project | HIGH | M |
| B4 | SEC-0029 | `_ensure_bootstrap_compose` follows pre-planted symlinks → arbitrary root file overwrite in routine flows | HIGH | S |
| B5 | SEC-0026 | `needs.domain` unvalidated → root path traversal in cert pipeline + shell-string reloadcmd (conditional persistent RCE) | HIGH | S |
| B6 | SEC-0015 | AGE master key + CI deploy private key in ssh argv (`/proc` readable by any local account) and in deliver_fallback logs | HIGH | M |
| B7 | SEC-0016 | `secrets.env.tmp` written 0644-before-chmod, no crash cleanup → permanent world-readable copy of all secrets | HIGH | S |
| B8 | SEC-0017 | `.env.platform` regenerated with live tenant DB password at umask 0644 root-owned | MED-HIGH | S |
| B9 | SEC-0014 | sudoers `node-lifecycle.sh` accepts arbitrary args → `--ci-root-key` injects durable root SSH backdoor from platform account | MED-HIGH | S |
| B10 | SEC-0038 | All 22 external GHA actions pinned to mutable tags (incl. third-party trivy-action) — tag hijack = root-deploy secrets | HIGH | S |
| B11 | SEC-0045 | Ingress has no limit_conn and no client timeouts → ~2–4k slowloris sockets kill EVERY vhost on the node | HIGH | S |
| B12 | SEC-0046 | Receive channel buffers 1 GiB RAM/receive and extracts without expansion cap into /tmp (same fs as postgres) → bomb = node outage | HIGH | S–M |
| B13 | SEC-0034 | LiteLLM/Langfuse/MinIO attached to shared-db-net — documented hermes-agent-net isolation does not exist; tenants reach backup+trace stores | HIGH | S–M |
| B14 | SEC-0047 | Tenant limits checked for presence only — `memory: 32G` passes L1 → legitimate-looking config OOMs platform modules | MED-HIGH | S |
| B15 | SEC-0018 | Nightly pg_dumpall of ALL tenant DBs uploaded to third-party S3 unencrypted client-side | MED-HIGH | S |
| B16 | SEC-0020 | Langfuse traces (prompts/completions, end-user PII) retained indefinitely — no CH TTL, no S3 lifecycle | HIGH | S–M |
| B17 | SEC-0039 | setup-gitleaks downloads binary with no checksum — trojaned release silently disables the leak scanner | MED | XS |
| B18 | SEC-0002 | sshd drop-in lacks KbdInteractiveAuthentication=no and apply is best-effort — password login can silently re-enable while check-security PASSes | MED | XS |

## Per-blocker minimal fix (one line each)

- **B1:** add `_check_dangerous_volumes` + deny-keys (network_mode:host, pid, userns_mode, cgroup*, sysctls) to verify_contracts L1; R5-negative with exact C1 input.
- **B2:** call `verify_project_contracts(dir, l1_only=True)` inside `DeployOrchestrator.deploy` before `_apply_deploy`.
- **B3:** per-project authorized_keys lines with `environment="PLATFORM_ALLOWED_PROJECT=<n>"`, enforced in dispatch/validate.
- **B4:** replace plain `open("w")` with `atomic_writer.atomic_write` + islink refusal.
- **B5:** apply existing FQDN validator at register_project AND orchestrate_certs entry; shlex.quote/env-pass reloadcmd domain.
- **B6:** deliver keys out-of-band (stdin to bash -s / SetEnv / SCP'd 0600 root file); redact deliver_fallback log; hidepid=2.
- **B7:** `atomic_write(mode=0o600)` for both secrets_manager tmp writers; umask 077 belt-and-braces.
- **B8:** `atomic_write_text(mode=0o640)` + chown ci-deploy in gen_env_platform; post-copy chmod in receive_flow.
- **B9:** pin sudoers args (`--mode init` / `--mode update`) or root-owned flag-whitelisting launcher.
- **B10:** pin all actions to commit SHAs; start with trivy-action/codeql-action.
- **B11:** limit_conn_zone/perip 20 + client_header/body/send timeouts + SSE read_timeout ≤300s.
- **B12:** stream-extract with uncompressed-byte ceiling (~200 MB) + entry count; lower payload default to ~64 MiB.
- **B13:** move langfuse/minio/litellm onto hermes-agent-net per platform-infra.yaml (or re-declare SoT + auth boundaries); fix PLATFORM_LANGFUSE_URL port.
- **B14:** L1/L2 value bounds (memory ≤ ~25% MemTotal, cpus ≤ 2), [PRACTICES:BLOCK] above.
- **B15:** gzip → sops/age encrypt → upload .enc; restore gains one decrypt step.
- **B16:** TTL on langfuse ClickHouse tables + S3 event-upload retention envs; document trace-retention policy.
- **B17:** embed expected SHA256 per gitleaks version; sha256sum -c before extraction.
- **B18:** add KbdInteractiveAuthentication no + ChallengeResponseAuthentication no (+MaxAuthTries 3) to drop-in and _SSHD_EXTRA_DIRECTIVES; neutralize any *cloud* sshd_config.d; make apply failure loud.

## Not blockers (explicitly judged, with conditions)

- **SEC-0007 Redis no AUTH** — blocker only when ≥2 mutually distrusting tenants share a node; document flat-trust until then. Fix before external tenants.
- **SEC-0009 pull_request_target PR-head execution** — attacker set limited to existing collaborators while repo is private; must be split before public/forkable visibility.
- **SEC-0035 privoxy Tor open proxy for tenants** — external attack mitigated (ufw default-deny); deny project networks before untrusted tenants.
- **SEC-0008 Postgres PUBLIC CONNECT** — object grants protect data today; REVOKE CONNECT before distrusting tenants.
- **SEC-0013 ci-deploy docker group** — documented risk acceptance (TRAP); residual stated, socket-proxy roadmap item.
- **SEC-0021 shared basic-auth cred, SEC-0037 test port publish, SEC-0001 key rotation tooling, SEC-0048/49 disk-fill paths** — tracked, non-blocking with existing monitoring.
