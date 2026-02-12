from app.services.order_parser import parse_order_text


def test_propagate_head_for_ellipsis_color_item():
    items = parse_order_text("Молния серая, беж по 5 шт")
    assert len(items) == 2
    assert items[0]["query_core"] == "молния серая"
    assert items[1]["query_core"] == "молния беж"
    assert items[1]["qty"] == 5
    assert items[1]["unit"] == "шт"


def test_single_item_query_core_unchanged():
    items = parse_order_text("болт 8x30 дин 933 10шт")
    assert len(items) == 1
    assert items[0]["query_core"] == "болт 8x30 дин 933"
    assert items[0]["query"] == "болт 8x30 дин 933"
