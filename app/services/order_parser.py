from __future__ import annotations

import re
from typing import Any

_SPLIT_RE = re.compile(r"[\n;,]+")
_QTY_UNIT_RE = re.compile(r"(?P<qty>\d+)\s*(?P<unit>шт|кг|уп|м)\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+")
_SIZE_X_RE = re.compile(r"(\d)\s*[xх*]\s*(\d)", re.IGNORECASE)


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
        query = cleaned.strip()
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
    return items


if __name__ == "__main__":
    for raw, expected in _normalization_examples():
        got = _normalize(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"
