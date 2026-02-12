from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.llm_client import llm_available, chat as llm_chat
from app.services.order_parser import parse_order_text

logger = logging.getLogger(__name__)


class Action(BaseModel):
    type: Literal["ADD_ITEM", "ASK_STOCK_ETA", "MANAGER", "UNKNOWN"]
    query_core: str | None = None
    qty: float | None = None
    unit: str | None = None


class RouterResult(BaseModel):
    actions: list[Action] = Field(default_factory=list)


def _extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _fallback_actions(text: str) -> RouterResult:
    parsed = parse_order_text(text)
    actions: list[Action] = []
    for item in parsed:
        query_core = (item.get("query_core") or item.get("query") or "").strip()
        if not query_core:
            continue
        actions.append(
            Action(
                type="ADD_ITEM",
                query_core=query_core,
                qty=float(item.get("qty", 1) or 1),
                unit=(item.get("unit") or None),
            )
        )
    if not actions:
        actions.append(Action(type="UNKNOWN"))
    return RouterResult(actions=actions)


async def route_message(text: str) -> dict:
    if not llm_available():
        return _fallback_actions(text).model_dump()

    system_prompt = (
        "Классифицируй сообщение клиента в JSON. "
        "Верни ТОЛЬКО JSON формата "
        '{"actions":[{"type":"ADD_ITEM|ASK_STOCK_ETA|MANAGER|UNKNOWN","query_core":"...","qty":1,"unit":"шт"}]}. '
        "Если есть заказ и вопрос о сроках, верни несколько действий. "
        "query_core короткий, без вежливых слов и мусора."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        content = await llm_chat(messages, temperature=0.1)
        payload = _extract_json_object(content or "")
        if payload is None:
            return _fallback_actions(text).model_dump()
        result = RouterResult.model_validate(payload)
        if not result.actions:
            return _fallback_actions(text).model_dump()
        return result.model_dump()
    except (ValidationError, RuntimeError, Exception):
        logger.info("Intent router fallback activated", exc_info=True)
        return _fallback_actions(text).model_dump()


async def get_stock_eta(query_core: str) -> str:
    query_core = re.sub(r"\s+", " ", (query_core or "").strip())
    if not query_core:
        return "Уточню срок поставки и вернусь с ответом."
    return f"По позиции '{query_core}' проверяем срок поставки. Скоро уточним дату."
