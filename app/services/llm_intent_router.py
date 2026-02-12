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
_LATIN_RE = re.compile(r"[A-Za-z]")
_COMMAND_RE = re.compile(
    r"\b(добавь(?:те)?|добавить|нужно|надо|положи|закажи|в\s+заказ|пожалуйста|мне\s+нужно|кстати|что\s+там|по\s+поводу)\b",
    re.IGNORECASE,
)

_UNIT_MAP = {
    "мотка": "моток",
    "мотков": "моток",
    "моток": "моток",
    "штук": "шт",
    "шт": "шт",
    "рулона": "рулон",
    "рулонов": "рулон",
    "рулон": "рулон",
    "упаковка": "упаковка",
    "упаковки": "упаковка",
    "коробочки": "коробка",
    "коробка": "коробка",
    "коробки": "коробка",
    "пачка": "пачка",
    "пачки": "пачка",
    "кг": "кг",
}

_NOISE_PHRASES = [
    "что там",
    "по поводу",
    "и кстати",
    "а также",
    "пожалуйста",
    "мне нужно",
    "в заказ",
]

_ETA_SUBJECT_KEYS = [
    ("поролон", "поролон"),
    ("ппу", "ппу"),
    ("синтепон", "синтепон"),
    ("спанбонд", "спанбонд"),
]


class Action(BaseModel):
    type: Literal["ADD_ITEM", "ASK_STOCK_ETA", "MANAGER", "UNKNOWN"]
    query_core: str | None = None
    subject: str | None = None
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

    if not re.search(r"\b(добавь(?:те)?|добавить|нужно|надо|положи|закажи|в\s+заказ)\b", cleaned, flags=re.IGNORECASE):
        return None

    work = _ADD_PREFIX_RE.sub("", cleaned)
    work = _ADD_SPLIT_RE.split(work)[0].strip()
    for phrase in _NOISE_PHRASES:
        work = re.sub(rf"\b{re.escape(phrase)}\b", " ", work, flags=re.IGNORECASE)

    match = _QTY_UNIT_RE.search(work)
    qty: float | None = None
    unit: str | None = None
    if match:
        qty = float(match.group("qty"))
        unit_raw = match.group("unit").lower()
        unit = _UNIT_MAP.get(unit_raw, unit_raw)
        work = (work[: match.start()] + " " + work[match.end() :]).strip()

    work = _COMMAND_RE.sub(" ", work)
    work = re.sub(r"\s+", " ", work).strip(" ,.-")
    if not work:
        return None

    return Action(type="ADD_ITEM", query_core=work, qty=qty or 1.0, unit=unit)


def _extract_eta_subject(text: str) -> str | None:
    lower = (text or "").lower()
    for needle, subject in _ETA_SUBJECT_KEYS:
        if needle in lower:
            return subject
    return None


def _ensure_stock_eta_action(text: str, actions: list[Action]) -> list[Action]:
    has_eta = any(action.type == "ASK_STOCK_ETA" for action in actions)
    if has_eta:
        return actions
    if not _ETA_HINT_RE.search(text or ""):
        return actions
    subject = _extract_eta_subject(text)
    if subject:
        actions.append(Action(type="ASK_STOCK_ETA", query_core=subject, subject=subject))
    return actions


def _sanitize_action_language(actions: list[Action]) -> list[Action]:
    cleaned: list[Action] = []
    dropped_non_ru = False
    for action in actions:
        if action.type == "ADD_ITEM":
            query = (action.query_core or "").strip()
            if _LATIN_RE.search(query):
                dropped_non_ru = True
                continue
            action.query_core = query
        if action.type == "ASK_STOCK_ETA":
            subject = (action.subject or action.query_core or "").strip()
            if _LATIN_RE.search(subject):
                dropped_non_ru = True
                continue
            action.subject = subject or None
            if not action.query_core:
                action.query_core = action.subject
        cleaned.append(action)

    if dropped_non_ru and not cleaned:
        return [Action(type="UNKNOWN", query_core="Уточните запрос по-русски")]
    return cleaned


def _fallback_actions(text: str) -> RouterResult:
    if _LATIN_RE.search(text or "") and not re.search(r"[а-яё]", (text or "").lower()):
        return RouterResult(actions=[Action(type="UNKNOWN", query_core="Уточните запрос по-русски")])

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
                for action in result.actions:
                    if action.type == "ASK_STOCK_ETA" and not action.subject:
                        action.subject = action.query_core or _extract_eta_subject(text)
                        if not action.query_core:
                            action.query_core = action.subject
                result.actions = _sanitize_action_language(result.actions)
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
    heuristic_actions = parse_actions_from_text(text)
    has_meaningful = any(action.type in {"ADD_ITEM", "ASK_STOCK_ETA", "MANAGER"} for action in heuristic_actions.actions)
    if has_meaningful or not llm_available():
        return heuristic_actions.model_dump()

    system_prompt = (
        "Ты роутер намерений для B2B заказов. Верни ТОЛЬКО JSON без пояснений. "
        "Допустимы 2 формата: массив действий или объект {\"actions\":[...]}. "
        "Каждое действие: {\"type\":\"ADD_ITEM|ASK_STOCK_ETA|MANAGER|UNKNOWN\",\"query_core\":\"...\",\"subject\":\"...\",\"qty\":number,\"unit\":\"...\"}. "
        "Если есть и добавление товара, и вопрос о сроке — верни оба действия."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        content = await llm_chat(messages, temperature=0.1)
        parsed = parse_actions_from_text(text, content)
        parsed.actions = _sanitize_action_language(parsed.actions)
        if not parsed.actions:
            parsed = RouterResult(actions=[Action(type="UNKNOWN", query_core="Уточните запрос по-русски")])
        return parsed.model_dump()
    except Exception:
        logger.info("Intent router fallback activated", exc_info=True)
        return parse_actions_from_text(text).model_dump()


async def get_stock_eta(query_core: str) -> str:
    query_core = re.sub(r"\s+", " ", (query_core or "").strip())
    if not query_core:
        return "Уточню срок поставки и вернусь с ответом."
    return f"По {query_core} уточню срок поставки. Уточни, какой именно {query_core}: марка/толщина/артикул."
