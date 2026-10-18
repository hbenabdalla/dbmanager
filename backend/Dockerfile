# syntax=docker/dockerfile:1.7
# ---------- build stage: resolve and compile dependencies ----------
# Debian 12 is pinned because the Microsoft ODBC repository below is versioned per Debian release.
FROM python:3.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
# unixodbc-dev: headers for pyodbc when no binary wheel matches the platform
RUN apt-get update && apt-get install -y --no-install-recommends build-essential unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip && /opt/venv/bin/pip install .

# ---------- runtime stage ----------
FROM python:3.12-slim-bookworm AS runtime
LABEL org.opencontainers.image.title="database-manager-backend" \
      org.opencontainers.image.description="DB Manager API, scheduler and collector workers"
# build metadata shown on Platform -> About (pass with --build-arg; nothing else is recorded)
ARG DBM_BUILD_VERSION=""
ARG DBM_GIT_COMMIT=""
ARG DBM_BUILD_DATE=""
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DBM_PROCESS_ROLE=all DBM_LOG_JSON=true PORT=8000 \
    DBM_BUILD_VERSION=${DBM_BUILD_VERSION} DBM_GIT_COMMIT=${DBM_GIT_COMMIT} DBM_BUILD_DATE=${DBM_BUILD_DATE}
# Database drivers (see docs/database-engines/index.md and Platform -> Database drivers):
#  - PostgreSQL (asyncpg), MySQL/MariaDB (PyMySQL), Oracle (python-oracledb thin mode) are pure
#    Python wheels: no client libraries and no Oracle software are installed in the image.
#  - Db2 (ibm_db) ships IBM's CLI driver inside its wheel; libxml2 is its only system dependency.
#  - SQL Server needs unixODBC and Microsoft ODBC Driver 18. Installing msodbcsql18 means accepting
#    the Microsoft EULA (https://aka.ms/odbc18eula). The build FAILS if packages.microsoft.com is
#    unreachable, unless you opt out explicitly with MSODBC_OPTIONAL=true (then Platform > Database
#    drivers shows the SQL Server driver as MISSING and SQL Server registrations explain why).
#    INSTALL_MSODBC=false skips the attempt entirely (same runtime result).
ARG INSTALL_MSODBC=true
ARG MSODBC_OPTIONAL=false
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends unixodbc libxml2 ca-certificates curl gnupg \
    && if [ "$INSTALL_MSODBC" = "true" ]; then \
         ( curl -fsSL --retry 3 https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
           && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
                > /etc/apt/sources.list.d/mssql-release.list \
           && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
           && odbcinst -q -d -n "ODBC Driver 18 for SQL Server" ) \
         || { rm -f /etc/apt/sources.list.d/mssql-release.list; \
              if [ "$MSODBC_OPTIONAL" = "true" ]; then \
                echo "WARNING: Microsoft ODBC Driver 18 could not be installed; SQL Server support will report the driver as MISSING."; \
              else \
                echo "ERROR: Microsoft ODBC Driver 18 for SQL Server could not be installed (is packages.microsoft.com reachable?)."; \
                echo "       Fix network access, or build with MSODBC_OPTIONAL=true to continue without SQL Server support."; \
                exit 1; \
              fi; } ; \
       fi \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --gid 0 --no-create-home --shell /usr/sbin/nologin dbm
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=10001:0 alembic.ini ./
COPY --chown=10001:0 alembic ./alembic
COPY --chown=10001:0 app ./app
# OpenShift runs with an arbitrary UID in group 0: keep files group-readable, nothing writable needed.
RUN chmod -R g=u /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# --proxy-headers: the frontend nginx (or ingress) forwards client IP/proto; trusted list via FORWARDED_ALLOW_IPS.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-server-header"]

# ---------- integration-test runner (docker-compose.test.yml, service "it-runner") ----------
# Same image as runtime plus the test tooling; the tests themselves are mounted at run time.
FROM runtime AS it
USER root
RUN /opt/venv/bin/pip install --no-cache-dir "pytest>=8" "pytest-asyncio>=0.23" \
    && mkdir -p /results && chown 10001:0 /results
USER 10001
ENV DBM_IT_RESULTS_DIR=/results DBM_TIMESERIES_BACKEND=memory
CMD ["python", "-m", "pytest", "-m", "real_database", "tests/integration", "-p", "no:cacheprovider", "-q", "-rs"]
