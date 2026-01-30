from typing import Any

import hashlib
import logging
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.services.one_c import upsert_catalog

router = APIRouter()
logger = logging.getLogger(__name__)


def _extract_token(
    authorization: str | None,
    token_header: str | None,
    x_token_header: str | None,
    token_query: str | None,
) -> str | None:
    if token_header:
        return token_header
    if x_token_header:
        return x_token_header
    if token_query:
        return token_query
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return payload["items"]
        if isinstance(payload.get("catalog"), list):
            return payload["catalog"]
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payload")


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.replace(" ", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return 0.0
    return 0.0


def _hash_sku(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _normalize_sku(value: str, fallback: str) -> tuple[str, bool]:
    source = value or fallback
    if not source:
        return "", False
    normalized = _coerce_str(source)
    if not normalized:
        return "", False
    if len(normalized) <= 64:
        return normalized, normalized != value
    return _hash_sku(normalized), True


def _normalize_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    normalized: list[dict[str, Any]] = []
    skipped = 0
    sku_adjusted = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        title = _coerce_str(item.get("title") or item.get("name"))
        if not title:
            skipped += 1
            continue
        raw_sku = _coerce_str(item.get("sku"))
        fallback = _coerce_str(item.get("id")) or title
        sku, adjusted = _normalize_sku(raw_sku, fallback)
        if adjusted:
            sku_adjusted += 1
        if not sku:
            skipped += 1
            continue
        title = title[:255]
        category = _coerce_str(item.get("category"))[:64]
        description = _coerce_str(item.get("description"))
        price = _coerce_float(item.get("price"))
        qty_value = item.get("stock_qty")
        if qty_value is None:
            qty_value = item.get("qty")
        if qty_value is None:
            qty_value = item.get("quantity")
        stock_qty = _coerce_float(qty_value)
        normalized.append(
            {
                "sku": sku,
                "title": title,
                "category": category,
                "price": price,
                "stock_qty": stock_qty,
                "description": description,
            }
        )
    return normalized, skipped, sku_adjusted


@router.post("/integrations/1c/catalog")
@router.post("/onec/catalog")
@router.post("/api/onec/catalog")
async def one_c_catalog(
    payload: Any = Body(...),
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None, alias="Authorization"),
    token_header: str | None = Header(default=None, alias="X-1C-Token"),
    x_token_header: str | None = Header(default=None, alias="X-Token"),
    token_query: str | None = Query(default=None, alias="token"),
) -> dict[str, Any]:
    if settings.one_c_webhook_token:
        provided_token = _extract_token(authorization, token_header, x_token_header, token_query)
        if provided_token != settings.one_c_webhook_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    raw_items = _extract_items(payload)
    items, skipped, sku_adjusted = _normalize_items(raw_items)
    try:
        updated = await upsert_catalog(session, items)
    except Exception:
        logger.exception("1C webhook upsert failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Upsert failed")
    logger.info(
        "1C webhook processed",
        extra={
            "received": len(raw_items),
            "upserted": updated,
            "skipped": skipped,
            "sku_adjusted": sku_adjusted,
        },
    )
    return {"ok": True, "received": len(raw_items), "upserted": updated, "skipped": skipped}
