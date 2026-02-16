from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import SearchAlias

_ALIAS_CACHE_TTL = 600
_TOKEN_RE = re.compile(r"\b[\w\-]+\b", re.IGNORECASE)
_ARTICLE_ANCHOR_RE = re.compile(r"\b(?:st\d{3,6}|[a-z]{1,3}\d{2,6}|\d{5,})\b", re.IGNORECASE)


def _redis_client() -> Redis | None:
    if not settings.redis_url:
        return None
    return Redis.from_url(settings.redis_url)


def _cache_key(org_id: int | None) -> str:
    return f"search_alias_map:{org_id or 0}"


async def get_alias_map(session: AsyncSession, org_id: int | None) -> dict[str, str]:
    key = _cache_key(org_id)
    client = _redis_client()
    if client:
        try:
            cached = await client.get(key)
            if cached:
                try:
                    data = json.loads(cached)
                    if isinstance(data, dict):
                        return {str(k): str(v) for k, v in data.items()}
                except json.JSONDecodeError:
                    pass
        except RedisError:
            pass

    result: dict[str, str] = {}
    try:
        global_rows = (
            await session.execute(
                select(SearchAlias).where(SearchAlias.enabled.is_(True), SearchAlias.org_id.is_(None), SearchAlias.kind == "token")
            )
        ).scalars().all()
        result = {row.src: row.dst for row in global_rows}

        if org_id is not None:
            org_rows = (
                await session.execute(
                    select(SearchAlias).where(SearchAlias.enabled.is_(True), SearchAlias.org_id == org_id, SearchAlias.kind == "token")
                )
            ).scalars().all()
            for row in org_rows:
                result[row.src] = row.dst
    except Exception:
        result = {}

    result = {**DEFAULT_ALIASES, **result}
    if client:
        try:
            await client.setex(key, _ALIAS_CACHE_TTL, json.dumps(result, ensure_ascii=False))
        except RedisError:
            pass
    return result


async def invalidate_alias_cache(org_id: int | None) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        await client.delete(_cache_key(org_id))
    except RedisError:
        pass


def normalize_query_with_aliases(text: str, alias_map: dict[str, str]) -> tuple[str, dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return "", {}
    tokens = _TOKEN_RE.findall(raw.lower())
    applied: dict[str, str] = {}
    normalized_tokens: list[str] = []

    short_query = len(tokens) <= 3 and not _ARTICLE_ANCHOR_RE.search(raw.lower())

    for token in tokens:
        replacement = alias_map.get(token, token)
        if token == "ппу" and short_query:
            replacement = alias_map.get(token, "поролон")
        if replacement != token:
            applied[token] = replacement
        normalized_tokens.append(replacement)

    return " ".join(normalized_tokens).strip(), applied


DEFAULT_ALIASES = {
    "спандбонд": "спанбонд",
    "спандбон": "спанбонд",
    "синтепонн": "синтепон",
    "ппу": "поролон",
}


async def seed_default_aliases(session: AsyncSession) -> None:
    for src, dst in DEFAULT_ALIASES.items():
        existing = (
            await session.execute(
                select(SearchAlias).where(SearchAlias.org_id.is_(None), SearchAlias.src == src)
            )
        ).scalar_one_or_none()
        if existing:
            if existing.dst != dst or not existing.enabled:
                existing.dst = dst
                existing.enabled = True
                existing.updated_at = datetime.utcnow()
            continue
        session.add(
            SearchAlias(
                org_id=None,
                src=src,
                dst=dst,
                kind="token",
                enabled=True,
            )
        )
    await session.flush()
