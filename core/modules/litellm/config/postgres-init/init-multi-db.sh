#!/bin/sh
# GREP_SUMMARY: postgres init script litellm langfuse multi-database
# STRUCTURE: psql → SELECT CREATE DATABASE litellm → SELECT CREATE DATABASE langfuse → \gexec
# region MODULE_CONTRACT
## @purpose  Create separate databases for litellm and langfuse test containers.
##           Postgres init script (shell, not SQL) because CREATE DATABASE cannot
##           be executed from a PL/pgSQL function.
## @invariants
##   - litellm and langfuse databases are created if they don't exist
##   - Uses psql \gexec pattern to bypass CREATE DATABASE restriction
## @rationale CREATE DATABASE is a utility statement, not SQL — cannot run inside
##            a function. Shell script runs before PostgreSQL accepts connections.
# endregion MODULE_CONTRACT
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
SELECT 'CREATE DATABASE litellm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec
SELECT 'CREATE DATABASE langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
EOSQL
