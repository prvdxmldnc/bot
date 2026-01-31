from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx
import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(шт|кг|уп|м)\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"(\d+)\s*[xх*]\s*(\d+)", re.IGNORECASE)
_DIN_RE = re.compile(r"din\s*(\d+)", re.IGNORECASE)


def _cache_key(prefix: str, text: str) -> str:
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


async def _get_cache(key: str) -> dict[str, Any] | None:
    if not settings.redis_url:
        return None
    client = redis.from_url(settings.redis_url)
    raw = await client.get(key)
    if raw:
        return json.loads(raw)
    return None


async def _set_cache(key: str, value: dict[str, Any], ttl: int = 300) -> None:
    if not settings.redis_url:
        return
    client = redis.from_url(settings.redis_url)
    await client.set(key, json.dumps(value), ex=ttl)


def _fallback_parse(text: str) -> dict[str, Any]:
    items = []
    for part in re.split(r"[\n,;]+", text):
        raw = part.strip()
        if not raw:
            continue
        qty = 1
        unit = "шт"
        match = _UNIT_RE.search(raw)
        if match:
            qty = int(float(match.group(1).replace(",", ".")))
            unit = match.group(2).lower()
        size = None
        size_match = _SIZE_RE.search(raw)
        if size_match:
            size = f"{size_match.group(1)}x{size_match.group(2)}"
        din_match = _DIN_RE.search(raw)
        attrs = {}
        if size:
            attrs["size"] = size
        if din_match:
            attrs["din"] = din_match.group(1)
        numbers = [int(n) for n in re.findall(r"\d+", raw)]
        attrs["key_numbers"] = numbers
        items.append(
            {
                "raw": raw,
                "query": raw,
                "qty": qty,
                "unit": unit,
                "attrs": attrs,
            }
        )
    return {"items": items, "language": "ru"}


async def parse_order(text: str) -> dict[str, Any]:
    if not settings.gigachat_api_key:
        return _fallback_parse(text)
    cache_key = _cache_key("gigachat:parse", text)
    cached = await _get_cache(cache_key)
    if cached:
        return cached
    payload = {
        "model": settings.gigachat_model or "GigaChat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Разбери заказ на позиции. Верни JSON вида "
                    "{\"items\":[{\"raw\":\"...\",\"query\":\"...\",\"qty\":1,\"unit\":\"шт\","
                    "\"attrs\":{\"size\":\"8x30\",\"din\":\"933\",\"coating\":\"оцинк\",\"key_numbers\":[8,30,933]}}],"
                    "\"language\":\"ru\"}. Не добавляй лишний текст."
                ),
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
    }
    timeout = settings.gigachat_timeout_seconds or 20
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=settings.gigachat_verify_ssl) as client:
            response = await client.post(
                f"{settings.gigachat_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.gigachat_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        logger.exception("GigaChat parse request failed, fallback")
        parsed = _fallback_parse(text)
        await _set_cache(cache_key, parsed)
        return parsed
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("GigaChat parse failed, fallback", extra={"content": content})
        parsed = _fallback_parse(text)
    await _set_cache(cache_key, parsed)
    return parsed


async def rerank_candidates(item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"best_id": None, "confidence": 0.0, "reason": "no_candidates", "alternatives": []}
    if not settings.gigachat_api_key:
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
    payload = {
        "model": settings.gigachat_model or "GigaChat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Выбери лучший товар. Верни JSON: "
                    "{\"best_id\":123,\"confidence\":0.85,\"reason\":\"...\","
                    "\"alternatives\":[{\"id\":1,\"confidence\":0.6}]}"
                ),
            },
            {"role": "user", "content": json.dumps({"item": item, "candidates": candidates}, ensure_ascii=False)},
        ],
        "temperature": 0.1,
    }
    timeout = settings.gigachat_timeout_seconds or 20
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=settings.gigachat_verify_ssl) as client:
            response = await client.post(
                f"{settings.gigachat_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.gigachat_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        logger.exception("GigaChat rerank request failed, fallback")
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
