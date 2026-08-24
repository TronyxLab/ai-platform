# Findings 04 — Secrets handling & credential leakage

## SEC-0015 — AGE master key & CI deploy SSH key ride in remote bootstrap command line and DR-fallback logs
- **Severity:** HIGH · **Attack surface:** operator workstation `ps`, target VPS `/proc/<pid>/cmdline` during entire bootstrap (~30 min), logs/journal · **Confidence:** 0.9 · **Must fix before launch: YES**
- **Files:** `core/internal/shared/ssh_cmd_builder.py:190-198` (`_append_export` embeds `AGE_SECRET_KEY=<plaintext>` and `PLATFORM_CI_DEPLOY_KEY=<private key>` inside the ssh argv; comment :170 claims ps-hardening but the export lives IN the argv — nominal hardening only); `core/entrypoints/bootstrap.sh:92-98` (exec ssh with full string; only dry-run masks); `bootstrap/core_deliverer.py:637-646` (`deliver_fallback` builds `AGE_SECRET_KEY='…' make node-update` AND logs it verbatim via `logger.info("[IMP:8]… WOULD run: %s", " ".join(update_cmd))`)
- **Symbols:** `build_ssh_cmd()`, `_append_export()`, `deliver_fallback()`
- **Preconditions:** node has multiple local accounts by design (φ2 creates platform/ci-deploy); no hidepid hardening anywhere (rg: 0 hits).
- **Attack path:** any local account (incl. ci-deploy used by every project CI push) reads `/proc/<pid>/cmdline` of the running root bash → master AGE key (decrypts everything: POSTGRES_PASSWORD, LITELLM_MASTER_KEY, TELEGRAM_BOT_TOKEN…) + CI_DEPLOY_KEY (impersonates receive channel). Same strings visible in operator-machine ps and journal via the IMP:8 log line — violates explicit canon «ключ НИКОГДА не попадает в логи».
- **Minimal fix:** out-of-band key delivery (stdin to `bash -s`, SendEnv/SetEnv, or 0600 root file via existing SCP channel); redact the deliver_fallback log; defense-in-depth `hidepid=2`; update the false TRAP invariant text.
- **Regression test:** sentinel-key unit test asserting no `AGE-SECRET-KEY-`/`PRIVATE KEY` substring in any argv-bound segment; log-capture test asserting masked dry-run output.

## SEC-0016 — Plaintext `secrets.env.tmp`: permission window + permanent crash residue, no cleanup handler
- **Severity:** HIGH · **Attack surface:** node filesystem next to `/var/lib/platform/run/secrets.env` · **Confidence:** 0.85 · **Must fix before launch: YES**
- **Files:** `core/internal/bootstrap/lifecycle/secrets_manager.py:524-537` (fixed-name `.env.tmp`, plain `open("w")` of full merged plaintext, chmod 0600 AFTER write, no try/finally/unlink-on-failure/signal handlers), `:707-715` (`_plw_body_persist_new_vars` same pattern)
- **Preconditions:** umask 022 default ⇒ tmp created 0644; crash/SIGKILL/OSError mid-write leaves permanent world-readable copy of every secret incl. PLATFORM_MASTER_PASSWORD.
- **Impact:** silent long-lived plaintext disclosure on multi-user node; contradicts module's own 0600 invariant.
- **Minimal fix:** replace both sites with `shared/atomic_writer.atomic_write(mode=0o600)` (mkstemp 0600 from creation); `os.umask(0o077)` belt-and-braces in lifecycle entrypoints.
- **Regression test:** fault-injected writer raising mid-serialization → no `*.tmp` sibling remains; observed tmp inode had 0600 from creation.

## SEC-0017 — Tenant DB password written world-readable (0644) into `.env.platform` by node-side regen path
- **Severity:** MEDIUM-HIGH · **Attack surface:** `/opt/projects/<org>/<project>/.env.platform`; all local principals (and every container given F2-class mounts) · **Confidence:** 0.9 · **Must fix before launch: YES** (one-line class fix)
- **Files:** `core/internal/scaffold/gen_env_platform.py:498-501` (`output_file.write_text(...)` — no mode/chown → umask 0644 root-owned); password source `postgres/hooks/on_project_deploy.py:_regenerate_env_platform` + `gen_env_platform.py:209-224` (`_apply_credentials_to_dsn` replaces `***` with real token_urlsafe(24) password in PLATFORM_POSTGRES_DSN); baseline inconsistency: converge creates 0640 ci-deploy:ci-deploy (`converge/projects.py:226-239`), CI overwrite resets to 0644
- **Attack path:** first role creation regenerates .env.platform with live DSN → readable by platform/operator/any local service account → cross-tenant credential disclosure (chains with SEC-0008 CONNECT gap → direct data access).
- **Minimal fix:** `atomic_write_text(..., mode=0o640)` + chown ci-deploy:ci-deploy when root; post-copy chmod 0600 in receive_flow for delivered env files.
- **Regression test:** run generator CLI in tmp dir → assert mode ≤ 0640 and no group/world bits.

## SEC-0018 — Nightly `pg_dumpall` backups uploaded to third-party S3 without client-side encryption
- **Severity:** MEDIUM-HIGH · **Confidence:** 0.8 (code-side certain; provider SSE unverifiable from repo) · **Must fix before launch: YES** before hosting real tenant data
- **Files:** `core/modules/backup-cron/scripts/backup_postgres.py` (`pg_dumpall | gzip`, no age/sops stage); `upload.py:281-293` (`upload_file(...)` — no ServerSideEncryption kwarg, no pre-upload encryption)
- **Preconditions:** bucket credential compromise (S3 keys live in same trust domain), bucket misconfig, or provider-side access → complete historical business data (all tenant DBs, superuser scope) in cleartext; many retained generations.
- **Impact:** bulk exfiltration class; contradicts platform's own principle «шифрование ДО выгрузки» implemented correctly for the AGE key backup but not DB dumps.
- **Minimal fix:** gzip → sops/age encrypt → upload `.enc`; restore gains one decrypt step; verify/document bucket default-SSE as interim control.
- **Regression test:** fake-client pipeline test asserting uploaded body starts with ciphertext header; raw SQL never reaches upload_file.

## SEC-0019 — Secret values passed as process argv: `sops --set '<value>'`, `htpasswd --password`
- **Severity:** MEDIUM · **Confidence:** 0.85 · **Must fix:** NO (seconds-per-secret windows; fix alongside SEC-0015 transport work)
- **Files:** `secrets_manager.py:401-408` (`subprocess.run(["sops","--set", f'["{var}"] "{value}"', enc_file])` — each freshly generated secret ps-visible ~30s during φ4); `makefiles/helpers.mk:96-97` (master password as make/python argv). Contrast correct: `docker_auth.py --password-stdin`.
- **Minimal fix:** decrypt→merge in memory→re-encrypt round-trip for sops; stdin/env option for htpasswd CLI.
- **Regression test:** static gate scanning subprocess list-args for f-string interpolation of known secret-var names.

Checked-and-clean (exemplary where it matters): decrypt_secrets temp-key hygiene (tmpfs /dev/shm mkstemp 0600-from-creation, dd-wipe, atexit+signals, stderr redaction); AGE masking everywhere; secrets_env_parser logs key+len only (prior leak TRAP-fixed); PlatformFatalError embeds sanitized/truncated stderr only; no set -x anywhere; generated committed artifacts placeholder-only with gitleaks + fail-fast required-vars gates; payload .env.platform carries `***` placeholder (real injection node-side only); GHCR token presence-check never logged; telegram token never logged, egress via Tor; backups don't bundle secrets.env; `source` subcommand prints KEY=VALUE lines but has zero production callers (dormant risk noted).
