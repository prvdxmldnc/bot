from __future__ import annotations

from app.request_handler.intent import detect_intent
from app.request_handler.normalize import normalize_text
from app.request_handler.parser import parse_items
from app.request_handler.state import resolve_state
from app.request_handler.types import DialogContext, HandlerResult


def handle_message(text: str, context: DialogContext | None = None) -> HandlerResult:
    normalized = normalize_text(text)
    intent, confidence = detect_intent(normalized)
    items, clarifications = parse_items(normalized)
    state = resolve_state(intent, len(items), clarifications)
    return HandlerResult(
        text=text,
        intent=intent,
        state=state,
        items=items,
        need_clarification=clarifications,
        confidence=confidence,
    )


__all__ = ["handle_message"]
