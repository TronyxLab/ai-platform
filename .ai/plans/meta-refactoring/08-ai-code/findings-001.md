# Findings 001 — Cross-module contract drift
# Wave 1 · agent: contract-drift · adversarially verified (see findings-012)

## AI-0001 [HIGH·LATENT] [contract-drift/port] VERIFIED
Files: core/platform-infra.yaml:129-130 (port 3001, url_template http://langfuse:3001); core/modules/langfuse/docker-compose.base.yml:39,103 (container listens 3000; host map 3001→3000); templates/*/.env.example baked
Symbols: provides.langfuse.url_template → gen_env_platform.py:320-334 emits verbatim → PLATFORM_LANGFUSE_URL=http://langfuse:3001
Evidence: nothing binds 3001 in-container; all internal consumers correctly use langfuse:3000 — ONLY the project-facing PLATFORM_LANGFUSE_URL carries wrong port.
Problem: first project using Langfuse tracing init (`langfuse.Langfuse(host=PLATFORM_LANGFUSE_URL)`) gets connection-refused.
Why AI-pattern: SoT field authored from host-port perspective, container port forgotten; parity gate checks compose mapping, never url_template.
Minimal cleanup: url_template → :3000 + regen platform-env/templates. Code churn: <5 lines.
Pre-launch: YES (before any project onboards). Confidence: high.

## AI-0002 [LOW·LATENT] [contract-drift/timeout] VERIFIED-DOWNGRADED
Files: core/platform-infra.yaml:284 + platform-env.yaml:215 + .env.example:276 (=600) vs core/internal/shared/timeouts.py:130 (DEPLOY_TIMEOUT=900); app_config.py:118
Evidence (verifier): mechanical override direction real (env wins), BUT deploy paths never source these env files (CI uses raw ssh; local channels read os.environ only; no dotenv loading) ⇒ effective value is 900 everywhere today.
Problem reduced to: inert config/doc drift (documented 600 vs actual 900) — misleading budget tables, plus 10 stale 600 literals (channels/base.py:15, core_deliverer.py:87, phases/docker.py:703, orchestrator_cli.py:85…). Related: AI-0039 (SoT doc table), AI-0020 (shell facade default).
Minimal cleanup: align platform-infra.yaml default to 900 + regen; sweep literals opportunistically. Code churn: ~15 lines + regen.
Post-launch OK. Confidence: high.

## AI-0003 [LOW·LATENT] [contract-drift/hostname] VERIFIED
Files: core/platform-infra.yaml:116 (host: nginx-proxy) vs core/modules/nginx/docker-compose.base.yml:39,92-96 (container_name/alias: nginx)
Evidence (verifier): sole runtime consumer = practices/generators.py:361 → generated tests/test_health.py probes PLATFORM_NGINX_HOST → socket.gaierror → pytest.skip("nginx not running") = false-pass no-op smoke test.
Problem: phantom facade hostname; generated static health check can never actually run.
Minimal cleanup: align SoT host to `nginx`. Code churn: <5 lines + regen.
Pre-launch: cheap, bundle with AI-0001 regen. Confidence: high.

## AI-0004 [HIGH·ACTIVE] [contract-drift/path] VERIFIED-UPGRADED
Files: core/internal/shared/deploy_paths.py:304 (fallback /opt/prometheus/rules); monitoring/constants.py:34 (import-time ALERT_RULES_DIR=prometheus_rules_dir()); monitoring/alert_rules.py:92 vs core/platform-infra.yaml:271 (/opt/platform/prometheus-rules); monitoring/docker-compose.base.yml:72,117 (mount)
Evidence (verifier): prod call path config_renderer.run_monitoring_reconfig → _render_step("alert_rules", …) does NOT pass output_dir (config_renderer.py:702); PROMETHEUS_RULES_DIR exported nowhere into node env ⇒ stale fallback wins; rendered rules land outside the mounted dir → prometheus never loads them.
Problem: silent alerting loss whenever alerting_enabled — contradicts deploy_paths.py:296 claim «3-way рассинхрон закрыт»; 170-W12 fix incomplete; env_example_drift gate covers .env.example but not Python fallback.
Minimal cleanup: deploy_paths fallback ← canonical path (or resolver-only, fail if unset). Code churn: ~10 lines.
Pre-launch: YES (top priority). Confidence: high.

## AI-0005 [LOW] [contract-drift/resources]
Files: core/modules/langfuse/module.yaml:41-42 vs docker-compose.base.yml:94-99,131-136
Symbols: resources.reservations; langfuse-worker absent from module.yaml
Evidence: gate test_memory_limits.py:91 sums only limits.memory ≥; reservations/cpus/worker unmodeled despite «синхронизировано с base.yml».
Minimal cleanup: model worker+reservations or trim schema doc. Churn ~15 lines. Post-launch OK. Confidence: high.

# Verified clean: S3_ENDPOINT_URL family; AGE_SECRET_KEY semantics; redis digest sync.
