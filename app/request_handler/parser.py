from __future__ import annotations

import re

from app.request_handler.types import Item, NeedClarification

_SPLIT_RE = re.compile(r"[;,]+|\s+и\s+", re.IGNORECASE)
_QTY_THOUSAND_RE = re.compile(r"(?P<qty>\d+)\s*т\.?\s*шт\b", re.IGNORECASE)
_QTY_UNIT_RE = re.compile(
    r"(?P<qty>\d+)\s*(?P<unit>шт|штук|кг|уп|м|кор|коробка|коробки|коробку)\b",
    re.IGNORECASE,
)
_PACK_RE = re.compile(r"по\s*(?P<qty>\d+)\s*(?P<unit>шт|штук)\b", re.IGNORECASE)


def _normalize_unit(unit: str) -> str:
    unit = unit.lower()
    if unit in {"штук", "шт"}:
        return "шт"
    if unit.startswith("короб"):
        return "кор"
    return unit


def _extract_qty_unit(text: str) -> tuple[int | None, str | None, str]:
    match = _QTY_THOUSAND_RE.search(text)
    if match:
        qty = int(match.group("qty")) * 1000
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        return qty, "шт", cleaned
    match = _QTY_UNIT_RE.search(text)
    if match:
        qty = int(match.group("qty"))
        unit = _normalize_unit(match.group("unit"))
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        return qty, unit, cleaned
    return None, None, text


def _extract_pack(text: str) -> tuple[int | None, str | None, str]:
    match = _PACK_RE.search(text)
    if not match:
        return None, None, text
    qty = int(match.group("qty"))
    unit = _normalize_unit(match.group("unit"))
    cleaned = (text[: match.start()] + text[match.end() :]).strip()
    return qty, unit, cleaned


def parse_items(text: str) -> tuple[list[Item], list[NeedClarification]]:
    items: list[Item] = []
    clarifications: list[NeedClarification] = []
    for part in _SPLIT_RE.split(text):
        raw = part.strip(" .:-")
        if not raw:
            continue
        qty, unit, cleaned = _extract_qty_unit(raw)
        pack_qty, pack_unit, cleaned = _extract_pack(cleaned)
        name = cleaned.strip(" .:-")
        if name in {"по"}:
            name = ""
        attrs: dict[str, object] = {}
        if pack_qty:
            attrs["pack_qty"] = pack_qty
            attrs["pack_unit"] = pack_unit
        if not name and (qty is not None or pack_qty is not None):
            clarifications.append(NeedClarification.MISSING_ITEM)
            continue
        if name:
            items.append(Item(name=name, qty=qty, unit=unit, attrs=attrs))
    if not items:
        clarifications.append(NeedClarification.MISSING_ITEM)
    return items, clarifications
