import logging
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.routes import router as admin_router
from app.config import settings
from app.database import get_session, init_db
from app.models import Category, Organization, Order, Product, User
from app.services.one_c import normalize_one_c_items, schedule_one_c_sync, upsert_catalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="Partner-M API")
app.include_router(admin_router)


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    if settings.one_c_enabled:
        import asyncio
        asyncio.create_task(schedule_one_c_sync(get_session))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/integrations/1c/catalog")
async def one_c_catalog(
    payload: Any = Body(...),
    session: AsyncSession = Depends(get_session),
    token: str | None = Header(default=None, alias="X-1C-Token"),
) -> dict[str, int]:
    if settings.one_c_webhook_token and token != settings.one_c_webhook_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    items = normalize_one_c_items(payload)
    if not items:
        return {"updated": 0}
    updated = await upsert_catalog(session, items)
    return {"updated": updated}


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
