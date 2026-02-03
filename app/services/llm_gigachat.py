from __future__ import annotations

import os
import hashlib
import json
import logging
import re
import time
import uuid
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


def _redis_client() -> redis.Redis | None:
    if not settings.redis_url:
        return None
    return redis.from_url(settings.redis_url)


async def _get_cache(key: str) -> dict[str, Any] | None:
    client = _redis_client()
    if not client:
        return None
    raw = await client.get(key)
    if raw:
        return json.loads(raw)
    return None


async def _set_cache(key: str, value: dict[str, Any], ttl: int = 300) -> None:
    client = _redis_client()
    if not client:
        return
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


async def get_access_token(redis_client: redis.Redis | None = None) -> str:
    if not settings.gigachat_basic_auth_key:
        raise ValueError("GigaChat basic auth key is missing")

    client = redis_client or _redis_client()
    cache_prefix = settings.gigachat_token_cache_prefix or "gigachat:token"
    token_key = f"{cache_prefix}:value"
    expires_key = f"{cache_prefix}:expires_at"

    if client:
        cached_token = await client.get(token_key)
        cached_expires = await client.get(expires_key)
        if cached_token and cached_expires:
            try:
                expires_at = int(cached_expires)
            except ValueError:
                expires_at = 0
            now_ms = int(time.time() * 1000)
            if expires_at - now_ms > 60_000:
                return cached_token.decode("utf-8")

    timeout = settings.gigachat_timeout_seconds or 20
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {settings.gigachat_basic_auth_key}",
    }
    data = {"scope": settings.gigachat_scope or "GIGACHAT_API_PERS"}

    async with httpx.AsyncClient(timeout=timeout, verify=os.getenv("SSL_CERT_FILE") or True) as http_client:
        try:
            response = await http_client.post(settings.gigachat_oauth_url, headers=headers, data=data)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("GigaChat OAuth failed status=%s", exc.response.status_code)
            raise
        except httpx.HTTPError:
            logger.exception("GigaChat OAuth request failed")
            raise

    token = payload.get("access_token")
    expires_at = payload.get("expires_at")
    if not token or not expires_at:
        raise ValueError("GigaChat OAuth response missing token")

    if client:
        now_ms = int(time.time() * 1000)
        ttl = max(int((int(expires_at) - now_ms) / 1000) - 60, 1)
        await client.set(token_key, token, ex=ttl)
        await client.set(expires_key, str(expires_at), ex=ttl)

    return token


async def _invalidate_token_cache(redis_client: redis.Redis | None = None) -> None:
    client = redis_client or _redis_client()
    if not client:
        return
    cache_prefix = settings.gigachat_token_cache_prefix or "gigachat:token"
    await client.delete(f"{cache_prefix}:value", f"{cache_prefix}:expires_at")


async def chat(messages: list[dict[str, str]], temperature: float = 0.2) -> dict[str, Any]:
    timeout = settings.gigachat_timeout_seconds or 20
    payload = {
        "model": settings.gigachat_model or "GigaChat",
        "messages": messages,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=timeout, verify=os.getenv("SSL_CERT_FILE") or True) as http_client:
        token = await get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = await http_client.post(
            f"{settings.gigachat_api_base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        if response.status_code in {401, 403}:
            logger.warning("GigaChat chat unauthorized, refreshing token")
            await _invalidate_token_cache()
            token = await get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await http_client.post(
                f"{settings.gigachat_api_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("GigaChat chat failed status=%s", exc.response.status_code)
            raise
        return response.json()


async def parse_order(text: str) -> dict[str, Any]:
    if not settings.gigachat_basic_auth_key:
        return _fallback_parse(text)
    cache_key = _cache_key("gigachat:parse", text)
    cached = await _get_cache(cache_key)
    if cached:
        return cached
    try:
        data = await chat(
            messages=[
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
            temperature=0.1,
        )
    except (httpx.HTTPError, ValueError):
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


async def safe_parse_order(text: str) -> dict[str, Any]:
    try:
        return await parse_order(text)
    except Exception:
        logger.exception("GigaChat parse crashed, fallback")
        return _fallback_parse(text)


async def rerank_candidates(item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"best_id": None, "confidence": 0.0, "reason": "no_candidates", "alternatives": []}
    if not settings.gigachat_basic_auth_key:
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
    try:
        data = await chat(
            messages=[
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
            temperature=0.1,
        )
    except (httpx.HTTPError, ValueError):
        logger.exception("GigaChat rerank request failed, fallback")
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        best = max(candidates, key=lambda c: c.get("score", 0))
        return {"best_id": best["id"], "confidence": 0.6, "reason": "fallback", "alternatives": []}
