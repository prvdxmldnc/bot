from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgProductStats, Product

_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_STOP_TOKENS = {
    "по", "и", "для", "на", "в", "с", "без", "шт", "штук", "кг", "мм", "см", "тип", "нужно", "добавь", "добавить"
}
_COLOR_RE = re.compile(r"\b(бел\w*|черн\w*|сер\w*|беж\w*|крас\w*|син\w*|зел\w*)\b", re.IGNORECASE)
_DIM_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:х|x|мм|см|м)\s?\d*(?:\s?(?:х|x)\s?\d+)?\b", re.IGNORECASE)
_CODE_RE = re.compile(r"\b(?:[A-ZА-Я]{1,3}-?\d{1,6}|ST\d{3,6}|M\d{1,3}|PH\d|\d{3,6})\b", re.IGNORECASE)
_TYPE_RE = re.compile(r"\b(рулон\w*|разъ[её]м\w*|агро\w*|сс\b|с\s+пружин\w*|без\s+пружин\w*)\b", re.IGNORECASE)


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


def _entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if total <= 1:
        return 0.0
    score = 0.0
    for count in counter.values():
        p = count / total
        score -= p * math.log2(p)
    return score


def build_facet_options(candidates: list[dict[str, Any]], max_values: int = 30) -> tuple[str, list[dict[str, Any]]] | None:
    if not candidates:
        return None

    buckets: dict[str, Counter[str]] = {
        "цвет": Counter(),
        "размер": Counter(),
        "код": Counter(),
        "тип": Counter(),
    }
    for item in candidates:
        title = str(item.get("title_ru") or "")
        for m in _COLOR_RE.findall(title):
            buckets["цвет"][m.lower()] += 1
        for m in _DIM_RE.findall(title):
            buckets["размер"][m.lower().replace(" ", "")] += 1
        for m in _CODE_RE.findall(title):
            buckets["код"][m.upper()] += 1
        for m in _TYPE_RE.findall(title):
            buckets["тип"][m.lower()] += 1

    ranked = [(facet, _entropy(counter), counter) for facet, counter in buckets.items() if len(counter) >= 2]
    if not ranked:
        return None

    facet, _, counter = max(ranked, key=lambda x: x[1])
    options = []
    for idx, (value, _cnt) in enumerate(counter.most_common(max_values), start=1):
        options.append({"id": f"facet_{facet}_{idx}", "label": value, "apply": {"append_tokens": [value]}})
    return (facet, options)


def build_clarification(
    *,
    reason: str,
    options: list[dict[str, Any]],
    offset: int = 0,
    page_size: int = 10,
    question: str | None = None,
) -> dict[str, Any]:
    total = len(options)
    safe_offset = max(0, min(offset, max(total - 1, 0))) if total > 0 else 0
    page = options[safe_offset : safe_offset + page_size]
    next_offset = safe_offset + page_size if safe_offset + page_size < total else None
    prev_offset = safe_offset - page_size if safe_offset - page_size >= 0 else (0 if safe_offset > 0 else None)

    if not question:
        question = "Уточни товар:" if reason == "no_candidates" else "Уточни вариант:"

    return {
        "question": question,
        "reason": reason,
        "options": page,
        "offset": safe_offset,
        "next_offset": next_offset,
        "prev_offset": prev_offset,
        "total": total,
    }


def suggestions_to_options(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(suggestions, start=1):
        title = str(item.get("title") or item.get("title_ru") or "Товар")
        out.append({"id": f"opt_{idx}", "label": _short_label(title), "apply": {"append_tokens": [title]}})
    return out
