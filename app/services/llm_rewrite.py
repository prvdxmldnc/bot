from __future__ import annotations

import logging
import re

from app.services.llm_client import chat

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)


async def rewrite_query(text: str) -> str:
    prompt = (
        "Перепиши пользовательский запрос в короткий поисковый запрос для товарного каталога. "
        "Верни только одну строку без пояснений, 2-6 слов, без знаков препинания. "
        "Убери мусор и вводные слова (мне нужно, пожалуйста, универсальные, по кор, наличие). "
        "Сохрани критические токены: название товара, модель/серия, размеры, числа (например 70, 5, 308, ll70)."
    )
    try:
        raw = await chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
        )
    except Exception:
        logger.exception("LLM rewrite failed")
        return text
    tokens = [token.lower() for token in _TOKEN_RE.findall(raw)]
    if not tokens:
        return text
    return " ".join(tokens[:6])
