from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.category_manifest import get_category_manifest
from app.services.llm_client import chat

logger = logging.getLogger(__name__)

_REMOVE_QTY_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(шт|штук|кг|уп|упаков\w*|кор|короб\w*|рол|рул|рулон|комплект|м|пог\.м)\b",
    re.IGNORECASE,
)
_REMOVE_DASH_QTY_RE = re.compile(r"[-–—]\s*\d+\s*(рол|рул|рулон|уп|кор|шт|штук)\b", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-zа-я]{4,}", re.IGNORECASE)

async def narrow_categories(user_text: str, session) -> dict[str, Any]:
    manifest = await get_category_manifest(session)
    filtered = []
    for item in manifest:
        title = (item.get("title") or "").lower()
        path = (item.get("path") or "").lower()
        if any(
            token in title or token in path
            for token in [
                "удален",
                "удаленные",
                "устарел",
                "устарев",
                "наименован",
                "test",
                "cat",
            ]
        ):
            continue
        if item.get("count_direct", 0) <= 0:
            continue
        examples = [
            example
            for example in (item.get("examples") or [])
            if example and len(example) >= 2 and not str(example).isdigit()
        ]
        if not examples:
            continue
        filtered.append({**item, "examples": examples})
    filtered.sort(key=lambda item: item.get("count_direct", 0), reverse=True)
    narrowed_query = _normalize_query(user_text)
    tokens = _extract_tokens(narrowed_query)
    candidate_categories = _select_candidates(filtered, tokens)
    if candidate_categories:
        logger.info(
            "Category narrow tokens=%s candidates=%s top_paths=%s",
            tokens,
            len(candidate_categories),
            [item.get("path") for item in candidate_categories[:5]],
        )
    else:
        logger.info("Category narrow tokens=%s candidates=0", tokens)
    context_source = candidate_categories if candidate_categories else filtered[:150]
    context_items = [
        {
            "id": item.get("category_id"),
            "path": item.get("path"),
            "count": item.get("count_direct"),
            "examples": item.get("examples", [])[:3],
        }
        for item in context_source
    ]
    prompt = (
        "Выбери до 5 наиболее релевантных категорий для запроса. "
        "Выбирай category_ids только из списка ids. Если не уверен — верни []. "
        "Ответь строго JSON: {\"category_ids\":[1,2],\"confidence\":0.0,\"reason\":\"...\"}."
    )
    try:
        content = await chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"query": narrowed_query, "categories": context_items})},
            ],
            temperature=0.2,
        )
    except Exception:
        logger.exception("LLM category narrow failed")
        return {"category_ids": [], "confidence": 0.0, "reason": "llm_failed"}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"category_ids": [], "confidence": 0.0, "reason": "parse_failed"}
    if not isinstance(data, dict):
        return {"category_ids": [], "confidence": 0.0, "reason": "parse_failed"}
    ids = data.get("category_ids")
    confidence = data.get("confidence")
    if not isinstance(ids, list):
        return {"category_ids": [], "confidence": 0.0, "reason": "parse_failed"}
    allowed_ids = {item["id"] for item in context_items if isinstance(item.get("id"), int)}
    cleaned: list[int] = []
    seen = set()
    for value in ids:
        try:
            category_id = int(value)
        except (TypeError, ValueError):
            return {"category_ids": [], "confidence": 0.0, "reason": "parse_failed"}
        if category_id not in allowed_ids:
            return {"category_ids": [], "confidence": 0.0, "reason": "parse_failed"}
        if category_id in seen:
            continue
        seen.add(category_id)
        cleaned.append(category_id)
        if len(cleaned) >= 5:
            break
    try:
        parsed_confidence = float(confidence)
    except (TypeError, ValueError):
        parsed_confidence = 0.0
    return {
        "category_ids": cleaned,
        "confidence": parsed_confidence,
        "reason": str(data.get("reason") or ""),
    }


def _normalize_query(text: str) -> str:
    cleaned = text.lower()
    cleaned = _REMOVE_DASH_QTY_RE.sub("", cleaned)
    cleaned = _REMOVE_QTY_UNIT_RE.sub("", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _extract_tokens(text: str) -> list[str]:
    tokens = []
    seen = set()
    for match in _TOKEN_RE.findall(text):
        token = match.lower()
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _select_candidates(categories: list[dict[str, Any]], tokens: list[str]) -> list[dict[str, Any]]:
    if not tokens:
        return []
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for item in categories:
        path = (item.get("path") or "").lower()
        examples = [str(example).lower() for example in (item.get("examples") or [])]
        match_count = 0
        for token in tokens:
            if token in path or any(token in example for example in examples):
                match_count += 1
        if match_count:
            scored.append((match_count, int(item.get("count_direct", 0)), item))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in scored[:80]]
