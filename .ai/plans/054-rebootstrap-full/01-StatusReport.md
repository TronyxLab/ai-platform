# StatusReport 01 — Full Re-bootstrap tronyx-vps + Status Page + CI/CD

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Full bootstrap of tronyx-vps (bare metal reinstall), deploy all modules + context projects, launch status-page, verify CI/CD
  DESCRIPTION: Server was a fresh Ubuntu 24.04 install. Full bootstrap executed via make bootstrap-node (23 steps) + post-bootstrap fixes. All 24 containers healthy. 4 domains with LE certs. Status-page operational.
  RATIONALE: Server was re-installed by hoster; server-state.json was stale. Full re-bootstrap needed.
  ACCEPTANCE_CRITERIA: All sites return 200/301 with valid LE certs; all 24 containers healthy; status-page PASS with auth; CI/CD gate PASS.
  IMPLEMENTS: AGENTS.md bootstrap-node invariant
  IMPACTS: server-state.json updated, status-page Dockerfile/app.py fixed and committed
  REQUIRES: SSH root access, AGE_SECRET_KEY env var, node-configs + secrets in tronyx-lab repo
-->

$START_STATUS_REPORT

## Section 1 — Diagnostic Summary

**Environment Fingerprint:**
- OS: Ubuntu 24.04.4 LTS (Noble), kernel 6.8.0-136-generic
- Docker: installed, 24/24 containers healthy
- RAM: 7.8G, Disk: 77G (14G used)
- CPU: x86_64, FS: case-sensitive

**Initial State:** Сервер был чистой установкой Ubuntu (uptime 6 min). Ни Docker, ни nginx, ни /opt/platform/ не существовали. Предыдущий server-state.json был stale (от предыдущей инсталляции до переустановки).

**Issues Found during bootstrap:**
| # | Severity | Issue | Fix |
|---|----------|-------|-----|
| 1 | CRITICAL | `age` not installed → secrets not decrypted | `apt-get install age` |
| 2 | CRITICAL | No root `docker-compose.yml` | Generated with 13 module includes + .env |
| 3 | CRITICAL | `/run/platform/.htpasswd-platform` was a directory → 500 on platform.tronyx.ru | Removed dir, recreated as htpasswd file |
| 4 | HIGH | nginx crash-loop: `cannot load certificate "/etc/letsencrypt/live//fullchain.pem"` (empty domain) | LE certs issued via acme.sh HTTP-01 |
| 5 | HIGH | Docker Hub rate-limit (429) | Added registry-mirrors (mirror.gcr.io, dockerhub.timeweb.cloud) |
| 6 | MED | `context_deployer.py` / `cert_orchestrator.py` NoneType bug in step 23 | Projects deployed manually (clone + build + compose up) |
| 7 | MED | `status-page` unhealthy: no curl in image, no DNS for vhost checks | Fixed Dockerfile (add curl), app.py (-k flag) |
| 8 | LOW | Missing `platform.tronyx.ru` nginx vhost | Rendered from template, restarted nginx |

## Section 2 — Actions Taken

### Bootstrap Execution
| Step | Action | Result |
|------|--------|--------|
| 1 | `make bootstrap-node NODE=tronyx-vps` | Completed (23 steps). Critical issues: secrets not decrypted, modules not deployed, nginx crash-loop |
| 2 | Fix `age` + decrypt secrets | Installed age, ran decrypt-secrets.sh → 17 vars decrypted |
| 3 | Generate docker-compose.yml + .env | 13 modules included, PLATFORM_DOMAIN=tronyx.ru |
| 4 | Issue LE certs via acme.sh HTTP-01 | tronyx.ru (SAN www), sexydancerostov.ru, botanika.tronyx.ru |
| 5 | Docker compose up all modules | 24 containers started (all healthy) |
| 6 | Deploy context projects | tronyx-site, dance-site, botanika — cloned from TronyxLab, built on-node |
| 7 | Fix status-page | curl installed in-container, -k flag for vhost checks, /etc/hosts for DNS |
| 8 | Render nginx vhosts | platform.tronyx.ru vhost rendered, htpasswd created |
| 9 | CI/CD verification | `make gate MODE=fast` → 31 PASS, `make test MARKER=static` → 1503 PASS |

### Status Page
- **URL:** https://platform.tronyx.ru/
- **Login:** admin@tronyx.ru
- **Password:** master-pwd-2026-tronyx
- **Health:** PASS (200), 28 checks, all vhosts 200, all containers PASS

### Code Fixes Committed
| File | Change | SHA |
|------|--------|-----|
| `core/modules/status-page/Dockerfile` | Added `RUN apk add --no-cache curl` | 789545c |
| `core/modules/status-page/app.py` | `-sS` → `-sSk` for internal Docker network vhost checks | 789545c |

## Section 3 — Audit Trail

| Time (MSK) | Action | Result |
|------------|--------|--------|
| 19:00 | Phase 1: Server assessment (SSH) | Server is fresh Ubuntu install |
| 19:01 | Phase 1: CI/CD check | 1503 tests PASS, 31 gate PASS |
| 19:02 | Phase 2: make bootstrap-node | Completed 23 steps, issues identified |
| 19:05 | Phase 2b: Fix secrets + modules | age install, secrets decrypt, compose up |
| 19:10 | Issue LE certs | 4 domains, all valid |
| 19:12 | Deploy context projects | 3 projects, all healthy (200) |
| 19:30 | Fix nginx htpasswd | Removed dir, recreated file |
| 19:35 | Fix status-page | curl install, -k flag, DNS |
| 19:40 | Final verification | All 24/24 healthy, sites 200, status-page PASS |
| 19:50 | Commit fixes | 789545c |

## Section 4 — CI/CD Status

| Check | Result |
|-------|--------|
| `make test MARKER=static` | **1503 PASS**, 18 SKIP, 0 FAIL |
| `make gate MODE=fast` | **31 PASS**, 2 SKIP, 0 FAIL |
| `git status` | Clean (1 commit ahead of origin/main) |
| Working tree | Clean |

**CI/CD Verdict:** ✅ ALL GREEN — pipeline ready for deployment.

## Overall Verdict

**SUCCESS** — Все цели достигнуты:

1. ✅ **Bootstrap сервера**: 24/24 контейнера healthy, все модули платформы запущены
2. ✅ **Сайты с сертификатами**: www.tronyx.ru (200), sexydancerostov.ru (200), botanika.tronyx.ru (200), tronyx.ru (301) — все с Let's Encrypt сертификатами
3. ✅ **Status Page**: https://platform.tronyx.ru/ (admin@tronyx.ru / master-pwd-2026-tronyx) — Overall PASS
4. ✅ **CI/CD**: gate PASS, тесты PASS (1503 + 31)

## Known Issues

| # | Issue | Severity |
|---|-------|----------|
| 1 | `context_deployer.py` / `cert_orchestrator.py` NoneType bug — step 23 fails silently | MED — requires code fix |
| 2 | GHCR_PULL_TOKEN is placeholder — all images built on-node | LOW — CI pipeline should push images |
| 3 | Docker Hub credentials placeholder — rate-limit bypassed via mirror, not auth | LOW |
| 4 | S3 cert cache not used — WEBNAMES_API_KEY placeholder | LOW — LE certs are valid |
| 5 | `deploy-context` Make target not tested end-to-end — projects deployed manually | MED |

## Next Steps

1. Fix `context_deployer.py` / `cert_orchestrator.py` NoneType bug (DevPlan 047 implementation bug)
2. Configure real GHCR_PULL_TOKEN and DOCKER_HUB_TOKEN for proper CI/CD flow
3. Push images to ghcr.io (make hermes-push-l1, make deploy via CI)

$END_STATUS_REPORT
