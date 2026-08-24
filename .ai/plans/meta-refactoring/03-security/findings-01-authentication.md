# Findings 01 — Authentication

## SEC-0001 — authorized_keys lifecycle is append-only: rotated/leaked keys remain valid forever
- **Severity:** MEDIUM · **Attack surface:** node SSH auth for root/platform/ci-deploy · **Confidence:** 0.9 · **Must fix:** NO (before first real rotation)
- **Files:** `core/internal/bootstrap/lifecycle/helpers/users.py:269-270` (comment admits: reconcile НЕ удаляет — вручную), `:293-320`, `:322-324`; `lifecycle/phases/system.py:740-758`
- **Symbols:** `add_ssh_key()`, `phase_user_accounts()`
- **Preconditions:** any key ever written (rotated owner key, replaced CI_DEPLOY_KEY, incident-response manual key); state.json has no key inventory.
- **Attack path:** rotate VPS_SSH_KEY per runbook → new key appended; human step "remove old key" skipped → old private key (GitHub Secrets history, laptops, 30-day windows) authenticates indefinitely; owner entries on `platform` are unrestricted-shell lines.
- **Impact:** stale credentials accumulate silently across rotations; S4/S7 posture stays green.
- **Evidence:** `add_ssh_key` loop has skip/rewrite paths only; new keys `open("a")`; S7 scope covers ci-deploy keys only (`deploy_channel_posture.py:42`).
- **Minimal fix:** reconcile-to-desired-state in φ2 (rebuild each user's file from node.yaml + PLATFORM_CI_ROOT_KEY + explicit extras; removed lines → dated quarantine); extend S-check to diff live vs desired.
- **Regression test:** authorized_keys contains key A; rotate env to key B → post-φ2 file contains exactly B; A quarantined, not appended.

## SEC-0002 — sshd hardening: `KbdInteractiveAuthentication` neither pinned nor checked — password login can silently re-enable while S4 reports PASS
- **Severity:** MEDIUM · **Attack surface:** node SSH auth · **Confidence:** 0.75 · **Must fix before launch: YES**
- **Files:** `core/internal/bootstrap/security/sshd_policy.py:314-329` (drop-in lacks kbd-interactive/challenge directives), `:87-108` (assumption documented: «PasswordAuthentication=no уже закрывает» — false with kbd-int+PAM), `:473-477` (cloud-init neutralizer matches only literal `50-cloud-init.conf`), `:486-487` (rename failure → WARN only); `lifecycle/phases/system.py:498-518` (apply is best-effort non-fatal)
- **Symbols:** `desired_ssh_hardening_dropin()`, `apply_sshd_dropin()`, `check_sshd()`
- **Preconditions:** vendor drop-in enabling kbd-int sorting before `99-platform-*`, or failed rename/reload during φ1 — exactly the environments bootstrap targets.
- **Attack path:** effective config allows password/kbd-interactive for AllowUsers; `check_sshd` parses `sshd -T` without these rows → PASS. Strongest claimed invariant («root только по ключу») false with zero signal.
- **Minimal fix:** add `KbdInteractiveAuthentication no` + `ChallengeResponseAuthentication no` (+ `MaxAuthTries 3`) to drop-in and `_SSHD_EXTRA_DIRECTIVES`; neutralize any `*cloud*` sshd_config.d file; make apply failure blocking or re-checked in later phases.
- **Regression test:** feed `parse_sshd_effective_config` output with `kbdinteractiveauthentication yes` → `check_sshd` FAILs; template asserts both directives present.

## SEC-0003 — LiteLLM project virtual keys persisted as plaintext JSON in tmpdir; chmod-after-write window; never expires
- **Severity:** MEDIUM · **Attack surface:** bearer creds for shared LLM gateway (all projects' spend) · **Confidence:** 0.85 · **Must fix:** NO (scheduled debt; do atomic-write swap now)
- **Files:** `core/internal/llm/key_provisioner.py:331-341` (gettempdir fallback `/var/tmp/litellm-project-keys.json`), `:391-399` (`open("w") → json.dump → chmod(0600)` after), docstring «stub — SOPS integration planned Wave 6/B»; `phases/docker.py:551-563` (φ11 invokes without `--persist`)
- **Preconditions:** local read access or crash between open and chmod; keys have no expiry.
- **Attack path:** node-update → φ11 dumps JSON to `/tmp` on node → any later image/backup/forensic capture yields live gateway tokens.
- **Minimal fix:** `atomic_writer.atomic_write(mode=0o600)`; remove tmpdir fallback (fail-fast on missing PLATFORM_STATE_DIR); delete-on-success; add `expires`.
- **Regression test:** mocked raise mid-dump → no plaintext residue; unset PLATFORM_STATE_DIR raises instead of resolving /var/tmp.

## SEC-0004 — nginx stub_status ACL is decorative east-west: every project container passes
- **Severity:** LOW · **Confidence:** 0.8 · **Must fix:** NO
- **Files:** `core/modules/nginx/config/nginx.conf:155-165` (`listen 8081` all interfaces; `allow 127.0.0.1; ::1; 172.0.0.0/8; deny all`)
- **Preconditions:** foothold in any container on proxy-net (every project joins it).
- **Attack path:** compromised tenant container → `GET http://nginx:8081/stub_status` — bridge IP ∈ 172.0.0.0/8 → served; live traffic recon the `deny all` was meant to prevent.
- **Minimal fix:** allow only the exporter subnet; correct CIDR to 172.16.0.0/12 (see SEC-0036 CIDR split).

## SEC-0005 — Brute-force posture: no fail2ban/sshguard; `MaxAuthTries` at default 6
- **Severity:** LOW · **Confidence:** 0.9 (verified absence) · **Must fix:** NO
- **Files:** rg `fail2ban|MaxAuthTries` → single neutralizer hit (`sshd_policy.py:474`); firewall baseline 22/80/443 no rate rule (`firewall.py:107,268-277`)
- **Evidence:** controls = pubkey-only + AllowUsers×3 + MaxStartups (concurrency, not retries); htpasswd surfaces throttled by generic limit_req 10 r/s/IP.
- **Impact:** acceptable at launch (random autogen passwords, pubkey-only); log noise only.
- **Minimal fix:** fold `MaxAuthTries 3` into SEC-0002's drop-in.

Checked-and-clean: LiteLLM master key fails-fast required (no empty-key window, loopback-only publish); minioadmin confined to dev defaults with runtime gate; seeded users get independent random autogen passwords; pg_hba trust strictly container-local (scram-sha-256 for RFC1918, md5 removed); pgbouncer admin console scram-gated; virtual keys carry model scoping+budgets, master-key return disabled; forced-command coverage enforced by S7; no debug/pprof routes on status-page; Docker API over TCP forbidden by ufw+validation; weak-KEX/MAC denylists verified against `sshd -T`.
