# Findings 003 — Exception swallowing, resilience asymmetry
# Wave 1 · agent: exception-swallow

## AI-0010 [MEDIUM·ACTIVE-conditional] [exception-swallow] VERIFIED-CORRECTED
Files: core/internal/bootstrap/lifecycle/phases/docker.py:558-562; core/internal/llm/key_provisioner.py:626,657,665-672,811; admin_client.py:254,307
Symbols: _registry_step_llm_provision, provision_all, main
Evidence (verifier): claimed broad-except→exit-0 mechanism REFUTED — httpx errors are NOT subclasses of the caught tuple, they propagate and main() returns 1. Silent outcome is REAL at a different layer: phase runs `bash provision-llm.sh` with non_fatal=True ⇒ bootstrap continues despite total key-provision failure; summary conflates failures with legitimate "skipped" (no failure count/metric).
Scenario: LiteLLM down during φ11 ⇒ exit 1 logged as best-effort, bootstrap completes green; projects hit runtime 401s with no breadcrumb.
Why AI-pattern: best-effort wrapping around primary side effect without surfacing failure counts upward.
Minimal cleanup: parse provisioner exit/failure-count in phase; WARN→ERROR summary line + bootstrap report entry when provisioned<expected.
Code churn: ~20 lines. Pre-launch: yes (bootstrap observability). Confidence: high.

## AI-0011 [HIGH] [exception-swallow]
Files: core/internal/bootstrap/deploy/deploy_orchestrator.py:688-693
Symbols: _deploy_sequential (env_check block)
Evidence: `except Exception as exc:` → warning → `missing=[]`.
Problem: crash inside secrets_validator.check_env_requires becomes "no vars missing" — validator failure indistinguishable from pass; module deploys without required secrets.
Why AI-pattern: blanket except converting check-crash into check-pass.
Minimal cleanup: distinguish validator-error from missing-vars; abort or mark DEPLOY_BEST_EFFORT explicitly.
Code churn: ~15 lines. Pre-launch: yes.
Confidence: high.

## AI-0012 [MEDIUM] [timeout-drift]
Files: core/internal/bootstrap/lifecycle/helpers/reporting.py:137-138; shared/module_interface.py:67,83; core/internal/healthcheck/modules_healthcheck.py:177
Symbols: run_healthchecks, invoke
Evidence: same op `<mod> healthcheck liveness` gets timeout 30 / COMPOSE_UP_TIMEOUT=180 / DOCKER_CMD_TIMEOUT=10 depending on caller; SoT HEALTHCHECK_* unused.
Problem: verdict depends on code path: slow-starting module fails lifecycle check but passes standalone.
Minimal cleanup: single HEALTHCHECK_CMD_TIMEOUT in timeouts.py used everywhere.
Code churn: <20 lines. Pre-launch: yes.
Confidence: high.

## AI-0013 [MEDIUM] [missing-timeout]
Files: core/internal/deploy/context_promoter.py:171-179
Symbols: promote_via_ssh
Evidence: `git push --mirror` / `ls-remote` with check=True, no timeout=; SSH ConnectTimeout bounds connect only.
Problem: stalled network during release-critical context-promote hangs indefinitely; sibling channels enforce DEPLOY_TIMEOUT.
Minimal cleanup: wrap with DEPLOY_TIMEOUT.
Code churn: <10 lines. Pre-launch: yes (release path).
Confidence: high.

## AI-0014 [MEDIUM] [missing-timeout]
Files: core/internal/shared/docker_auth.py:117,194
Symbols: docker_login, ghcr_login
Evidence: subprocess.run([... "--password-stdin"], input=token, no timeout=).
Problem: unreachable registry during bootstrap φ3/φ6 hangs whole state machine (no outer watchdog).
Minimal cleanup: DOCKER_CMD_TIMEOUT.
Code churn: <10 lines. Pre-launch: yes.
Confidence: high.

## AI-0015 [MEDIUM] [retry-asymmetry]
Files: core/internal/bootstrap/lifecycle/helpers/reporting.py:157-162
Symbols: run_healthchecks
Evidence: FileNotFoundError("bash") retried hc_max_retries×hc_retry_interval (~100s/module); no retryable predicate unlike shared/retry users.
Problem: permanent error burned through retry budget; transient-vs-permanent conflated.
Minimal cleanup: exclude permanent errors from retry loop.
Code churn: <10 lines. Post-launch acceptable; trivial fix.
Confidence: high.

## AI-0016 [LOW] [exception-swallow]
Files: core/internal/bootstrap/deploy/parallel_runner.py:96-100
Symbols: pull_module_images
Evidence: compose read OSError → pass → proceeds to retry_pull of nonexistent image.
Problem: misleading "pull failed" after burning [5,10,20] retries instead of surfacing perms/read error.
Minimal cleanup: log-and-treat-as-needs-build on OSError.
Code churn: <10 lines.
Confidence: high.

## AI-0017 [LOW] [missing-timeout + swallow-by-design]
Files: core/internal/scaffold/github_ops.py:47,75,90
Symbols: create_github_repo
Evidence: gh/git subprocess without timeout; contract "never raises — return True".
Problem: hung network stalls new-project forever; total failure still reported success (compounds AI-0037 doc contradiction).
Minimal cleanup: timeout + return False on failure.
Code churn: <15 lines.
Confidence: high.

## AI-0018 [LOW] [missing-timeout]
Files: core/modules/nginx/dev_cert_generator.py:190,267,319,412
Symbols: openssl/certbot invocations
Evidence: four unbounded subprocess.run; siblings bounded by DEFAULT_OPENSSL_TIMEOUT=10.
Minimal cleanup: reuse ssl_certs timeout constant.
Code churn: <10 lines.
Confidence: med.

## AI-0019 [LOW] [exception-swallow]
Files: core/modules/platform-secrets/installer.py:172-173,284
Symbols: _plw_body_ensure_platform_dirs
Evidence: contextlib.suppress(OSError, TimeoutExpired) around chown; chmod succeeds so prereq reports True.
Problem: silent root-owned dirs surface much later as service write failures far from cause.
Minimal cleanup: log warning on suppress; include chown rc in prereq result.
Code churn: <10 lines.
Confidence: med.
