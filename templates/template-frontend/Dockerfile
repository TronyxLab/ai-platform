# GREP_SUMMARY: multi-stage build node alpine nginx healthcheck vite
# STRUCTURE: node:22-alpine(build stage: npm ci + vite build) → nginx:alpine(run stage) + healthcheck
# region DOCKERFILE
## @purpose  Multi-stage Docker build: Node.js build → Nginx serve (DevPlan 141 B4)
## @scope    Frontend projects (Vite/React/TypeScript SPA)
## @invariants
##   - package.json — всегда присутствует (шаблон поставляется с Vite-тулкитом)
##   - Build stage: npm ci → npm run build (tsc + vite build)
##   - Run stage serves static files via Nginx with healthcheck
## @rationale Multi-stage keeps image small; healthcheck enables zero-downtime atomic deploys (06 §7)
# endregion DOCKERFILE

# ---- Build Stage ----
# 170 W12 C1: digest-pins (imagetools inspect, 2026-08-15)
FROM node:22.23.0-alpine@sha256:ab07539e0988b63558ff621f5fbe1077054c39d9809112974fb79993949d41cd AS build
WORKDIR /app
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm install --no-audit --no-fund
COPY . .
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm run build

# ---- Run Stage ----
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# DevPlan 170 W2-A1 (C3): localhost → 127.0.0.1 — канон modules/AGENTS.md (localhost → ::1, nginx слушает IPv4)
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:80/health || exit 1
