from app.db.base import Base
from app.db.engine import engine
from app.db import models  # noqa: F401  (нужно, чтобы модель зарегистрировалась)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
