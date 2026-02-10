from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def db_session(session_factory: sessionmaker) -> Session:
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
