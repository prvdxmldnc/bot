from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product

logger = logging.getLogger(__name__)


async def fetch_one_c_catalog() -> list[dict[str, Any]]:
    if not settings.one_c_base_url:
        return []
    url = f"{settings.one_c_base_url.rstrip('/')}/catalog"
    auth = None
    if settings.one_c_username and settings.one_c_password:
        auth = (settings.one_c_username, settings.one_c_password)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, auth=auth)
        response.raise_for_status()
        data = response.json()
    return data.get("items", []) if isinstance(data, dict) else []


async def upsert_catalog(session: AsyncSession, items: list[dict[str, Any]]) -> int:
    updated = 0
    for item in items:
        sku = (item.get("sku") or "").strip() or None
        title_ru = (item.get("title") or item.get("title_ru") or "").strip()
        if not title_ru:
            continue
        category_title = (item.get("category") or "").strip()
        category_id = None
        if category_title:
            result = await session.execute(select(Category).where(Category.title_ru == category_title))
            category = result.scalar_one_or_none()
            if not category:
                category = Category(title_ru=category_title)
                session.add(category)
                await session.flush()
            category_id = category.id
        query = select(Product).where(Product.sku == sku) if sku else select(Product).where(Product.title_ru == title_ru)
        result = await session.execute(query)
        product = result.scalar_one_or_none()
        if not product:
            product = Product(
                sku=sku,
                title_ru=title_ru,
                title_lat=item.get("title_lat"),
                description=item.get("description"),
                stock_qty=int(item.get("stock_qty") or 0),
                price=float(item.get("price") or 0),
                category_id=category_id,
            )
            session.add(product)
        else:
            product.title_ru = title_ru
            product.title_lat = item.get("title_lat") or product.title_lat
            product.description = item.get("description") or product.description
            product.stock_qty = int(item.get("stock_qty") or product.stock_qty or 0)
            product.price = float(item.get("price") or product.price or 0)
            if category_id:
                product.category_id = category_id
        updated += 1
    await session.commit()
    return updated


async def run_one_c_sync(session: AsyncSession) -> int:
    items = await fetch_one_c_catalog()
    if not items:
        logger.info("1C sync: no items received")
        return 0
    updated = await upsert_catalog(session, items)
    logger.info("1C sync: upserted %s items", updated)
    return updated


async def schedule_one_c_sync(get_session) -> None:
    while settings.one_c_enabled:
        try:
            async for session in get_session():
                await run_one_c_sync(session)
        except Exception:
            logger.exception("1C sync failed")
        await asyncio.sleep(settings.one_c_sync_interval_minutes * 60)
