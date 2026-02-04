from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.services.llm_gigachat import chat

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return ""


def _parse_rerank_content(content: str) -> dict[str, Any]:
    raw = _extract_json_object(content)
    if not raw:
        return {"best": [], "need_clarify": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"best": [], "need_clarify": []}

    best_raw = data.get("best") if isinstance(data, dict) else []
    need_clarify = data.get("need_clarify") if isinstance(data, dict) else []
    if not isinstance(best_raw, list):
        best_raw = []
    if not isinstance(need_clarify, list):
        need_clarify = []

    seen: set[int] = set()
    best: list[dict[str, Any]] = []
    for item in best_raw:
        if not isinstance(item, dict):
            continue
        product_id = item.get("product_id")
        score = item.get("score")
        if not isinstance(product_id, int):
            continue
        if product_id in seen:
            continue
        seen.add(product_id)
        if not isinstance(score, (int, float)):
            score = 0.0
        best.append(
            {
                "product_id": product_id,
                "score": float(score),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
        if len(best) >= 5:
            break

    return {"best": best, "need_clarify": need_clarify}


async def rerank_products(
    query: str,
    candidates: list[dict[str, Any]],
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(candidates) < 2:
        return {"best": [], "need_clarify": []}
    payload_candidates = []
    for item in candidates:
        payload_candidates.append(
            {
                "product_id": item.get("id") or item.get("product_id"),
                "title": item.get("title_ru") or item.get("title"),
                "category": item.get("category"),
                "price": item.get("price"),
                "stock": item.get("stock_qty") or item.get("stock"),
            }
        )

    prompt = (
        "Ты ранжируешь список товаров по релевантности запросу. "
        "Верни строго JSON: {\"best\":[{\"product_id\":int,\"score\":float,\"reason\":str}],"
        "\"need_clarify\":[{\"field\":\"qty|unit|size|color|code|din\",\"reason\":str}]}. "
        "best максимум 5, score 0..1. Без лишнего текста. "
        f"Запрос: {query}. Атрибуты: {attrs or {}}. Кандидаты: {payload_candidates}"
    )

    try:
        data = await chat(
            messages=[
                {"role": "system", "content": "Ты помощник по подбору товаров."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
    except (httpx.HTTPError, ValueError):
        logger.exception("LLM rerank failed")
        return {"best": [], "need_clarify": []}

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_rerank_content(content)
    best_ids = [item["product_id"] for item in parsed.get("best", []) if "product_id" in item]
    if best_ids:
        top_score = parsed["best"][0].get("score") if parsed.get("best") else None
        logger.info("LLM rerank best_ids=%s top_score=%s", best_ids, top_score)
    return parsed
