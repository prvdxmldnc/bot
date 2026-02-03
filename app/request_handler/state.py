from __future__ import annotations

from app.request_handler.types import Intent, NeedClarification


def resolve_state(intent: Intent, items_count: int, clarifications: list[NeedClarification]) -> str:
    if clarifications:
        return "need_clarification"
    if intent in {Intent.STOCK_CHECK, Intent.STOCK_ETA}:
        return "stock_inquiry"
    if intent in {Intent.ORDER_ADD, Intent.ORDER_UPDATE} and items_count > 0:
        return "order_ready"
    if intent == Intent.SMALLTALK:
        return "smalltalk"
    if intent == Intent.INQUIRY_GENERAL:
        return "inquiry"
    return "idle"
