from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product

logger = logging.getLogger(__name__)

SKU_MAX_LEN = 64
TITLE_MAX_LEN = 255
CATEGORY_MAX_LEN = 64


def _to_str(v: Any) -> str:
    return ("" if v is None else str(v)).strip()


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n]


def _normalize_sku(raw: Any, fallback: Any = None) -> Optional[str]:
    s = _to_str(raw)
    if not s:
        s = _to_str(fallback)
    if not s:
        return None
    if len(s) <= SKU_MAX_LEN:
        return s
    # stable short id (40 chars)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _safe_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = _to_str(v)
    if not s:
        return default
    s = s.replace(" ", "").replace(",", ".")
    try:
        return int(float(s))
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    s = _to_str(v)
    if not s:
        return default
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return default


def normalize_one_c_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            items = payload["items"]
        elif isinstance(payload.get("catalog"), list):
            items = payload["catalog"]
        else:
            items = [payload]
    else:
        return []
    return [item for item in items if isinstance(item, dict)]


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
        title_ru = _truncate(_to_str(item.get("title") or item.get("title_ru")), TITLE_MAX_LEN)
        if not title_ru:
            continue

        # Protect DB constraints even if caller didn't normalize
        sku = _normalize_sku(item.get("sku"), fallback=item.get("id") or title_ru)
        category_title = _truncate(_to_str(item.get("category")), CATEGORY_MAX_LEN)

        description = _to_str(item.get("description")) or ""
        title_lat = item.get("title_lat")

        stock_qty = _safe_int(item.get("stock_qty"), 0)
        price = _safe_float(item.get("price"), 0.0)

        category_id = None
        if category_title:
            # avoid premature autoflush during category lookup
            with session.no_autoflush:
                result = await session.execute(select(Category).where(Category.title_ru == category_title))
                category = result.scalar_one_or_none()

            if not category:
                category = Category(title_ru=category_title)
                session.add(category)
                await session.flush()
            category_id = category.id

        # lookup
        if sku:
            query = select(Product).where(Product.sku == sku)
        else:
            query = select(Product).where(Product.title_ru == title_ru)

        result = await session.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            product = Product(
                sku=sku,
                title_ru=title_ru,
                title_lat=title_lat,
                description=description,
                stock_qty=stock_qty,
                price=price,
                category_id=category_id,
            )
            session.add(product)
        else:
            product.title_ru = title_ru
            product.title_lat = title_lat or product.title_lat
            product.description = description or product.description
            product.stock_qty = stock_qty
            product.price = price
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
