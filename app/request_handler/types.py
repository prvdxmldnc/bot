from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(str, Enum):
    ORDER_ADD = "order.add"
    ORDER_UPDATE = "order.update"
    STOCK_CHECK = "stock.check"
    STOCK_ETA = "stock.eta"
    PRODUCT_MATCH = "product.match"
    INQUIRY_GENERAL = "inquiry.general"
    SMALLTALK = "smalltalk"


class NeedClarification(str, Enum):
    MISSING_ITEM = "missing_item"
    MISSING_QTY = "missing_qty"
    AMBIGUOUS = "ambiguous"


class DialogContext(BaseModel):
    user_id: int | None = None
    channel: str = "telegram"
    locale: str = "ru"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    name: str
    qty: int | None = None
    unit: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class HandlerResult(BaseModel):
    text: str
    intent: Intent
    state: str
    items: list[Item] = Field(default_factory=list)
    need_clarification: list[NeedClarification] = Field(default_factory=list)
    confidence: float = 0.0
