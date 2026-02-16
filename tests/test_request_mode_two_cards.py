import asyncio
from contextlib import asynccontextmanager

from app.bot import handlers


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, *, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))


class FakeSent:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeMessage:
    def __init__(self, text="", uid=901, bot=None):
        self.text = text
        self.from_user = type("U", (), {"id": uid})()
        self.chat = type("C", (), {"id": uid})()
        self.bot = bot or FakeBot()
        self.sent = []

    async def answer(self, text, reply_markup=None):
        self.sent.append((text, reply_markup))
        return FakeSent(100 + len(self.sent))


class FakeState:
    async def set_state(self, *_args, **_kwargs):
        return None


class FakeCallback:
    def __init__(self, data, message, uid=901):
        self.data = data
        self.message = message
        self.from_user = type("U", (), {"id": uid})()

    async def answer(self, *_args, **_kwargs):
        return None


def test_rm_start_creates_two_cards_and_stores_ids(monkeypatch):
    handlers._REQUEST_MODE_MEM.clear()

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_a, **_k):
        return type("User", (), {"id": 11})()

    async def fake_org(*_a, **_k):
        return 42

    monkeypatch.setattr(handlers, "_redis_client", lambda: None)
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "_resolve_org_for_user", fake_org)

    msg = FakeMessage("Отправить заявку")
    asyncio.run(handlers.request_mode_start(msg, FakeState()))

    state = handlers._REQUEST_MODE_MEM[901]
    assert state["results_msg_id"] == 101
    assert state["control_msg_id"] == 102
    assert len(msg.sent) == 2


def test_request_text_edits_cards_without_extra_send(monkeypatch):
    handlers._REQUEST_MODE_MEM.clear()

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_a, **_k):
        return type("User", (), {"id": 11})()

    async def fake_org(*_a, **_k):
        return 42

    async def fake_route(_text):
        return {"actions": [{"type": "ADD_ITEM", "query_core": "поролон 10", "qty": 1, "unit": "шт"}]}

    async def fake_pipeline(_session, **_kwargs):
        return {"results": [{"title_ru": "Поролон 10", "sku": "P10"}], "decision": {}}

    monkeypatch.setattr(handlers, "_redis_client", lambda: None)
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "_resolve_org_for_user", fake_org)
    monkeypatch.setattr(handlers, "route_message", fake_route)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_pipeline)

    bot = FakeBot()
    handlers._REQUEST_MODE_MEM[901] = handlers._default_request_state(42)
    handlers._REQUEST_MODE_MEM[901]["results_msg_id"] = 101
    handlers._REQUEST_MODE_MEM[901]["control_msg_id"] = 102

    msg = FakeMessage("поролон 10", bot=bot)
    asyncio.run(handlers.request_mode_text(msg, FakeState()))

    assert len(msg.sent) == 0
    assert len(bot.edits) >= 2


def test_clarify_next_uses_offset_and_edits_cards(monkeypatch):
    handlers._REQUEST_MODE_MEM.clear()
    captured = {"offset": None}

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_a, **_k):
        return type("User", (), {"id": 11})()

    async def fake_org(*_a, **_k):
        return 42

    async def fake_pipeline(_session, **kwargs):
        captured["offset"] = kwargs.get("clarify_offset")
        return {
            "results": [],
            "decision": {
                "clarification": {
                    "question": "Уточни товар:",
                    "options": [{"id": "opt_11", "label": "Вариант 11", "apply": {"append_tokens": ["11"]}}],
                    "offset": kwargs.get("clarify_offset", 0),
                    "next_offset": 20,
                    "prev_offset": 0,
                    "total": 30,
                }
            },
        }

    monkeypatch.setattr(handlers, "_redis_client", lambda: None)
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "_resolve_org_for_user", fake_org)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_pipeline)

    handlers._REQUEST_MODE_MEM[901] = handlers._default_request_state(42)
    handlers._REQUEST_MODE_MEM[901].update(
        {
            "results_msg_id": 101,
            "control_msg_id": 102,
            "mode": "review",
            "clarify_expanded": True,
            "items": [
                {
                    "raw": "нитки белые",
                    "status": "needs_clarification",
                    "clarification": {
                        "options": [{"id": "opt_1", "label": "Вариант 1", "apply": {"append_tokens": ["1"]}}],
                        "offset": 0,
                        "next_offset": 10,
                        "total": 30,
                    },
                }
            ],
        }
    )

    bot = FakeBot()
    msg = FakeMessage("", bot=bot)
    cb = FakeCallback("rm:clarify:next:10", msg)
    asyncio.run(handlers.request_mode_callback(cb))

    assert captured["offset"] == 10
    assert bot.edits
