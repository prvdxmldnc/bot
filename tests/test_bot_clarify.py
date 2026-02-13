import asyncio
import json
from contextlib import asynccontextmanager

from app.bot import handlers


def test_apply_clarification_tokens_appends_tokens():
    query = handlers._apply_clarification_tokens("нитки белые", ["70", "лл"])
    assert query == "нитки белые 70 лл"


def test_clarify_choose_callback_applies_tokens_and_reruns_pipeline(monkeypatch):
    captured = {"text": None}

    class FakeRedis:
        async def get(self, _key):
            return json.dumps(
                {
                    "org_id": 42,
                    "user_id": 7,
                    "base_query": "нитки белые",
                    "clarification": {
                        "options": [
                            {"label": "70 ЛЛ", "apply": {"append_tokens": ["70", "лл"]}},
                        ]
                    },
                },
                ensure_ascii=False,
            )

        async def setex(self, *_args, **_kwargs):
            return True

    class FakeMessage:
        message_id = 100

        async def edit_text(self, *_args, **_kwargs):
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
