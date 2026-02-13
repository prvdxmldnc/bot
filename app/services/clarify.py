from __future__ import annotations

import re
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgProductStats, Product

_COLOR_TOKENS = {"сер", "сера", "серый", "беж", "бежев", "бел", "белый", "черн", "черный", "син", "зел"}
_STOP_TOKENS = {
    "по", "и", "для", "на", "в", "с", "без", "шт", "штук", "кг", "мм", "см", "тип", "нужно", "нужны", "добавь", "добавить"
}
_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"\bн\s*-?\s*(\d{2,4})\b", re.IGNORECASE)


def _tokenize(query: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(query or "")]


def _short_label(title: str, max_len: int = 48) -> str:
    cleaned = " ".join((title or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _key_token(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in _STOP_TOKENS or token.isdigit() or len(token) < 4:
            continue
        return token
    return None


async def history_suggestions(session: AsyncSession, org_id: int, token: str, limit: int = 6) -> list[dict[str, Any]]:
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
    result = await session.execute(stmt)
    rows = result.all()
    return [{"product_id": row[0], "title": row[1]} for row in rows]


def build_clarification(
    *,
    query_core: str,
    candidates: list[dict[str, Any]],
    history_top_titles: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    tokens = _tokenize(query_core)

    if not candidates:
        if history_top_titles:
            options = [
                {
                    "id": f"hist_{idx}",
                    "label": _short_label(item.get("title") or "Товар"),
                    "apply": {"append_tokens": [item.get("title") or ""]},
                }
                for idx, item in enumerate(history_top_titles[:6], start=1)
            ]
            if options:
                return {
                    "question": "Уточни товар:",
                    "reason": "history_suggestions",
                    "options": options,
                }
        return {
            "question": "Не нашёл точный вариант. Уточни товар/артикул:",
            "reason": "no_candidates",
            "options": [],
        }

    # нитки: ЛЛ vs АП
    if any("нит" in t for t in tokens):
        has_ll = any(re.search(r"\bлл\b", (c.get("title_ru") or "").lower()) for c in candidates)
        has_ap = any(re.search(r"\bап\b", (c.get("title_ru") or "").lower()) for c in candidates)
        if has_ll and has_ap:
            return {
                "question": "Уточни вариант ниток:",
                "reason": "ambiguous_facets",
                "options": [
                    {"id": "thread_ll", "label": "70 ЛЛ", "apply": {"append_tokens": ["70", "лл"]}},
                    {"id": "thread_ap", "label": "70 АП", "apply": {"append_tokens": ["70", "ап"]}},
                ],
            }

    # опоры: Н-40 / Н-100
    if any("опор" in t for t in tokens):
        heights: list[str] = []
        for candidate in candidates:
            title = (candidate.get("title_ru") or "")
            m = _HEIGHT_RE.search(title)
            if m:
                h = m.group(1)
                if h not in heights:
                    heights.append(h)
        if len(heights) >= 2:
            return {
                "question": "Уточни высоту опоры:",
                "reason": "ambiguous_facets",
                "options": [
                    {"id": f"opora_h_{h}", "label": f"Н-{h}", "apply": {"append_tokens": [f"н-{h}"]}}
                    for h in heights[:4]
                ],
            }

    # category/diversity cluster fallback
    category_groups: dict[int, int] = {}
    for c in candidates:
        cat = c.get("category_id")
        if isinstance(cat, int):
            category_groups[cat] = category_groups.get(cat, 0) + 1
    major = [cat for cat, cnt in category_groups.items() if cnt >= 2]
    if len(major) >= 2:
        return {
            "question": "Уточни вариант:",
            "reason": "ambiguous_clusters",
            "options": [
                {
                    "id": f"cat_{cat}",
                    "label": f"Категория {cat}",
                    "apply": {"restrict_category_ids": [cat]},
                }
                for cat in major[:4]
            ],
        }

    return None
