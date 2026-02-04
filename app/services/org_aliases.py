from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgAlias

_SPACES_RE = re.compile(r"\s+")
_QTY_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:"
    r"т\.?\s*шт|т\s*шт|тыс\.?\s*шт|шт|кг|кор(?:обка)?|уп(?:ак)?|рулон|"
    r"рол(?:ик)?|пог\.?\s*м|м"
    r")\b",
    flags=re.IGNORECASE,
)


def normalize_alias(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = _QTY_UNIT_RE.sub(" ", cleaned)
    cleaned = _SPACES_RE.sub(" ", cleaned)
    return cleaned[:255]


async def upsert_org_alias(
    session: AsyncSession,
    org_id: int,
    alias_text: str,
    product_id: int,
) -> None:
    now = datetime.utcnow()
    normalized = normalize_alias(alias_text)
    if not normalized:
        return
    stmt = select(OrgAlias).where(
        OrgAlias.org_id == org_id,
        OrgAlias.normalized_alias == normalized,
        OrgAlias.product_id == product_id,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        existing.weight += 1
        existing.last_used_at = now
        existing.updated_at = now
    else:
        session.add(
            OrgAlias(
                org_id=org_id,
                alias_text=alias_text[:255],
                normalized_alias=normalized,
                product_id=product_id,
                weight=1,
                last_used_at=now,
                created_at=now,
                updated_at=now,
            )
        )


async def find_org_alias_candidates(
    session: AsyncSession,
    org_id: int,
    alias_text: str,
    limit: int = 5,
) -> list[int]:
    normalized = normalize_alias(alias_text)
    if not normalized:
        return []
    stmt = (
        select(OrgAlias.product_id)
        .where(OrgAlias.org_id == org_id, OrgAlias.normalized_alias == normalized)
        .order_by(OrgAlias.weight.desc(), OrgAlias.last_used_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    product_ids = [row[0] for row in result.all()]
    if product_ids:
        return product_ids
    stmt = (
        select(OrgAlias.product_id)
        .where(OrgAlias.org_id == org_id, OrgAlias.normalized_alias.ilike(f"%{normalized}%"))
        .order_by(OrgAlias.weight.desc(), OrgAlias.last_used_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]
