from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.url import sqlalchemy_url

_settings = get_settings()

# Not settings.database_url directly: a managed provider's string uses libpq's
# spelling, which this dialect rejects. See app/db/url.py.
engine = create_async_engine(
    sqlalchemy_url(_settings.database_url), pool_size=10, max_overflow=20
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
