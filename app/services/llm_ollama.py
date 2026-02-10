from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    timeout = settings.llm_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.exception("Ollama chat timeout")
            raise RuntimeError("Ollama timeout") from exc
        except httpx.HTTPError as exc:
            logger.exception("Ollama chat request failed")
            raise RuntimeError("Ollama request failed") from exc
    data = response.json()
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    content = str(content or "").strip()
    if not content:
        raise RuntimeError("Ollama empty response")
    return content
