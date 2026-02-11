from app.services.order_parser import parse_order_text


def test_propagate_head_for_ellipsis_color_item():
    items = parse_order_text("Молния серая, беж по 5 шт")
    assert len(items) == 2
    assert items[0]["query_core"].startswith("молния")
    assert "молния" in items[1]["query_core"]
    assert "беж" in items[1]["query_core"]
