# Findings 001 — Cross-module contract drift
# Wave 1 · agent: contract-drift

## AI-0001 [HIGH] [contract-drift/port]
Files: core/platform-infra.yaml:128 (url_template :3001); core/modules/langfuse/docker-compose.base.yml:39,103 (container listens 3000, host map 3001→3000); AGENTS.md:211 (docs say langfuse:3000)
Symbols: PLATFORM_LANGFUSE_URL generation
Evidence: url_template `http://langfuse:3001` propagated by gen_env_platform.py:334; nothing binds 3001 in-container.
Problem: projects consuming PLATFORM_LANGFUSE_URL on hermes-agent-net get connection-refused; docs contradict generated env.
Gate gap: port_parity checks compose mapping, never url_template.
Minimal cleanup: fix url_template to 3000 (one line + regen).
Code churn: <5 lines. Pre-launch: yes.
Confidence: high.

## AI-0002 [HIGH] [contract-drift/timeout]
Files: core/platform-infra.yaml:281 + platform-env.yaml:215 + .env.example:276 (`PLATFORM_DEPLOY_TIMEOUT=600`) vs core/internal/shared/timeouts.py:130 (`DEPLOY_TIMEOUT=900`); app_config.py:118 env overrides constant
Symbols: PLATFORM_DEPLOY_TIMEOUT
Evidence: cold-node fix bumped constant to 900 ("600s мало"), but sourced .env still says 600 and wins everywhere .env is loaded.
Problem: φ8 cold-node deploy-timeout fix silently defeated on real nodes; 10 stale 600 literals across channels/base.py, core_deliverer.py, phases/docker.py, orchestrator_cli.py.
Gate gap: no gate compares env-default vs SoT constant for this key.
Minimal cleanup: update platform-infra.yaml default to 900 → regen env files; sweep literals.
Code churn: ~15 lines + regen. Pre-launch: yes.
Confidence: high.

## AI-0003 [HIGH] [contract-drift/hostname]
Files: core/platform-infra.yaml:114 (`host: nginx-proxy`); core/modules/nginx/docker-compose.base.yml:39,92-96 (`container_name: nginx`, alias `[nginx]`)
Symbols: provides.host for nginx
Evidence: `nginx-proxy` exists nowhere in compose definitions; generators.py:361 bakes it as PLATFORM_NGINX_HOST default.
Problem: documented/generated facade hostname is phantom; direct project calls resolve-fail.
Minimal cleanup: align SoT host to actual container name/alias.
Code churn: <5 lines + regen. Pre-launch: cheap.
Confidence: high (harm latent — few consumers dial nginx directly).

## AI-0004 [MEDIUM] [contract-drift/path]
Files: core/internal/shared/deploy_paths.py:304; sync_env_defaults.py:737; monitoring/alert_rules.py:74 (+constants.py:34) vs platform-infra.yaml:268 (`/opt/platform/prometheus-rules`)
Symbols: PROMETHEUS_RULES_DIR fallback
Evidence: Python-side fallbacks use legacy `/opt/prometheus/rules`; SoT/compose mount `/opt/platform/prometheus-rules`; deploy_paths.py:296 claims "3-way рассинхрон закрыт".
Problem: unset env ⇒ alert rules written where prometheus doesn't mount → alerts silently not loaded.
Gate gap: env_example_drift pins .env.example+volumes but not Python fallbacks.
Minimal cleanup: single resolver import in the three sites.
Code churn: ~10 lines. Pre-launch: yes (silent alerting loss).
Confidence: high.

## AI-0005 [LOW] [contract-drift/resources]
Files: core/modules/langfuse/module.yaml:41-42 vs docker-compose.base.yml:94-99,131-136
Symbols: resources.reservations; langfuse-worker absent from module.yaml
Evidence: gate test_memory_limits.py:91 sums only limits.memory ≥ check; reservations/cpus/worker unmodeled.
Impact: module.yaml "синхронизировано с base.yml" claim unverifiable for half its fields.
Minimal cleanup: model worker container + reservations or drop fields from schema doc.
Code churn: ~15 lines. Post-launch OK.
Confidence: high.

# Verified clean (no finding): S3_ENDPOINT_URL family, AGE_SECRET_KEY semantics, redis image digest sync.
