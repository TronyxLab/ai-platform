## GREP_SUMMARY: backup-cron Dockerfile pgdg cron debian bash pg-client-16 date-parser s3-client retention upload wal-sync healthcheck
## STRUCTURE: FROM debian:bookworm-slim → add PGDG repo → install cron+pg-client-16+python → COPY scripts (incl date_parser+s3_client+wal_sync) → COPY crontab → ENTRYPOINT cron
# region MODULE_CONTRACT
## @purpose  Build backup-cron image with cron daemon + backup scripts + pg_dump client
## @scope    Phase 02: installs cron schedule; S3 upload = stub (phase 06 fills upload-s3.sh)
## @invariants
##   - Base: debian:bookworm-slim (stable, minimal, has apt)
##   - Packages: cron, postgresql-client-16 (pg_dump, via PGDG repo), curl (S3 stub), ca-certificates
##   - PGDG APT repo added before installing postgresql-client-16 (not in Debian bookworm default repos)
##   - Crontab installed to /etc/cron.d/platform-backup
##   - Scripts mounted/copied to /usr/local/bin/
##   - No EXPOSE — backup-net internal only
##   - Resource limits NOT in image (AR3)
## @rationale Separate image (not postgres image) — backup logic may evolve independently of DB version
# endregion MODULE_CONTRACT

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

LABEL maintainer="core" \
      module="backup-cron" \
      install_type="docker" \
      phase="02-skeleton" \
      org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform" \
      org.opencontainers.image.description="Backup cron — pg_dump + S3 upload with cron scheduling" \
      org.opencontainers.image.name="ghcr.io/tronyxlab/backup-cron"

# [IMP:8][backup-cron][Dockerfile] Add PostgreSQL APT repo for pg_dump 16 (not in Debian bookworm)
# ⚠️ TRAP[BUG] · 2026-07-08 · HI · postgresql-client-16 not in Debian bookworm repos
# Phase 06: added python3 + pip for boto3 S3 operations
# RUN layers merged + BuildKit --mount=type=cache for apt/pip — saves ~50MB image size, faster rebuilds
# ⚠️ TRAP[BUG] awscli 2.9.19 requires urllib3<1.27 — upgraded urllib3 may break aws CLI
#   workaround: boto3 uses urllib3 directly, awscli fallback to manual S3 commands
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg lsb-release \
  && install -m 0755 -d /etc/apt/keyrings \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
  && sh -c 'echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    cron \
    postgresql-client-16 \
    procps \
    python3 \
    python3-pip \
    awscli \
  && mkdir -p /var/lib/platform/backup-spool/postgres \
              /var/lib/platform/backup-spool/app-data \
              /var/log/platform/backup \
  && rm -rf /var/lib/apt/lists/* \
  && pip3 install --break-system-packages --no-cache-dir boto3==1.43.* \
  && pip3 install --break-system-packages --no-cache-dir \
    "urllib3>=2.7.0,<3" \
    "pyjwt>=2.13.0,<3" \
    "pygments>=2.20.0,<3"

# Copy backup scripts
COPY scripts/backup-postgres.sh  /usr/local/bin/backup-postgres.sh
COPY scripts/backup_postgres.py  /usr/local/bin/backup_postgres.py
COPY scripts/backup-app-data.sh  /usr/local/bin/backup-app-data.sh
COPY scripts/backup-cleanup.sh   /usr/local/bin/backup-cleanup.sh
COPY scripts/upload-s3.sh        /usr/local/bin/upload-s3.sh
COPY scripts/upload.py           /usr/local/bin/upload.py
COPY scripts/backup_config.py    /usr/local/bin/backup_config.py
COPY scripts/retention.py        /usr/local/bin/retention.py
# DevPlan 119 C1 (AC-C1.1): retention.py imports date_parser + s3_client — эти модули
# ОБЯЗАТЕЛЬНО копируются в образ, иначе cron-запуск retention падает на ImportError.
COPY scripts/date_parser.py      /usr/local/bin/date_parser.py
COPY scripts/s3_client.py        /usr/local/bin/s3_client.py
# DevPlan 132 W2: wal_sync.py (WAL → S3, hourly cron) — импортирует s3_client (уже скопирован)
COPY scripts/wal_sync.py         /usr/local/bin/wal_sync.py

# Copy healthcheck scripts
COPY healthcheck.sh  /usr/local/bin/healthcheck.sh

# Copy crontab
COPY scripts/crontab /etc/cron.d/platform-backup

# Combined chmod: crontab permissions + script executability
RUN chmod 0644 /etc/cron.d/platform-backup && \
    crontab /etc/cron.d/platform-backup && \
    chmod +x \
      /usr/local/bin/backup-postgres.sh \
      /usr/local/bin/backup_postgres.py \
      /usr/local/bin/backup-app-data.sh \
      /usr/local/bin/backup-cleanup.sh \
      /usr/local/bin/upload-s3.sh \
      /usr/local/bin/healthcheck.sh

# [IMP:8][backup-cron][Dockerfile] No EXPOSE — backup-net internal only
# ENTRYPOINT: start cron in foreground (Docker requires PID 1 to be long-lived)
CMD ["cron", "-f"]
