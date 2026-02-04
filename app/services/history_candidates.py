from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgProductStats


async def get_org_candidates(session: AsyncSession, org_id: int, limit: int = 200) -> list[int]:
    result = await session.execute(
        select(OrgProductStats.product_id)
        .where(OrgProductStats.org_id == org_id)
        .order_by(desc(OrgProductStats.orders_count), desc(OrgProductStats.last_order_at))
        .limit(limit)
    )
    return [row[0] for row in result.all()]


async def upsert_org_product_stats(
    session: AsyncSession,
    org_id: int,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        product_id = int(item["product_id"])
        qty = float(item.get("qty") or 0)
        unit = item.get("unit")
        ordered_at = item.get("ordered_at")
        result = await session.execute(
            select(OrgProductStats)
            .where(OrgProductStats.org_id == org_id)
            .where(OrgProductStats.product_id == product_id)
        )
        stats = result.scalar_one_or_none()
        if stats:
            stats.orders_count += 1
            stats.qty_sum = float(stats.qty_sum or 0) + qty
            if ordered_at and (stats.last_order_at is None or ordered_at >= stats.last_order_at):
                stats.last_order_at = ordered_at
                stats.last_qty = qty
                stats.last_unit = unit
        else:
            stats = OrgProductStats(
                org_id=org_id,
                product_id=product_id,
                orders_count=1,
                qty_sum=qty,
                last_order_at=ordered_at,
                last_qty=qty if ordered_at else None,
                last_unit=unit if ordered_at else None,
            )
            session.add(stats)
    await session.flush()
