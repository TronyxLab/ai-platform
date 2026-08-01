# GREP_SUMMARY: hermes-agent-context L2 context overlay Dockerfile CONTEXT guard Telegram skills
# region MODULE_CONTRACT
## @purpose     L2 context overlay for hermes-agent — extends L1 (hermes-agent-base) with CONTEXT guard,
##              Telegram configuration, context-specific configs, and context skills. Builds on L1.
## @scope       Build context: repo root (.) — paths relative to repo root: core/modules/hermes-agent/context/
## @layer       L2 (context overlay): FROM hermes-agent-base:latest (L1)
## @invariants
##   - REQUIRED build-arg: CONTEXT — must be provided at build time
##   - REQUIRED runtime env: CONTEXT — enforced by init-context.sh guard (exit 1 if empty)
##   - Copies context configs to /opt/hermes/context/config/
##   - Copies context skills to /opt/hermes/context/skills/
##   - Init script at /etc/cont-init.d/04-context-init (runs after L1 02-platform-init, 03-register-profiles)
##   - HEALTHCHECK identical to L1 (curl http://127.0.0.1:9119/)
##   - NO EXPOSE — agent accessed through proxy-net (nginx reverse proxy) only
##   - NO resource limits set (AR3)
## @rationale   L1 (hermes-agent-base) is the platform base with all generic configs, profiles, skills.
##              L1 is obtained from ghcr.io/tronyx161/hermes-agent-base (public distribution base,
##              DevPlan 116 B3 T7, D1) and tagged locally as hermes-agent-base:latest before the L2
##              build (see build-platform.yml pull step / make hermes-build-context).
##              L2 adds context-specific overlays (Telegram config, per-context skills, CONTEXT guard).
##              This separation allows multiple L2 images for different contexts (personal, dev, ci)
##              all sharing the same L1 base. See AGENTS.md §Layer Architecture for canonical L0/L1/L2.
## $ARTIFACT_CONTRACT
## - PURPOSE:    L2 Dockerfile for hermes-agent context overlay
## - IMPLEMENTS: Brief_2.md §4 Phase 0, step 0.4; DevPlan.md §4 TASK-0.4
## - IMPACTS:    New image hermes-agent-context:latest (local, not pushed)
## - REQUIRES:   hermes-agent-base:latest (L1) built first
# endregion MODULE_CONTRACT
# DOCREF:
#   - AGENTS.md:           §Layer Architecture (L0/L1/L2)
#   - DevPlan.md:          §4 TASK-0.4 — L2 Dockerfile migration
#   - build/Dockerfile:    L1 base image — hermes-agent-base
#   - build/scripts/init.sh: L1 init — creates profiles, applies context overlays

# ═══════════════════════════════════════════════════════════════════════════
# region STAGE: final (L2 context overlay)
## @purpose     Build L2 context overlay image on top of L1 hermes-agent-base
## @complexity  Single stage — extends L1 with context-specific files and guard init script
FROM hermes-agent-base:latest

# ── Labels ────────────────────────────────────────────────────────────────
LABEL org.tronyxlab.module="hermes-agent-context" \
      org.tronyxlab.layer="L2" \
      role="context" \
      org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform" \
      org.opencontainers.image.description="Hermes Agent Context — L2 context overlay with Telegram config, context skills, CONTEXT guard" \
      org.opencontainers.image.name="hermes-agent-context" \
      maintainer="hermes-agent-context"

# ── Required build-arg: CONTEXT ───────────────────────────────────────────
# 🧐 TRAP[DECISION] · 2026-07-09 · — · ARG CONTEXT without default enforces build-time awareness
# · ARG CONTEXT with no default means the build will not fail if omitted, but ENV will be empty.
# · Runtime guard via init-context.sh (04-context-init) catches empty CONTEXT at container start.
# · Rejected: ARG CONTEXT=default (would mask missing build arg)
# · Reason: Better to surface missing CONTEXT immediately at container start than silently use default.
# · Rev: If Docker adds required ARG syntax in a future version, switch to `ARG --required CONTEXT`.
ARG CONTEXT

# Persist build-arg as runtime ENV so init-context.sh can read it
# NOTE: User can override at runtime with `-e CONTEXT=prod`
ENV CONTEXT=${CONTEXT}

# ── Copy context-specific artifacts ───────────────────────────────────────
# [IMP:8][L2-DOCKERFILE] Context config overlay (e.g., Telegram config, per-context settings)
# These files are rsync'd over L1 configs by init-context.sh at container start
COPY core/modules/hermes-agent/context/config/ /opt/hermes/context/config/

# [IMP:8][L2-DOCKERFILE] Context skills overlay (per-context custom skills)
# Placed alongside L1 skills, merged at container start by init-context.sh
COPY core/modules/hermes-agent/context/skills/ /opt/hermes/context/skills/

# [IMP:9][L2-DOCKERFILE] L2 init script with CONTEXT guard
# Runs after L1 s6 init scripts (02-platform-init, 03-register-profiles)
COPY core/modules/hermes-agent/context/scripts/init-context.sh /etc/cont-init.d/04-context-init

# ── Ensure init script is executable ──────────────────────────────────────
RUN chmod +x /etc/cont-init.d/04-context-init

# ── HEALTHCHECK (same as L1) ──────────────────────────────────────────────
# [IMP:8][L2-DOCKERFILE] Liveness via dashboard HTTP endpoint (port 9119)
# Same interval/timeout/retries as L1 — consistent healthcheck across all layers
HEALTHCHECK --interval=15s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://127.0.0.1:9119/ || exit 1

# CMD is inherited from L1 (upstream hermes-agent s6-overlay init)
# endregion STAGE: final (L2 context overlay)
# DOCREF: See HERMES_DOCS.md for full documentation reference list
