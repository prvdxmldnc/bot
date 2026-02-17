from datetime import datetime
from typing import Any
import inspect
import time
from uuid import uuid4

import hashlib
import logging
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Category, OrgMember, Organization, Product, User
from app.services.history_candidates import upsert_org_product_stats
from app.services.one_c import upsert_catalog

router = APIRouter()
logger = logging.getLogger(__name__)


class OneCCatalogCompatPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[dict[str, Any]] | None = None
    catalog: list[dict[str, Any]] | None = None
    categories: list[dict[str, Any]] | None = None
    products: list[dict[str, Any]] | None = None
    price_type: str | None = None


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


def _safe_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _payload_overview(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return {"kind": "list", "len": len(payload)}
    if not isinstance(payload, dict):
        return {"kind": type(payload).__name__}
    return {
        "kind": "dict",
        "keys": sorted(payload.keys()),
        "categories": len(payload.get("categories") or []) if isinstance(payload.get("categories"), list) else None,
        "products": len(payload.get("products") or []) if isinstance(payload.get("products"), list) else None,
        "items": len(payload.get("items") or []) if isinstance(payload.get("items"), list) else None,
        "catalog": len(payload.get("catalog") or []) if isinstance(payload.get("catalog"), list) else None,
    }


def _invalid_payload_http(request_id: str, errors: list[dict[str, Any]], message: str = "Invalid payload") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"detail": message, "errors": errors, "request_id": request_id},
    )


async def _prepare_catalog_items(
    session: AsyncSession,
    payload: Any,
    request_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {
        "categories_received": 0,
        "categories_upserted": 0,
        "products_received": 0,
        "products_skipped_missing_category": 0,
        "products_skipped_invalid": 0,
    }
    try:
        parsed = OneCCatalogCompatPayload.model_validate(payload)
    except ValidationError as exc:
        logger.exception(
            "1C catalog payload validation failed",
            extra={"request_id": request_id, "errors": exc.errors(), "payload": _payload_overview(payload)},
        )
        raise _invalid_payload_http(request_id, errors=[_safe_jsonable(e) for e in exc.errors()])

    if parsed.items is not None or parsed.catalog is not None or isinstance(payload, list):
        raw_items = _extract_items(payload)
        stats["products_received"] = len(raw_items)
        return raw_items, stats

    if parsed.categories is None and parsed.products is None:
        errors = [
            {
                "loc": ["body"],
                "msg": "expected one of: items/catalog or categories+products",
                "type": "value_error",
            }
        ]
        raise _invalid_payload_http(request_id, errors=errors)

    categories = parsed.categories or []
    products = parsed.products or []
    stats["categories_received"] = len(categories)
    stats["products_received"] = len(products)

    category_by_external: dict[str, Category] = {}
    category_by_title: dict[str, Category] = {}

    for entry in categories:
        if not isinstance(entry, dict):
            stats["products_skipped_invalid"] += 1
            continue
        title_ru = _coerce_str(entry.get("title_ru") or entry.get("title"))
        title_lat = _coerce_str(entry.get("title_lat")) or None
        if not title_ru:
            continue
        result = await session.execute(select(Category).where(Category.title_ru == title_ru))
        category = result.scalar_one_or_none()
        created = False
        if not category:
            category = Category(title_ru=title_ru[:255], title_lat=title_lat, order_index=int(_coerce_float(entry.get("order_index"))))
            session.add(category)
            await session.flush()
            created = True
        else:
            if title_lat:
                category.title_lat = title_lat
        ext = _coerce_str(entry.get("external_id"))
        if ext:
            category_by_external[ext] = category
        category_by_title[title_ru] = category
        if created:
            stats["categories_upserted"] += 1

    normalized_items: list[dict[str, Any]] = []
    for entry in products:
        if not isinstance(entry, dict):
            stats["products_skipped_invalid"] += 1
            continue
        category_external_id = _coerce_str(entry.get("category_external_id") or entry.get("category_external"))
        category_title = _coerce_str(entry.get("category") or entry.get("category_title"))
        category = None
        if category_external_id:
            category = category_by_external.get(category_external_id)
        if not category and category_title:
            category = category_by_title.get(category_title)
        if not category:
            stats["products_skipped_missing_category"] += 1
            continue

        title = _coerce_str(entry.get("title_ru") or entry.get("title") or entry.get("name"))
        if not title:
            stats["products_skipped_invalid"] += 1
            continue
        normalized_items.append(
            {
                "id": _coerce_str(entry.get("external_id")) or None,
                "sku": _coerce_str(entry.get("sku")) or None,
                "title": title,
                "title_lat": _coerce_str(entry.get("title_lat")) or None,
                "category": category.title_ru,
                "price": _coerce_float(entry.get("price")),
                "stock_qty": _coerce_float(entry.get("stock_qty")),
                "description": _coerce_str(entry.get("description") or ""),
            }
        )

    return normalized_items, stats


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


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def process_orders_payload(session: AsyncSession, payload: Any) -> dict[str, Any]:
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payload")
        org_external_id = _coerce_str(payload.get("org_external_id"))
        org_name = _coerce_str(payload.get("org_name"))
        org = None
        if org_external_id:
            result = await session.execute(select(Organization).where(Organization.external_id == org_external_id))
            org = result.scalar_one_or_none()
            if not org:
                org = Organization(name=org_name or org_external_id, external_id=org_external_id, owner_user_id=None)
                session.add(org)
                await session.flush()
            elif org_name and org.name != org_name:
                org.name = org_name
        else:
            if not org_name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="org_name or org_external_id is required"
                )
            result = await session.execute(select(Organization).where(Organization.name == org_name))
            org = result.scalar_one_or_none()
            if not org:
                org = Organization(name=org_name, owner_user_id=None)
                session.add(org)
                await session.flush()

        orders = payload.get("orders")
        if not orders and isinstance(payload.get("items"), list):
            orders = [{"ordered_at": None, "items": payload.get("items")}]
        if not isinstance(orders, list):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="orders or items are required")

        received_orders = len(orders)
        received_items = 0
        skipped = 0
        items_mapped: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            ordered_at = _parse_datetime(order.get("ordered_at"))
            items = order.get("items") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                received_items += 1
                sku = _coerce_str(item.get("sku"))
                title = _coerce_str(item.get("title") or item.get("name"))
                product = None
                if sku:
                    product_result = await session.execute(select(Product).where(Product.sku == sku))
                    product = product_result.scalar_one_or_none()
                if not product and title:
                    product_result = await session.execute(
                        select(Product).where(Product.title_ru.ilike(f"%{title}%")).limit(1)
                    )
                    product = product_result.scalar_one_or_none()
                if not product:
                    skipped += 1
                    continue
                qty = _coerce_float(item.get("qty") or item.get("quantity"))
                unit = _coerce_str(item.get("unit") or "") or None
                items_mapped.append(
                    {
                        "product_id": product.id,
                        "qty": qty,
                        "unit": unit,
                        "ordered_at": ordered_at,
                    }
                )

        if items_mapped:
            await upsert_org_product_stats(session, org.id, items_mapped)

        await _commit_session(session)

        return {
            "ok": True,
            "org_id": org.id,
            "received_orders": received_orders,
            "received_items": received_items,
            "updated_rows": len(items_mapped),
            "skipped": skipped,
        }
    except Exception:
        await _rollback_session(session)
        raise


def _extract_members_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        if any(key in payload for key in ("org", "org_name", "external_id", "members")):
            return [payload]
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payload")


async def process_members_payload(session: AsyncSession, payload: Any) -> dict[str, Any]:
    try:
        items = _extract_members_payload(payload)
        orgs_received = len(items)
        orgs_upserted = 0
        members_received = 0
        users_created = 0
        members_upserted = 0
        skipped = 0

        for entry in items:
            org_payload = entry.get("org") if isinstance(entry.get("org"), dict) else {}
            external_id = _coerce_str(org_payload.get("external_id") or entry.get("external_id"))
            org_name = _coerce_str(org_payload.get("name") or entry.get("org_name") or entry.get("name"))
            if not org_name and external_id:
                org_name = external_id
            if not org_name:
                skipped += 1
                continue

            org = None
            if external_id:
                result = await session.execute(select(Organization).where(Organization.external_id == external_id))
                org = result.scalar_one_or_none()
            if not org:
                result = await session.execute(select(Organization).where(Organization.name == org_name))
                org = result.scalar_one_or_none()
            if not org:
                org = Organization(name=org_name, external_id=external_id or None, owner_user_id=None)
                session.add(org)
                await session.flush()
                orgs_upserted += 1
            else:
                if external_id and org.external_id != external_id:
                    org.external_id = external_id
                if org.name != org_name and org_name:
                    org.name = org_name

            members = entry.get("members") if isinstance(entry.get("members"), list) else []
            for member in members:
                if not isinstance(member, dict):
                    skipped += 1
                    continue
                members_received += 1
                phone = _coerce_str(member.get("phone"))
                if not phone:
                    skipped += 1
                    continue
                fio = _coerce_str(member.get("fio")) or phone
                role_in_org = _coerce_str(member.get("role_in_org")) or "member"
                status_value = _coerce_str(member.get("status")) or "active"

                user_result = await session.execute(select(User).where(User.phone == phone))
                user = user_result.scalar_one_or_none()
                if not user:
                    user = User(
                        fio=fio,
                        phone=phone,
                        email=None,
                        password_hash="!",
                        address=None,
                        work_time=None,
                        is_24h=False,
                        role="client",
                    )
                    session.add(user)
                    await session.flush()
                    users_created += 1
                elif fio and fio != user.fio:
                    user.fio = fio

                member_result = await session.execute(
                    select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.user_id == user.id)
                )
                org_member = member_result.scalar_one_or_none()
                if not org_member:
                    org_member = OrgMember(
                        org_id=org.id,
                        user_id=user.id,
                        role_in_org=role_in_org,
                        status=status_value,
                    )
                    session.add(org_member)
                else:
                    org_member.role_in_org = role_in_org
                    org_member.status = status_value
                members_upserted += 1

        await session.flush()
        await _commit_session(session)

        logger.info(
            "committed org_members payload",
            extra={
                "orgs_upserted": orgs_upserted,
                "users_created": users_created,
                "members_upserted": members_upserted,
            },
        )

        return {
            "ok": True,
            "orgs_received": orgs_received,
            "members_received": members_received,
            "orgs_upserted": orgs_upserted,
            "users_created": users_created,
            "members_upserted": members_upserted,
            "skipped": skipped,
        }
    except Exception:
        await _rollback_session(session)
        raise


async def _commit_session(session: Any) -> None:
    commit = getattr(session, "commit", None)
    if not callable(commit):
        return
    result = commit()
    if inspect.isawaitable(result):
        await result


async def _rollback_session(session: Any) -> None:
    rollback = getattr(session, "rollback", None)
    if not callable(rollback):
        return
    result = rollback()
    if inspect.isawaitable(result):
        await result


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
    request_id = str(uuid4())
    started_at = time.perf_counter()
    if settings.one_c_webhook_token:
        provided_token = _extract_token(authorization, token_header, x_token_header, token_query)
        if provided_token != settings.one_c_webhook_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"detail": "Invalid token", "request_id": request_id})
    try:
        raw_items, stats = await _prepare_catalog_items(session, payload, request_id)
    except HTTPException:
        raise
    except ValidationError as exc:
        logger.exception(
            "1C catalog payload invalid",
            extra={"request_id": request_id, "errors": exc.errors(), "payload": _payload_overview(payload)},
        )
        raise _invalid_payload_http(request_id, errors=[_safe_jsonable(e) for e in exc.errors()])
    except Exception:
        logger.exception("1C catalog payload parsing failed", extra={"request_id": request_id, "payload": _payload_overview(payload)})
        raise _invalid_payload_http(
            request_id,
            errors=[{"loc": ["body"], "msg": "unexpected parser error", "type": "value_error"}],
        )

    items, skipped, sku_adjusted = _normalize_items(raw_items)
    stats["products_skipped_invalid"] += skipped
    try:
        updated = await upsert_catalog(session, items)
    except Exception:
        logger.exception("1C webhook upsert failed", extra={"request_id": request_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"detail": "Upsert failed", "request_id": request_id},
        )

    total_time_ms = int((time.perf_counter() - started_at) * 1000)
    summary = {
        "categories_received": stats.get("categories_received", 0),
        "categories_upserted": stats.get("categories_upserted", 0),
        "products_received": stats.get("products_received", len(raw_items)),
        "products_upserted": updated,
        "products_skipped_missing_category": stats.get("products_skipped_missing_category", 0),
        "products_skipped_invalid": stats.get("products_skipped_invalid", 0),
        "total_time_ms": total_time_ms,
    }
    logger.info(
        "1C webhook processed",
        extra={
            "request_id": request_id,
            **summary,
            "sku_adjusted": sku_adjusted,
        },
    )
    return {"ok": True, **summary, "request_id": request_id}


@router.post("/integrations/1c/orders")
@router.post("/onec/orders")
@router.post("/api/onec/orders")
async def one_c_orders(
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
    return await process_orders_payload(session, payload)


@router.post("/integrations/1c/orgs/members")
@router.post("/onec/orgs/members")
@router.post("/api/onec/orgs/members")
async def one_c_org_members(
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
    return await process_members_payload(session, payload)
