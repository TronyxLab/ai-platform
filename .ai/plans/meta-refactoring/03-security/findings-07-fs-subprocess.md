# Findings 07 — Filesystem / subprocess safety

Checked-and-clean (verified): tar extraction single filter="data" discipline everywhere (PEP 706 strips setuid/setgid); canonical atomic_writer correct (random NamedTemporaryFile in target dir, chmod-before-replace, os.replace does not follow symlinked destination) with audited consumers; zero tempfile.mktemp in core; AGE temp key tmpfs 0600+wipe; no shell=True/os.system/f-string command strings in core Python; timeouts pervasive via SoT; rsync --delete scopes pinned to dedicated subdirs; delivery-channel quoting clean; check-cache in git-dir is dev-machine single-user; receive staging copy dereferences payload symlinks via copy2 (no traversal smuggling), backup dir mkdtemp 0700 removed in finally.

## SEC-0029 — Root follows pre-planted symlink when writing bootstrap stub compose into ci-deploy-owned project dir
- **Severity:** HIGH · **Attack surface:** bootstrap φ8/φ12 + `make deploy-context` (root) writing into `/opt/projects/<name>` owned by ci-deploy (`users.py:345`) · **Confidence:** 0.85 · **Must fix before launch: YES** (one-line-class fix)
- **Files:** `core/internal/bootstrap/deploy/context_deployer.py:559-561` (`path_isfile()` guard FOLLOWS symlinks; dangling link → False → proceeds), `:623-624` (plain `open("w")` — no O_NOFOLLOW, not atomic, follows symlinks)
- **Symbols:** `_ensure_bootstrap_compose`
- **Preconditions:** attacker executes code as ci-deploy (the explicit C1 threat-model actor, receive_flow.py:269-271) at any moment between φ2 and next root-side redeploy.
- **Attack path:** `ln -s /opt/node-configs/<node>/node.yaml /opt/projects/victim/docker-compose.yml` (dangling) → routine bootstrap/node-update → root `open("w")` follows the link and overwrites the attacker-chosen absolute path with stub content → repeatable arbitrary-root file overwrite (node.yaml, core files, cron fragments — many targets only need replacement, not crafting).
- **Impact:** root file-write primitive in normal operational flows; violates the canonical-writer invariant everything else uses.
- **Minimal fix:** replace with `shared/atomic_writer.atomic_write` (rename commits through, replaces rather than follows a symlinked dest) + refuse on `os.path.islink`.
- **Regression test:** tmp project dir, docker-compose.yml → external sentinel symlink; run `_ensure_bootstrap_compose`; assert sentinel untouched, stub at real path, warning logged.

## SEC-0030 — Privileged compose execution bypasses the L1 pre-deploy gate on all non-receive channels (gate-placement bug)
- **Severity:** HIGH · **Attack surface:** root-side redeploys: bootstrap φ8/φ12, deploy-context, orchestrator-mode module deploy — executing ci-deploy-mutable compose files · **Confidence:** 0.8 · **Must fix before launch: YES**
- **Files:** gate lives ONLY in receive: `receive_flow.py:408-420`; `DeployOrchestrator.deploy` (`orchestrator.py:213-287`: lock → prepare → apply) has NO verify_project_contracts call (grep: references only in receive_flow.py:86,223 + manual CLI :420-431); executed with cwd=ci-deploy-owned dir (`shared/docker_compose.py:136`, `engine/engine.py:191`)
- **Attack path:** one legit gated deploy lands healthy → ci-deploy overwrites docker-compose.yml post-gate (`privileged: true` or host binds — see SEC-0011 class) → project goes unhealthy OR operator re-runs φ12/deploy-context → root executes tampered compose **ungated**.
- **Evidence asymmetry:** unprivileged receive channel is gated twice; every privileged execution path zero times — inverts the intent of DevPlan 176 A.2 ("containers NOT started" claim reopened).
- **Minimal fix:** invoke `verify_project_contracts(dir, l1_only=True)` inside `DeployOrchestrator.deploy` before `_apply_deploy` so ALL channels inherit the gate.
- **Regression test:** pass gate on receive; mutate target compose to privileged:true; run deploy_context_projects → block + audit event.

## SEC-0031 — SCPChannel executes payload-directory script as root; receive channel plants arbitrary top-level files (no server-side whitelist)
- **Severity:** MEDIUM · **Confidence:** 0.7 · **Must fix:** NO (legacy flag trigger; two-line hardening worth taking now)
- **Files:** exec sink `deploy/channels/scp.py:196-204` (`ssh … "{remote_dir}/{project}/remote-cmd.sh unpack {project}"` as root@default; result intentionally ignored); planting sink `receive_flow.py:424` copies ALL top-level staging files (`iterdir() if p.is_file()` — no whitelist) while sender canon is `payload_deliverer.py:72-84` WHITELIST_FILES
- **Preconditions:** malicious/compromised payload adds top-level remote-cmd.sh → persisted by receive → someone later runs legacy `--scp` delivery (DR/migration fallback) → attacker code as root. Unwhitelisted files also silently become platform-consumed state.
- **Minimal fix:** delete `_remote_unpack` (unpack happens VPS-side by receive today) or point at root-owned platform path; enforce WHITELIST_FILES in ReceiveFlow copy loop mirroring sender canon.
- **Regression test:** R5-negative: payload with remote-cmd.sh + evil.txt → after ReceiveFlow.run only whitelisted names exist in target dir.

## SEC-0032 — reboot_policy fixed-name temp `{state}.tmp`, plain open+replace — torn-write race, latent symlink hazard
- **Severity:** LOW-MED · **Confidence:** 0.9 facts / 0.3 exploitability · **Must fix:** NO
- **Files:** `core/internal/bootstrap/reboot_policy.py:222-226,421` vs canon atomic_writer; dir currently root-only
- **Impact:** concurrent writers (cron overlap) can publish torn JSON to load_state readers skewing reboot decisions; becomes SEC-0029-class symlink-follow write if state dir relocates to shared storage.
- **Minimal fix:** `atomic_write_json`. (Same bypass family as ARCH-0040.)

## SEC-0033 — Node-name regex accepts `-foo`/hyphen-only; node names flow unquoted into remote mkdir/rsync paths
- **Severity:** LOW (self-inflicted vectors only) · **Confidence:** 0.85 · **Must fix:** NO
- **Files:** `setup_node.py:82` (`^[a-zA-Z0-9_-]+$` allows leading `-`/all-hyphen; contrast `_CONTEXT_NAME_RE` context_overlay.py:73 requiring leading alnum); sinks `core_deliverer.py:192`, `overlay_deliverer.py:269,283`
- **Preconditions:** hostile directory name in operator-managed configs repo — not attacker-reachable; validate_node_name enforced only in sudoers setup (:299), nowhere on delivery path.
- **Minimal fix:** reuse leading-alnum regex; quote remote path args for T9.7 parity.
- **Regression test:** extend test_validate_node_name_rejects_path_injection with `-foo`, `-`, `--`.
