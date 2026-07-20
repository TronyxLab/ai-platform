# GREP_SUMMARY: status-page Dockerfile python3.12-alpine pyyaml app healthcheck
# STRUCTURE: FROM python:3.12-alpine → RUN pip install pyyaml → COPY app.py → HEALTHCHECK → CMD python3 app.py
# region MODULE_CONTRACT
## @purpose  Dockerfile for status-page — live node status aggregation service
## @scope    Builds minimal image: python:3.12-alpine + PyYAML (for node.yaml parsing)
## @invariants
##   - Base image: python:3.12-alpine (minimal, <50MB)
##   - Single external dependency: PyYAML (already used by platform tooling)
##   - HEALTHCHECK: curl localhost:8080/health
##   - CMD: python3 /app/app.py
## @rationale python3-stdlib http.server covers all requirements. PyYAML is the only
##            external dep needed for node.yaml parsing. No pip install of frameworks.
# endregion MODULE_CONTRACT

FROM python:3.12-alpine

RUN pip install --no-cache-dir pyyaml==6.0.2

COPY app.py /app/app.py

WORKDIR /app

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=5s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

CMD ["python3", "/app/app.py"]
