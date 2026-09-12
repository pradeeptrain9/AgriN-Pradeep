"""Minimal forward-only migration runner.

Alembic is overkill while the schema is still moving daily; every file in
migrations/ is idempotent SQL, applied in filename order and recorded so it is
only ever run once.
"""

import asyncio
import pathlib

import asyncpg

from app.config import get_settings
from app.db.url import asyncpg_dsn

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def _dsn() -> str:
    # asyncpg and SQLAlchemy want opposite spellings of the same SSL setting,
    # and this is the half that talks to asyncpg directly. See app/db/url.py --
    # getting this wrong applies every migration and then fails every request.
    return asyncpg_dsn(get_settings().database_url)


async def run() -> None:
    # statement_cache_size=0 because a managed provider's *pooled* endpoint is
    # PgBouncer in transaction mode, which hands each transaction a different
    # server connection. asyncpg caches prepared statements per client
    # connection and names them __asyncpg_stmt_N__, so the Nth statement
    # collides with one a previous client left on that server connection:
    #
    #   DuplicatePreparedStatementError: prepared statement
    #   "__asyncpg_stmt_3__" already exists
    #
    # Measured against PgBouncer 1.25 in transaction mode: this exact call
    # fails on a pooled DSN and succeeds on a direct one. `set -e` in the
    # entrypoint then aborts the container before it ever serves, so a node
    # given a pooler URL simply never starts.
    #
    # Unconditional rather than detected from the hostname: migrations run once
    # per boot and prepare each statement once, so the cache was worth nothing
    # here even on a direct connection.
    #
    # SQLAlchemy's asyncpg dialect is NOT affected -- it does its own prepared
    # statement handling, and 12 concurrent workers through the same pooler
    # with pooler-side support disabled produced no failures at all. The
    # request path needs nothing.
    conn = await asyncpg.connect(_dsn(), statement_cache_size=0)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                continue
            print(f"applying {path.name}")
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        print("migrations up to date")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
