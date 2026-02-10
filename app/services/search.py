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
_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)
_STOP_WORDS = {
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
    "тип",
    "номер",
    "цвет",
    "№",
}

_QTY_UNIT_TOKENS = {
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
}
_COLOR_STEM_MAP = {
    "беж": "бежев",
    "сер": "сер",
    "бел": "бел",
    "черн": "черн",
    "син": "син",
    "зел": "зел",
}

def normalize_query_text(text: str) -> str:
    normalized = text.lower().replace("ё", "е")
    normalized = _NON_ALNUM_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalize_query(text: str) -> str:
    return normalize_query_text(text)


def _normalization_examples() -> list[tuple[str, str]]:
    return [
        ("механизм подъема", "механизм подъема"),
        ("8х30", "8х30"),
        ("1010 x 40", "1010 x 40"),
    ]


def _extract_numbers(text: str) -> list[int]:
    return [int(token) for token in _TOKEN_RE.findall(text) if token.isdigit()]


def _extract_tokens(text: str) -> list[str]:
    tokens = []
    for token in _TOKEN_RE.findall(text):
        if token.isdigit() or token in _STOP_WORDS:
            continue
        if len(token) <= 2:
            continue
        tokens.append(_COLOR_STEM_MAP.get(token, token))
    return tokens


def _token_matches_title(token: str, title_words: list[str]) -> bool:
    return any(word == token or word.startswith(token) for word in title_words)


def _effective_numbers(query_text: str, numbers: list[int]) -> list[int]:
    if not numbers:
        return numbers
    query_tokens = _TOKEN_RE.findall(query_text)
    has_qty_units = any(token in _QTY_UNIT_TOKENS for token in query_tokens)
    if has_qty_units and len(numbers) == 1:
        return []
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


async def search_products(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    category_ids: list[int] | None = None,
    product_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    original = query.strip().lower()
    q = _normalize_query(query)
    numbers = _extract_numbers(q)
    tokens = _extract_tokens(q)
    numbers_for_match = _effective_numbers(q, numbers)
    base = select(Product)
    if category_ids:
        base = base.where(Product.category_id.in_(category_ids))
    if product_ids:
        base = base.where(Product.id.in_(product_ids))
    filters = []
    if numbers_for_match:
        for num in numbers_for_match:
            filters.append(Product.title_ru.ilike(f"%{num}%"))
        base = base.where(and_(*filters))
    else:
        if len(tokens) >= 2:
            base = base.where(and_(*[Product.title_ru.ilike(f"%{token}%") for token in tokens]))
        elif len(tokens) == 1:
            base = base.where(Product.title_ru.ilike(f"%{tokens[0]}%"))
        else:
            base = base.where(Product.title_ru.ilike(f"%{q}%"))
    result = await session.execute(base.limit(100))
    products = list(result.scalars().all())
    if not products and len(numbers_for_match) >= 3:
        size_match = _SIZE_RE.search(original)
        if size_match:
            main_numbers = [int(size_match.group(1)), int(size_match.group(2))]
        else:
            main_numbers = numbers_for_match[:2]
        fallback_filters = [Product.title_ru.ilike(f"%{num}%") for num in main_numbers]
        fallback_query = select(Product).where(and_(*fallback_filters)).limit(100)
        fallback_result = await session.execute(fallback_query)
        products = list(fallback_result.scalars().all())
    if numbers_for_match:
        products = [
            product
            for product in products
            if all(str(num) in (product.title_ru or "").lower() for num in numbers_for_match)
        ]

    tokens_to_check = tokens

    if tokens_to_check:
        filtered_products = []
        for product in products:
            title_words = _TOKEN_RE.findall(normalize_query_text(product.title_ru or ""))
            sku_words = _TOKEN_RE.findall(normalize_query_text(product.sku or ""))
            searchable_words = title_words + sku_words
            if all(_token_matches_title(token, searchable_words) for token in tokens_to_check):
                filtered_products.append(product)
        products = filtered_products
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
