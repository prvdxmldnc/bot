from __future__ import annotations

from datetime import datetime
from math import log1p
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgProductStats, Product
from app.services.search import normalize_query_text

_TOKEN_STOP = {
    "шт",
    "штук",
    "кор",
    "короб",
    "коробка",
    "коробочки",
    "рул",
    "рулон",
    "рулонная",
    "уп",
    "упак",
    "упаковка",
    "мм",
    "см",
    "м",
    "м2",
    "кг",
    "гр",
    "г",
    "по",
}
_COLOR_TOKENS = {"сер", "серая", "серый", "беж", "бежев", "бел", "белый", "черн", "черный", "син", "зел"}


def _tokenize_query(query_core: str) -> tuple[list[str], list[str], list[str], bool]:
    normalized = normalize_query_text(query_core)
    raw_tokens = normalized.split()
    numbers = [t for t in raw_tokens if t.isdigit()]
    text_tokens = [t for t in raw_tokens if not t.isdigit() and t not in _TOKEN_STOP]
    anchors = [t for t in text_tokens if t not in _COLOR_TOKENS and len(t) >= 4]
    if not anchors and text_tokens:
        anchors = sorted(text_tokens, key=len, reverse=True)[:1]
    optional = [t for t in text_tokens if t not in anchors]
    with_springs = "пружин" in normalized and "без пружин" not in normalized
    return anchors[:2], optional, numbers, with_springs


def _token_match(token: str, words: list[str]) -> bool:
    return any(w == token or w.startswith(token) for w in words)


async def count_org_candidates(session: AsyncSession, org_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(OrgProductStats).where(OrgProductStats.org_id == org_id)
    )
    return int(result.scalar() or 0)


async def get_org_candidates(session: AsyncSession, org_id: int, limit: int | None = 200) -> list[int]:
    query = (
        select(OrgProductStats.product_id)
        .where(OrgProductStats.org_id == org_id)
        .order_by(desc(OrgProductStats.orders_count), desc(OrgProductStats.last_order_at))
    )
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return [row[0] for row in result.all()]


async def search_history_products(
    session: AsyncSession,
    org_id: int,
    query_core: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    anchors, optional, numbers, with_springs = _tokenize_query(query_core)
    if not anchors and not numbers:
        return []

    result = await session.execute(
        select(OrgProductStats, Product)
        .join(Product, Product.id == OrgProductStats.product_id)
        .where(OrgProductStats.org_id == org_id)
        .order_by(desc(OrgProductStats.orders_count), desc(OrgProductStats.last_order_at))
        .limit(3000)
    )
    rows = result.all()

    now = datetime.utcnow()
    scored: list[dict[str, Any]] = []
    for stats, product in rows:
        title = normalize_query_text(f"{product.title_ru or ''} {product.sku or ''}")
        words = title.split()

        if numbers and not all(num in title for num in numbers):
            continue
        if anchors and not all(_token_match(anchor, words) for anchor in anchors):
            continue

        attr_conflict = with_springs and "без пружин" in title
        score = 0.0
        score += log1p(float(stats.orders_count or 0))
        if stats.last_order_at:
            days = max((now - stats.last_order_at).days, 0)
            score += 1 / (1 + days / 30)
        for token in optional:
            if _token_match(token, words):
                score += 0.35
        if attr_conflict:
            score -= 0.8

        scored.append(
            {
                "id": product.id,
                "sku": product.sku,
                "title_ru": product.title_ru,
                "price": float(product.price or 0),
                "stock_qty": product.stock_qty,
                "score": score,
                "attribute_conflict": attr_conflict,
            }
        )

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored[:limit]


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
