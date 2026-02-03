from __future__ import annotations

import json
import logging

from app.services.llm_gigachat import chat

logger = logging.getLogger(__name__)


async def suggest_queries(user_text: str) -> list[str]:
    prompt = (
        "Ты нормализуешь запросы для поиска по каталогу. "
        "Ответь строго JSON в формате "
        '{"alternatives":["...","...","..."],"notes":"..."}.\n'
        "Правила:\n"
        "- alternatives: 3-5 строк, максимум 60 символов каждая.\n"
        "- Убери количества и единицы (10шт, 2рол, 1коробка).\n"
        "- Преобразуй разговорные формы в нормальные термины.\n"
        "- Числа и размеры сохраняй.\n"
        "- Без лишнего текста вне JSON."
    )
    try:
        response = await chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.2,
        )
    except Exception:
        logger.exception("LLM normalize failed")
        return []
    content = response["choices"][0]["message"]["content"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    alternatives = data.get("alternatives") if isinstance(data, dict) else None
    if not isinstance(alternatives, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in alternatives:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        if len(value) > 60:
            value = value[:60].rstrip()
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
        if len(cleaned) >= 5:
            break
    return cleaned
