from __future__ import annotations

from app.request_handler.intent import detect_intents
from app.request_handler.normalize import normalize_text
from app.request_handler.parser import parse_items
from app.request_handler.state import resolve_state
from app.request_handler.types import ContextUpdates, DialogContext, HandlerResult, Item


def handle_message(text: str, context: DialogContext | None = None) -> HandlerResult:
    ctx = context or DialogContext()
    normalized = normalize_text(text)
    intents = detect_intents(normalized)
    items, clarifications = parse_items(normalized, bool(ctx.last_items))
    result = HandlerResult(
        intents=intents,
        state="S0_IDLE",
        items=items,
        need_clarification=clarifications,
        context_updates=ContextUpdates(
            last_items=[{"raw": item.raw, "normalized": item.normalized} for item in items],
            topic=_derive_topic(intents),
        ),
    )
    result.state = resolve_state(result)
    return result


def _derive_topic(intents: list) -> str:
    if not intents:
        return "unknown"
    name = intents[0].name
    if name.startswith("order") or name.startswith("draft"):
        return "order"
    if name.startswith("stock"):
        return "stock"
    if name == "product.match":
        return "match"
    return "unknown"


__all__ = ["handle_message"]
