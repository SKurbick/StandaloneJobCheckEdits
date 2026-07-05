"""Database connection helpers for the refactored standalone job."""

from contextlib import asynccontextmanager

import asyncpg
from loguru import logger

try:
    from .config import database as db_config
except ImportError:
    from config import database as db_config


class Database1:
    def __init__(
        self,
        user=db_config.DB_USER,
        password=db_config.DB_PASSWORD,
        database=db_config.DB_NAME,
        host=db_config.DB_HOST,
        port=db_config.DB_PORT,
    ):
        self._user = user
        self._password = password
        self._database = database
        self._host = host
        self._port = port
        self._pool = None
        self._min_size = db_config.DB_POOL_MIN_SIZE
        self._max_size = db_config.DB_POOL_MAX_SIZE
        self._timeout = db_config.DB_CONNECT_TIMEOUT
        self._command_timeout = db_config.DB_COMMAND_TIMEOUT

    async def connect(self):
        logger.info("Connecting to database with pool min_size={} max_size={}...", self._min_size, self._max_size)
        self._pool = await asyncpg.create_pool(
            user=self._user,
            password=self._password,
            database=self._database,
            host=self._host,
            port=self._port,
            max_size=self._max_size,
            min_size=self._min_size,
            timeout=self._timeout,
            command_timeout=self._command_timeout,
        )

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @asynccontextmanager
    async def acquire(self):
        if not self._pool:
            raise RuntimeError("Database pool is not initialized")
        async with self._pool.acquire() as connection:
            yield connection

    async def fetch(self, query, *args):
        async with self.acquire() as connection:
            return await connection.fetch(query, *args)

    async def executemany(self, query, args):
        async with self.acquire() as connection:
            return await connection.executemany(query, args)

