from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.database import get_session_context
from app.services.order_parser import parse_order_text
from app.services.search import search_products

DEFAULT_OUTPUT = Path("/tmp/search_eval.json")
QUERY_SET = [
    "болт 8x30 дин 933",
    "гайка м10",
    "шайба 12",
    "саморез 4.2x16",
    "шпилька м12х1000",
    "анкер 10x100",
    "дюбель 6x40",
    "шуруп 5x60 потай",
    "винт m6x20",
    "болт нерж м8х50",
    "шайба гровер 8",
    "шестигранник 5",
    "болт 10x20 цинк",
    "анкеры клиновые 12x120",
    "саморезы по металлу 3.5x25",
    "гвозди 100",
    "гайка 12x1.25",
    "винт din 912 m5x12",
    "шайба din 125 10",
    "болт мебельный 8x60",
    "болт 8х30",
    "болты 8x30 933",
    "шплинт 3x25",
    "шайба увеличенная 8",
    "заклепка 4x8",
    "перфолента 20x0.6",
    "саморез кровельный 5.5x32",
    "болт м16 933",
    "винт м3х8",
    "шайба плоская 6",
]


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def evaluate_search(output_path: Path = DEFAULT_OUTPUT) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    header = f"{'query':30} | {'count':5} | {'top1':40} | {'top5'}"
    print(header)
    print("-" * len(header))
    async with get_session_context() as session:
        for raw_query in QUERY_SET:
            parsed_items = parse_order_text(raw_query)
            item = parsed_items[0] if parsed_items else {}
            query = item.get("query") or item.get("raw") or ""
            candidates = await search_products(session, query, limit=5) if query else []
            top1 = candidates[0]["title_ru"] if candidates else ""
            top5 = [c["title_ru"] for c in candidates]
            report.append(
                {
                    "raw_query": raw_query,
                    "parsed_items": parsed_items,
                    "query": query,
                    "count": len(candidates),
                    "top1": top1,
                    "top5": top5,
                }
            )
            print(
                f"{_shorten(raw_query, 30):30} | {len(candidates):5} | "
                f"{_shorten(top1, 40):40} | {', '.join(_shorten(t, 30) for t in top5)}"
            )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    asyncio.run(evaluate_search())


if __name__ == "__main__":
    main()
