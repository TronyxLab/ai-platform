# Findings 010 — Copy-paste & subtly-diverged implementations
# Wave 2 · agent: duplication retry (deploy/healthcheck/practices/scripts/monitoring/verify_sweep/status-page)

## AI-0063 [MEDIUM] [duplication/diverged-fix-propagation]
Files: scripts/generate_entrypoint_manifest.py:586,624 vs generate_platform_env.py:465,500; generate_secrets_manifest.py:250; sync_env_defaults.py:947; sync_requirements.py:198,206; generate_agents_md.py:424,469
Symbols: _check_generated_content/_check_output family — 7 copies of byte-compare + difflib + DIFF_LINES_MAX=20
Evidence: DevPlan 123 P-14 fixed full-diff output ONLY in entrypoint-manifest copy (:624 no slice); other 5-6 sites still `diff_lines[:20]`.
Consequence: check-manifests RED on platform-env/secrets-manifest/.env.example/requirements/AGENTS.md still hides divergence source beyond line 20 — the exact CI-self-diagnosis problem P-14 solved recurs in 5 of 7 sites.
Why AI-pattern: fix applied to one copy of a pasted block instead of extracting helper.
Minimal cleanup: extract shared check_generated(path) helper; all sites call it. Churn ~50 lines. Pre-launch: yes (release-week diagnostic quality). Confidence: high.

## AI-0064 [MEDIUM] [duplication/curl-probe-triplet]
Files: shared/http_probe.py:43,69 (SoT, 172-W5.4) vs healthcheck/tor_proxy_check.py:58,60 vs modules/status-page/collectors/checks/http.py:71,92
Symbols: curl_http_code ×3
Evidence: flags already diverged (-sS vs -s vs -sSk); tor copy swallows OSError→None silently (SoT is fail-verbose); return shapes differ (str|None / dict / tuple). Status-page cross-layer copy lacks any TRAP documenting duplication (unlike app.py:18 yaml TRAP).
Consequence: probe semantics per caller drift silently; error-visibility inconsistent.
Minimal cleanup: same-layer tor_proxy_check imports SoT; status-page copy gets TRAP or param. Churn ~20 lines. Confidence: high.

## AI-0065 [MEDIUM] [contract-drift/health-criterion] (found independently by 2 agents)
Files: healthcheck/metrics/docker_collector.py:274 (`Status=="healthy"`) vs shared/docker_compose.py:520-536 canon (`running AND health∈{healthy,"","none"}`) vs lib/healthcheck.sh:123-129 (same canon) → consumer modules/status-page/collectors/checks/containers.py:48-49 (`running and not healthy → WARN`) → aggregate.py:116 (WARN ⇒ overall FAIL)
Evidence: running container WITHOUT HEALTHCHECK = healthy per deploy/bootstrap gates, healthy=False in status-metrics.json ⇒ permanent WARN on public status page + overall FAIL.
Consequence: two contradictory answers to «здоров?» in one codebase; pre-launch status page shows degraded for legitimately-healthcheck-less containers.
Minimal cleanup: docker_collector adopt canon criterion (one function); status-page WARN only when health=="unhealthy". Churn ~15 lines. Pre-launch: YES. Confidence: high.

## AI-0066 [LOW] [duplication/wildcard-san ×3]
Files: verify_sweep/tls_check.py:59,96-99; shared/ssl_certs.py:~302 (documented mirror); healthcheck/metrics/cert_collector.py:67,83-86 (undocumented third copy; :85 re-tests condition guaranteed at :83).
Cleanup: cert_collector imports ssl_certs helper. Churn ~15 lines. Confidence: high.

## AI-0067 [LOW] [duplication/generated-header-prefix]
Files: practices/generators.py:57 GENERATED_HEADER vs maturity.py:61 _GENERATED_HEADER (shorter string) + startswith check :230
Evidence: works via prefix coincidence today; editing either string breaks drift detection in the other. Cleanup: single constant import. Churn <10 lines. Confidence: high.

## AI-0068 [LOW] [duplication/two-readers-one-artifact]
Files: modules/status-page/collectors/config.py:217,243-244 load_status_metrics (schema_version gate) vs readiness.py:52,61-63 _read_metrics_file (no schema gate)
Consequence: /healthz can PASS with stale-schema file while /health flags it. Cleanup: readiness reuses load_status_metrics. Churn ~10 lines. Confidence: high.

# Slice summary: strongest = AI-0063 (partial fix propagation), AI-0064 (SoT bypass same layer), AI-0065 (dual health semantics).
