from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Product


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
    if settings.gigachat_api_key:
        payload = {
            "model": settings.gigachat_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты помощник по каталогу. Верни JSON-массив объектов с полями "
                        "`title` и опционально `qty`."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.gigachat_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.gigachat_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_llm_content(content, "gigachat")
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
