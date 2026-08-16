-- @purpose  Create observability databases and pg_stat_statements extension on first container start
-- @scope    Runs once during postgres container initialisation
-- @invariants
--   - litellm: LiteLLM proxy observability database
--   - langfuse: Langfuse tracing observability database
--   - platform: already created via POSTGRES_DB env, not recreated here
--   - pg_stat_statements: required for query observability (shared_preload_libraries must include it)
-- @rationale These databases are required by the observability stack.
--           Created by postgres:16 entrypoint on first run only.
--           pg_stat_statements extension must be created per shared_preload_libraries config (T5.2).
-- GREP_SUMMARY: initdb create-database litellm langfuse observability pg_stat_statements
CREATE DATABASE litellm;
CREATE DATABASE langfuse;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
