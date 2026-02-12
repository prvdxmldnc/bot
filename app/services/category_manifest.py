from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product

logger = logging.getLogger(__name__)

_CACHE_KEY = "category_manifest:v1"
_CACHE_TTL_SECONDS = 600


def _redis_client() -> redis.Redis | None:
    if not settings.redis_url:
        return None
    return redis.from_url(settings.redis_url)


def _shorten(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_path(category: Category, categories: dict[int, Category]) -> str:
    parts = [category.title_ru]
    visited = {category.id}
    current = category
    while current.parent_id:
        parent_id = current.parent_id
        if parent_id in visited:
            logger.warning("Category cycle detected: %s", visited)
            break
        parent = categories.get(parent_id)
        if not parent:
            break
        parts.append(parent.title_ru)
        visited.add(parent_id)
        current = parent
    return "/".join(reversed(parts))


async def _get_cache(client: redis.Redis, key: str) -> list[dict[str, Any]] | None:
    raw = await client.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _set_cache(client: redis.Redis, key: str, value: list[dict[str, Any]]) -> None:
    await client.set(key, json.dumps(value, ensure_ascii=False), ex=_CACHE_TTL_SECONDS)


async def get_category_manifest(
    session: AsyncSession, redis_client: redis.Redis | None = None
) -> list[dict[str, Any]]:
    client = redis_client or _redis_client()
    if client:
        cached = await _get_cache(client, _CACHE_KEY)
        if cached is not None:
            return cached

    categories_result = await session.execute(select(Category))
    categories = list(categories_result.scalars().all())
    categories_by_id = {category.id: category for category in categories}

    counts_result = await session.execute(
        select(Product.category_id, func.count(Product.id)).group_by(Product.category_id)
    )
    counts = {row[0]: int(row[1]) for row in counts_result if row[0] is not None}

    manifest: list[dict[str, Any]] = []
    for category in categories:
        examples_result = await session.execute(
            select(Product.title_ru)
            .where(Product.category_id == category.id)
            .order_by(Product.title_ru)
            .limit(5)
        )
        examples = [_shorten(row[0]) for row in examples_result.all() if row[0]]
        manifest.append(
            {
                "category_id": category.id,
                "path": _build_path(category, categories_by_id),
                "title": category.title_ru,
                "count_direct": counts.get(category.id, 0),
                "examples": examples,
            }
        )

    if client:
        await _set_cache(client, _CACHE_KEY, manifest)
    return manifest
