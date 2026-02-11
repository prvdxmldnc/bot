from __future__ import annotations

import re

from app.request_handler.types import Item, ItemAttributes, NeedClarification

_SPLIT_RE = re.compile(r"[;]+|\s+и\s+", re.IGNORECASE)
_QTY_THOUSAND_RE = re.compile(r"(?P<qty>\d+)\s*т\.?\s*шт\b", re.IGNORECASE)
_QTY_UNIT_RE = re.compile(
    r"(?P<qty>\d+(?:[\.,]\d+)?)\s*(?P<unit>шт|штук|кг|упак|уп|упаковку|м|пог\.м|кор|коробка|коробки|коробку|рул|рол|рулон|комп|компл|комплект)\b",
    re.IGNORECASE,
)
_PACK_RE = re.compile(r"по\s*(?P<qty>\d+)\s*(?P<unit>шт|штук)\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"\b\d+\s*x\s*\d+\b", re.IGNORECASE)
_CODE_RE = re.compile(r"(?:#|\()(?P<code>\d{3,5})\)?")
_DIN_RE = re.compile(r"din\s*(?P<din>\d{3,4})", re.IGNORECASE)
_COLOR_RE = re.compile(r"\b(беж|серый|черный|чёрный|желтый|жёлтый|капучино|грей)\b", re.IGNORECASE)


def _normalize_unit(raw: str) -> str | None:
    unit = raw.lower().strip()
    if unit in {"шт", "штук"}:
        return "шт"
    if unit in {"упак", "уп", "упаковку"}:
        return "уп"
    if unit.startswith("кор"):
        return "кор"
    if unit in {"рул", "рол", "рулон"}:
        return "рулон"
    if unit in {"комп", "компл", "комплект"}:
        return "комплект"
    if unit == "пог.м":
        return "пог.м"
    if unit == "м":
        return "м"
    if unit == "кг":
        return "кг"
    return None


def _extract_qty_unit(text: str) -> tuple[float | None, str | None, str]:
    match = _QTY_THOUSAND_RE.search(text)
    if match:
        qty = float(match.group("qty")) * 1000
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        return qty, "шт", cleaned
    match = _QTY_UNIT_RE.search(text)
    if match:
        qty = float(match.group("qty").replace(",", "."))
        unit = _normalize_unit(match.group("unit"))
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        return qty, unit, cleaned
    return None, None, text


def _extract_pack(text: str) -> tuple[float | None, str | None, str]:
    match = _PACK_RE.search(text)
    if not match:
        return None, None, text
    qty = float(match.group("qty"))
    unit = _normalize_unit(match.group("unit"))
    cleaned = (text[: match.start()] + text[match.end() :]).strip()
    return qty, unit, cleaned


def _extract_attributes(text: str) -> ItemAttributes:
    size_match = _SIZE_RE.search(text)
    code_match = _CODE_RE.search(text)
    din_match = _DIN_RE.search(text)
    color_match = _COLOR_RE.search(text)
    return ItemAttributes(
        size=size_match.group(0).replace(" ", "") if size_match else None,
        color=color_match.group(0) if color_match else None,
        code=code_match.group("code") if code_match else None,
        din=din_match.group("din") if din_match else None,
        notes=None,
    )


def parse_items(text: str, has_context: bool) -> tuple[list[Item], list[NeedClarification]]:
    items: list[Item] = []
    clarifications: list[NeedClarification] = []
    raw_parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    for part in raw_parts:
        qty, unit, cleaned = _extract_qty_unit(part)
        pack_qty, pack_unit, cleaned = _extract_pack(cleaned)
        attributes = _extract_attributes(cleaned)
        name = cleaned.strip(" .:-")
        if pack_qty:
            attributes.notes = "pack_qty"
        if name in {"по"}:
            name = ""
        if not name and (qty is not None or pack_qty is not None):
            if not has_context:
                clarifications.append(NeedClarification(field="target_item", reason="no previous item"))
            items.append(
                Item(
                    raw=part,
                    normalized="__PATCH__",
                    qty=qty or pack_qty,
                    unit=unit or pack_unit,
                    attributes=ItemAttributes(notes="apply_to_last_item"),
                    confidence=0.4,
                )
            )
            continue
        if name:
            items.append(
                Item(
                    raw=part,
                    normalized=name,
                    qty=qty,
                    unit=unit,
                    attributes=attributes,
                    confidence=0.6,
                )
            )
    if not items:
        clarifications.append(NeedClarification(field="item", reason="no items"))
    return items, clarifications
