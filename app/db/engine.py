from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def make_engine(
    database_url: str,
    *,
    pool_size: int = 2,
    max_overflow: int = 0,
    pool_timeout_sec: int = 10,
    pool_recycle_sec: int = 1800,
) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_sec,
        pool_recycle=pool_recycle_sec,
        pool_use_lifo=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
