from __future__ import annotations

from app.request_handler.types import HandlerResult, State


def resolve_state(result: HandlerResult, min_confidence: float = 0.45) -> State:
    if result.intents and result.intents[0].name == "smalltalk":
        return "S0_IDLE"
    intent_name = result.intents[0].name if result.intents else ""
    if intent_name.startswith("order."):
        if not result.items:
            return "S2_CLARIFY"
        if any(item.normalized == "__PATCH__" for item in result.items):
            return "S2_CLARIFY"
        for item in result.items:
            if item.qty is None or item.unit is None:
                return "S2_CLARIFY"
        return "S5_DRAFT"
    if intent_name.startswith("stock."):
        if not result.items:
            return "S2_CLARIFY"
        return "S1_INTAKE"
    if intent_name == "product.match":
        if not result.items:
            if result.intents[0].confidence < min_confidence:
                return "S0_IDLE"
            return "S2_CLARIFY"
        if result.intents and result.intents[0].confidence < min_confidence:
            return "S7_HANDOFF"
    if result.need_clarification:
        if intent_name == "product.match" and result.intents[0].confidence < min_confidence:
            return "S0_IDLE"
        return "S2_CLARIFY"
    if not intent_name or not result.items:
        if result.intents and result.intents[0].confidence < min_confidence:
            return "S7_HANDOFF"
    return "S0_IDLE"
