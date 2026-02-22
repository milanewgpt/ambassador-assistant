"""
Async database helpers using asyncpg against Supabase Postgres.
"""

import asyncpg
from contextlib import asynccontextmanager
from app.config import settings
from app.utils.logging import log

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        log.info("Creating database connection pool …")
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
            statement_cache_size=0,
        )
        log.info("Database pool ready.")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Database pool closed.")


@asynccontextmanager
async def acquire():
    """Yield a connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def fetch_one(query: str, *args):
    async with acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch_all(query: str, *args):
    async with acquire() as conn:
        return await conn.fetch(query, *args)


async def execute(query: str, *args):
    async with acquire() as conn:
        return await conn.execute(query, *args)


async def fetch_val(query: str, *args):
    async with acquire() as conn:
        return await conn.fetchval(query, *args)
