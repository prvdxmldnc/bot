from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.llm_client import llm_available, chat as llm_chat
from app.services.order_parser import parse_order_text

logger = logging.getLogger(__name__)

_QTY_UNIT_RE = re.compile(
    r"(?P<qty>\d+)\s*(?P<unit>мотка|мотков|моток|шт|штук|рулон|рулона|рулонов|упаковка|упаковки|коробка|коробки|пачка|пачки)",
    re.IGNORECASE,
)
_ADD_PREFIX_RE = re.compile(
    r"^(добавь(?:те)?|мне\s+нужно|в\s+заказ|пожалуйста|нужно|надо)\s+",
    re.IGNORECASE,
)
_ADD_SPLIT_RE = re.compile(r"\b(и\s+что|и\s+кстати|а\s+также|,)\b", re.IGNORECASE)
_ETA_HINT_RE = re.compile(r"когда\s+(придет|будет|ожидается)|срок\s+поставки", re.IGNORECASE)


class Action(BaseModel):
    type: Literal["ADD_ITEM", "ASK_STOCK_ETA", "MANAGER", "UNKNOWN"]
    query_core: str | None = None
    qty: float | None = None
    unit: str | None = None


class RouterResult(BaseModel):
    actions: list[Action] = Field(default_factory=list)


def _extract_json_payload(text: str) -> dict | list | None:
    if not text:
        return None
    starts = [(text.find("["), "]"), (text.find("{"), "}")]
    starts = [item for item in starts if item[0] != -1]
    if not starts:
        return None
    starts.sort(key=lambda item: item[0])
    start, closer = starts[0]
    end = text.rfind(closer)
    if end == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


def _extract_add_item_from_text(text: str) -> Action | None:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return None
    match = _QTY_UNIT_RE.search(cleaned)
    qty: float | None = None
    unit: str | None = None
    candidate = cleaned
    if match:
        qty = float(match.group("qty"))
        unit = match.group("unit").lower()
        candidate = cleaned[match.end() :].strip()
    candidate = _ADD_PREFIX_RE.sub("", candidate)
    candidate = _ADD_SPLIT_RE.split(candidate)[0].strip()
    candidate = re.sub(r"\b(пожалуйста|добавь(?:те)?|мне\s+нужно|в\s+заказ)\b", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,.-")
    if not candidate:
        return None
    return Action(type="ADD_ITEM", query_core=candidate, qty=qty or 1.0, unit=unit)


def _ensure_stock_eta_action(text: str, actions: list[Action]) -> list[Action]:
    has_eta = any(action.type == "ASK_STOCK_ETA" for action in actions)
    if has_eta:
        return actions
    if not _ETA_HINT_RE.search(text or ""):
        return actions
    lower = (text or "").lower()
    if "поролон" in lower:
        actions.append(Action(type="ASK_STOCK_ETA", query_core="поролон"))
    return actions


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
    actions = _ensure_stock_eta_action(text, actions)
    return RouterResult(actions=actions)


def parse_actions_from_text(text: str, llm_payload: str | None = None) -> RouterResult:
    if llm_payload:
        payload = _extract_json_payload(llm_payload)
        try:
            if isinstance(payload, list):
                result = RouterResult(actions=[Action.model_validate(item) for item in payload if isinstance(item, dict)])
            elif isinstance(payload, dict):
                if isinstance(payload.get("actions"), list):
                    result = RouterResult(
                        actions=[Action.model_validate(item) for item in payload.get("actions", []) if isinstance(item, dict)]
                    )
                else:
                    result = RouterResult(actions=[Action.model_validate(payload)])
            else:
                result = RouterResult(actions=[])
            if result.actions:
                result.actions = _ensure_stock_eta_action(text, result.actions)
                return result
        except ValidationError:
            logger.info("Intent router JSON validation failed, using fallback", exc_info=True)

    add_action = _extract_add_item_from_text(text)
    if add_action:
        actions = [add_action]
        actions = _ensure_stock_eta_action(text, actions)
        return RouterResult(actions=actions)

    return _fallback_actions(text)


async def route_message(text: str) -> dict:
    if not llm_available():
        return parse_actions_from_text(text).model_dump()

    system_prompt = (
        "Ты роутер намерений для B2B заказов. Верни строго JSON и ничего больше. "
        "Допустимы 2 формата: массив действий или объект {\"actions\":[...]}. "
        "Каждое действие: {\"type\":\"ADD_ITEM|ASK_STOCK_ETA|MANAGER|UNKNOWN\",\"query_core\":\"...\",\"qty\":number,\"unit\":\"...\"}. "
        "Если есть и добавление товара, и вопрос о сроке — верни оба действия."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        content = await llm_chat(messages, temperature=0.1)
        return parse_actions_from_text(text, content).model_dump()
    except Exception:
        logger.info("Intent router fallback activated", exc_info=True)
        return parse_actions_from_text(text).model_dump()


async def get_stock_eta(query_core: str) -> str:
    query_core = re.sub(r"\s+", " ", (query_core or "").strip())
    if not query_core:
        return "Уточню срок поставки и вернусь с ответом."
    return f"По {query_core} уточню срок поставки. Уточни, какой именно {query_core}: марка/толщина/артикул."
