from app.bot.handlers import _shorten_title


def test_shorten_title_limits_length() -> None:
    title = "Болт мебельный 8 * 30 (din 603) оцинкованный"
    shortened = _shorten_title(title, max_len=20)
    assert shortened
    assert len(shortened) <= 20
