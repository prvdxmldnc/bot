from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session, init_db
from app.models import Category, Organization, Order, Product, User

app = FastAPI(title="Partner-M API")


@app.on_event("startup")
async def startup() -> None:
    await init_db()


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
