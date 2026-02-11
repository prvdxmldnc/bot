import asyncio

import httpx

from app.config import settings
from app.services import llm_ollama


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": "ok"}}


def test_normalize_ollama_base_url_removes_api_suffix():
    assert llm_ollama.normalize_ollama_base_url("http://ollama:11434/api") == "http://ollama:11434"
    assert llm_ollama.normalize_ollama_base_url(" http://ollama:11434/api/ ") == "http://ollama:11434"


def test_ollama_chat_uses_single_api_chat_suffix(monkeypatch):
    called = {}

    async def fake_post(self, url, *args, **kwargs):
        called["url"] = str(url)
        return _FakeResponse()

    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama:11434/api")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = asyncio.run(llm_ollama.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result == "ok"
    assert called["url"] == "http://ollama:11434/api/chat"
