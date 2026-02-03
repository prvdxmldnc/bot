from __future__ import annotations

from app.request_handler import handle_message
from app.request_handler.types import Intent, NeedClarification


def test_bolt_with_weight():
    result = handle_message("Болт м6х20-6кг")
    assert result.items
    assert result.items[0].qty == 6
    assert result.items[0].unit == "кг"


def test_thousand_pieces_and_color():
    result = handle_message("Саморез 4х25 -4т.шт жёлтый добавьте пожалуйста")
    assert result.intent in {Intent.ORDER_ADD, Intent.PRODUCT_MATCH}
    assert result.items[0].qty == 4000
    assert result.items[0].unit == "шт"


def test_only_box_requires_clarification():
    result = handle_message("1 кор")
    assert NeedClarification.MISSING_ITEM in result.need_clarification


def test_pack_only_requires_clarification():
    result = handle_message("По 10 шт")
    assert NeedClarification.MISSING_ITEM in result.need_clarification


def test_two_items_split():
    result = handle_message("Механизм 236 : 1 кор и к нему пружинны 20 штук.")
    assert len(result.items) >= 2


def test_stock_question():
    result = handle_message("Стежка грей. Есть у вас?")
    assert result.intent == Intent.STOCK_CHECK


def test_eta_question():
    result = handle_message("120 ка 65 или63 когда будет")
    assert result.intent == Intent.STOCK_ETA


def test_order_list_text():
    result = handle_message("В заказ: Тик матрасный и спндбонд белый 30")
    assert result.intent in {Intent.ORDER_ADD, Intent.PRODUCT_MATCH}
    assert result.items
