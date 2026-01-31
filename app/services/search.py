from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Product
from app.services.llm_gigachat import chat


def _extract_numbers(text: str) -> list[int]:
    numbers = []
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
        elif current:
            numbers.append(int(current))
            current = ""
    if current:
        numbers.append(int(current))
    return numbers


def _score_product(product: Product, query: str, numbers: list[int]) -> float:
    score = 0.0
    q = query.lower()
    title = (product.title_ru or "").lower()
    sku = (product.sku or "").lower()
    if sku and q in sku:
        score += 3.0
    if q in title:
        score += 1.5
    if numbers:
        hits = sum(1 for n in numbers if str(n) in title)
        score += hits * 0.5
    return score


async def search_products(session: AsyncSession, query: str, limit: int = 10) -> list[dict[str, Any]]:
    q = query.strip()
    numbers = _extract_numbers(q)
    base = select(Product)
    if any(char.isdigit() for char in q):
        base = base.where(or_(Product.sku.ilike(f"%{q}%"), Product.title_ru.ilike(f"%{q}%")))
    else:
        base = base.where(Product.title_ru.ilike(f"%{q}%"))
    result = await session.execute(base.limit(50))
    products = list(result.scalars().all())
    scored = [
        {"product": product, "score": _score_product(product, q, numbers)}
        for product in products
    ]
    scored.sort(key=lambda item: item["score"], reverse=True)
    return [
        {
            "id": item["product"].id,
            "sku": item["product"].sku,
            "title_ru": item["product"].title_ru,
            "price": float(item["product"].price or 0),
            "stock_qty": item["product"].stock_qty,
            "score": item["score"],
        }
        for item in scored[:limit]
    ]


def _parse_llm_content(content: str, source: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [{"title": content.strip(), "source": source}]
    if isinstance(data, list):
        results = []
        for item in data:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or "").strip()
                qty = item.get("qty") or item.get("quantity")
                if title:
                    results.append({"title": title, "qty": qty, "source": source})
            elif isinstance(item, str):
                results.append({"title": item.strip(), "source": source})
        return results or [{"title": content.strip(), "source": source}]
    return [{"title": content.strip(), "source": source}]


async def llm_search(session: AsyncSession, query: str) -> list[dict[str, Any]]:
    if settings.gigachat_basic_auth_key:
        try:
            data = await chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты помощник по каталогу. Верни JSON-массив объектов с полями "
                            "`title` и опционально `qty`."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0.2,
            )
            content = data["choices"][0]["message"]["content"]
            return _parse_llm_content(content, "gigachat")
        except (httpx.HTTPError, ValueError):
            pass
    if settings.openai_api_key:
        payload = {
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return JSON array of objects with `title` and optional `qty`.",
                },
                {"role": "user", "content": query},
            ],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_llm_content(content, "llm")
    result = await session.execute(select(Product).where(Product.title_ru.ilike(f"%{query}%")))
    return [{"title": product.title_ru, "source": "db"} for product in result.scalars().all()]
