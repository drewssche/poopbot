from __future__ import annotations

import logging
import time
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Порог логирования медленных запросов (мс)
SLOW_QUERY_THRESHOLD_MS = 500


def make_engine(
    database_url: str,
    *,
    pool_size: int = 6,
    max_overflow: int = 4,
    pool_timeout_sec: int = 30,
    pool_recycle_sec: int = 1800,
) -> Engine:
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_sec,
        pool_recycle=pool_recycle_sec,
        pool_use_lifo=True,
    )
    
    # Логирование медленных SQL-запросов
    @event.listens_for(engine, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, params, context, executemany):
        conn.info.setdefault("query_start_time", []).append(time.time())
    
    @event.listens_for(engine, "after_cursor_execute")
    def receive_after_cursor_execute(conn, cursor, statement, params, context, executemany):
        total_ms = (time.time() - conn.info["query_start_time"].pop(-1)) * 1000
        if total_ms > SLOW_QUERY_THRESHOLD_MS:
            logger.warning(
                "Slow query: %.0fms — %s",
                total_ms,
                statement[:200].replace("\n", " "),
            )
    
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
