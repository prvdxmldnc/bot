from __future__ import annotations

import re
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgProductStats, Product

_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_STOP_TOKENS = {
    "по", "и", "для", "на", "в", "с", "без", "шт", "штук", "кг", "мм", "см", "тип", "нужно", "добавь", "добавить"
}


def _short_label(title: str, max_len: int = 56) -> str:
    cleaned = " ".join((title or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _tokenize(query: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(query or "")]


def extract_head_token(query: str) -> str | None:
    for token in _tokenize(query):
        if token in _STOP_TOKENS or token.isdigit() or len(token) < 4:
            continue
        return token
    return None


async def history_suggestions(session: AsyncSession, org_id: int, token: str, limit: int = 60) -> list[dict[str, Any]]:
    if not token:
        return []
    stmt = (
        select(Product.id, Product.title_ru)
        .join(OrgProductStats, OrgProductStats.product_id == Product.id)
        .where(OrgProductStats.org_id == org_id)
        .where(Product.title_ru.ilike(f"%{token}%"))
        .order_by(desc(OrgProductStats.orders_count), desc(OrgProductStats.last_order_at))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [{"product_id": row[0], "title": row[1]} for row in rows if row[1]]


def build_clarification(
    *,
    query_core: str,
    reason: str,
    suggestions: list[dict[str, Any]],
    offset: int = 0,
    page_size: int = 10,
) -> dict[str, Any] | None:
    total = len(suggestions)
    if total <= 0:
        return {
            "question": "Не нашёл точный вариант. Уточни товар/артикул:",
            "reason": reason,
            "options": [],
            "offset": 0,
            "next_offset": None,
            "prev_offset": None,
            "total": 0,
        }

    safe_offset = max(0, min(offset, max(total - 1, 0)))
    page = suggestions[safe_offset : safe_offset + page_size]

    options: list[dict[str, Any]] = []
    for idx, item in enumerate(page, start=1):
        title = str(item.get("title") or item.get("title_ru") or "Товар")
        label = _short_label(title)
        options.append(
            {
                "id": f"opt_{safe_offset + idx}",
                "label": label,
                "apply": {"append_tokens": [title]},
            }
        )

    next_offset = safe_offset + page_size if safe_offset + page_size < total else None
    prev_offset = safe_offset - page_size if safe_offset - page_size >= 0 else (0 if safe_offset > 0 else None)

    question = "Уточни товар:" if reason == "no_candidates" else "Нашёл много вариантов. Уточни товар:"
    return {
        "question": question,
        "reason": reason,
        "options": options,
        "offset": safe_offset,
        "next_offset": next_offset,
        "prev_offset": prev_offset,
        "total": total,
    }
