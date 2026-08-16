# GREP_SUMMARY: snippets db asyncpg pool PLATFORM_POSTGRES_DSN reference
# STRUCTURE: ┌get_pool()┐ → ◇ lazy create_pool(dsn) → ◇ close_pool() → ⎋ pool|None
# region MODULE_CONTRACT
## @purpose  Reference: asyncpg connection pool (DevPlan 141 Q4). НЕ подключается автоматически —
##           скопируйте в src/db.py и раскомментируйте asyncpg в requirements.txt при необходимости.
## @scope    Backend projects, использующие PostgreSQL (PLATFORM_POSTGRES_DSN из .env.platform)
## @invariants
##   - DSN пуст → RuntimeError с инструкцией (make sync-env)
##   - Пул lazy-создаётся один раз; close_pool() — на shutdown
## @rationale Не всем backend-проектам нужна БД — asyncpg опциональная зависимость
# endregion MODULE_CONTRACT

"""Database connection pool via asyncpg — reads PLATFORM_POSTGRES_DSN from .env.platform."""

import logging

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        if not settings.postgres_dsn:
            raise RuntimeError("PLATFORM_POSTGRES_DSN not set. Run `make sync-env` or set manually.")
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
        )
        logger.info("Database pool created")
    return _pool


async def close_pool() -> None:
    """Close the connection pool (call on shutdown)."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")
