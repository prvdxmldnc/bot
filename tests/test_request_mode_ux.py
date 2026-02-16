import asyncio
from contextlib import asynccontextmanager

from app.bot import handlers


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, *, chat_id, message_id, text, reply_markup=None):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup})


class FakeSent:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeMessage:
    def __init__(self, text, bot=None):
        self.text = text
        self.from_user = type("U", (), {"id": 501})()
        self.chat = type("C", (), {"id": 501})()
        self.bot = bot or FakeBot()
        self.sent = []

    async def answer(self, text, reply_markup=None):
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return FakeSent(len(self.sent) + 100)


class FakeState:
    async def set_state(self, *_args, **_kwargs):
        return None


class FakeCallback:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.from_user = type("U", (), {"id": 501})()
        self.answered = 0

    async def answer(self, *_args, **_kwargs):
        self.answered += 1


def test_request_mode_creates_two_cards_and_updates_by_edit(monkeypatch):
    handlers._REQUEST_MODE_MEM.clear()

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_args, **_kwargs):
        return type("User", (), {"id": 42})()

    async def fake_resolve(*_args, **_kwargs):
        return 42

    async def fake_route_message(_text):
        return {"actions": [{"type": "ADD_ITEM", "query_core": "спандбонд 70 белый", "qty": 1, "unit": "шт"}]}

    async def fake_pipeline(_session, **_kwargs):
        return {
            "results": [],
            "decision": {
                "clarification": {
                    "question": "Уточни товар:",
                    "options": [{"id": "opt_1", "label": "Спанбонд 70 белый", "apply": {"append_tokens": ["белый"]}}],
                    "offset": 0,
                    "next_offset": 10,
                    "total": 11,
                }
            },
        }

    monkeypatch.setattr(handlers, "_redis_client", lambda: None)
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "_resolve_org_for_user", fake_resolve)
    monkeypatch.setattr(handlers, "route_message", fake_route_message)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_pipeline)

    bot = FakeBot()
    msg_start = FakeMessage("Отправить заявку", bot=bot)
    asyncio.run(handlers.request_mode_start(msg_start, FakeState()))

    state = handlers._REQUEST_MODE_MEM[501]
    assert isinstance(state.get("results_msg_id"), int)
    assert isinstance(state.get("control_msg_id"), int)

    before_send_count = len(msg_start.sent)
    msg_text = FakeMessage("спандбонд 70 белый 3 шт", bot=bot)
    asyncio.run(handlers.request_mode_text(msg_text, FakeState()))

    assert len(msg_text.sent) == 0
    assert len(bot.edits) >= 2
    assert len(msg_start.sent) == before_send_count


def test_request_mode_position_and_clarify_paging_edit_only(monkeypatch):
    handlers._REQUEST_MODE_MEM.clear()
    captured = {"offset": None}

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_user(*_args, **_kwargs):
        return type("User", (), {"id": 42})()

    async def fake_resolve(*_args, **_kwargs):
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
                    "prev_offset": 0,
                    "next_offset": 20,
                    "total": 30,
                }
            },
        }

    monkeypatch.setattr(handlers, "_redis_client", lambda: None)
    monkeypatch.setattr(handlers, "get_session_context", fake_session_ctx)
    monkeypatch.setattr(handlers, "get_user_by_tg_id", fake_get_user)
    monkeypatch.setattr(handlers, "_resolve_org_for_user", fake_resolve)
    monkeypatch.setattr(handlers, "run_search_pipeline", fake_pipeline)

    handlers._REQUEST_MODE_MEM[501] = {
        "control_msg_id": 101,
        "results_msg_id": 100,
        "org_id_effective": 42,
        "selected_order_id": None,
        "last_request_text": "",
        "items": [
            {
                "raw": "нитки белые",
                "status": "needs_clarification",
                "clarification": {
                    "question": "Уточни товар:",
                    "options": [{"id": "opt_1", "label": "Вариант 1", "apply": {"append_tokens": ["1"]}}],
                    "offset": 0,
                    "next_offset": 10,
                    "total": 30,
                },
            }
        ],
        "selected_item_index": 0,
        "items_page_offset": 0,
        "clarify_page_offset": 0,
        "clarify_expanded": True,
        "expanded": True,
        "mode": "review",
        "status": "Статус: ✅ Готово",
        "questions": [],
        "orders_offset": 0,
        "orders_page": [],
    }

    bot = FakeBot()
    message = FakeMessage("", bot=bot)

    asyncio.run(handlers.request_mode_callback(FakeCallback("rm:item:0", message)))
    assert handlers._REQUEST_MODE_MEM[501]["selected_item_index"] == 0

    asyncio.run(handlers.request_mode_callback(FakeCallback("rm:clarify:next:10", message)))
    assert captured["offset"] == 10
    assert bot.edits
