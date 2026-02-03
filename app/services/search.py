from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Product
from app.services.llm_gigachat import chat

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"(\d+)\s*[xх*]\s*(\d+)")
_SIZE_SEPARATOR_RE = re.compile(r"(\d)\s*[xх*]\s*(\d)", re.IGNORECASE)
_SIZE_NA_RE = re.compile(r"(\d)\s+на\s+(\d)", re.IGNORECASE)


def _normalize_query(text: str) -> str:
    normalized = text.lower()
    normalized = _SIZE_SEPARATOR_RE.sub(r"\1 \2", normalized)
    normalized = _SIZE_NA_RE.sub(r"\1 \2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalization_examples() -> list[tuple[str, str]]:
    return [
        ("механизм подъема", "механизм подъема"),
        ("8х30", "8 30"),
        ("1010 x 40", "1010 40"),
    ]


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
    original = query.strip().lower()
    q = _normalize_query(query)
    numbers = _extract_numbers(q)
    base = select(Product)
    filters = []
    if numbers:
        for num in numbers:
            filters.append(Product.title_ru.ilike(f"%{num}%"))
        base = base.where(and_(*filters))
    else:
        base = base.where(Product.title_ru.ilike(f"%{q}%"))
    result = await session.execute(base.limit(100))
    products = list(result.scalars().all())
    if not products and len(numbers) >= 3:
        size_match = _SIZE_RE.search(original)
        if size_match:
            main_numbers = [int(size_match.group(1)), int(size_match.group(2))]
        else:
            main_numbers = numbers[:2]
        fallback_filters = [Product.title_ru.ilike(f"%{num}%") for num in main_numbers]
        fallback_query = select(Product).where(and_(*fallback_filters)).limit(100)
        fallback_result = await session.execute(fallback_query)
        products = list(fallback_result.scalars().all())
    if q:
        for word in q.split():
            if not word.isdigit() and len(word) > 1:
                products = [p for p in products if word in (p.title_ru or "").lower()]
    scored = []
    for product in products:
        score = _score_product(product, q, numbers)
        if "din" in original and 933 in numbers:
            title = (product.title_ru or "").lower()
            if "din" in title and "933" in title:
                score += 2.5
        scored.append({"product": product, "score": score})
    scored.sort(key=lambda item: item["score"], reverse=True)
    logger.info("search_products query=%s numbers=%s results=%s", q, numbers, len(scored))
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


if __name__ == "__main__":
    for raw, expected in _normalization_examples():
        got = _normalize_query(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"


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
