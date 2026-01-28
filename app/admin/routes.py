from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Category, Order, Product, Thread, User
from app.services.search import llm_search

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


@router.get("/search", response_class=HTMLResponse)
async def admin_search(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("search.html", {"request": request})


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
