import asyncio
from contextlib import asynccontextmanager

from app.bot import handlers


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def setex(self, key, _ttl, value):
        self.data[key] = value


class FakeState:
    async def clear(self):
        return None

    async def set_state(self, *_args, **_kwargs):
        return None


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.from_user = type("U", (), {"id": 700, "username": "admin"})()
        self.chat = type("C", (), {"id": 700})()
        self.bot = type("B", (), {"edit_message_text": self._edit})()
        self.answers = []

    async def _edit(self, **_kwargs):
        return None

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)
        return type("S", (), {"message_id": 1})()


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.from_user = type("U", (), {"id": 700})()
        self.message = type("M", (), {"edit_text": self._edit, "message_id": 1})()

    async def _edit(self, *_args, **_kwargs):
        return None

    async def answer(self, *_args, **_kwargs):
        return None


def test_org_command_sets_override_and_pipeline_uses_it(monkeypatch):
    redis = FakeRedis()
    captured = {"org_id": None}

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_args, **_kwargs):
        return type("User", (), {"id": 70})()

    async def fake_route_message(_text):
        return {"actions": [{"type": "ADD_ITEM", "query_core": "поролон"}]}

    async def fake_pipeline(_session, **kwargs):
        captured["org_id"] = kwargs.get("org_id")
        return {"results": [{"title_ru": "Поролон 10", "sku": "P10"}], "decision": {}}

    monkeypatch.setattr(handlers, "_redis_client", lambda: redis)
    monkeypatch.setattr(handlers, "_admin_user_ids", lambda: {700})
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "route_message", fake_route_message)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_pipeline)

    msg = FakeMessage()
    asyncio.run(handlers.org_command(msg, FakeState()))
    asyncio.run(handlers.org_callback(FakeCallback("org:set:42"), FakeState()))

    # request mode input should now use override org=42
    handlers._REQUEST_MODE_MEM[700] = handlers._default_request_state(42)
    msg.text = "поролон 10"
    asyncio.run(handlers.request_mode_text(msg, FakeState()))

    assert captured["org_id"] == 42
