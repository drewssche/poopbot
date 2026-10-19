"""One configured connection pool for handlers and scheduler in this process."""
import logging

from app.core.config import Settings
from app.db.engine import make_engine, make_session_factory

_engine = None
_session_factory = None
_database_url = None


def configure_database(settings: Settings):
    global _engine, _session_factory, _database_url
    if _engine is not None:
        raise RuntimeError('Database runtime is already configured')
    _engine = make_engine(settings.database_url, pool_size=settings.db_pool_size,
                          max_overflow=settings.db_max_overflow,
                          pool_timeout_sec=settings.db_pool_timeout_sec,
                          pool_recycle_sec=settings.db_pool_recycle_sec)
    _session_factory = make_session_factory(_engine)
    _database_url = settings.database_url
    logging.getLogger(__name__).info('Shared database pool configured size=%s max_overflow=%s',
                                    settings.db_pool_size, settings.db_max_overflow)
    return _session_factory


def get_session_factory(database_url: str):
    if _session_factory is None:
        raise RuntimeError('Database runtime must be configured before handling updates')
    if database_url != _database_url:
        raise RuntimeError('Handler database does not match configured runtime')
    return _session_factory


def close_database():
    global _engine, _session_factory, _database_url
    if _engine is not None:
        _engine.dispose()
    _engine = _session_factory = _database_url = None
