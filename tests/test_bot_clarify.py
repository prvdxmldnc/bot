from app.bot.handlers import _apply_clarification_tokens


def test_apply_clarification_tokens_appends_tokens():
    query = _apply_clarification_tokens("нитки белые", ["70", "лл"])
    assert query == "нитки белые 70 лл"
