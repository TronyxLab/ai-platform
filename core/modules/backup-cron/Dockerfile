## GREP_SUMMARY: backup-cron Dockerfile pgdg cron debian bash pg-client-18 age date-parser s3-client retention upload wal-sync healthcheck entrypoint-py REF-0009
## STRUCTURE: FROM debian:bookworm-slim → add PGDG repo → install cron+pg-client-18+age+python → COPY scripts (incl cleanup_spool+age_cipher+spool_retry) → COPY crontab (cron.d ONLY) → CMD python3 entrypoint
# region MODULE_CONTRACT
## @purpose  Build backup-cron image with cron daemon + backup scripts + pg_dump client
## @scope    Phase 02: installs cron schedule; S3 upload = stub (phase 06 fills upload-s3.sh)
## @invariants
##   - Base: debian:bookworm-slim (stable, minimal, has apt)
##   - Packages: cron, postgresql-client-18 (pg_dump, via PGDG repo), age (dump encryption,
##     REF-0009), curl (S3 stub), ca-certificates
##   - PGDG APT repo added before installing postgresql-client-18 (not in Debian bookworm default repos)
##   - Crontab installed to /etc/cron.d/platform-backup ONLY (no `crontab` spool copy — AI-0070)
##   - Scripts mounted/copied to /usr/local/bin/
##   - No EXPOSE — backup-net internal only
##   - Resource limits NOT in image (AR3)
## @rationale Separate image (not postgres image) — backup logic may evolve independently of DB version
## @changes 2026-08-08 | DevPlan 143 W1B — entrypoint.py (cron env dump) + CMD python3 entrypoint
# endregion MODULE_CONTRACT

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

LABEL maintainer="core" \
      module="backup-cron" \
      install_type="docker" \
      phase="02-skeleton" \
      org.opencontainers.image.source="https://github.com/Tronyx161/ai-platform" \
      org.opencontainers.image.description="Backup cron — pg_dump + S3 upload with cron scheduling" \
      org.opencontainers.image.name="ghcr.io/tronyxlab/backup-cron"

# [IMP:8][backup-cron][Dockerfile] Add PostgreSQL APT repo for pg_dump 18 (not in Debian bookworm)
# Phase 06: added python3 + pip for boto3 S3 operations
# RUN layers merged + BuildKit --mount=type=cache for apt/pip — saves ~50MB image size, faster rebuilds
# ⚠️ TRAP[BUG] · 2026-08-06 · HI · B18b (141 r2): apt fail на ноде (Dockerfile:33)
# · Symptom: docker compose build backup-cron падал на apt postgresql-client-18 (только на VPS;
# ·   локальный build успешен). Кандидат-корень: BuildKit cache-mount /var/lib/apt/lists хранит
# ·   битые/частичные списки от прерванного build → "Unable to locate package postgresql-client-18".
# · Fix: (1) cache-mount /var/lib/apt/lists УДАЛЁН — каждый build делает свежий apt-get update;
# ·   (2) ретрай-проход install: при фейле — rm -rf списков + apt-get update + повторный install.
# · Rev: если фейл повторится на ноде после фикса — смотреть сетевую доступность apt.postgresql.org.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg lsb-release \
  && install -m 0755 -d /etc/apt/keyrings \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
  && sh -c 'echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
  && apt-get update \
  && (apt-get install -y --no-install-recommends \
        cron \
        postgresql-client-18 \
        procps \
        age \
        python3 \
        python3-pip \
        awscli \
      || (rm -rf /var/lib/apt/lists/* \
          && apt-get update \
          && apt-get install -y --no-install-recommends \
            cron \
            postgresql-client-18 \
            procps \
            age \
            python3 \
            python3-pip \
            awscli)) \
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
# REF-0009: sentinel-gated cleanup logic (Python port) + age encryption helper +
# daily spool rescan retry (thin facade spool-retry-upload.sh)
COPY scripts/cleanup_spool.py    /usr/local/bin/cleanup_spool.py
COPY scripts/age_cipher.py       /usr/local/bin/age_cipher.py
COPY scripts/spool_retry.py      /usr/local/bin/spool_retry.py
COPY scripts/spool-retry-upload.sh /usr/local/bin/spool-retry-upload.sh
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

# DevPlan 143 W1B: Python entrypoint — dumps container env to /etc/environment
# before exec'ing cron -f (Debian cron reads /etc/environment at daemon start →
# jobs inherit POSTGRES_HOST/S3_*/WAL_*). Language policy: new code = Python.
COPY scripts/entrypoint.py /usr/local/bin/entrypoint.py

# Copy crontab
COPY scripts/crontab /etc/cron.d/platform-backup

# Combined chmod: crontab permissions + script executability
# REF-0009 (AI-0070): двойная установка crontab УДАЛЕНА — /etc/cron.d/platform-backup
# достаточно (Debian cron читает cron.d напрямую); `crontab <file>` дублировал расписание
# в spool root'а (риск двойного запуска + рассинхронизации при правках).
RUN chmod 0644 /etc/cron.d/platform-backup && \
    chmod +x \
      /usr/local/bin/backup-postgres.sh \
      /usr/local/bin/backup_postgres.py \
      /usr/local/bin/backup-app-data.sh \
      /usr/local/bin/backup-cleanup.sh \
      /usr/local/bin/spool-retry-upload.sh \
      /usr/local/bin/upload-s3.sh \
      /usr/local/bin/healthcheck.sh \
      /usr/local/bin/entrypoint.py

# [IMP:8][backup-cron][Dockerfile] No EXPOSE — backup-net internal only
# DevPlan 143 W1B: entrypoint.py writes /etc/environment (cron env) then exec's cron -f.
# PID 1 = cron (healthcheck `pgrep cron` semantics unchanged).
CMD ["python3", "/usr/local/bin/entrypoint.py"]
