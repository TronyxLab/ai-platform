# Direction 2: retry/timeout — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit

Working tree dirty (audit as-is). Scope: core/internal/** (pollers, channels, shared/retry,
shared/timeouts), deploy-critical subprocess/ssh/git/docker calls, module hooks.

---

## BUG-0201 — HealthcheckPoller: nested budget multiplication turns documented 60s poll window into ~21 min

- Severity: HIGH
- Confidence: 90%
- File: core/internal/deploy/healthcheck_poller.py:143
- Symbol: `HealthcheckPoller.poll_until_healthy` → `poll_project` → `_try_docker` → `docker_compose.healthcheck_poll`
- Trigger: project deployed with `project_dir` whose containers exist but never become healthy
  (bad image, crash-looping app) — exactly the case the poller exists for.
- Execution path:
  trigger → `orchestrator.py:544` `poll_until_healthy(project_name, project_dir)` → outer loop
  `healthcheck_poller.py:143` runs `max_retries=20` attempts → each attempt's `_try_docker`
  (`healthcheck_poller.py:214`) delegates to `healthcheck_poll(timeout=self.timeout=60)` which has
  its OWN internal deadline loop (`docker_compose.py:558-560`, burns full 60s when containers are
  not healthy/absent) → per attempt ≈ 60s inner poll + HTTP budget (`:197`,
  `max(5, 60//6)=10s × 6 URLs`) + `sleep(3)` (`:156`, executed even after the last failed attempt)
  → total ≈ 20 × ~63–123s ≈ 21+ min, while the class invariant (`:91`) and the returned detail
  message (`:163`) claim a 60s window.
- Actual behavior: unhealthy project blocks the deploy phase ~21 min (per project); result reports
  `"Healthcheck timeout after 60s"`.
- Expected behavior: total wall time bounded by the declared window (interval × max_retries = 60s),
  or invariant/docstring updated to the real composite formula.
- Impact: CI/receive deploys of failing projects stall for tens of minutes per project;
  `deploy-context` over N projects multiplies this; monitoring/alerts keyed to "60s healthcheck
  window" misread timelines.
- Minimal fix: give `poll_until_healthy` one monotonic deadline covering HTTP+docker legs
  (deadline = start + max_retries×interval), pass remaining budget into `_try_docker`, skip final
  sleep after last attempt.
- Required regression test: `test_poll_until_healthy_total_window_bounded` — fake
  `healthcheck_poll` that always sleeps past its timeout and returns "unhealthy"; assert
  `poll_until_healthy` wall time ≤ max_retries×interval + ε and `result.detail` matches actual
  elapsed budget.

## BUG-0202 — ForcedCommandChannel 900s SSH budget < worst-case receive path → mid-deploy kill + 3× repeated delivery

- Severity: MEDIUM
- Confidence: 75%
- File: core/internal/deploy/channels/base.py:190
- Symbol: `DeliveryChannel._retry_deliver` / `ForcedCommandChannel._send_forced`
- Trigger: project deploy via forced-command where compose up + pull + healthcheck exceed 900s
  (cold pull or BUG-0201's 21-min poll).
- Execution path: trigger → `orchestrator.py:466` `channel._retry_deliver(payload)` →
  `forced.py:140-142` `self._run(ssh_cmd, stdin=tar_file, ..., timeout=self.timeout)` with
  `self.timeout = AppConfig.deploy_timeout = DEPLOY_TIMEOUT=900` (`base.py:127`,
  `timeouts.py:130`) → server-side receive needs up to PULL_TIMEOUT 300 + COMPOSE_UP_TIMEOUT 180
  + poller ~1260s (`timeouts.py:49,52`; BUG-0201) > 900s → `subprocess.TimeoutExpired` kills ssh
  mid-deploy → `_retry_deliver` (`base.py:190-196`) retries the WHOLE delivery 2 more times with
  backoff [5,10] → each retry repeats compose-up + full doomed poll.
- Actual behavior: ssh client killed at 900s while VPS-side deploy is mid-flight (rollback skipped);
  delivery retried 3× against the same node; exit 124 reported.
- Expected behavior: channel budget must dominate the worst-case server-side receive
  (compose+pull+healthcheck), or retries must be suppressed for timeout-class failures.
- Impact: duplicated half-applied deploys, misleading FAILED after 45 min of real work, load spike
  on the node during incident windows.
- Minimal fix: raise channel default above sum of server budgets (or derive it), and mark
  TimeoutExpired results non-retryable in `_retry_deliver`.
- Required regression test: `test_retry_deliver_timeout_not_retried` — fake deliver raising
  `TimeoutExpired`; assert `_attempt` converts it to `success=False` AND shared.retry receives a
  non-retryable predicate (exactly 1 call).

## BUG-0203 — module_interface.invoke timeout kills only bash; docker grandchildren survive as orphans

- Severity: MEDIUM
- Confidence: 75%
- File: core/internal/shared/module_interface.py:83
- Symbol: `invoke()` (deploy-hook / install / healthcheck dispatch)
- Trigger: any module interface invocation exceeding its timeout (default COMPOSE_UP_TIMEOUT=180s),
  e.g. nginx reload-hook or system-module install stuck on `docker exec`.
- Execution path: trigger → `module_interface.py:83`
  `subprocess.run(["bash","-c",bash_cmd], ... timeout=timeout)` → on expiry stdlib kills ONLY the
  direct child (bash) → grandchildren (`docker exec`, `docker compose up`) keep running detached →
  `:84-86` returns `(False, str(exc))` → caller logs WARN "hook failed" and continues pipeline while
  the orphan still mutates containers/vhosts concurrently.
- Actual behavior: hook reported failed at T, mutation completes at T+Δ unobserved; subsequent
  steps race with it.
- Expected behavior: timeout kills the whole process group (platform canon exists:
  `subprocess_io.py:245,250` — `Popen(start_new_session=True)` + `os.killpg(SIGKILL)`).
- Impact: post-deploy chain state divergence ("failed" hook actually succeeded later, or vice
  versa); flaky converge/monitoring renders.
- Minimal fix: route invoke through `run_subprocess_streaming`/killpg pattern (or
  `start_new_session=True` + manual killpg on TimeoutExpired).
- Required regression test: `test_invoke_timeout_kills_process_group` — bash spawns `sleep 300 &`
  child writing a sentinel file after 5s; invoke with timeout=1; assert process-group dead
  (no sentinel within grace period).

## BUG-0204 — context_promoter: release-critical `git push --mirror` has no timeout (probe is protected, payload phase is not)

- Severity: MEDIUM
- Confidence: 80%
- File: core/internal/deploy/context_promoter.py:171
- Symbol: `promote_via_ssh()` (also `ls-remote` at :179)
- Trigger: stalled connection to github.com (TCP accepted, transfer black-holed) during
  `make context-promote`.
- Execution path: trigger → `context-promote.sh` → `promote_via_ssh` → `:171-176`
  `subprocess.run(["git", "push", "--mirror", target], check=True, capture_output=True)` with no
  `timeout=` → git spawns system ssh WITHOUT the platform's SSH_OPTS (no BatchMode/ConnectTimeout/
  ServerAlive injected into git transport; contrast the probe `:127-132` which does use SSH_OPTS) →
  hangs indefinitely until external job timeout.
- Actual behavior: `make context-promote` blocks with zero deadline; release checklist step 4 stalls.
- Expected behavior: bounded push/fetch like every other network op (timeouts.py SoT has no git
  entry — add GIT_PUSH_TIMEOUT).
- Impact: operator/CI hang on the promotion step of the Triple-Delivery model; silent mirror drift.
- Minimal fix: `GIT_SSH_COMMAND="ssh <SSH_OPTS>"` env for git children + `timeout=` on both
  subprocess.run calls.
- Required regression test: `test_promote_via_ssh_bounded` — fake runner asserting `timeout` kwarg
  present and env contains BatchMode/ConnectTimeout flags for git push/ls-remote.

## BUG-0205 — docker_auth login: no timeout on registry round-trip in bootstrap φ3/φ6

- Severity: LOW
- Confidence: 70%
- File: core/internal/shared/docker_auth.py:117
- Symbol: `docker_login()` (and `ghcr_login()`, :194)
- Trigger: registry endpoint black-holed/slow TLS handshake during bootstrap or module preflight.
- Execution path: trigger → bootstrap φ3/φ6 (`docker_registry_auth`) or deploy-modules facade →
  `docker_auth.py:117-124`
  ```python
  result = subprocess.run(
      ["docker", "login", registry, "--username", username, "--password-stdin"],
      input=token,
      ...
      check=False,
  )
  ```
  → no `timeout=` → docker CLI waits indefinitely on daemon↔registry I/O → phase blocked.
- Actual behavior: bootstrap/deploy phase hangs with no deadline; state machine cannot advance.
- Expected behavior: bounded auth call (e.g. DOCKER_CMD_TIMEOUT-family value from timeouts.py).
- Impact: rare but total bootstrap stall requiring operator intervention.
- Minimal fix: add `timeout=` from shared/timeouts (new DOCKER_AUTH_TIMEOUT constant).
- Required regression test: `test_docker_login_has_timeout` — fake runner asserts `timeout` kwarg
  is not None for docker_login/ghcr_login.

## BUG-0206 — postgres hook auto_create_db: `docker exec psql CREATE DATABASE` without timeout in runner=None branch

- Severity: LOW
- Confidence: 75%
- File: core/modules/postgres/hooks/on_project_deploy.py:133
- Symbol: `auto_create_db()`
- Trigger: postgres container paused/stuck (disk full, lock wait) while hook invoked standalone —
  the documented canonical mode (`core/modules/postgres/module.yaml`: «operator-инвокация»).
- Execution path: trigger → operator runs `python3 on_project_deploy.py <dir> <project>` → main()
  passes `runner=None` → `:132-147`
  ```python
  if runner is None:
      result = subprocess.run(
          ["docker", "exec", "postgres", "psql", "-U", "postgres",
           "-c", f"CREATE DATABASE {db_name} OWNER postgres;"],
          capture_output=True, text=True, check=False,
      )
  ```
  → no `timeout=` → hangs forever on a wedged container.
- Actual behavior: indefinite block of the invoking terminal.
- Expected behavior: parity with sibling calls — `_psql` (:298-310) and gen_env_platform call
  (:428-448) both use `timeout=60` in BOTH branches; the DI branch here even has `timeout=60`
  (:148-162), proving the omission is accidental asymmetry.
- Impact: operator-facing hang during incident triage; no pipeline path affected (hook not wired
  into deploy chain).
- Minimal fix: add `timeout=60` to the runner=None branch (mirror :148-162).
- Required regression test: `test_auto_create_db_default_runner_timeout` — monkeypatched
  subprocess.run capturing kwargs; assert `timeout == 60`.

## BUG-0207 — acme.sh issue retry: zero backoff between attempts against Let's Encrypt rate limits

- Severity: LOW
- Confidence: 85%
- File: core/internal/bootstrap/issue_cert.py:393
- Symbol: `_acme_issue_with_retry()`
- Trigger: first `acme.sh --issue` fails (DNS propagation lag, transient provider error).
- Execution path: trigger → `issue_cert.py:393-398`
  ```python
  for attempt in range(1, ctx.max_attempts + 1):
      result = ctx.runner.run(acme_args, timeout=ACME_CMD_TIMEOUT, check=False)
      last_rc = result.returncode
      if last_rc == 0:
          break
      _log_step(log_step, "WARN", warn_fn(last_rc, attempt))
  ```
  → immediate second issue attempt (ISSUE_MAX_ATTEMPTS=2, :95), no sleep/jitter → burns a second
  validation against LE while the first failure's cause (propagation) is still pending.
- Actual behavior: tight 2-attempt loop; second attempt likely fails identically and consumes
  failed-validation quota.
- Expected behavior: backoff between attempts (platform canon: shared/retry.py +
  RETRY_BACKOFF_SECONDS; every other external-system retry uses it).
- Impact: avoidable cert-issuance failures under rate limits; wasted ACME_CMD_TIMEOUT windows.
- Minimal fix: delegate loop to shared `retry()` (exception_mode=False, backoff [30]) or insert
  `ctx.clock(...)` sleep between attempts.
- Required regression test: `test_acme_issue_with_retry_sleeps_between_attempts` — fake clock;
  assert sleep called once between attempt 1 and 2 on rc≠0.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0201 | HIGH | 90% | HealthcheckPoller: outer 20×interval window умножается на внутренний 60s docker-poll и per-URL HTTP бюджет → ~21 мин вместо заявленных 60s |
| BUG-0202 | MEDIUM | 75% | ForcedCommandChannel 900s < worst-case receive (up+pull+poll) → ssh убивает деплой mid-flight, retry повторяет всю доставку ×3 |
| BUG-0203 | MEDIUM | 75% | module_interface.invoke при таймауте убивает только bash — docker-compose/exec внуки живут и мутируют состояние параллельно с пайплайном |
| BUG-0204 | MEDIUM | 80% | context_promoter git push --mirror/ls-remote без timeout и без BatchMode в git-транспорте → make context-promote может висеть бесконечно |
| BUG-0205 | LOW | 70% | docker_login/ghcr_login без timeout → bootstrap φ3/φ6 виснет на недоступном registry |
| BUG-0206 | LOW | 75% | postgres hook CREATE DATABASE в runner=None ветке без timeout=60 (сиблинги имеют) → вечный hang на wedged контейнере |
| BUG-0207 | LOW | 85% | _acme_issue_with_retry без паузы между попытками — тугой реissu против LE rate-limit |
