-- GREP_SUMMARY: init-observability create-database litellm langfuse postgres
-- @purpose Create databases for LiteLLM and Langfuse in shared PostgreSQL
-- @scope   Called once at first container start (docker-entrypoint-initdb.d)
-- @invariants
--   - Idempotent: IF NOT EXISTS guards against re-run on container restart
--   - LiteLLM creates its own schema (public + spend_logs table) on first startup
--   - Langfuse creates its own schema on first startup via Prisma migrations
--   - Both databases are in the shared PostgreSQL instance (postgres)
-- @rationale LiteLLM and Langfuse each require their own database in shared PG.
--           Separate databases prevent table name collisions and allow independent
--           backup/restore policies.

-- Database for LiteLLM (LLM gateway state + permanent spend_logs)
CREATE DATABASE IF NOT EXISTS litellm WITH ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8';

-- Database for Langfuse (LLM trace storage)
CREATE DATABASE IF NOT EXISTS langfuse WITH ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8';
