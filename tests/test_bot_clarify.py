import asyncio
import json
from contextlib import asynccontextmanager

from app.bot import handlers


def test_apply_clarification_tokens_appends_tokens():
    query = handlers._apply_clarification_tokens("нитки белые", ["70", "лл"])
    assert query == "нитки белые 70 лл"


def test_clarify_choose_callback_applies_tokens_and_edits_message(monkeypatch):
    captured = {"text": None, "edited_text": None}

    class FakeRedis:
        async def get(self, _key):
            return json.dumps(
                {
                    "org_id": 42,
                    "user_id": 7,
                    "base_query": "нитки белые",
                    "clarification": {
                        "options": [
                            {"id": "opt_1", "label": "70 ЛЛ", "apply": {"append_tokens": ["70", "лл"]}},
                        ]
                    },
                },
                ensure_ascii=False,
            )

        async def setex(self, *_args, **_kwargs):
            return True

    class FakeMessage:
        message_id = 100

        async def edit_text(self, text, **_kwargs):
            captured["edited_text"] = text
            return None

    class FakeFromUser:
        id = 11

    class FakeCallback:
        data = "clarify:choose:1"
        message = FakeMessage()
        from_user = FakeFromUser()

        async def answer(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_run_search_pipeline(_session, **kwargs):
        captured["text"] = kwargs.get("text")
        return {"results": [{"title_ru": "Нитка 70 ЛЛ", "sku": "T-1"}], "decision": {}}

    monkeypatch.setattr(handlers, "_redis_client", lambda: FakeRedis())
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_run_search_pipeline)

    asyncio.run(handlers.clarify_choice(FakeCallback()))

    assert captured["text"] == "нитки белые 70 лл"
    assert "Вот что нашлось" in (captured["edited_text"] or "")


def test_clarify_next_callback_uses_offset_and_edits(monkeypatch):
    captured = {"offset": None, "edited_text": None}

    class FakeRedis:
        async def get(self, _key):
            return json.dumps(
                {
                    "org_id": 42,
                    "user_id": 7,
                    "base_query": "спанбонд",
                    "clarification": {
                        "options": [{"id": "opt_1", "label": "v1", "apply": {"append_tokens": ["v1"]}}]
                    },
                },
                ensure_ascii=False,
            )

        async def setex(self, *_args, **_kwargs):
            return True

    class FakeMessage:
        message_id = 101

        async def edit_text(self, text, **_kwargs):
            captured["edited_text"] = text
            return None

    class FakeFromUser:
        id = 12

    class FakeCallback:
        data = "clarify:next:10"
        message = FakeMessage()
        from_user = FakeFromUser()

        async def answer(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_run_search_pipeline(_session, **kwargs):
        captured["offset"] = kwargs.get("clarify_offset")
        return {
            "results": [],
            "decision": {
                "clarification": {
                    "question": "Уточни товар:",
                    "options": [{"id": "opt_11", "label": "Вариант 11", "apply": {"append_tokens": ["11"]}}],
                    "next_offset": 20,
                    "prev_offset": 0,
                    "total": 30,
                    "offset": 10,
                }
            },
        }

    monkeypatch.setattr(handlers, "_redis_client", lambda: FakeRedis())
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_run_search_pipeline)

    asyncio.run(handlers.clarify_choice(FakeCallback()))

    assert captured["offset"] == 10
    assert captured["edited_text"] == "Уточни товар:"
