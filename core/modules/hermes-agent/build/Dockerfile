# GREP_SUMMARY: hermes-agent-base Dockerfile multi-stage validate final hermes-agent config profiles skills
# region MODULE_CONTRACT
## @purpose  Multi-stage build for hermes-agent-base (L1): platform base image with config, profiles, skills, init scripts.
##           Built in tronyx161 CI and pushed to ghcr.io/tronyx161 as a PUBLIC distribution base
##           (DevPlan 116 B3 T7, U-60, D1 — package visibility is a manual operator step).
##           Context orgs pull it anonymously and build only their thin L2.
## @scope    Build context: ./build/ — only platform artifacts (no orchestration files)
## @invariants
##   - Stage validate: lightweight alpine image for pre-copy validation (shellcheck, yq, chmod)
##   - Stage final: FROM nousresearch/hermes-agent:v2026.8.3@sha256:16788311e2fa3035456bdc1bafb8ec2b1777db64ebf020af9bb7eb73c3712c9e  # v1.0.1 digest-pin (index-digest, imagetools inspect) — upstream s6-overlay image
##   - Hermes Agent v2026.8.3 (v0.20.0 «The Herald Release», 2026-08-03) — L0 pin (164 W1-5)
##   - Configs in /opt/hermes/config/, templates in /opt/hermes/templates/, skills in /opt/hermes/skills/
##   - Init scripts in /etc/cont-init.d/ for s6-overlay
##   - NO EXPOSE — agent accessed through proxy-net (nginx reverse proxy) only
##   - Resource limits NOT set (AR3)
## @rationale Multi-stage keeps final image minimal — validate stage adds shellcheck/yq/alpine only during build
# endregion MODULE_CONTRACT
# DOCREF: Hermes Agent documentation references
#   - GitHub:          https://github.com/NousResearch/hermes-agent
#   - Releases:        https://github.com/NousResearch/hermes-agent/releases
#   - Docs:            https://hermes-agent.nousresearch.com/docs
#   - Config Ref:      https://hermes-agent.nousresearch.com/docs/user-guide/configuration
#   - Env Vars:        https://hermes-agent.nousresearch.com/docs/user-guide/configuration/environment-variables

# region STAGE: validate
# 🧐 TRAP[DECISION] · 2026-07-07 · — · Alpine upgraded 3.20 → 3.21 (TASK-2)
# · 3.21 is latest stable Alpine release
# · Latest patch: 3.21.7 (as of 2026-07-07)
# · Rev: when Alpine 3.22 stable is released — upgrade to latest
# v1.0.1 digest-pin (imagetools inspect, DevOps-политика). ⚠️ TRAP[BUG]: комментарий ПОСЛЕ
# «AS validate» — новый buildkit-парсер трактует как 4-й аргумент FROM («requires either
# one or three arguments») — комментарий обязан быть на отдельной строке.
FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d AS validate

# BuildKit --mount=type=cache for apk — keeps layer small, speeds up rebuilds
RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    apk add --no-cache shellcheck yq bash

COPY config/ /tmp/config/
COPY templates/ /tmp/templates/
COPY skills/ /tmp/skills/
COPY scripts/ /tmp/scripts/
COPY plugins/ /tmp/plugins/

# Validate scripts syntax
RUN shellcheck /tmp/scripts/*.sh || true
# Validate config YAML syntax
RUN for f in /tmp/config/*.yaml; do yq eval 'true' "$f" >/dev/null 2>&1 || { echo "Invalid YAML: $f"; exit 1; }; done
# Validate template profile YAMLs
RUN find /tmp/templates -name "*.yaml" -exec sh -c 'yq eval "true" "$1" >/dev/null 2>&1 || { echo "Invalid YAML: $1"; exit 1; }' _ {} \;
# Make scripts executable
RUN chmod +x /tmp/scripts/*.sh

# endregion STAGE: validate

# region STAGE: final
# v1.0.1 digest-pin (index-digest, imagetools inspect; комментарий на отдельной строке —
# см. TRAP[BUG] в STAGE: validate)
FROM nousresearch/hermes-agent:v2026.8.3@sha256:16788311e2fa3035456bdc1bafb8ec2b1777db64ebf020af9bb7eb73c3712c9e

# ═══════════════════════════════════════════════════════════════════════
# 🐛 TRAP[UPSTREAM] · 2026-07-08 · CRITICAL · #55985 BasicAuthProvider NotImplementedError
# · Symptom: GET / → 302 → /auth/login?provider=basic → 500 (NotImplementedError)
# · Root: dashboard_auth/routes.py:196 unconditionally calls p.start_login()
# ·   but BasicAuthProvider.start_login() raises NotImplementedError
# ·   (password-only provider has no OAuth redirect flow).
# · Fix: catch NotImplementedError from start_login() and gracefully set
# ·   ls=None. The password-only provider falls through to password login page.
# ·   APPLIED: 2026-07-24 — monkey-patch in RUN step below.
# · Upstream: https://github.com/NousResearch/hermes-agent/issues/55985
# · Upstream PR: https://github.com/NousResearch/hermes-agent/pull/60978
# · Rev: when upstream releases fix (v2026.7.8+) → remove RUN patch step below,
# ·   keep this TRAP comment for audit trail.
# ═══════════════════════════════════════════════════════════════════════

# Apply monkey-patch for #55985: catch NotImplementedError from BasicAuthProvider.start_login()
COPY --from=validate /tmp/scripts/patch-basic-auth-provider.py /tmp/patch-basic-auth-provider.py
RUN python3 /tmp/patch-basic-auth-provider.py && rm /tmp/patch-basic-auth-provider.py

# ⚠️ 142 W8 (R13): rsync для context overlay (init.py 02-platform-init)
# · Symptom: hermes-agent-test smoke падал «02-platform-init: line 131: rsync: command not found»
# ·   (exited 127 → unhealthy → compose up --wait fail) — init.py использует rsync для
# ·   CONTEXT_DIR/config → /opt/hermes/config overlay (первый init на чистом volume).
# · Root: базовый образ nousresearch/hermes-agent не содержит rsync (не входит
# ·   в апстрим-сборку). Локальные dev-сборки и контекстные L2 работали, т.к. overlay
# ·   доставлялся иначе; smoke на чистом L1-образе падал.
# · Fix: install rsync via пакетный менеджер base-образа.
# · ⚠️ UPSTREAM-DRIFT · 2026-08-09 · HI · nousresearch/hermes-agent:v2026.8.3 сменил
# ·   базу Alpine → Debian 13 (trixie). `apk` больше НЕ доступен в final-стадии
# ·   (`/bin/sh: 1: apk: not found`, exit 127 в CI Build Platform Agent).
# ·   CI ранее ложно классифицировался как Docker Hub rate-limit (B38-external).
# ·   Замена: apt-get (Debian). validate-стадия (alpine:3.21) НЕ затронута — там apk валиден.
# · Prevention: init.py — единственный потребитель rsync в рантайме образа.
# · Rev: если апстрим вернётся на Alpine — восстановить apk add (меньше слой).
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/*

LABEL org.tronyxlab.patches="known-issues:#55985-basic-auth-provider-patched-2026-07-24"
LABEL org.tronyxlab.version="${PLATFORM_VERSION:-dev}"

LABEL org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform"
LABEL org.opencontainers.image.description="Hermes Agent Base (L1) — platform base image with config, profiles, skills. Pushed to ghcr.io/tronyx161 as PUBLIC distribution base (DevPlan 116 B3 T7, D1)."
LABEL org.opencontainers.image.name="hermes-agent-base"
LABEL maintainer="hermes-agent-base" \
      module="hermes-agent" \
      install_type="docker"
LABEL org.tronyxlab.role="platform-base"
LABEL org.tronyxlab.do-not-deploy="true"

# Copy validated platform artifacts
COPY --from=validate /tmp/config/ /opt/hermes/config/
COPY --from=validate /tmp/templates/ /opt/hermes/templates/
COPY --from=validate /tmp/skills/ /opt/hermes/skills/
# DevPlan 119 D5: init.py — Python-реализация init.sh (HermesInit); init.sh (02-platform-init) exec'ит его
COPY --from=validate /tmp/scripts/init.py /usr/local/bin/init.py
COPY --from=validate /tmp/scripts/init.sh /etc/cont-init.d/02-platform-init
COPY --from=validate /tmp/scripts/register-profiles.sh /etc/cont-init.d/03-register-profiles
COPY --from=validate /tmp/plugins/ /opt/hermes/plugins/

# [IMP:8] Apply config defaults: ensure directory structure
RUN mkdir -p /opt/hermes/context && chmod 755 /opt/hermes/context

# [IMP:8] HEALTHCHECK: liveness via dashboard HTTP (port 9119)
HEALTHCHECK --interval=15s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://127.0.0.1:9119/ || exit 1

# CMD is inherited from upstream hermes-agent image (s6-overlay init)
# endregion STAGE: final
# DOCREF: See HERMES_DOCS.md for full documentation reference list
