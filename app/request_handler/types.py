from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Topic = Literal["order", "stock", "match", "unknown"]
State = Literal["S0_IDLE", "S1_INTAKE", "S2_CLARIFY", "S5_DRAFT", "S7_HANDOFF"]
Unit = Literal[
    "шт",
    "кг",
    "кор",
    "уп",
    "рулон",
    "комплект",
    "м",
    "пог.м",
    "каркас",
    "позиция",
]
ClarifyField = Literal["item", "qty", "unit", "size", "color", "target_item"]


class DialogContext(BaseModel):
    last_state: str | None = None
    last_items: list[dict[str, str]] = Field(default_factory=list)
    topic: Topic | None = None


class IntentCandidate(BaseModel):
    name: str
    confidence: float


class ItemAttributes(BaseModel):
    size: str | None = None
    color: str | None = None
    code: str | None = None
    din: str | None = None
    notes: str | None = None


class Item(BaseModel):
    raw: str
    normalized: str
    qty: float | None = None
    unit: Unit | None = None
    attributes: ItemAttributes = Field(default_factory=ItemAttributes)
    confidence: float = 0.0


class NeedClarification(BaseModel):
    field: ClarifyField
    reason: str


class ContextUpdates(BaseModel):
    last_items: list[dict[str, str]] = Field(default_factory=list)
    topic: Topic = "unknown"


class HandlerResult(BaseModel):
    intents: list[IntentCandidate]
    state: State
    items: list[Item]
    need_clarification: list[NeedClarification]
    context_updates: ContextUpdates
