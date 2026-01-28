from time import sleep

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    retries = 10
    delay = 1
    for attempt in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception:
            if attempt == retries - 1:
                raise
            sleep(delay)
            delay = min(delay * 2, 10)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
