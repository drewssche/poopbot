import os

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing in .env")
    return url


engine: AsyncEngine = create_async_engine(get_database_url(), echo=False)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
