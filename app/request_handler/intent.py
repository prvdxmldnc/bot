from __future__ import annotations

from collections import defaultdict
import re

from app.request_handler.types import IntentCandidate


INTENT_KEYWORDS = {
    "order.create": ["заказ", "оформить", "сделать заказ"],
    "order.add": ["добавьте", "добавить", "в заказ", "нужно", "нужны"],
    "order.bulk": ["список", "перечень", "оптом"],
    "order.change_qty": ["по ", "шт", "кор", "уп"],
    "order.remove": ["убрать", "уберите", "удалить", "исключить"],
    "stock.check": ["есть", "в наличии", "наличие", "остаток"],
    "stock.forecast": ["когда", "срок", "будет", "ожидается"],
    "stock.reserve": ["резерв", "забронировать"],
    "product.match": ["подберите", "аналог", "подходит", "подобрать"],
    "draft.show": ["черновик", "показать заказ"],
    "draft.confirm": ["подтвердить", "подтверждаю"],
    "draft.cancel": ["отменить заказ", "отмена заказа", "отменить"],
    "inquiry.general": ["как", "какой", "что", "где", "почему"],
    "smalltalk": ["привет", "спасибо", "добрый", "хорошего"],
}
INTENT_WEIGHTS = {
    "draft.confirm": 0.8,
    "draft.cancel": 0.8,
    "stock.check": 0.6,
    "stock.forecast": 0.6,
    "stock.reserve": 0.6,
    "order.remove": 0.6,
}
_QTY_ONLY_RE = re.compile(r"\bпо\s*\d+\s*(шт|штук)\b|\b\d+\s*(шт|штук|кг|уп|кор|м|пог\.м)\b", re.IGNORECASE)


def detect_intents(text: str) -> list[IntentCandidate]:
    scores: dict[str, float] = defaultdict(float)
    lowered = text.lower()
    if _QTY_ONLY_RE.search(lowered):
        scores["order.change_qty"] += 0.7
    for intent, keywords in INTENT_KEYWORDS.items():
        weight = INTENT_WEIGHTS.get(intent, 0.4)
        for keyword in keywords:
            if keyword in lowered:
                scores[intent] += weight
    if not scores:
        scores["product.match"] = 0.2
    intents = [IntentCandidate(name=name, confidence=min(score, 1.0)) for name, score in scores.items()]
    intents.sort(key=lambda item: item.confidence, reverse=True)
    return intents
