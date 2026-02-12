import asyncio

from app.services import llm_intent_router


def test_route_message_llm_actions(monkeypatch):
    async def fake_chat(messages, temperature=0.2):
        return (
            '{"actions":[{"type":"ADD_ITEM","query_core":"нитка белая","qty":3,"unit":"моток"},'
            '{"type":"ASK_STOCK_ETA","query_core":"поролон"}]}'
        )

    monkeypatch.setattr(llm_intent_router, "llm_available", lambda: True)
    monkeypatch.setattr(llm_intent_router, "llm_chat", fake_chat)

    result = asyncio.run(
        llm_intent_router.route_message(
            "добавьте 3 мотка ниток белых и что там с поролоном когда придет"
        )
    )

    assert len(result["actions"]) == 2
    assert result["actions"][0]["type"] == "ADD_ITEM"
    assert result["actions"][1]["type"] == "ASK_STOCK_ETA"


def test_route_message_invalid_json_fallback(monkeypatch):
    async def fake_chat(messages, temperature=0.2):
        return "не json"

    monkeypatch.setattr(llm_intent_router, "llm_available", lambda: True)
    monkeypatch.setattr(llm_intent_router, "llm_chat", fake_chat)

    result = asyncio.run(llm_intent_router.route_message("болт 8 30 5шт"))

    assert result["actions"]
    assert result["actions"][0]["type"] == "ADD_ITEM"
