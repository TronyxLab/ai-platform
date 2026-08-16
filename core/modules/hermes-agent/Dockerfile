# GREP_SUMMARY: hermes-agent Dockerfile multi-stage validate base final CONTEXT-guard hermes-agent-context user-10000
# STRUCTURE: ▶ STAGE validate (alpine: shellcheck+yq+chmod) → ▶ STAGE base (FROM L0 + build/ артефакты + monkey-patch + rsync) → ▶ STAGE final (FROM base + context/ + 04-context-init + USER 10000 + HEALTHCHECK)
# region MODULE_CONTRACT
## @purpose  Единый multi-stage Dockerfile для hermes-agent-context (L1→L2 коллапс, DevPlan 002):
##           base-стадия (бывший L1) + final-стадия (бывший L2 context overlay).
##           Строится из repo root (context = корень репозитория) — COPY-пути core/modules/hermes-agent/…
## @scope    Build context: repo root (.) — paths relative to repo root: core/modules/hermes-agent/build/,
##           core/modules/hermes-agent/context/. Вызывается `make hermes-build-context CONTEXT=<org>`
##           → core.internal.build.hermes_images build-context.
## @invariants
##   - Stage validate: lightweight alpine image for pre-copy validation (shellcheck, yq, chmod)
##   - Stage base: FROM nousresearch/hermes-agent:v2026.8.3@sha256:… (L0, digest-pin) + platform
##     artifacts (config/skills/profiles/plugins) + monkey-patch #55985 + rsync (upstream Debian)
##   - Stage final: FROM base + context config/skills + init-context.sh (04-context-init) +
##     USER 10000:10000 (non-root, DevPlan 140 W6) + HEALTHCHECK
##   - REQUIRED build-arg: CONTEXT — persists as runtime ENV (init-context.sh guard exits 1 if empty)
##   - Порядок COPY: base-слои до context-слоёв (layer-cache — gha-cache переиспользует base-слои)
##   - NO EXPOSE — agent accessed through proxy-net (nginx reverse proxy) only
##   - Resource limits NOT set (AR3)
## @rationale L1 (бывш. отдельный base-образ) схлопнут в L2 (hermes-agent-context): единый образ, один
##            Dockerfile, L1-distribution больше не публикуется. Version-машинерия (sha/version-теги,
##            bare-tag, l1-distribution-digest) удалена. Контекстные org собирают L2 из source.
## @changes  2026-08-16 | DevPlan 002 W1 T1.1 — создан (коллапс build/Dockerfile + context/Dockerfile)
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

COPY core/modules/hermes-agent/build/config/ /tmp/config/
COPY core/modules/hermes-agent/build/templates/ /tmp/templates/
COPY core/modules/hermes-agent/build/skills/ /tmp/skills/
COPY core/modules/hermes-agent/build/scripts/ /tmp/scripts/
COPY core/modules/hermes-agent/build/plugins/ /tmp/plugins/

# Validate scripts syntax
RUN shellcheck /tmp/scripts/*.sh || true
# Validate config YAML syntax
RUN for f in /tmp/config/*.yaml; do yq eval 'true' "$f" >/dev/null 2>&1 || { echo "Invalid YAML: $f"; exit 1; }; done
# Validate template profile YAMLs
RUN find /tmp/templates -name "*.yaml" -exec sh -c 'yq eval "true" "$1" >/dev/null 2>&1 || { echo "Invalid YAML: $1"; exit 1; }' _ {} \;
# Make scripts executable
RUN chmod +x /tmp/scripts/*.sh

# endregion STAGE: validate

# region STAGE: base
# v1.0.1 digest-pin (index-digest, imagetools inspect; комментарий на отдельной строке —
# см. TRAP[BUG] в STAGE: validate)
FROM nousresearch/hermes-agent:v2026.8.3@sha256:16788311e2fa3035456bdc1bafb8ec2b1777db64ebf020af9bb7eb73c3712c9e AS base

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
# ·   в апстрим-сборку).
# · Fix: install rsync via пакетный менеджер base-образа.
# · ⚠️ UPSTREAM-DRIFT · 2026-08-09 · HI · nousresearch/hermes-agent:v2026.8.3 сменил
# ·   базу Alpine → Debian 13 (trixie). `apk` больше НЕ доступен в final-стадии
# ·   (`/bin/sh: 1: apk: not found`, exit 127 в CI Build Platform Agent).
# ·   Замена: apt-get (Debian). validate-стадия (alpine:3.21) НЕ затронута — там apk валиден.
# · Prevention: init.py — единственный потребитель rsync в рантайме образа.
# · Rev: если апстрим вернётся на Alpine — восстановить apk add (меньше слой).
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/*

LABEL org.tronyxlab.patches="known-issues:#55985-basic-auth-provider-patched-2026-07-24"
LABEL org.tronyxlab.version="${PLATFORM_VERSION:-dev}"

LABEL org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform"
LABEL org.opencontainers.image.description="Hermes Agent Context — единый образ (L1→L2 коллапс): base-артефакты + контекстный overlay + CONTEXT guard. Собирается из source (DevPlan 002)."
LABEL org.opencontainers.image.name="hermes-agent-context"
LABEL maintainer="hermes-agent-context" \
      module="hermes-agent" \
      install_type="docker"
LABEL org.tronyxlab.role="context"

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

# endregion STAGE: base

# region STAGE: final (context overlay)
## @purpose     Build context overlay on top of base stage (CONTEXT guard, context skills/configs, non-root)
FROM base

# ── Labels ────────────────────────────────────────────────────────────────
LABEL org.tronyxlab.module="hermes-agent-context" \
      org.tronyxlab.layer="context" \
      role="context"

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
# [IMP:8] Context config overlay (e.g., Telegram config, per-context settings)
# These files are rsync'd over base configs by init-context.sh at container start
COPY core/modules/hermes-agent/context/config/ /opt/hermes/context/config/

# [IMP:8] Context skills overlay (per-context custom skills)
# Placed alongside base skills, merged at container start by init-context.sh
COPY core/modules/hermes-agent/context/skills/ /opt/hermes/context/skills/

# [IMP:9] Context init script with CONTEXT guard
# Runs after base s6 init scripts (02-platform-init, 03-register-profiles)
COPY core/modules/hermes-agent/context/scripts/init-context.sh /etc/cont-init.d/04-context-init

# ── Ensure init script is executable ──────────────────────────────────────
RUN chmod +x /etc/cont-init.d/04-context-init

# ── Non-root runtime (DevPlan 140 W6: hermes-root-500) ──
# USER 10000:10000 — соответствует HERMES_UID/HERMES_GID в build/scripts/init.py
# (chown-if-root страховка в init.py остаётся для base-root случая; final работает non-root)
# ⚠️ RISK (DevPlan 140 W6 §7): s6-overlay /init (PID 1, ENTRYPOINT унаследован от upstream)
# спроектирован для root-PID1; non-root запуск верифицируется на test-VPS ДО промоута
# (docker inspect Config.User + dashboard 9119 + API 8642); откат = revert этого блока.
USER 10000:10000

# ── HEALTHCHECK ───────────────────────────────────────────────────────────
# [IMP:8] Liveness via dashboard HTTP endpoint (port 9119)
# Port 9119 non-privileged — curl as UID 10000 is permitted (no root needed)
HEALTHCHECK --interval=15s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://127.0.0.1:9119/ || exit 1

# CMD is inherited from upstream hermes-agent image (s6-overlay init)
# endregion STAGE: final (context overlay)
# DOCREF: See HERMES_DOCS.md for full documentation reference list
