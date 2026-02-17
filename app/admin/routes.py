from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Category, CategoryFacetConfig, OrgMember, Order, Organization, Product, SearchAlias, Thread, User
from app.services.search_pipeline import run_search_pipeline
from app.services.search import llm_search
from app.services.search_aliases import invalidate_alias_cache

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, session: SessionDep) -> HTMLResponse:
    users = await session.execute(select(User))
    orders = await session.execute(select(Order))
    threads = await session.execute(select(Thread))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "users": len(users.scalars().all()),
            "orders": len(orders.scalars().all()),
            "threads": len(threads.scalars().all()),
        },
    )


@router.get("/catalog", response_class=HTMLResponse)
async def admin_catalog(request: Request, session: SessionDep) -> HTMLResponse:
    categories = await session.execute(select(Category).order_by(Category.order_index))
    products = await session.execute(select(Product).order_by(Product.title_ru))
    return templates.TemplateResponse(
        "catalog.html",
        {
            "request": request,
            "categories": categories.scalars().all(),
            "products": products.scalars().all(),
        },
    )


@router.post("/catalog/import")
async def import_catalog(
    session: SessionDep,
    file: UploadFile = File(...),
) -> RedirectResponse:
    import csv

    content = (await file.read()).decode("utf-8").splitlines()
    reader = csv.DictReader(content)
    for row in reader:
        category_title = (row.get("category") or "").strip()
        if category_title:
            result = await session.execute(select(Category).where(Category.title_ru == category_title))
            category = result.scalar_one_or_none()
            if not category:
                category = Category(title_ru=category_title, title_lat=row.get("category_lat"))
                session.add(category)
                await session.flush()
        else:
            category = None
        product = Product(
            title_ru=row.get("title_ru") or "",
            title_lat=row.get("title_lat"),
            description=row.get("description"),
            sku=row.get("sku"),
            stock_qty=int(row.get("stock_qty") or 0),
            price=float(row.get("price") or 0),
            category_id=category.id if category else None,
        )
        session.add(product)
    await session.commit()
    return RedirectResponse(url="/admin/catalog", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
async def admin_orders(request: Request, session: SessionDep) -> HTMLResponse:
    orders = await session.execute(select(Order).order_by(Order.created_at.desc()))
    return templates.TemplateResponse(
        "orders.html",
        {"request": request, "orders": orders.scalars().all()},
    )


@router.post("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    status: Annotated[str, Form()],
    session: SessionDep,
) -> RedirectResponse:
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.status = status
        order.updated_at = datetime.utcnow()
        await session.commit()
    return RedirectResponse(url="/admin/orders", status_code=303)


@router.get("/threads", response_class=HTMLResponse)
async def admin_threads(request: Request, session: SessionDep) -> HTMLResponse:
    threads = await session.execute(select(Thread).order_by(Thread.created_at.desc()))
    return templates.TemplateResponse(
        "threads.html",
        {"request": request, "threads": threads.scalars().all()},
    )


@router.get("/debug/users")
async def debug_users(session: SessionDep) -> dict[str, object]:
    users = await session.execute(select(User).order_by(User.created_at.desc()))
    user_list = [
        {"id": user.id, "phone": user.phone, "fio": user.fio, "created_at": user.created_at.isoformat()}
        for user in users.scalars().all()
    ]
    return {"count": len(user_list), "users": user_list}


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else ""


@router.get("/search", response_class=HTMLResponse)
async def admin_search(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("search.html", {"request": request})


@router.post("/sync/1c")
async def sync_one_c(session: SessionDep) -> RedirectResponse:
    from app.services.one_c import run_one_c_sync

    await run_one_c_sync(session)
    return RedirectResponse(url="/admin/catalog", status_code=303)


@router.post("/search", response_class=HTMLResponse)
async def admin_search_post(
    request: Request,
    session: SessionDep,
    query: Annotated[str, Form()],
) -> HTMLResponse:
    results = await llm_search(session, query)
    return templates.TemplateResponse(
        "search.html",
        {"request": request, "query": query, "results": results, "model": settings.openai_model},
    )






@router.get("/facets", response_class=HTMLResponse)
async def admin_facets(
    request: Request,
    session: SessionDep,
    category_id: int | None = None,
    q: str | None = None,
) -> HTMLResponse:
    cat_stmt = select(Category).order_by(Category.title_ru)
    categories = (await session.execute(cat_stmt)).scalars().all()

    facet_stmt = select(CategoryFacetConfig, Category.title_ru).join(Category, Category.id == CategoryFacetConfig.category_id)
    if category_id is not None:
        facet_stmt = facet_stmt.where(CategoryFacetConfig.category_id == category_id)
    if q:
        facet_stmt = facet_stmt.where(Category.title_ru.ilike(f"%{q}%"))
    facet_stmt = facet_stmt.order_by(CategoryFacetConfig.category_id, CategoryFacetConfig.priority)
    rows = (await session.execute(facet_stmt)).all()
    return templates.TemplateResponse(
        "facets.html",
        {
            "request": request,
            "rows": rows,
            "categories": categories,
            "category_id": category_id,
            "q": q or "",
        },
    )


@router.post("/facets")
async def admin_facets_create(
    session: SessionDep,
    category_id: Annotated[int, Form()],
    facet_key: Annotated[str, Form()],
    enabled: Annotated[bool, Form()] = True,
    priority: Annotated[int, Form()] = 100,
) -> RedirectResponse:
    cfg = CategoryFacetConfig(category_id=category_id, facet_key=(facet_key or "").strip(), enabled=enabled, priority=priority)
    session.add(cfg)
    await session.commit()
    return RedirectResponse(url="/admin/facets", status_code=303)


@router.post("/facets/{cfg_id}/toggle")
async def admin_facets_toggle(cfg_id: int, session: SessionDep) -> RedirectResponse:
    cfg = (await session.execute(select(CategoryFacetConfig).where(CategoryFacetConfig.id == cfg_id))).scalar_one_or_none()
    if cfg:
        cfg.enabled = not cfg.enabled
        await session.commit()
    return RedirectResponse(url="/admin/facets", status_code=303)


@router.post("/facets/{cfg_id}/delete")
async def admin_facets_delete(cfg_id: int, session: SessionDep) -> RedirectResponse:
    cfg = (await session.execute(select(CategoryFacetConfig).where(CategoryFacetConfig.id == cfg_id))).scalar_one_or_none()
    if cfg:
        await session.delete(cfg)
        await session.commit()
    return RedirectResponse(url="/admin/facets", status_code=303)

@router.get("/search-aliases", response_class=HTMLResponse)
async def admin_search_aliases(
    request: Request,
    session: SessionDep,
    org_id: int | None = None,
) -> HTMLResponse:
    stmt = select(SearchAlias).order_by(SearchAlias.org_id.is_(None).desc(), SearchAlias.src)
    if org_id is not None:
        stmt = stmt.where(SearchAlias.org_id == org_id)
    aliases = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        "search_aliases.html",
        {
            "request": request,
            "aliases": aliases,
            "org_id": org_id,
        },
    )


@router.post("/search-aliases")
async def admin_search_aliases_create(
    session: SessionDep,
    org_id: Annotated[str | None, Form()] = None,
    src: Annotated[str, Form()] = "",
    dst: Annotated[str, Form()] = "",
    enabled: Annotated[bool, Form()] = True,
) -> RedirectResponse:
    src_val = (src or "").strip().lower()
    dst_val = (dst or "").strip().lower()
    org_val = int(org_id) if org_id and org_id.strip().isdigit() else None
    if src_val and dst_val:
        existing = (
            await session.execute(
                select(SearchAlias).where(SearchAlias.org_id == org_val, SearchAlias.src == src_val)
            )
        ).scalar_one_or_none()
        if existing:
            existing.dst = dst_val
            existing.enabled = enabled
        else:
            session.add(
                SearchAlias(
                    org_id=org_val,
                    src=src_val,
                    dst=dst_val,
                    kind="token",
                    enabled=enabled,
                )
            )
        await session.commit()
        await invalidate_alias_cache(org_val)
    q = f"?org_id={org_val}" if org_val is not None else ""
    return RedirectResponse(url=f"/admin/search-aliases{q}", status_code=303)


@router.post("/search-aliases/{alias_id}/toggle")
async def admin_search_aliases_toggle(alias_id: int, session: SessionDep) -> RedirectResponse:
    alias = (await session.execute(select(SearchAlias).where(SearchAlias.id == alias_id))).scalar_one_or_none()
    if alias:
        alias.enabled = not alias.enabled
        await session.commit()
        await invalidate_alias_cache(alias.org_id)
    return RedirectResponse(url="/admin/search-aliases", status_code=303)


@router.post("/search-aliases/{alias_id}/delete")
async def admin_search_aliases_delete(alias_id: int, session: SessionDep) -> RedirectResponse:
    alias = (await session.execute(select(SearchAlias).where(SearchAlias.id == alias_id))).scalar_one_or_none()
    if alias:
        org_id = alias.org_id
        await session.delete(alias)
        await session.commit()
        await invalidate_alias_cache(org_id)
    return RedirectResponse(url="/admin/search-aliases", status_code=303)

@router.get("/debug/search-as", response_class=HTMLResponse)
async def debug_search_as(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "debug_search_as.html",
        {
            "request": request,
            "org_id": "",
            "phone": "",
            "query": "",
            "batch": "",
            "error": None,
            "results": None,
            "decision": None,
            "trace": None,
            "batch_results": [],
            "selected_user": None,
            "selected_org": None,
            "offset": 0,
        },
    )


@router.post("/debug/search-as", response_class=HTMLResponse)
async def debug_search_as_post(
    request: Request,
    session: SessionDep,
    org_id: Annotated[str | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    query: Annotated[str | None, Form()] = None,
    batch: Annotated[str | None, Form()] = None,
    offset: Annotated[int, Form()] = 0,
) -> HTMLResponse:
    resolved_org_id: int | None = None
    resolved_user_id: int | None = None
    selected_user = None
    selected_org = None
    error = None

    if org_id and org_id.strip().isdigit():
        resolved_org_id = int(org_id.strip())
        result = await session.execute(select(Organization).where(Organization.id == resolved_org_id))
        selected_org = result.scalar_one_or_none()
    elif phone:
        normalized_phone = _normalize_phone(phone)
        if normalized_phone:
            user_result = await session.execute(
                select(User).where(or_(User.phone == normalized_phone, User.phone.ilike(f"%{normalized_phone}%")))
            )
            selected_user = user_result.scalar_one_or_none()
            if selected_user:
                resolved_user_id = selected_user.id
                membership_result = await session.execute(
                    select(OrgMember)
                    .where(OrgMember.user_id == selected_user.id, OrgMember.status == "active")
                    .order_by(OrgMember.org_id)
                )
                membership = membership_result.scalars().first()
                if membership:
                    resolved_org_id = membership.org_id
                    org_result = await session.execute(
                        select(Organization).where(Organization.id == resolved_org_id)
                    )
                    selected_org = org_result.scalar_one_or_none()
        if not selected_user:
            error = "Пользователь с таким телефоном не найден."

    if resolved_org_id is None:
        error = error or "Не удалось определить организацию для поиска."

    batch_results: list[dict[str, object]] = []
    results = None
    decision = None
    trace = None
    if not error:
        if batch and batch.strip():
            for line in [row.strip() for row in batch.splitlines() if row.strip()]:
                payload = await run_search_pipeline(
                    session,
                    org_id=resolved_org_id,
                    user_id=resolved_user_id,
                    text=line,
                    limit=5,
                    clarify_offset=offset,
                )
                batch_results.append(
                    {
                        "query": line,
                        "results": payload["results"],
                        "decision": payload["decision"],
                        "trace": payload.get("trace"),
                    }
                )
        elif query and query.strip():
            payload = await run_search_pipeline(
                session,
                org_id=resolved_org_id,
                user_id=resolved_user_id,
                text=query.strip(),
                limit=5,
                clarify_offset=offset,
            )
            results = payload["results"]
            decision = payload["decision"]
            trace = payload.get("trace")

    return templates.TemplateResponse(
        "debug_search_as.html",
        {
            "request": request,
            "org_id": org_id or "",
            "phone": phone or "",
            "query": query or "",
            "batch": batch or "",
            "error": error,
            "results": results,
            "decision": decision,
            "trace": trace,
            "batch_results": batch_results,
            "selected_user": selected_user,
            "selected_org": selected_org,
            "offset": offset,
        },
    )
