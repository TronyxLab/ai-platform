# Overnight Report — 2026-09-02

## Verdict: PARTIAL (1 blocked: asi-team-vps)

tronyx-vps fully green + verified. asi-team-vps permanently blocked on the documented
legacy-layout debt (migration out of overnight scope). Honest partial, no silent-green.

## Green matrix

### Локально
- [x] `make agent-check` → exit 0 (0 blocking / 0 advisory)
- [~] `make check` → 18/20 green; F1/F2/F3 CLOSED. 2 environmental failures (NOT regressions):
  E1 `uv audit` sandbox ~/.cache/uv EPERM; E2 setgid-2775 macOS semantics; E3 sudo sandbox EPERM (x2).
- [~] git tree → journal/reports/plans + loadtest-history + **uncommitted core fixes** (F6/F10/F12 + tests)

### tronyx-vps (tronyx-lab)
- [x] validate-node-input PASS
- [x] bootstrap-node rc=0 (final-verify: certs/secrets.env/vhosts/GHCR all OK)
- [x] converge FULLY CONVERGED · node-update rc=0 (φ9-φ13)
- [x] healthcheck ALL MODULES HEALTHY
- [x] e2e-verify PASS (tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru → HTTP 200 + TLS ok)
- [x] tronyx-site, dance-site, botanika — live; oldapp — explicit stub (adopted, no domain, no local source)
- [x] повторный bootstrap no-op (delivered=0 skipped=4, final-verify no-op)
- [x] дрейф-дриллы: cert→heal(S3 restore), container→restore(compose up), vhost→fail-loud(exit 2) — все heal-or-fail-loud

### asi-team-vps (asi-group)
- [x] validate-node-input PASS
- [~] bootstrap-node → φ1-φ7 done, φ8 modules done, **φ-final-verify FAIL (assertion a: roadmap cert)** — exit 10
- [ ] converge/node-update/healthcheck/e2e — NOT RUN (blocked)
- [ ] roadmap — NOT live (HTTP 502)
- [ ] повторный bootstrap no-op / дрейф-дриллы — N/A (blocked)

### CI-канал
- [x] N/A — изменений кода проектов нет (проекты задеплоены прямым каналом `make deploy-project`)

## Findings

| # | Sev | Finding | Status |
|---|-----|---------|--------|
| F1/F2/F3 | — | 029 QA (bootstrap LOC / pass-test / generated-files) | CLOSED (confirmed by make check) |
| E1 | env | uv-audit sandbox cache | documented (environmental) |
| E2 | env | setgid-2775 macOS | documented (environmental) |
| E3 | env | sudo sandbox EPERM | documented (environmental) |
| F6 | HIGH | probe_sops_enc_file missing per-node candidate (core/internal/bootstrap/preflight.py) | FIXED (+test) |
| F7 | SEC | SSH host keys changed (VPS re-provision, postmortem-documented) | RESOLVED (known_hosts) |
| F8 | INFO | both nodes BARE (fresh re-provision) → full cold bootstrap | handled |
| F9 | INFO | asi φ8 clone transient (recovered) | transient |
| F10 | HIGH | git clone HTTP/2 401 anonymous (core/internal/bootstrap/deploy/context_overlay.py) | FIXED (pin http.version=HTTP/1.1) |
| F11 | HIGH | tronyx overlay deploy-key missing (retro context, T6 skip) | FIXED (runbook fallback, gh deploy-key) |
| F12 | HIGH | final-verify assertion (d) false-positive GHCR (final_verify.py reads os.environ, not secrets.env) | FIXED (secrets.env fallback) |
| F13 | HIGH | asi φ8 nginx compose up partial (transient; resolved on re-run) | resolved |
| F14 | HIGH | asi roadmap cert "issued" but missing (wildcard mismatch) | BLOCKED |
| F15 | HIGH | asi roadmap project not deployed (legacy-layout mirror has no projects/) | BLOCKED |
| F16 | MED | tronyx projects STUB after cold bootstrap (CI-deployed) | RESOLVED (make deploy-project x3) |

## Blocked

- **asi-team-vps** — φ-final-verify FAIL (a) roadmap.asiteam.ru cert missing + roadmap project not deployed (HTTP 502).
  - Root: **legacy-layout TRAP[DEBT]** — repos.core = https mirror (asi-group/ai-platform full source clone) has NO projects/ dir → roadmap2 never deployed; + cert orchestration issues roadmap cert but vhost uses wildcard (F14).
  - **Owner action**: migrate asi to canonical overlay (create `asi-group-overlay` with projects/ + node-configs/ + context.yaml), reconcile roadmap cert policy (wildcard vs per-project), then re-run bootstrap.

## Timeline (сжато)

- 18:40 session start (fresh, no journal); Q1-Q5 → both nodes, detected AGE keys.
- 18:41 make check (F1/F2/F3 closed, 2 env fails) + agent-check PASS.
- 18:42 validate-node-input FAIL (F6) → coder fix → PASS.
- 18:49 SSH host keys changed (F7) → known_hosts (escalated).
- 18:52 nodes BARE (F8) → cold bootstrap subagents.
- 19:07 tronyx φ8 clone fail (F11 deploy key) + asi φ8 clone fail (F10 HTTP/2) → fixes.
- 19:14 tronyx φf fail (F12 GHCR false-positive) → coder fix.
- 20:01 tronyx bootstrap GREEN (10 phases).
- 20:12 converge/node-update/healthcheck rc=0; projects STUB (F16).
- 20:23 deploy tronyx-site/dance-site/botanika (DEPLOYED healthy).
- 20:25 e2e-verify PASS.
- 20:29 re-bootstrap no-op PASS.
- 20:30-20:35 drills 1-3 PASS (cert heal / container restore / vhost fail-loud) + recovery.
- 20:39 final e2e PASS.

## Resume state

execution-state.json финализирован: p0 done, p1 done, p2 done, p3 done, p4 N/A, p5 done.
asi-team-vps = permanently blocked (F14/F15) with exact owner action. tronyx-vps = green.
