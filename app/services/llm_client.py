from __future__ import annotations

from app.config import settings
from app.services import llm_gigachat, llm_ollama


async def chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    if not settings.llm_enabled or settings.llm_provider == "disabled":
        raise RuntimeError("LLM disabled")
    if settings.llm_provider == "ollama":
        return await llm_ollama.chat(messages=messages, temperature=temperature)
    if settings.llm_provider == "gigachat":
        response = await llm_gigachat.chat(messages=messages, temperature=temperature)
        return str(response.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
