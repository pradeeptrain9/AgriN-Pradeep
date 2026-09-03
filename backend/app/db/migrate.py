"""Minimal forward-only migration runner.

Alembic is overkill while the schema is still moving daily; every file in
migrations/ is idempotent SQL, applied in filename order and recorded so it is
only ever run once.
"""

import asyncio
import pathlib

import asyncpg

from app.config import get_settings

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def run() -> None:
    conn = await asyncpg.connect(_dsn())
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
