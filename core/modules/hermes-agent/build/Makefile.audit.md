# GREP_SUMMARY: Makefile audit ai-agent ai-platform target-migration TASK-8
# STRUCTURE: ▶ Makefile targets ┌ FOR migration ┐ ┌ NOT-for-migration ┐ → platform Makefile → ⎋

# Makefile Audit: ai-agent → ai-platform

> Generated: 2026-07-03 | Source: `~/projects/ai-agent/Makefile`
> Purpose: Determine which Makefile targets to migrate to `core/modules/hermes-agent/Makefile` (TASK-8)

## Targets FOR Migration (→ platform Makefile)

| Target | Purpose | Notes |
|--------|---------|-------|
| `build` | Build base agent image | → `build-agent` in platform Makefile. Uses `AGENT_IMAGE_SOURCE` flag |
| `push` | Push agent image to registry | → `push-agent` in platform Makefile |
| `test` | Smoke test (curl healthcheck) | Update endpoint from `:8642/health` → `:9119/` |
| `sync-config` | Copy config to running container | Keep for dev workflow |

## Targets NOT for Migration

| Target | Reason |
|--------|--------|
| `dev` | Replaced by new platform `dev` target (with AGENT_IMAGE_SOURCE / bind-mount support) |
| `dev-down` | Replaced by `make down` in platform |
| `dev-logs` | Not needed — `docker compose logs` direct usage |
| `dev-restart` | Not needed — `docker compose restart` direct usage |
| `dev-shell` | Not needed — `docker compose exec` direct usage |
| `shell` | Not needed — `docker compose exec` direct usage |
| `sync-soul` | Not needed — handled by unified init.sh |
