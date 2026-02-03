from __future__ import annotations

import pytest

from app.request_handler import handle_message


@pytest.mark.parametrize(
    "text,expected_qty,expected_unit",
    [
        ("Болт м6х20-6кг", 6.0, "кг"),
        ("Саморез 4х25 -4т.шт жёлтый  добавьте пожалуйста", 4000.0, "шт"),
        ("Механизм 236 : 1 кор и к нему пружинны 20 штук.", 1.0, "кор"),
    ],
)
def test_qty_unit_parsing(text, expected_qty, expected_unit):
    result = handle_message(text)
    assert result.items
    assert result.items[0].qty == expected_qty
    assert result.items[0].unit == expected_unit


def test_thousand_pieces_color_and_size():
    result = handle_message("Саморез 4х25 -4т.шт жёлтый  добавьте пожалуйста")
    assert result.items[0].attributes.color in {"жёлтый", "желтый"}
    assert result.items[0].attributes.size == "4x25"


def test_greeting_prefix_removed_and_size_detected():
    result = handle_message("Здравствуйте,  ортопед основание 200х120-2(1733) ...")
    assert result.items[0].attributes.size == "200x120"
    assert result.items[0].attributes.code == "1733"


def test_patch_only_box_requires_target():
    result = handle_message("1 кор")
    assert result.items[0].normalized == "__PATCH__"
    assert result.need_clarification


def test_patch_only_pack_requires_target():
    result = handle_message("По 10 шт")
    assert result.items[0].normalized == "__PATCH__"
    assert result.need_clarification


def test_two_items_split():
    result = handle_message("Механизм 236 : 1 кор и к нему пружинны 20 штук.")
    assert len(result.items) >= 2
    assert result.items[1].qty == 20
    assert result.items[1].unit == "шт"


def test_stock_question():
    result = handle_message("Стежка грей. Есть у вас?")
    assert result.intents[0].name == "stock.check"
    assert result.state == "S1_INTAKE"


def test_eta_question():
    result = handle_message("120 ка 65 или63 когда будет")
    assert result.intents[0].name == "stock.forecast"
    assert result.state == "S1_INTAKE"


def test_order_list_text():
    result = handle_message("В заказ: Тик матрасный и спндбонд белый 30")
    assert result.intents[0].name in {"order.add", "order.create"}
    assert result.items


def test_din_and_pack_request():
    result = handle_message("Гайка забивная М 6/9 (din1624) упаковку")
    assert result.items[0].attributes.din == "1624"


def test_inquiry_general():
    result = handle_message("Ткань капучино ( как дива 05) но подешевле")
    assert result.intents[0].name == "inquiry.general"


def test_size_normalization_in_word():
    result = handle_message("механизм подъема 8х30")
    assert any(item.attributes.size == "8x30" for item in result.items)


def test_smalltalk_idle():
    result = handle_message("Привет, спасибо")
    assert result.intents[0].name == "smalltalk"
    assert result.state == "S0_IDLE"


def test_draft_confirm():
    result = handle_message("Подтверждаю заказ")
    assert result.intents[0].name == "draft.confirm"


def test_draft_cancel():
    result = handle_message("Отменить заказ")
    assert result.intents[0].name == "draft.cancel"


def test_product_match_default_low_confidence():
    result = handle_message("Поролон 10мм")
    assert result.intents[0].name == "product.match"


def test_order_change_qty_confidence():
    result = handle_message("40 шт")
    assert result.intents[0].name == "order.change_qty"


def test_order_remove():
    result = handle_message("Уберите позицию")
    assert result.intents[0].name == "order.remove"


def test_stock_reserve():
    result = handle_message("Поставьте в резерв")
    assert result.intents[0].name == "stock.reserve"


def test_product_match_keyword():
    result = handle_message("Подберите аналог")
    assert result.intents[0].name == "product.match"


def test_order_bulk_keyword():
    result = handle_message("Список позиций оптом")
    assert result.intents[0].name == "order.bulk"


def test_stock_check_keyword():
    result = handle_message("Наличие есть?")
    assert result.intents[0].name == "stock.check"


def test_state_for_order_with_qty_unit():
    result = handle_message("Саморез 4х25 10шт")
    assert result.state == "S5_DRAFT"


def test_state_for_order_missing_qty():
    result = handle_message("Болт м6х20")
    assert result.state in {"S2_CLARIFY", "S7_HANDOFF"}


def test_context_updates():
    result = handle_message("Саморез 4х25 10шт")
    assert result.context_updates.last_items
    assert result.context_updates.topic in {"order", "match", "unknown"}
