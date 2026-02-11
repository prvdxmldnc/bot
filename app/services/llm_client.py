from __future__ import annotations

from app.config import settings
from app.services import llm_gigachat, llm_ollama


def llm_available() -> bool:
    if not settings.llm_enabled or settings.llm_provider == "disabled":
        return False

    if settings.llm_provider == "ollama":
        return bool((settings.ollama_base_url or "").strip() and (settings.ollama_model or "").strip())

    if settings.llm_provider == "gigachat":
        return bool(
            (settings.gigachat_basic_auth_key or "").strip()
            and (settings.gigachat_api_base_url or "").strip()
            and (settings.gigachat_model or "").strip()
        )

    return False


async def chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    if not llm_available():
        raise RuntimeError("LLM disabled")

    if settings.llm_provider == "ollama":
        return await llm_ollama.chat(messages=messages, temperature=temperature)

    if settings.llm_provider == "gigachat":
        response = await llm_gigachat.chat(messages=messages, temperature=temperature)
        return str(response.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()

    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
