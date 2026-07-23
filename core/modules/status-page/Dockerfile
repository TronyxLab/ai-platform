# GREP_SUMMARY: status-page Dockerfile python3.12-alpine pyyaml jinja2 cryptography app templates healthcheck
# STRUCTURE: FROM python:3.12-alpine → RUN pip install pyyaml jinja2 cryptography → COPY app.py + templates/ → HEALTHCHECK → CMD python3 app.py
# region MODULE_CONTRACT
## @purpose  Dockerfile for status-page — live node status aggregation service
## @scope    Builds minimal image: python:3.12-alpine + PyYAML + Jinja2 + cryptography
## @invariants
##   - Base image: python:3.12-alpine (minimal, <50MB)
##   - Dependencies: PyYAML (node.yaml), Jinja2 (templates), cryptography (cert validation)
##   - HEALTHCHECK: python3 /health via urllib
##   - CMD: python3 /app/app.py
##   - COPY templates/ — Jinja2 template directory
## @rationale Jinja2 replaces inline HTML for maintainability. cryptography enables
##            server-side cert validation. PyYAML for node.yaml parsing.
## @changes
##   2026-07-23 | +jinja2, +cryptography, COPY templates/ | META Δ2, Δ7
# endregion MODULE_CONTRACT

FROM python:3.12-alpine

RUN apk add --no-cache curl && \
    pip install --no-cache-dir pyyaml==6.0.2 jinja2==3.1.4 cryptography==41.0.7

COPY app.py /app/app.py
COPY templates/ /app/templates/

WORKDIR /app

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=5s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

CMD ["python3", "/app/app.py"]
