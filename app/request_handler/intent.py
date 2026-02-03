from __future__ import annotations

from collections import defaultdict

from app.request_handler.types import Intent


def _score_keywords(text: str) -> dict[Intent, float]:
    scores: dict[Intent, float] = defaultdict(float)
    lowered = text.lower()
    if any(word in lowered for word in ["заказ", "добавьте", "добавить", "нужно", "нужны"]):
        scores[Intent.ORDER_ADD] += 1.0
    if any(word in lowered for word in ["изменить", "заменить", "убрать", "исправить"]):
        scores[Intent.ORDER_UPDATE] += 1.0
    if any(word in lowered for word in ["есть", "в наличии", "наличие", "остаток"]):
        scores[Intent.STOCK_CHECK] += 1.0
    if any(word in lowered for word in ["когда", "срок", "будет", "ожидается"]):
        scores[Intent.STOCK_ETA] += 1.0
    if any(word in lowered for word in ["подберите", "аналог", "подходит", "подобрать"]):
        scores[Intent.PRODUCT_MATCH] += 1.0
    if any(word in lowered for word in ["как", "какой", "что", "где", "почему"]):
        scores[Intent.INQUIRY_GENERAL] += 0.5
    if any(word in lowered for word in ["привет", "спасибо", "добрый", "хорошего"]):
        scores[Intent.SMALLTALK] += 1.0
    return scores


def detect_intent(text: str) -> tuple[Intent, float]:
    scores = _score_keywords(text)
    if not scores:
        return Intent.PRODUCT_MATCH, 0.2
    intent = max(scores, key=scores.get)
    return intent, min(scores[intent], 1.0)
