import logging
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.routes import router as admin_router
from app.config import settings
from app.database import get_session, init_db
from app.integrations.onec import router as one_c_router
from app.models import Category, Organization, Order, Product, User
from app.services.one_c import schedule_one_c_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="Partner-M API")
app.include_router(admin_router)
app.include_router(one_c_router)


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    if settings.one_c_enabled:
        import asyncio
        asyncio.create_task(schedule_one_c_sync(get_session))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin/summary")
async def admin_summary(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    users = await session.execute(select(User))
    orgs = await session.execute(select(Organization))
    categories = await session.execute(select(Category))
    products = await session.execute(select(Product))
    orders = await session.execute(select(Order))
    return {
        "users": len(users.scalars().all()),
        "organizations": len(orgs.scalars().all()),
        "categories": len(categories.scalars().all()),
        "products": len(products.scalars().all()),
        "orders": len(orders.scalars().all()),
    }
