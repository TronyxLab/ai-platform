# GREP_SUMMARY: status-page Dockerfile python3.12-alpine pyyaml jinja2 cryptography app templates healthcheck
# STRUCTURE: FROM python:3.12-alpine → RUN pip install pyyaml jinja2 cryptography → COPY app.py + collectors/ + renderer/ + templates/ → HEALTHCHECK → CMD python3 app.py
# region MODULE_CONTRACT
## @purpose  Dockerfile for status-page — live node status aggregation service
## @scope    Builds minimal image: python:3.12-alpine + PyYAML + Jinja2 + cryptography
## @invariants
##   - Base image: python:3.12-alpine (minimal, <50MB)
##   - Dependencies: PyYAML (node.yaml), Jinja2 (templates), cryptography (cert validation)
##   - HEALTHCHECK: python3 /health via urllib
##   - CMD: python3 /app/app.py
##   - COPY collectors/ + renderer/ — Python-пакеты (DevPlan 170 W7-E2, декомпозиция монолитов)
##   - COPY templates/ — Jinja2 template directory
## @rationale Jinja2 replaces inline HTML for maintainability. cryptography enables
##            server-side cert validation. PyYAML for node.yaml parsing.
## @changes
##   2026-07-23 | +jinja2, +cryptography, COPY templates/ | META Δ2, Δ7
##   2026-08-15 | COPY collectors.py/renderer.py → COPY collectors/ + renderer/ (пакеты) | 170 W7-E2
# endregion MODULE_CONTRACT

# 170 W12 C1: digest-pin (imagetools inspect python:3.12-alpine, 2026-08-15)
FROM python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff

RUN apk add --no-cache curl && \
    pip install --no-cache-dir pyyaml==6.0.2 jinja2==3.1.4 cryptography==41.0.7

COPY app.py /app/app.py
COPY collectors/ /app/collectors/
COPY renderer/ /app/renderer/
COPY templates/ /app/templates/

WORKDIR /app

# /healthz — lightweight readiness probe (~50ms): checks status-metrics.json exists, readable, fresh.
# Unlike /health (full system checks), this is fast enough for Docker HEALTHCHECK timeout (5s).
# DevPlan 170 W2-A1 (C3): localhost → 127.0.0.1 — канон modules/AGENTS.md (localhost резолвится в ::1,
# приложение слушает IPv4 → healthcheck фейлится).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=5s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

EXPOSE 8080

CMD ["python3", "/app/app.py"]
