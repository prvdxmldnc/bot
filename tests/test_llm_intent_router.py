import asyncio

from app.services import llm_intent_router


def test_parse_actions_from_text_multi_intent_message():
    text = "добавь 3 мотка ниток белых и что там по поводу поролона, когда придет?"
    result = llm_intent_router.parse_actions_from_text(text)

    assert len(result.actions) >= 2
    add_action = next(a for a in result.actions if a.type == "ADD_ITEM")
    eta_action = next(a for a in result.actions if a.type == "ASK_STOCK_ETA")

    assert add_action.qty == 3
    assert add_action.unit == "моток"
    assert add_action.query_core == "ниток белых"
    assert "3" not in (add_action.query_core or "")
    assert "добав" not in (add_action.query_core or "")
    assert (eta_action.subject or "") == "поролон"


def test_parse_actions_heuristic_adds_eta_when_llm_unknown():
    text = "что там по поводу поролона, когда придет?"
    result = llm_intent_router.parse_actions_from_text(
        text,
        llm_payload='{"actions":[{"type":"UNKNOWN"}]}'
    )

    assert any(action.type == "ASK_STOCK_ETA" for action in result.actions)


def test_route_message_supports_list_payload(monkeypatch):
    async def fake_chat(messages, temperature=0.2):
        return (
            '[{"type":"ADD_ITEM","query_core":"нитки белые","qty":3,"unit":"моток"},'
            '{"type":"ASK_STOCK_ETA","query_core":"поролон"}]'
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


def test_route_message_rejects_english_llm_payload(monkeypatch):
    async def fake_chat(messages, temperature=0.2):
        return '{"actions":[{"type":"ADD_ITEM","query_core":"white thread 3 meters","qty":3,"unit":"m"}]}'

    monkeypatch.setattr(llm_intent_router, "llm_available", lambda: True)
    monkeypatch.setattr(llm_intent_router, "llm_chat", fake_chat)
    result = asyncio.run(llm_intent_router.route_message("add white thread"))

    assert result["actions"]
    assert result["actions"][0]["type"] == "UNKNOWN"
    assert "русск" in (result["actions"][0].get("query_core") or "").lower()


def test_route_message_invalid_json_fallback(monkeypatch):
    async def fake_chat(messages, temperature=0.2):
        return "не json"

    monkeypatch.setattr(llm_intent_router, "llm_available", lambda: True)
    monkeypatch.setattr(llm_intent_router, "llm_chat", fake_chat)

    result = asyncio.run(llm_intent_router.route_message("болт 8 30 5шт"))

    assert result["actions"]
    assert result["actions"][0]["type"] == "ADD_ITEM"
