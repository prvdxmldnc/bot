from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Product


async def llm_search(session: AsyncSession, query: str) -> list[dict[str, Any]]:
    if settings.openai_api_key:
        payload = {
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a catalog assistant. Return JSON list of product titles to match.",
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
        return [{"title": content, "source": "llm"}]
    result = await session.execute(select(Product).where(Product.title_ru.ilike(f"%{query}%")))
    return [{"title": product.title_ru, "source": "db"} for product in result.scalars().all()]
