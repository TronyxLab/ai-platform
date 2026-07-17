## GREP_SUMMARY: postgres Dockerfile postgres16 fixed-version initdb healthcheck
## STRUCTURE: FROM postgres:16 → COPY config → COPY healthcheck → EXPOSE none
# region MODULE_CONTRACT
## @purpose  Build postgres image with platform config baked in
## @scope    Single-stage; no multi-stage needed (data managed via volume)
## @invariants
##   - Base image pinned to postgres:16 (version controlled in module.yaml)
##   - No EXPOSE directive — network isolation via shared-db-net only
##   - Platform configs copied into /etc/postgresql/
##   - Healthcheck scripts copied to /usr/local/bin/
##   - Resource limits NOT set in image (AR3 — set at compose level if needed, currently none)
## @rationale Fixed postgres version prevents surprise upgrades mid-deployment (06 §9)
# endregion MODULE_CONTRACT

FROM postgres:16.14

LABEL maintainer="core" \
      module="postgres" \
      install_type="docker" \
      org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform" \
      org.opencontainers.image.description="PostgreSQL 16.14 shared database — platform config baked in" \
      org.opencontainers.image.name="ghcr.io/tronyxlab/postgres"

# Copy platform postgresql config
COPY config/postgresql.conf /etc/postgresql/postgresql.conf
COPY config/pg_hba.conf /etc/postgresql/pg_hba.conf

# Copy healthcheck scripts
COPY healthcheck.sh /usr/local/bin/healthcheck.sh
COPY ready-check.sh /usr/local/bin/ready-check.sh

# Copy init scripts (database creation for platform projects)
COPY init/ /docker-entrypoint-initdb.d/

RUN chmod +x /usr/local/bin/healthcheck.sh /usr/local/bin/ready-check.sh

# [IMP:8][postgres][Dockerfile] No EXPOSE — ports blocked at network level (shared-db-net only)
# Internal postgres port 5432 reachable only within shared-db-net

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
