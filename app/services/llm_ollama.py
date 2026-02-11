from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def normalize_ollama_base_url(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base


async def _post_ollama(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = normalize_ollama_base_url(settings.ollama_base_url)
    endpoint = f"{base_url}{path}"
    timeout = settings.llm_timeout_seconds

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(endpoint, json=payload)
            if response.status_code == 404:
                logger.error(
                    "Ollama endpoint 404: likely base_url includes /api twice, base=%s endpoint=%s",
                    base_url,
                    endpoint,
                )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.exception("Ollama timeout base=%s endpoint=%s", base_url, endpoint)
            raise RuntimeError("Ollama timeout") from exc
        except httpx.HTTPError as exc:
            logger.exception("Ollama request failed base=%s endpoint=%s", base_url, endpoint)
            raise RuntimeError("Ollama request failed") from exc

    data = response.json()
    return data if isinstance(data, dict) else {}


async def chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    data = await _post_ollama("/api/chat", payload)
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    content = str(content or "").strip()
    if not content:
        raise RuntimeError("Ollama empty response")
    return content


async def generate(prompt: str, temperature: float = 0.2) -> str:
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    data = await _post_ollama("/api/generate", payload)
    content = str(data.get("response") or "").strip()
    if not content:
        raise RuntimeError("Ollama empty response")
    return content
