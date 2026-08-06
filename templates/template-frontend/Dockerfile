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
FROM node:22.23.0-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm install --no-audit --no-fund
COPY . .
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm run build

# ---- Run Stage ----
FROM nginx:1.28-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost:80/health || exit 1
