# GREP_SUMMARY: multi-stage build node alpine nginx healthcheck
# STRUCTURE: node:22-alpine(build stage) → nginx:alpine(run stage) + healthcheck
# region DOCKERFILE
## @purpose  Multi-stage Docker build: Node.js build → Nginx serve (04-templates §3)
## @scope    Frontend projects (SPA / static sites)
## @invariants
##   - Build stage compiles/minifies the frontend
##   - Run stage serves static files via Nginx with healthcheck
## @rationale Multi-stage keeps image small; healthcheck enables zero-downtime atomic deploys (06 §7)
# endregion DOCKERFILE

# ---- Build Stage ----
FROM node:22.23.0-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    if [ -f package.json ]; then npm ci --prefer-offline --no-audit; fi
COPY src/ ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    if [ -f package.json ]; then npm run build; fi

# ---- Run Stage ----
FROM nginx:1.28-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app /usr/share/nginx/html
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost:80/health || exit 1
