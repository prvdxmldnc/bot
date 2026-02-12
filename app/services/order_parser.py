from __future__ import annotations

import re
from typing import Any

_SPLIT_RE = re.compile(r"[\n;,]+")
_QTY_UNIT_RE = re.compile(r"(?P<qty>\d+)\s*(?P<unit>шт|кг|уп|м)\b", re.IGNORECASE)
_QTY_THOUSAND_RE = re.compile(r"(?P<qty>\d+)\s*т\.?\s*шт\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+")
_SIZE_X_RE = re.compile(r"(\d)\s*[xх*]\s*(\d)", re.IGNORECASE)

_STOP_HEAD_WORDS = {"по", "и", "для", "на", "в", "с", "без", "шт", "уп", "кг", "м", "мм", "см", "кор", "короб", "рул"}
_COLOR_WORDS = {"беж", "бежев", "бел", "белый", "сер", "серый", "серая", "черн", "черный", "син", "зел"}

_QUERY_SERVICE_TOKENS = {"по", "и", "для", "на", "в", "с"}


def _to_query_core(cleaned: str) -> str:
    tokens = re.findall(r"[a-zа-я0-9x]+", cleaned)
    while tokens and tokens[-1] in _QUERY_SERVICE_TOKENS:
        tokens.pop()
    return " ".join(tokens).strip()


def _head_token(query: str) -> str | None:
    tokens = re.findall(r"[a-zа-я0-9]+", query)
    candidates = [
        token
        for token in tokens
        if not token.isdigit() and token not in _STOP_HEAD_WORDS and token not in _COLOR_WORDS and len(token) >= 4
    ]
    if not candidates:
        return None
    return sorted(candidates, key=len, reverse=True)[0]


def propagate_head(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev_head: str | None = None
    for item in items:
        query = (item.get("query") or "").strip()
        head = _head_token(query)
        if head:
            prev_head = head
            item["query_core"] = _to_query_core(query) or query
            continue
        if prev_head and query:
            item["query"] = f"{prev_head} {query}".strip()
            item["query_core"] = _to_query_core(item["query"]) or item["query"]
        else:
            item["query_core"] = _to_query_core(query) or query
    return items


def _normalize(text: str) -> str:
    normalized = text.lower()
    normalized = normalized.replace(" на ", " x ")
    normalized = _SIZE_X_RE.sub(r"\1x\2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalization_examples() -> list[tuple[str, str]]:
    return [
        ("механизм подъёма", "механизм подъёма"),
        ("8х30", "8x30"),
        ("1010 x 40", "1010x40"),
    ]


def _extract_qty_unit(text: str) -> tuple[int, str, str]:
    match = _QTY_THOUSAND_RE.search(text)
    if match:
        qty = int(match.group("qty")) * 1000
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        return qty, "шт", cleaned
    match = _QTY_UNIT_RE.search(text)
    if not match:
        return 1, "", text
    qty = int(match.group("qty"))
    unit = match.group("unit").lower()
    cleaned = (text[: match.start()] + text[match.end() :]).strip()
    return qty, unit, cleaned


def parse_order_text(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in _SPLIT_RE.split(text):
        raw = part.strip()
        if not raw:
            continue
        normalized = _normalize(raw)
        qty, unit, cleaned = _extract_qty_unit(normalized)
        numbers = [int(n) for n in _NUM_RE.findall(cleaned)]
        if unit:
            numbers = [n for n in numbers if n != qty]
        query = _to_query_core(cleaned) or cleaned.strip()
        items.append(
            {
                "raw": raw,
                "qty": qty,
                "unit": unit,
                "normalized": normalized,
                "numbers": numbers,
                "query": query,
            }
        )
    return propagate_head(items)


if __name__ == "__main__":
    for raw, expected in _normalization_examples():
        got = _normalize(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"
    examples = [
        ("гайка ус 6мм-2т.шт", 2000, "шт", {6}, {2}),
        ("саморез 4х25 -4т.шт жёлтый", 4000, "шт", {4, 25}, set()),
        ("болт 8*30 дин 933 10шт", 10, "шт", {8, 30, 933}, {10}),
    ]
    for raw, qty_expected, unit_expected, must_have, must_not_have in examples:
        parsed = parse_order_text(raw)[0]
        assert parsed["qty"] == qty_expected, raw
        assert parsed["unit"] == unit_expected, raw
        assert must_have.issubset(set(parsed["numbers"])), raw
        assert must_not_have.isdisjoint(set(parsed["numbers"])), raw
