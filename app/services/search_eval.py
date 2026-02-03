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
   "механизм подъема газлифт 502 1 коробка",
"гайка ус 6мм 2000 шт",
"поролон 10мм 2 рулона",
"фабертекс бежевый 2 рулона",
"саморез 4x25 желтый 4000 шт",
"ортопедическое основание 180x80 1 шт (1667)",
"ортопедическое основание 170x80 2 шт (1688)",
"ортопедическое основание 200x120 2 шт (1733)",
"ортопедическое основание 200x90 2 шт (1733,1707)",
"ортопедическое основание 190x80 2 шт (1729,1709)",
"ортопедическое основание 180x90 1 шт (1718)",
"ппу 140 200x80 16 шт (2036)",
"ппу 140 200x90 16 шт (2036)",
"болт м6x20 6 кг",
"болт м6x40 8 кг",
"джес 42 1 рулон",
"джес 39 1 рулон",
"липа серая широкая 4 коробки",
"липа бежевая широкая 2 коробки",
"120ка 1.60 2м 10 шт",
"120ка 1.40 2м 5 шт",
"120ка 1.80 2м 5 шт",
"финка диван труба 30x20 20 комплектов",
"механизм 236 1 коробка",
"пружина для механизма 236 20 шт",
"локти хром на финку 3 комплекта",
"аккордеон 195 1 каркас (пикаллы)",
"болт 8x40 1 коробка",
"поролон 80 1.5x2 10 упаковок (2536)",
"тик матрасный 1 позиция",
"спанбонд белый 30 1 позиция",
"гайка забивная м6/9 1 упаковка (din1624)",
"болт шестигранный 6x30 5 кг",
"спанбонд 80гр серый с рисунком файбертекс 1.6м",
"саморез 3.5x19 черный по дереву",
"болт мебельный 6x25 10 кг",
"болт мебельный 6x40 6 кг",
"шайба м6 10 кг",
"аккордеон 120 1 шт",
"гайка врезная крыльчатая 8/11 1 коробка",
"нитки белые 70 5 шт (1 спайка)",
"скоба узкая 30мм",
"ткань капучино аналог дива05 (дешевле)",
"стежка ролик 1 шт",
"нитки 5 шт",
"киперная лента 6 шт",
"заглушка 16 круглая внутренняя",
"заглушка 20x20 квадратная внутренняя",
"стежка грей наличие",
"стежка блек наличие",
"подушки рм 1 2 упаковки",
"нога хром колодец 2 коробки",
"декор дуга венге шоколад 1 упаковка",
"нитки ll70 225 4 коробки",
"нитки ll70 109 4 коробки",
"нитки ll70 235 4 коробки",
"нитки ll70 219 4 коробки",
"нитки ll70 116 4 коробки",
"нитки ll70 216 2 коробки",
"нитки ll70 212 4 коробки",
"нитки ll70 104 4 коробки",
"резинка трусовая 10 5 мотков",
"поролон в тюках наличие"

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
