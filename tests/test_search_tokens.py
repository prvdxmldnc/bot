from __future__ import annotations

import asyncio

from app.models import Product
from app.services.search import search_products


class DummyResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class DummySession:
    def __init__(self, items):
        self._items = items

    async def execute(self, *_args, **_kwargs):
        return DummyResult(self._items)


def test_query_parens_matches_gray_variants():
    products = [
        Product(id=1, title_ru="Молния рулонная тип 5 серая", sku="MZ-1"),
        Product(id=2, title_ru="Молния рулонная тип 5 бежевая", sku="MZ-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "молния сер(ая)", limit=5))
    assert results
    assert results[0]["id"] == 1


def test_query_no_sign_matches_type_and_beige():
    products = [
        Product(id=1, title_ru="Молния рулонная Тип 5 (спираль) бежевый цвет КТ 308", sku="MZ-1"),
        Product(id=2, title_ru="Молния рулонная Тип 3 (спираль) черный цвет", sku="MZ-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "молния рулонная тип № 5 беж", limit=5))
    assert results
    assert results[0]["id"] == 1


def test_chalk_not_velcro_top1_is_chalk():
    products = [
        Product(id=1, title_ru="Липа контактная белый 20мм", sku="VC-1"),
        Product(id=2, title_ru="Карандаш меловой белый для разметки", sku="CH-1"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "мел белый 2 коробочки", limit=5))
    assert results
    assert "мел" in results[0]["title_ru"].lower()


def test_search_products_numbers_not_regressed():
    products = [
        Product(id=1, title_ru="Болт мебельный 8 * 30 (din 603)", sku="BOLT-1"),
        Product(id=2, title_ru="Болт мебельный 6 * 20", sku="BOLT-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "болт 8 30", limit=5))
    assert results
    assert results[0]["id"] == 1
