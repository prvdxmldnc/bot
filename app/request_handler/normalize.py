from __future__ import annotations

import re

_SIZE_X_RE = re.compile(r"(\d)\s*[xх*]\s*(\d)", re.IGNORECASE)
_SIZE_NA_RE = re.compile(r"(\d)\s+на\s+(\d)", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    normalized = text.lower()
    normalized = _SIZE_X_RE.sub(r"\1x\2", normalized)
    normalized = _SIZE_NA_RE.sub(r"\1x\2", normalized)
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    return normalized
